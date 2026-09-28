from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest


from core import catalog
from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from indexing.state_machine import DocumentIngestJob


class FakeChunk:
    def __init__(self, chunk_id: str = "chunk-1") -> None:
        self.chunk_id = chunk_id
        self.doc_id = "doc"
        self.text = f"content for {chunk_id}"
        self.text_hash = f"hash-{chunk_id}"
        self.parent_chunk_id = None
        self.metadata = {"chunk_index": 0}
        self.tenant_id = "tenant-1"
        self.dataset_id = "dataset-1"
        self.document_revision = 0
        self.content_revision = 0


class RecordingQueue:
    def __init__(self, queue: IndexOperationQueue) -> None:
        self._queue = queue
        self.enqueued_target_stores: list[str] = []

    def enqueue_operation(self, **kwargs):
        self.enqueued_target_stores.append(str(kwargs["target_store"]))
        return self._queue.enqueue_operation(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self._queue, name)


class SuccessfulPipeline:
    def __init__(self, *, graph_enabled: bool = False) -> None:
        self.graph_enabled = graph_enabled
        self.graph_builder = object() if graph_enabled else None

    def parse_and_chunk(self, *args, progress=None, **kwargs):
        if progress is not None:
            progress("parsing", 1.0, "parsed")
            progress("splitting", 1.0, "split")
        return [FakeChunk()]

    def embed_and_insert(self, chunks, *args, progress=None, **kwargs):
        if progress is not None:
            progress("indexing", 1.0, "indexed")
        return SimpleNamespace(chunk_count=len(chunks), graph=None)

    def ingest_meta(self, _result):
        return {}


def configure_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify")
    catalog.ensure_tenant("tenant-1", "Tenant")
    catalog.ensure_dataset("tenant-1", "dataset-1", "KB")
    document = catalog.create_document(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        name="Document",
        file_path=str(tmp_path / "document.md"),
    )
    return catalog.get_engine(), document


def run_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    graph_enabled: bool = False,
    ledger_mode: str = "shadow",
):
    engine, document = configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    queue = RecordingQueue(IndexOperationQueue(engine))
    job = DocumentIngestJob(
        SuccessfulPipeline(graph_enabled=graph_enabled),
        document["id"],
        dataset_id="dataset-1",
        ledger=IngestLedger(engine),
        operation_queue=queue,
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode=ledger_mode,
    )
    result = job.run(str(file_path))
    return engine, document, queue, result


def test_ingest_producer_registry_preserves_builtin_store_order_and_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document, queue, result = run_ingest(tmp_path, monkeypatch, graph_enabled=True)
    try:
        operations = queue.list_operations()
        assert result == {"status": "completed", "chunk_count": 1}
        assert queue.enqueued_target_stores == ["milvus_chunks", "graph_projection"]
        assert {item.target_store for item in operations} == {
            "milvus_chunks",
            "graph_projection",
        }
        assert {item.target_store: item.dedup_key for item in operations} == {
            "milvus_chunks": f"{document['id']}:1:milvus:upsert",
            "graph_projection": f"{document['id']}:1:graph:upsert",
        }
        assert {item.status for item in operations} == {"shadow"}
    finally:
        engine.dispose()
        catalog.reset_engine()


def test_incomplete_new_projection_target_is_rejected_before_enqueue_without_host_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.projection_target_producers import (
        ProjectionTargetPolicy,
        register_projection_target_policy,
        unregister_projection_target_policy,
    )

    host = Path("indexing/state_machine.py")
    host_bytes = host.read_bytes()
    register_projection_target_policy(
        "custom_vector",
        ProjectionTargetPolicy(
            order=30,
            dedup_key_component="custom_vector",
            enabled=lambda _context: True,
        ),
    )
    engine = None
    try:
        engine, document, queue, result = run_ingest(tmp_path, monkeypatch, ledger_mode="active")
        operations = queue.list_operations()
        assert result["status"] == "error"
        assert "custom_vector" in result["error"]
        assert queue.enqueued_target_stores == []
        assert operations == []
        assert catalog.get_document(document["id"])["status"] == "error"
        assert host.read_bytes() == host_bytes
    finally:
        unregister_projection_target_policy("custom_vector")
        if engine is not None:
            engine.dispose()
        catalog.reset_engine()


