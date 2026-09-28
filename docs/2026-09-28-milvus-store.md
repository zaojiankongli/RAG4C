# 检索评测改用真实 Milvus（pymilvus 官方 SDK，2026-09-28）

## 1. 背景与目标

Round 1 的 `LocalVectorStore`（numpy 余弦）是"连不上 Milvus 时的权宜之计"：
它只替掉了余弦 Top-K 那段算术，BM25、过滤表达式、分组、一致性级别这些
真正影响结果的东西全都测不到。虚拟机上的 **Milvus 3.0.0** 现在可用
（`192.168.100.128:19530`，生产集合 `rag4c_chunks` 已有 9723 行、HNSW/COSINE、
dim 1024、BM25 分词器 chinese），所以把评测搬到**真实索引**上。

| 项 | 内容 |
|---|---|
| 交付 | `eval/retrieval_eval/milvus_store.py`（配置 / Schema / 索引 / 写入 / 检索 / 释放 / CLI） |
| 接线 | `eval/run_retrieval_eval.py --store {local,milvus}`；新增 `hybrid_rrf` 策略 |
| 测试 | `tests/test_milvus_store.py`：16 条离线 + 1 条集成（需显式开关） |
| 验收 | ① 真实 Milvus 端到端跑通；② 复现 Round 1 的 numpy 基线；③ 参数全部可配、无硬编码 |

## 2. 连接参数（全部环境变量，零硬编码）

读取顺序：**`RAG4C_MILVUS_<KEY>` → `MILVUS_<KEY>` → 默认值**。

| 变量 | 默认 | 缺失/异常处理 |
|---|---|---|
| `URI` | `http://127.0.0.1:19530` | 没配时先退回 `HOST`+`PORT` 拼装，再没有就用默认地址并打 warning；连不上时异常里给出四条排查方向（服务是否起 / 地址是否通 / 是否要 token / Windows 别用 Milvus Lite） |
| `TOKEN` | `""` | 留空不报错（本地无认证实例）；需要认证的服务端会拿到鉴权失败，那时再配 |
| `DB` / `DB_NAME` | `""` | 留空 = 服务端默认库，**不调用** `use_database`（省一次往返） |
| `HOST` / `PORT` | `""` / `19530` | 仅当没有 `URI` 时用于拼装 |
| `COLLECTION` | `rag4c_eval_chunks` | 默认**刻意不指向生产集合** `rag4c_chunks`——评测要写数据、建索引、可能删集合 |
| `DIM` | `1024`（BGE-M3） | 与向量不一致时写入报错（异常里点明维度不匹配） |
| `INDEX_TYPE` | `HNSW` | 非法值最终由服务端拒绝并翻译成本模块异常 |
| `METRIC_TYPE` | `COSINE` | 改它必须重建索引 |
| `M` / `EF_CONSTRUCTION` / `NLIST` | `16` / `200` / `1024` | 建索引参数 |
| `EF` / `NPROBE` | `64` / `16` | 检索参数；`ef` 会自动取 `max(ef, limit)` 满足 Milvus 硬约束 |
| `CONSISTENCY` | `Bounded` | `Strong` / `Session` / `Bounded` / `Eventually` |
| `BATCH_SIZE` / `TIMEOUT` | `256` / `30.0` | 单批条数 / 单次 RPC 超时秒 |
| `ENABLE_BM25` / `BM25_ANALYZER` | `1` / `chinese` | 关掉则 Schema 不含 `sparse_vector` 与 BM25 Function |
| `TEXT_MAX_LENGTH` | `65535` | **按字节**计，中文一字 3 字节 |

> Windows 注意：`uri` 写本地文件（`./xx.db`）是 Milvus Lite，**只支持 Linux/macOS**，
> Windows 上会报 `ModuleNotFoundError: milvus_lite`。要用就上 Docker 或连服务端。

## 3. 集合与 Schema（与生产 `rag4c_chunks` 同构）

