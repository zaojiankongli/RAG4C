"""Pure canonical authority for Stage 26 knowledge-serving reliability.

No ORM, database, HTTP, or dynamic dispatch is imported here.  This module is
the single deterministic authority for bounded values, five-stage state, route
projection, and domain-separated digests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any
from urllib.parse import quote

SERVING_STAGE_CODES: tuple[str, ...] = ("source", "parse", "chunk", "index", "serve")
SERVING_STAGE_STATES = {"ready", "lagging", "blocked", "missing", "unavailable"}
SERVING_OVERALL_STATES = {"ready", "degraded", "blocked", "unavailable"}
SERVING_PROFILE_STATUSES = {"draft", "active", "paused", "archived"}
EVIDENCE_KINDS = {
    "source",
    "source_sync_run",
    "document",
    "ingest_attempt",
    "chunk_head",
    "index_operation",
    "release",
    "certification",
    "task",
}
SERVING_EVENT_TYPES = {
    "profile_created",
    "policy_revision_created",
    "policy_activated",
    "snapshot_recorded",
    "stage_degraded",
    "stage_blocked",
    "service_recovered",
}

_STAGE_SEQUENCE = {code: index for index, code in enumerate(SERVING_STAGE_CODES, start=1)}
_PROFILE_KEYS = {
    "id",
    "tenant_id",
    "workspace_id",
    "dataset_id",
    "name",
    "normalized_name",
    "status",
    "active_profile_key",
    "revision",
    "current_policy_revision_id",
    "current_snapshot_id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "archived_at",
    "archived_by",
}
_POLICY_KEYS = {
    "id",
    "tenant_id",
    "profile_id",
    "revision",
    "max_source_staleness_seconds",
    "max_parse_lag_seconds",
    "max_index_lag_seconds",
    "max_failed_document_count",
    "max_pending_index_count",
    "require_current_release",
    "require_passing_certification",
    "policy_digest",
    "created_at",
    "created_by",
}
_STAGE_KEYS = {
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stage_code",
    "sequence",
    "state",
    "item_count",
    "ready_count",
    "warning_count",
    "pending_count",
    "error_count",
    "lag_seconds",
    "expected_revision",
    "observed_revision",
    "expected_digest",
    "observed_digest",
    "safe_error_code",
    "safe_error",
    "stage_digest",
    "observed_at",
}
_SNAPSHOT_KEYS = {
    "id",
    "tenant_id",
    "profile_id",
    "policy_revision_id",
    "observation_key",
    "state",
    "source_count",
    "ready_source_count",
    "stale_source_count",
    "active_document_count",
    "failed_document_count",
    "pending_index_count",
    "expected_serving_generation",
    "observed_serving_generation",
    "current_release_id",
    "current_certification_id",
    "stage_count",
    "ready_stage_count",
    "blocked_stage_count",
    "snapshot_digest",
    "as_of",
    "created_at",
    "created_by",
}
_EVIDENCE_KEYS = {
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stage_fact_id",
    "evidence_kind",
    "resource_id",
    "resource_revision",
    "resource_digest",
    "route_code",
    "safe_label",
    "evidence_digest",
    "created_at",
}
_EVENT_KEYS = {
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stream_key",
    "sequence",
    "event_type",
    "previous_event_digest",
    "event_digest",
    "actor_id",
    "request_id",
    "safe_snapshot_json",
    "occurred_at",
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URI_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|(?:^|\s)www\.)\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])"
)
_SQL_RE = re.compile(
    r"(?is)(?:\bselect\s+(?:distinct\s+)?[\w*\"`'([]|\binsert\s+(?:into\s+)?[\w\"`'(]|"
    r"\bupdate\s+[\w\"`.]+\s+set\b|\bdelete\s+from\b|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|"
    r"\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|"
    r"\bgrant\s+\w+\s+on\b|\brevoke\s+\w+\s+on\b|\bexec(?:ute)?\s+\S+|\bunion\s+(?:all\s+)?select\b|"
    r"\b(?:select|insert|update|delete|drop|alter|create|grant|revoke|exec(?:ute)?|union)\s*$)"
)
_UNSAFE_KEY_PARTS = {
    "query",
    "url",
    "uri",
    "href",
    "sql",
    "webhook",
    "script",
    "expression",
    "eval",
    "exec",
    "prompt",
    "body",
    "content",
    "raw",
    "payload",
    "token",
    "credential",
    "ticket",
    "password",
    "secret",
    "authorization",
    "bearer",
    "cookie",
    "header",
    "answer",
    "apikey",
    "accesskey",
}
_MAX_SAFE_DEPTH = 12
_MAX_SAFE_ITEMS = 64
_MAX_SAFE_UTF8_BYTES = 16_384
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class KnowledgeServingAuthorityError(ValueError):
    """Base error for invalid or unverifiable Stage26 facts."""

    code = "knowledge_serving_authority_invalid"


class KnowledgeServingAuthorityInvalid(KnowledgeServingAuthorityError):
    """Raised when a canonical Stage26 value cannot be proven safe."""


ServingAuthorityError = KnowledgeServingAuthorityError
ServingAuthorityInvalid = KnowledgeServingAuthorityInvalid


def _fail(message: str) -> None:
    raise KnowledgeServingAuthorityInvalid(message)


def _required_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    unknown = set(value) - expected
    if unknown:
        _fail(f"{field} contains unknown fields: {', '.join(sorted(map(str, unknown)))}")


def _id(value: Any, field: str, *, optional: bool = False, maximum: int = 128) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        _fail(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _ID_RE.fullmatch(result) is None:
        _fail(f"{field} is not a safe identifier")
    return result


def _resource_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        _fail(f"{field} must be a string")
    result = value.strip()
    if (
        not result
        or len(result) > 128
        or _CONTROL_RE.search(result)
        or _URI_RE.search(result)
        or _BEARER_RE.search(result)
        or _JWT_RE.search(result)
        or re.fullmatch(r"[^\s]{1,128}", result) is None
    ):
        _fail(f"{field} is not a safe resource identifier")
    return result


def _code(value: Any, field: str, allowed: set[str] | None = None) -> str:
    if not isinstance(value, str):
        _fail(f"{field} must be a string")
    result = value.strip()
    if _CODE_RE.fullmatch(result) is None:
        _fail(f"{field} is not a safe code")
    if allowed is not None and result not in allowed:
        _fail(f"{field} is not allow-listed")
    return result


def _int(value: Any, field: str, *, minimum: int = 0, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < minimum:
        _fail(f"{field} must be a nonnegative integer" if minimum == 0 else f"{field} is invalid")
    if value > _MAX_SAFE_INTEGER:
        _fail(f"{field} exceeds cross-language safe integer precision")
    return value


def _bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        _fail(f"{field} must be a boolean")
    return value


def _digest_value(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _safe_text(value: Any, field: str, *, maximum: int = 512, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        _fail(f"{field} must not be empty")
    if len(result) > maximum or _CONTROL_RE.search(result):
        _fail(f"{field} is unsafe or too long")
    if (
        _URI_RE.search(result)
        or _BEARER_RE.search(result)
        or _JWT_RE.search(result)
        or _SQL_RE.search(result)
    ):
        _fail(f"{field} contains forbidden or unsafe content")
    return result


def _timestamp(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise KnowledgeServingAuthorityInvalid(f"{field} is not ISO-8601") from exc
    else:
        _fail(f"{field} must be a datetime or ISO-8601 string")
    if moment.tzinfo is None or moment.utcoffset() is None:
        _fail(f"{field} must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _camel_parts(value: str) -> tuple[str, ...]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", expanded)
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", expanded.casefold()) if part)


def _safe_key(key: Any) -> str:
    if not isinstance(key, str) or not key.strip() or len(key) > 96:
        _fail("safe mapping contains an unsafe key")
    normalized = key.strip()
    parts = _camel_parts(normalized)
    compact = re.sub(r"[^a-z0-9]", "", normalized.casefold())
    if (
        any(part in _UNSAFE_KEY_PARTS for part in parts)
        or compact in {"apikey", "accesskey"}
        or tuple(parts[-2:]) in {("api", "key"), ("access", "key")}
    ):
        _fail(f"safe mapping key is forbidden: {normalized}")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", normalized) is None:
        _fail(f"safe mapping key is invalid: {normalized}")
    return normalized


def _canonical_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): normalize(item[key]) for key in sorted(item, key=lambda x: str(x))}
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        return item

    try:
        return json.dumps(
            normalize(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise KnowledgeServingAuthorityInvalid("value is not canonically serializable") from exc


def _safe_value(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> Any:
    counter = counter if counter is not None else [0]
    if depth > _MAX_SAFE_DEPTH:
        _fail("safe mapping exceeds maximum depth")
    counter[0] += 1
    if counter[0] > _MAX_SAFE_ITEMS:
        _fail("safe mapping exceeds maximum item count")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if abs(value) > _MAX_SAFE_INTEGER:
            _fail("safe mapping integer exceeds cross-language safe integer precision")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("safe mapping contains a non-finite number")
        _fail("safe mapping does not allow floating point values")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            _fail("safe mapping value must not be empty")
        if len(text.encode("utf-8")) > _MAX_SAFE_UTF8_BYTES:
            _fail("safe mapping value exceeds UTF-8 byte limit")
        if (
            _CONTROL_RE.search(text)
            or _URI_RE.search(text)
            or _BEARER_RE.search(text)
            or _JWT_RE.search(text)
            or _SQL_RE.search(text)
        ):
            _fail("safe mapping value contains forbidden or unsafe content")
        return text
    if isinstance(value, Mapping):
        result = {
            _safe_key(key): _safe_value(value[key], depth=depth + 1, counter=counter)
            for key in sorted(value, key=lambda x: str(x))
        }
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        result = [_safe_value(item, depth=depth + 1, counter=counter) for item in value]
    else:
        _fail("safe mapping contains an unsupported value")
    if len(_canonical_json(result).encode("utf-8")) > _MAX_SAFE_UTF8_BYTES:
        _fail("safe mapping exceeds UTF-8 byte limit")
    return result


def canonical_safe_mapping(value: Any) -> dict[str, Any]:
    """Return a deterministic, bounded mapping safe for durable evidence."""

    result = _safe_value(_required_mapping(value, "safe mapping"))
    if not isinstance(result, dict):
        _fail("safe mapping must be an object")
    return result


def _domain_digest(domain: str, value: Mapping[str, Any]) -> str:
    encoded = _canonical_json(value).encode("utf-8")
    prefix = f"rag4c:knowledge-serving:{domain}:v1\x00".encode("utf-8")
    return sha256(prefix + len(encoded).to_bytes(8, "big") + encoded).hexdigest()


def _set_or_verify_digest(
    result: dict[str, Any], field: str, domain: str, *, payload: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    supplied = result.get(field)
    payload = dict(result) if payload is None else dict(payload)
    payload.pop(field, None)
    expected = _domain_digest(domain, payload)
    if supplied is not None:
        _digest_value(supplied, field)
        if supplied != expected:
            _fail(f"{field} does not match canonical {domain} digest")
    result[field] = expected
    return result


def canonical_serving_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize operator-owned profile metadata."""

    item = _required_mapping(value, "profile")
    _keys(item, _PROFILE_KEYS, "profile")
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "profile.id"),
        "tenant_id": _id(item.get("tenant_id"), "profile.tenant_id", maximum=64),
        "workspace_id": _id(item.get("workspace_id"), "profile.workspace_id", optional=True),
        "dataset_id": _id(item.get("dataset_id"), "profile.dataset_id", maximum=64),
        "name": _safe_text(item.get("name"), "profile.name", maximum=128),
        "normalized_name": _safe_text(
            item.get("normalized_name", str(item.get("name", "")).casefold()),
            "profile.normalized_name",
            maximum=128,
        ),
        "status": _code(item.get("status"), "profile.status", SERVING_PROFILE_STATUSES),
        "active_profile_key": _id(
            item.get("active_profile_key"), "profile.active_profile_key", optional=True
        ),
        "revision": _int(item.get("revision"), "profile.revision", minimum=1),
        "current_policy_revision_id": _id(
            item.get("current_policy_revision_id"),
            "profile.current_policy_revision_id",
            optional=True,
        ),
        "current_snapshot_id": _id(
            item.get("current_snapshot_id"), "profile.current_snapshot_id", optional=True
        ),
        "created_at": _timestamp(item.get("created_at"), "profile.created_at"),
        "created_by": _id(item.get("created_by"), "profile.created_by"),
        "updated_at": _timestamp(item.get("updated_at"), "profile.updated_at"),
        "updated_by": _id(item.get("updated_by"), "profile.updated_by"),
        "archived_at": _timestamp(item.get("archived_at"), "profile.archived_at", optional=True),
        "archived_by": _id(item.get("archived_by"), "profile.archived_by", optional=True),
    }
    if (
        result["active_profile_key"] is not None
        and result["active_profile_key"] != result["dataset_id"]
    ):
        _fail("profile.active_profile_key must equal dataset_id")
    if result["status"] == "active" and (
        result["active_profile_key"] != result["dataset_id"]
        or result["current_policy_revision_id"] is None
    ):
        _fail("active profile requires active_profile_key and current policy")
    if result["status"] == "archived" and result["active_profile_key"] is not None:
        _fail("archived profile cannot retain active_profile_key")
    return result


