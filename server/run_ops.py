"""Security boundary, signed cursor codec, and public DTOs for Runs Ops.

This module intentionally contains no registry/store query service, router, or app
bootstrap behavior. Those integrations are owned by later Route B tasks.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import threading
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    WithJsonSchema,
    field_validator,
    model_serializer,
)
from starlette.requests import Request

from config.settings import resolve_tenant
from core.run_events import EventType
from core.run_history_store import RunHistoryReadError, RunHistoryStore, StoredRun
from core.run_registry import (
    EventIntegrity,
    NodeRollup,
    PersistenceStatus,
    RegistryIdentity,
    RunDetail,
    RunHistory,
    RunListQuery,
    RunOutcome,
    RunRegistry,
    RunStatus,
    RunSummary,
    RunView,
    derive_tenant_scope,
)


CursorErrorCode = Literal[
    "cursor_invalid",
    "cursor_expired",
    "cursor_scope_mismatch",
    "cursor_filter_mismatch",
]
HistoryState = Literal["complete", "partial", "expired"]

_CURSOR_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_CURSOR_TTL_S = 3600
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
_EVENT_ATTRIBUTE_KEYS = frozenset(
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


def _settings_run_history(settings: Any) -> Any:
    return getattr(settings, "run_history", settings)


def configured_operator_token(settings: Any) -> str | None:
    configured = getattr(_settings_run_history(settings), "ops_bearer_token", None)
    if configured is None:
        return None
    get_secret_value = getattr(configured, "get_secret_value", None)
    value = get_secret_value() if callable(get_secret_value) else configured
    return value if isinstance(value, str) and value else None


def client_is_loopback(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def bearer_credential(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
        return None
    return parts[1]


def require_operator_access(request: Request, settings: Any) -> None:
    """Permit loopback or require the configured Bearer token for remote clients."""

    if client_is_loopback(request):
        return

    configured = configured_operator_token(settings)
    if configured is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "operator_access_forbidden", "message": "远程运行历史访问未启用"},
        )

    provided = bearer_credential(request)
    if provided is None or not hmac.compare_digest(
        provided.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "operator_auth_required", "message": "需要有效的 Bearer 凭据"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def resolve_request_tenant(request: Request, tenant_id: str | None, settings: Any) -> str:
    """Resolve exactly one tenant without trusting proxy forwarding headers."""

    header_tenant = request.headers.get("x-rag4c-tenant")
    if header_tenant is not None and tenant_id is not None and header_tenant != tenant_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "tenant_mismatch", "message": "tenant header 与 query 不一致"},
        )
    if not client_is_loopback(request) and not header_tenant:
        raise HTTPException(
            status_code=400,
            detail={"code": "tenant_required", "message": "远程访问必须提供 X-RAG4C-Tenant"},
        )
    selected = header_tenant if header_tenant is not None else tenant_id
    return resolve_tenant(selected, settings)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64(value: str) -> bytes:
    if not value or _CURSOR_PART.fullmatch(value) is None:
        raise ValueError("invalid base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url") from exc
    if _b64(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


class CursorError(ValueError):
    """A safe, stable cursor failure code for the HTTP layer to map to 400."""

    def __init__(self, code: CursorErrorCode) -> None:
        self.code = code
        super().__init__(code)


class CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    v: Literal[1]
    tenant_scope: str = Field(min_length=1)
    filter_hash: str = Field(min_length=1)
    as_of_us: int = Field(ge=0)
    last_started_at_us: int = Field(ge=0)
    last_run_id: str = Field(min_length=1)
    exp: int = Field(ge=0)

    @field_validator("v", mode="before")
    @classmethod
    def _version_is_exact_integer_one(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("cursor version must be integer 1")
        return value


class CursorCodec:
    def __init__(self, key: bytes, ttl_s: int = _DEFAULT_CURSOR_TTL_S) -> None:
        if not isinstance(key, bytes) or not key:
            raise ValueError("cursor key must be non-empty bytes")
        if type(ttl_s) is not int or ttl_s <= 0:
            raise ValueError("cursor ttl must be a positive integer")
        self._key = key
        self._ttl_s = ttl_s

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def _validate_ttl(self, payload: CursorPayload) -> None:
        latest_exp = payload.as_of_us // 1_000_000 + self._ttl_s
        if payload.exp > latest_exp:
            raise CursorError("cursor_invalid")


    def encode(self, payload: CursorPayload) -> str:
        self._validate_ttl(payload)
        canonical = json.dumps(
            payload.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        signature = hmac.new(self._key, canonical, hashlib.sha256).digest()
        return f"{_b64(canonical)}.{_b64(signature)}"

    def decode(
        self,
        token: str,
        expected_scope: str,
        expected_filter_hash: str,
        now: datetime,
    ) -> CursorPayload:
        try:
            if type(token) is not str or token.count(".") != 1:
                raise ValueError("invalid cursor shape")
            payload_part, signature_part = token.split(".")
            canonical = _decode_b64(payload_part)
            signature = _decode_b64(signature_part)
            expected_signature = hmac.new(self._key, canonical, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError("invalid cursor signature")
            decoded_json = json.loads(canonical.decode("utf-8"))
            if type(decoded_json) is not dict:
                raise ValueError("cursor payload must be an object")
            recanonical = json.dumps(
                decoded_json,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            if recanonical != canonical:
                raise ValueError("cursor payload is not canonical")
            payload = CursorPayload.model_validate(decoded_json)
            self._validate_ttl(payload)
        except CursorError:
            raise
        except Exception as exc:
            raise CursorError("cursor_invalid") from exc

        if now.tzinfo is None or now.utcoffset() is None:
            raise CursorError("cursor_invalid")
        if payload.exp <= int(now.timestamp()):
            raise CursorError("cursor_expired")
        if payload.tenant_scope != expected_scope:
            raise CursorError("cursor_scope_mismatch")
        if payload.filter_hash != expected_filter_hash:
            raise CursorError("cursor_filter_mismatch")
        return payload


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDateTime = Annotated[
    datetime,
    AfterValidator(_as_utc),
    PlainSerializer(_utc_rfc3339, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}, mode="serialization"),
]


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


def _safe_json_value(value: Any, *, reject_keys: bool) -> JsonValue:
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("JSON values must be finite")
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item, reject_keys=True) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str or (reject_keys and _is_forbidden_nested_key(key)):
                raise ValueError("sensitive or invalid JSON key")
            result[key] = _safe_json_value(item, reject_keys=True)
        return result
    raise ValueError("value is not JSON-safe")


def _safe_json_object(value: Any, *, reject_keys: bool) -> dict[str, JsonValue]:
    checked = _safe_json_value(value, reject_keys=reject_keys)
    if type(checked) is not dict:
        raise ValueError("value must be a JSON object")
    return checked


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, from_attributes=True)


class RunMemoryHealthResponse(_PublicModel):
    active_runs: int = Field(ge=0)
    recent_runs: int = Field(ge=0)
    event_count: int = Field(ge=0)
    dropped_runs: int = Field(ge=0)
    dropped_events: int = Field(ge=0)


class RunPersistenceHealthResponse(_PublicModel):
    enabled: bool
    state: Literal["ready", "read_only", "memory_only", "disabled"]
    database: str
    wal: bool
    writer_queue_depth: int = Field(ge=0)
    last_commit_at: UtcDateTime | None
    commit_lag_ms: float | None = Field(ge=0)
    dropped_mutations: int = Field(ge=0)
    quick_check: Literal["ok", "failed", "not_run"]


class RunHeartbeatHealthResponse(_PublicModel):
    interval_s: int = Field(ge=1)
    stale_after_s: int = Field(ge=1)
    last_heartbeat_at: UtcDateTime | None


class RunHealthRetentionResponse(_PublicModel):
    days: int = Field(ge=1)
    max_runs: int = Field(ge=1)
    memory_terminal_ttl_s: int = Field(ge=0)


class RunListRetentionResponse(_PublicModel):
    days: int = Field(ge=1)
    max_runs: int = Field(ge=1)


class RunHealthResponse(_PublicModel):
    schema_version: Literal[1] = 1
    status: Literal["ok", "degraded", "disabled"]
    enabled: bool
    boot_id: str
    worker_id: str
    memory: RunMemoryHealthResponse
    persistence: RunPersistenceHealthResponse
    heartbeat: RunHeartbeatHealthResponse
    retention: RunHealthRetentionResponse
    scope_stability: Literal["installation", "boot"]


class RunSummaryResponse(_PublicModel):
    schema_version: Literal[1] = 1
    run_id: str
    status: RunStatus
    outcome: RunOutcome
    started_at: UtcDateTime
    updated_at: UtcDateTime
    finished_at: UtcDateTime | None
    elapsed_ms: float = Field(ge=0)
    boot_id: str
    worker_id: str
    topology_id: str
    topology_revision: str
    executor: str
    last_seq: int = Field(ge=0)
    event_count: int = Field(ge=0)
    earliest_available_seq: int = Field(ge=1)
    current_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    route: str | None
    degraded_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    attention: tuple[str, ...]
    event_integrity: EventIntegrity
    persistence_status: PersistenceStatus
    interruption_reason: str | None
    query_fingerprint: str | None = None

    @model_serializer(mode="wrap")
    def _omit_missing_fingerprint(self, handler: SerializerFunctionWrapHandler):
        serialized = handler(self)
        if self.query_fingerprint is None:
            serialized.pop("query_fingerprint", None)
        return serialized


class RunListResponse(_PublicModel):
    schema_version: Literal[1] = 1
    items: tuple[RunSummaryResponse, ...]
    next_cursor: str | None
    as_of: UtcDateTime
    source: Literal["memory", "sqlite", "memory+sqlite"]
    history_state: HistoryState
    retention: RunListRetentionResponse


class RunTopologyNodeResponse(_PublicModel):
    id: str
    label: str
    group: Literal["input", "understand", "retrieve", "generate", "verify", "output", "extension"]
    description: str
    optional: bool
    repeatable: bool
    available: bool
    plugin: str | None = None
    attributes: dict[str, JsonValue]

    @field_validator("attributes", mode="before")
    @classmethod
    def _attributes_are_safe(cls, value: Any) -> dict[str, JsonValue]:
        return _safe_json_object(value, reject_keys=True)


class RunTopologyEdgeResponse(_PublicModel):
    id: str
    source: str
    target: str
    kind: Literal["dependency", "conditional", "retry", "failure"]
    label: str | None = None


class RunTopologyResponse(_PublicModel):
    id: str
    revision: str
    executor: Literal["sequential_stream", "sequential", "langgraph", "cache_replay"]
    nodes: tuple[RunTopologyNodeResponse, ...]
    edges: tuple[RunTopologyEdgeResponse, ...]


class RunNodeRollupResponse(_PublicModel):
    node_id: str
    attempt: int = Field(ge=1)
    status: str
    started_elapsed_ms: float | None = Field(ge=0)
    finished_elapsed_ms: float | None = Field(ge=0)
    duration_ms: float | None = Field(ge=0)
    degraded_reason: str | None
    retry_reason: str | None
    error_type: str | None
    error_code: str | None


class RunHistoryResponse(_PublicModel):
    event_integrity: EventIntegrity
    earliest_available_seq: int = Field(ge=1)
    last_seq: int = Field(ge=0)
    persistence_status: PersistenceStatus


class RunDetailResponse(_PublicModel):
    schema_version: Literal[1] = 1
    summary: RunSummaryResponse
    topology: RunTopologyResponse
    node_rollup: tuple[RunNodeRollupResponse, ...]
    history: RunHistoryResponse


class RunEventErrorResponse(_PublicModel):
    type: str
    code: str | None = None
    recoverable: bool


class RunEventResponse(_PublicModel):
    schema_version: Literal[1] = 1
    run_id: str
    seq: int = Field(ge=1)
    occurred_at: UtcDateTime
    elapsed_ms: float = Field(ge=0)
    topology_id: str
    topology_revision: str
    type: EventType
    node_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    duration_ms: float | None = Field(default=None, ge=0)
    attributes: dict[str, JsonValue]
    error: RunEventErrorResponse | None = None

    @field_validator("attributes", mode="before")
    @classmethod
    def _attributes_are_allowlisted(cls, value: Any) -> dict[str, JsonValue]:
        attributes = _safe_json_object(value, reject_keys=False)
        if any(key not in _EVENT_ATTRIBUTE_KEYS for key in attributes):
            raise ValueError("event attributes contain an unknown key")
        return {
            key: _safe_json_value(item, reject_keys=True) for key, item in attributes.items()
        }


class RunEventsResponse(_PublicModel):
    schema_version: Literal[1] = 1
    run_id: str
    events: tuple[RunEventResponse, ...]
    after_seq: int = Field(ge=0)
    latest_seq: int = Field(ge=0)
    terminal: bool
    timed_out: bool
    history_state: HistoryState
    earliest_available_seq: int = Field(ge=1)
    persistence_status: PersistenceStatus
    retry_after_ms: int = Field(default=500, ge=0)


class RunNotFoundError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            detail={
                "code": "run_not_found",
                "message": "运行不存在或不属于当前作用域",
            },
        )


class _InvalidTimeBoundError(ValueError):
    pass


def _normalize_filter_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    try:
        return _as_utc(value)
    except ValueError as exc:
        raise _InvalidTimeBoundError("time bound must be timezone-aware") from exc


@dataclass(frozen=True, slots=True)
class RunListFilters:
    view: RunView = "recent"
    statuses: tuple[RunStatus, ...] = ()
    slow_ms: int = 30000
    started_after: datetime | None = None
    started_before: datetime | None = None
    fingerprint: str | None = None
    limit: int = 50
    cursor: str | None = None

    def normalized(self) -> "RunListFilters":
        return RunListFilters(
            view=self.view,
            statuses=tuple(sorted(set(self.statuses))),
            slow_ms=self.slow_ms,
            started_after=_normalize_filter_time(self.started_after),
            started_before=_normalize_filter_time(self.started_before),
            fingerprint=self.fingerprint,
            limit=self.limit,
            cursor=self.cursor,
        )

    def filter_hash(self) -> str:
        value = self.normalized()
        payload = {
            "fingerprint": value.fingerprint,
            "slow_ms": value.slow_ms,
            "started_after": None
            if value.started_after is None
            else value.started_after.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "started_before": None
            if value.started_before is None
            else value.started_before.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "statuses": list(value.statuses),
            "view": value.view,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class RunOpsService:
    def __init__(
        self,
        registry: RunRegistry,
        store: RunHistoryStore | None,
        identity: RegistryIdentity,
        settings: Any,
        clock: Callable[[], datetime],
    ) -> None:
        self.registry = registry
        self.store = store
        self.identity = identity
        self.settings = settings
        self._history_settings = _settings_run_history(settings)
        self._clock = clock
        self._cursor_codec = CursorCodec(
            identity.cursor_key,
            ttl_s=self._history_settings.cursor_ttl_s,
        )
        self._long_poll_slots = threading.BoundedSemaphore(
            self._history_settings.long_poll_max_clients
        )

    async def health(self) -> RunHealthResponse:
        registry_health = await asyncio.to_thread(self.registry.health_snapshot)
        store_health = (
            None if self.store is None else await asyncio.to_thread(self.store.health_snapshot)
        )
        enabled = bool(registry_health.enabled)
        if not enabled:
            status: Literal["ok", "degraded", "disabled"] = "disabled"
        elif registry_health.status == "degraded" or (
            store_health is not None and store_health.status == "degraded"
        ):
            status = "degraded"
        elif self._history_settings.persistence_enabled and store_health is None:
            status = "degraded"
        else:
            status = "ok"
        persistence_enabled = bool(self._history_settings.persistence_enabled)
        persistence_state = (
            store_health.state
            if store_health is not None
            else ("disabled" if not self._history_settings.persistence_enabled else "memory_only")
        )
        return RunHealthResponse.model_validate(
            {
                "schema_version": 1,
                "status": status,
                "enabled": enabled,
                "boot_id": registry_health.boot_id,
                "worker_id": registry_health.worker_id,
                "memory": registry_health.memory,
                "persistence": {
                    "enabled": persistence_enabled,
                    "state": persistence_state,
                    "database": (
                        store_health.database_filename
                        if store_health is not None
                        else "run-history.sqlite3"
                    ),
                    "wal": bool(store_health is not None and persistence_state in {"ready", "read_only"}),
                    "writer_queue_depth": (
                        0 if store_health is None else store_health.writer_queue_depth
                    ),
                    "last_commit_at": (
                        None if store_health is None else store_health.last_commit_at
                    ),
                    "commit_lag_ms": (
                        None if store_health is None else store_health.commit_lag_ms
                    ),
                    "dropped_mutations": (
                        0 if store_health is None else store_health.dropped_mutations
                    ),
                    "quick_check": "not_run" if store_health is None else store_health.quick_check,
                },
                "heartbeat": {
                    "interval_s": self._history_settings.heartbeat_interval_s,
                    "stale_after_s": self._history_settings.worker_stale_after_s,
                    "last_heartbeat_at": (
                        None if store_health is None else store_health.last_heartbeat_at
                    ),
                },
                "retention": {
                    "days": self._history_settings.retention_days,
                    "max_runs": self._history_settings.max_persisted_runs,
                    "memory_terminal_ttl_s": self._history_settings.memory_terminal_ttl_s,
                },
                "scope_stability": self.identity.tenant_scope_stability,
            }
        )

    async def list_runs(self, scope: str, filters: RunListFilters) -> RunListResponse:
        normalized = filters.normalized()
        if normalized.fingerprint is not None and _configured_fingerprint_secret(self.settings) is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "fingerprint_unavailable",
                    "message": "query fingerprint 功能未启用",
                },
            )
        now = _as_utc(self._clock())
        filter_hash = normalized.filter_hash()
        before: tuple[datetime, str] | None = None
        if normalized.cursor is None:
            as_of = now
        else:
            payload = self._cursor_codec.decode(normalized.cursor, scope, filter_hash, now)
            as_of = datetime.fromtimestamp(payload.as_of_us / 1_000_000, tz=timezone.utc)
            before = (
                datetime.fromtimestamp(payload.last_started_at_us / 1_000_000, tz=timezone.utc),
                payload.last_run_id,
            )
        fetch_limit = self._history_settings.api_max_page_size + 1
        query = RunListQuery(
            tenant_scope=scope,
            view=normalized.view,
            statuses=normalized.statuses,
            slow_ms=normalized.slow_ms,
            started_after=normalized.started_after,
            started_before=normalized.started_before,
            fingerprint=normalized.fingerprint,
            limit=fetch_limit,
            as_of=as_of,
        )
        memory_future = asyncio.to_thread(self.registry.list_runs, query, before=before)
        store_unavailable = False
        if self.store is None:
            memory = await memory_future
            durable: list[RunSummary] = []
        else:
            memory_result, durable_result = await asyncio.gather(
                memory_future,
                asyncio.to_thread(self.store.list_runs, scope, query, before=before),
                return_exceptions=True,
            )
            if isinstance(memory_result, BaseException):
                raise memory_result
            memory = memory_result
            if isinstance(durable_result, RunHistoryReadError):
                store_unavailable = True
                durable = []
            elif isinstance(durable_result, BaseException):
                raise durable_result
            else:
                durable = durable_result
            if store_unavailable and not memory:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
                )
        if before is not None:
            memory = [item for item in memory if (item.started_at, item.run_id) < before]
        merged: dict[str, tuple[RunSummary, bool]] = {
            item.run_id: (item, False) for item in durable
        }
        for item in memory:
            current = merged.get(item.run_id)
            if current is None or item.updated_at >= current[0].updated_at:
                merged[item.run_id] = (item, True)
        ordered = sorted(
            (item for item, _memory_won in merged.values() if item.started_at <= as_of),
            key=lambda item: (item.started_at, item.run_id),
            reverse=True,
        )
        page = ordered[: normalized.limit]
        next_cursor = None
        if len(ordered) > normalized.limit and page:
            last = page[-1]
            as_of_us = int(as_of.timestamp() * 1_000_000)
            next_cursor = self._cursor_codec.encode(
                CursorPayload(
                    v=1,
                    tenant_scope=scope,
                    filter_hash=filter_hash,
                    as_of_us=as_of_us,
                    last_started_at_us=int(last.started_at.timestamp() * 1_000_000),
                    last_run_id=last.run_id,
                    exp=as_of_us // 1_000_000 + self._cursor_codec.ttl_s,
                )
            )
        source: Literal["memory", "sqlite", "memory+sqlite"]
        if self.store is None or store_unavailable:
            source = "memory"
        else:
            source = "memory+sqlite"
        history_state: HistoryState = "complete"
        if self._history_settings.persistence_enabled and (self.store is None or store_unavailable):
            history_state = "partial"
        return RunListResponse.model_validate(
            {
                "schema_version": 1,
                "items": tuple(page),
                "next_cursor": next_cursor,
                "as_of": as_of,
                "source": source,
                "history_state": history_state,
                "retention": {
                    "days": self._history_settings.retention_days,
                    "max_runs": self._history_settings.max_persisted_runs,
                },
            }
        )

    async def get_events(
        self,
        scope: str,
        run_id: str,
        after_seq: int,
        limit: int,
        wait_ms: int,
    ) -> RunEventsResponse:
        bounded_limit = max(1, min(limit, self._history_settings.events_max_page_size))
        bounded_wait_ms = max(0, min(wait_ms, self._history_settings.long_poll_max_ms, 25000))
        current, same_worker = await self._read_events(scope, run_id, after_seq, bounded_limit)
        if current.events or current.terminal or bounded_wait_ms == 0:
            return current
        if not self._long_poll_slots.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail={"code": "run_poll_capacity", "message": "长轮询容量已满"},
                headers={"Retry-After": "1"},
            )
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + bounded_wait_ms / 1000.0
            version = 0
            if same_worker:
                version = await asyncio.to_thread(self.registry.current_version, run_id)
                current, same_worker = await self._read_events(
                    scope, run_id, after_seq, bounded_limit
                )
                if current.events or current.terminal:
                    return current
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return current.model_copy(update={"timed_out": True})
                if same_worker:
                    version = await asyncio.to_thread(
                        self.registry.wait_for_change,
                        run_id,
                        version,
                        remaining,
                    )
                else:
                    await asyncio.sleep(min(0.25, remaining))
                    if self.store is not None:
                        await asyncio.to_thread(self.store.durable_watermark, scope, run_id)
                current, same_worker = await self._read_events(
                    scope, run_id, after_seq, bounded_limit
                )
                if current.events or current.terminal:
                    return current
        finally:
            self._long_poll_slots.release()

    async def _read_events(
        self, scope: str, run_id: str, after_seq: int, limit: int
    ) -> tuple[RunEventsResponse, bool]:
        memory_future = asyncio.to_thread(
            self.registry.get_events, scope, run_id, after_seq, limit
        )
        store_unavailable = False
        if self.store is None:
            memory = await memory_future
            durable = None
        else:
            memory_result, durable_result = await asyncio.gather(
                memory_future,
                asyncio.to_thread(self.store.get_events, scope, run_id, after_seq, limit),
                return_exceptions=True,
            )
            if isinstance(memory_result, BaseException):
                raise memory_result
            memory = memory_result
            if isinstance(durable_result, RunHistoryReadError):
                store_unavailable = True
                durable = None
            elif isinstance(durable_result, BaseException):
                raise durable_result
            else:
                durable = durable_result
            if store_unavailable and memory is None:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
                )
        if memory is None and durable is None:
            raise RunNotFoundError()
        event_by_seq: dict[int, Mapping[str, Any]] = {}
        if durable is not None:
            event_by_seq.update((int(event["seq"]), event) for event in durable.events)
        if memory is not None:
            event_by_seq.update((int(event["seq"]), event) for event in memory.events)
        ordered_all = tuple(event_by_seq[seq] for seq in sorted(event_by_seq))
        expected_seq = after_seq + 1
        for event in ordered_all:
            event_seq = int(event["seq"])
            if event_seq != expected_seq:
                raise _RunEventGapError(event_seq, max(
                    0 if memory is None else memory.latest_seq,
                    0 if durable is None else durable.latest_seq,
                ))
            expected_seq += 1
        ordered = ordered_all[:limit]
        latest_seq = max(
            0 if memory is None else memory.latest_seq,
            0 if durable is None else durable.latest_seq,
        )
        earliest_available_seq = min(
            value
            for value in (
                None if memory is None else memory.earliest_available_seq,
                None if durable is None else durable.earliest_available_seq,
            )
            if value is not None
        )
        if after_seq < earliest_available_seq - 1:
            raise _RunEventGapError(earliest_available_seq, latest_seq)
        same_worker = memory is not None
        terminal = (
            memory.terminal
            if memory is not None
            else bool(durable is not None and durable.terminal)
        )
        if store_unavailable or (memory is not None and memory.history_state == "partial"):
            history_state: HistoryState = "partial"
        else:
            history_state = "complete"
        durable_covers_latest = bool(
            durable is not None
            and durable.persistence_status == "durable"
            and durable.latest_seq >= latest_seq
        )
        if durable_covers_latest:
            persistence_status: PersistenceStatus = "durable"
            if earliest_available_seq == 1:
                history_state = "complete"
        elif store_unavailable:
            persistence_status = "unavailable"
        elif memory is not None:
            persistence_status = memory.persistence_status
        else:
            persistence_status = "unavailable"
        next_after = int(ordered[-1]["seq"]) if ordered else after_seq
        response = RunEventsResponse.model_validate(
            {
                "schema_version": 1,
                "run_id": run_id,
                "events": ordered,
                "after_seq": next_after,
                "latest_seq": latest_seq,
                "terminal": terminal,
                "timed_out": False,
                "history_state": history_state,
                "earliest_available_seq": earliest_available_seq,
                "persistence_status": persistence_status,
                "retry_after_ms": 500,
            }
        )
        return response, same_worker

    async def get_detail(self, scope: str, run_id: str) -> RunDetailResponse:
        memory_future = asyncio.to_thread(self.registry.get_detail, scope, run_id)
        if self.store is None:
            memory = await memory_future
            stored = None
        else:
            memory_result, stored_result = await asyncio.gather(
                memory_future,
                asyncio.to_thread(self.store.get_run, scope, run_id),
                return_exceptions=True,
            )
            if isinstance(memory_result, BaseException):
                raise memory_result
            memory = memory_result
            if isinstance(stored_result, RunHistoryReadError):
                if memory is None:
                    raise HTTPException(
                        status_code=503,
                        detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
                    )
                stored = None
            elif isinstance(stored_result, BaseException):
                raise stored_result
            else:
                stored = stored_result
        if memory is not None and (
            stored is None or memory.summary.updated_at >= stored.summary.updated_at
        ):
            return RunDetailResponse.model_validate(memory)
        if stored is None:
            raise RunNotFoundError()
        events = await asyncio.to_thread(self._all_stored_events, scope, run_id, stored)
        detail = RunDetail(
            schema_version=1,
            summary=stored.summary,
            topology=stored.topology,
            node_rollup=_rollups_from_events(events),
            history=RunHistory(
                event_integrity=stored.summary.event_integrity,
                earliest_available_seq=stored.summary.earliest_available_seq,
                last_seq=stored.summary.last_seq,
                persistence_status="durable",
            ),
        )
        return RunDetailResponse.model_validate(detail)

    def _all_stored_events(
        self, scope: str, run_id: str, stored: StoredRun
    ) -> tuple[Mapping[str, Any], ...]:
        if self.store is None:
            return ()
        after_seq = 0
        events: list[Mapping[str, Any]] = []
        while after_seq < stored.summary.last_seq:
            page = self.store.get_events(
                scope,
                run_id,
                after_seq,
                self._history_settings.events_max_page_size,
            )
            if page is None or not page.events:
                break
            events.extend(page.events)
            if page.after_seq <= after_seq:
                break
            after_seq = page.after_seq
        return tuple(events)



class _RunEventGapError(ValueError):
    def __init__(self, earliest_available_seq: int, latest_seq: int) -> None:
        self.earliest_available_seq = earliest_available_seq
        self.latest_seq = latest_seq
        super().__init__("run_event_gap")


def _configured_fingerprint_secret(settings: Any) -> str | None:
    configured = getattr(_settings_run_history(settings), "fingerprint_secret", None)
    if configured is None:
        return None
    getter = getattr(configured, "get_secret_value", None)
    value = getter() if callable(getter) else configured
    return value if isinstance(value, str) and value else None


def _rollups_from_events(events: tuple[Mapping[str, Any], ...]) -> tuple[NodeRollup, ...]:
    values: dict[tuple[str, str, int], dict[str, Any]] = {}
    for event in events:
        node_id = event.get("node_id")
        event_type = event.get("type")
        attempt_value = event.get("attempt")
        attempt = attempt_value if type(attempt_value) is int and attempt_value >= 1 else 1
        elapsed = event.get("elapsed_ms")
        attributes = event.get("attributes")
        reason = attributes.get("reason") if isinstance(attributes, Mapping) else None
        reason_code = reason if isinstance(reason, str) else None
        error = event.get("error")
        error_value = error if isinstance(error, Mapping) else {}
        if event_type == "node.started" and isinstance(node_id, str) and node_id:
            values[("node", node_id, attempt)] = {
                "node_id": node_id,
                "attempt": attempt,
                "status": "running",
                "started_elapsed_ms": elapsed,
                "finished_elapsed_ms": None,
                "duration_ms": None,
                "degraded_reason": None,
                "retry_reason": None,
                "error_type": None,
                "error_code": None,
            }
        elif event_type in {
            "node.completed",
            "node.failed",
            "node.cancelled",
            "node.skipped",
        } and isinstance(node_id, str) and node_id:
            key = ("node", node_id, attempt)
            item = values.setdefault(
                key,
                {
                    "node_id": node_id,
                    "attempt": attempt,
                    "status": "unknown",
                    "started_elapsed_ms": None,
                    "finished_elapsed_ms": None,
                    "duration_ms": None,
                    "degraded_reason": None,
                    "retry_reason": None,
                    "error_type": None,
                    "error_code": None,
                },
            )
            item["status"] = str(event_type).removeprefix("node.")
            item["finished_elapsed_ms"] = elapsed
            item["duration_ms"] = event.get("duration_ms")
            item["error_type"] = error_value.get("type")
            item["error_code"] = error_value.get("code")
        elif event_type == "retry.started" and isinstance(node_id, str) and node_id:
            values[("retry", node_id, attempt)] = {
                "node_id": node_id,
                "attempt": attempt,
                "status": "retry_running",
                "started_elapsed_ms": elapsed,
                "finished_elapsed_ms": None,
                "duration_ms": None,
                "degraded_reason": None,
                "retry_reason": reason_code,
                "error_type": None,
                "error_code": None,
            }
        elif event_type in {
            "retry.completed",
            "retry.failed",
            "retry.skipped",
        } and isinstance(node_id, str) and node_id:
            key = ("retry", node_id, attempt)
            item = values.setdefault(
                key,
                {
                    "node_id": node_id,
                    "attempt": attempt,
                    "status": "retry_unknown",
                    "started_elapsed_ms": None,
                    "finished_elapsed_ms": None,
                    "duration_ms": None,
                    "degraded_reason": None,
                    "retry_reason": None,
                    "error_type": None,
                    "error_code": None,
                },
            )
            item["status"] = f"retry_{str(event_type).removeprefix('retry.')}"
            item["finished_elapsed_ms"] = elapsed
            item["duration_ms"] = event.get("duration_ms")
            item["retry_reason"] = item["retry_reason"] or reason_code
            item["error_type"] = error_value.get("type")
            item["error_code"] = error_value.get("code")
        elif event_type == "degraded" and isinstance(node_id, str) and node_id:
            for key in reversed(values):
                item = values[key]
                if item["node_id"] == node_id:
                    item["degraded_reason"] = reason_code
                    break
    return tuple(NodeRollup(**item) for item in values.values())


class RunOpsRuntime:
    """Boot-scoped owner of the Registry, optional store, and Ops service."""

    def __init__(
        self,
        settings: Any,
        identity: RegistryIdentity,
        registry: RunRegistry,
        store: RunHistoryStore | None,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.registry = registry
        self.store = store
        self.service = RunOpsService(
            registry, store, identity, settings, lambda: datetime.now(timezone.utc)
        )
        self._close_lock = threading.Lock()
        self._closed = False

    @classmethod
    def _memory_only(
        cls,
        settings: Any,
        *,
        enabled: bool,
        boot_id: str | None = None,
        worker_id: str | None = None,
    ) -> "RunOpsRuntime":
        history_settings = _settings_run_history(settings)
        if bool(history_settings.enabled) != enabled:
            history_settings = history_settings.model_copy(update={"enabled": enabled})
            settings = history_settings
        identity = RegistryIdentity(
            boot_id=boot_id or str(uuid.uuid4()),
            worker_id=worker_id or str(uuid.uuid4()),
            scope_key=os.urandom(32),
            cursor_key=os.urandom(32),
            tenant_scope_stability="boot",
        )
        registry = RunRegistry(history_settings, identity)
        return cls(settings, identity, registry, None)

    @classmethod
    def disabled(cls, settings: Any) -> "RunOpsRuntime":
        return cls._memory_only(settings, enabled=False)

    @classmethod
    def bootstrap(cls, settings: Any) -> "RunOpsRuntime":
        history_settings = _settings_run_history(settings)
        if not history_settings.enabled:
            return cls.disabled(settings)
        if not history_settings.persistence_enabled:
            return cls._memory_only(settings, enabled=True)

        boot_id = str(uuid.uuid4())
        worker_id = str(uuid.uuid4())
        store: RunHistoryStore | None = None
        try:
            store = RunHistoryStore.open(
                history_settings, boot_id=boot_id, worker_id=worker_id
            )
            store_health = store.health_snapshot()
            stability = (
                "installation"
                if store_health.state in {"ready", "read_only"}
                else "boot"
            )
            identity = RegistryIdentity(
                boot_id=boot_id,
                worker_id=worker_id,
                scope_key=store.scope_key,
                cursor_key=store.cursor_key,
                tenant_scope_stability=stability,
            )
            registry = RunRegistry(
                history_settings,
                identity,
                persistence_offer=store.enqueue if store.write_enabled else None,
            )
            runtime = cls(settings, identity, registry, store)
            store.start_background_tasks()
            return runtime
        except Exception:
            if store is not None:
                grace_ms = 0
                try:
                    grace_ms = history_settings.writer_shutdown_grace_ms
                except Exception:
                    pass
                try:
                    store.close(grace_ms)
                except Exception:
                    pass
            return cls._memory_only(
                settings,
                enabled=True,
                boot_id=boot_id,
                worker_id=worker_id,
            )

    def scope_for(self, normalized_tenant_id: str) -> str:
        return derive_tenant_scope(normalized_tenant_id, self.identity.scope_key)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self.store is None:
            return
        grace_ms = 0
        try:
            grace_ms = _settings_run_history(self.settings).writer_shutdown_grace_ms
        except Exception:
            pass
        try:
            self.store.mark_clean_shutdown(datetime.now(timezone.utc), grace_ms)
        except Exception:
            pass
        try:
            self.store.close(grace_ms)
        except Exception:
            pass


def get_run_ops_service(request: Request) -> RunOpsService:
    runtime = getattr(request.app.state, "run_ops_runtime", None)
    service = runtime if isinstance(runtime, RunOpsService) else getattr(runtime, "service", None)
    if not isinstance(service, RunOpsService):
        raise HTTPException(
            status_code=503,
            detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
        )
    return service


def resolve_service_and_scope(
    request: Request, tenant_id: str | None
) -> tuple[RunOpsService, str]:
    service = get_run_ops_service(request)
    raw_tenant = resolve_request_tenant(request, tenant_id, service.settings)
    return service, derive_tenant_scope(raw_tenant, service.identity.scope_key)


def _require_run_operator_access(
    request: Request,
    service: Annotated[RunOpsService, Depends(get_run_ops_service)],
) -> None:
    require_operator_access(request, service.settings)


def _cursor_http_error(exc: CursorError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": exc.code, "message": "分页游标无效或已失效"},
    )


def create_run_ops_router() -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.get("/health", response_model=RunHealthResponse)
    async def run_health(
        service: Annotated[RunOpsService, Depends(get_run_ops_service)],
        _: Annotated[None, Depends(_require_run_operator_access)],
    ) -> RunHealthResponse:
        try:
            return await service.health()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
            ) from exc

    @router.get("", response_model=RunListResponse)
    async def list_runs(
        request: Request,
        _: Annotated[None, Depends(_require_run_operator_access)],
        view: RunView = "recent",
        status: Annotated[list[RunStatus] | None, Query()] = None,
        slow_ms: int = Query(default=30000, ge=1000, le=3600000),
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        fingerprint: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        tenant_id: str | None = None,
    ) -> RunListResponse:
        service, scope = resolve_service_and_scope(request, tenant_id)
        filters = RunListFilters(
            view=view,
            statuses=tuple(sorted(status or [])),
            slow_ms=slow_ms,
            started_after=started_after,
            started_before=started_before,
            fingerprint=fingerprint,
            limit=limit,
            cursor=cursor,
        )
        try:
            return await service.list_runs(scope, filters)
        except CursorError as exc:
            raise _cursor_http_error(exc) from exc
        except _InvalidTimeBoundError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_time_bound", "message": "时间边界必须包含时区"},
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
            ) from exc

    @router.get("/{run_id}", response_model=RunDetailResponse)
    async def get_run_detail(
        request: Request,
        run_id: str,
        _: Annotated[None, Depends(_require_run_operator_access)],
        tenant_id: str | None = None,
    ) -> RunDetailResponse:
        service, scope = resolve_service_and_scope(request, tenant_id)
        try:
            return await service.get_detail(scope, run_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
            ) from exc

    @router.get("/{run_id}/events", response_model=RunEventsResponse)
    async def get_run_events(
        request: Request,
        run_id: str,
        _: Annotated[None, Depends(_require_run_operator_access)],
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
        wait_ms: int = Query(default=0, ge=0, le=25000),
        tenant_id: str | None = None,
    ) -> RunEventsResponse:
        service, scope = resolve_service_and_scope(request, tenant_id)
        try:
            return await service.get_events(scope, run_id, after_seq, limit, wait_ms)
        except _RunEventGapError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_event_gap",
                    "earliest_available_seq": exc.earliest_available_seq,
                    "latest_seq": exc.latest_seq,
                },
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_ops_unavailable", "message": "运行历史暂不可用"},
            ) from exc

    return router


__all__ = [
    "CursorCodec",
    "CursorError",
    "CursorPayload",
    "RunDetailResponse",
    "RunEventsResponse",
    "RunHealthResponse",
    "RunListFilters",
    "RunListResponse",
    "RunNotFoundError",
    "RunOpsRuntime",
    "RunOpsService",
    "RunSummaryResponse",
    "bearer_credential",
    "client_is_loopback",
    "configured_operator_token",
    "create_run_ops_router",
    "get_run_ops_service",
    "resolve_service_and_scope",
    "require_operator_access",
    "resolve_request_tenant",
]
