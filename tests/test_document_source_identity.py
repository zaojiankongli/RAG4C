from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

from core import catalog


def configure_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url

    url = head_db_url(tmp_path / "catalog.db")
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify")
    return catalog.get_engine()


def test_document_schema_contains_stable_source_identity_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = configure_catalog(tmp_path, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("documents")}

    assert {
        "source_uri",
        "source_uri_hash",
        "external_id",
        "logical_folder_path",
        "source_type",
        "source_id",
    } <= columns
    catalog.reset_engine()


def test_source_uri_identity_survives_local_path_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_catalog(tmp_path, monkeypatch)
    created = catalog.create_document(
        "tenant-1",
        "dataset-1",
        "Guide",
        file_path="C:/cache/old/guide.md",
        source_uri="github://owner/docs@main/guide.md",
        external_id="guide.md",
        source_type="github_repo",
        source_id="source-1",
        logical_folder_path="handbook/guide.md",
    )

    found = catalog.find_document_by_source(
        "dataset-1",
        source_uri="github://owner/docs@main/guide.md",
    )

    assert found is not None
    assert found["id"] == created["id"]
    assert found["file_path"] == "C:/cache/old/guide.md"
    refreshed = catalog.get_document(created["id"])
    assert refreshed is not None
    assert refreshed["logical_folder_path"] == "handbook/guide.md"
    catalog.reset_engine()


def test_synced_document_reuses_existing_source_identity_when_doc_id_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_catalog(tmp_path, monkeypatch)
    first = catalog.register_synced_document(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        doc_id="doc-old",
        name="guide.md",
        chunk_count=2,
        source_uri="fake://guide.md",
        external_id="guide.md",
        source_type="fake",
        source_id="source-1",
    )
    second = catalog.register_synced_document(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        doc_id="doc-new",
        name="renamed-guide.md",
        chunk_count=3,
        source_uri="fake://guide.md",
        external_id="guide.md",
        source_type="fake",
        source_id="source-1",
    )

    assert second["id"] == first["id"] == "doc-old"
    assert len(catalog.list_documents("dataset-1")) == 1
    catalog.reset_engine()


def test_empty_source_identity_keeps_path_fallback_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_catalog(tmp_path, monkeypatch)
    created = catalog.create_document(
        "tenant-1", "dataset-1", "Local", file_path="C:/docs/local.md"
    )

    found = catalog.find_document_by_path("dataset-1", "C:/docs/local.md")

    assert found is not None
    assert found["id"] == created["id"]
    catalog.reset_engine()
