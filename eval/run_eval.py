"""RAG4C 评测执行器与 CLI。

用法：:

    python -m eval.run_eval --dry-run                    # 离线干跑（默认）
    python -m eval.run_eval --pipeline rag:answer_query  # 真实评测（惰性导入管线）

评测流程：对每条用例调用 ``pipeline_fn(question)`` 得到 QueryResult，
非拒答（有回答且未弃权）的用例再交给两个独立裁判打分。
聚合指标包括拒答率、过度拒答率、幻觉率、平均有据性、平均相关性、引用失败率。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from core.llm import ParseFallbackError
from eval.judges import GroundednessJudge, RelevanceJudge, create_judges
from models.schemas import Chunk, Citation, QueryResult


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class CaseResult(BaseModel):
    """单条评测用例的结果。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    question: str
    unanswerable: bool
    abstained: bool
    answered: bool
    groundedness: float | None = None
    relevance: float | None = None
    citations_ok: bool = True
    #: 引用总数 / 非 ok 数。存下来是为了让聚合指标能从报告**重算**，
    #: 而不是只能信一次运行时的中间变量——也是对比两轮报告时的依据。
    citations_total: int = 0
    citations_failed: int = 0
    #: 本用例是否踩到上游故障（检索/生成/裁判/引用校验没跑成）。
    #: 与 :data:`EvalReport.degraded_cases` 同源——见那里的说明。
    degraded: bool = False
    degraded_kinds: list[str] = Field(default_factory=list)
    #: 本用例的 LLM 用量台账原文（``QueryResult.usage``；没开台账时为空字典）。
    usage: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class EvalReport(BaseModel):
    """一次评测的报告：聚合指标 + 逐用例结果。"""

    model_config = ConfigDict(extra="ignore")

    metrics: dict[str, float] = Field(default_factory=dict)
    cases: list[CaseResult] = Field(default_factory=list)
    #: 上游故障用例数。与质量指标分开报：一次限流或一次裁判超时会把
    #: 有据性/幻觉率拉低，那是"这一轮数字不可信"，不是"模型变笨了"。
    #: 检索层 runner 里同一个语义叫 ``degraded_cases``，这里沿用命名。
    degraded_cases: int = 0
    #: 故障分类计数 ``{"judge_failed": 3, ...}``，用来判断是偶发还是系统性。
    degraded_kinds: dict[str, int] = Field(default_factory=dict)
    #: 全轮 LLM 用量汇总（逐 case 台账相加）；开不出台账时为空字典。
    usage: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 证据提取
# ---------------------------------------------------------------------------

def _build_chunk(item: Any, index: int) -> Chunk | None:
    """把证据条目构建为 Chunk：支持完整字段 dict / 部分字段 dict / 纯文本。"""
    now = datetime.now()
    if isinstance(item, Chunk):
        return item
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return Chunk(
            chunk_id=f"evidence_{index + 1}",
            doc_id="evidence",
            text=text,
            text_hash="",
            created_at=now,
            updated_at=now,
        )
    if isinstance(item, dict):
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            return Chunk.model_validate(item)
        except Exception:
            return Chunk(
                chunk_id=str(item.get("chunk_id") or f"evidence_{index + 1}"),
                doc_id=str(item.get("doc_id") or "evidence"),
                text=text.strip(),
                text_hash=str(item.get("text_hash") or ""),
                created_at=item.get("created_at") or now,
                updated_at=item.get("updated_at") or now,
            )
    return None


def extract_evidence(qr: QueryResult) -> list[Chunk]:
    """从 QueryResult 中提取裁判用证据片段。

    读取顺序：

    1. ``qr.verdict["evidence_chunks"]``：元素可为完整 Chunk dict、
       含 text 的部分字段 dict，或纯字符串文本；
    2. ``qr.traces`` 中以 ``evidence:`` 开头的行，格式
       ``evidence:<chunk_id>|<text>``。

    两者都没有时返回空列表（裁判收到空证据，按 Evidence-Only 原则判无支撑）。
    真实管线（rag.py）可在生成结果时把检索片段写入上述位置供裁判使用。
    """
    chunks: list[Chunk] = []
    raw_list = qr.verdict.get("evidence_chunks") if isinstance(qr.verdict, dict) else None
    if isinstance(raw_list, list):
        for i, item in enumerate(raw_list):
            chunk = _build_chunk(item, i)
            if chunk is not None:
                chunks.append(chunk)
    if not chunks:
        for line in qr.traces:
            if line.startswith("evidence:"):
                body = line[len("evidence:"):]
                chunk_id, _, text = body.partition("|")
                chunk = _build_chunk(text, len(chunks))
                if chunk is not None:
                    chunks.append(chunk.model_copy(update={"chunk_id": chunk_id or chunk.chunk_id}))
    return chunks


