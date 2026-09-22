"""Axis #13 guards: a catalog sort is one declaration, and the keyset cursor stays single.

Four places used to have to agree about the sorts: an accepted-name frozenset, the ORDER BY
expression ladder, the direction ladder, and the ``"updated_at_desc"`` comparisons — which
were five, not the two the first pass claimed (expression ladder, encoder, decoder, and two
paging checks). The encoder's was missed there and is converted now. The failure that could
stay silent was the direction ladder: a new ascending sort missing from it fell into the
descending ``else`` and returned a plausible page in the wrong order. These tests pin that
behaviourally, not by reading code.
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
                # 两行 created 与 updated **顺序不一致**的数据：没有它们，"表达式按 A 列排序、
                # 游标却按 B 列取值"这种错配在任何断言上都看不出来（评审发现 D）。
                _document("doc-created-late", name="Gamma", created_offset=90, updated_offset=5),
                _document("doc-updated-late", name="Delta", created_offset=5, updated_offset=90),
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
    """游标谓词写死了 ``<``、编码值必须是 datetime：第二个声明会翻错页。"""
    with pytest.raises(ValueError, match="updated_at_desc"):
        register_document_sort(
            DocumentSortSpec(
                sort="probe_cursor",
                direction="desc",
                expression=lambda table, _available: table.c.created_at,
                keyset_cursor=True,
                cursor_value=lambda row: row.get("created_at"),
            )
        )
    assert resolve_document_sort("probe_cursor") is None


def test_a_keyset_declaration_must_carry_the_value_it_pages_on() -> None:
    """评审发现 B(ii)： unregister 内建后再注册一个"自称能用游标"的排序，表只能数名字，
    数不出它的 ORDER BY 和游标编码是不是同一列 —— 于是运行时才炸。现在这件事在注册期判死：
    要游标能力就得同时交出取值方式，表达式与游标值同出一门。
    """
    unregister_document_sort("updated_at_desc")
    try:
        with pytest.raises(ValueError, match="cursor_value"):
            register_document_sort(
                DocumentSortSpec(
                    sort="probe_nameless_cursor",
                    direction="desc",
                    expression=lambda table, _available: table.c.created_at,
                    keyset_cursor=True,
                )
            )
        assert resolve_document_sort("probe_nameless_cursor") is None
    finally:
        catalog_module.register_builtin_document_sorts()


def test_replacing_the_sort_that_holds_the_cursor_is_allowed() -> None:
    """单一实现者的计数不能把自己算进去：重新声明内建是合法操作。"""
    spec = resolve_document_sort("updated_at_desc")
    assert spec is not None and spec.keyset_cursor
    register_document_sort(spec, replace=True)  # 不加 replace 会先撞"重名"，加了就不该再撞游标
    assert cursor_capable_sorts() == ["updated_at_desc"]


def test_the_builtin_declarations_survive_a_second_instantiation() -> None:
    """评审发现 G：宿主在自己的模块末尾往导入的表里注册，第二次实例化（reload / 副本装载）
    会直接 ImportError 在启动路径上。内建声明必须可重放。
    """
    catalog_module.register_builtin_document_sorts()
    catalog_module.register_builtin_document_sorts()
    assert set(document_sort_names()) == {"updated_at_desc", "created_at_asc", "name_asc"}
    assert cursor_capable_sorts() == ["updated_at_desc"]


def test_a_duplicate_sort_name_is_refused_without_an_explicit_replace() -> None:
    before = set(document_sort_names())
    spec = resolve_document_sort("name_asc")
    assert spec is not None
    with pytest.raises(ValueError, match="already registered"):
        register_document_sort(spec)
    assert set(document_sort_names()) == before


def test_the_encoder_refuses_a_sort_that_never_declared_a_cursor() -> None:
    """评审发现 S2：编码这一侧原先靠一句写死的名字比对，删掉它 12 条守卫全绿。
    现在它和别处一样查表，所以这一句是实时判据而不是注释。
    """
    from datetime import datetime

    assert resolve_document_sort("name_asc").keyset_cursor is False  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="cursor is supported only for"):
        catalog_module._encode_document_cursor(  # noqa: SLF001
            "name_asc", datetime(2026, 1, 1), "doc-old", "0" * 64
        )
    with pytest.raises(ValueError, match="unsupported document sort"):
        catalog_module._encode_document_cursor(  # noqa: SLF001
            "never_declared", datetime(2026, 1, 1), "doc-old", "0" * 64
        )


def test_the_builtin_cursor_pages_the_whole_table_when_created_and_updated_disagree() -> None:
    """声明把 ORDER BY 与游标取值放在同一处书写，但它们是同一个作者写的**两遍**：这一条把
    错配变成看得见的东西 —— 把内建的 cursor_value 改成 created_at，limit=1 全表走查就会
    漏行或重行。"""
    engine = _seeded_engine()
    capable = cursor_capable_sorts()
    assert capable, "内建的 keyset 排序没注册上，这条守卫就成了空话"
    for sort in capable:
        whole = _ids_sorted_by(engine, sort)
        assert len(whole) >= 4, "至少要几行 created/updated 顺序不一致的数据才判得出错配"
        walked: list[str] = []
        cursor: str | None = None
        for _ in range(len(whole) + 1):
            page = list_documents_page(
                "tenant-a", "dataset-a", limit=1, sort=sort, cursor=cursor, engine_override=engine
            )
            walked += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert walked == whole, f"{sort}：按自己续出的游标走不完这张表，表达式与取值不一致"


def test_a_newly_registered_cursor_sort_pages_with_its_own_cursor() -> None:
    """评审发现 S3/S4：续游标和拒游标两处都写成比字面量时，删掉声明里的那一位没人发现。
    换一个探针排序（内建已摘掉）跑完整两页，字面量版本就会在这里红。
    """
    engine = _seeded_engine()
    unregister_document_sort("updated_at_desc")
    register_document_sort(
        DocumentSortSpec(
            sort="probe_created_desc",
            direction="desc",
            expression=lambda table, _available: table.c.created_at,
            keyset_cursor=True,
            cursor_value=lambda row: row.get("created_at"),
        )
    )
    try:
        whole = [
            item["id"]
            for item in list_documents_page(
                "tenant-a", "dataset-a", limit=50, sort="probe_created_desc", engine_override=engine
            )["items"]
        ]
        assert len(whole) >= 2, "至少两行才判得出续游标"
        walked: list[str] = []
        cursor: str | None = None
        for _ in range(len(whole) + 1):
            page = list_documents_page(
                "tenant-a",
                "dataset-a",
                limit=1,
                sort="probe_created_desc",
                cursor=cursor,
                engine_override=engine,
            )
            walked += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert walked == whole, (
            "声明了游标能力的排序必须能靠自己续出的游标走完整张表：编码、谓词、解码三处"
            "都由这张表驱动，任何一处退回比字面量都会在这里断掉"
        )
    finally:
        unregister_document_sort("probe_created_desc")
        catalog_module.register_builtin_document_sorts()
    assert cursor_capable_sorts() == ["updated_at_desc"]


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
                    cursor_value=lambda row: row.get("created_at"),
                )
            )
    finally:
        unregister_document_sort("probe_asc_cursor")
        catalog_module.register_builtin_document_sorts()
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
    # 评审发现 A-1/S6：只禁一个名字，等于允许把表达式分发退回一张按另两个名字分支的梯子
    # （仍然调 resolve_document_sort 判存在性），12 条守卫全绿。三个内建名一个都不许出现。
    for spelling in ("updated_at_desc", "created_at_asc", "name_asc"):
        assert f'"{spelling}"' not in expression_source, f"表达式分发按 {spelling} 点名"
    assert "resolve_document_sort" in expression_source
    page_source = inspect.getsource(catalog_module.list_documents_page)
    assert 'if normalized_sort == "created_at_asc"' not in page_source
    assert "sort_spec.direction" in page_source
    assert "sort_spec.keyset_cursor" in page_source, "续/拒游标又退回比名字"
    assert "sort_spec.cursor_value" in page_source, "游标取值又写死成某一列"
    encode_source = inspect.getsource(catalog_module._encode_document_cursor)  # noqa: SLF001
    assert "resolve_document_sort" in encode_source, "编码这一侧没查表"
    decode_source = inspect.getsource(catalog_module._decode_document_cursor)  # noqa: SLF001
    assert 'payload.get("s") != "updated_at_desc"' not in decode_source
