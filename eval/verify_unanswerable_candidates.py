"""验证 F1.1 不可答候选：真的在语料里找不到答案才算数。

为什么必须有这一步：9/28 已经踩过一次——原本打算把"公司年假的申请流程"
当不可答用例，实测库里就有写着年假制度的中文演示切片，那条其实**有答案**。
掺进一条假不可答，过度拒答率与幻觉率就都被污染了，而且很难回头发现。

判定：用真实 bge-m3 嵌入 + 真实 Milvus 检索，取 top1 余弦——

    答得上（实测）min 0.576 / 中位 0.700
    答不上（实测）max 0.492
    弃权阈值 0.52 落在中间的空档里

所以：< 0.50 通过；0.50~0.58 灰区弃用；>= 0.58 判定为"其实有答案"，剔除。

用法：

    python -m eval.verify_unanswerable_candidates            # 只打印
    python -m eval.verify_unanswerable_candidates --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# 9/28 实测分界（见 config/settings.py 的 dense_cosine_threshold 注释）
PASS_BELOW = 0.50   # 低于此值：确认找不到答案
GREY_ABOVE = 0.58   # 高于此值：判定其实有答案，必须剔除


def _top_chunks(question: str, store: Any, embedder: Any, top_k: int = 5) -> tuple[float, list[str]]:
    """返回 (top1 余弦, top-k 切片文本列表)。COSINE 下 score 即余弦。"""
    vec = embedder.embed_query(question)
    hits = store.search(vec, top_k=top_k)
    if not hits:
        return 0.0, []
    texts = [str((h.get("entity") or {}).get("text", "") or "") for h in hits]
    return float(hits[0].get("score", 0.0) or 0.0), texts


def _judge_answerable(question: str, chunks: list[str], client: Any) -> tuple[bool, str, str]:
    """让裁判判断"这个具体事实在不在片段里"。返回 (可答?, 摘录, 理由)。"""
    from pathlib import Path

    path = Path("prompts/judge_answerability_v1.txt")
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    tpl = path.read_text(encoding="utf-8")
    # 去掉模板头部的注释行（# 开头），只保留正文
    tpl = "\n".join(line for line in tpl.splitlines() if not line.startswith("#"))
    evidence = "\n\n".join(f"[{i}] {t[:600]}" for i, t in enumerate(chunks)) or "（无证据）"
    SCHEMA_HINT = '{"answerable": bool, "evidence": "str", "rationale": "str"}'
    prompt = tpl.replace("{question}", question).replace("{evidence}", evidence)
    try:
        raw = client.chat_json([{"role": "user", "content": prompt}], schema_hint=SCHEMA_HINT)
    except Exception as exc:  # noqa: BLE001
        return True, "", f"judge 失败({type(exc).__name__})，保守判为可答"
    if not isinstance(raw, dict):
        return True, "", "judge 返回非 dict，保守判为可答"
    ans = bool(raw.get("answerable", True))
    return ans, str(raw.get("evidence", "")), str(raw.get("rationale", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证不可答候选")
    parser.add_argument("--collection", default="rag4c_chunks")
    parser.add_argument("--sleep-ms", type=int, default=300, help="避免上游限流")
    parser.add_argument("--json", dest="json_out", default="")
    args = parser.parse_args(argv)

    from eval.answer_eval.unanswerable_candidates import CANDIDATES
    from eval.retrieval_eval.milvus_store import (
        MilvusStoreConfig,
        MilvusVectorStore,
    )

    cfg = MilvusStoreConfig.from_env()
    cfg.collection = args.collection
    store = MilvusVectorStore(cfg)
    store.connect()

    # 直接造真实嵌入器：_make_embedder 会套一层磁盘缓存并绑定语料指纹，
    # 这里只是探测"库里有没有"，不需要缓存层。
    from config.settings import get_settings
    from core.embedding import create_embedder

    embedder = create_embedder(get_settings().embedding)

    # 事实可答性裁判：余弦阈值在这批"同领域不同事实"的问题上**不管用**
    # （实测 top1 全部落在 0.59~0.73，远高于弃权阈值 0.52）。主题相关 != 能回答，
    # 只能靠"片段里有没有写出那个具体事实"来判。用 judge 槽位。
    from config.settings import get_settings as _gs

    _settings = _gs()
    from core.llm import create_client

    judge_client = create_client(_settings.llm.judge, slot="judge")

    results: list[dict[str, Any]] = []
    import time

    for cid, question, why in CANDIDATES:
        cos, chunks = _top_chunks(question, store, embedder, top_k=5)
        answerable, evidence, rationale = _judge_answerable(question, chunks, judge_client)
        # PASS = 裁判确认片段里没有那个事实；ANSWERABLE = 其实能回答，剔除
        verdict = "ANSWERABLE" if answerable else "PASS"
        results.append(
            {
                "case_id": cid,
                "question": question,
                "top1_cosine": round(cos, 4),
                "verdict": verdict,
                "judge_rationale": rationale,
                "judge_evidence": evidence,
                "top1_snippet": (chunks[0][:120].replace("\n", " ") if chunks else ""),
                "why": why,
            }
        )
        print(f"[{verdict:10s}] cos={cos:.4f}  {cid}")
        print(f"{'':12s} 裁判：{rationale[:90]}")
        if args.sleep_ms:
            time.sleep(args.sleep_ms / 1000.0)

    passed = [r for r in results if r["verdict"] == "PASS"]
    bad = [r for r in results if r["verdict"] == "ANSWERABLE"]

    print()
    print(f"候选 {len(results)} 条：确认不可答 {len(passed)} / 其实有答案（剔除）{len(bad)}")
    for r in bad:
        print(f"  剔除 {r['case_id']}：{r['judge_rationale'][:80]}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
