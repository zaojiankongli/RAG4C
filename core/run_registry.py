"""Privacy boundary and identity helpers for the RunRegistry.

This module intentionally contains no persistence or HTTP behavior. Canonical
RunEvent facts are copied, allowlisted, bounded, and detached before later
registry tasks may place them in memory or a persistence queue.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence

from config.settings import RunHistorySettings
from core.metrics import get_metrics
from core.run_events import RunEvent, run_event_dict


_LOGGER = logging.getLogger(__name__)


_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "seq",
        "occurred_at",
        "elapsed_ms",
        "topology_id",
        "topology_revision",
        "type",
        "node_id",
        "attempt",
        "duration_ms",
        "attributes",
        "error",
    }
)
_ATTRIBUTE_KEYS = frozenset(
    {
        "outcome",
        "reason",
        "request_path",
        "cache_level",
        "mode",
        "executor",
        "executor_requested",
        "executor_used",
        "retry_enabled",
        "delivery",
        "singleflight",
        "queue_admitted",
        "flight_released",
        "cache_write",
        "route",
        "effective",
        "effective_route",
        "fallback_route",
        "configured_route",
        "component",
        "source",
        "enabled",
        "available",
        "skipped",
        "abstained",
        "recoverable",
        "degraded",
        "changed",
        "rewrite_required",
        "output_present",
        "scoped",
        "supported",
        "missing_evidence",
        "entailment_evaluated",
        "target_attempt",
        "last_attempt",
        "attempt_count",
        "top_k",
        "chunk_count",
        "candidate_count",
        "requested_count",
        "input_count",
        "output_count",
        "source_count",
        "citation_count",
        "valid_count",
        "invalid_count",
        "subquery_count",
        "successful_count",
        "failure_count",
        "added_count",
        "added_chunk_count",
        "removed_count",
        "output_chars",
        "token_count",
        "queue_wait_ms",
        "slot_wait_ms",
        "confidence",
        "score_bucket",
        "topology",
    }
)
_BOOLEAN_ATTRIBUTE_KEYS = frozenset(
    {
        "retry_enabled",
        "queue_admitted",
        "flight_released",
        "cache_write",
        "enabled",
        "available",
        "skipped",
        "abstained",
        "recoverable",
        "degraded",
        "changed",
        "rewrite_required",
        "output_present",
        "scoped",
        "supported",
        "missing_evidence",
        "entailment_evaluated",
    }
)
_NUMERIC_ATTRIBUTE_KEYS = frozenset(
    {
        "target_attempt",
        "last_attempt",
        "attempt_count",
        "top_k",
        "chunk_count",
        "candidate_count",
        "requested_count",
        "input_count",
        "output_count",
        "source_count",
        "citation_count",
        "valid_count",
        "invalid_count",
        "subquery_count",
        "successful_count",
        "failure_count",
        "added_count",
        "added_chunk_count",
        "removed_count",
        "output_chars",
        "token_count",
        "queue_wait_ms",
        "slot_wait_ms",
        "confidence",
    }
)
_ERROR_KEYS = frozenset({"type", "code", "recoverable"})
_TOPOLOGY_KEYS = frozenset({"id", "revision", "executor", "nodes", "edges"})
_TOPOLOGY_NODE_KEYS = frozenset(
    {
        "id",
        "label",
        "group",
        "description",
        "optional",
        "repeatable",
        "available",
        "plugin",
        "attributes",
    }
)
_TOPOLOGY_EDGE_KEYS = frozenset({"id", "source", "target", "kind", "label"})
_TOPOLOGY_GROUPS = frozenset(
    {"input", "understand", "retrieve", "generate", "verify", "output", "extension"}
)
_TOPOLOGY_EDGE_KINDS = frozenset({"dependency", "conditional", "retry", "failure"})
_TOPOLOGY_EXECUTORS = frozenset({"sequential_stream", "sequential", "langgraph", "cache_replay"})
_FORBIDDEN_NESTED_KEYS = (
    "query",
    "question",
    "answer",
    "response",
    "text",
    "prompt",
    "acl",
    "tenant",
    "dataset",
    "document",
    "chunk",
    "embedding",
    "vector",
    "score",
    "scores",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "token",
    "message",
    "traceback",
    "stack",
    "path",
)
_REDACTED_LENGTH = "[redacted:length]"


@dataclass(frozen=True, slots=True)
class RegistryIdentity:
    boot_id: str
    worker_id: str
    scope_key: bytes
    cursor_key: bytes
    tenant_scope_stability: Literal["installation", "boot"]


@dataclass(frozen=True, slots=True)
class BoundRunContext:
    tenant_scope: str
    query_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class RedactionResult:
    event: dict[str, Any] | None
    reason: str | None
    partial_started_envelope: dict[str, Any] | None = None


class _RejectedValue(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _base64url_prefix(digest: bytes, size: int) -> str:
    return base64.urlsafe_b64encode(digest[:size]).rstrip(b"=").decode("ascii")


def derive_tenant_scope(normalized_tenant_id: str, scope_key: bytes) -> str:
    """Return an opaque, installation/boot-scoped tenant identifier."""

    digest = hmac.new(
        scope_key,
        b"tenant\0" + normalized_tenant_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _base64url_prefix(digest, 18)


def fingerprint_query(query: str, secret: bytes) -> str:
    """Return an optional domain-separated HMAC of normalized query text."""

    normalized = " ".join(unicodedata.normalize("NFKC", query).strip().split())
    digest = hmac.new(
        secret,
        b"query\0" + normalized.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _base64url_prefix(digest, 16)


def _unknown_keys(value: dict[str, Any], allowed: frozenset[str]) -> bool:
    return any(type(key) is not str or key not in allowed for key in value)


def _normalized_key_forms(key: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", key)
    folded: list[str] = []
    previous_was_lower_or_digit = False
    for character in normalized:
        if character.isalnum():
            if character.isupper() and previous_was_lower_or_digit and folded[-1:] != ["_"]:
                folded.append("_")
            folded.append(character.casefold())
            previous_was_lower_or_digit = character.islower() or character.isdigit()
        else:
            if folded and folded[-1] != "_":
                folded.append("_")
            previous_was_lower_or_digit = False
    separated = "".join(folded).strip("_")
    compact = "".join(character for character in separated if character.isalnum())
    return separated, compact


def _is_forbidden_nested_key(key: str) -> bool:
    separated, compact = _normalized_key_forms(key)
    for forbidden in _FORBIDDEN_NESTED_KEYS:
        forbidden_separated, forbidden_compact = _normalized_key_forms(forbidden)
        if forbidden_separated in separated or forbidden_compact in compact:
            return True
    return False


def _finite_number(value: Any, *, reason_prefix: str, non_negative: bool) -> int | float:
    if type(value) not in {int, float}:
        raise _RejectedValue(f"{reason_prefix}_invalid_type")
    if type(value) is float and not math.isfinite(value):
        raise _RejectedValue(f"{reason_prefix}_non_finite_number")
    if non_negative and value < 0:
        raise _RejectedValue(f"{reason_prefix}_negative_number")
    return value


def _sanitize_json_value(
    value: Any,
    *,
    reason_prefix: str,
    depth: int,
    max_string_length: int,
    replace_overlong: bool,
    non_negative: bool = False,
) -> Any:
    if depth > 4:
        raise _RejectedValue(f"{reason_prefix}_too_deep")
    if value is None or type(value) is bool:
        return value
    if type(value) in {int, float}:
        return _finite_number(
            value,
            reason_prefix=reason_prefix,
            non_negative=non_negative,
        )
    if type(value) is str:
        if len(value) <= max_string_length:
            return value
        if replace_overlong:
            return _REDACTED_LENGTH
        raise _RejectedValue(f"{reason_prefix}_string_too_long")
    if type(value) is list:
        if len(value) > 32:
            raise _RejectedValue(f"{reason_prefix}_array_too_large")
        return [
            _sanitize_json_value(
                item,
                reason_prefix=reason_prefix,
                depth=depth + 1,
                max_string_length=max_string_length,
                replace_overlong=replace_overlong,
                non_negative=non_negative,
            )
            for item in value
        ]
    if type(value) is dict:
        if len(value) > 64:
            raise _RejectedValue(f"{reason_prefix}_object_too_large")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or _is_forbidden_nested_key(key):
                raise _RejectedValue(f"{reason_prefix}_unknown_key")
            if len(key) > max_string_length:
                raise _RejectedValue(f"{reason_prefix}_key_too_long")
            result[key] = _sanitize_json_value(
                item,
                reason_prefix=reason_prefix,
                depth=depth + 1,
                max_string_length=max_string_length,
                replace_overlong=replace_overlong,
                non_negative=non_negative,
            )
        return result
    raise _RejectedValue(f"{reason_prefix}_invalid_type")


def _is_non_negative_attribute(key: str) -> bool:
    return (
        key.endswith("_count")
        or key.endswith("_ms")
        or key.endswith("_chars")
        or key in {"top_k", "target_attempt", "last_attempt", "attempt_count", "confidence"}
    )


def _sanitize_attributes(
    value: Any, topology_revision: str, topology_id: str, settings: RunHistorySettings
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _RejectedValue("attributes_invalid_type")
    if len(value) > 64:
        raise _RejectedValue("attributes_object_too_large")
    if _unknown_keys(value, _ATTRIBUTE_KEYS):
        raise _RejectedValue("attributes_unknown_key")

    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in _BOOLEAN_ATTRIBUTE_KEYS and type(item) is not bool:
            raise _RejectedValue("attributes_invalid_type")
        if key in _NUMERIC_ATTRIBUTE_KEYS and type(item) not in {int, float}:
            raise _RejectedValue("attributes_invalid_type")
        if key == "topology":
            result[key] = _sanitize_topology(
                item,
                topology_revision=topology_revision,
                topology_id=topology_id,
                settings=settings,
            )
            continue
        result[key] = _sanitize_json_value(
            item,
            reason_prefix="attributes",
            depth=1,
            max_string_length=96,
            replace_overlong=True,
            non_negative=_is_non_negative_attribute(key),
        )
    return result


def _topology_string(value: Any) -> str:
    if type(value) is not str:
        raise _RejectedValue("topology_invalid_type")
    if len(value) > 128:
        raise _RejectedValue("topology_string_too_long")
    return value


def _sanitize_topology_node(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise _RejectedValue("topology_node_invalid_type")
    if len(value) > 64:
        raise _RejectedValue("topology_object_too_large")
    if _unknown_keys(value, _TOPOLOGY_NODE_KEYS):
        raise _RejectedValue("topology_node_unknown_key")
    required = {
        "id",
        "label",
        "group",
        "description",
        "optional",
        "repeatable",
        "available",
        "attributes",
    }
    if not required.issubset(value):
        raise _RejectedValue("topology_node_missing_field")

    group = _topology_string(value["group"])
    if group not in _TOPOLOGY_GROUPS:
        raise _RejectedValue("topology_node_invalid_group")
    for key in ("optional", "repeatable", "available"):
        if type(value[key]) is not bool:
            raise _RejectedValue("topology_node_invalid_type")

    node = {
        "id": _topology_string(value["id"]),
        "label": _topology_string(value["label"]),
        "group": group,
        "description": _topology_string(value["description"]),
        "optional": value["optional"],
        "repeatable": value["repeatable"],
        "available": value["available"],
        "attributes": _sanitize_json_value(
            value["attributes"],
            reason_prefix="topology",
            depth=4,
            max_string_length=128,
            replace_overlong=False,
        ),
    }
    if "plugin" in value:
        node["plugin"] = _topology_string(value["plugin"])
    return node


def _sanitize_topology_edge(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise _RejectedValue("topology_edge_invalid_type")
    if len(value) > 64:
        raise _RejectedValue("topology_object_too_large")
    if _unknown_keys(value, _TOPOLOGY_EDGE_KEYS):
        raise _RejectedValue("topology_edge_unknown_key")
    if not {"id", "source", "target", "kind"}.issubset(value):
        raise _RejectedValue("topology_edge_missing_field")

    kind = _topology_string(value["kind"])
    if kind not in _TOPOLOGY_EDGE_KINDS:
        raise _RejectedValue("topology_edge_invalid_kind")
    edge = {
        "id": _topology_string(value["id"]),
        "source": _topology_string(value["source"]),
        "target": _topology_string(value["target"]),
        "kind": kind,
    }
    if "label" in value:
        edge["label"] = _topology_string(value["label"])
    return edge


def _compact_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sanitize_topology(
    value: Any,
    *,
    topology_revision: str,
    topology_id: str,
    settings: RunHistorySettings,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _RejectedValue("topology_invalid_type")
    if len(value) > 64:
        raise _RejectedValue("topology_object_too_large")
    if _unknown_keys(value, _TOPOLOGY_KEYS):
        raise _RejectedValue("topology_unknown_key")
    if not _TOPOLOGY_KEYS.issubset(value):
        raise _RejectedValue("topology_missing_field")

    topology_value_id = _topology_string(value["id"])
    revision = _topology_string(value["revision"])
    executor = _topology_string(value["executor"])
    if topology_value_id != topology_id:
        raise _RejectedValue("topology_id_mismatch")
    if revision != topology_revision:
        raise _RejectedValue("topology_revision_mismatch")
    if executor not in _TOPOLOGY_EXECUTORS:
        raise _RejectedValue("topology_invalid_executor")
    if type(value["nodes"]) is not list or type(value["edges"]) is not list:
        raise _RejectedValue("topology_invalid_type")
    if len(value["nodes"]) > 32 or len(value["edges"]) > 32:
        raise _RejectedValue("topology_array_too_large")

    nodes = [_sanitize_topology_node(node) for node in value["nodes"]]
    edges = [_sanitize_topology_edge(edge) for edge in value["edges"]]
    node_ids = [node["id"] for node in nodes]
    edge_ids = [edge["id"] for edge in edges]
    if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
        raise _RejectedValue("topology_duplicate_id")
    known_nodes = set(node_ids)
    if any(
        edge["source"] not in known_nodes or edge["target"] not in known_nodes for edge in edges
    ):
        raise _RejectedValue("topology_unknown_endpoint")

    topology = {
        "revision": revision,
        "id": topology_value_id,
        "executor": executor,
        "nodes": nodes,
        "edges": edges,
    }
    if len(_compact_json_bytes(topology)) > settings.max_topology_json_bytes:
        raise _RejectedValue("topology_json_too_large")
    return topology


def _bounded_error_string(value: Any) -> str:
    if type(value) is not str or not value:
        raise _RejectedValue("error_invalid_type")
    if len(value) > 96:
        raise _RejectedValue("error_string_too_long")
    return value


def _sanitize_error(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise _RejectedValue("error_invalid_type")
    if _unknown_keys(value, _ERROR_KEYS):
        raise _RejectedValue("error_unknown_key")
    if not {"type", "recoverable"}.issubset(value):
        raise _RejectedValue("error_missing_field")
    if type(value["recoverable"]) is not bool:
        raise _RejectedValue("error_invalid_type")

    result = {
        "type": _bounded_error_string(value["type"]),
        "recoverable": value["recoverable"],
    }
    if "code" in value:
        result["code"] = _bounded_error_string(value["code"])
    return result


def _sanitize_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    if _unknown_keys(payload, _ENVELOPE_KEYS):
        raise _RejectedValue("envelope_unknown_key")
    required = {
        "schema_version",
        "run_id",
        "seq",
        "occurred_at",
        "elapsed_ms",
        "topology_id",
        "topology_revision",
        "type",
        "attributes",
    }
    if not required.issubset(payload):
        raise _RejectedValue("envelope_missing_field")
    if payload["schema_version"] != 1 or type(payload["schema_version"]) is not int:
        raise _RejectedValue("envelope_invalid_type")
    if type(payload["seq"]) is not int or payload["seq"] < 1:
        raise _RejectedValue("envelope_invalid_type")

    result: dict[str, Any] = {
        "schema_version": 1,
        "run_id": _envelope_string(payload["run_id"]),
        "seq": payload["seq"],
        "occurred_at": _envelope_string(payload["occurred_at"]),
        "elapsed_ms": _finite_number(
            payload["elapsed_ms"],
            reason_prefix="envelope",
            non_negative=True,
        ),
        "topology_id": _envelope_string(payload["topology_id"], maximum=128),
        "topology_revision": _envelope_string(payload["topology_revision"], maximum=128),
        "type": _envelope_string(payload["type"]),
    }
    if "node_id" in payload:
        result["node_id"] = _envelope_string(payload["node_id"])
    if "attempt" in payload:
        if type(payload["attempt"]) is not int or payload["attempt"] < 1:
            raise _RejectedValue("envelope_invalid_type")
        result["attempt"] = payload["attempt"]
    if "duration_ms" in payload:
        result["duration_ms"] = _finite_number(
            payload["duration_ms"],
            reason_prefix="envelope",
            non_negative=True,
        )
    return result


def _envelope_string(value: Any, *, maximum: int = 96) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise _RejectedValue("envelope_invalid_type")
    return value


def _partial_started_envelope(
    payload: dict[str, Any], envelope: dict[str, Any]
) -> dict[str, Any] | None:
    if payload.get("type") != "run.started":
        return None
    return dict(envelope)


def sanitize_run_event(event: RunEvent, settings: RunHistorySettings) -> RedactionResult:
    """Return a detached allowlisted event, or a safe fail-closed rejection."""

    try:
        payload = run_event_dict(event)
    except Exception:
        return RedactionResult(event=None, reason="event_copy_failed")
    if type(payload) is not dict:
        return RedactionResult(event=None, reason="event_copy_failed")

    envelope: dict[str, Any] | None = None
    try:
        envelope = _sanitize_envelope(payload)
        attributes = _sanitize_attributes(
            payload["attributes"],
            topology_revision=envelope["topology_revision"],
            topology_id=envelope["topology_id"],
            settings=settings,
        )
        sanitized = {**envelope, "attributes": attributes}
        if "error" in payload:
            sanitized["error"] = _sanitize_error(payload["error"])

        size_checked = dict(sanitized)
        if "topology" in attributes:
            size_checked["attributes"] = {
                key: value for key, value in attributes.items() if key != "topology"
            }
        if len(_compact_json_bytes(size_checked)) > settings.max_event_json_bytes:
            raise _RejectedValue("event_json_too_large")
        return RedactionResult(event=sanitized, reason=None)
    except _RejectedValue as exc:
        partial = None
        if envelope is not None and exc.reason.startswith("topology_"):
            partial = _partial_started_envelope(payload, envelope)
        return RedactionResult(
            event=None,
            reason=exc.reason,
            partial_started_envelope=partial,
        )
    except Exception:
        return RedactionResult(event=None, reason="redaction_failed")


RunStatus = Literal["running", "completed", "failed", "cancelled", "interrupted"]
RunOutcome = Literal["answered", "abstained", "unknown"]
EventIntegrity = Literal["complete", "partial", "unknown"]
PersistenceStatus = Literal["pending", "durable", "partial", "memory_only", "unavailable"]
RunView = Literal["active", "recent", "slow", "errors", "stuck"]


@dataclass(frozen=True, slots=True)
class RunSummary:
    schema_version: Literal[1]
    run_id: str
    status: RunStatus
    outcome: RunOutcome
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    elapsed_ms: float
    boot_id: str
    worker_id: str
    topology_id: str
    topology_revision: str
    executor: str
    last_seq: int
    event_count: int
    earliest_available_seq: int
    current_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    route: str | None
    degraded_count: int
    retry_count: int
    attention: tuple[str, ...]
    event_integrity: EventIntegrity
    persistence_status: PersistenceStatus
    interruption_reason: str | None
    query_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class NodeRollup:
    node_id: str
    attempt: int
    status: str
    started_elapsed_ms: float | None
    finished_elapsed_ms: float | None
    duration_ms: float | None
    degraded_reason: str | None
    retry_reason: str | None
    error_type: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RunHistory:
    event_integrity: EventIntegrity
    earliest_available_seq: int
    last_seq: int
    persistence_status: PersistenceStatus


@dataclass(frozen=True, slots=True)
class RunDetail:
    schema_version: Literal[1]
    summary: RunSummary
    topology: Mapping[str, Any]
    node_rollup: tuple[NodeRollup, ...]
    history: RunHistory


@dataclass(frozen=True, slots=True)
class RunEventSlice:
    schema_version: Literal[1]
    run_id: str
    events: tuple[Mapping[str, Any], ...]
    after_seq: int
    latest_seq: int
    terminal: bool
    timed_out: bool
    history_state: Literal["complete", "partial", "expired"]
    earliest_available_seq: int
    persistence_status: PersistenceStatus
    retry_after_ms: int = 500


@dataclass(frozen=True, slots=True)
class RunListQuery:
    tenant_scope: str
    view: RunView = "recent"
    statuses: tuple[RunStatus, ...] = ()
    slow_ms: int | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None
    fingerprint: str | None = None
    limit: int = 50
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class RegistryMemoryHealth:
    active_runs: int
    recent_runs: int
    event_count: int
    dropped_runs: int
    dropped_events: int


@dataclass(frozen=True, slots=True)
class RegistryHealth:
    schema_version: Literal[1]
    status: Literal["ok", "degraded", "disabled"]
    enabled: bool
    boot_id: str
    worker_id: str
    memory: RegistryMemoryHealth
    sink_errors: int
    rejected_events: int


@dataclass(frozen=True, slots=True)
class RegistryMutation:
    context: BoundRunContext
    summary: RunSummary
    topology: Mapping[str, Any]
    event: Mapping[str, Any]


@dataclass(slots=True)
class _MutableRollup:
    node_id: str
    attempt: int
    status: str
    started_elapsed_ms: float | None = None
    finished_elapsed_ms: float | None = None
    duration_ms: float | None = None
    degraded_reason: str | None = None
    retry_reason: str | None = None
    error_type: str | None = None
    error_code: str | None = None


@dataclass(slots=True)
class _MutableRun:
    tenant_scope: str
    query_fingerprint: str | None
    run_id: str
    status: RunStatus
    outcome: RunOutcome
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    elapsed_ms: float
    boot_id: str
    worker_id: str
    topology_id: str
    topology_revision: str
    executor: str
    last_seq: int
    event_count: int
    integrity: EventIntegrity
    persistence_status: PersistenceStatus
    topology: dict[str, Any]
    events: OrderedDict[int, dict[str, Any]] = field(default_factory=OrderedDict)
    active_nodes: OrderedDict[str, int] = field(default_factory=OrderedDict)
    active_retries: OrderedDict[str, int] = field(default_factory=OrderedDict)
    failed_nodes: OrderedDict[str, None] = field(default_factory=OrderedDict)
    rollups: OrderedDict[tuple[str, str, int], _MutableRollup] = field(default_factory=OrderedDict)
    route: str | None = None
    degraded_count: int = 0
    retry_count: int = 0
    interruption_reason: str | None = None
    missing_ranges: list[tuple[int, int]] = field(default_factory=list)
    truncated_floor: int = 1


def _freeze_public(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_public(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_public(item) for item in value)
    return value


def _thaw_public(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_public(item) for item in value]
    return value


def _event_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _metric(name: str, value: float = 1.0) -> None:
    try:
        get_metrics().incr(name, value=value)
    except Exception:
        return


class RunRegistrySink:
    """A request-bound, failure-silent consumer of canonical run events."""

    __slots__ = ("_context", "_registry")

    def __init__(self, registry: "RunRegistry", context: BoundRunContext) -> None:
        self._registry = registry
        self._context = context

    def __call__(self, event: RunEvent) -> None:
        try:
            self._registry._accept(self._context, event)
        except Exception as exc:
            self._registry._record_sink_error(event.run_id, type(exc).__name__)


class RunRegistry:
    """Bounded in-memory projection of canonical :class:`RunEvent` facts."""

    def __init__(
        self,
        settings: RunHistorySettings,
        identity: RegistryIdentity,
        *,
        utcnow: Callable[[], datetime] | None = None,
        persistence_offer: Callable[[RegistryMutation], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._identity = identity
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active: OrderedDict[str, _MutableRun] = OrderedDict()
        self._recent: OrderedDict[str, _MutableRun] = OrderedDict()
        self._versions: dict[str, int] = {}
        self._event_total = 0
        self._dropped_runs = 0
        self._dropped_events = 0
        self._sink_errors = 0
        self._rejected_events = 0
        self._degraded = False
        self._persistence_offer = persistence_offer
        self._heartbeats: dict[str, tuple[bool, datetime]] = {
            identity.worker_id: (True, self._now())
        }

    def _now(self) -> datetime:
        value = self._utcnow()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def bound_sink(
        self, tenant_scope: str, query_fingerprint: str | None = None
    ) -> RunRegistrySink:
        return RunRegistrySink(
            self,
            BoundRunContext(
                tenant_scope=tenant_scope,
                query_fingerprint=query_fingerprint,
            ),
        )

    def set_persistence_offer(self, offer: Callable[[RegistryMutation], bool] | None) -> None:
        with self._lock:
            self._persistence_offer = offer

    def _accept(self, context: BoundRunContext, event: RunEvent) -> None:
        if not self._settings.enabled:
            return
        redacted = sanitize_run_event(event, self._settings)
        mutation: RegistryMutation | None = None
        if redacted.event is None:
            with self._condition:
                existing = self._find_locked(event.run_id)
                if existing is not None and existing.tenant_scope != context.tenant_scope:
                    return
                self._rejected_events += 1
                if redacted.partial_started_envelope is not None:
                    mutation = self._admit_started_locked(
                        context,
                        redacted.partial_started_envelope,
                        integrity="partial",
                    )
                else:
                    record = self._find_locked(event.run_id)
                    if record is not None and record.status == "running":
                        record.integrity = "partial"
                        self._degraded = True
                        self._changed_locked(record.run_id)
            if mutation is not None:
                self._offer_persistence(mutation)
            _metric("run_registry.events_rejected_redaction")
            return

        payload = redacted.event
        with self._condition:
            record = self._find_locked(payload["run_id"])
            if record is None:
                if payload["type"] != "run.started":
                    return
                mutation = self._admit_started_locked(
                    context,
                    payload,
                    integrity="complete" if payload["seq"] == 1 else "partial",
                )
            elif record.status != "running":
                seq = int(payload["seq"])
                if not self._seq_is_missing_locked(record, seq):
                    return
                mutation = self._apply_event_locked(context, record, payload)
            else:
                mutation = self._apply_event_locked(context, record, payload)
        if mutation is not None:
            self._offer_persistence(mutation)

    def _admit_started_locked(
        self,
        context: BoundRunContext,
        payload: dict[str, Any],
        *,
        integrity: EventIntegrity,
    ) -> RegistryMutation | None:
        run_id = payload["run_id"]
        existing = self._find_locked(run_id)
        if existing is not None:
            stored = existing.events.get(payload["seq"])
            if existing.tenant_scope != context.tenant_scope:
                return None
            if stored == payload:
                return None
            if existing.status == "running":
                existing.integrity = "partial"
                self._degraded = True
                self._changed_locked(run_id)
            return None
        if len(self._active) >= self._settings.memory_max_active_runs:
            self._dropped_runs += 1
            self._degraded = True
            _metric("run_registry.runs_dropped_capacity")
            return None

        occurred_at = _event_datetime(payload["occurred_at"])
        attributes = payload.get("attributes", {})
        topology_value = attributes.get("topology", {})
        topology = _thaw_public(topology_value) if isinstance(topology_value, Mapping) else {}
        executor = str(attributes.get("executor") or topology.get("executor") or "unknown")
        persistence: PersistenceStatus = (
            "pending" if self._settings.persistence_enabled else "memory_only"
        )
        record = _MutableRun(
            tenant_scope=context.tenant_scope,
            query_fingerprint=context.query_fingerprint,
            run_id=run_id,
            status="running",
            outcome=self._outcome(attributes.get("outcome")),
            started_at=occurred_at,
            updated_at=occurred_at,
            finished_at=None,
            elapsed_ms=float(payload["elapsed_ms"]),
            boot_id=self._identity.boot_id,
            worker_id=self._identity.worker_id,
            topology_id=payload["topology_id"],
            topology_revision=payload["topology_revision"],
            executor=executor,
            last_seq=int(payload["seq"]),
            event_count=1,
            integrity=integrity,
            persistence_status=persistence,
            topology=topology,
        )
        if record.last_seq > 1:
            self._add_missing_range_locked(record, 1, record.last_seq - 1)
        record.events[int(payload["seq"])] = _thaw_public(payload)
        self._event_total += 1
        self._active[run_id] = record
        self._versions.setdefault(run_id, 0)
        self._changed_locked(run_id)
        self._enforce_event_caps_locked(record)
        _metric("run_registry.runs_accepted")
        _metric("run_registry.events_accepted")
        return self._mutation_locked(context, record, payload)

    def _apply_event_locked(
        self,
        context: BoundRunContext,
        record: _MutableRun,
        payload: dict[str, Any],
    ) -> RegistryMutation | None:
        if context.tenant_scope != record.tenant_scope:
            return None
        seq = int(payload["seq"])
        terminal_fill = record.status != "running"
        if terminal_fill and not self._seq_is_missing_locked(record, seq):
            return None

        if (
            payload["topology_id"] != record.topology_id
            or payload["topology_revision"] != record.topology_revision
        ):
            record.integrity = "partial"
            self._degraded = True
            self._changed_locked(record.run_id)
            return None

        stored = record.events.get(seq)
        if stored is not None:
            if stored != payload:
                record.integrity = "partial"
                self._degraded = True
                self._changed_locked(record.run_id)
            return None
        previous_last = record.last_seq
        fills_gap = seq <= previous_last and self._seq_is_missing_locked(record, seq)
        if fills_gap:
            record.events[seq] = _thaw_public(payload)
            record.event_count += 1
            self._event_total += 1
            record.updated_at = max(record.updated_at, _event_datetime(payload["occurred_at"]))
            record.elapsed_ms = max(record.elapsed_ms, float(payload["elapsed_ms"]))
            self._fill_missing_seq_locked(record, seq)
            if (
                payload["type"] in {"run.completed", "run.failed", "run.cancelled"}
                and record.status == "running"
            ):
                self._reduce_event_locked(record, payload)
            self._rebuild_projection_locked(record)
            self._changed_locked(record.run_id)
            self._enforce_event_caps_locked(record)
            _metric("run_registry.events_accepted")
            return self._mutation_locked(context, record, payload)
        if seq <= previous_last:
            return None
        if seq != previous_last + 1:
            record.integrity = "partial"
            self._degraded = True
            self._add_missing_range_locked(record, previous_last + 1, seq - 1)
            _metric("run_registry.seq_gaps")

        record.events[seq] = _thaw_public(payload)
        record.event_count += 1
        self._event_total += 1
        if seq > previous_last:
            record.last_seq = seq
            record.updated_at = max(record.updated_at, _event_datetime(payload["occurred_at"]))
            record.elapsed_ms = max(record.elapsed_ms, float(payload["elapsed_ms"]))
            self._reduce_event_locked(record, payload)
        self._changed_locked(record.run_id)
        self._enforce_event_caps_locked(record)
        _metric("run_registry.events_accepted")
        return self._mutation_locked(context, record, payload)

    @staticmethod
    def _outcome(value: Any) -> RunOutcome:
        if value in {"answered", "abstained"}:
            return value
        return "unknown"

    def _reduce_event_locked(
        self,
        record: _MutableRun,
        payload: dict[str, Any],
        *,
        project_only: bool = False,
    ) -> None:
        event_type = payload["type"]
        node_id = payload.get("node_id")
        attempt = int(payload.get("attempt") or 1)
        elapsed = float(payload["elapsed_ms"])
        attributes = payload.get("attributes", {})
        error = payload.get("error", {})
        reason = attributes.get("reason")
        reason_code = reason if isinstance(reason, str) else None

        if event_type == "node.started" and node_id is not None:
            record.active_nodes[node_id] = attempt
            record.rollups[("node", node_id, attempt)] = _MutableRollup(
                node_id=node_id,
                attempt=attempt,
                status="running",
                started_elapsed_ms=elapsed,
            )
        elif (
            event_type
            in {
                "node.completed",
                "node.failed",
                "node.cancelled",
                "node.skipped",
            }
            and node_id is not None
        ):
            record.active_nodes.pop(node_id, None)
            key = ("node", node_id, attempt)
            rollup = record.rollups.get(key)
            if rollup is None:
                rollup = _MutableRollup(node_id=node_id, attempt=attempt, status="unknown")
                record.rollups[key] = rollup
            rollup.status = event_type.removeprefix("node.")
            rollup.finished_elapsed_ms = elapsed
            rollup.duration_ms = payload.get("duration_ms")
            rollup.error_type = error.get("type")
            rollup.error_code = error.get("code")
            if event_type == "node.failed":
                record.failed_nodes.setdefault(node_id, None)
        elif event_type == "retry.started" and node_id is not None:
            record.retry_count += 1
            record.active_retries[node_id] = attempt
            record.rollups[("retry", node_id, attempt)] = _MutableRollup(
                node_id=node_id,
                attempt=attempt,
                status="retry_running",
                started_elapsed_ms=elapsed,
                retry_reason=reason_code,
            )
        elif (
            event_type
            in {
                "retry.completed",
                "retry.failed",
                "retry.skipped",
            }
            and node_id is not None
        ):
            record.active_retries.pop(node_id, None)
            key = ("retry", node_id, attempt)
            rollup = record.rollups.get(key)
            if rollup is None:
                rollup = _MutableRollup(
                    node_id=node_id,
                    attempt=attempt,
                    status="retry_unknown",
                )
                record.rollups[key] = rollup
            rollup.status = f"retry_{event_type.removeprefix('retry.')}"
            rollup.finished_elapsed_ms = elapsed
            rollup.duration_ms = payload.get("duration_ms")
            rollup.retry_reason = rollup.retry_reason or reason_code
            rollup.error_type = error.get("type")
            rollup.error_code = error.get("code")
        elif event_type == "route.selected":
            route = (
                attributes.get("effective_route")
                or attributes.get("effective")
                or attributes.get("route")
                or attributes.get("configured_route")
            )
            if isinstance(route, str):
                record.route = route
        elif event_type == "degraded":
            record.degraded_count += 1
            if node_id is not None:
                for key in reversed(record.rollups):
                    rollup = record.rollups[key]
                    if rollup.node_id == node_id:
                        rollup.degraded_reason = reason_code
                        break

        terminal_status: dict[str, RunStatus] = {
            "run.completed": "completed",
            "run.failed": "failed",
            "run.cancelled": "cancelled",
        }
        status = terminal_status.get(event_type)
        if status is not None and not project_only:
            record.status = status
            record.outcome = self._outcome(attributes.get("outcome"))
            record.finished_at = _event_datetime(payload["occurred_at"])
            record.active_nodes.clear()
            record.active_retries.clear()
            self._active.pop(record.run_id, None)
            self._recent[record.run_id] = record
            self._recent.move_to_end(record.run_id)
            self._enforce_recent_cap_locked()

    def _mutation_locked(
        self,
        context: BoundRunContext,
        record: _MutableRun,
        payload: Mapping[str, Any],
    ) -> RegistryMutation:
        return RegistryMutation(
            context=context,
            summary=self._summary_locked(record, self._now()),
            topology=_freeze_public(record.topology),
            event=_freeze_public(payload),
        )

    def _offer_persistence(self, mutation: RegistryMutation) -> None:
        if not self._settings.persistence_enabled:
            return
        with self._lock:
            offer = self._persistence_offer
        if offer is None:
            return
        try:
            accepted = bool(offer(mutation))
        except Exception:
            accepted = False
        if not accepted:
            self.mark_persistence_gap(mutation.context.tenant_scope, mutation.summary.run_id)

    def _record_sink_error(self, run_id: str, exception_type: str) -> None:
        with self._lock:
            self._sink_errors += 1
            self._degraded = True
        _metric("run_registry.sink_errors")
        _LOGGER.warning(
            "run registry sink failed",
            extra={
                "run_suffix": run_id[-8:],
                "exception_type": exception_type[:64],
            },
        )

    def _changed_locked(self, run_id: str) -> None:
        self._versions[run_id] = self._versions.get(run_id, 0) + 1
        self._condition.notify_all()

    def current_version(self, run_id: str) -> int:
        with self._lock:
            return self._versions.get(run_id, 0)

    def wait_for_change(self, run_id: str, version: int, timeout_s: float) -> int:
        timeout = max(0.0, timeout_s)
        with self._condition:
            self._condition.wait_for(
                lambda: self._versions.get(run_id, 0) != version,
                timeout=timeout,
            )
            return self._versions.get(run_id, 0)

    def _find_locked(self, run_id: str) -> _MutableRun | None:
        return self._active.get(run_id) or self._recent.get(run_id)

    @staticmethod
    def _seq_is_missing_locked(record: _MutableRun, seq: int) -> bool:
        return any(start <= seq <= end for start, end in record.missing_ranges)

    def _add_missing_range_locked(self, record: _MutableRun, start: int, end: int) -> None:
        if start > end:
            return
        merged: list[tuple[int, int]] = []
        for range_start, range_end in sorted([*record.missing_ranges, (start, end)]):
            if merged and range_start <= merged[-1][1] + 1:
                previous_start, previous_end = merged[-1]
                merged[-1] = (previous_start, max(previous_end, range_end))
            else:
                merged.append((range_start, range_end))
        max_ranges = min(64, self._settings.memory_max_events_per_run)
        record.missing_ranges = merged[:max_ranges]

    @staticmethod
    def _fill_missing_seq_locked(record: _MutableRun, seq: int) -> None:
        updated: list[tuple[int, int]] = []
        for start, end in record.missing_ranges:
            if not start <= seq <= end:
                updated.append((start, end))
                continue
            if start < seq:
                updated.append((start, seq - 1))
            if seq < end:
                updated.append((seq + 1, end))
        record.missing_ranges = updated

    def _rebuild_projection_locked(self, record: _MutableRun) -> None:
        terminal = record.status != "running"
        record.active_nodes.clear()
        record.active_retries.clear()
        record.failed_nodes.clear()
        record.rollups.clear()
        record.route = None
        record.degraded_count = 0
        record.retry_count = 0
        for _, payload in sorted(record.events.items()):
            self._reduce_event_locked(record, payload, project_only=True)
        if terminal:
            record.active_nodes.clear()
            record.active_retries.clear()

    def _earliest_available_seq_locked(self, record: _MutableRun) -> int:
        if record.truncated_floor > 1:
            return record.truncated_floor
        if record.missing_ranges:
            return record.missing_ranges[0][1] + 1
        if record.integrity == "partial":
            return 1
        if record.integrity == "complete":
            return 1
        tail = [seq for seq in record.events if seq != 1]
        if tail:
            return min(tail)
        return record.last_seq + 1

    def _attention_locked(
        self, record: _MutableRun, as_of: datetime, slow_ms: int
    ) -> tuple[str, ...]:
        attention: list[str] = []
        if record.status in {"failed", "interrupted"}:
            attention.append("error")
        if record.status == "interrupted":
            attention.append("interrupted")
        if record.status == "cancelled":
            attention.append("cancelled")
        elapsed = record.elapsed_ms
        if record.status == "running":
            elapsed = max(
                elapsed,
                max(0.0, (as_of - record.started_at).total_seconds() * 1000.0),
            )
        if elapsed >= slow_ms:
            attention.append("slow")
        if self._is_stuck_locked(record, as_of):
            attention.append("stuck")
        return tuple(attention)

    def _is_stuck_locked(self, record: _MutableRun, as_of: datetime) -> bool:
        if record.status != "running":
            return False
        heartbeat = self._heartbeats.get(record.worker_id)
        if heartbeat is None:
            return False
        alive, recorded_at = heartbeat
        if not alive:
            return False
        if (as_of - recorded_at).total_seconds() > self._settings.worker_stale_after_s:
            return False
        return (as_of - record.updated_at).total_seconds() >= self._settings.stuck_after_s

    def _summary_locked(
        self,
        record: _MutableRun,
        as_of: datetime,
        *,
        slow_ms: int | None = None,
    ) -> RunSummary:
        threshold = self._settings.slow_threshold_ms if slow_ms is None else slow_ms
        elapsed = record.elapsed_ms
        if record.status == "running":
            elapsed = max(
                elapsed,
                max(0.0, (as_of - record.started_at).total_seconds() * 1000.0),
            )
        current = tuple(sorted(set(record.active_nodes) | set(record.active_retries))[:32])
        failed = tuple(sorted(record.failed_nodes)[:32])
        return RunSummary(
            schema_version=1,
            run_id=record.run_id,
            status=record.status,
            outcome=record.outcome,
            started_at=record.started_at,
            updated_at=record.updated_at,
            finished_at=record.finished_at,
            elapsed_ms=elapsed,
            boot_id=record.boot_id,
            worker_id=record.worker_id,
            topology_id=record.topology_id,
            topology_revision=record.topology_revision,
            executor=record.executor,
            last_seq=record.last_seq,
            event_count=record.event_count,
            earliest_available_seq=self._earliest_available_seq_locked(record),
            current_node_ids=current,
            failed_node_ids=failed,
            route=record.route,
            degraded_count=record.degraded_count,
            retry_count=record.retry_count,
            attention=self._attention_locked(record, as_of, threshold),
            event_integrity=record.integrity,
            persistence_status=record.persistence_status,
            interruption_reason=record.interruption_reason,
            query_fingerprint=record.query_fingerprint,
        )

    @staticmethod
    def _rollup_snapshot(rollup: _MutableRollup) -> NodeRollup:
        return NodeRollup(
            node_id=rollup.node_id,
            attempt=rollup.attempt,
            status=rollup.status,
            started_elapsed_ms=rollup.started_elapsed_ms,
            finished_elapsed_ms=rollup.finished_elapsed_ms,
            duration_ms=rollup.duration_ms,
            degraded_reason=rollup.degraded_reason,
            retry_reason=rollup.retry_reason,
            error_type=rollup.error_type,
            error_code=rollup.error_code,
        )

    def get_detail(self, tenant_scope: str, run_id: str) -> RunDetail | None:
        with self._lock:
            record = self._find_locked(run_id)
            if record is None or record.tenant_scope != tenant_scope:
                return None
            summary = self._summary_locked(record, self._now())
            topology = _freeze_public(record.topology)
            rollups = tuple(self._rollup_snapshot(item) for item in record.rollups.values())
            history = RunHistory(
                event_integrity=record.integrity,
                earliest_available_seq=summary.earliest_available_seq,
                last_seq=record.last_seq,
                persistence_status=record.persistence_status,
            )
            return RunDetail(
                schema_version=1,
                summary=summary,
                topology=topology,
                node_rollup=rollups,
                history=history,
            )

    def get_events(
        self,
        tenant_scope: str,
        run_id: str,
        after_seq: int,
        limit: int,
    ) -> RunEventSlice | None:
        with self._lock:
            record = self._find_locked(run_id)
            if record is None or record.tenant_scope != tenant_scope:
                return None
            bounded_limit = max(1, min(limit, self._settings.events_max_page_size))
            events = tuple(
                _freeze_public(payload)
                for seq, payload in sorted(record.events.items())
                if seq > after_seq
            )[:bounded_limit]
            next_after = int(events[-1]["seq"]) if events else after_seq
            return RunEventSlice(
                schema_version=1,
                run_id=run_id,
                events=events,
                after_seq=next_after,
                latest_seq=record.last_seq,
                terminal=record.status != "running",
                timed_out=False,
                history_state=("complete" if record.integrity == "complete" else "partial"),
                earliest_available_seq=self._earliest_available_seq_locked(record),
                persistence_status=record.persistence_status,
            )

    def list_runs(
        self, query: RunListQuery, *, before: tuple[datetime, str] | None = None
    ) -> list[RunSummary]:
        with self._lock:
            as_of = query.as_of or self._now()
            slow_ms = query.slow_ms or self._settings.slow_threshold_ms
            records = [*self._active.values(), *self._recent.values()]
            summaries: list[RunSummary] = []
            for record in records:
                if record.tenant_scope != query.tenant_scope:
                    continue
                if record.started_at > as_of:
                    continue
                if before is not None and (record.started_at, record.run_id) >= before:
                    continue
                if query.statuses and record.status not in query.statuses:
                    continue
                if query.started_after and record.started_at < query.started_after:
                    continue
                if query.started_before and record.started_at >= query.started_before:
                    continue
                if query.fingerprint is not None and record.query_fingerprint != query.fingerprint:
                    continue
                summary = self._summary_locked(record, as_of, slow_ms=slow_ms)
                if query.view == "active" and summary.status != "running":
                    continue
                if query.view == "errors" and summary.status not in {
                    "failed",
                    "interrupted",
                }:
                    continue
                if query.view == "slow" and "slow" not in summary.attention:
                    continue
                if query.view == "stuck" and "stuck" not in summary.attention:
                    continue
                summaries.append(summary)
            summaries.sort(key=lambda item: (item.started_at, item.run_id), reverse=True)
            internal_max = self._settings.api_max_page_size + 1
            limit = max(1, min(query.limit, internal_max))
            return summaries[:limit]

    def record_worker_heartbeat(
        self,
        worker_id: str,
        *,
        alive: bool,
        at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._heartbeats[worker_id] = (alive, at or self._now())

    def recover_interrupted(
        self,
        tenant_scope: str,
        run_id: str,
        *,
        reason: str,
        finished_at: datetime | None = None,
    ) -> bool:
        with self._condition:
            record = self._active.get(run_id)
            if record is None or record.tenant_scope != tenant_scope or record.status != "running":
                return False
            ended = finished_at or self._now()
            record.status = "interrupted"
            record.finished_at = ended
            record.updated_at = max(record.updated_at, ended)
            record.elapsed_ms = max(
                record.elapsed_ms,
                max(0.0, (ended - record.started_at).total_seconds() * 1000.0),
            )
            record.interruption_reason = reason[:96]
            record.integrity = "partial"
            record.active_nodes.clear()
            record.active_retries.clear()
            self._active.pop(run_id, None)
            self._recent[run_id] = record
            self._enforce_recent_cap_locked()
            self._changed_locked(run_id)
            return True

    def mark_persistence_gap(self, tenant_scope: str, run_id: str) -> bool:
        with self._condition:
            record = self._find_locked(run_id)
            if record is None or record.tenant_scope != tenant_scope:
                return False
            record.persistence_status = "partial"
            self._degraded = True
            self._changed_locked(run_id)
            return True

    def mark_persistence_durable(
        self,
        tenant_scope: str,
        run_id: str,
        through_seq: int | None = None,
    ) -> bool:
        with self._condition:
            record = self._find_locked(run_id)
            if record is None or record.tenant_scope != tenant_scope:
                return False
            if through_seq is None or through_seq >= record.last_seq:
                record.persistence_status = "durable"
                self._changed_locked(run_id)
            return True

    def mark_persistence_unavailable(self) -> None:
        with self._condition:
            self._degraded = True
            records = (*self._active.values(), *self._recent.values())
            for record in records:
                if record.persistence_status != "memory_only":
                    record.persistence_status = "unavailable"
                    self._changed_locked(record.run_id)

    def mark_integrity_gap(self, tenant_scope: str, run_id: str) -> bool:
        with self._condition:
            record = self._find_locked(run_id)
            if record is None or record.tenant_scope != tenant_scope:
                return False
            record.integrity = "partial"
            self._degraded = True
            self._changed_locked(run_id)
            return True

    def hydrate(
        self,
        tenant_scope: str,
        detail: RunDetail,
        events: Sequence[Mapping[str, Any]] = (),
    ) -> bool:
        summary = detail.summary
        if summary.status == "running":
            return False
        with self._condition:
            if self._find_locked(summary.run_id) is not None:
                return False
            record = _MutableRun(
                tenant_scope=tenant_scope,
                query_fingerprint=summary.query_fingerprint,
                run_id=summary.run_id,
                status=summary.status,
                outcome=summary.outcome,
                started_at=summary.started_at,
                updated_at=summary.updated_at,
                finished_at=summary.finished_at,
                elapsed_ms=summary.elapsed_ms,
                boot_id=summary.boot_id,
                worker_id=summary.worker_id,
                topology_id=summary.topology_id,
                topology_revision=summary.topology_revision,
                executor=summary.executor,
                last_seq=summary.last_seq,
                event_count=summary.event_count,
                integrity=summary.event_integrity,
                persistence_status=summary.persistence_status,
                topology=_thaw_public(detail.topology),
                route=summary.route,
                degraded_count=summary.degraded_count,
                retry_count=summary.retry_count,
                interruption_reason=summary.interruption_reason,
                truncated_floor=max(1, summary.earliest_available_seq),
            )
            for node_id in summary.failed_node_ids:
                record.failed_nodes[node_id] = None
            for item in detail.node_rollup:
                kind = "retry" if item.status.startswith("retry_") else "node"
                record.rollups[(kind, item.node_id, item.attempt)] = _MutableRollup(
                    node_id=item.node_id,
                    attempt=item.attempt,
                    status=item.status,
                    started_elapsed_ms=item.started_elapsed_ms,
                    finished_elapsed_ms=item.finished_elapsed_ms,
                    duration_ms=item.duration_ms,
                    degraded_reason=item.degraded_reason,
                    retry_reason=item.retry_reason,
                    error_type=item.error_type,
                    error_code=item.error_code,
                )
            for event in events:
                payload = _thaw_public(event)
                record.events[int(payload["seq"])] = payload
            self._event_total += len(record.events)
            self._recent[record.run_id] = record
            self._versions.setdefault(record.run_id, 0)
            self._changed_locked(record.run_id)
            self._enforce_recent_cap_locked()
            self._enforce_global_event_cap_locked()
            return True

    def cleanup(self) -> int:
        with self._condition:
            now = self._now()
            expired = [
                run_id
                for run_id, record in self._recent.items()
                if record.finished_at is not None
                and (now - record.finished_at).total_seconds()
                >= self._settings.memory_terminal_ttl_s
            ]
            for run_id in expired:
                self._evict_recent_locked(run_id)
            return len(expired)

    def _enforce_recent_cap_locked(self) -> None:
        while len(self._recent) > self._settings.memory_max_recent_runs:
            run_id = next(iter(self._recent))
            self._evict_recent_locked(run_id)

    def _evict_recent_locked(self, run_id: str) -> None:
        record = self._recent.pop(run_id, None)
        if record is None:
            return
        self._event_total -= len(record.events)
        self._versions.pop(run_id, None)
        self._condition.notify_all()

    def _enforce_event_caps_locked(self, record: _MutableRun) -> None:
        limit = self._settings.memory_max_events_per_run
        if len(record.events) > limit:
            ordered = sorted(record.events)
            keep = set(ordered[-(limit - 1) :])
            if 1 in record.events:
                keep.add(1)
            for seq in ordered:
                if seq not in keep:
                    record.events.pop(seq, None)
                    self._event_total -= 1
                    self._dropped_events += 1
            tail = [seq for seq in record.events if seq != 1]
            record.truncated_floor = min(tail) if tail else record.last_seq + 1
            record.missing_ranges = []
            record.integrity = "partial"
            self._degraded = True
            _metric("run_registry.events_dropped_capacity")
        self._enforce_global_event_cap_locked()

    def _enforce_global_event_cap_locked(self) -> None:
        cap = self._settings.memory_max_events_total
        for record in self._recent.values():
            if self._event_total <= cap:
                return
            removed = len(record.events)
            if removed:
                record.events.clear()
                record.truncated_floor = record.last_seq + 1
                record.missing_ranges = []
                record.integrity = "partial"
                self._event_total -= removed
                self._dropped_events += removed
        while self._event_total > cap:
            changed = False
            for record in self._active.values():
                if self._event_total <= cap:
                    break
                ordered = sorted(record.events)
                removable = [seq for seq in ordered if seq != 1]
                if not removable and ordered:
                    removable = ordered
                if removable:
                    record.events.pop(removable[0], None)
                    tail = [seq for seq in record.events if seq != 1]
                    record.truncated_floor = min(tail) if tail else record.last_seq + 1
                    record.missing_ranges = []
                    record.integrity = "partial"
                    self._event_total -= 1
                    self._dropped_events += 1
                    changed = True
            if not changed:
                break
        if self._dropped_events:
            self._degraded = True

    def health_snapshot(self) -> RegistryHealth:
        with self._lock:
            enabled = self._settings.enabled
            status: Literal["ok", "degraded", "disabled"]
            if not enabled:
                status = "disabled"
            elif self._degraded:
                status = "degraded"
            else:
                status = "ok"
            return RegistryHealth(
                schema_version=1,
                status=status,
                enabled=enabled,
                boot_id=self._identity.boot_id,
                worker_id=self._identity.worker_id,
                memory=RegistryMemoryHealth(
                    active_runs=len(self._active),
                    recent_runs=len(self._recent),
                    event_count=self._event_total,
                    dropped_runs=self._dropped_runs,
                    dropped_events=self._dropped_events,
                ),
                sink_errors=self._sink_errors,
                rejected_events=self._rejected_events,
            )


__all__ = [
    "BoundRunContext",
    "RedactionResult",
    "EventIntegrity",
    "RegistryIdentity",
    "NodeRollup",
    "PersistenceStatus",
    "derive_tenant_scope",
    "fingerprint_query",
    "RegistryHealth",
    "RegistryMemoryHealth",
    "RegistryMutation",
    "RunDetail",
    "RunEventSlice",
    "RunHistory",
    "RunListQuery",
    "RunRegistry",
    "RunRegistrySink",
    "RunStatus",
    "sanitize_run_event",
    "RunSummary",
]
