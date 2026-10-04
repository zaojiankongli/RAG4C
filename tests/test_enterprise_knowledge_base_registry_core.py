from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext
from models.orm import (
    Account,
    App,
    AppDatasetReference,
    DataSourceRecord,
    Dataset,
    DatasetWorkspaceOwnership,
    Tenant,
    TenantMember,
    TenantReleaseChannel,
    TenantWorkspace,
    TenantWorkspaceDataset,
)


def _registry_engine(tmp_path: Path):
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要某个特定 revision（不验证「从某 revision 升到 head」的
    # 过程本身），所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "knowledge-base-registry.db")
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    now = datetime(2026, 8, 27, 12, 0, 0)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", plan="enterprise", status="active"),
                Tenant(id="tenant-b", name="Tenant B", plan="enterprise", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                TenantMember(
                    account_id="owner-a", tenant_id="tenant-a", role="owner", status="active"
                ),
                TenantMember(
                    account_id="editor-a", tenant_id="tenant-a", role="editor", status="active"
                ),
                TenantMember(
                    account_id="owner-b", tenant_id="tenant-b", role="owner", status="active"
                ),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Alpha Knowledge Base",
                    description="Support documents",
                    status="active",
                    visibility="tenant",
                    doc_count=0,
                    chunk_count=0,
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=1),
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-a",
                    name="Beta Knowledge Base",
                    description="Operations",
                    status="archived",
                    visibility="private",
                    doc_count=None,
                    chunk_count=None,
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                    archived_at=now - timedelta(days=1),
                    archived_by="owner-a",
                ),
                Dataset(
                    id="dataset-x",
                    tenant_id="tenant-b",
                    name="Other Tenant Base",
                    status="active",
                    created_at=now - timedelta(days=1),
                    updated_at=now,
                ),
                TenantWorkspace(
                    id="workspace-a",
                    tenant_id="tenant-a",
                    code="alpha",
                    name="Alpha Workspace",
                    normalized_name="alpha workspace",
                    environment="production",
                    status="active",
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now - timedelta(days=5),
                    updated_at=now - timedelta(days=5),
                ),
                TenantWorkspace(
                    id="workspace-target",
                    tenant_id="tenant-a",
                    code="target",
                    name="Target Workspace",
                    normalized_name="target workspace",
                    environment="production",
                    status="active",
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
                TenantWorkspace(
                    id="workspace-b",
                    tenant_id="tenant-b",
                    code="beta",
                    name="Beta Workspace",
                    normalized_name="beta workspace",
                    environment="production",
                    status="active",
                    created_by="owner-b",
                    updated_by="owner-b",
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                ),
                TenantWorkspaceDataset(
                    tenant_id="tenant-a",
                    workspace_id="workspace-a",
                    dataset_id="dataset-a",
                    binding_kind="primary",
                    active_primary_slot="primary",
                    status="active",
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now - timedelta(days=3),
                    updated_at=now - timedelta(days=3),
                ),
                TenantWorkspaceDataset(
                    tenant_id="tenant-a",
                    workspace_id="workspace-target",
                    dataset_id="dataset-a",
                    binding_kind="shared",
                    status="active",
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                TenantWorkspaceDataset(
                    tenant_id="tenant-a",
                    workspace_id="workspace-target",
                    dataset_id="dataset-b",
                    binding_kind="primary",
                    active_primary_slot="primary",
                    status="active",
                    created_by="owner-a",
                    updated_by="owner-a",
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                ),
                TenantWorkspaceDataset(
                    tenant_id="tenant-b",
                    workspace_id="workspace-b",
                    dataset_id="dataset-x",
                    binding_kind="primary",
                    active_primary_slot="primary",
                    status="active",
                    created_by="owner-b",
                    updated_by="owner-b",
                    created_at=now - timedelta(days=1),
                    updated_at=now - timedelta(days=1),
                ),
                App(id="app-a", tenant_id="tenant-a", name="Alpha App", kind="chat"),
                App(id="app-b", tenant_id="tenant-b", name="Other App", kind="api"),
            ]
        )
        session.flush()
        from core.enterprise_release_channels import (
            ensure_default_release_channels_in_session,
        )

        ensure_default_release_channels_in_session(
            session, tenant_id="tenant-a", actor_id="owner-a", now=now
        )
        ensure_default_release_channels_in_session(
            session, tenant_id="tenant-b", actor_id="owner-b", now=now
        )
        default_channel = session.scalar(
            select(TenantReleaseChannel.id).where(
                TenantReleaseChannel.tenant_id == "tenant-a",
                TenantReleaseChannel.is_default_serving.is_(True),
            )
        )
        assert default_channel is not None
        session.add(
            DataSourceRecord(
                id="source-a",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                name="Alpha Source",
                source_type="upload",
                status="active",
            )
        )
        session.add_all(
            [
                DatasetWorkspaceOwnership(
                    id="ownership-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    workspace_id="workspace-a",
                    revision=1,
                    created_at=now,
                    created_by="owner-a",
                    updated_at=now,
                    updated_by="owner-a",
                ),
                DatasetWorkspaceOwnership(
                    id="ownership-b",
                    tenant_id="tenant-a",
                    dataset_id="dataset-b",
                    workspace_id="workspace-target",
                    revision=1,
                    created_at=now,
                    created_by="owner-a",
                    updated_at=now,
                    updated_by="owner-a",
                ),
                DatasetWorkspaceOwnership(
                    id="ownership-x",
                    tenant_id="tenant-b",
                    dataset_id="dataset-x",
                    workspace_id="workspace-b",
                    revision=1,
                    created_at=now,
                    created_by="owner-b",
                    updated_at=now,
                    updated_by="owner-b",
                ),
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
                    release_channel_id=str(default_channel),
                    pinned_release_id=None,
                ),
            ]
        )
        session.commit()
    return engine


