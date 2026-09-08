"""Approval-backed, Release-scoped quality waiver authority.

This module is deliberately a sidecar to the existing Release Quality service.
It accepts only an opaque ApprovalExecutionFact at grant time and persists a
bounded, immutable waiver receipt. Approval requests are created from the same
transaction as the waiver_requested quality event so the request snapshot and
quality stream cannot drift apart.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Iterator, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core import enterprise_release_quality_service as _quality_service
from core.enterprise_approval_control import (
    ApprovalExecutionFact,
    _ensure_0025,
    _redact_snapshot,
    _request_payload,
    _scope_matches,
    _table,
    _transaction,
)
from core.enterprise_release_quality_evidence import canonical_quality_digest
from core.enterprise_release_quality_service import (
    ReleaseQualityConflict,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    ReleaseQualityNotFound,
    ReleaseQualityUnavailable,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot
from models.orm import (
    Account,
    DatasetReleaseManifest,
    DatasetReleaseQualityEvent,
    DatasetReleaseQualityWaiver,
    Tenant,
    TenantAuditEvent,
    TenantReleaseChannel,
    TenantReleaseQualityGatePolicy,
)

ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER = "knowledge_base_release_quality_waiver"
RESOURCE_KNOWLEDGE_BASE = "knowledge_base"
QUALITY_WAIVER_OPERATION = "release_quality_waiver"
_ALLOWED_GATE_REASONS = frozenset(
    {
        "certification_required",
        "certification_failed",
        "certification_expired",
        "certification_stale",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    try:
        return _quality_service._clean(value, field, maximum, allow_empty=allow_empty)
    except ReleaseQualityInvalid:
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ReleaseQualityInvalid(f"{field} is invalid") from exc


def _reason(value: Any) -> str:
    try:
        return _quality_service._reason(value)
    except ReleaseQualityInvalid:
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise ReleaseQualityInvalid("reason is invalid") from exc


def _as_datetime(value: Any, field: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReleaseQualityInvalid(f"{field} is invalid") from exc
    if not isinstance(value, datetime):
        raise ReleaseQualityInvalid(f"{field} must be a datetime")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        value = value.replace(tzinfo=None)
    return value


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.casefold()):
        raise ReleaseQualityConflict(f"{field} is invalid")
    return value.casefold()


def _safe_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fact_value(fact: ApprovalExecutionFact, *names: str) -> Any:
    values = [getattr(fact, name, None) for name in names]
    present = [value for value in values if value is not None]
    if not present:
        return None
    if any(value != present[0] for value in present[1:]):
        raise ReleaseQualityConflict("approval execution fact aliases do not agree")
    return present[0]


def _fact_text(fact: ApprovalExecutionFact, field: str, maximum: int) -> str:
    value = _fact_value(fact, field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ReleaseQualityConflict(f"approval execution fact {field} is invalid")
    return value.strip()


def _fact_int(fact: ApprovalExecutionFact, field: str, minimum: int = 1) -> int:
    value = _fact_value(fact, field)
    if type(value) is not int or value < minimum:
        raise ReleaseQualityConflict(f"approval execution fact {field} is invalid")
    return value


def _validate_fact(fact: Any) -> ApprovalExecutionFact:
    if not isinstance(fact, ApprovalExecutionFact):
        raise ReleaseQualityConflict("approval execution fact must be opaque internal authority")
    if fact.action_type != ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER:
        raise ReleaseQualityConflict("approval execution fact action mismatch")
    if fact.resource_type != RESOURCE_KNOWLEDGE_BASE:
        raise ReleaseQualityConflict("approval execution fact resource type mismatch")
    if not fact.reason.strip():
        raise ReleaseQualityConflict("approval execution fact reason is invalid")
    _fact_text(fact, "release_id", 64)
    _fact_int(fact, "release_number")
    _sha256(fact.manifest_digest, field="manifest_digest")
    _fact_text(fact, "channel_id", 128)
    _fact_int(fact, "channel_revision")
    _fact_text(fact, "policy_id", 64)
    _fact_int(fact, "policy_revision")
    _sha256(fact.policy_digest, field="policy_digest")
    _sha256(fact.quality_gate_digest, field="quality_gate_digest")
    evidence_digest = _fact_value(fact, "quality_evidence_digest", "evidence_digest")
    if evidence_digest is not None:
        _sha256(evidence_digest, field="quality_evidence_digest")
    expiry = _fact_value(fact, "waiver_expires_at", "requested_expires_at")
    if not isinstance(expiry, str):
        raise ReleaseQualityConflict("approval execution fact waiver expiry is invalid")
    expiry_dt = _as_datetime(expiry, "waiver_expires_at")
    if _iso(expiry_dt) != expiry:
        raise ReleaseQualityConflict("approval execution fact waiver expiry is not canonical")
    for field, minimum in (
        ("profile_revision", 1),
        ("ownership_revision", 1),
        ("workspace_revision", 1),
        ("serving_generation", 0),
        ("mutation_generation", 0),
    ):
        _fact_int(fact, field, minimum)
    _fact_text(fact, "workspace_id", 128)
    return fact


def _gate_binding_snapshot(gate: Mapping[str, Any]) -> dict[str, Any]:
    policy = gate.get("policy")
    certification = gate.get("certification")
    if policy is not None and not isinstance(policy, Mapping):
        raise ReleaseQualityUnavailable("quality gate policy projection is malformed")
    if certification is not None and not isinstance(certification, Mapping):
        raise ReleaseQualityUnavailable("quality gate certification projection is malformed")
    return {
        "state": gate.get("state"),
        "reason": gate.get("reason"),
        "release_manifest_digest": gate.get("release_manifest_digest"),
        "channel_revision": gate.get("channel_revision"),
        "policy_id": policy.get("id") if policy else None,
        "policy_revision": policy.get("revision") if policy else None,
        "policy_digest": policy.get("policy_digest") if policy else None,
        "certification_id": certification.get("id") if certification else None,
        "certification_digest": certification.get("certification_digest")
        if certification
        else None,
        "quality_evidence_digest": certification.get("evidence_digest") if certification else None,
        "certification_valid_until": certification.get("valid_until") if certification else None,
    }


def _quality_evidence_digest(gate: Mapping[str, Any]) -> str | None:
    value = _gate_binding_snapshot(gate).get("quality_evidence_digest")
    return value if isinstance(value, str) else None


def _waiver_payload(row: DatasetReleaseQualityWaiver) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "release_id": row.release_id,
        "channel_id": row.channel_id,
        "policy_id": row.policy_id,
        "policy_revision": int(row.policy_revision),
        "release_manifest_digest": row.release_manifest_digest,
        "approval_request_id": row.approval_request_id,
        "approval_execution_id": row.approval_execution_id,
        "reason": row.reason,
        "valid_from": _iso(row.valid_from),
        "expires_at": _iso(row.expires_at),
        "waiver_digest": row.waiver_digest,
        "created_at": _iso(row.created_at),
        "created_by": row.created_by,
        "request_id": row.request_id,
    }


def _result_payload(
    row: DatasetReleaseQualityWaiver, *, event: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "state": "applied",
        "operation": QUALITY_WAIVER_OPERATION,
        "resource_id": row.id,
        "waiver": _waiver_payload(row),
        "event": dict(event),
        "message": "Release quality waiver applied",
        "retryable": False,
    }


def _append_event(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    event_type: str,
    state: str | None,
    certification_id: str | None,
    waiver_id: str | None,
    approval_request_id: str | None,
    approval_execution_id: str | None,
    actor_id: str,
    reason: str,
    snapshot: Mapping[str, Any],
    request_id: str,
    now: datetime,
) -> dict[str, Any]:
    previous = session.scalar(
        select(DatasetReleaseQualityEvent)
        .where(
            DatasetReleaseQualityEvent.tenant_id == tenant_id,
            DatasetReleaseQualityEvent.dataset_id == dataset_id,
            DatasetReleaseQualityEvent.release_id == release_id,
            DatasetReleaseQualityEvent.channel_id == channel_id,
        )
        .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
        .with_for_update()
    )
    sequence = int(previous.event_sequence) + 1 if previous is not None else 1
    previous_digest = previous.event_digest if previous is not None else None
    safe_snapshot = sanitize_audit_snapshot(dict(snapshot))
    event_digest = canonical_quality_digest(
        "quality_event",
        {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
            "release_id": release_id,
            "channel_id": channel_id,
            "event_sequence": sequence,
            "event_type": event_type,
            "state": state,
            "previous_event_digest": previous_digest,
            "snapshot": safe_snapshot,
        },
    )
    event_id = f"quality-event-{uuid.uuid4().hex}"
    session.add(
        DatasetReleaseQualityEvent(
            id=event_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            release_id=release_id,
            channel_id=channel_id,
            certification_id=certification_id,
            waiver_id=waiver_id,
            event_type=event_type,
            event_sequence=sequence,
            state=state,
            previous_event_digest=previous_digest,
            event_digest=event_digest,
            approval_request_id=approval_request_id,
            approval_execution_id=approval_execution_id,
            actor_id=actor_id,
            reason=reason,
            safe_snapshot_json=safe_snapshot,
            request_id=request_id,
            occurred_at=now,
        )
    )
    session.flush()
    return {"id": event_id, "sequence": sequence, "event_digest": event_digest}


def _audit(
    session: Session,
    *,
    account: Account,
    tenant_id: str,
    actor_id: str,
    action: str,
    resource_id: str,
    after: Mapping[str, Any],
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
            resource_type="release_quality_waiver",
            resource_id=resource_id,
            before_snapshot=None,
            after_snapshot=sanitize_audit_snapshot(dict(after)),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )
    session.flush()


@contextmanager
def _waiver_mutation_scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
) -> Iterator[tuple[Session, Account, Any]]:
    try:
        digest = tenant_idempotency_key_digest(tenant_id, actor_id, idempotency_key)
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with idempotency_key_lock(tenant_id, actor_id, digest):
        with engine_serialization_lock(engine):
            with Session(engine, expire_on_commit=False) as session:
                with _transaction(session):
                    _quality_service._ensure_capability(session.connection())
                    _ensure_0025(session.connection())
                    tenant = session.scalar(
                        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
                    )
                    if tenant is None or tenant.status != "active":
                        raise ReleaseQualityForbidden("Tenant is unavailable")
                    membership, account = _quality_service._actor_scope(
                        session, tenant_id, actor_id
                    )
                    _quality_service._require_dataset_manage(
                        engine, session, membership, dataset_id
                    )
                    try:
                        reservation = reserve_tenant_mutation(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            raw_idempotency_key=idempotency_key,
                            request_hash=request_hash,
                            operation=operation,
                            resource_type="release_quality_waiver",
                        )
                    except TenantMutationIdempotencyConflict as exc:
                        raise ReleaseQualityConflict("idempotency key conflict") from exc
                    except TenantMutationIdempotencyInProgress as exc:
                        raise ReleaseQualityConflict("idempotency request is in progress") from exc
                    except TenantMutationIdempotencyValidationError as exc:
                        raise ReleaseQualityInvalid(str(exc)) from exc
                    try:
                        yield session, account, reservation
                    except IntegrityError as exc:
                        raise ReleaseQualityConflict(
                            "Release quality waiver violates an authority constraint"
                        ) from exc
                    except SQLAlchemyError as exc:
                        raise ReleaseQualityUnavailable() from exc


def _complete(
    session: Session, reservation: Any, payload: Mapping[str, Any], status: int, resource_id: str
) -> dict[str, Any]:
    try:
        return complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc


def _normalise_common_inputs(
    *,
    tenant_id: Any,
    actor_id: Any,
    dataset_id: Any,
    release_id: Any,
    channel_id: Any,
    policy_id: Any,
    expected_policy_revision: Any,
    expected_channel_revision: Any,
    approval_policy_id: Any,
    requested_expires_at: Any,
    reason: Any,
    request_id: Any,
    request_ip: Any,
    idempotency_key: Any,
    now: Any,
) -> tuple[Any, ...]:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    release = _clean(release_id, "release_id", 64)
    channel = _clean(channel_id, "channel_id", 128)
    policy = _clean(policy_id, "policy_id", 64)
    if type(expected_policy_revision) is not int or expected_policy_revision < 1:
        raise ReleaseQualityInvalid("expected_policy_revision must be an exact integer >= 1")
    if type(expected_channel_revision) is not int or expected_channel_revision < 1:
        raise ReleaseQualityInvalid("expected_channel_revision must be an exact integer >= 1")
    approval_policy = _clean(approval_policy_id, "approval_policy_id", 64)
    expires = _as_datetime(requested_expires_at, "requested_expires_at")
    clean_reason = _reason(reason)
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    key = _clean(idempotency_key, "idempotency_key", 128)
    current = _as_datetime(now if now is not None else datetime.utcnow(), "now")
    return (
        tenant,
        actor,
        dataset,
        release,
        channel,
        policy,
        expected_policy_revision,
        expected_channel_revision,
        approval_policy,
        expires,
        clean_reason,
        req,
        ip,
        key,
        current,
    )


def request_quality_waiver(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    policy_id: str,
    expected_policy_revision: int,
    expected_channel_revision: int,
    approval_policy_id: str,
    requested_expires_at: datetime,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> Any:
    (
        tenant,
        actor,
        dataset,
        release,
        channel,
        policy,
        policy_revision,
        channel_revision,
        approval_policy,
        expires,
        clean_reason,
        req,
        ip,
        key,
        current,
    ) = _normalise_common_inputs(
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        release_id=release_id,
        channel_id=channel_id,
        policy_id=policy_id,
        expected_policy_revision=expected_policy_revision,
        expected_channel_revision=expected_channel_revision,
        approval_policy_id=approval_policy_id,
        requested_expires_at=requested_expires_at,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        now=now,
    )
    request_hash = tenant_request_hash(
        operation="request_quality_waiver",
        path_identity={
            "tenant_id": tenant,
            "dataset_id": dataset,
            "release_id": release,
            "channel_id": channel,
        },
        body={
            "policy_id": policy,
            "expected_policy_revision": policy_revision,
            "expected_channel_revision": channel_revision,
            "approval_policy_id": approval_policy,
            "requested_expires_at": _iso(expires),
            "reason": clean_reason,
        },
    )
    with _waiver_mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        dataset_id=dataset,
        idempotency_key=key,
        request_hash=request_hash,
        operation="request_quality_waiver",
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return _quality_service.ServiceResult(
                dict(reservation.replay.response), reservation.replay.http_status
            )
        release_row = session.scalar(
            select(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant,
                DatasetReleaseManifest.dataset_id == dataset,
                DatasetReleaseManifest.id == release,
            )
            .with_for_update()
        )
        if release_row is None:
            raise ReleaseQualityNotFound("Release does not exist")
        channel_row = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant,
                TenantReleaseChannel.id == channel,
            )
            .with_for_update()
        )
        if channel_row is None or channel_row.status != "active":
            raise ReleaseQualityNotFound("Release Channel is unavailable")
        if int(channel_row.revision) != channel_revision:
            raise ReleaseQualityConflict("Release Channel revision changed")
        quality_policy = session.scalar(
            select(TenantReleaseQualityGatePolicy)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant,
                TenantReleaseQualityGatePolicy.id == policy,
            )
            .with_for_update()
        )
        if quality_policy is None or quality_policy.status != "active":
            raise ReleaseQualityNotFound("Quality gate policy does not exist")
        if int(quality_policy.revision) != policy_revision:
            raise ReleaseQualityConflict("Quality gate policy revision changed")
        if expires <= current:
            raise ReleaseQualityInvalid("requested_expires_at must be in the future")
        if expires > current + timedelta(minutes=int(quality_policy.max_certification_age_minutes)):
            raise ReleaseQualityInvalid("requested_expires_at exceeds policy maximum age")

        gate = _quality_service.resolve_release_quality_gate(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release,
            channel_id=channel,
            now=current,
        )
        gate_policy = gate.get("policy")
        if (
            not isinstance(gate_policy, Mapping)
            or gate_policy.get("id") != policy
            or gate_policy.get("revision") != policy_revision
            or gate_policy.get("policy_digest") != quality_policy.policy_digest
        ):
            raise ReleaseQualityConflict("quality gate policy binding is stale")
        if gate.get("state") == "unavailable":
            raise ReleaseQualityUnavailable("quality gate is unavailable")
        if gate.get("state") != "blocked" or gate.get("reason") not in _ALLOWED_GATE_REASONS:
            raise ReleaseQualityConflict("quality gate does not require a waiver")
        gate_snapshot = _gate_binding_snapshot(gate)
        gate_digest = canonical_quality_digest("quality_gate", gate_snapshot)
        evidence_digest = _quality_evidence_digest(gate)

        approval_table = _table(session, "tenant_approval_policies")
        approval_row = (
            session.execute(
                select(approval_table)
                .where(
                    approval_table.c.tenant_id == tenant,
                    approval_table.c.id == approval_policy,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if approval_row is None:
            raise ReleaseQualityNotFound("Approval policy does not exist")
        if str(approval_row["status"]) != "active":
            raise ReleaseQualityConflict("Approval policy is disabled")
        if str(approval_row["action_type"]) != ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER:
            raise ReleaseQualityConflict(
                "Approval policy action does not authorize quality waivers"
            )
        if not _scope_matches(approval_row.get("resource_scope"), RESOURCE_KNOWLEDGE_BASE, dataset):
            raise ReleaseQualityConflict("Approval policy scope does not match Dataset")

        snapshot = {
            "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
            "resource_type": RESOURCE_KNOWLEDGE_BASE,
            "resource_id": dataset,
            "tenant_id": tenant,
            "dataset_id": dataset,
            "release_id": release_row.id,
            "release_number": int(release_row.release_number),
            "manifest_digest": release_row.manifest_digest,
            "channel_id": channel_row.id,
            "channel_revision": int(channel_row.revision),
            "quality_gate_revision": int(channel_row.revision),
            "quality_gate_digest": gate_digest,
            "quality_gate_state": gate["state"],
            "quality_gate_reason": gate["reason"],
            "policy_id": quality_policy.id,
            "policy_revision": int(quality_policy.revision),
            "policy_digest": quality_policy.policy_digest,
            "quality_evidence_digest": evidence_digest,
            "certification_id": (
                gate.get("certification", {}).get("id")
                if isinstance(gate.get("certification"), Mapping)
                else None
            ),
            "certification_digest": (
                gate.get("certification", {}).get("certification_digest")
                if isinstance(gate.get("certification"), Mapping)
                else None
            ),
            "profile_revision": int(release_row.profile_revision),
            "mutation_generation": int(release_row.mutation_generation),
            "ownership_revision": int(release_row.ownership_revision),
            "workspace_id": release_row.workspace_id,
            "workspace_revision": int(release_row.workspace_revision),
            "serving_generation": int(release_row.serving_generation),
            "requested_expires_at": _iso(expires),
            "waiver_expires_at": _iso(expires),
            "reason": clean_reason,
        }
        safe_snapshot = _redact_snapshot(snapshot)
        snapshot_hash = _safe_snapshot_hash(safe_snapshot)
        approval_id = f"approval-request-{uuid.uuid4().hex}"
        approval_expires_at = current + timedelta(
            minutes=int(approval_row["request_expiry_minutes"])
        )
        approval_requests = _table(session, "tenant_approval_requests")
        session.execute(
            approval_requests.insert().values(
                id=approval_id,
                tenant_id=tenant,
                policy_id=approval_policy,
                requester_id=actor,
                action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
                resource_type=RESOURCE_KNOWLEDGE_BASE,
                resource_id=dataset,
                snapshot_json=safe_snapshot,
                payload_hash=snapshot_hash,
                reason=clean_reason,
                status="pending",
                required_approvals=int(approval_row["required_approvals"]),
                received_approvals=0,
                idempotency_key=key,
                execution_ticket_hash=None,
                ticket_issued_at=None,
                ticket_consumed_at=None,
                expires_at=approval_expires_at,
                rejected_at=None,
                rejected_by=None,
                rejection_comment=None,
                cancelled_at=None,
                cancelled_by=None,
                executed_at=None,
                executed_by=None,
                execution_failed_at=None,
                execution_failed_by=None,
                execution_error=None,
                revision=1,
                created_at=current,
                created_by=actor,
                updated_at=current,
                updated_by=actor,
            )
        )
        request_row = (
            session.execute(
                select(approval_requests).where(
                    approval_requests.c.tenant_id == tenant,
                    approval_requests.c.id == approval_id,
                )
            )
            .mappings()
            .one()
        )
        request_payload = _request_payload(request_row, now=current)
        event = _append_event(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release,
            channel_id=channel,
            event_type="waiver_requested",
            state="blocked",
            certification_id=snapshot.get("certification_id"),
            waiver_id=None,
            approval_request_id=approval_id,
            approval_execution_id=None,
            actor_id=actor,
            reason=clean_reason,
            snapshot={**safe_snapshot, "approval_policy_id": approval_policy},
            request_id=req,
            now=current,
        )
        _audit(
            session,
            account=account,
            tenant_id=tenant,
            actor_id=actor,
            action="knowledge_base.release_quality.waiver_requested",
            resource_id=approval_id,
            after={
                "approval_request": request_payload,
                "event": event,
                "release_id": release,
                "channel_id": channel,
                "policy_id": policy,
                "policy_revision": policy_revision,
                "quality_gate_digest": gate_digest,
                "requested_expires_at": _iso(expires),
            },
            request_id=req,
            request_ip=ip,
            now=current,
        )
        payload = {
            "state": "approval_required",
            "operation": QUALITY_WAIVER_OPERATION,
            "resource_id": approval_id,
            "approval_request": request_payload,
            "approval_snapshot": safe_snapshot,
            "event": event,
            "message": "Release quality waiver approval requested",
            "retryable": False,
        }
        _complete(session, reservation, payload, 202, approval_id)
        return _quality_service.ServiceResult(payload, 202)


def _assert_snapshot_fact(
    snapshot: Mapping[str, Any], fact: ApprovalExecutionFact, *, reason: str
) -> None:
    checks = {
        "tenant_id": fact.tenant_id,
        "action_type": fact.action_type,
        "resource_type": fact.resource_type,
        "resource_id": fact.resource_id,
        "dataset_id": fact.resource_id,
        "release_id": fact.release_id,
        "release_number": fact.release_number,
        "manifest_digest": fact.manifest_digest,
        "channel_id": fact.channel_id,
        "channel_revision": fact.channel_revision,
        "quality_gate_revision": fact.channel_revision,
        "policy_id": fact.policy_id,
        "policy_revision": fact.policy_revision,
        "policy_digest": fact.policy_digest,
        "quality_gate_digest": fact.quality_gate_digest,
        "profile_revision": fact.profile_revision,
        "mutation_generation": fact.mutation_generation,
        "ownership_revision": fact.ownership_revision,
        "workspace_id": fact.workspace_id,
        "workspace_revision": fact.workspace_revision,
        "serving_generation": fact.serving_generation,
        "reason": reason,
    }
    for field, expected in checks.items():
        if snapshot.get(field) != expected:
            raise ReleaseQualityConflict("approval request snapshot does not match execution fact")
    expiry = _fact_value(fact, "waiver_expires_at", "requested_expires_at")
    if (
        snapshot.get("requested_expires_at") != expiry
        or snapshot.get("waiver_expires_at") != expiry
    ):
        raise ReleaseQualityConflict("approval request expiry does not match execution fact")
    evidence = _fact_value(fact, "quality_evidence_digest", "evidence_digest")
    if snapshot.get("quality_evidence_digest") != evidence:
        raise ReleaseQualityConflict("approval request evidence does not match execution fact")


def _approval_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("snapshot_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ReleaseQualityConflict("approval request snapshot is invalid") from exc
    if not isinstance(raw, Mapping):
        raise ReleaseQualityConflict("approval request snapshot is invalid")
    safe = _redact_snapshot(raw)
    if _safe_snapshot_hash(safe) != str(row.get("payload_hash") or "").casefold():
        raise ReleaseQualityConflict("approval request snapshot hash does not match authority")
    return dict(safe)


def grant_quality_waiver(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    approval_execution_fact: ApprovalExecutionFact,
    release_id: str | None = None,
    channel_id: str | None = None,
    reason: str | None = None,
    request_id: str = "",
    request_ip: str = "",
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> Any:
    fact = _validate_fact(approval_execution_fact)
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    if fact.tenant_id != tenant or fact.resource_id != dataset:
        raise ReleaseQualityConflict("approval execution fact tenant or resource mismatch")
    fact_release = _fact_text(fact, "release_id", 64)
    fact_channel = _fact_text(fact, "channel_id", 128)
    release_key = _clean(release_id or fact_release, "release_id", 64)
    channel_key = _clean(channel_id or fact_channel, "channel_id", 128)
    if release_key != fact_release or channel_key != fact_channel:
        raise ReleaseQualityConflict("approval execution fact release or channel mismatch")
    clean_reason = _reason(reason if reason is not None else fact.reason)
    if clean_reason != fact.reason:
        raise ReleaseQualityConflict("approval execution fact reason mismatch")
    key = _clean(idempotency_key or fact.execution_id, "idempotency_key", 128)
    current = _as_datetime(now if now is not None else datetime.utcnow(), "now")
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    expiry_raw = _fact_value(fact, "waiver_expires_at", "requested_expires_at")
    expiry = _as_datetime(expiry_raw, "waiver_expires_at")
    request_hash = tenant_request_hash(
        operation="grant_quality_waiver",
        path_identity={
            "tenant_id": tenant,
            "dataset_id": dataset,
            "release_id": release_key,
            "channel_id": channel_key,
        },
        body={
            "approval_request_id": fact.approval_request_id,
            "approval_execution_id": fact.execution_id,
            "request_revision": fact.request_revision,
            "execution_revision": fact.execution_revision,
            "snapshot_hash": fact.snapshot_hash,
            "policy_id": fact.policy_id,
            "policy_revision": fact.policy_revision,
            "manifest_digest": fact.manifest_digest,
            "quality_gate_digest": fact.quality_gate_digest,
            "waiver_expires_at": _iso(expiry),
            "reason": clean_reason,
        },
    )
    with _waiver_mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        dataset_id=dataset,
        idempotency_key=key,
        request_hash=request_hash,
        operation="grant_quality_waiver",
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return _quality_service.ServiceResult(
                dict(reservation.replay.response), reservation.replay.http_status
            )
        approval_requests = _table(session, "tenant_approval_requests")
        approval_row = (
            session.execute(
                select(approval_requests)
                .where(
                    approval_requests.c.tenant_id == tenant,
                    approval_requests.c.id == fact.approval_request_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if approval_row is None:
            raise ReleaseQualityNotFound("Approval request does not exist")
        if str(approval_row.get("status")) != "executing":
            raise ReleaseQualityConflict("approval execution is not in the executing state")
        if int(approval_row.get("revision") or 0) != fact.execution_revision:
            raise ReleaseQualityConflict("approval execution revision is stale")
        if str(approval_row.get("action_type")) != ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER:
            raise ReleaseQualityConflict("approval action does not authorize quality waivers")
        if str(approval_row.get("resource_type")) != RESOURCE_KNOWLEDGE_BASE:
            raise ReleaseQualityConflict("approval resource type does not match quality waiver")
        if str(approval_row.get("resource_id")) != dataset:
            raise ReleaseQualityConflict("approval resource does not match Dataset")
        request_snapshot = _approval_snapshot(approval_row)
        if str(approval_row.get("payload_hash")) != fact.snapshot_hash:
            raise ReleaseQualityConflict(
                "approval execution snapshot hash does not match authority"
            )
        if str(approval_row.get("reason")) != clean_reason:
            raise ReleaseQualityConflict("approval execution reason does not match authority")
        _assert_snapshot_fact(request_snapshot, fact, reason=clean_reason)

        release_row = session.scalar(
            select(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant,
                DatasetReleaseManifest.dataset_id == dataset,
                DatasetReleaseManifest.id == release_key,
            )
            .with_for_update()
        )
        if release_row is None:
            raise ReleaseQualityNotFound("Release does not exist")
        if release_row.manifest_digest != fact.manifest_digest:
            raise ReleaseQualityConflict("Release Manifest digest is stale")
        if int(release_row.release_number) != fact.release_number:
            raise ReleaseQualityConflict("Release number is stale")
        if int(release_row.profile_revision) != fact.profile_revision:
            raise ReleaseQualityConflict("Release profile revision is stale")
        if int(release_row.mutation_generation) != fact.mutation_generation:
            raise ReleaseQualityConflict("Release mutation generation is stale")
        if int(release_row.ownership_revision) != fact.ownership_revision:
            raise ReleaseQualityConflict("Release ownership revision is stale")
        if (
            release_row.workspace_id != fact.workspace_id
            or int(release_row.workspace_revision) != fact.workspace_revision
        ):
            raise ReleaseQualityConflict("Release Workspace authority is stale")
        if int(release_row.serving_generation) != fact.serving_generation:
            raise ReleaseQualityConflict("Release serving generation is stale")
        channel_row = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant,
                TenantReleaseChannel.id == channel_key,
            )
            .with_for_update()
        )
        if channel_row is None or channel_row.status != "active":
            raise ReleaseQualityNotFound("Release Channel is unavailable")
        if int(channel_row.revision) != fact.channel_revision:
            raise ReleaseQualityConflict("Release Channel revision is stale")
        policy_row = session.scalar(
            select(TenantReleaseQualityGatePolicy)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant,
                TenantReleaseQualityGatePolicy.id == fact.policy_id,
            )
            .with_for_update()
        )
        if policy_row is None or policy_row.status != "active":
            raise ReleaseQualityNotFound("Quality gate policy does not exist")
        if int(policy_row.revision) != fact.policy_revision:
            raise ReleaseQualityConflict("Quality gate policy revision is stale")
        if policy_row.policy_digest != fact.policy_digest:
            raise ReleaseQualityConflict("Quality gate policy digest is stale")
        if expiry <= current:
            raise ReleaseQualityConflict("Approval waiver expiry has elapsed")
        if expiry > current + timedelta(minutes=int(policy_row.max_certification_age_minutes)):
            raise ReleaseQualityConflict("Approval waiver expiry exceeds policy maximum age")

        gate = _quality_service.resolve_release_quality_gate(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release_key,
            channel_id=channel_key,
            now=current,
        )
        if gate.get("state") == "unavailable":
            raise ReleaseQualityUnavailable("quality gate is unavailable")
        if gate.get("state") != "blocked" or gate.get("reason") not in _ALLOWED_GATE_REASONS:
            raise ReleaseQualityConflict("quality gate is no longer waiver-eligible")
        current_gate_snapshot = _gate_binding_snapshot(gate)
        if (
            canonical_quality_digest("quality_gate", current_gate_snapshot)
            != fact.quality_gate_digest
        ):
            raise ReleaseQualityConflict("quality gate digest is stale")
        current_evidence = _quality_evidence_digest(gate)
        expected_evidence = _fact_value(fact, "quality_evidence_digest", "evidence_digest")
        if current_evidence != expected_evidence:
            raise ReleaseQualityConflict("quality evidence digest is stale")

        existing = session.scalar(
            select(DatasetReleaseQualityWaiver)
            .where(
                DatasetReleaseQualityWaiver.tenant_id == tenant,
                DatasetReleaseQualityWaiver.approval_execution_id == fact.execution_id,
            )
            .with_for_update()
        )
        if existing is not None:
            raise ReleaseQualityConflict("approval execution receipt has already been consumed")
        waiver_snapshot = {
            "tenant_id": tenant,
            "dataset_id": dataset,
            "release_id": release_key,
            "channel_id": channel_key,
            "policy_id": fact.policy_id,
            "policy_revision": fact.policy_revision,
            "release_manifest_digest": fact.manifest_digest,
            "approval_request_id": fact.approval_request_id,
            "approval_execution_id": fact.execution_id,
            "quality_gate_digest": fact.quality_gate_digest,
            "quality_evidence_digest": expected_evidence,
            "valid_from": _iso(current),
            "expires_at": _iso(expiry),
            "reason": clean_reason,
        }
        waiver_id = f"quality-waiver-{uuid.uuid4().hex}"
        waiver_digest = canonical_quality_digest("quality_waiver", waiver_snapshot)
        row = DatasetReleaseQualityWaiver(
            id=waiver_id,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release_key,
            channel_id=channel_key,
            policy_id=fact.policy_id,
            policy_revision=fact.policy_revision,
            release_manifest_digest=fact.manifest_digest,
            approval_request_id=fact.approval_request_id,
            approval_execution_id=fact.execution_id,
            reason=clean_reason,
            valid_from=current,
            expires_at=expiry,
            waiver_digest=waiver_digest,
            created_at=current,
            created_by=actor,
            request_id=req,
        )
        session.add(row)
        session.flush()
        event = _append_event(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release_key,
            channel_id=channel_key,
            event_type="waiver_approved",
            state="active",
            certification_id=(
                gate.get("certification", {}).get("id")
                if isinstance(gate.get("certification"), Mapping)
                else None
            ),
            waiver_id=waiver_id,
            approval_request_id=fact.approval_request_id,
            approval_execution_id=fact.execution_id,
            actor_id=actor,
            reason=clean_reason,
            snapshot={**waiver_snapshot, "waiver_digest": waiver_digest},
            request_id=req,
            now=current,
        )
        payload = _result_payload(row, event=event)
        _audit(
            session,
            account=account,
            tenant_id=tenant,
            actor_id=actor,
            action="knowledge_base.release_quality.waiver_approved",
            resource_id=waiver_id,
            after={
                "waiver": payload["waiver"],
                "event": event,
                "approval_request_id": fact.approval_request_id,
                "approval_execution_id": fact.execution_id,
                "quality_gate_digest": fact.quality_gate_digest,
            },
            request_id=req,
            request_ip=ip,
            now=current,
        )
        _complete(session, reservation, payload, 201, waiver_id)
        return _quality_service.ServiceResult(payload, 201)


__all__ = [
    "ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER",
    "QUALITY_WAIVER_OPERATION",
    "RESOURCE_KNOWLEDGE_BASE",
    "grant_quality_waiver",
    "request_quality_waiver",
    "_quality_evidence_digest",
    "_waiver_payload",
]
