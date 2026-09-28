"""检索层评测 CLI。

用法（与 ``eval/run_eval.py`` 的风格保持一致）::

    # 真实嵌入 + 真实重排（需要能连到 .env 里的嵌入/重排端点）
    python -m eval.run_retrieval_eval --embedder api

    # 离线自测（零网络、零成本，指标不能与真实嵌入横向比）
    python -m eval.run_retrieval_eval --embedder hashing --strategies dense

    # 与上一版基线对比
    python -m eval.run_retrieval_eval --embedder api --baseline eval/.cache/retrieval-baseline.json

退出码：0 = 跑完（无门禁时）；1 = 指定了 --fail-under 且未达标；2 = 运行出错。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.retrieval_eval.runner import (
    DEFAULT_CANDIDATES,
    DEFAULT_TOP_K,
    EvalConfig,
    available_strategies,
    compare_reports,
    format_report,
    load_report,
    run_eval,
    save_report,
)


def _build_reranker(enabled: bool):
    """构造真实重排器；失败返回 None（策略会标记 degraded 而不是崩掉）。"""
    if not enabled:
        return None
    try:
        from config.settings import get_settings
        from core.reranker import create_reranker

        return create_reranker(get_settings().reranker)
    except Exception as exc:  # noqa: BLE001 - 评测工具不该因为装配失败就中断
        print(f"[warn] 重排器不可用，dense_rerank 将按未重排处理: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG4C 检索层离线评测")
    parser.add_argument("--root", default=".", help="仓库根目录（语料来源）")
    parser.add_argument(
        "--embedder",
        default="hashing",
        choices=["hashing", "api"],
        help="hashing=离线伪嵌入（默认，零成本）；api=真实 bge-m3",
    )
    parser.add_argument(
        "--strategies",
        default="dense,dense_rerank",
        help=f"逗号分隔；可选 {', '.join(available_strategies())}",
    )
    parser.add_argument(
        "--store",
        default="local",
        choices=["local", "milvus"],
        help="local=进程内 numpy（默认，零依赖）；milvus=真实 Milvus 集合（连接参数读环境变量）",
    )
    parser.add_argument("--milvus-collection", default="", help="覆盖 Milvus 集合名")
    parser.add_argument(
        "--corpus",
        default="docs",
        choices=["docs", "milvus"],
        help="docs=仓库文档切片（默认）；milvus=生产集合（只读，不写入）",
    )
    parser.add_argument(
        "--gold", default="docs", choices=["docs", "production"], help="使用哪套黄金标注"
    )
    parser.add_argument("--sleep-ms", type=int, default=0, help="每条用例之间的间隔，避免上游限流")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    parser.add_argument("--no-rerank", action="store_true", help="不构造真实重排器")
    parser.add_argument("--out", default="eval/.cache/retrieval-report.json")
    parser.add_argument("--baseline", default="", help="基线报告路径，用于输出逐指标 delta")
    parser.add_argument("--save-baseline", default="", help="把本次结果另存为基线")
    parser.add_argument(
        "--fail-under",
        default="",
        help='质量墙，如 "ndcg@10=0.60"，低于阈值 exit 1（只对第一个策略生效）',
    )
    args = parser.parse_args(argv)

    strategies = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    unknown = [name for name in strategies if name not in available_strategies()]
    if unknown:
        print(f"[error] 未知策略 {unknown}；可用 {available_strategies()}", file=sys.stderr)
        return 2

    cfg = EvalConfig(
        top_k=args.top_k,
        candidates=args.candidates,
        embedder=args.embedder,
        strategies=strategies,
        store=args.store,
        milvus_collection=args.milvus_collection,
        corpus_source=args.corpus,
        gold=args.gold,
        sleep_ms=args.sleep_ms,
    )
    try:
        report = run_eval(Path(args.root), cfg, reranker=_build_reranker(not args.no_rerank))
    except Exception as exc:  # noqa: BLE001 - CLI 边界：给出可读失败原因
        print(f"[error] 评测失败: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    saved = save_report(report, Path(args.out))
    print(f"\n报告: {saved}")
    if args.save_baseline:
        print(f"基线: {save_report(report, Path(args.save_baseline))}")

    exit_code = 0
    if args.baseline:
        try:
            baseline = load_report(Path(args.baseline))
        except Exception as exc:  # noqa: BLE001
            print(f"[error] 基线读取失败: {exc}", file=sys.stderr)
            return 2
        deltas = compare_reports(report, baseline)
        print("\n对比基线:")
        for name, payload in deltas.items():
            print(f"  [{name}]")
            for metric, values in payload.items():
                sign = "+" if values["delta"] >= 0 else ""
                print(
                    f"    {metric}: {values['current']:.4f} vs {values['baseline']:.4f} "
                    f"({sign}{values['delta']:.4f})"
                )

    if args.fail_under:
        metric, _, threshold = args.fail_under.partition("=")
        first = next(iter(report["strategies"].values()), {})
        value = float(first.get("metrics", {}).get(metric.strip(), 0.0))
        if value < float(threshold):
            print(f"[gate] {metric}={value:.4f} < {threshold} → FAIL", file=sys.stderr)
            exit_code = 1
        else:
            print(f"[gate] {metric}={value:.4f} >= {threshold} → PASS")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
