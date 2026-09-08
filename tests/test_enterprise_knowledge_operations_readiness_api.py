from __future__ import annotations

from core import catalog_schema as manifest
from server import enterprise_readiness_api as readiness


def test_stage27_readiness_capability_is_last_and_exact() -> None:
    capabilities = {item.key: item for item in readiness._CAPABILITIES}
    capability = capabilities["enterprise_knowledge_operations_feedback"]
    assert capability.revision == manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION
    assert capability.tables == manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_TABLES
    assert (
        list(capabilities).index("enterprise_knowledge_operations_feedback")
        == list(capabilities).index("enterprise_knowledge_serving_reliability") + 1
    )
    assert readiness.HEAD_REVISION == "0037_enterprise_knowledge_operations_feedback"


def test_stage26_revision_is_behind_only_stage27() -> None:
    assert readiness._groups_after_revision("0036_enterprise_knowledge_serving_reliability") == (
        "enterprise_knowledge_operations_feedback",
    )
