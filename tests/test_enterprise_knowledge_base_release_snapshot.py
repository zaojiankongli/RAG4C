from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from core.catalog_schema import upgrade_catalog
from core.enterprise_knowledge_base_releases import (
    build_release_snapshot,
    collect_release_snapshot,
)
from models.orm import (
    Account,
    DataSourceRecord,
    Dataset,
    DatasetWorkspaceOwnership,
    Document,
    DocumentVersion,
    QAKnowledge,
    SourceSyncRun,
    Tenant,
    TenantMember,
    TenantReleaseChannel,
    TenantWorkspace,
    TenantWorkspaceDataset,
)


def _manifest() -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "release_number": 3,
        "profile_revision": 8,
        "ownership_revision": 5,
        "workspace_revision": 4,
        "dataset_mutation_generation": 21,
        "dataset_serving_generation": 13,
    }


def _entries() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "qa_revision",
            "resource_id": "qa-a",
            "resource_revision": "7",
            "content_digest": "c" * 64,
            "facts": {"review_status": "approved", "retrieval_enabled": True},
        }
    ]


def test_snapshot_is_ready_only_when_no_blockers_exist() -> None:
    snapshot = build_release_snapshot(manifest=_manifest(), entries=_entries(), blockers=[])
    assert snapshot.readiness_state == "ready"
    assert snapshot.blockers == ()
    assert snapshot.manifest_digest
    assert snapshot.readiness_fingerprint


def test_snapshot_readiness_is_fail_closed_and_order_stable() -> None:
    blockers = [
        {"code": "projection_pending", "resource_id": "document-b", "severity": "blocked"},
        {"code": "source_authority_missing", "resource_id": "source-a", "severity": "unavailable"},
    ]
    first = build_release_snapshot(manifest=_manifest(), entries=_entries(), blockers=blockers)
    second = build_release_snapshot(
        manifest=_manifest(), entries=_entries(), blockers=list(reversed(blockers))
    )

    assert first.readiness_state == "unavailable"
    assert first.readiness_fingerprint == second.readiness_fingerprint
    assert first.blockers == second.blockers


def test_blocked_snapshot_does_not_become_unavailable_without_missing_authority() -> None:
    snapshot = build_release_snapshot(
        manifest=_manifest(),
        entries=_entries(),
        blockers=[
            {
                "code": "document_index_drift",
                "resource_id": "document-a",
                "severity": "blocked",
            }
        ],
    )
    assert snapshot.readiness_state == "blocked"


