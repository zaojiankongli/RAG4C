"""Axis: the ``passing`` ↔ ``passed`` alias has exactly one owner.

Three places used to write the inverse ternary by hand and three kept both spellings in a set
or a ``Literal``. The interesting failure this pins is not the alias itself but the drift
between the Python vocabulary and the database ``CHECK`` that decides what a row can hold:
today nothing asserts they agree, so a state can be added to one side and stay unknown to the
other — and a total translator built on the wrong set would start refusing real rows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.release_quality_gate_states import (
    GATE_STATES_BASE,
    GATE_STATE_PASSED_PUBLIC,
    GATE_STATE_PASSED_STORAGE,
    gate_states_public,
    gate_states_storage,
    to_public_gate_state,
    to_storage_gate_state,
)

REPO = Path(__file__).resolve().parents[1]
ORM = REPO / "models" / "orm.py"
MIGRATION = (
    REPO / "catalog_migrations" / "versions" / "0031_enterprise_release_quality_operations.py"
)

_CHECK_RE = re.compile(r"gate_state IN \(([^)]*)\)")


def _check_states(text: str) -> frozenset[str]:
    found = _CHECK_RE.search(text)
    assert found is not None, "找不到 gate_state 的 CHECK —— 词表与约束的对账必须有约束可对"
    return frozenset(part.strip().strip("'") for part in found.group(1).split(","))


# --------------------------------------------------------------------------- #
# 字面值钉死：删掉任何一项，参数化只会少跑一条，不会红
# --------------------------------------------------------------------------- #


def test_the_alias_pair_and_the_rest_of_the_vocabulary_are_pinned() -> None:
    assert GATE_STATE_PASSED_STORAGE == "passing"
    assert GATE_STATE_PASSED_PUBLIC == "passed"
    assert GATE_STATES_BASE == frozenset({"waived", "not_required", "blocked", "unavailable"})
    assert gate_states_storage() == GATE_STATES_BASE | {"passing"}
    assert gate_states_public() == GATE_STATES_BASE | {"passed"}
    # 只有"通过"这一档改名，其余四种两侧同词 —— 这条钉住"别名只有一对"这个判断。
    assert gate_states_storage() & gate_states_public() == GATE_STATES_BASE


@pytest.mark.parametrize("path", [ORM, MIGRATION], ids=["orm", "migration-0031"])
def test_the_declared_storage_vocabulary_equals_what_the_database_allows(path: Path) -> None:
    """声明表与 ``CHECK`` 是同一句话的两份抄本，这里就是那件"没人断言过"的事。

    库里存不进 ``passed``，所以任何把 ``passed`` 当合法存储值的词表都含一个**永不命中**的成员
    （``core/enterprise_release_quality_alerts.py`` 改之前就是这样）。
    """
    assert _check_states(path.read_text(encoding="utf-8")) == gate_states_storage()


# --------------------------------------------------------------------------- #
# 两个方向各是什么形状
# --------------------------------------------------------------------------- #


def test_caller_supplied_states_land_on_the_spelling_the_catalog_stores() -> None:
    # 入参侧两种拼写都得收：契约对外同时承诺了它们（ObservationGateState 就是两值并列）。
    assert to_storage_gate_state(GATE_STATE_PASSED_PUBLIC) == GATE_STATE_PASSED_STORAGE
    assert to_storage_gate_state(GATE_STATE_PASSED_STORAGE) == GATE_STATE_PASSED_STORAGE
    for state in GATE_STATES_BASE:
        assert to_storage_gate_state(state) == state


def test_stored_states_leave_as_the_spelling_the_console_is_promised() -> None:
    assert to_public_gate_state(GATE_STATE_PASSED_STORAGE) == GATE_STATE_PASSED_PUBLIC
    for state in GATE_STATES_BASE:
        assert to_public_gate_state(state) == state


def test_translation_is_idempotent_in_the_direction_that_must_be() -> None:
    """存储方向可重入（入参本来就允许两种拼写）；对外方向**不**可重入 ——
    `passed` 不是合法的存储值，拿它再走一次 `to_public` 必须拒（见下一条用例）。
    """
    for value in gate_states_storage() | gate_states_public():
        stored = to_storage_gate_state(value)
        assert to_storage_gate_state(stored) == stored
        assert to_public_gate_state(stored) in gate_states_public()


# --------------------------------------------------------------------------- #
# total 的那一半：没声明过的状态是拒绝的理由，不是原样放行的理由
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "stale",  # 另一张表（release quality operations）的词，不属于这一列
        "PASSED",
        " passing",
        "",
        None,
        "certified",
    ],
)
def test_an_undeclared_state_is_refused_rather_than_passed_through(value: object) -> None:
    with pytest.raises(ValueError):
        to_storage_gate_state(value)
    with pytest.raises(ValueError):
        to_public_gate_state(value)


def test_the_public_spelling_is_never_a_legal_stored_value() -> None:
    """``to_public_gate_state("passed")`` 拒：库里不可能有这一行。

    改之前 :3235 那句三元式对它是**原样透传**，所以这个收紧只有在数据绕过 CHECK 写进来时
    才看得见 —— 而那正是该报而不是该静默顺着往下算的情形。
    """
    with pytest.raises(ValueError):
        to_public_gate_state(GATE_STATE_PASSED_PUBLIC)


# --------------------------------------------------------------------------- #
# 回潮栅栏：别名只能有一个主人
# --------------------------------------------------------------------------- #


def test_no_module_writes_the_alias_as_an_inline_ternary_again() -> None:
    """这三处转换过去各写一遍方向相反的同款三元式，收进声明之后要挡住"顺手再写一处"。

    与 ``tests/test_chunk_writers.py`` 同型：判据不是行数也不是"我记得改完了"，
    而是源码里不能再出现这个形状。

    形状按 ``"passing"`` 认，不按 ``"passed"`` —— 后者是通用词，
    ``enterprise_release_quality_evidence.py:367`` 的 ``verdict="passed" if not failed`` 是
    另一套词表（规则判定结果），拿 ``"passed" if`` 当模式会把它误伤成违规。
    """
    offenders: list[str] = []
    for path in sorted((REPO / "core").glob("*.py")) + sorted((REPO / "server").glob("*.py")):
        if path.name == "release_quality_gate_states.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if '"passing"' in code and " if " in code:
                offenders.append(f"{path.name}:{line.strip()[:70]}")
    assert offenders == [], f"别名又被人就地抄了一遍：{offenders}"


def test_the_two_spelling_lists_outside_the_declaration_still_match_it() -> None:
    """请求侧那个 ``Literal`` 必须是静态字面量（OpenAPI 要它），没法从函数派生 ——
    所以这里替它把账对平：它并列的两种拼写，正好等于声明侧 public ∪ storage。

    少了成员 = 界面上的一个合法筛选值会在边界被拒；多了成员 = 又回到"两份手抄表各写各的"。
    """
    text = (REPO / "server" / "enterprise_release_quality_operations_api.py").read_text(
        encoding="utf-8"
    )
    found = re.search(r"ObservationGateState = Literal\[(.*?)\]", text, re.S)
    assert found is not None, "请求侧的 ObservationGateState 字面量不见了"
    listed = frozenset(part.strip().strip('"') for part in found.group(1).split(","))
    assert listed == gate_states_public() | gate_states_storage(), listed


def test_the_alerts_vocabulary_validates_rows_so_it_must_be_the_storage_set() -> None:
    """``core/enterprise_release_quality_alerts.py`` 校验的是 ``row.gate_state``，
    它过去把 ``passed`` 也列进去 —— 一个 CHECK 永不产出的值。这里钉住"那一栏不再有死成员"。
    """
    from core import enterprise_release_quality_alerts as alerts

    assert alerts._OBSERVATION_GATE_STATES == gate_states_storage()  # noqa: SLF001
    assert GATE_STATE_PASSED_PUBLIC not in alerts._OBSERVATION_GATE_STATES  # noqa: SLF001

