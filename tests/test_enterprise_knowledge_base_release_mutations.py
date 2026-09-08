from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_release_quality import (
    certify_release,
    create_quality_baseline,
    create_quality_gate_policy,
)
from core.enterprise_knowledge_base_releases import (
    ReleaseManifestConflict,
    ReleaseManifestNotFound,
    ReleaseManifestInvalid,
    capture_release_candidate,
    create_release_channel,
    promote_release,
    rollback_channel_release,
    update_app_release_binding,
    update_release_channel,
)
from models.orm import (
    Account,
    App,
    AppDatasetReference,
    Dataset,
    DatasetChannelRelease,
    DatasetReleaseEvent,
    DatasetReleaseQualityEvent,
    Document,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantReleaseChannel,
    TenantReleaseQualityGatePolicy,
)
from test_enterprise_release_quality_evidence import seed_experiment
from test_enterprise_knowledge_base_release_snapshot import _release_engine


def _capture(
    engine,
    *,
    key: str,
    reason: str,
    mutation_generation: int = 9,
    serving_generation: int = 3,
):
    return capture_release_candidate(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_mutation_generation=mutation_generation,
        expected_serving_generation=serving_generation,
        reason=reason,
        request_id=f"request-{key}",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def _second_release(engine):
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.mutation_generation = 10
        session.commit()
    return _capture(
        engine,
        key="capture-second",
        reason="second candidate",
        mutation_generation=10,
    )


def _seed_app_reference(engine, *, release_channel_id: str = "channel-production") -> None:
    now = datetime(2026, 8, 28, 13, 0, 0)
    with Session(engine) as session:
        session.add(App(id="app-a", tenant_id="tenant-a", name="Support App", kind="agent"))
        session.flush()
        session.add(
            AppDatasetReference(
                id="reference-a",
                tenant_id="tenant-a",
                app_id="app-a",
                dataset_id="dataset-a",
                reference_kind="knowledge",
                status="active",
                active_slot="active",
                revision=1,
                created_at=now,
                created_by="owner-a",
                updated_at=now,
                updated_by="owner-a",
                request_id="seed-reference",
                release_mode="follow_channel",
                release_channel_id=release_channel_id,
                pinned_release_id=None,
            )
        )
        session.commit()


def _promote(engine, release_id: str, *, channel_id: str, channel_revision: int, key: str):
    return promote_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id=channel_id,
        expected_channel_revision=channel_revision,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_serving_generation=3,
        reason="promote candidate",
        request_id=f"request-{key}",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def _certify_channel(engine, release_id: str, channel_id: str, *, prefix: str) -> None:
    seed_experiment(engine)
    policy = create_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        name=f"{prefix} quality",
        scope_type="channel",
        scope_value=channel_id,
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
        reason="protect release channel",
        request_id=f"request-{prefix}-quality-policy",
        request_ip="127.0.0.1",
        idempotency_key=f"{prefix}-quality-policy-key",
    ).body["policy"]
    baseline = create_quality_baseline(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        name=f"{prefix} baseline",
        experiment_ids=["experiment-quality-a"],
        reason="freeze promotion evidence",
        request_id=f"request-{prefix}-baseline",
        request_ip="127.0.0.1",
        idempotency_key=f"{prefix}-baseline-key",
    ).body["baseline"]
    certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release_id,
        channel_id=channel_id,
        baseline_id=baseline["id"],
        policy_id=policy["id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="certify promotion candidate",
        request_id=f"request-{prefix}-certification",
        request_ip="127.0.0.1",
        idempotency_key=f"{prefix}-certification-key",
    )


