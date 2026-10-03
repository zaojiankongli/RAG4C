"""槽位温度守卫测试 ——「配置里提到了这个槽位，温度意图却被重置」的回归。

背景（实测，本机 ``.env``）：八个槽位在 ``.env`` 里写了
``PROVIDER_REF`` / ``MODEL`` 指向 DashScope。pydantic 见到
``llm.<槽位>`` 就会拿那份配置**重建** ``LlmSlotSettings``，于是类字段默认值
``temperature=0.2`` 顶掉了代码里为每个槽位声明的意图：

    generation 0.7 -> 0.2     judge 0.0 -> 0.2     router_llm 0.1 -> 0.2
    hyde / subqueries / stepback / metadata_filter 0.0 -> 0.2

而**没在 .env 里出现过**的 triplet / contextual / classifier 反而保住了 0.0
——越是真正配过的槽位，越拿不到为它声明的温度。judge 从 0 变 0.2 的后果不止
"答案保守一点"：L3 蕴含判定与评测裁判不再是确定性函数，幻觉率/有据性这类
数字第二次跑就对不上第一次，F1.2 的基线失去可比性。

守卫五条：
1. 只写 model / provider_ref 的槽位，温度仍拿到声明值；
2. 显式写了温度的槽位，一律尊重（判空或判 0.2 都会让"我就是要 0.2"无法表达）；
3. 不写任何配置时，默认值与声明意图一致；
4. 修复是**幂等**的（重复解析不会把温度越改越乱）；
5. 源数据守卫：温度清单覆盖全部槽位，且与类字段默认值同源。
"""
from __future__ import annotations

from config.settings import (
    LLM_SLOT_NAMES,
    LlmSlotSettings,
    LlmSlotsSettings,
    _SLOT_TEMPERATURE_DEFAULTS,
    _default_slot_temperature,
)


def test_slot_temperature_survives_partial_override() -> None:
    """只写 model / provider_ref 时，温度必须仍是声明值（本 bug 的核心）。"""
    slots = LlmSlotsSettings(
        generation={"model": "qwen-max", "provider_ref": "dashscope"},
        judge={"model": "qwen-max", "provider_ref": "dashscope"},
        router_llm={"model": "qwen-max"},
    )
    assert slots.generation.temperature == 0.7
    assert slots.judge.temperature == 0.0
    assert slots.router_llm.temperature == 0.1
    # 没被提到的槽位不受影响
    assert slots.triplet.temperature == 0.0


def test_explicit_temperature_is_always_respected() -> None:
    """显式写过温度就必须尊重：包括"我就是要 0.2"这种与默认值相同的写法。"""
    slots = LlmSlotsSettings(
        generation={"temperature": 0.05, "model": "m"},
        judge={"temperature": 0.2},
    )
    assert slots.generation.temperature == 0.05
    assert slots.judge.temperature == 0.2


def test_defaults_match_declared_intent() -> None:
    """完全不配置时，各槽位温度就是 :data:`_SLOT_TEMPERATURE_DEFAULTS`。"""
    slots = LlmSlotsSettings()
    for name in LLM_SLOT_NAMES:
        assert getattr(slots, name).temperature == _SLOT_TEMPERATURE_DEFAULTS[name]


def test_repair_is_idempotent() -> None:
    """修复跑第二遍不得再改任何东西（热更新会反复解析同一份配置）。

    构造"被重建过的槽位"只能传 model，**不能**传 temperature——后者会被
    ``model_fields_set`` 记成"显式写过"，修复就会（正确地）跳过它。这正是
    本 bug 的成因，写测试时踩一次印象更深。
    """
    once = _default_slot_temperature("generation", LlmSlotSettings(model="m"))
    twice = _default_slot_temperature("generation", once)
    assert once.temperature == 0.7
    assert twice.temperature == 0.7
    assert twice.model_dump() == once.model_dump()


def test_temperature_table_covers_every_slot() -> None:
    """清单必须与 LLM_SLOT_NAMES 严格同源：多一个少一个都是静默漂移。"""
    assert set(_SLOT_TEMPERATURE_DEFAULTS) == set(LLM_SLOT_NAMES)
