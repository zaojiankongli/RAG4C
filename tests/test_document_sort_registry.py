"""Axis #13 guards: a catalog sort is one declaration, and the keyset cursor stays single.

Four places used to have to agree about the sorts: an accepted-name frozenset, the ORDER BY
expression ladder, the direction ladder, and two hard-coded ``"updated_at_desc"`` cursor
checks. The one that could fail silently was the direction ladder — a new ascending sort
missing from it fell into the descending ``else`` and returned a plausible page in the wrong
order. These tests pin that behaviourally, not by reading code.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

import core.catalog as catalog_module
from core.catalog import list_documents_page
from core.document_sorts import (
    DocumentSortSpec,
    cursor_capable_sorts,
    document_sort_names,
    register_document_sort,
    resolve_document_sort,
    unregister_document_sort,
)
from tests.test_document_catalog_api import _document, _engine

HOST = Path(inspect.getsourcefile(catalog_module) or "")


def _seeded_engine() -> Any:
    engine = _engine()
    with Session(engine) as session:
        session.add_all(
            [
                _document("doc-old", name="Alpha", created_offset=0, updated_offset=0),
                _document("doc-new", name="Beta", created_offset=40, updated_offset=40),
            ]
        )
        session.commit()
    return engine


def _ids_sorted_by(engine: Any, sort: str) -> list[str]:
    page = list_documents_page(
        "tenant-a", "dataset-a", limit=50, sort=sort, engine_override=engine
    )
    ids = [str(item["id"]) for item in page["items"]]
    return ids


def _relative(ids: list[str]) -> str:
    older, newer = ids.index("doc-old"), ids.index("doc-new")
    return "first" if older < newer else "last"


# --------------------------------------------------------------------------- #
# 判据 §3.1：注册一个排序，宿主逐字节不变，而且方向与表达式都实时照声明走
# --------------------------------------------------------------------------- #


def test_a_new_sort_is_honoured_live_without_editing_the_catalog() -> None:
    before = HOST.read_bytes()
    engine = _seeded_engine()
    # 两个**新名字**共用同一个表达式，只有声明里的 direction 不同。旧的分支梯是
    # ``if 名字 in {created_at_asc, name_asc} -> asc else desc``，所以任何新的升序排序
    # 都会掉进 else 反着翻页 —— 只注册一个降序探针是测不出这件事的（我第一版就是这样，
    # 变异跑"方向退回硬编码"全绿）。
    register_document_sort(
        DocumentSortSpec(
            sort="probe_created_desc",
            direction="desc",
            expression=lambda table, _available: table.c.created_at,
        )
    )
    register_document_sort(
        DocumentSortSpec(
            sort="probe_created_asc",
            direction="asc",
            expression=lambda table, _available: table.c.created_at,
        )
    )
    try:
        assert _relative(_ids_sorted_by(engine, "probe_created_asc")) == "first"
        assert _relative(_ids_sorted_by(engine, "probe_created_desc")) == "last"
    finally:
        unregister_document_sort("probe_created_desc")
        unregister_document_sort("probe_created_asc")

    assert HOST.read_bytes() == before, "宿主里又长回一份排序梯"
    assert resolve_document_sort("probe_created_desc") is None
    with pytest.raises(ValueError, match="unsupported document sort"):
        _ids_sorted_by(engine, "probe_created_desc")


def test_an_unknown_sort_is_rejected_rather_than_defaulted() -> None:
    engine = _seeded_engine()
    with pytest.raises(ValueError, match="unsupported document sort"):
        _ids_sorted_by(engine, "size_desc")
    # 缺省不是"未知排序"，而是产品决定的默认那一个：省略与显式写它必须同序。
    assert _ids_sorted_by(engine, None) == _ids_sorted_by(  # type: ignore[arg-type]
        engine, "updated_at_desc"
    )


def test_a_cursor_is_only_accepted_for_the_sort_that_declared_it() -> None:
    engine = _seeded_engine()
    page = list_documents_page(
        "tenant-a", "dataset-a", limit=1, sort="updated_at_desc", engine_override=engine
    )
    assert page["next_cursor"], "声明了 keyset 的排序应该续出游标"
    again = list_documents_page(
        "tenant-a",
        "dataset-a",
        limit=1,
        sort="updated_at_desc",
        cursor=page["next_cursor"],
        engine_override=engine,
    )
    assert [i["id"] for i in again["items"]] != [i["id"] for i in page["items"]]

    # 没声明游标能力的排序即便被递一个格式正确的游标，也照样拒 —— 原先是比字面量。
    with pytest.raises(ValueError, match="cursor is supported only for"):
        list_documents_page(
            "tenant-a",
            "dataset-a",
            limit=1,
            sort="name_asc",
            cursor=page["next_cursor"],
            engine_override=engine,
        )


# --------------------------------------------------------------------------- #
# 内建声明的字面值 + keyset 单一实现者
# --------------------------------------------------------------------------- #


def test_the_builtin_sorts_keep_their_direction_and_cursor_privilege() -> None:
    assert set(document_sort_names()) == {"updated_at_desc", "created_at_asc", "name_asc"}
    assert resolve_document_sort("updated_at_desc").direction == "desc"  # type: ignore[union-attr]
    assert resolve_document_sort("updated_at_desc").keyset_cursor is True  # type: ignore[union-attr]
    assert resolve_document_sort("created_at_asc").direction == "asc"  # type: ignore[union-attr]
    assert resolve_document_sort("name_asc").direction == "asc"  # type: ignore[union-attr]
    assert resolve_document_sort("name_asc").keyset_cursor is False  # type: ignore[union-attr]
    assert cursor_capable_sorts() == ["updated_at_desc"]


def test_a_second_keyset_capable_sort_is_refused_not_silently_wrong() -> None:
    """游标谓词写死了 ``<``、编码值取自 updated_at 的 coalesce：第二个声明会翻错页。"""
    with pytest.raises(ValueError, match="updated_at_desc"):
        register_document_sort(
            DocumentSortSpec(
                sort="probe_cursor",
                direction="desc",
                expression=lambda table, _available: table.c.created_at,
                keyset_cursor=True,
            )
        )
    assert resolve_document_sort("probe_cursor") is None


def test_an_ascending_keyset_sort_is_refused_because_the_predicate_pages_downward() -> None:
    unregister_document_sort("updated_at_desc")
    try:
        with pytest.raises(ValueError, match="descending"):
            register_document_sort(
                DocumentSortSpec(
                    sort="probe_asc_cursor",
                    direction="asc",
                    expression=lambda table, _available: table.c.created_at,
                    keyset_cursor=True,
                )
            )
    finally:
        register_document_sort(
            DocumentSortSpec(
                sort="updated_at_desc",
                direction="desc",
                expression=catalog_module._sort_updated_at,  # noqa: SLF001
                keyset_cursor=True,
            )
        )
    assert cursor_capable_sorts() == ["updated_at_desc"]


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (
            DocumentSortSpec(sort="  ", direction="asc", expression=lambda t, a: t),
            "空排序名",
        ),
        (
            DocumentSortSpec(sort="Name_Asc", direction="asc", expression=lambda t, a: t),
            "非规范拼写当键",
        ),
        (
            DocumentSortSpec(sort="probe", direction="sideways", expression=lambda t, a: t),
            "未知方向",
        ),
        (DocumentSortSpec(sort="probe", direction="asc", expression=None), "表达式不可调用"),
    ],
)
def test_a_bad_declaration_dies_at_registration(spec: DocumentSortSpec, reason: str) -> None:
    before = set(document_sort_names())
    with pytest.raises(ValueError):
        register_document_sort(spec)
    assert set(document_sort_names()) == before, reason


def test_only_a_real_declaration_can_be_registered() -> None:
    before = set(document_sort_names())
    for junk in ({"sort": "junk"}, "junk", None):
        with pytest.raises(TypeError):
            register_document_sort(junk)  # type: ignore[arg-type]
    assert set(document_sort_names()) == before


def test_the_catalog_holds_no_copy_of_the_sort_ladders() -> None:
    assert not hasattr(catalog_module, "_DOCUMENT_SORTS"), "那份名字 frozenset 又长回来了"
    expression_source = inspect.getsource(catalog_module._document_catalog_sort_expression)  # noqa: SLF001
    assert 'sort == "name_asc"' not in expression_source
    assert "resolve_document_sort" in expression_source
    page_source = inspect.getsource(catalog_module.list_documents_page)
    assert 'if normalized_sort == "created_at_asc"' not in page_source
    assert "sort_spec.direction" in page_source
    decode_source = inspect.getsource(catalog_module._decode_document_cursor)  # noqa: SLF001
    assert 'payload.get("s") != "updated_at_desc"' not in decode_source
