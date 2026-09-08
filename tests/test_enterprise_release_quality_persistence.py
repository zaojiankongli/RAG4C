from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.enterprise_release_quality import (
    ReleaseQualityConflict,
    certify_release,
    create_quality_baseline,
    create_quality_gate_policy,
    get_quality_baseline,
    get_release_quality_gate,
    list_quality_baselines,
    list_quality_gate_policies,
    list_release_certifications,
    update_quality_gate_policy,
)
from models.orm import (
    Dataset,
    DatasetQualityBaseline,
    DatasetQualityBaselineItem,
    DatasetReleaseQualityCertification,
    DatasetReleaseQualityCertificationEvidence,
    DatasetReleaseEntry,
    DatasetReleaseEvent,
    DatasetReleaseQualityEvent,
    RetrievalExperiment,
    RetrievalJudgment,
    TenantControlMutationRequest,
    TenantReleaseQualityGatePolicy,
)
from test_enterprise_release_quality_evidence import capture, seed_experiment
from test_enterprise_knowledge_base_release_snapshot import _release_engine


def create_policy(engine, *, key: str = "quality-policy-key"):
    return create_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        name="Production quality",
        scope_type="channel",
        scope_value="channel-production",
        channel_id="channel-production",
        min_experiment_count=1,
        min_judged_result_count=2,
        min_judgment_coverage_bps=10_000,
        min_exact_agreement_bps=10_000,
        min_mean_score_milli=2_000,
        max_conflicting_results=0,
        require_all_experiments_completed=True,
        require_no_degraded_results=True,
        max_certification_age_minutes=1440,
        reason="protect production",
        request_id="request-quality-policy",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def create_baseline(engine, *, key: str = "quality-baseline-key"):
    return create_quality_baseline(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        name="客服核心问题",
        experiment_ids=["experiment-quality-a"],
        reason="freeze reviewer evidence",
        request_id="request-quality-baseline",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def test_quality_policy_is_tenant_scoped_revisioned_audited_and_replayed(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    first = create_policy(engine)
    replay = create_policy(engine)
    assert first.status == 201
    assert replay.body == first.body
    policy = first.body["policy"]
    assert policy["scope_type"] == "channel"
    assert policy["scope_value"] == "channel-production"
    assert policy["revision"] == 1
    assert len(policy["policy_digest"]) == 64
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(TenantReleaseQualityGatePolicy)) == 1
        assert session.scalar(select(func.count()).select_from(TenantControlMutationRequest)) == 1
    engine.dispose()


def test_quality_baseline_freezes_existing_experiment_and_judgment_digests(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    seed_experiment(engine)
    created = create_baseline(engine)
    assert created.status == 201
    baseline = created.body["baseline"]
    assert baseline["baseline_revision"] == 1
    assert baseline["experiment_count"] == 1
    assert baseline["query_count"] == 1
    assert len(baseline["baseline_digest"]) == 64
    rendered = repr(created.body)
    assert "Where is the handbook?" not in rendered
    assert "private reviewer note" not in rendered
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetQualityBaseline)) == 1
        assert session.scalar(select(func.count()).select_from(DatasetQualityBaselineItem)) == 1
    engine.dispose()


def test_certification_persists_passed_verdict_evidence_event_and_gate(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    certified = certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
        baseline_id=baseline["id"],
        policy_id=policy["id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="certify production candidate",
        request_id="request-quality-certify",
        request_ip="127.0.0.1",
        idempotency_key="quality-certify-key",
    )
    assert certified.status == 201
    certification = certified.body["certification"]
    assert certification["status"] == "passed"
    assert certification["judgment_coverage_bps"] == 10_000
    assert certification["exact_agreement_bps"] == 10_000
    assert certification["mean_score_milli"] == 2_000
    assert certification["failed_rule_count"] == 0
    gate = get_release_quality_gate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
    ).body
    assert gate["state"] == "passed"
    assert gate["certification"]["id"] == certification["id"]
    assert gate["waiver"] is None
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(DatasetReleaseQualityCertification))
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(DatasetReleaseQualityCertificationEvidence)
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(DatasetReleaseQualityEvent)) == 1
    engine.dispose()


