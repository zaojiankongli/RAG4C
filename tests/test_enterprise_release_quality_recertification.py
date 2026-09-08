from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_release_quality_operations import canonical_operations_digest
from core.enterprise_release_quality_service import (
    ReleaseQualityConflict,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    certify_release as stage20_certify_release,
)
from models.orm import (
    DatasetReleaseManifest,
    DatasetReleaseRecertificationJob,
    TenantControlMutationRequest,
    TenantReleaseQualitySloPolicy,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine
from test_enterprise_release_quality_evidence import capture, seed_experiment
from test_enterprise_release_quality_persistence import create_baseline, create_policy

NOW = datetime(2026, 8, 28, 12, 0, 0)


def recertification_module() -> Any:
    try:
        return import_module("core.enterprise_release_quality_recertification")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 21 Recertification service is missing: {exc}")


def _seed_authority(tmp_path: Path) -> tuple[Any, dict[str, str]]:
    engine, now = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    quality_policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    slo_values = {
        "tenant_id": "tenant-a",
        "name": "Enterprise Release SLO",
        "scope_type": "global",
        "scope_value": "*",
        "channel_id": None,
        "active_scope_key": "global:*",
        "status": "active",
        "revision": 1,
        "certification_warning_minutes": 10080,
        "certification_critical_minutes": 1440,
        "waiver_warning_minutes": 720,
        "max_open_alerts": 100,
        "auto_queue_recertification": True,
        "require_passing_certification": True,
        "allow_active_waiver": True,
    }
    slo_digest = canonical_operations_digest("slo_policy", slo_values)
    with Session(engine) as session:
        session.add(
            TenantReleaseQualitySloPolicy(
                id="slo-policy-a",
                policy_digest=slo_digest,
                created_at=now,
                created_by="owner-a",
                updated_at=now,
                updated_by="owner-a",
                disabled_at=None,
                disabled_by=None,
                **slo_values,
            )
        )
        session.commit()
    return engine, {
        "release_id": release_id,
        "baseline_id": baseline["id"],
        "policy_id": quality_policy["id"],
        "manifest_digest": _manifest_digest(engine, release_id),
        "evidence_digest": baseline["baseline_digest"],
    }


def _manifest_digest(engine: Any, release_id: str) -> str:
    with Session(engine) as session:
        return str(
            session.scalar(
                select(DatasetReleaseManifest.manifest_digest).where(
                    DatasetReleaseManifest.tenant_id == "tenant-a",
                    DatasetReleaseManifest.dataset_id == "dataset-a",
                    DatasetReleaseManifest.id == release_id,
                )
            )
        )


def _queue(module: Any, engine: Any, authority: dict[str, str], **overrides: Any) -> Any:
    payload = {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "dataset_id": "dataset-a",
        "release_id": authority["release_id"],
        "channel_id": "channel-production",
        "release_role": "active",
        "baseline_id": authority["baseline_id"],
        "policy_id": authority["policy_id"],
        "expected_policy_revision": 1,
        "slo_policy_id": "slo-policy-a",
        "expected_slo_policy_revision": 1,
        "trigger": "manual",
        "expected_manifest_digest": authority["manifest_digest"],
        "expected_evidence_digest": authority["evidence_digest"],
        "expected_channel_revision": 1,
        "reason": "operator requested governed recertification",
        "request_id": "request-recertification-queue",
        "request_ip": "127.0.0.1",
        "idempotency_key": "recertification-queue-key",
        "now": NOW,
    }
    payload.update(overrides)
    return module.queue_recertification_job(engine, **payload)


def test_queue_is_idempotent_and_cycle_coalesced_without_raw_key(tmp_path: Path) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    first = _queue(module, engine, authority)
    replay = _queue(module, engine, authority, now=NOW + timedelta(hours=1))
    coalesced = _queue(
        module,
        engine,
        authority,
        idempotency_key="different-request-same-cycle",
        request_id="request-same-cycle",
        trigger="alert_escalation",
        now=NOW + timedelta(hours=2),
    )

    assert first.status == 201
    assert replay.body == first.body
    assert coalesced.status == 200
    assert coalesced.body["state"] == "coalesced"
    assert coalesced.body["job"]["id"] == first.body["job"]["id"]
    job = first.body["job"]
    assert job["status"] == "pending"
    assert len(job["cycle_key"]) == 64
    assert job["active_job_key"].startswith("dataset-a:")
    rendered = repr(first.body) + repr(coalesced.body)
    assert "recertification-queue-key" not in rendered
    assert "different-request-same-cycle" not in rendered

    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(DatasetReleaseRecertificationJob)) == 1
        )
        assert session.scalar(select(func.count()).select_from(TenantControlMutationRequest)) >= 2
        row = session.scalar(select(DatasetReleaseRecertificationJob))
        assert row is not None
        assert row.idempotency_key_digest != "recertification-queue-key"
        assert len(row.idempotency_key_digest) == 64
        assert len(row.request_hash) == 64
    engine.dispose()


