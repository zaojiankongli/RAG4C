"""扩展轴 #12 的触发器那一半：适配器注册表是唯一声明，顺序与校验都从它派生。

以前 `TRIGGER_ADAPTER_ORDER`（文件开头一个手写 tuple）与 `TRIGGER_ADAPTER_REGISTRY`（文件末尾
一个 dict）平行维护，加一个触发器要把两处改成一样，而唯一在核对它们一致的是另一份测试里的
**第三份副本**（`tests/test_enterprise_automation_workflows_service.py:35` 的 `TRIGGERS`）——
副本对副本，谁都不是权威。

这里钉三件事：顺序由注册表派生；注册表对着**权威词表** `AUTOMATION_TRIGGER_CODES` 而不是对着
测试副本对账；以及注册即生效 —— 运行时挂一个新适配器，校验与顺序都当场跟上，且宿主文件零改动。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import pytest

import core.enterprise_automation_workflows_service as svc
from core.enterprise_automation_workflows import AUTOMATION_TRIGGER_CODES

REPO = Path(__file__).resolve().parents[1]
HOST = REPO / "core/enterprise_automation_workflows_service.py"


def test_the_order_is_the_registry_not_a_parallel_copy() -> None:
    assert svc.TRIGGER_ADAPTER_ORDER == tuple(svc.TRIGGER_ADAPTER_REGISTRY)
    source = HOST.read_text(encoding="utf-8")
    # 查**声明形状**而不是查 code 字面量：适配器自己的函数体里当然会写自己那个 code，
    # 按字面量扫整段文件头会一路误报（第一版就是这么红在自己身上的）。
    assert re.search(
        r"TRIGGER_ADAPTER_ORDER: tuple\[str, \.\.\.\] = tuple\(TRIGGER_ADAPTER_REGISTRY\)", source
    )
    assert source.count("TRIGGER_ADAPTER_ORDER: tuple[str, ...] =") == 1, (
        "又多了一份并行的顺序声明 —— 这正是本轴要消掉的东西"
    )


def test_the_registry_covers_the_authoritative_vocabulary_exactly() -> None:
    """对着权威词表对账，不是对着测试里的那份副本。"""
    assert set(svc.TRIGGER_ADAPTER_REGISTRY) == set(AUTOMATION_TRIGGER_CODES)
    assert all(callable(a) for a in svc.TRIGGER_ADAPTER_REGISTRY.values())


def test_registering_an_adapter_takes_effect_live_and_edits_no_host_file() -> None:
    """零改分支判据：挂一个适配器进注册表，校验与顺序当场跟上，宿主文件字节不变。"""
    before = HOST.read_bytes()

    def _probe_adapter(*args: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
        return []

    assert "knowledge_index_drifted" not in svc.TRIGGER_ADAPTER_REGISTRY
    # 词表是 frozenset，所以换掉模块属性而不是就地改。
    original_vocab = svc.AUTOMATION_TRIGGER_CODES
    try:
        svc.AUTOMATION_TRIGGER_CODES = original_vocab | {"knowledge_index_drifted"}
        svc.TRIGGER_ADAPTER_REGISTRY["knowledge_index_drifted"] = _probe_adapter
        order = svc._validate_trigger_codes(None)
        assert order[-1] == "knowledge_index_drifted"
        picked = svc._validate_trigger_codes(["knowledge_index_drifted", "task_failed"])
        assert set(picked) == {"knowledge_index_drifted", "task_failed"}, (
            f"新注册的 code 被静默丢掉了：{picked}"
        )
    finally:
        svc.TRIGGER_ADAPTER_REGISTRY.pop("knowledge_index_drifted", None)
        svc.AUTOMATION_TRIGGER_CODES = original_vocab

    assert HOST.read_bytes() == before, "注册即生效却要求改宿主文件，等于没收回注册表"
    assert svc._validate_trigger_codes(None) == svc.TRIGGER_ADAPTER_ORDER


def test_validate_trigger_codes_reads_the_registry_not_the_import_time_snapshot() -> None:
    """快照与注册表必须同步 —— 只改一处会让校验"看得到新 code、顺序里却没有"。"""
    source = HOST.read_text(encoding="utf-8")
    body = source.split("def _validate_trigger_codes", 1)[1].split("\n\n\n", 1)[0]
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert "TRIGGER_ADAPTER_ORDER" not in code, (
        "校验又去读导入期快照：运行时注册的适配器会只生效一半"
    )


def test_an_unadapted_vocabulary_code_is_not_silently_dropped() -> None:
    """今天词表与注册表等集，所以"没有适配器的合法 code"这条路走不到。

    这条不是运行时保护，是**别忘了它现在靠的是等集这个事实**：谁给词表加一个 code 而没挂适配器，
    `_validate_trigger_codes` 会把它从结果里悄悄滤掉（用户建了规则却永远不触发）。真到那一步的
    正确出口是"注册期/校验期拒"，不是在这里补 `return False` 式的兜底。
    """
    missing = set(AUTOMATION_TRIGGER_CODES) - set(svc.TRIGGER_ADAPTER_REGISTRY)
    assert not missing, (
        f"词表里有、适配器没有：{missing} —— 这些 code 现在会被静默丢掉，"
        "要么补适配器，要么在注册期拒，别让规则静默不触发"
    )


@pytest.mark.parametrize("code", sorted(AUTOMATION_TRIGGER_CODES))
def test_every_vocabulary_code_survives_validation(code: str) -> None:
    assert svc._validate_trigger_codes([code]) == (code,)
