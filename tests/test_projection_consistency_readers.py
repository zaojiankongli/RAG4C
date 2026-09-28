from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from config.settings import MilvusSettings
from core import projection_consistency_fences as fences
from core import projection_consistency_readers as readers
from core.providers import UnknownProviderError
from core.milvus_client import RagMilvusClient
from core.projection_consistency_fences import (
    ProjectionConsistencyFenceContext,
    ProjectionConsistencyFenceObservation,
    register_projection_consistency_fence,
    resolve_projection_consistency_fence,
    unregister_projection_consistency_fence,
)
from core.projection_consistency_readers import (
    ProjectionConsistencyReadRequest,
    ProjectionConsistencyReadResult,
    ProjectionConsistencyReaderContext,
    projection_consistency_reader_names,
    read_projection_document,
    register_projection_consistency_reader,
    resolve_projection_consistency_reader,
    unregister_projection_consistency_reader,
)
from models.schemas import Chunk


def _request(
    *,
    target_store: str = "custom_chunks",
    tenant_id: str = "tenant-a",
    dataset_id: str = "dataset-a",
    document_id: str = "doc-a",
) -> ProjectionConsistencyReadRequest:
    return ProjectionConsistencyReadRequest(
        target_store=target_store,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
    )


def _chunk(chunk_id: str, *, document_id: str = "doc-a") -> Chunk:
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=document_id,
        text="projection content",
        text_hash="f" * 64,
        created_at=now,
        updated_at=now,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )


class _Reader:
    def __init__(self, chunks: tuple[Chunk, ...] = ()) -> None:
        self.chunks = chunks
        self.requests: list[ProjectionConsistencyReadRequest] = []

    def read_document(
        self, request: ProjectionConsistencyReadRequest
    ) -> ProjectionConsistencyReadResult:
        self.requests.append(request)
        return ProjectionConsistencyReadResult(
            target_store=request.target_store,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            document_id=request.document_id,
            chunks=self.chunks,
            completeness="best_effort",
        )


def test_builtin_milvus_reader_is_reserved_and_graph_has_no_fallback() -> None:
    assert projection_consistency_reader_names() == ("milvus_chunks",)
    with pytest.raises(ValueError, match="built-in.*reserved"):
        register_projection_consistency_reader("milvus_chunks", lambda _context: _Reader())
    with pytest.raises(ValueError, match="built-in.*cannot be unregistered"):
        unregister_projection_consistency_reader("milvus_chunks")

    with pytest.raises(UnknownProviderError, match="projection consistency reader"):
        resolve_projection_consistency_reader("graph_projection", backend=object())
    with pytest.raises(ValueError, match="intentionally unsupported"):
        register_projection_consistency_reader("graph_projection", lambda _context: _Reader())

    registry = readers._PROJECTION_CONSISTENCY_READERS
    builtin_factory = registry.get_factory("milvus_chunks")
    registry.register(
        "milvus_chunks",
        lambda _context: readers._ReaderPolicy(factory=lambda _factory_context: _Reader()),
        replace=True,
    )
    try:
        with pytest.raises(TypeError, match="built-in.*contract was replaced"):
            resolve_projection_consistency_reader("milvus_chunks", backend=object())
    finally:
        registry.register("milvus_chunks", builtin_factory, replace=True)

    registry.register(
        "graph_projection",
        lambda _context: readers._ReaderPolicy(factory=lambda _factory_context: _Reader()),
    )
    try:
        with pytest.raises(UnknownProviderError, match="not chunk-shaped"):
            resolve_projection_consistency_reader("graph_projection", backend=object())
    finally:
        registry.unregister("graph_projection")


def test_custom_reader_is_constructed_for_exact_target_and_reads_scoped_records() -> None:
    target = "custom_chunks"
    backend = object()
    reader = _Reader((_chunk("chunk-a"),))
    seen_contexts: list[ProjectionConsistencyReaderContext] = []

    def factory(context: ProjectionConsistencyReaderContext) -> _Reader:
        seen_contexts.append(context)
        return reader

    register_projection_consistency_reader(target, factory)
    try:
        resolved = resolve_projection_consistency_reader(target, backend=backend)
        request = _request(target_store=target)
        result = read_projection_document(resolved, request)

        assert resolved is reader
        assert seen_contexts == [
            ProjectionConsistencyReaderContext(target_store=target, backend=backend)
        ]
        assert reader.requests == [request]
        assert result.chunks[0].chunk_id == "chunk-a"
        assert target in projection_consistency_reader_names()
    finally:
        unregister_projection_consistency_reader(target)


