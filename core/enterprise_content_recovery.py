"""Pure, Tenant-safe Content Recovery authority primitives.

This module is intentionally independent from SQLAlchemy, HTTP and the
physical deletion executor. It canonicalizes only the small, safe authority
facts that Stage 23 may persist: recycle identity, retention snapshots, purge
request snapshots, recovery events and server-owned navigation routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

UTC = timezone.utc
CONTENT_RECOVERY_SCHEMA_VERSION = 1
RECOVERY_SCHEMA_VERSION = CONTENT_RECOVERY_SCHEMA_VERSION

_ALLOWED_RECYCLE_STATUSES = frozenset(
    {"recycled", "restoring", "restored", "purge_requested", "purged", "failed"}
)
_ALLOWED_LIFECYCLE_STATES = frozenset({"active", "expired"})
_ALLOWED_PURGE_STATUSES = frozenset(
    {"pending_approval", "approved", "cancelled", "expired", "executed"}
)
_ALLOWED_EVENT_TYPES = frozenset(
    {
        "recycled",
        "restored",
        "hold_applied",
        "hold_released",
        "purge_requested",
        "purge_approved",
        "purge_cancelled",
    }
)
_ALLOWED_ROUTE_CODES = frozenset(
    {
        "enterprise_recycle_bin",
        "enterprise_content_recovery",
        "enterprise_recovery",
        "recycle_bin",
        "content_recovery",
        "enterprise_approval",
        "enterprise_approvals",
        "approval_center",
        "knowledge_documents",
        "enterprise_documents",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_EMAIL_RE = re.compile(r"(?i)(?<![\\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}(?![\\w.-])")
_URL_RE = re.compile(
    r"(?i)(?:https?|ftp|file|mailto|javascript|data):\\S+|"
    r"\\b(?:www\\.)\\S+|"
    r"\\b(?:mysql|mariadb|postgres(?:ql)?|mongodb|redis(?:s)?|sqlite):\\/\\/\\S+"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\\b(?:mysql(?:\\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\\+[a-z0-9_]+)?|"
    r"mongodb(?:\\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\\/\\/\\S+"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|"
    r"api[_ -]?key|client[_ -]?secret|id[_ -]?token|idempotency[_ -]?key|ticket|token)"
    r"\\s*[:=]\\s*\\S+|bearer\\s+\\S+|secret://\\S+|"
    r"(?:sk_(?:live|test)[-_]|ghp_|xox[baprs]-)\\S+|"
    r"[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}"
    r")"
)
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?i)\\b(?:select|insert|update|delete)\\b|"
    r"\\b(?:raw[ _-]+content|document[ _-]+body|raw[ _-]+metadata|metadata|"
    r"query|result[ _-]+body|reviewer[ _-]+note|judgment[ _-]+note|note|comment|"
    r"ticket|token|credential|password|authorization)\\b"
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
        "token",
        "body",
        "content",
        "metadata",
        "note",
        "notes",
        "comment",
        "comments",
        "query",
        "url",
        "uri",
        "href",
        "webhook",
        "cookie",
        "header",
        "prompt",
        "answer",
        "question",
        "result",
        "idempotency",
    }
)
_SAFE_DIGEST_KEYS = frozenset(
    {
        "event_digest",
        "previous_event_digest",
        "snapshot_digest",
        "request_digest",
        "idempotency_key_digest",
    }
)
_TIMESTAMP_KEY_PARTS = frozenset(
    {
        "at",
        "occurred_at",
        "created_at",
        "updated_at",
        "recycled_at",
        "purge_eligible_at",
        "restored_at",
        "purge_requested_at",
        "purged_at",
        "failed_at",
        "held_at",
        "released_at",
        "requested_at",
        "approved_at",
        "cancelled_at",
        "executed_at",
        "expires_at",
    }
)
_MAX_CANONICAL_DEPTH = 24
_MAX_CANONICAL_ITEMS = 256
_MAX_CANONICAL_STRING = 4096


class ContentRecoveryAuthorityError(ValueError):
    """Base error for malformed or unsafe Content Recovery authority."""

    code = "content_recovery_authority_invalid"
    status = 422

    def __init__(self, message: str = "Content Recovery authority is invalid") -> None:
        super().__init__(message)
        self.message = message


class ContentRecoveryAuthorityInvalid(ContentRecoveryAuthorityError):
    """Raised when a recovery value cannot be trusted or canonicalized."""


class ContentRecoveryAuthorityConflict(ContentRecoveryAuthorityInvalid):
    """Raised when two recovery values claim one canonical identity."""

    code = "content_recovery_authority_conflict"
    status = 409


ContentRecoveryError = ContentRecoveryAuthorityError
ContentRecoveryInvalid = ContentRecoveryAuthorityInvalid
ContentRecoveryConflict = ContentRecoveryAuthorityConflict
RecoveryAuthorityError = ContentRecoveryAuthorityError
RecoveryAuthorityInvalid = ContentRecoveryAuthorityInvalid
RecoveryAuthorityConflict = ContentRecoveryAuthorityConflict
EnterpriseContentRecoveryError = ContentRecoveryAuthorityError
EnterpriseContentRecoveryInvalid = ContentRecoveryAuthorityInvalid
EnterpriseContentRecoveryConflict = ContentRecoveryAuthorityConflict


def _invalid(message: str) -> ContentRecoveryAuthorityInvalid:
    return ContentRecoveryAuthorityInvalid(message)


def _is_forbidden_key(key: str) -> bool:
    if key in _SAFE_DIGEST_KEYS:
        return False
    parts = key.split("_")
    return (
        key in _FORBIDDEN_KEY_PARTS
        or any(part in _FORBIDDEN_KEY_PARTS for part in parts)
        or key.endswith(("_body", "_content", "_metadata", "_note", "_notes", "_comment"))
        or key.endswith(("_query", "_ticket", "_token", "_credential", "_secret", "_password"))
        or "ticket" in key
    )


def _normalized_key(raw_key: Any, path: str) -> str:
    if not isinstance(raw_key, str):
        raise _invalid(f"{path} object keys must be strings")
    key = raw_key.strip()
    if not key:
        raise _invalid(f"{path} object keys must not be empty")
    key = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
    key = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").casefold()
    if not key:
        raise _invalid(f"{path} object key is invalid")
    if _is_forbidden_key(key):
        raise _invalid(f"{path}.{key} is a forbidden field")
    return key


def _safe_text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise _invalid(f"{field} must not be empty")
    if len(result) > maximum:
        raise _invalid(f"{field} is too long")
    if any(ord(character) < 32 and character not in "\\t\\n\\r" for character in result):
        raise _invalid(f"{field} contains unsupported control characters")
    if (
        _URL_RE.search(result)
        or _DATABASE_URL_RE.search(result)
        or _EMAIL_RE.search(result)
        or _SECRET_VALUE_RE.search(result)
        or _FORBIDDEN_TEXT_RE.search(result)
    ):
        raise _invalid(f"{field} contains forbidden sensitive content")
    return result


def _safe_identifier(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum)
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise _invalid(f"{field} contains unsupported identity characters")
    return result


def _safe_actor(value: Any, field: str) -> str:
    result = _safe_text(value, field, maximum=128)
    if _ACTOR_RE.fullmatch(result) is None:
        raise _invalid(f"{field} contains unsupported actor characters")
    return result


def _safe_code(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum).casefold()
    if _CODE_RE.fullmatch(result) is None:
        raise _invalid(f"{field} must be a safe lowercase code")
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


def _sha256(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
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
    return key in _TIMESTAMP_KEY_PARTS or key.endswith(("_at", "_until", "_timestamp"))


def _canonical_value(
    value: Any,
    *,
    path: str,
    key_hint: str | None = None,
    depth: int = 0,
) -> Any:
    if depth > _MAX_CANONICAL_DEPTH:
        raise _invalid(f"{path} exceeds the canonical JSON depth limit")
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, datetime):
        return _iso_utc(value, path)
    if isinstance(value, str):
        return (
            _iso_utc(value, path)
            if key_hint and _is_timestamp_key(key_hint)
            else _safe_text(value, path, maximum=_MAX_CANONICAL_STRING)
        )
    if isinstance(value, float):
        raise _invalid(f"{path} must use an exact integer or boolean, not a float")
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_CANONICAL_ITEMS:
            raise _invalid(f"{path} has too many items")
        return [
            _canonical_value(item, path=f"{path}[{i}]", depth=depth + 1)
            for i, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        if len(value) > _MAX_CANONICAL_ITEMS:
            raise _invalid(f"{path} has too many fields")
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = _normalized_key(raw_key, path)
            if key in result:
                raise _invalid(f"{path} contains duplicate canonical keys")
            result[key] = _canonical_value(
                nested, path=f"{path}.{key}", key_hint=key, depth=depth + 1
            )
        return {key: result[key] for key in sorted(result)}
    raise _invalid(f"{path} contains an unsupported value")


def _canonical_json(value: Any, *, path: str) -> bytes:
    encoded = json.dumps(
        _canonical_value(value, path=path),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise _invalid(f"{path} exceeds the canonical JSON size limit")
    return encoded


def canonical_recovery_digest(namespace: str, value: Any) -> str:
    """Return a deterministic, domain-separated SHA-256 recovery digest."""

    label = _safe_code(namespace, "digest namespace", maximum=64)
    encoded = _canonical_json(value, path=f"recovery.{label}")
    digest = sha256()
    digest.update(b"rag4c:enterprise-content-recovery:v1\\x00")
    for payload in (label.encode("utf-8"), encoded):
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _normalized_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object")
    result: dict[str, Any] = {}
    for raw_key, nested in value.items():
        key = _normalized_key(raw_key, field)
        if key in result and result[key] != nested:
            raise _invalid(f"{field}.{key} has conflicting values")
        result[key] = nested
    return result


def _merge_mapping_input(
    primary: Mapping[str, Any] | None, fields: Mapping[str, Any], field: str
) -> dict[str, Any]:
    result = _normalized_mapping(primary, field) if primary is not None else {}
    for raw_key, value in fields.items():
        key = _normalized_key(raw_key, field)
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


def canonical_recycle_key(
    dataset_id: str | Mapping[str, Any] | None = None,
    document_id: str | None = None,
    **fields: Any,
) -> str:
    """Return the stable active recycle identity dataset_id:document_id."""

    raw: dict[str, Any] = {}
    if isinstance(dataset_id, Mapping):
        raw.update(_normalized_mapping(dataset_id, "recycle_key"))
    elif dataset_id is not None:
        raw["dataset_id"] = dataset_id
    if document_id is not None:
        raw["document_id"] = document_id
    raw = _merge_mapping_input(raw, fields, "recycle_key")
    aliases = {
        "dataset": "dataset_id",
        "document": "document_id",
        "active_key": "active_recycle_key",
    }
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = aliases.get(key, key)
        if canonical in normalized and normalized[canonical] != value:
            raise _invalid(f"recycle_key.{canonical} has conflicting aliases")
        normalized[canonical] = value
    allowed = {
        "tenant_id",
        "dataset_id",
        "document_id",
        "recycle_generation",
        "recycle_entry_id",
        "active_recycle_key",
    }
    if set(normalized) - allowed:
        raise _invalid("recycle key contains unsupported fields")
    if "tenant_id" in normalized:
        _safe_identifier(normalized["tenant_id"], "tenant_id")
    if "recycle_generation" in normalized:
        _exact_integer(normalized["recycle_generation"], "recycle_generation", minimum=1)
    if "recycle_entry_id" in normalized:
        _safe_identifier(normalized["recycle_entry_id"], "recycle_entry_id")
    dataset = _safe_identifier(_first_value(normalized, "dataset_id", required=True), "dataset_id")
    document = _safe_identifier(
        _first_value(normalized, "document_id", required=True), "document_id"
    )
    key = f"{dataset}:{document}"
    supplied = normalized.get("active_recycle_key")
    if supplied is not None and supplied != key:
        raise _invalid("active_recycle_key does not match canonical identity")
    return key


_SNAPSHOT_ALIASES = {
    "entry_id": "recycle_entry_id",
    "generation": "recycle_generation",
    "original_lifecycle": "original_lifecycle_state",
    "retrieval_enabled": "original_retrieval_enabled",
    "retention_days": "retention_days_snapshot",
}
_SNAPSHOT_ALLOWED = frozenset(
    {
        "tenant_id",
        "recycle_entry_id",
        "dataset_id",
        "document_id",
        "recycle_generation",
        "active_recycle_key",
        "status",
        "revision",
        "document_mutation_generation",
        "document_revision",
        "document_version_id",
        "dataset_revision",
        "original_lifecycle_state",
        "original_retrieval_enabled",
        "retention_days_snapshot",
        "recycled_at",
        "purge_eligible_at",
        "restored_at",
        "purge_requested_at",
        "purged_at",
        "failed_at",
        "created_at",
        "updated_at",
        "recycled_by",
        "restored_by",
        "purge_requested_by",
        "purged_by",
        "failed_by",
        "request_id",
        "approval_request_id",
        "legal_hold_count",
        "reason_code",
        "safe_reason",
        "snapshot_digest",
    }
)
_SNAPSHOT_TIME_FIELDS = frozenset(
    {"restored_at", "purge_requested_at", "purged_at", "failed_at", "created_at", "updated_at"}
)
_SNAPSHOT_ACTOR_FIELDS = frozenset(
    {"recycled_by", "restored_by", "purge_requested_by", "purged_by", "failed_by"}
)
_SNAPSHOT_ID_FIELDS = frozenset(
    {"recycle_entry_id", "document_version_id", "request_id", "approval_request_id"}
)


def canonical_recycle_snapshot(
    snapshot: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> dict[str, Any]:
    """Canonicalize the body-free snapshot stored with one recycle entry."""

    raw = _merge_mapping_input(snapshot, fields, "recycle_snapshot")
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = _SNAPSHOT_ALIASES.get(key, key)
        if canonical in normalized and normalized[canonical] != value:
            raise _invalid(f"recycle_snapshot.{canonical} has conflicting aliases")
        normalized[canonical] = value
    if set(normalized) - _SNAPSHOT_ALLOWED:
        raise _invalid("recycle snapshot contains unsupported fields")

    tenant_id = _safe_identifier(_first_value(normalized, "tenant_id", required=True), "tenant_id")
    dataset_id = _safe_identifier(
        _first_value(normalized, "dataset_id", required=True), "dataset_id"
    )
    document_id = _safe_identifier(
        _first_value(normalized, "document_id", required=True), "document_id"
    )
    recycle_generation = _exact_integer(
        _first_value(normalized, "recycle_generation", required=True),
        "recycle_generation",
        minimum=1,
    )
    document_mutation_generation = _exact_integer(
        _first_value(normalized, "document_mutation_generation", required=True),
        "document_mutation_generation",
        minimum=0,
    )
    lifecycle = _first_value(normalized, "original_lifecycle_state", required=True)
    if lifecycle not in _ALLOWED_LIFECYCLE_STATES:
        raise _invalid("original_lifecycle_state is not an allowed lifecycle state")
    retrieval_enabled = _exact_boolean(
        _first_value(normalized, "original_retrieval_enabled", required=True),
        "original_retrieval_enabled",
    )
    retention_days = _exact_integer(
        _first_value(normalized, "retention_days_snapshot", required=True),
        "retention_days_snapshot",
        minimum=1,
    )
    if retention_days > 3650:
        raise _invalid("retention_days_snapshot must be <= 3650")
    recycled_at = _iso_utc(_first_value(normalized, "recycled_at", required=True), "recycled_at")
    purge_eligible_at = _iso_utc(
        _first_value(normalized, "purge_eligible_at", required=True), "purge_eligible_at"
    )
    if _parse_utc_datetime(purge_eligible_at, "purge_eligible_at") < _parse_utc_datetime(
        recycled_at, "recycled_at"
    ):
        raise _invalid("purge_eligible_at must not precede recycled_at")

    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "recycle_generation": recycle_generation,
        "document_mutation_generation": document_mutation_generation,
        "original_lifecycle_state": lifecycle,
        "original_retrieval_enabled": retrieval_enabled,
        "retention_days_snapshot": retention_days,
        "recycled_at": recycled_at,
        "purge_eligible_at": purge_eligible_at,
    }
    if "recycle_entry_id" in normalized:
        result["recycle_entry_id"] = _safe_identifier(
            normalized["recycle_entry_id"], "recycle_entry_id"
        )
    status = normalized.get("status")
    if status is not None:
        if status not in _ALLOWED_RECYCLE_STATUSES:
            raise _invalid("recycle snapshot status is not allowed")
        result["status"] = status
    if "revision" in normalized:
        result["revision"] = _exact_integer(normalized["revision"], "revision", minimum=1)
    for field in ("document_revision", "dataset_revision"):
        if field in normalized:
            result[field] = _exact_integer(normalized[field], field, minimum=1)
    for field in _SNAPSHOT_TIME_FIELDS:
        if field in normalized:
            result[field] = _iso_utc(normalized[field], field)
    for field in _SNAPSHOT_ID_FIELDS - {"recycle_entry_id"}:
        if field in normalized:
            result[field] = _safe_identifier(normalized[field], field)
    for field in _SNAPSHOT_ACTOR_FIELDS:
        if field in normalized:
            result[field] = _safe_actor(normalized[field], field)
    if "legal_hold_count" in normalized:
        result["legal_hold_count"] = _exact_integer(
            normalized["legal_hold_count"], "legal_hold_count", minimum=0
        )
    if "reason_code" in normalized:
        result["reason_code"] = _safe_code(normalized["reason_code"], "reason_code")
    if "safe_reason" in normalized:
        result["safe_reason"] = _safe_text(normalized["safe_reason"], "safe_reason", maximum=512)

    expected_key = canonical_recycle_key(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        recycle_generation=recycle_generation,
        recycle_entry_id=result.get("recycle_entry_id"),
    )
    active_key = (
        expected_key
        if status is None or status in {"recycled", "restoring", "purge_requested"}
        else None
    )
    supplied_key = normalized.get("active_recycle_key")
    if supplied_key is not None and supplied_key != active_key:
        raise _invalid("active_recycle_key does not match recycle status")
    result["active_recycle_key"] = active_key

    supplied_digest = normalized.get("snapshot_digest")
    digest = canonical_recovery_digest("recycle-snapshot", result)
    if supplied_digest is not None and _sha256(supplied_digest, "snapshot_digest") != digest:
        raise _invalid("snapshot_digest does not match canonical snapshot")
    result["snapshot_digest"] = digest
    return result


def canonical_purge_request_digest(
    request: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> str:
    """Return the digest of a safe, approval-gated purge request snapshot."""

    raw = _merge_mapping_input(request, fields, "purge_request")
    aliases = {
        "entry_id": "recycle_entry_id",
        "expected_entry_revision": "entry_revision",
        "legal_hold_count_snapshot": "legal_hold_count",
        "retention_days": "retention_days_snapshot",
        "digest": "request_digest",
    }
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = aliases.get(key, key)
        if canonical in normalized and normalized[canonical] != value:
            raise _invalid(f"purge_request.{canonical} has conflicting aliases")
        normalized[canonical] = value
    allowed = {
        "tenant_id",
        "recycle_entry_id",
        "dataset_id",
        "document_id",
        "recycle_generation",
        "entry_revision",
        "purge_eligible_at",
        "retention_days_snapshot",
        "legal_hold_count",
        "approval_request_id",
        "requested_at",
        "requested_by",
        "expires_at",
        "status",
        "request_id",
        "idempotency_key_digest",
        "retention_snapshot",
        "retention_snapshot_json",
        "request_digest",
    }
    if set(normalized) - allowed:
        raise _invalid("purge request contains unsupported fields")

    payload: dict[str, Any] = {
        "tenant_id": _safe_identifier(
            _first_value(normalized, "tenant_id", required=True), "tenant_id"
        ),
        "recycle_entry_id": _safe_identifier(
            _first_value(normalized, "recycle_entry_id", required=True), "recycle_entry_id"
        ),
        "dataset_id": _safe_identifier(
            _first_value(normalized, "dataset_id", required=True), "dataset_id"
        ),
        "document_id": _safe_identifier(
            _first_value(normalized, "document_id", required=True), "document_id"
        ),
        "entry_revision": _exact_integer(
            _first_value(normalized, "entry_revision", required=True), "entry_revision", minimum=1
        ),
        "purge_eligible_at": _iso_utc(
            _first_value(normalized, "purge_eligible_at", required=True), "purge_eligible_at"
        ),
        "retention_days_snapshot": _exact_integer(
            _first_value(normalized, "retention_days_snapshot", required=True),
            "retention_days_snapshot",
            minimum=1,
        ),
        "legal_hold_count": _exact_integer(
            _first_value(normalized, "legal_hold_count", required=True),
            "legal_hold_count",
            minimum=0,
        ),
    }
    if payload["retention_days_snapshot"] > 3650:
        raise _invalid("retention_days_snapshot must be <= 3650")
    if payload["legal_hold_count"] != 0:
        raise _invalid("purge request requires zero active legal holds")
    if "recycle_generation" in normalized:
        payload["recycle_generation"] = _exact_integer(
            normalized["recycle_generation"], "recycle_generation", minimum=1
        )
    if "approval_request_id" in normalized:
        payload["approval_request_id"] = _safe_identifier(
            normalized["approval_request_id"], "approval_request_id"
        )
    if "requested_at" in normalized:
        payload["requested_at"] = _iso_utc(normalized["requested_at"], "requested_at")
    if "expires_at" in normalized:
        payload["expires_at"] = _iso_utc(normalized["expires_at"], "expires_at")
    if "requested_by" in normalized:
        payload["requested_by"] = _safe_actor(normalized["requested_by"], "requested_by")
    if "status" in normalized:
        status = normalized["status"]
        if status not in _ALLOWED_PURGE_STATUSES:
            raise _invalid("purge request status is not allowed")
        payload["status"] = status
    if "request_id" in normalized:
        payload["request_id"] = _safe_identifier(normalized["request_id"], "request_id")
    if "idempotency_key_digest" in normalized:
        payload["idempotency_key_digest"] = _sha256(
            normalized["idempotency_key_digest"], "idempotency_key_digest"
        )
    for key in ("retention_snapshot", "retention_snapshot_json"):
        if key in normalized:
            if not isinstance(normalized[key], Mapping):
                raise _invalid(f"{key} must be an object")
            payload["retention_snapshot"] = _canonical_value(
                normalized[key], path=f"purge_request.{key}"
            )
            break

    digest = canonical_recovery_digest("purge-request", payload)
    supplied = normalized.get("request_digest")
    if supplied is not None and _sha256(supplied, "request_digest") != digest:
        raise _invalid("request_digest does not match canonical purge request")
    return digest


def canonical_recovery_event(
    event: Mapping[str, Any] | None = None,
    /,
    **fields: Any,
) -> dict[str, Any]:
    """Canonicalize one immutable recovery event and extend its hash chain."""

    raw = _merge_mapping_input(event, fields, "recovery_event")
    aliases = {
        "entry_id": "recycle_entry_id",
        "event_sequence": "sequence",
        "kind": "event_type",
        "previous_digest": "previous_event_digest",
        "snapshot": "safe_snapshot",
        "safe_snapshot_json": "safe_snapshot",
        "digest": "event_digest",
        "occurred": "occurred_at",
    }
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = aliases.get(key, key)
        if canonical in normalized and normalized[canonical] != value:
            raise _invalid(f"recovery_event.{canonical} has conflicting aliases")
        normalized[canonical] = value
    allowed = {
        "tenant_id",
        "recycle_entry_id",
        "dataset_id",
        "document_id",
        "recycle_generation",
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
        raise _invalid("recovery event contains unsupported fields")

    tenant_id = _safe_identifier(_first_value(normalized, "tenant_id", required=True), "tenant_id")
    entry_id = _safe_identifier(
        _first_value(normalized, "recycle_entry_id", required=True), "recycle_entry_id"
    )
    dataset_id = _safe_identifier(
        _first_value(normalized, "dataset_id", required=True), "dataset_id"
    )
    document_id = _safe_identifier(
        _first_value(normalized, "document_id", required=True), "document_id"
    )
    recycle_generation = None
    if "recycle_generation" in normalized:
        recycle_generation = _exact_integer(
            normalized["recycle_generation"], "recycle_generation", minimum=1
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
            raise _invalid("first event must not have a previous event digest")
        if event_type != "recycled":
            raise _invalid("first event must be recycled")
    else:
        if previous is None:
            raise _invalid("non-first recovery event requires previous_event_digest")
        previous = _sha256(previous, "previous_event_digest")
    actor_id = _safe_actor(_first_value(normalized, "actor_id", required=True), "actor_id")
    request_id = _safe_identifier(
        _first_value(normalized, "request_id", required=True), "request_id"
    )
    snapshot = _first_value(normalized, "safe_snapshot", required=True)
    if not isinstance(snapshot, Mapping):
        raise _invalid("safe_snapshot must be an object")
    safe_snapshot = _canonical_value(snapshot, path="recovery_event.safe_snapshot")
    assert isinstance(safe_snapshot, dict)
    occurred_at = _iso_utc(_first_value(normalized, "occurred_at", required=True), "occurred_at")

    canonical: dict[str, Any] = {
        "tenant_id": tenant_id,
        "recycle_entry_id": entry_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
    }
    if recycle_generation is not None:
        canonical["recycle_generation"] = recycle_generation
    canonical.update(
        {
            "sequence": sequence,
            "event_type": event_type,
            "previous_event_digest": previous,
            "actor_id": actor_id,
            "request_id": request_id,
            "safe_snapshot": safe_snapshot,
            "occurred_at": occurred_at,
        }
    )
    digest = canonical_recovery_digest("recovery-event", canonical)
    supplied = normalized.get("event_digest")
    if supplied is not None and _sha256(supplied, "event_digest") != digest:
        raise _invalid("event_digest does not match canonical recovery event")
    canonical["event_digest"] = digest
    return canonical


_ROUTE_FAMILIES = {
    "enterprise_recycle_bin": "recycle_entry",
    "enterprise_content_recovery": "recovery_entry",
    "enterprise_recovery": "recovery_entry",
    "recycle_bin": "recycle_entry",
    "content_recovery": "recovery_entry",
    "enterprise_approval": "approval",
    "enterprise_approvals": "approval",
    "approval_center": "approval",
    "knowledge_documents": "document",
    "enterprise_documents": "document",
}


def project_recovery_route(
    route: Mapping[str, Any] | str | None = None,
    params: Mapping[str, Any] | None = None,
    *,
    route_code: str | None = None,
    target_route_code: str | None = None,
    route_params: Mapping[str, Any] | None = None,
    target_route_params: Mapping[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Project a server-owned, allow-listed Recovery Center handoff route."""

    raw: dict[str, Any] = {}
    if isinstance(route, Mapping):
        raw.update(_normalized_mapping(route, "recovery_route"))
    elif route is not None:
        if not isinstance(route, str):
            raise _invalid("route must be a route code or object")
        raw["route_code"] = route
    if params is not None:
        raw["params"] = params
    for key, value in {
        "route_code": route_code,
        "target_route_code": target_route_code,
        "route_params": route_params,
        "target_route_params": target_route_params,
    }.items():
        if value is not None:
            if key in raw and raw[key] != value:
                raise _invalid(f"recovery_route.{key} has conflicting values")
            raw[key] = value
    for raw_key, value in fields.items():
        key = _normalized_key(raw_key, "recovery_route")
        if key in raw and raw[key] != value:
            raise _invalid(f"recovery_route.{key} has conflicting values")
        raw[key] = value

    code_value = _first_value(raw, "route_code", "target_route_code", required=True)
    if not isinstance(code_value, str) or _CODE_RE.fullmatch(code_value) is None:
        raise _invalid("route code is invalid")
    if code_value not in _ALLOWED_ROUTE_CODES:
        raise _invalid("route code is not allowed")
    family = _ROUTE_FAMILIES[code_value]
    parameter_value = _first_value(
        raw, "params", "route_params", "target_route_params", "target_route_params_json"
    )
    if parameter_value is None:
        parameter_value = {
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
    if not isinstance(parameter_value, Mapping):
        raise _invalid("route parameters must be an object")
    normalized_params = _normalized_mapping(parameter_value, "recovery_route.params")

    if family == "recycle_entry":
        aliases = {"recycle_entry_id": "entry_id"}
        schema = {"tenant_id", "entry_id"}
    elif family == "recovery_entry":
        aliases = {"entry_id": "recycle_entry_id"}
        schema = {"tenant_id", "recycle_entry_id"}
    elif family == "approval":
        aliases = {"request_id": "approval_request_id"}
        schema = {"tenant_id", "approval_request_id"}
    else:
        aliases = {}
        schema = {"tenant_id", "dataset_id", "document_id"}

    projected: dict[str, Any] = {}
    for key, value in normalized_params.items():
        canonical = aliases.get(key, key)
        if canonical in projected and projected[canonical] != value:
            raise _invalid(f"recovery_route.params.{canonical} has conflicting aliases")
        projected[canonical] = value
    if set(projected) != schema:
        raise _invalid("route parameter schema must match exactly")
    return {
        "target_route_code": code_value,
        "target_route_params_json": {
            key: _safe_identifier(projected[key], f"route.params.{key}") for key in sorted(schema)
        },
    }


__all__ = [
    "CONTENT_RECOVERY_SCHEMA_VERSION",
    "RECOVERY_SCHEMA_VERSION",
    "ContentRecoveryAuthorityConflict",
    "ContentRecoveryAuthorityError",
    "ContentRecoveryAuthorityInvalid",
    "ContentRecoveryConflict",
    "ContentRecoveryError",
    "ContentRecoveryInvalid",
    "EnterpriseContentRecoveryConflict",
    "EnterpriseContentRecoveryError",
    "EnterpriseContentRecoveryInvalid",
    "RecoveryAuthorityConflict",
    "RecoveryAuthorityError",
    "RecoveryAuthorityInvalid",
    "canonical_purge_request_digest",
    "canonical_recovery_digest",
    "canonical_recycle_key",
    "canonical_recycle_snapshot",
    "canonical_recovery_event",
    "project_recovery_route",
]