def canonical_knowledge_serving_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one immutable bounded evaluation policy revision."""

    item = _required_mapping(value, "policy")
    _keys(item, _POLICY_KEYS, "policy")
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "policy.id", optional=True),
        "tenant_id": _id(item.get("tenant_id"), "policy.tenant_id", maximum=64),
        "profile_id": _id(item.get("profile_id"), "policy.profile_id"),
        "revision": _int(item.get("revision"), "policy.revision", minimum=1),
        "max_source_staleness_seconds": _int(
            item.get("max_source_staleness_seconds"), "policy.max_source_staleness_seconds"
        ),
        "max_parse_lag_seconds": _int(
            item.get("max_parse_lag_seconds"), "policy.max_parse_lag_seconds"
        ),
        "max_index_lag_seconds": _int(
            item.get("max_index_lag_seconds"), "policy.max_index_lag_seconds"
        ),
        "max_failed_document_count": _int(
            item.get("max_failed_document_count"), "policy.max_failed_document_count"
        ),
        "max_pending_index_count": _int(
            item.get("max_pending_index_count"), "policy.max_pending_index_count"
        ),
        "require_current_release": _bool(
            item.get("require_current_release"), "policy.require_current_release"
        ),
        "require_passing_certification": _bool(
            item.get("require_passing_certification"), "policy.require_passing_certification"
        ),
        "policy_digest": item.get("policy_digest"),
        "created_at": _timestamp(item.get("created_at"), "policy.created_at", optional=True),
        "created_by": _id(item.get("created_by"), "policy.created_by", optional=True),
    }
    return _set_or_verify_digest(result, "policy_digest", "policy")


def canonical_knowledge_serving_policy_digest(value: Mapping[str, Any]) -> str:
    return canonical_knowledge_serving_policy(value)["policy_digest"]


def canonical_knowledge_serving_stage_fact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one of the exactly five immutable stage facts."""

    item = _required_mapping(value, "stage fact")
    _keys(item, _STAGE_KEYS, "stage fact")
    code = _code(item.get("stage_code"), "stage_fact.stage_code", set(SERVING_STAGE_CODES))
    sequence = _int(item.get("sequence"), "stage_fact.sequence", minimum=1)
    if sequence != _STAGE_SEQUENCE[code]:
        _fail("stage fact sequence does not match exact five-stage ordering")
    error_code = item.get("safe_error_code")
    if error_code is not None:
        error_code = _code(error_code, "stage_fact.safe_error_code")
    error_text = item.get("safe_error")
    if error_text is not None:
        error_text = _safe_text(error_text, "stage_fact.safe_error", maximum=512)
    if (error_code is None) != (error_text is None):
        _fail("stage fact safe error code and message must be paired")
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "stage_fact.id", optional=True),
        "tenant_id": _id(item.get("tenant_id"), "stage_fact.tenant_id", maximum=64),
        "profile_id": _id(item.get("profile_id"), "stage_fact.profile_id"),
        "snapshot_id": _id(item.get("snapshot_id"), "stage_fact.snapshot_id"),
        "stage_code": code,
        "sequence": sequence,
        "state": _code(item.get("state"), "stage_fact.state", set(SERVING_STAGE_STATES)),
        "item_count": _int(item.get("item_count"), "stage_fact.item_count"),
        "ready_count": _int(item.get("ready_count"), "stage_fact.ready_count"),
        "warning_count": _int(item.get("warning_count"), "stage_fact.warning_count"),
        "pending_count": _int(item.get("pending_count"), "stage_fact.pending_count"),
        "error_count": _int(item.get("error_count"), "stage_fact.error_count"),
        "lag_seconds": _int(item.get("lag_seconds"), "stage_fact.lag_seconds"),
        "expected_revision": _int(
            item.get("expected_revision"), "stage_fact.expected_revision", optional=True
        ),
        "observed_revision": _int(
            item.get("observed_revision"), "stage_fact.observed_revision", optional=True
        ),
        "expected_digest": _digest_value(
            item.get("expected_digest"), "stage_fact.expected_digest", optional=True
        ),
        "observed_digest": _digest_value(
            item.get("observed_digest"), "stage_fact.observed_digest", optional=True
        ),
        "safe_error_code": error_code,
        "safe_error": error_text,
        "stage_digest": item.get("stage_digest"),
        "observed_at": _timestamp(item.get("observed_at"), "stage_fact.observed_at"),
    }
    if any(
        result[field] > result["item_count"]
        for field in ("ready_count", "warning_count", "pending_count", "error_count")
    ):
        _fail("stage fact derived counters cannot exceed item_count")
    return _set_or_verify_digest(result, "stage_digest", "stage-fact")


