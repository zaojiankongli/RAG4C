# ef=256 落地复测 + 标注 83 条 + 降级率进 /api/metrics 与监控页（2026-09-28，Round 8）

## 1. milvus.ef 改 256 并复测

`.env` 与 `config/.env.postgres` 写入 `RAG4C_MILVUS_EF=256`（原 64），
依据：生产语料 32 条标注上 NDCG@10 0.681 → 0.720（+5.7%），p50 无上升；
512 无进一步收益。

复测结果（52 条标注，候选 30）：

| 指标 | 改前（ef=64） | 改后（ef=256） | 复测 |
|---|---|---|---|
| dense_rerank NDCG@10 | 0.681 | 0.712 | **0.712（完全一致，可复现）** |
| dense_rerank recall@10 | 0.828 | 0.881 | 0.881 |

**端到端真跑**（不再只是评测脚本）：一次完整问答走通
「PG 目录库 + Milvus + 真实 bge-m3 嵌入 + 真实 LLM」：

```
问题：Milvus 的 BIN_IVF_FLAT 索引适用于什么向量？
结果：34.4s，未弃权，路由 hybrid，引用 4 条，
     答案正确（二进制向量 / 先按 nlist 分簇再比对簇心）
台账：calls=3, total_tokens=11787, failures=0, cost=0（未配价格表，如实为 0）
```

## 2. 降级率接进 /api/metrics 与监控页

- `core/metrics.py` 新增 `record_outcome(scope, degraded=...)`：
  写 `<scope>.total` 与 `<scope>.errors`，snapshot 自动给出 `error_rate`。
  > 踩坑：最初命名 `<scope>.degraded.errors`，snapshot 的 base 变成
  > `<scope>.degraded`，**找不到分母、比率恒为 0**——命名不对，指标就是死的。
- 埋点位置：
  - `retrieval/pipeline.py`：每次检索记一次；路由降级算降级；
  - `retrieval/stages.py::report_rerank_degraded`：重排降级额外记一次；
  - 若开了 `rerank_on` 却没重排成功，也记一次 rerank 降级。
- `/api/metrics` 新增 `degraded` 段（服务端把比率算好，前端不拼分母）：
  `{"retrieval": {total, degraded, rate}, "rerank": {...}, "endpoint_unreachable": {...}}`
- 监控页新增「上游降级（进程累计）」卡片：检索降级率、重排降级率、
  端点不可达次数（0 绿、<10% 黄、≥10% 红，并标出 降级数/总数）。

设计意图：**"上游没扛住"和"检索变差"必须分开看**——一次重排限流
（429）不该被误读成检索质量下降，也不该悄悄消失在日志里。

## 3. 标注扩到 83 条（含中文语料）

- 新增 28 条 Milvus 文档侧条目（模式匹配运算符、添加向量字段、
  AISAQ/DISKANN、Cohere Ranker、Switchover、CLI 导入 CSV、
  权限位过滤、Blob 存储、K8s 部署、配置 sections、stop nodes 等）；
- 补 4 条**中文语料**条目（Milvus Insight 周边工具、四种一致性级别、
  多一致性/PACELC、接入层职责）——此前标注几乎全是英文技术文档；
- 修正 1 处标注错误：`birdwatcher` 条目的 chunk_id 在集合里不存在
  （抄录错误），已换成验证过的中文条目。评测前的 id 校验把这个拦住了。

顺带修的健壮性问题：`MilvusStoreConfig.from_env()` 现在会退回
`config.settings.milvus.uri`。用 `RAG4C_ENV_FILE` 指定配置文件时，
URI 只在文件里、不进进程环境，之前会莫名连到本机默认端口。

## 4. 83 条上的最终结果（ef=256，候选 30）

| 策略 | recall@5 | recall@10 | MRR@10 | NDCG@10 | p50 | 降级 |
|---|---|---|---|---|---|---|
| 稠密（无重排） | 0.714 | 0.769 | 0.524 | 0.580 | 116ms | 0（0.0%） |
| **稠密 + 重排** | **0.827** | **0.889** | **0.705** | **0.742** | 534ms | 0（0.0%） |
| 稠密 + BM25(RRF) + 重排 | 0.778 | 0.793 | 0.668 | 0.689 | 495ms | 0（0.0%） |

**结论三连一致**（32 → 52 → 83 条）：稠密 + 重排最好；
混合检索在这份语料上**持续更差**（NDCG 0.689 vs 0.742，约 -7%）。
重排的收益（+28% NDCG）也一次比一次稳。

## 5. 变更清单

| 文件 | 改动 |
|---|---|
| `.env` / `config/.env.postgres` | `RAG4C_MILVUS_EF=256` |
| `core/metrics.py` | `record_outcome()`（总数 + 降级数 → error_rate） |
| `retrieval/pipeline.py`、`retrieval/stages.py` | 降级埋点 |
| `server/app.py` | `/api/metrics` 新增 `degraded` 段 |
| `frontend/src/types/rag.ts`、`MonitorPage.tsx` | 降级类型 + 「上游降级」卡片 |
| `eval/retrieval_eval/gold_production.py` | 83 可答 + 3 不可答；补中文条目；修正错误 id |
| `eval/retrieval_eval/milvus_store.py` | URI 退回项目配置 |

## 6. 下一步

1. 生产 `milvus.ef=256` 跑一段时间后，看真实查询的 P95 与弃权率有没有变化；
2. 中文语料只占 4 条，应继续补到 20 条以上（中文一半的语料还没被充分测到）；
3. 把 `degraded_rate` 做成告警阈值（如重排降级率 >5% 持续 5 分钟）；
4. 混合检索的结论需要在**中文语料**上单独复测（现在中文样本太少）。
