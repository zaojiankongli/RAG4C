from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.orm import App

from core.knowledge_governance import AuditContext
from tests.test_enterprise_knowledge_base_registry_core import _registry_engine


def _add_app(engine, app_id: str = "app-c") -> None:
    with Session(engine) as session:
        session.add(App(id=app_id, tenant_id="tenant-a", name="Stage18 App", kind="chat"))
        session.commit()


def _audit(actor: str = "owner-a", request: str = "registry-mutation") -> AuditContext:
    return AuditContext(actor_id=actor, request_id=request, request_ip="127.0.0.1")


def test_app_reference_create_replays_identically_and_conflicts_on_key_reuse(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import (
        KnowledgeBaseRegistryConflict,
        create_app_reference,
    )

    engine = _registry_engine(tmp_path)
    try:
        _add_app(engine)
        first = create_app_reference(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            app_id="app-c",
            dataset_id="dataset-a",
            reason="connect application",
            request_id="request-1",
            request_ip="127.0.0.1",
            idempotency_key="reference-key-1",
        )
        replay = create_app_reference(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            app_id="app-c",
            dataset_id="dataset-a",
            reason="connect application",
            request_id="request-1",
            request_ip="127.0.0.1",
            idempotency_key="reference-key-1",
        )
        assert first.body == replay.body
        assert first.body["reference"]["status"] == "active"

        with pytest.raises(KnowledgeBaseRegistryConflict):
            create_app_reference(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                app_id="app-a",
                dataset_id="dataset-a",
                reason="reuse key with another resource",
                request_id="request-2",
                request_ip="127.0.0.1",
                idempotency_key="reference-key-1",
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM app_dataset_references "
                        "WHERE tenant_id='tenant-a' AND app_id='app-c' AND dataset_id='dataset-a' "
                        "AND status='active'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_audit_events "
                        "WHERE tenant_id='tenant-a' AND action='knowledge_base.app_reference.create'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_app_reference_remove_is_revision_fenced_and_tenant_scoped(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import (
        KnowledgeBaseRegistryConflict,
        KnowledgeBaseRegistryNotFound,
        create_app_reference,
        remove_app_reference,
    )

    engine = _registry_engine(tmp_path)
    try:
        _add_app(engine)
        created = create_app_reference(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            app_id="app-c",
            dataset_id="dataset-a",
            reason="connect application",
            request_id="request-create",
            request_ip="127.0.0.1",
            idempotency_key="reference-create",
        )
        reference_id = created.body["reference"]["id"]
        with pytest.raises(KnowledgeBaseRegistryConflict):
            remove_app_reference(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                app_id="app-c",
                dataset_id="dataset-a",
                expected_revision=99,
                reason="stale removal",
                request_id="request-stale",
                request_ip="127.0.0.1",
                idempotency_key="reference-remove-stale",
            )
        removed = remove_app_reference(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            app_id="app-c",
            dataset_id="dataset-a",
            expected_revision=1,
            reason="remove application",
            request_id="request-remove",
            request_ip="127.0.0.1",
            idempotency_key="reference-remove",
        )
        assert removed.body["reference"]["id"] == reference_id
        assert removed.body["reference"]["status"] == "removed"
        with pytest.raises(KnowledgeBaseRegistryNotFound):
            remove_app_reference(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                app_id="app-b",
                dataset_id="dataset-a",
                expected_revision=1,
                reason="cross tenant app",
                request_id="request-cross",
                request_ip="127.0.0.1",
                idempotency_key="reference-cross",
            )
    finally:
        engine.dispose()


def test_ownership_transfer_updates_authority_and_primary_binding_projection(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import (
        KnowledgeBaseRegistryConflict,
        transfer_dataset_ownership,
    )

    engine = _registry_engine(tmp_path)
    try:
        result = transfer_dataset_ownership(
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
            reason="move to target workspace",
            request_id="request-transfer",
            request_ip="127.0.0.1",
            idempotency_key="transfer-key",
        )
        assert result.body["ownership"]["workspace_id"] == "workspace-target"
        assert result.body["ownership"]["revision"] == 2
        assert result.body["dataset"]["profile_revision"] == 2
        with engine.connect() as connection:
            bindings = (
                connection.execute(
                    text(
                        "SELECT workspace_id, binding_kind, active_primary_slot, status "
                        "FROM tenant_workspace_datasets "
                        "WHERE tenant_id='tenant-a' AND dataset_id='dataset-a' "
                        "ORDER BY workspace_id"
                    )
                )
                .mappings()
                .all()
            )
        assert [
            (row["workspace_id"], row["binding_kind"], row["active_primary_slot"])
            for row in bindings
        ] == [
            ("workspace-a", "shared", None),
            ("workspace-target", "primary", "primary"),
        ]

        with pytest.raises(KnowledgeBaseRegistryConflict):
            transfer_dataset_ownership(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                dataset_id="dataset-a",
                target_workspace_id="workspace-a",
                expected_dataset_profile_revision=1,
                expected_ownership_revision=1,
                expected_source_workspace_revision=1,
                expected_target_workspace_revision=1,
                reason="stale transfer",
                request_id="request-transfer-stale",
                request_ip="127.0.0.1",
                idempotency_key="transfer-key-stale",
            )
    finally:
        engine.dispose()


def test_active_application_reference_hard_blocks_dataset_archive(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import DatasetArchiveBlocked
    from core.knowledge_datasets import KnowledgeDatasetRepository

    engine = _registry_engine(tmp_path)
    try:
        repository = KnowledgeDatasetRepository(engine)
        with pytest.raises(DatasetArchiveBlocked):
            repository.archive(
                "tenant-a",
                "dataset-a",
                expected_revision=1,
                audit=_audit(),
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE app_dataset_references SET status='removed', active_slot=NULL, "
                    "removed_at=CURRENT_TIMESTAMP, removed_by='owner-a', revision=revision+1 "
                    "WHERE id='reference-a'"
                )
            )
        archived = repository.archive(
            "tenant-a",
            "dataset-a",
            expected_revision=1,
            audit=_audit(request="archive-after-removal"),
        )
        assert archived.status == "archived"
    finally:
        engine.dispose()