def test_ingest_target_selection_has_no_builtin_store_branches() -> None:
    source = inspect.getsource(DocumentIngestJob._enqueue_projection_intent)
    preflight = inspect.getsource(DocumentIngestJob._persist_authority_and_enqueue)

    assert '"milvus_chunks"' not in source
    assert '"graph_projection"' not in source
    assert "if include_graph" not in source
    assert "projection_targets" in preflight
    assert "validate_projection_target_runtime" in preflight


def test_target_runtime_preflight_requires_worker_revision_lifecycle_and_delete_support() -> None:
    from core.projection_attempt_lifecycle import (
        ProjectionAttemptLifecyclePolicy,
        register_projection_attempt_lifecycle_policy,
        unregister_projection_attempt_lifecycle_policy,
    )
    from core.projection_revision_strategies import (
        register_projection_revision_strategy,
        unregister_projection_revision_strategy,
    )
    from indexing.projection_handlers import (
        register_projection_operation,
        unregister_projection_operation,
    )
    from indexing.projection_target_runtime import (
        IncompleteProjectionTargetRuntime,
        validate_projection_target_runtime,
    )

    target_store = "custom_vector"
    operations = ("upsert", "reconcile", "delete", "delete_document")
    for operation in operations:
        register_projection_operation(target_store, operation, lambda _context: None)
    register_projection_revision_strategy(target_store, lambda _context: None)
    register_projection_attempt_lifecycle_policy(
        target_store,
        ProjectionAttemptLifecyclePolicy(
            role="primary",
            blocks_finalization=False,
            is_ready=lambda _context: True,
        ),
    )
    try:
        with pytest.raises(
            IncompleteProjectionTargetRuntime,
            match="not covered by durable document deletion",
        ):
            validate_projection_target_runtime(target_store)
    finally:
        for operation in operations:
            unregister_projection_operation(target_store, operation)
        unregister_projection_revision_strategy(target_store)
        unregister_projection_attempt_lifecycle_policy(target_store)


def test_target_runtime_preflight_requires_consistency_api_operation_coverage() -> None:
    from core.projection_attempt_lifecycle import (
        ProjectionAttemptLifecyclePolicy,
        register_projection_attempt_lifecycle_policy,
        unregister_projection_attempt_lifecycle_policy,
    )
    from core.projection_revision_strategies import (
        register_projection_revision_strategy,
        unregister_projection_revision_strategy,
    )
    from indexing.projection_target_runtime import (
        IncompleteProjectionTargetRuntime,
        validate_projection_target_runtime,
    )
    from indexing.projection_handlers import (
        register_projection_operation,
        unregister_projection_operation,
    )

    target_store = "custom_vector"
    operations = ("upsert", "reconcile", "delete", "delete_document")
    for operation in operations:
        register_projection_operation(target_store, operation, lambda _context: None)
    register_projection_revision_strategy(target_store, lambda _context: None)
    register_projection_attempt_lifecycle_policy(
        target_store,
        ProjectionAttemptLifecyclePolicy(
            role="primary",
            blocks_finalization=False,
            is_ready=lambda _context: True,
        ),
    )
    try:
        with pytest.raises(
            IncompleteProjectionTargetRuntime,
            match="missing consistency operation coverage",
        ):
            validate_projection_target_runtime(target_store, require_delete_target=False)
    finally:
        for operation in operations:
            unregister_projection_operation(target_store, operation)
        unregister_projection_revision_strategy(target_store)
        unregister_projection_attempt_lifecycle_policy(target_store)


