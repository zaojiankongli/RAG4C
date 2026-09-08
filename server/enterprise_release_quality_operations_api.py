"""Strict authenticated HTTP API for Stage 21 Release Quality Operations.

The operations API is intentionally a thin transport boundary. It owns strict
request validation, actor/permission selection, read-versus-mutation engine
separation, safe error projection and service dispatch. Durable authority
lives in the Stage 21 core modules; the Alert and Recertification modules are
loaded lazily so this router can be mounted while those services are deployed
independently.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
import importlib
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ, role_allows
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

EngineProvider = Callable[[], Any]
ServiceProvider = Any

_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SAFE_DATASET_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")

SloPolicyId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
ScheduleId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
RunId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
AlertId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
JobId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID)]
DatasetPathId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]

ScopeType = Literal["global", "risk_tier", "channel"]
RiskTier = Literal["low", "medium", "high"]
SloPolicyStatus = Literal["active", "disabled"]
ScheduleStatus = Literal["active", "paused", "archived"]
ScanRunStatus = Literal["pending", "claimed", "running", "completed", "failed", "cancelled"]
ObservationSeverity = Literal["healthy", "warning", "critical", "unavailable"]
ObservationGateState = Literal[
    "passing", "passed", "waived", "not_required", "blocked", "unavailable"
]
AlertStatus = Literal["open", "acknowledged", "resolved", "suppressed"]
AlertSeverity = Literal["warning", "critical"]
ReleaseRole = Literal["active", "pinned"]
RecertificationTrigger = Literal[
    "manual",
    "certification_warning",
    "certification_expired",
    "stale_evidence",
    "alert_escalation",
]
RecertificationStatus = Literal[
    "pending",
    "claimed",
    "awaiting_evidence",
    "ready_to_certify",
    "completed",
    "failed",
    "cancelled",
]

_UNAVAILABLE_CODE = "enterprise_release_quality_operations_unavailable"
_UNAVAILABLE_MESSAGE = "Enterprise Release Quality Operations 服务暂不可用"
_INVALID_RESULT_MESSAGE = "Enterprise Release Quality Operations 返回无效"
_PERMISSION_CODE = "enterprise_release_quality_operations_forbidden"

_SLO_MODULE = "core.enterprise_release_quality_operation_mutations"
_SCHEDULER_MODULE = "core.enterprise_release_quality_scheduler"
_ALERT_MODULE = "core.enterprise_release_quality_alerts"
_RECERTIFICATION_MODULE = "core.enterprise_release_quality_recertification"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SloPolicyCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    scope_type: ScopeType
    scope_value: str = Field(min_length=1, max_length=128)
    channel_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=_SAFE_ID)
    certification_warning_minutes: int = Field(ge=1, strict=True)
    certification_critical_minutes: int = Field(ge=1, strict=True)
    waiver_warning_minutes: int = Field(ge=1, strict=True)
    max_open_alerts: int = Field(ge=1, strict=True)
    auto_queue_recertification: bool = Field(strict=True)
    require_passing_certification: bool = Field(strict=True)
    allow_active_waiver: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "SloPolicyCreateRequest":
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
        if self.certification_warning_minutes <= self.certification_critical_minutes:
            raise ValueError(
                "certification_warning_minutes must exceed certification_critical_minutes"
            )
        return self


class SloPolicyPatchRequest(_StrictModel):
    policy_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    certification_warning_minutes: int | None = Field(default=None, ge=1, strict=True)
    certification_critical_minutes: int | None = Field(default=None, ge=1, strict=True)
    waiver_warning_minutes: int | None = Field(default=None, ge=1, strict=True)
    max_open_alerts: int | None = Field(default=None, ge=1, strict=True)
    auto_queue_recertification: bool | None = Field(default=None, strict=True)
    require_passing_certification: bool | None = Field(default=None, strict=True)
    allow_active_waiver: bool | None = Field(default=None, strict=True)
    status: SloPolicyStatus | None = None
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_change(self) -> "SloPolicyPatchRequest":
        if not any(
            value is not None
            for value in (
                self.name,
                self.certification_warning_minutes,
                self.certification_critical_minutes,
                self.waiver_warning_minutes,
                self.max_open_alerts,
                self.auto_queue_recertification,
                self.require_passing_certification,
                self.allow_active_waiver,
                self.status,
            )
        ):
            raise ValueError("quality SLO policy patch must include a changed field")
        return self


class ScanScheduleCreateRequest(_StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)
    slo_policy_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    interval_seconds: int = Field(ge=300, le=604_800, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ScanSchedulePatchRequest(_StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)
    schedule_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    expected_revision: int = Field(ge=1, strict=True)
    interval_seconds: int = Field(ge=300, le=604_800, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class ScanRunCancelRequest(_StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID)
    reason: str = Field(default="", max_length=512)


class AlertActionRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    comment: str = Field(default="", max_length=512)


class AlertSuppressRequest(AlertActionRequest):
    suppressed_until: datetime

    @field_validator("suppressed_until", mode="before")
    @classmethod
    def validate_suppressed_until(cls, value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("suppressed_until must be timezone-aware")
            return value
        if not isinstance(value, str):
            raise ValueError("suppressed_until must be an ISO-8601 timestamp")
        raw = value.strip()
        if not raw:
            raise ValueError("suppressed_until must be an ISO-8601 timestamp")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("suppressed_until must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("suppressed_until must be timezone-aware")
        return parsed


class RecertificationJobCreateRequest(_StrictModel):
    release_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    channel_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    release_role: ReleaseRole
    baseline_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    policy_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    expected_policy_revision: int = Field(ge=1, strict=True)
    slo_policy_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID)
    expected_slo_policy_revision: int = Field(ge=1, strict=True)
    trigger: RecertificationTrigger
    expected_manifest_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_evidence_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    expected_channel_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)


class RecertificationJobCancelRequest(_StrictModel):
    expected_status: RecertificationStatus
    reason: str = Field(default="", max_length=512)


class ManualQualityScanRequest(_StrictModel):
    limit: int = Field(default=50, ge=1, le=200, strict=True)


# Compatibility aliases keep the transport vocabulary discoverable to callers that use
# the longer database names.
QualitySloPolicyCreateRequest = SloPolicyCreateRequest
QualitySloPolicyPatchRequest = SloPolicyPatchRequest
QualityScanScheduleCreateRequest = ScanScheduleCreateRequest
QualityScanSchedulePatchRequest = ScanSchedulePatchRequest
QualityScanRunCancelRequest = ScanRunCancelRequest
QualityAlertActionRequest = AlertActionRequest
QualityAlertSuppressRequest = AlertSuppressRequest
QualityRecertificationJobCreateRequest = RecertificationJobCreateRequest
QualityRecertificationJobCancelRequest = RecertificationJobCancelRequest


def _unavailable(message: str = _UNAVAILABLE_MESSAGE) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": _UNAVAILABLE_CODE, "message": message},
    )


def _safe_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, Mapping):
            code = detail.get("code")
            message = detail.get("message")
            status = exc.status_code
            if (
                type(status) is int
                and 400 <= status <= 599
                and isinstance(code, str)
                and _ERROR_CODE.fullmatch(code)
                and isinstance(message, str)
                and 0 < len(message) <= 512
                and not any(char in message for char in "\r\n\x00")
            ):
                return HTTPException(
                    status_code=status,
                    detail={"code": code, "message": message},
                )
        return _unavailable()

    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if (
        type(status) is int
        and 400 <= status <= 599
        and isinstance(code, str)
        and _ERROR_CODE.fullmatch(code)
        and isinstance(message, str)
        and 0 < len(message) <= 512
        and not any(char in message for char in "\r\n\x00")
    ):
        return HTTPException(
            status_code=status,
            detail={"code": code, "message": message},
        )
    return _unavailable()


def _load_service(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise _unavailable() from exc


def _resolve_service(injected: ServiceProvider | None, module_name: str) -> Any:
    return injected if injected is not None else _load_service(module_name)


def _clean_idempotency_key(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "idempotency_key_required",
                "message": "Idempotency-Key is required",
            },
        )
    return cleaned


def _assert_actor_permission(
    actor: KnowledgeActor, permission: str, *, owner_admin: bool = False
) -> None:
    role = str(actor.role or "").strip().casefold()
    if owner_admin:
        allowed = role in {"owner", "admin"}
    else:
        allowed = role_allows(role, permission)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": _PERMISSION_CODE,
                "message": (
                    "Tenant owner or admin is required"
                    if owner_admin
                    else f"Actor lacks {permission}"
                ),
            },
        )


def _service_call(service: Any, operation: str, engine: Any, **kwargs: Any) -> Any:
    try:
        method = getattr(service, operation)
    except AttributeError as exc:
        raise _unavailable() from exc
    if not callable(method):
        raise _unavailable()
    try:
        return method(engine, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _safe_http_error(exc) from exc


def _result(value: Any, *, default_status: int = 200) -> tuple[Any, int]:
    explicit_status = False
    if isinstance(value, Mapping):
        body: Any = dict(value)
        status = 200
    else:
        raw_body = getattr(value, "body", None)
        raw_status = getattr(value, "status", None)
        if isinstance(raw_body, Mapping):
            body = dict(raw_body)
            status = raw_status
            explicit_status = True
        elif is_dataclass(value):
            body = asdict(value)
            status = 200
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            body = {"items": list(value), "count": len(value), "next_cursor": None}
            status = 200
        else:
            raise _unavailable(_INVALID_RESULT_MESSAGE)
    if type(status) is not int or not 100 <= status <= 599:
        raise _unavailable(_INVALID_RESULT_MESSAGE)
    if not explicit_status and status == 200:
        status = default_status
    try:
        return jsonable_encoder(body), status
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(_INVALID_RESULT_MESSAGE) from exc


def _response(value: Any, *, default_status: int = 200) -> JSONResponse:
    body, status = _result(value, default_status=default_status)
    return JSONResponse(body, status_code=status)


def _actor_context(actor: KnowledgeActor) -> dict[str, str]:
    return {
        "tenant_id": actor.tenant_id,
        "actor_id": actor.account_id,
        "request_id": actor.request_id,
        "request_ip": actor.request_ip,
    }


def build_enterprise_release_quality_operations_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    slo_service: ServiceProvider | None = None,
    scheduler_service: ServiceProvider | None = None,
    alert_service: ServiceProvider | None = None,
    recertification_service: ServiceProvider | None = None,
    summary_service: ServiceProvider | None = None,
    operations_service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the authenticated Stage 21 Quality Operations router.

    One service can be injected for compatibility tests; precise service
    injections are preferred. Production services are imported lazily by the
    plan module name, so an independently deployed Alert or Recertification
    module fails closed at request time instead of making app import fail.
    """

    slo = slo_service or service
    scheduler = scheduler_service or service
    alerts = alert_service or service
    recertification = recertification_service or service
    summary = summary_service or operations_service or service

    read_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    manage_actor_dependency = actor_dependency or require_knowledge_permission(KNOWLEDGE_MANAGE)
    dataset_read_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_READ, resolve_path_dataset("dataset_id")
    )
    dataset_manage_dependency = actor_dependency or require_knowledge_permission(
        KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id")
    )
    router = APIRouter(tags=["enterprise-release-quality-operations"])

    def execute_read(
        provider: ServiceProvider | None,
        module_name: str,
        operation: str,
        actor: KnowledgeActor,
        **kwargs: Any,
    ) -> JSONResponse:
        _assert_actor_permission(actor, KNOWLEDGE_READ)
        try:
            target = _resolve_service(provider, module_name)
            value = _service_call(
                target,
                operation,
                read_engine_provider(),
                **_actor_context(actor),
                **kwargs,
            )
            return _response(value)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _safe_http_error(exc) from exc

    def execute_mutation(
        provider: ServiceProvider | None,
        module_name: str,
        operation: str,
        actor: KnowledgeActor,
        *,
        idempotency_key: str,
        default_status: int = 200,
        owner_admin: bool = False,
        **kwargs: Any,
    ) -> JSONResponse:
        _assert_actor_permission(actor, KNOWLEDGE_MANAGE, owner_admin=owner_admin)
        key = _clean_idempotency_key(idempotency_key)
        try:
            target = _resolve_service(provider, module_name)
            value = _service_call(
                target,
                operation,
                mutation_engine_provider(),
                **_actor_context(actor),
                idempotency_key=key,
                **kwargs,
            )
            return _response(value, default_status=default_status)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _safe_http_error(exc) from exc

    @router.get("/api/enterprise/release-quality/slo-policies")
    def list_slo_policies_route(
        actor: KnowledgeActor = Depends(read_actor_dependency),
        scope_type: ScopeType | None = Query(default=None),
        scope_value: str | None = Query(default=None, min_length=1, max_length=128),
        status: SloPolicyStatus | None = Query(default=None),
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
        return execute_read(slo, _SLO_MODULE, "list_quality_slo_policies", actor, **kwargs)

    @router.post("/api/enterprise/release-quality/slo-policies", status_code=201)
    def create_slo_policy_route(
        body: SloPolicyCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            slo,
            _SLO_MODULE,
            "create_quality_slo_policy",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            owner_admin=True,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/release-quality/slo-policies")
    def update_slo_policy_route(
        body: SloPolicyPatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            slo,
            _SLO_MODULE,
            "update_quality_slo_policy",
            actor,
            idempotency_key=idempotency_key,
            owner_admin=True,
            **body.model_dump(exclude_none=True),
        )

    @router.get("/api/enterprise/release-quality/scan-schedules")
    def list_scan_schedules_route(
        dataset_id: str = Query(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID),
        actor: KnowledgeActor = Depends(read_actor_dependency),
        status: ScheduleStatus | None = Query(default=None),
        slo_policy_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID
        ),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "cursor": cursor,
            "limit": limit,
        }
        if status is not None:
            kwargs["status"] = status
        if slo_policy_id is not None:
            kwargs["slo_policy_id"] = slo_policy_id
        return execute_read(
            scheduler,
            _SCHEDULER_MODULE,
            "list_quality_scan_schedules",
            actor,
            **kwargs,
        )

    @router.post("/api/enterprise/release-quality/scan-schedules", status_code=201)
    def create_scan_schedule_route(
        body: ScanScheduleCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            scheduler,
            _SCHEDULER_MODULE,
            "create_quality_scan_schedule",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            owner_admin=True,
            **body.model_dump(),
        )

    @router.patch("/api/enterprise/release-quality/scan-schedules")
    def update_scan_schedule_route(
        body: ScanSchedulePatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(manage_actor_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            scheduler,
            _SCHEDULER_MODULE,
            "update_quality_scan_schedule",
            actor,
            idempotency_key=idempotency_key,
            owner_admin=True,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/release-quality/scan-runs")
    def list_scan_runs_route(
        dataset_id: str = Query(min_length=1, max_length=64, pattern=_SAFE_DATASET_ID),
        actor: KnowledgeActor = Depends(read_actor_dependency),
        schedule_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID
        ),
        status: ScanRunStatus | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "cursor": cursor,
            "limit": limit,
        }
        if schedule_id is not None:
            kwargs["schedule_id"] = schedule_id
        if status is not None:
            kwargs["status"] = status
        return execute_read(
            scheduler,
            _SCHEDULER_MODULE,
            "list_quality_scan_runs",
            actor,
            **kwargs,
        )

    @router.post("/api/enterprise/release-quality/scan-runs/{run_id}/cancel")
    def cancel_scan_run_route(
        run_id: RunId,
        body: ScanRunCancelRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        _assert_actor_permission(actor, KNOWLEDGE_MANAGE)
        key = _clean_idempotency_key(idempotency_key)
        try:
            target = _resolve_service(scheduler, _SCHEDULER_MODULE)
            value = _service_call(
                target,
                "cancel_quality_scan_run",
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                dataset_id=body.dataset_id,
                run_id=run_id,
                owner_id=actor.account_id,
                reason=body.reason,
                request_id=actor.request_id,
                idempotency_key=key,
            )
            return _response(value)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _safe_http_error(exc) from exc

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/quality-operations/summary")
    def quality_operations_summary_route(
        dataset_id: DatasetPathId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
    ) -> JSONResponse:
        provider = summary or scheduler
        module_name = _SCHEDULER_MODULE if provider is scheduler else _SLO_MODULE
        return execute_read(
            provider,
            module_name,
            "get_quality_operations_summary",
            actor,
            dataset_id=dataset_id,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/quality-observations")
    def list_quality_observations_route(
        dataset_id: DatasetPathId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        release_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID
        ),
        channel_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID
        ),
        release_role: ReleaseRole | None = Query(default=None),
        severity: ObservationSeverity | None = Query(default=None),
        gate_state: ObservationGateState | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "cursor": cursor,
            "limit": limit,
        }
        for key, value in {
            "release_id": release_id,
            "channel_id": channel_id,
            "release_role": release_role,
            "severity": severity,
            "gate_state": gate_state,
        }.items():
            if value is not None:
                kwargs[key] = value
        return execute_read(
            scheduler,
            _SCHEDULER_MODULE,
            "list_quality_observations",
            actor,
            **kwargs,
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts")
    def list_quality_alerts_route(
        dataset_id: DatasetPathId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        status: AlertStatus | None = Query(default=None),
        severity: AlertSeverity | None = Query(default=None),
        alert_type: str | None = Query(default=None, min_length=1, max_length=64, pattern=_SAFE_ID),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {"dataset_id": dataset_id, "cursor": cursor, "limit": limit}
        for key, value in {
            "status": status,
            "severity": severity,
            "alert_type": alert_type,
        }.items():
            if value is not None:
                kwargs[key] = value
        return execute_read(
            alerts,
            _ALERT_MODULE,
            "list_quality_alerts",
            actor,
            **kwargs,
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/acknowledge"
    )
    def acknowledge_quality_alert_route(
        dataset_id: DatasetPathId,
        alert_id: AlertId,
        body: AlertActionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            alerts,
            _ALERT_MODULE,
            "acknowledge_quality_alert",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            alert_id=alert_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/resolve")
    def resolve_quality_alert_route(
        dataset_id: DatasetPathId,
        alert_id: AlertId,
        body: AlertActionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            alerts,
            _ALERT_MODULE,
            "resolve_quality_alert",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            alert_id=alert_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/suppress")
    def suppress_quality_alert_route(
        dataset_id: DatasetPathId,
        alert_id: AlertId,
        body: AlertSuppressRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            alerts,
            _ALERT_MODULE,
            "suppress_quality_alert",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            alert_id=alert_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs")
    def list_recertification_jobs_route(
        dataset_id: DatasetPathId,
        actor: KnowledgeActor = Depends(dataset_read_dependency),
        status: RecertificationStatus | None = Query(default=None),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        kwargs: dict[str, Any] = {"dataset_id": dataset_id, "cursor": cursor, "limit": limit}
        if status is not None:
            kwargs["status"] = status
        return execute_read(
            recertification,
            _RECERTIFICATION_MODULE,
            "list_recertification_jobs",
            actor,
            **kwargs,
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs",
        status_code=201,
    )
    def queue_recertification_job_route(
        dataset_id: DatasetPathId,
        body: RecertificationJobCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            recertification,
            _RECERTIFICATION_MODULE,
            "queue_recertification_job",
            actor,
            idempotency_key=idempotency_key,
            default_status=201,
            dataset_id=dataset_id,
            **body.model_dump(),
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs/{job_id}/cancel"
    )
    def cancel_recertification_job_route(
        dataset_id: DatasetPathId,
        job_id: JobId,
        body: RecertificationJobCancelRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        return execute_mutation(
            recertification,
            _RECERTIFICATION_MODULE,
            "cancel_recertification_job",
            actor,
            idempotency_key=idempotency_key,
            dataset_id=dataset_id,
            job_id=job_id,
            **body.model_dump(),
        )

    @router.post(
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-operations/scan",
        status_code=202,
    )
    def manual_quality_scan_route(
        dataset_id: DatasetPathId,
        body: ManualQualityScanRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(dataset_manage_dependency),
    ) -> JSONResponse:
        _assert_actor_permission(actor, KNOWLEDGE_MANAGE)
        key = _clean_idempotency_key(idempotency_key)
        try:
            target = _resolve_service(scheduler, _SCHEDULER_MODULE)
            value = _service_call(
                target,
                "enqueue_due_quality_scans",
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                dataset_id=dataset_id,
                request_id=actor.request_id,
                idempotency_key=key,
                body=body.model_dump(),
                limit=body.limit,
            )
            return _response(value, default_status=202)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _safe_http_error(exc) from exc

    return router


# Explicit alias for callers using the shorter API naming convention.
build_enterprise_release_quality_operations_api_router = (
    build_enterprise_release_quality_operations_router
)


__all__ = [
    "AlertActionRequest",
    "AlertSuppressRequest",
    "ManualQualityScanRequest",
    "QualityAlertActionRequest",
    "QualityAlertSuppressRequest",
    "QualityRecertificationJobCancelRequest",
    "QualityRecertificationJobCreateRequest",
    "QualityScanRunCancelRequest",
    "QualityScanScheduleCreateRequest",
    "QualityScanSchedulePatchRequest",
    "QualitySloPolicyCreateRequest",
    "QualitySloPolicyPatchRequest",
    "RecertificationJobCancelRequest",
    "RecertificationJobCreateRequest",
    "ScanRunCancelRequest",
    "ScanScheduleCreateRequest",
    "ScanSchedulePatchRequest",
    "SloPolicyCreateRequest",
    "SloPolicyPatchRequest",
    "build_enterprise_release_quality_operations_api_router",
    "build_enterprise_release_quality_operations_router",
]
