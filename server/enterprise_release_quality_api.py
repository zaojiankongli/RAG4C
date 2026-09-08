"""Strict authenticated HTTP API for Stage 20 Release quality authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
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
PolicyId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
BaselineId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ReleaseId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ChannelId = Annotated[str, Query(min_length=1, max_length=128, pattern=_SAFE_ID)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]

ScopeType = Literal["global", "risk_tier", "channel"]
RiskTier = Literal["low", "medium", "high"]
PolicyStatus = Literal["active", "disabled"]

_UNAVAILABLE_CODE = "enterprise_release_quality_unavailable"
_UNAVAILABLE_MESSAGE = "Enterprise Release Quality 服务暂不可用"
_INVALID_RESULT_MESSAGE = "Enterprise Release Quality 返回无效"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QualityPolicyCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    scope_type: ScopeType
    scope_value: str = Field(min_length=1, max_length=128)
    channel_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_SAFE_ID)
    min_experiment_count: int = Field(ge=1, strict=True)
    min_judged_result_count: int = Field(ge=0, strict=True)
    min_judgment_coverage_bps: int = Field(ge=0, le=10_000, strict=True)
    min_exact_agreement_bps: int = Field(ge=0, le=10_000, strict=True)
    min_mean_score_milli: int = Field(ge=0, le=3_000, strict=True)
    max_conflicting_results: int = Field(ge=0, strict=True)
    require_all_experiments_completed: bool = Field(strict=True)
    require_no_degraded_results: bool = Field(strict=True)
    max_certification_age_minutes: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "QualityPolicyCreateRequest":
        if self.scope_type == "global":
            if self.scope_value != "*" or self.channel_id is not None:
                raise ValueError("global policy scope must use scope_value='*' without channel_id")
        elif self.scope_type == "risk_tier":
            if self.scope_value not in {"low", "medium", "high"} or self.channel_id is not None:
                raise ValueError(
                    "risk_tier policy scope must use low/medium/high without channel_id"
                )
        elif self.channel_id is None or self.scope_value != self.channel_id:
            raise ValueError("channel policy scope_value must equal channel_id")
        return self


class QualityPolicyPatchRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    min_experiment_count: int | None = Field(default=None, ge=1, strict=True)
    min_judged_result_count: int | None = Field(default=None, ge=0, strict=True)
    min_judgment_coverage_bps: int | None = Field(default=None, ge=0, le=10_000, strict=True)
    min_exact_agreement_bps: int | None = Field(default=None, ge=0, le=10_000, strict=True)
    min_mean_score_milli: int | None = Field(default=None, ge=0, le=3_000, strict=True)
    max_conflicting_results: int | None = Field(default=None, ge=0, strict=True)
    require_all_experiments_completed: bool | None = Field(default=None, strict=True)
    require_no_degraded_results: bool | None = Field(default=None, strict=True)
    max_certification_age_minutes: int | None = Field(default=None, ge=1, strict=True)
    status: PolicyStatus | None = None
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "QualityPolicyPatchRequest":
        if not any(
            value is not None
            for value in (
                self.name,
                self.min_experiment_count,
                self.min_judged_result_count,
                self.min_judgment_coverage_bps,
                self.min_exact_agreement_bps,
                self.min_mean_score_milli,
                self.max_conflicting_results,
                self.require_all_experiments_completed,
                self.require_no_degraded_results,
                self.max_certification_age_minutes,
                self.status,
            )
        ):
            raise ValueError("quality policy patch must include at least one changed field")
        return self


class QualityPolicyCollectionPatchRequest(QualityPolicyPatchRequest):
    policy_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)


class QualityBaselineCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    experiment_ids: list[Annotated[str, Field(min_length=1, max_length=64, pattern=_SAFE_ID)]] = (
        Field(min_length=1, max_length=256)
    )
    parent_baseline_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_SAFE_ID
    )
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_unique_experiments(self) -> "QualityBaselineCreateRequest":
        if len(set(self.experiment_ids)) != len(self.experiment_ids):
            raise ValueError("experiment_ids must be unique")
        return self


class ReleaseQualityCertificationRequest(_StrictModel):
    channel_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    baseline_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    policy_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    expected_policy_revision: int = Field(ge=1, strict=True)
    expected_channel_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ReleaseQualityWaiverRequest(_StrictModel):
    channel_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    policy_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    expected_policy_revision: int = Field(ge=1, strict=True)
    expected_channel_revision: int = Field(ge=1, strict=True)
    approval_policy_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID)
    requested_expires_at: datetime
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_timezone_aware_expiry(self) -> "ReleaseQualityWaiverRequest":
        if (
            self.requested_expires_at.tzinfo is None
            or self.requested_expires_at.utcoffset() is None
        ):
            raise ValueError("requested_expires_at must be timezone-aware")
        return self


# Compatibility aliases keep the API model vocabulary discoverable to callers that use
# the database nouns rather than the route nouns.
QualityGatePolicyCreateRequest = QualityPolicyCreateRequest
QualityGatePolicyPatchRequest = QualityPolicyPatchRequest
ReleaseQualityCertificationCreateRequest = ReleaseQualityCertificationRequest


def _default_service() -> Any:
    try:
        return importlib.import_module("core.enterprise_release_quality")
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": _UNAVAILABLE_CODE, "message": _UNAVAILABLE_MESSAGE},
        ) from exc


def _unavailable(message: str = _UNAVAILABLE_MESSAGE) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": _UNAVAILABLE_CODE, "message": message},
    )


def _result(value: Any) -> tuple[dict[str, Any], int, bool]:
    if isinstance(value, Mapping):
        return dict(value), 200, False
    body = getattr(value, "body", None)
    response_status = getattr(value, "status", 200)
    if (
        not isinstance(body, Mapping)
        or type(response_status) is not int
        or not 100 <= response_status <= 599
    ):
        raise _unavailable(_INVALID_RESULT_MESSAGE)
    return dict(body), response_status, True


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
        and 0 < len(message) <= 512
        and not any(char in message for char in "\r\n\x00")
    ):
        return HTTPException(
            status_code=response_status,
            detail={"code": code, "message": message},
        )
    return _unavailable()


def build_enterprise_release_quality_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the authenticated Stage 20 quality authority router.

    Read routes use the read-only catalog engine and ``knowledge.read``. Mutation
    routes use the writable catalog engine and ``knowledge.manage``. The service
    is injectable for isolated contract tests; production imports the persistence
    service lazily so an unavailable 0030 capability fails closed as HTTP 503.
    """

    quality = service or _default_service()
    read_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    manage_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_MANAGE)
    dataset_read_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_READ, resolve_path_dataset("dataset_id")
    )
    dataset_manage_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id")
    )
    router = APIRouter(tags=["enterprise-release-quality"])

    def execute_read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        try:
            value = getattr(quality, operation)(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                **kwargs,
            )
            body, response_status, _explicit_status = _result(value)
            return JSONResponse(body, status_code=response_status)
        except HTTPException as exc:
            raise _error(exc) from exc
        except Exception as exc:  # noqa: BLE001
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
            value = getattr(quality, operation)(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=idempotency_key,
                **kwargs,
            )
            body, response_status, explicit_status = _result(value)
            if not explicit_status and response_status == 200:
                response_status = default_status
            return JSONResponse(body, status_code=response_status)
        except HTTPException as exc:
            raise _error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise _error(exc) from exc

    @router.get("/api/enterprise/release-quality/policies")
    def list_quality_policies_route(
        actor: KnowledgeActor = Depends(read_actor_dependency),
        scope_type: ScopeType | None = Query(default=None),
        scope_value: str | None = Query(default=None, min_length=1, max_length=128),
        status: PolicyStatus | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {"cursor": cursor, "limit": limit}
        if scope_type is not None:
            kwargs["scope_type"] = scope_type
        if scope_value is not None:
            kwargs["scope_value"] = scope_value
        if status is not None:
            kwargs["status"] = status
        return execute_read("list_quality_gate_policies", actor, **kwargs)

    @router.post("/api/enterprise/release-quality/policies", status_code=201)
    def create_quality_policy_route(
        body: QualityPolicyCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "create_quality_gate_policy",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/release-quality/policies/{policy_id}")
    def update_quality_policy_route(
        policy_id: PolicyId,
        body: QualityPolicyPatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "update_quality_gate_policy",
            actor,
            idempotency_key=idempotency_key,
            policy_id=policy_id,
            **body.model_dump(exclude_none=True),
        )

    @router.patch("/api/enterprise/release-quality/policies")
    def update_quality_policy_collection_route(
        body: QualityPolicyCollectionPatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        data = body.model_dump(exclude_none=True)
        policy_id = data.pop("policy_id")
        return execute_mutation(
            "update_quality_gate_policy",
            actor,
            idempotency_key=idempotency_key,
            policy_id=policy_id,
            **data,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/quality-baselines")
    def list_quality_baselines_route(
        dataset_id: DatasetId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read(
            "list_quality_baselines",
            actor,
            dataset_id=dataset_id,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/quality-baselines", status_code=201)
    def create_quality_baseline_route(
        dataset_id: DatasetId,
        body: QualityBaselineCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "create_quality_baseline",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            dataset_id=dataset_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/quality-baselines/{baseline_id}")
    def get_quality_baseline_route(
        dataset_id: DatasetId,
        baseline_id: BaselineId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read(
            "get_quality_baseline",
            actor,
            dataset_id=dataset_id,
            baseline_id=baseline_id,
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certifications",
        status_code=201,
    )
    def certify_release_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        body: ReleaseQualityCertificationRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "certify_release",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            dataset_id=dataset_id,
            release_id=release_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certifications")
    def list_release_certifications_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        return execute_read(
            "list_release_certifications",
            actor,
            dataset_id=dataset_id,
            release_id=release_id,
            cursor=cursor,
            limit=limit,
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-waivers",
        status_code=202,
    )
    def request_quality_waiver_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        body: ReleaseQualityWaiverRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            "request_quality_waiver",
            actor,
            idempotency_key=idempotency_key,
            default_status=202,
            dataset_id=dataset_id,
            release_id=release_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-gate")
    def get_release_quality_gate_route(
        dataset_id: DatasetId,
        release_id: ReleaseId,
        channel_id: ChannelId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        return execute_read(
            "get_release_quality_gate",
            actor,
            dataset_id=dataset_id,
            release_id=release_id,
            channel_id=channel_id,
        )

    return router


# Explicit alias for callers that prefer the ``api`` suffix used by older modules.
build_enterprise_release_quality_api_router = build_enterprise_release_quality_router


__all__ = [
    "QualityBaselineCreateRequest",
    "QualityGatePolicyCreateRequest",
    "QualityGatePolicyPatchRequest",
    "QualityPolicyCollectionPatchRequest",
    "QualityPolicyCreateRequest",
    "QualityPolicyPatchRequest",
    "ReleaseQualityCertificationCreateRequest",
    "ReleaseQualityCertificationRequest",
    "build_enterprise_release_quality_api_router",
    "build_enterprise_release_quality_router",
]