创建顺序严格是 `has_collection → create_collection(schema + index_params) → load_collection`：
Milvus 的硬约束是**先有索引才能 load，先 load 才能查**，跳步会在检索时报
`index not found / collection not loaded`。

- 主键：`chunk_id` VARCHAR(512)——用业务 ID 而不是自增 ID，去重与 upsert 才有意义
- 向量：`dense_vector` FLOAT_VECTOR(1024)
- 稀疏：`sparse_vector` SPARSE_FLOAT_VECTOR，由 **BM25 Function** 从 `text` 自动算
  （应用层不生成稀疏向量），中文必须 `analyzer_params={"type":"chinese"}`，
  否则 standard 分析器按空白切词会让中文 BM25 召回恒为 0（**不报错，静默失效**）
- 标量：`doc_id / text / text_hash / created_at / updated_at / source / parent_chunk_id /
  acl / tenant_id / dataset_id / document_revision / content_revision / metadata(JSON)`

## 4. 索引与度量的选型依据

| 类型 | 何时用 | 关键参数 |
|---|---|---|
| **HNSW**（默认，生产同款） | 要低延迟高召回、内存够；百万级以内首选 | `M`（每层连接数，16~64，越大图越密、内存越高）、`efConstruction`（建索引宽度 200~500，越大越准越慢） |
| **IVF_FLAT** | 想省内存、构建快；召回靠检索期 `nprobe` 调 | `nlist`（分桶数，常取 `sqrt(N)*4`） |
| **AUTOINDEX** | 交给 Milvus 按数据规模自动挑；Zilliz Serverless / 懒得调参 | 无（**不要塞无关参数**，会被拒） |

度量：`COSINE`（默认）——文本嵌入的通行选择，只比方向不比长度，Milvus 自动归一化，
返回值即 [-1,1] 相似度、越大越近；`IP` 在向量已归一化时等价但更快（未归一化会被长向量带偏）；
`L2` 适合坐标/图像类。改度量必须重建索引。

## 5. 写入与检索

- 写入：`upsert()`（同主键覆盖，**幂等**，重复跑评测不会重复入库）/ `insert()`（主键冲突报错）。
  按 `batch_size` 切批，默认 256 条 ≈ 1MB（1024 维 float32 约 4KB/条），接近官方建议的单批体积。
- 检索：`search(query_vector, top_k, filter_expr, output_fields, consistency_level)`；
  `search_text()` 走 BM25 分支；`hybrid_search()` 稠密+稀疏双路 **RRF 融合**（`rrf_k=60`，与生产一致）。
- 输出统一成一层 `[{chunk_id, score, distance, entity}]`，并把"越大越好"的方向对齐
  （L2 转成 `1/(1+d)`，避免调用方拿错方向）。
- 过滤：表达式原样透传给 Milvus（如 `tenant_id == "eval" and dataset_id != "x"`）。
- 删除：`delete(expr=...)` 或 `delete(ids=...)`；**两者都不给时直接拒绝**（等于删全表，fail-closed）。

## 6. 异常处理与资源释放

- 统一异常 `MilvusStoreError`，把 gRPC 错误码翻成"下一步查什么"，原始异常挂在 `__cause__`：
  连接失败（检查服务/地址/token/Lite 平台）、集合不存在、索引未建（先建索引再 load）、
  参数错误（维度不符、text 超长、过滤表达式语法错、`ef < top_k`）。
- 连接释放：`close()` 幂等；**推荐用 `with MilvusVectorStore(cfg) as store:`**；
  评测管线 `run_eval()` 用 `try/finally` 保证 Milvus 连接一定被释放。

## 7. 常见优化手段（按性价比排序）

1. **连接复用**：`MilvusClient` 是长连接，务必复用（本模块单例持有 + `with` 释放），
   别每次检索新建——新建连接的握手成本远大于一次搜索。
