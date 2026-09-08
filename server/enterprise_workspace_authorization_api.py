"""Strict HTTP API for Stage 17 Workspace authorization rollout."""

from __future__ import annotations
from collections.abc import Callable, Mapping
import importlib
import re
from typing import Annotated, Any, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
WorkspaceId = Annotated[
    str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Mode = Literal["disabled", "shadow", "enforced"]
_ERROR = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


class ModeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    target_mode: Mode
    reason: str = Field(min_length=1, max_length=512)


def _service():
    try:
        return importlib.import_module("core.enterprise_workspace_authorization")
    except (ImportError, ModuleNotFoundError) as e:
        raise HTTPException(
            503,
            detail={
                "code": "workspace_authorization_unavailable",
                "message": "Workspace 授权服务暂不可用",
            },
        ) from e


def _result(value: Any) -> tuple[dict[str, Any], int]:
    if isinstance(value, Mapping):
        return dict(value), 200
    body = getattr(value, "body", None)
    status = getattr(value, "status", 200)
    if not isinstance(body, Mapping):
        raise HTTPException(
            503,
            detail={
                "code": "workspace_authorization_unavailable",
                "message": "Workspace 授权服务暂不可用",
            },
        )
    return dict(body), int(status)


def _error(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if (
        type(status) is int
        and 400 <= status <= 599
        and isinstance(code, str)
        and _ERROR.fullmatch(code)
        and isinstance(message, str)
    ):
        detail = {"code": code, "message": message}
        policy = getattr(exc, "policy", None)
        if isinstance(policy, Mapping):
            detail["approval_required"] = {
                "policy_id": str(policy.get("id", "")),
                "policy_name": str(policy.get("name", "")),
                "required_approvals": int(policy.get("required_approvals", 1)),
            }
        return HTTPException(status, detail=detail)
    return HTTPException(
        503,
        detail={
            "code": "workspace_authorization_unavailable",
            "message": "Workspace 授权服务暂不可用",
        },
    )


def build_workspace_authorization_router(
    *, read_engine_provider: EngineProvider, mutation_engine_provider: EngineProvider
) -> APIRouter:
    router = APIRouter(tags=["enterprise-workspace-authorization"])
    require_actor = require_knowledge_permission(KNOWLEDGE_READ)

    @router.get("/api/enterprise/workspaces/{workspace_id}/authorization")
    def get_policy(
        workspace_id: WorkspaceId, actor: KnowledgeActor = Depends(require_actor)
    ) -> JSONResponse:
        try:
            body, status = _result(
                _service().get_workspace_authorization_policy(
                    read_engine_provider(),
                    tenant_id=actor.tenant_id,
                    actor_id=actor.account_id,
                    workspace_id=workspace_id,
                )
            )
            return JSONResponse(body, status_code=status)
        except HTTPException:
            raise
        except Exception as e:
            raise _error(e) from e

    @router.get("/api/enterprise/workspaces/{workspace_id}/authorization/impact")
    def get_impact(
        workspace_id: WorkspaceId,
        dataset_id: str = Query(min_length=1, max_length=64),
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            body, status = _result(
                _service().get_workspace_authorization_impact(
                    read_engine_provider(),
                    tenant_id=actor.tenant_id,
                    actor_id=actor.account_id,
                    workspace_id=workspace_id,
                    dataset_id=dataset_id,
                )
            )
            return JSONResponse(body, status_code=status)
        except HTTPException:
            raise
        except Exception as e:
            raise _error(e) from e

    @router.patch("/api/enterprise/workspaces/{workspace_id}/authorization")
    def change_mode(
        workspace_id: WorkspaceId,
        body: ModeChange,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            service = _service()
            result = service.change_workspace_authorization_mode(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                workspace_id=workspace_id,
                expected_policy_revision=body.expected_revision,
                target_mode=body.target_mode,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
            # Return the strict policy/evidence projection expected by the UI after mutation.
            policy_result = service.get_workspace_authorization_policy(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                workspace_id=workspace_id,
            )
            response, status = _result(policy_result)
            response["mutation"] = {
                "result": "updated",
                "resource_id": result.get("authorization_policy", {}).get("id")
                if isinstance(result, Mapping)
                else None,
            }
            return JSONResponse(response, status_code=status)
        except HTTPException:
            raise
        except Exception as e:
            raise _error(e) from e

    return router


__all__ = ["build_workspace_authorization_router"]
