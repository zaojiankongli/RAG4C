#!/usr/bin/env python
"""对已入库的知识库问真问题，跑通「检索 -> 生成 -> 引用验证」最后一段。

用法::

    # 默认问一个英文库 + 一个中文库，验证全链路在真实官方文档上成立
    python scripts/ask_docs_kb.py

    # 问自己的问题
    python scripts/ask_docs_kb.py --dataset springboot --query "How do I configure a DataSource?"

与 ``scripts/verify_docs_kb.py`` 的分工是刻意的：

- ``verify_docs_kb.py`` **只验到检索层，不调 LLM**，所以能秒级重复跑，
  适合每次改完检索链路就跑一遍；
- 本脚本补上最后一段——真的让模型基于召回的官方文档作答。代价是慢：
  LLM 槽位指向云端（通义千问）**实测单问 30~90 秒**，指向本机 Ollama（CPU）
  则是 **240~1140 秒**。跨度这么大是因为除了生成本身，引用验证还要再调一次
  LLM，触发二轮检索时还会再来一遍。所以默认只问两个问题，**不要**把它放进
  run_tests.py（那 21 个冒烟脚本总共才 40 秒）。

判据不是「答案看起来对」（那需要人读），而是两条机器可判的硬条件：
库里明明有对应官方文档却弃权 = 链路某一环没接上；有答案但零引用 =
答案无法溯源，等于没有 RAG。

弃权时会连 ``traces`` / ``verdict`` 一并打印。弃权门有两道——检索分低于
``pipeline.retrieval_score_threshold``、蕴含分低于
``pipeline.entailment_score_threshold``——两道对外都表现为空答案，成因却
完全相反（没召回对 vs 召回对了没验过），不打印就只能靠改代码再等一轮生成。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from rag import answer_query  # noqa: E402

#: 默认问题：一个英文库、一个中文库。中英各一是有意的——BM25 走的是 jieba
#: 分词，中文路径挂了而英文路径正常的情况实测出现过，只问一种语言看不出来。
_DEFAULT_CASES = [
    ("springboot", "What is a Spring Boot starter and why would I use one?"),
    ("milvus", "Milvus 里的 collection 和 index 是什么关系？"),
]


def _ask(dataset: str, question: str) -> bool:
    """问一次，打印结果，返回是否通过。"""
    print(f"\n{'=' * 70}\n[{dataset}] {question}")
    t0 = time.perf_counter()
    try:
        res = answer_query(question, dataset_id=dataset)
    except Exception as exc:  # noqa: BLE001 - 链路异常要如实报告，不吞
        print(f"  [FAIL] 抛异常: {type(exc).__name__}: {exc}")
        return False
    dt = time.perf_counter() - t0

    answer = (res.answer or "").strip()
    cites = res.citations or []
    print(f"  耗时 {dt:.1f}s  弃权={res.abstained}  引用 {len(cites)} 条")
    print(f"  答案: {answer[:600]}{'…' if len(answer) > 600 else ''}")

    # Citation 带的是 chunk_id 而不是文档路径，所以要回查一次才能显示出处。
    # 这一步纯 Milvus 查询、不调 LLM，代价可以忽略；没有它，引用对人来说
    # 只是一串哈希，「能溯源」就成了口头承诺。
    try:
        from config.settings import get_settings
        from core.milvus_client import RagMilvusClient

        chunks = RagMilvusClient(get_settings().milvus).get_chunks_by_ids(
            [c.chunk_id for c in cites[:5]]
        )
        for c in chunks:
            print(f"    - {(c.source or c.doc_id or '?')[:120]}")
    except Exception as exc:  # noqa: BLE001 - 出处显示失败不改变判定
        print(f"    （出处回查失败: {exc}）")

    status = {}
    for c in cites:
        status[c.status] = status.get(c.status, 0) + 1
    if status:
        print(f"  引用验证: {status}")

    if res.abstained or not answer:
        # 弃权本身是合法行为，但在「库里明明有对应官方文档」的问题上弃权，
        # 说明链路某一环没接上，要暴露出来而不是当成通过。
        #
        # 必须连 traces / verdict 一起打印：弃权门有两道（检索分低于阈值、
        # 蕴含分低于阈值），两道给出的结论都是「空答案」，但成因和修法完全
        # 不同——前者是检索没召回对，后者是召回对了却没验过。不打印的话，
        # 想分辨是哪一道就只能改代码再等一次生成，而本机一次要十几分钟。
        print("  [FAIL] 库里有对应文档却弃权/空答案")
        for t in res.traces:
            print(f"    trace: {t[:160]}")
        if res.verdict:
            print(f"    verdict: {str(res.verdict)[:400]}")
        return False
    if not cites:
        print("  [FAIL] 有答案但无引用（无法溯源到官方文档）")
        return False
    print("  [PASS] 有答案且有引用")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="对知识库问真问题，验证检索->生成->引用整条链路",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--dataset", default="", help="知识库 id（不给则跑默认两问）")
    ap.add_argument("--query", default="", help="问题（配合 --dataset）")
    args = ap.parse_args()

    if bool(args.dataset) != bool(args.query):
        print("[错误] --dataset 与 --query 要么都给，要么都不给", file=sys.stderr)
        return 2

    cases = [(args.dataset, args.query)] if args.dataset else _DEFAULT_CASES

    # 报出实际生效的生成槽位，而不是写死"本机 LLM"：单问耗时在 30 秒和 1140 秒
    # 之间差 30 倍，全看这里指向哪。等了十分钟才发现自己在跑本机 CPU，是这个
    # 脚本最容易让人吃的一次亏。
    try:
        from config.settings import get_settings

        _gen = get_settings().llm.generation
        print(f"生成槽位：{_gen.model} @ {_gen.base_url}")
    except Exception as exc:  # noqa: BLE001 - 只是提示，读不到不该拦住提问
        print(f"（生成槽位信息读取失败: {exc}）")
    print(f"共 {len(cases)} 问。云端单问约 30~90 秒，本机 CPU 则 240~1140 秒。")

    fails = sum(0 if _ask(ds, q) else 1 for ds, q in cases)
    print(f"\n{'=' * 70}\n结果：{len(cases) - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
