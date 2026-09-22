from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models.orm import Dataset, Document, Tenant


def chunk_module() -> ModuleType:
    try:
        return importlib.import_module("core.chunk_catalog")
    except ModuleNotFoundError:
        pytest.fail("core.chunk_catalog is missing")


def create_catalog(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                content_revision=1,
                desired_index_revision=1,
            )
        )
        session.commit()
    return engine, chunk_module().ChunkCatalog(engine)


def create_head(
    catalog,
    chunk_id: str,
    *,
    role: str = "child",
    enabled: bool = True,
    chunk_index: int = 0,
):
    return catalog.upsert_head(
        chunk_id=chunk_id,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id="parent-1" if role == "child" else None,
        chunk_index=chunk_index,
        chunk_role=role,
        document_revision=1,
        source_content="original text",
        content="original text",
        content_hash="hash-original",
        enabled=enabled,
        metadata={"section": "intro"},
    )


def test_chunk_edit_is_optimistic_and_preserves_superseded_revision(tmp_path: Path) -> None:
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "chunk-1")

    edited = catalog.edit_chunk(
        "chunk-1",
        expected_revision=0,
        content="edited text",
        editor_id="user-1",
        edit_source="user",
    )

    assert edited.content_revision == 1
    assert edited.content == "edited text"
    revisions = catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1")
    assert [(item.revision, item.content) for item in revisions] == [(0, "original text")]
    with pytest.raises(chunk_module().ChunkRevisionConflict, match="revision conflict"):
        catalog.edit_chunk(
            "chunk-1",
            expected_revision=0,
            content="stale write",
            editor_id="user-2",
        )
    engine.dispose()


def test_revert_creates_a_new_head_revision_instead_of_rewinding(tmp_path: Path) -> None:
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "chunk-1")
    catalog.edit_chunk(
        "chunk-1", expected_revision=0, content="edited text", editor_id="user-1"
    )

    reverted = catalog.revert_chunk(
        "chunk-1",
        target_revision=0,
        expected_revision=1,
        editor_id="user-2",
    )

    assert reverted.content_revision == 2
    assert reverted.content == "original text"
    assert [(item.revision, item.content) for item in catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1")] == [
        (0, "original text"),
        (1, "edited text"),
    ]
    engine.dispose()


def test_list_revisions_is_scoped_to_its_tenant_dataset_and_document(
    tmp_path: Path,
) -> None:
    """Chunk ids are caller-supplied, so revision reads must not be keyed by id alone.

    Removing any one of the three scope columns from the lookup turns this red: the
    negative assertions each isolate one column (other tenant / other dataset / other
    document) while the chunk_id still matches.
    """
    engine, catalog = create_catalog(tmp_path)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-2", name="Other"))
        session.add(Dataset(id="dataset-2", tenant_id="tenant-2", name="Other KB"))
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-2",
                dataset_id="dataset-2",
                name="Other Document",
                content_revision=1,
                desired_index_revision=1,
            )
        )
        session.add(
            Document(
                id="doc-1b",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Sibling Document",
                content_revision=1,
                desired_index_revision=1,
            )
        )
        session.commit()

    def head(chunk_id: str, *, tenant: str, dataset: str, document: str, text: str):
        return catalog.upsert_head(
            chunk_id=chunk_id,
            tenant_id=tenant,
            dataset_id=dataset,
            document_id=document,
            parent_chunk_id=None,
            chunk_index=0,
            chunk_role="flat",
            document_revision=1,
            source_content=text,
            content=text,
            content_hash=f"hash-{chunk_id}",
            enabled=True,
        )

    head("shared-id", tenant="tenant-1", dataset="dataset-1", document="doc-1", text="mine")
    head("other-id", tenant="tenant-2", dataset="dataset-2", document="doc-2", text="theirs")
    for owner in ("shared-id", "other-id"):
        catalog.edit_chunk(
            owner, expected_revision=0, content=f"{owner} edited", editor_id="user-1"
        )

    mine = catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "shared-id")
    assert [(item.revision, item.content) for item in mine] == [(0, "mine")]

    # Same chunk_id, foreign scope on exactly one column each.
    assert catalog.list_revisions("tenant-2", "dataset-1", "doc-1", "shared-id") == []
    assert catalog.list_revisions("tenant-1", "dataset-2", "doc-1", "shared-id") == []
    assert catalog.list_revisions("tenant-1", "dataset-1", "doc-1b", "shared-id") == []
    # An attacker knowing only a chunk id learns nothing about another tenant's content.
    assert catalog.list_revisions("tenant-2", "dataset-2", "doc-2", "shared-id") == []
    engine.dispose()


def test_revert_rejects_a_revision_number_that_was_never_recorded(
    tmp_path: Path,
) -> None:
    """A nonexistent target revision must not roll the head back to blank content.

    Scope-foreign revisions are covered at the endpoint level
    (``tests/test_knowledge_chunks_api.py``), because the production path is
    ``server.chunk_operations._write_revert`` filtering by the head's own scope.
    """
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "chunk-1")
    catalog.edit_chunk(
        "chunk-1", expected_revision=0, content="edited text", editor_id="user-1"
    )

    with pytest.raises(chunk_module().ChunkRevisionConflict, match="does not exist"):
        catalog.revert_chunk(
            "chunk-1", target_revision=99, expected_revision=1, editor_id="user-2"
        )
    engine.dispose()


def test_tombstone_keeps_the_operator_reason_on_the_head(tmp_path: Path) -> None:
    """Disabling a chunk is an auditable action, so its reason must survive.

    Without this, ``PATCH {enabled: false, reason: "…"}`` answers 200 and silently
    drops the only reason the operator was asked to type.
    """
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "chunk-1")

    disabled = catalog.tombstone_chunk(
        "chunk-1",
        expected_revision=0,
        editor_id="user-1",
        metadata_patch={"edit_reason": "条款已废止", "edit_reason_by": "user-1"},
    )

    assert disabled.enabled is False
    assert disabled.chunk_metadata["edit_reason"] == "条款已废止"
    assert disabled.edit_source == "delete"
    engine.dispose()


def test_projection_candidates_exclude_parent_and_disabled_chunks(tmp_path: Path) -> None:
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "parent", role="parent", chunk_index=0)
    create_head(catalog, "child", role="child", chunk_index=1)
    create_head(catalog, "disabled", role="flat", enabled=False, chunk_index=2)

    candidates = catalog.list_projection_candidates("doc-1", document_revision=1)

    assert [item.id for item in candidates] == ["child"]
    engine.dispose()


def test_stale_enrichment_cannot_overwrite_newer_chunk_revision(tmp_path: Path) -> None:
    engine, catalog = create_catalog(tmp_path)
    create_head(catalog, "chunk-1")
    assert catalog.update_context_header(
        "chunk-1", input_revision=0, context_header="context v0"
    )
    catalog.edit_chunk(
        "chunk-1", expected_revision=0, content="edited text", editor_id="user-1"
    )

    accepted = catalog.update_context_header(
        "chunk-1", input_revision=0, context_header="stale context"
    )
    current = catalog.get_head("chunk-1")

    assert accepted is False
    assert current.context_header == "context v0"
    engine.dispose()
