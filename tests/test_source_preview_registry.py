"""Axis: viewable source kinds. The table decides what may be shown and as what.

Two failures this shape prevents, neither of which had an owner before:

* Serving an unknown type as ``application/octet-stream`` and letting the browser decide —
  which is how a stored ``.html`` runs as a script on the console's own origin.
* Labelling bytes by *family* instead of by suffix. Five image extensions are one kind but
  five media types, and a JPEG served as ``image/png`` is broken under ``nosniff``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

import core.source_preview_access as access
from core.source_preview_access import SourcePreviewRefused, locate_preview_source
from core.source_previews import (
    BUILTIN_SOURCE_PREVIEWS,
    INLINE_SAFE_MEDIA_TYPES,
    SourcePreviewSpec,
    register_source_preview,
    resolve_source_preview,
    resolve_source_preview_for_path,
    source_preview_suffix_names,
    unregister_source_preview,
)

HOST = Path(inspect.getsourcefile(access) or "")


def _spec(**overrides: Any) -> SourcePreviewSpec:
    fields: dict[str, Any] = {
        "suffix": ".probe",
        "kind": "probe",
        "media_type": "application/pdf",
        "inline_renderable": True,
        "max_bytes": 1024,
    }
    fields.update(overrides)
    return SourcePreviewSpec(**fields)


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一个后缀，取文件这一侧零改动且实时照它办事
# --------------------------------------------------------------------------- #


def test_a_new_suffix_is_honoured_live_by_the_locator(tmp_path: Path) -> None:
    before = HOST.read_bytes()
    root = tmp_path / "original s"  # 名字里带空格：root 的规范化不能把空格当分隔
    root.mkdir()
    original = root / "diagram.probe"
    original.write_bytes(b"%PDF-1.4 probe")

    register_source_preview(_spec(max_bytes=6))
    try:
        # 声明里的 max_bytes 实时生效：6 字节上限装不下这 14 字节文件。
        with pytest.raises(SourcePreviewRefused) as oversize:
            locate_preview_source(original, roots=[root])
        assert oversize.value.code == "source_preview_too_large"

        register_source_preview(_spec(max_bytes=4096), replace=True)
        found = locate_preview_source(original, roots=[root])
        assert found.spec.kind == "probe"
        assert found.spec.media_type == "application/pdf"
        assert found.spec.inline_renderable is True
        assert found.size == 14
    finally:
        unregister_source_preview(".probe")

    assert HOST.read_bytes() == before, "取文件这一侧又长回一份后缀清单"
    assert resolve_source_preview(".probe") is None
    with pytest.raises(SourcePreviewRefused) as gone:
        locate_preview_source(original, roots=[root])
    assert gone.value.code == "source_preview_unsupported_kind"


# --------------------------------------------------------------------------- #
# 内建声明的字面值
# --------------------------------------------------------------------------- #


def test_the_builtin_rows_keep_their_exact_contracts() -> None:
    assert source_preview_suffix_names() == tuple(sorted(row.suffix for row in BUILTIN_SOURCE_PREVIEWS))
    pdf = resolve_source_preview(".pdf")
    assert (pdf.kind, pdf.media_type, pdf.inline_renderable) == (  # type: ignore[union-attr]
        "pdf",
        "application/pdf",
        True,
    )
    # 同一族的两个后缀必须是**各自**的 media type —— 按家族标就是这条被写坏的地方。
    assert resolve_source_preview(".jpg").media_type == "image/jpeg"  # type: ignore[union-attr]
    assert resolve_source_preview(".jpeg").media_type == "image/jpeg"  # type: ignore[union-attr]
    assert resolve_source_preview(".png").media_type == "image/png"  # type: ignore[union-attr]
    for suffix in (".docx", ".xlsx", ".pptx"):
        row = resolve_source_preview(suffix)
        assert row is not None and row.inline_renderable is False, suffix
        assert row.kind == "office"
    assert resolve_source_preview(".md").media_type == "text/plain"  # type: ignore[union-attr]
    assert resolve_source_preview(".md").max_bytes < resolve_source_preview(".pdf").max_bytes  # type: ignore[union-attr,operator]


def test_every_inline_row_names_a_media_type_that_cannot_execute_the_document() -> None:
    for row in BUILTIN_SOURCE_PREVIEWS:
        if row.inline_renderable:
            assert row.media_type in INLINE_SAFE_MEDIA_TYPES, row.suffix
    assert "text/html" not in INLINE_SAFE_MEDIA_TYPES
    assert "image/svg+xml" not in INLINE_SAFE_MEDIA_TYPES
    assert "application/octet-stream" not in INLINE_SAFE_MEDIA_TYPES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("/var/lib/rag4c/a.pdf", ".pdf"), ("C:\\kb\\a.PDF", ".pdf"), ("/kb/no-extension", None)],
)
def test_lookup_answers_by_suffix_and_is_case_insensitive(raw: str, expected: str | None) -> None:
    row = resolve_source_preview_for_path(raw)
    assert (row.suffix if row else None) == expected


# --------------------------------------------------------------------------- #
# 注册期把形状判死
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"suffix": "pdf"}, "少了点：'.pdf' 与 'pdf' 会变成两种拼法"),
        ({"suffix": ".PDF"}, "大写键"),
        ({"suffix": " .pdf "}, "带空格的键"),
        ({"suffix": ".probe-x-long-suffix"}, "后缀过长"),
        ({"suffix": ""}, "空后缀"),
        ({"kind": " "}, "空 kind"),
        ({"kind": "Probe"}, "非规范 kind"),
        ({"media_type": "pdf"}, "media_type 不像 type/subtype"),
        ({"media_type": "Text/HTML"}, "大写 media_type"),
        ({"media_type": "text/html"}, "浏览器会执行文档内容，不许 inline"),
        ({"media_type": "image/svg+xml"}, "SVG 会带脚本，不许 inline"),
        ({"media_type": "application/octet-stream"}, "兜底类型不许 inline"),
        ({"media_type": ""}, "空 media_type"),
        ({"max_bytes": 0}, "零上限"),
        ({"max_bytes": -5}, "负上限"),
        ({"max_bytes": True}, "bool 不是字节数"),
        ({"max_bytes": "4096"}, "字符串不是字节数"),
    ],
)
def test_a_bad_declaration_dies_at_registration(overrides: dict[str, Any], reason: str) -> None:
    before = set(source_preview_suffix_names())
    with pytest.raises(ValueError):
        register_source_preview(_spec(**overrides))
    assert set(source_preview_suffix_names()) == before, reason


def test_a_download_only_row_may_still_name_any_type() -> None:
    """不 inline 的行不受白名单约束（office 那三行就是），但它也不会被浏览器就地渲染。"""
    register_source_preview(
        _spec(suffix=".probe2", kind="probe", media_type="text/html", inline_renderable=False)
    )
    try:
        assert resolve_source_preview(".probe2").inline_renderable is False  # type: ignore[union-attr]
    finally:
        unregister_source_preview(".probe2")


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(source_preview_suffix_names())
    for junk in ({"suffix": ".x"}, ".x", None):
        with pytest.raises(TypeError):
            register_source_preview(junk)  # type: ignore[arg-type]
    assert set(source_preview_suffix_names()) == before


def test_reassigning_a_suffix_needs_an_explicit_replace() -> None:
    register_source_preview(_spec())
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_source_preview(_spec(media_type="image/png"))
        register_source_preview(_spec(media_type="image/png"), replace=True)
        assert resolve_source_preview(".probe").media_type == "image/png"  # type: ignore[union-attr]
    finally:
        unregister_source_preview(" .PROBE ")
    assert resolve_source_preview(".probe") is None


# --------------------------------------------------------------------------- #
# 落点收口：这是这个端点真正的安全面
# --------------------------------------------------------------------------- #


def test_no_roots_means_the_feature_is_off_not_permissive(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4")
    for roots in ([], [""], [None], ["   "]):
        with pytest.raises(SourcePreviewRefused) as excinfo:
            locate_preview_source(f, roots=roots)
        assert excinfo.value.code == "source_preview_disabled"


def test_a_path_outside_every_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "secrets"
    outside.mkdir()
    secret = outside / "id.pdf"
    secret.write_bytes(b"%PDF-1.4 secret")
    with pytest.raises(SourcePreviewRefused) as excinfo:
        locate_preview_source(secret, roots=[root])
    assert excinfo.value.code == "source_preview_out_of_scope"


def test_a_traversal_that_climbs_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    escapee = tmp_path / "outside.pdf"
    escapee.write_bytes(b"%PDF-1.4")
    sneaky = root / ".." / "outside.pdf"
    with pytest.raises(SourcePreviewRefused) as excinfo:
        locate_preview_source(sneaky, roots=[root])
    assert excinfo.value.code == "source_preview_out_of_scope"


def test_a_symlink_inside_the_root_that_points_out_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = elsewhere / "lease.pdf"
    real.write_bytes(b"%PDF-1.4")
    try:
        (root / "link.pdf").symlink_to(real)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无符号链接权限
        pytest.skip("this checkout cannot create symlinks")
    with pytest.raises(SourcePreviewRefused) as excinfo:
        locate_preview_source(root / "link.pdf", roots=[root])
    assert excinfo.value.code == "source_preview_out_of_scope"


def test_a_symlink_inside_the_root_is_typed_by_the_bytes_it_reaches_not_by_its_own_name(
    tmp_path: Path,
) -> None:
    """越界检查只管"跳出去"，管不到**root 内**的符号链接。按记录下来的原始名判类型，
    root 里一个 ``report.pdf -> payload.html`` 就会拿到 ``application/pdf`` 却交出 HTML：
    界面把它塞进 iframe，而 content type 说的不是这些字节。
    """
    root = tmp_path / "kb"
    root.mkdir()
    (root / "payload.html").write_bytes(b"<script>alert(1)</script>")
    try:
        (root / "report.pdf").symlink_to(root / "payload.html")
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无符号链接权限
        pytest.skip("this checkout cannot create symlinks")
    with pytest.raises(SourcePreviewRefused) as excinfo:
        locate_preview_source(root / "report.pdf", roots=[root])
    assert excinfo.value.code == "source_preview_unsupported_kind", (
        "原始名 .pdf 在表里 —— 放行就是按名字贴标签"
    )


def test_the_reverse_symlink_is_served_because_the_target_is_what_gets_read(
    tmp_path: Path,
) -> None:
    """上一条的反向，用来挡住"那就干脆禁止符号链接"这种过修：真正的判据是**交出去的字节
    有一个登记的类型**，不是路径里出现过链接。target 登记、link 名没登记 → 应该正常放行，
    且 spec 来自 target。
    """
    root = tmp_path / "kb"
    root.mkdir()
    target = root / "real.probe"
    payload = b"%PDF-1.4 target"
    target.write_bytes(payload)
    try:
        (root / "alias.html").symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无符号链接权限
        pytest.skip("this checkout cannot create symlinks")
    register_source_preview(_spec(max_bytes=4096))
    try:
        found = locate_preview_source(root / "alias.html", roots=[root])
        assert found.spec.suffix == ".probe"
        assert found.path == target.resolve()
        assert found.size == len(payload)
    finally:
        unregister_source_preview(".probe")


def test_the_root_itself_counts_and_a_directory_is_not_a_file(tmp_path: Path) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    register_source_preview(_spec(suffix=".dirprobe", kind="probe", media_type="text/plain",
                                  inline_renderable=False, max_bytes=10))
    try:
        with pytest.raises(SourcePreviewRefused) as excinfo:
            locate_preview_source(root / "nested.dirprobe", roots=[root])
        assert excinfo.value.code == "source_preview_missing", "文件不存在要说存在性问题，不说越界"
        with pytest.raises(SourcePreviewRefused) as dir_excinfo:
            locate_preview_source(root, roots=[root])
        assert dir_excinfo.value.code == "source_preview_unsupported_kind"
    finally:
        unregister_source_preview(".dirprobe")


def test_an_unknown_suffix_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    """不给未知类型一个默认 content type：这条必须在读字节之前就断。"""
    weird = tmp_path / "payload.html"
    weird.write_bytes(b"<script>alert(1)</script>")
    with pytest.raises(SourcePreviewRefused) as excinfo:
        locate_preview_source(weird, roots=[tmp_path])
    assert excinfo.value.code == "source_preview_unsupported_kind"


def test_an_empty_path_is_missing_rather_than_the_current_directory(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    try:
        for blank in ("", "   ", None):
            with pytest.raises(SourcePreviewRefused) as excinfo:
                locate_preview_source(blank, roots=[tmp_path])
            assert excinfo.value.code in {"source_preview_missing", "source_preview_unsupported_kind"}
    finally:
        monkeypatch.undo()


def test_the_size_ceiling_can_only_be_lowered_by_the_caller(tmp_path: Path) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    f = root / "big.pdf"
    f.write_bytes(b"x" * 100)
    assert locate_preview_source(f, roots=[root]).size == 100
    with pytest.raises(SourcePreviewRefused) as tightened:
        locate_preview_source(f, roots=[root], max_bytes=10)
    assert tightened.value.code == "source_preview_too_large"
    # 调用方把上限抬高也没用：这一类的上限是表说了算。
    assert locate_preview_source(f, roots=[root], max_bytes=10 * 1024 * 1024).size == 100
