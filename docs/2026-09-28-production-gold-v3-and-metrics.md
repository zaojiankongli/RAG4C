# 标注扩到 52 条 + 同义切片口径修正 + 失败率指标 + ef 扫描补齐（2026-09-28，Round 7）

## 1. 上游失败率 / 降级率成为报告常规指标

之前上游一出问题（如重排端点返回 429），整轮评测直接崩，既拿不到质量指标，
也拿不到失败率。现在：

- **检索失败**（Milvus 异常）与**重排失败**（429 / 抖动）都按**用例**降级：
  沿用检索顺序继续跑，不再中断整轮；
- 报告新增三个字段：`degraded_cases`（降级条数）、`degraded_rate`（降级率）、
  `failure_kinds`（按异常类型分类，如 `RerankError` / `MilvusException`）；
- 摘要里多打一行：`降级 N 条（x%）明细={...}`。

目的：把"质量变差"和"上游挂了"分开。一次重排限流不该被误读成检索质量下降，
也不该悄悄消失在日志里。另加 `--sleep-ms`，扫参数时避免把上游打成 429。

## 2. 标注 32 → 52 条，并修正"同义切片"口径

扩标方式不变（从 4000 条抽样里挑带标题的切片，读正文确认后写问题），
新增 20 条（Milvus 插入/struct schema/Boost Ranker/部署验证，
LangChain memory / subgraph / MCP / 路由 / 安全评估等）。

**修正一处会污染指标的标注口径**：

| 判定规则 | 结果 | 问题 |
|---|---|---|
| 同文档 + 相邻切片 = 同义（我上一轮的做法） | 给 52 条都加了"等价 gold" | **错的**：相邻切片是**接续内容**，不是重复。gold 集合被撑大 → Recall 的分母虚高、指标被压低 |
| **文本重合度 ≥ 0.65 = 真重复**（本轮改用） | 只有 **9 条**是真重复 | 正确 |

真重复的形态是：同一份 Milvus RAG 教程被拆进多个 `doc-*` 变体
（`milvus_client_uri` 一份内容出现在 11 个 chunk、`rag_private_knowledge` 8 个）——
这类才是"检索返回任意一片都算答对"。

## 3. 52 条上的最终结果（ef=256，候选 30，bge-m3 + bge-reranker-v2-m3）

| 策略 | recall@5 | recall@10 | MRR@10 | NDCG@10 | p50 | 降级 |
|---|---|---|---|---|---|---|
| 稠密（无重排） | 0.659 | 0.747 | 0.533 | 0.579 | 153ms | 0（0.0%） |
| **稠密 + 重排** | **0.799** | **0.881** | **0.673** | **0.712** | 598ms | 0（0.0%） |
| 稠密 + BM25(RRF) + 重排 | 0.741 | 0.766 | 0.637 | 0.652 | 528ms | 0（0.0%） |

**结论稳定了**：32 条与 52 条两轮一致——**稠密 + 重排最好**，
混合检索（BM25+RRF）在这份语料上**持续更差**（NDCG 0.652 vs 0.712）。
（10 条样本时"混合 +3.8%"确实是噪声，已两次证伪。）

## 4. ef 扫描补齐 128 / 512 → 生产推荐值

同口径（32 条标注，候选 30，稠密+重排）：

| ef | NDCG@10 | 说明 |
|---|---|---|
| 64 | 0.681 | 生产当前值 |
| 128 | 0.693 | +1.8% |
| **256** | **0.720** | **+5.7%，延迟不涨** |
| 512 | ≈256（无进一步收益，p50 更高） | 不划算 |

**推荐：`milvus.ef = 256`**（当前 64 偏保守；512 无额外收益）。
同时候选系数建议 3（候选 30）：20→30 收益明显，30→50 只再加约 6% 却要多付约 0.4s，
性价比一般。

## 5. 变更清单

| 文件 | 改动 |
|---|---|
| `eval/retrieval_eval/runner.py` | 检索/重排失败按用例降级；新增 `degraded_cases` / `degraded_rate` / `failure_kinds`；`sleep_ms` 节流 |
| `eval/run_retrieval_eval.py` | `--sleep-ms` |
| `eval/retrieval_eval/gold_production.py` | 扩到 55 条（52 可答 + 3 不可答）；按文本重合度重标真重复 |

## 6. 复现

```bash
export RAG4C_MILVUS_URI=http://192.168.100.128:19530
export RAG4C_MILVUS_COLLECTION=rag4c_chunks
export RAG4C_MILVUS_EF=256
RAG4C_ENV_FILE=config/.env.postgres python -m eval.run_retrieval_eval \
  --corpus milvus --gold production --embedder api \
  --strategies dense,dense_rerank,hybrid_rrf --candidates 30 --sleep-ms 1500 \
  --out eval/.cache/final52-clean-ef256.json
```

## 7. 下一步

1. 生产 `milvus.ef` 改 256 后做一次端到端复测（含弃权率、首条余弦分布）；
2. 标注再补到 80+ 条并覆盖中文 Milvus 文档那一半语料（现在偏英文技术文档）；
3. 把 `degraded_rate` 接进 `/api/metrics` 与监控页（现在只在评测报告里）；
4. 重排本身的替代方案对比（本地 reranker vs 云端）与成本测算。