def test_reader_snapshot_token_is_not_trusted_without_a_registered_fence() -> None:
    class TokenReader:
        def read_document(
            self, request: ProjectionConsistencyReadRequest
        ) -> ProjectionConsistencyReadResult:
            return ProjectionConsistencyReadResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                document_id=request.document_id,
                chunks=(_chunk("chunk-a"),),
                completeness="best_effort",
                snapshot_token="reader-asserted-token",
            )

    result = read_projection_document(TokenReader(), _request())

    assert result.snapshot_token is None
    assert result.fence_status == "unfenced"
    assert result.completeness == "best_effort"


def test_milvus_is_explicitly_reserved_as_unfenced() -> None:
    with pytest.raises(ValueError, match="intentionally unfenced.*reserved"):
        register_projection_consistency_fence("milvus_chunks", lambda _context: object())
    with pytest.raises(ValueError, match="intentionally unfenced.*reserved"):
        unregister_projection_consistency_fence("milvus_chunks")

    registry = fences._PROJECTION_CONSISTENCY_FENCES
    registry.register(
        "milvus_chunks",
        lambda _context: (_ for _ in ()).throw(
            AssertionError("reserved Milvus fence must never be constructed")
        ),
    )
    try:
        assert resolve_projection_consistency_fence("milvus_chunks", backend=object()) is None
    finally:
        registry.unregister("milvus_chunks")


def test_registered_generation_fence_marks_stable_and_changed_reads_explicitly() -> None:
    target = "fenced_chunks"
    backend = SimpleNamespace(generation=7, change_before_finish=False)

    class GenerationFence:
        def __init__(self, state: SimpleNamespace) -> None:
            self.state = state

        def begin(self, _request: ProjectionConsistencyReadRequest) -> int:
            return self.state.generation

        def finish(
            self,
            _request: ProjectionConsistencyReadRequest,
            begin_token: int,
            _read_result: ProjectionConsistencyReadResult,
        ) -> ProjectionConsistencyFenceObservation:
            if self.state.change_before_finish:
                self.state.generation += 1
            if begin_token != self.state.generation:
                return ProjectionConsistencyFenceObservation(
                    status="changed",
                    reason="target_generation_changed",
                )
            return ProjectionConsistencyFenceObservation(
                status="stable",
                snapshot_token=f"generation:{begin_token}",
            )

    register_projection_consistency_fence(target, lambda context: GenerationFence(context.backend))
    try:
        fence = resolve_projection_consistency_fence(target, backend=backend)
        assert fence is not None
        stable = read_projection_document(
            _Reader((_chunk("stable"),)),
            _request(target_store=target),
            fence=fence,
        )
        assert stable.fence_status == "target_observation_stable"
        assert stable.snapshot_token == "generation:7"
        assert stable.completeness == "best_effort"

        backend.change_before_finish = True
        changed = read_projection_document(
            _Reader((_chunk("changed"),)),
            _request(target_store=target),
            fence=fence,
        )
        assert changed.fence_status == "target_changed"
        assert changed.completeness == "incomplete"
        assert changed.incomplete_reason == "target_generation_changed"
        assert changed.chunks[0].chunk_id == "changed"
    finally:
        unregister_projection_consistency_fence(target)


def test_registered_fence_factory_errors_are_not_misclassified_as_missing() -> None:
    target = "raising_fence"

    def factory(_context: ProjectionConsistencyFenceContext):
        raise UnknownProviderError("factory failure")

    register_projection_consistency_fence(target, factory)
    try:
        with pytest.raises(UnknownProviderError, match="factory failure"):
            resolve_projection_consistency_fence(target, backend=object())
    finally:
        unregister_projection_consistency_fence(target)


def test_fence_registration_rejects_bad_factories_and_graph_remains_unsupported() -> None:
    with pytest.raises(ValueError, match="lowercase code"):
        register_projection_consistency_fence("FencedChunks", lambda _context: object())

    async def async_factory(_context: ProjectionConsistencyFenceContext):
        return object()

    def generator_factory(_context: ProjectionConsistencyFenceContext):
        yield object()

    def no_context():
        return object()

    for index, candidate in enumerate((async_factory, generator_factory, no_context)):
        with pytest.raises(TypeError, match="projection fence factory"):
            register_projection_consistency_fence(f"invalid_fence_{index}", candidate)

    with pytest.raises(ValueError, match="intentionally unsupported"):
        register_projection_consistency_fence("graph_projection", lambda _context: object())

    registry = fences._PROJECTION_CONSISTENCY_FENCES
    registry.register("graph_projection", lambda _context: object())
    try:
        with pytest.raises(UnknownProviderError, match="projection consistency fence"):
            resolve_projection_consistency_fence("graph_projection", backend=object())
    finally:
        registry.unregister("graph_projection")


