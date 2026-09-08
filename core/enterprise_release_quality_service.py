"""Transactional Stage 20 Release quality policy, Baseline and Certification authority."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
import base64
from hashlib import sha256
import json
import unicodedata
import uuid
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_release_quality_certification_capability
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_approval_control import _execution_id
from core.enterprise_knowledge_base_releases import (
    ReleaseManifestError,
    ReleaseManifestForbidden,
    ReleaseManifestInvalid,
    ReleaseManifestNotFound,
    ReleaseManifestUnavailable,
    ServiceResult,
    _actor,
    canonicalize_release_entries,
    collect_release_snapshot,
    _release_revisions,
    _require_manage,
    _require_read,
    _safe_release_reason,
    _safe_string,
)
from core.enterprise_release_quality_evidence import (
    _BPS_MAX,
    _SCORE_MILLI_MAX,
    _snapshot_quality_digest,
    _strict_snapshot_value,
    canonical_quality_digest,
    canonicalize_experiment_evidence,
    collect_release_experiment_evidence,
    evaluate_quality_policy,
    summarize_quality_evidence,
)
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
    Dataset,
    DatasetQualityBaseline,
    DatasetQualityBaselineItem,
    DatasetReleaseEntry,
    DatasetReleaseEvent,
    DatasetReleaseManifest,
    DatasetReleaseQualityCertification,
    DatasetReleaseQualityCertificationEvidence,
    DatasetReleaseQualityEvent,
    DatasetReleaseQualityWaiver,
    RetrievalExperiment,
    RetrievalJudgment,
    Tenant,
    TenantApprovalRequest,
    TenantAuditEvent,
    TenantMember,
    TenantReleaseChannel,
    TenantReleaseQualityGatePolicy,
)


class ReleaseQualityError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ReleaseQualityInvalid(ReleaseQualityError, ValueError):
    def __init__(self, message: str = "Release quality request is invalid") -> None:
        super().__init__("release_quality_invalid", message, 422)


class ReleaseQualityConflict(ReleaseQualityError):
    def __init__(
        self, message: str = "Release quality request conflicts with current authority"
    ) -> None:
        super().__init__("release_quality_conflict", message, 409)


class ReleaseQualityNotFound(ReleaseQualityError):
    def __init__(self, message: str = "Release quality authority was not found") -> None:
        super().__init__("release_quality_not_found", message, 404)


class ReleaseQualityForbidden(ReleaseQualityError):
    def __init__(self, message: str = "Release quality access is forbidden") -> None:
        super().__init__("release_quality_forbidden", message, 403)


class ReleaseQualityUnavailable(ReleaseQualityError):
    def __init__(self, message: str = "Release quality authority is unavailable") -> None:
        super().__init__("release_quality_unavailable", message, 503)


def _translate(exc: ReleaseManifestError) -> None:
    if isinstance(exc, ReleaseManifestInvalid):
        raise ReleaseQualityInvalid(exc.message) from exc
    if isinstance(exc, ReleaseManifestNotFound):
        raise ReleaseQualityNotFound(exc.message) from exc
    if isinstance(exc, ReleaseManifestForbidden):
        raise ReleaseQualityForbidden(exc.message) from exc
    if isinstance(exc, ReleaseManifestUnavailable):
        raise ReleaseQualityUnavailable(exc.message) from exc
    raise ReleaseQualityConflict(exc.message) from exc


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip()
    if not normalized and not allow_empty:
        raise ReleaseQualityInvalid(f"{field} is required")
    if len(normalized) > maximum:
        raise ReleaseQualityInvalid(f"{field} must be at most {maximum} characters")
    return normalized


def _safe(value: Any, field: str, maximum: int) -> str:
    try:
        return _safe_string(_clean(value, field, maximum), path=field)
    except ReleaseManifestError as exc:
        _translate(exc)
    raise AssertionError("unreachable")


def _reason(value: Any) -> str:
    try:
        return _safe_release_reason(value)
    except ReleaseManifestError as exc:
        _translate(exc)
    raise AssertionError("unreachable")


def _integer(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ReleaseQualityInvalid(f"{field} must be an exact integer >= {minimum}{suffix}")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReleaseQualityInvalid(f"{field} must be an exact boolean")
    return value


def _ensure_capability(connection: Any) -> None:
    state, issues = inspect_enterprise_release_quality_certification_capability(connection)
    if state != "ready":
        raise ReleaseQualityUnavailable("; ".join(issues) or state)


def _actor_scope(session: Session, tenant_id: str, actor_id: str) -> tuple[TenantMember, Account]:
    try:
        return _actor(session, tenant_id, actor_id)
    except ReleaseManifestError as exc:
        _translate(exc)
    raise AssertionError("unreachable")


def _require_dataset_manage(
    engine: Any, session: Session, membership: TenantMember, dataset_id: str
) -> None:
    try:
        _require_manage(engine, session, membership, dataset_id)
    except ReleaseManifestError as exc:
        _translate(exc)


def _require_dataset_read(
    engine: Any, session: Session, membership: TenantMember, dataset_id: str
) -> None:
    try:
        _require_read(engine, session, membership, dataset_id)
    except ReleaseManifestError as exc:
        _translate(exc)


@contextmanager
def _mutation_scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
    dataset_id: str | None = None,
    tenant_manager: bool = False,
) -> Iterator[tuple[Session, Account, Any]]:
    try:
        digest = tenant_idempotency_key_digest(tenant_id, actor_id, idempotency_key)
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant_id, actor_id, digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        _ensure_capability(session.connection())
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None or tenant.status != "active":
            raise ReleaseQualityForbidden("Tenant is unavailable")
        membership, account = _actor_scope(session, tenant_id, actor_id)
        if tenant_manager and str(membership.role).casefold() not in {"owner", "admin"}:
            raise ReleaseQualityForbidden("Tenant owner or admin is required")
        if dataset_id is not None:
            _require_dataset_manage(engine, session, membership, dataset_id)
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
            raise ReleaseQualityConflict("idempotency key conflict") from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise ReleaseQualityConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise ReleaseQualityInvalid(str(exc)) from exc
        try:
            yield session, account, reservation
        except IntegrityError as exc:
            raise ReleaseQualityConflict(
                "Release quality mutation violates an authority constraint"
            ) from exc
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable() from exc


def _complete(
    session: Session,
    reservation: Any,
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
        raise ReleaseQualityInvalid(str(exc)) from exc
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
            resource_type=resource_type,
            resource_id=resource_id,
            before_snapshot=None,
            after_snapshot=sanitize_audit_snapshot(dict(after)),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def _thresholds(row: TenantReleaseQualityGatePolicy) -> dict[str, Any]:
    return {
        "min_experiment_count": int(row.min_experiment_count),
        "min_judged_result_count": int(row.min_judged_result_count),
        "min_judgment_coverage_bps": int(row.min_judgment_coverage_bps),
        "min_exact_agreement_bps": int(row.min_exact_agreement_bps),
        "min_mean_score_milli": int(row.min_mean_score_milli),
        "max_conflicting_results": int(row.max_conflicting_results),
        "require_all_experiments_completed": bool(row.require_all_experiments_completed),
        "require_no_degraded_results": bool(row.require_no_degraded_results),
    }


def _policy_authority_digest(
    *,
    scope_type: str,
    scope_value: str,
    channel_id: str | None,
    thresholds: Mapping[str, Any],
    max_certification_age_minutes: int,
) -> str:
    return canonical_quality_digest(
        "policy_authority",
        {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "channel_id": channel_id,
            "thresholds": dict(thresholds),
            "max_certification_age_minutes": max_certification_age_minutes,
        },
    )


def _policy_payload(row: TenantReleaseQualityGatePolicy) -> dict[str, Any]:
    return {
        "id": _clean(row.id, "policy.id", 64),
        "tenant_id": _clean(row.tenant_id, "policy.tenant_id", 64),
        "name": _safe(row.name, "policy.name", 128),
        "scope_type": _clean(row.scope_type, "policy.scope_type", 16),
        "scope_value": _clean(row.scope_value, "policy.scope_value", 128),
        "channel_id": (
            _clean(row.channel_id, "policy.channel_id", 128) if row.channel_id is not None else None
        ),
        "status": row.status,
        "revision": int(row.revision),
        **_thresholds(row),
        "max_certification_age_minutes": int(row.max_certification_age_minutes),
        "policy_digest": row.policy_digest,
        "created_at": row.created_at.isoformat(),
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat(),
        "updated_by": row.updated_by,
    }


def _baseline_item_digest(item: Mapping[str, Any]) -> str:
    return canonical_quality_digest("baseline_item_evidence", item)


def _experiment_digest(item: Mapping[str, Any]) -> str:
    return canonical_quality_digest(
        "experiment_evidence", {key: value for key, value in item.items() if key != "judgments"}
    )


def _baseline_item_payload(row: DatasetQualityBaselineItem) -> dict[str, Any]:
    return {
        "id": row.id,
        "ordinal": int(row.ordinal),
        "experiment_id": row.experiment_id,
        "experiment_sequence": int(row.experiment_sequence),
        "query_hash": row.query_hash,
        "experiment_serving_generation": int(row.experiment_serving_generation),
        "strategy_digest": row.strategy_digest,
        "result_digest": row.result_digest,
        "evidence_digest": row.evidence_digest,
        "judgment_digest": row.judgment_digest,
        "created_at": row.created_at.isoformat(),
    }


def _baseline_payload(
    row: DatasetQualityBaseline, items: Sequence[DatasetQualityBaselineItem] = ()
) -> dict[str, Any]:
    return {
        "id": _clean(row.id, "baseline.id", 64),
        "tenant_id": _clean(row.tenant_id, "baseline.tenant_id", 64),
        "dataset_id": _clean(row.dataset_id, "baseline.dataset_id", 64),
        "name": _safe(row.name, "baseline.name", 128),
        "baseline_revision": int(row.baseline_revision),
        "parent_baseline_id": row.parent_baseline_id,
        "experiment_count": int(row.experiment_count),
        "query_count": int(row.query_count),
        "baseline_digest": row.baseline_digest,
        "created_at": row.created_at.isoformat(),
        "created_by": _clean(row.created_by, "baseline.created_by", 64),
        "reason": _safe(row.reason, "baseline.reason", 512),
        "items": [_baseline_item_payload(item) for item in items],
    }


def _certification_payload(row: DatasetReleaseQualityCertification) -> dict[str, Any]:
    return {
        "id": _clean(row.id, "certification.id", 64),
        "tenant_id": _clean(row.tenant_id, "certification.tenant_id", 64),
        "dataset_id": _clean(row.dataset_id, "certification.dataset_id", 64),
        "release_id": _clean(row.release_id, "certification.release_id", 64),
        "baseline_id": _clean(row.baseline_id, "certification.baseline_id", 64),
        "policy_id": _clean(row.policy_id, "certification.policy_id", 64),
        "policy_revision": int(row.policy_revision),
        "release_manifest_digest": row.release_manifest_digest,
        "release_mutation_generation": int(row.release_mutation_generation),
        "release_serving_generation": int(row.release_serving_generation),
        "status": row.status,
        "experiment_count": int(row.experiment_count),
        "completed_experiment_count": int(row.completed_experiment_count),
        "degraded_experiment_count": int(row.degraded_experiment_count),
        "query_count": int(row.query_count),
        "judged_result_count": int(row.judged_result_count),
        "total_result_count": int(row.total_result_count),
        "judgment_count": int(row.judgment_count),
        "judgment_coverage_bps": row.judgment_coverage_bps,
        "multi_judged_results": int(row.multi_judged_results),
        "unanimous_results": int(row.unanimous_results),
        "conflicting_results": int(row.conflicting_results),
        "exact_agreement_bps": row.exact_agreement_bps,
        "mean_score_milli": row.mean_score_milli,
        "failed_rule_count": int(row.failed_rule_count),
        "evidence_digest": row.evidence_digest,
        "certification_digest": row.certification_digest,
        "valid_until": row.valid_until.isoformat(),
        "created_at": row.created_at.isoformat(),
        "created_by": _clean(row.created_by, "certification.created_by", 64),
        "reason": _safe(row.reason, "certification.reason", 512),
    }


def _waiver_payload(row: DatasetReleaseQualityWaiver) -> dict[str, Any]:
    return {
        "id": _clean(row.id, "waiver.id", 64),
        "tenant_id": _clean(row.tenant_id, "waiver.tenant_id", 64),
        "dataset_id": _clean(row.dataset_id, "waiver.dataset_id", 64),
        "release_id": _clean(row.release_id, "waiver.release_id", 64),
        "channel_id": _clean(row.channel_id, "waiver.channel_id", 128),
        "policy_id": _clean(row.policy_id, "waiver.policy_id", 64),
        "policy_revision": int(row.policy_revision),
        "release_manifest_digest": row.release_manifest_digest,
        "approval_request_id": _clean(row.approval_request_id, "waiver.approval_request_id", 64),
        "approval_execution_id": _clean(
            row.approval_execution_id, "waiver.approval_execution_id", 128
        ),
        "reason": _safe(row.reason, "waiver.reason", 512),
        "valid_from": row.valid_from.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "waiver_digest": row.waiver_digest,
        "created_at": row.created_at.isoformat(),
        "created_by": _clean(row.created_by, "waiver.created_by", 64),
    }


def collect_baseline_experiment_evidence(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    experiment_ids: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    ids = [_clean(value, "experiment_id", 64) for value in experiment_ids]
    if not ids:
        raise ReleaseQualityInvalid("experiment_ids must be nonempty")
    if len(set(ids)) != len(ids):
        raise ReleaseQualityInvalid("experiment_ids must be unique")
    experiments = list(
        session.scalars(
            select(RetrievalExperiment)
            .where(
                RetrievalExperiment.tenant_id == tenant_id,
                RetrievalExperiment.dataset_id == dataset_id,
                RetrievalExperiment.id.in_(ids),
            )
            .order_by(RetrievalExperiment.sequence, RetrievalExperiment.id)
            .with_for_update()
        )
    )
    if len(experiments) != len(ids):
        raise ReleaseQualityConflict("experiment scope is incomplete")
    output: list[dict[str, Any]] = []
    for experiment in experiments:
        try:
            strategy = _strict_snapshot_value(
                experiment.strategy_snapshot, path="experiment.strategy"
            )
            result = _strict_snapshot_value(experiment.result_snapshot, path="experiment.result")
            lineage = _strict_snapshot_value(experiment.evidence_lineage, path="experiment.lineage")
        except ReleaseManifestError as exc:
            _translate(exc)
        if not all(isinstance(value, Mapping) for value in (strategy, result, lineage)):
            raise ReleaseQualityConflict("experiment snapshot authority is malformed")
        generations = {
            _integer(
                value.get("dataset_serving_generation"),
                "experiment.dataset_serving_generation",
                0,
            )
            for value in (strategy, result, lineage)
        }
        if len(generations) != 1:
            raise ReleaseQualityConflict("experiment serving generation authority is inconsistent")
        generation = _integer(next(iter(generations)), "experiment.dataset_serving_generation", 0)
        results = result.get("results")
        if not isinstance(results, list) or any(not isinstance(item, Mapping) for item in results):
            raise ReleaseQualityConflict("experiment result authority is malformed")
        ranks = [_integer(item.get("rank"), "experiment.result.rank", 1) for item in results]
        judgments = list(
            session.scalars(
                select(RetrievalJudgment)
                .where(
                    RetrievalJudgment.tenant_id == tenant_id,
                    RetrievalJudgment.dataset_id == dataset_id,
                    RetrievalJudgment.experiment_id == experiment.id,
                )
                .order_by(
                    RetrievalJudgment.result_rank,
                    RetrievalJudgment.created_by,
                    RetrievalJudgment.id,
                )
                .with_for_update()
            )
        )
        output.append(
            {
                "experiment_id": experiment.id,
                "experiment_sequence": int(experiment.sequence),
                "query_hash": experiment.query_hash,
                "status": experiment.status,
                "dataset_serving_generation": generation,
                "degraded": _boolean(result.get("degraded"), "experiment.result.degraded"),
                "result_count": len(results),
                "result_ranks": ranks,
                "strategy_digest": _snapshot_quality_digest("experiment.strategy", strategy),
                "result_digest": _snapshot_quality_digest("experiment.result", result),
                "lineage_digest": _snapshot_quality_digest("experiment.lineage", lineage),
                "judgments": [
                    {
                        "result_rank": int(row.result_rank),
                        "relevance_label": row.relevance_label,
                        "score": row.score,
                        "created_by": row.created_by,
                        "revision": int(row.revision),
                    }
                    for row in judgments
                ],
            }
        )
    try:
        return canonicalize_experiment_evidence(output)
    except ReleaseManifestError as exc:
        _translate(exc)
    raise AssertionError("unreachable")


def create_quality_gate_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    name: str,
    scope_type: str,
    scope_value: str,
    channel_id: str | None,
    min_experiment_count: int,
    min_judged_result_count: int,
    min_judgment_coverage_bps: int,
    min_exact_agreement_bps: int,
    min_mean_score_milli: int,
    max_conflicting_results: int,
    require_all_experiments_completed: bool,
    require_no_degraded_results: bool,
    max_certification_age_minutes: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    clean_name = _safe(name, "name", 128)
    scope = _clean(scope_type, "scope_type", 16).casefold()
    value = _clean(scope_value, "scope_value", 128)
    channel_key = None if channel_id is None else _clean(channel_id, "channel_id", 128)
    if scope == "global":
        if value != "*" or channel_key is not None:
            raise ReleaseQualityInvalid("global policy scope must use '*' and no channel_id")
    elif scope == "risk_tier":
        value = value.casefold()
        if value not in {"low", "medium", "high"} or channel_key is not None:
            raise ReleaseQualityInvalid("risk_tier policy scope is invalid")
    elif scope == "channel":
        if channel_key is None or value != channel_key:
            raise ReleaseQualityInvalid("channel policy scope must match channel_id")
    else:
        raise ReleaseQualityInvalid("scope_type is invalid")
    thresholds = {
        "min_experiment_count": _integer(min_experiment_count, "min_experiment_count", 1),
        "min_judged_result_count": _integer(min_judged_result_count, "min_judged_result_count", 0),
        "min_judgment_coverage_bps": _integer(
            min_judgment_coverage_bps, "min_judgment_coverage_bps", 0, _BPS_MAX
        ),
        "min_exact_agreement_bps": _integer(
            min_exact_agreement_bps, "min_exact_agreement_bps", 0, _BPS_MAX
        ),
        "min_mean_score_milli": _integer(
            min_mean_score_milli, "min_mean_score_milli", 0, _SCORE_MILLI_MAX
        ),
        "max_conflicting_results": _integer(max_conflicting_results, "max_conflicting_results", 0),
        "require_all_experiments_completed": _boolean(
            require_all_experiments_completed, "require_all_experiments_completed"
        ),
        "require_no_degraded_results": _boolean(
            require_no_degraded_results, "require_no_degraded_results"
        ),
    }
    max_age = _integer(max_certification_age_minutes, "max_certification_age_minutes", 1)
    clean_reason = _reason(reason)
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    key = _clean(idempotency_key, "idempotency_key", 128)
    request_hash = tenant_request_hash(
        operation="create_quality_gate_policy",
        path_identity={"tenant_id": tenant},
        body={
            "name": clean_name,
            "scope_type": scope,
            "scope_value": value,
            "channel_id": channel_key,
            **thresholds,
            "max_certification_age_minutes": max_age,
            "reason": clean_reason,
        },
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="create_quality_gate_policy",
        resource_type="release_quality_policy",
        tenant_manager=True,
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        if channel_key is not None:
            channel = session.scalar(
                select(TenantReleaseChannel)
                .where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.id == channel_key,
                )
                .with_for_update()
            )
            if channel is None or channel.status != "active":
                raise ReleaseQualityNotFound("Release Channel is unavailable")
        active_scope_key = f"{scope}:{value}"
        duplicate = session.scalar(
            select(TenantReleaseQualityGatePolicy.id)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant,
                TenantReleaseQualityGatePolicy.active_scope_key == active_scope_key,
            )
            .with_for_update()
        )
        if duplicate is not None:
            raise ReleaseQualityConflict("An active quality policy already exists for this scope")
        now = datetime.utcnow()
        row = TenantReleaseQualityGatePolicy(
            id=f"quality-policy-{uuid.uuid4().hex}",
            tenant_id=tenant,
            name=clean_name,
            scope_type=scope,
            scope_value=value,
            channel_id=channel_key,
            active_scope_key=active_scope_key,
            status="active",
            revision=1,
            **thresholds,
            max_certification_age_minutes=max_age,
            policy_digest=_policy_authority_digest(
                scope_type=scope,
                scope_value=value,
                channel_id=channel_key,
                thresholds=thresholds,
                max_certification_age_minutes=max_age,
            ),
            created_at=now,
            created_by=actor,
            updated_at=now,
            updated_by=actor,
        )
        session.add(row)
        session.flush()
        policy = _policy_payload(row)
        payload = {
            "state": "applied",
            "operation": "quality_policy_create",
            "resource_id": row.id,
            "policy": policy,
            "message": "Release quality policy created",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.policy_created",
            resource_type="release_quality_policy",
            resource_id=row.id,
            after={"policy": policy, "reason": clean_reason},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload, 201, row.id)


def create_quality_baseline(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    name: str,
    experiment_ids: Sequence[str],
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    parent_baseline_id: str | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    clean_name = _safe(name, "name", 128)
    normalized_name = unicodedata.normalize("NFKC", clean_name).casefold()
    parent = (
        None if parent_baseline_id is None else _clean(parent_baseline_id, "parent_baseline_id", 64)
    )
    experiment_keys = tuple(_clean(value, "experiment_id", 64) for value in experiment_ids)
    if not experiment_keys:
        raise ReleaseQualityInvalid("experiment_ids must be nonempty")
    if len(set(experiment_keys)) != len(experiment_keys):
        raise ReleaseQualityInvalid("experiment_ids must be unique")
    clean_reason = _reason(reason)
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    key = _clean(idempotency_key, "idempotency_key", 128)
    request_hash = tenant_request_hash(
        operation="create_quality_baseline",
        path_identity={"tenant_id": tenant, "dataset_id": dataset},
        body={
            "name": clean_name,
            "experiment_ids": list(experiment_keys),
            "parent_baseline_id": parent,
            "reason": clean_reason,
        },
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="create_quality_baseline",
        resource_type="quality_baseline",
        dataset_id=dataset,
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        if parent is not None:
            parent_row = session.scalar(
                select(DatasetQualityBaseline)
                .where(
                    DatasetQualityBaseline.tenant_id == tenant,
                    DatasetQualityBaseline.dataset_id == dataset,
                    DatasetQualityBaseline.id == parent,
                )
                .with_for_update()
            )
            if parent_row is None:
                raise ReleaseQualityNotFound("Parent quality Baseline does not exist")
        evidence = collect_baseline_experiment_evidence(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            experiment_ids=experiment_keys,
        )
        revision = (
            int(
                session.scalar(
                    select(func.max(DatasetQualityBaseline.baseline_revision)).where(
                        DatasetQualityBaseline.tenant_id == tenant,
                        DatasetQualityBaseline.dataset_id == dataset,
                        DatasetQualityBaseline.normalized_name == normalized_name,
                    )
                )
                or 0
            )
            + 1
        )
        item_authority = [
            {
                "ordinal": ordinal,
                "experiment_id": item["experiment_id"],
                "experiment_sequence": item["experiment_sequence"],
                "query_hash": item["query_hash"],
                "experiment_serving_generation": item["dataset_serving_generation"],
                "strategy_digest": item["strategy_digest"],
                "result_digest": item["result_digest"],
                "evidence_digest": _baseline_item_digest(item),
                "judgment_digest": item["judgment_digest"],
            }
            for ordinal, item in enumerate(evidence, 1)
        ]
        summary = summarize_quality_evidence(evidence)
        baseline_digest = canonical_quality_digest(
            "baseline",
            {
                "tenant_id": tenant,
                "dataset_id": dataset,
                "normalized_name": normalized_name,
                "baseline_revision": revision,
                "parent_baseline_id": parent,
                "items": item_authority,
            },
        )
        now = datetime.utcnow()
        row = DatasetQualityBaseline(
            id=f"quality-baseline-{uuid.uuid4().hex}",
            tenant_id=tenant,
            dataset_id=dataset,
            name=clean_name,
            normalized_name=normalized_name,
            baseline_revision=revision,
            parent_baseline_id=parent,
            experiment_count=summary.experiment_count,
            query_count=summary.query_count,
            baseline_digest=baseline_digest,
            created_at=now,
            created_by=actor,
            reason=clean_reason,
            request_id=req,
        )
        session.add(row)
        session.flush()
        item_rows: list[DatasetQualityBaselineItem] = []
        for item in item_authority:
            item_row = DatasetQualityBaselineItem(
                id=f"quality-baseline-item-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset,
                baseline_id=row.id,
                created_at=now,
                **item,
            )
            session.add(item_row)
            item_rows.append(item_row)
        session.flush()
        baseline = _baseline_payload(row, item_rows)
        payload = {
            "state": "applied",
            "operation": "quality_baseline_create",
            "resource_id": row.id,
            "baseline": baseline,
            "message": "Quality Baseline created",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.baseline_created",
            resource_type="quality_baseline",
            resource_id=row.id,
            after={"baseline": baseline},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload, 201, row.id)


def _resolve_policy(
    session: Session, *, tenant_id: str, channel: TenantReleaseChannel
) -> TenantReleaseQualityGatePolicy | None:
    for scope_type, scope_value in (
        ("channel", channel.id),
        ("risk_tier", channel.risk_tier),
        ("global", "*"),
    ):
        row = session.scalar(
            select(TenantReleaseQualityGatePolicy)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant_id,
                TenantReleaseQualityGatePolicy.scope_type == scope_type,
                TenantReleaseQualityGatePolicy.scope_value == scope_value,
                TenantReleaseQualityGatePolicy.status == "active",
            )
            .order_by(
                TenantReleaseQualityGatePolicy.revision.desc(),
                TenantReleaseQualityGatePolicy.updated_at.desc(),
                TenantReleaseQualityGatePolicy.id.desc(),
            )
        )
        if row is not None:
            return row
    return None


def _release_evidence(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    experiment_ids: Sequence[str],
    lock_for_update: bool,
) -> tuple[dict[str, Any], ...]:
    try:
        return collect_release_experiment_evidence(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            release_id=release_id,
            experiment_ids=experiment_ids,
            lock_for_update=lock_for_update,
        )
    except ReleaseManifestError as exc:
        _translate(exc)
    raise AssertionError("unreachable")


def certify_release(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    baseline_id: str,
    policy_id: str,
    expected_policy_revision: int,
    expected_channel_revision: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    release_key = _clean(release_id, "release_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128)
    baseline_key = _clean(baseline_id, "baseline_id", 64)
    policy_key = _clean(policy_id, "policy_id", 64)
    policy_revision = _integer(expected_policy_revision, "expected_policy_revision", 1)
    channel_revision = _integer(expected_channel_revision, "expected_channel_revision", 1)
    clean_reason = _reason(reason)
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    key = _clean(idempotency_key, "idempotency_key", 128)
    request_hash = tenant_request_hash(
        operation="certify_release",
        path_identity={
            "tenant_id": tenant,
            "dataset_id": dataset,
            "release_id": release_key,
            "channel_id": channel_key,
        },
        body={
            "baseline_id": baseline_key,
            "policy_id": policy_key,
            "expected_policy_revision": policy_revision,
            "expected_channel_revision": channel_revision,
            "reason": clean_reason,
        },
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="certify_release",
        resource_type="release_quality_certification",
        dataset_id=dataset,
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        try:
            current_dataset, ownership, workspace = _release_revisions(session, tenant, dataset)
        except ReleaseManifestError as exc:
            _translate(exc)
        release = session.scalar(
            select(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant,
                DatasetReleaseManifest.dataset_id == dataset,
                DatasetReleaseManifest.id == release_key,
            )
            .with_for_update()
        )
        if release is None:
            raise ReleaseQualityNotFound("Release does not exist")
        current_authority = (
            int(current_dataset.profile_revision),
            int(current_dataset.mutation_generation),
            int(current_dataset.serving_generation),
            int(ownership.revision),
            workspace.id,
            int(workspace.revision),
        )
        release_authority = (
            int(release.profile_revision),
            int(release.mutation_generation),
            int(release.serving_generation),
            int(release.ownership_revision),
            release.workspace_id,
            int(release.workspace_revision),
        )
        if current_authority != release_authority or release.readiness_state != "ready":
            raise ReleaseQualityConflict("Release authority is stale")
        if (
            session.scalar(
                select(DatasetReleaseEvent.id).where(
                    DatasetReleaseEvent.tenant_id == tenant,
                    DatasetReleaseEvent.dataset_id == dataset,
                    DatasetReleaseEvent.release_id == release_key,
                    DatasetReleaseEvent.event_type == "retired",
                )
            )
            is not None
        ):
            raise ReleaseQualityConflict("Release is retired")
        if not _release_manifest_authority_current(session, release):
            raise ReleaseQualityConflict("Release Manifest authority is stale")
        channel = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant,
                TenantReleaseChannel.id == channel_key,
            )
            .with_for_update()
        )
        if channel is None or channel.status != "active":
            raise ReleaseQualityNotFound("Release Channel is unavailable")
        if int(channel.revision) != channel_revision:
            raise ReleaseQualityConflict("Release Channel revision changed")
        policy = session.scalar(
            select(TenantReleaseQualityGatePolicy)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant,
                TenantReleaseQualityGatePolicy.id == policy_key,
            )
            .with_for_update()
        )
        if policy is None or policy.status != "active":
            raise ReleaseQualityNotFound("Quality policy is unavailable")
        if int(policy.revision) != policy_revision:
            raise ReleaseQualityConflict("Quality policy revision changed")
        authoritative = _resolve_policy(session, tenant_id=tenant, channel=channel)
        if authoritative is None or authoritative.id != policy.id:
            raise ReleaseQualityConflict("Quality policy is not authoritative for this Channel")
        baseline = session.scalar(
            select(DatasetQualityBaseline)
            .where(
                DatasetQualityBaseline.tenant_id == tenant,
                DatasetQualityBaseline.dataset_id == dataset,
                DatasetQualityBaseline.id == baseline_key,
            )
            .with_for_update()
        )
        if baseline is None:
            raise ReleaseQualityNotFound("Quality Baseline does not exist")
        baseline_items = list(
            session.scalars(
                select(DatasetQualityBaselineItem)
                .where(
                    DatasetQualityBaselineItem.tenant_id == tenant,
                    DatasetQualityBaselineItem.dataset_id == dataset,
                    DatasetQualityBaselineItem.baseline_id == baseline.id,
                )
                .order_by(DatasetQualityBaselineItem.ordinal, DatasetQualityBaselineItem.id)
                .with_for_update()
            )
        )
        if len(baseline_items) != int(baseline.experiment_count):
            raise ReleaseQualityUnavailable("Quality Baseline authority is incomplete")
        if _baseline_authority_digest(baseline, baseline_items) != baseline.baseline_digest:
            raise ReleaseQualityConflict("Quality Baseline root digest is stale")
        if len({item.query_hash for item in baseline_items}) != int(baseline.query_count):
            raise ReleaseQualityConflict("Quality Baseline query authority is stale")
        evidence = _release_evidence(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release_key,
            experiment_ids=[item.experiment_id for item in baseline_items],
            lock_for_update=True,
        )
        by_experiment = {item["experiment_id"]: item for item in evidence}
        for baseline_item in baseline_items:
            current = by_experiment.get(baseline_item.experiment_id)
            if current is None:
                raise ReleaseQualityConflict("Baseline evidence is stale")
            expected = (
                int(baseline_item.experiment_sequence),
                baseline_item.query_hash,
                int(baseline_item.experiment_serving_generation),
                baseline_item.strategy_digest,
                baseline_item.result_digest,
                baseline_item.evidence_digest,
                baseline_item.judgment_digest,
            )
            actual = (
                int(current["experiment_sequence"]),
                current["query_hash"],
                int(current["dataset_serving_generation"]),
                current["strategy_digest"],
                current["result_digest"],
                _baseline_item_digest(current),
                current["judgment_digest"],
            )
            if actual != expected:
                raise ReleaseQualityConflict("Baseline evidence is stale")
        evaluation = evaluate_quality_policy(_thresholds(policy), evidence)
        if policy.policy_digest != _policy_authority_digest(
            scope_type=policy.scope_type,
            scope_value=policy.scope_value,
            channel_id=policy.channel_id,
            thresholds=_thresholds(policy),
            max_certification_age_minutes=int(policy.max_certification_age_minutes),
        ):
            raise ReleaseQualityConflict("Quality policy digest is stale")
        existing = session.scalar(
            select(DatasetReleaseQualityCertification)
            .where(
                DatasetReleaseQualityCertification.tenant_id == tenant,
                DatasetReleaseQualityCertification.dataset_id == dataset,
                DatasetReleaseQualityCertification.release_id == release_key,
                DatasetReleaseQualityCertification.baseline_id == baseline.id,
                DatasetReleaseQualityCertification.policy_id == policy.id,
                DatasetReleaseQualityCertification.policy_revision == policy_revision,
                DatasetReleaseQualityCertification.evidence_digest == evaluation.evidence_digest,
            )
            .order_by(DatasetReleaseQualityCertification.created_at.desc())
        )
        if existing is not None:
            payload = {
                "state": "unchanged",
                "operation": "release_quality_certify",
                "resource_id": existing.id,
                "certification": _certification_payload(existing),
                "message": "Release quality Certification already exists",
                "retryable": False,
            }
            return _complete(session, reservation, payload, 200, existing.id)
        now = datetime.utcnow()
        valid_until = now + timedelta(minutes=int(policy.max_certification_age_minutes))
        summary = asdict(evaluation.summary)
        policy_snapshot = {
            "id": policy.id,
            "scope_type": policy.scope_type,
            "scope_value": policy.scope_value,
            "revision": int(policy.revision),
            "policy_digest": policy.policy_digest,
            "max_certification_age_minutes": int(policy.max_certification_age_minutes),
            **_thresholds(policy),
        }
        summary_snapshot = {
            **summary,
            "verdict": evaluation.verdict,
            "failed_rules": [dict(rule) for rule in evaluation.failed_rules],
            "rule_results": [dict(rule) for rule in evaluation.rule_results],
        }
        certification_digest = canonical_quality_digest(
            "certification",
            {
                "tenant_id": tenant,
                "dataset_id": dataset,
                "release_id": release_key,
                "baseline_id": baseline.id,
                "baseline_digest": baseline.baseline_digest,
                "policy_id": policy.id,
                "policy_revision": int(policy.revision),
                "policy_digest": policy.policy_digest,
                "release_manifest_digest": release.manifest_digest,
                "release_mutation_generation": int(release.mutation_generation),
                "release_serving_generation": int(release.serving_generation),
                "status": evaluation.verdict,
                "summary": summary_snapshot,
                "evidence_digest": evaluation.evidence_digest,
                "valid_until": valid_until.isoformat(),
            },
        )
        row = DatasetReleaseQualityCertification(
            id=f"quality-certification-{uuid.uuid4().hex}",
            tenant_id=tenant,
            dataset_id=dataset,
            release_id=release_key,
            baseline_id=baseline.id,
            policy_id=policy.id,
            policy_revision=int(policy.revision),
            release_manifest_digest=release.manifest_digest,
            release_mutation_generation=int(release.mutation_generation),
            release_serving_generation=int(release.serving_generation),
            status=evaluation.verdict,
            **summary,
            failed_rule_count=len(evaluation.failed_rules),
            policy_snapshot_json=policy_snapshot,
            summary_json=summary_snapshot,
            evidence_digest=evaluation.evidence_digest,
            certification_digest=certification_digest,
            valid_until=valid_until,
            created_at=now,
            created_by=actor,
            reason=clean_reason,
            request_id=req,
        )
        session.add(row)
        session.flush()
        for baseline_item in baseline_items:
            item = by_experiment[baseline_item.experiment_id]
            item_summary = summarize_quality_evidence([item])
            session.add(
                DatasetReleaseQualityCertificationEvidence(
                    id=f"quality-certification-evidence-{uuid.uuid4().hex}",
                    tenant_id=tenant,
                    dataset_id=dataset,
                    certification_id=row.id,
                    baseline_item_id=baseline_item.id,
                    experiment_id=baseline_item.experiment_id,
                    ordinal=int(baseline_item.ordinal),
                    status=item["status"],
                    result_count=item_summary.total_result_count,
                    judged_result_count=item_summary.judged_result_count,
                    judgment_count=item_summary.judgment_count,
                    multi_judged_results=item_summary.multi_judged_results,
                    unanimous_results=item_summary.unanimous_results,
                    conflicting_results=item_summary.conflicting_results,
                    exact_agreement_bps=item_summary.exact_agreement_bps,
                    mean_score_milli=item_summary.mean_score_milli,
                    experiment_digest=_experiment_digest(item),
                    judgment_digest=item["judgment_digest"],
                    safe_facts_json={
                        "query_hash": item["query_hash"],
                        "dataset_serving_generation": item["dataset_serving_generation"],
                        "degraded": item["degraded"],
                        "strategy_digest": item["strategy_digest"],
                        "result_digest": item["result_digest"],
                        "lineage_digest": item["lineage_digest"],
                    },
                    created_at=now,
                )
            )
        previous_event = session.scalar(
            select(DatasetReleaseQualityEvent)
            .where(
                DatasetReleaseQualityEvent.tenant_id == tenant,
                DatasetReleaseQualityEvent.dataset_id == dataset,
                DatasetReleaseQualityEvent.release_id == release_key,
                DatasetReleaseQualityEvent.channel_id == channel_key,
            )
            .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
            .with_for_update()
        )
        sequence = int(previous_event.event_sequence) + 1 if previous_event is not None else 1
        previous_digest = previous_event.event_digest if previous_event is not None else None
        event_snapshot = {
            "certification_id": row.id,
            "status": row.status,
            "policy_id": policy.id,
            "policy_revision": int(policy.revision),
            "baseline_id": baseline.id,
            "baseline_digest": baseline.baseline_digest,
            "policy_digest": policy.policy_digest,
            "release_manifest_digest": release.manifest_digest,
            "evidence_digest": row.evidence_digest,
            "certification_digest": row.certification_digest,
            "failed_rule_count": int(row.failed_rule_count),
            "valid_until": valid_until.isoformat(),
        }
        event_digest = canonical_quality_digest(
            "quality_event",
            {
                "tenant_id": tenant,
                "dataset_id": dataset,
                "release_id": release_key,
                "channel_id": channel_key,
                "event_sequence": sequence,
                "event_type": "certification_created",
                "state": row.status,
                "previous_event_digest": previous_digest,
                "snapshot": event_snapshot,
            },
        )
        session.add(
            DatasetReleaseQualityEvent(
                id=f"quality-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset,
                release_id=release_key,
                channel_id=channel_key,
                certification_id=row.id,
                waiver_id=None,
                event_type="certification_created",
                event_sequence=sequence,
                state=row.status,
                previous_event_digest=previous_digest,
                event_digest=event_digest,
                approval_request_id=None,
                approval_execution_id=None,
                actor_id=actor,
                reason=clean_reason,
                safe_snapshot_json=event_snapshot,
                request_id=req,
                occurred_at=now,
            )
        )
        session.flush()
        certification = _certification_payload(row)
        payload = {
            "state": "applied",
            "operation": "release_quality_certify",
            "resource_id": row.id,
            "certification": certification,
            "message": "Release quality Certification created",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.certified",
            resource_type="release_quality_certification",
            resource_id=row.id,
            after={"certification": certification, "channel_id": channel_key},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload, 201, row.id)


def _baseline_authority_digest(
    baseline: DatasetQualityBaseline,
    items: Sequence[DatasetQualityBaselineItem],
) -> str:
    authority = [
        {
            "ordinal": int(item.ordinal),
            "experiment_id": item.experiment_id,
            "experiment_sequence": int(item.experiment_sequence),
            "query_hash": item.query_hash,
            "experiment_serving_generation": int(item.experiment_serving_generation),
            "strategy_digest": item.strategy_digest,
            "result_digest": item.result_digest,
            "evidence_digest": item.evidence_digest,
            "judgment_digest": item.judgment_digest,
        }
        for item in items
    ]
    return canonical_quality_digest(
        "baseline",
        {
            "tenant_id": baseline.tenant_id,
            "dataset_id": baseline.dataset_id,
            "normalized_name": baseline.normalized_name,
            "baseline_revision": int(baseline.baseline_revision),
            "parent_baseline_id": baseline.parent_baseline_id,
            "items": authority,
        },
    )


def _release_manifest_authority_current(session: Session, release: DatasetReleaseManifest) -> bool:
    rows = list(
        session.scalars(
            select(DatasetReleaseEntry)
            .where(
                DatasetReleaseEntry.tenant_id == release.tenant_id,
                DatasetReleaseEntry.dataset_id == release.dataset_id,
                DatasetReleaseEntry.release_id == release.id,
            )
            .order_by(DatasetReleaseEntry.ordinal, DatasetReleaseEntry.id)
        )
    )
    persisted = canonicalize_release_entries(
        [
            {
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "resource_revision": int(row.resource_revision),
                "content_digest": row.content_digest,
                "facts": row.safe_facts_json or {},
            }
            for row in rows
        ]
    )
    if len(rows) != int(release.entry_count):
        return False
    try:
        current = collect_release_snapshot(
            session,
            tenant_id=release.tenant_id,
            dataset_id=release.dataset_id,
            release_number=int(release.release_number),
            now=release.created_at,
        )
    except ReleaseManifestError:
        return False
    return current.readiness_state == "ready" and current.entries == persisted


def _certification_is_current(
    session: Session,
    certification: DatasetReleaseQualityCertification,
    *,
    lock_evidence: bool,
) -> bool:
    release = session.scalar(
        select(DatasetReleaseManifest).where(
            DatasetReleaseManifest.tenant_id == certification.tenant_id,
            DatasetReleaseManifest.dataset_id == certification.dataset_id,
            DatasetReleaseManifest.id == certification.release_id,
        )
    )
    policy = session.scalar(
        select(TenantReleaseQualityGatePolicy).where(
            TenantReleaseQualityGatePolicy.tenant_id == certification.tenant_id,
            TenantReleaseQualityGatePolicy.id == certification.policy_id,
            TenantReleaseQualityGatePolicy.revision == certification.policy_revision,
        )
    )
    baseline = session.scalar(
        select(DatasetQualityBaseline).where(
            DatasetQualityBaseline.tenant_id == certification.tenant_id,
            DatasetQualityBaseline.dataset_id == certification.dataset_id,
            DatasetQualityBaseline.id == certification.baseline_id,
        )
    )
    if release is None or policy is None or baseline is None:
        return False
    if (
        release.manifest_digest != certification.release_manifest_digest
        or int(release.mutation_generation) != int(certification.release_mutation_generation)
        or int(release.serving_generation) != int(certification.release_serving_generation)
        or not _release_manifest_authority_current(session, release)
    ):
        return False
    if policy.policy_digest != _policy_authority_digest(
        scope_type=policy.scope_type,
        scope_value=policy.scope_value,
        channel_id=policy.channel_id,
        thresholds=_thresholds(policy),
        max_certification_age_minutes=int(policy.max_certification_age_minutes),
    ):
        return False
    baseline_items = list(
        session.scalars(
            select(DatasetQualityBaselineItem)
            .where(
                DatasetQualityBaselineItem.tenant_id == certification.tenant_id,
                DatasetQualityBaselineItem.dataset_id == certification.dataset_id,
                DatasetQualityBaselineItem.baseline_id == baseline.id,
            )
            .order_by(DatasetQualityBaselineItem.ordinal, DatasetQualityBaselineItem.id)
        )
    )
    if (
        len(baseline_items) != int(baseline.experiment_count)
        or _baseline_authority_digest(baseline, baseline_items) != baseline.baseline_digest
    ):
        return False
    evidence_rows = list(
        session.scalars(
            select(DatasetReleaseQualityCertificationEvidence)
            .where(
                DatasetReleaseQualityCertificationEvidence.tenant_id == certification.tenant_id,
                DatasetReleaseQualityCertificationEvidence.dataset_id == certification.dataset_id,
                DatasetReleaseQualityCertificationEvidence.certification_id == certification.id,
            )
            .order_by(
                DatasetReleaseQualityCertificationEvidence.ordinal,
                DatasetReleaseQualityCertificationEvidence.id,
            )
        )
    )
    if len(evidence_rows) != int(certification.experiment_count):
        return False
    try:
        current_evidence = collect_release_experiment_evidence(
            session,
            tenant_id=certification.tenant_id,
            dataset_id=certification.dataset_id,
            release_id=certification.release_id,
            experiment_ids=[item.experiment_id for item in baseline_items],
            lock_for_update=lock_evidence,
        )
    except ReleaseManifestError:
        return False
    by_experiment = {item["experiment_id"]: item for item in current_evidence}
    evidence_by_item = {item.baseline_item_id: item for item in evidence_rows}
    if len(evidence_by_item) != len(evidence_rows):
        return False
    for baseline_item in baseline_items:
        current = by_experiment.get(baseline_item.experiment_id)
        stored = evidence_by_item.get(baseline_item.id)
        if current is None or stored is None:
            return False
        if (
            int(baseline_item.ordinal) != int(stored.ordinal)
            or baseline_item.experiment_id != stored.experiment_id
            or int(baseline_item.experiment_sequence) != int(current["experiment_sequence"])
            or baseline_item.query_hash != current["query_hash"]
            or int(baseline_item.experiment_serving_generation)
            != int(current["dataset_serving_generation"])
            or baseline_item.strategy_digest != current["strategy_digest"]
            or baseline_item.result_digest != current["result_digest"]
            or baseline_item.evidence_digest != _baseline_item_digest(current)
            or baseline_item.judgment_digest != current["judgment_digest"]
        ):
            return False
        item_summary = summarize_quality_evidence([current])
        expected_facts = {
            "query_hash": current["query_hash"],
            "dataset_serving_generation": current["dataset_serving_generation"],
            "degraded": current["degraded"],
            "strategy_digest": current["strategy_digest"],
            "result_digest": current["result_digest"],
            "lineage_digest": current["lineage_digest"],
        }
        if (
            stored.status != current["status"]
            or int(stored.result_count) != item_summary.total_result_count
            or int(stored.judged_result_count) != item_summary.judged_result_count
            or int(stored.judgment_count) != item_summary.judgment_count
            or int(stored.multi_judged_results) != item_summary.multi_judged_results
            or int(stored.unanimous_results) != item_summary.unanimous_results
            or int(stored.conflicting_results) != item_summary.conflicting_results
            or stored.exact_agreement_bps != item_summary.exact_agreement_bps
            or stored.mean_score_milli != item_summary.mean_score_milli
            or stored.experiment_digest != _experiment_digest(current)
            or stored.judgment_digest != current["judgment_digest"]
            or stored.safe_facts_json != expected_facts
        ):
            return False
    if {item.id for item in baseline_items} != set(evidence_by_item):
        return False
    evaluation = evaluate_quality_policy(_thresholds(policy), current_evidence)
    summary = asdict(evaluation.summary)
    summary_snapshot = {
        **summary,
        "verdict": evaluation.verdict,
        "failed_rules": [dict(rule) for rule in evaluation.failed_rules],
        "rule_results": [dict(rule) for rule in evaluation.rule_results],
    }
    policy_snapshot = {
        "id": policy.id,
        "scope_type": policy.scope_type,
        "scope_value": policy.scope_value,
        "revision": int(policy.revision),
        "policy_digest": policy.policy_digest,
        "max_certification_age_minutes": int(policy.max_certification_age_minutes),
        **_thresholds(policy),
    }
    metrics = (
        int(certification.experiment_count),
        int(certification.completed_experiment_count),
        int(certification.degraded_experiment_count),
        int(certification.query_count),
        int(certification.total_result_count),
        int(certification.judged_result_count),
        int(certification.judgment_count),
        int(certification.multi_judged_results),
        int(certification.unanimous_results),
        int(certification.conflicting_results),
        certification.judgment_coverage_bps,
        certification.exact_agreement_bps,
        certification.mean_score_milli,
    )
    expected_metrics = (
        summary["experiment_count"],
        summary["completed_experiment_count"],
        summary["degraded_experiment_count"],
        summary["query_count"],
        summary["total_result_count"],
        summary["judged_result_count"],
        summary["judgment_count"],
        summary["multi_judged_results"],
        summary["unanimous_results"],
        summary["conflicting_results"],
        summary["judgment_coverage_bps"],
        summary["exact_agreement_bps"],
        summary["mean_score_milli"],
    )
    if (
        metrics != expected_metrics
        or certification.status != evaluation.verdict
        or int(certification.failed_rule_count) != len(evaluation.failed_rules)
        or certification.policy_snapshot_json != policy_snapshot
        or certification.summary_json != summary_snapshot
        or certification.evidence_digest != evaluation.evidence_digest
    ):
        return False
    expected_digest = canonical_quality_digest(
        "certification",
        {
            "tenant_id": certification.tenant_id,
            "dataset_id": certification.dataset_id,
            "release_id": certification.release_id,
            "baseline_id": baseline.id,
            "baseline_digest": baseline.baseline_digest,
            "policy_id": policy.id,
            "policy_revision": int(policy.revision),
            "policy_digest": policy.policy_digest,
            "release_manifest_digest": release.manifest_digest,
            "release_mutation_generation": int(release.mutation_generation),
            "release_serving_generation": int(release.serving_generation),
            "status": evaluation.verdict,
            "summary": summary_snapshot,
            "evidence_digest": evaluation.evidence_digest,
            "valid_until": certification.valid_until.isoformat(),
        },
    )
    if certification.certification_digest != expected_digest:
        return False
    event = session.scalar(
        select(DatasetReleaseQualityEvent)
        .where(
            DatasetReleaseQualityEvent.tenant_id == certification.tenant_id,
            DatasetReleaseQualityEvent.dataset_id == certification.dataset_id,
            DatasetReleaseQualityEvent.release_id == certification.release_id,
            DatasetReleaseQualityEvent.certification_id == certification.id,
            DatasetReleaseQualityEvent.event_type == "certification_created",
        )
        .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
    )
    if event is None:
        return False
    expected_event_snapshot = {
        "certification_id": certification.id,
        "status": certification.status,
        "policy_id": policy.id,
        "policy_revision": int(policy.revision),
        "baseline_id": baseline.id,
        "baseline_digest": baseline.baseline_digest,
        "policy_digest": policy.policy_digest,
        "release_manifest_digest": release.manifest_digest,
        "evidence_digest": certification.evidence_digest,
        "certification_digest": certification.certification_digest,
        "failed_rule_count": int(certification.failed_rule_count),
        "valid_until": certification.valid_until.isoformat(),
    }
    expected_event_digest = canonical_quality_digest(
        "quality_event",
        {
            "tenant_id": event.tenant_id,
            "dataset_id": event.dataset_id,
            "release_id": event.release_id,
            "channel_id": event.channel_id,
            "event_sequence": int(event.event_sequence),
            "event_type": event.event_type,
            "state": event.state,
            "previous_event_digest": event.previous_event_digest,
            "snapshot": expected_event_snapshot,
        },
    )
    return (
        event.state == certification.status
        and event.safe_snapshot_json == expected_event_snapshot
        and event.event_digest == expected_event_digest
    )


def _waiver_iso(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _waiver_is_current(
    session: Session,
    waiver: DatasetReleaseQualityWaiver,
    *,
    release: DatasetReleaseManifest,
    policy: TenantReleaseQualityGatePolicy,
) -> bool:
    approval = session.scalar(
        select(TenantApprovalRequest).where(
            TenantApprovalRequest.tenant_id == waiver.tenant_id,
            TenantApprovalRequest.id == waiver.approval_request_id,
        )
    )
    if (
        approval is None
        or approval.status != "executed"
        or approval.action_type != "knowledge_base_release_quality_waiver"
        or approval.resource_type != "knowledge_base"
        or approval.resource_id != waiver.dataset_id
        or approval.execution_ticket_hash is None
        or approval.ticket_consumed_at is None
        or not isinstance(approval.execution_ticket_hash, str)
        or len(approval.execution_ticket_hash) != 64
        or any(
            character not in "0123456789abcdef"
            for character in approval.execution_ticket_hash.casefold()
        )
        or approval.executed_at is None
        or approval.executed_by is None
        or waiver.approval_execution_id
        != _execution_id(waiver.tenant_id, waiver.approval_request_id)
    ):
        return False
    if (
        release.readiness_state != "ready"
        or not _release_manifest_authority_current(session, release)
        or session.scalar(
            select(DatasetReleaseEvent.id).where(
                DatasetReleaseEvent.tenant_id == waiver.tenant_id,
                DatasetReleaseEvent.dataset_id == waiver.dataset_id,
                DatasetReleaseEvent.release_id == waiver.release_id,
                DatasetReleaseEvent.event_type == "retired",
            )
        )
        is not None
    ):
        return False
    snapshot = approval.snapshot_json
    if not isinstance(snapshot, Mapping):
        return False
    try:
        payload_hash = sha256(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return False
    if payload_hash != approval.payload_hash:
        return False
    if (
        snapshot.get("tenant_id") != waiver.tenant_id
        or snapshot.get("dataset_id") != waiver.dataset_id
        or snapshot.get("resource_id") != waiver.dataset_id
        or snapshot.get("release_id") != waiver.release_id
        or snapshot.get("channel_id") != waiver.channel_id
        or snapshot.get("policy_id") != waiver.policy_id
        or snapshot.get("policy_revision") != int(waiver.policy_revision)
        or snapshot.get("policy_digest") != policy.policy_digest
        or snapshot.get("manifest_digest") != release.manifest_digest
        or snapshot.get("requested_expires_at") != _waiver_iso(waiver.expires_at)
        or snapshot.get("reason") != waiver.reason
    ):
        return False
    latest_certification = session.scalar(
        select(DatasetReleaseQualityCertification)
        .where(
            DatasetReleaseQualityCertification.tenant_id == waiver.tenant_id,
            DatasetReleaseQualityCertification.dataset_id == waiver.dataset_id,
            DatasetReleaseQualityCertification.release_id == waiver.release_id,
            DatasetReleaseQualityCertification.policy_id == waiver.policy_id,
            DatasetReleaseQualityCertification.policy_revision == waiver.policy_revision,
        )
        .order_by(
            DatasetReleaseQualityCertification.created_at.desc(),
            DatasetReleaseQualityCertification.id.desc(),
        )
    )
    expected_certification_id = snapshot.get("certification_id")
    expected_certification_digest = snapshot.get("certification_digest")
    expected_evidence_digest = snapshot.get("quality_evidence_digest")
    if expected_certification_id is None:
        if latest_certification is not None or expected_certification_digest is not None:
            return False
    elif (
        latest_certification is None
        or latest_certification.id != expected_certification_id
        or latest_certification.certification_digest != expected_certification_digest
        or latest_certification.evidence_digest != expected_evidence_digest
        or not _certification_is_current(session, latest_certification, lock_evidence=False)
    ):
        return False
    event = session.scalar(
        select(DatasetReleaseQualityEvent)
        .where(
            DatasetReleaseQualityEvent.tenant_id == waiver.tenant_id,
            DatasetReleaseQualityEvent.dataset_id == waiver.dataset_id,
            DatasetReleaseQualityEvent.release_id == waiver.release_id,
            DatasetReleaseQualityEvent.channel_id == waiver.channel_id,
            DatasetReleaseQualityEvent.waiver_id == waiver.id,
            DatasetReleaseQualityEvent.event_type == "waiver_approved",
            DatasetReleaseQualityEvent.approval_request_id == waiver.approval_request_id,
            DatasetReleaseQualityEvent.approval_execution_id == waiver.approval_execution_id,
        )
        .order_by(DatasetReleaseQualityEvent.event_sequence.desc())
    )
    if (
        event is None
        or event.state != "active"
        or not isinstance(event.safe_snapshot_json, Mapping)
    ):
        return False
    waiver_snapshot = {
        "tenant_id": waiver.tenant_id,
        "dataset_id": waiver.dataset_id,
        "release_id": waiver.release_id,
        "channel_id": waiver.channel_id,
        "policy_id": waiver.policy_id,
        "policy_revision": int(waiver.policy_revision),
        "release_manifest_digest": waiver.release_manifest_digest,
        "approval_request_id": waiver.approval_request_id,
        "approval_execution_id": waiver.approval_execution_id,
        "quality_gate_digest": snapshot.get("quality_gate_digest"),
        "quality_evidence_digest": snapshot.get("quality_evidence_digest"),
        "valid_from": _waiver_iso(waiver.valid_from),
        "expires_at": _waiver_iso(waiver.expires_at),
        "reason": waiver.reason,
    }
    expected_waiver_digest = canonical_quality_digest("quality_waiver", waiver_snapshot)
    expected_event_snapshot = {**waiver_snapshot, "waiver_digest": expected_waiver_digest}
    expected_event_digest = canonical_quality_digest(
        "quality_event",
        {
            "tenant_id": event.tenant_id,
            "dataset_id": event.dataset_id,
            "release_id": event.release_id,
            "channel_id": event.channel_id,
            "event_sequence": int(event.event_sequence),
            "event_type": event.event_type,
            "state": event.state,
            "previous_event_digest": event.previous_event_digest,
            "snapshot": expected_event_snapshot,
        },
    )
    return (
        waiver.release_manifest_digest == release.manifest_digest
        and waiver.waiver_digest == expected_waiver_digest
        and event.safe_snapshot_json == expected_event_snapshot
        and event.event_digest == expected_event_digest
    )


def resolve_release_quality_gate(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    now: datetime | None = None,
    lock_evidence: bool = False,
) -> dict[str, Any]:
    """Resolve the current gate inside the caller's transaction and lock scope."""

    _ensure_capability(session.connection())
    release = session.scalar(
        select(DatasetReleaseManifest).where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
            DatasetReleaseManifest.id == release_id,
        )
    )
    if release is None:
        raise ReleaseQualityNotFound("Release does not exist")
    channel = session.scalar(
        select(TenantReleaseChannel).where(
            TenantReleaseChannel.tenant_id == tenant_id,
            TenantReleaseChannel.id == channel_id,
        )
    )
    if channel is None or channel.status != "active":
        raise ReleaseQualityNotFound("Release Channel is unavailable")
    policy = _resolve_policy(session, tenant_id=tenant_id, channel=channel)
    base = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "channel_id": channel_id,
        "release_manifest_digest": release.manifest_digest,
        "channel_revision": int(channel.revision),
        "risk_tier": channel.risk_tier,
        "is_default_serving": bool(channel.is_default_serving),
        "policy": _policy_payload(policy) if policy is not None else None,
        "certification": None,
        "waiver": None,
    }
    if policy is None:
        if channel.risk_tier == "high" or channel.is_default_serving:
            return {**base, "state": "unavailable", "reason": "quality_policy_required"}
        return {**base, "state": "not_required", "reason": "no_policy"}
    current = now or datetime.utcnow()
    certification = session.scalar(
        select(DatasetReleaseQualityCertification)
        .where(
            DatasetReleaseQualityCertification.tenant_id == tenant_id,
            DatasetReleaseQualityCertification.dataset_id == dataset_id,
            DatasetReleaseQualityCertification.release_id == release_id,
            DatasetReleaseQualityCertification.policy_id == policy.id,
            DatasetReleaseQualityCertification.policy_revision == policy.revision,
            DatasetReleaseQualityCertification.release_manifest_digest == release.manifest_digest,
            DatasetReleaseQualityCertification.status == "passed",
            DatasetReleaseQualityCertification.valid_until > current,
        )
        .order_by(
            DatasetReleaseQualityCertification.created_at.desc(),
            DatasetReleaseQualityCertification.id.desc(),
        )
    )
    if certification is not None:
        revoked = session.scalar(
            select(DatasetReleaseQualityEvent.id).where(
                DatasetReleaseQualityEvent.tenant_id == tenant_id,
                DatasetReleaseQualityEvent.dataset_id == dataset_id,
                DatasetReleaseQualityEvent.release_id == release_id,
                DatasetReleaseQualityEvent.channel_id == channel_id,
                DatasetReleaseQualityEvent.certification_id == certification.id,
                DatasetReleaseQualityEvent.event_type.in_(("gate_revoked", "gate_expired")),
            )
        )
        if revoked is None and _certification_is_current(
            session, certification, lock_evidence=lock_evidence
        ):
            return {
                **base,
                "state": "passed",
                "reason": "certification_passed",
                "certification": _certification_payload(certification),
            }
    waiver = session.scalar(
        select(DatasetReleaseQualityWaiver)
        .where(
            DatasetReleaseQualityWaiver.tenant_id == tenant_id,
            DatasetReleaseQualityWaiver.dataset_id == dataset_id,
            DatasetReleaseQualityWaiver.release_id == release_id,
            DatasetReleaseQualityWaiver.channel_id == channel_id,
            DatasetReleaseQualityWaiver.policy_id == policy.id,
            DatasetReleaseQualityWaiver.policy_revision == policy.revision,
            DatasetReleaseQualityWaiver.release_manifest_digest == release.manifest_digest,
            DatasetReleaseQualityWaiver.valid_from <= current,
            DatasetReleaseQualityWaiver.expires_at > current,
        )
        .order_by(
            DatasetReleaseQualityWaiver.created_at.desc(),
            DatasetReleaseQualityWaiver.id.desc(),
        )
    )
    if waiver is not None:
        revoked = session.scalar(
            select(DatasetReleaseQualityEvent.id).where(
                DatasetReleaseQualityEvent.tenant_id == tenant_id,
                DatasetReleaseQualityEvent.dataset_id == dataset_id,
                DatasetReleaseQualityEvent.release_id == release_id,
                DatasetReleaseQualityEvent.channel_id == channel_id,
                DatasetReleaseQualityEvent.waiver_id == waiver.id,
                DatasetReleaseQualityEvent.event_type.in_(("waiver_revoked", "waiver_expired")),
            )
        )
        if revoked is None and _waiver_is_current(session, waiver, release=release, policy=policy):
            return {
                **base,
                "state": "waived",
                "reason": "approved_waiver",
                "waiver": _waiver_payload(waiver),
            }
    latest = session.scalar(
        select(DatasetReleaseQualityCertification)
        .where(
            DatasetReleaseQualityCertification.tenant_id == tenant_id,
            DatasetReleaseQualityCertification.dataset_id == dataset_id,
            DatasetReleaseQualityCertification.release_id == release_id,
            DatasetReleaseQualityCertification.policy_id == policy.id,
            DatasetReleaseQualityCertification.policy_revision == policy.revision,
        )
        .order_by(
            DatasetReleaseQualityCertification.created_at.desc(),
            DatasetReleaseQualityCertification.id.desc(),
        )
    )
    reason = "certification_required"
    safe_latest: DatasetReleaseQualityCertification | None = None
    if latest is not None and _certification_is_current(
        session, latest, lock_evidence=lock_evidence
    ):
        safe_latest = latest
        if latest.status == "failed":
            reason = "certification_failed"
        elif latest.valid_until <= current:
            reason = "certification_expired"
        else:
            reason = "certification_stale"
    elif latest is not None:
        reason = "certification_stale"
    return {
        **base,
        "state": "blocked",
        "reason": reason,
        "certification": (_certification_payload(safe_latest) if safe_latest is not None else None),
    }