def canonical_knowledge_serving_stage_digest(value: Mapping[str, Any]) -> str:
    return canonical_knowledge_serving_stage_fact(value)["stage_digest"]


def derive_knowledge_serving_state(stage_facts: Sequence[Mapping[str, Any]]) -> str:
    """Derive overall state from exactly five canonical facts, never caller state."""

    if not isinstance(stage_facts, Sequence) or isinstance(stage_facts, (str, bytes, bytearray)):
        _fail("stage facts must be a sequence")
    if len(stage_facts) != 5:
        _fail("serving snapshot requires exactly five stage facts")
    facts = [canonical_knowledge_serving_stage_fact(item) for item in stage_facts]
    facts.sort(key=lambda item: item["sequence"])
    if [item["stage_code"] for item in facts] != list(SERVING_STAGE_CODES):
        _fail("stage facts do not cover the exact five stages")
    states = [item["state"] for item in facts]
    if any(state in {"missing", "unavailable"} for state in states):
        return "unavailable"
    if "blocked" in states:
        return "blocked"
    if "lagging" in states:
        return "degraded"
    return "ready"


def canonical_knowledge_serving_snapshot(
    value: Mapping[str, Any],
    *,
    stage_facts: Sequence[Mapping[str, Any]] | None = None,
    evidence_links: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Canonicalize a snapshot and derive state/counters from stage facts."""

    item = _required_mapping(value, "snapshot")
    _keys(item, _SNAPSHOT_KEYS, "snapshot")
    if stage_facts is None:
        _fail("snapshot stage facts are required for canonical derivation")
    if evidence_links is None:
        _fail("snapshot evidence links are required for canonical derivation")
    if len(stage_facts) != 5:
        _fail("snapshot requires exactly five stage facts")
    facts = [canonical_knowledge_serving_stage_fact(fact) for fact in stage_facts]
    facts.sort(key=lambda fact: fact["sequence"])
    if [fact["stage_code"] for fact in facts] != list(SERVING_STAGE_CODES):
        _fail("snapshot stage facts do not match exact five-stage ordering")
    evidence = [canonical_knowledge_serving_evidence_link(link) for link in evidence_links]
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "snapshot.id", optional=True),
        "tenant_id": _id(item.get("tenant_id"), "snapshot.tenant_id", maximum=64),
        "profile_id": _id(item.get("profile_id"), "snapshot.profile_id"),
        "policy_revision_id": _id(item.get("policy_revision_id"), "snapshot.policy_revision_id"),
        "observation_key": _id(
            item.get("observation_key"), "snapshot.observation_key", maximum=192
        ),
        "state": item.get("state"),
        "source_count": _int(item.get("source_count"), "snapshot.source_count"),
        "ready_source_count": _int(item.get("ready_source_count"), "snapshot.ready_source_count"),
        "stale_source_count": _int(item.get("stale_source_count"), "snapshot.stale_source_count"),
        "active_document_count": _int(
            item.get("active_document_count"), "snapshot.active_document_count"
        ),
        "failed_document_count": _int(
            item.get("failed_document_count"), "snapshot.failed_document_count"
        ),
        "pending_index_count": _int(
            item.get("pending_index_count"), "snapshot.pending_index_count"
        ),
        "expected_serving_generation": _int(
            item.get("expected_serving_generation"), "snapshot.expected_serving_generation"
        ),
        "observed_serving_generation": _int(
            item.get("observed_serving_generation"), "snapshot.observed_serving_generation"
        ),
        "current_release_id": _id(
            item.get("current_release_id"), "snapshot.current_release_id", optional=True
        ),
        "current_certification_id": _id(
            item.get("current_certification_id"), "snapshot.current_certification_id", optional=True
        ),
        "stage_count": item.get("stage_count"),
        "ready_stage_count": item.get("ready_stage_count"),
        "blocked_stage_count": item.get("blocked_stage_count"),
        "snapshot_digest": item.get("snapshot_digest"),
        "as_of": _timestamp(item.get("as_of"), "snapshot.as_of"),
        "created_at": _timestamp(item.get("created_at"), "snapshot.created_at"),
        "created_by": _id(item.get("created_by"), "snapshot.created_by"),
    }
    if (
        result["ready_source_count"] > result["source_count"]
        or result["stale_source_count"] > result["source_count"]
    ):
        _fail("snapshot source counters are inconsistent")
    for fact in facts:
        if (
            fact["tenant_id"] != result["tenant_id"]
            or fact["profile_id"] != result["profile_id"]
            or fact["snapshot_id"] != result["id"]
        ):
            _fail("snapshot stage fact identity does not match snapshot ownership")
    stage_fact_ids = {fact["id"] for fact in facts if fact["id"] is not None}
    for link in evidence:
        if (
            link["tenant_id"] != result["tenant_id"]
            or link["profile_id"] != result["profile_id"]
            or link["snapshot_id"] != result["id"]
            or link["stage_fact_id"] not in stage_fact_ids
        ):
            _fail("snapshot evidence ownership does not match an owned stage fact")
    evidence.sort(
        key=lambda link: (
            link["stage_fact_id"],
            link["evidence_kind"],
            link["resource_id"],
            link["id"] or "",
        )
    )
    derived = derive_knowledge_serving_state(facts)
    by_code = {fact["stage_code"]: fact for fact in facts}
    source = by_code["source"]
    parse = by_code["parse"]
    index = by_code["index"]
    serve = by_code["serve"]
    expected = {
        "state": derived,
        "source_count": source["item_count"],
        "ready_source_count": source["ready_count"],
        "stale_source_count": source["warning_count"],
        "active_document_count": parse["item_count"],
        "failed_document_count": parse["warning_count"],
        "pending_index_count": index["pending_count"],
        "expected_serving_generation": serve["expected_revision"] or 0,
        "observed_serving_generation": serve["observed_revision"] or 0,
        "stage_count": 5,
        "ready_stage_count": sum(fact["state"] == "ready" for fact in facts),
        "blocked_stage_count": sum(fact["state"] == "blocked" for fact in facts),
    }
    for field, expected_value in expected.items():
        if result[field] is not None and result[field] != expected_value:
            _fail(f"snapshot {field} is not derived from stage facts")
        result[field] = expected_value
    payload = dict(result)
    payload.pop("snapshot_digest")
    payload["stage_fact_digests"] = [fact["stage_digest"] for fact in facts]
    payload["evidence_digests"] = [link["evidence_digest"] for link in evidence]
    return _set_or_verify_digest(result, "snapshot_digest", "snapshot", payload=payload)


def canonical_knowledge_serving_snapshot_digest(
    value: Mapping[str, Any],
    *,
    stage_facts: Sequence[Mapping[str, Any]] | None = None,
    evidence_links: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    return canonical_knowledge_serving_snapshot(
        value, stage_facts=stage_facts, evidence_links=evidence_links
    )["snapshot_digest"]


_ROUTE_CATALOG: dict[str, tuple[str, str, str]] = {
    "source": ("knowledge_sources", "/enterprise/knowledge-base", "source"),
    "source_sync_run": ("knowledge_sources", "/enterprise/knowledge-base", "sync"),
    "document": ("knowledge_documents", "/enterprise/knowledge-base", "document"),
    "ingest_attempt": ("knowledge_documents", "/enterprise/knowledge-base", "document"),
    "chunk_head": ("knowledge_documents", "/enterprise/knowledge-base", "document"),
    "index_operation": ("knowledge_indexing", "/enterprise/tasks", "operation"),
    "release": ("knowledge_base_releases", "/enterprise/knowledge-base", "release"),
    "certification": ("release_quality", "/enterprise/knowledge-base", "certification"),
    "task": ("enterprise_tasks", "/enterprise/tasks", "task"),
}


def canonical_knowledge_serving_evidence_link(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one bounded evidence reference; raw URLs are impossible."""

    item = _required_mapping(value, "evidence link")
    _keys(item, _EVIDENCE_KEYS, "evidence link")
    kind = _code(item.get("evidence_kind"), "evidence.evidence_kind", set(EVIDENCE_KINDS))
    route_code, _, _ = _ROUTE_CATALOG[kind]
    supplied_route = _code(item.get("route_code"), "evidence.route_code")
    if supplied_route != route_code:
        _fail("evidence route is not the allow-listed route for evidence kind")
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "evidence.id", optional=True),
        "tenant_id": _id(item.get("tenant_id"), "evidence.tenant_id", maximum=64),
        "profile_id": _id(item.get("profile_id"), "evidence.profile_id"),
        "snapshot_id": _id(item.get("snapshot_id"), "evidence.snapshot_id"),
        "stage_fact_id": _id(item.get("stage_fact_id"), "evidence.stage_fact_id"),
        "evidence_kind": kind,
        "resource_id": _resource_id(item.get("resource_id"), "evidence.resource_id"),
        "resource_revision": _int(
            item.get("resource_revision"), "evidence.resource_revision", minimum=1, optional=True
        ),
        "resource_digest": _digest_value(
            item.get("resource_digest"), "evidence.resource_digest", optional=True
        ),
        "route_code": supplied_route,
        "safe_label": _safe_text(item.get("safe_label"), "evidence.safe_label", maximum=256),
        "evidence_digest": item.get("evidence_digest"),
        "created_at": _timestamp(item.get("created_at"), "evidence.created_at", optional=True),
    }
    return _set_or_verify_digest(result, "evidence_digest", "evidence")