def _audit(actor: str = "owner-a", request: str = "registry-test") -> AuditContext:
    return AuditContext(actor_id=actor, request_id=request, request_ip="127.0.0.1")


def test_registry_list_is_tenant_scoped_keyset_filtered_and_preserves_null_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from core.enterprise_knowledge_base_registry import list_knowledge_bases

    engine = _registry_engine(tmp_path)
    try:
        first = list_knowledge_bases(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            status=None,
            keyword="Knowledge Base",
            limit=1,
        )
        assert [item["id"] for item in first.body["items"]] == ["dataset-a"]
        assert first.body["items"][0]["document_count"] == 0
        assert first.body["items"][0]["chunk_count"] == 0
        assert first.body["items"][0]["source_count"] == 1
        assert first.body["next_cursor"] is not None

        registry = __import__("core.enterprise_knowledge_base_registry", fromlist=["_group_count"])
        original_group_count = registry._group_count

        def unavailable_source_count(session, table, **kwargs):
            if table.name == "data_sources":
                raise registry.KnowledgeBaseRegistryUnavailable("source count unavailable")
            return original_group_count(session, table, **kwargs)

        monkeypatch.setattr(registry, "_group_count", unavailable_source_count)
        second = list_knowledge_bases(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            cursor=first.body["next_cursor"],
            limit=1,
        )
        assert [item["id"] for item in second.body["items"]] == ["dataset-b"]
        assert second.body["items"][0]["document_count"] == 0
        assert second.body["items"][0]["chunk_count"] == 0
        assert second.body["items"][0]["source_count"] is None
        assert all(item["tenant_id"] == "tenant-a" for item in second.body["items"])
    finally:
        engine.dispose()


def test_registry_detail_and_dependencies_explain_active_app_archive_blocker(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import (
        get_knowledge_base,
        get_knowledge_base_dependencies,
    )

    engine = _registry_engine(tmp_path)
    try:
        detail = get_knowledge_base(
            engine, tenant_id="tenant-a", actor_id="owner-a", dataset_id="dataset-a"
        )
        assert detail.body["knowledge_base"]["owning_workspace"]["id"] == "workspace-a"
        assert detail.body["knowledge_base"]["active_shared_association_count"] == 1
        assert detail.body["knowledge_base"]["active_application_reference_count"] == 1

        dependencies = get_knowledge_base_dependencies(
            engine, tenant_id="tenant-a", actor_id="owner-a", dataset_id="dataset-a"
        )
        assert dependencies.body["archive_readiness"]["ready"] is False
        assert dependencies.body["archive_readiness"]["blocker_count"] == 1
        assert dependencies.body["application_references"][0]["id"] == "reference-a"
        assert dependencies.body["associations"][0]["binding_kind"] in {"primary", "shared"}
    finally:
        engine.dispose()


def test_registry_rejects_cross_tenant_actor_and_dataset_scope(tmp_path: Path):
    from core.enterprise_knowledge_base_registry import (
        KnowledgeBaseRegistryForbidden,
        KnowledgeBaseRegistryNotFound,
        get_knowledge_base,
    )

    engine = _registry_engine(tmp_path)
    try:
        with pytest.raises(KnowledgeBaseRegistryForbidden):
            get_knowledge_base(
                engine, tenant_id="tenant-a", actor_id="owner-b", dataset_id="dataset-a"
            )
        with pytest.raises(KnowledgeBaseRegistryNotFound):
            get_knowledge_base(
                engine, tenant_id="tenant-a", actor_id="owner-a", dataset_id="dataset-x"
            )
    finally:
        engine.dispose()
