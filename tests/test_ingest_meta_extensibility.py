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
    """判据：生产方新加一个诊断字段，ingest 一行都不改也能落库。

    这条必须走**真生产方**：一个 parser 实例经 ``parse_and_chunk`` 填
    ``_last_parsed_metadata``，再经 ``ingest_meta`` → ``json.dumps`` → 目录层那两份
    投影。手工拼 ``_last_parsed_metadata`` 只证明 ``ingest_meta`` 认它，不证明解析器
    真能把它送到那里，也不证明字符串化的 meta 过得了 catalog 的解码。
    """
    from types import SimpleNamespace

    from core.catalog import _coerce_document_parser_meta, _document_management_values

    class _ProducerAddingOneField:
        def __init__(self) -> None:
            self.supports_calls = 0

        def supports(self, file_path: str) -> bool:
            self.supports_calls += 1
            return file_path.endswith(".md")

        def parse(self, file_path: str):
            return SimpleNamespace(
                text="# 标题\n\n正文一段，够长到能切出一个片段。\n",
                metadata={
                    "provider": "markdown-inspector",
                    "file_name": Path(file_path).name,
                    "producer_added_scalar": "page-level tables",
                },
                layout=[],
            )

    probe = tmp_path / "note.md"
    probe.write_text("# 标题\n\n正文一段，够长到能切出一个片段。\n", encoding="utf-8")
    pipeline = IngestPipeline(
        embedder=_FakeEmbedder(), milvus=_FakeStore(), parser=_ProducerAddingOneField()
    )
    host = Path(inspect.getsourcefile(ingest_module) or "").read_bytes()

    assert pipeline.parse_and_chunk(str(probe), doc_id="doc-note")
    meta = pipeline.ingest_meta(None)
    assert meta["producer_added_scalar"] == "page-level tables"
    # 生产方**独占**的键（管线自己不写）必须照样进来 —— 改成"先写者胜"时别把它一起挡掉。
    assert meta["file_name"] == "note.md"
    assert meta["provider"] == "markdown-inspector"

    stored = json.dumps(meta, ensure_ascii=False)
    assert _coerce_document_parser_meta(stored)["producer_added_scalar"] == "page-level tables"
    _folder, _tags, projected = _document_management_values("", stored)
    assert projected["producer_added_scalar"] == "page-level tables"
    assert Path(inspect.getsourcefile(ingest_module) or "").read_bytes() == host


def test_a_parser_cannot_rewrite_the_pipelines_own_diagnostics() -> None:
    """解析器只**补齐**诊断，不许接管：``chunking_mode`` 是管线算出来的事实。

    把投影从"按键名点菜"改成"按类型判定"时，晚到的解析器键会盖掉先写入的管线键 ——
    一个 parser 只要肯写 ``chunking_mode``，文档管理页就会显示一篇从没发生过的切分。
    """
    pipeline = _pipeline()
    pipeline._last_chunk_decision = "recursive"
    pipeline._last_chunk_decision_meta = {"mode": "recursive", "facts": {"doc_type": "md"}}
    pipeline._last_parsed_metadata = {
        "router": {"engine": "vision", "chunking_mode": "clobbered-by-router"},
        "file_name": "note.md",
        "chunking_mode": "clobbered-by-parser",
        "chunk_count": 99999,
        "text_chars": 99999,
        "stage_ms": "not-a-dict",
        "parse_ms": 99999.0,
        "provider": "cli",
    }
    pipeline._last_counts = {"text_chars": 12, "chunk_count": 1}
    pipeline._last_parse_ms = 3.0
    pipeline._last_stage_ms = {name: 0.5 for name in (
        "parse", "clean", "split", "contextual", "embed", "insert", "graph")}
    meta = pipeline.ingest_meta(None)
    assert meta["chunking_mode"] == "recursive"
    assert meta["chunk_count"] == 1
    assert meta["text_chars"] == 12
    assert meta["parse_ms"] == 3.0
    assert isinstance(meta["stage_ms"], dict)
    # 不冲突的键照旧透传 —— 修这一条不能把扩展性一起修回去。
    assert meta["provider"] == "cli" and meta["engine"] == "vision"


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
