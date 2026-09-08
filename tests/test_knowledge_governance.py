from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import knowledge_governance as governance
from models.orm import (
    Dataset,
    Document,
    KnowledgeAuditEvent,
    KnowledgeFolder,
    KnowledgeTag,
    Tenant,
)


def create_repository(tmp_path: Path) -> tuple[object, governance.KnowledgeGovernanceRepository]:
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'governance.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Tenant(id="tenant-2", name="Tenant 2"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge Base 1"),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="Knowledge Base 2"),
                Dataset(id="dataset-3", tenant_id="tenant-2", name="Knowledge Base 3"),
                Document(
                    id="doc-1",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    name="Guide",
                ),
                Document(
                    id="doc-2",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    name="Policy",
                ),
                Document(
                    id="doc-other-dataset",
                    tenant_id="tenant-1",
                    dataset_id="dataset-2",
                    name="Other dataset",
                ),
                Document(
                    id="doc-other-tenant",
                    tenant_id="tenant-2",
                    dataset_id="dataset-3",
                    name="Other tenant",
                ),
            ]
        )
        session.commit()
    return engine, governance.KnowledgeGovernanceRepository(engine)


def audit(number: int = 1) -> object:
    return governance.AuditContext(
        actor_id=f"user-{number}",
        request_id=f"request-{number}",
        request_ip="127.0.0.1",
    )