def test_certification_rejects_stale_baseline_judgment_digest(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    with Session(engine) as session:
        judgment = session.get(RetrievalJudgment, "judgment-quality-2-a")
        assert judgment is not None
        judgment.score = 0
        judgment.revision = 2
        session.commit()
    with pytest.raises(ReleaseQualityConflict, match="Baseline evidence is stale"):
        certify_release(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            release_id=release_id,
            channel_id="channel-production",
            baseline_id=baseline["id"],
            policy_id=policy["id"],
            expected_policy_revision=1,
            expected_channel_revision=1,
            reason="stale certification",
            request_id="request-quality-stale",
            request_ip="127.0.0.1",
            idempotency_key="quality-stale-key",
        )
    engine.dispose()


def test_quality_gate_fails_closed_when_policy_exists_without_certification(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    create_policy(engine)
    gate = get_release_quality_gate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
    ).body
    assert gate["state"] == "blocked"
    assert gate["reason"] == "certification_required"
    assert gate["policy"]["revision"] == 1
    engine.dispose()


def test_quality_policy_update_is_revision_fenced_replayed_and_listed(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    policy = create_policy(engine).body["policy"]
    updated = update_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        policy_id=policy["id"],
        expected_revision=1,
        min_mean_score_milli=2500,
        reason="raise production threshold",
        request_id="request-quality-policy-update",
        request_ip="127.0.0.1",
        idempotency_key="quality-policy-update-key",
    )
    replay = update_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        policy_id=policy["id"],
        expected_revision=1,
        min_mean_score_milli=2500,
        reason="raise production threshold",
        request_id="request-quality-policy-update",
        request_ip="127.0.0.1",
        idempotency_key="quality-policy-update-key",
    )
    assert updated.status == 200
    assert replay.body == updated.body
    authority = updated.body["policy"]
    assert authority["revision"] == 2
    assert authority["min_mean_score_milli"] == 2500
    assert authority["policy_digest"] != policy["policy_digest"]
    listed = list_quality_gate_policies(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        scope_type="channel",
        scope_value="channel-production",
        status="active",
        cursor=None,
        limit=20,
    ).body
    assert [item["id"] for item in listed["items"]] == [policy["id"]]
    with pytest.raises(ReleaseQualityConflict, match="revision changed"):
        update_quality_gate_policy(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            policy_id=policy["id"],
            expected_revision=1,
            min_mean_score_milli=2600,
            reason="stale update",
            request_id="request-quality-policy-update-stale",
            request_ip="127.0.0.1",
            idempotency_key="quality-policy-update-stale-key",
        )
    engine.dispose()


def test_baseline_detail_and_certification_history_are_safe_and_tenant_scoped(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    detail = get_quality_baseline(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        baseline_id=baseline["id"],
    ).body
    listed = list_quality_baselines(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        cursor=None,
        limit=20,
    ).body
    assert detail["baseline"]["id"] == baseline["id"]
    assert listed["items"][0]["id"] == baseline["id"]
    assert "Where is the handbook?" not in repr(detail)
    assert "private reviewer note" not in repr(detail)
    certification = certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
        baseline_id=baseline["id"],
        policy_id=policy["id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="certification history",
        request_id="request-quality-certification-history",
        request_ip="127.0.0.1",
        idempotency_key="quality-certification-history-key",
    ).body["certification"]
    history = list_release_certifications(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        cursor=None,
        limit=20,
    ).body
    assert history["items"][0]["id"] == certification["id"]
    assert "private result body" not in repr(history)
    engine.dispose()


def test_certification_rejects_release_authority_drift_and_retirement(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.mutation_generation = 10
        session.commit()
    with pytest.raises(ReleaseQualityConflict, match="Release authority is stale"):
        certify_release(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            release_id=release_id,
            channel_id="channel-production",
            baseline_id=baseline["id"],
            policy_id=policy["id"],
            expected_policy_revision=1,
            expected_channel_revision=1,
            reason="stale release authority",
            request_id="request-quality-release-stale",
            request_ip="127.0.0.1",
            idempotency_key="quality-release-stale-key",
        )
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.mutation_generation = 9
        session.add(
            DatasetReleaseEvent(
                id="release-retired-for-quality",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release_id,
                channel_id=None,
                event_type="retired",
                actor_id="owner-a",
                reason="retire quality candidate",
                request_id="request-retire-quality",
                previous_binding_revision=None,
                current_binding_revision=None,
            )
        )
        session.commit()
    with pytest.raises(ReleaseQualityConflict, match="Release is retired"):
        certify_release(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            release_id=release_id,
            channel_id="channel-production",
            baseline_id=baseline["id"],
            policy_id=policy["id"],
            expected_policy_revision=1,
            expected_channel_revision=1,
            reason="retired release authority",
            request_id="request-quality-release-retired",
            request_ip="127.0.0.1",
            idempotency_key="quality-release-retired-key",
        )
    engine.dispose()


def test_baseline_rejects_malformed_experiment_snapshots_with_domain_error(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            RetrievalExperiment(
                id="experiment-quality-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                query="malformed snapshot",
                query_hash="a" * 64,
                strategy_snapshot=["malformed"],
                result_snapshot={
                    "dataset_serving_generation": 3,
                    "degraded": False,
                    "results": [],
                },
                evidence_lineage={"dataset_serving_generation": 3},
                latency_ms=1,
                status="completed",
                created_by="runner-a",
                run_id="run-malformed",
            )
        )
        session.commit()
    with pytest.raises(ReleaseQualityConflict, match="authority is malformed"):
        create_baseline(engine, key="quality-malformed-baseline-key")
    engine.dispose()


def test_gate_rejects_tampered_certification_envelope_and_manifest_append(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    certification = certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
        baseline_id=baseline["id"],
        policy_id=policy["id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="tamper probe",
        request_id="request-quality-tamper",
        request_ip="127.0.0.1",
        idempotency_key="quality-tamper-key",
    ).body["certification"]
    with engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER trg_dataset_release_quality_certifications_no_update")
        )
        connection.execute(
            text(
                "UPDATE dataset_release_quality_certifications "
                "SET certification_digest=:digest, summary_json=:summary WHERE id=:id"
            ),
            {
                "digest": "f" * 64,
                "summary": '{"body":"private result body must not leak"}',
                "id": certification["id"],
            },
        )
        connection.execute(
            text(
                "CREATE TRIGGER trg_dataset_release_quality_certifications_no_update "
                "BEFORE UPDATE ON dataset_release_quality_certifications "
                "BEGIN SELECT RAISE(ABORT, 'dataset_release_quality_certifications are immutable'); END"
            )
        )
    gate = get_release_quality_gate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
    ).body
    assert gate["state"] != "passed"
    assert "private result body" not in repr(gate)

    with Session(engine) as session:
        session.add(
            DatasetReleaseEntry(
                id="release-entry-appended",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release_id,
                ordinal=999,
                resource_type="document_version",
                resource_id="document-appended",
                resource_revision=1,
                content_digest="1" * 64,
                safe_facts_json={"content_revision": 1},
            )
        )
        session.commit()
    gate = get_release_quality_gate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id="channel-production",
    ).body
    assert gate["state"] != "passed"
    engine.dispose()


