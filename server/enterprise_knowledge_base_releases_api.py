"""Strict authenticated HTTP API for Stage19 Knowledge Base Releases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

EngineProvider = Callable[[], Any]
ServiceProvider = Any
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SAFE_DATASET_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)]
AppId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ReleaseId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ChannelId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
RiskTier = Literal["low", "medium", "high"]
ReleaseMode = Literal["follow_channel", "pinned"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReleaseChannelCreateRequest(_StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    name: str = Field(min_length=1, max_length=128)
    risk_tier: RiskTier
    promotion_order: int = Field(ge=0, strict=True)
    is_default_serving: bool = Field(default=False, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ReleaseChannelPatchRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    risk_tier: RiskTier | None = None
    promotion_order: int | None = Field(default=None, ge=0, strict=True)
    is_default_serving: bool | None = Field(default=None, strict=True)
    status: Literal["active", "archived"] | None = None
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "ReleaseChannelPatchRequest":
        if not any(
            value is not None
            for value in (
                self.name,
                self.risk_tier,
                self.promotion_order,
                self.is_default_serving,
                self.status,
            )
        ):
            raise ValueError("channel patch must include at least one changed field")
        return self


class ReleaseCaptureRequest(_StrictModel):
    expected_profile_revision: int = Field(ge=1, strict=True)
    expected_ownership_revision: int = Field(ge=1, strict=True)
    expected_workspace_revision: int = Field(ge=1, strict=True)
    expected_mutation_generation: int = Field(ge=0, strict=True)
    expected_serving_generation: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ReleasePromotionRequest(_StrictModel):
    channel_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    expected_channel_revision: int = Field(ge=1, strict=True)
    expected_profile_revision: int = Field(ge=1, strict=True)
    expected_ownership_revision: int = Field(ge=1, strict=True)
    expected_workspace_revision: int = Field(ge=1, strict=True)
    expected_serving_generation: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ReleaseRollbackRequest(_StrictModel):
    target_release_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    expected_channel_revision: int = Field(ge=1, strict=True)
    expected_serving_generation: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class AppReleaseBindingRequest(_StrictModel):
    expected_reference_revision: int = Field(ge=1, strict=True)
    release_mode: ReleaseMode
    release_channel_id: str | None = Field(default=None, min_length=1, max_length=128)
    pinned_release_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def exact_binding_authority(self) -> "AppReleaseBindingRequest":
        follows = self.release_mode == "follow_channel"
        if follows != (self.release_channel_id is not None):
            raise ValueError("follow_channel requires release_channel_id")
        if follows != (self.pinned_release_id is None):
            raise ValueError("follow_channel forbids pinned_release_id")
        if not follows and self.pinned_release_id is None:
            raise ValueError("pinned requires pinned_release_id")
        return self


def _default_service() -> Any:
    try:
        return importlib.import_module("core.enterprise_knowledge_base_releases")
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_base_release_unavailable",
                "message": "Knowledge Base Release 服务暂不可用",
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
                "code": "knowledge_base_release_unavailable",
                "message": "Knowledge Base Release 返回无效",
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
        if isinstance(policy, Mapping) and code in {
            "knowledge_base_release_publish_approval_required",
            "knowledge_base_release_rollback_approval_required",
        }:
            detail["approval_required"] = {
                "policy_id": str(policy.get("id", "")),
                "policy_name": str(policy.get("name", "")),
                "required_approvals": int(policy.get("required_approvals", 1)),
            }
        return HTTPException(status_code=response_status, detail=detail)
    return HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_base_release_unavailable",
            "message": "Knowledge Base Release 服务暂不可用",
        },
    )


def build_enterprise_knowledge_base_releases_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    releases = service or _default_service()
    read_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    manage_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_MANAGE)
    dataset_read_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_READ, resolve_path_dataset("dataset_id")
    )
    dataset_manage_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id")
    )
    router = APIRouter(tags=["enterprise-knowledge-base-releases"])

    def execute_read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        try:
            value = getattr(releases, operation)(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                **kwargs,
            )
            body, response_status = _result(value)
            return JSONResponse(body, status_code=response_status)
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
            value = getattr(releases, operation)(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
                **kwargs,
            )
            body, response_status = _result(value)
            if response_status == 200:
                response_status = default_status
            return JSONResponse(body, status_code=response_status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/release-channels")
    def list_channels_route(
        actor: KnowledgeActor = Depends(read_actor_dependency),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read("list_release_channels", actor, cursor=cursor, limit=limit)

    @router.post("/api/enterprise/release-channels", status_code=201)
    def create_channel_route(
        body: ReleaseChannelCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "create_release_channel",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/release-channels/{channel_id}")
    def update_channel_route(
        channel_id: ChannelId,
        body: ReleaseChannelPatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "update_release_channel",
            actor,
            idempotency_key=idempotency_key,
            channel_id=channel_id,
            **body.model_dump(exclude_none=True),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases")
    def list_releases_route(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        channel_id: str | None = Query(default=None, min_length=1, max_length=128),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "cursor": cursor,
            "limit": limit,
        }
        if channel_id is not None:
            kwargs["channel_id"] = channel_id
        return execute_read("list_releases", actor, **kwargs)

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/releases", status_code=201)
    def capture_release_route(
        dataset_id: DatasetId,
        body: ReleaseCaptureRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "capture_release_candidate",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            dataset_id=dataset_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}")
    def get_release_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read("get_release", actor, dataset_id=dataset_id, release_id=release_id)

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/readiness")
    def get_readiness_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read(
            "get_release_readiness", actor, dataset_id=dataset_id, release_id=release_id
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/impact")
    def get_impact_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read(
            "get_release_impact", actor, dataset_id=dataset_id, release_id=release_id
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/audit")
    def get_audit_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read(
            "get_release_audit", actor, dataset_id=dataset_id, release_id=release_id
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/release-channels")
    def list_bindings_route(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read("list_channel_bindings", actor, dataset_id=dataset_id)

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/promote")
    def promote_release_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        body: ReleasePromotionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "promote_release",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            release_id=release_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/channels/{channel_id}/rollback")
    def rollback_release_route(
        dataset_id: DatasetId,
        channel_id: ChannelId,
        body: ReleaseRollbackRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "rollback_channel_release",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            channel_id=channel_id,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}/release-binding")
    def update_app_binding_route(
        app_id: AppId,
        dataset_id: DatasetId,
        body: AppReleaseBindingRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "update_app_release_binding",
            actor,
            idempotency_key=idempotency_key,
            app_id=app_id,
            dataset_id=dataset_id,
            **body.model_dump(),
        )

    return router


__all__ = [
    "AppReleaseBindingRequest",
    "ReleaseCaptureRequest",
    "ReleaseChannelCreateRequest",
    "ReleaseChannelPatchRequest",
    "ReleasePromotionRequest",
    "ReleaseRollbackRequest",
    "build_enterprise_knowledge_base_releases_router",
]