def test_projection_target_policy_registration_rejects_duplicates_and_bad_shapes() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        ProjectionTargetPolicy,
        register_projection_target_policy,
    )

    with pytest.raises(InvalidProjectionTarget, match="built-in.*immutable"):
        register_projection_target_policy(
            "milvus_chunks",
            ProjectionTargetPolicy(
                order=1,
                dedup_key_component="milvus",
                enabled=lambda _context: True,
            ),
        )
    with pytest.raises(TypeError, match="one positional context"):
        register_projection_target_policy(
            "custom_vector",
            ProjectionTargetPolicy(
                order=30,
                dedup_key_component="custom_vector",
                enabled=lambda: True,
            ),
        )
    with pytest.raises(InvalidProjectionTarget, match="reserved"):
        register_projection_target_policy(
            "custom_vector",
            ProjectionTargetPolicy(
                order=30,
                dedup_key_component="milvus",
                enabled=lambda _context: True,
            ),
        )
    with pytest.raises(ValueError, match="lowercase code"):
        register_projection_target_policy(
            "CustomVector",
            ProjectionTargetPolicy(
                order=30,
                dedup_key_component="custom_vector",
                enabled=lambda _context: True,
            ),
        )


def test_projection_target_selection_rejects_reserved_alias_from_registry() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    PROJECTION_TARGET_PRODUCERS.register(
        "milvus",
        lambda _context: ProjectionTargetPolicy(
            order=5,
            dedup_key_component="milvus",
            enabled=lambda _production_context: True,
        ),
    )
    try:
        with pytest.raises(InvalidProjectionTarget, match="reserved"):
            projection_targets(ProjectionTargetProductionContext(include_graph=False))
    finally:
        PROJECTION_TARGET_PRODUCERS.unregister("milvus")


def test_unregistered_target_component_cannot_be_reused_by_another_store() -> None:
    from core.projection_target_producers import (
        ProjectionTargetPolicy,
        register_projection_target_policy,
        unregister_projection_target_policy,
    )

    register_projection_target_policy(
        "old_vector",
        ProjectionTargetPolicy(
            order=30,
            dedup_key_component="old_vector",
            enabled=lambda _context: True,
        ),
    )
    unregister_projection_target_policy("old_vector")

    with pytest.raises(ValueError, match="must match target_store"):
        register_projection_target_policy(
            "new_vector",
            ProjectionTargetPolicy(
                order=30,
                dedup_key_component="old_vector",
                enabled=lambda _context: True,
            ),
        )


def test_projection_target_selection_rejects_non_string_component_from_registry() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    class AliasComponent(str):
        def __new__(cls) -> AliasComponent:
            return super().__new__(cls, "bypassed_component")

        def __format__(self, _format_spec: str) -> str:
            return "milvus"

    PROJECTION_TARGET_PRODUCERS.register(
        "bypassed_component",
        lambda _context: ProjectionTargetPolicy(
            order=5,
            dedup_key_component=AliasComponent(),
            enabled=lambda _production_context: True,
        ),
    )
    try:
        with pytest.raises(InvalidProjectionTarget, match="dedup key component"):
            projection_targets(ProjectionTargetProductionContext(include_graph=False))
    finally:
        PROJECTION_TARGET_PRODUCERS.unregister("bypassed_component")


def test_builtin_target_policies_cannot_be_unregistered() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        unregister_projection_target_policy,
    )

    with pytest.raises(InvalidProjectionTarget, match="cannot be unregistered"):
        unregister_projection_target_policy("milvus_chunks")


def test_projection_target_selection_fails_closed_when_builtin_is_removed_directly() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    original = PROJECTION_TARGET_PRODUCERS.create("milvus_chunks", None)
    PROJECTION_TARGET_PRODUCERS.unregister("milvus_chunks")
    try:
        with pytest.raises(InvalidProjectionTarget, match="required built-in"):
            projection_targets(ProjectionTargetProductionContext(include_graph=False))
    finally:
        PROJECTION_TARGET_PRODUCERS.register("milvus_chunks", lambda _context: original)


