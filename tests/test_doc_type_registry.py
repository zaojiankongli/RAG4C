"""Document-format capability table guards.

Before this there were five hand-maintained literals describing one fact
(``parsers/base.SUPPORTED_EXTENSIONS``, ``ingest._EXTENSION_DOC_TYPES``,
``ingest._PLAINTEXT_EXTENSIONS``, ``chunking_router._TABLE_DOC_TYPES``,
``documents.FOLDER_IMPORT_EXTENSIONS``), and real defects fell out of the drift:

* no extension ever produced ``doc_type="csv"``, and ``.csv`` sat on neither side of
  the readable/parser split, so a CSV upload failed outright in ``_parse_file`` while
  ``CsvQaChunker``'s qa mode stayed unreachable;
* ``DocumentParser._validate_file`` gated on MinerU's capability list, so any other
  parser reported "该文件类型不支持" with MinerU's list in the message — turning
  "this parser can't" into "the system can't read it".

The guards pin behavioural equivalence with the old literals (except the recorded
CSV delta), fail-closed lookups, and the acceptance criterion: declaring one more
format costs zero edits at the consumers — proved by *behaviour* through the real
ingest / router / folder-scan paths, not by re-calling a projection function.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import indexing.chunking_router as chunking_router_module
import indexing.ingest as ingest_module
import server.documents as documents_module
from indexing.chunking_router import ChunkingRouter
from indexing.doc_types import (
    DOC_TYPE_SPECS,
    DocTypeSpec,
    all_specs,
    doc_type_for_extension,
    importable_extensions,
    is_importable_extension,
    is_table_like_doc_type,
    plain_text_extensions,
    readable_without_parser,
    register_doc_type_spec,
    resolve_spec_by_doc_type,
    resolve_spec_by_extension,
    table_like_doc_types,
    unregister_doc_type_spec,
)
from indexing.parsers.base import DocumentParser, MineruParserError

# 改造前五处字面量的原文，逐字抄下来当等价性对照。
OLD_EXTENSION_DOC_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".rst": "restructuredtext",
    ".pdf": "pdf",
    ".doc": "word",
    ".docx": "word",
    ".txt": "txt",
    ".xls": "excel",
    ".xlsx": "excel",
}
OLD_PLAINTEXT_EXTENSIONS = frozenset(
    {".md", ".markdown", ".mdx", ".adoc", ".asciidoc", ".rst", ".txt", ".text"}
)
OLD_TABLE_DOC_TYPES = ("csv", "excel", "xlsx", "xls")
OLD_FOLDER_IMPORT_EXTENSIONS = frozenset(
    {
        ".adoc", ".asciidoc", ".doc", ".docx", ".jpeg", ".jpg", ".markdown",
        ".md", ".mdx", ".pdf", ".png", ".ppt", ".pptx", ".rst", ".text",
        ".txt", ".xls", ".xlsx",
    }
)


def _pipeline(parser=None):
    from indexing.ingest import IngestPipeline

    return IngestPipeline(embedder=None, milvus=None, parser=parser)


# --------------------------------------------------------------------------- #
# 等价性：四张投影 vs 原字面量
# --------------------------------------------------------------------------- #


def test_extension_to_doc_type_matches_the_old_literal_apart_from_the_csv_fix() -> None:
    from indexing.doc_types import extension_to_doc_type

    assert extension_to_doc_type() == {**OLD_EXTENSION_DOC_TYPES, ".csv": "csv"}


def test_plaintext_set_matches_the_old_literal_apart_from_the_csv_fix() -> None:
    assert plain_text_extensions() == OLD_PLAINTEXT_EXTENSIONS | {".csv"}


def test_table_like_set_is_the_old_tuple_verbatim() -> None:
    assert set(table_like_doc_types()) == set(OLD_TABLE_DOC_TYPES)


def test_folder_import_set_is_the_same_table_not_a_fifth_copy() -> None:
    assert importable_extensions() == OLD_FOLDER_IMPORT_EXTENSIONS | {".csv"}
    assert documents_module.FOLDER_IMPORT_EXTENSIONS == importable_extensions()


def test_declared_but_unrouted_formats_keep_todays_asymmetry() -> None:
    """``.ppt`` / 图片 / ``.text`` 在改造前就不在「扩展名 -> 切分类型」那张表里。

    这里声明它们只为了格式清单完整；``ingest_resolves=False`` 把今天这个不对称
    如实钉住，免得「统一到一张表」顺手改掉别人的切分结果。
    """
    for ext in (".ppt", ".pptx", ".png", ".jpg", ".text"):
        assert resolve_spec_by_extension(ext) is not None
        assert doc_type_for_extension(ext) is None
        assert is_importable_extension(ext) is True
    assert readable_without_parser(".text") is True
    assert doc_type_for_extension(".txt") == "txt"


def test_html_is_deliberately_absent_because_registering_it_would_recut() -> None:
    """.html 不登记：过去的行为要逐字留住。

    普查 §D2 说它"被贴成 doc_type=txt"，核实不成立 —— 本表的 doc_type 只喂
    ``_segment`` 与切分路由，落库那份来自别处。登记它真正的后果是切分变化：过去
    查不到 doc_type，落回文本启发式（解析产物是带 ``#`` 的 Markdown）走
    MarkdownStrategy；登记后变 ParagraphStrategy。那是改契约，另说。
    """
    from indexing.ingest import _infer_doc_type
    from indexing.strategies import MarkdownStrategy, ParagraphStrategy, strategy_for

    assert resolve_spec_by_extension(".html") is None
    assert is_importable_extension(".html") is False
    assert isinstance(strategy_for(_infer_doc_type("# 标题\n\n正文\n")), MarkdownStrategy)
    assert isinstance(strategy_for("html"), ParagraphStrategy)


# --------------------------------------------------------------------------- #
# 查表语义：fail closed，且不顺手放宽
# --------------------------------------------------------------------------- #


def test_lookups_fail_closed_instead_of_guessing() -> None:
    for unknown in (".exe", "", "no-dot", None):
        assert resolve_spec_by_extension(unknown) is None  # type: ignore[arg-type]
        assert doc_type_for_extension(unknown) is None  # type: ignore[arg-type]
        assert readable_without_parser(unknown) is False  # type: ignore[arg-type]
        assert is_importable_extension(unknown) is False  # type: ignore[arg-type]
    assert is_table_like_doc_type("") is False


def test_extension_lookup_normalises_case_but_doc_type_membership_does_not() -> None:
    assert doc_type_for_extension(".PDF") == "pdf"
    assert readable_without_parser(".MD") is True
    assert is_importable_extension(".PDF") is True
    assert doc_type_for_extension("md") is None
    # 表本身按精确值匹配（旧代码就是 `doc_type in _TABLE_DOC_TYPES`）。注意
    # router 入口早就自己 .lower()（explain_decision 里的 normalized_type），所以
    # 经 router 的 "Excel" 改造前后都是 qa —— 那是它唯一的归一化点，不是在表里。
    assert is_table_like_doc_type("PDF") is False
    assert is_table_like_doc_type("Excel") is False
    assert is_table_like_doc_type(" excel") is False
    assert is_table_like_doc_type("excel") is True
    assert ChunkingRouter(mode="auto").explain_decision("Excel", "a,b\n1,2")["mode"] == "qa"
    assert ChunkingRouter(mode="auto").explain_decision(" excel", "a,b\n1,2")["mode"] != "qa"


# --------------------------------------------------------------------------- #
# 注册表自身的形状
# --------------------------------------------------------------------------- #


def test_registering_rejects_a_second_owner_for_one_extension() -> None:
    """一处冲突整行作废：被拒的登记不许留下半个自己。"""
    with pytest.raises(ValueError, match="同时属于"):
        register_doc_type_spec(
            DocTypeSpec("rival", "Rival", (".md",), readable_without_parser=True)
        )
    assert resolve_spec_by_doc_type("rival") is None
    with pytest.raises(ValueError, match="扩展名要带点"):
        register_doc_type_spec(DocTypeSpec("rival2", "Rival", ("csv",)))
    assert resolve_spec_by_doc_type("rival2") is None
    with pytest.raises(ValueError, match="doc_type 不能为空"):
        register_doc_type_spec(DocTypeSpec("  ", "Blank", (".blank",)))
    with pytest.raises(ValueError, match="至少要有一个扩展名"):
        register_doc_type_spec(DocTypeSpec("rival3", "Rival", ()))
    assert doc_type_for_extension(".blank") is None
    assert all_specs() == DOC_TYPE_SPECS


def test_a_missing_trailing_comma_is_named_instead_of_misparsed() -> None:
    """``(".typo")`` 少个尾逗号会退化成字符串；逐字符迭代报的错指不到真因。"""
    with pytest.raises(ValueError, match="尾逗号"):
        register_doc_type_spec(DocTypeSpec("typo", "Typo", ".typo"))  # type: ignore[arg-type]
    assert resolve_spec_by_doc_type("typo") is None


def test_duplicate_doc_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="重复登记"):
        register_doc_type_spec(DocTypeSpec("pdf", "Dupe", (".pdf2",)))


# --------------------------------------------------------------------------- #
# 判据：注册一种格式，宿主零改动 —— 用真实行为证明，不用同义反复
# --------------------------------------------------------------------------- #


def test_a_new_format_costs_zero_edits_and_is_honoured_live(tmp_path: Path) -> None:
    """宿主文件逐字节不变 + 三个宿主**实时**认它。

    字节不变只证明"没改代码"，不证明"改了也不生效"。所以三条断言都走真实路径：
    入库管线读文件、切分路由决策、目录扫描过滤。任一宿主被换回 import 期快照，
    对应那条立刻变红。
    """
    hosts = {
        module: Path(inspect.getsourcefile(module) or "").read_bytes()
        for module in (ingest_module, chunking_router_module, documents_module)
    }
    probe = tmp_path / "pairs.probecsv"
    probe.write_text("问题,答案\n为什么,因为\n", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "doc.probecsv").write_text("x", encoding="utf-8")

    register_doc_type_spec(
        DocTypeSpec(
            doc_type="probedb_csv",
            label="Probe 表格",
            extensions=(".probecsv",),
            readable_without_parser=True,
            table_like=True,
        )
    )
    try:
        assert doc_type_for_extension(".probecsv") == "probedb_csv"
        # 走 parse_and_chunk：一次覆盖「读文件闸门 + ingest 的 doc_type 查表 +
        # 切分路由 + 切分器」。宿主任一处退回 import 期快照，这里就不是 qa 切分。
        pipeline = _pipeline()
        chunks = pipeline.parse_and_chunk(str(probe), doc_id="doc-probe")
        assert chunks, "probe 格式没切出任何 chunk"
        assert pipeline._last_chunk_decision == "qa"
        # 目录扫描真放行（宿主若是快照 -> 计成 unsupported）
        scan = documents_module.discover_folder_documents(folder)
        assert [p.name for p in scan.files] == ["doc.probecsv"]
        assert scan.unsupported_count == 0
    finally:
        unregister_doc_type_spec("probedb_csv")

    for module, source in hosts.items():
        assert Path(inspect.getsourcefile(module) or "").read_bytes() == source, module
    assert doc_type_for_extension(".probecsv") is None
    assert is_importable_extension(".probecsv") is False
    with pytest.raises(KeyError):
        unregister_doc_type_spec("markdown")  # built-ins are not withdrawable


def test_ingest_router_and_documents_hold_no_copy_of_the_lists() -> None:
    """宿主守卫：五份抄本里的三份不许长回来。"""
    for name in ("_EXTENSION_DOC_TYPES", "_PLAINTEXT_EXTENSIONS"):
        assert not hasattr(ingest_module, name), f"indexing/ingest.py 又抄了一份 {name}"
    assert not hasattr(
        chunking_router_module, "_TABLE_DOC_TYPES"
    ), "indexing/chunking_router.py 又抄了一份表格类清单"
    source = Path(inspect.getsourcefile(documents_module) or "").read_text("utf-8")
    assert "FOLDER_IMPORT_EXTENSIONS: frozenset[str] = importable_extensions()" in source, (
        "server/documents.py 的导入清单不再是那张表的投影"
    )


# --------------------------------------------------------------------------- #
# 两条真实缺陷的正证
# --------------------------------------------------------------------------- #


def test_a_real_csv_file_reaches_the_qa_mode_end_to_end(tmp_path: Path) -> None:
    """缺陷 #D3 的正证：改造前 .csv 在 ``_parse_file`` 里**必抛** IngestError。

    ``sources/runner.py:759`` 调 add_file 时不传 doc_type，扩展名是唯一线索，
    所以只测投影等式不足以证明修好了 —— 得从真实文件走一遍。
    """
    from indexing.ingest import IngestError

    csv = tmp_path / "pairs.csv"
    csv.write_text("问题,答案\n为什么sky是蓝的,瑞利散射\n", encoding="utf-8")
    pipeline = _pipeline(parser=_ZipOnlyParser())

    # 一个不认领 .csv 的 parser 也必须能入：直读是兜底，不是"没配 parser 的特例"。
    chunks = pipeline.parse_and_chunk(str(csv), doc_id="doc-csv")
    assert pipeline._last_chunk_decision == "qa"
    assert [c.text for c in chunks] and "瑞利散射" in chunks[0].text

    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(IngestError):
        pipeline._parse_file(str(exe), "doc-exe")


def test_a_binary_file_declared_readable_fails_loudly_instead_of_becoming_garbage(
    tmp_path: Path,
) -> None:
    """直读通道由声明表开启，所以读进来的东西必须自己证明是文本。

    ``read_text(errors="replace")`` 会把二进制糊成一整段 U+FFFD 且不报错；那样一行
    ``readable_without_parser=True`` 就能把二进制静默灌进语料库。
    """
    from indexing.ingest import IngestError

    register_doc_type_spec(
        DocTypeSpec("probebin", "Probe 二进制", (".probebin",),
                    readable_without_parser=True)
    )
    try:
        blob = tmp_path / "x.probebin"
        blob.write_bytes(bytes(range(256)) * 40)
        with pytest.raises(IngestError, match="NUL"):
            _pipeline()._parse_file(str(blob), "doc-bin")
    finally:
        unregister_doc_type_spec("probebin")


# --------------------------------------------------------------------------- #
# parser 闸门：本 parser 的能力，不是全管线的闸门
# --------------------------------------------------------------------------- #


class _ZipOnlyParser(DocumentParser):
    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".zip"

    def parse(self, file_path: str):  # pragma: no cover - never reached
        raise AssertionError("不该走到解析")


def test_validate_file_asks_the_parser_not_the_pipeline_wide_list(
    tmp_path: Path,
) -> None:
    parser = _ZipOnlyParser()
    (tmp_path / "a.zip").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"x")

    parser._validate_file(str(tmp_path / "a.zip"))  # 不抛：这个 parser 认它
    with pytest.raises(MineruParserError, match="_ZipOnlyParser 不支持"):
        parser._validate_file(str(tmp_path / "b.pdf"))


def test_validate_file_still_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MineruParserError, match="文件不存在"):
        _ZipOnlyParser()._validate_file(str(tmp_path / "gone.zip"))


def test_parser_gate_source_does_not_read_the_mineru_list() -> None:
    from indexing.parsers import base

    source = inspect.getsource(base.DocumentParser._validate_file)
    assert "SUPPORTED_EXTENSIONS" not in source, (
        "_validate_file 又在按 MinerU 的能力清单判全管线"
    )
