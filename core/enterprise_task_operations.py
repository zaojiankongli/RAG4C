"""Pure, Tenant-safe Task Operations authority primitives.

The module has no ORM, HTTP, or executor dependency.  It canonicalizes only
small, safe facts for the Stage 24 unified task read model.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from core.task_source_kinds import (
    TASK_SOURCE_KIND_NAMES,
    task_route_codes,
    task_route_schema,
    task_route_source_kinds,
    task_source_kind,
    task_source_kind_names,
)
from core.task_vocabulary import (
    TASK_CATEGORY_VALUES,
    TASK_NORMALIZED_STATUS_VALUES,
    canonical_task_category,
    canonical_task_status,
)

UTC = timezone.utc
TASK_OPERATIONS_SCHEMA_VERSION = 1
TASK_SCHEMA_VERSION = TASK_OPERATIONS_SCHEMA_VERSION

TASK_SOURCE_KINDS = frozenset(TASK_SOURCE_KIND_NAMES)
ALLOWED_SOURCE_KINDS = TASK_SOURCE_KINDS
TASK_NORMALIZED_STATUSES = frozenset(TASK_NORMALIZED_STATUS_VALUES)
ALLOWED_NORMALIZED_STATUSES = TASK_NORMALIZED_STATUSES
TASK_ACTIONS = frozenset({"retry", "cancel", "acknowledge"})
ALLOWED_ACTIONS = TASK_ACTIONS
TASK_ACTION_STATUSES = frozenset({"requested", "dispatched", "applied", "rejected", "expired"})
ALLOWED_ACTION_STATUSES = TASK_ACTION_STATUSES
TASK_EVENT_TYPES = frozenset(
    {
        "materialized",
        "status_changed",
        "source_stale",
        "action_requested",
        "action_applied",
        "action_rejected",
        "attention_acknowledged",
    }
)
ALLOWED_EVENT_TYPES = TASK_EVENT_TYPES
TASK_RECONCILIATION_STATUSES = frozenset({"started", "completed", "failed"})
ALLOWED_RECONCILIATION_STATUSES = TASK_RECONCILIATION_STATUSES
TASK_VIEW_STATUSES = frozenset({"active", "archived"})
ALLOWED_VIEW_STATUSES = TASK_VIEW_STATUSES
TASK_CATEGORIES = frozenset(TASK_CATEGORY_VALUES)
_CATEGORY_BY_SOURCE_KIND = {
    name: spec.public_category
    for name in task_source_kind_names()
    if (spec := task_source_kind(name)) is not None
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_URL_RE = re.compile(
    r"(?i)(?:https?|ftp|file|mailto|javascript|data):\S+|\b(?:www\.)\S+|"
    r"\b(?:mysql|mariadb|postgres(?:ql)?|mongodb|redis(?:s)?|sqlite):\/\/\S+"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\+[a-z0-9_]+)?|"
    r"mongodb(?:\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\/\/\S+"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|"
    r"api[_ -]?key|client[_ -]?secret|id[_ -]?token|idempotency[_ -]?key|ticket|token)"
    r"\s*[:=]\s*\S+|bearer\s+\S+|secret:\/\/\S+|"
    r"(?:sk_(?:live|test)[-_]|ghp_|xox[baprs]-)\S+|"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")"
)
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?i)\b(?:select|insert|update|delete)\b|"
    r"\b(?:payload|raw[ _-]+(?:payload|content|body|metadata)|query|"
    r"result[ _-]+body|raw[ _-]+result|reviewer[ _-]+note|judgment[ _-]+note|"
    r"note|comment|ticket|token|credential)\b"
)
_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "api",
        "apikey",
        "authorization",
        "access",
        "refresh",
        "client",
        "credential",
        "password",
        "passwd",
        "secret",
        "ticket",
        "token",
        "payload",
        "body",
        "content",
        "metadata",
        "note",
        "notes",
        "comment",
        "comments",
        "query",
        "result",
        "url",
        "uri",
        "href",
        "webhook",
        "cookie",
        "header",
        "prompt",
        "answer",
        "question",
        "raw",
    }
)
_TIMESTAMP_KEY_PARTS = frozenset(
    {
        "at",
        "occurred_at",
        "observed_at",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "lease_until",
        "requested_at",
        "dispatched_at",
        "applied_at",
        "rejected_at",
        "expired_at",
        "expires_at",
        "occurred_from",
        "occurred_to",
        "started_from",
        "started_to",
    }
)
_MAX_CANONICAL_DEPTH = 24
_MAX_CANONICAL_ITEMS = 256
_MAX_CANONICAL_STRING = 4096


class TaskOperationsAuthorityError(ValueError):
    code = "task_operations_authority_invalid"
    status = 422

    def __init__(self, message: str = "Task Operations authority is invalid") -> None:
        super().__init__(message)
        self.message = message


class TaskOperationsAuthorityInvalid(TaskOperationsAuthorityError):
    """Malformed or unsafe task authority."""


class TaskOperationsAuthorityConflict(TaskOperationsAuthorityInvalid):
    code = "task_operations_authority_conflict"
    status = 409


TaskAuthorityError = TaskOperationsAuthorityError
TaskAuthorityInvalid = TaskOperationsAuthorityInvalid
TaskAuthorityConflict = TaskOperationsAuthorityConflict
EnterpriseTaskOperationsError = TaskOperationsAuthorityError
EnterpriseTaskOperationsInvalid = TaskOperationsAuthorityInvalid
EnterpriseTaskOperationsConflict = TaskOperationsAuthorityConflict


def _invalid(message: str) -> TaskOperationsAuthorityInvalid:
    return TaskOperationsAuthorityInvalid(message)


def _is_forbidden_key(key: str) -> bool:
    if key in {
        "source_digest",
        "projection_digest",
        "event_digest",
        "action_digest",
        "view_digest",
        "reconciliation_digest",
        "idempotency_key_digest",
    }:
        return False
    if key == "idempotency_key" or key.endswith("_idempotency_key"):
        return True
    if key in _FORBIDDEN_KEY_PARTS or set(key.split("_")) & _FORBIDDEN_KEY_PARTS:
        return True
    if key.startswith("raw_") or key.endswith(
        (
            "_body",
            "_content",
            "_metadata",
            "_note",
            "_notes",
            "_comment",
            "_comments",
            "_query",
            "_url",
            "_uri",
            "_href",
        )
    ):
        return True
    return "ticket" in key or "token" in key or "credential" in key


def _normalized_key(raw_key: Any, path: str) -> str:
    if not isinstance(raw_key, str):
        raise _invalid(f"{path} object keys must be strings")
    key = raw_key.strip()
    if not key:
        raise _invalid(f"{path} object keys must not be empty")
    key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).casefold()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    if not key:
        raise _invalid(f"{path} object key is invalid")
    if _is_forbidden_key(key):
        raise _invalid(f"{path}.{key} contains a forbidden field")
    return key


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
    if any(ord(character) < 32 for character in result):
        raise _invalid(f"{field} contains unsupported control characters")
    if (
        _URL_RE.search(result)
        or _EMAIL_RE.search(result)
        or _DATABASE_URL_RE.search(result)
        or _SECRET_VALUE_RE.search(result)
        or _FORBIDDEN_TEXT_RE.search(result)
    ):
        raise _invalid(f"{field} contains forbidden sensitive content")
    return result


def _safe_component(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum)
    assert result is not None
    if re.fullmatch(rf"[A-Za-z0-9][A-Za-z0-9_.:@-]{{0,{maximum - 1}}}", result) is None:
        raise _invalid(f"{field} contains unsupported identity characters")
    return result


def _safe_code(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum)
    assert result is not None
    result = result.casefold()
    if re.fullmatch(rf"[a-z][a-z0-9_.-]{{0,{maximum - 1}}}", result) is None:
        raise _invalid(f"{field} must be a safe lowercase code")
    return result


def _safe_actor(value: Any, field: str) -> str:
    result = _safe_text(value, field, maximum=128)
    assert result is not None
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise _invalid(f"{field} contains unsupported identity characters")
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


def _canonical_value(value: Any, *, path: str, key_hint: str | None = None, depth: int = 0) -> Any:
    if depth > _MAX_CANONICAL_DEPTH:
        raise _invalid(f"{path} exceeds the canonical JSON depth limit")
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, datetime):
        return _iso_utc(value, path)
    if isinstance(value, str):
        return (
            _iso_utc(value, path)
            if key_hint
            and (
                key_hint in _TIMESTAMP_KEY_PARTS
                or key_hint.endswith("_at")
                or key_hint.endswith("_until")
                or key_hint.endswith("_timestamp")
            )
            else _safe_text(value, path, maximum=_MAX_CANONICAL_STRING)
        )
    if isinstance(value, float):
        raise _invalid(f"{path} must use an exact integer or boolean, not a float")
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_CANONICAL_ITEMS:
            raise _invalid(f"{path} exceeds the canonical item limit")
        return [
            _canonical_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        if len(value) > _MAX_CANONICAL_ITEMS:
            raise _invalid(f"{path} exceeds the canonical item limit")
        normalized: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = _normalized_key(raw_key, path)
            if key in normalized:
                raise _invalid(f"{path} contains duplicate canonical keys")
            normalized[key] = _canonical_value(
                nested, path=f"{path}.{key}", key_hint=key, depth=depth + 1
            )
        return {key: normalized[key] for key in sorted(normalized)}
    raise _invalid(f"{path} contains an unsupported value")


def _canonical_json(value: Any, *, path: str) -> bytes:
    normalized = _canonical_value(value, path=path)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def canonical_task_digest(namespace: str, value: Any) -> str:
    """Return a deterministic, length-delimited Task Operations digest."""
    label = _safe_code(namespace, "digest namespace", maximum=64)
    encoded = _canonical_json(value, path=f"task.{label}")
    digest = sha256(b"rag4c:enterprise-task-operations:v1\x00")
    for payload in (label.encode("utf-8"), encoded):
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _merge_mapping_input(
    primary: Mapping[str, Any] | None, fields: Mapping[str, Any], path: str
) -> dict[str, Any]:
    if primary is not None and not isinstance(primary, Mapping):
        raise _invalid(f"{path} must be an object")
    result: dict[str, Any] = {}
    for mapping in (primary or {}, fields):
        for raw_key, value in mapping.items():
            key = _normalized_key(raw_key, path)
            if key in result and result[key] != value:
                raise _invalid(f"{path}.{key} has conflicting values")
            result[key] = value
    return result


def _apply_aliases(raw: Mapping[str, Any], aliases: Mapping[str, str], path: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = aliases.get(key, key)
        if canonical in result and result[canonical] != value:
            raise _invalid(f"{path}.{canonical} has conflicting aliases")
        result[canonical] = value
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


def _normalize_source_kind(value: Any) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip().casefold()
        or task_source_kind(value) is None
    ):
        raise _invalid("source_kind is not allowed")
    return value


def _normalize_category(value: Any, source_kind: str) -> str:
    spec = task_source_kind(source_kind)
    if spec is None:
        raise _invalid("source_kind is not allowed")
    expected = spec.public_category
    if value is None:
        return expected
    try:
        normalized = canonical_task_category(value)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    if normalized != expected:
        raise _invalid("category does not match source_kind")
    return normalized


def _normalize_status(value: Any, field: str = "normalized_status") -> str:
    try:
        return canonical_task_status(value, field=field)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc


def _normalize_filter_status(value: Any, field: str = "filters.normalized_statuses") -> str:
    """Accept only canonical Task Projection statuses in Saved View filters."""

    if not isinstance(value, str):
        raise _invalid(f"{field} is not allowed")
    normalized = value.strip().casefold()
    if normalized not in TASK_NORMALIZED_STATUSES:
        raise _invalid(f"{field} is not allowed")
    return normalized


_ACTION_ALIASES = {
    "retry": "retry",
    "retry_task": "retry",
    "rerun": "retry",
    "re_run": "retry",
    "cancel": "cancel",
    "cancel_task": "cancel",
    "canceled": "cancel",
    "cancelled": "cancel",
    "ack": "acknowledge",
    "acknowledge": "acknowledge",
    "acknowledged": "acknowledge",
    "acknowledge_attention": "acknowledge",
}


def _normalize_action(value: Any, field: str = "action_type") -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field} is not allowed")
    result = _ACTION_ALIASES.get(value.strip().casefold().replace("-", "_").replace(" ", "_"))
    if result not in TASK_ACTIONS:
        raise _invalid(f"{field} is not allowed")
    return result


_ACTION_STATUS_ALIASES = {
    "requested": "requested",
    "pending": "requested",
    "dispatched": "dispatched",
    "sent": "dispatched",
    "applied": "applied",
    "completed": "applied",
    "rejected": "rejected",
    "denied": "rejected",
    "expired": "expired",
}


def _normalize_action_status(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("action_status is not allowed")
    result = _ACTION_STATUS_ALIASES.get(
        value.strip().casefold().replace("-", "_").replace(" ", "_")
    )
    if result not in TASK_ACTION_STATUSES:
        raise _invalid("action_status is not allowed")
    return result


def _normalize_view_status(value: Any) -> str:
    if value is None:
        return "active"
    if not isinstance(value, str):
        raise _invalid("view_status is not allowed")
    result = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if result not in TASK_VIEW_STATUSES:
        raise _invalid("view_status is not allowed")
    return result


def _normalize_reconciliation_status(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("status is not allowed")
    result = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if result not in TASK_RECONCILIATION_STATUSES:
        raise _invalid("status is not allowed")
    return result


def _optional_component(raw: Mapping[str, Any], key: str, *, maximum: int = 128) -> str | None:
    return None if raw.get(key) is None else _safe_component(raw[key], key, maximum=maximum)


def _optional_timestamp(raw: Mapping[str, Any], key: str) -> str | None:
    return None if raw.get(key) is None else _iso_utc(raw[key], key)


def _canonical_digest_input(
    value: Mapping[str, Any] | None, fields: Mapping[str, Any], path: str, digest_key: str
) -> dict[str, Any]:
    raw = _merge_mapping_input(value, fields, path)
    canonical = _canonical_value(raw, path=path)
    assert isinstance(canonical, dict)
    canonical.pop(digest_key, None)
    if digest_key == "reconciliation_digest":
        canonical.pop("run_digest", None)
    return canonical


def canonical_task_source_digest(value: Mapping[str, Any] | None = None, /, **fields: Any) -> str:
    return canonical_task_digest(
        "task-source", _canonical_digest_input(value, fields, "task_source_digest", "source_digest")
    )


def canonical_task_projection_digest(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> str:
    return canonical_task_digest(
        "task-projection",
        _canonical_digest_input(value, fields, "task_projection_digest", "projection_digest"),
    )


def canonical_task_event_digest(value: Mapping[str, Any] | None = None, /, **fields: Any) -> str:
    return canonical_task_digest(
        "task-event", _canonical_digest_input(value, fields, "task_event_digest", "event_digest")
    )


def canonical_task_action_digest(value: Mapping[str, Any] | None = None, /, **fields: Any) -> str:
    return canonical_task_digest(
        "task-action", _canonical_digest_input(value, fields, "task_action_digest", "action_digest")
    )


def canonical_task_saved_view_digest(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> str:
    return canonical_task_digest(
        "task-saved-view",
        _canonical_digest_input(value, fields, "task_saved_view_digest", "view_digest"),
    )


def canonical_task_reconciliation_digest(
    value: Mapping[str, Any] | None = None, /, **fields: Any
) -> str:
    return canonical_task_digest(
        "task-reconciliation",
        _canonical_digest_input(
            value, fields, "task_reconciliation_digest", "reconciliation_digest"
        ),
    )


_SOURCE_ALIASES = {
    "kind": "source_kind",
    "id": "source_id",
    "revision": "source_revision",
    "digest": "source_digest",
    "source_observation_digest": "source_digest",
    "source_dataset_id": "dataset_id",
    "source_workspace_id": "workspace_id",
    "status": "normalized_status",
    "state": "normalized_status",
    "progress": "progress_percent",
    "progress_percentage": "progress_percent",
    "attempt": "attempt_number",
    "attempt_count": "attempt_number",
    "claim_owner": "lease_owner",
    "claim_lease_until": "lease_until",
    "observed_at": "occurred_at",
    "at": "occurred_at",
    "facts": "safe_facts",
    "target_route": "route",
    "route_params": "target_route_params_json",
    "target_route_params": "target_route_params_json",
}
_SOURCE_ALLOWED = {
    "tenant_id",
    "source_kind",
    "source_id",
    "source_revision",
    "source_digest",
    "dataset_id",
    "workspace_id",
    "category",
    "normalized_status",
    "action_required",
    "progress_percent",
    "attempt_number",
    "max_attempts",
    "lease_owner",
    "lease_until",
    "safe_error_code",
    "safe_error",
    "target_route_code",
    "target_route_params_json",
    "route",
    "occurred_at",
    "started_at",
    "finished_at",
    "updated_at",
    "safe_facts",
}


def canonical_task_source(
    source: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(source, fields, "task_source"), _SOURCE_ALIASES, "task_source"
    )
    if set(raw) - _SOURCE_ALLOWED:
        raise _invalid("task source contains unsupported fields")
    tenant_id = _safe_component(
        _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
    )
    source_kind = _normalize_source_kind(_first_value(raw, "source_kind", required=True))
    source_id = _safe_component(_first_value(raw, "source_id", required=True), "source_id")
    source_revision = _exact_integer(
        _first_value(raw, "source_revision", required=True), "source_revision", minimum=1
    )
    source_digest = _sha256(_first_value(raw, "source_digest", required=True), "source_digest")
    assert source_digest is not None
    category = _normalize_category(raw.get("category"), source_kind)
    normalized_status = _normalize_status(raw.get("normalized_status"), "status")
    action_required = _exact_boolean(raw.get("action_required", False), "action_required")
    dataset_id = _optional_component(raw, "dataset_id", maximum=64)
    workspace_id = _optional_component(raw, "workspace_id", maximum=64)
    progress = raw.get("progress_percent")
    if progress is not None:
        progress = _exact_integer(progress, "progress_percent", minimum=0)
        if progress > 100:
            raise _invalid("progress_percent must be between 0 and 100")
    attempt = raw.get("attempt_number")
    if attempt is not None:
        attempt = _exact_integer(attempt, "attempt_number", minimum=0)
    max_attempts = raw.get("max_attempts")
    if max_attempts is not None:
        max_attempts = _exact_integer(max_attempts, "max_attempts", minimum=1)
    if attempt is not None and max_attempts is not None and attempt > max_attempts:
        raise _invalid("attempt_number must not exceed max_attempts")
    lease_owner = _optional_component(raw, "lease_owner")
    lease_until = _optional_timestamp(raw, "lease_until")
    if (lease_owner is None) != (lease_until is None):
        raise _invalid("lease_owner and lease_until must be supplied together")
    safe_error_code, safe_error = raw.get("safe_error_code"), raw.get("safe_error")
    if (safe_error_code is None) != (safe_error is None):
        raise _invalid("safe error code and safe error must be supplied together")
    if safe_error_code is not None:
        safe_error_code = _safe_code(safe_error_code, "safe_error_code", maximum=64)
        safe_error = _safe_text(safe_error, "safe_error", maximum=512)
    facts = raw.get("safe_facts", {})
    if not isinstance(facts, Mapping):
        raise _invalid("safe_facts must be an object")
    facts = _canonical_value(facts, path="task_source.safe_facts")
    assert isinstance(facts, dict)
    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_revision": source_revision,
        "source_digest": source_digest,
        "dataset_id": dataset_id,
        "workspace_id": workspace_id,
        "category": category,
        "normalized_status": normalized_status,
        "action_required": action_required,
        "safe_facts": facts,
    }
    for key, value in (
        ("progress_percent", progress),
        ("attempt_number", attempt),
        ("max_attempts", max_attempts),
    ):
        if value is not None:
            result[key] = value
    if lease_owner is not None:
        result["lease_owner"], result["lease_until"] = lease_owner, lease_until
    if safe_error_code is not None:
        result["safe_error_code"], result["safe_error"] = safe_error_code, safe_error
    for key in ("occurred_at", "started_at", "finished_at", "updated_at"):
        timestamp = _optional_timestamp(raw, key)
        if timestamp is not None:
            result[key] = timestamp
    route = raw.get("route")
    route_code = raw.get("target_route_code")
    route_params = raw.get("target_route_params_json")
    if route is not None and (route_code is not None or route_params is not None):
        raise _invalid("task source route has conflicting values")
    if route is not None:
        route_result = project_task_source_route(source_kind=source_kind, route=route)
    elif route_code is not None or route_params is not None:
        route_result = project_task_source_route(
            source_kind=source_kind, route_code=route_code, params=route_params
        )
    else:
        route_result = None
    if route_result:
        result.update(route_result)
    return result


_PROJECTION_ALIASES = {
    "id": "task_id",
    "projection_id": "task_id",
    "task_projection_id": "task_id",
    "kind": "source_kind",
    "revision": "source_revision",
    "source_observation_digest": "source_digest",
    "digest": "projection_digest",
    "status": "normalized_status",
    "state": "normalized_status",
    "progress": "progress_percent",
    "progress_percentage": "progress_percent",
    "attempt": "attempt_number",
    "attempt_count": "attempt_number",
    "claim_owner": "lease_owner",
    "claim_lease_until": "lease_until",
    "is_current": "source_current",
    "current": "source_current",
    "target_route": "route",
    "route_params": "target_route_params_json",
    "target_route_params": "target_route_params_json",
    "facts": "safe_facts",
}
_PROJECTION_ALLOWED = {
    "tenant_id",
    "task_id",
    "source_kind",
    "source_id",
    "source_revision",
    "source_digest",
    "dataset_id",
    "workspace_id",
    "category",
    "normalized_status",
    "action_required",
    "progress_percent",
    "attempt_number",
    "max_attempts",
    "lease_owner",
    "lease_until",
    "safe_error_code",
    "safe_error",
    "target_route_code",
    "target_route_params_json",
    "route",
    "source_current",
    "occurred_at",
    "started_at",
    "finished_at",
    "updated_at",
    "safe_facts",
    "projection_digest",
    "source",
}


def canonical_task_projection(
    projection: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(projection, fields, "task_projection"),
        _PROJECTION_ALIASES,
        "task_projection",
    )
    nested_source = raw.pop("source", None)
    if nested_source is not None:
        source = canonical_task_source(nested_source)
        for key, value in source.items():
            if key in raw and raw[key] != value:
                raise _invalid(f"task_projection.{key} conflicts with source")
            if key not in {"safe_facts", "target_route_code", "target_route_params_json"}:
                raw[key] = value
        raw.setdefault("safe_facts", source["safe_facts"])
        if "target_route_code" in source:
            raw.setdefault("target_route_code", source["target_route_code"])
            raw.setdefault("target_route_params_json", source["target_route_params_json"])
    if set(raw) - _PROJECTION_ALLOWED:
        raise _invalid("task projection contains unsupported fields")
    tenant_id = _safe_component(
        _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
    )
    task_id = _safe_component(_first_value(raw, "task_id", required=True), "task_id")
    source_kind = _normalize_source_kind(_first_value(raw, "source_kind", required=True))
    source_id = _safe_component(_first_value(raw, "source_id", required=True), "source_id")
    source_revision = _exact_integer(
        _first_value(raw, "source_revision", required=True), "source_revision", minimum=1
    )
    source_digest = _sha256(_first_value(raw, "source_digest", required=True), "source_digest")
    assert source_digest is not None
    category = _normalize_category(raw.get("category"), source_kind)
    normalized_status = _normalize_status(raw.get("normalized_status"), "status")
    action_required = _exact_boolean(raw.get("action_required", False), "action_required")
    source_current = _exact_boolean(raw.get("source_current", True), "source_current")
    dataset_id = _optional_component(raw, "dataset_id", maximum=64)
    workspace_id = _optional_component(raw, "workspace_id", maximum=64)
    progress = raw.get("progress_percent")
    if progress is not None:
        progress = _exact_integer(progress, "progress_percent", minimum=0)
        if progress > 100:
            raise _invalid("progress_percent must be between 0 and 100")
    attempt = raw.get("attempt_number")
    if attempt is not None:
        attempt = _exact_integer(attempt, "attempt_number", minimum=0)
    max_attempts = raw.get("max_attempts")
    if max_attempts is not None:
        max_attempts = _exact_integer(max_attempts, "max_attempts", minimum=1)
    if attempt is not None and max_attempts is not None and attempt > max_attempts:
        raise _invalid("attempt_number must not exceed max_attempts")
    lease_owner, lease_until = (
        _optional_component(raw, "lease_owner"),
        _optional_timestamp(raw, "lease_until"),
    )
    if (lease_owner is None) != (lease_until is None):
        raise _invalid("lease_owner and lease_until must be supplied together")
    error_code, error_text = raw.get("safe_error_code"), raw.get("safe_error")
    if (error_code is None) != (error_text is None):
        raise _invalid("safe error code and safe error must be supplied together")
    if error_code is not None:
        error_code, error_text = (
            _safe_code(error_code, "safe_error_code", maximum=64),
            _safe_text(error_text, "safe_error", maximum=512),
        )
    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_revision": source_revision,
        "source_digest": source_digest,
        "dataset_id": dataset_id,
        "workspace_id": workspace_id,
        "category": category,
        "normalized_status": normalized_status,
        "action_required": action_required,
        "source_current": source_current,
    }
    for key, value in (
        ("progress_percent", progress),
        ("attempt_number", attempt),
        ("max_attempts", max_attempts),
    ):
        if value is not None:
            result[key] = value
    if lease_owner is not None:
        result["lease_owner"], result["lease_until"] = lease_owner, lease_until
    if error_code is not None:
        result["safe_error_code"], result["safe_error"] = error_code, error_text
    if "safe_facts" in raw:
        if not isinstance(raw["safe_facts"], Mapping):
            raise _invalid("safe_facts must be an object")
        facts = _canonical_value(raw["safe_facts"], path="task_projection.safe_facts")
        assert isinstance(facts, dict)
        result["safe_facts"] = facts
    for key in ("occurred_at", "started_at", "finished_at", "updated_at"):
        timestamp = _optional_timestamp(raw, key)
        if timestamp is not None:
            result[key] = timestamp
    route = raw.get("route")
    route_code, route_params = raw.get("target_route_code"), raw.get("target_route_params_json")
    if route is not None and (route_code is not None or route_params is not None):
        raise _invalid("task projection route has conflicting values")
    if route is not None:
        result.update(project_task_source_route(source_kind=source_kind, route=route))
    elif route_code is not None or route_params is not None:
        result.update(
            project_task_source_route(
                source_kind=source_kind, route_code=route_code, params=route_params
            )
        )
    computed = canonical_task_projection_digest(result)
    supplied = raw.get("projection_digest")
    if supplied is not None and _sha256(supplied, "projection_digest") != computed:
        raise _invalid("projection_digest does not match canonical task projection")
    result["projection_digest"] = computed
    return result


_EVENT_ALIASES = {
    "id": "task_id",
    "projection_id": "task_id",
    "kind": "event_type",
    "type": "event_type",
    "previous_digest": "previous_event_digest",
    "digest": "event_digest",
    "snapshot": "safe_snapshot",
    "safe_snapshot_json": "safe_snapshot",
    "revision": "source_revision",
    "source_observation_digest": "source_digest",
    "action": "action_type",
    "status": "action_status",
    "at": "occurred_at",
}
_EVENT_ALLOWED = {
    "tenant_id",
    "task_id",
    "source_kind",
    "source_id",
    "source_revision",
    "source_digest",
    "sequence",
    "event_type",
    "previous_event_digest",
    "event_digest",
    "action_id",
    "action_type",
    "action_status",
    "actor_id",
    "request_id",
    "safe_snapshot",
    "occurred_at",
}


def canonical_task_event(
    event: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(event, fields, "task_event"), _EVENT_ALIASES, "task_event"
    )
    if set(raw) - _EVENT_ALLOWED:
        raise _invalid("task event contains unsupported fields")
    tenant_id = _safe_component(
        _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
    )
    task_id = _safe_component(_first_value(raw, "task_id", required=True), "task_id")
    source_kind, source_id = raw.get("source_kind"), raw.get("source_id")
    if source_kind is not None:
        source_kind = _normalize_source_kind(source_kind)
        if source_id is None:
            raise _invalid("source_id is required when source_kind is supplied")
        source_id = _safe_component(source_id, "source_id")
    elif source_id is not None:
        raise _invalid("source_kind is required when source_id is supplied")
    source_revision, source_digest = raw.get("source_revision"), raw.get("source_digest")
    if (source_revision is None) != (source_digest is None):
        raise _invalid("source revision and source digest must be supplied together")
    if source_revision is not None:
        source_revision, source_digest = (
            _exact_integer(source_revision, "source_revision", minimum=1),
            _sha256(source_digest, "source_digest"),
        )
    sequence = _exact_integer(raw.get("sequence"), "sequence", minimum=1)
    event_type_value = raw.get("event_type")
    if not isinstance(event_type_value, str):
        raise _invalid("event_type is not allowed")
    event_type = event_type_value.strip().casefold().replace("-", "_").replace(" ", "_")
    if event_type not in TASK_EVENT_TYPES:
        raise _invalid("event_type is not allowed")
    previous = raw.get("previous_event_digest")
    if sequence == 1:
        if previous is not None:
            raise _invalid("first event must not have a previous event digest")
        if event_type != "materialized":
            raise _invalid("first event must be materialized")
    else:
        if previous is None:
            raise _invalid("non-first event requires previous_event_digest")
        previous = _sha256(previous, "previous_event_digest")
    actor_id = _safe_actor(raw.get("actor_id"), "actor_id")
    request_id = _safe_component(raw.get("request_id"), "request_id")
    snapshot = raw.get("safe_snapshot")
    if not isinstance(snapshot, Mapping):
        raise _invalid("safe_snapshot must be an object")
    snapshot = _canonical_value(snapshot, path="task_event.safe_snapshot")
    assert isinstance(snapshot, dict)
    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "sequence": sequence,
        "event_type": event_type,
        "previous_event_digest": previous,
        "actor_id": actor_id,
        "request_id": request_id,
        "safe_snapshot": snapshot,
        "occurred_at": _iso_utc(raw.get("occurred_at"), "occurred_at"),
    }
    if source_kind is not None:
        result["source_kind"], result["source_id"] = source_kind, source_id
    if source_revision is not None:
        result["source_revision"], result["source_digest"] = source_revision, source_digest
    action_id = _optional_component(raw, "action_id")
    if action_id is not None:
        result["action_id"] = action_id
    if "action_type" in raw:
        result["action_type"] = _normalize_action(raw["action_type"])
    if "action_status" in raw:
        result["action_status"] = _normalize_action_status(raw["action_status"])
    computed = canonical_task_event_digest(result)
    supplied = raw.get("event_digest")
    if supplied is not None and _sha256(supplied, "event_digest") != computed:
        raise _invalid("event_digest does not match canonical task event")
    result["event_digest"] = computed
    return result


_ACTION_ALIASES_CANONICAL = {
    "id": "action_id",
    "task_action_id": "action_id",
    "action": "action_type",
    "type": "action_type",
    "status": "action_status",
    "expected_revision": "expected_source_revision",
    "expected_digest": "expected_source_digest",
    "source_observation_digest": "expected_source_digest",
    "idempotency_digest": "idempotency_key_digest",
    "request_digest": "idempotency_key_digest",
    "reason": "safe_reason",
    "at": "requested_at",
}
_ACTION_ALLOWED = {
    "tenant_id",
    "action_id",
    "task_id",
    "source_kind",
    "source_id",
    "action_type",
    "action_status",
    "expected_source_revision",
    "expected_source_digest",
    "idempotency_key_digest",
    "actor_id",
    "request_id",
    "safe_reason",
    "adapter_code",
    "requested_at",
    "dispatched_at",
    "applied_at",
    "rejected_at",
    "expired_at",
    "safe_snapshot",
    "action_digest",
}


def canonical_task_action(
    action: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(action, fields, "task_action"),
        _ACTION_ALIASES_CANONICAL,
        "task_action",
    )
    if set(raw) - _ACTION_ALLOWED:
        raise _invalid("task action contains unsupported fields")
    tenant_id = _safe_component(
        _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
    )
    action_id = _safe_component(_first_value(raw, "action_id", required=True), "action_id")
    task_id = _safe_component(_first_value(raw, "task_id", required=True), "task_id")
    source_kind = _normalize_source_kind(_first_value(raw, "source_kind", required=True))
    source_id = _safe_component(_first_value(raw, "source_id", required=True), "source_id")
    action_type = _normalize_action(raw.get("action_type"))
    action_status = _normalize_action_status(raw.get("action_status", "requested"))
    expected_revision = _exact_integer(
        _first_value(raw, "expected_source_revision", required=True),
        "expected_source_revision",
        minimum=1,
    )
    expected_digest = _sha256(
        _first_value(raw, "expected_source_digest", required=True), "expected_source_digest"
    )
    idempotency_digest = _sha256(
        _first_value(raw, "idempotency_key_digest", required=True), "idempotency_key_digest"
    )
    assert expected_digest is not None and idempotency_digest is not None
    result: dict[str, Any] = {
        "tenant_id": tenant_id,
        "action_id": action_id,
        "task_id": task_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "action_type": action_type,
        "action_status": action_status,
        "expected_source_revision": expected_revision,
        "expected_source_digest": expected_digest,
        "idempotency_key_digest": idempotency_digest,
        "actor_id": _safe_actor(raw.get("actor_id"), "actor_id"),
        "request_id": _safe_component(raw.get("request_id"), "request_id"),
        "safe_reason": _safe_text(raw.get("safe_reason"), "safe_reason", maximum=512),
        "requested_at": _iso_utc(raw.get("requested_at"), "requested_at"),
    }
    if raw.get("adapter_code") is not None:
        result["adapter_code"] = _safe_component(raw["adapter_code"], "adapter_code", maximum=64)
    for key in ("dispatched_at", "applied_at", "rejected_at", "expired_at"):
        timestamp = _optional_timestamp(raw, key)
        if timestamp is not None:
            result[key] = timestamp
    if "safe_snapshot" in raw:
        if not isinstance(raw["safe_snapshot"], Mapping):
            raise _invalid("safe_snapshot must be an object")
        snapshot = _canonical_value(raw["safe_snapshot"], path="task_action.safe_snapshot")
        assert isinstance(snapshot, dict)
        result["safe_snapshot"] = snapshot
    computed = canonical_task_action_digest(result)
    if (
        raw.get("action_digest") is not None
        and _sha256(raw["action_digest"], "action_digest") != computed
    ):
        raise _invalid("action_digest does not match canonical task action")
    result["action_digest"] = computed
    return result


_VIEW_ALIASES = {
    "id": "view_id",
    "saved_view_id": "view_id",
    "status": "view_status",
    "lifecycle_status": "view_status",
    "filter": "filters",
    "created": "created_at",
    "updated": "updated_at",
    "digest": "view_digest",
}
_VIEW_ALLOWED = {
    "tenant_id",
    "account_id",
    "view_id",
    "name",
    "view_status",
    "filters",
    "created_at",
    "updated_at",
    "view_digest",
}
_FILTER_ALIASES = {
    "source_kind": "source_kinds",
    "source_types": "source_kinds",
    "category": "categories",
    "normalized_status": "normalized_statuses",
    "status": "normalized_statuses",
    "statuses": "normalized_statuses",
    "dataset_id": "dataset_ids",
    "workspace_id": "workspace_ids",
    "occurred_at_from": "occurred_from",
    "occurred_at_to": "occurred_to",
    "start_at": "occurred_from",
    "end_at": "occurred_to",
    "from": "occurred_from",
    "to": "occurred_to",
}
_FILTER_ALLOWED = {
    "source_kinds",
    "categories",
    "normalized_statuses",
    "action_required",
    "dataset_ids",
    "workspace_ids",
    "occurred_from",
    "occurred_to",
}


def _normalize_filter_category(value: Any) -> str:
    try:
        return canonical_task_category(value, field="filters.categories")
    except ValueError as exc:
        raise _invalid("filters.categories contains an invalid category") from exc


def _string_list(value: Any, field: str, normalizer: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)) or len(values) > _MAX_CANONICAL_ITEMS:
        raise _invalid(f"{field} must be a bounded array")
    result = [normalizer(item) for item in values]
    if len(set(result)) != len(result):
        raise _invalid(f"{field} must not contain duplicates")
    return sorted(result)


def _canonical_task_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid("filters must be an object")
    raw = _apply_aliases(
        _merge_mapping_input(value, {}, "task_filters"), _FILTER_ALIASES, "task_filters"
    )
    if set(raw) - _FILTER_ALLOWED:
        raise _invalid("task filters contain unsupported fields")
    result: dict[str, Any] = {}
    if "source_kinds" in raw:
        result["source_kinds"] = _string_list(
            raw["source_kinds"], "filters.source_kinds", _normalize_source_kind
        )
    if "categories" in raw:
        result["categories"] = _string_list(
            raw["categories"], "filters.categories", _normalize_filter_category
        )
    if "normalized_statuses" in raw:
        result["normalized_statuses"] = _string_list(
            raw["normalized_statuses"],
            "filters.normalized_statuses",
            _normalize_filter_status,
        )
    if "action_required" in raw:
        result["action_required"] = _exact_boolean(
            raw["action_required"], "filters.action_required"
        )
    for key in ("dataset_ids", "workspace_ids"):
        if key in raw:
            result[key] = _string_list(
                raw[key],
                f"filters.{key}",
                lambda item, field=f"filters.{key}": _safe_component(item, field, maximum=64),
            )
    start, end = raw.get("occurred_from"), raw.get("occurred_to")
    if (start is None) != (end is None):
        raise _invalid("bounded time filters require occurred_from and occurred_to")
    if start is not None:
        start_dt, end_dt = (
            _parse_utc_datetime(start, "filters.occurred_from"),
            _parse_utc_datetime(end, "filters.occurred_to"),
        )
        if start_dt > end_dt:
            raise _invalid("filters time range is invalid")
        result["occurred_from"], result["occurred_to"] = (
            start_dt.isoformat(timespec="microseconds") + "Z",
            end_dt.isoformat(timespec="microseconds") + "Z",
        )
    return result


def canonical_task_saved_view(
    view: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(view, fields, "task_saved_view"), _VIEW_ALIASES, "task_saved_view"
    )
    if set(raw) - _VIEW_ALLOWED:
        raise _invalid("task saved view contains unsupported fields")
    result: dict[str, Any] = {
        "tenant_id": _safe_component(
            _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
        ),
        "account_id": _safe_component(
            _first_value(raw, "account_id", required=True), "account_id", maximum=64
        ),
        "view_id": _safe_component(_first_value(raw, "view_id", required=True), "view_id"),
        "name": _safe_text(_first_value(raw, "name", required=True), "name", maximum=128),
        "view_status": _normalize_view_status(raw.get("view_status")),
        "filters": _canonical_task_filters(raw.get("filters", {})),
    }
    for key in ("created_at", "updated_at"):
        timestamp = _optional_timestamp(raw, key)
        if timestamp is not None:
            result[key] = timestamp
    computed = canonical_task_saved_view_digest(result)
    if (
        raw.get("view_digest") is not None
        and _sha256(raw["view_digest"], "view_digest") != computed
    ):
        raise _invalid("view_digest does not match canonical task saved view")
    result["view_digest"] = computed
    return result


_RECONCILIATION_ALIASES = {
    "id": "reconciliation_id",
    "run_id": "reconciliation_id",
    "reconciliation_run_id": "reconciliation_id",
    "state": "status",
    "inventory_digest": "source_inventory_digest",
    "source_counts_by_kind": "source_counts",
    "counts": "source_counts",
    "created": "created_count",
    "updated": "updated_count",
    "stale": "stale_count",
    "invalid": "invalid_count",
    "started": "started_at",
    "completed": "completed_at",
    "error_code": "safe_error_code",
    "error": "safe_error",
    "run_digest": "reconciliation_digest",
    "digest": "reconciliation_digest",
}
_RECONCILIATION_ALLOWED = {
    "tenant_id",
    "reconciliation_id",
    "status",
    "source_inventory_digest",
    "source_counts",
    "created_count",
    "updated_count",
    "stale_count",
    "invalid_count",
    "started_at",
    "completed_at",
    "safe_error_code",
    "safe_error",
    "reconciliation_digest",
}


def canonical_task_reconciliation(
    run: Mapping[str, Any] | None = None, /, **fields: Any
) -> dict[str, Any]:
    raw = _apply_aliases(
        _merge_mapping_input(run, fields, "task_reconciliation"),
        _RECONCILIATION_ALIASES,
        "task_reconciliation",
    )
    if set(raw) - _RECONCILIATION_ALLOWED:
        raise _invalid("task reconciliation contains unsupported fields")
    status = _normalize_reconciliation_status(_first_value(raw, "status", required=True))
    counts_value = raw.get("source_counts", {})
    if not isinstance(counts_value, Mapping):
        raise _invalid("source_counts must be an object")
    counts_raw = _merge_mapping_input(counts_value, {}, "task_reconciliation.source_counts")
    if set(counts_raw) - frozenset(task_source_kind_names()):
        raise _invalid("source_counts contains an unknown source_kind")
    counts = {
        kind: _exact_integer(counts_raw.get(kind, 0), f"source_counts.{kind}", minimum=0)
        for kind in sorted(task_source_kind_names())
    }
    result: dict[str, Any] = {
        "tenant_id": _safe_component(
            _first_value(raw, "tenant_id", required=True), "tenant_id", maximum=64
        ),
        "reconciliation_id": _safe_component(
            _first_value(raw, "reconciliation_id", required=True), "reconciliation_id"
        ),
        "status": status,
        "source_inventory_digest": _sha256(
            _first_value(raw, "source_inventory_digest", required=True), "source_inventory_digest"
        ),
        "source_counts": counts,
        "created_count": _exact_integer(raw.get("created_count", 0), "created_count", minimum=0),
        "updated_count": _exact_integer(raw.get("updated_count", 0), "updated_count", minimum=0),
        "stale_count": _exact_integer(raw.get("stale_count", 0), "stale_count", minimum=0),
        "invalid_count": _exact_integer(raw.get("invalid_count", 0), "invalid_count", minimum=0),
        "started_at": _iso_utc(_first_value(raw, "started_at", required=True), "started_at"),
    }
    completed_at = _optional_timestamp(raw, "completed_at")
    if status in {"completed", "failed"} and completed_at is None:
        raise _invalid("completed_at is required for terminal reconciliation status")
    if completed_at is not None:
        if _parse_utc_datetime(completed_at, "completed_at") < _parse_utc_datetime(
            result["started_at"], "started_at"
        ):
            raise _invalid("completed_at must not precede started_at")
        result["completed_at"] = completed_at
    error_code, error_text = raw.get("safe_error_code"), raw.get("safe_error")
    if (error_code is None) != (error_text is None):
        raise _invalid("safe error code and safe error must be supplied together")
    if error_code is not None:
        result["safe_error_code"], result["safe_error"] = (
            _safe_code(error_code, "safe_error_code", maximum=64),
            _safe_text(error_text, "safe_error", maximum=512),
        )
    computed = canonical_task_reconciliation_digest(result)
    if (
        raw.get("reconciliation_digest") is not None
        and _sha256(raw["reconciliation_digest"], "reconciliation_digest") != computed
    ):
        raise _invalid("reconciliation_digest does not match canonical task reconciliation")
    result["reconciliation_digest"] = computed
    return result


_ROUTE_DEFAULT_BY_SOURCE_KIND = {
    name: spec.default_route_code
    for name in task_source_kind_names()
    if (spec := task_source_kind(name)) is not None
}
_ROUTE_SOURCE_KINDS = {
    route_code: task_route_source_kinds(route_code)
    for route_code in task_route_codes()
}
_ROUTE_CODES = frozenset(_ROUTE_SOURCE_KINDS)


def _route_schema(route_code: str, source_kind: str | None) -> tuple[set[str], set[str]]:
    try:
        return task_route_schema(route_code, source_kind)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc


def _parse_route_arguments(args: tuple[Any, ...]) -> tuple[Any, Any, Any]:
    if len(args) > 3:
        raise _invalid("task_source_route accepts at most three positional arguments")
    if len(args) == 0:
        return None, None, None
    if len(args) == 1:
        return (None, args[0], None)
    if len(args) == 2:
        if (
            isinstance(args[0], str)
            and task_source_kind(args[0]) is not None
            and isinstance(args[1], Mapping)
        ):
            return args[0], args[1], None
        return None, args[0], args[1]
    return args[0], args[1], args[2]


def project_task_source_route(
    *args: Any,
    route: Mapping[str, Any] | str | None = None,
    params: Mapping[str, Any] | None = None,
    source_kind: str | None = None,
    route_code: str | None = None,
    target_route_code: str | None = None,
    route_params: Mapping[str, Any] | None = None,
    target_route_params: Mapping[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    positional_source_kind, positional_route, positional_params = _parse_route_arguments(args)
    if positional_source_kind is not None:
        if source_kind is not None and source_kind != positional_source_kind:
            raise _invalid("source_kind has conflicting values")
        source_kind = positional_source_kind
    if positional_route is not None:
        if route is not None and route != positional_route:
            raise _invalid("route has conflicting values")
        route = positional_route
    if positional_params is not None:
        if params is not None and params != positional_params:
            raise _invalid("params have conflicting values")
        params = positional_params
    raw: dict[str, Any] = {}
    if isinstance(route, Mapping):
        raw.update(_merge_mapping_input(route, {}, "task_source_route"))
    elif route is not None:
        raw["route_code"] = route
    if params is not None:
        raw["params"] = params
    for key, value in (
        ("source_kind", source_kind),
        ("route_code", route_code),
        ("target_route_code", target_route_code),
        ("route_params", route_params),
        ("target_route_params", target_route_params),
    ):
        if value is not None:
            if key in raw and raw[key] != value:
                raise _invalid(f"task_source_route.{key} has conflicting values")
            raw[key] = value
    for raw_key, value in fields.items():
        key = _normalized_key(raw_key, "task_source_route")
        if key in raw and raw[key] != value:
            raise _invalid(f"task_source_route.{key} has conflicting values")
        raw[key] = value
    selected_source_kind = raw.get("source_kind")
    if selected_source_kind is not None:
        selected_source_kind = _normalize_source_kind(selected_source_kind)
    selected_route = _first_value(raw, "route_code", "target_route_code")
    if selected_route is None:
        if selected_source_kind is None:
            raise _invalid("route code is required")
        spec = task_source_kind(selected_source_kind)
        if spec is None:
            raise _invalid("source_kind is not allowed")
        selected_route = spec.default_route_code
    if (
        not isinstance(selected_route, str)
        or _CODE_RE.fullmatch(selected_route.strip().casefold()) is None
    ):
        raise _invalid("route code is invalid")
    selected_route = selected_route.strip().casefold()
    if selected_route not in task_route_codes():
        raise _invalid("route code is not allowed")
    if (
        selected_source_kind is not None
        and selected_source_kind not in task_route_source_kinds(selected_route)
    ):
        raise _invalid("route source scope is invalid")
    parameter_value = _first_value(raw, "params", "route_params", "target_route_params")
    if parameter_value is None:
        parameter_value = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "source_kind",
                "route_code",
                "target_route_code",
                "params",
                "route_params",
                "target_route_params",
            }
        }
    if not isinstance(parameter_value, Mapping):
        raise _invalid("route parameters must be an object")
    normalized_params: dict[str, Any] = {}
    for raw_key, value in parameter_value.items():
        key = _normalized_key(raw_key, "task_source_route.params")
        if key in normalized_params:
            raise _invalid("route parameters contain duplicate canonical keys")
        normalized_params[key] = value
    allowed, required = _route_schema(selected_route, selected_source_kind)
    if set(normalized_params) - allowed:
        raise _invalid("route parameters contain unsupported fields")
    if not required <= set(normalized_params):
        raise _invalid("route parameter schema is incomplete")
    projected = {
        key: _safe_component(
            value,
            f"route.params.{key}",
            maximum=64 if key == "tenant_id" or key.endswith("_id") else 128,
        )
        for key, value in normalized_params.items()
    }
    return {
        "target_route_code": selected_route,
        "target_route_params_json": {key: projected[key] for key in sorted(projected)},
    }


project_task_route = project_task_source_route
project_task_source_handoff_route = project_task_source_route
project_task_source = canonical_task_source
project_task = canonical_task_projection

__all__ = [
    "TASK_OPERATIONS_SCHEMA_VERSION",
    "TASK_SCHEMA_VERSION",
    "TASK_SOURCE_KINDS",
    "ALLOWED_SOURCE_KINDS",
    "TASK_NORMALIZED_STATUSES",
    "ALLOWED_NORMALIZED_STATUSES",
    "TASK_ACTIONS",
    "ALLOWED_ACTIONS",
    "TASK_ACTION_STATUSES",
    "ALLOWED_ACTION_STATUSES",
    "TASK_EVENT_TYPES",
    "ALLOWED_EVENT_TYPES",
    "TASK_RECONCILIATION_STATUSES",
    "ALLOWED_RECONCILIATION_STATUSES",
    "TASK_VIEW_STATUSES",
    "ALLOWED_VIEW_STATUSES",
    "TASK_CATEGORIES",
    "TaskOperationsAuthorityError",
    "TaskOperationsAuthorityInvalid",
    "TaskOperationsAuthorityConflict",
    "TaskAuthorityError",
    "TaskAuthorityInvalid",
    "TaskAuthorityConflict",
    "EnterpriseTaskOperationsError",
    "EnterpriseTaskOperationsInvalid",
    "EnterpriseTaskOperationsConflict",
    "canonical_task_digest",
    "canonical_task_source_digest",
    "canonical_task_projection_digest",
    "canonical_task_event_digest",
    "canonical_task_action_digest",
    "canonical_task_saved_view_digest",
    "canonical_task_reconciliation_digest",
    "canonical_task_source",
    "canonical_task_projection",
    "canonical_task_event",
    "canonical_task_action",
    "canonical_task_saved_view",
    "canonical_task_reconciliation",
    "project_task_source",
    "project_task",
    "project_task_route",
    "project_task_source_route",
    "project_task_source_handoff_route",
]
