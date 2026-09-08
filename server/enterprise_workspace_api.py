"""HTTP contract for the Stage 16 enterprise Workspace control plane.

The router is deliberately a thin, dependency-injected adapter.  The Workspace
core is loaded lazily so the main application can start while an older catalog
or a partially deployed binary still lacks the 0026 service; every Workspace
request then fails closed with a stable 503 response instead of importing an
incomplete authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.catalog_schema import CatalogSchemaError
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
)
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
WorkspaceStatus = Literal["active", "archived"]
WorkspaceEnvironment = Literal["development", "testing", "production"]
WorkspaceMemberRole = Literal["owner", "admin", "editor", "viewer"]
WorkspaceMemberStatus = Literal["active", "removed"]
WorkspaceDatasetKind = Literal["primary", "shared"]
WorkspaceDatasetStatus = Literal["active", "removed"]

_SAFE_RESOURCE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_SAFE_WORKSPACE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SAFE_CODE = r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_REQUIRED_SERVICE_METHODS = (
    "list_workspaces",
    "get_workspace",
    "create_workspace",
    "update_workspace",
    "archive_workspace",
    "list_workspace_members",
    "add_workspace_member",
    "update_workspace_member",
    "remove_workspace_member",
    "list_workspace_datasets",
    "bind_workspace_dataset",
    "remove_workspace_dataset",
)

WorkspaceId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_WORKSPACE_ID)]
AccountId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_RESOURCE_ID)]
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_RESOURCE_ID)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreateRequest(_StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=_SAFE_CODE)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    environment: WorkspaceEnvironment
    reason: str = Field(min_length=1, max_length=512)


class WorkspaceUpdateRequest(_StrictModel):
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    environment: WorkspaceEnvironment | None = None
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "WorkspaceUpdateRequest":
        if self.name is None and self.description is None and self.environment is None:
            raise ValueError("at least one workspace field must change")
        return self


class RevisionReasonRequest(_StrictModel):
    revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class WorkspaceMemberAddRequest(_StrictModel):
    account_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_RESOURCE_ID)
    role: WorkspaceMemberRole
    reason: str = Field(min_length=1, max_length=512)


class WorkspaceMemberUpdateRequest(_StrictModel):
    revision: int = Field(ge=1, strict=True)
    role: WorkspaceMemberRole
    reason: str = Field(min_length=1, max_length=512)


class WorkspaceDatasetBindRequest(_StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_RESOURCE_ID)
    binding_kind: WorkspaceDatasetKind
    reason: str = Field(min_length=1, max_length=512)


class _WorkspaceServiceUnavailable(RuntimeError):
    pass


def _load_default_service() -> Any:
    try:
        module = importlib.import_module("core.enterprise_workspace_control")
    except (ImportError, ModuleNotFoundError) as exc:
        raise _WorkspaceServiceUnavailable from exc
    if any(not callable(getattr(module, name, None)) for name in _REQUIRED_SERVICE_METHODS):
        raise _WorkspaceServiceUnavailable
    return module


def _service_or_fail(configured: Any | None) -> Any:
    service = configured if configured is not None else _load_default_service()
    if any(not callable(getattr(service, name, None)) for name in _REQUIRED_SERVICE_METHODS):
        raise _WorkspaceServiceUnavailable
    return service


def _normalized_result(result: Any, *, default_status: int) -> tuple[dict[str, Any], int]:
    if isinstance(result, Mapping):
        body = dict(result)
        status = default_status
    else:
        raw_body = getattr(result, "body", None)
        raw_status = getattr(result, "status", default_status)
        if not isinstance(raw_body, Mapping):
            raise _WorkspaceServiceUnavailable
        body = dict(raw_body)
        try:
            status = int(raw_status)
        except (TypeError, ValueError) as exc:
            raise _WorkspaceServiceUnavailable from exc
    if status < 200 or status > 299:
        raise _WorkspaceServiceUnavailable
    body["workspace_authorization_not_enforced"] = True
    return body, status


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantMutationIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "workspace_idempotency_conflict",
                "message": "Idempotency-Key 已用于不同的 Workspace 请求",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "workspace_idempotency_in_progress",
                "message": "同一 Workspace 操作正在处理中",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "workspace_request_invalid", "message": "Workspace 请求无效"},
        )

    class_name = type(exc).__name__
    if isinstance(exc, CatalogSchemaError) or "WorkspaceMigrationRequired" in class_name:
        return HTTPException(
            status_code=503,
            detail={
                "code": "enterprise_workspace_migration_required",
                "message": "企业 Workspace 数据库尚未升级到 0026",
            },
        )

    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if (
        type(status) is int
        and 400 <= status <= 599
        and isinstance(code, str)
        and _ERROR_CODE.fullmatch(code)
        and isinstance(message, str)
        and 1 <= len(message) <= 512
    ):
        return HTTPException(status_code=status, detail={"code": code, "message": message})

    return HTTPException(
        status_code=503,
        detail={
            "code": "enterprise_workspace_unavailable",
            "message": "企业 Workspace 服务暂不可用",
        },
    )


def _read_kwargs(actor: KnowledgeActor) -> dict[str, str]:
    return {
        "tenant_id": actor.tenant_id,
        "actor_id": actor.account_id,
    }


def _mutation_kwargs(actor: KnowledgeActor) -> dict[str, str]:
    return _read_kwargs(actor) | {
        "request_id": actor.request_id,
        "request_ip": actor.request_ip,
    }


def build_enterprise_workspace_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: Any | None = None,
) -> APIRouter:
    """Build the authenticated Stage 16 Workspace router.

    All routes require a signed active TenantMember.  Workspace-level and
    Tenant owner/admin authorization remains a core authority decision; the
    HTTP layer intentionally uses ``knowledge.read`` so a future Workspace
    admin is not silently rejected only because their Tenant role is lower.
    """

    if not callable(read_engine_provider):
        raise TypeError("read_engine_provider must be callable")
    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")

    router = APIRouter(tags=["enterprise-workspaces"])
    require_actor = require_knowledge_permission(KNOWLEDGE_READ)

    def execute_read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        try:
            handler = getattr(_service_or_fail(service), operation)
            result = handler(read_engine_provider(), **_read_kwargs(actor), **kwargs)
            body, status = _normalized_result(result, default_status=200)
            return JSONResponse(body, status_code=status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    def execute_mutation(
        operation: str,
        actor: KnowledgeActor,
        *,
        idempotency_key: str,
        default_status: int = 200,
        **kwargs: Any,
    ) -> JSONResponse:
        try:
            handler = getattr(_service_or_fail(service), operation)
            result = handler(
                mutation_engine_provider(),
                **_mutation_kwargs(actor),
                idempotency_key=idempotency_key,
                **kwargs,
            )
            body, status = _normalized_result(result, default_status=default_status)
            return JSONResponse(body, status_code=status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/workspaces")
    def list_workspaces_route(
        actor: KnowledgeActor = Depends(require_actor),
        status: WorkspaceStatus | None = Query(default=None),
        environment: WorkspaceEnvironment | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read(
            "list_workspaces",
            actor,
            status=status,
            environment=environment,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/api/enterprise/workspaces/{workspace_id}")
    def get_workspace_route(
        workspace_id: WorkspaceId,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_read("get_workspace", actor, workspace_id=workspace_id)

    @router.post("/api/enterprise/workspaces", status_code=201)
    def create_workspace_route(
        body: WorkspaceCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "create_workspace",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            code=body.code,
            name=body.name,
            description=body.description,
            environment=body.environment,
            reason=body.reason,
        )

    @router.patch("/api/enterprise/workspaces/{workspace_id}")
    def update_workspace_route(
        workspace_id: WorkspaceId,
        body: WorkspaceUpdateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        changes = body.model_dump(
            exclude={"revision", "reason"},
            exclude_unset=True,
        )
        return execute_mutation(
            "update_workspace",
            actor,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
            expected_revision=body.revision,
            **changes,
            reason=body.reason,
        )

    @router.post("/api/enterprise/workspaces/{workspace_id}/archive")
    def archive_workspace_route(
        workspace_id: WorkspaceId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "archive_workspace",
            actor,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
            expected_revision=body.revision,
            reason=body.reason,
        )

    @router.get("/api/enterprise/workspaces/{workspace_id}/members")
    def list_workspace_members_route(
        workspace_id: WorkspaceId,
        actor: KnowledgeActor = Depends(require_actor),
        status: WorkspaceMemberStatus | None = Query(default=None),
        role: WorkspaceMemberRole | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read(
            "list_workspace_members",
            actor,
            workspace_id=workspace_id,
            status=status,
            role=role,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/workspaces/{workspace_id}/members", status_code=201)
    def add_workspace_member_route(
        workspace_id: WorkspaceId,
        body: WorkspaceMemberAddRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "add_workspace_member",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            workspace_id=workspace_id,
            account_id=body.account_id,
            role=body.role,
            reason=body.reason,
        )

    @router.patch("/api/enterprise/workspaces/{workspace_id}/members/{account_id}")
    def update_workspace_member_route(
        workspace_id: WorkspaceId,
        account_id: AccountId,
        body: WorkspaceMemberUpdateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "update_workspace_member",
            actor,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
            account_id=account_id,
            expected_revision=body.revision,
            role=body.role,
            reason=body.reason,
        )

    @router.post("/api/enterprise/workspaces/{workspace_id}/members/{account_id}/remove")
    def remove_workspace_member_route(
        workspace_id: WorkspaceId,
        account_id: AccountId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "remove_workspace_member",
            actor,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
            account_id=account_id,
            expected_revision=body.revision,
            reason=body.reason,
        )

    @router.get("/api/enterprise/workspaces/{workspace_id}/datasets")
    def list_workspace_datasets_route(
        workspace_id: WorkspaceId,
        actor: KnowledgeActor = Depends(require_actor),
        status: WorkspaceDatasetStatus | None = Query(default=None),
        binding_kind: WorkspaceDatasetKind | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read(
            "list_workspace_datasets",
            actor,
            workspace_id=workspace_id,
            status=status,
            binding_kind=binding_kind,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/workspaces/{workspace_id}/datasets", status_code=201)
    def bind_workspace_dataset_route(
        workspace_id: WorkspaceId,
        body: WorkspaceDatasetBindRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "bind_workspace_dataset",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            workspace_id=workspace_id,
            dataset_id=body.dataset_id,
            binding_kind=body.binding_kind,
            reason=body.reason,
        )

    @router.post("/api/enterprise/workspaces/{workspace_id}/datasets/{dataset_id}/remove")
    def remove_workspace_dataset_route(
        workspace_id: WorkspaceId,
        dataset_id: DatasetId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return execute_mutation(
            "remove_workspace_dataset",
            actor,
            idempotency_key=idempotency_key,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            expected_revision=body.revision,
            reason=body.reason,
        )

    return router


__all__ = ["build_enterprise_workspace_router"]
