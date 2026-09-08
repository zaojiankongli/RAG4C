from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from core.enterprise_approval_control import (
    consume_approval_ticket,
    create_approval_policy,
    create_approval_request,
    decide_approval_request,
)
from core.enterprise_knowledge_base_releases import capture_release_candidate, promote_release
from core.enterprise_release_quality import (
    certify_release,
    create_quality_baseline,
    create_quality_gate_policy,
)
from models.orm import Account, Dataset, TenantMember, TenantReleaseChannel
from server.enterprise_approval_consumers import (
    ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
    ApprovalConsumerError,
    build_enterprise_approval_execution_adapters,
    build_knowledge_base_release_publish_consumer,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine
from test_enterprise_release_quality_evidence import seed_experiment

NOW = datetime(2026, 8, 28, 14, 0, 0)
TENANT = "tenant-a"
DATASET = "dataset-a"
CHANNEL = "channel-production"


def _seed_admin(engine) -> None:
    with Session(engine) as session:
        session.add(Account(id="admin-a", name="Admin A", email="admin-a@release.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="admin-a",
                tenant_id=TENANT,
                role="admin",
                status="active",
            )
        )
        session.commit()


def _capture(engine) -> dict[str, Any]:
    release = capture_release_candidate(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_mutation_generation=9,
        expected_serving_generation=3,
        reason="capture approval candidate",
        request_id="request-capture-approval",
        request_ip="127.0.0.1",
        idempotency_key="capture-approval-key",
    ).body["release"]
    seed_experiment(engine)
    quality_policy = create_quality_gate_policy(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        name="Production quality gate",
        scope_type="channel",
        scope_value=CHANNEL,
        channel_id=CHANNEL,
        min_experiment_count=1,
        min_judged_result_count=2,
        min_judgment_coverage_bps=10_000,
        min_exact_agreement_bps=10_000,
        min_mean_score_milli=2_000,
        max_conflicting_results=0,
        require_all_experiments_completed=True,
        require_no_degraded_results=True,
        max_certification_age_minutes=1440,
        reason="protect production quality",
        request_id="request-release-quality-policy",
        request_ip="127.0.0.1",
        idempotency_key="release-quality-policy-key",
    ).body["policy"]
    baseline = create_quality_baseline(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        name="Production approval baseline",
        experiment_ids=["experiment-quality-a"],
        reason="freeze approval evidence",
        request_id="request-release-quality-baseline",
        request_ip="127.0.0.1",
        idempotency_key="release-quality-baseline-key",
    ).body["baseline"]
    certify_release(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        release_id=release["id"],
        channel_id=CHANNEL,
        baseline_id=baseline["id"],
        policy_id=quality_policy["id"],
        expected_policy_revision=quality_policy["revision"],
        expected_channel_revision=1,
        reason="certify approval candidate",
        request_id="request-release-quality-certification",
        request_ip="127.0.0.1",
        idempotency_key="release-quality-certification-key",
    )
    return release


def _policy(engine) -> dict[str, Any]:
    return create_approval_policy(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        name="Production release publication",
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_scope=f"knowledge_base:{DATASET}",
        required_approvals=1,
        request_expiry_minutes=60,
        approvers=[{"kind": "account", "ref": "admin-a"}],
        reason="protect default serving publication",
        idempotency_key="release-policy-key",
        request_id="request-release-policy",
        request_ip="127.0.0.1",
        now=NOW,
    ).body["policy"]


def _approval_gate(engine, release: dict[str, Any]) -> dict[str, Any]:
    result = promote_release(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        release_id=release["id"],
        channel_id=CHANNEL,
        expected_channel_revision=1,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_serving_generation=3,
        reason="publish production release",
        request_id="request-release-gate",
        request_ip="127.0.0.1",
        idempotency_key="release-gate-key",
    )
    assert result.status == 202
    assert result.body["state"] == "approval_required"
    return result.body


def _approved_request(engine, policy: dict[str, Any], gate: dict[str, Any]):
    created = create_approval_request(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        policy_id=policy["id"],
        resource_type="knowledge_base",
        resource_id=DATASET,
        snapshot=gate["approval_snapshot"],
        reason="publish production release",
        idempotency_key="release-request-key",
        request_id="request-release-approval",
        request_ip="127.0.0.1",
        now=NOW,
    ).body["request"]
    decided = decide_approval_request(
        engine,
        tenant_id=TENANT,
        actor_id="admin-a",
        request_id=created["id"],
        decision="approved",
        expected_revision=created["revision"],
        comment="approved for production",
        idempotency_key="release-decision-key",
        request_id_header="request-release-decision",
        request_ip="127.0.0.1",
        now=NOW,
    ).body
    return decided["request"], decided["execution"]["ticket"]


def test_release_publish_consumer_executes_once_and_projects_default_serving(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    _seed_admin(engine)
    release = _capture(engine)
    policy = _policy(engine)
    gate = _approval_gate(engine, release)
    approved, ticket = _approved_request(engine, policy, gate)
    adapters = build_enterprise_approval_execution_adapters(lambda: engine)
    assert ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH in adapters

    first = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-consume-key",
        request_id_header="request-release-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH],
    )
    replay = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-consume-key",
        request_id_header="request-release-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH],
    )

    assert first.status == 200
    assert replay.body == first.body
    assert first.body["request"]["status"] == "executed"
    result = first.body["execution"]["result"]
    assert result["operation"] == "promote"
    assert result["resource_id"] == release["id"]
    assert "ticket" not in repr(result).casefold()
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        channel = session.get(TenantReleaseChannel, CHANNEL)
        assert dataset is not None and channel is not None
        assert dataset.serving_release_id == release["id"]
        assert dataset.serving_generation == 4
        assert dataset.release_revision == 2
        assert channel.revision == 2
    engine.dispose()