def test_queue_rejects_stale_or_cross_tenant_authority(tmp_path: Path) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    with pytest.raises(ReleaseQualityConflict, match="manifest"):
        _queue(
            module,
            engine,
            authority,
            expected_manifest_digest="f" * 64,
            idempotency_key="stale-manifest-key",
        )
    with pytest.raises(ReleaseQualityConflict, match="policy revision"):
        _queue(
            module,
            engine,
            authority,
            expected_policy_revision=99,
            idempotency_key="stale-policy-key",
        )
    with pytest.raises(ReleaseQualityForbidden):
        _queue(
            module,
            engine,
            authority,
            tenant_id="tenant-b",
            actor_id="owner-b",
            idempotency_key="cross-tenant-key",
        )
    engine.dispose()


def test_job_claim_heartbeat_ready_and_stale_owner_fences(tmp_path: Path) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    job_id = _queue(module, engine, authority).body["job"]["id"]

    claimed = module.claim_recertification_job(
        engine,
        worker_id="worker-a",
        now=NOW + timedelta(minutes=1),
        lease_seconds=60,
    )
    assert claimed["id"] == job_id
    assert claimed["status"] == "claimed"
    assert claimed["claim_owner"] == "worker-a"

    with pytest.raises(ReleaseQualityConflict, match="owner|lease"):
        module.heartbeat_recertification_job(
            engine,
            job_id=job_id,
            worker_id="worker-b",
            now=NOW + timedelta(minutes=1, seconds=10),
            lease_seconds=60,
        )
    heartbeat = module.heartbeat_recertification_job(
        engine,
        job_id=job_id,
        worker_id="worker-a",
        now=NOW + timedelta(minutes=1, seconds=10),
        lease_seconds=120,
    )
    assert heartbeat["claim_lease_until"] == "2026-08-28T12:03:10.000000Z"

    ready = module.mark_recertification_ready(
        engine,
        job_id=job_id,
        worker_id="worker-a",
        expected_manifest_digest=authority["manifest_digest"],
        expected_evidence_digest=authority["evidence_digest"],
        now=NOW + timedelta(minutes=2),
    )
    assert ready["status"] == "ready_to_certify"
    assert ready["claim_owner"] is None
    assert ready["claim_lease_until"] is None
    engine.dispose()


def test_cancel_is_revision_fenced_and_terminal_replay_is_stable(tmp_path: Path) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    job = _queue(module, engine, authority).body["job"]
    cancelled = module.cancel_recertification_job(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        job_id=job["id"],
        expected_status="pending",
        reason="operator cancelled obsolete cycle",
        request_id="request-recertification-cancel",
        request_ip="127.0.0.1",
        idempotency_key="recertification-cancel-key",
        now=NOW + timedelta(minutes=1),
    )
    replay = module.cancel_recertification_job(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        job_id=job["id"],
        expected_status="pending",
        reason="operator cancelled obsolete cycle",
        request_id="request-recertification-cancel-retry",
        request_ip="127.0.0.1",
        idempotency_key="recertification-cancel-key",
        now=NOW + timedelta(hours=1),
    )
    assert cancelled.status == 200
    assert replay.body == cancelled.body
    assert cancelled.body["job"]["status"] == "cancelled"
    assert cancelled.body["job"]["active_job_key"] is None
    assert cancelled.body["job"]["cancelled_by"] == "owner-a"
    engine.dispose()


