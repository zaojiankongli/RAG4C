"""Round 11 Phase 4：dataset 诞生即带 workspace ownership 权威行。

Round 10 §3 的实机缺口：`dataset_workspace_ownerships` 只有 0028/0029 的一次性
回填，运行期新建 dataset 无人写行 → 问答在 serving fence 安全弃权
（`dataset_workspace_ownerships.missing_for_dataset`）。裁定：在
`catalog._ensure_dataset_row` 同事务自动补种（默认工作区 + ownership），
权威完整性由构造保证，而不是靠运维记得跑 seed 脚本。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from core import catalog


@pytest.fixture()
def cat(monkeypatch):
    """与 test_phase7 同款的隔离 sqlite 夹具（legacy create_all，企业表齐全）。"""
    tmp = tempfile.mkdtemp(prefix="rag4c-ownprov-")
    monkeypatch.setenv("RAG4C_CATALOG_DB_PATH", str(Path(tmp) / "t.db"))
    monkeypatch.setenv("RAG4C_CATALOG_DB_URL", "")
    monkeypatch.setenv("RAG4C_CATALOG_SCHEMA_MODE", "legacy")
    from config.settings import get_settings

    get_settings.cache_clear()
    catalog.reset_engine()
    yield catalog
    catalog.reset_engine()
    get_settings.cache_clear()


def _ownership(cat, tenant: str, dataset: str):
    from models.orm import DatasetWorkspaceOwnership

    with catalog._session() as s:  # noqa: SLF001
        return (
            s.query(DatasetWorkspaceOwnership)
            .filter(
                DatasetWorkspaceOwnership.tenant_id == tenant,
                DatasetWorkspaceOwnership.dataset_id == dataset,
            )
            .first()
        )


def test_ensure_dataset_provisions_default_workspace_and_ownership(cat) -> None:
    cat.ensure_dataset("t1", "ds1", "测试库")
    ownership = _ownership(cat, "t1", "ds1")
    assert ownership is not None, "知识库创建后必须自带 workspace 归属权威行"
    assert ownership.workspace_id == "ws-t1"
    assert ownership.revision >= 1
    assert ownership.created_by == "system"

    from models.orm import TenantWorkspace

    with catalog._session() as s:  # noqa: SLF001
        workspace = s.get(TenantWorkspace, "ws-t1")
    assert workspace is not None
    assert workspace.is_default is True
    assert workspace.status == "active"
    assert workspace.active_default_slot == "default"


def test_provisioning_is_idempotent_and_never_bumps_revision(cat) -> None:
    cat.ensure_dataset("t1", "ds1", "测试库")
    first = _ownership(cat, "t1", "ds1")
    assert first is not None
    for _ in range(3):
        cat.ensure_dataset("t1", "ds1", "测试库")
    again = _ownership(cat, "t1", "ds1")
    assert again is not None
    assert again.id == first.id
    assert again.revision == first.revision
    # 工作区行也没有被反复重建
    from models.orm import TenantWorkspace

    with catalog._session() as s:  # noqa: SLF001
        count = (
            s.query(TenantWorkspace)
            .filter(TenantWorkspace.tenant_id == "t1")
            .count()
        )
    assert count == 1


def test_existing_ownership_transfer_is_never_clobbered(cat) -> None:
    """人工 transfer 过的归属不许被补种抢回默认工作区。"""
    cat.ensure_dataset("t1", "ds1", "测试库")
    from models.orm import DatasetWorkspaceOwnership, TenantWorkspace

    with catalog._session() as s:  # noqa: SLF001
        s.add(
            TenantWorkspace(
                id="ws-target",
                tenant_id="t1",
                code="target",
                normalized_name="target",
                name="目标工作区",
                status="active",
                environment="production",
                revision=1,
                created_by="tester",
                updated_by="tester",
            )
        )
        s.flush()
        ownership = (
            s.query(DatasetWorkspaceOwnership)
            .filter(
                DatasetWorkspaceOwnership.tenant_id == "t1",
                DatasetWorkspaceOwnership.dataset_id == "ds1",
            )
            .first()
        )
        assert ownership is not None
        ownership.workspace_id = "ws-target"
        s.commit()

    # 再触发 ensure（新文档登记等路径都会走到这里）
    cat.ensure_dataset("t1", "ds1", "测试库")
    after = _ownership(cat, "t1", "ds1")
    assert after is not None
    assert after.workspace_id == "ws-target", "补种不得改变已存在归属"


def test_preexisting_nondefault_workspace_is_reused(cat) -> None:
    """租户已有别的默认工作区时，归属挂到它而不是再建一个默认槽。"""
    from models.orm import TenantWorkspace

    cat.ensure_tenant("t1", "测试租户")
    with catalog._session() as s:  # noqa: SLF001
        s.add(
            TenantWorkspace(
                id="ws-custom",
                tenant_id="t1",
                code="main",
                normalized_name="main",
                name="主工作区",
                status="active",
                environment="production",
                is_default=True,
                active_default_slot="default",
                revision=1,
                created_by="tester",
                updated_by="tester",
            )
        )
        s.commit()

    cat.ensure_dataset("t1", "ds1", "测试库")
    ownership = _ownership(cat, "t1", "ds1")
    assert ownership is not None
    assert ownership.workspace_id == "ws-custom"


def test_register_document_path_provisions_too(cat) -> None:
    """入库路径（create_document 原子登记）同样触发补种——self-healing。"""
    cat.ensure_tenant("t1", "测试租户")
    # 故意不先 ensure_dataset：直接走文档登记的原子路径
    doc = cat.create_document(
        tenant_id="t1",
        dataset_id="fresh-ds",
        name="直入文档",
        file_path="/tmp/fresh.md",
        file_hash="h1",
        doc_type="md",
    )
    assert doc["id"]
    ownership = _ownership(cat, "t1", "fresh-ds")
    assert ownership is not None, "文档登记诞生的知识库同样必须带 ownership"


def test_missing_enterprise_tables_skips_with_warning(cat, caplog) -> None:
    """0028/0029 未执行的旧部署（企业表不存在）→ 记 warning 后跳过，不炸主链路。"""
    from models.orm import Base

    with catalog._session() as s:  # noqa: SLF001
        drop_tables = [
            Base.metadata.tables["dataset_workspace_ownerships"],
            Base.metadata.tables["tenant_workspaces"],
        ]
        Base.metadata.drop_all(s.get_bind(), tables=drop_tables)
        s.commit()
    with caplog.at_level(logging.WARNING, logger="core.catalog"):
        cat.ensure_dataset("t1", "ds1", "测试库")
    assert any("ownership 自动补种" in r.message for r in caplog.records)
    # 主功能不受影响
    assert cat.list_documents("ds1") == []
