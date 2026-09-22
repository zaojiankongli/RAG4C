"""Tenant-safe Stage 25 Automation Workflow service.

This module is the durable orchestration boundary for the pure automation
authority in core.enterprise_automation_workflows. It deliberately creates
only bounded automation facts: immutable rule revisions, source cursors,
evaluation runs, requested action rows, and hash-chained events. It never
dispatches a notification, approval, task operation, source sync, or external
request.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_automation_workflows import (
    AUTOMATION_ACTION_CODES,
    AUTOMATION_CONDITION_CODES,
    AUTOMATION_EVENT_TYPES,
    AUTOMATION_TRIGGER_CODES,
    AutomationAuthorityError,
    canonical_automation_action_request_digest,
    canonical_automation_event,
    canonical_automation_run_digest,
    canonical_automation_rule_revision,
    canonical_automation_trigger_event,
    evaluate_automation_condition,
    preview_automation_rule as preview_automation_rule_pure,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_request_hash,
)
from models.orm import (
    Account,
    Dataset,
    DatasetReleaseQualityAlert,
    DatasetReleaseRecertificationJob,
    SourceSyncRun,
    TenantApprovalRequest,
    TenantAutomationActionRequest,
    TenantAutomationEvent,
    TenantAutomationRule,
    TenantAutomationRuleRevision,
    TenantAutomationRun,
    TenantAutomationSourceCursor,
    TenantMember,
    TenantTaskProjection,
    TenantWorkspace,
)

UTC = timezone.utc
# 触发器顺序以前是这里的一份手写 tuple，与文件末尾的 TRIGGER_ADAPTER_REGISTRY 平行维护
# —— 两件事必须同时改且改得一样，而唯一在核对它的是一份测试里的第三份副本。现在注册表
# 是唯一声明，`TRIGGER_ADAPTER_ORDER` 由它在定义处派生。

TriggerAdapter = Callable[..., Iterable[Mapping[str, Any]]]


class EnterpriseAutomationWorkflowsError(RuntimeError):
    """Base class for service errors exposed by the API boundary."""

    code = "enterprise_automation_workflows_error"
    status = 500

    def __init__(self, message: str = "Enterprise Automation Workflows failed") -> None:
        super().__init__(message)
        self.message = message


class EnterpriseAutomationWorkflowsUnavailable(EnterpriseAutomationWorkflowsError):
    code = "enterprise_automation_workflows_unavailable"
    status = 503


class EnterpriseAutomationWorkflowsInvalid(EnterpriseAutomationWorkflowsError):
    code = "enterprise_automation_workflows_invalid"
    status = 422


class EnterpriseAutomationWorkflowsNotFound(EnterpriseAutomationWorkflowsError):
    code = "enterprise_automation_workflows_not_found"
    status = 404


class EnterpriseAutomationWorkflowsConflict(EnterpriseAutomationWorkflowsError):
    code = "enterprise_automation_workflows_conflict"
    status = 409


class EnterpriseAutomationWorkflowsBlocked(EnterpriseAutomationWorkflowsError):
    code = "enterprise_automation_workflows_blocked"
    status = 409


AutomationWorkflowsError = EnterpriseAutomationWorkflowsError
AutomationWorkflowsUnavailable = EnterpriseAutomationWorkflowsUnavailable
AutomationWorkflowsInvalid = EnterpriseAutomationWorkflowsInvalid
AutomationWorkflowsNotFound = EnterpriseAutomationWorkflowsNotFound
AutomationWorkflowsConflict = EnterpriseAutomationWorkflowsConflict
AutomationWorkflowsBlocked = EnterpriseAutomationWorkflowsBlocked


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|(?:^|\s)www\.)\S+")
_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|access[_ -]?token|"
    r"refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|ticket|token)\s*[:=]\s*\S+"
)
_CURSOR_MAX = 2048
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_CURSOR_LEASE = timedelta(seconds=30)
_ACTION_EXPIRY = timedelta(hours=24)


def _id(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be a string")
    result = value.strip()
    if (
        not result
        or len(result) > maximum
        or _CONTROL_RE.search(result)
        or _ID_RE.fullmatch(result) is None
        or ".." in result
    ):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} is invalid")
    return result


def _safe_text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be a string")
    result = value.strip()
    if not result or len(result) > maximum or _CONTROL_RE.search(result):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} is invalid")
    if _URL_RE.search(result) or _SECRET_RE.search(result):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} is unsafe")
    return result


def _code(value: Any, field: str, allowed: frozenset[str] | None = None) -> str:
    result = _safe_text(value, field, 128).casefold().replace("-", "_").replace(" ", "_")
    if _CODE_RE.fullmatch(result) is None or (allowed is not None and result not in allowed):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} is not allowed")
    return result


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be an exact integer")
    return value


def _as_datetime(value: Any, field: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise EnterpriseAutomationWorkflowsInvalid(f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.replace(tzinfo=None)


def _now(value: Any = None) -> datetime:
    return (
        _as_datetime(value, "now") if value is not None else datetime.now(UTC).replace(tzinfo=None)
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    parsed = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normal_name(value: Any) -> tuple[str, str]:
    name = _safe_text(value, "name", 128)
    normalized = " ".join(name.split()).casefold()
    if not normalized or len(normalized) > 128:
        raise EnterpriseAutomationWorkflowsInvalid("name is invalid")
    return name, normalized


def _key(value: Any) -> str:
    return _safe_text(value, "idempotency_key", 128)


def _request_id(value: Any, fallback: str) -> str:
    if value is None:
        return _id(fallback[:64], "request_id", 64)
    return _id(value, "request_id", 64)


def _validate_actor_account(account_id: Any, actor_id: str) -> None:
    if account_id is not None and _id(account_id, "account_id", 64) != actor_id:
        raise EnterpriseAutomationWorkflowsInvalid("account_id must match actor_id")


def _validate_limit(value: Any) -> int:
    return _integer(value, "limit", 1, _MAX_LIMIT)


def _validate_trigger_codes(value: Any) -> tuple[str, ...]:
    # 两处都直接读注册表而不是 TRIGGER_ADAPTER_ORDER：后者是导入期的快照，留着它当权威
    # 就会让"运行时注册一个适配器"只生效一半（校验看得到、顺序看不到）。
    adapters = TRIGGER_ADAPTER_REGISTRY
    if value is None:
        return tuple(adapters)
    if not isinstance(value, (list, tuple)) or not value:
        raise EnterpriseAutomationWorkflowsInvalid("trigger_codes must be a non-empty list")
    result: list[str] = []
    for index, item in enumerate(value):
        code = _code(item, f"trigger_codes[{index}]", AUTOMATION_TRIGGER_CODES)
        if code in result:
            raise EnterpriseAutomationWorkflowsInvalid("trigger_codes must not contain duplicates")
        result.append(code)
    return tuple(code for code in adapters if code in result)


def _stable_id(prefix: str, *parts: str) -> str:
    encoded = "\x00".join(parts).encode("utf-8")
    return f"{prefix}-{sha256(encoded).hexdigest()[:40]}"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _cursor_encode(kind: str, values: Mapping[str, Any]) -> str:
    payload = {"version": 1, "kind": kind, "values": dict(values)}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    result = urlsafe_b64encode(encoded).decode("ascii")
    if len(result) > _CURSOR_MAX:
        raise EnterpriseAutomationWorkflowsInvalid("cursor is too large")
    return result


def _cursor_decode(value: Any, expected_kind: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _CURSOR_MAX:
        raise EnterpriseAutomationWorkflowsInvalid("cursor is invalid")
    try:
        decoded = urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise EnterpriseAutomationWorkflowsInvalid("cursor is invalid") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("version") != 1
        or payload.get("kind") != expected_kind
        or not isinstance(payload.get("values"), Mapping)
    ):
        raise EnterpriseAutomationWorkflowsInvalid("cursor is invalid")
    return dict(payload["values"])


def _scope_actor(session: Session, tenant_id: str, actor_id: str) -> TenantMember:
    member = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.account_id == actor_id,
            TenantMember.status == "active",
        )
    )
    account = session.scalar(select(Account).where(Account.id == actor_id))
    if member is None or account is None:
        raise EnterpriseAutomationWorkflowsUnavailable("active Tenant actor is unavailable")
    return member


def _require_manager(member: TenantMember) -> None:
    if str(member.role) not in {"owner", "admin", "editor"}:
        raise EnterpriseAutomationWorkflowsBlocked(
            "actor cannot manage Enterprise Automation Workflows"
        )


def _validate_resource_scope(
    session: Session,
    tenant_id: str,
    workspace_id: str | None,
    dataset_id: str | None,
) -> None:
    if workspace_id is not None:
        workspace = session.scalar(
            select(TenantWorkspace).where(
                TenantWorkspace.tenant_id == tenant_id,
                TenantWorkspace.id == workspace_id,
            )
        )
        if workspace is None:
            raise EnterpriseAutomationWorkflowsNotFound("workspace was not found in Tenant scope")
    if dataset_id is not None:
        dataset = session.scalar(
            select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
        )
        if dataset is None:
            raise EnterpriseAutomationWorkflowsNotFound("dataset was not found in Tenant scope")


def _revision_body(row: TenantAutomationRuleRevision) -> dict[str, Any]:
    condition_params = dict(row.condition_params_json or {})
    action_plan = [dict(item) for item in (row.action_plan_json or [])]
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "rule_id": str(row.rule_id),
        "revision": int(row.revision),
        "trigger_code": str(row.trigger_code),
        "condition_code": str(row.condition_code),
        "condition_params_json": condition_params,
        "action_plan_json": action_plan,
        "definition_digest": str(row.definition_digest),
        "created_at": _iso(row.created_at),
        "created_by": str(row.created_by),
    }


def _action_plan_input(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise EnterpriseAutomationWorkflowsUnavailable("automation action plan is unavailable")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise EnterpriseAutomationWorkflowsUnavailable("automation action plan is unavailable")
        result.append({"action_code": item.get("action_code"), "params": item.get("params", {})})
    return result


def _revision_authority_body(row: TenantAutomationRuleRevision) -> dict[str, Any]:
    body = _canonical_revision(row)
    body["action_plan"] = _action_plan_input(body["action_plan"])
    return body


def _canonical_revision(row: TenantAutomationRuleRevision) -> dict[str, Any]:
    try:
        return canonical_automation_rule_revision(
            tenant_id=str(row.tenant_id),
            rule_id=str(row.rule_id),
            rule_revision_id=str(row.id),
            revision=int(row.revision),
            trigger_code=str(row.trigger_code),
            condition_code=str(row.condition_code),
            condition_params=dict(row.condition_params_json or {}),
            action_plan=[
                {key: value for key, value in dict(item).items() if key != "step_index"}
                for item in (row.action_plan_json or [])
            ],
            created_at=row.created_at,
            created_by=str(row.created_by),
            definition_digest=str(row.definition_digest),
        )
    except AutomationAuthorityError as exc:
        raise EnterpriseAutomationWorkflowsUnavailable(
            "automation rule revision digest or definition is unavailable"
        ) from exc


def _rule_body(
    row: TenantAutomationRule,
    current_revision: TenantAutomationRuleRevision | None = None,
) -> dict[str, Any]:
    del current_revision
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "name": str(row.name),
        "status": str(row.status),
        "revision": int(row.revision),
        "current_revision_id": (
            str(row.current_revision_id) if row.current_revision_id is not None else None
        ),
        "workspace_id": str(row.workspace_id) if row.workspace_id is not None else None,
        "dataset_id": str(row.dataset_id) if row.dataset_id is not None else None,
        "priority": int(row.priority),
        "created_at": _iso(row.created_at),
        "created_by": str(row.created_by),
        "updated_at": _iso(row.updated_at),
        "updated_by": str(row.updated_by),
        "archived_at": _iso(row.archived_at),
    }


def _run_body(row: TenantAutomationRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "rule_id": str(row.rule_id),
        "rule_revision_id": str(row.rule_revision_id),
        "trigger_event_id": str(row.trigger_event_id),
        "trigger_event_digest": str(row.trigger_event_digest),
        "status": str(row.status),
        "condition_matched": bool(row.condition_matched),
        "action_count": int(row.action_count),
        "requested_count": int(row.requested_count),
        "rejected_count": int(row.rejected_count),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "safe_error_code": str(row.safe_error_code) if row.safe_error_code is not None else None,
        "safe_error": str(row.safe_error) if row.safe_error is not None else None,
    }


def _action_body(row: TenantAutomationActionRequest) -> dict[str, Any]:
    params = dict(row.safe_params_json or {})
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "run_id": str(row.run_id),
        "rule_id": str(row.rule_id),
        "step_index": int(row.step_index),
        "action_code": str(row.action_code),
        "status": str(row.status),
        "target_kind": str(row.target_kind) if row.target_kind is not None else None,
        "target_id": str(row.target_id) if row.target_id is not None else None,
        "target_revision": (int(row.target_revision) if row.target_revision is not None else None),
        "target_digest": str(row.target_digest) if row.target_digest is not None else None,
        "safe_params_json": params,
        "safe_reason": str(row.safe_reason),
        "approval_request_id": (
            str(row.approval_request_id) if row.approval_request_id is not None else None
        ),
        "notification_id": str(row.notification_id) if row.notification_id is not None else None,
        "task_id": str(row.task_id) if row.task_id is not None else None,
        "requested_at": _iso(row.requested_at),
        "dispatched_at": _iso(row.dispatched_at),
        "applied_at": _iso(row.applied_at),
        "rejected_at": _iso(row.rejected_at),
        "expires_at": _iso(row.expires_at),
    }


def _event_body(row: TenantAutomationEvent) -> dict[str, Any]:
    snapshot = dict(row.safe_snapshot_json or {})
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "rule_id": str(row.rule_id),
        "run_id": str(row.run_id) if row.run_id is not None else None,
        "stream_key": str(row.stream_key),
        "sequence": int(row.sequence),
        "event_type": str(row.event_type),
        "previous_event_digest": (
            str(row.previous_event_digest) if row.previous_event_digest is not None else None
        ),
        "event_digest": str(row.event_digest),
        "actor_id": str(row.actor_id),
        "request_id": str(row.request_id),
        "safe_snapshot_json": snapshot,
        "occurred_at": _iso(row.occurred_at),
    }


def _page(items: list[dict[str, Any]], next_cursor: str | None, invalid: int = 0) -> ServiceResult:
    return ServiceResult(
        {"items": items, "next_cursor": next_cursor, "invalid_item_count": int(invalid)}
    )


def _mutation_outcome(
    *,
    state: str,
    operation: str,
    resource_id: str | None,
    message: str | None = None,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "state": state,
        "operation": operation,
        "resource_id": resource_id,
        "message": message,
        "retryable": bool(retryable),
    }
    result.update(extra)
    return result


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    operation: str,
    resource_type: str,
    path_identity: Mapping[str, Any],
    request_body: Mapping[str, Any],
) -> tuple[Any, ServiceResult | None]:
    try:
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={str(key): str(value) for key, value in path_identity.items()},
            body=request_body,
        )
        reservation = reserve_tenant_mutation(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            raw_idempotency_key=idempotency_key,
            request_hash=request_hash,
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise EnterpriseAutomationWorkflowsConflict(
            "idempotency key was used for a different request"
        ) from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise EnterpriseAutomationWorkflowsConflict("request is already in progress") from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise EnterpriseAutomationWorkflowsInvalid(str(exc)) from exc
    if reservation.replay is not None:
        return reservation, ServiceResult(
            dict(reservation.replay.response), int(reservation.replay.http_status)
        )
    return reservation, None


def _complete(
    session: Session,
    reservation: Any,
    response: Mapping[str, Any],
    resource_id: str | None,
    status: int = 200,
) -> ServiceResult:
    try:
        complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=dict(response),
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise EnterpriseAutomationWorkflowsInvalid(str(exc)) from exc
    return ServiceResult(dict(response), status)


def _append_event(
    session: Session,
    *,
    tenant_id: str,
    rule_id: str,
    run_id: str | None,
    stream_key: str,
    event_type: str,
    actor_id: str,
    request_id: str,
    safe_snapshot: Mapping[str, Any],
    occurred_at: datetime,
) -> TenantAutomationEvent:
    last = session.scalar(
        select(TenantAutomationEvent)
        .where(
            TenantAutomationEvent.tenant_id == tenant_id,
            TenantAutomationEvent.stream_key == stream_key,
        )
        .order_by(TenantAutomationEvent.sequence.desc())
        .limit(1)
    )
    sequence = int(last.sequence) + 1 if last is not None else 1
    previous = str(last.event_digest) if last is not None else None
    try:
        canonical = canonical_automation_event(
            tenant_id=tenant_id,
            rule_id=rule_id,
            run_id=run_id,
            stream_key=stream_key,
            sequence=sequence,
            event_type=event_type,
            previous_event_digest=previous,
            actor_id=actor_id,
            request_id=request_id,
            safe_snapshot=dict(safe_snapshot),
            occurred_at=occurred_at,
        )
    except AutomationAuthorityError as exc:
        raise EnterpriseAutomationWorkflowsInvalid("automation event snapshot is unsafe") from exc
    row = TenantAutomationEvent(
        id=_new_id("automation-event"),
        tenant_id=tenant_id,
        rule_id=rule_id,
        run_id=run_id,
        stream_key=stream_key,
        sequence=sequence,
        event_type=canonical["event_type"],
        previous_event_digest=canonical["previous_event_digest"],
        event_digest=canonical["event_digest"],
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot_json=canonical["safe_snapshot"],
        occurred_at=occurred_at,
    )
    session.add(row)
    session.flush()
    return row


def _validate_event_chain(events: Iterable[TenantAutomationEvent]) -> None:
    previous: str | None = None
    expected_sequence = 1
    for event in events:
        if int(event.sequence) != expected_sequence or event.previous_event_digest != previous:
            raise EnterpriseAutomationWorkflowsUnavailable("automation event chain is unavailable")
        try:
            canonical = canonical_automation_event(
                tenant_id=str(event.tenant_id),
                rule_id=str(event.rule_id),
                run_id=str(event.run_id) if event.run_id is not None else None,
                stream_key=str(event.stream_key),
                sequence=int(event.sequence),
                event_type=str(event.event_type),
                previous_event_digest=(
                    str(event.previous_event_digest)
                    if event.previous_event_digest is not None
                    else None
                ),
                event_digest=str(event.event_digest),
                actor_id=str(event.actor_id),
                request_id=str(event.request_id),
                safe_snapshot=dict(event.safe_snapshot_json or {}),
                occurred_at=event.occurred_at,
            )
        except AutomationAuthorityError as exc:
            raise EnterpriseAutomationWorkflowsUnavailable(
                "automation event chain is unavailable"
            ) from exc
        if canonical["event_digest"] != str(event.event_digest):
            raise EnterpriseAutomationWorkflowsUnavailable("automation event chain is unavailable")
        previous = str(event.event_digest)
        expected_sequence += 1


def _get_rule(
    session: Session, tenant_id: str, rule_id: str, *, lock: bool = False
) -> TenantAutomationRule:
    statement = select(TenantAutomationRule).where(
        TenantAutomationRule.tenant_id == tenant_id,
        TenantAutomationRule.id == rule_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise EnterpriseAutomationWorkflowsNotFound("automation rule was not found")
    return row


def _get_revision(
    session: Session,
    tenant_id: str,
    rule_id: str,
    revision_id: str,
    *,
    lock: bool = False,
) -> TenantAutomationRuleRevision:
    statement = select(TenantAutomationRuleRevision).where(
        TenantAutomationRuleRevision.tenant_id == tenant_id,
        TenantAutomationRuleRevision.rule_id == rule_id,
        TenantAutomationRuleRevision.id == revision_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise EnterpriseAutomationWorkflowsNotFound("automation rule revision was not found")
    _canonical_revision(row)
    return row


def get_automation_summary(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rules = list(
            session.scalars(
                select(TenantAutomationRule).where(TenantAutomationRule.tenant_id == tenant)
            )
        )
        runs = list(
            session.scalars(
                select(TenantAutomationRun).where(TenantAutomationRun.tenant_id == tenant)
            )
        )
        actions = list(
            session.scalars(
                select(TenantAutomationActionRequest).where(
                    TenantAutomationActionRequest.tenant_id == tenant
                )
            )
        )
        rule_counts = {status: 0 for status in ("draft", "active", "paused", "archived")}
        for row in rules:
            if str(row.status) in rule_counts:
                rule_counts[str(row.status)] += 1
        run_statuses = ("started", "not_matched", "requested", "completed", "failed", "blocked")
        run_counts = {
            status: sum(1 for row in runs if str(row.status) == status) for status in run_statuses
        }
        requested_actions = sum(1 for row in actions if str(row.status) == "requested")
        return ServiceResult(
            {
                "tenant_id": tenant,
                "state": "ready",
                "active_rule_count": rule_counts["active"],
                "paused_rule_count": rule_counts["paused"],
                "failed_run_count": run_counts["failed"],
                "pending_request_count": requested_actions,
                "as_of": _iso(moment),
                "reason_code": None,
            }
        )


def list_automation_rules(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    status: str | None = None,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    page_size = _validate_limit(limit)
    _validate_actor_account(account_id, actor)
    status_value = None if status is None or status == "all" else _code(status, "status")
    if status_value is not None and status_value not in {"draft", "active", "paused", "archived"}:
        raise EnterpriseAutomationWorkflowsInvalid("status is invalid")
    workspace = None if workspace_id is None else _id(workspace_id, "workspace_id", 64)
    dataset = None if dataset_id is None else _id(dataset_id, "dataset_id", 64)
    decoded = _cursor_decode(cursor, "automation-rules")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantAutomationRule).where(TenantAutomationRule.tenant_id == tenant)
        if status_value is not None:
            query = query.where(TenantAutomationRule.status == status_value)
        if workspace is not None:
            query = query.where(TenantAutomationRule.workspace_id == workspace)
        if dataset is not None:
            query = query.where(TenantAutomationRule.dataset_id == dataset)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("updated_at"), "cursor.updated_at")
            row_id = _id(decoded.get("id"), "cursor.id", 64)
            query = query.where(
                or_(
                    TenantAutomationRule.updated_at < stamp,
                    and_(
                        TenantAutomationRule.updated_at == stamp,
                        TenantAutomationRule.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantAutomationRule.updated_at.desc(), TenantAutomationRule.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        items = [_rule_body(row) for row in rows]
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "automation-rules",
                {"updated_at": _iso(rows[-1].updated_at), "id": str(rows[-1].id)},
            )
        return _page(items, next_cursor)


def get_automation_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    _validate_actor_account(account_id, actor)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rule = _get_rule(session, tenant, identifier)
        revision = None
        if rule.current_revision_id is not None:
            revision = _get_revision(session, tenant, identifier, str(rule.current_revision_id))
        events = list(
            session.scalars(
                select(TenantAutomationEvent)
                .where(
                    TenantAutomationEvent.tenant_id == tenant,
                    TenantAutomationEvent.rule_id == identifier,
                    TenantAutomationEvent.stream_key == f"rule:{identifier}",
                )
                .order_by(TenantAutomationEvent.sequence.asc())
            )
        )
        if events:
            _validate_event_chain(events)
        recent_runs = list(
            session.scalars(
                select(TenantAutomationRun)
                .where(
                    TenantAutomationRun.tenant_id == tenant,
                    TenantAutomationRun.rule_id == identifier,
                )
                .order_by(TenantAutomationRun.started_at.desc(), TenantAutomationRun.id.desc())
                .limit(20)
            )
        )
        return ServiceResult(
            {
                "rule": _rule_body(rule),
                "current_revision": _revision_body(revision) if revision is not None else None,
                "recent_runs": [_run_body(row) for row in recent_runs],
            }
        )


def list_rule_revisions(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    page_size = _validate_limit(limit)
    _validate_actor_account(account_id, actor)
    decoded = _cursor_decode(cursor, "automation-revisions")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        _get_rule(session, tenant, identifier)
        query = select(TenantAutomationRuleRevision).where(
            TenantAutomationRuleRevision.tenant_id == tenant,
            TenantAutomationRuleRevision.rule_id == identifier,
        )
        if decoded is not None:
            revision = _integer(decoded.get("revision"), "cursor.revision", 1)
            revision_id = _id(decoded.get("id"), "cursor.id", 64)
            query = query.where(
                or_(
                    TenantAutomationRuleRevision.revision < revision,
                    and_(
                        TenantAutomationRuleRevision.revision == revision,
                        TenantAutomationRuleRevision.id < revision_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantAutomationRuleRevision.revision.desc(),
                    TenantAutomationRuleRevision.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        for row in rows:
            _canonical_revision(row)
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "automation-revisions",
                {"revision": int(rows[-1].revision), "id": str(rows[-1].id)},
            )
        return _page([_revision_body(row) for row in rows], next_cursor)


def list_automation_runs(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    rule_id: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    page_size = _validate_limit(limit)
    _validate_actor_account(account_id, actor)
    rule = None if rule_id is None else _id(rule_id, "rule_id", 64)
    status_value = None if status is None else _code(status, "status")
    allowed_statuses = {"started", "not_matched", "requested", "completed", "failed", "blocked"}
    if status_value is not None and status_value not in allowed_statuses:
        raise EnterpriseAutomationWorkflowsInvalid("status is invalid")
    decoded = _cursor_decode(cursor, "automation-runs")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantAutomationRun).where(TenantAutomationRun.tenant_id == tenant)
        if rule is not None:
            query = query.where(TenantAutomationRun.rule_id == rule)
        if status_value is not None:
            query = query.where(TenantAutomationRun.status == status_value)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("started_at"), "cursor.started_at")
            run_id = _id(decoded.get("id"), "cursor.id", 64)
            query = query.where(
                or_(
                    TenantAutomationRun.started_at < stamp,
                    and_(TenantAutomationRun.started_at == stamp, TenantAutomationRun.id < run_id),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantAutomationRun.started_at.desc(), TenantAutomationRun.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "automation-runs",
                {"started_at": _iso(rows[-1].started_at), "id": str(rows[-1].id)},
            )
        return _page([_run_body(row) for row in rows], next_cursor)


def get_automation_run(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    run_id: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(run_id, "run_id", 64)
    _validate_actor_account(account_id, actor)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        run = session.scalar(
            select(TenantAutomationRun).where(
                TenantAutomationRun.tenant_id == tenant,
                TenantAutomationRun.id == identifier,
            )
        )
        if run is None:
            raise EnterpriseAutomationWorkflowsNotFound("automation run was not found")
        actions = list(
            session.scalars(
                select(TenantAutomationActionRequest)
                .where(
                    TenantAutomationActionRequest.tenant_id == tenant,
                    TenantAutomationActionRequest.run_id == identifier,
                )
                .order_by(
                    TenantAutomationActionRequest.step_index.asc(),
                    TenantAutomationActionRequest.id.asc(),
                )
            )
        )
        events = list(
            session.scalars(
                select(TenantAutomationEvent)
                .where(
                    TenantAutomationEvent.tenant_id == tenant,
                    TenantAutomationEvent.run_id == identifier,
                    TenantAutomationEvent.stream_key == f"run:{identifier}",
                )
                .order_by(TenantAutomationEvent.sequence.asc())
            )
        )
        _validate_event_chain(events)
        return ServiceResult(
            {
                "run": _run_body(run),
                "action_requests": [_action_body(row) for row in actions],
                "events": [_event_body(row) for row in events],
            }
        )


def list_action_requests(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    rule_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    page_size = _validate_limit(limit)
    _validate_actor_account(account_id, actor)
    rule = None if rule_id is None else _id(rule_id, "rule_id", 64)
    run = None if run_id is None else _id(run_id, "run_id", 64)
    status_value = None if status is None else _code(status, "status")
    if status_value is not None and status_value not in {
        "requested",
        "dispatched",
        "applied",
        "rejected",
        "expired",
    }:
        raise EnterpriseAutomationWorkflowsInvalid("status is invalid")
    decoded = _cursor_decode(cursor, "automation-actions")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantAutomationActionRequest).where(
            TenantAutomationActionRequest.tenant_id == tenant
        )
        if rule is not None:
            query = query.where(TenantAutomationActionRequest.rule_id == rule)
        if run is not None:
            query = query.where(TenantAutomationActionRequest.run_id == run)
        if status_value is not None:
            query = query.where(TenantAutomationActionRequest.status == status_value)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("requested_at"), "cursor.requested_at")
            action_id = _id(decoded.get("id"), "cursor.id", 64)
            query = query.where(
                or_(
                    TenantAutomationActionRequest.requested_at < stamp,
                    and_(
                        TenantAutomationActionRequest.requested_at == stamp,
                        TenantAutomationActionRequest.id < action_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantAutomationActionRequest.requested_at.desc(),
                    TenantAutomationActionRequest.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "automation-actions",
                {"requested_at": _iso(rows[-1].requested_at), "id": str(rows[-1].id)},
            )
        return _page([_action_body(row) for row in rows], next_cursor)


def list_automation_events(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    rule_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    page_size = _validate_limit(limit)
    _validate_actor_account(account_id, actor)
    rule = None if rule_id is None else _id(rule_id, "rule_id", 64)
    run = None if run_id is None else _id(run_id, "run_id", 64)
    event_code = None if event_type is None else _code(event_type, "event_type")
    if event_code is not None and event_code not in AUTOMATION_EVENT_TYPES:
        raise EnterpriseAutomationWorkflowsInvalid("event_type is invalid")
    decoded = _cursor_decode(cursor, "automation-events")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantAutomationEvent).where(TenantAutomationEvent.tenant_id == tenant)
        if rule is not None:
            query = query.where(TenantAutomationEvent.rule_id == rule)
        if run is not None:
            query = query.where(TenantAutomationEvent.run_id == run)
        if event_code is not None:
            query = query.where(TenantAutomationEvent.event_type == event_code)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("occurred_at"), "cursor.occurred_at")
            event_id = _id(decoded.get("id"), "cursor.id", 64)
            query = query.where(
                or_(
                    TenantAutomationEvent.occurred_at > stamp,
                    and_(
                        TenantAutomationEvent.occurred_at == stamp,
                        TenantAutomationEvent.id > event_id,
                    ),
                )
            )
        all_events = list(
            session.scalars(
                select(TenantAutomationEvent)
                .where(TenantAutomationEvent.tenant_id == tenant)
                .order_by(
                    TenantAutomationEvent.stream_key.asc(),
                    TenantAutomationEvent.sequence.asc(),
                )
            )
        )
        streams: dict[str, list[TenantAutomationEvent]] = {}
        for event in all_events:
            streams.setdefault(str(event.stream_key), []).append(event)
        for stream in streams.values():
            _validate_event_chain(stream)
        rows = list(
            session.scalars(
                query.order_by(
                    TenantAutomationEvent.occurred_at.asc(), TenantAutomationEvent.id.asc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "automation-events",
                {"occurred_at": _iso(rows[-1].occurred_at), "id": str(rows[-1].id)},
            )
        return _page([_event_body(row) for row in rows], next_cursor)


def _revision_request_body(
    *,
    trigger_code: str,
    condition_code: str,
    condition_params: Mapping[str, Any],
    action_plan: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "trigger_code": trigger_code,
        "condition_code": condition_code,
        "condition_params": dict(condition_params),
        "action_plan": [dict(item) for item in action_plan],
    }


def _canonical_new_revision(
    *,
    tenant_id: str,
    rule_id: str,
    revision_id: str,
    revision: int,
    trigger_code: str,
    condition_code: str,
    condition_params: Any,
    action_plan: Any,
    created_at: datetime,
    created_by: str,
) -> dict[str, Any]:
    try:
        return canonical_automation_rule_revision(
            tenant_id=tenant_id,
            rule_id=rule_id,
            rule_revision_id=revision_id,
            revision=revision,
            trigger_code=trigger_code,
            condition_code=condition_code,
            condition_params=condition_params,
            action_plan=action_plan,
            created_at=created_at,
            created_by=created_by,
        )
    except AutomationAuthorityError as exc:
        raise EnterpriseAutomationWorkflowsInvalid(str(exc)) from exc


def create_automation_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    name: str,
    trigger_code: str,
    condition_code: str,
    condition_params: Mapping[str, Any],
    action_plan: Iterable[Mapping[str, Any]],
    reason: str,
    idempotency_key: str,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    priority: int = 100,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    safe_name, normalized_name = _normal_name(name)
    trigger = _code(trigger_code, "trigger_code", AUTOMATION_TRIGGER_CODES)
    condition = _code(condition_code, "condition_code", AUTOMATION_CONDITION_CODES)
    if not isinstance(condition_params, Mapping):
        raise EnterpriseAutomationWorkflowsInvalid("condition_params must be an object")
    if not isinstance(action_plan, (list, tuple)):
        raise EnterpriseAutomationWorkflowsInvalid("action_plan must be a list")
    safe_reason = _safe_text(reason, "reason")
    key = _key(idempotency_key)
    safe_workspace = None if workspace_id is None else _id(workspace_id, "workspace_id", 64)
    safe_dataset = None if dataset_id is None else _id(dataset_id, "dataset_id", 64)
    safe_priority = _integer(priority, "priority", 0, 1000)
    moment = _now(now)
    try:
        canonical_automation_rule_revision(
            tenant_id=tenant,
            rule_id="validation-rule",
            rule_revision_id="validation-revision",
            revision=1,
            trigger_code=trigger,
            condition_code=condition,
            condition_params=dict(condition_params),
            action_plan=[dict(item) for item in action_plan],
            created_at=moment,
            created_by=actor,
        )
    except AutomationAuthorityError as exc:
        raise EnterpriseAutomationWorkflowsInvalid(str(exc)) from exc
    _validate_actor_account(account_id, actor)
    request_body = {
        **_revision_request_body(
            trigger_code=trigger,
            condition_code=condition,
            condition_params=condition_params,
            action_plan=action_plan,
        ),
        "name": safe_name,
        "workspace_id": safe_workspace,
        "dataset_id": safe_dataset,
        "priority": safe_priority,
        "reason": safe_reason,
    }
    operation_name = "create_automation_rule"
    req = _request_id(request_id, f"automation-create-{sha256(key.encode()).hexdigest()}")
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                _validate_resource_scope(session, tenant, safe_workspace, safe_dataset)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_rule",
                    path_identity={"tenant_id": tenant},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                rule_id = _new_id("automation-rule")
                revision_id = _new_id("automation-revision")
                revision_values = _canonical_new_revision(
                    tenant_id=tenant,
                    rule_id=rule_id,
                    revision_id=revision_id,
                    revision=1,
                    trigger_code=trigger,
                    condition_code=condition,
                    condition_params=dict(condition_params),
                    action_plan=[dict(item) for item in action_plan],
                    created_at=moment,
                    created_by=actor,
                )
                rule = TenantAutomationRule(
                    id=rule_id,
                    tenant_id=tenant,
                    name=safe_name,
                    normalized_name=normalized_name,
                    status="draft",
                    active_rule_key=None,
                    revision=1,
                    current_revision_id=None,
                    workspace_id=safe_workspace,
                    dataset_id=safe_dataset,
                    priority=safe_priority,
                    created_at=moment,
                    created_by=actor,
                    updated_at=moment,
                    updated_by=actor,
                )
                revision_row = TenantAutomationRuleRevision(
                    id=revision_id,
                    tenant_id=tenant,
                    rule_id=rule_id,
                    revision=1,
                    trigger_code=revision_values["trigger_code"],
                    condition_code=revision_values["condition_code"],
                    condition_params_json=revision_values["condition_params"],
                    action_plan_json=revision_values["action_plan"],
                    definition_digest=revision_values["definition_digest"],
                    created_at=moment,
                    created_by=actor,
                )
                session.add(rule)
                session.flush()
                session.add(revision_row)
                session.flush()
                rule.current_revision_id = revision_id
                session.flush()
                stream_key = f"rule:{rule_id}"
                _append_event(
                    session,
                    tenant_id=tenant,
                    rule_id=rule_id,
                    run_id=None,
                    stream_key=stream_key,
                    event_type="rule_created",
                    actor_id=actor,
                    request_id=req,
                    safe_snapshot={
                        "rule_status": "draft",
                        "rule_revision": 1,
                        "rule_revision_id": revision_id,
                    },
                    occurred_at=moment,
                )
                _append_event(
                    session,
                    tenant_id=tenant,
                    rule_id=rule_id,
                    run_id=None,
                    stream_key=stream_key,
                    event_type="revision_created",
                    actor_id=actor,
                    request_id=req,
                    safe_snapshot={
                        "rule_revision": 1,
                        "rule_revision_id": revision_id,
                        "definition_digest": revision_values["definition_digest"],
                    },
                    occurred_at=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=rule_id,
                    rule=_rule_body(rule, revision_row),
                    revision=_revision_body(revision_row),
                )
                return _complete(session, reservation, response, rule_id)


def _expected_fence(
    expected_revision: Any,
    expected_definition_digest: Any,
) -> tuple[int, str]:
    if expected_revision is None or expected_definition_digest is None:
        raise EnterpriseAutomationWorkflowsInvalid(
            "expected_revision and expected_definition_digest are required"
        )
    return (
        _integer(expected_revision, "expected_revision", 1),
        _digest(expected_definition_digest, "expected_definition_digest"),
    )


def update_automation_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    expected_revision: int,
    expected_definition_digest: str,
    reason: str,
    idempotency_key: str,
    name: str | None = None,
    workspace_id: str | None = None,
    dataset_id: str | None = None,
    priority: int | None = None,
    status: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    expected_head, expected_digest = _expected_fence(expected_revision, expected_definition_digest)
    safe_reason = _safe_text(reason, "reason")
    safe_name, normalized_name = (None, None) if name is None else _normal_name(name)
    safe_workspace = None if workspace_id is None else _id(workspace_id, "workspace_id", 64)
    safe_dataset = None if dataset_id is None else _id(dataset_id, "dataset_id", 64)
    safe_priority = None if priority is None else _integer(priority, "priority", 0, 1000)
    status_value = (
        None
        if status is None
        else _code(status, "status", frozenset({"draft", "active", "paused", "archived"}))
    )
    if all(
        value is None
        for value in (safe_name, safe_workspace, safe_dataset, safe_priority, status_value)
    ):
        raise EnterpriseAutomationWorkflowsInvalid("rule update requires a change")
    key = _key(idempotency_key)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    operation_name = "update_automation_rule"
    req = _request_id(request_id, f"automation-update-{sha256(key.encode()).hexdigest()}")
    request_body = {
        "expected_revision": expected_head,
        "expected_definition_digest": expected_digest,
        "name": safe_name,
        "workspace_id": safe_workspace,
        "dataset_id": safe_dataset,
        "priority": safe_priority,
        "status": status_value,
        "reason": safe_reason,
    }
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                _validate_resource_scope(session, tenant, safe_workspace, safe_dataset)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_rule",
                    path_identity={"tenant_id": tenant, "rule_id": identifier},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                rule = _get_rule(session, tenant, identifier, lock=True)
                if int(rule.revision) != expected_head:
                    raise EnterpriseAutomationWorkflowsConflict("automation rule revision is stale")
                if rule.current_revision_id is None:
                    raise EnterpriseAutomationWorkflowsUnavailable(
                        "automation rule current revision is unavailable"
                    )
                current = _get_revision(
                    session, tenant, identifier, str(rule.current_revision_id), lock=True
                )
                if str(current.definition_digest) != expected_digest:
                    raise EnterpriseAutomationWorkflowsConflict(
                        "automation rule definition digest is stale"
                    )

                next_name = safe_name if safe_name is not None else str(rule.name)
                next_normalized_name = (
                    normalized_name if normalized_name is not None else str(rule.normalized_name)
                )
                next_status = status_value if status_value is not None else str(rule.status)
                if next_status == "active":
                    conflict = session.scalar(
                        select(TenantAutomationRule).where(
                            TenantAutomationRule.tenant_id == tenant,
                            TenantAutomationRule.id != identifier,
                            TenantAutomationRule.status == "active",
                            TenantAutomationRule.active_rule_key == next_normalized_name,
                        )
                    )
                    if conflict is not None:
                        raise EnterpriseAutomationWorkflowsConflict(
                            "another active rule uses the normalized rule name"
                        )

                previous_status = str(rule.status)
                rule.name = next_name
                rule.normalized_name = next_normalized_name
                rule.status = next_status
                rule.revision = expected_head
                rule.workspace_id = (
                    safe_workspace if workspace_id is not None else rule.workspace_id
                )
                rule.dataset_id = safe_dataset if dataset_id is not None else rule.dataset_id
                rule.priority = safe_priority if priority is not None else rule.priority
                if next_status == "active":
                    rule.active_rule_key = next_normalized_name
                    rule.archived_at = None
                    rule.archived_by = None
                elif next_status == "archived":
                    rule.active_rule_key = None
                    rule.archived_at = moment
                    rule.archived_by = actor
                else:
                    rule.active_rule_key = None
                    rule.archived_at = None
                    rule.archived_by = None
                rule.updated_at = moment
                rule.updated_by = actor
                session.flush()

                event_type = {
                    (False, "active"): "revision_activated",
                    (True, "paused"): "rule_paused",
                }.get((previous_status == "active", next_status))
                if event_type is not None:
                    _append_event(
                        session,
                        tenant_id=tenant,
                        rule_id=identifier,
                        run_id=None,
                        stream_key=f"rule:{identifier}",
                        event_type=event_type,
                        actor_id=actor,
                        request_id=req,
                        safe_snapshot={
                            "rule_status": next_status,
                            "rule_revision": int(current.revision),
                            "rule_revision_id": str(current.id),
                            "definition_digest": str(current.definition_digest),
                        },
                        occurred_at=moment,
                    )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=identifier,
                    rule=_rule_body(rule),
                    revision=_revision_body(current),
                )
                return _complete(session, reservation, response, identifier)


def create_rule_revision(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    trigger_code: str,
    condition_code: str,
    condition_params: Mapping[str, Any],
    action_plan: Iterable[Mapping[str, Any]],
    reason: str,
    idempotency_key: str,
    expected_revision: int | None = None,
    expected_definition_digest: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    trigger = _code(trigger_code, "trigger_code", AUTOMATION_TRIGGER_CODES)
    condition = _code(condition_code, "condition_code", AUTOMATION_CONDITION_CODES)
    if not isinstance(condition_params, Mapping):
        raise EnterpriseAutomationWorkflowsInvalid("condition_params must be an object")
    if not isinstance(action_plan, (list, tuple)):
        raise EnterpriseAutomationWorkflowsInvalid("action_plan must be a list")
    safe_reason = _safe_text(reason, "reason")
    key = _key(idempotency_key)
    expected_head, expected_digest = _expected_fence(expected_revision, expected_definition_digest)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    request_body = {
        **_revision_request_body(
            trigger_code=trigger,
            condition_code=condition,
            condition_params=condition_params,
            action_plan=action_plan,
        ),
        "expected_revision": expected_head,
        "expected_definition_digest": expected_digest,
        "reason": safe_reason,
    }
    operation_name = "create_rule_revision"
    req = _request_id(request_id, f"automation-revision-{sha256(key.encode()).hexdigest()}")
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_rule_revision",
                    path_identity={"tenant_id": tenant, "rule_id": identifier},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                rule = _get_rule(session, tenant, identifier, lock=True)
                if str(rule.status) == "archived":
                    raise EnterpriseAutomationWorkflowsBlocked(
                        "archived rule cannot receive revisions"
                    )
                if int(rule.revision) != expected_head:
                    raise EnterpriseAutomationWorkflowsConflict("automation rule revision is stale")
                if rule.current_revision_id is None:
                    raise EnterpriseAutomationWorkflowsUnavailable(
                        "automation rule current revision is unavailable"
                    )
                current = _get_revision(
                    session, tenant, identifier, str(rule.current_revision_id), lock=True
                )
                if str(current.definition_digest) != expected_digest:
                    raise EnterpriseAutomationWorkflowsConflict(
                        "automation rule definition digest is stale"
                    )
                maximum_revision = session.scalar(
                    select(func.max(TenantAutomationRuleRevision.revision)).where(
                        TenantAutomationRuleRevision.tenant_id == tenant,
                        TenantAutomationRuleRevision.rule_id == identifier,
                    )
                )
                if int(maximum_revision or 0) != int(rule.revision):
                    raise EnterpriseAutomationWorkflowsUnavailable(
                        "automation rule revision head is unavailable"
                    )
                next_revision = int(rule.revision) + 1
                revision_id = _new_id("automation-revision")
                revision_values = _canonical_new_revision(
                    tenant_id=tenant,
                    rule_id=identifier,
                    revision_id=revision_id,
                    revision=next_revision,
                    trigger_code=trigger,
                    condition_code=condition,
                    condition_params=dict(condition_params),
                    action_plan=[dict(item) for item in action_plan],
                    created_at=moment,
                    created_by=actor,
                )
                revision_row = TenantAutomationRuleRevision(
                    id=revision_id,
                    tenant_id=tenant,
                    rule_id=identifier,
                    revision=next_revision,
                    trigger_code=revision_values["trigger_code"],
                    condition_code=revision_values["condition_code"],
                    condition_params_json=revision_values["condition_params"],
                    action_plan_json=revision_values["action_plan"],
                    definition_digest=revision_values["definition_digest"],
                    created_at=moment,
                    created_by=actor,
                )
                session.add(revision_row)
                session.flush()
                rule.revision = next_revision
                if str(rule.status) != "active":
                    rule.current_revision_id = revision_id
                rule.updated_at = moment
                rule.updated_by = actor
                session.flush()
                _append_event(
                    session,
                    tenant_id=tenant,
                    rule_id=identifier,
                    run_id=None,
                    stream_key=f"rule:{identifier}",
                    event_type="revision_created",
                    actor_id=actor,
                    request_id=req,
                    safe_snapshot={
                        "rule_revision": next_revision,
                        "rule_revision_id": revision_id,
                        "definition_digest": revision_values["definition_digest"],
                    },
                    occurred_at=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=revision_id,
                    rule=_rule_body(
                        rule, current if str(rule.status) == "active" else revision_row
                    ),
                    revision=_revision_body(revision_row),
                )
                return _complete(session, reservation, response, revision_id)


def preview_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    trigger_event: Mapping[str, Any],
    revision_id: str | None = None,
    expected_revision: int | None = None,
    expected_definition_digest: str | None = None,
    idempotency_key: str | None = None,
    reason: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del idempotency_key, reason, request_id, request_ip, now
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    _validate_actor_account(account_id, actor)
    if not isinstance(trigger_event, Mapping):
        raise EnterpriseAutomationWorkflowsInvalid("trigger_event must be an object")
    try:
        canonical_event = canonical_automation_trigger_event(trigger_event)
    except AutomationAuthorityError as exc:
        raise EnterpriseAutomationWorkflowsInvalid(str(exc)) from exc
    if canonical_event["tenant_id"] != tenant:
        raise EnterpriseAutomationWorkflowsInvalid("preview crossed Tenant scope")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rule = _get_rule(session, tenant, identifier)
        if expected_revision is not None:
            if int(rule.revision) != _integer(expected_revision, "expected_revision", 1):
                raise EnterpriseAutomationWorkflowsConflict("automation rule revision is stale")
        selected_id = (
            _id(revision_id, "revision_id", 64)
            if revision_id is not None
            else str(rule.current_revision_id)
            if rule.current_revision_id is not None
            else None
        )
        if selected_id is None:
            raise EnterpriseAutomationWorkflowsNotFound("automation rule revision was not found")
        revision = _get_revision(session, tenant, identifier, selected_id)
        if expected_definition_digest is not None and str(revision.definition_digest) != _digest(
            expected_definition_digest, "expected_definition_digest"
        ):
            raise EnterpriseAutomationWorkflowsConflict(
                "automation rule definition digest is stale"
            )
        result = dict(
            preview_automation_rule_pure(_revision_authority_body(revision), canonical_event)
        )
        result.update(
            {
                "state": "applied",
                "operation": "preview_rule",
                "rule": _rule_body(rule, revision),
                "revision": _revision_body(revision),
            }
        )
        return ServiceResult(result)


def activate_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    revision_id: str,
    reason: str,
    idempotency_key: str,
    expected_revision: int | None = None,
    expected_definition_digest: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    selected_revision_id = _id(revision_id, "revision_id", 64)
    safe_reason = _safe_text(reason, "reason")
    key = _key(idempotency_key)
    expected_head, expected_digest = _expected_fence(expected_revision, expected_definition_digest)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    request_body = {
        "revision_id": selected_revision_id,
        "expected_revision": expected_head,
        "expected_definition_digest": expected_digest,
        "reason": safe_reason,
    }
    operation_name = "activate_rule"
    req = _request_id(request_id, f"automation-activate-{sha256(key.encode()).hexdigest()}")
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_rule",
                    path_identity={"tenant_id": tenant, "rule_id": identifier},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                rule = _get_rule(session, tenant, identifier, lock=True)
                if str(rule.status) == "archived":
                    raise EnterpriseAutomationWorkflowsBlocked("archived rule cannot be activated")
                if int(rule.revision) != expected_head:
                    raise EnterpriseAutomationWorkflowsConflict("automation rule revision is stale")
                revision = _get_revision(
                    session, tenant, identifier, selected_revision_id, lock=True
                )
                if str(revision.definition_digest) != expected_digest:
                    raise EnterpriseAutomationWorkflowsConflict(
                        "automation rule definition digest is stale"
                    )
                conflict = session.scalar(
                    select(TenantAutomationRule).where(
                        TenantAutomationRule.tenant_id == tenant,
                        TenantAutomationRule.id != identifier,
                        TenantAutomationRule.status == "active",
                        TenantAutomationRule.active_rule_key == str(rule.normalized_name),
                    )
                )
                if conflict is not None:
                    raise EnterpriseAutomationWorkflowsConflict(
                        "another active rule uses the normalized rule name"
                    )
                rule.status = "active"
                rule.active_rule_key = str(rule.normalized_name)
                rule.current_revision_id = selected_revision_id
                rule.updated_at = moment
                rule.updated_by = actor
                session.flush()
                _append_event(
                    session,
                    tenant_id=tenant,
                    rule_id=identifier,
                    run_id=None,
                    stream_key=f"rule:{identifier}",
                    event_type="revision_activated",
                    actor_id=actor,
                    request_id=req,
                    safe_snapshot={
                        "rule_status": "active",
                        "rule_revision": int(revision.revision),
                        "rule_revision_id": selected_revision_id,
                        "definition_digest": str(revision.definition_digest),
                    },
                    occurred_at=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=identifier,
                    rule=_rule_body(rule, revision),
                    revision=_revision_body(revision),
                )
                return _complete(session, reservation, response, identifier)


def pause_rule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    rule_id: str,
    reason: str,
    idempotency_key: str,
    expected_revision: int | None = None,
    expected_definition_digest: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    identifier = _id(rule_id, "rule_id", 64)
    safe_reason = _safe_text(reason, "reason")
    key = _key(idempotency_key)
    expected_head, expected_digest = _expected_fence(expected_revision, expected_definition_digest)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    request_body = {
        "expected_revision": expected_head,
        "expected_definition_digest": expected_digest,
        "reason": safe_reason,
    }
    operation_name = "pause_rule"
    req = _request_id(request_id, f"automation-pause-{sha256(key.encode()).hexdigest()}")
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_rule",
                    path_identity={"tenant_id": tenant, "rule_id": identifier},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                rule = _get_rule(session, tenant, identifier, lock=True)
                if int(rule.revision) != expected_head:
                    raise EnterpriseAutomationWorkflowsConflict("automation rule revision is stale")
                if rule.current_revision_id is None:
                    raise EnterpriseAutomationWorkflowsUnavailable(
                        "automation rule current revision is unavailable"
                    )
                revision = _get_revision(
                    session, tenant, identifier, str(rule.current_revision_id), lock=True
                )
                if str(revision.definition_digest) != expected_digest:
                    raise EnterpriseAutomationWorkflowsConflict(
                        "automation rule definition digest is stale"
                    )
                if str(rule.status) == "archived":
                    raise EnterpriseAutomationWorkflowsBlocked("archived rule cannot be paused")
                if str(rule.status) != "active":
                    raise EnterpriseAutomationWorkflowsBlocked("only an active rule can be paused")
                rule.status = "paused"
                rule.active_rule_key = None
                rule.updated_at = moment
                rule.updated_by = actor
                session.flush()
                _append_event(
                    session,
                    tenant_id=tenant,
                    rule_id=identifier,
                    run_id=None,
                    stream_key=f"rule:{identifier}",
                    event_type="rule_paused",
                    actor_id=actor,
                    request_id=req,
                    safe_snapshot={
                        "rule_status": "paused",
                        "rule_revision": int(revision.revision),
                        "rule_revision_id": str(revision.id),
                        "definition_digest": str(revision.definition_digest),
                    },
                    occurred_at=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=identifier,
                    rule=_rule_body(rule, revision),
                    revision=_revision_body(revision),
                )
                return _complete(session, reservation, response, identifier)


def _source_event(
    *,
    tenant_id: str,
    trigger_code: str,
    source_stream_id: str,
    source_event_id: str,
    source_event_digest: str,
    sequence: int,
    status: str,
    severity: str,
    action_required: bool,
    attempt_number: int,
    max_attempts: int,
    source_current: bool,
    occurred_at: datetime,
    safe_facts: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "trigger_code": trigger_code,
        "source_stream_id": source_stream_id,
        "source_event_id": source_event_id,
        "source_event_digest": source_event_digest,
        "sequence": max(1, int(sequence)),
        "status": status,
        "severity": severity,
        "action_required": bool(action_required),
        "attempt_number": max(0, int(attempt_number)),
        "max_attempts": max(1, int(max_attempts)),
        "source_current": bool(source_current),
        "occurred_at": occurred_at,
        "safe_facts": dict(safe_facts),
    }


def _task_failed_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(TenantTaskProjection).where(
            TenantTaskProjection.tenant_id == tenant_id,
            TenantTaskProjection.normalized_status == "failed",
        )
    )
    for row in rows:
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="task_failed",
            source_stream_id=f"task:{row.id}",
            source_event_id=f"task-failed:{row.id}:{int(row.source_revision)}",
            source_event_digest=str(row.source_digest),
            sequence=int(row.source_revision),
            status="failed",
            severity="error",
            action_required=bool(row.action_required),
            attempt_number=int(row.attempt_number),
            max_attempts=int(row.max_attempts),
            source_current=bool(row.source_current),
            occurred_at=row.updated_at,
            safe_facts={
                "task_id": str(row.id),
                "source_kind": str(row.source_kind),
                "reason_code": "task_failed",
            },
        )


def _task_source_stale_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(TenantTaskProjection).where(
            TenantTaskProjection.tenant_id == tenant_id,
            TenantTaskProjection.source_current.is_(False),
        )
    )
    for row in rows:
        source_digest = sha256(
            f"stale:{tenant_id}:{row.id}:{int(row.source_revision)}:{row.source_digest}".encode()
        ).hexdigest()
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="task_source_stale",
            source_stream_id=f"task:{row.id}",
            source_event_id=f"task-stale:{row.id}:{int(row.source_revision)}",
            source_event_digest=source_digest,
            sequence=int(row.source_revision),
            status="unavailable",
            severity="warning",
            action_required=True,
            attempt_number=int(row.attempt_number),
            max_attempts=int(row.max_attempts),
            source_current=False,
            occurred_at=row.updated_at,
            safe_facts={
                "task_id": str(row.id),
                "source_kind": str(row.source_kind),
                "reason_code": "source_stale",
            },
        )


def _source_sync_failed_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(SourceSyncRun).where(
            SourceSyncRun.tenant_id == tenant_id,
            SourceSyncRun.status == "failed",
        )
    )
    for row in rows:
        source_digest = sha256(
            f"source-sync:{tenant_id}:{row.id}:{row.updated_at.isoformat()}".encode()
        ).hexdigest()
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="source_sync_failed",
            source_stream_id=f"source:{row.source_id}",
            source_event_id=f"source-sync-failed:{row.id}",
            source_event_digest=source_digest,
            sequence=max(1, int(row.execution_attempts)),
            status="failed",
            severity="error",
            action_required=True,
            attempt_number=int(row.execution_attempts),
            max_attempts=3,
            source_current=True,
            occurred_at=row.updated_at,
            safe_facts={"source_id": str(row.source_id), "sync_run_id": str(row.id)},
        )


def _quality_alert_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(DatasetReleaseQualityAlert).where(
            DatasetReleaseQualityAlert.tenant_id == tenant_id,
            DatasetReleaseQualityAlert.status == "open",
        )
    )
    for row in rows:
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="release_quality_alert_opened",
            source_stream_id=f"quality-alert:{row.id}",
            source_event_id=f"quality-alert-opened:{row.id}:{int(row.revision)}",
            source_event_digest=str(row.source_observation_digest),
            sequence=int(row.revision),
            status="failed",
            severity="critical" if str(row.severity) == "critical" else "warning",
            action_required=True,
            attempt_number=1,
            max_attempts=1,
            source_current=True,
            occurred_at=row.last_observed_at,
            safe_facts={
                "alert_id": str(row.id),
                "dataset_id": str(row.dataset_id),
                "release_id": str(row.release_id),
                "channel_id": str(row.channel_id),
                "alert_type": str(row.alert_type),
            },
        )


def _recertification_blocked_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(DatasetReleaseRecertificationJob).where(
            DatasetReleaseRecertificationJob.tenant_id == tenant_id,
            DatasetReleaseRecertificationJob.status.in_(("awaiting_evidence", "failed")),
        )
    )
    for row in rows:
        source_digest = sha256(
            "recertification:".encode()
            + f"{tenant_id}:{row.id}:{int(row.policy_revision)}:{row.updated_at.isoformat()}".encode()
        ).hexdigest()
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="release_recertification_blocked",
            source_stream_id=f"recertification:{row.dataset_id}",
            source_event_id=f"recertification-blocked:{row.id}:{int(row.policy_revision)}",
            source_event_digest=source_digest,
            sequence=int(row.policy_revision),
            status="blocked",
            severity="critical",
            action_required=True,
            attempt_number=int(row.attempt_count),
            max_attempts=int(row.max_attempts),
            source_current=True,
            occurred_at=row.updated_at,
            safe_facts={
                "job_id": str(row.id),
                "dataset_id": str(row.dataset_id),
                "release_id": str(row.release_id),
                "reason_code": "recertification_blocked",
            },
        )


def _approval_terminal_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> Iterable[Mapping[str, Any]]:
    del now
    rows = session.scalars(
        select(TenantApprovalRequest).where(
            TenantApprovalRequest.tenant_id == tenant_id,
            TenantApprovalRequest.status.in_(
                ("approved", "rejected", "cancelled", "expired", "executed", "execution_failed")
            ),
        )
    )
    for row in rows:
        source_status = str(row.status)
        status = {"executed": "completed", "execution_failed": "failed"}.get(
            source_status, source_status
        )
        severity = "error" if status in {"rejected", "cancelled", "expired", "failed"} else "info"
        source_digest = sha256(
            f"approval:{tenant_id}:{row.id}:{int(row.revision)}:{source_status}:{row.updated_at.isoformat()}".encode()
        ).hexdigest()
        yield _source_event(
            tenant_id=tenant_id,
            trigger_code="approval_request_terminal",
            source_stream_id=f"approval:{row.id}",
            source_event_id=f"approval-terminal:{row.id}:{int(row.revision)}",
            source_event_digest=source_digest,
            sequence=int(row.revision),
            status=status,
            severity=severity,
            action_required=status in {"rejected", "cancelled", "expired", "failed"},
            attempt_number=1,
            max_attempts=1,
            source_current=True,
            occurred_at=row.updated_at,
            safe_facts={"approval_request_id": str(row.id), "action_type": str(row.action_type)},
        )


TRIGGER_ADAPTER_REGISTRY: dict[str, TriggerAdapter] = {
    "task_failed": _task_failed_adapter,
    "task_source_stale": _task_source_stale_adapter,
    "source_sync_failed": _source_sync_failed_adapter,
    "release_quality_alert_opened": _quality_alert_adapter,
    "release_recertification_blocked": _recertification_blocked_adapter,
    "approval_request_terminal": _approval_terminal_adapter,
}

# 顺序 = 注册表的插入序（Python 保证）。放在这里是因为适配器函数得先存在。
TRIGGER_ADAPTER_ORDER: tuple[str, ...] = tuple(TRIGGER_ADAPTER_REGISTRY)


def _target_for_event(
    event: Mapping[str, Any], action_code: str, rule_id: str
) -> tuple[str, str, str | None]:
    facts = event.get("safe_facts")
    safe_facts = facts if isinstance(facts, Mapping) else {}
    if action_code == "pause_rule":
        return "automation_rule", rule_id, None
    task_id = safe_facts.get("task_id")
    if isinstance(task_id, str) and task_id:
        clean_task = _id(task_id, "safe_facts.task_id", 128)
        return "task", clean_task, clean_task
    source_id = safe_facts.get("source_id")
    if isinstance(source_id, str) and source_id:
        return "source", _id(source_id, "safe_facts.source_id", 128), None
    approval_id = safe_facts.get("approval_request_id")
    if isinstance(approval_id, str) and approval_id:
        return (
            "approval_request",
            _id(approval_id, "safe_facts.approval_request_id", 128),
            None,
        )
    return "source_event", _id(event["source_event_id"], "source_event_id", 128), None


def _safe_event_for_processing(
    raw: Mapping[str, Any], tenant_id: str, trigger_code: str
) -> dict[str, Any] | None:
    try:
        event = canonical_automation_trigger_event(raw)
    except AutomationAuthorityError:
        return None
    if event["tenant_id"] != tenant_id or event["trigger_code"] != trigger_code:
        return None
    return event


def _claim_cursor(
    session: Session,
    *,
    tenant_id: str,
    rule_id: str,
    trigger_code: str,
    source_stream_id: str,
    owner: str,
    moment: datetime,
) -> tuple[TenantAutomationSourceCursor, str]:
    cursor = session.scalar(
        select(TenantAutomationSourceCursor)
        .where(
            TenantAutomationSourceCursor.tenant_id == tenant_id,
            TenantAutomationSourceCursor.rule_id == rule_id,
            TenantAutomationSourceCursor.source_kind == trigger_code,
            TenantAutomationSourceCursor.source_stream_id == source_stream_id,
        )
        .with_for_update()
    )
    if cursor is None:
        cursor = TenantAutomationSourceCursor(
            id=_stable_id("automation-cursor", tenant_id, rule_id, trigger_code, source_stream_id),
            tenant_id=tenant_id,
            rule_id=rule_id,
            source_kind=trigger_code,
            source_stream_id=source_stream_id,
            last_sequence=0,
            last_event_digest=None,
            status="claimed",
            lease_owner=owner,
            lease_until=moment + _CURSOR_LEASE,
            revision=1,
            updated_at=moment,
        )
        session.add(cursor)
        session.flush()
        return cursor, "claimed"
    if (
        str(cursor.status) == "claimed"
        and cursor.lease_until is not None
        and cursor.lease_until > moment
        and str(cursor.lease_owner) != owner
    ):
        return cursor, "blocked"
    cursor.status = "claimed"
    cursor.lease_owner = owner
    cursor.lease_until = moment + _CURSOR_LEASE
    cursor.updated_at = moment
    session.flush()
    return cursor, "claimed"


def _finish_cursor(
    cursor: TenantAutomationSourceCursor, event: Mapping[str, Any], moment: datetime
) -> None:
    cursor.last_sequence = int(event["sequence"])
    cursor.last_event_digest = str(event["source_event_digest"])
    cursor.status = "idle"
    cursor.lease_owner = None
    cursor.lease_until = None
    cursor.revision = int(cursor.revision) + 1
    cursor.updated_at = moment


def _process_rule_event(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    rule: TenantAutomationRule,
    revision: TenantAutomationRuleRevision,
    event: Mapping[str, Any],
    moment: datetime,
) -> str:
    existing = session.scalar(
        select(TenantAutomationRun)
        .where(
            TenantAutomationRun.tenant_id == tenant_id,
            TenantAutomationRun.rule_id == str(rule.id),
            TenantAutomationRun.trigger_event_digest == str(event["source_event_digest"]),
        )
        .with_for_update()
    )
    if existing is not None:
        return "replayed"
    cursor, claim_state = _claim_cursor(
        session,
        tenant_id=tenant_id,
        rule_id=str(rule.id),
        trigger_code=str(event["trigger_code"]),
        source_stream_id=str(event["source_stream_id"]),
        owner=request_id,
        moment=moment,
    )
    if claim_state == "blocked":
        return "blocked"
    if int(cursor.last_sequence) >= int(event["sequence"]):
        if str(cursor.last_event_digest) == str(event["source_event_digest"]):
            cursor.status = "idle"
            cursor.lease_owner = None
            cursor.lease_until = None
            cursor.updated_at = moment
            return "replayed"
        cursor.status = "blocked"
        cursor.lease_owner = None
        cursor.lease_until = None
        cursor.updated_at = moment
        return "blocked"
    revision_body = _canonical_revision(revision)
    run_id = _stable_id(
        "automation-run", tenant_id, str(rule.id), str(event["source_event_digest"])
    )
    run_digest = canonical_automation_run_digest(
        {
            "tenant_id": tenant_id,
            "rule_id": str(rule.id),
            "rule_revision_id": str(revision.id),
            "trigger_event_digest": str(event["source_event_digest"]),
        }
    )
    run = TenantAutomationRun(
        id=run_id,
        tenant_id=tenant_id,
        rule_id=str(rule.id),
        rule_revision_id=str(revision.id),
        trigger_event_id=str(event["source_event_id"]),
        trigger_event_digest=str(event["source_event_digest"]),
        status="started",
        condition_matched=False,
        action_count=0,
        requested_count=0,
        rejected_count=0,
        idempotency_digest=run_digest,
        started_at=moment,
        completed_at=None,
        safe_error_code=None,
        safe_error=None,
        created_at=moment,
        updated_at=moment,
    )
    session.add(run)
    session.flush()
    stream_key = f"run:{run_id}"
    _append_event(
        session,
        tenant_id=tenant_id,
        rule_id=str(rule.id),
        run_id=run_id,
        stream_key=stream_key,
        event_type="run_started",
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot={
            "rule_revision_id": str(revision.id),
            "definition_digest": str(revision.definition_digest),
            "trigger_event_digest": str(event["source_event_digest"]),
        },
        occurred_at=moment,
    )
    _append_event(
        session,
        tenant_id=tenant_id,
        rule_id=str(rule.id),
        run_id=run_id,
        stream_key=stream_key,
        event_type="trigger_observed",
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot={
            "trigger_code": str(event["trigger_code"]),
            "source_stream_id": str(event["source_stream_id"]),
            "source_event_id": str(event["source_event_id"]),
            "source_event_digest": str(event["source_event_digest"]),
            "sequence": int(event["sequence"]),
            "status": str(event["status"]),
            "severity": str(event["severity"]),
        },
        occurred_at=moment,
    )
    try:
        matched = evaluate_automation_condition(
            revision_body["condition_code"], revision_body["condition_params"], event
        )
    except AutomationAuthorityError as exc:
        run.status = "failed"
        run.condition_matched = False
        run.completed_at = moment
        run.safe_error_code = "condition_invalid"
        run.safe_error = "automation condition is unavailable"
        run.updated_at = moment
        _append_event(
            session,
            tenant_id=tenant_id,
            rule_id=str(rule.id),
            run_id=run_id,
            stream_key=stream_key,
            event_type="run_failed",
            actor_id=actor_id,
            request_id=request_id,
            safe_snapshot={"safe_error_code": "condition_invalid"},
            occurred_at=moment,
        )
        _finish_cursor(cursor, event, moment)
        raise EnterpriseAutomationWorkflowsUnavailable(
            "automation condition is unavailable"
        ) from exc
    run.condition_matched = bool(matched)
    if not matched:
        run.status = "not_matched"
        run.completed_at = moment
        run.updated_at = moment
        _append_event(
            session,
            tenant_id=tenant_id,
            rule_id=str(rule.id),
            run_id=run_id,
            stream_key=stream_key,
            event_type="condition_not_matched",
            actor_id=actor_id,
            request_id=request_id,
            safe_snapshot={"condition_code": revision_body["condition_code"]},
            occurred_at=moment,
        )
        _append_event(
            session,
            tenant_id=tenant_id,
            rule_id=str(rule.id),
            run_id=run_id,
            stream_key=stream_key,
            event_type="run_completed",
            actor_id=actor_id,
            request_id=request_id,
            safe_snapshot={
                "run_status": "not_matched",
                "action_count": 0,
                "requested_count": 0,
                "rejected_count": 0,
            },
            occurred_at=moment,
        )
        _finish_cursor(cursor, event, moment)
        return "created"
    action_plan = revision_body["action_plan"]
    run.action_count = len(action_plan)
    for action in action_plan:
        action_code = str(action["action_code"])
        if action_code not in AUTOMATION_ACTION_CODES:
            run.rejected_count = int(run.rejected_count) + 1
            continue
        target_kind, target_id, task_id = _target_for_event(event, action_code, str(rule.id))
        relational_task_id = task_id
        if task_id is not None:
            task_exists = session.scalar(
                select(TenantTaskProjection.id).where(
                    TenantTaskProjection.tenant_id == tenant_id,
                    TenantTaskProjection.id == task_id,
                )
            )
            if task_exists is None:
                relational_task_id = None
        safe_params = dict(action["params"])
        action_digest = canonical_automation_action_request_digest(
            {
                "tenant_id": tenant_id,
                "rule_id": str(rule.id),
                "rule_revision_id": str(revision.id),
                "trigger_event_digest": str(event["source_event_digest"]),
                "target_kind": target_kind,
                "target_id": target_id,
                "action_code": action_code,
                "step_index": int(action["step_index"]),
                "safe_params": safe_params,
            }
        )
        action_id = _stable_id("automation-action", tenant_id, run_id, str(action["step_index"]))
        action_row = TenantAutomationActionRequest(
            id=action_id,
            tenant_id=tenant_id,
            run_id=run_id,
            rule_id=str(rule.id),
            step_index=int(action["step_index"]),
            action_code=action_code,
            status="requested",
            target_kind=target_kind,
            target_id=target_id,
            target_revision=None,
            target_digest=None,
            idempotency_key_digest=action_digest,
            safe_params_json=safe_params,
            safe_reason="automation_rule_matched",
            approval_request_id=None,
            notification_id=None,
            task_id=relational_task_id,
            requested_at=moment,
            dispatched_at=None,
            applied_at=None,
            rejected_at=None,
            expires_at=moment + _ACTION_EXPIRY,
            created_at=moment,
            updated_at=moment,
        )
        session.add(action_row)
        session.flush()
        run.requested_count = int(run.requested_count) + 1
        _append_event(
            session,
            tenant_id=tenant_id,
            rule_id=str(rule.id),
            run_id=run_id,
            stream_key=stream_key,
            event_type="action_requested",
            actor_id=actor_id,
            request_id=request_id,
            safe_snapshot={
                "step_index": int(action["step_index"]),
                "action_code": action_code,
                "action_status": "requested",
                "target_kind": target_kind,
                "target_id": target_id,
                "action_digest": action_digest,
            },
            occurred_at=moment,
        )
    run.status = "requested" if int(run.requested_count) else "failed"
    run.completed_at = moment
    run.updated_at = moment
    if run.status == "failed":
        run.safe_error_code = "action_not_allowed"
        run.safe_error = "no allowed automation action could be requested"
    _append_event(
        session,
        tenant_id=tenant_id,
        rule_id=str(rule.id),
        run_id=run_id,
        stream_key=stream_key,
        event_type="run_completed" if run.status == "requested" else "run_failed",
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot={
            "run_status": str(run.status),
            "action_count": int(run.action_count),
            "requested_count": int(run.requested_count),
            "rejected_count": int(run.rejected_count),
        },
        occurred_at=moment,
    )
    _finish_cursor(cursor, event, moment)
    return "created"


def _observe_in_session(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    trigger_codes: tuple[str, ...],
    moment: datetime,
) -> dict[str, Any]:
    observed_count = 0
    run_count = 0
    action_request_count = 0
    replayed_count = 0
    blocked_count = 0
    for trigger_code in trigger_codes:
        adapter = TRIGGER_ADAPTER_REGISTRY.get(trigger_code)
        if not callable(adapter):
            raise EnterpriseAutomationWorkflowsUnavailable(
                f"trigger adapter is unavailable: {trigger_code}"
            )
        try:
            raw_events = list(adapter(session=session, tenant_id=tenant_id, now=moment))
        except EnterpriseAutomationWorkflowsError:
            raise
        except Exception as exc:
            raise EnterpriseAutomationWorkflowsUnavailable(
                f"trigger adapter is unavailable: {trigger_code}"
            ) from exc
        events: list[dict[str, Any]] = []
        for raw in raw_events:
            if not isinstance(raw, Mapping):
                blocked_count += 1
                continue
            event = _safe_event_for_processing(raw, tenant_id, trigger_code)
            if event is not None:
                events.append(event)
            else:
                blocked_count += 1
        events.sort(
            key=lambda item: (
                int(item["sequence"]),
                str(item["occurred_at"]),
                str(item["source_stream_id"]),
                str(item["source_event_id"]),
                str(item["source_event_digest"]),
            )
        )
        rules = list(
            session.scalars(
                select(TenantAutomationRule)
                .where(
                    TenantAutomationRule.tenant_id == tenant_id,
                    TenantAutomationRule.status == "active",
                )
                .order_by(
                    TenantAutomationRule.priority.asc(),
                    TenantAutomationRule.normalized_name.asc(),
                    TenantAutomationRule.id.asc(),
                )
            )
        )
        for rule in rules:
            if rule.current_revision_id is None:
                blocked_count += len(events)
                continue
            revision = _get_revision(
                session, tenant_id, str(rule.id), str(rule.current_revision_id), lock=True
            )
            if str(revision.trigger_code) != trigger_code:
                continue
            for event in events:
                result = _process_rule_event(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    request_id=request_id,
                    rule=rule,
                    revision=revision,
                    event=event,
                    moment=moment,
                )
                if result == "created":
                    observed_count += 1
                    run_count += 1
                    run_id = _stable_id(
                        "automation-run", tenant_id, str(rule.id), str(event["source_event_digest"])
                    )
                    run = session.scalar(
                        select(TenantAutomationRun).where(
                            TenantAutomationRun.tenant_id == tenant_id,
                            TenantAutomationRun.id == run_id,
                        )
                    )
                    if run is not None:
                        action_request_count += int(run.requested_count)
                elif result == "replayed":
                    replayed_count += 1
                else:
                    blocked_count += 1
    return {
        "observed_count": observed_count,
        "run_count": run_count,
        "action_request_count": action_request_count,
        "replayed_count": replayed_count,
        "blocked_count": blocked_count,
    }


def observe_automation_events(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    trigger_codes: Any = None,
    source_kinds: Any = None,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    if trigger_codes is not None and source_kinds is not None and trigger_codes != source_kinds:
        raise EnterpriseAutomationWorkflowsInvalid("trigger_codes and source_kinds disagree")
    selected = _validate_trigger_codes(trigger_codes if trigger_codes is not None else source_kinds)
    safe_reason = _safe_text(reason, "reason")
    key = _key(idempotency_key)
    moment = _now(now)
    _validate_actor_account(account_id, actor)
    req = _request_id(request_id, f"automation-observe-{sha256(key.encode()).hexdigest()}")
    operation_name = "observe_automation_events"
    request_body = {"trigger_codes": list(selected), "reason": safe_reason}
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_automation_observation",
                    path_identity={"tenant_id": tenant},
                    request_body=request_body,
                )
                if replay is not None:
                    return replay
                counts = _observe_in_session(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    request_id=req,
                    trigger_codes=selected,
                    moment=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=_stable_id("automation-observation", tenant, actor, key),
                    reason=safe_reason,
                    **counts,
                )
                return _complete(session, reservation, response, response["resource_id"])


__all__ = [
    "AutomationWorkflowsBlocked",
    "AutomationWorkflowsConflict",
    "AutomationWorkflowsError",
    "AutomationWorkflowsInvalid",
    "AutomationWorkflowsNotFound",
    "AutomationWorkflowsUnavailable",
    "EnterpriseAutomationWorkflowsBlocked",
    "EnterpriseAutomationWorkflowsConflict",
    "EnterpriseAutomationWorkflowsError",
    "EnterpriseAutomationWorkflowsInvalid",
    "EnterpriseAutomationWorkflowsNotFound",
    "EnterpriseAutomationWorkflowsUnavailable",
    "ServiceResult",
    "TRIGGER_ADAPTER_ORDER",
    "TRIGGER_ADAPTER_REGISTRY",
    "activate_rule",
    "create_automation_rule",
    "create_rule_revision",
    "get_automation_rule",
    "get_automation_run",
    "get_automation_summary",
    "list_action_requests",
    "list_automation_events",
    "list_automation_rules",
    "list_automation_runs",
    "list_rule_revisions",
    "observe_automation_events",
    "pause_rule",
    "preview_rule",
    "update_automation_rule",
]


# Stable HTTP-boundary operation names.
list_automation_rule_revisions = list_rule_revisions
list_automation_action_requests = list_action_requests
create_automation_rule_revision = create_rule_revision
preview_automation_rule = preview_rule
activate_automation_rule = activate_rule
pause_automation_rule = pause_rule