# ---------------------------------------------------------------------------
# 降级识别：把"上游挂了"从"质量差"里摘出来
# ---------------------------------------------------------------------------
#
# 检索层 runner 里 ``degraded`` 的分母是"答了的用例"，因为检索层只在答了的
# 用例上才可能有降级；答案层不一样——上游一挂，用例往往直接变成弃权，而弃
# 权恰恰是"最该被看见"的降级。所以这里的分母是**全部用例**（见
# :meth:`Evaluator.evaluate`）。

#: ``(分类名, 在 traces / verification.notes 里出现的特征串)``。
#: 只收**故障**，不收配置选择：``entailment_mode=skip`` 是人为关掉的开关，
#: 对每条用例一视同仁，算进降级率只会稀释真正的问题。
_DEGRADATION_MARKERS: tuple[tuple[str, str], ...] = (
    ("retrieval_failed", "检索失败（弃权）"),
    ("generation_failed", "生成失败"),
    ("entailment_unavailable", "蕴含不可用"),
    ("stale_check_failed", "取回最新 chunk 失败"),
    ("citation_reassign_failed", "引用指派嵌入失败"),
    ("second_round_failed", "二轮检索失败"),
    ("circuit_open", "熔断开启"),
    ("graph_fallback", "图编排不可用"),
    ("qa_retrieval_skipped", "QA 检索跳过"),
)


def detect_degradation(
    qr: QueryResult,
    *,
    groundedness: float | None = None,
    relevance: float | None = None,
    answered: bool = True,
) -> list[str]:
    """识别一条用例上的上游故障，返回分类名列表（无故障则空列表）。

    两类来源：

    1. 管线自己的 traces（含 ``verification.notes``）——检索/生成/引用校验
       没跑成都在这里留痕；
    2. 裁判打分失败（``score is None``）——那是评测侧的上游故障，同样不该
       被读成"这条答案质量差"。

    Args:
        qr: 管线返回的查询结果。
        groundedness: 有据性裁判分数；``None`` 且本条已作答 = 裁判没判成。
        relevance: 相关性裁判分数；同上。
        answered: 本条是否真正作答（未作答时不该追究裁判分）。
    """
    kinds: list[str] = []
    haystack = "\n".join(qr.traces)
    for kind, marker in _DEGRADATION_MARKERS:
        if marker in haystack:
            kinds.append(kind)
    if answered and (groundedness is None or relevance is None):
        kinds.append("judge_failed")
    # 去重保序：同一条 trace 命中两个特征串时只算一次
    return list(dict.fromkeys(kinds))


def summarize_usage(cases: list[CaseResult]) -> dict[str, Any]:
    """把逐用例台账加总成一轮的总账。

    只加标量字段；``by_slot`` 这类明细按槽位名再并一次。没有开台账时返回
    空字典——**不**返回一堆 0，那会让"没记账"看起来像"花了 0"。
    """
    if not any(case.usage for case in cases):
        return {}
    scalar_keys = (
        "calls",
        "cached_calls",
        "failures",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "saved_prompt_tokens",
        "saved_completion_tokens",
        "saved_total_tokens",
        "cost",
        "unpriced_total_tokens",
    )
    totals: dict[str, Any] = {key: 0.0 for key in scalar_keys}
    by_slot: dict[str, dict[str, Any]] = {}
    priced = True
    for case in cases:
        usage = case.usage or {}
        for key in scalar_keys:
            totals[key] += float(usage.get(key) or 0.0)
        priced = priced and bool(usage.get("cost_priced", False))
        for entry in usage.get("by_slot") or []:
            slot = str(entry.get("slot") or "-")
            bucket = by_slot.setdefault(
                slot, {key: 0.0 for key in scalar_keys if key != "unpriced_total_tokens"}
            )
            for key in bucket:
                bucket[key] += float(entry.get(key) or 0.0)
    totals["cost"] = round(totals["cost"], 6)
    totals["cost_priced"] = priced
    totals["by_slot"] = [
        {"slot": slot, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in bucket.items()}}
        for slot, bucket in sorted(by_slot.items(), key=lambda kv: -kv[1]["total_tokens"])
    ]
    return {k: (round(v, 3) if isinstance(v, float) and k != "cost" else v) for k, v in totals.items()}


