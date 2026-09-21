from __future__ import annotations

from indexing.chunking_router import ChunkingRouter


def test_explain_decision_reason_codes_and_modes():
    router = ChunkingRouter(mode="auto", simple_max_chars=4000)

    table = router.explain_decision("csv", "a,b\n1,2", [])
    assert table["mode"] == "qa"
    assert table["reason_code"] == "table_doc_type"
    assert "表格" in table["reason"]
    assert table["facts"]["doc_type"] == "csv"

    short = router.explain_decision("txt", "短文档内容", [])
    assert short["mode"] == "recursive"
    assert short["reason_code"] == "simple_short_no_layout"
    assert short["facts"]["text_chars"] == len("短文档内容")
    assert short["facts"]["simple_max_chars"] == 4000

    long_text = "x" * 5000
    complex_doc = router.explain_decision("pdf", long_text, [{"type": "table"}])
    assert complex_doc["mode"] == "parent_child"
    assert complex_doc["reason_code"] == "complex_or_structured"
    assert complex_doc["facts"]["layout_blocks"] == 1
    assert complex_doc["facts"]["text_chars"] == 5000

    forced = ChunkingRouter(mode="parent_child").explain_decision("csv", "a,b", [])
    assert forced["mode"] == "parent_child"
    assert forced["reason_code"] == "explicit_mode"
    assert "配置强制" in forced["reason"]


def test_decide_matches_explain_mode():
    router = ChunkingRouter(mode="auto", simple_max_chars=100)
    cases = [
        ("csv", "a,b", None, "qa"),
        ("txt", "short", None, "recursive"),
        ("md", "y" * 200, None, "parent_child"),
        ("txt", "short", [{"x": 1}], "parent_child"),
    ]
    for doc_type, text, layout, expected in cases:
        assert router.decide(doc_type, text, layout) == expected
        assert router.explain_decision(doc_type, text, layout)["mode"] == expected


def test_ingest_meta_carries_chunking_reason(monkeypatch):
    from indexing.chunking_router import ChunkingRouter
    from indexing.ingest import IngestPipeline

    class _FakeEmbedder:
        def embed(self, texts):
            return [[0.0] * 8 for _ in texts]

    class _FakeStore:
        def insert(self, *args, **kwargs):
            return True

        def hybrid_search(self, *args, **kwargs):
            return []

    pipeline = IngestPipeline(
        embedder=_FakeEmbedder(),
        milvus=_FakeStore(),
        parser=None,
        chunking_mode="auto",
    )
    # Simulate decision capture as parse path does
    info = pipeline.chunking_router.explain_decision("csv", "a,b\n1,2", [])
    pipeline._last_chunk_decision = str(info["mode"])
    pipeline._last_chunk_decision_meta = {
        "mode": info["mode"],
        "reason": info.get("reason"),
        "reason_code": info.get("reason_code"),
        "facts": info.get("facts") or {},
    }
    pipeline._last_counts = {"text_chars": 7, "chunk_count": 1}
    meta = pipeline.ingest_meta(None)
    assert meta["chunking_mode"] == "qa"
    assert meta["chunking_reason_code"] == "table_doc_type"
    assert "表格" in meta["chunking_reason"]
    assert meta["chunking_decision"]["doc_type"] == "csv"
