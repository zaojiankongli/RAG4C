from __future__ import annotations

import pytest

from config.settings import MilvusSettings
from core import projection_consistency_enumerators as enumerators
from core.milvus_client import RagMilvusClient
from core.providers import UnknownProviderError
from core.projection_consistency_enumerators import (
    ProjectionConsistencyEnumerationRequest,
    ProjectionConsistencyEnumerationResult,
    ProjectionConsistencyTargetEnumeratorContext,
    ProjectionConsistencyTargetReference,
    enumerate_projection_target,
    projection_consistency_enumerator_names,
    register_projection_consistency_enumerator,
    resolve_projection_consistency_enumerator,
    unregister_projection_consistency_enumerator,
)


def _request(
    *,
    target_store: str = "custom_chunks",
    tenant_id: str = "tenant-a",
    dataset_id: str = "dataset-a",
    limit: int = 16_384,
) -> ProjectionConsistencyEnumerationRequest:
    return ProjectionConsistencyEnumerationRequest(
        target_store=target_store,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        limit=limit,
    )


def _reference(
    chunk_id: str = "chunk-a",
    *,
    document_id: str = "doc-a",
    tenant_id: str = "tenant-a",
    dataset_id: str = "dataset-a",
    target_store: str = "custom_chunks",
) -> ProjectionConsistencyTargetReference:
    return ProjectionConsistencyTargetReference(
        target_store=target_store,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        chunk_id=chunk_id,
    )


class _Enumerator:
    def __init__(self, rows: tuple[ProjectionConsistencyTargetReference, ...]) -> None:
        self.rows = rows
        self.requests: list[ProjectionConsistencyEnumerationRequest] = []

    def enumerate(
        self, request: ProjectionConsistencyEnumerationRequest
    ) -> ProjectionConsistencyEnumerationResult:
        self.requests.append(request)
        return ProjectionConsistencyEnumerationResult(
            target_store=request.target_store,
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
            rows=self.rows,
            completeness="best_effort",
        )


def test_builtin_enumerator_is_reserved_and_graph_has_no_fallback() -> None:
    assert projection_consistency_enumerator_names() == ("milvus_chunks",)
    with pytest.raises(ValueError, match="built-in.*reserved"):
        register_projection_consistency_enumerator("milvus_chunks", lambda _context: _Enumerator(()))
    with pytest.raises(ValueError, match="built-in.*cannot be unregistered"):
        unregister_projection_consistency_enumerator("milvus_chunks")

    with pytest.raises(UnknownProviderError, match="projection consistency enumerator"):
        resolve_projection_consistency_enumerator("graph_projection", backend=object())
    with pytest.raises(ValueError, match="intentionally unsupported"):
        register_projection_consistency_enumerator("graph_projection", lambda _context: _Enumerator(()))


def test_custom_enumerator_is_constructed_for_exact_target_and_preserves_scope() -> None:
    target = "custom_chunks"
    backend = object()
    enumerator = _Enumerator((_reference(target_store=target),))
    contexts: list[ProjectionConsistencyTargetEnumeratorContext] = []

    def factory(context: ProjectionConsistencyTargetEnumeratorContext) -> _Enumerator:
        contexts.append(context)
        return enumerator

    register_projection_consistency_enumerator(target, factory)
    try:
        resolved = resolve_projection_consistency_enumerator(target, backend=backend)
        result = enumerate_projection_target(resolved, _request(target_store=target))

        assert resolved is enumerator
        assert contexts == [
            ProjectionConsistencyTargetEnumeratorContext(target_store=target, backend=backend)
        ]
        assert enumerator.requests == [_request(target_store=target)]
        assert result.rows[0].chunk_id == "chunk-a"
    finally:
        unregister_projection_consistency_enumerator(target)


def test_enumerator_marks_unscoped_rows_incomplete_and_rejects_foreign_scope() -> None:
    unscoped = _Enumerator(
        (
            _reference(
                tenant_id="",
                dataset_id="",
            ),
        )
    )
    incomplete = enumerate_projection_target(unscoped, _request())
    assert incomplete.completeness == "incomplete"
    assert incomplete.incomplete_reason == "target_scope_unavailable"
    assert incomplete.rows[0].document_id == "doc-a"

    class ForeignEnumerator:
        def enumerate(
            self, request: ProjectionConsistencyEnumerationRequest
        ) -> ProjectionConsistencyEnumerationResult:
            return ProjectionConsistencyEnumerationResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                rows=(_reference(tenant_id="tenant-b"),),
                completeness="best_effort",
            )

    with pytest.raises(ValueError, match="different tenant"):
        enumerate_projection_target(ForeignEnumerator(), _request())