def test_complete_requires_ready_state_and_delegates_only_to_stage20(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    job_id = _queue(module, engine, authority).body["job"]["id"]
    module.claim_recertification_job(
        engine,
        worker_id="worker-a",
        now=NOW + timedelta(minutes=1),
        lease_seconds=60,
    )
    module.mark_recertification_ready(
        engine,
        job_id=job_id,
        worker_id="worker-a",
        expected_manifest_digest=authority["manifest_digest"],
        expected_evidence_digest=authority["evidence_digest"],
        now=NOW + timedelta(minutes=1, seconds=30),
    )

    local_certification = stage20_certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=authority["release_id"],
        channel_id="channel-production",
        baseline_id=authority["baseline_id"],
        policy_id=authority["policy_id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="create local Stage20 certification fixture",
        request_id="request-stage20-certification-fixture",
        request_ip="127.0.0.1",
        idempotency_key="stage20-certification-fixture-key",
    )
    assert local_certification.status == 201
    certification_id = local_certification.body["certification"]["id"]

    calls: list[dict[str, Any]] = []

    def fake_certify_release(_engine: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return local_certification

    monkeypatch.setattr(module, "_certify_release", fake_certify_release)
    completed = module.complete_recertification_job(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        job_id=job_id,
        reason="operator confirms current evidence",
        request_id="request-recertification-complete",
        request_ip="127.0.0.1",
        idempotency_key="recertification-complete-key",
        now=NOW + timedelta(minutes=2),
    )
    assert completed.status == 201
    assert completed.body["job"]["status"] == "completed"
    assert completed.body["job"]["result_certification_id"] == certification_id
    assert completed.body["job"]["active_job_key"] is None
    assert len(calls) == 1
    assert calls[0]["release_id"] == authority["release_id"]
    assert calls[0]["baseline_id"] == authority["baseline_id"]
    assert calls[0]["policy_id"] == authority["policy_id"]
    assert "experiment" not in repr(completed.body).casefold()
    engine.dispose()


def test_strict_inputs_and_safe_fields_fail_closed(tmp_path: Path) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    with pytest.raises(ReleaseQualityInvalid, match="exact integer"):
        _queue(
            module,
            engine,
            authority,
            expected_channel_revision=True,
            idempotency_key="bad-channel-revision-key",
        )
    with pytest.raises(ReleaseQualityInvalid, match="reason"):
        _queue(
            module,
            engine,
            authority,
            reason="ticket=opaque-secret-ticket",
            idempotency_key="unsafe-reason-key",
        )
    forbidden = {
        "query",
        "result_body",
        "judgment_note",
        "ticket",
        "idempotency_key",
        "credential",
    }
    assert forbidden.isdisjoint(DatasetReleaseRecertificationJob.__table__.c.keys())
    engine.dispose()


def test_list_recertification_jobs_is_read_scoped_status_filtered_and_cursor_safe(
    tmp_path: Path,
) -> None:
    module = recertification_module()
    engine, authority = _seed_authority(tmp_path)
    queued = _queue(module, engine, authority).body["job"]

    page = module.list_recertification_jobs(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        status="pending",
        limit=1,
    )
    assert page.status == 200
    assert [item["id"] for item in page.body["items"]] == [queued["id"]]
    assert page.body["next_cursor"] is None
    assert "raw-idempotency" not in repr(page.body)

    empty = module.list_recertification_jobs(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        status="completed",
    )
    assert empty.body == {"items": [], "next_cursor": None}

    with pytest.raises(ReleaseQualityInvalid, match="cursor"):
        module.list_recertification_jobs(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            cursor="tampered-cursor",
        )
    with pytest.raises(ReleaseQualityInvalid, match="status"):
        module.list_recertification_jobs(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            status="mystery",
        )
    engine.dispose()