def test_release_consumer_rejects_forged_mapping_before_service_call() -> None:
    calls: list[dict[str, Any]] = []

    def fake_service(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "state": "applied",
            "operation": "promote",
            "resource_id": "release-a",
            "revision": 2,
        }

    consumer = build_knowledge_base_release_publish_consumer(lambda: "engine", service=fake_service)
    with pytest.raises(ApprovalConsumerError, match="opaque internal authority"):
        consumer(
            {
                "tenant_id": TENANT,
                "approval_request_id": "approval-request-forged",
                "execution_id": "approval-execution-forged",
                "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
                "resource_type": "knowledge_base",
                "resource_id": DATASET,
                "consumer_actor_id": "owner-a",
                "consumer_actor_role": "owner",
                "requester_id": "member-a",
                "reason": "publish production release",
                "request_snapshot": {"dataset_id": DATASET},
                "approval_execution_fact": {
                    "release_id": "release-forged",
                    "manifest_digest": "a" * 64,
                },
            }
        )
    assert calls == []


def test_stale_channel_revision_fails_once_and_replays_execution_failure(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    _seed_admin(engine)
    release = _capture(engine)
    policy = _policy(engine)
    gate = _approval_gate(engine, release)
    approved, ticket = _approved_request(engine, policy, gate)
    with Session(engine) as session:
        channel = session.get(TenantReleaseChannel, CHANNEL)
        assert channel is not None
        channel.revision = 2
        session.commit()
    adapters = build_enterprise_approval_execution_adapters(lambda: engine)

    failed = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-stale-consume-key",
        request_id_header="request-release-stale-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH],
    )
    replay = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-stale-consume-key",
        request_id_header="request-release-stale-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH],
    )

    assert failed.status == 502
    assert replay.body == failed.body
    assert failed.body["request"]["status"] == "execution_failed"
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        assert dataset is not None
        assert dataset.serving_release_id is None
        assert dataset.serving_generation == 3
    engine.dispose()


def test_approval_execution_rejects_dataset_mutation_generation_drift(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    _seed_admin(engine)
    release = _capture(engine)
    policy = _policy(engine)
    gate = _approval_gate(engine, release)
    approved, ticket = _approved_request(engine, policy, gate)
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        assert dataset is not None
        dataset.mutation_generation = 10
        session.commit()
    adapters = build_enterprise_approval_execution_adapters(lambda: engine)
    failed = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-generation-drift-consume",
        request_id_header="request-release-generation-drift",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH],
    )
    assert failed.status == 502
    assert failed.body["request"]["status"] == "execution_failed"
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        assert dataset is not None
        assert dataset.serving_release_id is None
        assert dataset.serving_generation == 3
    engine.dispose()


