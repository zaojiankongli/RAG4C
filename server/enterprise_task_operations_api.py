"""Strict authenticated HTTP boundary for Stage 24 Enterprise Task Operations."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
import importlib
import re
from typing import Annotated, Any, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
ServiceProvider = Any
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE = re.compile(
    r"(?i)(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key)(?:\s|[:=\"'([{])|(?:[a-z][a-z0-9+.-]{1,31}://)"
)
_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_MODULE = "core.enterprise_task_operations_service"
_UNAVAILABLE = "enterprise_task_operations_unavailable"
_SOURCE_KINDS = (
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
)
_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "blocked", "unavailable")
TaskId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
ViewId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
SourceKind = Literal[
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
]
TaskStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "blocked", "unavailable"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskActionRequest(StrictModel):
    expected_source_revision: int = Field(ge=1, strict=True)
    expected_source_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=512)


class ReconcileRequest(StrictModel):
    source_kinds: list[SourceKind] = Field(min_length=1, max_length=7)
    dry_run: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("source_kinds")
    @classmethod
    def unique_kinds(cls, value: list[SourceKind]) -> list[SourceKind]:
        if len(set(value)) != len(value):
            raise ValueError("source kinds must be unique")
        return value


class SavedViewFilters(StrictModel):
    source_kinds: list[SourceKind] | None = Field(default=None, max_length=7)
    statuses: list[TaskStatus] | None = Field(default=None, max_length=7)
    categories: (
        list[
            Literal[
                "content", "documents", "indexing", "source", "sources", "compliance", "quality"
            ]
        ]
        | None
    ) = Field(default=None, max_length=5)
    dataset_id: str | None = Field(default=None, max_length=64, pattern=_SAFE_ID.pattern)
    workspace_id: str | None = Field(default=None, max_length=128, pattern=_SAFE_ID.pattern)
    action_required: bool | None = Field(default=None, strict=True)
    occurred_from: str | None = Field(default=None, max_length=64)
    occurred_to: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_time_range(self) -> "SavedViewFilters":
        if (self.occurred_from is None) != (self.occurred_to is None):
            raise ValueError("bounded time filters require occurred_from and occurred_to")
        if self.occurred_from is not None and self.occurred_from > self.occurred_to:
            raise ValueError("saved view time range is invalid")
        return self


class SavedViewRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    filters: SavedViewFilters
    reason: str = Field(min_length=1, max_length=512)


class SavedViewPatch(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    filters: SavedViewFilters | None = None
    status: Literal["active", "archived"] | None = None
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "SavedViewPatch":
        if self.name is None and self.filters is None and self.status is None:
            raise ValueError("saved view patch requires a change")
        return self


def unavailable(message: str = "Enterprise Task Operations 服务暂不可用") -> HTTPException:
    return HTTPException(status_code=503, detail={"code": _UNAVAILABLE, "message": message})


def safe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc if isinstance(exc.detail, Mapping) else unavailable()
    status, code, message = (
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(exc, "message", None),
    )
    if (
        type(status) is int
        and 400 <= status <= 599
        and isinstance(code, str)
        and _CODE.fullmatch(code)
        and isinstance(message, str)
        and 0 < len(message) <= 512
        and not _CONTROL.search(message)
        and not _UNSAFE.search(message)
    ):
        return HTTPException(status_code=status, detail={"code": code, "message": message})
    return unavailable()


def clean_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or _CONTROL.search(normalized):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "enterprise_task_request_invalid",
                "message": "Idempotency-Key is invalid",
            },
        )
    return normalized


def load_service(injected: ServiceProvider | None) -> Any:
    if injected is not None:
        return injected
    try:
        return importlib.import_module(_MODULE)
    except Exception as exc:
        raise unavailable() from exc


def service_call(service: Any, operation: str, engine: Any, **kwargs: Any) -> Any:
    try:
        method = getattr(service, operation)
        if not callable(method):
            raise AttributeError(operation)
        return method(engine, **kwargs)
    except Exception as exc:
        raise safe_error(exc) from exc


def response(value: Any) -> JSONResponse:
    if isinstance(value, Mapping):
        body, status = dict(value), 200
    else:
        raw, status = getattr(value, "body", None), getattr(value, "status", 200)
        if isinstance(raw, Mapping):
            body = dict(raw)
        elif is_dataclass(value):
            body = asdict(value)
            status = 200
        else:
            raise unavailable("Enterprise Task Operations 返回无效")
    if type(status) is not int or not 100 <= status <= 599 or status == 204:
        raise unavailable("Enterprise Task Operations 返回无效")
    encoded = jsonable_encoder(body)
    if not isinstance(encoded, Mapping):
        raise unavailable("Enterprise Task Operations 返回无效")
    return JSONResponse(dict(encoded), status_code=status)


def build_enterprise_task_operations_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    actor_dep = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["enterprise-task-operations"])

    def read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        return response(
            service_call(
                load_service(service),
                operation,
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                **kwargs,
            )
        )

    def mutate(operation: str, actor: KnowledgeActor, key: str, **kwargs: Any) -> JSONResponse:
        return response(
            service_call(
                load_service(service),
                operation,
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                account_id=actor.account_id,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=clean_key(key),
                **kwargs,
            )
        )

    @router.get("/api/enterprise/tasks/summary")
    def summary(actor: KnowledgeActor = Depends(actor_dep)):
        return read("get_task_summary", actor)

    @router.get("/api/enterprise/tasks")
    def tasks(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: TaskStatus | None = None,
        source_kind: SourceKind | None = None,
        action_required: bool | None = None,
    ):
        return read(
            "list_tasks",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
            source_kind=source_kind,
            action_required=action_required,
        )

    @router.get("/api/enterprise/tasks/reconciliation-runs")
    def reconciliation_runs(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: Literal["started", "running", "completed", "failed", "all"] | None = None,
    ):
        return read(
            "list_reconciliation_runs",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
        )

    @router.post("/api/enterprise/tasks/reconcile/preview")
    def preview(
        body: ReconcileRequest, key: IdempotencyKey, actor: KnowledgeActor = Depends(actor_dep)
    ):
        return mutate("preview_task_reconciliation", actor, key, **body.model_dump())

    @router.post("/api/enterprise/tasks/reconcile")
    def reconcile(
        body: ReconcileRequest, key: IdempotencyKey, actor: KnowledgeActor = Depends(actor_dep)
    ):
        return mutate("reconcile_enterprise_tasks", actor, key, **body.model_dump())

    @router.get("/api/enterprise/tasks/{task_id}")
    def task(task_id: TaskId, actor: KnowledgeActor = Depends(actor_dep)):
        return read("get_task", actor, task_id=task_id)

    @router.get("/api/enterprise/tasks/{task_id}/events")
    def events(
        task_id: TaskId,
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return read(
            "list_task_events",
            actor,
            task_id=task_id,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/tasks/{task_id}/retry")
    def retry(
        task_id: TaskId,
        body: TaskActionRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ):
        return mutate("request_task_retry", actor, key, task_id=task_id, **body.model_dump())

    @router.post("/api/enterprise/tasks/{task_id}/cancel")
    def cancel(
        task_id: TaskId,
        body: TaskActionRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ):
        return mutate("request_task_cancel", actor, key, task_id=task_id, **body.model_dump())

    @router.post("/api/enterprise/tasks/{task_id}/acknowledge")
    def acknowledge(
        task_id: TaskId,
        body: TaskActionRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ):
        return mutate(
            "acknowledge_task_attention", actor, key, task_id=task_id, **body.model_dump()
        )

    @router.get("/api/enterprise/task-views")
    def views(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: Literal["active", "archived", "all"] | None = None,
    ):
        return read(
            "list_saved_task_views",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
        )

    @router.post("/api/enterprise/task-views")
    def create_view(
        body: SavedViewRequest, key: IdempotencyKey, actor: KnowledgeActor = Depends(actor_dep)
    ):
        return mutate("create_saved_task_view", actor, key, **body.model_dump())

    @router.patch("/api/enterprise/task-views/{view_id}")
    def update_view(
        view_id: ViewId,
        body: SavedViewPatch,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ):
        return mutate(
            "update_saved_task_view",
            actor,
            key,
            view_id=view_id,
            **body.model_dump(exclude_none=True),
        )

    return router


__all__ = [
    "ReconcileRequest",
    "SavedViewRequest",
    "TaskActionRequest",
    "build_enterprise_task_operations_router",
]
