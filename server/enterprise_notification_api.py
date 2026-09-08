"""Strict authenticated HTTP boundary for the Stage 22 Notification Center.

The router deliberately keeps transport concerns separate from Notification
authority.  Read routes use a read engine, receipt/subscription mutations use a
mutation engine, and the default service modules are imported lazily so the
App can start while the parallel Task 4 services are being delivered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
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
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_ERROR = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|token|"
    r"api[_ -]?key|access[_ -]?key|refresh[_ -]?token|ticket|query|body|"
    r"result[_ -]?body|content|webhook|email|phone)(?:\s|[:=\"'([{])"
)
_UNSAFE_URI = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|mailto:|data:|javascript:)")

_RECEIPTS_MODULE = "core.enterprise_notification_receipts"
_SUBSCRIPTIONS_MODULE = "core.enterprise_notification_subscriptions"

_UNAVAILABLE_CODE = "enterprise_notification_center_unavailable"
_UNAVAILABLE_MESSAGE = "Enterprise Notification Center 服务暂不可用"
_INVALID_RESULT_MESSAGE = "Enterprise Notification Center 返回无效"
_PERMISSION_CODE = "enterprise_notification_center_forbidden"
_INVALID_REQUEST_CODE = "enterprise_notification_request_invalid"

NotificationId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
SubscriptionId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]

NotificationStatus = Literal["unread", "read", "archived", "all"]
NotificationCategory = Literal["quality", "approval"]
NotificationSeverity = Literal["info", "warning", "critical"]
SubscriptionStatus = Literal["active", "archived", "all"]
NotificationPreference = Literal["subscribed", "muted"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReceiptMutationRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("reason is invalid")
        return value


class BulkReadItem(_StrictModel):
    notification_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    expected_revision: int = Field(ge=1, strict=True)


class BulkReadRequest(_StrictModel):
    items: list[BulkReadItem] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("reason is invalid")
        return value

    @field_validator("items")
    @classmethod
    def validate_unique_items(cls, value: list[BulkReadItem]) -> list[BulkReadItem]:
        identifiers = [item.notification_id for item in value]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("notification IDs must be unique")
        return value


class SubscriptionPatchRequest(_StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    preference: NotificationPreference
    minimum_severity: NotificationSeverity
    muted_until: datetime | None = None
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("reason is invalid")
        return value

    @field_validator("muted_until", mode="before")
    @classmethod
    def validate_muted_until(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            raw = value.strip()
            if not raw or _CONTROL.search(raw):
                raise ValueError("muted_until is invalid")
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("muted_until is invalid") from exc
        else:
            raise ValueError("muted_until is invalid")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("muted_until must be timezone-aware")
        return parsed

    @model_validator(mode="after")
    def validate_preference_window(self) -> "SubscriptionPatchRequest":
        if self.preference == "subscribed" and self.muted_until is not None:
            raise ValueError("subscribed preference must not have muted_until")
        return self


# Explicit aliases make the transport contract discoverable to clients that use
# the longer database vocabulary.
NotificationReceiptMutationRequest = ReceiptMutationRequest
NotificationBulkReadItem = BulkReadItem
NotificationBulkReadRequest = BulkReadRequest
NotificationSubscriptionPatchRequest = SubscriptionPatchRequest


def _unavailable(message: str = _UNAVAILABLE_MESSAGE) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": _UNAVAILABLE_CODE, "message": message},
    )


def _safe_error_detail(status: Any, code: Any, message: Any) -> dict[str, Any] | None:
    if type(status) is not int or not 400 <= status <= 599:
        return None
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        return None
    if (
        not isinstance(message, str)
        or not 0 < len(message) <= 512
        or _CONTROL.search(message)
        or _UNSAFE_ERROR.search(message)
        or _UNSAFE_URI.search(message)
    ):
        return None
    return {"status_code": status, "detail": {"code": code, "message": message}}


def _safe_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, Mapping):
            safe = _safe_error_detail(exc.status_code, detail.get("code"), detail.get("message"))
            if safe is not None:
                return HTTPException(**safe)
        return _unavailable()

    safe = _safe_error_detail(
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(exc, "message", None),
    )
    if safe is not None:
        return HTTPException(**safe)
    return _unavailable()


def _load_service(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable() from exc


def _resolve_service(injected: ServiceProvider | None, module_name: str) -> Any:
    return injected if injected is not None else _load_service(module_name)


def _clean_idempotency_key(value: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=422,
            detail={"code": _INVALID_REQUEST_CODE, "message": "Idempotency-Key is invalid"},
        )
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or _CONTROL.search(normalized):
        raise HTTPException(
            status_code=422,
            detail={"code": _INVALID_REQUEST_CODE, "message": "Idempotency-Key is invalid"},
        )
    return normalized


def _validate_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 2048 or _CONTROL.search(normalized):
        raise HTTPException(
            status_code=422,
            detail={"code": _INVALID_REQUEST_CODE, "message": "cursor is invalid"},
        )
    return normalized


def _validate_limit(value: str | int) -> int:
    if isinstance(value, str):
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", value) is None:
            raise HTTPException(
                status_code=422,
                detail={"code": _INVALID_REQUEST_CODE, "message": "limit is invalid"},
            )
        value = int(value)
    if type(value) is not int or not 1 <= value <= 200:
        raise HTTPException(
            status_code=422,
            detail={"code": _INVALID_REQUEST_CODE, "message": "limit is invalid"},
        )
    return value


def _service_call(service: Any, operation: str, engine: Any, **kwargs: Any) -> Any:
    try:
        method = getattr(service, operation)
    except AttributeError as exc:
        raise _unavailable() from exc
    if not callable(method):
        raise _unavailable()

    # Security context is an explicit provider contract. Never delete Tenant,
    # Actor, request or idempotency arguments to make an incompatible provider run.
    # A provider with a stale signature must fail closed before its body executes.
    try:
        return method(engine, **kwargs)
    except HTTPException as exc:
        raise _safe_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _safe_http_error(exc) from exc


def _result(value: Any, *, default_status: int = 200) -> tuple[dict[str, Any], int]:
    explicit_status = False
    if isinstance(value, Mapping):
        body: Any = dict(value)
        status = default_status
    else:
        raw_body = getattr(value, "body", None)
        raw_status = getattr(value, "status", None)
        if isinstance(raw_body, Mapping):
            body = dict(raw_body)
            status = raw_status
            explicit_status = True
        elif is_dataclass(value):
            body = asdict(value)
            status = default_status
        else:
            raise _unavailable(_INVALID_RESULT_MESSAGE)

    if status is None:
        status = default_status
    if type(status) is not int or not 100 <= status <= 599:
        raise _unavailable(_INVALID_RESULT_MESSAGE)
    try:
        encoded = jsonable_encoder(body)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(_INVALID_RESULT_MESSAGE) from exc
    if not isinstance(encoded, Mapping):
        raise _unavailable(_INVALID_RESULT_MESSAGE)
    if explicit_status and status == 204:
        # A JSON response with a body is not a valid 204 transport projection.
        raise _unavailable(_INVALID_RESULT_MESSAGE)
    return dict(encoded), status


def _response(value: Any, *, default_status: int = 200) -> JSONResponse:
    body, status = _result(value, default_status=default_status)
    return JSONResponse(body, status_code=status)


def _read_actor_context(actor: KnowledgeActor) -> dict[str, str]:
    return {"tenant_id": actor.tenant_id, "actor_id": actor.account_id}


def _mutation_actor_context(actor: KnowledgeActor) -> dict[str, str]:
    return {
        "tenant_id": actor.tenant_id,
        "actor_id": actor.account_id,
        "account_id": actor.account_id,
        "request_id": actor.request_id,
        "request_ip": actor.request_ip,
    }


def build_enterprise_notification_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    receipt_service: ServiceProvider | None = None,
    subscription_service: ServiceProvider | None = None,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the authenticated Stage 22 Notification Center router.

    ``receipt_service`` and ``subscription_service`` are explicit injection
    points for service doubles and deployment seams.  If omitted, their Task 4
    modules are resolved lazily at request time and unavailable dependencies
    fail closed with a safe 503 response.
    """

    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    if actor_dependency is not None and not callable(actor_dependency):
        raise TypeError("actor_dependency must be callable")

    receipts = receipt_service if receipt_service is not None else service
    subscriptions = subscription_service if subscription_service is not None else service
    actor_dep = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["enterprise-notification-center"])

    def execute_read(
        provider: ServiceProvider | None,
        module_name: str,
        operation: str,
        actor: KnowledgeActor,
        **kwargs: Any,
    ) -> JSONResponse:
        try:
            target = _resolve_service(provider, module_name)
            value = _service_call(
                target,
                operation,
                read_engine_provider(),
                **_read_actor_context(actor),
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
        **kwargs: Any,
    ) -> JSONResponse:
        key = _clean_idempotency_key(idempotency_key)
        try:
            target = _resolve_service(provider, module_name)
            value = _service_call(
                target,
                operation,
                mutation_engine_provider(),
                **_mutation_actor_context(actor),
                idempotency_key=key,
                **kwargs,
            )
            return _response(value)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _safe_http_error(exc) from exc

    @router.get("/api/enterprise/notifications/summary")
    def get_summary(actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return execute_read(receipts, _RECEIPTS_MODULE, "get_notification_summary", actor)

    @router.get("/api/enterprise/notifications")
    def list_notifications(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: str = Query(default="50", min_length=1, max_length=3),
        status: NotificationStatus | None = Query(default=None),
        category: NotificationCategory | None = Query(default=None),
        severity: NotificationSeverity | None = Query(default=None),
    ) -> JSONResponse:
        return execute_read(
            receipts,
            _RECEIPTS_MODULE,
            "list_notifications",
            actor,
            cursor=_validate_cursor(cursor),
            limit=_validate_limit(limit),
            status=status,
            category=category,
            severity=severity,
        )

    @router.get("/api/enterprise/notifications/{notification_id}")
    def get_notification(
        notification_id: NotificationId,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_read(
            receipts,
            _RECEIPTS_MODULE,
            "get_notification",
            actor,
            notification_id=notification_id,
        )

    @router.post("/api/enterprise/notifications/{notification_id}/read")
    def mark_read(
        notification_id: NotificationId,
        body: ReceiptMutationRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            receipts,
            _RECEIPTS_MODULE,
            "mark_notification_read",
            actor,
            idempotency_key=idempotency_key,
            notification_id=notification_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/notifications/{notification_id}/unread")
    def mark_unread(
        notification_id: NotificationId,
        body: ReceiptMutationRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            receipts,
            _RECEIPTS_MODULE,
            "mark_notification_unread",
            actor,
            idempotency_key=idempotency_key,
            notification_id=notification_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/notifications/{notification_id}/archive")
    def archive(
        notification_id: NotificationId,
        body: ReceiptMutationRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            receipts,
            _RECEIPTS_MODULE,
            "archive_notification",
            actor,
            idempotency_key=idempotency_key,
            notification_id=notification_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/notifications/bulk-read")
    def bulk_read(
        body: BulkReadRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            receipts,
            _RECEIPTS_MODULE,
            "bulk_mark_notifications_read",
            actor,
            idempotency_key=idempotency_key,
            items=[item.model_dump() for item in body.items],
            reason=body.reason,
        )

    @router.get("/api/enterprise/notification-subscriptions")
    def list_subscriptions(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, max_length=2048),
        limit: str = Query(default="50", min_length=1, max_length=3),
        status: SubscriptionStatus | None = Query(default=None),
        category: NotificationCategory | None = Query(default=None),
    ) -> JSONResponse:
        return execute_read(
            subscriptions,
            _SUBSCRIPTIONS_MODULE,
            "list_notification_subscriptions",
            actor,
            cursor=_validate_cursor(cursor),
            limit=_validate_limit(limit),
            status=status or "active",
            category=category,
        )

    @router.patch("/api/enterprise/notification-subscriptions/{subscription_id}")
    def update_subscription(
        subscription_id: SubscriptionId,
        body: SubscriptionPatchRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return execute_mutation(
            subscriptions,
            _SUBSCRIPTIONS_MODULE,
            "update_notification_subscription",
            actor,
            idempotency_key=idempotency_key,
            subscription_id=subscription_id,
            **body.model_dump(),
        )

    return router


build_enterprise_notification_api_router = build_enterprise_notification_router

__all__ = [
    "BulkReadItem",
    "BulkReadRequest",
    "NotificationBulkReadItem",
    "NotificationBulkReadRequest",
    "NotificationId",
    "NotificationReceiptMutationRequest",
    "NotificationSubscriptionPatchRequest",
    "ReceiptMutationRequest",
    "SubscriptionPatchRequest",
    "SubscriptionId",
    "build_enterprise_notification_api_router",
    "build_enterprise_notification_router",
]