def get_release_quality_gate(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    release_key = _clean(release_id, "release_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128)
    try:
        with Session(engine, expire_on_commit=False) as session:
            _ensure_capability(session.connection())
            membership, _account = _actor_scope(session, tenant, actor)
            _require_dataset_read(engine, session, membership, dataset)
            return ServiceResult(
                resolve_release_quality_gate(
                    session,
                    tenant_id=tenant,
                    dataset_id=dataset,
                    release_id=release_key,
                    channel_id=channel_key,
                )
            )
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc


def _encode_cursor(kind: str, moment: datetime, row_id: str) -> str:
    payload = json.dumps(
        {"kind": kind, "time": moment.isoformat(), "id": row_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, kind: str) -> tuple[datetime, str] | None:
    if value is None:
        return None
    raw_value = _clean(value, "cursor", 2048)
    try:
        raw = base64.urlsafe_b64decode(raw_value + "=" * (-len(raw_value) % 4))
        payload = json.loads(raw.decode("utf-8"))
        moment = datetime.fromisoformat(payload["time"])
        row_id = _clean(payload["id"], "cursor.id", 128)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseQualityInvalid("cursor is invalid") from exc
    if not isinstance(payload, Mapping) or payload.get("kind") != kind:
        raise ReleaseQualityInvalid("cursor kind is invalid")
    return moment, row_id


def _read_scope(
    engine: Any, tenant_id: str, actor_id: str, dataset_id: str | None = None
) -> tuple[Session, TenantMember]:
    session = Session(engine, expire_on_commit=False)
    try:
        _ensure_capability(session.connection())
        membership, _account = _actor_scope(session, tenant_id, actor_id)
        if dataset_id is not None:
            _require_dataset_read(engine, session, membership, dataset_id)
            if (
                session.scalar(
                    select(Dataset.id).where(
                        Dataset.tenant_id == tenant_id, Dataset.id == dataset_id
                    )
                )
                is None
            ):
                raise ReleaseQualityNotFound("Knowledge Base does not exist")
        return session, membership
    except Exception:
        session.close()
        raise


def list_quality_gate_policies(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    scope_type: str | None = None,
    scope_value: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    page_size = _integer(limit, "limit", 1, 200)
    scope = None if scope_type is None else _clean(scope_type, "scope_type", 16).casefold()
    if scope is not None and scope not in {"global", "risk_tier", "channel"}:
        raise ReleaseQualityInvalid("scope_type is invalid")
    value = None if scope_value is None else _clean(scope_value, "scope_value", 128)
    state = None if status is None else _clean(status, "status", 16).casefold()
    if state is not None and state not in {"active", "disabled"}:
        raise ReleaseQualityInvalid("status is invalid")
    after = _decode_cursor(cursor, "quality_policy")
    session: Session | None = None
    try:
        session, _membership = _read_scope(engine, tenant, actor)
        statement = select(TenantReleaseQualityGatePolicy).where(
            TenantReleaseQualityGatePolicy.tenant_id == tenant
        )
        if scope is not None:
            statement = statement.where(TenantReleaseQualityGatePolicy.scope_type == scope)
        if value is not None:
            statement = statement.where(TenantReleaseQualityGatePolicy.scope_value == value)
        if state is not None:
            statement = statement.where(TenantReleaseQualityGatePolicy.status == state)
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    TenantReleaseQualityGatePolicy.updated_at < moment,
                    and_(
                        TenantReleaseQualityGatePolicy.updated_at == moment,
                        TenantReleaseQualityGatePolicy.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantReleaseQualityGatePolicy.updated_at.desc(),
                    TenantReleaseQualityGatePolicy.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        next_cursor = (
            _encode_cursor("quality_policy", visible[-1].updated_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return ServiceResult(
            {"items": [_policy_payload(row) for row in visible], "next_cursor": next_cursor}
        )
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc
    finally:
        if session is not None:
            session.close()


def update_quality_gate_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    policy_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    name: str | None = None,
    min_experiment_count: int | None = None,
    min_judged_result_count: int | None = None,
    min_judgment_coverage_bps: int | None = None,
    min_exact_agreement_bps: int | None = None,
    min_mean_score_milli: int | None = None,
    max_conflicting_results: int | None = None,
    require_all_experiments_completed: bool | None = None,
    require_no_degraded_results: bool | None = None,
    max_certification_age_minutes: int | None = None,
    status: str | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    policy_key = _clean(policy_id, "policy_id", 64)
    expected = _integer(expected_revision, "expected_revision", 1)
    clean_reason = _reason(reason)
    req = _clean(request_id, "request_id", 128, allow_empty=True)
    ip = _clean(request_ip, "request_ip", 64, allow_empty=True)
    key = _clean(idempotency_key, "idempotency_key", 128)
    raw_changes = {
        "name": name,
        "min_experiment_count": min_experiment_count,
        "min_judged_result_count": min_judged_result_count,
        "min_judgment_coverage_bps": min_judgment_coverage_bps,
        "min_exact_agreement_bps": min_exact_agreement_bps,
        "min_mean_score_milli": min_mean_score_milli,
        "max_conflicting_results": max_conflicting_results,
        "require_all_experiments_completed": require_all_experiments_completed,
        "require_no_degraded_results": require_no_degraded_results,
        "max_certification_age_minutes": max_certification_age_minutes,
        "status": status,
    }
    if not any(value is not None for value in raw_changes.values()):
        raise ReleaseQualityInvalid("quality policy patch must include a changed field")
    normalized: dict[str, Any] = {}
    if name is not None:
        normalized["name"] = _safe(name, "name", 128)
    integer_fields = {
        "min_experiment_count": (min_experiment_count, 1, None),
        "min_judged_result_count": (min_judged_result_count, 0, None),
        "min_judgment_coverage_bps": (min_judgment_coverage_bps, 0, _BPS_MAX),
        "min_exact_agreement_bps": (min_exact_agreement_bps, 0, _BPS_MAX),
        "min_mean_score_milli": (min_mean_score_milli, 0, _SCORE_MILLI_MAX),
        "max_conflicting_results": (max_conflicting_results, 0, None),
        "max_certification_age_minutes": (max_certification_age_minutes, 1, None),
    }
    for field, (value, minimum, maximum) in integer_fields.items():
        if value is not None:
            normalized[field] = _integer(value, field, minimum, maximum)
    for field, value in (
        ("require_all_experiments_completed", require_all_experiments_completed),
        ("require_no_degraded_results", require_no_degraded_results),
    ):
        if value is not None:
            normalized[field] = _boolean(value, field)
    if status is not None:
        target_status = _clean(status, "status", 16).casefold()
        if target_status not in {"active", "disabled"}:
            raise ReleaseQualityInvalid("status is invalid")
        normalized["status"] = target_status
    request_hash = tenant_request_hash(
        operation="update_quality_gate_policy",
        path_identity={"tenant_id": tenant, "policy_id": policy_key},
        body={"expected_revision": expected, "changes": normalized, "reason": clean_reason},
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="update_quality_gate_policy",
        resource_type="release_quality_policy",
        tenant_manager=True,
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        row = session.scalar(
            select(TenantReleaseQualityGatePolicy)
            .where(
                TenantReleaseQualityGatePolicy.tenant_id == tenant,
                TenantReleaseQualityGatePolicy.id == policy_key,
            )
            .with_for_update()
        )
        if row is None:
            raise ReleaseQualityNotFound("Quality policy does not exist")
        if int(row.revision) != expected:
            raise ReleaseQualityConflict("Quality policy revision changed")
        before = _policy_payload(row)
        target_status = normalized.pop("status", row.status)
        for field, value in normalized.items():
            setattr(row, field, value)
        now = datetime.utcnow()
        active_key = f"{row.scope_type}:{row.scope_value}"
        if target_status == "active":
            duplicate = session.scalar(
                select(TenantReleaseQualityGatePolicy.id)
                .where(
                    TenantReleaseQualityGatePolicy.tenant_id == tenant,
                    TenantReleaseQualityGatePolicy.active_scope_key == active_key,
                    TenantReleaseQualityGatePolicy.id != row.id,
                )
                .with_for_update()
            )
            if duplicate is not None:
                raise ReleaseQualityConflict(
                    "An active quality policy already exists for this scope"
                )
            row.active_scope_key = active_key
            row.disabled_at = None
            row.disabled_by = None
        else:
            row.active_scope_key = None
            row.disabled_at = now
            row.disabled_by = actor
        row.status = target_status
        row.revision = expected + 1
        row.updated_at = now
        row.updated_by = actor
        row.policy_digest = _policy_authority_digest(
            scope_type=row.scope_type,
            scope_value=row.scope_value,
            channel_id=row.channel_id,
            thresholds=_thresholds(row),
            max_certification_age_minutes=int(row.max_certification_age_minutes),
        )
        session.flush()
        policy = _policy_payload(row)
        payload = {
            "state": "applied",
            "operation": "quality_policy_update",
            "resource_id": row.id,
            "policy": policy,
            "message": "Release quality policy updated",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.policy_updated",
            resource_type="release_quality_policy",
            resource_id=row.id,
            after={"before_revision": before["revision"], "policy": policy, "reason": clean_reason},
            request_id=req,
            request_ip=ip,
            now=now,
        )
        return _complete(session, reservation, payload, 200, row.id)


def list_quality_baselines(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    page_size = _integer(limit, "limit", 1, 200)
    after = _decode_cursor(cursor, "quality_baseline")
    session: Session | None = None
    try:
        session, _membership = _read_scope(engine, tenant, actor, dataset)
        statement = select(DatasetQualityBaseline).where(
            DatasetQualityBaseline.tenant_id == tenant,
            DatasetQualityBaseline.dataset_id == dataset,
        )
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    DatasetQualityBaseline.created_at < moment,
                    and_(
                        DatasetQualityBaseline.created_at == moment,
                        DatasetQualityBaseline.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetQualityBaseline.created_at.desc(), DatasetQualityBaseline.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        next_cursor = (
            _encode_cursor("quality_baseline", visible[-1].created_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return ServiceResult(
            {"items": [_baseline_payload(row) for row in visible], "next_cursor": next_cursor}
        )
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc
    finally:
        if session is not None:
            session.close()


def get_quality_baseline(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    baseline_id: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    baseline_key = _clean(baseline_id, "baseline_id", 64)
    session: Session | None = None
    try:
        session, _membership = _read_scope(engine, tenant, actor, dataset)
        row = session.scalar(
            select(DatasetQualityBaseline).where(
                DatasetQualityBaseline.tenant_id == tenant,
                DatasetQualityBaseline.dataset_id == dataset,
                DatasetQualityBaseline.id == baseline_key,
            )
        )
        if row is None:
            raise ReleaseQualityNotFound("Quality Baseline does not exist")
        items = list(
            session.scalars(
                select(DatasetQualityBaselineItem)
                .where(
                    DatasetQualityBaselineItem.tenant_id == tenant,
                    DatasetQualityBaselineItem.dataset_id == dataset,
                    DatasetQualityBaselineItem.baseline_id == row.id,
                )
                .order_by(DatasetQualityBaselineItem.ordinal, DatasetQualityBaselineItem.id)
            )
        )
        if len(items) != int(row.experiment_count):
            raise ReleaseQualityUnavailable("Quality Baseline authority is incomplete")
        return ServiceResult({"baseline": _baseline_payload(row, items)})
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc
    finally:
        if session is not None:
            session.close()


def list_release_certifications(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    release_key = _clean(release_id, "release_id", 64)
    page_size = _integer(limit, "limit", 1, 200)
    after = _decode_cursor(cursor, "release_quality_certification")
    session: Session | None = None
    try:
        session, _membership = _read_scope(engine, tenant, actor, dataset)
        if (
            session.scalar(
                select(DatasetReleaseManifest.id).where(
                    DatasetReleaseManifest.tenant_id == tenant,
                    DatasetReleaseManifest.dataset_id == dataset,
                    DatasetReleaseManifest.id == release_key,
                )
            )
            is None
        ):
            raise ReleaseQualityNotFound("Release does not exist")
        statement = select(DatasetReleaseQualityCertification).where(
            DatasetReleaseQualityCertification.tenant_id == tenant,
            DatasetReleaseQualityCertification.dataset_id == dataset,
            DatasetReleaseQualityCertification.release_id == release_key,
        )
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    DatasetReleaseQualityCertification.created_at < moment,
                    and_(
                        DatasetReleaseQualityCertification.created_at == moment,
                        DatasetReleaseQualityCertification.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetReleaseQualityCertification.created_at.desc(),
                    DatasetReleaseQualityCertification.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        next_cursor = (
            _encode_cursor("release_quality_certification", visible[-1].created_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return ServiceResult(
            {"items": [_certification_payload(row) for row in visible], "next_cursor": next_cursor}
        )
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc
    finally:
        if session is not None:
            session.close()


__all__ = [
    "ReleaseQualityConflict",
    "ReleaseQualityError",
    "ReleaseQualityForbidden",
    "ReleaseQualityInvalid",
    "ReleaseQualityNotFound",
    "ReleaseQualityUnavailable",
    "certify_release",
    "collect_baseline_experiment_evidence",
    "create_quality_baseline",
    "create_quality_gate_policy",
    "get_release_quality_gate",
    "resolve_release_quality_gate",
    "update_quality_gate_policy",
    "list_release_certifications",
    "list_quality_gate_policies",
    "list_quality_baselines",
    "get_quality_baseline",
]
