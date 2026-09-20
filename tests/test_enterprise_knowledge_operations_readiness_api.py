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
    # 这里原来冻结成 `readiness.HEAD_REVISION == "0037_enterprise_knowledge_operations_feedback"`。
    # QA/FAQ 线并入后链尾是 0038/0039，"0037 就是 head" 不再是事实，按 R2 先例动态化：
    # 保留"readiness 的 head 来自 catalog_schema 单一真源"这一半，以及"本能力已被排序登记"这一半。
    assert readiness.HEAD_REVISION == manifest.HEAD_REVISION
    assert capability.revision in readiness._REVISION_INDEX


def test_stage26_revision_is_behind_stage27_and_the_qa_tail() -> None:
    assert readiness._groups_after_revision("0036_enterprise_knowledge_serving_reliability") == (
        "enterprise_knowledge_operations_feedback",
        "enterprise_qa_faq_operations",
        "enterprise_storage_backends",
        "enterprise_answer_evidence_facts",
    )
