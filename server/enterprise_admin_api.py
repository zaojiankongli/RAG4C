"""Authenticated enterprise administration API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from core.enterprise_approval_control import ApprovalError, resolve_active_approval_policy
from core.enterprise_directory import (
    EnterpriseCapabilityUnavailable,
    EnterpriseDirectoryInconsistent,
    EnterpriseMemberApprovalRequired,
    EnterpriseMemberForbidden,
    EnterpriseMemberRevisionConflict,
    EnterpriseMemberStateConflict,
    EnterpriseMemberValidationError,
    EnterpriseMembershipMigrationRequired,
    change_tenant_member_role,
    get_dataset_access_summary,
    get_enterprise_context,
    list_tenant_audit_events,
    list_enterprise_members,
    resume_tenant_member,
    suspend_tenant_member,
)
from core.catalog_schema import CatalogSchemaError
from core.knowledge_permissions import KNOWLEDGE_AUDIT, KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import (
    KnowledgeActor,
    require_knowledge_permission,
    resolve_path_dataset,
)

EngineProvider = Callable[[], Any]
TenantRole = Literal["owner", "admin", "editor", "member"]
_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
AccountId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
MemberQuery = Annotated[str | None, Query(min_length=1, max_length=256)]
MemberRole = Annotated[TenantRole | None, Query()]
BeforeId = Annotated[int | None, Query(ge=1)]
PageLimit = Annotated[int, Query(ge=1, le=200)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantMemberRoleRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    role: TenantRole
    reason: str = Field(min_length=1, max_length=512)


class TenantMemberStateRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "enterprise_directory_unavailable",
            "message": "企业目录服务暂不可用",
        },
    )


def _mutation_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, EnterpriseMemberApprovalRequired):
        policy = exc.policy
        return HTTPException(
            status_code=409,
            detail={
                "code": "member_role_approval_required",
                "message": "成员角色变更需要先完成审批",
                "policy_id": str(policy.get("id", "")),
                "policy_name": str(policy.get("name", "")),
                "policy_revision": int(policy.get("revision", 1)),
                "required_approvals": int(policy.get("required_approvals", 1)),
                "request_expiry_minutes": int(policy.get("request_expiry_minutes", 15)),
            },
        )
    if isinstance(exc, ApprovalError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, (CatalogSchemaError, EnterpriseMembershipMigrationRequired)):
        return HTTPException(
            status_code=503,
            detail={
                "code": "enterprise_membership_migration_required",
                "message": "企业成员管理需要先完成 0016 数据库迁移",
            },
        )
    if isinstance(exc, EnterpriseMemberForbidden):
        return HTTPException(
            status_code=403,
            detail={
                "code": "tenant_member_target_forbidden",
                "message": "目标成员不属于当前租户或当前角色无权管理",
            },
        )
    if isinstance(exc, EnterpriseMemberRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "tenant_member_revision_conflict",
                "message": "成员记录已发生变化，请刷新后重试",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        )
    if isinstance(exc, EnterpriseMemberStateConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, EnterpriseMemberValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "enterprise_member_invalid", "message": str(exc)},
        )
    if isinstance(exc, (EnterpriseCapabilityUnavailable, EnterpriseDirectoryInconsistent)):
        return _unavailable(exc)
    return _unavailable(exc)


def _coalesce_filter(primary: str | None, legacy: str | None, *, field: str) -> str | None:
    primary_value = str(primary).strip() if primary is not None else None
    legacy_value = str(legacy).strip() if legacy is not None else None
    if primary_value and legacy_value and primary_value != legacy_value:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "enterprise_audit_filter_conflict",
                "message": f"{field} 过滤条件重复且不一致",
            },
        )
    return primary_value or legacy_value


def build_enterprise_admin_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
) -> APIRouter:
    """Build the enterprise router with isolated read and mutation engines."""

    if not callable(read_engine_provider):
        raise TypeError("read_engine_provider must be callable")
    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")

    router = APIRouter(tags=["enterprise-admin"])
    require_read = require_knowledge_permission(KNOWLEDGE_READ)
    require_audit = require_knowledge_permission(KNOWLEDGE_AUDIT)
    require_manage = require_knowledge_permission(KNOWLEDGE_MANAGE)
    require_dataset_read = require_knowledge_permission(
        KNOWLEDGE_READ,
        resolve_path_dataset("dataset_id"),
    )

    @router.get("/api/enterprise/context")
    def enterprise_context(
        actor: KnowledgeActor = Depends(require_read),
    ) -> dict[str, Any]:
        try:
            return get_enterprise_context(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                account_id=actor.account_id,
                actor_role=actor.role,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc

    @router.get("/api/enterprise/members")
    def enterprise_members(
        actor: KnowledgeActor = Depends(require_audit),
        q: MemberQuery = None,
        role: MemberRole = None,
        before_id: BeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_enterprise_members(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                query=q,
                role=role,
                before_id=before_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc

    @router.patch("/api/enterprise/members/{account_id}/role")
    def update_member_role(
        account_id: AccountId,
        body: TenantMemberRoleRequest,
        actor: KnowledgeActor = Depends(require_manage),
    ) -> dict[str, Any]:
        try:
            mutation_engine = mutation_engine_provider()
            policy = resolve_active_approval_policy(
                mutation_engine,
                tenant_id=actor.tenant_id,
                action_type="member_role_change",
                resource_type="tenant_member",
                resource_id=account_id,
            )
            if policy is not None:
                raise EnterpriseMemberApprovalRequired(policy)
            return change_tenant_member_role(
                mutation_engine,
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                target_account_id=account_id,
                expected_revision=body.expected_revision,
                role=body.role,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_exception(exc) from exc

    @router.post("/api/enterprise/members/{account_id}/suspend")
    def suspend_member(
        account_id: AccountId,
        body: TenantMemberStateRequest,
        actor: KnowledgeActor = Depends(require_manage),
    ) -> dict[str, Any]:
        try:
            return suspend_tenant_member(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                target_account_id=account_id,
                expected_revision=body.expected_revision,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_exception(exc) from exc

    @router.post("/api/enterprise/members/{account_id}/resume")
    def resume_member(
        account_id: AccountId,
        body: TenantMemberStateRequest,
        actor: KnowledgeActor = Depends(require_manage),
    ) -> dict[str, Any]:
        try:
            return resume_tenant_member(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                target_account_id=account_id,
                expected_revision=body.expected_revision,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_exception(exc) from exc

    @router.get("/api/enterprise/audit-events")
    def enterprise_audit_events(
        actor: KnowledgeActor = Depends(require_audit),
        actor_filter: str | None = Query(default=None, alias="actor", min_length=1, max_length=64),
        actor_id: str | None = Query(default=None, min_length=1, max_length=64),
        action: str | None = Query(default=None, min_length=1, max_length=128),
        resource: str | None = Query(default=None, min_length=1, max_length=64),
        resource_type: str | None = Query(default=None, min_length=1, max_length=64),
        target: str | None = Query(default=None, min_length=1, max_length=64),
        target_account_id: str | None = Query(default=None, min_length=1, max_length=64),
        request: str | None = Query(default=None, alias="request", min_length=1, max_length=128),
        request_id: str | None = Query(default=None, min_length=1, max_length=128),
        before_sequence: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            payload = list_tenant_audit_events(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=_coalesce_filter(actor_filter, actor_id, field="actor"),
                action=action,
                resource_type=_coalesce_filter(resource, resource_type, field="resource"),
                target_account_id=_coalesce_filter(target, target_account_id, field="target"),
                request_id=_coalesce_filter(request, request_id, field="request"),
                before_sequence=before_sequence,
                limit=limit,
            )
            payload["authorization"] = {
                "mode": "tenant_role_admin",
                "permission": "knowledge.audit",
                "actor_role": actor.role,
            }
            return payload
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_exception(exc) from exc

    @router.get("/api/knowledge-bases/{dataset_id}/access-summary")
    def dataset_access_summary(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(require_dataset_read),
    ) -> dict[str, Any]:
        try:
            return get_dataset_access_summary(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_role=actor.role,
                account_id=actor.account_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc

    return router


__all__ = ["build_enterprise_admin_router"]
