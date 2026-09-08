from __future__ import annotations

from core.enterprise_directory import enterprise_capabilities
from tests.test_enterprise_knowledge_operations_readiness import _upgrade, _url
from sqlalchemy import create_engine


def test_enterprise_context_exposes_stage27_capability(tmp_path) -> None:
    url = _url(tmp_path / "stage27-directory.db")
    _upgrade(url)
    engine = create_engine(url)
    try:
        capability = enterprise_capabilities(engine)["enterprise_knowledge_operations_feedback"]
        assert capability == {
            "label": "企业知识运营与反馈",
            "state": "ready",
            "reason": None,
        }
    finally:
        engine.dispose()