def test_certification_rejects_appended_release_entry(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)
    seed_experiment(engine)
    policy = create_policy(engine).body["policy"]
    baseline = create_baseline(engine).body["baseline"]
    with Session(engine) as session:
        session.add(
            DatasetReleaseEntry(
                id="release-entry-appended-before-certification",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release_id,
                ordinal=999,
                resource_type="document_version",
                resource_id="document-appended",
                resource_revision=1,
                content_digest="1" * 64,
                safe_facts_json={"content_revision": 1},
            )
        )
        session.commit()
    with pytest.raises(ReleaseQualityConflict, match="Manifest authority is stale"):
        certify_release(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            release_id=release_id,
            channel_id="channel-production",
            baseline_id=baseline["id"],
            policy_id=policy["id"],
            expected_policy_revision=1,
            expected_channel_revision=1,
            reason="reject appended entry",
            request_id="request-quality-appended-entry",
            request_ip="127.0.0.1",
            idempotency_key="quality-appended-entry-key",
        )
    engine.dispose()


def test_quality_policy_resolution_prefers_channel_then_risk_then_global(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release_id = capture(engine)

    def policy(scope_type: str, scope_value: str, channel_id: str | None, key: str):
        return create_quality_gate_policy(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            name=f"{scope_type} quality",
            scope_type=scope_type,
            scope_value=scope_value,
            channel_id=channel_id,
            min_experiment_count=1,
            min_judged_result_count=2,
            min_judgment_coverage_bps=10_000,
            min_exact_agreement_bps=10_000,
            min_mean_score_milli=2_000,
            max_conflicting_results=0,
            require_all_experiments_completed=True,
            require_no_degraded_results=True,
            max_certification_age_minutes=1440,
            reason="policy resolution",
            request_id=f"request-{key}",
            request_ip="127.0.0.1",
            idempotency_key=key,
        ).body["policy"]

    global_policy = policy("global", "*", None, "quality-global-key")
    risk_policy = policy("risk_tier", "high", None, "quality-risk-key")
    channel_policy = policy(
        "channel", "channel-production", "channel-production", "quality-channel-key"
    )

    def resolved_policy_id() -> str:
        gate = get_release_quality_gate(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            release_id=release_id,
            channel_id="channel-production",
        ).body
        assert gate["state"] == "blocked"
        return gate["policy"]["id"]

    assert resolved_policy_id() == channel_policy["id"]
    update_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        policy_id=channel_policy["id"],
        expected_revision=1,
        status="disabled",
        reason="fallback to risk",
        request_id="request-disable-channel-policy",
        request_ip="127.0.0.1",
        idempotency_key="disable-channel-policy-key",
    )
    assert resolved_policy_id() == risk_policy["id"]
    update_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        policy_id=risk_policy["id"],
        expected_revision=1,
        status="disabled",
        reason="fallback to global",
        request_id="request-disable-risk-policy",
        request_ip="127.0.0.1",
        idempotency_key="disable-risk-policy-key",
    )
    assert resolved_policy_id() == global_policy["id"]
    engine.dispose()
