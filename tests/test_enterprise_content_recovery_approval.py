from __future__ import annotations

import core.enterprise_approval_control as approval
import server.enterprise_approval_api as approval_api


def test_document_purge_is_an_approval_only_action_at_0033() -> None:
    assert "document_purge" in approval._ACTION_TYPES
    assert "0031_enterprise_release_quality_operations" in approval._SUPPORTED_REVISIONS
    assert "0032_enterprise_notification_center" in approval._SUPPORTED_REVISIONS
    assert "0033_enterprise_content_recovery" in approval._SUPPORTED_REVISIONS
    assert "0036_enterprise_knowledge_serving_reliability" in approval._SUPPORTED_REVISIONS
    assert "document_purge" in approval_api._DEDICATED_EXECUTION_ACTIONS


def test_document_purge_snapshot_keeps_only_safe_recovery_facts() -> None:
    snapshot = approval._redact_snapshot(
        {
            "recycle_entry_id": "recycle-entry-a",
            "document_id": "document-a",
            "dataset_id": "dataset-a",
            "entry_revision": 3,
            "purge_eligible_at": "2026-09-28T00:00:00Z",
            "retention_days_snapshot": 30,
            "legal_hold_count": 0,
            "execution_ticket": "stage23-ticket-never-store",
            "authorization": "Bearer stage23-secret-never-store",
        }
    )

    assert snapshot["recycle_entry_id"] == "recycle-entry-a"
    assert snapshot["entry_revision"] == 3
    assert snapshot["legal_hold_count"] == 0
    assert snapshot["execution_ticket"] == "[REDACTED]"
    assert snapshot["authorization"] == "[REDACTED]"