def test_enumerator_rejects_duplicate_references_and_deferred_results() -> None:
    duplicate = _Enumerator((_reference(), _reference()))
    with pytest.raises(ValueError, match="duplicate"):
        enumerate_projection_target(duplicate, _request())

    class DeferredEnumerator:
        def enumerate(self, _request):
            def deferred():
                yield object()

            return deferred()

    with pytest.raises(TypeError, match="materialized"):
        enumerate_projection_target(DeferredEnumerator(), _request())


def test_incomplete_reason_is_a_safe_code() -> None:
    with pytest.raises(ValueError, match="safe reason"):
        ProjectionConsistencyEnumerationResult(
            target_store="custom_chunks",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            rows=(),
            completeness="incomplete",
            incomplete_reason="doc-secret-123",
        )


def test_enumerator_rejects_duplicate_chunk_ids_across_documents_and_over_limit_results() -> None:
    duplicate = _Enumerator(
        (
            _reference("chunk-a", document_id="doc-a"),
            _reference("chunk-a", document_id="doc-b"),
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        enumerate_projection_target(duplicate, _request())

    class TooManyEnumerator:
        def enumerate(self, request):
            return ProjectionConsistencyEnumerationResult(
                target_store=request.target_store,
                tenant_id=request.tenant_id,
                dataset_id=request.dataset_id,
                rows=(_reference(), _reference("chunk-b")),
                completeness="best_effort",
            )

    with pytest.raises(ValueError, match="more rows"):
        enumerate_projection_target(TooManyEnumerator(), _request(limit=1))


def test_custom_enumerator_at_limit_is_conservatively_incomplete() -> None:
    result = enumerate_projection_target(
        _Enumerator((_reference(),)),
        _request(limit=1),
    )
    assert result.completeness == "incomplete"
    assert result.incomplete_reason == "target_enumeration_limit_reached"


def test_milvus_enumerator_passes_exact_scope_and_conservative_limit() -> None:
    class QueryClient:
        def query(self, **kwargs):
            self.kwargs = kwargs
            return [
                {
                    "chunk_id": "chunk-a",
                    "doc_id": "doc-a",
                    "tenant_id": "",
                    "dataset_id": "dataset-a",
                }
            ]

    client = RagMilvusClient(MilvusSettings(uri="./unused.db", collection_name="chunks"))
    query_client = QueryClient()
    client._ensure_client = lambda: query_client

    rows = client.query_chunk_references_by_dataset(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        limit=16_384,
        include_unscoped_scope=True,
    )
    assert rows == [
        {
            "chunk_id": "chunk-a",
            "doc_id": "doc-a",
            "tenant_id": "",
            "dataset_id": "dataset-a",
        }
    ]
    assert query_client.kwargs["limit"] == 16_384
    assert query_client.kwargs["output_fields"] == [
        "chunk_id",
        "doc_id",
        "tenant_id",
        "dataset_id",
    ]
    assert query_client.kwargs["filter"] == (
        '(tenant_id == "tenant-a" or tenant_id == "" or tenant_id is null) '
        'and (dataset_id == "dataset-a" or dataset_id == "" or dataset_id is null)'
    )


def test_milvus_enumerator_rejects_non_string_identity_fields() -> None:
    class InvalidQueryClient:
        def query(self, **_kwargs):
            return [
                {
                    "chunk_id": 123,
                    "doc_id": "doc-a",
                    "tenant_id": "tenant-a",
                    "dataset_id": "dataset-a",
                }
            ]

    client = RagMilvusClient(MilvusSettings(uri="./unused.db", collection_name="chunks"))
    client._ensure_client = lambda: InvalidQueryClient()
    with pytest.raises(Exception, match="非法 chunk_id"):
        client.query_chunk_references_by_dataset(
            tenant_id="tenant-a",
            dataset_id="dataset-a",
        )


def test_milvus_enumerator_marks_a_full_page_incomplete() -> None:
    class FullPageBackend:
        def query_chunk_references_by_dataset(self, **_kwargs):
            return [
                {
                    "chunk_id": "chunk-a",
                    "doc_id": "doc-a",
                    "tenant_id": "tenant-a",
                    "dataset_id": "dataset-a",
                }
            ]

    # The built-in path uses the exact request limit to detect truncation.
    result = enumerators._MilvusProjectionConsistencyEnumerator(FullPageBackend()).enumerate(
        _request(target_store="milvus_chunks", limit=1)
    )
    assert result.completeness == "incomplete"
    assert result.incomplete_reason == "target_enumeration_limit_reached"