def test_builtin_projection_target_policies_cannot_be_replaced() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        register_projection_target_policy,
    )

    original = PROJECTION_TARGET_PRODUCERS.create("milvus_chunks", None)
    try:
        with pytest.raises(InvalidProjectionTarget, match="built-in.*immutable"):
            register_projection_target_policy(
                "milvus_chunks",
                ProjectionTargetPolicy(
                    order=10,
                    dedup_key_component="milvus",
                    enabled=lambda _context: False,
                ),
                replace=True,
            )
    finally:
        PROJECTION_TARGET_PRODUCERS.register(
            "milvus_chunks", lambda _context: original, replace=True
        )


def test_direct_builtin_registry_replacement_fails_closed() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    original = PROJECTION_TARGET_PRODUCERS.create("milvus_chunks", None)
    PROJECTION_TARGET_PRODUCERS.register(
        "milvus_chunks",
        lambda _context: ProjectionTargetPolicy(
            order=10,
            dedup_key_component="milvus",
            enabled=lambda _production_context: False,
        ),
        replace=True,
    )
    try:
        with pytest.raises(InvalidProjectionTarget, match="was replaced"):
            projection_targets(ProjectionTargetProductionContext(include_graph=False))
    finally:
        PROJECTION_TARGET_PRODUCERS.register(
            "milvus_chunks", lambda _context: original, replace=True
        )


def test_duplicate_custom_target_registration_is_rejected() -> None:
    from core.projection_target_producers import (
        ProjectionTargetPolicy,
        register_projection_target_policy,
        unregister_projection_target_policy,
    )

    policy = ProjectionTargetPolicy(
        order=30,
        dedup_key_component="duplicate_vector",
        enabled=lambda _context: True,
    )
    register_projection_target_policy("duplicate_vector", policy)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_projection_target_policy("duplicate_vector", policy)
    finally:
        unregister_projection_target_policy("duplicate_vector")


def test_builtin_policy_runtime_guard_uses_callback_identity_not_custom_equality() -> None:
    from core.projection_target_producers import (
        InvalidProjectionTarget,
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    class AlwaysEqualDisabled:
        def __call__(self, _context) -> bool:
            return False

        def __eq__(self, _other) -> bool:
            return True

    original = PROJECTION_TARGET_PRODUCERS.create("milvus_chunks", None)
    PROJECTION_TARGET_PRODUCERS.register(
        "milvus_chunks",
        lambda _context: ProjectionTargetPolicy(
            order=10,
            dedup_key_component="milvus",
            enabled=AlwaysEqualDisabled(),  # type: ignore[arg-type]
        ),
        replace=True,
    )
    try:
        with pytest.raises(InvalidProjectionTarget, match="was replaced"):
            projection_targets(ProjectionTargetProductionContext(include_graph=False))
    finally:
        PROJECTION_TARGET_PRODUCERS.register(
            "milvus_chunks", lambda _context: original, replace=True
        )


def test_projection_targets_resolve_each_registry_factory_only_once() -> None:
    from core.projection_target_producers import (
        PROJECTION_TARGET_PRODUCERS,
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
    )

    original = PROJECTION_TARGET_PRODUCERS.create("milvus_chunks", None)
    calls = 0

    def stateful_factory(_context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original
        return ProjectionTargetPolicy(
            order=10,
            dedup_key_component="milvus",
            enabled=lambda _production_context: False,
        )

    PROJECTION_TARGET_PRODUCERS.register("milvus_chunks", stateful_factory, replace=True)
    try:
        targets = projection_targets(ProjectionTargetProductionContext(include_graph=False))
        assert calls == 1
        assert [target.target_store for target in targets] == ["milvus_chunks"]
    finally:
        PROJECTION_TARGET_PRODUCERS.register(
            "milvus_chunks", lambda _context: original, replace=True
        )
