from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_release_quality_alerts import (
    QualityAlertConflict,
    QualityAlertForbidden,
    QualityAlertInvalid,
    QualityAlertUnavailable,
    acknowledge_quality_alert,
    create_or_update_quality_alert_from_observation,
    get_quality_alert,
    list_quality_alerts,
    resolve_matching_quality_alert_from_observation,
    resolve_quality_alert,
    suppress_quality_alert,
)
from models.orm import (
    Account,
    DatasetReleaseManifest,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityObservation,
    TenantAuditEvent,
    TenantControlMutationRequest,
    TenantMember,
    TenantReleaseQualityScanRun,
    TenantReleaseQualityScanSchedule,
    TenantReleaseQualitySloPolicy,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


NOW = datetime(2026, 8, 28, 12, 0, 0)


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _seed_quality_authority(engine: Any) -> None:
    with Session(engine) as session:
        session.add(
            TenantReleaseQualitySloPolicy(
                id="slo-policy-a",
                tenant_id="tenant-a",
                name="Release quality SLO",
                scope_type="global",
                scope_value="*",
                channel_id=None,
                active_scope_key="global:*",
                status="active",
                revision=1,
                certification_warning_minutes=10080,
                certification_critical_minutes=1440,
                waiver_warning_minutes=720,
                max_open_alerts=20,
                auto_queue_recertification=True,
                require_passing_certification=True,
                allow_active_waiver=True,
                policy_digest=_digest("p"),
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.flush()
        session.add(
            TenantReleaseQualityScanSchedule(
                id="schedule-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                slo_policy_id="slo-policy-a",
                status="active",
                active_policy_slot="slo-policy-a",
                revision=1,
                interval_seconds=300,
                next_run_at=NOW,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.add(
            DatasetReleaseManifest(
                id="release-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_number=1,
                profile_revision=4,
                ownership_revision=5,
                workspace_id="workspace-a",
                workspace_revision=2,
                mutation_generation=9,
                serving_generation=3,
                schema_version=1,
                policy_digest=_digest("q"),
                manifest_digest=_digest("m"),
                readiness_digest=_digest("r"),
                readiness_state="ready",
                entry_count=0,
                blocker_count=0,
                readiness_blockers_json=[],
                created_at=NOW,
                created_by="owner-a",
                reason="quality operations fixture",
                request_id="fixture-release",
            )
        )
        session.flush()
        session.add(
            TenantReleaseQualityScanRun(
                id="scan-run-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                schedule_id="schedule-a",
                slo_policy_id="slo-policy-a",
                slo_policy_revision=1,
                status="pending",
                planned_at=NOW,
                attempt_count=0,
                max_attempts=3,
                observation_count=0,
                alert_count=0,
                recertification_job_count=0,
                idempotency_key_digest=_digest("i"),
                request_hash=_digest("h"),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add_all(
            [
                Account(id="editor-a", name="Editor A", email="editor-a@alerts.test"),
                Account(id="member-a", name="Member A", email="member-a@alerts.test"),
                TenantMember(
                    account_id="editor-a",
                    tenant_id="tenant-a",
                    role="editor",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-a",
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id="tenant-a",
                    role="member",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-a",
                ),
            ]
        )
        session.add(_observation("observation-a", "scan-run-a", observed_at=NOW))
        session.commit()


def _observation(
    observation_id: str,
    scan_run_id: str,
    *,
    observed_at: datetime,
    gate_state: str = "blocked",
    gate_reason: str = "quality_gate_blocked",
    severity: str = "critical",
) -> DatasetReleaseQualityObservation:
    return DatasetReleaseQualityObservation(
        id=observation_id,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        release_id="release-a",
        channel_id="channel-production",
        scan_run_id=scan_run_id,
        slo_policy_id="slo-policy-a",
        slo_policy_revision=1,
        release_role="active",
        gate_state=gate_state,
        gate_reason=gate_reason,
        certification_id=None,
        certification_digest=None,
        certification_valid_until=None,
        waiver_id=None,
        waiver_digest=None,
        waiver_expires_at=None,
        minutes_to_certification_expiry=None,
        minutes_to_waiver_expiry=None,
        severity=severity,
        observation_digest=_digest(observation_id),
        observed_at=observed_at,
        observed_by="system:quality-scanner",
        request_id=f"request-{observation_id}",
    )


def _add_observation(
    engine: Any,
    observation_id: str,
    *,
    observed_at: datetime,
    gate_state: str = "blocked",
    gate_reason: str = "quality_gate_blocked",
    severity: str = "critical",
) -> None:
    run_id = f"scan-run-{observation_id}"
    with Session(engine) as session:
        session.add(
            TenantReleaseQualityScanRun(
                id=run_id,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                schedule_id="schedule-a",
                slo_policy_id="slo-policy-a",
                slo_policy_revision=1,
                status="pending",
                planned_at=observed_at,
                attempt_count=0,
                max_attempts=3,
                observation_count=0,
                alert_count=0,
                recertification_job_count=0,
                idempotency_key_digest=_digest(run_id),
                request_hash=_digest(f"hash-{run_id}"),
                created_at=observed_at,
                updated_at=observed_at,
            )
        )
        session.flush()
        session.add(
            _observation(
                observation_id,
                run_id,
                observed_at=observed_at,
                gate_state=gate_state,
                gate_reason=gate_reason,
                severity=severity,
            )
        )
        session.commit()


@pytest.fixture
def quality_engine(tmp_path: Path):
    engine, _ = _release_engine(tmp_path)
    _seed_quality_authority(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _create_alert(engine: Any, *, observation_id: str = "observation-a") -> str:
    with Session(engine, expire_on_commit=False) as session:
        observation = session.scalar(
            select(DatasetReleaseQualityObservation).where(
                DatasetReleaseQualityObservation.tenant_id == "tenant-a",
                DatasetReleaseQualityObservation.dataset_id == "dataset-a",
                DatasetReleaseQualityObservation.id == observation_id,
            )
        )
        assert observation is not None
        alert = create_or_update_quality_alert_from_observation(
            session,
            observation,
            now=NOW,
        )
        assert alert is not None
        alert_id = alert.id
        session.commit()
        return alert_id


def test_scan_alert_helper_updates_active_cycle_resolves_and_starts_new_cycle(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)
    _add_observation(
        quality_engine,
        "observation-b",
        observed_at=NOW + timedelta(minutes=5),
    )

    with Session(quality_engine, expire_on_commit=False) as session:
        observation = session.get(DatasetReleaseQualityObservation, "observation-b")
        assert observation is not None
        updated = create_or_update_quality_alert_from_observation(
            session,
            observation,
            now=NOW + timedelta(minutes=5),
            expected_revision=1,
        )
        assert updated is not None
        assert updated.id == alert_id
        assert updated.status == "open"
        assert updated.revision == 2
        assert updated.occurrence_count == 2
        assert updated.source_observation_id == "observation-b"
        assert updated.last_observed_at == NOW + timedelta(minutes=5)
        session.commit()

    _add_observation(
        quality_engine,
        "observation-healthy",
        observed_at=NOW + timedelta(minutes=10),
        gate_state="passing",
        gate_reason="quality_gate_current",
        severity="healthy",
    )
    with Session(quality_engine, expire_on_commit=False) as session:
        healthy = session.get(DatasetReleaseQualityObservation, "observation-healthy")
        assert healthy is not None
        resolved = resolve_matching_quality_alert_from_observation(
            session,
            healthy,
            now=NOW + timedelta(minutes=10),
        )
        assert [row.id for row in resolved] == [alert_id]
        assert resolved[0].status == "resolved"
        assert resolved[0].active_alert_key is None
        assert resolved[0].resolved_by == "system:quality-scanner"
        assert resolved[0].revision == 3
        session.commit()

    _add_observation(
        quality_engine,
        "observation-new-cycle",
        observed_at=NOW + timedelta(minutes=15),
    )
    new_alert_id = _create_alert(quality_engine, observation_id="observation-new-cycle")
    assert new_alert_id != alert_id

    with Session(quality_engine) as session:
        rows = list(
            session.scalars(
                select(DatasetReleaseQualityAlert)
                .where(
                    DatasetReleaseQualityAlert.tenant_id == "tenant-a",
                    DatasetReleaseQualityAlert.dataset_id == "dataset-a",
                )
                .order_by(DatasetReleaseQualityAlert.created_at, DatasetReleaseQualityAlert.id)
            )
        )
        assert len(rows) == 2
        assert {row.status for row in rows} == {"open", "resolved"}
        assert all(row.active_alert_key is None for row in rows if row.status == "resolved")
        assert all(row.active_alert_key for row in rows if row.status == "open")


def test_operator_lifecycle_is_revision_fenced_audited_replayed_and_body_free(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)

    acknowledged = acknowledge_quality_alert(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        alert_id=alert_id,
        expected_revision=1,
        comment="Triaged by the release operations owner.",
        request_id="request-ack-1",
        request_ip="127.0.0.1",
        idempotency_key="alert-ack-key",
        now=NOW + timedelta(minutes=1),
    )
    assert acknowledged.status == 200
    assert acknowledged.body["alert"]["status"] == "acknowledged"
    assert acknowledged.body["alert"]["revision"] == 2

    replay = acknowledge_quality_alert(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        alert_id=alert_id,
        expected_revision=1,
        comment="Triaged by the release operations owner.",
        request_id="request-ack-replay",
        request_ip="127.0.0.1",
        idempotency_key="alert-ack-key",
        now=NOW + timedelta(minutes=2),
    )
    assert replay.body == acknowledged.body

    with pytest.raises(QualityAlertConflict, match="revision"):
        suppress_quality_alert(
            quality_engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
            expected_revision=1,
            suppressed_until=NOW + timedelta(hours=2),
            comment="Maintenance window.",
            request_id="request-stale-suppress",
            request_ip="127.0.0.1",
            idempotency_key="alert-stale-suppress-key",
            now=NOW + timedelta(minutes=3),
        )

    suppressed = suppress_quality_alert(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        alert_id=alert_id,
        expected_revision=2,
        suppressed_until=NOW + timedelta(hours=2),
        comment="Maintenance window.",
        request_id="request-suppress",
        request_ip="127.0.0.1",
        idempotency_key="alert-suppress-key",
        now=NOW + timedelta(minutes=4),
    )
    assert suppressed.body["alert"]["status"] == "suppressed"
    assert suppressed.body["alert"]["revision"] == 3
    assert suppressed.body["alert"]["active_alert_key"] == (
        "dataset-a:release-a:channel-production:active:quality_gate_blocked"
    )

    resolved = resolve_quality_alert(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        alert_id=alert_id,
        expected_revision=3,
        comment="Issue resolved after the maintenance window.",
        request_id="request-resolve",
        request_ip="127.0.0.1",
        idempotency_key="alert-resolve-key",
        now=NOW + timedelta(minutes=5),
    )
    assert resolved.body["alert"]["status"] == "resolved"
    assert resolved.body["alert"]["revision"] == 4
    assert resolved.body["alert"]["active_alert_key"] is None

    with Session(quality_engine) as session:
        observation = session.get(DatasetReleaseQualityObservation, "observation-a")
        assert observation is not None
        assert observation.gate_state == "blocked"
        assert observation.severity == "critical"
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.actor_id == "owner-a",
                    TenantAuditEvent.resource_type == "release_quality_alert",
                    TenantAuditEvent.resource_id == alert_id,
                )
            )
            == 3
        )
        stored_keys = list(session.scalars(select(TenantControlMutationRequest.idempotency_key)))
        assert "alert-ack-key" not in stored_keys
        rendered = repr(resolved.body)
        for forbidden in ("query", "result_body", "judgment_note", "ticket", "alert-ack-key"):
            assert forbidden not in rendered

    listed = list_quality_alerts(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
    )
    assert listed.body["count"] == 1
    assert listed.body["items"][0]["id"] == alert_id
    detail = get_quality_alert(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        alert_id=alert_id,
    )
    assert detail.body["alert"]["source_observation_id"] == "observation-a"


def test_unsafe_comments_are_rejected_without_persistence_or_raw_secret_leak(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)
    unsafe = "query=customer password=secret; private judgment note ticket-123"
    with pytest.raises(QualityAlertInvalid, match="comment"):
        acknowledge_quality_alert(
            quality_engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
            expected_revision=1,
            comment=unsafe,
            request_id="request-unsafe-comment",
            request_ip="127.0.0.1",
            idempotency_key="unsafe-comment-key",
            now=NOW,
        )

    with Session(quality_engine) as session:
        alert = session.get(DatasetReleaseQualityAlert, alert_id)
        assert alert is not None
        assert alert.status == "open"
        assert alert.acknowledged_comment is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantControlMutationRequest)
                .where(TenantControlMutationRequest.tenant_id == "tenant-a")
            )
            == 0
        )
        assert unsafe not in repr(alert)


def test_mutations_require_knowledge_manage_reads_require_knowledge_read_and_isolate_scope(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)

    with pytest.raises((QualityAlertForbidden, QualityAlertUnavailable)):
        acknowledge_quality_alert(
            quality_engine,
            tenant_id="tenant-a",
            actor_id="editor-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
            expected_revision=1,
            comment="Editor cannot manage alerts.",
            request_id="request-editor",
            request_ip="127.0.0.1",
            idempotency_key="editor-alert-key",
            now=NOW,
        )

    with pytest.raises(QualityAlertUnavailable):
        list_quality_alerts(
            quality_engine,
            tenant_id="tenant-a",
            actor_id="member-a",
            dataset_id="dataset-a",
        )

    readable = list_quality_alerts(
        quality_engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
    )
    assert readable.body["count"] == 1

    with pytest.raises(QualityAlertForbidden):
        get_quality_alert(
            quality_engine,
            tenant_id="tenant-b",
            actor_id="owner-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
        )


def test_read_fails_closed_when_alert_source_observation_does_not_match(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)
    with Session(quality_engine) as session:
        alert = session.get(DatasetReleaseQualityAlert, alert_id)
        assert alert is not None
        alert.source_observation_digest = _digest("forged")
        session.commit()

    with pytest.raises(QualityAlertUnavailable, match="observation"):
        get_quality_alert(
            quality_engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            alert_id=alert_id,
        )


def test_system_scanner_lifecycle_writes_safe_tenant_audit_facts_in_transaction(
    quality_engine: Any,
) -> None:
    with Session(quality_engine, expire_on_commit=False) as session:
        observation = session.get(DatasetReleaseQualityObservation, "observation-a")
        assert observation is not None
        created = create_or_update_quality_alert_from_observation(
            session,
            observation,
            now=NOW,
        )
        assert created is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.actor_id == "system:quality-scanner",
                    TenantAuditEvent.resource_type == "release_quality_alert",
                    TenantAuditEvent.resource_id == created.id,
                )
            )
            == 1
        )
        alert_id = created.id
        session.commit()

    _add_observation(
        quality_engine,
        "observation-b",
        observed_at=NOW + timedelta(minutes=5),
    )
    with Session(quality_engine, expire_on_commit=False) as session:
        observation = session.get(DatasetReleaseQualityObservation, "observation-b")
        assert observation is not None
        updated = create_or_update_quality_alert_from_observation(
            session,
            observation,
            now=NOW + timedelta(minutes=5),
            expected_revision=1,
        )
        assert updated is not None
        assert updated.id == alert_id
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.actor_id == "system:quality-scanner",
                    TenantAuditEvent.resource_type == "release_quality_alert",
                    TenantAuditEvent.resource_id == alert_id,
                )
            )
            == 2
        )
        session.commit()

    _add_observation(
        quality_engine,
        "observation-healthy",
        observed_at=NOW + timedelta(minutes=10),
        gate_state="passing",
        gate_reason="quality_gate_current",
        severity="healthy",
    )
    with Session(quality_engine, expire_on_commit=False) as session:
        observation = session.get(DatasetReleaseQualityObservation, "observation-healthy")
        assert observation is not None
        resolved = resolve_matching_quality_alert_from_observation(
            session,
            observation,
            now=NOW + timedelta(minutes=10),
        )
        assert [row.id for row in resolved] == [alert_id]
        assert (
            session.scalar(
                select(func.count())
                .select_from(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.actor_id == "system:quality-scanner",
                    TenantAuditEvent.resource_type == "release_quality_alert",
                    TenantAuditEvent.resource_id == alert_id,
                )
            )
            == 3
        )
        session.commit()

    with Session(quality_engine) as session:
        events = list(
            session.scalars(
                select(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.actor_id == "system:quality-scanner",
                    TenantAuditEvent.resource_type == "release_quality_alert",
                    TenantAuditEvent.resource_id == alert_id,
                )
                .order_by(TenantAuditEvent.sequence)
            )
        )
        assert [event.action for event in events] == [
            "knowledge_base.release_quality.alert_created",
            "knowledge_base.release_quality.alert_updated",
            "knowledge_base.release_quality.alert_resolved",
        ]
        expected_facts = [
            (_digest("observation-a"), "scan-run-a", _digest("observation-a"), "scan-run-a"),
            (
                _digest("observation-a"),
                "scan-run-a",
                _digest("observation-b"),
                "scan-run-observation-b",
            ),
            (
                _digest("observation-b"),
                "scan-run-observation-b",
                _digest("observation-healthy"),
                "scan-run-observation-healthy",
            ),
        ]
        for event, (before_digest, before_run, after_digest, after_run) in zip(
            events, expected_facts, strict=True
        ):
            assert event.before_snapshot is not None
            assert event.after_snapshot is not None
            assert event.before_snapshot["observation_digest"] == before_digest
            assert event.before_snapshot["scan_run_id"] == before_run
            assert event.after_snapshot["observation_digest"] == after_digest
            assert event.after_snapshot["scan_run_id"] == after_run
            rendered = repr((event.before_snapshot, event.after_snapshot))
            for forbidden in ("query", "result_body", "judgment_note", "ticket", "credential"):
                assert forbidden not in rendered