def test_channel_create_normalizes_code_preserves_default_audits_and_replays(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    first = create_release_channel(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        code="  UAT-CN  ",
        name="中国区 UAT",
        risk_tier="medium",
        promotion_order=25,
        is_default_serving=False,
        reason="create regional validation lane",
        request_id="request-channel-create",
        request_ip="127.0.0.1",
        idempotency_key="channel-create-key",
    )
    replay = create_release_channel(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        code="  UAT-CN  ",
        name="中国区 UAT",
        risk_tier="medium",
        promotion_order=25,
        is_default_serving=False,
        reason="create regional validation lane",
        request_id="request-channel-create",
        request_ip="127.0.0.1",
        idempotency_key="channel-create-key",
    )

    assert first.status == 201
    assert replay.body == first.body
    channel = first.body["channel"]
    assert channel["code"] == "UAT-CN"
    assert channel["normalized_code"] == "uat-cn"
    assert channel["is_default_serving"] is False
    with Session(engine) as session:
        defaults = list(
            session.scalars(
                select(TenantReleaseChannel).where(
                    TenantReleaseChannel.tenant_id == "tenant-a",
                    TenantReleaseChannel.is_default_serving.is_(True),
                )
            )
        )
        assert [row.id for row in defaults] == ["channel-production"]
        old_default = session.get(TenantReleaseChannel, "channel-production")
        assert old_default is not None and old_default.is_default_serving is True
        assert session.scalar(select(func.count()).select_from(TenantAuditEvent)) == 1
    with pytest.raises(ReleaseManifestConflict, match="default serving"):
        create_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            code="regulated-production",
            name="Regulated Production",
            risk_tier="high",
            promotion_order=40,
            is_default_serving=True,
            reason="attempt unsafe default switch",
            request_id="request-default-switch",
            request_ip="127.0.0.1",
            idempotency_key="default-switch-key",
        )
    with pytest.raises(ReleaseManifestConflict, match="normalized code"):
        create_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            code="uat-cn",
            name="Duplicate",
            risk_tier="low",
            promotion_order=26,
            is_default_serving=False,
            reason="duplicate",
            request_id="request-duplicate",
            request_ip="127.0.0.1",
            idempotency_key="duplicate-channel-key",
        )
    engine.dispose()


def test_channel_update_is_revision_fenced_and_cannot_archive_bound_channel(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    captured = _capture(engine, key="capture-bound", reason="bound candidate")
    release_id = captured.body["release"]["id"]
    _promote(
        engine,
        release_id,
        channel_id="channel-development",
        channel_revision=1,
        key="promote-bound",
    )

    with pytest.raises(ReleaseManifestConflict, match="bound"):
        update_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            channel_id="channel-development",
            expected_revision=2,
            name=None,
            risk_tier=None,
            promotion_order=None,
            is_default_serving=None,
            status="archived",
            reason="archive bound channel",
            request_id="request-channel-archive",
            request_ip="127.0.0.1",
            idempotency_key="channel-archive-key",
        )
    with pytest.raises(ReleaseManifestConflict, match="revision"):
        update_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            channel_id="channel-testing",
            expected_revision=99,
            name="QA",
            risk_tier=None,
            promotion_order=None,
            is_default_serving=None,
            status=None,
            reason="stale update",
            request_id="request-channel-stale",
            request_ip="127.0.0.1",
            idempotency_key="channel-stale-key",
        )
    with pytest.raises(ReleaseManifestNotFound):
        update_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            channel_id="channel-other-tenant",
            expected_revision=1,
            name="Foreign",
            risk_tier=None,
            promotion_order=None,
            is_default_serving=None,
            status=None,
            reason="tenant isolation",
            request_id="request-channel-isolation",
            request_ip="127.0.0.1",
            idempotency_key="channel-isolation-key",
        )
    engine.dispose()


