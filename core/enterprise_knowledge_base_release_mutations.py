"""Atomic Stage 19 Knowledge Base Release mutations."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime
from hashlib import sha256
import json
import re
import uuid
from typing import Any, Iterator, Mapping

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_release_channels import ensure_default_release_channels_in_session
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot
from models.orm import (
    Account,
    App,
    AppDatasetReference,
    Dataset,
    DatasetChannelRelease,
    DatasetReleaseEntry,
    DatasetReleaseEvent,
    DatasetReleaseManifest,
    DatasetReleaseQualityEvent,
    DatasetWorkspaceOwnership,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantReleaseChannel,
    TenantWorkspace,
)

from core.enterprise_knowledge_base_releases import (
    ReleaseManifestConflict,
    ReleaseManifestForbidden,
    ReleaseManifestInvalid,
    ReleaseManifestNotFound,
    ReleaseManifestUnavailable,
    ServiceResult,
    _actor,
    _canonical_json,
    _channel_payload,
    _clean,
    canonicalize_release_entries,
    collect_release_snapshot,
    _ensure_release_capability,
    _exact_integer,
    _manifest_payload,
    _release_revisions,
    _require_manage,
    _safe_release_reason,
    _safe_string,
)

ACTION_RELEASE_PUBLISH = "knowledge_base_release_publish"
ACTION_RELEASE_ROLLBACK = "knowledge_base_release_rollback"
RESOURCE_KNOWLEDGE_BASE = "knowledge_base"
_CHANNEL_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_CHANNEL_RISK_TIERS = frozenset({"low", "medium", "high"})
_CHANNEL_STATUSES = frozenset({"active", "archived"})
_RELEASE_MODES = frozenset({"follow_channel", "pinned"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _manager(member: TenantMember) -> None:
    if str(member.role).casefold() not in {"owner", "admin"}:
        raise ReleaseManifestForbidden("Tenant owner or admin is required")


def _channel_code(value: Any) -> tuple[str, str]:
    code = _clean(value, "code", 64)
    if not _CHANNEL_CODE_RE.fullmatch(code):
        raise ReleaseManifestInvalid("code contains unsupported characters")
    return code, code.casefold()


def _risk(value: Any) -> str:
    risk = _clean(value, "risk_tier", 16).casefold()
    if risk not in _CHANNEL_RISK_TIERS:
        raise ReleaseManifestInvalid("risk_tier is invalid")
    return risk


def _status(value: Any) -> str:
    status = _clean(value, "status", 16).casefold()
    if status not in _CHANNEL_STATUSES:
        raise ReleaseManifestInvalid("status is invalid")
    return status


def _fields(
    *, request_id: Any, request_ip: Any, idempotency_key: Any, reason: Any
) -> tuple[str, str, str, str]:
    return (
        _clean(request_id, "request_id", 128, allow_empty=True),
        _clean(request_ip, "request_ip", 64, allow_empty=True),
        _clean(idempotency_key, "idempotency_key", 128),
        _safe_release_reason(reason),
    )


@contextmanager
def _scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
) -> Iterator[tuple[Session, TenantMember, Account, Any]]:
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, idempotency_key)
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant_id, actor_id, digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        tenant = session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None:
            raise ReleaseManifestForbidden("Tenant is unavailable")
        member, account = _actor(session, tenant_id, actor_id)
        ensure_default_release_channels_in_session(session, tenant_id=tenant_id, actor_id=actor_id)
        _ensure_release_capability(session.connection())
        try:
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
            raise ReleaseManifestConflict("idempotency key conflict") from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise ReleaseManifestConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise ReleaseManifestInvalid(str(exc)) from exc
        try:
            yield session, member, account, reservation
        except IntegrityError as exc:
            raise ReleaseManifestConflict(
                "release mutation violates an authority constraint"
            ) from exc
        except SQLAlchemyError as exc:
            raise ReleaseManifestUnavailable() from exc


def _complete(
    session: Session,
    reservation: Any,
    *,
    payload: Mapping[str, Any],
    status: int,
    resource_id: str,
) -> ServiceResult:
    try:
        replay = complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseManifestInvalid(str(exc)) from exc
    return ServiceResult(dict(replay), status)


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    account: Account,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=str(account.name)[:128],
            actor_email_snapshot=str(account.email)[:256],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_snapshot=(sanitize_audit_snapshot(dict(before)) if before is not None else None),
            after_snapshot=(sanitize_audit_snapshot(dict(after)) if after is not None else None),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def _channel(row: TenantReleaseChannel) -> dict[str, Any]:
    return {**_channel_payload(row), "normalized_code": row.normalized_code}


def _binding(row: DatasetChannelRelease) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "channel_id": row.channel_id,
        "active_release_id": row.active_release_id,
        "previous_release_id": row.previous_release_id,
        "status": row.status,
        "revision": int(row.revision),
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "activated_by": row.activated_by,
        "reason": row.reason,
    }


def _reference(row: AppDatasetReference) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "app_id": row.app_id,
        "dataset_id": row.dataset_id,
        "status": row.status,
        "revision": int(row.revision),
        "release_mode": row.release_mode,
        "release_channel_id": row.release_channel_id,
        "pinned_release_id": row.pinned_release_id,
    }


def _retired(session: Session, *, tenant_id: str, dataset_id: str, release_id: str) -> bool:
    return (
        session.scalar(
            select(DatasetReleaseEvent.id).where(
                DatasetReleaseEvent.tenant_id == tenant_id,
                DatasetReleaseEvent.dataset_id == dataset_id,
                DatasetReleaseEvent.release_id == release_id,
                DatasetReleaseEvent.event_type == "retired",
            )
        )
        is not None
    )


def _policy(
    session: Session, *, tenant_id: str, action_type: str, dataset_id: str
) -> dict[str, Any] | None:
    from core.enterprise_approval_control import resolve_active_approval_policy_in_session

    return resolve_active_approval_policy_in_session(
        session,
        tenant_id=tenant_id,
        action_type=action_type,
        resource_type=RESOURCE_KNOWLEDGE_BASE,
        resource_id=dataset_id,
        lock_for_update=True,
        tenant_already_locked=True,
    )


def _snapshot(
    *,
    action_type: str,
    dataset: Dataset,
    ownership: DatasetWorkspaceOwnership,
    workspace: TenantWorkspace,
    manifest: DatasetReleaseManifest,
    channel: TenantReleaseChannel,
    reason: str,
    active_release_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action_type": action_type,
        "dataset_id": dataset.id,
        "release_id": manifest.id,
        "release_number": int(manifest.release_number),
        "manifest_digest": manifest.manifest_digest,
        "channel_id": channel.id,
        "channel_revision": int(channel.revision),
        "profile_revision": int(dataset.profile_revision),
        "mutation_generation": int(dataset.mutation_generation),
        "ownership_revision": int(ownership.revision),
        "workspace_id": workspace.id,
        "workspace_revision": int(workspace.revision),
        "serving_generation": int(dataset.serving_generation),
        "reason": reason,
    }
    if active_release_id is not None:
        result["active_release_id"] = active_release_id
        result["target_release_id"] = manifest.id
    return result


def _approval_outcome(
    *,
    operation: str,
    resource_id: str,
    channel_revision: int,
    policy: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "state": "approval_required",
        "operation": operation,
        "resource_id": resource_id,
        "approval_request_id": None,
        "revision": channel_revision,
        "message": (
            "已匹配审批策略，请创建并完成审批申请"
            if policy is not None
            else "该高风险 Channel 尚未配置审批策略，不能执行"
        ),
        "retryable": False,
        "approval_policy": (
            {
                "id": str(policy.get("id", "")),
                "name": str(policy.get("name", "")),
                "required_approvals": int(policy.get("required_approvals", 1)),
            }
            if policy is not None
            else None
        ),
        "approval_snapshot": sanitize_audit_snapshot(dict(snapshot)),
    }


def _needs_approval(channel: TenantReleaseChannel, policy: Mapping[str, Any] | None) -> bool:
    return bool(policy is not None or channel.risk_tier == "high" or channel.is_default_serving)


_QUALITY_GATE_ACCEPTED_STATES = frozenset({"not_required", "passed", "waived"})
_QUALITY_GATE_KNOWN_STATES = _QUALITY_GATE_ACCEPTED_STATES | frozenset({"blocked", "unavailable"})
_QUALITY_GATE_REASON_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _quality_gate_summary(quality_gate: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(quality_gate, Mapping):
        raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
    state = quality_gate.get("state")
    reason = quality_gate.get("reason")
    if not isinstance(state, str) or state not in _QUALITY_GATE_KNOWN_STATES:
        raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
    if not isinstance(reason, str) or not _QUALITY_GATE_REASON_RE.fullmatch(reason):
        raise ReleaseManifestUnavailable("release quality gate authority is unavailable")

    def component(name: str) -> tuple[str | None, int | None]:
        value = quality_gate.get(name)
        if value is None:
            return None, None
        if not isinstance(value, Mapping):
            raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
        component_id = value.get("id")
        if not isinstance(component_id, str) or not component_id or len(component_id) > 128:
            raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
        revision = value.get("revision")
        if name == "policy":
            if type(revision) is not int or revision < 1:
                raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
            return component_id, revision
        return component_id, None

    policy_id, policy_revision = component("policy")
    certification_id, _ = component("certification")
    waiver_id, _ = component("waiver")

    def component_digest(name: str, field: str) -> str | None:
        value = quality_gate.get(name)
        if value is None:
            return None
        raw = value.get(field) if isinstance(value, Mapping) else None
        if raw is None:
            return None
        digest = str(raw).strip().casefold()
        if not _SHA256_RE.fullmatch(digest):
            raise ReleaseManifestUnavailable("release quality gate authority is unavailable")
        return digest

    summary = {
        "state": state,
        "reason": reason,
        "policy_id": policy_id,
        "policy_revision": policy_revision,
        "certification_id": certification_id,
        "waiver_id": waiver_id,
    }
    for field, digest in (
        ("policy_digest", component_digest("policy", "policy_digest")),
        (
            "certification_digest",
            component_digest("certification", "certification_digest"),
        ),
        ("evidence_digest", component_digest("certification", "evidence_digest")),
        ("waiver_digest", component_digest("waiver", "waiver_digest")),
    ):
        if digest is not None:
            summary[field] = digest
    return summary


def _require_quality_gate(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
) -> dict[str, Any]:
    from core.enterprise_release_quality_service import (
        ReleaseQualityError,
        resolve_release_quality_gate,
    )

    try:
        quality_gate = resolve_release_quality_gate(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            release_id=release_id,
            channel_id=channel_id,
            lock_evidence=True,
        )
    except ReleaseQualityError as exc:
        raise ReleaseManifestUnavailable("release quality gate authority is unavailable") from exc
    summary = _quality_gate_summary(quality_gate)
    if summary["state"] not in _QUALITY_GATE_ACCEPTED_STATES:
        raise ReleaseManifestConflict(f"release quality gate blocked: {summary['reason']}")
    return summary


def create_release_channel(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    code: str,
    name: str,
    risk_tier: str,
    promotion_order: int,
    is_default_serving: bool,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    display_code, normalized_code = _channel_code(code)
    clean_name = _safe_string(_clean(name, "name", 128), path="channel.name")
    risk = _risk(risk_tier)
    order = _exact_integer(promotion_order, "promotion_order", 0)
    if type(is_default_serving) is not bool:
        raise ReleaseManifestInvalid("is_default_serving must be an exact boolean")
    req, ip, key, clean_reason = _fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    request_hash = tenant_request_hash(
        operation="create_release_channel",
        path_identity={"tenant_id": tenant},
        body={
            "code": display_code,
            "normalized_code": normalized_code,
            "name": clean_name,
            "risk_tier": risk,
            "promotion_order": order,
            "is_default_serving": is_default_serving,
            "reason": clean_reason,
        },
    )
    with _scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="create_release_channel",
        resource_type="release_channel",
    ) as (session, member, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        _manager(member)
        if (
            session.scalar(
                select(TenantReleaseChannel.id).where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.normalized_code == normalized_code,
                )
            )
            is not None
        ):
            raise ReleaseManifestConflict("release channel normalized code already exists")
        now = datetime.utcnow()
        if is_default_serving:
            previous = session.scalar(
                select(TenantReleaseChannel.id)
                .where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.status == "active",
                    TenantReleaseChannel.is_default_serving.is_(True),
                )
                .with_for_update()
            )
            if previous is not None:
                raise ReleaseManifestConflict(
                    "default serving Channel switch requires a dedicated approved control"
                )
        row = TenantReleaseChannel(
            id=f"release-channel-{uuid.uuid4().hex}",
            tenant_id=tenant,
            code=display_code,
            normalized_code=normalized_code,
            name=clean_name,
            status="active",
            risk_tier=risk,
            promotion_order=order,
            is_default_serving=is_default_serving,
            active_default_slot="default" if is_default_serving else None,
            revision=1,
            created_at=now,
            created_by=actor,
            updated_at=now,
            updated_by=actor,
        )
        session.add(row)
        session.flush()
        payload = {
            "state": "applied",
            "operation": "channel_create",
            "resource_id": row.id,
            "channel": _channel(row),
            "revision": 1,
            "message": "Release Channel 已创建",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_channel.created",
            resource_type="release_channel",
            resource_id=row.id,
            before=None,
            after={"channel": payload["channel"], "reason": clean_reason},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload=payload, status=201, resource_id=row.id)


def update_release_channel(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    channel_id: str,
    expected_revision: int,
    name: str | None,
    risk_tier: str | None,
    promotion_order: int | None,
    is_default_serving: bool | None,
    status: str | None,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128)
    revision = _exact_integer(expected_revision, "expected_revision", 1)
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = _safe_string(_clean(name, "name", 128), path="channel.name")
    if risk_tier is not None:
        changes["risk_tier"] = _risk(risk_tier)
    if promotion_order is not None:
        changes["promotion_order"] = _exact_integer(promotion_order, "promotion_order", 0)
    if is_default_serving is not None:
        if type(is_default_serving) is not bool:
            raise ReleaseManifestInvalid("is_default_serving must be an exact boolean")
        changes["is_default_serving"] = is_default_serving
    if status is not None:
        changes["status"] = _status(status)
    if not changes:
        raise ReleaseManifestInvalid("channel update requires at least one field")
    req, ip, key, clean_reason = _fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    request_hash = tenant_request_hash(
        operation="update_release_channel",
        path_identity={"tenant_id": tenant, "channel_id": channel_key},
        body={"expected_revision": revision, **changes, "reason": clean_reason},
    )
    with _scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="update_release_channel",
        resource_type="release_channel",
    ) as (session, member, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        _manager(member)
        row = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant,
                TenantReleaseChannel.id == channel_key,
            )
            .with_for_update()
        )
        if row is None:
            raise ReleaseManifestNotFound("Release Channel does not exist")
        if int(row.revision) != revision:
            raise ReleaseManifestConflict("release channel revision conflict")
        before = _channel(row)
        target_status = changes.get("status", row.status)
        target_default = changes.get("is_default_serving", row.is_default_serving)
        if target_status == "archived":
            if row.is_default_serving:
                raise ReleaseManifestConflict("default serving channel cannot be archived")
            dataset_binding = session.scalar(
                select(DatasetChannelRelease.id).where(
                    DatasetChannelRelease.tenant_id == tenant,
                    DatasetChannelRelease.channel_id == row.id,
                    DatasetChannelRelease.status == "active",
                )
            )
            app_binding = session.scalar(
                select(AppDatasetReference.id).where(
                    AppDatasetReference.tenant_id == tenant,
                    AppDatasetReference.release_mode == "follow_channel",
                    AppDatasetReference.release_channel_id == row.id,
                    AppDatasetReference.status == "active",
                )
            )
            if dataset_binding is not None or app_binding is not None:
                raise ReleaseManifestConflict("release channel is still bound")
            target_default = False
        if row.is_default_serving and target_default is False and target_status == "active":
            raise ReleaseManifestConflict("Tenant must retain one default serving channel")
        now = datetime.utcnow()
        if target_default is True and not row.is_default_serving:
            previous = session.scalar(
                select(TenantReleaseChannel.id)
                .where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.status == "active",
                    TenantReleaseChannel.is_default_serving.is_(True),
                    TenantReleaseChannel.id != row.id,
                )
                .with_for_update()
            )
            if previous is not None:
                raise ReleaseManifestConflict(
                    "default serving Channel switch requires a dedicated approved control"
                )
        for field, value in changes.items():
            if field not in {"is_default_serving", "status"}:
                setattr(row, field, value)
        row.status = target_status
        row.is_default_serving = bool(target_default)
        row.active_default_slot = "default" if row.is_default_serving else None
        row.archived_at = now if target_status == "archived" else None
        row.archived_by = actor if target_status == "archived" else None
        row.revision += 1
        row.updated_at = now
        row.updated_by = actor
        session.flush()
        after = _channel(row)
        payload = {
            "state": "applied",
            "operation": "channel_update",
            "resource_id": row.id,
            "channel": after,
            "revision": int(row.revision),
            "message": "Release Channel 已更新",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_channel.updated",
            resource_type="release_channel",
            resource_id=row.id,
            before={"channel": before},
            after={"channel": after, "reason": clean_reason},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload=payload, status=200, resource_id=row.id)


def _validate_approval_fact(
    session: Session,
    fact: Any,
    *,
    tenant_id: str,
    action_type: str,
    dataset_id: str,
    release_id: str,
    manifest_digest: str,
    release_number: int,
    channel_id: str,
    channel_revision: int,
    profile_revision: int,
    mutation_generation: int,
    ownership_revision: int,
    workspace_id: str,
    workspace_revision: int,
    serving_generation: int,
    reason: str,
) -> None:
    from core.enterprise_approval_control import ApprovalExecutionFact, _ensure_0025, _execution_id

    if not isinstance(fact, ApprovalExecutionFact):
        raise ReleaseManifestInvalid("approval_execution_fact must be an internal fact")
    expected = {
        "tenant_id": tenant_id,
        "action_type": action_type,
        "resource_type": RESOURCE_KNOWLEDGE_BASE,
        "resource_id": dataset_id,
        "release_id": release_id,
        "manifest_digest": manifest_digest,
        "release_number": release_number,
        "channel_id": channel_id,
        "channel_revision": channel_revision,
        "profile_revision": profile_revision,
        "mutation_generation": mutation_generation,
        "ownership_revision": ownership_revision,
        "workspace_id": workspace_id,
        "workspace_revision": workspace_revision,
        "serving_generation": serving_generation,
        "reason": reason,
    }
    if any(getattr(fact, key, None) != value for key, value in expected.items()):
        raise ReleaseManifestConflict("approval execution fact does not match release authority")
    if (
        type(fact.request_revision) is not int
        or fact.request_revision < 1
        or type(fact.execution_revision) is not int
        or fact.execution_revision != fact.request_revision + 1
        or fact.execution_id != _execution_id(tenant_id, fact.approval_request_id)
        or not _SHA256_RE.fullmatch(str(fact.snapshot_hash).casefold())
    ):
        raise ReleaseManifestInvalid("approval execution fact identity is invalid")
    _ensure_0025(session.connection())
    requests = Table("tenant_approval_requests", MetaData(), autoload_with=session.connection())
    row = (
        session.execute(
            select(requests)
            .where(
                requests.c.tenant_id == tenant_id,
                requests.c.id == fact.approval_request_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ReleaseManifestConflict("approval execution request is missing")
    raw_snapshot = row["snapshot_json"]
    if isinstance(raw_snapshot, str):
        try:
            raw_snapshot = json.loads(raw_snapshot)
        except json.JSONDecodeError as exc:
            raise ReleaseManifestConflict("approval snapshot is invalid") from exc
    if not isinstance(raw_snapshot, Mapping):
        raise ReleaseManifestConflict("approval snapshot is invalid")
    canonical_hash = sha256(_canonical_json(dict(raw_snapshot)).encode()).hexdigest()
    snapshot_expected = {
        "action_type": action_type,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "release_number": release_number,
        "manifest_digest": manifest_digest,
        "channel_id": channel_id,
        "channel_revision": channel_revision,
        "profile_revision": profile_revision,
        "mutation_generation": mutation_generation,
        "ownership_revision": ownership_revision,
        "workspace_id": workspace_id,
        "workspace_revision": workspace_revision,
        "serving_generation": serving_generation,
        "reason": reason,
    }
    if (
        str(row["status"]) != "executing"
        or int(row["revision"]) != fact.execution_revision
        or str(row["action_type"]) != action_type
        or str(row["resource_type"]) != RESOURCE_KNOWLEDGE_BASE
        or str(row["resource_id"]) != dataset_id
        or str(row["payload_hash"]) != fact.snapshot_hash
        or canonical_hash != fact.snapshot_hash
        or row["execution_ticket_hash"] is None
        or row["ticket_consumed_at"] is None
        or str(row["reason"]) != reason
        or any(raw_snapshot.get(key) != value for key, value in snapshot_expected.items())
    ):
        raise ReleaseManifestConflict("approval execution fact is stale or scope mismatched")


def _release_authority(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
) -> tuple[
    Dataset,
    DatasetWorkspaceOwnership,
    TenantWorkspace,
    DatasetReleaseManifest,
    TenantReleaseChannel,
    DatasetChannelRelease | None,
]:
    dataset, ownership, workspace = _release_revisions(session, tenant_id, dataset_id)
    manifest = session.scalar(
        select(DatasetReleaseManifest)
        .where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
            DatasetReleaseManifest.id == release_id,
        )
        .with_for_update()
    )
    if manifest is None:
        raise ReleaseManifestNotFound()
    channel = session.scalar(
        select(TenantReleaseChannel)
        .where(
            TenantReleaseChannel.tenant_id == tenant_id,
            TenantReleaseChannel.id == channel_id,
        )
        .with_for_update()
    )
    if channel is None:
        raise ReleaseManifestNotFound("Release Channel does not exist")
    binding = session.scalar(
        select(DatasetChannelRelease)
        .where(
            DatasetChannelRelease.tenant_id == tenant_id,
            DatasetChannelRelease.dataset_id == dataset_id,
            DatasetChannelRelease.channel_id == channel_id,
        )
        .with_for_update()
    )
    return dataset, ownership, workspace, manifest, channel, binding


def _verify_manifest_current_authority(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    manifest: DatasetReleaseManifest,
) -> None:
    current_snapshot = collect_release_snapshot(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        release_number=int(manifest.release_number),
    )
    if current_snapshot.readiness_state != "ready":
        raise ReleaseManifestConflict("current release authority is not ready")
    persisted_rows = list(
        session.scalars(
            select(DatasetReleaseEntry)
            .where(
                DatasetReleaseEntry.tenant_id == tenant_id,
                DatasetReleaseEntry.dataset_id == dataset_id,
                DatasetReleaseEntry.release_id == manifest.id,
            )
            .order_by(DatasetReleaseEntry.ordinal, DatasetReleaseEntry.id)
        )
    )
    persisted_entries = canonicalize_release_entries(
        [
            {
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "resource_revision": int(row.resource_revision),
                "content_digest": row.content_digest,
                "facts": row.safe_facts_json or {},
            }
            for row in persisted_rows
        ]
    )
    if current_snapshot.entries != persisted_entries:
        raise ReleaseManifestConflict("current release authority drifted from Manifest")


def promote_release(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    expected_channel_revision: int,
    expected_profile_revision: int,
    expected_ownership_revision: int,
    expected_workspace_revision: int,
    expected_serving_generation: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    approval_execution_fact: Any | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    release_key = _clean(release_id, "release_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128)
    expected = {
        "channel": _exact_integer(expected_channel_revision, "expected_channel_revision", 1),
        "profile": _exact_integer(expected_profile_revision, "expected_profile_revision", 1),
        "ownership": _exact_integer(expected_ownership_revision, "expected_ownership_revision", 1),
        "workspace": _exact_integer(expected_workspace_revision, "expected_workspace_revision", 1),
        "serving": _exact_integer(expected_serving_generation, "expected_serving_generation", 0),
    }
    req, ip, key, clean_reason = _fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    approval_id = getattr(approval_execution_fact, "execution_id", None)
    request_hash = tenant_request_hash(
        operation="promote_release",
        path_identity={
            "tenant_id": tenant,
            "dataset_id": dataset_key,
            "release_id": release_key,
            "channel_id": channel_key,
        },
        body={**expected, "reason": clean_reason, "approval_execution_id": approval_id},
    )
    with _scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="promote_release",
        resource_type="knowledge_base_release",
    ) as (session, member, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        _require_manage(engine, session, member, dataset_key)
        dataset, ownership, workspace, manifest, channel, binding = _release_authority(
            session,
            tenant_id=tenant,
            dataset_id=dataset_key,
            release_id=release_key,
            channel_id=channel_key,
        )
        actual = {
            "profile": int(dataset.profile_revision),
            "mutation": int(dataset.mutation_generation),
            "ownership": int(ownership.revision),
            "workspace": int(workspace.revision),
            "serving": int(dataset.serving_generation),
        }
        if any(
            actual[name] != expected[name]
            for name in ("profile", "ownership", "workspace", "serving")
        ):
            raise ReleaseManifestConflict("release promotion revision fence changed")
        if int(channel.revision) != expected["channel"]:
            raise ReleaseManifestConflict("release channel revision conflict")
        if channel.status != "active":
            raise ReleaseManifestConflict("archived release channel cannot receive promotion")
        if manifest.readiness_state != "ready":
            raise ReleaseManifestConflict("release is not ready for promotion")
        if _retired(session, tenant_id=tenant, dataset_id=dataset_key, release_id=release_key):
            raise ReleaseManifestConflict("retired release cannot be promoted")
        if (
            int(manifest.profile_revision) != expected["profile"]
            or int(manifest.mutation_generation) != actual["mutation"]
            or int(manifest.ownership_revision) != expected["ownership"]
            or int(manifest.workspace_revision) != expected["workspace"]
        ):
            raise ReleaseManifestConflict("release manifest does not match configured authority")
        if binding is not None and binding.active_release_id == release_key:
            raise ReleaseManifestConflict("release is already active in channel")
        if str(session.connection().dialect.name).casefold() == "sqlite":
            session.execute(
                text(
                    "UPDATE datasets SET release_revision=release_revision "
                    "WHERE tenant_id=:tenant_id AND id=:dataset_id"
                ),
                {"tenant_id": tenant, "dataset_id": dataset_key},
            )
        _verify_manifest_current_authority(
            session,
            tenant_id=tenant,
            dataset_id=dataset_key,
            manifest=manifest,
        )
        from core.enterprise_release_quality_service import (
            ReleaseQualityError,
            resolve_release_quality_gate,
        )

        try:
            quality_gate = resolve_release_quality_gate(
                session,
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=release_key,
                channel_id=channel_key,
                lock_evidence=True,
            )
        except ReleaseQualityError as exc:
            raise ReleaseManifestUnavailable(
                "release quality gate authority is unavailable"
            ) from exc
        quality_gate_summary = _quality_gate_summary(quality_gate)
        if quality_gate_summary["state"] not in _QUALITY_GATE_ACCEPTED_STATES:
            raise ReleaseManifestConflict(
                f"release quality gate blocked: {quality_gate_summary['reason']}"
            )
        policy = _policy(
            session,
            tenant_id=tenant,
            action_type=ACTION_RELEASE_PUBLISH,
            dataset_id=dataset_key,
        )
        approval_snapshot = _snapshot(
            action_type=ACTION_RELEASE_PUBLISH,
            dataset=dataset,
            ownership=ownership,
            workspace=workspace,
            manifest=manifest,
            channel=channel,
            reason=clean_reason,
        )
        if _needs_approval(channel, policy):
            if approval_execution_fact is None:
                payload = _approval_outcome(
                    operation="promote",
                    resource_id=release_key,
                    channel_revision=int(channel.revision),
                    policy=policy,
                    snapshot=approval_snapshot,
                )
                return _complete(
                    session,
                    reservation,
                    payload=payload,
                    status=202,
                    resource_id=release_key,
                )
            if policy is None:
                raise ReleaseManifestConflict(
                    "high-risk/default-serving promotion requires an active approval policy"
                )
            _validate_approval_fact(
                session,
                approval_execution_fact,
                tenant_id=tenant,
                action_type=ACTION_RELEASE_PUBLISH,
                dataset_id=dataset_key,
                release_id=release_key,
                manifest_digest=manifest.manifest_digest,
                release_number=int(manifest.release_number),
                channel_id=channel_key,
                channel_revision=int(channel.revision),
                profile_revision=int(dataset.profile_revision),
                mutation_generation=int(dataset.mutation_generation),
                ownership_revision=int(ownership.revision),
                workspace_id=workspace.id,
                workspace_revision=int(workspace.revision),
                serving_generation=int(dataset.serving_generation),
                reason=clean_reason,
            )
        elif approval_execution_fact is not None:
            raise ReleaseManifestInvalid(
                "low-risk promotion does not accept approval execution fact"
            )
        _verify_manifest_current_authority(
            session,
            tenant_id=tenant,
            dataset_id=dataset_key,
            manifest=manifest,
        )
        now = datetime.utcnow()
        before_binding = _binding(binding) if binding is not None else None
        previous_release_id = binding.active_release_id if binding is not None else None
        previous_binding_revision = int(binding.revision) if binding is not None else 0
        if binding is None:
            binding = DatasetChannelRelease(
                id=f"release-binding-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                channel_id=channel_key,
                active_release_id=release_key,
                previous_release_id=None,
                status="active",
                active_slot="active",
                revision=1,
                activated_at=now,
                activated_by=actor,
                request_id=req,
                reason=clean_reason,
                created_at=now,
                updated_at=now,
                updated_by=actor,
            )
            session.add(binding)
        else:
            binding.previous_release_id = previous_release_id
            binding.active_release_id = release_key
            binding.status = "active"
            binding.active_slot = "active"
            binding.revision += 1
            binding.activated_at = now
            binding.activated_by = actor
            binding.request_id = req
            binding.reason = clean_reason
            binding.updated_at = now
            binding.updated_by = actor
        channel.revision += 1
        channel.updated_at = now
        channel.updated_by = actor
        if channel.is_default_serving:
            dataset.serving_release_id = release_key
            dataset.serving_generation += 1
            dataset.release_revision += 1
            dataset.updated_at = now
        session.flush()
        from core.enterprise_release_quality_evidence import canonical_quality_digest

        previous_quality_event = session.scalar(
            select(DatasetReleaseQualityEvent)
            .where(
                DatasetReleaseQualityEvent.tenant_id == tenant,
                DatasetReleaseQualityEvent.dataset_id == dataset_key,
                DatasetReleaseQualityEvent.release_id == release_key,
                DatasetReleaseQualityEvent.channel_id == channel_key,
            )
            .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
            .with_for_update()
        )
        quality_event_sequence = (
            int(previous_quality_event.event_sequence) + 1
            if previous_quality_event is not None
            else 1
        )
        previous_quality_digest = (
            previous_quality_event.event_digest if previous_quality_event is not None else None
        )
        quality_event_state = "active" if quality_gate["state"] == "waived" else "passed"
        quality_event_snapshot = {
            **quality_gate_summary,
            "binding_revision": int(binding.revision),
            "channel_revision": int(channel.revision),
            "release_manifest_digest": manifest.manifest_digest,
        }
        quality_event_digest = canonical_quality_digest(
            "quality_event",
            {
                "tenant_id": tenant,
                "dataset_id": dataset_key,
                "release_id": release_key,
                "channel_id": channel_key,
                "event_sequence": quality_event_sequence,
                "event_type": "release_promoted_with_quality_gate",
                "state": quality_event_state,
                "previous_event_digest": previous_quality_digest,
                "snapshot": quality_event_snapshot,
            },
        )
        session.add(
            DatasetReleaseQualityEvent(
                id=f"quality-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=release_key,
                channel_id=channel_key,
                certification_id=quality_gate_summary["certification_id"],
                waiver_id=quality_gate_summary["waiver_id"],
                event_type="release_promoted_with_quality_gate",
                event_sequence=quality_event_sequence,
                state=quality_event_state,
                previous_event_digest=previous_quality_digest,
                event_digest=quality_event_digest,
                approval_request_id=getattr(approval_execution_fact, "approval_request_id", None),
                approval_execution_id=approval_id,
                actor_id=actor,
                reason=clean_reason,
                safe_snapshot_json=quality_event_snapshot,
                request_id=req,
                occurred_at=now,
            )
        )
        if previous_release_id is not None:
            session.add(
                DatasetReleaseEvent(
                    id=f"release-event-{uuid.uuid4().hex}",
                    tenant_id=tenant,
                    dataset_id=dataset_key,
                    release_id=previous_release_id,
                    channel_id=channel_key,
                    event_type="superseded",
                    actor_id=actor,
                    reason=clean_reason,
                    request_id=req,
                    occurred_at=now,
                    previous_binding_revision=previous_binding_revision,
                    current_binding_revision=int(binding.revision),
                )
            )
        session.add(
            DatasetReleaseEvent(
                id=f"release-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=release_key,
                channel_id=channel_key,
                event_type="promoted",
                actor_id=actor,
                reason=clean_reason,
                request_id=req,
                occurred_at=now,
                previous_binding_revision=previous_binding_revision,
                current_binding_revision=int(binding.revision),
            )
        )
        after_binding = _binding(binding)
        payload = {
            "state": "applied",
            "operation": "promote",
            "resource_id": release_key,
            "approval_request_id": getattr(approval_execution_fact, "approval_request_id", None),
            "revision": int(channel.revision),
            "message": "Release 已发布到 Channel",
            "retryable": False,
            "release": _manifest_payload(manifest),
            "channel": _channel_payload(channel),
            "binding": after_binding,
            "quality_gate": quality_gate_summary,
            "serving": {
                "release_id": dataset.serving_release_id,
                "generation": int(dataset.serving_generation),
                "revision": int(dataset.release_revision),
            },
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release.promoted",
            resource_type="knowledge_base_release",
            resource_id=release_key,
            before={"binding": before_binding},
            after={
                "binding": after_binding,
                "channel_revision": int(channel.revision),
                "serving_release_id": dataset.serving_release_id,
                "serving_generation": int(dataset.serving_generation),
                "reason": clean_reason,
                "approval_execution_id": approval_id,
                "quality_gate": quality_gate_summary,
            },
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload=payload, status=200, resource_id=release_key)


def rollback_channel_release(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    channel_id: str,
    target_release_id: str,
    expected_channel_revision: int,
    expected_serving_generation: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    approval_execution_fact: Any | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128)
    target_key = _clean(target_release_id, "target_release_id", 64)
    channel_revision = _exact_integer(expected_channel_revision, "expected_channel_revision", 1)
    serving_generation = _exact_integer(
        expected_serving_generation, "expected_serving_generation", 0
    )
    req, ip, key, clean_reason = _fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    approval_id = getattr(approval_execution_fact, "execution_id", None)
    request_hash = tenant_request_hash(
        operation="rollback_channel_release",
        path_identity={
            "tenant_id": tenant,
            "dataset_id": dataset_key,
            "channel_id": channel_key,
        },
        body={
            "target_release_id": target_key,
            "channel_revision": channel_revision,
            "serving_generation": serving_generation,
            "reason": clean_reason,
            "approval_execution_id": approval_id,
        },
    )
    with _scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="rollback_channel_release",
        resource_type="knowledge_base_release",
    ) as (session, member, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        _require_manage(engine, session, member, dataset_key)
        dataset, ownership, workspace = _release_revisions(session, tenant, dataset_key)
        if int(dataset.serving_generation) != serving_generation:
            raise ReleaseManifestConflict("dataset serving generation conflict")
        channel = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant,
                TenantReleaseChannel.id == channel_key,
            )
            .with_for_update()
        )
        if channel is None:
            raise ReleaseManifestNotFound("Release Channel does not exist")
        if channel.status != "active":
            raise ReleaseManifestConflict("archived release channel cannot be rolled back")
        if int(channel.revision) != channel_revision:
            raise ReleaseManifestConflict("release channel revision conflict")
        binding = session.scalar(
            select(DatasetChannelRelease)
            .where(
                DatasetChannelRelease.tenant_id == tenant,
                DatasetChannelRelease.dataset_id == dataset_key,
                DatasetChannelRelease.channel_id == channel_key,
                DatasetChannelRelease.status == "active",
            )
            .with_for_update()
        )
        if binding is None or binding.active_release_id is None:
            raise ReleaseManifestConflict("release channel has no active binding")
        if binding.active_release_id == target_key:
            raise ReleaseManifestConflict("target release is already active")
        target = session.scalar(
            select(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant,
                DatasetReleaseManifest.dataset_id == dataset_key,
                DatasetReleaseManifest.id == target_key,
            )
            .with_for_update()
        )
        if target is None:
            raise ReleaseManifestNotFound("rollback target Release does not exist")
        if target.readiness_state != "ready" or _retired(
            session, tenant_id=tenant, dataset_id=dataset_key, release_id=target_key
        ):
            raise ReleaseManifestConflict("retired or unready release cannot be rollback target")
        prior_event = session.scalar(
            select(DatasetReleaseEvent.id).where(
                DatasetReleaseEvent.tenant_id == tenant,
                DatasetReleaseEvent.dataset_id == dataset_key,
                DatasetReleaseEvent.release_id == target_key,
                DatasetReleaseEvent.channel_id == channel_key,
                DatasetReleaseEvent.event_type.in_(("promoted", "rolled_back")),
            )
        )
        if prior_event is None:
            raise ReleaseManifestConflict("rollback target was never active in this channel")
        quality_gate_summary = _require_quality_gate(
            session,
            tenant_id=tenant,
            dataset_id=dataset_key,
            release_id=target_key,
            channel_id=channel_key,
        )
        policy = _policy(
            session,
            tenant_id=tenant,
            action_type=ACTION_RELEASE_ROLLBACK,
            dataset_id=dataset_key,
        )
        approval_snapshot = _snapshot(
            action_type=ACTION_RELEASE_ROLLBACK,
            dataset=dataset,
            ownership=ownership,
            workspace=workspace,
            manifest=target,
            channel=channel,
            reason=clean_reason,
            active_release_id=binding.active_release_id,
        )
        if _needs_approval(channel, policy):
            if approval_execution_fact is None:
                payload = _approval_outcome(
                    operation="rollback",
                    resource_id=target_key,
                    channel_revision=int(channel.revision),
                    policy=policy,
                    snapshot=approval_snapshot,
                )
                return _complete(
                    session,
                    reservation,
                    payload=payload,
                    status=202,
                    resource_id=target_key,
                )
            if policy is None:
                raise ReleaseManifestConflict(
                    "high-risk/default-serving rollback requires an active approval policy"
                )
            _validate_approval_fact(
                session,
                approval_execution_fact,
                tenant_id=tenant,
                action_type=ACTION_RELEASE_ROLLBACK,
                dataset_id=dataset_key,
                release_id=target_key,
                manifest_digest=target.manifest_digest,
                release_number=int(target.release_number),
                channel_id=channel_key,
                channel_revision=int(channel.revision),
                profile_revision=int(dataset.profile_revision),
                mutation_generation=int(dataset.mutation_generation),
                ownership_revision=int(ownership.revision),
                workspace_id=workspace.id,
                workspace_revision=int(workspace.revision),
                serving_generation=int(dataset.serving_generation),
                reason=clean_reason,
            )
        elif approval_execution_fact is not None:
            raise ReleaseManifestInvalid(
                "low-risk rollback does not accept approval execution fact"
            )
        now = datetime.utcnow()
        before_binding = _binding(binding)
        current_release_id = binding.active_release_id
        previous_binding_revision = int(binding.revision)
        binding.previous_release_id = current_release_id
        binding.active_release_id = target_key
        binding.revision += 1
        binding.activated_at = now
        binding.activated_by = actor
        binding.request_id = req
        binding.reason = clean_reason
        binding.updated_at = now
        binding.updated_by = actor
        channel.revision += 1
        channel.updated_at = now
        channel.updated_by = actor
        if channel.is_default_serving:
            dataset.serving_release_id = target_key
            dataset.serving_generation += 1
            dataset.release_revision += 1
            dataset.updated_at = now
        session.flush()
        from core.enterprise_release_quality import canonical_quality_digest

        previous_quality_event = session.scalar(
            select(DatasetReleaseQualityEvent)
            .where(
                DatasetReleaseQualityEvent.tenant_id == tenant,
                DatasetReleaseQualityEvent.dataset_id == dataset_key,
                DatasetReleaseQualityEvent.release_id == target_key,
                DatasetReleaseQualityEvent.channel_id == channel_key,
            )
            .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
            .with_for_update()
        )
        quality_event_sequence = (
            int(previous_quality_event.event_sequence) + 1
            if previous_quality_event is not None
            else 1
        )
        previous_quality_digest = (
            previous_quality_event.event_digest if previous_quality_event is not None else None
        )
        quality_event_state = "active" if quality_gate_summary["state"] == "waived" else "passed"
        quality_event_snapshot = {
            **quality_gate_summary,
            "binding_revision": int(binding.revision),
            "channel_revision": int(channel.revision),
            "release_manifest_digest": target.manifest_digest,
        }
        quality_event_digest = canonical_quality_digest(
            "quality_event",
            {
                "tenant_id": tenant,
                "dataset_id": dataset_key,
                "release_id": target_key,
                "channel_id": channel_key,
                "event_sequence": quality_event_sequence,
                "event_type": "release_rolled_back_with_quality_gate",
                "state": quality_event_state,
                "previous_event_digest": previous_quality_digest,
                "snapshot": quality_event_snapshot,
            },
        )
        session.add(
            DatasetReleaseQualityEvent(
                id=f"quality-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=target_key,
                channel_id=channel_key,
                certification_id=quality_gate_summary["certification_id"],
                waiver_id=quality_gate_summary["waiver_id"],
                event_type="release_rolled_back_with_quality_gate",
                event_sequence=quality_event_sequence,
                state=quality_event_state,
                previous_event_digest=previous_quality_digest,
                event_digest=quality_event_digest,
                approval_request_id=getattr(approval_execution_fact, "approval_request_id", None),
                approval_execution_id=approval_id,
                actor_id=actor,
                reason=clean_reason,
                safe_snapshot_json=quality_event_snapshot,
                request_id=req,
                occurred_at=now,
            )
        )
        session.add(
            DatasetReleaseEvent(
                id=f"release-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=target_key,
                channel_id=channel_key,
                event_type="rolled_back",
                actor_id=actor,
                reason=clean_reason,
                request_id=req,
                occurred_at=now,
                previous_binding_revision=previous_binding_revision,
                current_binding_revision=int(binding.revision),
            )
        )
        after_binding = _binding(binding)
        payload = {
            "state": "applied",
            "operation": "rollback",
            "resource_id": target_key,
            "approval_request_id": getattr(approval_execution_fact, "approval_request_id", None),
            "revision": int(channel.revision),
            "message": "Release Channel 已回滚",
            "retryable": False,
            "release": _manifest_payload(target),
            "channel": _channel_payload(channel),
            "binding": after_binding,
            "quality_gate": quality_gate_summary,
            "serving": {
                "release_id": dataset.serving_release_id,
                "generation": int(dataset.serving_generation),
                "revision": int(dataset.release_revision),
            },
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release.rolled_back",
            resource_type="knowledge_base_release",
            resource_id=target_key,
            before={"binding": before_binding},
            after={
                "binding": after_binding,
                "channel_revision": int(channel.revision),
                "serving_release_id": dataset.serving_release_id,
                "serving_generation": int(dataset.serving_generation),
                "reason": clean_reason,
                "approval_execution_id": approval_id,
                "quality_gate": quality_gate_summary,
            },
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload=payload, status=200, resource_id=target_key)


def update_app_release_binding(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    app_id: str,
    dataset_id: str,
    expected_reference_revision: int,
    release_mode: str,
    release_channel_id: str | None,
    pinned_release_id: str | None,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    app_key = _clean(app_id, "app_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    revision = _exact_integer(expected_reference_revision, "expected_reference_revision", 1)
    mode = _clean(release_mode, "release_mode", 16).casefold()
    if mode not in _RELEASE_MODES:
        raise ReleaseManifestInvalid("release_mode is invalid")
    channel_key = (
        _clean(release_channel_id, "release_channel_id", 128)
        if release_channel_id is not None
        else None
    )
    pinned_key = (
        _clean(pinned_release_id, "pinned_release_id", 64)
        if pinned_release_id is not None
        else None
    )
    if not (
        (mode == "follow_channel" and channel_key is not None and pinned_key is None)
        or (mode == "pinned" and channel_key is None and pinned_key is not None)
    ):
        raise ReleaseManifestInvalid("release binding requires exactly one channel or pin")
    req, ip, key, clean_reason = _fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    request_hash = tenant_request_hash(
        operation="update_app_release_binding",
        path_identity={
            "tenant_id": tenant,
            "app_id": app_key,
            "dataset_id": dataset_key,
        },
        body={
            "expected_reference_revision": revision,
            "release_mode": mode,
            "release_channel_id": channel_key,
            "pinned_release_id": pinned_key,
            "reason": clean_reason,
        },
    )
    with _scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="update_app_release_binding",
        resource_type="application_release_binding",
    ) as (session, member, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        _require_manage(engine, session, member, dataset_key)
        app = session.scalar(
            select(App).where(App.tenant_id == tenant, App.id == app_key).with_for_update()
        )
        if app is None:
            raise ReleaseManifestNotFound("Application does not exist")
        dataset = session.scalar(
            select(Dataset)
            .where(Dataset.tenant_id == tenant, Dataset.id == dataset_key)
            .with_for_update()
        )
        if dataset is None:
            raise ReleaseManifestNotFound("Knowledge Base does not exist")
        row = session.scalar(
            select(AppDatasetReference)
            .where(
                AppDatasetReference.tenant_id == tenant,
                AppDatasetReference.app_id == app_key,
                AppDatasetReference.dataset_id == dataset_key,
                AppDatasetReference.reference_kind == "knowledge",
                AppDatasetReference.status == "active",
                AppDatasetReference.active_slot == "active",
            )
            .with_for_update()
        )
        if row is None:
            raise ReleaseManifestNotFound("active Application reference does not exist")
        if int(row.revision) != revision:
            raise ReleaseManifestConflict("Application reference revision conflict")
        quality_gate_summary: dict[str, Any] | None = None
        quality_gate_channel_id: str | None = None
        if channel_key is not None:
            channel = session.scalar(
                select(TenantReleaseChannel)
                .where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.id == channel_key,
                    TenantReleaseChannel.status == "active",
                )
                .with_for_update()
            )
            if channel is None:
                raise ReleaseManifestNotFound("active Release Channel does not exist")
        if pinned_key is not None:
            release = session.scalar(
                select(DatasetReleaseManifest)
                .where(
                    DatasetReleaseManifest.tenant_id == tenant,
                    DatasetReleaseManifest.dataset_id == dataset_key,
                    DatasetReleaseManifest.id == pinned_key,
                )
                .with_for_update()
            )
            if release is None:
                raise ReleaseManifestNotFound("pinned Release does not exist")
            if release.readiness_state != "ready":
                raise ReleaseManifestConflict("blocked or unavailable release cannot be pinned")
            if _retired(session, tenant_id=tenant, dataset_id=dataset_key, release_id=pinned_key):
                raise ReleaseManifestConflict("retired release cannot be pinned")
            default_channels = list(
                session.scalars(
                    select(TenantReleaseChannel)
                    .where(
                        TenantReleaseChannel.tenant_id == tenant,
                        TenantReleaseChannel.status == "active",
                        TenantReleaseChannel.is_default_serving.is_(True),
                    )
                    .order_by(TenantReleaseChannel.id)
                    .with_for_update()
                )
            )
            if len(default_channels) != 1:
                raise ReleaseManifestUnavailable(
                    "default serving Release Channel authority is unavailable"
                )
            quality_gate_channel_id = default_channels[0].id
            quality_gate_summary = _require_quality_gate(
                session,
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=pinned_key,
                channel_id=quality_gate_channel_id,
            )
        before = _reference(row)
        now = datetime.utcnow()
        row.release_mode = mode
        row.release_channel_id = channel_key
        row.pinned_release_id = pinned_key
        row.revision += 1
        row.updated_at = now
        row.updated_by = actor
        row.request_id = req
        session.flush()
        after = _reference(row)
        payload = {
            "state": "applied",
            "operation": "app_release_binding",
            "resource_id": row.id,
            "revision": int(row.revision),
            "message": "Application Release binding 已更新",
            "retryable": False,
            "reference": after,
        }
        if quality_gate_summary is not None:
            payload["quality_gate"] = quality_gate_summary
            payload["quality_gate_channel_id"] = quality_gate_channel_id
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.application_release_binding.updated",
            resource_type="application_release_binding",
            resource_id=row.id,
            before={"reference": before},
            after={
                "reference": after,
                "reason": clean_reason,
                **(
                    {
                        "quality_gate": quality_gate_summary,
                        "quality_gate_channel_id": quality_gate_channel_id,
                    }
                    if quality_gate_summary is not None
                    else {}
                ),
            },
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload=payload, status=200, resource_id=row.id)


__all__ = [
    "ACTION_RELEASE_PUBLISH",
    "ACTION_RELEASE_ROLLBACK",
    "create_release_channel",
    "promote_release",
    "rollback_channel_release",
    "update_app_release_binding",
    "update_release_channel",
]
