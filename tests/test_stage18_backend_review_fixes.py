from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.catalog_schema import (
    inspect_catalog_schema,
    inspect_enterprise_knowledge_base_registry_capability,
    upgrade_catalog,
)
from core.knowledge_governance import AuditContext
from models.orm import App, AppDatasetReference
from tests.test_enterprise_knowledge_base_registry_core import _registry_engine


NOW = datetime(2026, 8, 28, 12, 0, 0)


def _transfer_snapshot(*, reason: str = "Move the Knowledge Base") -> dict[str, Any]:
    return {
        "dataset_id": "dataset-a",
        "profile_revision": 1,
        "ownership_revision": 1,
        "source_workspace_id": "workspace-a",
        "target_workspace_id": "workspace-target",
        "source_workspace_revision": 1,
        "target_workspace_revision": 1,
        "reason": reason,
    }


def _transfer_payload(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.enterprise_approval_control import _dataset_workspace_transfer_execution_fact

    snapshot = snapshot or _transfer_snapshot()
    fact = _dataset_workspace_transfer_execution_fact(
        tenant_id="tenant-a",
        approval_request_id="approval-request-review",
        request_revision=2,
        execution_revision=3,
        resource_id="dataset-a",
        snapshot=snapshot,
        snapshot_hash=_snapshot_hash(snapshot),
        reason=snapshot["reason"],
    )
    return {
        "tenant_id": "tenant-a",
        "approval_request_id": "approval-request-review",
        "execution_id": fact.execution_id,
        "execution_revision": 3,
        "action_type": "dataset_workspace_transfer",
        "resource_type": "knowledge_base",
        "resource_id": "dataset-a",
        "consumer_actor_id": "owner-a",
        "consumer_actor_role": "owner",
        "requester_id": "owner-a",
        "request_snapshot": snapshot,
        "reason": snapshot["reason"],
        "approval_execution_fact": fact,
        "request_evidence": {"request_id": "review-consume", "request_ip": "127.0.0.1"},
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _new_catalog_engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'review-capability.db').as_posix()}"
    upgrade_catalog(url)
    return create_engine(url)


def test_workspace_control_accepts_current_head_and_checks_authorization_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import enterprise_workspace_control as control

    engine = _registry_engine(tmp_path)
    try:
        result = control.list_workspaces(engine, tenant_id="tenant-a", actor_id="owner-a")
        assert result.body["items"]

        monkeypatch.setattr(
            control,
            "inspect_workspace_authorization_capability",
            lambda _connection: ("unavailable", ("workspace authorization evidence missing",)),
        )
        with pytest.raises(control.WorkspaceMigrationRequired) as caught:
            control.list_workspaces(engine, tenant_id="tenant-a", actor_id="owner-a")
        assert caught.value.missing == ("workspace authorization evidence missing",)
    finally:
        engine.dispose()


def test_transfer_consumer_unpacks_real_registry_service_result(tmp_path: Path) -> None:
    from server.enterprise_approval_consumers import build_dataset_workspace_transfer_consumer

    engine = _registry_engine(tmp_path)
    try:
        consumer = build_dataset_workspace_transfer_consumer(lambda: engine)
        result = consumer(_transfer_payload())
        assert result["ownership"]["workspace_id"] == "workspace-target"
        assert result["dataset"]["profile_revision"] == 2
    finally:
        engine.dispose()


def test_registry_transfer_uses_canonical_approval_scope_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import enterprise_knowledge_base_registry as registry

    engine = _registry_engine(tmp_path)
    calls: list[dict[str, Any]] = []

    def resolver(_session: Session, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "id": "policy-review",
            "name": "Review policy",
            "action_type": "dataset_workspace_transfer",
            "resource_scope": "knowledge_base:*",
            "required_approvals": 1,
        }

    monkeypatch.setattr(
        registry, "resolve_active_approval_policy_in_session", resolver, raising=False
    )
    try:
        with Session(engine) as session:
            policy = registry._active_transfer_policy(session, "tenant-a", "dataset-a")
        assert policy == {
            "id": "policy-review",
            "name": "Review policy",
            "action_type": "dataset_workspace_transfer",
            "resource_scope": "knowledge_base:*",
            "required_approvals": 1,
        }
        assert calls == [
            {
                "tenant_id": "tenant-a",
                "action_type": "dataset_workspace_transfer",
                "resource_type": "knowledge_base",
                "resource_id": "dataset-a",
                "lock_for_update": True,
                "tenant_already_locked": True,
            }
        ]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "missing_field", ["source_workspace_revision", "target_workspace_revision"]
)
def test_transfer_approval_fact_requires_both_workspace_revisions(missing_field: str) -> None:
    from core.enterprise_approval_control import (
        ApprovalConflict,
        _dataset_workspace_transfer_execution_fact,
    )

    snapshot = _transfer_snapshot()
    snapshot.pop(missing_field)
    with pytest.raises(ApprovalConflict, match="revision"):
        _dataset_workspace_transfer_execution_fact(
            tenant_id="tenant-a",
            approval_request_id="approval-request-review",
            request_revision=2,
            execution_revision=3,
            resource_id="dataset-a",
            snapshot=snapshot,
            snapshot_hash=_snapshot_hash(snapshot),
            reason=snapshot["reason"],
        )