# ---------------------------------------------------------------------------
# 离线桩
# ---------------------------------------------------------------------------

class FakeJudgeLLM:
    """离线裁判桩：不联网，按 schema_hint 返回固定合法 JSON。

    - 有据性：返回 2 条判定，1 条 supported、1 条 unsupported（score=0.5）；
    - 相关性：返回 relevance=4（映射 score=0.75）。
    """

    def chat_json(self, messages: list[dict], schema_hint: str | None = None) -> dict:
        if schema_hint and "verdicts" in schema_hint:
            return {
                "verdicts": [
                    {
                        "claim": "占位声明一",
                        "status": "supported",
                        "cited_chunk_ids": [1],
                        "reason": "模拟：证据可支撑",
                    },
                    {
                        "claim": "占位声明二",
                        "status": "unsupported",
                        "cited_chunk_ids": [],
                        "reason": "模拟：证据无法支撑",
                    },
                ]
            }
        if schema_hint and "relevance" in schema_hint:
            return {
                "relevance": 4,
                "rationale": "模拟：按评分锚点 4 分",
                "unsupported_claims": [],
                "missing_facts": [],
            }
        raise ParseFallbackError(f"无法模拟未知 schema_hint: {schema_hint!r}")


def _dry_run_pipeline(dataset: list[dict]) -> Callable[[str], QueryResult]:
    """构造离线干跑管线：固定拒答策略 + 占位回答，无需任何外部依赖。"""
    by_question = {case["question"]: index for index, case in enumerate(dataset)}

    def pipeline(question: str) -> QueryResult:
        index = by_question.get(question, -1)
        if index >= 0 and index % 3 == 1:
            # 拒答样本（覆盖可答被拒与不可答拒答两种情形）
            return QueryResult(query=question, answer="", abstained=True, route="abstain")
        now = datetime.now()
        evidence: list[dict[str, Any]] = []
        citations: list[Citation] = []
        if index >= 0 and dataset[index].get("doc_text"):
            chunk_id = f"dry_{index}"
            evidence.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": "dry",
                    "text": str(dataset[index]["doc_text"]),
                    "text_hash": "",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            citations.append(
                Citation(
                    claim=f"模拟引用（{question[:12]}）",
                    chunk_id=chunk_id,
                    status="ok",
                    reason="dry-run 模拟",
                )
            )
        answer = f"（离线干跑占位回答）关于「{question}」的模拟答案。"
        return QueryResult(
            query=question,
            answer=answer,
            citations=citations,
            abstained=False,
            route="dry_run",
            verdict={"evidence_chunks": evidence} if evidence else {},
        )

    return pipeline


def run_dry_run(dataset: list[dict], *, sleep_ms: float = 0.0, workers: int = 1) -> EvalReport:
    """离线干跑：使用 FakeJudgeLLM 与占位管线计算指标，不联网、不加载模型。

    ``sleep_ms`` / ``workers`` 与真实模式同义：干跑路径也照样透传，
    免得"参数在真实模式有效、在干跑无效"这种差异让人误判开关本身坏了。
    """
    groundedness = GroundednessJudge(llm_client=FakeJudgeLLM())
    relevance = RelevanceJudge(llm_client=FakeJudgeLLM())
    return Evaluator(_dry_run_pipeline(dataset), groundedness, relevance).evaluate(
        dataset, sleep_ms=sleep_ms, workers=workers
    )


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------

