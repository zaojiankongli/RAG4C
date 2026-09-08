"""FastAPI routes for the enterprise audit compliance control plane."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from core.enterprise_compliance import (
    ComplianceError,
    create_audit_export,
    create_legal_hold,
    download_audit_export,
    execute_retention,
    get_audit_export,
    get_retention_policy,
    list_audit_exports,
    list_legal_holds,
    preview_retention,
    release_legal_hold,
    update_retention_policy,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
)
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
ExportRootProvider = Callable[[], Path]
NowProvider = Callable[[], datetime]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyUpdateRequest(_StrictModel):
    audit_retention_days: int
    export_retention_days: int
    status: Literal["active", "paused"]
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


class RetentionPreviewRequest(_StrictModel):
    pass


class RetentionExecuteRequest(_StrictModel):
    revision: int = Field(ge=1)
    preview_fingerprint: str = Field(min_length=64, max_length=64)
    confirmation: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=512)


class LegalHoldCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)
    sequence_from: int | None = Field(default=None, ge=1)
    sequence_to: int | None = Field(default=None, ge=1)
    time_from: datetime | None = None
    time_to: datetime | None = None


class RevisionReasonRequest(_StrictModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


class AuditExportCreateRequest(_StrictModel):
    format: Literal["ndjson", "csv"]
    filters: dict[str, Any] = Field(default_factory=dict)
    sequence_from: int | None = Field(default=None, ge=1)
    sequence_to: int | None = Field(default=None, ge=1)
    time_from: datetime | None = None
    time_to: datetime | None = None
    expires_in_days: int = Field(ge=1, le=365)
    reason: str = Field(min_length=1, max_length=512)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ComplianceError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        )
    if isinstance(exc, TenantMutationIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "compliance_idempotency_conflict",
                "message": "Idempotency-Key 已用于不同请求",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "compliance_idempotency_in_progress",
                "message": "同一合规操作正在处理中",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": "compliance_request_invalid", "message": str(exc)},
        )
    return HTTPException(
        status_code=503,
        detail={"code": "compliance_unavailable", "message": "审计合规服务暂不可用"},
    )


def build_enterprise_compliance_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    export_root_provider: ExportRootProvider | None = None,
    now_provider: NowProvider | None = None,
) -> APIRouter:
    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    export_root = export_root_provider or (lambda: Path("data/audit-exports"))
    clock = now_provider or datetime.utcnow
    require_actor = require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["enterprise-compliance"])

    @router.get("/api/enterprise/compliance/retention-policy")
    def retention_policy(actor: KnowledgeActor = Depends(require_actor)) -> dict[str, Any]:
        try:
            return get_retention_policy(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.put("/api/enterprise/compliance/retention-policy")
    def put_retention_policy(
        body: PolicyUpdateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> Response:
        try:
            result = update_retention_policy(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                audit_retention_days=body.audit_retention_days,
                export_retention_days=body.export_retention_days,
                status=body.status,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(result.body, status_code=result.status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/compliance/retention/preview")
    def retention_preview(
        _body: RetentionPreviewRequest,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> dict[str, Any]:
        try:
            return preview_retention(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                now=clock(),
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/compliance/retention/execute")
    def retention_execute(
        body: RetentionExecuteRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> dict[str, Any]:
        try:
            return execute_retention(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                expected_revision=body.revision,
                preview_fingerprint=body.preview_fingerprint,
                confirmation=body.confirmation,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/compliance/legal-holds")
    def legal_holds(actor: KnowledgeActor = Depends(require_actor)) -> dict[str, Any]:
        try:
            return list_legal_holds(
                read_engine_provider(), tenant_id=actor.tenant_id, actor_id=actor.account_id
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/compliance/legal-holds", status_code=201)
    def post_legal_hold(
        body: LegalHoldCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> Response:
        try:
            result = create_legal_hold(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                name=body.name,
                reason=body.reason,
                sequence_from=body.sequence_from,
                sequence_to=body.sequence_to,
                time_from=body.time_from,
                time_to=body.time_to,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(result.body, status_code=result.status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/compliance/legal-holds/{hold_id}/release")
    def release_hold(
        hold_id: str,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> dict[str, Any]:
        try:
            return release_legal_hold(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                hold_id=hold_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/compliance/audit-exports")
    def audit_exports(actor: KnowledgeActor = Depends(require_actor)) -> dict[str, Any]:
        try:
            return list_audit_exports(
                read_engine_provider(), tenant_id=actor.tenant_id, actor_id=actor.account_id
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/compliance/audit-exports", status_code=201)
    def post_audit_export(
        body: AuditExportCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> Response:
        try:
            result = create_audit_export(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                export_root=Path(export_root()),
                format_name=body.format,
                filters=body.filters,
                sequence_from=body.sequence_from,
                sequence_to=body.sequence_to,
                time_from=body.time_from,
                time_to=body.time_to,
                expires_in_days=body.expires_in_days,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            from fastapi.responses import JSONResponse

            return JSONResponse(result.body, status_code=result.status)
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/compliance/audit-exports/{export_id}")
    def audit_export(
        export_id: str,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> dict[str, Any]:
        try:
            return get_audit_export(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                export_id=export_id,
            ).body
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/compliance/audit-exports/{export_id}/download")
    def download_export(
        export_id: str,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> Response:
        try:
            result = download_audit_export(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                export_id=export_id,
                export_root=Path(export_root()),
                now=clock(),
            )
            return Response(
                content=result.content,
                media_type=result.media_type,
                headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    return router


__all__ = ["build_enterprise_compliance_router"]