def test_approval_execution_fact_rejects_incomplete_transfer_fields() -> None:
    from core.enterprise_approval_control import ApprovalExecutionFact

    with pytest.raises(ValueError, match="source_workspace_revision"):
        ApprovalExecutionFact(
            tenant_id="tenant-a",
            approval_request_id="approval-request-review",
            execution_id="approval-execution-review",
            request_revision=2,
            execution_revision=3,
            action_type="dataset_workspace_transfer",
            resource_type="knowledge_base",
            resource_id="dataset-a",
            snapshot_hash="a" * 64,
            reason="Move the Knowledge Base",
            profile_revision=1,
            dataset_profile_revision=1,
            expected_dataset_profile_revision=1,
            ownership_revision=1,
            expected_ownership_revision=1,
            source_workspace_id="workspace-a",
            target_workspace_id="workspace-target",
            source_workspace_revision=None,
            target_workspace_revision=1,
        )


def test_registry_rejects_mapping_that_is_not_an_approval_execution_fact() -> None:
    from core import enterprise_knowledge_base_registry as registry

    snapshot = _transfer_snapshot()
    fact = _transfer_payload(snapshot)["approval_execution_fact"]
    with pytest.raises(registry.KnowledgeBaseRegistryConflict, match="ApprovalExecutionFact"):
        registry._validate_approval_fact(
            dict(fact),
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            target_workspace_id="workspace-target",
            expected_dataset_profile_revision=1,
            expected_ownership_revision=1,
        )


@pytest.mark.parametrize("stale_field", ["source", "target"])
def test_transfer_rechecks_locked_source_and_target_workspace_revisions(
    tmp_path: Path, stale_field: str
) -> None:
    from core.enterprise_knowledge_base_registry import (
        KnowledgeBaseRegistryConflict,
        transfer_dataset_ownership,
    )

    engine = _registry_engine(tmp_path)
    try:
        expected_source = 2 if stale_field == "source" else 1
        expected_target = 2 if stale_field == "target" else 1
        with pytest.raises(KnowledgeBaseRegistryConflict, match="Workspace revision"):
            transfer_dataset_ownership(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                dataset_id="dataset-a",
                target_workspace_id="workspace-target",
                expected_dataset_profile_revision=1,
                expected_ownership_revision=1,
                expected_source_workspace_revision=expected_source,
                expected_target_workspace_revision=expected_target,
                reason="move to target workspace",
                request_id="review-transfer",
                request_ip="127.0.0.1",
                idempotency_key=f"review-transfer-{stale_field}",
                now=NOW,
            )
    finally:
        engine.dispose()


def test_stage18_binding_api_cannot_mutate_primary_projection(tmp_path: Path) -> None:
    from core import enterprise_workspace_control as control

    engine = _registry_engine(tmp_path)
    try:
        with pytest.raises(control.WorkspaceConflict) as caught:
            control.bind_workspace_dataset(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                workspace_id="workspace-a",
                dataset_id="dataset-a",
                binding_kind="shared",
                reason="demote primary binding",
                idempotency_key="review-primary-demotion",
                request_id="review-primary-demotion",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert caught.value.code == "workspace_dataset_primary_managed_by_registry"

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT binding_kind, active_primary_slot FROM tenant_workspace_datasets "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-a' AND dataset_id='dataset-a'"
                )
            ).one()
        assert row == ("primary", "primary")
    finally:
        engine.dispose()


