"""Pure Stage 21 Release Quality Operations policy and SLO authority.

This module deliberately has no database or ORM imports.  It is the small,
byte-stable boundary shared by the scan service, observation writer and read
projections.  Inputs are treated as untrusted authority envelopes: malformed
policy/gate facts never become a healthy result, and canonical values never
carry query text, content, reviewer notes, tickets or raw credentials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any


UTC = timezone.utc
OPERATIONS_SCHEMA_VERSION = 1

_ALLOWED_RISK_TIERS = frozenset({"low", "medium", "high"})
_ALLOWED_POLICY_STATUSES = frozenset({"active", "disabled"})
_ALLOWED_GATE_STATES = frozenset(
    {"passed", "waived", "blocked", "unavailable", "not_required", "stale"}
)
_ALLOWED_SEVERITIES = frozenset({"healthy", "warning", "critical", "unavailable"})
_ALLOWED_RELEASE_ROLES = frozenset({"active", "pinned"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_KEYS = frozenset(
    {
        "at",
        "created_at",
        "updated_at",
        "observed_at",
        "valid_until",
        "certification_valid_until",
        "expires_at",
        "waiver_expires_at",
        "opened_at",
        "resolved_at",
        "started_at",
        "finished_at",
        "claimed_at",
        "claim_lease_until",
        "heartbeat_at",
        "next_attempt_at",
        "planned_at",
    }
)
_FORBIDDEN_KEY_MARKERS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "client_secret",
        "credential",
        "password",
        "passwd",
        "secret",
        "ticket",
        "idempotency_key",
        "body",
        "content",
        "question",
        "answer",
        "note",
        "comment",
        "query",
    }
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|client[_-]?secret|(?:raw[_-]?|opaque[_-]?|invite[_-]?|session[_-]?)?ticket|"
    r"token)\s*[:=]\s*\S+|"
    r"bearer\s+\S+|secret://\S+|sk_(?:live|test)[-_]\S+|ghp_\S+|xox[baprs]-\S+"
    r")"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\+[a-z0-9_]+)?|"
    r"mongodb(?:\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\/\/\S+"
)
_JWT_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


class ReleaseQualityOperationsError(ValueError):
    """Base error for malformed or ambiguous operations authority."""

    code = "release_quality_operations_invalid"
    status = 422

    def __init__(self, message: str = "Release quality operations authority is invalid") -> None:
        super().__init__(message)
        self.message = message


class ReleaseQualityOperationsInvalid(ReleaseQualityOperationsError):
    """Raised when a strict policy or canonical value cannot be trusted."""


class ReleaseQualityOperationsConflict(ReleaseQualityOperationsInvalid):
    """Raised when two active authority rows claim the same canonical scope."""

    code = "release_quality_operations_conflict"
    status = 409


# Friendly aliases for callers that use the shorter domain vocabulary.
QualityOperationsError = ReleaseQualityOperationsError
QualityOperationsInvalid = ReleaseQualityOperationsInvalid
QualityOperationsConflict = ReleaseQualityOperationsConflict


def _invalid(message: str) -> ReleaseQualityOperationsInvalid:
    return ReleaseQualityOperationsInvalid(message)


def _exact_integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        suffix = f" >= {minimum}" if minimum is not None else ""
        raise _invalid(f"{field} must be an exact integer{suffix}")
    return value


def _exact_boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise _invalid(f"{field} must be an exact boolean")
    return value


def _safe_text(value: Any, field: str, *, required: bool = True, maximum: int = 512) -> str | None:
    if not isinstance(value, str):
        if value is None and not required:
            return None
        raise _invalid(f"{field} must be a string")
    result = value.strip()
    if required and not result:
        raise _invalid(f"{field} must not be empty")
    if len(result) > maximum:
        raise _invalid(f"{field} is too long")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in result):
        raise _invalid(f"{field} contains unsupported control characters")
    if (
        _SECRET_VALUE_RE.search(result)
        or _DATABASE_URL_RE.search(result)
        or _JWT_LIKE_RE.search(result)
    ):
        raise _invalid(f"{field} contains credential-like text")
    return result


def _safe_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > 128:
        raise _invalid(f"{field} is too long")
    if any(ord(character) < 32 for character in result):
        raise _invalid(f"{field} contains unsupported control characters")
    if (
        _SECRET_VALUE_RE.search(result)
        or _DATABASE_URL_RE.search(result)
        or _JWT_LIKE_RE.search(result)
    ):
        raise _invalid(f"{field} contains credential-like text")
    return result


def _sha256(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _invalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical_key(raw_key: Any, path: str) -> tuple[str, str]:
    key = _safe_key(raw_key, f"{path}.key")
    normalized = key.casefold().replace("-", "_")
    return key, normalized


def _is_forbidden_key(normalized: str) -> bool:
    if normalized in _FORBIDDEN_KEY_MARKERS:
        return True
    if normalized.endswith("_body") or normalized.endswith("_content"):
        return True
    if normalized.endswith("_note") or normalized.endswith("_notes"):
        return True
    if normalized.endswith("_comment") or normalized.endswith("_comments"):
        return True
    if normalized.endswith("_query") or normalized == "query":
        return True
    if "ticket" in normalized:
        return True
    if normalized.startswith("query_") and normalized not in {"query_hash", "query_digest"}:
        return True
    if normalized.startswith("api_key") or normalized.startswith("apikey"):
        return True
    if normalized.endswith("_secret") or normalized.endswith("_password"):
        return True
    if normalized.endswith("_token") or normalized.endswith("_credential"):
        return True
    if normalized == "idempotency_key" or normalized.endswith("_idempotency_key"):
        return True
    return False


def _parse_utc_datetime(value: Any, field: str) -> datetime:
    """Parse an authority timestamp and normalize it to UTC with microseconds.

    Existing catalog rows use timezone-naive UTC datetimes.  They are accepted
    as UTC explicitly; no local timezone is ever consulted.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or "T" not in text and " " not in text:
            raise _invalid(f"{field} must be an ISO timestamp")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError) as exc:
            raise _invalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise _invalid(f"{field} must be a datetime or ISO timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        try:
            parsed = parsed.astimezone(UTC)
        except (TypeError, ValueError) as exc:
            raise _invalid(f"{field} timezone is invalid") from exc
    return parsed.replace(tzinfo=None, fold=0)


def _iso_utc(value: Any, field: str) -> str:
    return _parse_utc_datetime(value, field).isoformat(timespec="microseconds") + "Z"


def _key_looks_like_timestamp(normalized_key: str) -> bool:
    return (
        normalized_key in _TIMESTAMP_KEYS
        or normalized_key.endswith("_at")
        or normalized_key.endswith("_until")
    )


def _canonical_value(value: Any, *, path: str, key_hint: str | None = None, depth: int = 0) -> Any:
    if depth > 24:
        raise _invalid(f"{path} exceeds the canonical JSON depth limit")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid(f"{path} contains a non-finite number")
        return value
    if isinstance(value, datetime):
        return _iso_utc(value, path)
    if isinstance(value, str):
        if key_hint is not None and _key_looks_like_timestamp(key_hint):
            return _iso_utc(value, path)
        safe = _safe_text(value, path, maximum=4096)
        return safe
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key, normalized_key = _canonical_key(raw_key, path)
            if _is_forbidden_key(normalized_key):
                continue
            if key in normalized:
                raise _invalid(f"{path} contains duplicate canonical keys")
            normalized[key] = _canonical_value(
                nested,
                path=f"{path}.{key}",
                key_hint=normalized_key,
                depth=depth + 1,
            )
        return {key: normalized[key] for key in sorted(normalized)}
    raise _invalid(f"{path} contains an unsupported value")


def canonical_operations_digest(namespace: str, value: Any) -> str:
    """Return a deterministic, domain-separated SHA-256 operations digest."""

    label = _safe_key(namespace, "digest namespace")
    if len(label) > 64:
        raise _invalid("digest namespace is too long")
    normalized = _canonical_value(value, path=f"operations.{label}")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = sha256()
    digest.update(b"rag4c:release-quality-operations:v1\x00")
    for payload in (label, encoded):
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _policy_candidates(policies: Any) -> list[Mapping[str, Any]]:
    if isinstance(policies, Mapping):
        nested = policies.get("policies", policies.get("items"))
        if nested is None:
            return [policies]
        policies = nested
    if not isinstance(policies, Sequence) or isinstance(policies, (str, bytes, bytearray)):
        raise _invalid("policies must be a policy object or an array")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(policies):
        if not isinstance(item, Mapping):
            raise _invalid(f"policies[{index}] must be an object")
        result.append(item)
    return result


def _scope_projection(raw: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    prefix = f"policy[{index}]"
    policy_id = _safe_key(raw.get("id"), f"{prefix}.id")
    tenant_id = _safe_key(raw.get("tenant_id"), f"{prefix}.tenant_id")
    name = _safe_text(raw.get("name"), f"{prefix}.name", maximum=256)
    scope_type = raw.get("scope_type")
    if not isinstance(scope_type, str) or scope_type not in {"global", "risk_tier", "channel"}:
        raise _invalid(f"{prefix}.scope_type is invalid")
    scope_value = _safe_key(raw.get("scope_value"), f"{prefix}.scope_value")
    channel_id = raw.get("channel_id")
    if channel_id is not None:
        channel_id = _safe_key(channel_id, f"{prefix}.channel_id")
    if scope_type == "global":
        if scope_value != "*" or channel_id is not None:
            raise _invalid(f"{prefix}.global scope is not canonical")
    elif scope_type == "risk_tier":
        if scope_value not in _ALLOWED_RISK_TIERS or channel_id is not None:
            raise _invalid(f"{prefix}.risk_tier scope is not canonical")
    elif channel_id != scope_value:
        raise _invalid(f"{prefix}.channel scope is not canonical")

    active_scope_key = _safe_key(raw.get("active_scope_key"), f"{prefix}.active_scope_key")
    expected_scope_key = f"{scope_type}:{scope_value}"
    if active_scope_key != expected_scope_key:
        raise _invalid(f"{prefix}.active_scope_key is not canonical")

    status = raw.get("status")
    if status not in _ALLOWED_POLICY_STATUSES:
        raise _invalid(f"{prefix}.status is invalid")
    if status != "active":
        raise _invalid(f"{prefix}.disabled policy must not be projected")

    revision = _exact_integer(raw.get("revision"), f"{prefix}.revision", minimum=1)
    certification_warning = _exact_integer(
        raw.get("certification_warning_minutes"),
        f"{prefix}.certification_warning_minutes",
        minimum=1,
    )
    certification_critical = _exact_integer(
        raw.get("certification_critical_minutes"),
        f"{prefix}.certification_critical_minutes",
        minimum=1,
    )
    if certification_warning <= certification_critical:
        raise _invalid(
            "certification_warning_minutes must be greater than certification_critical_minutes"
        )
    waiver_warning = _exact_integer(
        raw.get("waiver_warning_minutes"), f"{prefix}.waiver_warning_minutes", minimum=1
    )
    max_open_alerts = _exact_integer(
        raw.get("max_open_alerts"), f"{prefix}.max_open_alerts", minimum=1
    )
    auto_queue = _exact_boolean(
        raw.get("auto_queue_recertification"), f"{prefix}.auto_queue_recertification"
    )
    require_passing = _exact_boolean(
        raw.get("require_passing_certification"), f"{prefix}.require_passing_certification"
    )
    allow_waiver = _exact_boolean(raw.get("allow_active_waiver"), f"{prefix}.allow_active_waiver")
    policy_digest = _sha256(raw.get("policy_digest"), f"{prefix}.policy_digest")

    projection: dict[str, Any] = {
        "id": policy_id,
        "tenant_id": tenant_id,
        "name": name,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "channel_id": channel_id,
        "active_scope_key": active_scope_key,
        "status": status,
        "revision": revision,
        "certification_warning_minutes": certification_warning,
        "certification_critical_minutes": certification_critical,
        "waiver_warning_minutes": waiver_warning,
        "max_open_alerts": max_open_alerts,
        "auto_queue_recertification": auto_queue,
        "require_passing_certification": require_passing,
        "allow_active_waiver": allow_waiver,
        "policy_digest": policy_digest,
    }
    for key in ("created_at", "updated_at", "disabled_at"):
        if key in raw and raw[key] is not None:
            projection[key] = _iso_utc(raw[key], f"{prefix}.{key}")
    for key in ("created_by", "updated_by", "disabled_by"):
        if key in raw and raw[key] is not None:
            projection[key] = _safe_key(raw[key], f"{prefix}.{key}")
    return projection


def resolve_slo_policy_projection(
    policies: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    channel_id: Any = None,
    risk_tier: Any = None,
    tenant_id: Any = None,
    channel: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve one active SLO policy using channel, risk-tier, global precedence."""

    if channel is not None:
        if not isinstance(channel, Mapping):
            raise _invalid("channel must be an object")
        channel_mapping_id = channel.get("id", channel.get("channel_id"))
        channel_mapping_risk = channel.get("risk_tier")
        if channel_id is not None and channel_mapping_id != channel_id:
            raise _invalid("channel_id conflicts with channel authority")
        if risk_tier is not None and channel_mapping_risk != risk_tier:
            raise _invalid("risk_tier conflicts with channel authority")
        channel_id = channel_mapping_id
        risk_tier = channel_mapping_risk

    if channel_id is not None:
        channel_id = _safe_key(channel_id, "channel_id")
    if risk_tier is not None:
        if not isinstance(risk_tier, str) or risk_tier not in _ALLOWED_RISK_TIERS:
            raise _invalid("risk_tier is invalid")
    if tenant_id is not None:
        tenant_id = _safe_key(tenant_id, "tenant_id")

    projected: list[dict[str, Any]] = []
    seen_tenants: set[str] = set()
    for index, raw in enumerate(_policy_candidates(policies)):
        status = raw.get("status")
        if status == "disabled":
            continue
        if status != "active":
            raise _invalid(f"policy[{index}].status is invalid")
        candidate = _scope_projection(raw, index=index)
        if tenant_id is not None and candidate["tenant_id"] != tenant_id:
            continue
        seen_tenants.add(candidate["tenant_id"])
        projected.append(candidate)
    if tenant_id is None and len(seen_tenants) > 1:
        raise _invalid("policies contain multiple tenants without tenant_id")

    choices: list[tuple[str, str | None]] = [
        ("channel", channel_id),
        ("risk_tier", risk_tier),
        ("global", "*"),
    ]
    for resolution, value in choices:
        if value is None:
            continue
        matches = [
            item
            for item in projected
            if item["scope_type"] == resolution and item["scope_value"] == value
        ]
        if len(matches) > 1:
            raise ReleaseQualityOperationsConflict(
                f"duplicate active SLO policy scope: {resolution}:{value}"
            )
        if matches:
            result = dict(matches[0])
            result["resolution"] = resolution
            return result
    return None


def _as_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object")
    return value


def _first_present(raw: Mapping[str, Any], *keys: str) -> Any:
    found = [key for key in keys if key in raw]
    if not found:
        return None
    first = raw[found[0]]
    for key in found[1:]:
        if raw[key] != first:
            raise _invalid(f"{found[0]} and {key} conflict")
    return first


def _project_certification(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = _as_mapping(raw, "certification")
    result: dict[str, Any] = {
        "id": _safe_key(_first_present(value, "id", "certification_id"), "certification.id"),
        "digest": _sha256(
            _first_present(value, "certification_digest", "digest"), "certification.digest"
        ),
        "valid_until": _iso_utc(
            _first_present(value, "valid_until", "certification_valid_until"),
            "certification.valid_until",
        ),
    }
    status = value.get("status")
    if status is not None:
        if not isinstance(status, str) or status not in {"passed", "stale", "expired", "revoked"}:
            raise _invalid("certification.status is invalid")
        result["status"] = status
    for key in ("stale", "is_stale", "current", "revoked"):
        if key in value:
            result[key] = _exact_boolean(value[key], f"certification.{key}")
    return result


def _project_waiver(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = _as_mapping(raw, "waiver")
    result: dict[str, Any] = {
        "id": _safe_key(_first_present(value, "id", "waiver_id"), "waiver.id"),
        "digest": _sha256(_first_present(value, "waiver_digest", "digest"), "waiver.digest"),
        "expires_at": _iso_utc(
            _first_present(value, "expires_at", "waiver_expires_at"), "waiver.expires_at"
        ),
    }
    status = value.get("status")
    if status is not None:
        if not isinstance(status, str) or status not in {
            "active",
            "approved",
            "expired",
            "revoked",
        }:
            raise _invalid("waiver.status is invalid")
        result["status"] = status
    if "revoked" in value:
        result["revoked"] = _exact_boolean(value["revoked"], "waiver.revoked")
    return result


def _project_channel(gate: Mapping[str, Any]) -> dict[str, Any] | None:
    nested = gate.get("channel")
    if nested is None:
        if not any(
            key in gate
            for key in (
                "channel_id",
                "risk_tier",
                "is_default_serving",
                "default_serving",
            )
        ):
            return None
        nested = gate
    channel = _as_mapping(nested, "gate.channel")
    channel_id = _first_present(channel, "id", "channel_id")
    risk_tier = channel.get("risk_tier")
    is_default = _first_present(channel, "is_default_serving", "default_serving")
    result: dict[str, Any] = {
        "id": None if channel_id is None else _safe_key(channel_id, "gate.channel.id"),
        "risk_tier": risk_tier,
        "is_default_serving": (
            None
            if is_default is None
            else _exact_boolean(is_default, "gate.channel.is_default_serving")
        ),
    }
    if result["id"] is None:
        raise _invalid("gate.channel.id is required")
    if not isinstance(risk_tier, str) or risk_tier not in _ALLOWED_RISK_TIERS:
        raise _invalid("gate.channel.risk_tier is invalid")
    return result


def _unavailable_result() -> dict[str, Any]:
    return {
        "state": "unavailable",
        "severity": "unavailable",
        "reason": "quality_authority_unavailable",
        "alert_type": "quality_authority_unavailable",
        "gate_state": "unavailable",
        "gate_reason": "quality_authority_unavailable",
        "certification_id": None,
        "certification_digest": None,
        "certification_valid_until": None,
        "minutes_to_certification_expiry": None,
        "waiver_id": None,
        "waiver_digest": None,
        "waiver_expires_at": None,
        "minutes_to_waiver_expiry": None,
        "policy_id": None,
        "policy_revision": None,
        "policy_digest": None,
    }


def _base_result(
    *,
    gate_state: str,
    gate_reason: str,
    policy: Mapping[str, Any] | None,
    certification: Mapping[str, Any] | None,
    waiver: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    certification_expiry = (
        _parse_utc_datetime(certification["valid_until"], "certification.valid_until")
        if certification is not None
        else None
    )
    waiver_expiry = (
        _parse_utc_datetime(waiver["expires_at"], "waiver.expires_at")
        if waiver is not None
        else None
    )
    result: dict[str, Any] = {
        "state": "unavailable",
        "severity": "unavailable",
        "reason": "quality_authority_unavailable",
        "alert_type": "quality_authority_unavailable",
        "gate_state": gate_state,
        "gate_reason": gate_reason,
        "certification_id": None if certification is None else certification["id"],
        "certification_digest": None if certification is None else certification["digest"],
        "certification_valid_until": (
            None
            if certification_expiry is None
            else _iso_utc(certification_expiry, "certification.valid_until")
        ),
        "minutes_to_certification_expiry": (
            None
            if certification_expiry is None
            else int((certification_expiry - now) // timedelta(minutes=1))
        ),
        "waiver_id": None if waiver is None else waiver["id"],
        "waiver_digest": None if waiver is None else waiver["digest"],
        "waiver_expires_at": (
            None if waiver_expiry is None else _iso_utc(waiver_expiry, "waiver.expires_at")
        ),
        "minutes_to_waiver_expiry": (
            None if waiver_expiry is None else int((waiver_expiry - now) // timedelta(minutes=1))
        ),
        "policy_id": None if policy is None else policy["id"],
        "policy_revision": None if policy is None else policy["revision"],
        "policy_digest": None if policy is None else policy["policy_digest"],
    }
    return result


def _finish(
    result: dict[str, Any],
    *,
    state: str,
    severity: str,
    reason: str,
    alert_type: str | None,
) -> dict[str, Any]:
    result.update(
        {"state": state, "severity": severity, "reason": reason, "alert_type": alert_type}
    )
    return result


def evaluate_release_quality_slo(
    now: Any = None,
    policy: Mapping[str, Any] | None = None,
    gate: Mapping[str, Any] | None = None,
    *,
    slo_policy: Mapping[str, Any] | None = None,
    quality_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate an already-resolved Stage 20 gate without touching persistence.

    The evaluator returns an explicit ``unavailable`` result for malformed
    authority.  It never converts missing evidence into healthy or failed
    evidence, which lets the scan service persist a safe observation.
    """

    try:
        if policy is not None and slo_policy is not None and policy != slo_policy:
            raise _invalid("policy and slo_policy conflict")
        if gate is not None and quality_gate is not None and gate != quality_gate:
            raise _invalid("gate and quality_gate conflict")
        policy = policy if policy is not None else slo_policy
        gate = gate if gate is not None else quality_gate
        current = _parse_utc_datetime(now, "now")
        projected_policy = None if policy is None else _scope_projection(policy, index=0)
        gate_value = _as_mapping(gate, "gate")
        gate_state = _first_present(gate_value, "state", "gate_state")
        if not isinstance(gate_state, str) or gate_state not in _ALLOWED_GATE_STATES:
            raise _invalid("gate.state is invalid")
        gate_reason_value = _first_present(gate_value, "reason", "gate_reason")
        gate_reason = _safe_text(gate_reason_value, "gate.reason", maximum=512)
        channel = _project_channel(gate_value)
        if projected_policy is not None and channel is not None:
            if (
                projected_policy["scope_type"] == "channel"
                and projected_policy["scope_value"] != channel["id"]
            ):
                raise _invalid("policy channel scope does not match gate channel")
            if (
                projected_policy["scope_type"] == "risk_tier"
                and projected_policy["scope_value"] != channel["risk_tier"]
            ):
                raise _invalid("policy risk-tier scope does not match gate channel")

        certification_raw = gate_value.get("certification")
        if certification_raw is None and any(
            key in gate_value for key in ("certification_id", "certification_digest", "valid_until")
        ):
            certification_raw = gate_value
        certification = _project_certification(certification_raw)
        waiver_raw = gate_value.get("waiver")
        if waiver_raw is None and any(
            key in gate_value for key in ("waiver_id", "waiver_digest", "expires_at")
        ):
            waiver_raw = gate_value
        waiver = _project_waiver(waiver_raw)
        result = _base_result(
            gate_state=gate_state,
            gate_reason=gate_reason,
            policy=projected_policy,
            certification=certification,
            waiver=waiver,
            now=current,
        )

        if gate_state == "unavailable":
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )
        if gate_state == "blocked":
            if gate_reason == "certification_stale":
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="certification_stale",
                    alert_type="certification_stale",
                )
            if gate_reason == "certification_expired":
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="certification_expired",
                    alert_type="certification_expired",
                )
            return _finish(
                result,
                state="critical",
                severity="critical",
                reason="quality_gate_blocked",
                alert_type="quality_gate_blocked",
            )
        if gate_state == "stale":
            return _finish(
                result,
                state="critical",
                severity="critical",
                reason="certification_stale",
                alert_type="certification_stale",
            )

        if certification is not None:
            status = certification.get("status")
            if status == "expired":
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="certification_expired",
                    alert_type="certification_expired",
                )
            if status in {"stale", "revoked"}:
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="certification_stale",
                    alert_type="certification_stale",
                )
            for key in ("stale", "is_stale"):
                if certification.get(key) is True:
                    return _finish(
                        result,
                        state="critical",
                        severity="critical",
                        reason="certification_stale",
                        alert_type="certification_stale",
                    )
            if "current" in certification and certification["current"] is False:
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="certification_stale",
                    alert_type="certification_stale",
                )

        if gate_state == "not_required":
            if (
                channel is not None
                and channel["risk_tier"] == "low"
                and channel["is_default_serving"] is False
            ):
                return _finish(
                    result,
                    state="not_required",
                    severity="healthy",
                    reason="not_required",
                    alert_type=None,
                )
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )

        if projected_policy is None:
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )

        if gate_state == "waived":
            if waiver is None:
                return _finish(
                    result,
                    state="unavailable",
                    severity="unavailable",
                    reason="quality_authority_unavailable",
                    alert_type="quality_authority_unavailable",
                )
            waiver_status = waiver.get("status")
            if waiver_status in {"expired", "revoked"} or waiver.get("revoked") is True:
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="waiver_expired",
                    alert_type="waiver_expired",
                )
            minutes = result["minutes_to_waiver_expiry"]
            if minutes is None:
                return _finish(
                    result,
                    state="unavailable",
                    severity="unavailable",
                    reason="quality_authority_unavailable",
                    alert_type="quality_authority_unavailable",
                )
            if minutes <= 0:
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="waiver_expired",
                    alert_type="waiver_expired",
                )
            if minutes <= projected_policy["waiver_warning_minutes"]:
                return _finish(
                    result,
                    state="critical",
                    severity="critical",
                    reason="waiver_expiring",
                    alert_type="waiver_expiring",
                )
            if projected_policy["allow_active_waiver"]:
                return _finish(
                    result,
                    state="healthy",
                    severity="healthy",
                    reason="waiver_active",
                    alert_type=None,
                )
            return _finish(
                result,
                state="warning",
                severity="warning",
                reason="active_waiver_not_allowed",
                alert_type="quality_gate_blocked",
            )

        if gate_state != "passed":
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )
        if certification is None:
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )
        minutes = result["minutes_to_certification_expiry"]
        if minutes is None:
            return _finish(
                result,
                state="unavailable",
                severity="unavailable",
                reason="quality_authority_unavailable",
                alert_type="quality_authority_unavailable",
            )
        if minutes <= 0:
            return _finish(
                result,
                state="critical",
                severity="critical",
                reason="certification_expired",
                alert_type="certification_expired",
            )
        if minutes <= projected_policy["certification_critical_minutes"]:
            return _finish(
                result,
                state="critical",
                severity="critical",
                reason="certification_expiring",
                alert_type="certification_expiring",
            )
        if minutes <= projected_policy["certification_warning_minutes"]:
            return _finish(
                result,
                state="warning",
                severity="warning",
                reason="certification_expiring",
                alert_type="certification_expiring",
            )
        return _finish(
            result,
            state="healthy",
            severity="healthy",
            reason="certification_current",
            alert_type=None,
        )
    except (ReleaseQualityOperationsError, TypeError, ValueError, OverflowError):
        return _unavailable_result()


