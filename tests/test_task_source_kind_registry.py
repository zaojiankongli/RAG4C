"""扩展轴 #6：Task source kind 的数据策略与 route schema 注册表。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import enterprise_task_operations as task_core
from core import enterprise_task_operations_service as task_service
from core.task_source_kinds import (
    TaskRouteParamSpec,
    TaskSourceKindSpec,
    register_task_source_kind,
    task_route_schema,
    task_source_kind,
    task_source_kind_names,
    task_source_kinds_for_category,
    task_source_route_params,
    unregister_task_source_kind,
)
import core.task_source_kinds as task_source_registry

REPO = Path(__file__).resolve().parents[1]
SERVICE_HOST = REPO / "core" / "enterprise_task_operations_service.py"


def _custom_spec() -> TaskSourceKindSpec:
    return TaskSourceKindSpec(
        kind="knowledge_prune",
        public_category="documents",
        storage_category="documents",
        public_route_code="knowledge_prune",
        storage_route_code="knowledge_prune",
        default_route_code="enterprise_tasks",
        allowed_route_codes=frozenset({"enterprise_tasks", "enterprise_task_operations"}),
        route_params=(
            TaskRouteParamSpec("tenant_id", source_field="tenant_id"),
            TaskRouteParamSpec("task_id", source_field="source_id"),
        ),
    )


def test_builtins_are_one_ordered_registry_and_keep_legacy_contract() -> None:
    assert task_source_kind_names() == (
        "document_ingest",
        "index_operation",
        "source_sync",
        "document_delete",
        "audit_export",
        "release_quality_scan",
        "release_recertification",
    )
    assert task_source_kind("release_quality_scan").default_route_code == (
        "knowledge_quality_operations"
    )
    with pytest.raises(task_core.TaskOperationsAuthorityInvalid):
        task_core.canonical_task_source(
            {
                "tenant_id": "tenant-a",
                "source_kind": " source_sync ",
                "source_id": "run-a",
                "source_revision": 1,
                "source_digest": "a" * 64,
                "normalized_status": "queued",
                "action_required": False,
            }
        )
    with pytest.raises(task_service.EnterpriseTaskOperationsInvalid):
        task_service._normalize_kind("SOURCE_SYNC")
    assert task_route_schema("enterprise_tasks", "source_sync") == (
        {"tenant_id", "task_id"},
        {"tenant_id", "task_id"},
    )
    assert task_source_route_params(
        {
            "source_kind": "source_sync",
            "tenant_id": "tenant-a",
            "source_id": "run-a",
            "safe_facts": {"data_source_id": "source-a"},
        }
    ) == {"run_id": "run-a", "source_id": "source-a", "tenant_id": "tenant-a"}
    with pytest.raises(ValueError, match="required"):
        task_source_route_params(
            {
                "source_kind": "source_sync",
                "source_id": "run-a",
                "safe_facts": {},
            }
        )


def test_registering_a_source_kind_drives_core_and_service_without_host_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = SERVICE_HOST.read_bytes()
    spec = _custom_spec()
    register_task_source_kind(spec)
    original_adapters = task_service.SOURCE_ADAPTER_REGISTRY
    try:
        task_service.SOURCE_ADAPTER_REGISTRY = {
            **original_adapters,
            "knowledge_prune": lambda **_: [
                task_service._source_fact(
                    tenant_id="tenant-a",
                    source_kind="knowledge_prune",
                    source_id="prune-a",
                    source_revision=1,
                    dataset_id=None,
                    workspace_id=None,
                    normalized_status="failed",
                    action_required=True,
                    progress_percent=None,
                    attempt_number=1,
                    max_attempts=2,
                    safe_facts={},
                )
            ],
        }
        selected = task_service._validate_source_kinds(
            ["source_sync", "knowledge_prune"]
        )
        assert selected == ("source_sync", "knowledge_prune")

        canonical = task_core.canonical_task_source(
            {
                "tenant_id": "tenant-a",
                "source_kind": "knowledge_prune",
                "source_id": "prune-a",
                "source_revision": 1,
                "source_digest": "a" * 64,
                "category": "documents",
                "normalized_status": "queued",
                "action_required": False,
                "target_route_code": "enterprise_tasks",
                "target_route_params_json": {
                    "tenant_id": "tenant-a",
                    "task_id": "prune-a",
                },
            }
        )
        assert canonical["source_kind"] == "knowledge_prune"
        assert canonical["target_route_code"] == "enterprise_tasks"
        assert canonical["target_route_params_json"] == {
            "tenant_id": "tenant-a",
            "task_id": "prune-a",
        }
        assert task_service._source_spec("knowledge_prune") is spec
        assert "knowledge_prune" in task_source_kinds_for_category("documents")
        collected = task_service._collect_sources(
            object(),
            "tenant-a",
            ("knowledge_prune",),
            task_service.datetime.now(task_service.UTC).replace(tzinfo=None),
        )
        assert tuple(collected.sources) == (("knowledge_prune", "prune-a"),)
    finally:
        task_service.SOURCE_ADAPTER_REGISTRY = original_adapters
        unregister_task_source_kind("knowledge_prune")

    assert SERVICE_HOST.read_bytes() == before
    assert task_source_kind("knowledge_prune") is None
    with pytest.raises(task_core.TaskOperationsAuthorityInvalid):
        task_core.canonical_task_source(
            {
                "tenant_id": "tenant-a",
                "source_kind": "knowledge_prune",
                "source_id": "prune-a",
                "source_revision": 1,
                "source_digest": "a" * 64,
            }
        )


def test_registry_rejects_invalid_shapes_and_builtin_removal() -> None:
    with pytest.raises(ValueError, match="allowed task category"):
        register_task_source_kind(
            TaskSourceKindSpec(
                **{
                    **_custom_spec().__dict__,
                    "kind": "invalid_category_kind",
                    "public_category": "new_category",
                }
            )
        )
    with pytest.raises(ValueError, match="required_params"):
        from core.task_source_kinds import TaskRouteSpec, register_task_route_spec

        register_task_route_spec(
            TaskRouteSpec("broken_route", ("tenant_id",), ("task_id",))
        )
    with pytest.raises(ValueError, match="reserved"):
        unregister_task_source_kind("source_sync")
    with pytest.raises(ValueError, match="duplicate required_by_source_kind"):
        from core.task_source_kinds import TaskRouteSpec, register_task_route_spec

        register_task_route_spec(
            TaskRouteSpec(
                "duplicate_route",
                ("tenant_id",),
                ("tenant_id",),
                (("source_sync", ()), ("source_sync", ())),
            )
        )


def test_raw_builtin_registry_replacement_and_removal_fail_closed() -> None:
    registry = task_source_registry._SOURCE_KIND_REGISTRY
    builtin = registry.get_factory("source_sync")
    registry.register("source_sync", lambda _config: _custom_spec(), replace=True)
    try:
        with pytest.raises(TypeError, match="contract was replaced"):
            task_source_kind("source_sync")
    finally:
        registry.register("source_sync", builtin, replace=True)

    registry.unregister("source_sync")
    try:
        with pytest.raises(TypeError, match="was unregistered"):
            task_source_kind("source_sync")
    finally:
        registry.register("source_sync", builtin)
