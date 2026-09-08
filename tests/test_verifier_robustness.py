from __future__ import annotations

from datetime import datetime, timezone

import pytest

from indexing.hashing import text_hash
from models.schemas import Chunk, RetrievedChunk
from verify.verifier import (
    MAX_EVIDENCE_CHUNKS,
    CitationVerifier,
    resolve_verify_settings,
)

# 这个文件守的是验证层的**失败姿态**。
#
# 验证层的职责是给已经生成好的答案打标。它自己坏掉时有两种错法，方向相反、都很贵：
#
#   fail-closed 过头：judge 抖一下 -> 整个答案被丢弃 -> 二轮重算。双倍计费，零输出。
#   fail-open：judge 漏判 / 返回体缺字段 -> 那条声明默认 supported(1.0)
#              -> 幻觉带着绿色引用发给用户。这是整条链上唯一的 fail-open。
#
# 正确姿态是第三种：**如实说"没判"**，让弃权门知道这一轮蕴含信息不可用
# （传 None 而非分数），而不是编一个分数出来。

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def chunk(cid: str, text: str, hash_of: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id="doc-1",
        text=text,
        text_hash=text_hash(hash_of if hash_of is not None else text),
        created_at=_NOW,
        updated_at=_NOW,
    )


def evidence(*chunks: Chunk) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk=c, score=0.9, rank=i, branch="hybrid")
        for i, c in enumerate(chunks, 1)
    ]


C1 = chunk("c1", "北京是中国首都。")
EV = evidence(C1)


class ScriptedJudge:
    """按预设 payload 回话的 judge 桩（可回不合规结构）。"""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = 0
        self.last_prompt = ""

    def chat_json(self, messages, schema_hint=None):
        self.calls += 1
        self.last_prompt = "\n".join(str(m.get("content", "")) for m in messages)
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeMilvus:
    def __init__(self, by_id: dict[str, Chunk], exc: BaseException | None = None):
        self._by_id = by_id
        self._exc = exc

    def get_chunks_by_ids(self, ids):
        if self._exc is not None:
            raise self._exc
        return [self._by_id[i] for i in ids if i in self._by_id]


# --------------------------------------------------------------------------- #
# fail-open：未知 / 缺失判定不得默认 supported
# --------------------------------------------------------------------------- #

def test_missing_status_field_defaults_to_neutral_not_supported() -> None:
    # judge 回了这条声明，但没给 status。从前 .get("status", "supported")
    # 让它直接拿到 1.0 满分蕴含。
    judge = ScriptedJudge({"verdicts": [{"claim": "北京是首都", "reason": "忘了写"}]})
    r = CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", EV)

    assert r.entailment_scores["北京是首都"] == 0.5, "缺 status 不该等于「已确认支撑」"


def test_empty_status_string_defaults_to_neutral() -> None:
    judge = ScriptedJudge({"verdicts": [{"claim": "北京是首都", "status": ""}]})
    r = CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", EV)

    assert r.entailment_scores["北京是首都"] == 0.5


def test_unknown_status_string_is_not_treated_as_support() -> None:
    judge = ScriptedJudge(
        {"verdicts": [{"claim": "北京是首都", "status": "probably_fine"}]}
    )
    r = CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", EV)

    assert r.entailment_scores["北京是首都"] < 1.0


def test_explicit_support_still_scores_full_marks() -> None:
    # 修完 fail-open 别把正常路径也修坏了。
    judge = ScriptedJudge(
        {"verdicts": [{"claim": "北京是首都", "status": "supported"}]}
    )
    r = CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", EV)

    assert r.entailment_scores["北京是首都"] == 1.0
    assert r.entailment_evaluated is True