def test_stage18_binding_api_cannot_remove_primary_projection(tmp_path: Path) -> None:
    from core import enterprise_workspace_control as control

    engine = _registry_engine(tmp_path)
    try:
        with pytest.raises(control.WorkspaceConflict) as caught:
            control.remove_workspace_dataset(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                workspace_id="workspace-a",
                dataset_id="dataset-a",
                expected_revision=1,
                reason="remove primary binding",
                idempotency_key="review-primary-remove",
                request_id="review-primary-remove",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert caught.value.code == "workspace_dataset_primary_managed_by_registry"
    finally:
        engine.dispose()


def test_archive_dependency_check_uses_the_same_tenant_lock_as_reference_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import enterprise_knowledge_base_registry as registry
    from core.knowledge_datasets import DatasetArchiveBlocked

    engine = _registry_engine(tmp_path)
    calls: list[str] = []
    original = registry._lock_tenant

    def spy(session: Session, tenant_id: str) -> None:
        calls.append(tenant_id)
        original(session, tenant_id)

    monkeypatch.setattr(registry, "_lock_tenant", spy)
    try:
        with Session(engine) as session:
            with pytest.raises(DatasetArchiveBlocked):
                registry.assert_dataset_archive_allowed(session, "tenant-a", "dataset-a")
        assert calls == ["tenant-a"]
    finally:
        engine.dispose()


def test_partial_0028_archive_check_fails_closed(tmp_path: Path) -> None:
    from core.enterprise_knowledge_base_registry import KnowledgeBaseRegistryUnavailable
    from core.knowledge_datasets import KnowledgeDatasetRepository

    engine = _registry_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE app_dataset_references"))
        with pytest.raises(KnowledgeBaseRegistryUnavailable):
            KnowledgeDatasetRepository(engine).archive(
                "tenant-a",
                "dataset-a",
                expected_revision=1,
                audit=AuditContext(
                    actor_id="owner-a", request_id="review-partial-archive", request_ip="127.0.0.1"
                ),
            )
    finally:
        engine.dispose()


def test_registry_reason_redacts_value_level_secret_before_audit(tmp_path: Path) -> None:
    from core.enterprise_knowledge_base_registry import create_app_reference

    engine = _registry_engine(tmp_path)
    try:
        with Session(engine) as session:
            session.add(App(id="app-secret", tenant_id="tenant-a", name="Secret test", kind="chat"))
            session.commit()
        create_app_reference(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            app_id="app-secret",
            dataset_id="dataset-a",
            reason="password: stage18-review-secret",
            request_id="review-reason",
            request_ip="127.0.0.1",
            idempotency_key="review-reason-key",
            now=NOW,
        )
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT before_snapshot, after_snapshot FROM tenant_audit_events "
                    "WHERE action='knowledge_base.app_reference.create'"
                )
            ).one()
            serialized = json.dumps(tuple(row), ensure_ascii=False)
        assert "stage18-review-secret" not in serialized
        assert "[REDACTED]" in serialized
    finally:
        engine.dispose()


def test_approval_reason_redacts_value_level_secret() -> None:
    from core.enterprise_approval_control import _safe_reason

    assert _safe_reason("password: stage18-review-secret") == "[REDACTED]"


@pytest.mark.parametrize(
    ("damage", "expected_fragment"),
    [
        ("missing", "dataset_workspace_ownerships.missing_for_dataset"),
        ("mismatch", "dataset_workspace_ownerships.ownership_primary_mismatch"),
    ],
)
def test_readiness_reports_data_level_ownership_blockers(
    tmp_path: Path, damage: str, expected_fragment: str
) -> None:
    from server import enterprise_readiness_api as readiness_api

    engine = _registry_engine(tmp_path)
    try:
        with engine.begin() as connection:
            if damage == "missing":
                connection.execute(
                    text(
                        "DELETE FROM dataset_workspace_ownerships "
                        "WHERE tenant_id='tenant-a' AND dataset_id='dataset-a'"
                    )
                )
            else:
                connection.execute(
                    text(
                        "UPDATE dataset_workspace_ownerships SET workspace_id='workspace-target' "
                        "WHERE tenant_id='tenant-a' AND dataset_id='dataset-a'"
                    )
                )
        state = inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert any(expected_fragment in issue for issue in state.schema_issues)
        capability_state, capability_issues = inspect_enterprise_knowledge_base_registry_capability(
            engine
        )
        assert capability_state == "unavailable"
        assert any(expected_fragment in issue for issue in capability_issues)
        report = readiness_api._evaluate_readiness(
            lambda: engine,
            inspect_catalog_schema,
        )
        assert report.status == "malformed"
        assert report.missing_capability_groups == ["enterprise_knowledge_base_registry"]
    finally:
        engine.dispose()


