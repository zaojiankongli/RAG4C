"""提示词模板渲染契约。

这个文件守的是一类**静默且致命**的缺陷：模板正文里带着一段字面量 JSON 骨架
（"只输出如下格式"），而渲染时用了 ``str.format``——format 把 JSON 的 ``{``
当成占位符起点，抛 ``KeyError: '\\n  "triplets"'``。

后果不是"提示词差一点"，是**这条链路 100% 不可用**。
``indexing/triplet_extractor.py`` 正是如此：图索引一打开，每个 chunk 的抽取
都抛异常，图库恒为空，图检索永远静默降级回 hybrid，而日志只说"降级成功"。

审查时全仓 11 份模板里有 **10 份**含这样的未转义花括号；它们没炸，只是因为
各自的调用点碰巧用的是 ``.replace("{field}", value)``。两套约定并存，谁写新
调用点时顺手用了 format，谁就中招。现在统一到 :func:`prompts.render_prompt`。

因此这里断言的是**全部模板**，而不只是当初炸掉的那一份。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from prompts import render_prompt

_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

# 每份模板在生产调用点实际传入的占位符集合。
# 新增模板时必须在这里登记——漏登记会被 test_every_template_is_covered 抓住，
# 那正是"新模板没人渲染过就上线"的信号。
_TEMPLATE_SLOTS: dict[str, dict[str, object]] = {
    "contextual_v1.txt": {"document": "全文", "chunk": "片段"},
    "generation_v1.txt": {
        "question": "报销流程是什么",
        "evidence": "[1] 证据",
        "max_tokens_hint": "500",
        "language": "中文",
    },
    "graph_rerank_v1.txt": {
        "query": "报销",
        "candidates": "0. 甲 属于 乙",
        "count": 1,
        "count_minus_one": 0,
        "max_pick": 1,
    },
    "graph_triplet_v1.txt": {"text": "甲公司收购了乙公司", "max_triplets": 20},
    "hyde_v1.txt": {"query": "报销流程"},
    "judge_groundedness_v1.txt": {"claims": "[1] 声明", "evidence": "[1] 证据"},
    "judge_relevance_v1.txt": {
        "question": "问",
        "answer": "答",
        "evidence": "[1] 证据",
    },
    "query_rewrite_v1.txt": {"query": "报销"},
    "router_llm_v1.txt": {"query": "报销", "candidate_routes": "hybrid, graph"},
    "stepback_v1.txt": {"query": "报销"},
    "subqueries_v1.txt": {"query": "报销", "max_sub_queries": 3},
}


def _read(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def test_every_template_is_covered() -> None:
    """新增模板必须在 _TEMPLATE_SLOTS 登记，否则下面的断言会悄悄漏掉它。"""
    on_disk = {p.name for p in _PROMPTS.glob("*.txt")}
    assert on_disk == set(_TEMPLATE_SLOTS), (
        f"模板与登记表不一致：仅在磁盘 {sorted(on_disk - set(_TEMPLATE_SLOTS))}，"
        f"仅在表里 {sorted(set(_TEMPLATE_SLOTS) - on_disk)}"
    )


@pytest.mark.parametrize("name", sorted(_TEMPLATE_SLOTS))
def test_template_renders_without_error(name: str) -> None:
    # 从前 graph_triplet_v1.txt 在这里抛 KeyError: '\n  "triplets"'。
    rendered = render_prompt(_read(name), **_TEMPLATE_SLOTS[name])
    assert rendered


@pytest.mark.parametrize("name", sorted(_TEMPLATE_SLOTS))
def test_all_slots_are_substituted(name: str) -> None:
    """渲染后不得残留任何已登记的占位符。

    这是 ``.replace`` 路线的独有风险：占位符拼错时 format 会抛错，replace 只是
    悄悄什么都不做，于是模型收到一句字面的 ``{question}`` 并照样编一个答案出来。
    """
    rendered = render_prompt(_read(name), **_TEMPLATE_SLOTS[name])
    for slot in _TEMPLATE_SLOTS[name]:
        assert "{" + slot + "}" not in rendered, f"{name} 的 {{{slot}}} 未被替换"


@pytest.mark.parametrize("name", sorted(_TEMPLATE_SLOTS))
def test_literal_json_braces_survive_rendering(name: str) -> None:
    """字面量 JSON 骨架必须原样活到提示词里。

    它是模型输出合法 JSON 的唯一依据。转义成 ``{{`` 后如果哪天渲染方式再变，
    模型会收到一对双花括号并照抄——解析全部失败。所以模板里一律写单花括号，
    由 render_prompt 保证不动它。

    骨架的**有无**是从模板自身推断的，不另外维护一张名单：generation_v1.txt
    要的是自然语言答案，本来就没有 JSON 骨架，不该被硬塞一条断言。
    """
    raw = _read(name)
    rendered = render_prompt(raw, **_TEMPLATE_SLOTS[name])

    assert "{{" not in rendered and "}}" not in rendered, (
        f"{name} 渲染后出现双花括号——模板里还留着 str.format 时代的转义"
    )
    skeleton = re.compile(r'\{\s*\n?\s*"')
    if skeleton.search(raw):
        assert skeleton.search(rendered), f"{name} 渲染后 JSON 骨架不见了"


@pytest.mark.parametrize("name", sorted(_TEMPLATE_SLOTS))
def test_rendering_does_not_disturb_other_braces(name: str) -> None:
    """渲染只应吃掉占位符的花括号，一对不多一对不少。

    这条对所有模板都成立（包括没有 JSON 骨架的 generation_v1.txt），
    是"format 会啃掉别的花括号"这个缺陷的直接反面。
    """
    raw = _read(name)
    slots = _TEMPLATE_SLOTS[name]
    # 取值均不含花括号，因此消耗量 = 各占位符出现次数之和
    consumed = sum(raw.count("{" + s + "}") for s in slots)
    rendered = render_prompt(raw, **slots)

    assert rendered.count("{") == raw.count("{") - consumed
    assert rendered.count("}") == raw.count("}") - consumed


def test_unknown_braces_are_left_alone() -> None:
    """未传入的花括号内容一律不碰——这正是与 str.format 的分水岭。"""
    tpl = '给你 {query}。只输出：{\n  "triplets": [{"subject": "甲"}]\n}'
    out = render_prompt(tpl, query="报销")

    assert "报销" in out
    assert '{\n  "triplets": [{"subject": "甲"}]\n}' in out


def test_values_containing_placeholders_are_not_reinterpreted() -> None:
    """用户文档里恰好写着 {max_triplets} 时，不得篡改提示词。

    逐个 ``.replace`` 会二次解释已替换的内容（先填 text，text 里的
    ``{max_triplets}`` 又被下一轮 replace 命中）；render_prompt 是单次扫描。
    """
    out = render_prompt(
        "文本：{text}\n上限：{max_triplets}", text="用法：{max_triplets}", max_triplets=20
    )

    assert out == "文本：用法：{max_triplets}\n上限：20"


def test_missing_slot_raises_instead_of_silently_doing_nothing() -> None:
    """模板改名 / 占位符拼错必须当场炸，而不是发一个残缺提示词出去。"""
    with pytest.raises(KeyError, match="quesiton"):
        render_prompt("问题：{question}", quesiton="拼错了")


def test_triplet_extractor_builds_a_prompt_for_a_real_template() -> None:
    """端到端：真模板 + 真调用点。这是当初 100% 失败的那条路径。"""
    from indexing.triplet_extractor import TripletExtractor

    captured: dict[str, str] = {}

    class _CapturingLlm:
        def chat_json(self, messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return {"triplets": [{"subject": "甲", "predicate": "收购", "object": "乙"}]}

    triplets = TripletExtractor(llm=_CapturingLlm(), max_triplets=7).extract(
        "甲公司收购了乙公司"
    )

    assert [t.subject for t in triplets] == ["甲"]
    assert "甲公司收购了乙公司" in captured["prompt"]
    assert "最多输出 7 条" in captured["prompt"]
    # JSON 骨架必须完好——它是模型输出可解析结果的依据
    assert '"triplets"' in captured["prompt"]
    assert "{text}" not in captured["prompt"]
