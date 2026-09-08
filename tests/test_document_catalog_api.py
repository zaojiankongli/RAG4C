from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import Account, Base, Dataset, Document, Tenant, TenantMember
from server.document_catalog_api import router
from server.knowledge_auth import issue_knowledge_actor_token


BASE_TIME = datetime(2026, 8, 26, 9, 0, 0)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("document-catalog-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _document(
    document_id: str,
    *,
    tenant_id: str = "tenant-a",
    dataset_id: str = "dataset-a",
    name: str,
    status: str = "completed",
    doc_type: str = "pdf",
    engine: str | None = "vision",
    folder: str = "制度/人力",
    tags: list[str] | None = None,
    chunks: int = 10,
    lifecycle_state: str = "active",
    updated_offset: int = 0,
    created_offset: int | None = None,
    parser_observed: bool = True,
) -> Document:
    parser_meta: dict[str, object] = {}
    if parser_observed:
        if engine is not None:
            parser_meta["engine"] = engine
        parser_meta["management"] = {"tags": list(tags or [])}
    return Document(
        id=document_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        name=name,
        status=status,
        status_detail=f"{status} detail",
        progress=1.0 if status == "completed" else 0.5,
        chunk_count=chunks,
        doc_type=doc_type,
        error_message="failed" if status == "error" else "",
        logical_folder_path=folder or None,
        source_uri=f"https://example.test/{document_id}",
        source_type="web",
        source_id="source-a",
        external_id=f"external-{document_id}",
        mutation_generation=2,
        lifecycle_state=lifecycle_state,
        retrieval_enabled=lifecycle_state == "active",
        parser_meta=parser_meta,
        created_at=BASE_TIME
        + timedelta(minutes=created_offset if created_offset is not None else updated_offset),
        updated_at=BASE_TIME + timedelta(minutes=updated_offset),
    )


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="member-b", name="Member B", email="member-b@example.test"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="member-b", tenant_id="tenant-b", role="member"),
                Dataset(id="dataset-a", tenant_id="tenant-a", name="Dataset A", status="active"),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                _document(
                    "doc-a1",
                    name="员工手册.pdf",
                    tags=["员工", "制度"],
                    chunks=12,
                    updated_offset=5,
                ),
                _document(
                    "doc-a2",
                    name="薪酬制度.docx",
                    status="waiting",
                    doc_type="docx",
                    engine="mineru",
                    folder="制度/人力/薪酬",
                    tags=["薪酬", "制度"],
                    chunks=0,
                    updated_offset=4,
                ),
                _document(
                    "doc-a3",
                    name="产品发布说明.md",
                    status="parsing",
                    doc_type="md",
                    engine="mineru",
                    folder="产品/发布",
                    tags=["产品"],
                    chunks=3,
                    updated_offset=3,
                ),
                _document(
                    "doc-a4",
                    name="旧版制度.txt",
                    status="error",
                    doc_type="txt",
                    engine=None,
                    folder="制度",
                    tags=["制度"],
                    chunks=4,
                    lifecycle_state="expired",
                    updated_offset=2,
                    parser_observed=False,
                ),
                _document(
                    "doc-a5",
                    name="并列乙.pdf",
                    tags=["制度"],
                    chunks=8,
                    updated_offset=1,
                ),
                _document(
                    "doc-a0",
                    name="并列甲.pdf",
                    tags=["制度"],
                    chunks=7,
                    updated_offset=1,
                ),
                _document(
                    "doc-b1",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    name="Tenant B Secret.pdf",
                    tags=["制度"],
                    chunks=99,
                    updated_offset=99,
                ),
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def catalog_engine(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def api(catalog_engine):
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = catalog_engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), settings


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": "req-document-catalog",
    }


