"""Authenticated read-only enterprise access-graph API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from core.enterprise_access_graph import (
    EnterpriseAccessGraphMigrationRequired,
    EnterpriseAccessGraphResourceNotFound,
    list_dataset_access_grants,
    list_group_members,
    list_groups,
    list_invitations,
    list_organization_units,
)
from core.catalog_schema import CatalogSchemaError
from core.enterprise_access_mutations import (
    EnterpriseAccessApprovalRequired,
    EnterpriseAccessControlRevisionConflict,
    EnterpriseAccessGrantForbidden,
    EnterpriseAccessGrantMigrationRequired,
    EnterpriseAccessGrantNotFound,
    EnterpriseAccessGrantRevisionConflict,
    EnterpriseAccessGrantStateConflict,
    EnterpriseAccessGrantUnavailable,
    EnterpriseAccessGrantValidationError,
    change_dataset_access_grant_role,
    create_dataset_access_grant,
    disable_dataset_acl,
    resume_dataset_access_grant,
    revoke_dataset_access_grant,
)
from core.enterprise_approval_control import (
    ApprovalError,
    resolve_active_approval_policy,
)
from core.enterprise_acl_idempotency import (
    EnterpriseAclIdempotencyConflict,
    EnterpriseAclIdempotencyInProgress,
    EnterpriseAclIdempotencyValidationError,
)
from core.enterprise_invitation_mutations import (
    TenantInvitationConflict,
    TenantInvitationForbidden,
    TenantInvitationMigrationRequired,
    TenantInvitationNotFound,
    TenantInvitationRevisionConflict,
    TenantInvitationUnavailable,
    TenantInvitationValidationError,
    accept_invitation,
    create_invitation,
    resend_invitation,
    revoke_invitation,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
)
from core.enterprise_identity_control import (
    IdentityConflict,
    IdentityControlUnavailable,
    IdentityForbidden,
    IdentityMigrationRequired,
    IdentityNotFound,
    IdentityRevisionConflict,
    IdentityValidationError,
    activate_provider,
    create_domain,
    create_provider,
    disable_provider,
    issue_scim_token,
    list_domains,
    list_providers,
    list_scim_tokens,
    revoke_domain,
    revoke_scim_token,
    update_provider,
    verify_domain,
)
from core.enterprise_identity_dns import (
    DnspythonIdentityResolver,
    IdentityDnsResolverUnavailable,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import (
    InvitationAcceptActor,
    KnowledgeActor,
    require_invitation_accept_identity,
    require_knowledge_permission,
    resolve_path_dataset,
)

EngineProvider = Callable[[], Any]
_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
ResourceId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
BeforeId = Annotated[str | None, Query(min_length=1, max_length=64, pattern=_SAFE_ID)]
MemberBeforeId = Annotated[int | None, Query(ge=1)]
PageLimit = Annotated[int, Query(ge=1, le=200)]
OrganizationStatus = Literal["active", "archived"]
GroupStatus = Literal["active", "archived"]
InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]
InvitationMemberRole = Literal["owner", "admin", "editor", "member"]
GrantSubjectType = Literal["account", "group", "organization_unit"]
GrantRole = Literal["viewer", "editor", "manager"]
GrantStatus = Literal["active", "revoked"]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetAccessGrantCreateRequest(_StrictModel):
    subject_type: GrantSubjectType
    subject_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    role: GrantRole
    reason: str = Field(min_length=1, max_length=512)


class DatasetAccessGrantRoleRequest(_StrictModel):
    # ``revision`` is the public API contract used by the enterprise web
    # client.  Keep ``expected_revision`` as an input-only compatibility alias
    # for callers that use the older member-mutation naming convention.
    revision: int = Field(
        ge=1,
        strict=True,
        validation_alias=AliasChoices("revision", "expected_revision"),
    )
    role: GrantRole
    reason: str = Field(min_length=1, max_length=512)

    @property
    def expected_revision(self) -> int:
        return self.revision


class DatasetAccessGrantStateRequest(_StrictModel):
    revision: int = Field(
        ge=1,
        strict=True,
        validation_alias=AliasChoices("revision", "expected_revision"),
    )
    reason: str = Field(min_length=1, max_length=512)

    @property
    def expected_revision(self) -> int:
        return self.revision


class DatasetAccessGrantResumeRequest(DatasetAccessGrantStateRequest):
    role: GrantRole | None = None


class DatasetAccessControlDisableRequest(_StrictModel):
    expected_acl_revision: int = Field(
        ge=1,
        strict=True,
        validation_alias=AliasChoices("expected_acl_revision", "acl_revision", "revision"),
    )
    reason: str = Field(min_length=1, max_length=512)


class TenantInvitationCreateRequest(_StrictModel):
    email: str = Field(min_length=3, max_length=256)
    role: InvitationMemberRole
    expires_in_days: int = Field(ge=1, le=30, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class TenantInvitationResendRequest(_StrictModel):
    revision: int = Field(
        ge=1,
        strict=True,
        validation_alias=AliasChoices("revision", "expected_revision"),
    )
    expires_in_days: int = Field(ge=1, le=30, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class TenantInvitationRevokeRequest(_StrictModel):
    revision: int = Field(
        ge=1,
        strict=True,
        validation_alias=AliasChoices("revision", "expected_revision"),
    )
    reason: str = Field(min_length=1, max_length=512)


class TenantInvitationAcceptRequest(_StrictModel):
    invite_token: str = Field(min_length=32, max_length=256)


class IdentityDomainCreateRequest(_StrictModel):
    domain: str = Field(min_length=1, max_length=253)
    reason: str = Field(min_length=1, max_length=512)


class IdentityRevisionRequest(_StrictModel):
    revision: int = Field(
        ge=1, strict=True, validation_alias=AliasChoices("revision", "expected_revision")
    )
    reason: str = Field(min_length=1, max_length=512)


class IdentityProviderCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    provider_type: Literal["oidc", "saml"]
    domain_id: str = Field(min_length=1, max_length=64)
    issuer_url: str | None = Field(default=None, max_length=1024)
    client_id: str | None = Field(default=None, max_length=256)
    secret_ref: str | None = Field(default=None, max_length=512)
    scopes: list[str] = Field(default_factory=list, max_length=32)
    entity_id: str | None = Field(default=None, max_length=512)
    sso_url: str | None = Field(default=None, max_length=1024)
    metadata_url: str | None = Field(default=None, max_length=1024)
    certificate_fingerprint: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class IdentityProviderUpdateRequest(_StrictModel):
    revision: int = Field(
        ge=1, strict=True, validation_alias=AliasChoices("revision", "expected_revision")
    )
    issuer_url: str | None = Field(default=None, max_length=1024)
    client_id: str | None = Field(default=None, max_length=256)
    secret_ref: str | None = Field(default=None, max_length=512)
    scopes: list[str] | None = Field(default=None, max_length=32)
    entity_id: str | None = Field(default=None, max_length=512)
    sso_url: str | None = Field(default=None, max_length=1024)
    metadata_url: str | None = Field(default=None, max_length=1024)
    certificate_fingerprint: str | None = Field(default=None, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class IdentityScimIssueRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=16)
    expires_in_days: int = Field(ge=1, le=365, strict=True)
    reason: str = Field(min_length=1, max_length=512)


def _migration_required(_exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "enterprise_access_graph_migration_required",
            "message": "企业访问图谱需要先完成 0019_dataset_acl_control 数据库迁移",
        },
    )


def _unavailable(_exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "enterprise_access_graph_unavailable",
            "message": "企业访问图谱服务暂不可用",
        },
    )


def _resource_not_found(_exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
    )


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EnterpriseAccessGraphMigrationRequired):
        return _migration_required(exc)
    if isinstance(exc, EnterpriseAccessGraphResourceNotFound):
        return _resource_not_found(exc)
    return _unavailable(exc)


def _mutation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApprovalError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, CatalogSchemaError):
        return _migration_required(exc)
    if isinstance(exc, EnterpriseAccessGrantMigrationRequired):
        return _migration_required(exc)
    if isinstance(exc, EnterpriseAccessGrantForbidden):
        return HTTPException(
            status_code=403,
            detail={
                "code": "dataset_access_grant_forbidden",
                "message": "当前身份无权管理该知识库授权",
            },
        )
    if isinstance(exc, EnterpriseAccessGrantNotFound):
        return _resource_not_found(exc)
    if isinstance(exc, EnterpriseAccessApprovalRequired):
        policy = exc.policy
        return HTTPException(
            status_code=409,
            detail={
                "code": "dataset_acl_approval_required",
                "message": "该知识库停用 ACL 需要先完成审批",
                "policy_id": policy.get("id"),
                "policy_name": policy.get("name"),
                "policy_revision": policy.get("revision"),
                "required_approvals": policy.get("required_approvals"),
                "request_expiry_minutes": policy.get("request_expiry_minutes"),
                "policy": {
                    "id": policy.get("id"),
                    "name": policy.get("name"),
                    "revision": policy.get("revision"),
                    "required_approvals": policy.get("required_approvals"),
                    "request_expiry_minutes": policy.get("request_expiry_minutes"),
                    "resource_scope": policy.get("resource_scope"),
                },
            },
        )
    if isinstance(exc, EnterpriseAccessGrantRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "dataset_access_grant_revision_conflict",
                "message": "知识库授权已发生变化，请刷新后重试",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        )
    if isinstance(exc, EnterpriseAccessControlRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "dataset_access_control_revision_conflict",
                "message": "知识库 ACL 模式已发生变化，请刷新后重试",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        )
    if isinstance(exc, EnterpriseAccessGrantStateConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, EnterpriseAccessGrantValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "dataset_access_grant_invalid", "message": str(exc)},
        )
    if isinstance(exc, EnterpriseAclIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "dataset_acl_idempotency_conflict",
                "message": "幂等键已用于不同的请求",
            },
        )
    if isinstance(exc, EnterpriseAclIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "dataset_acl_idempotency_in_progress",
                "message": "相同幂等请求正在处理中，请稍后重试",
            },
        )
    if isinstance(exc, EnterpriseAclIdempotencyValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "dataset_acl_idempotency_invalid", "message": str(exc)},
        )
    if isinstance(exc, EnterpriseAccessGrantUnavailable):
        return _unavailable(exc)
    return _unavailable(exc)


def _invitation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (CatalogSchemaError, TenantInvitationMigrationRequired)):
        return HTTPException(
            status_code=503,
            detail={
                "code": "tenant_invitation_migration_required",
                "message": (
                    "企业邀请生命周期需要先完成 0020_tenant_invitation_lifecycle 数据库迁移"
                ),
            },
        )
    if isinstance(exc, TenantInvitationForbidden):
        return HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, TenantInvitationNotFound):
        return HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, TenantInvitationRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        )
    if isinstance(exc, TenantInvitationConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, TenantMutationIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "tenant_control_idempotency_conflict",
                "message": "幂等键已用于不同的租户控制请求",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "tenant_control_idempotency_in_progress",
                "message": "相同租户控制请求正在处理中",
            },
        )
    if isinstance(exc, (TenantInvitationValidationError, TenantMutationIdempotencyValidationError)):
        return HTTPException(
            status_code=422,
            detail={"code": "tenant_invitation_invalid", "message": str(exc)},
        )
    if isinstance(exc, TenantInvitationUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "code": "tenant_invitation_unavailable",
                "message": "企业邀请服务暂不可用",
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "tenant_invitation_unavailable",
            "message": "企业邀请服务暂不可用",
        },
    )


def _identity_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (CatalogSchemaError, IdentityMigrationRequired)):
        return HTTPException(
            status_code=503,
            detail={
                "code": "identity_federation_migration_required",
                "message": (
                    "企业身份联邦需要先完成 0021_enterprise_identity_federation 数据库迁移"
                ),
            },
        )
    if isinstance(exc, IdentityDnsResolverUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "code": "identity_dns_resolver_unavailable",
                "message": "身份联邦 DNS resolver 暂不可用",
            },
        )
    if isinstance(exc, IdentityForbidden):
        return HTTPException(status_code=403, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, IdentityNotFound):
        return HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, IdentityRevisionConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            },
        )
    if isinstance(exc, IdentityConflict):
        return HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, TenantMutationIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "tenant_control_idempotency_conflict",
                "message": "幂等键已用于不同的租户控制请求",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "tenant_control_idempotency_in_progress",
                "message": "相同租户控制请求正在处理中",
            },
        )
    if isinstance(exc, (IdentityValidationError, TenantMutationIdempotencyValidationError)):
        code = exc.code if isinstance(exc, IdentityValidationError) else "identity_request_invalid"
        return HTTPException(status_code=422, detail={"code": code, "message": str(exc)})
    if isinstance(exc, IdentityControlUnavailable):
        return HTTPException(
            status_code=503,
            detail={
                "code": "identity_control_unavailable",
                "message": "企业身份控制面暂不可用",
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "identity_control_unavailable",
            "message": "企业身份控制面暂不可用",
        },
    )


def build_enterprise_access_graph_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    identity_resolver_provider: EngineProvider | None = None,
) -> APIRouter:
    """Build the tenant-isolated access-graph router with an explicit read engine."""

    if not callable(read_engine_provider):
        raise TypeError("read_engine_provider must be callable")
    if not callable(mutation_engine_provider):
        raise TypeError("mutation_engine_provider must be callable")
    if identity_resolver_provider is not None and not callable(identity_resolver_provider):
        raise TypeError("identity_resolver_provider must be callable")

    def identity_resolver() -> Any:
        if identity_resolver_provider is not None:
            return identity_resolver_provider()
        return DnspythonIdentityResolver()

    def writable_engine() -> Any:
        return mutation_engine_provider()

    router = APIRouter(tags=["enterprise-access-graph"])
    require_manage = require_knowledge_permission(KNOWLEDGE_MANAGE)
    require_invitation_member = require_knowledge_permission(KNOWLEDGE_READ)
    require_dataset_manage = require_knowledge_permission(
        KNOWLEDGE_MANAGE,
        resolve_path_dataset("dataset_id"),
    )

    @router.get("/api/enterprise/identity/domains")
    def identity_domains(
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return list_domains(read_engine_provider(), tenant_id=actor.tenant_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/domains", status_code=201)
    def create_identity_domain(
        body: IdentityDomainCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return create_domain(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                domain=body.domain,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/domains/{domain_id}/verify")
    def verify_identity_domain(
        domain_id: ResourceId,
        body: IdentityRevisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return verify_domain(
                writable_engine(),
                resolver=identity_resolver(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                domain_id=domain_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/domains/{domain_id}/revoke")
    def revoke_identity_domain(
        domain_id: ResourceId,
        body: IdentityRevisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return revoke_domain(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                domain_id=domain_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.get("/api/enterprise/identity/providers")
    def identity_providers(
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return list_providers(read_engine_provider(), tenant_id=actor.tenant_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/providers", status_code=201)
    def create_identity_provider(
        body: IdentityProviderCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        configuration = body.model_dump(
            include={
                "issuer_url",
                "client_id",
                "secret_ref",
                "scopes",
                "entity_id",
                "sso_url",
                "metadata_url",
                "certificate_fingerprint",
            }
        )
        try:
            return create_provider(
                writable_engine(),
                resolver=identity_resolver(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                name=body.name,
                provider_type=body.provider_type,
                trusted_domain_id=body.domain_id,
                configuration=configuration,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.patch("/api/enterprise/identity/providers/{provider_id}")
    def update_identity_provider(
        provider_id: ResourceId,
        body: IdentityProviderUpdateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        configuration = body.model_dump(
            exclude_none=True,
            include={
                "issuer_url",
                "client_id",
                "secret_ref",
                "scopes",
                "entity_id",
                "sso_url",
                "metadata_url",
                "certificate_fingerprint",
            },
        )
        try:
            return update_provider(
                writable_engine(),
                resolver=identity_resolver(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                provider_id=provider_id,
                expected_revision=body.revision,
                configuration=configuration,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/providers/{provider_id}/activate")
    def activate_identity_provider(
        provider_id: ResourceId,
        body: IdentityRevisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return activate_provider(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                provider_id=provider_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/providers/{provider_id}/disable")
    def disable_identity_provider(
        provider_id: ResourceId,
        body: IdentityRevisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return disable_provider(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                provider_id=provider_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.get("/api/enterprise/identity/scim-tokens")
    def identity_scim_tokens(
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return list_scim_tokens(read_engine_provider(), tenant_id=actor.tenant_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/scim-tokens", status_code=201)
    def issue_identity_scim_token(
        body: IdentityScimIssueRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return issue_scim_token(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                name=body.name,
                scopes=body.scopes,
                expires_in_days=body.expires_in_days,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.post("/api/enterprise/identity/scim-tokens/{token_id}/revoke")
    def revoke_identity_scim_token(
        token_id: ResourceId,
        body: IdentityRevisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return revoke_scim_token(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                token_id=token_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _identity_error(exc) from exc

    @router.get("/api/enterprise/organization-units")
    def organization_units(
        actor: KnowledgeActor = Depends(require_manage),
        parent_id: str | None = Query(default=None, min_length=1, max_length=64),
        status: OrganizationStatus | None = Query(default=None),
        before_id: BeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_organization_units(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                parent_id=parent_id,
                status=status,
                before_id=before_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    @router.get("/api/enterprise/groups")
    def groups(
        actor: KnowledgeActor = Depends(require_manage),
        q: str | None = Query(default=None, min_length=1, max_length=256),
        status: GroupStatus | None = Query(default=None),
        before_id: BeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_groups(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                query=q,
                status=status,
                before_id=before_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    @router.get("/api/enterprise/groups/{group_id}/members")
    def group_members(
        group_id: ResourceId,
        actor: KnowledgeActor = Depends(require_manage),
        before_id: MemberBeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_group_members(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                group_id=group_id,
                before_id=str(before_id) if before_id is not None else None,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    @router.get("/api/enterprise/invitations")
    def invitations(
        actor: KnowledgeActor = Depends(require_manage),
        status: InvitationStatus | None = Query(default=None),
        before_id: BeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_invitations(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                status=status,
                before_id=before_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    @router.post("/api/enterprise/invitations", status_code=201)
    def create_tenant_invitation(
        body: TenantInvitationCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return create_invitation(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                email=body.email,
                role=body.role,
                expires_in_days=body.expires_in_days,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _invitation_error(exc) from exc

    @router.post("/api/enterprise/invitations/{invitation_id}/resend")
    def resend_tenant_invitation(
        invitation_id: ResourceId,
        body: TenantInvitationResendRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return resend_invitation(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                invitation_id=invitation_id,
                expected_revision=body.revision,
                expires_in_days=body.expires_in_days,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _invitation_error(exc) from exc

    @router.post("/api/enterprise/invitations/{invitation_id}/revoke")
    def revoke_tenant_invitation(
        invitation_id: ResourceId,
        body: TenantInvitationRevokeRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_invitation_member),
    ) -> dict[str, Any]:
        try:
            return revoke_invitation(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                invitation_id=invitation_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _invitation_error(exc) from exc

    @router.post("/api/enterprise/invitations/accept")
    def accept_tenant_invitation(
        body: TenantInvitationAcceptRequest,
        idempotency_key: IdempotencyKey,
        actor: InvitationAcceptActor = Depends(require_invitation_accept_identity()),
    ) -> dict[str, Any]:
        try:
            return accept_invitation(
                writable_engine(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                actor_email=actor.normalized_email,
                invite_token=body.invite_token,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _invitation_error(exc) from exc

    @router.get("/api/knowledge-bases/{dataset_id}/access-grants")
    def access_grants(
        dataset_id: ResourceId,
        actor: KnowledgeActor = Depends(require_dataset_manage),
        subject_type: GrantSubjectType | None = Query(default=None),
        role: GrantRole | None = Query(default=None),
        status: GrantStatus | None = Query(default=None),
        before_id: BeforeId = None,
        limit: PageLimit = 100,
    ) -> dict[str, Any]:
        try:
            return list_dataset_access_grants(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                subject_type=subject_type,
                role=role,
                status=status,
                before_id=before_id,
                limit=limit,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    @router.post("/api/knowledge-bases/{dataset_id}/access-grants", status_code=201)
    def create_access_grant(
        dataset_id: ResourceId,
        body: DatasetAccessGrantCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_dataset_manage),
    ) -> dict[str, Any]:
        try:
            return create_dataset_access_grant(
                writable_engine(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                subject_type=body.subject_type,
                subject_id=body.subject_id,
                role=body.role,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_error(exc) from exc

    @router.patch(
        "/api/knowledge-bases/{dataset_id}/access-grants/{grant_id}/role",
        include_in_schema=False,
    )
    @router.patch("/api/knowledge-bases/{dataset_id}/access-grants/{grant_id}")
    def change_access_grant_role(
        dataset_id: ResourceId,
        grant_id: ResourceId,
        body: DatasetAccessGrantRoleRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_dataset_manage),
    ) -> dict[str, Any]:
        try:
            return change_dataset_access_grant_role(
                writable_engine(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                grant_id=grant_id,
                expected_revision=body.revision,
                role=body.role,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_error(exc) from exc

    @router.post("/api/knowledge-bases/{dataset_id}/access-grants/{grant_id}/revoke")
    def revoke_access_grant(
        dataset_id: ResourceId,
        grant_id: ResourceId,
        body: DatasetAccessGrantStateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_dataset_manage),
    ) -> dict[str, Any]:
        try:
            return revoke_dataset_access_grant(
                writable_engine(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                grant_id=grant_id,
                expected_revision=body.revision,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_error(exc) from exc

    @router.post("/api/knowledge-bases/{dataset_id}/access-grants/{grant_id}/resume")
    def resume_access_grant(
        dataset_id: ResourceId,
        grant_id: ResourceId,
        body: DatasetAccessGrantResumeRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_dataset_manage),
    ) -> dict[str, Any]:
        try:
            return resume_dataset_access_grant(
                writable_engine(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                grant_id=grant_id,
                expected_revision=body.revision,
                role=body.role,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_error(exc) from exc

    @router.post("/api/knowledge-bases/{dataset_id}/access-control/disable")
    def disable_access_control(
        dataset_id: ResourceId,
        body: DatasetAccessControlDisableRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_manage),
    ) -> dict[str, Any]:
        try:
            policy = resolve_active_approval_policy(
                writable_engine(),
                tenant_id=actor.tenant_id,
                action_type="dataset_acl_disable",
                resource_type="knowledge_base",
                resource_id=dataset_id,
            )
            if policy is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "dataset_acl_approval_required",
                        "message": "该知识库停用 ACL 需要先完成审批",
                        "policy_id": policy["id"],
                        "policy_name": policy["name"],
                        "policy_revision": policy["revision"],
                        "required_approvals": policy["required_approvals"],
                        "request_expiry_minutes": policy["request_expiry_minutes"],
                        "policy": {
                            "id": policy["id"],
                            "name": policy["name"],
                            "revision": policy["revision"],
                            "required_approvals": policy["required_approvals"],
                            "request_expiry_minutes": policy["request_expiry_minutes"],
                            "resource_scope": policy["resource_scope"],
                        },
                    },
                )
            return disable_dataset_acl(
                writable_engine(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                actor_id=actor.account_id,
                actor_role=actor.role,
                expected_acl_revision=body.expected_acl_revision,
                reason=body.reason,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _mutation_error(exc) from exc

    return router


__all__ = [
    "DatasetAccessGrantCreateRequest",
    "TenantInvitationAcceptRequest",
    "TenantInvitationCreateRequest",
    "TenantInvitationResendRequest",
    "TenantInvitationRevokeRequest",
    "DatasetAccessGrantRoleRequest",
    "DatasetAccessGrantResumeRequest",
    "DatasetAccessGrantStateRequest",
    "DatasetAccessControlDisableRequest",
    "IdempotencyKey",
    "build_enterprise_access_graph_router",
]