def test_approval_recovers_completed_release_receipt_when_adapter_raises_after_commit(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    _seed_admin(engine)
    release = _capture(engine)
    policy = _policy(engine)
    gate = _approval_gate(engine, release)
    approved, ticket = _approved_request(engine, policy, gate)
    real_consumer = build_enterprise_approval_execution_adapters(lambda: engine)[
        ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH
    ]

    def commit_then_raise(payload: dict[str, Any]) -> dict[str, Any]:
        real_consumer(payload)
        raise RuntimeError("response lost after downstream commit")

    recovered = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="release-receipt-recovery-key",
        request_id_header="request-release-receipt-recovery",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=commit_then_raise,
    )
    assert recovered.status == 200
    assert recovered.body["request"]["status"] == "executed"
    assert recovered.body["execution"]["result"]["operation"] == "promote"
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        assert dataset is not None
        assert dataset.serving_release_id == release["id"]
        assert dataset.serving_generation == 4
    engine.dispose()


def test_retry_reconciles_process_crash_after_release_commit_before_approval_finalize(
    tmp_path: Path,
) -> None:
    from core import enterprise_approval_control as control
    from core.enterprise_tenant_idempotency import (
        reserve_tenant_mutation,
        tenant_request_hash,
    )

    engine, _ = _release_engine(tmp_path)
    _seed_admin(engine)
    release = _capture(engine)
    policy = _policy(engine)
    gate = _approval_gate(engine, release)
    approved, ticket = _approved_request(engine, policy, gate)
    consume_key = "release-crash-reconcile-key"
    claim_revision = approved["revision"] + 1
    request_hash = tenant_request_hash(
        operation="approval.ticket.consume",
        path_identity={"tenant_id": TENANT, "request_id": approved["id"]},
        body={
            "revision": approved["revision"],
            "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
            "resource_type": "knowledge_base",
            "resource_id": DATASET,
            "ticket_digest": control._ticket_digest(TENANT, approved["id"], ticket),
        },
    )
    with Session(engine) as session:
        request_table = Table(
            "tenant_approval_requests", MetaData(), autoload_with=session.connection()
        )
        row = (
            session.execute(select(request_table).where(request_table.c.id == approved["id"]))
            .mappings()
            .one()
        )
        session.execute(
            request_table.update()
            .where(request_table.c.id == approved["id"])
            .values(
                status="executing",
                ticket_consumed_at=NOW,
                revision=claim_revision,
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        reserve_tenant_mutation(
            session,
            tenant_id=TENANT,
            actor_id="owner-a",
            raw_idempotency_key=consume_key,
            request_hash=request_hash,
            operation="approval.ticket.consume",
            resource_type="tenant_approval_request",
        )
        session.commit()

    snapshot = gate["approval_snapshot"]
    fact = control._knowledge_base_release_execution_fact(
        tenant_id=TENANT,
        approval_request_id=approved["id"],
        request_revision=approved["revision"],
        execution_revision=claim_revision,
        resource_id=DATASET,
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        snapshot=snapshot,
        snapshot_hash=str(row["payload_hash"]),
        reason="publish production release",
    )
    promote_release(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        release_id=release["id"],
        channel_id=CHANNEL,
        expected_channel_revision=1,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_serving_generation=3,
        reason="publish production release",
        request_id="request-release-crash-downstream",
        request_ip="127.0.0.1",
        idempotency_key=fact.execution_id,
        approval_execution_fact=fact,
    )

    recovered = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key=consume_key,
        request_id_header="request-release-crash-reconcile",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=build_enterprise_approval_execution_adapters(lambda: engine)[
            ACTION_KNOWLEDGE_BASE_RELEASE_PUBLISH
        ],
    )
    assert recovered.status == 200
    assert recovered.body["request"]["status"] == "executed"
    assert recovered.body["execution"]["recovered"] is True
    with Session(engine) as session:
        dataset = session.get(Dataset, DATASET)
        assert dataset is not None
        assert dataset.serving_release_id == release["id"]
        assert dataset.serving_generation == 4
    engine.dispose()