def test_catalog_queries_legacy_schema_without_governance_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Account.__table__,
            TenantMember.__table__,
            Dataset.__table__,
            Document.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="legacy-tenant", name="Legacy", status="active"),
                Dataset(
                    id="legacy-dataset",
                    tenant_id="legacy-tenant",
                    name="Legacy Dataset",
                    status="active",
                ),
                _document(
                    "legacy-doc",
                    tenant_id="legacy-tenant",
                    dataset_id="legacy-dataset",
                    name="Legacy.pdf",
                    tags=["legacy"],
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)

    table_names = set(inspect(engine).get_table_names())
    assert "knowledge_tags" not in table_names
    assert "document_tags" not in table_names
    page = catalog.list_documents_page(
        "legacy-tenant",
        "legacy-dataset",
        offset=0,
        limit=20,
        tag="legacy",
    )
    summary = catalog.summarize_documents("legacy-tenant", "legacy-dataset")

    assert [item["id"] for item in page["items"]] == ["legacy-doc"]
    assert page["tag_authority"] == "legacy_projection"
    assert summary["summary"]["total"] == 1
    assert summary["facets"]["tags"] == [{"name": "legacy", "documents": 1, "chunks": 10}]
    engine.dispose()


def test_legacy_list_documents_keeps_list_contract_and_shared_projection(catalog_engine) -> None:
    rows = catalog.list_documents("dataset-a")

    assert isinstance(rows, list)
    item = next(row for row in rows if row["id"] == "doc-a1")
    assert item["tags"] == ["员工", "制度"]
    assert item["logical_folder_path"] == "制度/人力"
    assert item["parser_meta"]["engine"] == "vision"


def test_list_documents_page_is_tenant_scoped_and_stably_sorted(catalog_engine) -> None:
    page = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        offset=0,
        limit=5,
        query="",
        status="all",
        doc_type="all",
        engine="all",
        folder="all",
        folder_mode="exact",
        tag="all",
        lifecycle_state="all",
        sort="updated_at_desc",
    )

    assert page["total"] == 6
    assert [item["id"] for item in page["items"]] == [
        "doc-a1",
        "doc-a2",
        "doc-a3",
        "doc-a4",
        "doc-a5",
    ]
    assert page["items"][-1]["id"] != "doc-b1"
    second = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        offset=5,
        limit=5,
        sort="updated_at_desc",
    )
    assert [item["id"] for item in second["items"]] == ["doc-a0"]
    assert page["tag_authority"] == "legacy_projection"


def test_list_documents_page_supports_combined_enterprise_filters(catalog_engine) -> None:
    page = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        offset=0,
        limit=20,
        query="薪酬",
        status="processing",
        doc_type="docx",
        engine="mineru",
        folder="制度/人力",
        folder_mode="subtree",
        tag="制度",
        lifecycle_state="active",
        sort="name_asc",
    )

    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == ["doc-a2"]
    assert page["items"][0]["tags"] == ["薪酬", "制度"]


def test_list_documents_page_exact_folder_and_unknown_engine(catalog_engine) -> None:
    exact = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        offset=0,
        limit=20,
        folder="制度",
        folder_mode="exact",
        engine="unknown",
        lifecycle_state="expired",
    )

    assert exact["total"] == 1
    assert exact["items"][0]["id"] == "doc-a4"
    assert exact["items"][0]["parser_meta"] == {}


def test_summarize_documents_builds_authoritative_counts_facets_and_recent(catalog_engine) -> None:
    result = catalog.summarize_documents("tenant-a", "dataset-a", recent_limit=3)

    assert result["dataset_id"] == "dataset-a"
    assert result["tag_authority"] == "legacy_projection"
    assert result["summary"] == {
        "total": 6,
        "completed": 3,
        "processing": 2,
        "failed": 1,
        "chunks": 34,
        "parser_observed": 5,
        "parser_coverage": 83,
    }
    assert result["facets"]["statuses"] == {
        "all": 6,
        "waiting": 1,
        "parsing": 1,
        "splitting": 0,
        "indexing": 0,
        "processing": 2,
        "completed": 3,
        "error": 1,
    }
    assert result["facets"]["types"] == [
        {"value": "pdf", "count": 3},
        {"value": "docx", "count": 1},
        {"value": "md", "count": 1},
        {"value": "txt", "count": 1},
    ]
    assert result["facets"]["engines"] == [
        {"value": "vision", "count": 3},
        {"value": "mineru", "count": 2},
        {"value": "unknown", "count": 1},
    ]
    folders = {item["path"]: item for item in result["facets"]["folders"]}
    assert folders["制度/人力"] == {"path": "制度/人力", "documents": 3, "chunks": 27}
    tags = {item["name"]: item for item in result["facets"]["tags"]}
    assert tags["制度"] == {"name": "制度", "documents": 4, "chunks": 27}
    assert [item["id"] for item in result["recent"]] == ["doc-a1", "doc-a2", "doc-a3"]
    assert result["generated_at"].endswith("Z")


