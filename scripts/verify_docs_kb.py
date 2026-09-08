#!/usr/bin/env python
"""验证官方文档知识库的检索质量与隔离性（不调 LLM）。

运行：python scripts/verify_docs_kb.py [--dataset milvus] [--top-k 5]

**刻意只验到检索这一层，不做生成。** 两个原因：

1. 生成一次要走改写 / 路由 / 重排 / 生成 / 验证整条链，本机 Ollama 上单次
   接近 200 秒；而这里要验的是「文档进没进库、切得能不能召回、知识库之间
   隔不隔离」——这些在检索层就能给出确定答案，往后走只会引入噪声。
2. 换成云端 LLM 能快，但那要花用户的额度，不该由一个校验脚本默默决定。

验的四件事：

- **在库**：每个 dataset 的 chunk 数与文档数（直接 query，不经检索）
- **可召回**：领域内的真实问题能不能召回该项目的内容
- **隔离**：把 A 项目的问题投到 B 知识库，必须召回不到 A 的内容
- **稀疏路生效**：对比「稠密+BM25」与「纯稠密」的命中，确认 BM25 那一路
  真的在贡献（中文分词修好之前，这一路恒为空）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from core.embedding import create_embedder  # noqa: E402
from core.milvus_client import RagMilvusClient  # noqa: E402

#: 每个知识库一个「领域内必须能召回」的探针问题，外加它绝不该出现在
#: 哪些别的库里。问题刻意用该项目独有的术语——用通用词（"怎么安装"）
#: 探不出隔离性，因为每个库都能召回点什么。
_PROBES: dict[str, dict[str, object]] = {
    "milvus": {
        "query": "如何创建一个 collection 并建立向量索引？",
        "expect_terms": ["collection", "index", "milvus"],
    },
    "springboot": {
        "query": "How does Spring Boot auto-configuration work with starters?",
        "expect_terms": ["spring", "auto-configuration", "starter"],
    },
    "langchain": {
        "query": "How do I build a chain with a chat model and a prompt template?",
        "expect_terms": ["langchain", "model", "prompt"],
    },
    "langgraph": {
        "query": "How do I add persistent memory and checkpointing to a graph?",
        "expect_terms": ["graph", "checkpoint", "state"],
    },
    "redis-stack": {
        "query": "How do I use Redis sorted sets and streams?",
        "expect_terms": ["redis", "stream", "sorted set"],
    },
}

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="校验官方文档知识库（仅检索层）")
    ap.add_argument("--dataset", action="append", default=[], help="只验指定知识库，可重复")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    settings = get_settings()
    milvus = RagMilvusClient(settings.milvus)
    embedder = create_embedder(settings.embedding)
    client = milvus._ensure_client()  # noqa: SLF001 - 校验脚本需要裸 query 统计

    targets = args.dataset or list(_PROBES)
    unknown = [d for d in targets if d not in _PROBES]
    if unknown:
        print(f"[错误] 未知知识库 {unknown}；可选 {list(_PROBES)}", file=sys.stderr)
        return 2

    print(f"集合: {settings.milvus.collection_name} @ {settings.milvus.uri}")
    print(f"分词: {settings.milvus.bm25_analyzer}   top_k={args.top_k}")
    print("=" * 70)

    # -- 1. 在库统计 --------------------------------------------------- #
    print("\n== 1. 入库统计 ==")
    stats: dict[str, dict[str, object]] = {}
    for ds in targets:
        rows = client.query(
            collection_name=settings.milvus.collection_name,
            filter=f'dataset_id == "{ds}"',
            output_fields=["doc_id"],
            limit=16384,
        )
        docs = {r["doc_id"] for r in rows}
        stats[ds] = {"chunks": len(rows), "docs": len(docs)}
        check(f"{ds}: 库里有内容", len(rows) > 0,
              f"chunks={len(rows)} docs={len(docs)}")
        print(f"         {len(docs)} 篇文档 / {len(rows)} chunk")

    live = [ds for ds in targets if stats[ds]["chunks"]]
    if not live:
        print("\n[提示] 没有任何知识库有内容，先跑 scripts/ingest_source.py --all")
        return 1

    # -- 2. 领域内召回 -------------------------------------------------- #
    print("\n== 2. 领域内召回 ==")
    for ds in live:
        q = str(_PROBES[ds]["query"])
        hits = milvus.hybrid_search(
            query_dense=embedder.embed_query(q),
            top_k=args.top_k,
            query_text=q,
            filter_expr=milvus.build_filters(tenant_id="", dataset_id=ds),
        )
        check(f"{ds}: 领域问题能召回", len(hits) > 0, f"query={q!r}")
        if not hits:
            continue
        same = all(h.chunk.dataset_id == ds for h in hits)
        check(f"{ds}: 召回结果全部属于本知识库", same,
              str({h.chunk.dataset_id for h in hits}))
        blob = " ".join(h.chunk.text.lower() for h in hits)
        terms = [t for t in _PROBES[ds]["expect_terms"] if t.lower() in blob]  # type: ignore[union-attr]
        check(f"{ds}: 召回内容含领域术语 {terms}", len(terms) >= 1,
              f"一个都没命中: {_PROBES[ds]['expect_terms']}")
        top = hits[0].chunk
        print(f"         top1 doc={top.doc_id[:44]}  score={hits[0].score:.4f}")
        print(f"         {top.text[:110].replace(chr(10), ' ')}…")

    # -- 3. 知识库隔离 -------------------------------------------------- #
    # 这条是多知识库设计的硬证明：拿 A 的问题去查 B，若还能召回 A 的内容，
    # 说明 dataset_id 过滤没真正生效——那多知识库就只是个标签而非边界。
    print("\n== 3. 知识库隔离 ==")
    if len(live) < 2:
        print("  [SKIP] 少于两个非空知识库，无法验证隔离")
    else:
        a, b = live[0], live[1]
        q = str(_PROBES[a]["query"])
        hits = milvus.hybrid_search(
            query_dense=embedder.embed_query(q),
            top_k=args.top_k,
            query_text=q,
            filter_expr=milvus.build_filters(tenant_id="", dataset_id=b),
        )
        leaked = [h for h in hits if h.chunk.dataset_id != b]
        check(f"{a} 的问题投到 {b}，不泄漏 {a} 的内容", not leaked,
              str([h.chunk.dataset_id for h in leaked]))
        # 不存在的知识库必须返回空而不是报错——空结果曾经会让服务端抛
        # "unsupported ID type"，进而累积熔断失败。
        empty = milvus.hybrid_search(
            query_dense=embedder.embed_query(q),
            top_k=args.top_k,
            query_text=q,
            filter_expr=milvus.build_filters(tenant_id="", dataset_id="__no_such_kb__"),
        )
        check("查不存在的知识库返回空而不是抛错", empty == [], f"{len(empty)} 条")

    # -- 4. BM25 稀疏路是否真的在贡献 ----------------------------------- #
    print("\n== 4. BM25 稀疏路 ==")
    ds = live[0]
    q = str(_PROBES[ds]["query"])
    filt = milvus.build_filters(tenant_id="", dataset_id=ds)
    vec = embedder.embed_query(q)
    hybrid = milvus.hybrid_search(query_dense=vec, top_k=args.top_k,
                                  query_text=q, filter_expr=filt)
    dense_only = milvus.hybrid_search(query_dense=vec, top_k=args.top_k,
                                      query_text=None, filter_expr=filt)
    h_ids = [h.chunk.chunk_id for h in hybrid]
    d_ids = [h.chunk.chunk_id for h in dense_only]
    check("纯稠密路可用", len(d_ids) > 0)
    check("混合检索可用", len(h_ids) > 0)
    # 两者结果不同 = 稀疏那一路确实改变了排序。相同不一定是坏事（可能本来
    # 就该是这个顺序），所以只提示不判失败。
    if h_ids and d_ids:
        print(f"  [INFO] 混合 vs 纯稠密 top{args.top_k} "
              f"{'排序不同（BM25 在起作用）' if h_ids != d_ids else '排序一致'}")
        print(f"         hybrid: {[i[-8:] for i in h_ids]}")
        print(f"         dense : {[i[-8:] for i in d_ids]}")

    print()
    print(f"结果：{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
