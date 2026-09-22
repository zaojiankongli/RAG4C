"""Document-format capability table guards.

Before this there were four hand-maintained literals describing one fact
(``parsers/base.SUPPORTED_EXTENSIONS``, ``ingest._EXTENSION_DOC_TYPES``,
``ingest._PLAINTEXT_EXTENSIONS``, ``chunking_router._TABLE_DOC_TYPES``), and two
live defects fell out of the drift:

* no extension ever produced ``doc_type="csv"``, so the qa (row-per-QA) mode was
  unreachable for real CSV uploads even though ``CsvQaChunker`` was wired up;
* ``DocumentParser._validate_file`` gated on MinerU's capability list, so any other
  parser reported "该文件类型不支持" with MinerU's list in the message — turning
  "this parser can't" into "the system can't read it".

The tests below pin behavioural equivalence with the old literals (except the two
deliberate deltas), the fail-closed lookups, and the acceptance criterion: declaring
one more format costs zero edits at the consumers.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import indexing.chunking_router as chunking_router_module
import indexing.ingest as ingest_module
from indexing.chunking_router import ChunkingRouter
from indexing.doc_types import (
    DOC_TYPE_SPECS,
    DocTypeSpec,
    all_specs,
    doc_type_for_extension,
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

# 改造前四处字面量的原文，逐字抄下来当等价性对照。
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


def test_extension_to_doc_type_matches_the_old_literal_apart_from_recorded_fixes() -> None:
    from indexing.doc_types import extension_to_doc_type

    assert extension_to_doc_type() == {
        **OLD_EXTENSION_DOC_TYPES,
        # 两条有意的 delta，对应交接文档 §D 的缺陷 3 与 2：
        ".csv": "csv",      # 此前没有任何扩展名产出 doc_type="csv"，qa 切分是死代码
        ".html": "html",    # 此前一份 HTML 被文本启发式贴成 doc_type="txt"
        ".htm": "html",
    }


def test_plaintext_set_matches_the_old_literal_apart_from_the_csv_fix() -> None:
    assert plain_text_extensions() == OLD_PLAINTEXT_EXTENSIONS | {".csv"}


def test_table_like_set_is_the_old_tuple_verbatim() -> None:
    assert set(table_like_doc_types()) == set(OLD_TABLE_DOC_TYPES)


def test_html_is_labelled_honestly_without_gaining_a_read_channel() -> None:
    """.html 的修法只有一件事：别再把元数据写成 txt。

    它仍然**不能**免 parser 直读（HTML 原文不是可读正文），也仍然落回段落切分，
    所以贴标签之外的行为逐字不变。
    """
    assert doc_type_for_extension(".html") == "html"
    assert readable_without_parser(".html") is False
    assert is_table_like_doc_type("html") is False
    from indexing.strategies import ParagraphStrategy, strategy_for

    assert isinstance(strategy_for("html"), ParagraphStrategy)


def test_lookups_fail_closed_instead_of_guessing() -> None:
    for unknown in (".exe", "", "no-dot", None):
        assert resolve_spec_by_extension(unknown) is None  # type: ignore[arg-type]
        assert doc_type_for_extension(unknown) is None  # type: ignore[arg-type]
        assert readable_without_parser(unknown) is False  # type: ignore[arg-type]
    assert is_table_like_doc_type("") is False
    assert is_table_like_doc_type("PDF") is False


def test_spellings_are_normalised_but_missing_dot_is_not() -> None:
    assert doc_type_for_extension(".PDF") == "pdf"
    assert readable_without_parser(".MD") is True
    assert doc_type_for_extension("md") is None


def test_declared_but_unrouted_formats_keep_todays_asymmetry() -> None:
    """``.ppt`` / 图片 / ``.text`` 在改造前就不在「扩展名 -> 切分类型」那张表里。

    这里声明它们只为了格式清单完整；``ingest_resolves=False`` 把今天这个不对称
    如实钉住，免得「统一到一张表」顺手改掉别人的切分结果。
    """
    for ext in (".ppt", ".pptx", ".png", ".jpg", ".text"):
        assert resolve_spec_by_extension(ext) is not None
        assert doc_type_for_extension(ext) is None
    assert readable_without_parser(".text") is True
    assert doc_type_for_extension(".txt") == "txt"


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


def test_a_new_format_costs_zero_edits_at_the_consumers() -> None:
    """判据：注册一种格式后四处消费点都认它，而两个宿主文件一个字都没改。"""
    hosts = {
        module: Path(inspect.getsourcefile(module) or "").read_bytes()
        for module in (ingest_module, chunking_router_module)
    }
    probe = DocTypeSpec(
        doc_type="probedb_csv",
        label="Probe 表格",
        extensions=(".probecsv",),
        readable_without_parser=True,
        table_like=True,
    )
    register_doc_type_spec(probe)
    try:
        assert doc_type_for_extension(".probecsv") == "probedb_csv"
        assert readable_without_parser(".probecsv") is True
        assert is_table_like_doc_type("probedb_csv") is True
        # 宿主是实时查表，不是 import 期快照 —— 这两条断言才是判据的全部重量。
        assert chunking_router_module._is_table_type("probedb_csv", 0, 0, 0) is True
        assert ingest_module.doc_type_for_extension(".probecsv") == "probedb_csv"
        assert ChunkingRouter(mode="auto").explain_decision(
            "probedb_csv", "a,b\n1,2"
        )["mode"] == "qa"
    finally:
        unregister_doc_type_spec("probedb_csv")

    for module, source in hosts.items():
        assert Path(inspect.getsourcefile(module) or "").read_bytes() == source
    assert doc_type_for_extension(".probecsv") is None
    assert is_table_like_doc_type("probedb_csv") is False
    with pytest.raises(KeyError):
        unregister_doc_type_spec("markdown")  # built-ins are not withdrawable


def test_a_real_csv_file_reaches_the_qa_mode_end_to_end(tmp_path: Path) -> None:
    """缺陷 #D3 的正证：改造前 .csv 在 ``_parse_file`` 里**必抛** IngestError。

    只测投影等式不足以证明这条修好了 —— 得从真实文件走一遍入库管线的读文件与
    路由两步（``sources/runner.py`` 调 ``add_file`` 时不传 doc_type，扩展名是它
    唯一的线索）。
    """
    from indexing.ingest import IngestPipeline, IngestError

    csv = tmp_path / "pairs.csv"
    csv.write_text("问题,答案\n为什么sky是蓝的,瑞利散射\n", encoding="utf-8")
    pipeline = IngestPipeline(embedder=None, milvus=None, parser=_ZipOnlyParser())

    text = pipeline._parse_file(str(csv), "doc-csv")
    assert text.startswith("问题,答案")
    doc_type = doc_type_for_extension(csv.suffix)
    assert pipeline.chunking_router.explain_decision(doc_type, text)["mode"] == "qa"

    # 反面对照：一种既没有 parser 认领、又没登记为可直读的格式仍然 fail closed。
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(IngestError):
        pipeline._parse_file(str(exe), "doc-exe")


def test_ingest_and_router_no_longer_keep_their_own_extension_sets() -> None:
    """宿主守卫：那三张抄本不许长回来。"""
    for name in ("_EXTENSION_DOC_TYPES", "_PLAINTEXT_EXTENSIONS"):
        assert not hasattr(ingest_module, name), f"indexing/ingest.py 又自己抄了一份 {name}"
    assert not hasattr(
        chunking_router_module, "_TABLE_DOC_TYPES"
    ), "indexing/chunking_router.py 又自己抄了一份表格类清单"


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