def test_document_catalog_api_requires_actor_and_enforces_limit(api) -> None:
    client, settings = api

    missing = client.get("/api/knowledge-bases/dataset-a/documents")
    assert missing.status_code == 401

    invalid = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=_headers(settings, "member-a"),
        params={"limit": 101},
    )
    assert invalid.status_code == 422


def test_document_catalog_api_applies_filters_and_returns_summary(api) -> None:
    client, settings = api
    headers = _headers(settings, "member-a")

    page = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=headers,
        params={
            "offset": 0,
            "limit": 20,
            "status": "processing",
            "folder": "制度/人力",
            "folder_mode": "subtree",
            "tag": "制度",
            "sort": "name_asc",
        },
    )
    assert page.status_code == 200
    assert [item["id"] for item in page.json()["items"]] == ["doc-a2"]
    assert page.json()["tag_authority"] == "legacy_projection"

    summary = client.get(
        "/api/knowledge-bases/dataset-a/documents/summary",
        headers=headers,
        params={"recent_limit": 2},
    )
    assert summary.status_code == 200
    assert summary.json()["summary"]["total"] == 6
    assert len(summary.json()["recent"]) == 2
    assert summary.json()["tag_authority"] == "legacy_projection"


def test_document_catalog_api_rejects_cross_tenant_dataset(api) -> None:
    client, settings = api
    response = client.get(
        "/api/knowledge-bases/dataset-b/documents",
        headers=_headers(settings, "member-a", tenant="tenant-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_dataset_scope_forbidden"


def test_main_application_mounts_document_catalog_routes() -> None:
    from server.app import app

    paths = app.openapi()["paths"]
    assert "/api/knowledge-bases/{dataset_id}/documents" in paths
    assert "/api/knowledge-bases/{dataset_id}/documents/summary" in paths


def test_tag_filter_uses_sql_json_membership_and_bounded_rows(
    catalog_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(catalog_engine, "before_cursor_execute", capture_sql)

    def fail_orm_scalars(*args, **kwargs):
        raise AssertionError("tag filtering must not materialize an ORM document collection")

    monkeypatch.setattr(Session, "scalars", fail_orm_scalars)
    try:
        page = catalog.list_documents_page(
            "tenant-a",
            "dataset-a",
            tag="制度",
            limit=1,
        )
    finally:
        event.remove(catalog_engine, "before_cursor_execute", capture_sql)

    assert page["total"] == 4
    assert len(page["items"]) == 1
    assert any("json_each" in statement for statement in statements)
    assert any("from documents" in statement and "limit" in statement for statement in statements)


def test_summary_uses_sql_aggregates_and_a_limited_projection_for_recent(
    catalog_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(catalog_engine, "before_cursor_execute", capture_sql)

    def fail_orm_scalars(*args, **kwargs):
        raise AssertionError("summary must not load Document ORM objects")

    monkeypatch.setattr(Session, "scalars", fail_orm_scalars)
    try:
        result = catalog.summarize_documents("tenant-a", "dataset-a", recent_limit=2)
    finally:
        event.remove(catalog_engine, "before_cursor_execute", capture_sql)

    assert result["summary"]["total"] == 6
    assert len(result["recent"]) == 2
    assert any("count(" in statement for statement in statements)
    assert any("group by" in statement for statement in statements)
    assert any("from documents" in statement and "limit" in statement for statement in statements)
    assert result["tag_facets_complete"] is True
    assert result["tag_facets_scanned"] == 6


def test_summary_tag_facets_have_a_hard_scan_limit(catalog_engine) -> None:
    with Session(catalog_engine) as session:
        session.add_all(
            [
                _document(
                    f"bounded-{index:04d}",
                    name=f"Bounded {index:04d}.txt",
                    tags=["bounded"],
                    chunks=1,
                    updated_offset=200 + index,
                )
                for index in range(1005)
            ]
        )
        session.commit()

    result = catalog.summarize_documents("tenant-a", "dataset-a")

    assert result["summary"]["total"] == 1011
    assert result["tag_facets_complete"] is False
    assert result["tag_facets_scan_limit"] == 1000
    assert result["tag_facets_scanned"] == 1000
    assert result["tag_facets_truncated"] is True


def test_updated_at_cursor_is_opaque_and_survives_a_concurrent_insert(catalog_engine) -> None:
    first = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        limit=2,
        sort="updated_at_desc",
    )

    assert isinstance(first["next_cursor"], str)
    assert first["next_cursor"]
    assert "updated_at" not in first["next_cursor"]

    with Session(catalog_engine) as session:
        session.add(
            _document(
                "doc-newer",
                name="后来插入.pdf",
                tags=["制度"],
                chunks=1,
                updated_offset=1000,
            )
        )
        session.commit()

    second = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        limit=2,
        sort="updated_at_desc",
        cursor=first["next_cursor"],
    )

    assert [item["id"] for item in first["items"]] == ["doc-a1", "doc-a2"]
    assert [item["id"] for item in second["items"]] == ["doc-a3", "doc-a4"]
    assert "doc-newer" not in [item["id"] for item in second["items"]]


def test_catalog_rejects_unbounded_offsets_and_invalid_cursor_combinations(catalog_engine) -> None:
    with pytest.raises(ValueError, match="offset"):
        catalog.list_documents_page(
            "tenant-a",
            "dataset-a",
            offset=1_000_001,
        )

    with pytest.raises(ValueError, match="cursor"):
        catalog.list_documents_page(
            "tenant-a",
            "dataset-a",
            offset=1,
            cursor="opaque-cursor",
        )

    with pytest.raises(ValueError, match="recent_limit"):
        catalog.summarize_documents("tenant-a", "dataset-a", recent_limit=101)


def test_document_catalog_api_accepts_keyset_cursor_and_rejects_deep_offset(api) -> None:
    client, settings = api
    headers = _headers(settings, "member-a")

    first = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=headers,
        params={"limit": 2},
    )
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor

    second = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=headers,
        params={"limit": 2, "cursor": cursor},
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["doc-a3", "doc-a4"]

    mixed = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=headers,
        params={"offset": 1, "limit": 2, "cursor": cursor},
    )
    assert mixed.status_code == 422

    deep = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=headers,
        params={"offset": 1_000_001},
    )
    assert deep.status_code == 422


