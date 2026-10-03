"""F1.3 分析：把分数分布变成「过度拒答 / 幻觉」的取舍曲线。

计划要求："看两个分布的重叠区；在『过度拒答』与『幻觉』之间给出取舍曲线，
而不是单点。"

读入 ``eval/.cache/f13-scores.json``（由 ``collect_abstention_scores.py`` 产出），
输出：

1. 可答 / 不可答两族在两把尺子上的分布（min / p10 / p25 / 中位 / p75 / p90 / max）；
2. **重叠区**——这是关键：两族完全分开时阈值可以随便取，分不开时任何阈值都
   是在拿一类错误换另一类，那才是需要决策的地方；
3. 沿阈值扫描的取舍曲线：每个候选阈值对应的过度拒答率与幻觉率；
4. 用 ``calibrate_retrieval_threshold`` 取到的分位数阈值，并给出它在曲线上的
   位置与代价。

术语与口径（与 ``eval/run_eval.py`` 保持一致，���可混用）：

- **过度拒答**（over-refusal）：可答的问题被弃权 → 阈值放松时的代价；
- **幻觉**（hallucination）：不可答的问题被放行去作答 → 阈值收紧时的代价。

两把尺子的量级完全不同（rerank 分 0.4~0.99，RRF 分塌到 0.02~0.03，稠密余弦
0.55~0.75），**不可互换、不可共用阈值**，所以本脚本分开出曲线。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from verify.abstention import calibrate_retrieval_threshold

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def q(p: float) -> float:
        """最近秩分位数（与 calibrate_retrieval_threshold 的无 numpy 分支同口径）。"""
        import math

        rank = max(1, math.ceil(p * len(ordered)))
        return round(ordered[min(rank, len(ordered)) - 1], 6)

    return {
        "n": len(ordered),
        "min": round(ordered[0], 6),
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "max": round(ordered[-1], 6),
    }


def _curve(
    answerable: list[float],
    unanswerable: list[float],
    key: str,
    extra_thresholds: list[float] | None = None,
) -> list[dict[str, Any]]:
    """沿候选阈值扫描，画过度拒答率与幻觉率的取舍曲线。

    语义：分数 **低于** 阈值 = 判为"知识库无相关内容" → 弃权。

    - 可答问题被弃权 = 过度拒答（分母 = 可答总数）；
    - 不可答问题被放行 = 幻觉（分母 = 不可答总数）。

    Args:
        extra_thresholds: 额外要出现在曲线上的阈值。分位数校准出来的值通常
            落在两个观测值**之间**（numpy 线性插值），而只按观测值取候选的话
            它就不在曲线上——报告里就会出现"这个阈值代价多少"查不到的情况。
            既然计划要求校准阈值能在取舍曲线上定位，就把它们塞进候选集。
    """
    if not answerable or not unanswerable:
        return []
    candidates = {round(v, 4) for v in answerable + unanswerable}
    candidates.update(round(t, 6) for t in (extra_thresholds or []))
    points: list[dict[str, Any]] = []
    for threshold in sorted(candidates):
        over = sum(1 for v in answerable if v < threshold) / len(answerable)
        hall = sum(1 for v in unanswerable if v >= threshold) / len(unanswerable)
        points.append(
            {
                "threshold": threshold,
                "over_refusal_rate": round(over, 6),
                "hallucination_rate": round(hall, 6),
                # 两者等权时的合计代价：没有业务权重时唯一中性的读法
                "total_error": round(over + hall, 6),
            }
        )
    return points


def _overlap(a: list[float], b: list[float]) -> dict[str, Any]:
    """两族分布的重叠区。

    判据用「不可答的 p90 是否高于可答的 p10」这种可解释的区间口径，而不是
    协方差之类的统计量——这份数据只有 20 来条不可答样本，任何拟合出来的
    分界点都会带着比它本身大得多的误差。
    """
    if not a or not b:
        return {"separable": None, "reason": "样本不足"}
    a_sorted, b_sorted = sorted(a), sorted(b)
    a_p10, a_p90 = a_sorted[max(0, int(0.10 * len(a)) - 1)], a_sorted[min(len(a) - 1, int(0.90 * len(a)))]
    b_p10, b_p90 = b_sorted[max(0, int(0.10 * len(b)) - 1)], b_sorted[min(len(b) - 1, int(0.90 * len(b)))]
    lo, hi = max(a_p10, b_p10), min(a_p90, b_p90)
    overlap = lo < hi
    return {
        "answerable_p10": round(a_p10, 6),
        "answerable_p90": round(a_p90, 6),
        "unanswerable_p10": round(b_p10, 6),
        "unanswerable_p90": round(b_p90, 6),
        "central_overlap": [round(lo, 6), round(hi, 6)] if overlap else None,
        "separable": not overlap,
        "note": (
            "两族在 p10~p90 区间内不重叠：阈值可以取在中间任意位置，"
            "过度拒答与幻觉都能压到很低"
            if not overlap
            else "两族在 p10~p90 区间内重叠：这个区间里的任何阈值都是在拿一类错误换另一类"
        ),
    }


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in payload.get("rows", []) if not r.get("error")]
    answerable = [r for r in rows if not r["unanswerable"]]
    unanswerable = [r for r in rows if r["unanswerable"]]
    on_domain = [r for r in unanswerable if r.get("kind") == "on_domain"]
    off_domain = [r for r in unanswerable if r.get("kind") == "off_domain"]

    result: dict[str, Any] = {
        "source": {
            "generated_at": payload.get("generated_at"),
            "mode": payload.get("mode"),
            "collection": payload.get("collection"),
            "total": payload.get("total"),
            "errors": payload.get("errors"),
        },
        "current_thresholds": {
            "retrieval_score_threshold": payload.get("retrieval_score_threshold"),
            "dense_cosine_threshold": payload.get("dense_cosine_threshold"),
            "entailment_score_threshold": payload.get("entailment_score_threshold"),
        },
        "counts": {
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "unanswerable_on_domain": len(on_domain),
            "unanswerable_off_domain": len(off_domain),
        },
    }

    key = "top1_score" if payload.get("mode") == "score" else "top1_cosine"
    a_scores = [r[key] for r in answerable if r.get(key) is not None]
    u_scores = [r[key] for r in unanswerable if r.get(key) is not None]
    u_on = [r[key] for r in on_domain if r.get(key) is not None]
    u_off = [r[key] for r in off_domain if r.get(key) is not None]

    result["distributions"] = {
        "metric": key,
        "answerable": _percentiles(a_scores),
        "unanswerable_all": _percentiles(u_scores),
        "unanswerable_on_domain": _percentiles(u_on),
        "unanswerable_off_domain": _percentiles(u_off),
    }
    result["overlap"] = {
        "all": _overlap(a_scores, u_scores),
        # 同域硬负样本是真正的难点：余弦阈值就是被它们顶穿的
        "on_domain_only": _overlap(a_scores, u_on),
    }

    # 先算校准阈值，再把它们塞进曲线候选——分位数通常落在两个观测值之间，
    # 不塞进去的话报告里"这个阈值代价多少"就查不到（见 _curve 的说明）。
    calib: dict[str, Any] = {}
    if u_scores:
        for p in (0.50, 0.75, 0.85, 0.90, 0.95):
            threshold = calibrate_retrieval_threshold(u_scores, p)
            calib[f"p{int(p * 100)}"] = {
                "threshold": round(threshold, 6),
                "over_refusal_rate": round(
                    sum(1 for v in a_scores if v < threshold) / len(a_scores), 6
                )
                if a_scores
                else 0.0,
                "hallucination_rate": round(
                    sum(1 for v in u_scores if v >= threshold) / len(u_scores), 6
                ),
            }
    result["calibrated"] = calib
    result["tradeoff_curve"] = _curve(
        a_scores, u_scores, key, extra_thresholds=[c["threshold"] for c in calib.values()]
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.3 阈值分布分析")
    parser.add_argument("--scores", default="eval/.cache/f13-scores.json", help="采集结果 JSON")
    parser.add_argument("--out", default="eval/.cache/f13-analysis.json", help="分析结果 JSON")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    result = analyze(payload)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    d = result["distributions"]
    print(f"口径：{d['metric']}（mode={result['source']['mode']}，集合 {result['source']['collection']}）")
    print(f"样本：可答 {result['counts']['answerable']} / 不可答 {result['counts']['unanswerable']}"
          f"（同域 {result['counts']['unanswerable_on_domain']}，离域 {result['counts']['unanswerable_off_domain']}）")
    for name in ("answerable", "unanswerable_all", "unanswerable_on_domain", "unanswerable_off_domain"):
        s = d.get(name) or {}
        if s:
            print(f"  {name:26s} n={s['n']:3d}  min={s['min']:.3f} p10={s['p10']:.3f} "
                  f"p50={s['p50']:.3f} p90={s['p90']:.3f} max={s['max']:.3f}")
    print("\n重叠区：")
    for name, ov in result["overlap"].items():
        print(f"  {name:14s} separable={ov.get('separable')}  {ov.get('note', '')}")
    if result.get("calibrated"):
        print("\n分位数校准（calibrate_retrieval_threshold）：")
        for name, c in result["calibrated"].items():
            print(f"  {name:5s} 阈值={c['threshold']:.4f}  过度拒答={c['over_refusal_rate']:.3f} "
                  f"幻觉={c['hallucination_rate']:.3f}")
    best = min(result["tradeoff_curve"], key=lambda p: p["total_error"]) if result["tradeoff_curve"] else None
    if best:
        print(f"\n等权最优：阈值={best['threshold']:.4f} 过度拒答={best['over_refusal_rate']:.3f} "
              f"幻觉={best['hallucination_rate']:.3f} 合计={best['total_error']:.3f}")
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