def test_fence_observation_requires_explicit_tokens_and_reasons() -> None:
    with pytest.raises(ValueError, match="stable.*snapshot_token"):
        ProjectionConsistencyFenceObservation(status="stable")
    with pytest.raises(ValueError, match="non-stable.*reason"):
        ProjectionConsistencyFenceObservation(status="changed")
    with pytest.raises(ValueError, match="non-stable.*reason"):
        ProjectionConsistencyFenceObservation(status="unavailable")


def test_milvus_reader_passes_dataset_scope_and_marks_unscoped_rows_incomplete() -> None:
    backend = SimpleNamespace()
    reader = _Reader((_chunk("unscoped").model_copy(update={"dataset_id": ""}),))

    class RecordingBackend:
        def query_chunks_by_doc(
            self,
            document_id: str,
            tenant_id: str = "",
            *,
            dataset_id: str = "",
            include_unscoped_scope: bool = False,
        ) -> list[Chunk]:
            backend.request = (document_id, tenant_id, dataset_id, include_unscoped_scope)
            return list(reader.chunks)

    resolved = resolve_projection_consistency_reader(
        "milvus_chunks",
        backend=RecordingBackend(),
    )
    request = _request(target_store="milvus_chunks")
    result = read_projection_document(resolved, request)

    assert backend.request == ("doc-a", "tenant-a", "dataset-a", True)
    assert result.completeness == "incomplete"
    assert result.incomplete_reason == "chunk_scope_unavailable"


def test_milvus_document_query_filters_tenant_and_dataset() -> None:
    class QueryClient:
        def query(self, **kwargs):
            self.kwargs = kwargs
            if 'or tenant_id == ""' in kwargs["filter"]:
                return [
                    {
                        "chunk_id": "unscoped-chunk",
                        "doc_id": "doc-a",
                        "text": "content",
                        "text_hash": "f" * 64,
                        "created_at": 0,
                        "updated_at": 0,
                        "tenant_id": "",
                        "dataset_id": "dataset-a",
                        "metadata": {},
                    }
                ]
            return []

    client = RagMilvusClient(MilvusSettings(uri="./unused.db", collection_name="chunks"))
    query_client = QueryClient()
    client._ensure_client = lambda: query_client

    assert (
        client.query_chunks_by_doc(
            "doc-a",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
        )
        == []
    )
    assert query_client.kwargs["filter"] == (
        'doc_id == "doc-a" and tenant_id == "tenant-a" and dataset_id == "dataset-a"'
    )
    client.query_chunks_by_doc(
        "doc-a",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        include_unscoped_scope=True,
    )
    assert query_client.kwargs["filter"] == (
        'doc_id == "doc-a" and (tenant_id == "tenant-a" or tenant_id == "" or tenant_id is null) '
        'and (dataset_id == "dataset-a" or dataset_id == "" or dataset_id is null)'
    )
    reader = resolve_projection_consistency_reader("milvus_chunks", backend=client)
    result = read_projection_document(
        reader,
        _request(target_store="milvus_chunks"),
    )
    assert result.completeness == "incomplete"
    assert result.incomplete_reason == "chunk_scope_unavailable"


@pytest.mark.parametrize(
    ("tenant_id", "dataset_id"),
    [("", "dataset-a"), ("tenant-a", "")],
)
def test_unscoped_milvus_query_requires_both_scope_dimensions(
    tenant_id: str,
    dataset_id: str,
) -> None:
    client = RagMilvusClient(MilvusSettings(uri="./unused.db", collection_name="chunks"))
    client._ensure_client = lambda: pytest.fail("invalid scope must fail before connecting")

    with pytest.raises(ValueError, match="requires both tenant_id and dataset_id"):
        client.query_chunks_by_doc(
            "doc-a",
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            include_unscoped_scope=True,
        )


