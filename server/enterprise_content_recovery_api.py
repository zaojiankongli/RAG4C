"""Strict HTTP boundary for Stage 23 Enterprise Content Recovery."""

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
    r"(?i)(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key)"
    r"(?:\s|[:=\"'([{])|(?:[a-z][a-z0-9+.-]{1,31}://)"
)
_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_SERVICE_MODULE = "core.enterprise_content_recovery_service"
_UNAVAILABLE = "enterprise_content_recovery_unavailable"
_INVALID = "enterprise_content_recovery_request_invalid"

EntryId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
DocumentId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
HoldId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
PurgeRequestId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecycleRequest(StrictModel):
    dataset_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_SAFE_ID.pattern
    )
    expected_mutation_generation: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class BulkRecycleItem(StrictModel):
    document_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    expected_mutation_generation: int = Field(ge=0, strict=True)


class BulkRecycleRequest(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID.pattern)
    items: list[BulkRecycleItem] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("items")
    @classmethod
    def unique_documents(cls, value: list[BulkRecycleItem]) -> list[BulkRecycleItem]:
        ids = [item.document_id for item in value]
        if len(set(ids)) != len(ids):
            raise ValueError("document IDs must be unique")
        return value


class RevisionReasonRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class LegalHoldRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    safe_reason: str = Field(min_length=1, max_length=512)


class PurgeRequest(RevisionReasonRequest):
    approval_policy_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
    )


class RetentionPolicyPatch(RevisionReasonRequest):
    retention_days: int = Field(ge=1, le=3650, strict=True)
    auto_purge_enabled: bool = Field(strict=True)
    purge_requires_approval: bool = Field(strict=True)
    status: Literal["active", "paused"]

    @model_validator(mode="after")
    def enterprise_purge_boundary(self) -> "RetentionPolicyPatch":
        if self.auto_purge_enabled and not self.purge_requires_approval:
            raise ValueError("automatic purge requires approval")
        return self


for model in (
    RecycleRequest,
    BulkRecycleRequest,
    RevisionReasonRequest,
    LegalHoldRequest,
    PurgeRequest,
    RetentionPolicyPatch,
):
    model.model_rebuild()


def unavailable(message: str = "Enterprise Content Recovery 服务暂不可用") -> HTTPException:
    return HTTPException(status_code=503, detail={"code": _UNAVAILABLE, "message": message})


def safe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, Mapping):
            code, message = detail.get("code"), detail.get("message")
            if (
                type(exc.status_code) is int
                and 400 <= exc.status_code <= 599
                and isinstance(code, str)
                and _CODE.fullmatch(code)
                and isinstance(message, str)
                and 0 < len(message) <= 512
                and not _CONTROL.search(message)
                and not _UNSAFE.search(message)
            ):
                return HTTPException(
                    status_code=exc.status_code, detail={"code": code, "message": message}
                )
        return unavailable()
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
            status_code=422, detail={"code": _INVALID, "message": "Idempotency-Key is invalid"}
        )
    return normalized


def load_service(injected: ServiceProvider | None) -> Any:
    if injected is not None:
        return injected
    try:
        return importlib.import_module(_SERVICE_MODULE)
    except Exception as exc:  # noqa: BLE001
        raise unavailable() from exc