def canonical_knowledge_serving_evidence_digest(value: Mapping[str, Any]) -> str:
    return canonical_knowledge_serving_evidence_link(value)["evidence_digest"]


def project_knowledge_serving_route(value: Mapping[str, Any]) -> dict[str, str]:
    """Project a link to a fixed internal route with an encoded resource ID."""

    link = canonical_knowledge_serving_evidence_link(value)
    route_code, path, parameter = _ROUTE_CATALOG[link["evidence_kind"]]
    href = f"{path}?{parameter}={quote(link['resource_id'], safe='')}"
    return {"code": route_code, "path": path, "parameter": parameter, "href": href}


def canonical_knowledge_serving_event(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize one immutable per-profile/snapshot event-chain node."""

    item = _required_mapping(value, "serving event")
    _keys(item, _EVENT_KEYS, "serving event")
    sequence = _int(item.get("sequence"), "event.sequence", minimum=1)
    previous = _digest_value(
        item.get("previous_event_digest"), "event.previous_event_digest", optional=True
    )
    event_type = _code(item.get("event_type"), "event.event_type", set(SERVING_EVENT_TYPES))
    if sequence == 1 and previous is not None:
        _fail("first event must not have a previous digest")
    if sequence == 1 and event_type != "profile_created":
        _fail("first event must be profile_created")
    if sequence > 1 and previous is None:
        _fail("non-first event requires a previous digest")
    result: dict[str, Any] = {
        "id": _id(item.get("id"), "event.id", optional=True),
        "tenant_id": _id(item.get("tenant_id"), "event.tenant_id", maximum=64),
        "profile_id": _id(item.get("profile_id"), "event.profile_id"),
        "snapshot_id": _id(item.get("snapshot_id"), "event.snapshot_id", optional=True),
        "stream_key": _id(item.get("stream_key"), "event.stream_key", maximum=128),
        "sequence": sequence,
        "event_type": event_type,
        "previous_event_digest": previous,
        "event_digest": item.get("event_digest"),
        "actor_id": _id(item.get("actor_id"), "event.actor_id"),
        "request_id": _id(item.get("request_id"), "event.request_id", maximum=128),
        "safe_snapshot_json": canonical_safe_mapping(item.get("safe_snapshot_json")),
        "occurred_at": _timestamp(item.get("occurred_at"), "event.occurred_at"),
    }
    return _set_or_verify_digest(result, "event_digest", "event")


def canonical_knowledge_serving_event_digest(value: Mapping[str, Any]) -> str:
    return canonical_knowledge_serving_event(value)["event_digest"]


def project_knowledge_serving_snapshot_route(dataset_id: str) -> str:
    """Return the safe internal route for a dataset serving center."""

    identifier = _id(dataset_id, "dataset_id", maximum=64)
    return f"/enterprise/knowledge-base?dataset={quote(identifier or '', safe='')}&section=serving"


canonical_policy = canonical_knowledge_serving_policy
canonical_policy_digest = canonical_knowledge_serving_policy_digest
canonical_stage_fact = canonical_knowledge_serving_stage_fact
canonical_stage_digest = canonical_knowledge_serving_stage_digest
canonical_snapshot = canonical_knowledge_serving_snapshot
canonical_snapshot_digest = canonical_knowledge_serving_snapshot_digest
canonical_evidence_link = canonical_knowledge_serving_evidence_link
canonical_evidence_digest = canonical_knowledge_serving_evidence_digest
canonical_event = canonical_knowledge_serving_event
canonical_event_digest = canonical_knowledge_serving_event_digest
derive_serving_state = derive_knowledge_serving_state
project_serving_route = project_knowledge_serving_route

__all__ = [
    "EVIDENCE_KINDS",
    "KnowledgeServingAuthorityError",
    "KnowledgeServingAuthorityInvalid",
    "SERVING_EVENT_TYPES",
    "SERVING_OVERALL_STATES",
    "SERVING_PROFILE_STATUSES",
    "SERVING_STAGE_CODES",
    "SERVING_STAGE_STATES",
    "ServingAuthorityError",
    "ServingAuthorityInvalid",
    "canonical_evidence_digest",
    "canonical_evidence_link",
    "canonical_event",
    "canonical_event_digest",
    "canonical_knowledge_serving_evidence_digest",
    "canonical_knowledge_serving_evidence_link",
    "canonical_knowledge_serving_event",
    "canonical_knowledge_serving_event_digest",
    "canonical_knowledge_serving_policy",
    "canonical_knowledge_serving_policy_digest",
    "canonical_knowledge_serving_snapshot",
    "canonical_knowledge_serving_snapshot_digest",
    "canonical_knowledge_serving_stage_digest",
    "canonical_knowledge_serving_stage_fact",
    "canonical_policy",
    "canonical_policy_digest",
    "canonical_safe_mapping",
    "canonical_serving_profile",
    "canonical_snapshot",
    "canonical_snapshot_digest",
    "canonical_stage_digest",
    "canonical_stage_fact",
    "derive_knowledge_serving_state",
    "derive_serving_state",
    "project_knowledge_serving_route",
    "project_knowledge_serving_snapshot_route",
    "project_serving_route",
]
