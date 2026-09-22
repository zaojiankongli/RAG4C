"""parser_meta projection guards: the producer declares fields, ingest follows.

``IngestPipeline.ingest_meta`` used to copy diagnostics through two hand-written
lists of key *names* (five for the chunking facts, four for the parser's router
decision) under a comment saying "只保留稳定标量". The comment described a type
rule; the code was a name menu. Consequence: a producer adding one more diagnostic
scalar had to remember to edit this file, and if it didn't, the field was dropped
**silently** — the frontend could never show it and nothing anywhere complained.

The rule is now applied as stated: JSON scalars pass, containers and objects don't.
"""

from __future__ import annotations


import inspect
import json
from pathlib import Path

import indexing.ingest as ingest_module
from indexing.ingest import IngestPipeline


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class _FakeStore:
    def insert(self, *args, **kwargs):
        return True

    def hybrid_search(self, *args, **kwargs):
        return []


def _pipeline() -> IngestPipeline:
    return IngestPipeline(embedder=_FakeEmbedder(), milvus=_FakeStore(), parser=None)


def _meta_with(*, facts: dict, router: dict) -> dict:
    pipeline = _pipeline()
    pipeline._last_chunk_decision = "qa"
    pipeline._last_chunk_decision_meta = {
        "mode": "qa",
        "reason": "表格类",
        "reason_code": "table_doc_type",
        "facts": facts,
    }
    pipeline._last_parsed_metadata = {
        "router": router,
        "file_name": "a.csv",
        "provider": "cli",
        "language": "ch",
    }
    pipeline._last_counts = {"text_chars": 7, "chunk_count": 1}
    pipeline._last_parse_ms = 1.0
    pipeline._last_stage_ms = {name: 0.0 for name in (
        "parse", "clean", "split", "contextual", "embed", "insert", "graph")}
    return pipeline.ingest_meta(None)


def test_a_new_scalar_field_reaches_the_db_without_editing_ingest(tmp_path: Path) -> None:
    """判据：生产方新加一个诊断字段，ingest 一行都不改也能落库。"""
    host = Path(inspect.getsourcefile(ingest_module) or "")
    before = host.read_bytes()
    meta = _meta_with(
        facts={
            "doc_type": "csv",
            "layout_blocks": 0,
            "producer_added_yesterday": "page-level tables",
        },
        router={
            "engine": "vision",
            "pdf_type": "mixed",
            "page_count": 12,
            "confidence": 0.9,
            "classifier_build": "2026.09",
        },
    )
    assert meta["chunking_decision"]["producer_added_yesterday"] == "page-level tables"
    assert meta["classifier_build"] == "2026.09"
    assert meta["provider"] == "cli" and meta["language"] == "ch"
    assert meta["file_name"] == "a.csv"
    assert host.read_bytes() == before


def test_the_old_whitelisted_fields_are_all_still_there() -> None:
    """前端 ``chunkDiagnostics.ts`` 读的就是这些键，一个都不能因为改造而消失。"""
    meta = _meta_with(
        facts={
            "doc_type": "csv",
            "text_chars": 7,
            "layout_blocks": 0,
            "simple_max_chars": 4000,
            "configured_mode": "auto",
        },
        router={"engine": "vision", "pdf_type": "text_based", "page_count": 3, "confidence": 1.0},
    )
    assert meta["chunking_decision"] == {
        "doc_type": "csv",
        "text_chars": 7,
        "layout_blocks": 0,
        "simple_max_chars": 4000,
        "configured_mode": "auto",
    }
    assert meta["engine"] == "vision"
    assert meta["pdf_type"] == "text_based"
    assert meta["page_count"] == 3
    assert meta["confidence"] == 1.0


def test_containers_and_objects_stay_out_because_the_db_column_is_json() -> None:
    """原注释的真实意图是"别把不可序列化的东西写进库"，这条钉住它。"""

    class _Opaque:
        pass

    meta = _meta_with(
        facts={
            "doc_type": "csv",
            "pages_needing_ocr": [1, 2, 3],
            "nested": {"a": 1},
            "opaque": _Opaque(),
            "raw": b"bytes",
            "maybe_none": None,
        },
        router={
            "engine": "fast",
            "pages_needing_ocr": [4, 5],
            "layout": [_Opaque()],
            "fallback_reason": "pdf-inspector 未安装",
        },
    )
    assert set(meta["chunking_decision"]) == {"doc_type", "maybe_none"}
    assert meta["engine"] == "fast"
    assert meta["fallback_reason"] == "pdf-inspector 未安装"
    assert "pages_needing_ocr" not in meta
    assert "layout" not in meta
    json.dumps(meta, ensure_ascii=False)  # 落库前的硬约束：整个 meta 必须可序列化


def test_ingest_holds_no_hand_written_list_of_field_names() -> None:
    """宿主守卫：那两份按名字的键清单不许长回来。

    不用通用的"没有字符串元组"扫描 —— ``stage_ms`` 那行合法地枚举阶段名，通用扫描会
    把它一起判成违规（我试过，确实误报）。这里钉的是这两张清单独有的键。
    """
    source = inspect.getsource(ingest_module.IngestPipeline.ingest_meta)
    for dead_key in ("simple_max_chars", "configured_mode", "layout_blocks", "engine"):
        assert f'"{dead_key}"' not in source, (
            f"ingest_meta 又按名字点菜了（{dead_key}）—— 投影应按类型判定"
        )
    assert "_json_scalar_snapshot" in source