def load_dataset(spec: str) -> list[dict]:
    """加载评测数据集。

    ``spec`` 形如 ``"eval/dataset_sample.py:SAMPLE_DATASET"`` 或
    ``"eval.dataset_sample:SAMPLE_DATASET"``；冒号后缺省时使用模块的
    ``SAMPLE_DATASET`` 属性；属性若为可调用对象则调用之。
    """
    module_part, _, attr = spec.partition(":")
    module_name = module_part
    if module_name.endswith(".py"):
        module_name = module_name[:-3]
    module_name = module_name.replace("/", ".").replace("\\", ".")
    module = importlib.import_module(module_name)
    obj = getattr(module, attr or "SAMPLE_DATASET")
    if callable(obj):
        obj = obj()
    if not isinstance(obj, list) or not all(isinstance(case, dict) for case in obj):
        raise ValueError(f"数据集 {spec!r} 必须是 dict 列表")
    return obj


def load_pipeline(spec: str) -> Callable[[str], QueryResult]:
    """惰性导入真实管线函数，``spec`` 形如 ``"rag:answer_query"``。"""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError(f"管线规格须为 '模块:函数' 形式，收到 {spec!r}")
    module = importlib.import_module(module_name)
    fn = getattr(module, attr)
    if not callable(fn):
        raise TypeError(f"{spec!r} 不是可调用对象")
    return fn


# ---------------------------------------------------------------------------
# 评测执行器
# ---------------------------------------------------------------------------

class Evaluator:
    """评测执行器：管线 + 两个独立裁判。"""

    def __init__(
        self,
        pipeline_fn: Callable[[str], QueryResult],
        groundedness_judge: GroundednessJudge,
        relevance_judge: RelevanceJudge,
    ) -> None:
        self.pipeline_fn = pipeline_fn
        self.groundedness_judge = groundedness_judge
        self.relevance_judge = relevance_judge

    def _run_case(self, case: dict, index: int) -> CaseResult:
        """跑一条用例：管线 -> 裁判 -> 引用统计 -> 降级识别。"""
        question = case["question"]
        qr = self.pipeline_fn(question)
        answer = (qr.answer or "").strip()
        abstained = bool(qr.abstained or not answer)
        answered = not abstained
        unanswerable = bool(case.get("unanswerable", False))

        g_score: float | None = None
        r_score: float | None = None
        if answered:
            evidence = extract_evidence(qr)
            g_res = self.groundedness_judge.judge(question, answer, evidence)
            r_res = self.relevance_judge.judge(question, answer, evidence)
            g_score = g_res.score
            r_score = r_res.score

        citations = qr.citations or []
        failed_citations = sum(1 for c in citations if c.status != "ok")

        notes = str(case.get("notes") or "")
        if answered:
            judge_failures = []
            if g_score is None:
                judge_failures.append("有据性判定失败")
            if r_score is None:
                judge_failures.append("相关性判定失败")
            if judge_failures:
                notes = f"{notes}；{'；'.join(judge_failures)}" if notes else "；".join(judge_failures)

        kinds = detect_degradation(qr, groundedness=g_score, relevance=r_score, answered=answered)
        return CaseResult(
            id=str(case.get("id") or f"case_{index}"),
            question=question,
            unanswerable=unanswerable,
            abstained=abstained,
            answered=answered,
            groundedness=g_score,
            relevance=r_score,
            citations_ok=failed_citations == 0,
            citations_total=len(citations),
            citations_failed=failed_citations,
            degraded=bool(kinds),
            degraded_kinds=kinds,
            usage=dict(qr.usage or {}),
            notes=notes,
        )

    def evaluate(
        self,
        dataset: list[dict],
        *,
        sleep_ms: float = 0.0,
        workers: int = 1,
    ) -> EvalReport:
        """对数据集逐条评测并汇总指标。

        指标定义：

        - ``refusal_rate``: 拒答用例 / 总用例
        - ``over_refusal_rate``: 答案可答却被拒 / 可答总数
        - ``hallucination_rate``: 知识库无答案却作答 / 不可答总数
        - ``avg_groundedness``: 有分用例的有据性均值（无分时 0.0）
        - ``avg_relevance``: 有分用例的相关性均值（无分时 0.0）
        - ``citation_failure_rate``: 状态非 ok 的引用 / 引用总数（无引用时 0.0）
        - ``degraded_rate``: 踩到上游故障的用例 / 总用例
          （分母取**全部用例**，不是"答了的"：上游一挂，用例往往直接变成
          弃权，而弃权恰恰是最该被看见的降级）

        拒答的用例跳过裁判（分数保持 None）；裁判解析失败同样显式为 None。

        Args:
            dataset: 用例列表。
            sleep_ms: 两条用例之间的间隔毫秒数。上游（硅基流动）有 429 限流，
                全速打会把配额烧在重试上——节流是为了拿到完整的一轮，不是为了慢。
            workers: 并发线程数，``1``（默认）= 串行。端到端延迟几乎全在等
                上游 HTTP，并发能把 145 条的墙钟时间压下来；默认保持串行是
                因为并发会让共享组件（如管线上的路由状态）出现交叉，
                默认路径不该冒这个险。
        """
        n_workers = max(1, int(workers or 1))
        slots: list[CaseResult | None] = [None] * len(dataset)
        gap = max(0.0, float(sleep_ms or 0.0)) / 1000.0

        if n_workers == 1:
            for index, case in enumerate(dataset):
                if index and gap:
                    time.sleep(gap)
                slots[index] = self._run_case(case, index)
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures: dict[Any, int] = {}
                for index, case in enumerate(dataset):
                    if index and gap:
                        time.sleep(gap)
                    futures[pool.submit(self._run_case, case, index)] = index
                for future in as_completed(futures):
                    slots[futures[future]] = future.result()

        case_results: list[CaseResult] = [c for c in slots if c is not None]
        return aggregate(case_results)