def test_registration_rejects_bad_names_duplicate_keys_and_deferred_factories() -> None:
    with pytest.raises(ValueError, match="lowercase code"):
        register_projection_consistency_reader("CustomChunks", lambda _context: _Reader())

    target = "duplicate_chunks"

    def factory(_context: ProjectionConsistencyReaderContext) -> _Reader:
        return _Reader()

    register_projection_consistency_reader(target, factory)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_projection_consistency_reader(target, factory)
    finally:
        unregister_projection_consistency_reader(target)

    async def async_factory(_context: ProjectionConsistencyReaderContext) -> _Reader:
        return _Reader()

    def generator_factory(_context: ProjectionConsistencyReaderContext):
        yield _Reader()

    def no_context() -> _Reader:
        return _Reader()

    for index, candidate in enumerate((async_factory, generator_factory, no_context)):
        with pytest.raises(TypeError, match="projection reader factory"):
            register_projection_consistency_reader(f"invalid_reader_{index}", candidate)


@pytest.mark.parametrize(
    ("chunks", "expected_error"),
    [
        ((_chunk("wrong-doc", document_id="another-doc"),), "different document"),
        ((_chunk("duplicate"), _chunk("duplicate")), "duplicate chunk"),
        (
            (_chunk("wrong-dataset").model_copy(update={"dataset_id": "dataset-b"}),),
            "different dataset",
        ),
        (
            (_chunk("missing-dataset").model_copy(update={"dataset_id": ""}),),
            "without complete tenant/dataset scope",
        ),
        (
            (
                Chunk(
                    **{
                        **_chunk("wrong-tenant").model_dump(),
                        "tenant_id": "tenant-b",
                    }
                ),
            ),
            "different tenant",
        ),
    ],
)
def test_reader_result_rejects_wrong_scope_and_duplicate_chunk_ids(
    chunks: tuple[Chunk, ...],
    expected_error: str,
) -> None:
    class InvalidReader:
        def read_document(
            self, request: ProjectionConsistencyReadRequest
        ) -> ProjectionConsistencyReadResult:
            return ProjectionConsistencyReadResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                document_id=request.document_id,
                chunks=chunks,
                completeness="best_effort",
            )

    with pytest.raises(ValueError, match=expected_error):
        read_projection_document(InvalidReader(), _request())


def test_reader_result_rejects_scope_substitution_non_materialized_and_deferred_results() -> None:
    class WrongScopeReader:
        def read_document(
            self, request: ProjectionConsistencyReadRequest
        ) -> ProjectionConsistencyReadResult:
            return ProjectionConsistencyReadResult(
                target_store=request.target_store,
                tenant_id="other-tenant",
                dataset_id=request.dataset_id,
                document_id=request.document_id,
                chunks=(),
                completeness="best_effort",
            )

    with pytest.raises(ValueError, match="different scope"):
        read_projection_document(WrongScopeReader(), _request())

    class IncompleteScopeReader:
        def read_document(
            self, request: ProjectionConsistencyReadRequest
        ) -> ProjectionConsistencyReadResult:
            return ProjectionConsistencyReadResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                document_id=request.document_id,
                chunks=(_chunk("unscoped").model_copy(update={"dataset_id": ""}),),
                completeness="incomplete",
                incomplete_reason="scope_not_available",
            )

    incomplete = read_projection_document(IncompleteScopeReader(), _request())
    assert incomplete.completeness == "incomplete"

    class GeneratorReader:
        def read_document(self, _request: ProjectionConsistencyReadRequest):
            yield "not a read result"

    with pytest.raises(TypeError, match="must be synchronous"):
        read_projection_document(GeneratorReader(), _request())

    class DeferredReader:
        async def read_document(
            self, request: ProjectionConsistencyReadRequest
        ) -> ProjectionConsistencyReadResult:
            return ProjectionConsistencyReadResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                document_id=request.document_id,
                chunks=(),
                completeness="best_effort",
            )

    with pytest.raises(TypeError, match="must be synchronous"):
        read_projection_document(DeferredReader(), _request())


def test_registry_lookup_revalidates_raw_factory_policy() -> None:
    registry = readers._PROJECTION_CONSISTENCY_READERS
    registry.register("raw_invalid", lambda _context: object())
    try:
        with pytest.raises(TypeError, match="projection consistency reader policy"):
            resolve_projection_consistency_reader("raw_invalid", backend=SimpleNamespace())
    finally:
        registry.unregister("raw_invalid")


def test_incomplete_result_requires_a_reason_code() -> None:
    with pytest.raises(ValueError, match="require a reason code"):
        ProjectionConsistencyReadResult(
            target_store="custom_chunks",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            chunks=(),
            completeness="incomplete",
        )
