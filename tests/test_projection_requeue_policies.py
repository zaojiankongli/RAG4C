from __future__ import annotations

import pytest

from core import projection_target_contract as contract
from core.projection_target_contract import (
    ProjectionRequeuePolicy,
    ProjectionRequeueValidationContext,
    projection_requeue_operation_names,
    register_projection_requeue_policy,
    resolve_projection_requeue_policy,
    unregister_projection_requeue_policy,
    unsupported_target_requeue_operations,
    validate_projection_requeue_policy,
)


BUILTIN_ORDINARY = {
    "milvus_chunks:upsert",
    "milvus_chunks:delete",
    "milvus_chunks:reconcile",
    "graph_projection:upsert",
    "graph_projection:delete",
}
BUILTIN_DELETE = {
    "milvus_chunks:delete_document",
    "graph_projection:delete_document",
    "catalog_finalize:finalize_document_delete",
}


def _accepted(_context: ProjectionRequeueValidationContext) -> bool:
    return True


def test_builtin_projection_requeue_pairs_keep_their_families_and_are_reserved() -> None:
    assert set(projection_requeue_operation_names()) == BUILTIN_ORDINARY | BUILTIN_DELETE
    for key in BUILTIN_ORDINARY:
        target, operation = key.split(":", 1)
        policy = resolve_projection_requeue_policy(target, operation)
        assert policy is not None
        assert policy.family == "ordinary"
    for key in BUILTIN_DELETE:
        target, operation = key.split(":", 1)
        policy = resolve_projection_requeue_policy(target, operation)
        assert policy is not None
        assert policy.family == "document_delete"

    with pytest.raises(ValueError, match="built-in.*reserved"):
        register_projection_requeue_policy(
            "milvus_chunks", "upsert", ProjectionRequeuePolicy("ordinary", _accepted)
        )
    with pytest.raises(ValueError, match="built-in.*cannot be unregistered"):
        unregister_projection_requeue_policy("graph_projection", "delete_document")


def test_lookup_revalidates_internal_registry_factories_and_builtin_contracts() -> None:
    registry = contract._PROJECTION_REQUEUE_POLICIES
    builtin_factory = registry.get_factory("milvus_chunks:upsert")
    registry.register(
        "milvus_chunks:upsert",
        lambda _context: ProjectionRequeuePolicy("ordinary", _accepted),
        replace=True,
    )
    registry.register(
        "raw_custom:upsert",
        lambda _context: ProjectionRequeuePolicy("ordinary"),
    )
    try:
        assert resolve_projection_requeue_policy("milvus_chunks", "upsert") is None
        assert resolve_projection_requeue_policy("raw_custom", "upsert") is None
        assert unsupported_target_requeue_operations("raw_custom") == frozenset(
            {"upsert", "delete", "delete_document"}
        )
        assert "PROJECTION_REQUEUE_POLICIES" not in contract.__all__
    finally:
        registry.register("milvus_chunks:upsert", builtin_factory, replace=True)
        registry.unregister("raw_custom:upsert")


def test_custom_required_pairs_are_resolved_live_and_drive_runtime_coverage() -> None:
    target = "custom_vector"
    required = ("upsert", "delete", "delete_document")
    assert unsupported_target_requeue_operations(target) == frozenset(required)

    seen: list[str] = []

    def validator(context: ProjectionRequeueValidationContext) -> bool:
        seen.append(f"{context.target_store}:{context.operation}")
        return True

    policies = {
        "upsert": ProjectionRequeuePolicy("ordinary", validator),
        "delete": ProjectionRequeuePolicy("ordinary", validator),
        "delete_document": ProjectionRequeuePolicy("document_delete", validator),
    }
    try:
        for operation, policy in policies.items():
            register_projection_requeue_policy(target, operation, policy)

        assert unsupported_target_requeue_operations(target) == frozenset()
        assert resolve_projection_requeue_policy(target, "upsert") is policies["upsert"]
        context = ProjectionRequeueValidationContext(
            target_store=target,
            operation="upsert",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            document_id="doc-a",
            attempt_id="attempt-a",
            target_revision=2,
            document_generation=4,
            attempt_kind="ingest",
            delete_operation_id=None,
            payload={"chunk_ids": ("chunk-a",)},
        )
        assert validate_projection_requeue_policy(policies["upsert"], context) is True
        assert seen == ["custom_vector:upsert"]
    finally:
        for operation in required:
            unregister_projection_requeue_policy(target, operation)


@pytest.mark.parametrize(
    ("target", "operation", "policy", "error"),
    [
        (
            "CustomVector",
            "upsert",
            ProjectionRequeuePolicy("ordinary", _accepted),
            "lowercase code",
        ),
        (
            "custom_vector",
            "upsert",
            ProjectionRequeuePolicy("ordinary", None),
            "requires a target validator",
        ),
        (
            "custom_vector",
            "delete_document",
            ProjectionRequeuePolicy("ordinary", _accepted),
            "does not match operation",
        ),
    ],
)
def test_custom_projection_policy_rejects_invalid_registration(
    target: str,
    operation: str,
    policy: ProjectionRequeuePolicy,
    error: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        register_projection_requeue_policy(target, operation, policy)


def test_custom_projection_policy_rejects_duplicate_keys() -> None:
    target = "duplicate_vector"
    policy = ProjectionRequeuePolicy("ordinary", _accepted)
    register_projection_requeue_policy(target, "upsert", policy)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_projection_requeue_policy(target, "upsert", policy)
    finally:
        unregister_projection_requeue_policy(target, "upsert")


def test_custom_projection_policy_rejects_async_generator_and_bad_signature() -> None:
    async def async_validator(_context: ProjectionRequeueValidationContext) -> bool:
        return True

    def generator_validator(_context: ProjectionRequeueValidationContext):
        yield True

    def bad_signature() -> bool:
        return True

    for index, validator in enumerate((async_validator, generator_validator, bad_signature)):
        with pytest.raises(TypeError, match="projection requeue validator"):
            register_projection_requeue_policy(
                f"invalid_vector_{index}",
                "upsert",
                ProjectionRequeuePolicy("ordinary", validator),
            )


def test_target_validator_must_return_exact_bool() -> None:
    context = ProjectionRequeueValidationContext(
        target_store="custom_vector",
        operation="upsert",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        document_id="doc-a",
        attempt_id="attempt-a",
        target_revision=2,
        document_generation=4,
        attempt_kind="ingest",
        delete_operation_id=None,
        payload={},
    )
    policy = ProjectionRequeuePolicy("ordinary", lambda _context: 1)

    with pytest.raises(TypeError, match="must return bool"):
        validate_projection_requeue_policy(policy, context)
