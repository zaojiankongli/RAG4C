"""Pure, Tenant-safe Notification Center authority primitives.

The functions in this module are intentionally independent of SQLAlchemy and
HTTP.  They accept untrusted source envelopes and return deterministic,
body-free projections suitable for persistence by later Stage 22 services.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any


UTC = timezone.utc
NOTIFICATION_CENTER_SCHEMA_VERSION = 1

_ALLOWED_SOURCE_KINDS = frozenset({"quality_alert", "approval_pending_for_me"})
_ALLOWED_ROUTE_CODES = frozenset({"knowledge_quality_operations", "enterprise_approval"})
_ALLOWED_CATEGORIES = frozenset({"quality", "approval"})
_ALLOWED_SEVERITIES = frozenset({"info", "warning", "critical"})
_ALLOWED_EVENT_TYPES = frozenset({"materialized", "marked_read", "marked_unread", "archived"})
_ALLOWED_RECIPIENT_REASONS = frozenset(
    {
        "tenant_owner",
        "tenant_admin",
        "dataset_owner",
        "eligible_approver",
        "explicit_subscription",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_URL_RE = re.compile(
    r"(?i)(?:https?|ftp|file|mailto|javascript|data):\S+|"
    r"\b(?:www\.)\S+|"
    r"\b(?:mysql|mariadb|postgres(?:ql)?|redis|sqlite):\/\/\S+"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|"
    r"api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]\s*\S+|"
    r"bearer\s+\S+|secret://\S+|"
    r"(?:sk_(?:live|test)[-_]|ghp_|xox[baprs]-)\S+|"
    r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r")"
)
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?i)\b(?:select|insert|update|delete)\b|"
    r"\b(?:query|result\s+body|judgment\s+note|reviewer\s+note|ticket|email|token|credential)\b"
)
_FORBIDDEN_KEY_PARTS = frozenset(
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
        "body",
        "content",
        "note",
        "comment",
        "query",
        "email",
        "token",
        "url",
        "uri",
        "href",
        "webhook",
        "idempotency_key",
    }
)
_TIMESTAMP_KEY_PARTS = frozenset(
    {
        "at",
        "occurred_at",
        "created_at",
        "updated_at",
        "assigned_at",
        "requested_at",
        "observed_at",
        "expires_at",
        "valid_until",
        "muted_until",
    }
)


class NotificationAuthorityError(ValueError):
    """Base error for malformed or unsafe Notification authority."""

    code = "notification_authority_invalid"
    status = 422

    def __init__(self, message: str = "Notification authority is invalid") -> None:
        super().__init__(message)
        self.message = message


class NotificationAuthorityInvalid(NotificationAuthorityError):
    """Raised when a Notification value cannot be trusted or canonicalized."""


class NotificationAuthorityConflict(NotificationAuthorityInvalid):
    """Raised when two Notification authority values claim one identity."""

    code = "notification_authority_conflict"
    status = 409


# Compatibility aliases for callers using the product or domain vocabulary.
NotificationCenterError = NotificationAuthorityError
NotificationCenterInvalid = NotificationAuthorityInvalid
EnterpriseNotificationError = NotificationAuthorityError
EnterpriseNotificationInvalid = NotificationAuthorityInvalid


def _invalid(message: str) -> NotificationAuthorityInvalid:
    return NotificationAuthorityInvalid(message)


def _normalized_key(raw_key: Any, path: str) -> tuple[str, str]:
    if not isinstance(raw_key, str):
        raise _invalid(f"{path} object keys must be strings")
    key = raw_key.strip()
    if not key:
        raise _invalid(f"{path} object keys must not be empty")
    key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).casefold()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    if not key:
        raise _invalid(f"{path} object key is invalid")
    if any(part in _FORBIDDEN_KEY_PARTS for part in key.split("_")) or key in _FORBIDDEN_KEY_PARTS:
        raise _invalid(f"{path} contains a forbidden field")
    return key, key


def _safe_text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise _invalid(f"{field} must not be empty")
    if len(result) > maximum:
        raise _invalid(f"{field} is too long")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in result):
        raise _invalid(f"{field} contains unsupported control characters")
    if (
        _URL_RE.search(result)
        or _EMAIL_RE.search(result)
        or _SECRET_VALUE_RE.search(result)
        or _FORBIDDEN_TEXT_RE.search(result)
    ):
        raise _invalid(f"{field} contains forbidden sensitive content")
    return result


def _exact_integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        suffix = f" >= {minimum}" if minimum is not None else ""
        raise _invalid(f"{field} must be an exact integer{suffix}")
    return value


def _exact_boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise _invalid(f"{field} must be an exact boolean")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _invalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _parse_utc_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or ("T" not in text and " " not in text):
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


def _is_timestamp_key(key: str) -> bool:
    return (
        key in _TIMESTAMP_KEY_PARTS
        or key.endswith("_at")
        or key.endswith("_until")
        or key.endswith("_timestamp")
    )


def _canonical_value(value: Any, *, path: str, key_hint: str | None = None, depth: int = 0) -> Any:
    if depth > 24:
        raise _invalid(f"{path} exceeds the canonical JSON depth limit")
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, datetime):
        return _iso_utc(value, path)
    if isinstance(value, str):
        return (
            _iso_utc(value, path)
            if key_hint and _is_timestamp_key(key_hint)
            else _safe_text(value, path, maximum=4096)
        )
    if isinstance(value, float):
        raise _invalid(f"{path} must use an exact integer or boolean, not a float")
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key, normalized = _normalized_key(raw_key, path)
            if key in result:
                raise _invalid(f"{path} contains duplicate canonical keys")
            result[key] = _canonical_value(
                nested,
                path=f"{path}.{key}",
                key_hint=normalized,
                depth=depth + 1,
            )
        return {key: result[key] for key in sorted(result)}
    raise _invalid(f"{path} contains an unsupported value")


def _canonical_json(value: Any, *, path: str) -> bytes:
    normalized = _canonical_value(value, path=path)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_notification_digest(namespace: str, value: Any) -> str:
    """Return a deterministic, domain-separated Notification SHA-256 digest."""

    label = _safe_text(namespace, "digest namespace", maximum=64)
    if re.fullmatch(r"[a-z][a-z0-9_.-]*", label) is None:
        raise _invalid("digest namespace contains unsupported characters")
    encoded = _canonical_json(value, path=f"notification.{label}")
    digest = sha256()
    digest.update(b"rag4c:enterprise-notification-center:v1\x00")
    for payload in (label.encode("utf-8"), encoded):
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _safe_component(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum)
    if maximum < 1 or len(result) > maximum:
        raise _invalid(f"{field} is too long")
    pattern = rf"[A-Za-z0-9][A-Za-z0-9_.-]{{0,{maximum - 1}}}"
    if re.fullmatch(pattern, result) is None:
        raise _invalid(f"{field} contains unsupported identity characters")
    return result


def _safe_actor(value: Any, field: str) -> str:
    result = _safe_text(value, field, maximum=128)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", result) is None:
        raise _invalid(f"{field} contains unsupported identity characters")
    return result


def _safe_code(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum).casefold()
    if maximum < 1 or len(result) > maximum:
        raise _invalid(f"{field} is too long")
    pattern = rf"[a-z][a-z0-9_.-]{{0,{maximum - 1}}}"
    if re.fullmatch(pattern, result) is None:
        raise _invalid(f"{field} must be a safe lowercase code")
    return result


def _normalized_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object")
    result: dict[str, Any] = {}
    for raw_key, nested in value.items():
        key, _ = _normalized_key(raw_key, field)
        if key in result:
            raise _invalid(f"{field} contains duplicate canonical keys")
        result[key] = nested
    return result


def _merge_mapping_input(
    primary: Mapping[str, Any] | None,
    fields: Mapping[str, Any],
    field: str,
) -> dict[str, Any]:
    result = _normalized_mapping(primary, field) if primary is not None else {}
    for raw_key, value in fields.items():
        key, _ = _normalized_key(raw_key, field)
        if key in result and result[key] != value:
            raise _invalid(f"{field}.{key} has conflicting values")
        result[key] = value
    return result


def _first_value(
    values: Mapping[str, Any], canonical: str, *aliases: str, required: bool = False
) -> Any:
    present = [key for key in (canonical, *aliases) if key in values]
    if not present:
        if required:
            raise _invalid(f"{canonical} is required")
        return None
    selected = values[present[0]]
    for key in present[1:]:
        if values[key] != selected:
            raise _invalid(f"{canonical} has conflicting aliases")
    return selected


def _source_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_SOURCE_KINDS:
        raise _invalid("source_kind is not allowed")
    return value


def _route_code(value: Any) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_ROUTE_CODES:
        raise _invalid("route code is not allowed")
    return value


def canonical_notification_key(
    source_kind: str | Mapping[str, Any] | None = None,
    source_id: str | None = None,
    source_revision: Any = None,
    event_semantic: str | None = None,
    *,
    cycle_key: Any = None,
    event_type: str | None = None,
    source: Mapping[str, Any] | None = None,
    **fields: Any,
) -> str:
    """Return the stable source/event identity for one Notification.

    The function accepts either explicit keyword/positional fields or one
    mapping.  A source revision and a materialization cycle key are mutually
    exclusive because they represent alternative source authorities.
    """

    raw: dict[str, Any] = {}
    if source is not None:
        raw.update(_normalized_mapping(source, "notification_key.source"))
    if isinstance(source_kind, Mapping):
        mapped = _normalized_mapping(source_kind, "notification_key")
        for key, value in mapped.items():
            if key in raw and raw[key] != value:
                raise _invalid(f"notification_key.{key} has conflicting values")
            raw[key] = value
        source_kind = None
    positional = {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_revision": source_revision,
        "event_semantic": event_semantic,
        "cycle_key": cycle_key,
        "event_type": event_type,
    }
    for key, value in positional.items():
        if value is not None:
            if key in raw and raw[key] != value:
                raise _invalid(f"notification_key.{key} has conflicting values")
            raw[key] = value
    for raw_key, value in fields.items():
        key, _ = _normalized_key(raw_key, "notification_key")
        if key in raw and raw[key] != value:
            raise _invalid(f"notification_key.{key} has conflicting values")
        raw[key] = value

    allowed = {
        "source_kind",
        "source_id",
        "id",
        "source_revision",
        "revision",
        "cycle_key",
        "event_semantic",
        "event_type",
        "event",
    }
    unsupported = sorted(set(raw) - allowed)
    if unsupported:
        raise _invalid("notification_key contains unsupported fields")
    kind = _source_kind(_first_value(raw, "source_kind", required=True))
    identity = _first_value(raw, "source_id", "id", required=True)
    identity = _safe_component(identity, "source_id")
    revision = _first_value(raw, "source_revision", "revision")
    cycle = raw.get("cycle_key")
    if (revision is None) == (cycle is None):
        raise _invalid("notification identity requires exactly one revision or cycle_key")
    if revision is not None:
        marker = str(_exact_integer(revision, "source_revision", minimum=1))
    else:
        marker = _safe_component(cycle, "cycle_key")
    semantic = _first_value(raw, "event_semantic", "event_type", "event", required=True)
    semantic = _safe_code(semantic, "event_type", maximum=24)
    identity_payload = {
        "source_kind": kind,
        "source_id": identity,
        "event_semantic": semantic,
    }
    if revision is not None:
        identity_payload["source_revision"] = _exact_integer(revision, "source_revision", minimum=1)
    else:
        identity_payload["cycle_key"] = marker
    return canonical_notification_digest("notification-key", identity_payload)


def project_notification_source(
    source: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> dict[str, Any]:
    """Project an allow-listed source into a safe Notification envelope."""

    raw = _merge_mapping_input(source, fields, "notification_source")
    kind = _source_kind(_first_value(raw, "source_kind", "kind", required=True))
    source_id = _first_value(raw, "source_id", "id")
    if source_id is None:
        source_id = (
            _first_value(raw, "alert_id")
            if kind == "quality_alert"
            else _first_value(raw, "approval_request_id")
        )
    source_id = _safe_component(source_id, "source_id")
    revision = _first_value(raw, "source_revision", "revision", required=True)
    revision = _exact_integer(revision, "source_revision", minimum=1)
    source_digest = _first_value(
        raw,
        "source_digest",
        "source_observation_digest",
        "observation_digest",
        required=True,
    )
    source_digest = _sha256(source_digest, "source_digest")

    category = "quality" if kind == "quality_alert" else "approval"
    supplied_category = _first_value(raw, "category")
    if supplied_category is not None and supplied_category != category:
        raise _invalid("category does not match source_kind")
    severity = _first_value(raw, "severity", required=True)
    if not isinstance(severity, str) or severity not in _ALLOWED_SEVERITIES:
        raise _invalid("severity is not allowed")

    dataset_id = _first_value(raw, "source_dataset_id", "dataset_id")
    if kind == "quality_alert":
        dataset_id = _safe_component(dataset_id, "source_dataset_id", maximum=64)
    elif dataset_id is not None:
        raise _invalid("source_dataset_id must be null for approval_pending_for_me")

    semantic = _first_value(raw, "event_semantic", "event_type", "event")
    if semantic is None:
        semantic = "opened" if kind == "quality_alert" else "pending"
    semantic = _safe_code(semantic, "event_type")
    cycle_key = raw.get("cycle_key")
    if cycle_key is not None:
        cycle_key = _safe_component(cycle_key, "cycle_key")

    occurred_at = _first_value(raw, "occurred_at", "at")
    if occurred_at is not None:
        occurred_at = _iso_utc(occurred_at, "occurred_at")

    facts_value = _first_value(raw, "safe_facts", "facts")
    if facts_value is None:
        facts: dict[str, Any] = {}
    elif not isinstance(facts_value, Mapping):
        raise _invalid("safe_facts must be an object")
    else:
        facts = dict(facts_value)

    known = {
        "tenant_id",
        "source_kind",
        "kind",
        "source_id",
        "id",
        "alert_id",
        "approval_request_id",
        "source_revision",
        "revision",
        "source_digest",
        "source_observation_digest",
        "observation_digest",
        "source_dataset_id",
        "dataset_id",
        "category",
        "severity",
        "event_semantic",
        "event_type",
        "event",
        "cycle_key",
        "occurred_at",
        "at",
        "safe_facts",
        "facts",
    }
    for key, value in raw.items():
        if key not in known:
            if key in facts and facts[key] != value:
                raise _invalid(f"safe_facts.{key} has conflicting values")
            facts[key] = value
    canonical_facts = _canonical_value(facts, path="notification_source.safe_facts")
    assert isinstance(canonical_facts, dict)

    result: dict[str, Any] = {
        "tenant_id": _safe_component(
            _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
        ),
        "source_kind": kind,
        "source_id": source_id,
        "source_revision": revision,
        "source_dataset_id": dataset_id,
        "category": category,
        "severity": severity,
        "event_semantic": semantic,
        "source_digest": source_digest,
        "safe_facts": canonical_facts,
    }
    if cycle_key is not None:
        result["cycle_key"] = cycle_key
    if occurred_at is not None:
        result["occurred_at"] = occurred_at
    return result


def project_notification_route(
    route: Mapping[str, Any] | str | None = None,
    params: Mapping[str, Any] | None = None,
    *,
    route_code: str | None = None,
    target_route_code: str | None = None,
    route_params: Mapping[str, Any] | None = None,
    target_route_params: Mapping[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Project one of the two server-owned Notification handoff routes."""

    raw: dict[str, Any] = {}
    if isinstance(route, Mapping):
        raw.update(_normalized_mapping(route, "notification_route"))
    elif route is not None:
        if not isinstance(route, str):
            raise _invalid("route must be a route code or object")
        raw["route_code"] = route
    if params is not None:
        if "params" in raw and raw["params"] != params:
            raise _invalid("route params have conflicting values")
        raw["params"] = params
    for key, value in {
        "route_code": route_code,
        "target_route_code": target_route_code,
        "route_params": route_params,
        "target_route_params": target_route_params,
    }.items():
        if value is not None:
            normalized = key
            if normalized in raw and raw[normalized] != value:
                raise _invalid(f"notification_route.{normalized} has conflicting values")
            raw[normalized] = value
    for raw_key, value in fields.items():
        key, _ = _normalized_key(raw_key, "notification_route")
        if key in raw and raw[key] != value:
            raise _invalid(f"notification_route.{key} has conflicting values")
        raw[key] = value

    code = _route_code(_first_value(raw, "route_code", "target_route_code", required=True))
    parameter_value = _first_value(
        raw, "params", "route_params", "target_route_params", "target_route_params_json"
    )
    schema = (
        ("tenant_id", "dataset_id", "release_id", "channel_id", "alert_id")
        if code == "knowledge_quality_operations"
        else ("tenant_id", "approval_request_id")
    )
    if parameter_value is None:
        direct = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "route_code",
                "target_route_code",
                "params",
                "route_params",
                "target_route_params",
                "target_route_params_json",
            }
        }
        parameter_value = direct
    if not isinstance(parameter_value, Mapping):
        raise _invalid("route parameters must be an object")
    normalized_params = _normalized_mapping(parameter_value, "route.params")
    if set(normalized_params) != set(schema):
        raise _invalid("route parameter schema must match exactly")
    projected_params = {
        key: _safe_component(normalized_params[key], f"route.params.{key}") for key in schema
    }
    return {
        "target_route_code": code,
        "target_route_params_json": projected_params,
    }


