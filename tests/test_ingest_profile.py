"""入库槽位配置档（``llm.ingest_profile``）的守卫测试 —— master-plan F3.3。

背景：``triplet`` / ``contextual`` / ``classifier`` 三个槽位**刻意**指向本机
Ollama——它们按 chunk 逐个调用，调用量随文档片段数线性放大，走云端的钱与
时间都不可控。但"本机跑不动大模型"是真实约束，需要能切云端档做对比实验，
所以提供**机制**（F3.3），**不改默认**（9/28 明确要求不得擅自给这三个槽位
补云端 provider_ref）。

守卫四条，前三条是行为、第四条是源码边界：
1. 缺省 / 显式 ``ollama`` 档 → 三槽位与引入本机制前**逐字一致**（都是本机
   Ollama + qwen2.5），且 retrieval / answer 槽位不受影响；
2. ``cloud`` 档 → 三槽位切到云端端点，其余槽位不动；
3. **显式配置优先于档位**：``.env`` 里单独配过的槽位，档位不覆盖
   （否则"切档"就变成了"强制改写我的配置"）；
4. 源码守卫：不设档时不得给这三个槽位补 ``provider_ref``——把"不改默认"
   这条约束钉在测试里，而不是只写在注释里。
"""
from __future__ import annotations

from config.settings import (
    LlmSlotSettings,
    LlmSlotsSettings,
    _INGEST_PROFILE_SLOTS,
)

# 缺省行为：这三个槽位吃 LlmSlotSettings 的默认值，指向本机 Ollama。
_LOCAL_OLLAMA = "http://localhost:11434/v1"
_OLLAMA_MODEL = "qwen2.5"


def _ingest_triple(slots: LlmSlotsSettings) -> list[LlmSlotSettings]:
    return [getattr(slots, name) for name in _INGEST_PROFILE_SLOTS]


def test_ingest_profile_slots_match_group_definition() -> None:
    """档位作用域必须与 LLM_SLOT_GROUPS['ingest'] 同源，不能各写一份。"""
    from config.settings import LLM_SLOT_GROUPS

    assert set(_INGEST_PROFILE_SLOTS) == set(LLM_SLOT_GROUPS["ingest"])
    assert set(_INGEST_PROFILE_SLOTS) == {"triplet", "contextual", "classifier"}


def test_default_profile_leaves_ingest_slots_on_local_ollama() -> None:
    """缺省即 ollama 档：三槽位一个字节都不动。"""
    slots = LlmSlotsSettings()
    assert slots.ingest_profile == "ollama"
    for slot in _ingest_triple(slots):
        assert slot.base_url == _LOCAL_OLLAMA
        assert slot.model == _OLLAMA_MODEL
        # 关键：不得凭空出现云端 provider_ref
        assert slot.provider_ref == ""


def test_explicit_ollama_profile_is_identical_to_default() -> None:
    """显式写 ollama 与不写，必须完全等价——否则"显式声明"就有风险。"""
    default = LlmSlotsSettings()
    explicit = LlmSlotsSettings(ingest_profile="ollama")
    for a, b in zip(_ingest_triple(default), _ingest_triple(explicit)):
        assert (a.base_url, a.model, a.provider_ref) == (
            b.base_url,
            b.model,
            b.provider_ref,
        )


def test_cloud_profile_switches_only_ingest_slots() -> None:
    """云端档只动入库三槽位，检索期与生成期不受影响。"""
    ollama = LlmSlotsSettings()
    cloud = LlmSlotsSettings(ingest_profile="cloud")

    for slot in _ingest_triple(cloud):
        assert slot.provider_ref == "dashscope"
        assert slot.base_url != _LOCAL_OLLAMA
        assert "11434" not in slot.base_url

    # 其余槽位逐字不动
    for name in ("rewrite", "router_llm", "hyde", "generation", "judge"):
        assert getattr(cloud, name) == getattr(ollama, name), name


def test_explicit_slot_config_wins_over_profile() -> None:
    """已显式配过的槽位，档位不得覆盖。"""
    slots = LlmSlotsSettings(
        ingest_profile="cloud",
        triplet=LlmSlotSettings(provider_ref="siliconflow", model="my-own-model"),
    )
    # 显式配过的保持原样
    assert slots.triplet.provider_ref == "siliconflow"
    assert slots.triplet.model == "my-own-model"
    # 没配过的照常吃档位
    assert slots.contextual.provider_ref == "dashscope"


def test_unknown_profile_falls_back_instead_of_raising() -> None:
    """档位名写错不该让整条入库链路崩——退回 ollama。"""
    slots = LlmSlotsSettings(ingest_profile="nonsense")
    for slot in _ingest_triple(slots):
        assert slot.base_url == _LOCAL_OLLAMA


def test_profile_application_is_idempotent() -> None:
    """反复构造/复制不得把配置越改越乱（热更新会反复走这里）。"""
    first = LlmSlotsSettings(ingest_profile="cloud")
    second = first.model_copy()
    third = LlmSlotsSettings(ingest_profile="cloud")
    for a, b, c in zip(_ingest_triple(first), _ingest_triple(second), _ingest_triple(third)):
        assert (a.base_url, a.model) == (b.base_url, b.model) == (c.base_url, c.model)