def test_registry_capability_requires_scope_idempotency_and_audit_dependencies() -> None:
    from core import catalog_schema

    assert {
        "accounts",
        "tenant_members",
        "tenant_control_mutation_requests",
        "tenant_audit_events",
    } <= catalog_schema.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_TABLES


def test_registry_capability_fails_when_idempotency_dependency_is_missing(tmp_path: Path) -> None:
    engine = _new_catalog_engine(tmp_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(text("DROP TABLE tenant_control_mutation_requests"))
            connection.commit()
        state, issues = inspect_enterprise_knowledge_base_registry_capability(engine)
        assert state == "unavailable"
        assert "missing table tenant_control_mutation_requests" in issues
    finally:
        engine.dispose()


def test_app_dataset_reference_request_id_is_128_characters_in_orm_and_migration(
    tmp_path: Path,
) -> None:
    assert AppDatasetReference.__table__.c.request_id.type.length == 128

    engine = _new_catalog_engine(tmp_path)
    try:
        columns = {
            item["name"]: item
            for item in __import__("sqlalchemy")
            .inspect(engine)
            .get_columns("app_dataset_references")
        }
        assert columns["request_id"]["type"].length == 128
    finally:
        engine.dispose()


def test_archived_dataset_cannot_gain_an_active_application_reference(tmp_path: Path) -> None:
    from core import enterprise_knowledge_base_registry as registry

    engine = _registry_engine(tmp_path)
    try:
        with Session(engine) as session:
            session.add(App(id="app-c", tenant_id="tenant-a", name="Archived blocker", kind="chat"))
            session.commit()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE datasets SET status='archived' WHERE tenant_id='tenant-a' AND id='dataset-a'"
                )
            )
        with pytest.raises(registry.KnowledgeBaseRegistryConflict, match="active"):
            registry.create_app_reference(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                app_id="app-c",
                dataset_id="dataset-a",
                reason="must stay blocked",
                request_id="archived-reference",
                idempotency_key="archived-reference-key",
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM app_dataset_references "
                        "WHERE tenant_id='tenant-a' AND app_id='app-c' AND status='active'"
                    )
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_ownership_transfer_reuses_a_removed_target_binding(tmp_path: Path) -> None:
    from core import enterprise_knowledge_base_registry as registry

    engine = _registry_engine(tmp_path)
    try:
        with engine.begin() as connection:
            binding_id = connection.execute(
                text(
                    "SELECT id FROM tenant_workspace_datasets "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-target' "
                    "AND dataset_id='dataset-a'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE tenant_workspace_datasets SET status='removed', active_primary_slot=NULL, "
                    "removed_at=CURRENT_TIMESTAMP, removed_by='owner-a', revision=revision+1 "
                    "WHERE id=:binding_id"
                ),
                {"binding_id": binding_id},
            )
            target_revision = connection.execute(
                text("SELECT revision FROM tenant_workspace_datasets WHERE id=:binding_id"),
                {"binding_id": binding_id},
            ).scalar_one()
        result = registry.transfer_dataset_ownership(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            dataset_id="dataset-a",
            target_workspace_id="workspace-target",
            expected_dataset_profile_revision=1,
            expected_ownership_revision=1,
            expected_source_workspace_revision=1,
            expected_target_workspace_revision=1,
            reason="restore historical target binding",
            request_id="transfer-removed-binding",
            idempotency_key="transfer-removed-binding-key",
        )
        assert result.body["state"] == "applied"
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT id,status,binding_kind,active_primary_slot,revision,removed_at,removed_by "
                        "FROM tenant_workspace_datasets WHERE tenant_id='tenant-a' "
                        "AND workspace_id='workspace-target' AND dataset_id='dataset-a'"
                    )
                )
                .mappings()
                .one()
            )
        assert row["id"] == binding_id
        assert row["status"] == "active"
        assert row["binding_kind"] == "primary"
        assert row["active_primary_slot"] == "primary"
        assert row["revision"] == target_revision + 1
        assert row["removed_at"] is None and row["removed_by"] is None
    finally:
        engine.dispose()