def project_notification_payload(
    payload: Mapping[str, Any] | None = None,
    /,
    *,
    source: Mapping[str, Any] | None = None,
    route: Mapping[str, Any] | str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Compose a persistence-ready, body-free Notification projection."""

    raw = _merge_mapping_input(payload, fields, "notification_payload")
    source_value = source if source is not None else _first_value(raw, "source", required=True)
    route_value = route if route is not None else _first_value(raw, "route", required=True)
    if not isinstance(source_value, Mapping):
        raise _invalid("source must be an object")
    source_projected = project_notification_source(source_value)
    route_projected = project_notification_route(route_value)

    category = _first_value(raw, "category")
    if category is None:
        category = source_projected["category"]
    if category not in _ALLOWED_CATEGORIES or category != source_projected["category"]:
        raise _invalid("category does not match source")
    severity = _first_value(raw, "severity")
    if severity is None:
        severity = source_projected["severity"]
    if severity not in _ALLOWED_SEVERITIES or severity != source_projected["severity"]:
        raise _invalid("severity does not match source")

    expected_route = (
        "knowledge_quality_operations"
        if source_projected["source_kind"] == "quality_alert"
        else "enterprise_approval"
    )
    if route_projected["target_route_code"] != expected_route:
        raise _invalid("route code does not match source_kind")
    route_params = route_projected["target_route_params_json"]
    if route_params["tenant_id"] != source_projected["tenant_id"]:
        raise _invalid("route tenant_id does not match source")
    if expected_route == "knowledge_quality_operations":
        if route_params["dataset_id"] != source_projected["source_dataset_id"]:
            raise _invalid("route dataset_id does not match source")
        if route_params["alert_id"] != source_projected["source_id"]:
            raise _invalid("route alert_id does not match source")
        for fact_key in ("release_id", "channel_id"):
            fact_value = source_projected["safe_facts"].get(fact_key)
            if fact_value is not None and route_params[fact_key] != fact_value:
                raise _invalid(f"route {fact_key} does not match source")
    elif route_params["approval_request_id"] != source_projected["source_id"]:
        raise _invalid("route approval_request_id does not match source")

    action_required = _exact_boolean(
        _first_value(raw, "action_required", required=True), "action_required"
    )
    mandatory = _exact_boolean(_first_value(raw, "mandatory", required=True), "mandatory")
    title_code = _safe_code(
        _first_value(raw, "title_code", required=True), "title_code", maximum=64
    )
    summary_code = _safe_code(
        _first_value(raw, "summary_code", required=True), "summary_code", maximum=64
    )
    occurred_at = _first_value(raw, "occurred_at", "at")
    if occurred_at is None:
        occurred_at = source_projected.get("occurred_at")
    if occurred_at is None:
        raise _invalid("occurred_at is required")
    occurred_at = _iso_utc(occurred_at, "occurred_at")

    semantic = _first_value(raw, "event_semantic", "event_type")
    if semantic is None:
        semantic = source_projected["event_semantic"]
    semantic = _safe_code(semantic, "event_type")
    source_revision = source_projected["source_revision"]
    cycle_key = source_projected.get("cycle_key")
    notification_key = canonical_notification_key(
        source_kind=source_projected["source_kind"],
        source_id=source_projected["source_id"],
        source_revision=source_revision if cycle_key is None else None,
        cycle_key=cycle_key,
        event_semantic=semantic,
    )
    supplied_key = _first_value(raw, "notification_key")
    if supplied_key is not None and supplied_key != notification_key:
        raise _invalid("notification_key does not match canonical identity")

    return {
        "tenant_id": source_projected["tenant_id"],
        "source_kind": source_projected["source_kind"],
        "source_id": source_projected["source_id"],
        "source_revision": source_revision,
        "source_dataset_id": source_projected["source_dataset_id"],
        "category": category,
        "severity": severity,
        "action_required": action_required,
        "mandatory": mandatory,
        "notification_key": notification_key,
        "source_digest": source_projected["source_digest"],
        "title_code": title_code,
        "summary_code": summary_code,
        "safe_facts_json": source_projected["safe_facts"],
        "target_route_code": route_projected["target_route_code"],
        "target_route_params_json": route_projected["target_route_params_json"],
        "occurred_at": occurred_at,
    }


def canonical_assignment_digest(
    assignment: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> str:
    """Return the deterministic digest for one frozen recipient assignment."""

    raw = _merge_mapping_input(assignment, fields, "notification_assignment")
    allowed = {
        "tenant_id",
        "notification_id",
        "account_id",
        "recipient_reason",
        "reason",
        "mandatory",
        "assigned_at",
    }
    if set(raw) - allowed:
        raise _invalid("notification assignment contains unsupported fields")
    reason = _first_value(raw, "recipient_reason", "reason", required=True)
    if reason not in _ALLOWED_RECIPIENT_REASONS:
        raise _invalid("recipient_reason is not allowed")
    mandatory = _exact_boolean(_first_value(raw, "mandatory", required=True), "mandatory")
    canonical: dict[str, Any] = {
        "tenant_id": _safe_component(
            _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
        ),
        "notification_id": _safe_component(
            _first_value(raw, "notification_id", required=True), "notification_id"
        ),
        "account_id": _safe_component(_first_value(raw, "account_id", required=True), "account_id"),
        "recipient_reason": reason,
        "mandatory": mandatory,
    }
    assigned_at = _first_value(raw, "assigned_at")
    if assigned_at is not None:
        canonical["assigned_at"] = _iso_utc(assigned_at, "assigned_at")
    return canonical_notification_digest("notification-assignment", canonical)


def canonical_notification_event(
    event: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> dict[str, Any]:
    """Canonicalize one immutable receipt event and extend its hash chain."""

    raw = _merge_mapping_input(event, fields, "notification_event")
    aliases = {
        "previous_digest": "previous_event_digest",
        "snapshot": "safe_snapshot",
        "safe_snapshot_json": "safe_snapshot",
        "digest": "event_digest",
        "kind": "event_type",
    }
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical_key = aliases.get(key, key)
        if canonical_key in normalized and normalized[canonical_key] != value:
            raise _invalid(f"notification_event.{canonical_key} has conflicting aliases")
        normalized[canonical_key] = value
    allowed = {
        "tenant_id",
        "notification_id",
        "account_id",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot",
        "occurred_at",
    }
    if set(normalized) - allowed:
        raise _invalid("notification event contains unsupported fields")

    tenant_id = _safe_component(
        _first_value(normalized, "tenant_id", required=True), "tenant_id", maximum=64
    )
    notification_id = _safe_component(
        _first_value(normalized, "notification_id", required=True), "notification_id"
    )
    account_id = _safe_component(
        _first_value(normalized, "account_id", required=True), "account_id"
    )
    sequence = _exact_integer(
        _first_value(normalized, "sequence", required=True), "sequence", minimum=1
    )
    event_type = _first_value(normalized, "event_type", required=True)
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise _invalid("event_type is not allowed")
    previous = _first_value(normalized, "previous_event_digest")
    if sequence == 1:
        if previous is not None:
            raise _invalid("sequence 1 must not have a previous event digest")
    else:
        previous = _sha256(previous, "previous_event_digest")
    actor_id = _safe_text(
        _first_value(normalized, "actor_id", required=True), "actor_id", maximum=64
    )
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", actor_id) is None:
        raise _invalid("actor_id contains unsupported identity characters")
    request_id = _safe_component(
        _first_value(normalized, "request_id", required=True), "request_id", maximum=128
    )
    snapshot = _first_value(normalized, "safe_snapshot", required=True)
    if not isinstance(snapshot, Mapping):
        raise _invalid("safe_snapshot must be an object")
    snapshot = _canonical_value(snapshot, path="notification_event.safe_snapshot")
    assert isinstance(snapshot, dict)
    occurred_at = _iso_utc(_first_value(normalized, "occurred_at", required=True), "occurred_at")
    canonical: dict[str, Any] = {
        "tenant_id": tenant_id,
        "notification_id": notification_id,
        "account_id": account_id,
        "sequence": sequence,
        "event_type": event_type,
        "previous_event_digest": previous,
        "actor_id": actor_id,
        "request_id": request_id,
        "safe_snapshot": snapshot,
        "occurred_at": occurred_at,
    }
    computed = canonical_notification_digest("notification-event", canonical)
    supplied = _first_value(normalized, "event_digest")
    if supplied is not None:
        supplied = _sha256(supplied, "event_digest")
        if supplied != computed:
            raise _invalid("event_digest does not match canonical event")
    canonical["event_digest"] = computed
    return canonical


__all__ = [
    "NOTIFICATION_CENTER_SCHEMA_VERSION",
    "NotificationAuthorityConflict",
    "NotificationAuthorityError",
    "NotificationAuthorityInvalid",
    "NotificationCenterError",
    "NotificationCenterInvalid",
    "EnterpriseNotificationError",
    "EnterpriseNotificationInvalid",
    "canonical_assignment_digest",
    "canonical_notification_digest",
    "canonical_notification_event",
    "canonical_notification_key",
    "project_notification_payload",
    "project_notification_route",
    "project_notification_source",
]