def _required_observation_text(raw: Mapping[str, Any], key: str) -> str:
    value = _safe_text(raw.get(key), f"observation.{key}", maximum=512)
    assert value is not None
    return value


def canonical_observation(
    observation: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> dict[str, Any]:
    """Project one immutable, body-free observation and attach its digest."""

    if observation is None:
        raw: dict[str, Any] = {}
    elif isinstance(observation, Mapping):
        raw = dict(observation)
    else:
        raise _invalid("observation must be an object")
    raw.update(fields)

    # Accept the natural service shape (gate/evaluation/policy envelopes) while
    # keeping the persisted projection flat and explicitly allow-listed.
    for source_name in ("gate", "evaluation"):
        source = raw.get(source_name)
        if not isinstance(source, Mapping):
            continue
        aliases = {
            "gate_state": ("gate_state", "state"),
            "gate_reason": ("gate_reason", "reason"),
            "severity": ("severity",),
            "minutes_to_certification_expiry": ("minutes_to_certification_expiry",),
            "minutes_to_waiver_expiry": ("minutes_to_waiver_expiry",),
            "certification_id": ("certification_id",),
            "certification_digest": ("certification_digest",),
            "certification_valid_until": ("certification_valid_until",),
            "waiver_id": ("waiver_id",),
            "waiver_digest": ("waiver_digest",),
            "waiver_expires_at": ("waiver_expires_at",),
            "channel_id": ("channel_id",),
        }
        channel = source.get("channel")
        if isinstance(channel, Mapping):
            aliases["channel_id"] = ("channel_id", "id")
            if "channel_id" not in source:
                source = {**source, "channel_id": channel.get("id", channel.get("channel_id"))}
            if "risk_tier" not in raw and "risk_tier" in channel:
                raw["risk_tier"] = channel["risk_tier"]
        for target, source_keys in aliases.items():
            if target in raw:
                continue
            for source_key in source_keys:
                if source_key in source:
                    raw[target] = source[source_key]
                    break
        for nested_name, target_prefix in (
            ("certification", "certification"),
            ("waiver", "waiver"),
        ):
            nested = source.get(nested_name)
            if not isinstance(nested, Mapping):
                continue
            nested_aliases = (
                (f"{target_prefix}_id", ("id", f"{target_prefix}_id")),
                (
                    f"{target_prefix}_digest",
                    (f"{target_prefix}_digest", "digest"),
                ),
                (
                    "certification_valid_until"
                    if target_prefix == "certification"
                    else "waiver_expires_at",
                    (
                        ("valid_until", "certification_valid_until")
                        if target_prefix == "certification"
                        else ("expires_at", "waiver_expires_at")
                    ),
                ),
            )
            for target, source_keys in nested_aliases:
                if target in raw:
                    continue
                for source_key in source_keys:
                    if source_key in nested:
                        raw[target] = nested[source_key]
                        break

    policy_source = raw.get("slo_policy", raw.get("policy"))
    if isinstance(policy_source, Mapping):
        if "slo_policy_id" not in raw and "id" in policy_source:
            raw["slo_policy_id"] = policy_source["id"]
        if "slo_policy_revision" not in raw and "revision" in policy_source:
            raw["slo_policy_revision"] = policy_source["revision"]

    tenant_id = _safe_key(raw.get("tenant_id"), "observation.tenant_id")
    dataset_id = _safe_key(raw.get("dataset_id"), "observation.dataset_id")
    release_id = _safe_key(raw.get("release_id"), "observation.release_id")
    channel_id = _safe_key(raw.get("channel_id"), "observation.channel_id")
    scan_run_id = _safe_key(raw.get("scan_run_id"), "observation.scan_run_id")
    policy_id = raw.get("slo_policy_id")
    if policy_id is not None:
        policy_id = _safe_key(policy_id, "observation.slo_policy_id")
    policy_revision = raw.get("slo_policy_revision")
    if policy_id is None and policy_revision is not None:
        raise _invalid("slo_policy_revision requires slo_policy_id")
    if policy_id is not None and policy_revision is None:
        raise _invalid("slo_policy_id requires slo_policy_revision")
    if policy_revision is not None:
        policy_revision = _exact_integer(
            policy_revision, "observation.slo_policy_revision", minimum=1
        )
    release_role = raw.get("release_role")
    if release_role not in _ALLOWED_RELEASE_ROLES:
        raise _invalid("observation.release_role is invalid")
    gate_state = raw.get("gate_state")
    if gate_state not in _ALLOWED_GATE_STATES:
        raise _invalid("observation.gate_state is invalid")
    gate_reason = _required_observation_text(raw, "gate_reason")
    severity = raw.get("severity")
    if severity not in _ALLOWED_SEVERITIES:
        raise _invalid("observation.severity is invalid")
    observed_at_value = raw.get("observed_at")
    observed_at_dt = _parse_utc_datetime(observed_at_value, "observation.observed_at")
    observed_at = _iso_utc(observed_at_dt, "observation.observed_at")

    certification_id = raw.get("certification_id")
    certification_digest = raw.get("certification_digest")
    certification_valid_until = raw.get("certification_valid_until")
    certification_expiry: datetime | None = None
    if certification_id is None:
        if certification_digest is not None or certification_valid_until is not None:
            raise _invalid("certification fields must be null without certification_id")
    else:
        certification_id = _safe_key(certification_id, "observation.certification_id")
        certification_digest = _sha256(certification_digest, "observation.certification_digest")
        certification_expiry = _parse_utc_datetime(
            certification_valid_until, "observation.certification_valid_until"
        )
        certification_valid_until = _iso_utc(
            certification_expiry, "observation.certification_valid_until"
        )

    waiver_id = raw.get("waiver_id")
    waiver_digest = raw.get("waiver_digest")
    waiver_expires_at = raw.get("waiver_expires_at")
    waiver_expiry: datetime | None = None
    if waiver_id is None:
        if waiver_digest is not None or waiver_expires_at is not None:
            raise _invalid("waiver fields must be null without waiver_id")
    else:
        waiver_id = _safe_key(waiver_id, "observation.waiver_id")
        waiver_digest = _sha256(waiver_digest, "observation.waiver_digest")
        waiver_expiry = _parse_utc_datetime(waiver_expires_at, "observation.waiver_expires_at")
        waiver_expires_at = _iso_utc(waiver_expiry, "observation.waiver_expires_at")

    def _signed_or_computed(key: str, expiry: datetime | None) -> int | None:
        supplied = raw.get(key)
        if expiry is None:
            if supplied is not None:
                raise _invalid(f"{key} must be null without an expiry timestamp")
            return None
        computed = int((expiry - observed_at_dt) // timedelta(minutes=1))
        if supplied is not None and _exact_integer(supplied, f"observation.{key}") != computed:
            raise _invalid(f"{key} does not match the signed UTC minute calculation")
        return computed

    minutes_to_certification_expiry = _signed_or_computed(
        "minutes_to_certification_expiry", certification_expiry
    )
    minutes_to_waiver_expiry = _signed_or_computed("minutes_to_waiver_expiry", waiver_expiry)
    observed_by = _safe_key(raw.get("observed_by"), "observation.observed_by")
    request_id = _safe_key(raw.get("request_id"), "observation.request_id")
    canonical: dict[str, Any] = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "channel_id": channel_id,
        "scan_run_id": scan_run_id,
        "slo_policy_id": policy_id,
        "slo_policy_revision": policy_revision,
        "release_role": release_role,
        "gate_state": gate_state,
        "gate_reason": gate_reason,
        "certification_id": certification_id,
        "certification_digest": certification_digest,
        "certification_valid_until": certification_valid_until,
        "waiver_id": waiver_id,
        "waiver_digest": waiver_digest,
        "waiver_expires_at": waiver_expires_at,
        "minutes_to_certification_expiry": minutes_to_certification_expiry,
        "minutes_to_waiver_expiry": minutes_to_waiver_expiry,
        "severity": severity,
        "observed_at": observed_at,
        "observed_by": observed_by,
        "request_id": request_id,
    }
    canonical["observation_digest"] = canonical_operations_digest("observation", canonical)
    return canonical


__all__ = [
    "OPERATIONS_SCHEMA_VERSION",
    "QualityOperationsConflict",
    "QualityOperationsError",
    "QualityOperationsInvalid",
    "ReleaseQualityOperationsConflict",
    "ReleaseQualityOperationsError",
    "ReleaseQualityOperationsInvalid",
    "canonical_observation",
    "canonical_operations_digest",
    "evaluate_release_quality_slo",
    "resolve_slo_policy_projection",
]
