"""PDF classification -> parser engine, as a declaration instead of a ladder.

``indexing/parsers/router.py`` decided which engine a PDF gets with a three-branch ladder
(``text_based`` -> fast, ``mixed`` -> vision, everything else -> vision). Two problems with
that shape:

* A new classifier result silently landed in the ``else`` branch. That happens to be the
  safe answer today (the heavier engine), but it is an accident of branch order — nothing
  said so, and nothing would keep a later edit from putting the cheap engine there.
* The fallback itself was implicit. Now ``UNKNOWN_PDF_TYPE_ROUTE`` is a value, and the
  router says so: an undeclared classification is routed to the *conservative* engine on
  purpose, and the reason is visible in the decision dict.

Scope, stated honestly: adding a **classification** costs one row here and zero edits to the
router. Adding an **engine** costs one entry in ``ENGINE_NAMES`` plus the dispatch in
``parse`` — an engine is a live parser object, not a label, so pretending otherwise would
just move the branch somewhere less visible.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PdfTypeRoute",
    "BUILTIN_PDF_TYPE_ROUTES",
    "ENGINE_NAMES",
    "UNKNOWN_PDF_TYPE_ROUTE",
    "register_pdf_type_route",
    "unregister_pdf_type_route",
    "resolve_pdf_type_route",
    "pdf_type_route_names",
]

#: Engine labels ``parse`` knows how to actually run.
ENGINE_NAMES = frozenset({"fast", "vision"})

#: The reason lands in ``documents.parser_meta`` and on the operator's diagnostics pane, so
#: it is a display field with a bounded value, not a free-form scratch pad.
REASON_MAX_CHARS = 200


@dataclass(frozen=True)
class PdfTypeRoute:
    """One classifier result and the engine it earns, plus why."""

    pdf_type: str
    engine: str
    reason: str = ""


#: What an undeclared classifier result does: the heavier engine, never the cheap one.
UNKNOWN_PDF_TYPE_ROUTE = PdfTypeRoute(
    pdf_type="*",
    engine="vision",
    reason="未声明的分类结果保守走 vision",
)

BUILTIN_PDF_TYPE_ROUTES: tuple[PdfTypeRoute, ...] = (
    PdfTypeRoute(
        pdf_type="text_based",
        engine="fast",
        reason="整本有文本层，fast 引擎足够",
    ),
    PdfTypeRoute(
        pdf_type="mixed",
        engine="vision",
        reason="MVP：混合型整本走 vision（页级混合拼接预留 pages_needing_ocr）",
    ),
    PdfTypeRoute(
        pdf_type="scanned",
        engine="vision",
        reason="扫描件需要视觉引擎",
    ),
    PdfTypeRoute(
        pdf_type="image_based",
        engine="vision",
        reason="整页图像需要视觉引擎",
    ),
    # 分类器没给出结论的三种情形也算"分类结果"：它们同样只能从这张表拿引擎，
    # 否则宿主里就还留着两处写死的 "engine": "vision"（改一处就没人发现）。
    PdfTypeRoute(
        pdf_type="other",
        engine="vision",
        reason="不是 PDF，交给通用（vision）解析路径",
    ),
    PdfTypeRoute(
        pdf_type="routing_disabled",
        engine="vision",
        reason="路由开关已关闭，按配置走通用解析路径",
    ),
    PdfTypeRoute(
        pdf_type="classification_failed",
        engine="vision",
        reason="分类器失败，保守交给能处理扫描件的视觉引擎（原因见 fallback_reason）",
    ),
)

_BY_TYPE: dict[str, PdfTypeRoute] = {}


def _validate(route: PdfTypeRoute) -> None:
    if not isinstance(route, PdfTypeRoute):
        raise TypeError(f"expected PdfTypeRoute, got {type(route).__name__}")
    if not isinstance(route.pdf_type, str) or not route.pdf_type.strip():
        raise ValueError("PdfTypeRoute.pdf_type must be a non-empty string")
    if route.pdf_type != route.pdf_type.strip().lower():
        raise ValueError(
            f"PdfTypeRoute.pdf_type must already be its own canonical key: {route.pdf_type!r}"
        )
    if route.pdf_type == UNKNOWN_PDF_TYPE_ROUTE.pdf_type:
        raise ValueError("the unknown-classification fallback is not a registerable key")
    if route.engine not in ENGINE_NAMES:
        raise ValueError(
            f"{route.pdf_type}: engine must be one of {sorted(ENGINE_NAMES)}; "
            "a new engine needs a parser object and a dispatch in parse, not just a label"
        )
    if not isinstance(route.reason, str) or not route.reason.strip():
        raise ValueError(
            f"{route.pdf_type}: reason must be a non-empty string — it is the operator-facing "
            "answer to \u201cwhy this engine\u201d, and a missing one reads as an unexplained choice"
        )
    if len(route.reason) > REASON_MAX_CHARS:
        raise ValueError(
            f"{route.pdf_type}: reason must stay within {REASON_MAX_CHARS} characters "
            f"(got {len(route.reason)}); it is stored in documents.parser_meta"
        )


def register_pdf_type_route(route: PdfTypeRoute, *, replace: bool = False) -> None:
    """Declare which engine one classifier result earns."""
    _validate(route)
    if route.pdf_type in _BY_TYPE and not replace:
        raise ValueError(f"pdf_type route already registered: {route.pdf_type}")
    _BY_TYPE[route.pdf_type] = route


def unregister_pdf_type_route(pdf_type: str) -> None:
    """Withdraw a declaration; unknown types are ignored (idempotent teardown)."""
    _BY_TYPE.pop(str(pdf_type).strip().lower(), None)


def resolve_pdf_type_route(pdf_type: object) -> PdfTypeRoute | None:
    """Look one classifier result up by exact spelling; ``None`` means "use the fallback"."""
    if not isinstance(pdf_type, str):
        return None
    return _BY_TYPE.get(pdf_type)


def pdf_type_route_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_TYPE))


for _route in BUILTIN_PDF_TYPE_ROUTES:
    register_pdf_type_route(_route)