def test_low_risk_promotion_preserves_previous_release_and_rolls_back(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    first_release = _capture(engine, key="capture-first", reason="first candidate").body["release"]
    first = _promote(
        engine,
        first_release["id"],
        channel_id="channel-development",
        channel_revision=1,
        key="promote-first",
    )
    replay = _promote(
        engine,
        first_release["id"],
        channel_id="channel-development",
        channel_revision=1,
        key="promote-first",
    )
    assert first.body == replay.body
    assert first.body["state"] == "applied"
    assert first.body["operation"] == "promote"
    assert first.body["revision"] == 2

    second_release = _second_release(engine).body["release"]
    second = _promote(
        engine,
        second_release["id"],
        channel_id="channel-development",
        channel_revision=2,
        key="promote-second",
    )
    assert second.body["binding"]["active_release_id"] == second_release["id"]
    assert second.body["binding"]["previous_release_id"] == first_release["id"]
    assert second.body["revision"] == 3

    rolled_back = rollback_channel_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        channel_id="channel-development",
        target_release_id=first_release["id"],
        expected_channel_revision=3,
        expected_serving_generation=3,
        reason="restore previous release",
        request_id="request-rollback",
        request_ip="127.0.0.1",
        idempotency_key="rollback-key",
    )
    assert rolled_back.body["state"] == "applied"
    assert rolled_back.body["operation"] == "rollback"
    assert rolled_back.body["binding"]["active_release_id"] == first_release["id"]
    assert rolled_back.body["binding"]["previous_release_id"] == second_release["id"]
    assert rolled_back.body["revision"] == 4
    assert rolled_back.body["quality_gate"] == {
        "state": "not_required",
        "reason": "no_policy",
        "policy_id": None,
        "policy_revision": None,
        "certification_id": None,
        "waiver_id": None,
    }

    with Session(engine) as session:
        channel = session.get(TenantReleaseChannel, "channel-development")
        binding = session.scalar(
            select(DatasetChannelRelease).where(
                DatasetChannelRelease.tenant_id == "tenant-a",
                DatasetChannelRelease.dataset_id == "dataset-a",
                DatasetChannelRelease.channel_id == "channel-development",
            )
        )
        dataset = session.get(Dataset, "dataset-a")
        assert channel is not None and channel.revision == 4
        assert binding is not None and binding.revision == 3
        assert dataset is not None
        assert dataset.serving_generation == 3
        assert dataset.serving_release_id is None
        quality_events = list(
            session.scalars(
                select(DatasetReleaseQualityEvent)
                .where(
                    DatasetReleaseQualityEvent.tenant_id == "tenant-a",
                    DatasetReleaseQualityEvent.dataset_id == "dataset-a",
                    DatasetReleaseQualityEvent.channel_id == "channel-development",
                )
                .order_by(DatasetReleaseQualityEvent.event_sequence)
            )
        )
        assert [event.event_type for event in quality_events] == [
            "release_promoted_with_quality_gate",
            "release_promoted_with_quality_gate",
            "release_rolled_back_with_quality_gate",
        ]
        assert quality_events[-1].state == "passed"
        assert quality_events[-1].safe_snapshot_json["state"] == "not_required"
        assert [
            event.event_type
            for event in session.scalars(
                select(DatasetReleaseEvent)
                .where(DatasetReleaseEvent.channel_id == "channel-development")
                .order_by(DatasetReleaseEvent.occurred_at, DatasetReleaseEvent.id)
            )
        ].count("promoted") == 2
    engine.dispose()


