from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.enterprise_approval_control import (
    consume_approval_ticket,
    create_approval_policy,
    decide_approval_request,
)
from core.enterprise_knowledge_base_releases import capture_release_candidate
from core.enterprise_release_quality import create_quality_gate_policy
from core.enterprise_release_quality_service import resolve_release_quality_gate
from core.enterprise_release_quality_waivers import request_quality_waiver
from models.orm import (
    Account,
    DatasetReleaseQualityEvent,
    DatasetReleaseQualityWaiver,
    TenantMember,
)
from server.enterprise_approval_consumers import (
    ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
    ApprovalConsumerError,
    build_enterprise_approval_execution_adapters,
    build_knowledge_base_release_quality_waiver_consumer,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine

NOW = datetime(2026, 8, 28, 14, 0, 0)
TENANT = "tenant-a"
DATASET = "dataset-a"
CHANNEL = "channel-production"


def _capture(engine) -> dict[str, Any]:
    return capture_release_candidate(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        expected_profile_revision=4,
        expected_ownership_revision=5,
        expected_workspace_revision=2,
        expected_mutation_generation=9,
        expected_serving_generation=3,
        reason="capture waiver candidate",
        request_id="request-waiver-capture",
        request_ip="127.0.0.1",
        idempotency_key="waiver-capture-key",
    ).body["release"]


def _quality_policy(engine) -> dict[str, Any]:
    return create_quality_gate_policy(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        name="Production quality",
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
        max_certification_age_minutes=1_440,
        reason="protect production quality",
        request_id="request-waiver-quality-policy",
        request_ip="127.0.0.1",
        idempotency_key="waiver-quality-policy-key",
    ).body["policy"]


def _approval_policy(engine) -> dict[str, Any]:
    with Session(engine) as session:
        session.add(Account(id="admin-a", name="Admin A", email="admin-a@waiver.test"))
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
    return create_approval_policy(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        name="Production quality waiver approval",
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
        resource_scope=f"knowledge_base:{DATASET}",
        required_approvals=1,
        request_expiry_minutes=60,
        approvers=[{"kind": "account", "ref": "admin-a"}],
        reason="require independent risk acceptance",
        idempotency_key="waiver-approval-policy-key",
        request_id="request-waiver-approval-policy",
        request_ip="127.0.0.1",
        now=NOW,
    ).body["policy"]


def _requested(engine, *, quality_policy: dict[str, Any], approval_policy: dict[str, Any]):
    release = _capture(engine)
    result = request_quality_waiver(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        release_id=release["id"],
        channel_id=CHANNEL,
        policy_id=quality_policy["id"],
        expected_policy_revision=quality_policy["revision"],
        expected_channel_revision=1,
        approval_policy_id=approval_policy["id"],
        requested_expires_at=NOW + timedelta(hours=1),
        reason="accept temporary certification gap",
        request_id="request-waiver-create",
        request_ip="127.0.0.1",
        idempotency_key="waiver-request-key",
        now=NOW,
    )
    return release, result


def _approve(engine, request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    decided = decide_approval_request(
        engine,
        tenant_id=TENANT,
        actor_id="admin-a",
        request_id=request["id"],
        decision="approved",
        expected_revision=request["revision"],
        comment="approved with bounded expiry",
        idempotency_key="waiver-decision-key",
        request_id_header="request-waiver-decision",
        request_ip="127.0.0.1",
        now=NOW,
    )
    return decided.body["request"], decided.body["execution"]["ticket"]


def test_request_quality_waiver_creates_sanitized_approval_snapshot_and_event(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    quality_policy = _quality_policy(engine)
    approval_policy = _approval_policy(engine)

    _release, first = _requested(
        engine,
        quality_policy=quality_policy,
        approval_policy=approval_policy,
    )
    replay = request_quality_waiver(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        dataset_id=DATASET,
        release_id=first.body["approval_snapshot"]["release_id"],
        channel_id=CHANNEL,
        policy_id=quality_policy["id"],
        expected_policy_revision=quality_policy["revision"],
        expected_channel_revision=1,
        approval_policy_id=approval_policy["id"],
        requested_expires_at=NOW + timedelta(hours=1),
        reason="accept temporary certification gap",
        request_id="request-waiver-create",
        request_ip="127.0.0.1",
        idempotency_key="waiver-request-key",
        now=NOW,
    )

    assert first.status == 202
    assert replay.body == first.body
    request = first.body["approval_request"]
    assert request["action_type"] == ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER
    assert request["status"] == "pending"
    assert request["snapshot"]["manifest_digest"]
    assert request["snapshot"]["requested_expires_at"].endswith("Z")
    assert "ticket" not in repr(first.body).casefold()
    assert "secret" not in repr(first.body).casefold()
    with Session(engine) as session:
        events = list(
            session.scalars(
                select(DatasetReleaseQualityEvent).where(
                    DatasetReleaseQualityEvent.tenant_id == TENANT,
                    DatasetReleaseQualityEvent.dataset_id == DATASET,
                    DatasetReleaseQualityEvent.release_id
                    == first.body["approval_snapshot"]["release_id"],
                    DatasetReleaseQualityEvent.channel_id == CHANNEL,
                )
            )
        )
        assert len(events) == 1
        assert events[0].event_type == "waiver_requested"
        assert events[0].approval_request_id == request["id"]
        assert session.scalar(select(func.count()).select_from(DatasetReleaseQualityWaiver)) == 0
    engine.dispose()


def test_approved_quality_waiver_consumer_writes_immutable_receipt_and_replays_once(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    quality_policy = _quality_policy(engine)
    approval_policy = _approval_policy(engine)
    release, requested = _requested(
        engine,
        quality_policy=quality_policy,
        approval_policy=approval_policy,
    )
    approved, ticket = _approve(engine, requested.body["approval_request"])
    adapters = build_enterprise_approval_execution_adapters(lambda: engine)
    assert ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER in adapters

    first = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="waiver-consume-key",
        request_id_header="request-waiver-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER],
    )
    replay = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="waiver-consume-key",
        request_id_header="request-waiver-consume-replay",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER],
    )

    assert first.body == replay.body
    assert first.body["request"]["status"] == "executed"
    result = first.body["execution"]["result"]
    assert result["operation"] == "release_quality_waiver"
    assert result["waiver"]["release_id"] == release["id"]
    assert "ticket" not in repr(result).casefold()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetReleaseQualityWaiver)) == 1
        event_types = list(
            session.scalars(
                select(DatasetReleaseQualityEvent.event_type)
                .where(
                    DatasetReleaseQualityEvent.tenant_id == TENANT,
                    DatasetReleaseQualityEvent.dataset_id == DATASET,
                    DatasetReleaseQualityEvent.release_id == release["id"],
                    DatasetReleaseQualityEvent.channel_id == CHANNEL,
                )
                .order_by(DatasetReleaseQualityEvent.event_sequence)
            )
        )
        assert event_types == ["waiver_requested", "waiver_approved"]
    with Session(engine) as session:
        gate = resolve_release_quality_gate(
            session,
            tenant_id=TENANT,
            dataset_id=DATASET,
            release_id=release["id"],
            channel_id=CHANNEL,
            now=NOW,
        )
    assert gate["state"] == "waived"
    engine.dispose()