def test_folder_updates_use_parent_traversal_for_percent_and_underscore_paths(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    root = repository.create_folder(
        "tenant-1",
        "dataset-1",
        " Rate%_Plans ",
        description="Published guidance",
        sort_order=20,
        audit=audit(1),
    )
    child = repository.create_folder(
        "tenant-1", "dataset-1", "API_100%", parent_id=root.id, audit=audit(2)
    )
    grandchild = repository.create_folder(
        "tenant-1", "dataset-1", "Python", parent_id=child.id, audit=audit(3)
    )
    decoy = repository.create_folder(
        "tenant-1", "dataset-1", "RateXXPlans", audit=audit(4)
    )
    decoy_child = repository.create_folder(
        "tenant-1", "dataset-1", "Do not rewrite", parent_id=decoy.id, audit=audit(5)
    )

    renamed = repository.update_folder(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        folder_id=root.id,
        name="Developer%_Guides",
        audit=audit(6),
    )
    folders = repository.list_folders("tenant-1", "dataset-1", include_archived=True)
    paths = {item.id: item.path for item in folders}

    assert root.created_by == "user-1"
    assert renamed.path_hash == governance.path_hash("Developer%_Guides")
    assert paths[grandchild.id] == "Developer%_Guides/API_100%/Python"
    assert paths[decoy_child.id] == "RateXXPlans/Do not rewrite"
    assert [item.path for item in folders] == sorted(
        (item.path for item in folders), key=lambda value: (value.casefold(), value)
    )
    engine.dispose()


def test_folder_scope_uniqueness_cycle_and_normalized_length_are_safe(tmp_path: Path) -> None:
    engine, repository = create_repository(tmp_path)
    root = repository.create_folder(
        "tenant-1", "dataset-1", "Policies", audit=audit(1)
    )
    child = repository.create_folder(
        "tenant-1", "dataset-1", "HR", parent_id=root.id, audit=audit(2)
    )

    with pytest.raises(governance.GovernanceConflict, match="sibling"):
        repository.create_folder(
            "tenant-1", "dataset-1", "ＰＯＬＩＣＩＥＳ", audit=audit(3)
        )
    with pytest.raises(governance.GovernanceConflict, match="descendant"):
        repository.move_folder(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            folder_id=root.id,
            new_parent_id=child.id,
            audit=audit(4),
        )
    with pytest.raises(governance.GovernanceNotFound):
        repository.create_folder(
            "tenant-1",
            "dataset-2",
            "Cross scope",
            parent_id=root.id,
            audit=audit(5),
        )

    expanded = repository.create_tag(
        "tenant-1", "dataset-1", "İ" * 128, audit=audit(6)
    )
    assert len(expanded.normalized_name) == 256
    with pytest.raises(ValueError, match="128 characters"):
        repository.create_tag(
            "tenant-1", "dataset-1", "x" * 129, audit=audit(7)
        )
    engine.dispose()


def test_folder_lifecycle_delete_and_document_assignment_are_audited(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    root = repository.create_folder(
        "tenant-1", "dataset-1", "Root", audit=audit(1)
    )
    child = repository.create_folder(
        "tenant-1", "dataset-1", "Child", parent_id=root.id, audit=audit(2)
    )
    assigned = repository.assign_document_folder(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        folder_id=child.id,
        audit=audit(3),
    )
    repository.update_folder(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        folder_id=root.id,
        name="Renamed",
        audit=audit(4),
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.folder_id == child.id
        assert document.logical_folder_path == "Renamed/Child"
    assert assigned.folder_id == child.id
    with pytest.raises(governance.GovernanceConflict, match="children"):
        repository.delete_folder("tenant-1", "dataset-1", root.id, audit=audit(5))

    repository.assign_document_folder(
        "tenant-1", "dataset-1", "doc-1", None, audit=audit(6)
    )
    repository.delete_folder("tenant-1", "dataset-1", child.id, audit=audit(7))
    repository.delete_folder("tenant-1", "dataset-1", root.id, audit=audit(8))
    actions = [
        item.action
        for item in repository.list_audit_events("tenant-1", "dataset-1", limit=50)
    ]
    assert "document.folder.assign" in actions
    assert actions.count("folder.delete") == 2
    engine.dispose()


def test_tag_mutations_keep_legacy_projection_and_merge_links_atomically(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    source = repository.create_tag(
        "tenant-1",
        "dataset-1",
        "RAG",
        color="#3164F4",
        description="Retrieval",
        audit=audit(1),
    )
    target = repository.create_tag(
        "tenant-1", "dataset-1", "Retrieval", audit=audit(2)
    )
    assert source.created_by == "user-1"

    first = repository.attach_tag(
        "tenant-1", "dataset-1", "doc-1", source.id, audit=audit(3)
    )
    repeated = repository.attach_tag(
        "tenant-1", "dataset-1", "doc-1", source.id, audit=audit(4)
    )
    repository.attach_tag(
        "tenant-1", "dataset-1", "doc-1", target.id, audit=audit(5)
    )
    repository.attach_tag(
        "tenant-1", "dataset-1", "doc-2", source.id, audit=audit(6)
    )
    merged = repository.merge_tags(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        source_tag_id=source.id,
        target_tag_id=target.id,
        audit=audit(7),
    )
    renamed = repository.update_tag(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        tag_id=target.id,
        name="Approved Retrieval",
        audit=audit(8),
    )

    assert repeated.id == first.id
    assert merged.usage_count == 2
    assert renamed.name == "Approved Retrieval"
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.parser_meta["management"]["tags"] == ["Approved Retrieval"]
    assert repository.detach_tag(
        "tenant-1", "dataset-1", "doc-1", target.id, audit=audit(9)
    )
    assert not repository.detach_tag(
        "tenant-1", "dataset-1", "doc-1", target.id, audit=audit(10)
    )
    engine.dispose()


def test_governance_mutation_rolls_back_when_atomic_audit_insert_fails(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_governance_audit "
                "BEFORE INSERT ON knowledge_audit_events "
                "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )

    with pytest.raises((IntegrityError, governance.GovernanceConflict)):
        repository.create_folder(
            "tenant-1", "dataset-1", "Must rollback", audit=audit(1)
        )

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeFolder)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
    engine.dispose()


def test_legacy_management_backfill_is_idempotent_and_uses_system_actor(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    with Session(engine) as session:
        first = session.get(Document, "doc-1")
        second = session.get(Document, "doc-2")
        assert first is not None and second is not None
        first.logical_folder_path = "Policies/HR"
        first.parser_meta = {"management": {"tags": [" Security ", "SECURITY", "RAG"]}}
        second.logical_folder_path = "Policies/Engineering"
        second.parser_meta = {"management": {"tags": ["RAG"]}}
        session.commit()

    first_report = repository.backfill_legacy_management("tenant-1", "dataset-1")
    second_report = repository.backfill_legacy_management("tenant-1", "dataset-1")

    assert first_report.folders_created == 3
    assert first_report.tags_created == 2
    assert first_report.documents_assigned == 2
    assert first_report.tag_links_created == 3
    assert second_report == governance.GovernanceBackfillResult()
    assert [(item.name, item.usage_count) for item in repository.list_tags(
        "tenant-1", "dataset-1"
    )] == [("RAG", 2), ("Security", 1)]
    with Session(engine) as session:
        folders = list(session.scalars(select(KnowledgeFolder)))
        tags = list(session.scalars(select(KnowledgeTag)))
        assert {item.created_by for item in folders + tags} == {
            governance.SYSTEM_BACKFILL_ACTOR
        }
    events = repository.list_audit_events("tenant-1", "dataset-1", limit=100)
    assert events[0].actor_id == governance.SYSTEM_BACKFILL_ACTOR
    assert events[0].request_id.startswith("system:governance-backfill:")
    engine.dispose()


def test_audit_boundary_is_append_only_and_orders_equal_timestamps_by_sequence(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    occurred_at = datetime(2026, 8, 24, 12, 0, 0, 123456)
    first = repository.append_audit_event(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        audit=audit(1),
        action="folder.create",
        resource_type="knowledge_folder",
        resource_id="folder-1",
        after_snapshot={"name": "Policies"},
        occurred_at=occurred_at,
    )
    second = repository.append_audit_event(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        audit=audit(2),
        action="tag.attach",
        resource_type="document",
        resource_id="doc-1",
        before_snapshot={"tags": []},
        after_snapshot={"tags": ["security"]},
        occurred_at=occurred_at,
    )

    events = repository.list_audit_events("tenant-1", "dataset-1", limit=20)
    assert [item.id for item in events] == [second.id, first.id]
    assert second.sequence > first.sequence
    with pytest.raises(AttributeError):
        getattr(repository, "update_audit_event")
    with pytest.raises(AttributeError):
        getattr(repository, "delete_audit_event")
    engine.dispose()



def test_folder_archive_requires_empty_selection_and_emits_dedicated_action(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    parent = repository.create_folder("tenant-1", "dataset-1", "Parent", audit=audit(1))
    child = repository.create_folder(
        "tenant-1", "dataset-1", "Child", parent_id=parent.id, audit=audit(2)
    )
    with pytest.raises(governance.GovernanceConflict, match="active children"):
        repository.archive_folder(tenant_id="tenant-1", dataset_id="dataset-1",
            folder_id=parent.id, audit=audit(3))
    repository.delete_folder("tenant-1", "dataset-1", child.id, audit=audit(4))
    repository.assign_document_folder(
        "tenant-1", "dataset-1", "doc-1", parent.id, audit=audit(5))
    with pytest.raises(governance.GovernanceConflict, match="documents"):
        repository.archive_folder(tenant_id="tenant-1", dataset_id="dataset-1",
            folder_id=parent.id, audit=audit(6))
    repository.assign_document_folder(
        "tenant-1", "dataset-1", "doc-1", None, audit=audit(7))
    archived = repository.archive_folder(tenant_id="tenant-1", dataset_id="dataset-1",
        folder_id=parent.id, audit=audit(8))
    assert archived.status == "archived"
    actions = [event.action for event in repository.list_audit_events(
        "tenant-1", "dataset-1", resource_id=parent.id, limit=50)]
    assert actions.count("folder.archive") == 1
    engine.dispose()


def test_active_folders_cannot_be_created_or_moved_under_archived_parent(
    tmp_path: Path,
) -> None:
    engine, repository = create_repository(tmp_path)
    archived = repository.create_folder("tenant-1", "dataset-1", "Archived",
        status="archived", audit=audit(1))
    with pytest.raises(governance.GovernanceConflict, match="archived parent"):
        repository.create_folder("tenant-1", "dataset-1", "Active Child",
            parent_id=archived.id, audit=audit(2))
    active = repository.create_folder(
        "tenant-1", "dataset-1", "Active", audit=audit(3))
    with pytest.raises(governance.GovernanceConflict, match="archived parent"):
        repository.move_folder(tenant_id="tenant-1", dataset_id="dataset-1",
            folder_id=active.id, new_parent_id=archived.id, audit=audit(4))
    engine.dispose()


def test_audit_snapshots_are_sanitized_before_persistence(tmp_path: Path) -> None:
    engine, repository = create_repository(tmp_path)
    event = repository.append_audit_event(tenant_id="tenant-1", dataset_id="dataset-1",
        audit=audit(1), action="security.test", resource_type="dataset",
        resource_id="dataset-1", before_snapshot={
            "Pwd": "one", "passwd": "two", "passphrase": "three",
            "Bearer": "four", "cookie": "five", "session-id": "six",
            "sessionKey": "seven", "access_key": "eight", "signingKey": "nine",
            "encryption_key": "ten", "Authorization": "eleven", "credential": "twelve",
            "token": "thirteen",
            "benign": {
                "tokenizer": "bge-m3", "token_count": 73, "max_tokens": 8192,
                "cookie_domain": "example.test",
            },
            "nested": [
                {"dsn": "postgresql://user:pass@db.example/rag4c?sslmode=require&password=db-pass"},
                {"uri": "https://alice:secret@example.test/path?page=2&access_token=oauth&client_secret=client#section=overview&token=fragment"},
                {"plain_fragment": "https://example.test/path#dashboard"},
                {"aws": "https://bucket.s3.amazonaws.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=credential&X-Amz-Date=20260825T120000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host&X-Amz-Signature=signature&X-Amz-Security-Token=session"},
                {"safe": "visible"}]})
    with Session(engine) as session:
        stored = session.get(KnowledgeAuditEvent, event.sequence)
        assert stored is not None
        snapshot = stored.before_snapshot
    assert snapshot is not None
    for key in ("Pwd", "passwd", "passphrase", "Bearer", "cookie", "session-id",
                "sessionKey", "access_key", "signingKey", "encryption_key",
                "Authorization", "credential", "token"):
        assert snapshot[key] == "[REDACTED]"
    assert snapshot["benign"] == {
        "tokenizer": "bge-m3", "token_count": 73, "max_tokens": 8192,
        "cookie_domain": "example.test",
    }
    assert snapshot["nested"] == [
        {"dsn": "postgresql://db.example/rag4c?sslmode=require&password=%5BREDACTED%5D"},
        {"uri": "https://example.test/path?page=2&access_token=%5BREDACTED%5D&client_secret=%5BREDACTED%5D#section=overview&token=%5BREDACTED%5D"},
        {"plain_fragment": "https://example.test/path"},
        {"aws": "https://bucket.s3.amazonaws.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=%5BREDACTED%5D&X-Amz-Date=20260825T120000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host&X-Amz-Signature=%5BREDACTED%5D&X-Amz-Security-Token=%5BREDACTED%5D"},
        {"safe": "visible"}]
    engine.dispose()


def test_audit_sanitizer_redacts_file_uri_secrets_without_losing_shape() -> None:
    snapshot = governance.sanitize_audit_snapshot({
        "uri": "file:///srv/source?token=top-secret&lang=en#password=also-secret"
    })

    assert snapshot == {
        "uri": "file:///srv/source?token=%5BREDACTED%5D&lang=en#password=%5BREDACTED%5D"
    }


def test_audit_repository_pages_by_authoritative_sequence(tmp_path: Path) -> None:
    engine, repository = create_repository(tmp_path)
    first = repository.append_audit_event(tenant_id="tenant-1", dataset_id="dataset-1",
        audit=audit(1), action="sequence.test", resource_type="dataset",
        resource_id="dataset-1", occurred_at=datetime(2026, 8, 25, 10, 0, 0))
    second = repository.append_audit_event(tenant_id="tenant-1", dataset_id="dataset-1",
        audit=audit(2), action="sequence.test", resource_type="dataset",
        resource_id="dataset-1", occurred_at=datetime(2026, 8, 25, 8, 0, 0))
    third = repository.append_audit_event(tenant_id="tenant-1", dataset_id="dataset-1",
        audit=audit(3), action="sequence.test", resource_type="dataset",
        resource_id="dataset-1", occurred_at=datetime(2026, 8, 25, 9, 0, 0))
    page_one = repository.list_audit_events(
        "tenant-1", "dataset-1", action="sequence.test", limit=2)
    page_two = repository.list_audit_events("tenant-1", "dataset-1",
        action="sequence.test", before_sequence=page_one[-1].sequence, limit=2)
    assert [item.id for item in page_one] == [third.id, second.id]
    assert [item.id for item in page_two] == [first.id]
    engine.dispose()
