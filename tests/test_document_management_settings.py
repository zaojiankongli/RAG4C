from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from server import documents


@pytest.fixture()
def cat(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="rag4c-document-management-")
    monkeypatch.setenv("RAG4C_CATALOG_DB_PATH", str(Path(tmp) / "catalog.db"))
    monkeypatch.setenv("RAG4C_CATALOG_DB_URL", "")
    monkeypatch.setenv("RAG4C_CATALOG_SCHEMA_MODE", "legacy")
    from config.settings import get_settings
    from core import catalog
    get_settings.cache_clear()
    catalog.reset_engine()
    yield catalog
    catalog.reset_engine()
    get_settings.cache_clear()


def test_document_management_persists_category_and_normalized_tags(cat):
    doc = cat.create_document("default", "default", "handbook.pdf", file_path="D:/handbook.pdf")

    updated = cat.update_document_management(
        doc["id"], logical_folder_path="制度/人力", tags=["员工", " 制度 ", "员工", ""]
    )

    assert updated["logical_folder_path"] == "制度/人力"
    assert updated["tags"] == ["员工", "制度"]
    listed = cat.list_documents("default")[0]
    assert listed["logical_folder_path"] == "制度/人力"
    assert listed["tags"] == ["员工", "制度"]


def test_batch_settings_moves_documents_and_merges_tags(cat):
    first = cat.create_document("default", "default", "a.md")
    second = cat.create_document("default", "default", "b.md")
    cat.update_document_management(first["id"], tags=["旧标签"])

    result = documents.batch_document_settings(
        documents.BatchDocumentSettingsRequest(
            document_ids=[first["id"], second["id"]],
            logical_folder_path="产品/需求",
            add_tags=["项目A"],
            remove_tags=["旧标签"],
        )
    )

    assert result["updated"] == 2
    assert cat.get_document(first["id"])["logical_folder_path"] == "产品/需求"
    assert cat.get_document(first["id"])["tags"] == ["项目A"]
    assert cat.get_document(second["id"])["tags"] == ["项目A"]


def test_single_settings_endpoint_replaces_tags(cat):
    doc = cat.create_document("default", "default", "policy.md")

    result = documents.update_document_settings(
        doc["id"], documents.DocumentSettingsRequest(logical_folder_path="财务", tags=["报销", "制度"])
    )

    assert result["logical_folder_path"] == "财务"
    assert result["tags"] == ["报销", "制度"]