def test_quality_waiver_fails_closed_when_policy_revision_is_stale(
    tmp_path: Path,
) -> None:
    engine, _ = _release_engine(tmp_path)
    quality_policy = _quality_policy(engine)
    approval_policy = _approval_policy(engine)
    release, requested = _requested(
        engine,
        quality_policy=quality_policy,
        approval_policy=approval_policy,
    )
    approved, ticket = _approve(engine, requested.body["approval_request"])

    from core.enterprise_release_quality import update_quality_gate_policy

    update_quality_gate_policy(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        policy_id=quality_policy["id"],
        expected_revision=1,
        min_mean_score_milli=2_500,
        reason="raise threshold before waiver execution",
        request_id="request-waiver-policy-update",
        request_ip="127.0.0.1",
        idempotency_key="waiver-policy-update-key",
    )
    adapters = build_enterprise_approval_execution_adapters(lambda: engine)
    failed = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="waiver-consume-stale-key",
        request_id_header="request-waiver-stale",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=adapters[ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER],
    )

    assert failed.status == 502
    assert failed.body["request"]["status"] == "execution_failed"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetReleaseQualityWaiver)) == 0
    assert release["id"]
    engine.dispose()


def test_quality_waiver_consumer_rejects_forged_mapping_before_service_call() -> None:
    calls: list[dict[str, Any]] = []

    def fake_service(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"state": "applied", "operation": "release_quality_waiver", "resource_id": "x"}

    consumer = build_knowledge_base_release_quality_waiver_consumer(
        lambda: "engine", service=fake_service
    )
    with pytest.raises(ApprovalConsumerError, match="opaque internal authority"):
        consumer(
            {
                "tenant_id": TENANT,
                "approval_request_id": "approval-request-forged",
                "execution_id": "approval-execution-forged",
                "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
                "resource_type": "knowledge_base",
                "resource_id": DATASET,
                "consumer_actor_id": "owner-a",
                "consumer_actor_role": "owner",
                "reason": "accept temporary certification gap",
                "request_snapshot": {},
                "approval_execution_fact": {},
            }
        )
    assert calls == []


def test_gate_rejects_tampered_waiver_envelope(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    quality_policy = _quality_policy(engine)
    approval_policy = _approval_policy(engine)
    release, requested = _requested(
        engine, quality_policy=quality_policy, approval_policy=approval_policy
    )
    approved, ticket = _approve(engine, requested.body["approval_request"])
    result = consume_approval_ticket(
        engine,
        tenant_id=TENANT,
        actor_id="owner-a",
        request_id=approved["id"],
        ticket=ticket,
        expected_revision=approved["revision"],
        action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
        resource_type="knowledge_base",
        resource_id=DATASET,
        idempotency_key="waiver-tamper-consume-key",
        request_id_header="request-waiver-tamper-consume",
        request_ip="127.0.0.1",
        now=NOW,
        execution_adapter=build_enterprise_approval_execution_adapters(lambda: engine)[
            ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER
        ],
    )
    waiver_id = result.body["execution"]["result"]["waiver"]["id"]
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_dataset_release_quality_waivers_no_update"))
        connection.execute(
            text("UPDATE dataset_release_quality_waivers SET waiver_digest=:digest WHERE id=:id"),
            {"digest": "f" * 64, "id": waiver_id},
        )
        connection.execute(
            text(
                "CREATE TRIGGER trg_dataset_release_quality_waivers_no_update "
                "BEFORE UPDATE ON dataset_release_quality_waivers "
                "BEGIN SELECT RAISE(ABORT, 'dataset_release_quality_waivers are immutable'); END"
            )
        )
    with Session(engine) as session:
        gate = resolve_release_quality_gate(
            session,
            tenant_id=TENANT,
            dataset_id=DATASET,
            release_id=release["id"],
            channel_id=CHANNEL,
            now=NOW + timedelta(minutes=1),
        )
    assert gate["state"] == "blocked"
    assert gate["waiver"] is None
    engine.dispose()