2. **批量写入**：`BATCH_SIZE` 调到单批 1~10MB；写入后 `flush()` 让数据立即可见、
   并按需建索引。
3. **检索参数**：`EF`（HNSW，必须 ≥ limit）/ `NPROBE`（IVF）是"召回↔延迟"的主旋钮，
   先压测再定，不要盲调大。
4. **建索引参数**：`M`/`efConstruction` 只在建索引时生效，改了要重建；数据量大时
   先用小样本验证再全量。
5. **`load` 与一致性**：集合必须 load 才能查；不常查的集合可 `release()` 省常驻内存。
   一致性 `Bounded` 够用；刚写完立刻查要确定性就临时用 `Strong`（更贵）。
6. **只回带需要的字段**：`output_fields` 别放向量字段（1024 维 × top_k 会白白放大传输与序列化）。

## 8. 实测结果（227 切片语料，真实 bge-m3 + bge-reranker-v2-m3）

| 后端 | 策略 | recall@10 | MRR@10 | NDCG@10 | 检索 p50 |
|---|---|---|---|---|---|
| numpy（Round 1 代理） | dense | 0.833 | 0.609 | 0.658 | ~0.4ms |
| **Milvus 3.0（HNSW）** | dense | **0.833** | **0.609** | **0.658** | **5.7ms** |
| numpy | dense+rerank(c30) | 0.826 | 0.725 | 0.719 | — |
| Milvus | dense+rerank(c30) | 0.826 | 0.725 | 0.717 | 428ms |
| Milvus | **hybrid(BM25+RRF)+rerank** | 0.826 | 0.725 | 0.715 | 431ms |

三条结论：

1. **Round 1 的 numpy 代理被证实可用**：真实 Milvus 上 dense 五项指标 delta 全为 0，
   之前"重排 MRR +19%、NDCG +9%"、"候选系数 3 是甜点"、"MMR 负收益"的结论全部成立。
2. **BM25 分支在这份语料上没带来收益**（recall@5 0.797→0.739，NDCG 基本持平）。
   227 切片的中文文档里关键词重叠度高，稠密已经够；语料变大/术语变专精后再复测。
   注意生产默认 `hybrid_search_on=True`，这条数据说明"默认开"不一定赚，
   值得在生产语料上再测一次。
3. `get_collection_stats().row_count` 是**未压实口径**（upsert 会让它虚高，
   实测 227 行显示 454）；逻辑条数以 `query` 为准，别拿它当去重依据。

## 9. 复现命令

```bash
export RAG4C_MILVUS_URI=http://192.168.100.128:19530
export RAG4C_MILVUS_COLLECTION=rag4c_eval_chunks

python -m eval.retrieval_eval.milvus_store --check    # 只打印配置/Schema/索引（不连服务端）
python -m eval.retrieval_eval.milvus_store --probe    # 探活 + 列集合
python -m eval.retrieval_eval.milvus_store --demo     # 建→写→查→删（端到端）

python -m eval.run_retrieval_eval --store milvus --embedder api \
  --strategies dense,dense_rerank,hybrid_rrf --candidates 30 \
  --out eval/.cache/retrieval-report-milvus.json

RAG4C_MILVUS_RUN_INTEGRATION=1 python -m pytest tests/test_milvus_store.py -q
```

## 10. 遗留与下一步

1. 生产集合 `rag4c_chunks`（9723 行真实语料）还没有黄金标注，目前只能评测"文档代理语料"；
   下一步可以给生产语料补标注，把指标换成真实数据的。
2. `hybrid_rrf` 只在 Milvus 后端可用（本地 numpy 会 fail-closed 报错，这是有意的）。
3. 索引参数（M/efConstruction/ef）还没做参数扫描，下一步可用同一评测做网格对比。
4. Round 1 记录的环境坑仍未修：外部依赖不可达时全量测试会挂住（15 分钟 13%）。
