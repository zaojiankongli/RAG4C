from __future__ import annotations

from datetime import datetime
import pytest

from core.enterprise_approval_control import ApprovalExecutionFact
from server.enterprise_approval_consumers import (
    ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
    ApprovalConsumerError,
    build_knowledge_base_release_quality_waiver_consumer,
)


def test_quality_waiver_consumer_requires_opaque_approval_execution_fact() -> None:
    consumer = build_knowledge_base_release_quality_waiver_consumer(lambda: "engine")
    with pytest.raises(ApprovalConsumerError, match="opaque internal authority"):
        consumer(
            {
                "tenant_id": "tenant-a",
                "approval_request_id": "approval-request-a",
                "execution_id": "approval-execution-a",
                "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
                "resource_type": "knowledge_base",
                "resource_id": "dataset-a",
                "consumer_actor_id": "owner-a",
                "consumer_actor_role": "owner",
                "reason": "bounded quality risk acceptance",
                "request_snapshot": {},
                "approval_execution_fact": {
                    "action_type": ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER
                },
            }
        )


def test_quality_waiver_execution_fact_rejects_missing_quality_binding() -> None:
    with pytest.raises(ValueError, match="required for Quality Waiver fact"):
        ApprovalExecutionFact(
            tenant_id="tenant-a",
            approval_request_id="approval-request-a",
            execution_id="approval-execution-a",
            request_revision=1,
            execution_revision=2,
            action_type=ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER,
            resource_type="knowledge_base",
            resource_id="dataset-a",
            snapshot_hash="a" * 64,
            reason="bounded quality risk acceptance",
            release_id="release-a",
            release_number=1,
            manifest_digest="b" * 64,
            mutation_generation=1,
            channel_id="channel-production",
            channel_revision=1,
            profile_revision=1,
            ownership_revision=1,
            workspace_id="workspace-a",
            workspace_revision=1,
            serving_generation=1,
            policy_revision=1,
            policy_id="policy-a",
            policy_digest="c" * 64,
            quality_gate_digest="d" * 64,
            waiver_expires_at=datetime(2026, 8, 28, 15, 0, 0).isoformat() + "Z",
        )