# --------------------------------------------------------------------------- #
# fail-closed 过头：结构异常不得穿透验证层
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "label,payload",
    [
        ("verdicts 是字符串列表", {"verdicts": ["supported"]}),
        ("verdicts 是数字列表", {"verdicts": [1, 2, 3]}),
        ("verdicts 是字符串", {"verdicts": "supported"}),
        ("整个返回体是 None", None),
        ("返回体缺 verdicts", {"foo": "bar"}),
        ("judge 抛非 LLMError", TypeError("桩：模型客户端内部炸了")),
        ("judge 抛 KeyError", KeyError("choices")),
    ],
)
def test_malformed_judge_output_degrades_instead_of_discarding_the_answer(
    label: str, payload
) -> None:
    judge = ScriptedJudge(payload)
    v = CitationVerifier(milvus=None, judge_llm=judge)

    # 不抛：答案已经生成并付过费了，验证层坏掉不该让它作废。
    r = v.verify("北京是首都[1]。", EV)

    assert r.citations, f"{label}: 降级不该把引用清空"
    assert all(c.status == "exists_only" for c in r.citations), (
        f"{label}: 未能确认支撑，应降级为 exists_only"
    )
    # 关键：如实说"没判"，而不是编个 0.5 出来。0.5 低于默认阈值 0.6，
    # 会把 judge 故障伪装成"证据不足"，强制弃权且与"知识库里真的没有"不可区分。
    assert r.entailment_evaluated is False, f"{label}: judge 坏了不算「已判定」"
    assert r.gate_entailment_scores() is None, f"{label}: 必须向弃权门传 None"
    assert any("降级" in n for n in r.notes), f"{label}: 降级必须留痕"


def test_partially_malformed_verdicts_still_use_the_good_entries() -> None:
    # 一条坏条目不该带走整批。能对上的照常判，对不上的按 neutral。
    judge = ScriptedJudge(
        {
            "verdicts": [
                "垃圾条目",
                {"claim": "上海是魔都", "status": "unsupported"},
            ]
        }
    )
    v = CitationVerifier(milvus=None, judge_llm=judge)
    r = v.verify("北京是首都[1]。上海是魔都[1]。", EV)

    assert r.entailment_evaluated is True
    assert r.entailment_scores["上海是魔都"] == 0.0, "能对上的判定必须生效"
    assert r.entailment_scores["北京是首都"] == 0.5, "对不上的按 neutral，不是 supported"


# --------------------------------------------------------------------------- #
# 证据块上限：与生成侧对齐
# --------------------------------------------------------------------------- #

def test_evidence_cap_matches_the_generator() -> None:
    # 两边必须同一把尺子：生成器只喂前 N 条，judge 若铺开全部，
    # 答案里的 [N] 编号就和 judge 看到的编号错位了。
    from generation.generator import MAX_EVIDENCE_CHUNKS as GEN_CAP

    assert MAX_EVIDENCE_CHUNKS == GEN_CAP


def test_judge_prompt_caps_evidence_count() -> None:
    many = evidence(*[chunk(f"c{i}", f"正文{i}") for i in range(30)])
    judge = ScriptedJudge({"verdicts": [{"claim": "北京是首都", "status": "supported"}]})
    CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", many)

    assert f"[{MAX_EVIDENCE_CHUNKS}]" in judge.last_prompt
    assert f"[{MAX_EVIDENCE_CHUNKS + 1}]" not in judge.last_prompt, (
        "证据块超出上限，长尾文档会把 prompt 顶爆——而爆掉只表现为 judge 失败，"
        "失败又走降级，于是完全不可见"
    )


def test_judge_prompt_truncates_a_huge_chunk() -> None:
    huge = evidence(chunk("c1", "甲" * 50_000))
    judge = ScriptedJudge({"verdicts": [{"claim": "北京是首都", "status": "supported"}]})
    CitationVerifier(milvus=None, judge_llm=judge).verify("北京是首都[1]。", huge)

    assert len(judge.last_prompt) < 10_000
    assert "已截断" in judge.last_prompt, "截断必须显式标注，否则 judge 会把断点当原文结尾"


# --------------------------------------------------------------------------- #
# L2 陈旧性：跨时刻比较，不是自己和自己比
# --------------------------------------------------------------------------- #

def test_l2_flags_text_edited_after_retrieval() -> None:
    # 检索快照是旧文本；Milvus 当前记录是新文本（正文和哈希一起更新，现实中永远自洽）。
    snapshot = chunk("c1", "旧版介绍")
    current = chunk("c1", "新版介绍")
    v = CitationVerifier(
        milvus=FakeMilvus({"c1": current}),
        judge_llm=ScriptedJudge({"verdicts": []}),
    )
    r = v.verify("有个说法[1]。", evidence(snapshot))

    assert [c.status for c in r.citations] == ["stale"]