def _release_engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'release-snapshot.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    now = datetime(2026, 8, 28, 12, 0, 0)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="Tenant A", plan="enterprise", status="active"))
        session.add(Account(id="owner-a", name="Owner A", email="owner-a@release.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="owner-a",
                tenant_id="tenant-a",
                role="owner",
                status="active",
            )
        )
        session.add_all(
            [
                TenantReleaseChannel(
                    id=f"channel-{code}",
                    tenant_id="tenant-a",
                    code=code,
                    normalized_code=code,
                    name=name,
                    status="active",
                    risk_tier=risk,
                    promotion_order=order,
                    is_default_serving=code == "production",
                    active_default_slot="default" if code == "production" else None,
                    revision=1,
                    created_at=now,
                    created_by="owner-a",
                    updated_at=now,
                    updated_by="owner-a",
                )
                for code, name, risk, order in (
                    ("development", "Development", "low", 10),
                    ("testing", "Testing", "medium", 20),
                    ("production", "Production", "high", 30),
                )
            ]
        )
        dataset = Dataset(
            id="dataset-a",
            tenant_id="tenant-a",
            name="Release Knowledge",
            status="active",
            visibility="tenant",
            profile_revision=4,
            parser_policy={"engine": "mineru", "credential_ref": "secret://parser/main"},
            chunk_policy={"mode": "parent_child", "size": 800},
            retrieval_policy={"top_k": 12, "rerank": True},
            graph_enabled=True,
            qa_enabled=True,
            mutation_generation=9,
            serving_generation=3,
            doc_count=1,
            chunk_count=1,
            created_at=now,
            updated_at=now,
        )
        workspace = TenantWorkspace(
            id="workspace-a",
            tenant_id="tenant-a",
            code="release",
            name="Release Workspace",
            normalized_name="release workspace",
            environment="testing",
            status="active",
            revision=2,
            created_by="owner-a",
            updated_by="owner-a",
            created_at=now,
            updated_at=now,
        )
        session.add_all([dataset, workspace])
        session.flush()
        session.add_all(
            [
                TenantWorkspaceDataset(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    dataset_id="dataset-a",
                    binding_kind="primary",
                    status="active",
                    active_primary_slot="primary",
                    revision=1,
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now,
                    updated_at=now,
                ),
                DatasetWorkspaceOwnership(
                    id="ownership-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    workspace_id="workspace-a",
                    revision=5,
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        document = Document(
            id="document-a",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            name="handbook.md",
            status="completed",
            lifecycle_state="active",
            retrieval_enabled=True,
            content_revision=1,
            desired_index_revision=1,
            indexed_revision=1,
            graph_revision=1,
            mutation_generation=2,
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            id="document-version-a",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="document-a",
            revision=1,
            source_identity="upload:handbook.md",
            source_hash="a" * 64,
            parser_policy_snapshot={"engine": "mineru"},
            parser_metadata={},
            created_by="owner-a",
            created_at=now,
        )
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        session.add_all(
            [
                QAKnowledge(
                    id="qa-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    revision=3,
                    question="What is the private answer?",
                    answer="This body must never enter a Release entry.",
                    origin="manual",
                    review_status="approved",
                    lifecycle_state="active",
                    retrieval_enabled=True,
                    created_by="owner-a",
                    reviewed_by="owner-a",
                    reviewed_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                DataSourceRecord(
                    id="source-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Support Source",
                    source_type="http",
                    effective_config={"endpoint": "https://example.test/data"},
                    config_fingerprint="b" * 64,
                    status="active",
                    last_cursor={"page": 2, "access_token": "raw-source-secret"},
                    last_result={"fetched": 10},
                    last_sync_at=now,
                    mutation_generation=4,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.commit()
    return engine, now


def test_collect_release_snapshot_captures_effective_authority_without_bodies(
    tmp_path: Path,
) -> None:
    engine, now = _release_engine(tmp_path)
    with Session(engine) as session:
        snapshot = collect_release_snapshot(
            session,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            release_number=1,
            now=now,
        )

    assert snapshot.readiness_state == "ready"
    assert [entry["resource_type"] for entry in snapshot.entries] == [
        "dataset_profile",
        "document_version",
        "projection_revision",
        "qa_revision",
        "source_generation",
    ]
    rendered = repr(snapshot.entries)
    assert "What is the private answer?" not in rendered
    assert "This body must never enter" not in rendered
    assert "raw-source-secret" not in rendered
    assert "secret://parser/main" not in rendered
    assert len(snapshot.manifest_digest) == 64
    engine.dispose()


def test_collect_release_snapshot_blocks_index_drift_and_active_source_run(tmp_path: Path) -> None:
    engine, now = _release_engine(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "document-a")
        assert document is not None
        document.indexed_revision = 0
        session.add(
            SourceSyncRun(
                id="source-run-active",
                source_id="source-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                status="running",
                trigger="manual",
                source_generation=4,
                dataset_generation=9,
                started_at=now,
                created_at=now,
            )
        )
        session.commit()
        snapshot = collect_release_snapshot(
            session,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            release_number=2,
            now=now,
        )

    assert snapshot.readiness_state == "blocked"
    assert {blocker["code"] for blocker in snapshot.blockers} == {
        "document_index_drift",
        "source_sync_active",
    }
    engine.dispose()
