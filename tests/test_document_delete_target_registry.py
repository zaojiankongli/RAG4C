from __future__ import annotations

import pytest

from core.document_delete_targets import document_delete_projection_targets
from indexing.projection_target_runtime import (
    IncompleteProjectionTargetRuntime,
    register_projection_delete_target_runtime,
    retire_projection_delete_target_runtime,
)


def test_builtin_delete_targets_keep_their_existing_order() -> None:
    assert document_delete_projection_targets() == (
        "milvus_chunks",
        "graph_projection",
    )


def test_delete_target_registration_fails_closed_without_runtime_support() -> None:
    with pytest.raises(IncompleteProjectionTargetRuntime, match="missing worker handlers"):
        register_projection_delete_target_runtime("custom_vector", order=30)
    assert document_delete_projection_targets() == (
        "milvus_chunks",
        "graph_projection",
    )


def test_delete_target_registration_rejects_invalid_keys_and_builtin_removal() -> None:
    with pytest.raises(ValueError, match="lowercase code"):
        register_projection_delete_target_runtime("CustomVector", order=30)

    with pytest.raises(ValueError, match="non-negative"):
        register_projection_delete_target_runtime("custom_vector", order=-1)

    with pytest.raises(ValueError, match="built-in"):
        retire_projection_delete_target_runtime("graph_projection")
