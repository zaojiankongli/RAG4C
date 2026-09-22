"""Axis #10 guard: one declaration for what a source-sync ``state_mode`` promises.

``sources/runner.py`` used to ask the same question five different ways
(``in {"dual", "database"}`` twice, ``in {"json", "dual"}`` once, ``== "database"`` twice)
while ``config/settings.py`` wrote the same three-value vocabulary a second time as a
``Literal``. Adding a fourth mode meant remembering all six, and forgetting one is a source
quietly reading or writing the wrong store rather than an error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sources.state_modes import (
    SOURCE_STATE_MODE_NAMES,
    SourceStateModeSpec,
    source_state_mode,
    source_state_mode_names,
)

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "sources" / "runner.py"
SETTINGS = REPO / "config" / "settings.py"


# --------------------------------------------------------------------------- #
# 判据 §3.1：一种模式 = 一行声明，五个消费点实时查表
# --------------------------------------------------------------------------- #


def test_each_mode_declares_which_stores_it_writes_and_whom_it_trusts() -> None:
    """字面值钉死。改任何一行都会在这里红，而不是在某个部署上表现为"状态写到错的存储"。"""
    assert source_state_mode_names() == ("json", "dual", "database")
    assert source_state_mode("json") == SourceStateModeSpec(
        name="json",
        writes_json=True,
        uses_ledger=False,
        ledger_authoritative=False,
        ledger_failures_are_fatal=False,
        requires_ledger=False,
    )
    assert source_state_mode("dual") == SourceStateModeSpec(
        name="dual",
        writes_json=True,
        uses_ledger=True,
        ledger_authoritative=False,
        ledger_failures_are_fatal=False,
        requires_ledger=False,
    )
    assert source_state_mode("database") == SourceStateModeSpec(
        name="database",
        writes_json=False,
        uses_ledger=True,
        ledger_authoritative=True,
        ledger_failures_are_fatal=True,
        requires_ledger=True,
    )


def test_settings_vocabulary_is_the_declared_one() -> None:
    """两处词表对账：设置侧那个 ``Literal`` 必须是声明表恰好那一组。

    少了成员 = 一个已登记的模式在配置层被拒；多了成员 = 配置收下一个没人声明的模式，
    runner 那边才炸 —— 后者正是过去两份手抄表的形状。
    """
    text = SETTINGS.read_text(encoding="utf-8")
    found = re.search(r"state_mode:\s*Literal\[(.*?)\]\s*=", text, re.S)
    assert found is not None, "config/settings.py 里找不到 state_mode 的 Literal"
    declared = tuple(part.strip().strip('"') for part in found.group(1).split(","))
    assert declared == SOURCE_STATE_MODE_NAMES, declared


def test_an_undeclared_mode_is_refused_rather_than_defaulted() -> None:
    for value in ("", "JSON", " json", "hybrid", None, "postgres"):
        with pytest.raises(ValueError):
            source_state_mode(value)


# --------------------------------------------------------------------------- #
# 回潮栅栏：五个消费点不能再各自问一遍
# --------------------------------------------------------------------------- #


def test_the_runner_no_longer_asks_the_membership_question_itself() -> None:
    """判据的常驻版本：加一种模式不该要求改动 runner 里任何一处成员判断。

    这条不是风格检查。过去五处各问一遍，意味着"新模式忘了改第 4 处"会通过全部测试而
    只在真跑同步时表现错。这里钉的是"runner 里已经没有那个形状可忘"。
    """
    text = RUNNER.read_text(encoding="utf-8")
    offenders = [
        line.strip()[:78]
        for line in text.splitlines()
        if re.search(r'state_mode\s*(==|in)\s*("|\{)', line.split("#", 1)[0])
    ]
    assert offenders == [], f"runner 又自己问了一遍 state_mode：{offenders}"
    assert "self.state_mode_spec" in text, "消费点没有改走声明"
