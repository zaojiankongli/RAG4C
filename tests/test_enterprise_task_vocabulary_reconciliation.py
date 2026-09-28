"""Axis: the task-operations vocabularies are written twice — once for Python, once for the
database ``CHECK`` — so put a fence between them.

``core/enterprise_task_operations.py`` validates what a caller may send;
``core/catalog_schema.py`` declares what a row may hold
(``ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS`` feeds the ``CHECK`` constraints, and
``ck_tenant_task_projections_source_kind`` is one of them). They are two independent spellings
of the same seven words, in two modules, and **nothing in the repo compared them** before this
file. The failure is asymmetric and quiet:

* add a kind on the Python side only → rows with it are refused by the ``CHECK`` at flush time,
  surfacing as an integrity error nobody can trace to a missing vocabulary entry;
* add it on the schema side only → the ``CHECK`` admits it but every validator rejects it, so a
  row can exist that no code path can read back.

One entry is deliberately NOT collapsed — see the reconciliation test for why.
"""

from __future__ import annotations

import pytest

from core import catalog_schema as schema
from core import enterprise_task_operations as ops

#: (label, python-side set, schema-side tuple) for the vocabularies that are the same concept.
DUPLICATED = [
    ("source_kind", ops.TASK_SOURCE_KINDS, schema.ENTERPRISE_TASK_SOURCE_KINDS),
    ("category", ops.TASK_CATEGORIES, schema.ENTERPRISE_TASK_CATEGORIES),
    ("action", ops.TASK_ACTIONS, schema.ENTERPRISE_TASK_ACTION_TYPES),
    (
        "action_status",
        ops.TASK_ACTION_STATUSES,
        schema.ENTERPRISE_TASK_ACTION_STATUSES,
    ),
    ("event_type", ops.TASK_EVENT_TYPES, schema.ENTERPRISE_TASK_EVENT_TYPES),
    ("view_status", ops.TASK_VIEW_STATUSES, schema.ENTERPRISE_TASK_VIEW_STATUSES),
    # 名字两侧不同（python 侧叫 normalized_statuses，schema 侧叫 statuses），概念同一个。
    ("normalized_status", ops.TASK_NORMALIZED_STATUSES, schema.ENTERPRISE_TASK_STATUSES),
]


@pytest.mark.parametrize(("label", "python_side", "schema_side"), DUPLICATED)
def test_the_two_spellings_of_one_vocabulary_still_agree(
    label: str, python_side: frozenset[str], schema_side: tuple[str, ...]
) -> None:
    assert python_side, f"{label}: python 侧词表空了 —— 空集对空集会让自己放过自己"
    assert schema_side, f"{label}: schema 侧词表空了 —— 同上"
    assert python_side == frozenset(schema_side), (
        f"{label} 两份抄本已经漂移：只在 python 侧加 = 这种行写不进库（CHECK 在 flush 时报一个"
        f"看不出根因的完整性错误）；只在 schema 侧加 = 库里能存在一行而所有校验都拒读它。"
        f" python-only={sorted(python_side - set(schema_side))}"
        f" schema-only={sorted(set(schema_side) - python_side)}"
    )


def test_every_duplicated_pair_actually_compares_something_nonempty() -> None:
    """防"守卫自己变成摆设"：加一条词表却忘了登记进来，这里的数量断言会红。"""
    assert len(DUPLICATED) >= 7
    labels = {label for label, _p, _s in DUPLICATED}
    assert {
        "source_kind",
        "category",
        "action",
        "action_status",
        "event_type",
        "normalized_status",
    } <= labels


def test_the_projections_check_is_built_from_the_tuple_this_fence_compares() -> None:
    """如果 CHECK 改成从别处取词表，上面那条对账就成了对着空气较劲 —— 这条钉住它仍然有效。

    （映射是按表分层的：``{表名: {约束名: 值}}``，所以这里连表名一起断。）
    """
    fragments = schema.ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS
    projections = fragments["tenant_task_projections"]
    reconciliation = fragments["tenant_task_reconciliation_runs"]
    assert projections["ck_tenant_task_projections_source_kind"] is (
        schema.ENTERPRISE_TASK_SOURCE_KINDS
    )
    assert projections["ck_tenant_task_projections_category"] is (
        schema.ENTERPRISE_TASK_CATEGORIES
    )
    assert projections["ck_tenant_task_projections_status"] is schema.ENTERPRISE_TASK_STATUSES
    assert reconciliation["ck_tenant_task_reconciliation_runs_status"] is (
        schema.ENTERPRISE_TASK_RECONCILIATION_STATUSES
    )


def test_reconciliation_status_is_the_one_pair_that_currently_disagrees_on_record() -> None:
    """**登记一个已查清、按裁定保留的双层词表，而不是把它抹平。**

    python 侧校验入参用的是 ``started``，而库里那一列的 ``CHECK`` 只认 ``running``
    （``_STATUS_ALIASES`` 里确实有 ``"started" -> "running"``，但它服务的是**投影**的
    normalized status，``_normalize_reconciliation_status`` 没走那张表）。

    2026-09-27 写路径已查实（交接审查 §9 第 28 条裁定）：唯一的写入点
    ``enterprise_task_operations_service._reconcile_in_session`` 硬编码 ``running``，
    没有任何路径把 ``started`` 写进该列——不存在会被 CHECK 拒的真缺陷。
    实际形状是**双层词表**：存储层 ``running``（CHECK/ORM/迁移三处一致），公开层
    ``started``（门面词表 + 服务层 ``_run_body`` 出参把 ``running`` 映射回 ``started``、
    ``list_reconciliation_runs`` 入参把两种拼法都归一到 ``running``）。这与
    ``core/release_quality_gate_states.py`` 的 gate_state（存储 ``passing``、对外
    ``passed``）同型，属企业 API 常见的内外词表分离。
    本条继续把差异钉成字面事实：任何人只动一边，这条就红。
    """
    assert ops.TASK_RECONCILIATION_STATUSES == frozenset({"started", "completed", "failed"})
    assert schema.ENTERPRISE_TASK_RECONCILIATION_STATUSES == (
        "running",
        "completed",
        "failed",
    )
    assert (
        ops.TASK_RECONCILIATION_STATUSES ^ set(schema.ENTERPRISE_TASK_RECONCILIATION_STATUSES)
        == {"started", "running"}
    ), "差异不再只是 started/running —— 先查清是哪一侧错了，再改这条"
