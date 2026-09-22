"""Authenticated Source Control Plane APIs for existing source ledger tables."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Path as ApiPath, Query, Request, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from core import catalog
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ, KNOWLEDGE_WRITE
from core.source_schedules import (
    SourceScheduleConflict,
    SourceScheduleNotFound,
    SourceScheduleRepository,
)
from core.source_sync_ledger import (
    SourceRunRequestResult,
    SourceSyncIdempotencyConflict,
    SourceSyncLedger,
    SourceSyncLedgerConflict,
    SourceSyncLedgerNotFound,
    sanitize_source_config,
    sanitize_source_error,
)
from models.orm import DataSourceRecord, SourceSchedule, SourceSyncItem, SourceSyncRun
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset
from server.source_dispatcher import request_source_dispatch
from sources.base import SourceError
from sources.registry import (
    SourcePlugin,
    attach_source_contract,
    create_source as create_connector,
    source_contract,
)

_OPENAPI_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="KnowledgeBearerAuth",
    description="Actor-bound signed KnowledgeOps bearer token.",
)
router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/sources",
    tags=["knowledge-sources"],
    dependencies=[Depends(_OPENAPI_BEARER)],
)

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_CURSOR_PATTERN = r"^[A-Za-z0-9_-]{16,512}$"
DatasetId = Annotated[str, ApiPath(min_length=1, max_length=64, pattern=_SAFE_ID)]
SourceId = Annotated[str, ApiPath(min_length=1, max_length=64, pattern=_SAFE_ID)]
RunId = Annotated[str, ApiPath(min_length=1, max_length=64, pattern=_SAFE_ID)]

_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_WRITE = require_knowledge_permission(KNOWLEDGE_WRITE, resolve_path_dataset("dataset_id"))
_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
WriteActor = Annotated[KnowledgeActor, Depends(_WRITE)]
ManageActor = Annotated[KnowledgeActor, Depends(_MANAGE)]

SourceKind: TypeAlias = Literal["local_dir", "github_repo"]
SourceStatus: TypeAlias = Literal["active", "disabled"]
RunStatus: TypeAlias = Literal[
    "running", "completed", "failed", "incomplete", "dry_run", "superseded"
]
RunTrigger: TypeAlias = Literal["manual", "scheduled", "retry"]
ExecutionState: TypeAlias = Literal["pending", "executing", "failed", "completed"]
ScheduleStatus: TypeAlias = Literal["active", "paused", "archived"]
WritableScheduleStatus: TypeAlias = Literal["active", "paused"]
ScheduleCapability: TypeAlias = Literal["fixed_interval_utc"]
ItemAction: TypeAlias = Literal["upsert", "skip", "delete"]
ItemResult: TypeAlias = Literal[
    "completed", "failed", "skipped", "suppressed", "dry_run", "queued"
]

_SECRET_KEY = re.compile(
    r"(?:access[_-]?key|api[_-]?key|authorization|bearer|cookie|credential|password|passwd|"
    r"private[_-]?key|refresh[_-]?token|secret|session[_-]?key|token)",
    re.I,
)
_SENSITIVE_URI_PARAMETER = re.compile(
    r"(?:access[_-]?key|api[_-]?key|credential|password|passwd|refresh[_-]?token|secret|"
    r"signature|token)$",
    re.I,
)
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorEnvelope(StrictModel):
    error: ErrorDetail


class LocalDirConfig(StrictModel):
    path: str = Field(min_length=1, max_length=2048)
    include: list[str] = Field(default_factory=list, max_length=256)
    exclude: list[str] = Field(default_factory=list, max_length=256)
    extensions: list[str] | None = Field(default=None, max_length=64)
    max_files: int = Field(default=0, ge=0, le=1_000_000)
    credential_ref: str | None = Field(default=None, min_length=10, max_length=2048)

    @field_validator("credential_ref")
    @classmethod
    def _validate_reference(cls, value: str | None) -> str | None:
        return None if value is None else _validate_credential_ref(value)

    @field_validator("include", "exclude")
    @classmethod
    def _validate_globs(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or "\x00" in value for value in values):
            raise ValueError("glob entries must be non-empty and contain no NUL")
        return values

    @field_validator("extensions")
    @classmethod
    def _validate_extensions(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        for raw in values:
            value = raw.strip().casefold()
            if not value.startswith("."):
                value = f".{value}"
            if not value[1:].isalnum():
                raise ValueError("extensions must be simple suffixes")
            normalized.append(value)
        return list(dict.fromkeys(normalized))


class GitHubRepoConfig(StrictModel):
    repo: str = Field(min_length=3, max_length=256)
    ref: str = Field(default="main", min_length=1, max_length=256)
    include: list[str] = Field(default_factory=list, max_length=256)
    exclude: list[str] = Field(default_factory=list, max_length=256)
    extensions: list[str] | None = Field(default=None, max_length=64)
    strip_prefix: str = Field(default="", max_length=1024)
    timeout: float = Field(default=300.0, gt=0, le=3600)
    max_files: int = Field(default=0, ge=0, le=1_000_000)
    mode: Literal["auto", "tarball", "api"] = "auto"
    credential_ref: str | None = Field(default=None, min_length=10, max_length=2048)

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, value: str) -> str:
        if not _GITHUB_REPO.fullmatch(value):
            raise ValueError("repo must be owner/repository")
        return value

    @field_validator("include", "exclude")
    @classmethod
    def _validate_globs(cls, values: list[str]) -> list[str]:
        return LocalDirConfig._validate_globs(values)

    @field_validator("extensions")
    @classmethod
    def _validate_extensions(cls, values: list[str] | None) -> list[str] | None:
        return LocalDirConfig._validate_extensions(values)

    @field_validator("credential_ref")
    @classmethod
    def _validate_reference(cls, value: str | None) -> str | None:
        return None if value is None else _validate_credential_ref(value)


class SourceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    kind: SourceKind
    config: dict[str, JsonValue]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True


class SourcePatch(StrictModel):
    expected_generation: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: SourceKind | None = None
    config: dict[str, JsonValue] | None = None
    metadata: dict[str, JsonValue] | None = None


class SourceGenerationRequest(StrictModel):
    expected_generation: int = Field(ge=0)


class SourceSyncRequest(StrictModel):
    force_full: bool = False
    dry_run: bool = False


class SourceRetryRequest(StrictModel):
    pass


class SourceSchedulePutRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    interval_seconds: int = Field(ge=300, le=604_800)
    force_full: bool
    status: WritableScheduleStatus | None = None


class SourceScheduleRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class SourceScheduleResponse(StrictModel):
    id: str
    tenant_id: str
    dataset_id: str
    source_id: str
    revision: int = Field(ge=1)
    status: ScheduleStatus
    interval_seconds: int = Field(ge=300, le=604_800)
    force_full: bool
    next_run_at: datetime
    last_enqueued_at: datetime | None
    last_run_id: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    capability: ScheduleCapability


class SourceResponse(StrictModel):
    id: str
    tenant_id: str
    dataset_id: str
    name: str
    kind: SourceKind
    config: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    status: SourceStatus
    generation: int
    last_cursor: dict[str, JsonValue]
    last_result: dict[str, JsonValue]
    last_error: str
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceListResponse(StrictModel):
    items: list[SourceResponse]
    count: int


class RunCountsResponse(StrictModel):
    fetched: int
    ingested: int
    skipped: int
    removed: int
    pending_deletes: int
    chunks: int
    failed: int


class RunResponse(StrictModel):
    id: str
    source_id: str
    tenant_id: str
    dataset_id: str
    status: RunStatus
    trigger: RunTrigger
    force_full: bool
    dry_run: bool
    source_generation: int
    dataset_generation: int
    retry_of_run_id: str | None
    schedule_id: str | None
    schedule_revision: int | None
    planned_at: datetime | None
    execution_state: ExecutionState
    execution_attempts: int
    execution_last_error: str
    execution_finished_at: datetime | None
    cursor_before: dict[str, JsonValue]
    cursor_after: dict[str, JsonValue]
    counts: RunCountsResponse
    fetch_error: str
    duration_ms: int
    started_at: datetime
    finished_at: datetime | None


class RunAcceptedResponse(StrictModel):
    run_id: str
    source_id: str
    status: RunStatus
    trigger: RunTrigger
    retry_of_run_id: str | None = None
    execution_state: ExecutionState
    replayed: bool


class RunListResponse(StrictModel):
    items: list[RunResponse]
    count: int
    next_cursor: str | None = None


class RunItemResponse(StrictModel):
    id: str
    run_id: str
    source_id: str
    external_id: str
    doc_id: str
    source_uri: str
    content_hash: str
    action: ItemAction
    result: ItemResult
    chunk_count: int
    error_code: str
    error_message: str
    created_at: datetime
    updated_at: datetime


class RunItemListResponse(StrictModel):
    items: list[RunItemResponse]
    count: int
    next_cursor: str | None = None


def _error_responses(*codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        401: "Actor bearer token is missing or invalid.",
        403: "Tenant, dataset, or role scope is forbidden.",
        404: "The scoped source or run does not exist.",
        409: "Generation, idempotency, or retry state conflicts.",
        422: "The request, connector configuration, filter, or cursor is invalid.",
        503: "The source control-plane or scheduler is unavailable.",
    }
    return {
        code: {"model": ErrorEnvelope, "description": descriptions[code]}
        for code in codes
    }


def _ledger() -> SourceSyncLedger:
    return SourceSyncLedger(catalog.get_engine())


def _schedule_repository() -> SourceScheduleRepository:
    return SourceScheduleRepository(catalog.get_engine())


def _raise_source(exc: Exception) -> None:
    if isinstance(exc, SourceScheduleNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "source_schedule_not_found", "message": "schedule not found"},
        ) from exc
    if isinstance(exc, SourceScheduleConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "source_schedule_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, SourceSyncLedgerNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_source_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, SourceSyncIdempotencyConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledge_source_idempotency_conflict",
                "message": "Idempotency-Key 已用于不同的同步请求",
            },
        ) from exc
    if isinstance(exc, SourceSyncLedgerConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_source_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, (SourceError, ValueError, ValidationError)):
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_source_invalid", "message": sanitize_source_error(exc)},
        ) from exc
    raise exc


def _validate_credential_ref(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("credential_ref 必须是 secret:// 或 vault:// 引用")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"secret", "vault"}
        or not parsed.netloc
        or not parsed.path.strip("/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("credential_ref 必须是安全的 secret:// 或 vault:// 引用")
    return value.strip()


def _validate_no_inline_secrets(value: JsonValue, *, key: str = "config") -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            normalized = str(raw_key).strip().casefold().replace("-", "_")
            if normalized == "credential_ref":
                _validate_credential_ref(nested)
                continue
            if _SECRET_KEY.search(normalized):
                raise ValueError("配置不得包含内联凭据；请使用 credential_ref")
            _validate_no_inline_secrets(nested, key=normalized)
    elif isinstance(value, list):
        for nested in value:
            _validate_no_inline_secrets(nested, key=key)


def _settings(request: Request) -> Any:
    configured = getattr(request.app.state, "knowledge_auth_settings", None)
    if configured is not None:
        return configured
    from config.settings import get_settings

    return get_settings()


def _source_settings(request: Request) -> Any:
    return getattr(_settings(request), "sources", None)


def _resolve_root(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else _PROJECT_ROOT / path).resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _connector_contract(kind: str) -> SourcePlugin:
    """取这个 kind 自己的契约；未知或没挂契约的一律拒绝。

    过去这里写的是 ``if kind == "local_dir": 按 LocalDirConfig else: 按
    GitHubRepoConfig``。而 ``_update_source`` 的 kind 有一个来源是**数据库里的
    ``source_type``**（不受请求模型的 Literal 约束，列上也没有 CHECK），于是任何
    第四种值都会被默默按 GitHub 的形状与白名单放过 —— 典型的 else 分支 fail-open。
    """
    try:
        return source_contract(kind)
    except SourceError as exc:
        raise ValueError(str(exc)) from exc


def _validate_connector_model(kind: SourceKind, config: dict[str, JsonValue]) -> StrictModel:
    model = _connector_contract(kind).config_model
    try:
        return model.model_validate(config)
    except ValidationError as exc:
        locations = [".".join(str(part) for part in item["loc"]) for item in exc.errors()[:5]]
        raise ValueError(f"{kind} 配置字段无效: {', '.join(locations)}") from exc


def _local_dir_preflight(
    normalized: dict[str, Any], settings: Any
) -> dict[str, Any]:
    roots = [
        _resolve_root(value)
        for value in list(getattr(settings, "local_allowed_roots", []) or [])
    ]
    extensions = list(getattr(settings, "local_allowed_extensions", []) or [])
    if not roots or not extensions:
        raise ValueError("local_dir allowlist 未配置，已拒绝")
    path = _resolve_root(str(normalized["path"]))
    if not path.is_dir() or not any(_is_within(path, root) for root in roots):
        raise ValueError("local_dir path 不存在或不在允许目录内")
    requested = list(normalized.get("extensions") or extensions)
    allowed = {str(value).casefold() for value in extensions}
    if not requested or any(str(value).casefold() not in allowed for value in requested):
        raise ValueError("local_dir extensions 超出允许范围")
    normalized["path"] = str(path)
    normalized["extensions"] = requested
    return normalized


def _github_repo_preflight(
    normalized: dict[str, Any], settings: Any
) -> dict[str, Any]:
    repositories = {
        str(value).casefold()
        for value in list(getattr(settings, "github_allowed_repositories", []) or [])
    }
    organizations = {
        str(value).casefold()
        for value in list(getattr(settings, "github_allowed_organizations", []) or [])
    }
    if not repositories and not organizations:
        raise ValueError("github_repo allowlist 未配置，已拒绝")
    repo = str(normalized["repo"]).casefold()
    organization = repo.split("/", 1)[0]
    if repo not in repositories and organization not in organizations:
        raise ValueError("github_repo 不在 repository/organization allowlist 内")
    normalized["repo"] = repo
    return normalized


# 每个源把自己的「配置形状 + 白名单安检」挂到注册表上。加一种源 = 注册工厂 +
# 在这里加一行声明，本模块没有任何按 kind 的分支。
attach_source_contract(
    "local_dir", config_model=LocalDirConfig, preflight=_local_dir_preflight
)
attach_source_contract(
    "github_repo", config_model=GitHubRepoConfig, preflight=_github_repo_preflight
)


def _default_preflight(
    request: Request,
    kind: SourceKind,
    config: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    model = _validate_connector_model(kind, config)
    normalized = model.model_dump(exclude_none=True)
    normalized = _connector_contract(kind).preflight(normalized, _source_settings(request))
    create_connector(kind, dict(normalized), _settings(request))
    return normalized


def _preflight(
    request: Request,
    kind: SourceKind,
    config: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    _validate_no_inline_secrets(config)
    callback = getattr(request.app.state, "knowledge_source_connector_preflight", None)
    if callback is not None:
        checked = callback(kind, dict(config))
        if not isinstance(checked, dict):
            raise ValueError("connector preflight 必须返回 JSON object")
        _validate_no_inline_secrets(checked)
        _validate_connector_model(kind, checked)
        return checked
    return _default_preflight(request, kind, config)


def _is_uri_shaped(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme)
        and (parsed.netloc or parsed.scheme.casefold() == "file")
    )


def sanitize_source_uri(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or (not parsed.netloc and parsed.scheme.casefold() != "file"):
        return value
    host = parsed.netloc.rsplit("@", 1)[-1]
    netloc = f"[REDACTED]@{host}" if "@" in parsed.netloc else host
    try:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=256)
    except ValueError:
        query_pairs = []
    query = urlencode(
        [
            (name, "[REDACTED]" if _SENSITIVE_URI_PARAMETER.search(name) else item_value)
            for name, item_value in query_pairs
        ]
    )
    fragment = parsed.fragment
    if "=" in fragment:
        try:
            fragment = urlencode(
                [
                    (
                        name,
                        "[REDACTED]"
                        if _SENSITIVE_URI_PARAMETER.search(name)
                        else item_value,
                    )
                    for name, item_value in parse_qsl(fragment, keep_blank_values=True)
                ]
            )
        except ValueError:
            fragment = ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))


def _public_value(value: Any, *, key: str = "") -> Any:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    if normalized == "credentialref":
        return sanitize_source_config(value, "credential_ref")
    if key and _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _public_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_public_value(child, key=key) for child in value]
    if isinstance(value, str):
        if _is_uri_shaped(value):
            return sanitize_source_uri(value)
        if normalized.endswith("error") or normalized.endswith("errormessage"):
            return sanitize_source_error(value)
    return value


def _source_payload(source: DataSourceRecord) -> dict[str, Any]:
    effective = source.effective_config if isinstance(source.effective_config, dict) else {}
    return {
        "id": source.id,
        "tenant_id": source.tenant_id,
        "dataset_id": source.dataset_id,
        "name": source.name,
        "kind": source.source_type,
        "config": _public_value(effective.get("params", {})),
        "metadata": _public_value(effective.get("metadata", {})),
        "status": source.status,
        "generation": int(source.mutation_generation or 0),
        "last_cursor": _public_value(source.last_cursor or {}),
        "last_result": _public_value(source.last_result or {}),
        "last_error": _public_value(source.last_error, key="last_error"),
        "last_sync_at": source.last_sync_at,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def _schedule_payload(schedule: SourceSchedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "tenant_id": schedule.tenant_id,
        "dataset_id": schedule.dataset_id,
        "source_id": schedule.source_id,
        "revision": int(schedule.revision),
        "status": schedule.status,
        "interval_seconds": int(schedule.interval_seconds),
        "force_full": bool(schedule.force_full),
        "next_run_at": schedule.next_run_at,
        "last_enqueued_at": schedule.last_enqueued_at,
        "last_run_id": schedule.last_run_id,
        "created_by": schedule.created_by,
        "updated_by": schedule.updated_by,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "capability": "fixed_interval_utc",
    }


def _run_payload(run: SourceSyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source_id": run.source_id,
        "tenant_id": run.tenant_id,
        "dataset_id": run.dataset_id,
        "status": run.status,
        "trigger": run.trigger,
        "force_full": bool(run.force_full),
        "dry_run": bool(run.dry_run),
        "source_generation": int(run.source_generation or 0),
        "dataset_generation": int(run.dataset_generation or 0),
        "retry_of_run_id": run.retry_of_run_id,
        "schedule_id": run.schedule_id,
        "schedule_revision": run.schedule_revision,
        "planned_at": run.planned_at,
        "execution_state": run.execution_state,
        "execution_attempts": int(run.execution_attempts or 0),
        "execution_last_error": _public_value(run.execution_last_error, key="execution_last_error"),
        "execution_finished_at": run.execution_finished_at,
        "cursor_before": _public_value(run.cursor_before or {}),
        "cursor_after": _public_value(run.cursor_after or {}),
        "counts": {
            "fetched": run.fetched,
            "ingested": run.ingested,
            "skipped": run.skipped,
            "removed": run.removed,
            "pending_deletes": run.pending_deletes,
            "chunks": run.chunks,
            "failed": run.failed,
        },
        "fetch_error": _public_value(run.fetch_error, key="fetch_error"),
        "duration_ms": run.duration_ms,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _item_payload(item: SourceSyncItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "source_id": item.source_id,
        "external_id": item.external_id,
        "doc_id": item.doc_id,
        "source_uri": _public_value(item.source_uri, key="source_uri"),
        "content_hash": item.content_hash,
        "action": item.action,
        "result": item.result,
        "chunk_count": item.chunk_count,
        "error_code": item.error_code,
        "error_message": _public_value(item.error_message, key="error_message"),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _request_hash(source_id: str, body: SourceSyncRequest) -> str:
    canonical = json.dumps(
        {
            "source_id": source_id,
            "force_full": body.force_full,
            "dry_run": body.dry_run,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _encode_cursor(kind: Literal["run", "item"], timestamp: datetime, row_id: str) -> str:
    raw = json.dumps(
        {
            "v": 1,
            "kind": kind,
            "time": timestamp.isoformat(timespec="microseconds"),
            "id": row_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str | None,
    kind: Literal["run", "item"],
) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict) or set(payload) != {"v", "kind", "time", "id"}:
            raise ValueError
        if payload["v"] != 1 or payload["kind"] != kind:
            raise ValueError
        timestamp = datetime.fromisoformat(payload["time"])
        row_id = str(payload["id"])
        if not row_id or len(row_id) > 64 or re.search(r"[\x00-\x1f\x7f]", row_id):
            raise ValueError
        return timestamp, row_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_source_cursor_invalid", "message": "分页 cursor 无效"},
        ) from exc


@router.get(
    "",
    response_model=SourceListResponse,
    responses=_error_responses(401, 403, 422, 503),
)
def list_sources(
    dataset_id: DatasetId,
    actor: ReadActor,
    source_status: Annotated[SourceStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    try:
        rows = _ledger().list_sources(actor.tenant_id, dataset_id, status=source_status)
    except Exception as exc:
        _raise_source(exc)
    return {"items": [_source_payload(row) for row in rows], "count": len(rows)}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SourceResponse,
    responses=_error_responses(401, 403, 409, 422, 503),
)
def create_source(
    dataset_id: DatasetId,
    body: SourceCreate,
    actor: ManageActor,
    request: Request,
) -> dict[str, Any]:
    try:
        config = _preflight(request, body.kind, body.config)
        _validate_no_inline_secrets(body.metadata, key="metadata")
        source = _ledger().create_source(
            actor.tenant_id,
            dataset_id,
            name=body.name,
            kind=body.kind,
            config=config,
            metadata=dict(body.metadata),
            enabled=body.enabled,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _source_payload(source)


@router.get(
    "/{source_id}",
    response_model=SourceResponse,
    responses=_error_responses(401, 403, 404, 422, 503),
)
def get_source(dataset_id: DatasetId, source_id: SourceId, actor: ReadActor) -> dict[str, Any]:
    try:
        source = _ledger().get_source_scoped(actor.tenant_id, dataset_id, source_id)
    except Exception as exc:
        _raise_source(exc)
    return _source_payload(source)


@router.patch(
    "/{source_id}",
    response_model=SourceResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def update_source(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourcePatch,
    actor: ManageActor,
    request: Request,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    values.pop("expected_generation", None)
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_source_invalid", "message": "至少提供一个修改字段"},
        )
    try:
        current = _ledger().get_source_scoped(actor.tenant_id, dataset_id, source_id)
        kind: SourceKind = body.kind if body.kind is not None else current.source_type
        config = body.config
        if config is not None:
            config = _preflight(request, kind, config)
        elif body.kind is not None:
            effective = current.effective_config if isinstance(current.effective_config, dict) else {}
            config = _preflight(request, kind, dict(effective.get("params") or {}))
        if body.metadata is not None:
            _validate_no_inline_secrets(body.metadata, key="metadata")
        source = _ledger().update_source(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_generation=body.expected_generation,
            name=body.name,
            kind=body.kind,
            config=None if config is None else dict(config),
            metadata=None if body.metadata is None else dict(body.metadata),
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _source_payload(source)


def _set_enabled(
    dataset_id: str,
    source_id: str,
    body: SourceGenerationRequest,
    actor: KnowledgeActor,
    *,
    enabled: bool,
) -> dict[str, Any]:
    try:
        source = _ledger().set_source_enabled(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_generation=body.expected_generation,
            enabled=enabled,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _source_payload(source)


@router.post(
    "/{source_id}/enable",
    response_model=SourceResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def enable_source(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceGenerationRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    return _set_enabled(dataset_id, source_id, body, actor, enabled=True)


@router.post(
    "/{source_id}/disable",
    response_model=SourceResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def disable_source(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceGenerationRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    return _set_enabled(dataset_id, source_id, body, actor, enabled=False)


@router.get(
    "/{source_id}/schedule",
    response_model=SourceScheduleResponse,
    summary="Read the fixed-interval UTC source schedule",
    responses=_error_responses(401, 403, 404, 503),
)
def get_source_schedule(
    dataset_id: DatasetId,
    source_id: SourceId,
    actor: ReadActor,
) -> dict[str, Any]:
    try:
        schedule = _schedule_repository().get_scoped(
            actor.tenant_id, dataset_id, source_id
        )
    except Exception as exc:
        _raise_source(exc)
    return _schedule_payload(schedule)


@router.put(
    "/{source_id}/schedule",
    response_model=SourceScheduleResponse,
    summary="Create or replace the fixed-interval UTC source schedule",
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def put_source_schedule(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceSchedulePutRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        schedule = _schedule_repository().put(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_revision=body.expected_revision,
            interval_seconds=body.interval_seconds,
            force_full=body.force_full,
            status=body.status,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _schedule_payload(schedule)


@router.post(
    "/{source_id}/schedule/pause",
    response_model=SourceScheduleResponse,
    summary="Pause the fixed-interval UTC source schedule",
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def pause_source_schedule(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceScheduleRevisionRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        schedule = _schedule_repository().pause(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _schedule_payload(schedule)


@router.post(
    "/{source_id}/schedule/resume",
    response_model=SourceScheduleResponse,
    summary="Resume the fixed-interval UTC source schedule",
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def resume_source_schedule(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceScheduleRevisionRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        schedule = _schedule_repository().resume(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _schedule_payload(schedule)


@router.delete(
    "/{source_id}/schedule",
    response_model=SourceScheduleResponse,
    summary="Archive the fixed-interval UTC source schedule",
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def archive_source_schedule(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceScheduleRevisionRequest,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        schedule = _schedule_repository().archive(
            actor.tenant_id,
            dataset_id,
            source_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_source(exc)
    return _schedule_payload(schedule)


@router.post(
    "/{source_id}/sync-now",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunAcceptedResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def sync_now(
    dataset_id: DatasetId,
    source_id: SourceId,
    body: SourceSyncRequest,
    actor: WriteActor,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=16,
            max_length=128,
            pattern=_IDEMPOTENCY_PATTERN,
        ),
    ],
) -> dict[str, Any]:
    ledger = _ledger()
    try:
        source = ledger.get_source_scoped(actor.tenant_id, dataset_id, source_id)
        result = ledger.request_run(
            actor.tenant_id,
            dataset_id,
            source_id,
            trigger="manual",
            force_full=body.force_full,
            dry_run=body.dry_run,
            audit=actor.to_audit_context(),
            idempotency_key=idempotency_key,
            request_hash=_request_hash(source_id, body),
        )
        request_source_dispatch(request.app, result.run.id)
        run = result.run
    except HTTPException:
        raise
    except Exception as exc:
        _raise_source(exc)
    return {
        "run_id": run.id,
        "source_id": source.id,
        "status": run.status,
        "trigger": run.trigger,
        "retry_of_run_id": run.retry_of_run_id,
        "execution_state": run.execution_state,
        "replayed": not result.created,
    }


@router.get(
    "/{source_id}/runs",
    response_model=RunListResponse,
    responses=_error_responses(401, 403, 404, 422, 503),
)
def list_runs(
    dataset_id: DatasetId,
    source_id: SourceId,
    actor: ReadActor,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    trigger: Annotated[RunTrigger | None, Query()] = None,
    cursor: Annotated[str | None, Query(pattern=_CURSOR_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    before_time, before_id = _decode_cursor(cursor, "run")
    try:
        rows = _ledger().list_runs_scoped(
            actor.tenant_id,
            dataset_id,
            source_id,
            status=run_status,
            trigger=trigger,
            before_started_at=before_time,
            before_id=before_id,
            limit=limit + 1,
        )
    except Exception as exc:
        _raise_source(exc)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _encode_cursor("run", page[-1].started_at, page[-1].id)
    return {
        "items": [_run_payload(row) for row in page],
        "count": len(page),
        "next_cursor": next_cursor,
    }


@router.get(
    "/{source_id}/runs/{run_id}",
    response_model=RunResponse,
    responses=_error_responses(401, 403, 404, 422, 503),
)
def get_run(
    dataset_id: DatasetId,
    source_id: SourceId,
    run_id: RunId,
    actor: ReadActor,
) -> dict[str, Any]:
    try:
        run = _ledger().get_run_scoped(actor.tenant_id, dataset_id, source_id, run_id)
    except Exception as exc:
        _raise_source(exc)
    return _run_payload(run)


@router.get(
    "/{source_id}/runs/{run_id}/items",
    response_model=RunItemListResponse,
    responses=_error_responses(401, 403, 404, 422, 503),
)
def list_run_items(
    dataset_id: DatasetId,
    source_id: SourceId,
    run_id: RunId,
    actor: ReadActor,
    result: Annotated[ItemResult | None, Query()] = None,
    action: Annotated[ItemAction | None, Query()] = None,
    cursor: Annotated[str | None, Query(pattern=_CURSOR_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    before_time, before_id = _decode_cursor(cursor, "item")
    try:
        rows = _ledger().list_items_scoped(
            actor.tenant_id,
            dataset_id,
            source_id,
            run_id,
            result=result,
            action=action,
            before_created_at=before_time,
            before_id=before_id,
            limit=limit + 1,
        )
    except Exception as exc:
        _raise_source(exc)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _encode_cursor("item", page[-1].created_at, page[-1].id)
    return {
        "items": [_item_payload(row) for row in page],
        "count": len(page),
        "next_cursor": next_cursor,
    }


@router.post(
    "/{source_id}/runs/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunAcceptedResponse,
    responses=_error_responses(401, 403, 404, 409, 422, 503),
)
def retry_run(
    dataset_id: DatasetId,
    source_id: SourceId,
    run_id: RunId,
    body: SourceRetryRequest,
    actor: ManageActor,
    request: Request,
) -> dict[str, Any]:
    del body
    ledger = _ledger()
    try:
        source = ledger.get_source_scoped(actor.tenant_id, dataset_id, source_id)
        result: SourceRunRequestResult = ledger.retry_failed_run(
            actor.tenant_id,
            dataset_id,
            source_id,
            run_id,
            audit=actor.to_audit_context(),
        )
        request_source_dispatch(request.app, result.run.id)
        run = result.run
    except HTTPException:
        raise
    except Exception as exc:
        _raise_source(exc)
    return {
        "run_id": run.id,
        "source_id": source.id,
        "status": run.status,
        "trigger": run.trigger,
        "retry_of_run_id": run_id,
        "execution_state": run.execution_state,
        "replayed": not result.created,
    }


__all__ = ["router", "sanitize_source_uri"]