def test_default_serving_promotion_fails_closed_into_approval_outcome(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(engine, key="capture-default", reason="default candidate").body["release"]
    _certify_channel(engine, release["id"], "channel-production", prefix="default-approval")

    result = _promote(
        engine,
        release["id"],
        channel_id="channel-production",
        channel_revision=1,
        key="promote-default",
    )
    assert result.status == 202
    assert result.body["state"] == "approval_required"
    assert result.body["operation"] == "promote"
    assert result.body["resource_id"] == release["id"]
    assert result.body["approval_snapshot"]["manifest_digest"] == release["manifest_digest"]
    assert "ticket" not in repr(result.body).casefold()
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        assert dataset.serving_release_id is None
        assert dataset.serving_generation == 3
        assert dataset.release_revision == 1
    engine.dispose()


def test_application_binding_enforces_exact_xor_revisions_and_retired_release_blocker(
    tmp_path: Path,
) -> None:
    engine, now = _release_engine(tmp_path)
    release = _capture(engine, key="capture-pin", reason="pin candidate").body["release"]
    _certify_channel(engine, release["id"], "channel-production", prefix="pin")
    _seed_app_reference(engine)

    pinned = update_app_release_binding(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        app_id="app-a",
        dataset_id="dataset-a",
        expected_reference_revision=1,
        release_mode="pinned",
        release_channel_id=None,
        pinned_release_id=release["id"],
        reason="pin validated release",
        request_id="request-pin",
        request_ip="127.0.0.1",
        idempotency_key="pin-key",
    )
    assert pinned.body["reference"]["release_mode"] == "pinned"
    assert pinned.body["reference"]["pinned_release_id"] == release["id"]
    assert pinned.body["reference"]["revision"] == 2
    assert pinned.body["quality_gate"]["state"] == "passed"
    assert pinned.body["quality_gate"]["policy_id"]
    assert pinned.body["quality_gate"]["certification_id"]
    with Session(engine) as session:
        audit = session.scalar(
            select(TenantAuditEvent)
            .where(
                TenantAuditEvent.tenant_id == "tenant-a",
                TenantAuditEvent.resource_type == "application_release_binding",
                TenantAuditEvent.resource_id == "reference-a",
                TenantAuditEvent.action == "knowledge_base.application_release_binding.updated",
            )
            .order_by(TenantAuditEvent.occurred_at.desc(), TenantAuditEvent.id.desc())
        )
        assert audit is not None
        assert audit.after_snapshot["quality_gate"]["state"] == "passed"

    followed = update_app_release_binding(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        app_id="app-a",
        dataset_id="dataset-a",
        expected_reference_revision=2,
        release_mode="follow_channel",
        release_channel_id="channel-testing",
        pinned_release_id=None,
        reason="follow testing",
        request_id="request-follow",
        request_ip="127.0.0.1",
        idempotency_key="follow-key",
    )
    assert followed.body["reference"]["release_channel_id"] == "channel-testing"
    assert followed.body["reference"]["pinned_release_id"] is None

    with pytest.raises(ReleaseManifestInvalid, match="exactly one"):
        update_app_release_binding(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            app_id="app-a",
            dataset_id="dataset-a",
            expected_reference_revision=3,
            release_mode="follow_channel",
            release_channel_id="channel-testing",
            pinned_release_id=release["id"],
            reason="mixed binding",
            request_id="request-mixed",
            request_ip="127.0.0.1",
            idempotency_key="mixed-key",
        )

    with Session(engine) as session:
        session.add(
            DatasetReleaseEvent(
                id="release-event-retired",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_id=release["id"],
                channel_id=None,
                event_type="retired",
                actor_id="owner-a",
                reason="retired by retention authority",
                request_id="request-retired",
                occurred_at=now,
            )
        )
        session.commit()
    with pytest.raises(ReleaseManifestConflict, match="retired"):
        update_app_release_binding(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            app_id="app-a",
            dataset_id="dataset-a",
            expected_reference_revision=3,
            release_mode="pinned",
            release_channel_id=None,
            pinned_release_id=release["id"],
            reason="pin retired release",
            request_id="request-pin-retired",
            request_ip="127.0.0.1",
            idempotency_key="pin-retired-key",
        )
    engine.dispose()


def test_pinned_binding_rejects_release_without_default_serving_quality_gate(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(
        engine, key="capture-pin-quality-missing", reason="pin quality missing"
    ).body["release"]
    _seed_app_reference(engine)

    with pytest.raises(ReleaseManifestConflict, match="quality gate.*quality_policy_required"):
        update_app_release_binding(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            app_id="app-a",
            dataset_id="dataset-a",
            expected_reference_revision=1,
            release_mode="pinned",
            release_channel_id=None,
            pinned_release_id=release["id"],
            reason="reject unqualified pin",
            request_id="request-pin-quality-missing",
            request_ip="127.0.0.1",
            idempotency_key="pin-quality-missing-key",
        )

    with Session(engine) as session:
        reference = session.get(AppDatasetReference, "reference-a")
        assert reference is not None
        assert reference.revision == 1
        assert reference.release_mode == "follow_channel"
        assert reference.release_channel_id == "channel-production"
        assert reference.pinned_release_id is None
    engine.dispose()


def test_high_risk_rollback_fails_closed_before_approval_without_quality_gate(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    first_release = _capture(engine, key="capture-rollback-first", reason="rollback first").body[
        "release"
    ]
    _promote(
        engine,
        first_release["id"],
        channel_id="channel-development",
        channel_revision=1,
        key="promote-rollback-first",
    )
    second_release = _second_release(engine).body["release"]
    _promote(
        engine,
        second_release["id"],
        channel_id="channel-development",
        channel_revision=2,
        key="promote-rollback-second",
    )
    update_release_channel(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        channel_id="channel-development",
        expected_revision=3,
        name=None,
        risk_tier="high",
        promotion_order=None,
        is_default_serving=None,
        status=None,
        reason="protect rollback",
        request_id="request-protect-rollback",
        request_ip="127.0.0.1",
        idempotency_key="protect-rollback-key",
    )
    with pytest.raises(ReleaseManifestConflict, match="quality gate.*quality_policy_required"):
        rollback_channel_release(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            channel_id="channel-development",
            target_release_id=first_release["id"],
            expected_channel_revision=4,
            expected_serving_generation=3,
            reason="request protected rollback",
            request_id="request-protected-rollback",
            request_ip="127.0.0.1",
            idempotency_key="protected-rollback-key",
        )
    with Session(engine) as session:
        binding = session.scalar(
            select(DatasetChannelRelease).where(
                DatasetChannelRelease.channel_id == "channel-development"
            )
        )
        assert binding is not None
        assert binding.active_release_id == second_release["id"]
        assert (
            session.scalar(
                select(DatasetReleaseQualityEvent.id).where(
                    DatasetReleaseQualityEvent.tenant_id == "tenant-a",
                    DatasetReleaseQualityEvent.dataset_id == "dataset-a",
                    DatasetReleaseQualityEvent.release_id == first_release["id"],
                    DatasetReleaseQualityEvent.channel_id == "channel-development",
                    DatasetReleaseQualityEvent.event_type
                    == "release_rolled_back_with_quality_gate",
                )
            )
            is None
        )
    engine.dispose()


def test_first_release_mutation_provisions_three_deterministic_default_channels_for_new_tenant(
    tmp_path: Path,
) -> None:
    from core.enterprise_release_channels import default_release_channel_id

    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-new", name="New Tenant", plan="enterprise", status="active"))
        session.add(Account(id="owner-new", name="New Owner", email="owner-new@release.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="owner-new",
                tenant_id="tenant-new",
                role="owner",
                status="active",
            )
        )
        session.commit()
    custom = create_release_channel(
        engine,
        tenant_id="tenant-new",
        actor_id="owner-new",
        code="uat",
        name="UAT",
        risk_tier="medium",
        promotion_order=25,
        is_default_serving=False,
        reason="first release mutation",
        request_id="request-new-tenant-channel",
        request_ip="127.0.0.1",
        idempotency_key="new-tenant-channel-key",
    )
    assert custom.status == 201
    with Session(engine) as session:
        channels = list(
            session.scalars(
                select(TenantReleaseChannel)
                .where(TenantReleaseChannel.tenant_id == "tenant-new")
                .order_by(TenantReleaseChannel.promotion_order, TenantReleaseChannel.code)
            )
        )
        assert {row.code for row in channels} == {"development", "testing", "production", "uat"}
        production = next(row for row in channels if row.code == "production")
        assert production.id == default_release_channel_id("tenant-new", "production")
        assert production.is_default_serving is True
        assert sum(1 for row in channels if row.is_default_serving) == 1
    engine.dispose()


def test_application_binding_rejects_blocked_release_pin(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "document-a")
        assert document is not None
        document.indexed_revision = 0
        session.commit()
    release = _capture(engine, key="capture-blocked-pin", reason="blocked pin candidate").body[
        "release"
    ]
    assert release["readiness_state"] == "blocked"
    _seed_app_reference(engine)
    with pytest.raises(ReleaseManifestConflict, match="blocked or unavailable"):
        update_app_release_binding(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            app_id="app-a",
            dataset_id="dataset-a",
            expected_reference_revision=1,
            release_mode="pinned",
            release_channel_id=None,
            pinned_release_id=release["id"],
            reason="reject blocked pin",
            request_id="request-blocked-pin",
            request_ip="127.0.0.1",
            idempotency_key="blocked-pin-key",
        )
    engine.dispose()


def test_promotion_rechecks_current_readiness_and_manifest_entries(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(engine, key="capture-drift-check", reason="drift check").body["release"]
    with Session(engine) as session:
        document = session.get(Document, "document-a")
        assert document is not None
        document.indexed_revision = 0
        session.commit()
    with pytest.raises(ReleaseManifestConflict, match="current release authority is not ready"):
        _promote(
            engine,
            release["id"],
            channel_id="channel-development",
            channel_revision=1,
            key="promote-after-index-drift",
        )
    engine.dispose()


def test_promotion_rejects_manifest_after_dataset_mutation_generation_changes(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(engine, key="capture-generation-check", reason="generation check").body[
        "release"
    ]
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.mutation_generation = 10
        session.commit()
    with pytest.raises(ReleaseManifestConflict, match="does not match configured authority"):
        _promote(
            engine,
            release["id"],
            channel_id="channel-development",
            channel_revision=1,
            key="promote-after-generation-drift",
        )
    engine.dispose()


def test_release_channel_name_rejects_credential_like_text(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    with pytest.raises(Exception, match="credential-like"):
        create_release_channel(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            code="secret-channel",
            name="token=topsecret",
            risk_tier="low",
            promotion_order=90,
            is_default_serving=False,
            reason="safe reason",
            request_id="request-secret-channel",
            request_ip="127.0.0.1",
            idempotency_key="secret-channel-key",
        )
    engine.dispose()


def test_high_risk_promotion_fails_closed_before_publish_approval_without_quality_policy(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(engine, key="capture-quality-missing", reason="quality missing").body[
        "release"
    ]
    with pytest.raises(ReleaseManifestConflict, match="quality gate.*quality_policy_required"):
        _promote(
            engine,
            release["id"],
            channel_id="channel-production",
            channel_revision=1,
            key="promote-quality-missing",
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetChannelRelease)) == 0
    engine.dispose()


def test_matching_quality_policy_blocks_without_certification_and_passes_with_one(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    release = _capture(engine, key="capture-quality-policy", reason="quality policy").body[
        "release"
    ]
    create_quality_gate_policy(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        name="Development quality",
        scope_type="channel",
        scope_value="channel-development",
        channel_id="channel-development",
        min_experiment_count=1,
        min_judged_result_count=2,
        min_judgment_coverage_bps=10_000,
        min_exact_agreement_bps=10_000,
        min_mean_score_milli=2_000,
        max_conflicting_results=0,
        require_all_experiments_completed=True,
        require_no_degraded_results=True,
        max_certification_age_minutes=1440,
        reason="protect development",
        request_id="request-development-quality-policy",
        request_ip="127.0.0.1",
        idempotency_key="development-quality-policy-key",
    )
    with pytest.raises(ReleaseManifestConflict, match="quality gate.*certification_required"):
        _promote(
            engine,
            release["id"],
            channel_id="channel-development",
            channel_revision=1,
            key="promote-development-without-certification",
        )
    seed_experiment(engine)
    baseline = create_quality_baseline(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        name="Development baseline",
        experiment_ids=["experiment-quality-a"],
        reason="freeze evidence",
        request_id="request-development-baseline",
        request_ip="127.0.0.1",
        idempotency_key="development-baseline-key",
    ).body["baseline"]
    with Session(engine) as session:
        policy = session.scalar(
            select(TenantReleaseQualityGatePolicy).where(
                TenantReleaseQualityGatePolicy.tenant_id == "tenant-a",
                TenantReleaseQualityGatePolicy.scope_value == "channel-development",
            )
        )
        assert policy is not None
    certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=release["id"],
        channel_id="channel-development",
        baseline_id=baseline["id"],
        policy_id=policy.id,
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="certify development",
        request_id="request-development-certification",
        request_ip="127.0.0.1",
        idempotency_key="development-certification-key",
    )
    promoted = _promote(
        engine,
        release["id"],
        channel_id="channel-development",
        channel_revision=1,
        key="promote-development-with-certification",
    )
    assert promoted.body["state"] == "applied"
    assert promoted.body["quality_gate"]["state"] == "passed"
    with Session(engine) as session:
        events = list(
            session.scalars(
                select(DatasetReleaseQualityEvent)
                .where(
                    DatasetReleaseQualityEvent.tenant_id == "tenant-a",
                    DatasetReleaseQualityEvent.dataset_id == "dataset-a",
                    DatasetReleaseQualityEvent.release_id == release["id"],
                    DatasetReleaseQualityEvent.channel_id == "channel-development",
                )
                .order_by(DatasetReleaseQualityEvent.event_sequence)
            )
        )
        assert events[-1].event_type == "release_promoted_with_quality_gate"
        assert events[-1].state == "passed"
        assert events[-1].certification_id == promoted.body["quality_gate"]["certification_id"]
    engine.dispose()
