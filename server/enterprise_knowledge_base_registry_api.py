"""Strict authenticated HTTP API for the Stage 18 Knowledge Base Registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset


EngineProvider = Callable[[], Any]
ServiceProvider = Any
_SAFE_RESOURCE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SAFE_DATASET_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)]
AppId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_RESOURCE_ID)]
WorkspaceId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_RESOURCE_ID)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
DatasetStatus = Literal["active", "archived", "disabled"]
ReferenceStatus = Literal["active", "removed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AppReferenceCreateRequest(_StrictModel):
    reference_kind: Literal["knowledge"] = "knowledge"
    reason: str = Field(min_length=1, max_length=512)


class AppReferenceRemoveRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class WorkspaceTransferRequest(_StrictModel):
    target_workspace_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_RESOURCE_ID)
    expected_dataset_profile_revision: int = Field(ge=1, strict=True)
    expected_ownership_revision: int = Field(ge=1, strict=True)
    expected_source_workspace_revision: int = Field(ge=1, strict=True)
    expected_target_workspace_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


def _default_service() -> Any:
    try:
        return importlib.import_module("core.enterprise_knowledge_base_registry")
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_base_registry_unavailable",
                "message": "Knowledge Base Registry 服务暂不可用",
            },
        ) from exc


def _result(value: Any) -> tuple[dict[str, Any], int]:
    if isinstance(value, Mapping):
        return dict(value), 200
    body = getattr(value, "body", None)
    response_status = getattr(value, "status", 200)
    if not isinstance(body, Mapping) or type(response_status) is not int:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_base_registry_unavailable",
                "message": "Knowledge Base Registry 返回无效",
            },
        )
    return dict(body), response_status


def _error(exc: Exception) -> HTTPException:
    response_status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if (
        type(response_status) is int
        and 400 <= response_status <= 599
        and isinstance(code, str)
        and _ERROR_CODE.fullmatch(code)
        and isinstance(message, str)
    ):
        detail: dict[str, Any] = {"code": code, "message": message}
        policy = getattr(exc, "policy", None)
        if isinstance(policy, Mapping) and code == "dataset_workspace_transfer_approval_required":
            detail["approval_required"] = {
                "policy_id": str(policy.get("id", "")),
                "policy_name": str(policy.get("name", "")),
                "required_approvals": int(policy.get("required_approvals", 1)),
            }
        return HTTPException(status_code=response_status, detail=detail)
    return HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_base_registry_unavailable",
            "message": "Knowledge Base Registry 服务暂不可用",
        },
    )


def build_enterprise_knowledge_base_registry_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the Registry router with separate read and mutation engines.

    ``actor_dependency`` exists for isolated router tests and controlled
    integration harnesses. Production callers omit it so each route uses the
    appropriate signed KnowledgeActor permission dependency.
    """

    registry = service or _default_service()
    read_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    dataset_read_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_READ, resolve_path_dataset("dataset_id")
    )
    dataset_manage_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id")
    )
    router = APIRouter(tags=["enterprise-knowledge-base-registry"])

    def execute_read(method: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        try:
            body, response_status = _result(
                getattr(registry, method)(
                    read_engine_provider(),
                    tenant_id=actor.tenant_id,
                    actor_id=actor.account_id,
                    **kwargs,
                )
            )
            return JSONResponse(body, status_code=response_status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    def execute_mutation(
        method: str,
        actor: KnowledgeActor,
        *,
        idempotency_key: str,
        default_status: int | None = None,
        **kwargs: Any,
    ) -> JSONResponse:
        try:
            body, response_status = _result(
                getattr(registry, method)(
                    mutation_engine_provider(),
                    tenant_id=actor.tenant_id,
                    actor_id=actor.account_id,
                    actor_role=actor.role,
                    request_id=actor.request_id,
                    request_ip=actor.request_ip,
                    idempotency_key=idempotency_key,
                    **kwargs,
                )
            )
            if default_status is not None and response_status == 200:
                response_status = default_status
            return JSONResponse(body, status_code=response_status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/knowledge-bases")
    def list_knowledge_bases_route(
        actor: KnowledgeActor = Depends(read_actor_dependency),
        workspace_id: WorkspaceId | None = Query(default=None),
        status: DatasetStatus | None = Query(default=None),
        keyword: str | None = Query(default=None, min_length=1, max_length=128),
        q: str | None = Query(default=None, min_length=1, max_length=128),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> JSONResponse:
        return execute_read(
            "list_knowledge_bases",
            actor,
            workspace_id=workspace_id,
            status=status,
            keyword=keyword if keyword is not None else q,
            cursor=cursor,
            limit=limit,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}")
    def get_knowledge_base_route(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read("get_knowledge_base", actor, dataset_id=dataset_id)

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/dependencies")
    def get_dependencies_route(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read("get_knowledge_base_dependencies", actor, dataset_id=dataset_id)

    @router.get("/api/enterprise/apps/{app_id}/knowledge-bases")
    def list_app_references_route(
        app_id: AppId,
        actor: KnowledgeActor = Depends(read_actor_dependency),
        status: ReferenceStatus | None = Query(default=None),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {"app_id": app_id}
        if status is not None:
            kwargs["status"] = status
        return execute_read("list_app_references", actor, **kwargs)

    @router.post("/api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}", status_code=201)
    def create_app_reference_route(
        app_id: AppId,
        dataset_id: DatasetId,
        body: AppReferenceCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "create_app_reference",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            app_id=app_id,
            dataset_id=dataset_id,
            reference_kind=body.reference_kind,
            reason=body.reason,
        )

    @router.delete("/api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}")
    def remove_app_reference_route(
        app_id: AppId,
        dataset_id: DatasetId,
        body: AppReferenceRemoveRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "remove_app_reference",
            actor,
            idempotency_key=idempotency_key,
            app_id=app_id,
            dataset_id=dataset_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/workspace-transfer")
    def transfer_workspace_route(
        dataset_id: DatasetId,
        body: WorkspaceTransferRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "transfer_dataset_ownership",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            target_workspace_id=body.target_workspace_id,
            expected_dataset_profile_revision=body.expected_dataset_profile_revision,
            expected_ownership_revision=body.expected_ownership_revision,
            expected_source_workspace_revision=body.expected_source_workspace_revision,
            expected_target_workspace_revision=body.expected_target_workspace_revision,
            reason=body.reason,
        )

    return router


__all__ = [
    "AppReferenceCreateRequest",
    "AppReferenceRemoveRequest",
    "WorkspaceTransferRequest",
    "build_enterprise_knowledge_base_registry_router",
]