def aggregate(case_results: list[CaseResult]) -> EvalReport:
    """把逐用例结果汇总成报告（指标全部从结果重算，不依赖运行时中间量）。"""
    total_citations = sum(c.citations_total for c in case_results)
    failed_citations = sum(c.citations_failed for c in case_results)
    abstained_total = sum(1 for c in case_results if c.abstained)
    answerable = [c for c in case_results if not c.unanswerable]
    unanswerable_cases = [c for c in case_results if c.unanswerable]
    groundedness_scores = [c.groundedness for c in case_results if c.groundedness is not None]
    relevance_scores = [c.relevance for c in case_results if c.relevance is not None]
    degraded = [c for c in case_results if c.degraded]
    kinds: dict[str, int] = {}
    for case in degraded:
        for kind in case.degraded_kinds:
            kinds[kind] = kinds.get(kind, 0) + 1

    n = len(case_results)
    metrics: dict[str, float] = {
        "refusal_rate": (abstained_total / n) if n else 0.0,
        "over_refusal_rate": (
            sum(1 for c in answerable if c.abstained) / len(answerable) if answerable else 0.0
        ),
        "hallucination_rate": (
            sum(1 for c in unanswerable_cases if c.answered) / len(unanswerable_cases)
            if unanswerable_cases
            else 0.0
        ),
        "avg_groundedness": (
            sum(groundedness_scores) / len(groundedness_scores) if groundedness_scores else 0.0
        ),
        "avg_relevance": (sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0),
        "citation_failure_rate": (
            failed_citations / total_citations if total_citations else 0.0
        ),
        "degraded_rate": (len(degraded) / n) if n else 0.0,
    }
    return EvalReport(
        metrics=metrics,
        cases=case_results,
        degraded_cases=len(degraded),
        degraded_kinds=dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        usage=summarize_usage(case_results),
    )


# ---------------------------------------------------------------------------
# 输出与 CLI
# ---------------------------------------------------------------------------

def _fmt_rate(value: float) -> str:
    return f"{value:.3f}"