def service_call(service: Any, operation: str, engine: Any, **kwargs: Any) -> Any:
    try:
        method = getattr(service, operation)
        if not callable(method):
            raise AttributeError(operation)
        return method(engine, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise safe_error(exc) from exc


def response(value: Any) -> JSONResponse:
    if isinstance(value, Mapping):
        body, status = dict(value), 200
    else:
        raw_body, raw_status = getattr(value, "body", None), getattr(value, "status", 200)
        if isinstance(raw_body, Mapping):
            body, status = dict(raw_body), raw_status
        elif is_dataclass(value):
            body, status = asdict(value), 200
        else:
            raise unavailable("Enterprise Content Recovery 返回无效")
    if type(status) is not int or not 100 <= status <= 599 or status == 204:
        raise unavailable("Enterprise Content Recovery 返回无效")
    encoded = jsonable_encoder(body)
    if not isinstance(encoded, Mapping):
        raise unavailable("Enterprise Content Recovery 返回无效")
    return JSONResponse(dict(encoded), status_code=status)


def build_enterprise_content_recovery_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    actor_dep = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["enterprise-content-recovery"])

    def execute_read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        target = load_service(service)
        return response(
            service_call(
                target,
                operation,
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                **kwargs,
            )
        )

    def execute_mutation(
        operation: str,
        actor: KnowledgeActor,
        idempotency_key: str,
        **kwargs: Any,
    ) -> JSONResponse:
        target = load_service(service)
        return response(
            service_call(
                target,
                operation,
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                account_id=actor.account_id,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=clean_key(idempotency_key),
                **kwargs,
            )
        )

    @router.get("/api/enterprise/recovery/summary")
    def summary(actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return execute_read("get_recovery_summary", actor)

    @router.get("/api/enterprise/recycle-bin")
    def list_entries(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: str | None = Query(default=None, max_length=32),
        dataset_id: str | None = Query(default=None, max_length=64),
    ) -> JSONResponse:
        return execute_read(
            "list_recycle_entries",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
            dataset_id=dataset_id,
        )

    @router.get("/api/enterprise/recycle-bin/{entry_id}")
    def detail(entry_id: EntryId, actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return execute_read("get_recycle_entry", actor, entry_id=entry_id)

    @router.get("/api/enterprise/recovery/retention-policy")
    def retention(actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return execute_read("get_retention_policy", actor)

    @router.get("/api/enterprise/recycle-bin/{entry_id}/holds")
    def holds(entry_id: EntryId, actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return execute_read("list_legal_holds", actor, entry_id=entry_id)

    @router.get("/api/enterprise/recycle-bin/{entry_id}/purge-requests")
    def purge_requests(
        entry_id: EntryId, actor: KnowledgeActor = Depends(actor_dep)
    ) -> JSONResponse:
        return execute_read("list_purge_requests", actor, entry_id=entry_id)

    @router.post("/api/enterprise/recycle-bin/documents/bulk")
    def bulk_recycle(
        body: BulkRecycleRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "bulk_recycle_documents",
            actor,
            idempotency_key,
            dataset_id=body.dataset_id,
            items=[item.model_dump() for item in body.items],
            reason=body.reason,
        )

    @router.post("/api/enterprise/recycle-bin/documents/{document_id}")
    def recycle(
        document_id: DocumentId,
        body: RecycleRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "recycle_document",
            actor,
            idempotency_key,
            document_id=document_id,
            **body.model_dump(exclude_none=True),
        )

    @router.post("/api/enterprise/recycle-bin/{entry_id}/restore")
    def restore(
        entry_id: EntryId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "restore_document", actor, idempotency_key, entry_id=entry_id, **body.model_dump()
        )

    @router.post("/api/enterprise/recycle-bin/{entry_id}/holds")
    def apply_hold(
        entry_id: EntryId,
        body: LegalHoldRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "apply_legal_hold",
            actor,
            idempotency_key,
            entry_id=entry_id,
            expected_revision=body.expected_revision,
            reason_code=body.reason_code,
            reason=body.safe_reason,
        )

    @router.post("/api/enterprise/recycle-bin/{entry_id}/holds/{hold_id}/release")
    def release_hold(
        entry_id: EntryId,
        hold_id: HoldId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "release_legal_hold",
            actor,
            idempotency_key,
            entry_id=entry_id,
            hold_id=hold_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/recycle-bin/{entry_id}/purge-requests")
    def request_purge(
        entry_id: EntryId,
        body: PurgeRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "request_document_purge",
            actor,
            idempotency_key,
            entry_id=entry_id,
            **body.model_dump(exclude_none=True),
        )

    @router.post("/api/enterprise/recycle-bin/{entry_id}/purge-requests/{request_id}/cancel")
    def cancel_purge(
        entry_id: EntryId,
        request_id: PurgeRequestId,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "cancel_document_purge_request",
            actor,
            idempotency_key,
            entry_id=entry_id,
            purge_request_id=request_id,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/recovery/retention-policy")
    def update_retention(
        body: RetentionPolicyPatch,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            "update_content_retention_policy", actor, idempotency_key, **body.model_dump()
        )

    return router


__all__ = [
    "BulkRecycleRequest",
    "LegalHoldRequest",
    "PurgeRequest",
    "RecycleRequest",
    "RetentionPolicyPatch",
    "RevisionReasonRequest",
    "build_enterprise_content_recovery_router",
]