def test_document_catalog_api_maps_unsupported_catalog_capability_to_422(
    api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings = api

    def unsupported(*args, **kwargs):
        raise catalog.DocumentCatalogCapabilityError("legacy tag authority is unavailable")

    monkeypatch.setattr(catalog, "list_documents_page", unsupported)
    response = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=_headers(settings, "member-a"),
        params={"tag": "制度"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "document_catalog_capability_unavailable"



def test_document_catalog_router_uses_explicit_engine_without_global_catalog_side_effects(
    catalog_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = catalog_engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    monkeypatch.setattr(
        catalog,
        "get_engine",
        lambda: (_ for _ in ()).throw(AssertionError("global catalog engine used")),
    )
    client = TestClient(app, client=("10.0.0.2", 50000))

    response = client.get(
        "/api/knowledge-bases/dataset-a/documents",
        headers=_headers(settings, "member-a"),
        params={"limit": 2},
    )

    assert response.status_code == 200
    assert response.json()["items"]


def test_keyset_cursor_is_bound_to_tenant_dataset_filters_and_limit(catalog_engine) -> None:
    first = catalog.list_documents_page(
        "tenant-a",
        "dataset-a",
        limit=2,
        status="all",
        engine="all",
        folder="all",
        tag="all",
        sort="updated_at_desc",
        engine_override=catalog_engine,
    )
    cursor = first["next_cursor"]
    assert cursor

    with pytest.raises(ValueError, match="cursor.*query"):
        catalog.list_documents_page(
            "tenant-a",
            "dataset-a",
            limit=2,
            query="different-filter",
            cursor=cursor,
            engine_override=catalog_engine,
        )

    with pytest.raises(ValueError, match="cursor.*query"):
        catalog.list_documents_page(
            "tenant-a",
            "dataset-a",
            limit=3,
            cursor=cursor,
            engine_override=catalog_engine,
        )