def print_report(report: EvalReport) -> None:
    """打印中文可读的评测摘要与逐用例表格。"""
    m = report.metrics
    print("=" * 68)
    print("RAG4C 评测报告")
    print("=" * 68)
    print(f"用例总数            : {len(report.cases)}")
    print(f"拒答率              : {_fmt_rate(m.get('refusal_rate', 0.0))}  (refusal_rate)")
    print(f"过度拒答率          : {_fmt_rate(m.get('over_refusal_rate', 0.0))}  (over_refusal_rate，答案可答却被拒)")
    print(f"幻觉率              : {_fmt_rate(m.get('hallucination_rate', 0.0))}  (hallucination_rate，知识库无答案却作答)")
    print(f"平均有据性          : {_fmt_rate(m.get('avg_groundedness', 0.0))}  (avg_groundedness)")
    print(f"平均相关性          : {_fmt_rate(m.get('avg_relevance', 0.0))}  (avg_relevance)")
    print(f"引用失败率          : {_fmt_rate(m.get('citation_failure_rate', 0.0))}  (citation_failure_rate)")
    print(f"降级率              : {_fmt_rate(m.get('degraded_rate', 0.0))}  (degraded_rate，上游故障非质量)")
    if report.degraded_kinds:
        print(f"  故障分类          : {report.degraded_kinds}")
    usage = report.usage or {}
    if usage:
        print(
            f"用量台账            : {usage.get('calls', 0)} 次调用 / "
            f"{usage.get('total_tokens', 0)} tokens / 成本 {usage.get('cost', 0)}"
            + ("" if usage.get("cost_priced") else "（未配价格表，金额不可信）")
        )
    print("-" * 68)
    print("逐用例明细：")
    print(f"{'ID':<8}{'可答':<5}{'拒答':<5}{'作答':<5}{'有据性':<8}{'相关性':<8}{'引用OK':<7}问题")
    for c in report.cases:
        g = "-" if c.groundedness is None else f"{c.groundedness:.2f}"
        r = "-" if c.relevance is None else f"{c.relevance:.2f}"
        print(
            f"{c.id:<8}{'是' if not c.unanswerable else '否':<5}"
            f"{'是' if c.abstained else '否':<5}{'是' if c.answered else '否':<5}"
            f"{g:<8}{r:<8}{'是' if c.citations_ok else '否':<7}{c.question}"
        )
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="RAG4C 评测执行器")
    parser.add_argument(
        "--dataset",
        default="eval/dataset_sample.py:SAMPLE_DATASET",
        help="数据集规格（模块:属性），默认内置样例集",
    )
    parser.add_argument("--out", default="eval/results.json", help="结果 JSON 输出路径")
    parser.add_argument(
        "--pipeline",
        default="none",
        help="真实管线导入路径（如 rag:answer_query），none 表示干跑",
    )
    parser.add_argument("--dry-run", action="store_true", help="强制离线干跑（占位管线 + 桩裁判）")
    parser.add_argument(
        "--sleep-ms",
        type=float,
        default=0.0,
        help="两条用例之间的间隔毫秒数（上游 429 限流时用它节流）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发线程数，1（默认）= 串行。延迟几乎全在等上游 HTTP，"
        "并发可显著压低墙钟时间；共享组件存在交叉风险，故默认不开",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="评测后执行 release gate（固定阈值质量墙），未达标退出码 1",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="baseline 报告路径（JSON），提供时输出逐指标 delta（本次 - baseline）",
    )
    parser.add_argument(
        "--gate-thresholds",
        default="",
        help="release gate 阈值 JSON 文件路径（如 {\"hallucination_rate\": [\"le\", 0.1]}），"
        "未提供时用内置默认阈值",
    )
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    dry_run = args.dry_run or args.pipeline == "none"
    if dry_run:
        print("[eval] 离线干跑模式：使用占位管线与桩裁判，不发起任何真实调用。")
        report = run_dry_run(dataset, sleep_ms=args.sleep_ms, workers=args.workers)
    else:
        print(f"[eval] 真实评测模式：惰性导入管线 {args.pipeline}")
        print(
            f"[eval] 并发 {args.workers} 路，节流 {args.sleep_ms:g}ms；"
            f"上游限流或降级会体现在 degraded_rate 上，不会混进质量指标"
        )
        try:
            pipeline_fn = load_pipeline(args.pipeline)
            groundedness, relevance = create_judges()
            report = Evaluator(pipeline_fn, groundedness, relevance).evaluate(
                dataset, sleep_ms=args.sleep_ms, workers=args.workers
            )
        except Exception as exc:
            print(f"[eval] 真实评测启动失败: {exc}")
            return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[eval] 报告已写入 {out}")
    print_report(report)

    # ---- R5-A：baseline 对比 + release gate（质量墙） ----
    exit_code = 0
    if args.baseline or args.gate:
        current = as_v2(report, dataset_spec=args.dataset, pipeline_spec=args.pipeline)
        if args.baseline:
            base_path = Path(args.baseline)
            if not base_path.is_file():
                print(f"[eval] baseline 不存在: {args.baseline}")
                return 1
            diff = compare_reports(current, load_report(base_path))
            print("\n[eval] 与 baseline 对比（delta = 本次 - baseline）:")
            for key, delta in diff["delta"].items():
                direction = diff["direction"].get(key, "unknown")
                print(f"  {key}: {delta:+.4f} ({direction})")
        if args.gate:
            thresholds = None
            if args.gate_thresholds:
                th_path = Path(args.gate_thresholds)
                if not th_path.is_file():
                    print(f"[eval] gate 阈值文件不存在: {args.gate_thresholds}")
                    return 1
                thresholds = json.loads(th_path.read_text(encoding="utf-8-sig"))
            gate_result = release_gate(current, thresholds)
            print("\n[eval] release gate:")
            for check in gate_result["checks"]:
                mark = "PASS" if check["passed"] else "FAIL"
                op = "<=" if check["action"] == "le" else ">="
                print(
                    f"  {mark} {check['metric']}: {check['value']} {op} {check['threshold']}"
                )
            if not gate_result["passed"]:
                print(f"[eval] RELEASE GATE FAILED: {gate_result['failures']}")
                exit_code = 1
            else:
                print("[eval] RELEASE GATE PASSED")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# EvalReport v2：版本化报告 / baseline 快照 / 对比 / release gate
