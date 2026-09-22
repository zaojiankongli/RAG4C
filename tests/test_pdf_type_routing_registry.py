"""Axis #14 guards: a classifier result earns an engine through a declaration.

The ladder this replaces decided ``text_based`` -> fast and everything else -> vision. Two
things were unowned: an unrecognised classifier value fell into whatever the ``else``
happened to be, and an unrecognised **engine label** fell into the vision branch of
``parse`` — i.e. a typo in a route could quietly run a heavy engine instead of failing.
Both are explicit now.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import indexing.parsers.router as router_module
from indexing.pdf_type_routing import (
    BUILTIN_PDF_TYPE_ROUTES,
    ENGINE_NAMES,
    PdfTypeRoute,
    pdf_type_route_names,
    register_pdf_type_route,
    resolve_pdf_type_route,
    unregister_pdf_type_route,
)
from indexing.parsers.base import MineruParserError
from indexing.parsers.router import DocumentRouter


class _FakeFast:
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        self.parsed = 0

    def classify(self, file_path: str) -> dict[str, Any]:
        return dict(self.decision)

    def parse(self, file_path: str) -> Any:
        self.parsed += 1
        return SimpleNamespace(metadata={}, layout=[], engine="fast")


class _FakeVision:
    def __init__(self) -> None:
        self.parsed = 0

    def supports(self, file_path: str) -> bool:
        return True

    def parse(self, file_path: str) -> Any:
        self.parsed += 1
        return SimpleNamespace(metadata={}, layout=[], engine="vision")


def _router(decision: dict[str, Any]) -> tuple[DocumentRouter, _FakeFast, _FakeVision]:
    fast, vision = _FakeFast(decision), _FakeVision()
    return DocumentRouter(vision_parser=vision, fast_parser=fast, page_limit=0), fast, vision


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一种分类结果，宿主零改动且实时照它路由
# --------------------------------------------------------------------------- #


def test_a_new_classification_is_routed_live_without_editing_the_router() -> None:
    host = Path(inspect.getsourcefile(router_module) or "")
    before = host.read_bytes()
    register_pdf_type_route(PdfTypeRoute(pdf_type="vector_heavy", engine="fast", reason="探针"))
    try:
        router, fast, vision = _router({"pdf_type": "vector_heavy", "confidence": 0.7})
        decision = router.route("docs/a.pdf")
        assert decision["engine"] == "fast"
        assert decision["route_reason"] == "探针"
        router.parse("docs/a.pdf")
        assert (fast.parsed, vision.parsed) == (1, 0)
    finally:
        unregister_pdf_type_route("vector_heavy")

    assert host.read_bytes() == before, "宿主又长回一份分支梯"
    assert resolve_pdf_type_route("vector_heavy") is None
    # 撤掉声明后同一本走保守落点，而不是沿用刚才那次决策。
    router, fast, vision = _router({"pdf_type": "vector_heavy", "confidence": 0.7})
    assert router.route("docs/a.pdf")["engine"] == "vision"


def test_an_undeclared_classification_conservatively_goes_to_vision() -> None:
    """新分类结果默认走更重的引擎 —— 这条必须是有意的，不是分支顺序碰巧。"""
    router, _fast, vision = _router({"pdf_type": "brand_new_inspector_output", "confidence": 0.2})
    decision = router.route("docs/x.pdf")
    assert decision["engine"] == "vision"
    assert "保守" in decision["route_reason"]
    router.parse("docs/x.pdf")
    assert vision.parsed == 1


# --------------------------------------------------------------------------- #
# 行为等价：改表之前的四条决定，逐条钉住（这条轴此前**没有**任何用例覆盖）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pdf_type", "engine"),
    [
        ("text_based", "fast"),
        ("mixed", "vision"),
        ("scanned", "vision"),
        ("image_based", "vision"),
    ],
)
def test_the_builtin_routes_keep_their_engine(pdf_type: str, engine: str) -> None:
    router, fast, vision = _router({"pdf_type": pdf_type, "confidence": 0.9})
    assert router.route("docs/a.pdf")["engine"] == engine
    router.parse("docs/a.pdf")
    assert (fast.parsed, vision.parsed) == ((1, 0) if engine == "fast" else (0, 1))


def test_mixed_keeps_the_page_level_note_that_justifies_the_heavier_engine() -> None:
    """混合型走 vision 是 MVP 决定，理由要留在能被读到的地方。"""
    reason = {r.pdf_type: r.reason for r in BUILTIN_PDF_TYPE_ROUTES}["mixed"]
    assert "MVP" in reason and "pages_needing_ocr" in reason


# --------------------------------------------------------------------------- #
# 引擎标签必须真跑得起来
# --------------------------------------------------------------------------- #


def test_an_unknown_engine_label_fails_loudly_instead_of_running_vision() -> None:
    """``parse`` 的这一支是**纵深防御**，不是唯一的栅栏：今天的 route() 只会给出
    表里校验过的标签，所以要靠替换 route() 才够得到（真正的栅栏是注册期的
    ``engine in ENGINE_NAMES``）。留着是因为宿主允许被继承，不是因为它曾经漏过 ——
    原先那个 else 也同样是够不到的分支，改之前并没有东西真的 fail-open。
    """
    fast = _FakeFast({"pdf_type": "text_based", "confidence": 1.0})
    vision = _FakeVision()
    router = DocumentRouter(vision_parser=vision, fast_parser=fast, page_limit=0)
    original_route = router.route
    router.route = lambda path: {**original_route(path), "engine": "quantum"}  # type: ignore[method-assign]
    with pytest.raises(MineruParserError):
        router.parse("docs/a.pdf")
    assert vision.parsed == 0


# --------------------------------------------------------------------------- #
# 决策的**全部内容**要落到解析 metadata：判由、引擎、分类器自己的事实
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("engine", ["fast", "vision"])
def test_the_whole_decision_reaches_the_parse_metadata_not_just_the_engine(
    engine: str,
) -> None:
    """评审发现 P1/P3/P5：``route()`` 只回 ``{"engine", "route_reason"}`` 而丢掉分类器给的
    其余事实、fast 分支不再记 ``metadata["router"]``、或把它记到别的键下 —— 15 条守卫全绿。
    这三件事都只会体现在落库的 parser_meta 里，而前端就是读那一列，所以在这里钉实时产物。
    """
    pdf_type = "text_based" if engine == "fast" else "scanned"
    router, _fast, _vision = _router(
        {"pdf_type": pdf_type, "confidence": 0.83, "pages_needing_ocr": [3, 7]}
    )
    parsed = router.parse("docs/a.pdf")
    recorded = parsed.metadata["router"]
    assert recorded["engine"] == engine
    assert recorded["pdf_type"] == pdf_type, "分类器给的事实没跟过来，前端就只能显示引擎名"
    assert recorded["confidence"] == 0.83
    assert recorded["route_reason"], "判由没记下来，操作员看不见为什么走这个引擎"
    assert recorded["route_reason"] == resolve_pdf_type_route(pdf_type).reason  # type: ignore[union-attr]


def test_the_reason_is_a_bounded_operator_facing_string() -> None:
    """评审发现 E：判由会进 ``documents.parser_meta`` 并被前端直接渲染，但注册期只看
    pdf_type 和 engine，reason 给多长、什么类型都不问。
    """
    from indexing.pdf_type_routing import REASON_MAX_CHARS

    for bad in ("", "   ", "x" * (REASON_MAX_CHARS + 1), 42, None, {"why": "vision"}):
        before = set(pdf_type_route_names())
        with pytest.raises(ValueError):
            register_pdf_type_route(PdfTypeRoute(pdf_type="probe_reason", engine="vision", reason=bad))  # type: ignore[arg-type]
        assert set(pdf_type_route_names()) == before, f"{bad!r} 不该注册成功"
    for route in BUILTIN_PDF_TYPE_ROUTES:
        assert 0 < len(route.reason) <= REASON_MAX_CHARS, route.pdf_type


def test_a_duplicate_route_declaration_is_refused_without_an_explicit_replace() -> None:
    """评审发现 P2：重名栅栏删掉后全绿 —— 两条声明抢同一个分类结果时，后写的悄悄赢。"""
    with pytest.raises(ValueError, match="already registered"):
        register_pdf_type_route(PdfTypeRoute(pdf_type="text_based", engine="vision", reason="抢位"))
    assert resolve_pdf_type_route("text_based").engine == "fast"  # type: ignore[union-attr]
    register_pdf_type_route(
        PdfTypeRoute(pdf_type="text_based", engine="vision", reason="显式替换"), replace=True
    )
    try:
        assert resolve_pdf_type_route("text_based").engine == "vision"  # type: ignore[union-attr]
    finally:
        register_pdf_type_route(
            PdfTypeRoute(
                pdf_type="text_based",
                engine="fast",
                reason="整本有文本层，fast 引擎足够",
            ),
            replace=True,
        )
    assert resolve_pdf_type_route("text_based").engine == "fast"  # type: ignore[union-attr]


def test_a_route_may_only_name_an_engine_that_can_actually_run() -> None:
    before = set(pdf_type_route_names())
    with pytest.raises(ValueError):
        register_pdf_type_route(PdfTypeRoute(pdf_type="typo", engine="vison"))
    assert set(pdf_type_route_names()) == before
    assert "vison" not in ENGINE_NAMES


@pytest.mark.parametrize(
    ("route", "reason"),
    [
        (PdfTypeRoute(pdf_type="  ", engine="fast"), "空分类名"),
        (PdfTypeRoute(pdf_type="Mixed", engine="vision"), "非规范拼写当键"),
        (PdfTypeRoute(pdf_type="*", engine="vision"), "占用保守落点"),
        (PdfTypeRoute(pdf_type="ok", engine=""), "空引擎标签"),
    ],
)
def test_a_bad_declaration_dies_at_registration(route: PdfTypeRoute, reason: str) -> None:
    before = set(pdf_type_route_names())
    with pytest.raises(ValueError):
        register_pdf_type_route(route)
    assert set(pdf_type_route_names()) == before, reason


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(pdf_type_route_names())
    for junk in ({"pdf_type": "junk", "engine": "fast"}, "junk", None):
        with pytest.raises(TypeError):
            register_pdf_type_route(junk)  # type: ignore[arg-type]
    assert set(pdf_type_route_names()) == before


def test_the_router_holds_no_copy_of_the_classification_ladder() -> None:
    source = inspect.getsource(DocumentRouter.route)
    assert 'pdf_type == "text_based"' not in source
    assert 'elif pdf_type == "mixed"' not in source
    assert "resolve_pdf_type_route" in source
    parse_source = inspect.getsource(DocumentRouter.parse)
    assert 'if engine == "fast"' in parse_source
    assert 'elif engine == "vision"' in parse_source
