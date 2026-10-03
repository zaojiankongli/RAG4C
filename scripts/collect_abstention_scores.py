"""F1.3：弃权阈值的真实分布采集与取舍曲线。

计划要求："用 F1.1 的不可答样本调用 ``calibrate_retrieval_threshold``，
同时画可答样本的分数分布，看两个分布的重叠区；在『过度拒答』与『幻觉』
之间给出取舍曲线，而不是单点。"

为什么单独写这个采集器，而不是从 F1.2 的基线报告里挖：
基线只记了**结果**（答了/拒了），没记**分数**。而阈值校准要的恰恰是分数
分布——同一个 0.3 阈值，落在两族分布的哪个位置才是决策依据。所以这里
只跑检索段（embedding + 搜索 + 重排），不跑生成与裁判：快一个数量级，
也不会被生成质量污染。

产出的分数是**弃权门真正看到的那把尺子**，不是别的：
- ``top1_score``：rerank 分（与 ``retrieval_score_threshold=0.3`` 同尺）；
- ``top1_cosine``：稠密余弦（与 ``dense_cosine_threshold=0.52`` 同尺）。

跑法::

    python scripts/collect_abstention_scores.py
    python scripts/collect_abstention_scores.py --out eval/.cache/f13-scores.json

关于两把尺子：正常路径（rerank 生效）下 ``dense_cosine`` 拿不到值——
``retrieval/stages.py`` 里 ``want_cosine`` 只在"rerank 事前已知会失效"时
才为 True，因为取回向量要额外传 dim×4 字节/条。所以本采集器跑**两轮**：

- ``--mode score``：rerank 开着，采 rerank 分（``retrieval_score_threshold``
  那把尺子），顺带确认 top1 命中的是不是 gold chunk；
- ``--mode cosine``：把 ``pipeline.rerank_on`` 关掉再跑一遍，逼出稠密余弦
  （``dense_cosine_threshold`` 那把尺子）。

两轮的分数量级不同、不可互换，所以必须分开采、分开报——这正是弃权门注释里
反复强调的那件事。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from eval.answer_eval.gold_answer_production import ANSWER_GOLD  # noqa: E402


def collect(limit: int = 0, mode: str = "score") -> dict[str, Any]:
    """对每条用例跑一次检索，记录两把尺子上的分数。

    Args:
        limit: 只跑前 N 条（0 = 全量）。
        mode: ``score`` = rerank 开着（采 rerank 分）；``cosine`` = 关掉
            rerank 逼出稠密余弦。两把尺子量级不同、不可互换。
    """
    from rag import get_pipeline
    from retrieval.pipeline import dense_cosines_of

    settings = get_settings()
    # 生产语料：rag4c_chunks（9723 行）。评测集 rag4c_eval_chunks 只有 227 行，
    # 拿它校准出来的阈值不能用在这套语料上——两把尺子的量级都不一样。
    collection = settings.milvus.collection_name
    # want_cosine 只在 rerank 失效时才为 True（retrieval/stages.py），
    # 所以 cosine 模式必须在建管线**之前**改开关：组件是懒加载的，
    # 事后改 settings.pipeline 对已构造的管线无效。
    if mode == "cosine":
        settings.pipeline.rerank_on = False
    comp = get_pipeline(settings)
    retrieval = comp["retrieval"]

    cases = ANSWER_GOLD[:limit] if limit else ANSWER_GOLD
    rows: list[dict[str, Any]] = []
    t_start = time.time()
    for index, case in enumerate(cases):
        t0 = time.time()
        row: dict[str, Any] = {
            "id": case["id"],
            "question": case["question"],
            "unanswerable": bool(case.get("unanswerable", False)),
            "kind": case.get("kind", ""),
            "collection": collection,
        }
        try:
            result = retrieval.run(case["question"])
            chunks = list(result.chunks)
            row["hits"] = len(chunks)
            row["reranked"] = bool(result.reranked)
            row["top1_score"] = round(chunks[0].score, 6) if chunks else None
            row["max_score"] = round(max((c.score for c in chunks), default=0.0), 6)
            cosines = dense_cosines_of(chunks)
            row["top1_cosine"] = round(cosines[0], 6) if cosines else None
            row["top1_chunk_id"] = chunks[0].chunk.chunk_id if chunks else None
        except Exception as exc:  # noqa: BLE001 - 采集器要看到原始失败
            row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        row["ms"] = round((time.time() - t0) * 1000, 1)
        rows.append(row)
        if (index + 1) % 10 == 0:
            done = index + 1
            rate = (time.time() - t_start) / done
            print(
                f"[f13] {done}/{len(cases)}  {rate:.1f}s/条  预计剩余 {(len(cases) - done) * rate / 60:.1f} 分钟",
                flush=True,
            )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "collection": collection,
        "milvus_uri": settings.milvus.uri,
        "retrieval_score_threshold": settings.pipeline.retrieval_score_threshold,
        "dense_cosine_threshold": settings.pipeline.dense_cosine_threshold,
        "entailment_score_threshold": settings.pipeline.entailment_score_threshold,
        "total": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.3 弃权阈值分布采集")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0 = 全量）")
    parser.add_argument(
        "--mode",
        choices=("score", "cosine"),
        default="score",
        help="score=采 rerank 分（默认）；cosine=关掉 rerank 采稠密余弦",
    )
    parser.add_argument("--out", default="eval/.cache/f13-scores.json", help="输出路径")
    args = parser.parse_args(argv)

    payload = collect(args.limit, args.mode)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[f13] 已写入 {out}：{payload['total']} 条，失败 {payload['errors']} 条")
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
