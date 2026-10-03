"""F2.2 前置：路由器实际把多少查询分到哪条路径。

计划要求："先查清路由器实际把多少比例的查询分到 hybrid / dense 等路径；
再拿 F1.2 的答案层指标做 A/B——路由默认 hybrid vs 固定 dense+重排 vs 按语言
分流。"

本脚本只做前半：**先查清分布**。这一半不依赖 F1.2 基线，而且它本身就是
A/B 的前提——如果 99% 的查询都走同一条路，那么"换默认策略"这个决策的影响
面就小到不值得做 A/B；如果分布是分散的，才值得投入。

只跑路由决策，不跑检索/生成：路由只需要 embedding + 一组 exemplar 向量的
余弦比较，一次调用即可，成本与延迟都远低于整条链路。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def collect(limit: int = 0) -> dict[str, Any]:
    """对每条问题跑一次路由决策，统计 target 分布。"""
    from config.settings import get_settings
    from eval.answer_eval.gold_answer_production import ANSWER_GOLD

    settings = get_settings()
    from rag import get_pipeline

    comp = get_pipeline(settings)
    pipeline = comp.get("retrieval")
    router = getattr(pipeline, "router", None)
    if router is None:
        raise SystemExit("[f22] 管线里没有 router，无法采集路由分布")

    cases = ANSWER_GOLD[:limit] if limit else ANSWER_GOLD
    rows: list[dict[str, Any]] = []
    t_start = time.time()
    for index, case in enumerate(cases):
        t0 = time.time()
        row: dict[str, Any] = {
            "id": case["id"],
            "question": case["question"],
            "unanswerable": bool(case.get("unanswerable", False)),
            "corpus_language": case.get("corpus_language", "undeclared"),
        }
        try:
            # 走与线路上同一条决策路径：pipeline.run 内部才做路由，这里直接
            # 调 router 避免把检索也算进来（检索是秒级、路由是毫秒级）。
            decision = router.route(case["question"])
            row["target"] = decision.target
            row["confidence"] = round(float(decision.confidence), 6)
            row["degraded"] = bool(decision.degraded)
        except Exception as exc:  # noqa: BLE001 - 采集器要看到原始失败
            row["error"] = f"{type(exc).__name__}: {exc}"[:180]
        row["ms"] = round((time.time() - t0) * 1000, 2)
        rows.append(row)
        if (index + 1) % 20 == 0:
            done = index + 1
            print(
                f"[f22] {done}/{len(cases)}  {(time.time() - t_start) / done * 1000:.0f}ms/条",
                flush=True,
            )
    return {"total": len(rows), "rows": rows}


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """汇总分布，并按语言分组（语言分流策略的前提是先知道语言与路由的关系）。"""
    rows = [r for r in payload["rows"] if not r.get("error")]
    targets = Counter(r.get("target", "unknown") for r in rows)
    degraded = sum(1 for r in rows if r.get("degraded"))
    by_lang: dict[str, Counter] = {}
    for r in rows:
        by_lang.setdefault(r.get("corpus_language", "undeclared"), Counter())[r.get("target", "unknown")] += 1
    conf = [r["confidence"] for r in rows if r.get("confidence") is not None]
    conf.sort()
    return {
        "total": payload["total"],
        "errors": payload["total"] - len(rows),
        "targets": dict(targets),
        "shares": {k: round(v / len(rows), 4) for k, v in targets.items()} if rows else {},
        "degraded": degraded,
        "degraded_rate": round(degraded / len(rows), 4) if rows else 0.0,
        "confidence": {
            "n": len(conf),
            "min": round(conf[0], 4) if conf else None,
            "p50": round(conf[len(conf) // 2], 4) if conf else None,
            "max": round(conf[-1], 4) if conf else None,
        },
        "by_language": {lang: dict(c) for lang, c in sorted(by_lang.items())},
        "route_ms_p50": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F2.2 路由分布采集")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    parser.add_argument("--out", default="eval/.cache/f22-routing.json", help="输出路径")
    args = parser.parse_args(argv)

    payload = collect(args.limit)
    result = analyze(payload)
    Path(args.out).write_text(
        json.dumps({"summary": result, **payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print(f"路由分布（{result['total']} 条，失败 {result['errors']}）")
    print("=" * 60)
    for target, count in sorted(result["targets"].items(), key=lambda kv: -kv[1]):
        share = result["shares"].get(target, 0.0)
        bar = "█" * int(share * 40)
        print(f"  {target:<12} {count:4d}  {share:6.1%}  {bar}")
    print(f"\n  降级（低置信度）{result['degraded']} 条（{result['degraded_rate']:.1%}）")
    print(f"  置信度 min/p50/max: {result['confidence']['min']} / "
          f"{result['confidence']['p50']} / {result['confidence']['max']}")
    print(f"  路由耗时 p50: {result['route_ms_p50']}ms")
    print("\n  按语言：")
    for lang, counts in result["by_language"].items():
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"    {lang:<12} n={total:<4} {detail}")
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
