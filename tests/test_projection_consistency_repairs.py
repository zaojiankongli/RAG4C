from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import projection_consistency_repairs as repairs
from core.providers import UnknownProviderError
from core.projection_consistency_repairs import (
    ProjectionConsistencyRepairAdapterContext,
    ProjectionConsistencyRepairRequest,
    enqueue_projection_consistency_repair,
    projection_consistency_repair_adapter_names,
    register_projection_consistency_repair_adapter,
    resolve_projection_consistency_repair_adapter,
    unregister_projection_consistency_repair_adapter,
)


def _request(*, target_store: str = "custom_chunks") -> ProjectionConsistencyRepairRequest:
    return ProjectionConsistencyRepairRequest(
        target_store=target_store,
        operation="reconcile",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id="attempt-1",
        target_revision=4,
        dedup_key="knowledgeops:reconcile:doc-1:4:hash",
        payload={"missing_ids": ["chunk-1"]},
    )


def test_builtin_milvus_repair_adapter_is_reserved_and_dispatches() -> None:
    adapter = resolve_projection_consistency_repair_adapter(
        "milvus_chunks",
        backend=object(),
    )
    assert adapter is not None
    assert adapter.target_store == "milvus_chunks"
    assert adapter.operation == "reconcile"
    assert projection_consistency_repair_adapter_names() == ("milvus_chunks",)

    with pytest.raises(ValueError, match="reserved"):
        register_projection_consistency_repair_adapter("milvus_chunks", lambda _context: object())
    with pytest.raises(ValueError, match="cannot be unregistered"):
        unregister_projection_consistency_repair_adapter("milvus_chunks")


def test_custom_repair_adapter_is_honoured_live() -> None:
    target = "custom_chunks"
    calls: list[tuple[ProjectionConsistencyRepairRequest, object, object]] = []

    class Adapter:
        target_store = target
        operation = "reconcile"

        def enqueue(self, request, queue, session) -> None:
            calls.append((request, queue, session))

    register_projection_consistency_repair_adapter(
        target,
        lambda context: Adapter() if context.target_store == target else None,
    )
    try:
        adapter = resolve_projection_consistency_repair_adapter(target, backend=object())
        assert adapter is not None
        queue = object()
        session = object()
        request = _request(target_store=target)
        enqueue_projection_consistency_repair(adapter, request, queue, session)
        assert calls == [(request, queue, session)]
    finally:
        unregister_projection_consistency_repair_adapter(target)


def test_backend_dependent_factory_is_lazy_and_only_resolved_once() -> None:
    target = "lazy_repair"
    calls: list[object] = []
    backend = object()

    class Adapter:
        target_store = target
        operation = "reconcile"

        def enqueue(self, _request, _queue, _session) -> None:
            return None

    def factory(context: ProjectionConsistencyRepairAdapterContext):
        if context.backend is None:
            raise AssertionError("backend-dependent factory was invoked during registration")
        calls.append(context.backend)
        return Adapter()

    register_projection_consistency_repair_adapter(target, factory)
    try:
        assert calls == []
        adapter = resolve_projection_consistency_repair_adapter(target, backend=backend)
        assert adapter is not None
        assert calls == [backend]
    finally:
        unregister_projection_consistency_repair_adapter(target)


def test_repair_adapter_registry_rejects_bad_shapes_and_keeps_graph_closed() -> None:
    target = "invalid_repair"

    async def async_factory(_context):
        return object()

    with pytest.raises(TypeError, match="factory"):
        register_projection_consistency_repair_adapter(target, async_factory)

    class WrongTarget:
        target_store = "other_target"
        operation = "reconcile"

        def enqueue(self, _request, _queue, _session) -> None:
            return None

    register_projection_consistency_repair_adapter(target, lambda _context: WrongTarget())
    try:
        with pytest.raises(ValueError, match="does not match"):
            resolve_projection_consistency_repair_adapter(target, backend=object())
    finally:
        unregister_projection_consistency_repair_adapter(target)

    with pytest.raises(ValueError, match="unsupported"):
        register_projection_consistency_repair_adapter(
            "graph_projection",
            lambda _context: SimpleNamespace(
                target_store="graph_projection",
                operation="reconcile",
                enqueue=lambda _request, _queue, _session: None,
            ),
        )
    with pytest.raises(UnknownProviderError, match="not repairable"):
        resolve_projection_consistency_repair_adapter("graph_projection", backend=object())
    assert resolve_projection_consistency_repair_adapter(target, backend=object()) is None

    with pytest.raises(ValueError, match="at most 32"):
        register_projection_consistency_repair_adapter(
            "a" * 33,
            lambda _context: object(),
        )

    too_long_operation = "a" * 25

    class TooLongOperation:
        target_store = target
        operation = too_long_operation

        def enqueue(self, _request, _queue, _session) -> None:
            return None

    register_projection_consistency_repair_adapter(target, lambda _context: TooLongOperation())
    try:
        with pytest.raises(ValueError, match="at most 24"):
            resolve_projection_consistency_repair_adapter(target, backend=object())
    finally:
        unregister_projection_consistency_repair_adapter(target)


def test_builtin_raw_replacement_and_removal_fail_closed() -> None:
    registry = repairs._PROJECTION_CONSISTENCY_REPAIR_ADAPTERS
    builtin = registry.get_factory("milvus_chunks")
    registry.register("milvus_chunks", lambda _context: object(), replace=True)
    try:
        with pytest.raises(TypeError, match="contract was replaced"):
            resolve_projection_consistency_repair_adapter("milvus_chunks", backend=object())
    finally:
        registry.register("milvus_chunks", builtin, replace=True)

    registry.unregister("milvus_chunks")
    try:
        with pytest.raises(TypeError, match="was unregistered"):
            resolve_projection_consistency_repair_adapter("milvus_chunks", backend=object())
    finally:
        registry.register("milvus_chunks", builtin)


def test_enqueue_adapter_rejects_mismatched_or_deferred_results() -> None:
    target = "deferred_repair"

    class Adapter:
        target_store = target
        operation = "reconcile"

        def enqueue(self, _request, _queue, _session):
            return iter(())

    register_projection_consistency_repair_adapter(target, lambda _context: Adapter())
    try:
        adapter = resolve_projection_consistency_repair_adapter(target, backend=None)
        assert adapter is not None
        with pytest.raises(TypeError, match="must return None"):
            enqueue_projection_consistency_repair(adapter, _request(target_store=target), object(), object())
        with pytest.raises(ValueError, match="operation"):
            enqueue_projection_consistency_repair(
                adapter,
                ProjectionConsistencyRepairRequest(
                    target_store=target,
                    operation="upsert",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    document_id="doc-1",
                    attempt_id="attempt-1",
                    target_revision=4,
                    dedup_key="dedup",
                    payload={},
                ),
                object(),
                object(),
            )
    finally:
        unregister_projection_consistency_repair_adapter(target)
