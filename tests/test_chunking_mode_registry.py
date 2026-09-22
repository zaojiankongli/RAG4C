"""切分模式注册表的扩展点守卫。

判据与 ``docs/compose/spec/chunk-lifecycle-writers.md`` S2.5 同源：可扩展性要能被测，
即"新增一个实现 = 一次注册"，而不是"新增一个实现 = 改三处分支"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from indexing.chunking_router import (
    CHUNKING_MODES,
    ChunkingRouter,
    DEFAULT_SIMPLE_MAX_CHARS,
)

SOURCE = Path(__file__).resolve().parents[1] / "indexing" / "chunking_router.py"


class _ProbeMode:
    name = "probe"
    reason_code = "probe_mode"

    def chunk(self, request):
        return [f"probe:{request.doc_id}:{request.start_seq}"]


def test_router_dispatches_by_registered_mode_name() -> None:
    """A mode registered after import is usable by a freshly built router."""
    CHUNKING_MODES.register("probe", lambda _cfg: _ProbeMode())
    try:
        router = ChunkingRouter()
        assert "probe" in router.available_modes
        assert router.chunk_with_mode("probe", "doc-1", "text") == ["probe:doc-1:0"]
    finally:
        CHUNKING_MODES.unregister("probe")

    assert "probe" not in ChunkingRouter().available_modes


def test_unknown_mode_is_rejected_instantly_instead_of_falling_back() -> None:
    """A typo must not quietly re-chunk the document another way.

    The pre-refactor router returned parent_child for any unrecognised mode, so an
    invalid ``chunking_mode`` produced plausible-but-wrong chunks with no signal.
    """
    router = ChunkingRouter()
    with pytest.raises(ValueError, match="未知切分模式"):
        router.chunk_with_mode("parent_chld", "doc-1", "text")
    with pytest.raises(ValueError, match="非法切分模式"):
        ChunkingRouter(mode="parent_chld")


def test_router_never_compares_a_mode_name_with_equality() -> None:
    """The per-mode if-chain must not come back.

    ``self.mode != "auto"`` is the one allowed comparison: it distinguishes
    "operator pinned a mode" from "consult the routing table", not one mode from another.
    """
    source = SOURCE.read_text(encoding="utf-8")
    for needle in ('mode == "qa"', 'mode == "recursive"', 'mode == "parent_child"',
                   'mode == "probe"'):
        assert needle not in source, f"切分路由重新出现了按名分支: {needle}"


def test_routing_table_keeps_its_documented_decision_order() -> None:
    """Table docs -> qa, short & layout-free -> recursive, everything else -> parent_child.

    These assertions are the reason the rules are ordered data rather than inline ifs:
    reordering them must be visible in a test, not in a chunk diff.
    """
    router = ChunkingRouter(simple_max_chars=100)
    assert router.decide("csv", "x" * 500) == "qa"
    assert router.decide("markdown", "short text") == "recursive"
    assert router.decide("markdown", "x" * 500) == "parent_child"
    assert router.decide("markdown", "short text", layout=[{"type": "table"}]) == "parent_child"
    assert ChunkingRouter(mode="qa").decide("markdown", "short text") == "qa"


def test_explicit_and_auto_reason_codes_stay_machine_stable() -> None:
    """``parser_meta.chunking_reason_code`` is filtered on in the console UI."""
    router = ChunkingRouter(simple_max_chars=DEFAULT_SIMPLE_MAX_CHARS)
    assert router.explain_decision("excel", "a,b\n1,2")["reason_code"] == "table_doc_type"
    assert router.explain_decision("txt", "short")["reason_code"] == "simple_short_no_layout"
    assert router.explain_decision("pdf", "x" * 9000)["reason_code"] == "complex_or_structured"
    assert ChunkingRouter(mode="recursive").explain_decision("txt", "s")["reason_code"] == (
        "explicit_mode"
    )


def test_injected_chunkers_still_reach_their_mode() -> None:
    """Per-instance chunker overrides must not be lost by the registry."""
    sentinel = object()

    class _Stub:
        def chunk_document(self, doc_id, text, **_kwargs):  # noqa: ANN001, D102
            return [sentinel]

    router = ChunkingRouter(recursive=_Stub())  # type: ignore[arg-type]
    assert router.chunk_with_mode("recursive", "doc-1", "text") == [sentinel]
