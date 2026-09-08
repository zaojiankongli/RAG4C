"""HTTP contract for the reflection-backed enterprise approval center."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.enterprise_approval_control import (
    ApprovalError,
    ApprovalExecutionAdapterNotConnected,
    cancel_approval_request,
    consume_approval_ticket,
    create_approval_policy,
    create_approval_request,
    decide_approval_request,
    disable_approval_policy,
    get_approval_policy,
    get_approval_request,
    list_approval_policies,
    list_approval_requests,
    update_approval_policy,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
)
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

EngineProvider = Callable[[], Any]
NowProvider = Callable[[], datetime]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
_DEDICATED_EXECUTION_ACTIONS = frozenset({"dataset_workspace_transfer", "document_purge"})


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApproverRequest(_StrictModel):
    kind: str = Field(min_length=1, max_length=16)
    ref: str = Field(min_length=1, max_length=128)


class PolicyCreateRequest(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    action_type: str = Field(min_length=1, max_length=64)
    resource_scope: str | None = Field(default=None, max_length=512)
    required_approvals: int = Field(ge=1, le=5)
    request_expiry_minutes: int = Field(ge=15, le=10080)
    approvers: list[ApproverRequest] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=512)


class PolicyUpdateRequest(_StrictModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    resource_scope: str | None = Field(default=None, max_length=512)
    required_approvals: int | None = Field(default=None, ge=1, le=5)
    request_expiry_minutes: int | None = Field(default=None, ge=15, le=10080)
    approvers: list[ApproverRequest] | None = Field(default=None, min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=512)


class RevisionReasonRequest(_StrictModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


class ApprovalRequestCreate(_StrictModel):
    policy_id: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=256)
    snapshot: dict[str, Any]
    reason: str = Field(min_length=1, max_length=512)


class DecisionRequest(_StrictModel):
    revision: int = Field(ge=1)
    comment: str = Field(default="", max_length=500)


class TicketConsumeRequest(_StrictModel):
    ticket: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=1)
    action_type: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=256)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApprovalError):
        return HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        )
    if isinstance(exc, TenantMutationIdempotencyConflict):
        return HTTPException(
            status_code=409,
            detail={
                "code": "approval_idempotency_conflict",
                "message": "Idempotency-Key 已用于不同的审批请求",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyInProgress):
        return HTTPException(
            status_code=409,
            detail={
                "code": "approval_idempotency_in_progress",
                "message": "同一审批操作正在处理中",
            },
        )
    if isinstance(exc, TenantMutationIdempotencyValidationError):
        return HTTPException(
            status_code=422, detail={"code": "approval_request_invalid", "message": str(exc)}
        )
    return HTTPException(
        status_code=503, detail={"code": "approval_unavailable", "message": "企业审批服务暂不可用"}
    )


def _as_approvers(items: list[ApproverRequest] | None) -> list[dict[str, str]] | None:
    return [item.model_dump() for item in items] if items is not None else None


def _adapter_descriptor(
    action_type: Any,
    *,
    adapters: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    legacy_adapter: Callable[[Mapping[str, Any]], Any] | None,
) -> dict[str, Any]:
    action = str(action_type or "").strip()
    connected = action in adapters or (
        legacy_adapter is not None and action not in _DEDICATED_EXECUTION_ACTIONS
    )
    return {
        "action_type": action,
        "state": "connected" if connected else "not_connected",
        "connected": connected,
    }


def _decorate_read_payload(
    body: Mapping[str, Any],
    *,
    adapters: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    legacy_adapter: Callable[[Mapping[str, Any]], Any] | None,
) -> dict[str, Any]:
    """Expose adapter evidence without claiming unsupported actions are live."""

    result = dict(body)
    result["connected_actions"] = sorted(adapters)
    for key in ("policy", "request"):
        item = result.get(key)
        if isinstance(item, Mapping) and "action_type" in item:
            decorated = dict(item)
            descriptor = _adapter_descriptor(
                item.get("action_type"), adapters=adapters, legacy_adapter=legacy_adapter
            )
            decorated["execution_adapter_status"] = (
                "connected" if descriptor["connected"] else "execution_adapter_not_connected"
            )
            decorated["execution_adapter"] = decorated["execution_adapter_status"]
            decorated["execution_adapter_details"] = descriptor
            result[key] = decorated
    request_item = result.get("request")
    if isinstance(request_item, Mapping) and "action_type" in request_item:
        descriptor = _adapter_descriptor(
            request_item.get("action_type"),
            adapters=adapters,
            legacy_adapter=legacy_adapter,
        )
        status = "connected" if descriptor["connected"] else "execution_adapter_not_connected"
        execution = result.get("execution")
        decorated_execution = dict(execution) if isinstance(execution, Mapping) else {}
        decorated_execution["adapter"] = status
        decorated_execution["adapter_state"] = descriptor
        result["execution"] = decorated_execution
    items = result.get("items")
    if isinstance(items, list):
        decorated_items: list[Any] = []
        for item in items:
            if isinstance(item, Mapping) and "action_type" in item:
                decorated_item = dict(item)
                descriptor = _adapter_descriptor(
                    item.get("action_type"),
                    adapters=adapters,
                    legacy_adapter=legacy_adapter,
                )
                decorated_item["execution_adapter_status"] = (
                    "connected" if descriptor["connected"] else "execution_adapter_not_connected"
                )
                decorated_item["execution_adapter"] = decorated_item["execution_adapter_status"]
                decorated_item["execution_adapter_details"] = descriptor
                decorated_items.append(decorated_item)
            else:
                decorated_items.append(item)
        result["items"] = decorated_items
    return result


def _decorate_mutation_payload(
    body: Mapping[str, Any],
    *,
    adapters: Mapping[str, Callable[[Mapping[str, Any]], Any]],
    legacy_adapter: Callable[[Mapping[str, Any]], Any] | None,
) -> dict[str, Any]:
    result = dict(body)
    request = result.get("request")
    if isinstance(request, Mapping) and "action_type" in request:
        descriptor = _adapter_descriptor(
            request.get("action_type"), adapters=adapters, legacy_adapter=legacy_adapter
        )
        status = "connected" if descriptor["connected"] else "execution_adapter_not_connected"
        decorated_request = dict(request)
        decorated_request["execution_adapter_status"] = status
        decorated_request["execution_adapter"] = status
        result["request"] = decorated_request
        execution = result.get("execution")
        if isinstance(execution, Mapping):
            decorated_execution = dict(execution)
            decorated_execution["adapter"] = status
            decorated_execution["adapter_state"] = descriptor
            result["execution"] = decorated_execution
    result["connected_actions"] = sorted(adapters)
    return result


def build_enterprise_approval_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    now_provider: NowProvider | None = None,
    execution_adapters: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    execution_adapter: Callable[[dict[str, Any]], Any] | None = None,
) -> APIRouter:
    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    clock = now_provider or datetime.utcnow
    require_actor = require_knowledge_permission(KNOWLEDGE_READ)
    adapters: dict[str, Callable[[Mapping[str, Any]], Any]] = {}
    for raw_action, adapter in (execution_adapters or {}).items():
        action = str(raw_action).strip()
        if not action or not callable(adapter):
            raise TypeError("execution_adapters must map non-empty actions to callables")
        adapters[action] = adapter
    if execution_adapter is not None and not callable(execution_adapter):
        raise TypeError("execution_adapter must be callable")
    router = APIRouter(tags=["enterprise-approval"])

    @router.get("/api/enterprise/approvals/policies")
    def policies(
        actor: KnowledgeActor = Depends(require_actor),
        status: str | None = Query(default=None, max_length=16),
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            result = list_approval_policies(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                status=status,
                cursor=cursor,
                limit=limit,
            )
            return _decorate_read_payload(
                result.body, adapters=adapters, legacy_adapter=execution_adapter
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/approvals/policies/{policy_id}")
    def policy(policy_id: str, actor: KnowledgeActor = Depends(require_actor)) -> dict[str, Any]:
        try:
            result = get_approval_policy(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                policy_id=policy_id,
            )
            return _decorate_read_payload(
                result.body, adapters=adapters, legacy_adapter=execution_adapter
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/approvals/policies", status_code=201)
    def post_policy(
        body: PolicyCreateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            result = create_approval_policy(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                name=body.name,
                action_type=body.action_type,
                resource_scope=body.resource_scope,
                required_approvals=body.required_approvals,
                request_expiry_minutes=body.request_expiry_minutes,
                approvers=_as_approvers(body.approvers) or [],
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_read_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.patch("/api/enterprise/approvals/policies/{policy_id}")
    def patch_policy(
        policy_id: str,
        body: PolicyUpdateRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            result = update_approval_policy(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                policy_id=policy_id,
                expected_revision=body.revision,
                name=body.name,
                resource_scope=body.resource_scope,
                required_approvals=body.required_approvals,
                request_expiry_minutes=body.request_expiry_minutes,
                approvers=_as_approvers(body.approvers),
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_read_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/approvals/policies/{policy_id}/disable")
    def post_disable(
        policy_id: str,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            result = disable_approval_policy(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                policy_id=policy_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_mutation_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/approvals/requests")
    def requests(
        actor: KnowledgeActor = Depends(require_actor),
        status: str | None = Query(default=None, max_length=32),
        action_type: str | None = Query(default=None, max_length=64),
        mine: bool = Query(default=False),
        pending_for_me: bool = Query(default=False),
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            result = list_approval_requests(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                status=status,
                action_type=action_type,
                mine=mine,
                pending_for_me=pending_for_me,
                cursor=cursor,
                limit=limit,
                now=clock(),
            )
            return _decorate_read_payload(
                result.body, adapters=adapters, legacy_adapter=execution_adapter
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.get("/api/enterprise/approvals/requests/{request_id}")
    def request_detail(
        request_id: str, actor: KnowledgeActor = Depends(require_actor)
    ) -> dict[str, Any]:
        try:
            result = get_approval_request(
                read_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=request_id,
                now=clock(),
            )
            return _decorate_read_payload(
                result.body, adapters=adapters, legacy_adapter=execution_adapter
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/approvals/requests", status_code=201)
    def post_request(
        body: ApprovalRequestCreate,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            result = create_approval_request(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                policy_id=body.policy_id,
                resource_type=body.resource_type,
                resource_id=body.resource_id,
                snapshot=body.snapshot,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_mutation_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    def _decision(
        request_id: str, body: DecisionRequest, key: str, actor: KnowledgeActor, decision: str
    ) -> JSONResponse:
        try:
            result = decide_approval_request(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=request_id,
                decision=decision,
                expected_revision=body.revision,
                comment=body.comment,
                idempotency_key=key,
                request_id_header=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_mutation_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/approvals/requests/{request_id}/approve")
    def approve(
        request_id: str,
        body: DecisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return _decision(request_id, body, idempotency_key, actor, "approved")

    @router.post("/api/enterprise/approvals/requests/{request_id}/reject")
    def reject(
        request_id: str,
        body: DecisionRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        return _decision(request_id, body, idempotency_key, actor, "rejected")

    @router.post("/api/enterprise/approvals/requests/{request_id}/cancel")
    def cancel(
        request_id: str,
        body: RevisionReasonRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            result = cancel_approval_request(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=request_id,
                expected_revision=body.revision,
                reason=body.reason,
                idempotency_key=idempotency_key,
                request_id_header=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
            )
            return JSONResponse(
                _decorate_mutation_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/approvals/requests/{request_id}/consume-ticket")
    def consume(
        request_id: str,
        body: TicketConsumeRequest,
        idempotency_key: IdempotencyKey,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            action = body.action_type.strip()
            if action in {
                "dataset_acl_disable",
                "member_role_change",
                "workspace_authorization_mode_change",
                "dataset_workspace_transfer",
            } and actor.role not in {
                "owner",
                "admin",
            }:
                action_label = (
                    "Dataset ACL"
                    if action == "dataset_acl_disable"
                    else "成员角色变更"
                    if action == "member_role_change"
                    else "Knowledge Base Workspace 转移"
                    if action == "dataset_workspace_transfer"
                    else "Workspace 授权模式变更"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "approval_execution_forbidden",
                        "message": f"当前身份不能消费 {action_label} 执行授权",
                    },
                )
            adapter = adapters.get(action)
            if adapter is None and action not in _DEDICATED_EXECUTION_ACTIONS:
                adapter = execution_adapter
            if adapter is None:
                # This check intentionally happens before entering the core
                # claim transaction.  Unsupported actions cannot consume a
                # one-time ticket merely because the request was approved.
                raise ApprovalExecutionAdapterNotConnected()
            result = consume_approval_ticket(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                request_id=request_id,
                ticket=body.ticket,
                expected_revision=body.revision,
                action_type=body.action_type,
                resource_type=body.resource_type,
                resource_id=body.resource_id,
                idempotency_key=idempotency_key,
                request_id_header=actor.request_id,
                request_ip=actor.request_ip,
                now=clock(),
                execution_adapter=adapter,
            )
            return JSONResponse(
                _decorate_mutation_payload(
                    result.body, adapters=adapters, legacy_adapter=execution_adapter
                ),
                status_code=result.status,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    return router


__all__ = ["build_enterprise_approval_router"]
