"""验证 + 弃权模块离线冒烟测试。

覆盖：
- (a) L1 存在性：伪造的越界引用编号被标记 unsupported；
- (b) L2 文本哈希：入库后被编辑过的 chunk 被标记 stale；
- (c) L3 蕴含：judge_llm 的 unsupported 判定被正确映射；
- (d) 事后引用指派：无引用答案经嵌入对齐自动挂上引用；
- (e) LLM 失败降级：L3 异常不中断验证，降级为 exists_only；
- (f) 双重阈值弃权门四类决策（空检索 / 低检索 / 低蕴含 / 通过）；
- (g) 检索阈值分位数校准。

全程不联网、不加载模型、不连接 Milvus（FakeMilvus / FakeLLM /
FakeEmbedder 全内存实现）。

运行：
    python scripts/smoke_verify.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 保证从任意工作目录运行都能找到 config / models / core / indexing / verify
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 控制台默认 GBK，统一按 UTF-8 输出避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.llm import LLMError
from indexing.hashing import text_hash
from models.schemas import Chunk, RetrievedChunk
from verify import (
    AbstentionGate,
    CitationVerifier,
    NliNotImplemented,
    calibrate_retrieval_threshold,
    split_claims,
)


# ---------------------------------------------------------------------------
# 测试桩
# ---------------------------------------------------------------------------

def make_chunk(chunk_id: str, text: str, hash_override: str | None = None) -> Chunk:
    """构造文本哈希与文本一致的 Chunk（hash_override 可模拟入库后编辑）。"""
    return Chunk(
        chunk_id=chunk_id,
        doc_id="doc-001",
        text=text,
        text_hash=hash_override or text_hash(text),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def as_evidence(chunks: list[Chunk]) -> list[RetrievedChunk]:
    """Chunk 列表 -> 检索结果列表（rank 从 1 起，与生成模块一致）。"""
    return [
        RetrievedChunk(chunk=c, score=0.9, rank=i, branch="hybrid")
        for i, c in enumerate(chunks, 1)
    ]


class FakeMilvus:
    """内存 Milvus 桩：get_chunks_by_ids 返回"最新"记录。"""

    def __init__(self, chunks_by_id: dict[str, Chunk]) -> None:
        self._by_id = chunks_by_id

    def get_chunks_by_ids(self, ids: list[str]) -> list[Chunk]:
        return [self._by_id[i] for i in ids if i in self._by_id]


class FakeLLM:
    """judge 槽位桩：按声明文本返回预设判定，未知声明默认 supported。"""

    _CLAIM_RE = re.compile(r"\d+\. 声明：(.+?)（引用编号：\[([^\]]*)\]）")

    def __init__(self, verdicts: dict[str, str] | None = None) -> None:
        self.verdicts = dict(verdicts or {})
        self.calls = 0

    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        self.calls += 1
        text = "\n".join(str(m.get("content", "")) for m in messages)
        verdicts = []
        for m in self._CLAIM_RE.finditer(text):
            claim = m.group(1).strip()
            cited = [int(x) for x in m.group(2).split(",") if x.strip()]
            verdicts.append(
                {
                    "claim": claim,
                    "status": self.verdicts.get(claim, "supported"),
                    "cited_chunk_ids": cited,
                    "reason": "fake judge",
                }
            )
        return {"verdicts": verdicts}


class FailingLLM:
    """总是抛异常的 judge 桩（验证 L3 降级路径）。"""

    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        raise LLMError("模拟 judge 服务不可用")


class FakeEmbedder:
    """确定性嵌入桩：按关键词映射到正交向量，余弦结构已知。"""

    @staticmethod
    def _vec(text: str) -> list[float]:
        if "首都" in text:
            return [1.0, 0.0, 0.0]
        if "政治" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

# c1：正常 chunk；c3：正常 chunk（事后指派的候选）；c2：入库后被编辑
C1_TEXT = "北京是中国首都，人口约两千万。"
C3_TEXT = "政治中心是北京。"
C2_OLD_TEXT = "旧版北京介绍，内容已过期。"
C2_NEW_TEXT = "新版北京介绍，内容已更新。"

C1 = make_chunk("c1", C1_TEXT)
C2 = make_chunk("c2", C2_OLD_TEXT)
# c2 在 Milvus 里的**当前**记录：正文已改，哈希也随之更新（入库时一起写的，
# 现实中这两者永远自洽）。陈旧性只能靠"当前哈希"与"检索快照哈希"跨时刻比较发现。
# 从前这里用 hash_override 硬造出一条自相矛盾的记录（新文本配旧哈希），
# 好让 `record.text_hash != text_hash(record.text)` 成立——那是在为一段
# 永远不成立的死代码量身定做输入，测的是缺陷本身。
C2_FRESH = make_chunk("c2", C2_NEW_TEXT)
C3 = make_chunk("c3", C3_TEXT)

# FakeMilvus 中 c2 的最新记录：文本已变，哈希也已更新
FAKE_MILVUS = FakeMilvus({"c1": C1, "c2": C2_FRESH, "c3": C3})

EVIDENCE = as_evidence([C1, C2, C3])  # 检索证据仍是旧文本（模拟检索快照）

DEFAULT_JUDGE = FakeLLM()
UNSUPPORTED_JUDGE = FakeLLM({"北京是首都": "unsupported"})


# ---------------------------------------------------------------------------
# 断言辅助
# ---------------------------------------------------------------------------

def check(step: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{step} 失败: {detail}")
    print(f"[PASS] {step}" + (f"  ({detail})" if detail else ""))


def run() -> int:
    # ---------- 声明切分 ----------
    claims = split_claims("北京是首都。嗯。上海是魔都")
    check(
        "claims.split_claims 短碎片并入前句",
        claims == ["北京是首都嗯", "上海是魔都"],
        f"got {claims!r}",
    )
    check(
        "claims.split_claims 空输入",
        split_claims("") == [] and split_claims("  。  ") == [],
    )

    # ---------- (a) L1 存在性 ----------
    v = CitationVerifier(milvus=FAKE_MILVUS, judge_llm=DEFAULT_JUDGE)
    r = v.verify("北京是首都[1]。火星有生命[9]。", EVIDENCE)
    fake = [c for c in r.citations if c.status == "unsupported"]
    check(
        "L1 越界编号标记 unsupported",
        len(fake) == 1 and "citation id 不存在或不在本次检索集合" in fake[0].reason,
        f"citations={[(c.chunk_id, c.status) for c in r.citations]}",
    )
    check(
        "L1 合法编号保持 ok",
        any(c.chunk_id == "c1" and c.status == "ok" for c in r.citations),
    )
    check("L1 结果 supported 应为 False", r.supported is False)

    # ---------- (b) L2 文本哈希陈旧性 ----------
    v = CitationVerifier(milvus=FAKE_MILVUS, judge_llm=DEFAULT_JUDGE)
    r = v.verify("北京是首都[2]。", EVIDENCE)
    stale = [c for c in r.citations if c.status == "stale"]
    check(
        "L2 编辑过的 chunk 标记 stale",
        len(stale) == 1 and stale[0].chunk_id == "c2" and stale[0].reason == "文本已变更（stale）",
        f"citations={[(c.chunk_id, c.status, c.reason) for c in r.citations]}",
    )

    # ---------- (c) L3 蕴含判定 ----------
    v = CitationVerifier(milvus=None, judge_llm=UNSUPPORTED_JUDGE)
    r = v.verify("北京是首都[1]。", EVIDENCE)
    bad = [c for c in r.citations if c.status == "unsupported"]
    check(
        "L3 unsupported 判定映射",
        len(bad) == 1 and bad[0].reason == "L3 蕴含校验不支撑",
        f"citations={[(c.status, c.reason) for c in r.citations]}",
    )
    check(
        "L3 蕴含分数记录",
        r.entailment_scores.get("北京是首都") == 0.0,
        f"scores={r.entailment_scores}",
    )
    check("L3 结果 supported 应为 False", r.supported is False)

    # ---------- (d) 事后引用指派 ----------
    v = CitationVerifier(
        milvus=None,
        judge_llm=DEFAULT_JUDGE,
        embedder=FakeEmbedder(),
    )
    r = v.verify("北京是中国首都。也是政治中心。", EVIDENCE)
    assigned_ids = sorted(c.chunk_id for c in r.citations)
    check(
        "事后指派挂上引用",
        assigned_ids == ["c1", "c3"],
        f"chunk_ids={assigned_ids}",
    )
    check(
        "事后指派引用进入 L3 且全部 ok",
        all(c.status == "ok" for c in r.citations),
        f"citations={[(c.chunk_id, c.status) for c in r.citations]}",
    )
    check(
        "事后指派备注记录",
        "使用事后引用指派" in r.notes,
        f"notes={r.notes}",
    )
    check("事后指派后无缺失证据", r.missing_evidence is False)

    # ---------- (e) L3 失败降级 ----------
    v = CitationVerifier(milvus=None, judge_llm=FailingLLM())
    r = v.verify("北京是首都[1]。", EVIDENCE)
    degraded = [c for c in r.citations if c.status == "exists_only"]
    check(
        "L3 失败降级 exists_only 且不抛出",
        len(degraded) == 1 and degraded[0].reason == "L3 判定失败（降级）",
        f"citations={[(c.status, c.reason) for c in r.citations]}",
    )
    check(
        "L3 失败记入 notes",
        any("降级" in n for n in r.notes),
        f"notes={r.notes}",
    )
    # judge 挂掉时我们对蕴含一无所知。从前这里给每条声明合成 0.5 分，而默认阈值
    # 是 0.6 —— 于是"不知道"被伪装成"已判定为不支撑"，在检索和生成都已付费之后
    # 强制弃权，且与"知识库里真的没有"对用户完全不可区分。
    check(
        "L3 失败不再合成蕴含分",
        r.entailment_scores == {},
        f"entailment_scores={r.entailment_scores}",
    )
    check(
        "L3 失败标记为未评估",
        r.entailment_evaluated is False,
    )
    check(
        "L3 失败时向弃权门传 None（不参与判定）而非空列表",
        r.gate_entailment_scores() is None,
        f"gate_entailment_scores={r.gate_entailment_scores()}",
    )

    # ---------- 附加：无引用且无指派 -> missing_evidence 信号 ----------
    v = CitationVerifier(milvus=None, judge_llm=DEFAULT_JUDGE)
    r = v.verify("北京是首都。", EVIDENCE)
    check(
        "无引用无指派触发缺失证据信号",
        r.missing_evidence is True and r.citations == [],
    )

    # ---------- 附加：entailment_mode skip / nli ----------
    judge_probe = FakeLLM()
    v = CitationVerifier(milvus=None, judge_llm=judge_probe, entailment_mode="skip")
    r = v.verify("北京是首都[1]。", EVIDENCE)
    check(
        "entailment_mode=skip 不调用 judge 且保持 ok",
        judge_probe.calls == 0 and all(c.status == "ok" for c in r.citations),
    )
    # settings 文档承诺 skip 模式下"只剩检索分数这一道闸"。从前它传出的是空列表，
    # 被门读成"有证据但无可用蕴含分"直接弃权 —— 关掉 L3 反而导致 100% 拒答，
    # 与文档承诺完全相反。
    check(
        "entailment_mode=skip 向弃权门传 None 而非空列表",
        r.entailment_evaluated is False and r.gate_entailment_scores() is None,
        f"evaluated={r.entailment_evaluated} gate={r.gate_entailment_scores()}",
    )
    v = CitationVerifier(milvus=None, judge_llm=DEFAULT_JUDGE, entailment_mode="nli")
    try:
        v.verify("北京是首都[1]。", EVIDENCE)
        raise AssertionError("nli 模式应抛出 NliNotImplemented")
    except NliNotImplemented:
        check("entailment_mode=nli 抛出 NliNotImplemented", True)

    # ---------- (f) 双重阈值弃权门 ----------
    gate = AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6)
    abstain, reason = gate.decide([])
    check("弃权: 空检索分数", abstain and reason == "无检索结果", reason)
    abstain, reason = gate.decide([0.2, 0.1])
    check("弃权: 低检索分数", abstain and reason == "知识库无相关内容", reason)
    abstain, reason = gate.decide([0.8], [0.4])
    check("弃权: 低蕴含分数", abstain and reason == "证据不足以支撑可靠声明", reason)
    abstain, reason = gate.decide([0.8], [0.9])
    check("弃权: 全部通过不弃权", (not abstain) and reason == "", reason)
    abstain, reason = gate.decide([0.8], None)
    check("弃权: 蕴含分数缺省不判定", (not abstain) and reason == "", reason)
    # rerank 未生效时 score 是 RRF 融合分：只由名次决定，不含相似度信息。
    # 双路 RRF 的满分是 2/(60+1)=0.0328，远低于 0.3，拿它过闸 = 完美命中也被拒。
    RRF_PERFECT = 2.0 / 61.0
    abstain, reason = gate.decide([RRF_PERFECT], None)
    check(
        "RRF 分当可比分用会误判（这正是要修的行为）",
        abstain and reason == "知识库无相关内容",
        f"score={RRF_PERFECT:.6f} reason={reason}",
    )
    abstain, reason = gate.decide([RRF_PERFECT], None, retrieval_scores_comparable=False)
    check(
        "标记不可比后跳过检索阈值判定",
        (not abstain) and reason == "",
        reason,
    )
    # 但"一条都没召回"任何时候都该弃权——不可比不等于放行一切。
    abstain, reason = gate.decide([], None, retrieval_scores_comparable=False)
    check(
        "不可比时空检索结果仍弃权",
        abstain and reason == "无检索结果",
        reason,
    )
    # 不可比只关掉检索这一道闸，蕴含闸照常。
    abstain, reason = gate.decide([RRF_PERFECT], [0.4], retrieval_scores_comparable=False)
    check(
        "不可比时蕴含闸仍然生效",
        abstain and reason == "证据不足以支撑可靠声明",
        reason,
    )
    gate2 = AbstentionGate.from_settings()
    check(
        "弃权门读取配置默认阈值",
        gate2.retrieval_threshold == 0.3 and gate2.entailment_threshold == 0.6,
    )

    # ---------- (g) 检索阈值分位数校准 ----------
    try:
        import numpy  # noqa: F401

        numpy_ok = True
    except ImportError:
        numpy_ok = False
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    expected = 0.61 if numpy_ok else 0.6  # numpy 线性插值 vs 最近秩
    got = calibrate_retrieval_threshold(scores, 0.85)
    check(
        "阈值分位数校准",
        abs(got - expected) < 1e-9,
        f"expected={expected} got={got} (numpy={numpy_ok})",
    )

    print()
    print("SMOKE VERIFY PASSED (exit 0)")
    return 0


def main() -> int:
    try:
        return run()
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本统一兜底
        print(f"[FAIL] 未预期异常: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