def test_l2_passes_when_text_is_unchanged() -> None:
    same = chunk("c1", "没变过的正文")
    v = CitationVerifier(
        milvus=FakeMilvus({"c1": chunk("c1", "没变过的正文")}),
        judge_llm=ScriptedJudge({"verdicts": [{"claim": "有个说法", "status": "supported"}]}),
    )
    r = v.verify("有个说法[1]。", evidence(same))

    assert [c.status for c in r.citations] == ["ok"]


def test_l2_self_consistent_record_is_never_stale() -> None:
    """一条自洽的记录不该被判 stale —— 这正是旧实现唯一会"触发"的条件。

    旧代码比的是 ``record.text_hash != text_hash(record.text)``：同一条记录的
    存储哈希 vs 它自己文本重算的哈希。这两个值入库时一起写，**恒等**，
    所以旧的 L2 是一段永不成立的死代码。这里用一条正文变了、快照也跟着变了的
    记录钉死：内容一致就是一致，不管文本本身长什么样。
    """
    edited = chunk("c1", "改过的正文")
    v = CitationVerifier(
        milvus=FakeMilvus({"c1": edited}),
        judge_llm=ScriptedJudge({"verdicts": [{"claim": "有个说法", "status": "supported"}]}),
    )
    r = v.verify("有个说法[1]。", evidence(edited))

    assert [c.status for c in r.citations] == ["ok"]


def test_l2_flags_a_deleted_chunk() -> None:
    v = CitationVerifier(
        milvus=FakeMilvus({}), judge_llm=ScriptedJudge({"verdicts": []})
    )
    r = v.verify("有个说法[1]。", EV)

    assert [c.status for c in r.citations] == ["stale"]


@pytest.mark.parametrize(
    "label,milvus",
    [
        ("未接入 milvus", None),
        ("取回失败", FakeMilvus({}, exc=RuntimeError("桩：milvus 不可达"))),
    ],
)
def test_l2_says_so_when_it_cannot_compare(label: str, milvus) -> None:
    # 没有"当前"就没得比。此时必须记 note 说明本轮没校验，
    # 而不是让快照和自己比一遍然后报"通过"——那是把"不知道"写成"没问题"。
    v = CitationVerifier(
        milvus=milvus,
        judge_llm=ScriptedJudge({"verdicts": [{"claim": "北京是首都", "status": "supported"}]}),
    )
    r = v.verify("北京是首都[1]。", EV)

    assert any("L2" in n and "未校验" in n for n in r.notes), (
        f"{label}: 未校验必须留痕，notes={r.notes}"
    )


# --------------------------------------------------------------------------- #
# 配置兜底：nli 是唯一能通过校验却让每个请求都炸的取值
# --------------------------------------------------------------------------- #

class _Cfg:
    def __init__(self, **kw):
        self.verify = type("V", (), kw)()


def test_nli_mode_is_rejected_at_config_time() -> None:
    mode, _strict, _ratio = resolve_verify_settings(
        _Cfg(entailment_mode="nli", strict=True, sample_ratio=1.0)
    )
    assert mode == "llm", (
        "nli 在 _ENTAILMENT_MODES 里，于是它通过了配置校验，"
        "然后在每个请求的 L3 入口抛 NliNotImplemented。"
        "resolve_verify_settings 的全部意义就是让配置写错只会「更慢更严」而非坏服务"
    )


@pytest.mark.parametrize("mode,expected", [
    ("llm", "llm"),
    ("skip", "skip"),
    ("LLM", "llm"),
    ("  skip  ", "skip"),
    ("胡写的", "llm"),
    (None, "llm"),
])
def test_other_modes_resolve_as_before(mode, expected) -> None:
    got, _, _ = resolve_verify_settings(
        _Cfg(entailment_mode=mode, strict=True, sample_ratio=1.0)
    )
    assert got == expected
