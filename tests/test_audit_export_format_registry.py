"""Axis #16 guards: an audit export format is one declaration, and an unknown one is refused.

Four places used to have to agree about a format: the accepted-name check and its message, the
serializer's ``if/else``, the filename extension, and the download response's media type. Two
of those could produce a **wrong artefact instead of an error**: the serializer's ``else``
emitted CSV for any unrecognised name, and the download path re-derived media type/extension
from the stored row with two more ``== "ndjson"`` ternaries — so a row naming something unknown
was served as CSV, named ``.csv``, with no complaint.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import core.enterprise_compliance as compliance
from core.audit_export_formats import (
    AuditExportFormatSpec,
    audit_export_format_names,
    register_audit_export_format,
    resolve_audit_export_format,
    unregister_audit_export_format,
)

HOST = Path(inspect.getsourcefile(compliance) or "")
ROWS: list[dict[str, Any]] = [{"id": "ev-1", "action": "document.deleted", "before_snapshot": None,
                               "after_snapshot": None}]


def _probe_spec(**overrides: Any) -> AuditExportFormatSpec:
    fields: dict[str, Any] = {
        "format": "tsv",
        "extension": "tsv",
        "media_type": "text/tab-separated-values",
        "serialize": lambda rows: ("\t".join(str(len(rows))) + "\n").encode("utf-8"),
    }
    fields.update(overrides)
    return AuditExportFormatSpec(**fields)


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一种格式，宿主逐字节不变且实时认它
# --------------------------------------------------------------------------- #


def test_a_new_format_is_honoured_live_without_editing_the_compliance_host() -> None:
    before = HOST.read_bytes()
    register_audit_export_format(_probe_spec())
    try:
        assert compliance._serialize_export(ROWS, "tsv") == b"1\n"  # noqa: SLF001
        assert "tsv" in audit_export_format_names()
    finally:
        unregister_audit_export_format("tsv")
    assert HOST.read_bytes() == before, "宿主里又长回一份格式判断"
    assert resolve_audit_export_format("tsv") is None
    with pytest.raises(compliance.ComplianceValidation):
        compliance._serialize_export(ROWS, "tsv")  # noqa: SLF001


def test_an_unrecognised_format_is_refused_instead_of_becoming_csv() -> None:
    """旧写法的 else 会返回 CSV 字节，而任务行上写着别的格式 —— 那是发出去一个错的工件。"""
    with pytest.raises(compliance.ComplianceValidation):
        compliance._serialize_export(ROWS, "parquet")  # noqa: SLF001
    with pytest.raises(compliance.ComplianceValidation):
        compliance._serialize_export(ROWS, "CSV")  # noqa: SLF001
    with pytest.raises(compliance.ComplianceValidation):
        compliance._serialize_export(ROWS, None)  # noqa: SLF001


# --------------------------------------------------------------------------- #
# 行为等价：两种内建格式的字节必须一个标点都不差
# --------------------------------------------------------------------------- #


def test_the_builtin_formats_still_produce_their_exact_bytes() -> None:
    row = {
        "id": "ev-1",
        "action": "document.deleted",
        "before_snapshot": None,
        "after_snapshot": None,
    }
    ndjson = compliance._serialize_export([row], "ndjson")  # noqa: SLF001
    assert ndjson.endswith(b"\n")
    assert ndjson.count(b"\n") == 1
    assert b'"action":"document.deleted"' in ndjson
    assert b'"before_snapshot":null' in ndjson
    assert compliance._serialize_export([], "ndjson") == b""  # noqa: SLF001

    csv_bytes = compliance._serialize_export([row], "csv")  # noqa: SLF001
    assert csv_bytes.startswith(b"\xef\xbb\xbf"), "CSV 仍要带 BOM，Excel 才不猜编码"
    assert b"\r\n" in csv_bytes
    assert b"before_snapshot" in csv_bytes.split(b"\r\n")[0]

    by_name = {name: resolve_audit_export_format(name) for name in ("ndjson", "csv")}
    assert by_name["ndjson"].media_type == "application/x-ndjson"
    assert by_name["csv"].media_type == "text/csv; charset=utf-8"
    assert by_name["ndjson"].extension == "ndjson"
    assert by_name["csv"].extension == "csv"


def test_csv_still_defends_against_formula_injection() -> None:
    """= / + / - / @ 开头的单元格要加前缀 —— 换表时最容易被丢掉的一条。"""
    out = compliance._serialize_export(  # noqa: SLF001
        [{"id": "=HYPERLINK(\"http://evil\")", "action": "x"}], "csv"
    )
    assert b'"\'=HYPERLINK' in out or b"'=HYPERLINK" in out


# --------------------------------------------------------------------------- #
# 注册期把形状判死
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"format": "  "}, "空格式名"),
        ({"format": "TSV"}, "非规范拼写当键"),
        ({"extension": ""}, "空扩展名"),
        ({"extension": "../evil"}, "扩展名里带路径片段"),
        ({"extension": "a/b"}, "扩展名里带斜杠"),
        ({"media_type": "  "}, "空媒体类型"),
        ({"serialize": "not-callable"}, "序列化器不可调用"),
    ],
)
def test_a_bad_declaration_dies_at_registration(overrides: dict[str, Any], reason: str) -> None:
    before = set(audit_export_format_names())
    with pytest.raises(ValueError):
        register_audit_export_format(_probe_spec(**overrides))
    assert set(audit_export_format_names()) == before, reason


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(audit_export_format_names())
    for junk in ({"format": "junk"}, "junk", None):
        with pytest.raises(TypeError):
            register_audit_export_format(junk)  # type: ignore[arg-type]
    assert set(audit_export_format_names()) == before


def test_replacing_an_existing_format_is_explicit() -> None:
    register_audit_export_format(_probe_spec())
    try:
        with pytest.raises(ValueError):
            register_audit_export_format(_probe_spec(media_type="other"))
        register_audit_export_format(_probe_spec(media_type="other"), replace=True)
        assert resolve_audit_export_format("tsv").media_type == "other"  # type: ignore[union-attr]
    finally:
        unregister_audit_export_format("  TSV ")
    assert resolve_audit_export_format("tsv") is None


# --------------------------------------------------------------------------- #
# 宿主守卫：那四处判断不许再各抄一遍
# --------------------------------------------------------------------------- #


def test_a_stored_row_with_an_unknown_format_is_refused_not_served_as_csv() -> None:
    """下载贴标签这一步单独抽出来，才不用先把一个导出任务写进存储再下载它。"""
    assert compliance._download_format("csv").media_type == "text/csv; charset=utf-8"  # noqa: SLF001
    assert compliance._download_format("ndjson").extension == "ndjson"  # noqa: SLF001
    for unknown in ("parquet", "CSV", "", None):
        with pytest.raises(compliance.ComplianceConflict):
            compliance._download_format(unknown)  # type: ignore[arg-type]
    # 注册的格式在这里一样被认，且宿主没为它加过一行
    register_audit_export_format(_probe_spec())
    try:
        assert compliance._download_format("tsv").media_type == "text/tab-separated-values"  # noqa: SLF001
    finally:
        unregister_audit_export_format("tsv")


def test_the_host_holds_no_copy_of_the_format_decisions() -> None:
    serialize_source = inspect.getsource(compliance._serialize_export)  # noqa: SLF001
    assert 'format_name == "ndjson"' not in serialize_source
    assert "resolve_audit_export_format" in serialize_source

    download_source = inspect.getsource(compliance.download_audit_export)  # noqa: SLF001
    assert "if format_name == " not in download_source
    assert "_download_format(" in download_source
    assert "download_spec.media_type" in download_source
    # 存着不认识格式的导出任务必须拒绝下载，而不是按 CSV 发出去
    assert 'raise ComplianceConflict(' in download_source
    # 那句"仅支持 …"的文案必须从表里长出来；写死一份的话，加一种格式就变成一句过时的话
    host_source = HOST.read_text("utf-8")
    assert "extension = \"ndjson\" if clean_format == \"ndjson\" else \"csv\"" not in host_source
    assert "resolve_audit_export_format(clean_format).extension" in host_source
    assert '"format 仅支持 ndjson/csv"' not in host_source
    assert '"/".join(audit_export_format_names())' in host_source