def test_reads_fail_closed_for_tampered_persisted_lifecycle_comments_without_leak(
    quality_engine: Any,
) -> None:
    alert_id = _create_alert(quality_engine)
    unsafe_comments = {
        "acknowledged_comment": "query=customer-search",
        "resolved_comment": "ticket=INC-123",
        "suppressed_comment": "api_key=sk_test_tampered",
    }

    for field, unsafe in unsafe_comments.items():
        with Session(quality_engine) as session:
            alert = session.get(DatasetReleaseQualityAlert, alert_id)
            assert alert is not None
            alert.acknowledged_comment = None
            alert.resolved_comment = None
            alert.suppressed_comment = None
            setattr(alert, field, unsafe)
            session.commit()

        for reader in (list_quality_alerts, get_quality_alert):
            with pytest.raises(QualityAlertUnavailable, match="comment") as exc_info:
                if reader is list_quality_alerts:
                    reader(
                        quality_engine,
                        tenant_id="tenant-a",
                        actor_id="owner-a",
                        dataset_id="dataset-a",
                    )
                else:
                    reader(
                        quality_engine,
                        tenant_id="tenant-a",
                        actor_id="owner-a",
                        dataset_id="dataset-a",
                        alert_id=alert_id,
                    )
            assert unsafe not in str(exc_info.value)
