# 端点快速失败 + 生产语料黄金标注（2026-09-28，Round 5）

## 1. 端点不可用即快速失败（把 113 秒砍到 2 秒）

**现象**：入库期槽位（contextual / triplet / classifier）刻意指向本机 Ollama；
机器没起 Ollama 时，一次调用要把重试策略耗满才失败——**实测 113 秒**，
而失败还被上层静默降级成"没有上下文"。

**根因**：连接被拒（服务没起 / 地址写错）不是瞬态故障，重试多少次结果都一样，
但重试策略对所有异常一视同仁。

**改法**：`core/llm.py` 在发起调用前做一次**廉价探活**（一次 TCP 握手，
默认 1 秒），失败结论按地址缓存 15 秒，缓存期内不再握手：

- 新增 `retry.endpoint_probe_on`（默认开）/ `endpoint_probe_timeout_s`（1.0）
  / `endpoint_probe_negative_ttl_s`（15.0）三个配置项；
- `chat()` / `chat_json()` / `chat_stream()` 三处入口统一走 `_assert_endpoint_reachable()`；
- 探活失败直接抛 `LLMError`（**不发起调用**），记 `llm.endpoint.unreachable`
  指标，并往用量台账记一次失败（成本账上不能当作没发生）；
- 不用 HTTP 探活：各家健康检查路径不统一，多一次往返更慢，TCP 握手已足够区分
  "服务没起"和"服务在但忙"。

**实测**：同一台机器、同一个槽位，**113 秒 → 2.05 秒（约 55 倍）**；
生产槽位（DashScope）探活开启后照常应答，无副作用。

**测试会话默认关探活**（`tests/conftest.py`）：大量用例用桩客户端 / 不存在的地址，
探活会把它们挡在被测代码之外。专门验证探活的用例自己打开它
（`tests/test_llm_endpoint_probe.py`，5 条）。

> 踩坑记录：写 fixture 时只还原了环境变量、没还原进程级 settings 单例，
> 导致后面跑的 `test_llm_circuit` 被误挡（3 条转红）。已改为逐项保存/还原。

## 2. 生产语料黄金标注（第一次在真实数据上量检索质量）

### 语料与标注

生产集合 `rag4c_chunks` 实测 **9723 行**：Milvus 官方文档 1356 切片
（dataset `default`）+ LangChain 文档 644 切片（dataset `langchain`）。

做法：先按关键词捞候选 → **逐条读原文确认** → 只把"能独立回答该问题"的切片
记为 gold。共 **10 条可答 + 3 条不可答**（`eval/retrieval_eval/gold_production.py`），
每条都写了标注理由。跑评测前会按主键校验标注是否还在集合里，失效立刻报错。

评测走**只读**路径（`--corpus milvus`）：不切片、不写入、不动生产数据。

### 真实语料上的结果（bge-m3 + bge-reranker-v2-m3，候选 30）

| 策略 | recall@10 | MRR@10 | NDCG@10 | p50 |
|---|---|---|---|---|
| 稠密（无重排） | 0.600 | 0.433 | 0.476 | 127ms |
| 稠密 + 重排 | 0.700 | 0.475 | 0.532 | 580ms |
| **稠密 + BM25(RRF) + 重排** | **0.700** | **0.500** | **0.552** | 546ms |

### 三条结论（其中一条**推翻了之前的代理结论**）

1. **真实语料比代理语料难得多**：NDCG 0.476 vs 文档代理语料 0.658。
   之前"文档代理语料可用"的判断要修正——它可信，**但偏乐观**，
   绝对数值不能直接外推。
2. **重排收益在真实数据上更大**：NDCG +11.8%（代理语料是 +9.3%），
   recall@10 +10%。
3. **BM25 分支的收益与语料有关**：在中文文档代理语料上无收益（recall@5 甚至
   下降），在真实语料（大量英文技术文档 + 专有名词如 `BIN_IVF_FLAT`、
   `add_collection_field`）上 **+3.8% NDCG、+5.3% MRR**。
   → 生产默认 `hybrid_search_on=True` 在这份语料上是正确的；
   同时也说明"混合检索一定更好/一定没用"都不是结论，得看语料。
4. 不可答用例的首条得分：稠密 0.534（偏高，弃权阈值要盯），
   混合 RRF 0.020（RRF 分不是余弦，两把尺子不可混用）。

## 3. 变更清单

| 文件 | 改动 |
|---|---|
| `config/settings.py` | `retry.endpoint_probe_*` 三个配置 |
| `core/llm.py` | 探活 + 三处入口断言 + `reset_endpoint_probe_cache()` |
| `tests/conftest.py` | 测试会话默认关探活 |
| `tests/test_llm_endpoint_probe.py` | 新增 5 条 |
| `eval/retrieval_eval/gold_production.py` | 新增生产语料标注 |
| `eval/retrieval_eval/{runner,milvus_store}.py`、`eval/run_retrieval_eval.py` | `--corpus milvus --gold production`（只读） |

## 4. 复现

```bash
export RAG4C_MILVUS_URI=http://192.168.100.128:19530
export RAG4C_MILVUS_COLLECTION=rag4c_chunks
RAG4C_ENV_FILE=config/.env.postgres python -m eval.run_retrieval_eval \
  --corpus milvus --gold production --embedder api \
  --strategies dense,dense_rerank,hybrid_rrf --candidates 30 \
  --out eval/.cache/retrieval-report-production.json
```

## 5. 下一步

1. 生产标注只有 10 条，样本太小（单条 ±10%）；扩到 30~50 条才有统计意义；
2. 用真实语料做索引参数扫描（HNSW 的 `ef`、候选系数）——之前只在代理语料上做过；
3. 弃权阈值：不可答用例首条余弦 0.53，接近 `dense_cosine_threshold=0.52` 的边界，
   值得用真实数据重标一次；
4. 探活目前只覆盖 LLM 槽位，嵌入 / 重排端点可同样处理。
