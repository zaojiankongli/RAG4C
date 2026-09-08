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
    notes: str = ""


class EvalReport(BaseModel):
    """一次评测的报告：聚合指标 + 逐用例结果。"""

    model_config = ConfigDict(extra="ignore")

    metrics: dict[str, float] = Field(default_factory=dict)
    cases: list[CaseResult] = Field(default_factory=list)


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


def run_dry_run(dataset: list[dict]) -> EvalReport:
    """离线干跑：使用 FakeJudgeLLM 与占位管线计算指标，不联网、不加载模型。"""
    groundedness = GroundednessJudge(llm_client=FakeJudgeLLM())
    relevance = RelevanceJudge(llm_client=FakeJudgeLLM())
    return Evaluator(_dry_run_pipeline(dataset), groundedness, relevance).evaluate(dataset)


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

    def evaluate(self, dataset: list[dict]) -> EvalReport:
        """对数据集逐条评测并汇总指标。

        指标定义：

        - ``refusal_rate``: 拒答用例 / 总用例
        - ``over_refusal_rate``: 答案可答却被拒 / 可答总数
        - ``hallucination_rate``: 知识库无答案却作答 / 不可答总数
        - ``avg_groundedness``: 有分用例的有据性均值（无分时 0.0）
        - ``avg_relevance``: 有分用例的相关性均值（无分时 0.0）
        - ``citation_failure_rate``: 状态非 ok 的引用 / 引用总数（无引用时 0.0）

        拒答的用例跳过裁判（分数保持 None）；裁判解析失败同样显式为 None。
        """
        case_results: list[CaseResult] = []
        total_citations = 0
        failed_citations = 0
        abstained_total = 0
        answerable_total = 0
        unanswerable_total = 0
        abstained_answerable = 0
        answered_unanswerable = 0
        groundedness_scores: list[float] = []
        relevance_scores: list[float] = []

        for case in dataset:
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
                if g_score is not None:
                    groundedness_scores.append(g_score)
                if r_score is not None:
                    relevance_scores.append(r_score)

            citations = qr.citations or []
            case_failed_citations = sum(1 for c in citations if c.status != "ok")
            total_citations += len(citations)
            failed_citations += case_failed_citations
            citations_ok = case_failed_citations == 0

            if unanswerable:
                unanswerable_total += 1
                if answered:
                    answered_unanswerable += 1
            else:
                answerable_total += 1
                if abstained:
                    abstained_answerable += 1
            if abstained:
                abstained_total += 1

            notes = str(case.get("notes") or "")
            if answered:
                judge_failures = []
                if g_score is None:
                    judge_failures.append("有据性判定失败")
                if r_score is None:
                    judge_failures.append("相关性判定失败")
                if judge_failures:
                    notes = f"{notes}；{'；'.join(judge_failures)}" if notes else "；".join(judge_failures)

            case_results.append(
                CaseResult(
                    id=str(case.get("id") or f"case_{len(case_results)}"),
                    question=question,
                    unanswerable=unanswerable,
                    abstained=abstained,
                    answered=answered,
                    groundedness=g_score,
                    relevance=r_score,
                    citations_ok=citations_ok,
                    notes=notes,
                )
            )

        n = len(case_results)
        metrics: dict[str, float] = {
            "refusal_rate": (abstained_total / n) if n else 0.0,
            "over_refusal_rate": (abstained_answerable / answerable_total) if answerable_total else 0.0,
            "hallucination_rate": (answered_unanswerable / unanswerable_total) if unanswerable_total else 0.0,
            "avg_groundedness": (sum(groundedness_scores) / len(groundedness_scores)) if groundedness_scores else 0.0,
            "avg_relevance": (sum(relevance_scores) / len(relevance_scores)) if relevance_scores else 0.0,
            "citation_failure_rate": (failed_citations / total_citations) if total_citations else 0.0,
        }
        return EvalReport(metrics=metrics, cases=case_results)


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
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    dry_run = args.dry_run or args.pipeline == "none"
    if dry_run:
        print("[eval] 离线干跑模式：使用占位管线与桩裁判，不发起任何真实调用。")
        report = run_dry_run(dataset)
    else:
        print(f"[eval] 真实评测模式：惰性导入管线 {args.pipeline}")
        try:
            pipeline_fn = load_pipeline(args.pipeline)
            groundedness, relevance = create_judges()
            report = Evaluator(pipeline_fn, groundedness, relevance).evaluate(dataset)
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
    return 0


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
    """读回版本化报告快照（baseline / 历史对比用）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvalReportV2.model_validate(raw)


def compare_reports(current: EvalReportV2, baseline: EvalReportV2) -> dict[str, Any]:
    """逐指标 diff（current - baseline），含方向语义。

    Returns:
        ``{"current": {...}, "baseline": {...}, "delta": {...}, "direction": {...}}``
        其中 direction 记录"该指标变优/变劣"（lower_is_better / higher_is_better）。
    """
    lower_is_better = {"refusal_rate", "over_refusal_rate", "hallucination_rate", "citation_failure_rate"}
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
