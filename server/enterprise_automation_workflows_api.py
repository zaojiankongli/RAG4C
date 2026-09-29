"""Strict authenticated HTTP boundary for Stage 25 Enterprise Automation Workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
import importlib
import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from core.automation_rule_strategies import (
    AutomationParameterSpec,
    action_parameter_specs,
    condition_parameter_specs,
)
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
ServiceProvider = Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DECLARED_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DECLARED_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_UNSAFE_ERROR = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|token|"
    r"api[_ -]?key|access[_ -]?key|refresh[_ -]?token|ticket|query|body|"
    r"result[_ -]?body|content|webhook|email|phone)(?:\s|[:=\"'([{])"
)
_UNSAFE_URI = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|mailto:|data:|javascript:)")
_SQL_LIKE = re.compile(
    r"(?is)(?:"
    r"\bselect\s+(?:distinct\s+)?[\w*\"`'([]"
    r"|\binsert\s+(?:into\s+)?[\w\"`'(]"
    r"|\bupdate\s+[\w\"`.]+\s+set\b"
    r"|\bdelete\s+from\b"
    r"|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b"
    r"|\bgrant\s+\w+\s+on\b"
    r"|\brevoke\s+\w+\s+on\b"
    # 写成 \bex(?:ec|ecute) 而不是 \bexec(?:ute)?：前者与后者匹配的字符串完全一致
    # （exec / execute 两个词），但不含会被扫描器当成 exec( 调用签名的字面子串。
    r"|\bex(?:ec|ecute)\s+\S+"
    r"|\bunion\s+(?:all\s+)?select\b"
    r"|\b(?:select|insert|update|delete|drop|alter|create|grant|revoke|"
    r"ex(?:ec|ecute)|union)\s*$"
    r")"
)
_BEARER = re.compile(r"(?i)\bbearer\b")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])"
)
_MAX_SAFE_DEPTH = 12
_MAX_SAFE_ITEMS = 64
_MAX_SAFE_UTF8_BYTES = 16_384
_SAFE_CODE_KEYS = frozenset(
    {"trigger_code", "condition_code", "action_code", "event_type", "error_code", "reason_code", "status_code"}
)
_UNSAFE_KEY_PARTS = frozenset(
    {
        "query",
        "url",
        "uri",
        "href",
        "sql",
        "webhook",
        "script",
        "expression",
        "eval",
        "exec",
        "prompt",
        "body",
        "content",
        "raw",
        "payload",
        "token",
        "credential",
        "ticket",
        "password",
        "secret",
        "authorization",
        "bearer",
        "cookie",
        "header",
        "answer",
        "apikey",
        "accesskey",
        "code",
    }
)
_SERVICE_MODULE = "core.enterprise_automation_workflows_service"
_UNAVAILABLE_CODE = "enterprise_automation_workflows_unavailable"
_UNAVAILABLE_MESSAGE = "Enterprise Automation Workflows 服务暂不可用"
_INVALID_REQUEST_CODE = "enterprise_automation_workflows_request_invalid"
_INVALID_RESULT_MESSAGE = "Enterprise Automation Workflows 返回无效"

_WIRE_PAGE_KEYS = frozenset({"items", "next_cursor", "invalid_item_count"})
_WIRE_RULE_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "name",
        "status",
        "revision",
        "current_revision_id",
        "workspace_id",
        "dataset_id",
        "priority",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
    }
)
_WIRE_REVISION_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "rule_id",
        "revision",
        "trigger_code",
        "condition_code",
        "condition_params_json",
        "action_plan_json",
        "definition_digest",
        "created_at",
        "created_by",
    }
)
_WIRE_RUN_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "rule_id",
        "rule_revision_id",
        "trigger_event_id",
        "trigger_event_digest",
        "status",
        "condition_matched",
        "action_count",
        "requested_count",
        "rejected_count",
        "started_at",
        "completed_at",
        "safe_error_code",
        "safe_error",
    }
)
_WIRE_ACTION_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "run_id",
        "rule_id",
        "step_index",
        "action_code",
        "status",
        "target_kind",
        "target_id",
        "target_revision",
        "target_digest",
        "safe_params_json",
        "safe_reason",
        "approval_request_id",
        "notification_id",
        "task_id",
        "requested_at",
        "dispatched_at",
        "applied_at",
        "rejected_at",
        "expires_at",
    }
)
_WIRE_EVENT_KEYS = frozenset(
    {
        "id",
        "tenant_id",
        "rule_id",
        "run_id",
        "stream_key",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot_json",
        "occurred_at",
    }
)
_WIRE_SUMMARY_KEYS = frozenset(
    {
        "tenant_id",
        "state",
        "active_rule_count",
        "paused_rule_count",
        "failed_run_count",
        "pending_request_count",
        "as_of",
        "reason_code",
    }
)
_WIRE_MUTATION_KEYS = frozenset(
    {"state", "operation", "resource_id", "revision", "message", "retryable"}
)
_WIRE_MUTATION_STATES = frozenset(
    {"applied", "replayed", "conflict", "blocked", "rejected", "unavailable"}
)

RuleId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
RunId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _validate_declared_parameter(
    spec: AutomationParameterSpec,
    value: Any,
    field: str,
) -> None:
    if spec.kind == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{field} must be a boolean")
        return
    if spec.kind == "integer":
        if (
            type(value) is not int
            or type(value) is bool
            or value < (0 if spec.minimum is None else spec.minimum)
            or (spec.maximum is not None and value > spec.maximum)
        ):
            raise ValueError(f"{field} is invalid")
        return
    if spec.kind == "text":
        if type(value) is not str:
            raise ValueError(f"{field} must be a string")
        _safe_text(value, field, maximum=spec.maximum_length or 512)
        return
    if spec.kind == "identifier":
        if type(value) is not str:
            raise ValueError(f"{field} must be a string")
        normalized = _safe_text(value, field, maximum=128)
        if _DECLARED_IDENTIFIER.fullmatch(normalized) is None or ".." in normalized:
            raise ValueError(f"{field} is invalid")
        return
    if type(value) is not str:
        raise ValueError(f"{field} is not allowed")
    normalized = _safe_text(value, field, maximum=128)
    canonical = normalized.casefold().replace("-", "_").replace(" ", "_")
    if _DECLARED_CODE.fullmatch(canonical) is None or (
        spec.allowed_values
        and canonical
        not in {
            item.casefold().replace("-", "_").replace(" ", "_")
            for item in spec.allowed_values
        }
    ):
        raise ValueError(f"{field} is not allowed")

AutomationTriggerCode = Literal[
    "task_failed",
    "task_source_stale",
    "source_sync_failed",
    "release_quality_alert_opened",
    "release_recertification_blocked",
    "approval_request_terminal",
]
AutomationConditionCode = Literal[
    "always",
    "status_is",
    "action_required",
    "severity_at_least",
    "attempt_exhausted",
    "source_is_stale",
]
AutomationActionCode = Literal[
    "notify_operator",
    "request_approval",
    "open_task_attention",
    "pause_rule",
]
RuleStatus = Literal["draft", "active", "paused", "archived"]
RuleStatusFilter = Literal["draft", "active", "paused", "archived", "all"]
AutomationRunStatus = Literal[
    "started",
    "not_matched",
    "requested",
    "completed",
    "failed",
    "blocked",
]
AutomationActionRequestStatus = Literal["requested", "dispatched", "applied", "rejected", "expired"]
AutomationEventType = Literal[
    "rule_created",
    "revision_created",
    "revision_activated",
    "rule_paused",
    "trigger_observed",
    "condition_not_matched",
    "run_started",
    "action_requested",
    "action_rejected",
    "run_completed",
    "run_failed",
]

SafeScalar = StrictStr | StrictInt | StrictBool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ActionPlanStep(StrictModel):
    action_code: AutomationActionCode
    params: dict[str, SafeScalar]

    @model_validator(mode="after")
    def validate_params(self) -> "ActionPlanStep":
        specs = action_parameter_specs(self.action_code)
        allowed = frozenset(spec.name for spec in specs)
        _validate_safe_mapping(
            self.params, "action.params", allowed=allowed
        )
        if set(self.params) != set(allowed):
            raise ValueError("action.params must exactly match the selected action")
        for spec in specs:
            value = self.params[spec.name]
            _validate_declared_parameter(spec, value, f"action.params.{spec.name}")
        return self


class RuleCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    workspace_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
    )
    dataset_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_SAFE_ID.pattern
    )
    priority: int = Field(default=100, ge=0, le=1000, strict=True)
    trigger_code: AutomationTriggerCode
    condition_code: AutomationConditionCode
    condition_params: dict[str, SafeScalar] = Field(default_factory=dict)
    action_plan: list[ActionPlanStep] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("name", "reason")
    @classmethod
    def validate_safe_text(cls, value: str) -> str:
        return _safe_text(value, "request field")

    @model_validator(mode="after")
    def validate_definition(self) -> "RuleCreateRequest":
        _validate_condition_params(self.condition_code, self.condition_params)
        return self


class RulePatchRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    expected_definition_digest: str = Field(min_length=64, max_length=64, pattern=_DIGEST.pattern)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    workspace_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
    )
    dataset_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_SAFE_ID.pattern
    )
    priority: int | None = Field(default=None, ge=0, le=1000, strict=True)
    status: RuleStatus | None = None
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("name", "reason")
    @classmethod
    def validate_safe_text(cls, value: str) -> str:
        return _safe_text(value, "request field")

    @model_validator(mode="after")
    def require_change(self) -> "RulePatchRequest":
        if all(
            value is None
            for value in (self.name, self.workspace_id, self.dataset_id, self.priority, self.status)
        ):
            raise ValueError("rule patch requires a change")
        return self


class RuleRevisionCreateRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    expected_definition_digest: str = Field(min_length=64, max_length=64, pattern=_DIGEST.pattern)
    trigger_code: AutomationTriggerCode
    condition_code: AutomationConditionCode
    condition_params: dict[str, SafeScalar] = Field(default_factory=dict)
    action_plan: list[ActionPlanStep] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _safe_text(value, "reason")

    @model_validator(mode="after")
    def validate_definition(self) -> "RuleRevisionCreateRequest":
        _validate_condition_params(self.condition_code, self.condition_params)
        if len({step.action_code for step in self.action_plan}) == 0:
            raise ValueError("action_plan must not be empty")
        return self


class RevisionFenceRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    expected_definition_digest: str = Field(min_length=64, max_length=64, pattern=_DIGEST.pattern)
    reason: str = Field(min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _safe_text(value, "reason")


class ActivateRequest(RevisionFenceRequest):
    revision_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)


class TriggerEventRequest(StrictModel):
    tenant_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_SAFE_ID.pattern
    )
    trigger_code: AutomationTriggerCode
    source_stream_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    source_event_id: str = Field(min_length=1, max_length=128, pattern=_SAFE_ID.pattern)
    source_event_digest: str = Field(min_length=64, max_length=64, pattern=_DIGEST.pattern)
    sequence: int = Field(ge=1, strict=True)
    status: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "blocked",
        "unavailable",
        "approved",
        "rejected",
        "expired",
        "completed",
    ]
    severity: Literal["info", "warning", "error", "critical"]
    action_required: bool = Field(strict=True)
    attempt_number: int = Field(ge=0, le=1000, strict=True)
    max_attempts: int = Field(ge=1, le=1000, strict=True)
    source_current: bool = Field(strict=True)
    occurred_at: str = Field(min_length=1, max_length=64)
    safe_facts: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: str) -> str:
        return _safe_text(value, "occurred_at", maximum=64)

    @model_validator(mode="after")
    def validate_event(self) -> "TriggerEventRequest":
        if self.attempt_number > self.max_attempts:
            raise ValueError("attempt_number exceeds max_attempts")
        _validate_safe_mapping(self.safe_facts, "safe_facts")
        return self


class PreviewRequest(RevisionFenceRequest):
    trigger_event: TriggerEventRequest


def _normalized_key(key: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    return re.sub(r"[^a-z0-9]+", "_", separated.casefold()).strip("_")


def _is_unsafe_key(key: str, allowed: frozenset[str] | None = None) -> bool:
    normalized = _normalized_key(key)
    if allowed is not None and normalized in allowed:
        return False
    if normalized in _SAFE_CODE_KEYS:
        return False
    compact = normalized.replace("_", "")
    if compact == "code" or compact.startswith("raw"):
        return True
    return any(part in compact for part in _UNSAFE_KEY_PARTS)


def _safe_text(value: str, field: str, *, maximum: int = 512) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or _CONTROL.search(normalized):
        raise ValueError(f"{field} is invalid")
    if (
        _UNSAFE_URI.search(normalized)
        or _UNSAFE_ERROR.search(normalized)
        or _SQL_LIKE.search(normalized)
        or _BEARER.search(normalized)
        or _JWT.search(normalized)
    ):
        raise ValueError(f"{field} is unsafe")
    return normalized


def _validate_safe_value(
    value: Any, field: str, depth: int, item_count: list[int]
) -> None:
    if depth > _MAX_SAFE_DEPTH:
        raise ValueError(f"{field} is too deeply nested")
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        _safe_text(value, field)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValueError(f"{field} contains too many items")
        item_count[0] += len(value)
        if item_count[0] > _MAX_SAFE_ITEMS:
            raise ValueError(f"{field} contains too many items")
        for index, item in enumerate(value):
            _validate_safe_value(item, f"{field}[{index}]", depth + 1, item_count)
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValueError(f"{field} contains too many fields")
        item_count[0] += len(value)
        if item_count[0] > _MAX_SAFE_ITEMS:
            raise ValueError(f"{field} contains too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or _is_unsafe_key(key):
                raise ValueError(f"{field} contains a forbidden field")
            _validate_safe_value(item, f"{field}.{key}", depth + 1, item_count)
        return
    raise ValueError(f"{field} contains an unsupported value")


def _validate_safe_mapping(
    value: Mapping[str, Any],
    field: str,
    *,
    allowed: frozenset[str] | None = None,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if len(value) > _MAX_SAFE_ITEMS:
        raise ValueError(f"{field} contains too many fields")
    item_count = [len(value)]
    for key, item in value.items():
        if not isinstance(key, str) or not key or _is_unsafe_key(key, allowed):
            raise ValueError(f"{field} contains a forbidden field")
        _validate_safe_value(item, f"{field}.{key}", 1, item_count)
    if item_count[0] > _MAX_SAFE_ITEMS:
        raise ValueError(f"{field} contains too many items")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} contains an unsupported value") from exc
    if len(encoded) > _MAX_SAFE_UTF8_BYTES:
        raise ValueError(f"{field} exceeds the UTF-8 byte limit")


def _bound_trigger_event(event: TriggerEventRequest, tenant_id: str) -> dict[str, Any]:
    payload = event.model_dump(exclude_none=True)
    supplied_tenant = payload.get("tenant_id")
    if supplied_tenant is not None and supplied_tenant != tenant_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": _INVALID_REQUEST_CODE,
                "message": "trigger event Tenant scope is invalid",
            },
        )
    payload["tenant_id"] = tenant_id
    return payload


def _validate_condition_params(
    condition_code: AutomationConditionCode, params: Mapping[str, SafeScalar]
) -> None:
    specs = condition_parameter_specs(condition_code)
    allowed = frozenset(spec.name for spec in specs)
    _validate_safe_mapping(params, "condition_params", allowed=allowed)
    if set(params) != allowed:
        raise ValueError("condition_params must exactly match the selected condition")
    for spec in specs:
        _validate_declared_parameter(
            spec,
            params[spec.name],
            f"condition_params.{spec.name}",
        )


def unavailable(message: str = _UNAVAILABLE_MESSAGE) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": _UNAVAILABLE_CODE, "message": message})


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


def safe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, Mapping):
            safe = _safe_error_detail(exc.status_code, detail.get("code"), detail.get("message"))
            if safe is not None:
                return HTTPException(**safe)
        return unavailable()
    safe = _safe_error_detail(
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(exc, "message", None),
    )
    if safe is not None:
        return HTTPException(**safe)
    return unavailable()


def clean_key(value: str) -> str:
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


def _wire_object(value: Any, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise unavailable(_INVALID_RESULT_MESSAGE)
    return dict(value)


def _wire_page(value: Any, item_keys: frozenset[str]) -> dict[str, Any]:
    page = _wire_object(value, "page", _WIRE_PAGE_KEYS)
    if not isinstance(page["items"], list):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    page["items"] = [_wire_object(item, "page item", item_keys) for item in page["items"]]
    return page


def _wire_rule_detail(value: Any) -> dict[str, Any]:
    detail = _wire_object(
        value, "rule detail", frozenset({"rule", "current_revision", "recent_runs"})
    )
    detail["rule"] = _wire_object(detail["rule"], "rule", _WIRE_RULE_KEYS)
    if detail["current_revision"] is not None:
        detail["current_revision"] = _wire_object(
            detail["current_revision"], "current revision", _WIRE_REVISION_KEYS
        )
    if not isinstance(detail["recent_runs"], list):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    detail["recent_runs"] = [
        _wire_object(item, "recent run", _WIRE_RUN_KEYS) for item in detail["recent_runs"]
    ]
    return detail


def _wire_run_detail(value: Any) -> dict[str, Any]:
    detail = _wire_object(value, "run detail", frozenset({"run", "action_requests", "events"}))
    detail["run"] = _wire_object(detail["run"], "run", _WIRE_RUN_KEYS)
    if not isinstance(detail["action_requests"], list) or not isinstance(detail["events"], list):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    detail["action_requests"] = [
        _wire_object(item, "action request", _WIRE_ACTION_KEYS)
        for item in detail["action_requests"]
    ]
    detail["events"] = [_wire_object(item, "event", _WIRE_EVENT_KEYS) for item in detail["events"]]
    return detail


def _wire_mutation(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    raw = dict(value)
    if operation == "preview_automation_rule" and "matched" in raw:
        revision = raw.get("revision")
        revision_number = revision.get("revision") if isinstance(revision, Mapping) else None
        resource_id = raw.get("rule_id")
        if resource_id is None and isinstance(raw.get("rule"), Mapping):
            resource_id = raw["rule"].get("id")
        return {
            "state": "applied",
            "operation": "preview_rule",
            "resource_id": resource_id,
            "revision": revision_number,
            "message": None,
            "retryable": False,
        }
    if not {"state", "operation", "retryable"}.issubset(raw):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    state = raw["state"]
    operation_name = raw["operation"]
    retryable = raw["retryable"]
    if state not in _WIRE_MUTATION_STATES or not isinstance(operation_name, str):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    if not isinstance(retryable, bool):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    resource_id = raw.get("resource_id")
    if resource_id is not None and not isinstance(resource_id, str):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    revision = raw.get("revision")
    if isinstance(revision, Mapping):
        revision = revision.get("revision")
    if revision is not None and (type(revision) is not int or revision < 1):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    message = raw.get("message")
    if message is not None and (
        not isinstance(message, str)
        or not message
        or len(message) > 512
        or _CONTROL.search(message)
    ):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    return {
        "state": state,
        "operation": operation_name,
        "resource_id": resource_id,
        "revision": revision,
        "message": message,
        "retryable": retryable,
    }


def _wire_body(operation: str, value: Any) -> dict[str, Any]:
    if operation == "get_automation_summary":
        return _wire_object(value, "summary", _WIRE_SUMMARY_KEYS)
    if operation == "list_automation_rules":
        return _wire_page(value, _WIRE_RULE_KEYS)
    if operation == "get_automation_rule":
        return _wire_rule_detail(value)
    if operation == "list_automation_rule_revisions":
        return _wire_page(value, _WIRE_REVISION_KEYS)
    if operation == "list_automation_runs":
        return _wire_page(value, _WIRE_RUN_KEYS)
    if operation == "get_automation_run":
        return _wire_run_detail(value)
    if operation == "list_automation_action_requests":
        return _wire_page(value, _WIRE_ACTION_KEYS)
    if operation == "list_automation_events":
        return _wire_page(value, _WIRE_EVENT_KEYS)
    return _wire_mutation(value, operation)


def response(value: Any, *, operation: str | None = None) -> JSONResponse:
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
        else:
            raise unavailable(_INVALID_RESULT_MESSAGE)

    if status is None:
        status = 200
    if type(status) is not int or not 100 <= status <= 599 or (explicit_status and status == 204):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    try:
        encoded = jsonable_encoder(body)
    except Exception as exc:  # noqa: BLE001
        raise unavailable(_INVALID_RESULT_MESSAGE) from exc
    if not isinstance(encoded, Mapping):
        raise unavailable(_INVALID_RESULT_MESSAGE)
    wire = _wire_body(operation, encoded) if operation is not None else dict(encoded)
    return JSONResponse(wire, status_code=status)


def build_enterprise_automation_workflows_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    service: ServiceProvider | None = None,
    actor_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Build the strict, actor-bound Stage 25 automation workflows router."""

    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    if actor_dependency is not None and not callable(actor_dependency):
        raise TypeError("actor_dependency must be callable")

    actor_dep = actor_dependency or require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["enterprise-automation-workflows"])

    def read(operation: str, actor: KnowledgeActor, **kwargs: Any) -> JSONResponse:
        return response(
            service_call(
                load_service(service),
                operation,
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                **kwargs,
            ),
            operation=operation,
        )

    def mutate(operation: str, actor: KnowledgeActor, key: str, **kwargs: Any) -> JSONResponse:
        return response(
            service_call(
                load_service(service),
                operation,
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                account_id=actor.account_id,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                idempotency_key=clean_key(key),
                **kwargs,
            ),
            operation=operation,
        )

    @router.get("/api/enterprise/automations/summary")
    def summary(actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return read("get_automation_summary", actor)

    @router.get("/api/enterprise/automations/rules")
    def list_rules(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: RuleStatusFilter | None = Query(default=None),
    ) -> JSONResponse:
        return read("list_automation_rules", actor, cursor=cursor, limit=limit, status=status)

    @router.post("/api/enterprise/automations/rules")
    def create_rule(
        body: RuleCreateRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate("create_automation_rule", actor, key, **body.model_dump(exclude_none=True))

    @router.get("/api/enterprise/automations/rules/{rule_id}")
    def get_rule(rule_id: RuleId, actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return read("get_automation_rule", actor, rule_id=rule_id)

    @router.patch("/api/enterprise/automations/rules/{rule_id}")
    def patch_rule(
        rule_id: RuleId,
        body: RulePatchRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate(
            "update_automation_rule",
            actor,
            key,
            rule_id=rule_id,
            **body.model_dump(exclude_none=True),
        )

    @router.get("/api/enterprise/automations/rules/{rule_id}/revisions")
    def list_revisions(
        rule_id: RuleId,
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> JSONResponse:
        return read(
            "list_automation_rule_revisions",
            actor,
            rule_id=rule_id,
            cursor=cursor,
            limit=limit,
        )

    @router.post("/api/enterprise/automations/rules/{rule_id}/revisions")
    def create_revision(
        rule_id: RuleId,
        body: RuleRevisionCreateRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate(
            "create_automation_rule_revision",
            actor,
            key,
            rule_id=rule_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/automations/rules/{rule_id}/preview")
    def preview(
        rule_id: RuleId,
        body: PreviewRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate(
            "preview_automation_rule",
            actor,
            key,
            rule_id=rule_id,
            expected_revision=body.expected_revision,
            expected_definition_digest=body.expected_definition_digest,
            trigger_event=_bound_trigger_event(body.trigger_event, actor.tenant_id),
            reason=body.reason,
        )

    @router.post("/api/enterprise/automations/rules/{rule_id}/activate")
    def activate(
        rule_id: RuleId,
        body: ActivateRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate(
            "activate_automation_rule",
            actor,
            key,
            rule_id=rule_id,
            **body.model_dump(),
        )

    @router.post("/api/enterprise/automations/rules/{rule_id}/pause")
    def pause(
        rule_id: RuleId,
        body: RevisionFenceRequest,
        key: IdempotencyKey,
        actor: KnowledgeActor = Depends(actor_dep),
    ) -> JSONResponse:
        return mutate(
            "pause_automation_rule",
            actor,
            key,
            rule_id=rule_id,
            **body.model_dump(),
        )

    @router.get("/api/enterprise/automations/runs")
    def list_runs(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: AutomationRunStatus | None = Query(default=None),
        rule_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
    ) -> JSONResponse:
        return read(
            "list_automation_runs",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
            rule_id=rule_id,
        )

    @router.get("/api/enterprise/automations/runs/{run_id}")
    def get_run(run_id: RunId, actor: KnowledgeActor = Depends(actor_dep)) -> JSONResponse:
        return read("get_automation_run", actor, run_id=run_id)

    @router.get("/api/enterprise/automations/action-requests")
    def list_action_requests(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        status: AutomationActionRequestStatus | None = Query(default=None),
        rule_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        run_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
    ) -> JSONResponse:
        return read(
            "list_automation_action_requests",
            actor,
            cursor=cursor,
            limit=limit,
            status=status,
            rule_id=rule_id,
            run_id=run_id,
        )

    @router.get("/api/enterprise/automations/events")
    def list_events(
        actor: KnowledgeActor = Depends(actor_dep),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        limit: int = Query(default=50, ge=1, le=200),
        event_type: AutomationEventType | None = Query(default=None),
        rule_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
        run_id: str | None = Query(
            default=None, min_length=1, max_length=128, pattern=_SAFE_ID.pattern
        ),
    ) -> JSONResponse:
        return read(
            "list_automation_events",
            actor,
            cursor=cursor,
            limit=limit,
            event_type=event_type,
            rule_id=rule_id,
            run_id=run_id,
        )

    return router


build_enterprise_automation_workflows_api_router = build_enterprise_automation_workflows_router


__all__ = [
    "ActionPlanStep",
    "ActivateRequest",
    "PreviewRequest",
    "RevisionFenceRequest",
    "RuleCreateRequest",
    "RulePatchRequest",
    "RuleRevisionCreateRequest",
    "TriggerEventRequest",
    "build_enterprise_automation_workflows_api_router",
    "build_enterprise_automation_workflows_router",
    "clean_key",
    "load_service",
    "safe_error",
    "service_call",
    "unavailable",
]