# ---------------------------------------------------------------------------
#
# 增量功能（不改变上文的 Evaluator / extract_evidence / dry-run 行为）：
# - :class:`EvalReportV2`：在 EvalReport 基础上追加版本号与元信息
#   （生成时间 / 数据集规格 / 管线规格），使报告可被序列化存档与对比。
# - :func:`save_report` / :func:`load_report`：把报告写成 JSON 快照，
#   作为后续对比的 baseline。
# - :func:`compare_reports`：逐指标 diff（本次 - baseline），并给策略性指标
#   （refusal/hallucination/citation_failure，越低越好）与质量指标
#   （groundedness/relevance，越高越好）正确标注方向。
# - :func:`release_gate`：以固定阈值阻断劣化（质量墙），返回逐项通过/失败清单。

DEFAULT_RELEASE_THRESHOLDS: dict[str, tuple[str, float]] = {
    # metric -> (gate 方向, 阈值)。"le" 表示该指标不得超过阈值，"ge" 表示不得低于。
    "hallucination_rate": ("le", 0.10),
    "citation_failure_rate": ("le", 0.25),
    "over_refusal_rate": ("le", 0.30),
    "avg_groundedness": ("ge", 0.60),
    "avg_relevance": ("ge", 0.60),
    # 降级率与上面五项**不同类**：它量的不是答案好不好，而是这一轮的数字**能不能信**。
    # 上游挂了一半时，有据性会掉、幻觉率会涨——那时候门禁应该拦下"别拿这轮数字
    # 签字"，而不是把锅算到模型头上。阈值取 5%：145 条里超过 7 条踩到上游故障，
    # 剩余样本量已不足以支撑分位数级的结论。
    "degraded_rate": ("le", 0.05),
}


class EvalReportV2(EvalReport):
    """版本化评测报告：EvalReport + schema 元信息（供存档与对比）。"""

    schema_version: int = 2
    generated_at: str = ""
    dataset_spec: str = ""
    pipeline_spec: str = ""


def as_v2(report: EvalReport, *, dataset_spec: str = "", pipeline_spec: str = "") -> EvalReportV2:
    """把一次 :class:`EvalReport` 提升为带元信息的 :class:`EvalReportV2`。"""
    return EvalReportV2(
        metrics=report.metrics,
        cases=report.cases,
        degraded_cases=report.degraded_cases,
        degraded_kinds=report.degraded_kinds,
        usage=report.usage,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        dataset_spec=dataset_spec,
        pipeline_spec=pipeline_spec,
    )


