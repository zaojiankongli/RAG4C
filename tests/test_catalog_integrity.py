from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, inspect, text


def integrity_api() -> ModuleType:
    try:
        return importlib.import_module("core.catalog_integrity")
    except ModuleNotFoundError:
        pytest.fail("core.catalog_integrity is missing")


def schema_api() -> ModuleType:
    return importlib.import_module("core.catalog_schema")


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def seed_identity_rows(engine, *, duplicates: bool) -> None:
    now = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO accounts(id,name,email,created_at) "
                "VALUES(:id,:name,:email,:created_at)"
            ),
            {"id": "acct-1", "name": "One", "email": "one@example.test", "created_at": now},
        )
        connection.execute(
            text(
                "INSERT INTO tenants("
                "id,name,plan,status,quota_documents,quota_chunks,doc_count,chunk_count,created_at"
                ") VALUES(:id,:name,:plan,:status,:quota_documents,:quota_chunks,:doc_count,:chunk_count,:created_at)"
            ),
            {
                "id": "tenant-1",
                "name": "Tenant",
                "plan": "free",
                "status": "active",
                "quota_documents": 1000,
                "quota_chunks": 100000,
                "doc_count": 1,
                "chunk_count": 0,
                "created_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO datasets("
                "id,tenant_id,name,description,status,doc_count,chunk_count,created_at"
                ") VALUES(:id,:tenant_id,:name,:description,:status,:doc_count,:chunk_count,:created_at)"
            ),
            {
                "id": "dataset-1",
                "tenant_id": "tenant-1",
                "name": "KB",
                "description": "",
                "status": "active",
                "doc_count": 1,
                "chunk_count": 0,
                "created_at": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO documents("
                "id,dataset_id,tenant_id,name,file_path,file_hash,doc_type,status,status_detail,"
                "progress,chunk_count,error_message,parser_meta,created_at,updated_at"
                ") VALUES(:id,:dataset_id,:tenant_id,:name,:file_path,:file_hash,:doc_type,:status,"
                ":status_detail,:progress,:chunk_count,:error_message,:parser_meta,:created_at,:updated_at)"
            ),
            {
                "id": "doc-1",
                "dataset_id": "dataset-1",
                "tenant_id": "tenant-1",
                "name": "Document",
                "file_path": "",
                "file_hash": "",
                "doc_type": "",
                "status": "waiting",
                "status_detail": "",
                "progress": 0.0,
                "chunk_count": 0,
                "error_message": "",
                "parser_meta": "{}",
                "created_at": now,
                "updated_at": now,
            },
        )
        member_rows = [
            {
                "id": 1,
                "account_id": "acct-1",
                "tenant_id": "tenant-1",
                "role": "owner",
                "created_at": now,
            }
        ]
        segment_rows = [
            {
                "id": "seg-1",
                "document_id": "doc-1",
                "seq": 0,
                "status": "pending",
                "progress": 0.0,
                "chunk_count": 0,
                "segment_meta": "{}",
                "created_at": now,
            }
        ]
        metadata_rows = [
            {
                "id": 1,
                "dataset_id": "dataset-1",
                "key": "department",
                "value_type": "string",
                "source": "manual",
                "label": "department",
                "created_at": now,
            }
        ]
        if duplicates:
            member_rows.append({**member_rows[0], "id": 2})
            segment_rows.append({**segment_rows[0], "id": "seg-2"})
            metadata_rows.append({**metadata_rows[0], "id": 2})
        connection.execute(
            text(
                "INSERT INTO tenant_members(id,account_id,tenant_id,role,created_at) "
                "VALUES(:id,:account_id,:tenant_id,:role,:created_at)"
            ),
            member_rows,
        )
        connection.execute(
            text(
                "INSERT INTO document_segments("
                "id,document_id,seq,status,progress,chunk_count,segment_meta,created_at"
                ") VALUES(:id,:document_id,:seq,:status,:progress,:chunk_count,:segment_meta,:created_at)"
            ),
            segment_rows,
        )
        connection.execute(
            text(
                "INSERT INTO metadata_fields("
                "id,dataset_id,key,value_type,source,label,created_at"
                ") VALUES(:id,:dataset_id,:key,:value_type,:source,:label,:created_at)"
            ),
            metadata_rows,
        )


def test_duplicate_audit_reports_row_ids_without_identity_values(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "duplicates.db")
    schema_api().upgrade_catalog(url, schema_api().BASELINE_REVISION)
    engine = create_engine(url)
    try:
        seed_identity_rows(engine, duplicates=True)
        report = integrity_api().audit_catalog_duplicates(engine)
    finally:
        engine.dispose()

    assert report.unresolved_groups == 3
    assert report.groups["tenant_members"][0].row_ids == (1, 2)
    assert report.groups["document_segments"][0].row_ids == ("seg-1", "seg-2")
    assert report.groups["metadata_fields"][0].row_ids == (1, 2)
    safe = str(report.to_safe_dict())
    assert "one@example.test" not in safe
    assert "department" not in safe
    assert "tenant-1" not in safe


def test_upgrade_refuses_constraints_when_duplicates_exist(tmp_path: Path) -> None:
    api = schema_api()
    url = sqlite_url(tmp_path / "blocked.db")
    api.upgrade_catalog(url, api.BASELINE_REVISION)
    engine = create_engine(url)
    seed_identity_rows(engine, duplicates=True)
    engine.dispose()

    with pytest.raises(api.CatalogSchemaError, match="duplicate identity groups"):
        api.upgrade_catalog(url)

    engine = create_engine(url)
    try:
        assert api.inspect_catalog_schema(engine).revision == api.BASELINE_REVISION
    finally:
        engine.dispose()


def test_clean_catalog_upgrades_to_composite_identity_constraints(tmp_path: Path) -> None:
    api = schema_api()
    url = sqlite_url(tmp_path / "clean.db")
    api.upgrade_catalog(url, api.BASELINE_REVISION)
    engine = create_engine(url)
    seed_identity_rows(engine, duplicates=False)
    engine.dispose()

    api.upgrade_catalog(url)

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        constraints = {
            table: {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
            for table in ("tenant_members", "document_segments", "metadata_fields")
        }
        assert ("account_id", "tenant_id") in constraints["tenant_members"]
        assert ("document_id", "seq") in constraints["document_segments"]
        assert ("dataset_id", "key") in constraints["metadata_fields"]
        assert api.inspect_catalog_schema(engine).revision == api.HEAD_REVISION
    finally:
        engine.dispose()


def test_duplicate_audit_quotes_mysql_reserved_identifiers() -> None:
    from sqlalchemy.dialects.mysql import dialect

    sql = integrity_api().build_duplicate_group_query(
        dialect(), "metadata_fields", ("dataset_id", "key")
    )

    assert sql == (
        "SELECT dataset_id, \x60key\x60 FROM metadata_fields "
        "GROUP BY dataset_id, \x60key\x60 HAVING COUNT(*) > 1"
    )