def save_report(report: EvalReportV2, path: str | Path) -> Path:
    """把版本化报告写成 JSON 快照（供作为 baseline）。返回写入路径。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def load_report(path: str | Path) -> EvalReportV2:
    """读回版本化报告快照（baseline / 历史对比用）。

    ``utf-8-sig`` 读取：兼容 Windows 编辑器生成的带 BOM 的 JSON 文件。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return EvalReportV2.model_validate(raw)


def save_report_history(
    report: EvalReportV2,
    history_dir: str | Path = "eval/reports",
) -> Path:
    """把报告存入历史目录（带时间戳文件名），供趋势追踪与 baseline 复用。

    文件名 ``report-<generated_at>-<毫秒>.json``（generated_at 去冒号/空格，
    Windows 安全；毫秒后缀保证同秒多份不覆盖）。
    返回写入路径。
    """
    directory = Path(history_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "-").replace(" ", "T")
    ms = datetime.now().strftime("%f")[:3]
    out = directory / f"report-{stamp}-{ms}.json"
    return save_report(report, out)


def list_report_history(
    history_dir: str | Path = "eval/reports",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """列出历史报告（按修改时间倒序），含关键指标摘要。

    Returns:
        ``[{"path", "generated_at", "dataset_spec", "metrics": {...}}, ...]``
    """
    directory = Path(history_dir)
    if not directory.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for p in sorted(directory.glob("report-*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            report = load_report(p)
            entries.append(
                {
                    "path": str(p),
                    "generated_at": report.generated_at,
                    "dataset_spec": report.dataset_spec,
                    "metrics": report.metrics,
                }
            )
        except Exception:  # noqa: BLE001 - 单个损坏历史不阻塞列表
            continue
        if len(entries) >= limit:
            break
    return entries


def compare_reports(current: EvalReportV2, baseline: EvalReportV2) -> dict[str, Any]:
    """逐指标 diff（current - baseline），含方向语义。

    Returns:
        ``{"current": {...}, "baseline": {...}, "delta": {...}, "direction": {...}}``
        其中 direction 记录"该指标变优/变劣"（lower_is_better / higher_is_better）。
    """
    lower_is_better = {
        "refusal_rate",
        "over_refusal_rate",
        "hallucination_rate",
        "citation_failure_rate",
        "degraded_rate",
    }
    higher_is_better = {"avg_groundedness", "avg_relevance"}
    delta: dict[str, float] = {}
    direction: dict[str, str] = {}
    keys = sorted(set(current.metrics) | set(baseline.metrics))
    for key in keys:
        cur = current.metrics.get(key, 0.0)
        base = baseline.metrics.get(key, 0.0)
        diff = round(cur - base, 6)
        delta[key] = diff
        if key in lower_is_better:
            direction[key] = "improved" if diff < 0 else ("worse" if diff > 0 else "unchanged")
        elif key in higher_is_better:
            direction[key] = "improved" if diff > 0 else ("worse" if diff < 0 else "unchanged")
        else:
            direction[key] = "unknown"
    return {
        "current": current.metrics,
        "baseline": baseline.metrics,
        "delta": delta,
        "direction": direction,
    }


def release_gate(
    report: EvalReportV2,
    thresholds: dict[str, tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """以固定阈值做质量墙判定。

    Returns:
        ``{"passed": bool, "checks": [...], "failures": [...]}``
        每条 check 为 ``{"metric", "threshold", "action", "value", "passed"}``。
    """
    gates = thresholds if thresholds is not None else DEFAULT_RELEASE_THRESHOLDS
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for metric, (action, threshold) in gates.items():
        value = report.metrics.get(metric, 0.0)
        passed = value <= threshold if action == "le" else value >= threshold
        checks.append(
            {
                "metric": metric,
                "action": action,
                "threshold": threshold,
                "value": round(value, 6),
                "passed": passed,
            }
        )
        if not passed:
            failures.append(metric)
    return {"passed": not failures, "checks": checks, "failures": failures}
