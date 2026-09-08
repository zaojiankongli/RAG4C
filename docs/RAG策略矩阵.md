# RAG4C 可插拔策略矩阵

> 本文档由通读后端源码得出，用于指导「设置页」的信息架构设计。
> 所有结论均标注 `文件:行号`，可逐条复核。
> 排除目录：`build/lib/`、`.venv/`、`repo-snapshot/`、`langchain4j-sister-project/`、`scripts/`（测试桩）。

---

## 0. 结论摘要（先读这一节）

RAG4C **在设计上**确实是可插拔的：每个阶段都有多种可选策略，组件、配置开关、执行位置、降级逻辑全部写好了，且互相吻合。

**2026-08-20 修复前**，生产装配存在缺口：全系统只有两个地方构造管线，都只传了最小参数集，导致大部分可选策略永远不会被触发。**该缺口已修复**，本节保留原始诊断供追溯。

| 生产装配点 | 修复前传入 | 修复前遗漏（因而永久失效） |
|---|---|---|
| `rag.py` `_build_pipeline()` | `embedder` `milvus` `reranker` `rewriter` `router` `settings` | `hyde` `subqueries` `stepback` `sentence_window` `graph_retriever` |
| `server/documents.py` `_get_ingest_pipeline()` | `embedder` `milvus` `parser` | `cleaner` `contextualizer` `graph_builder` `graph_enabled` `chunking_mode` `simple_doc_max_chars` |

由于管线内的判断一律写成 `if 开关 and 组件 is not None`，组件为 `None` 时开关打开也没有任何效果。

### 修复内容（2026-08-20）

1. **`rag.py::_build_optional_components()`（新增）** —— 按 `pipeline` 段开关构造 HyDE / SubQueries / Stepback / SentenceWindow / GraphRetriever 并注入。每个组件单独 try 包裹：构造失败只让该策略退化为不可用（记 warning），不影响其余策略与主链路。
2. **`server/documents.py::_build_ingest_optional()`（新增）** —— 同构地构造 cleaner / contextualizer / graph_builder，并把 `chunking_mode`、`simple_doc_max_chars` 从配置传进 `IngestPipeline`。图集合的创建推迟到入库任务内（`_ensure_graph_collections`），避免装配期连 Milvus。
3. **`retrieval/pipeline.py`** —— 新增 `_bm25_text()` 辅助方法，`hybrid_search_on` 关闭时不传 `query_text`（Milvus 客户端据此只发稠密分支），主检索与子查询 / stepback 三处一致；`rerank_on` 关闭时保留召回顺序并记 trace。
4. **`indexing/ingest.py::_embedding_text()`（新增）** —— Contextual Retrieval 生成的上下文现在会拼在片段正文前**一起嵌入**（此前只写进 `metadata["context"]` 而无人读取，等于白算）。只影响嵌入向量，不改写 `chunk.text`，L2 文本哈希校验不受影响。
5. **`config/settings.py`** —— 补齐此前根本不存在的三个入库开关：`clean_on`（默认开）、`contextual_on`（默认关，成本高）、`graph_index_on`（默认关，成本很高）；`graph_retrieval_on` 默认值由 `True` 改为 `False`，与 `graph_index_on` 保持一致（此前默认 True 但从不生效，改为 False 恰好保持可观测行为不变）。

### 第二轮补齐（同日）

6. **配置热更新** —— `/api/config/update` 保存后调用 `rag.reset_pipeline(close=False)` 与 `documents.reset_ingest_pipelines()`，新配置对后续请求立即生效，不再需要重启（`bridge` 段除外，见 §4）。
7. **`verify` 段** —— 验证强度（`entailment_mode` / `strict` / `sample_ratio`）提到配置层，详见 §3.5。
8. **纯死配置项清理** —— `milvus.ef`、`mineru.page_limit`、`observability.tracing_enabled` 接入；`embedding.dim`、`milvus.consistency_level` 移除。详见 §3.2。
9. **`milvus` 检索参数下发** —— 新增 `ef`（HNSW）的下发，与既有的 `nprobe`（IVF）按索引类型二选一。

   > **更正**：本文早前版本称"平铺写法会被丢弃、`nprobe` 一直没生效"。该说法**是错的**，已查证 pymilvus `client/utils.py::get_params()` 会把 `param` 顶层的键合并进 `params` 子字典（注释原文：*after 2.5.2, all parameters of search_params can be written into one layer*），两种写法序列化结果相同。原先平铺的 `nprobe` 一直是生效的。现在用嵌套写法只是分层更清楚，不是修 bug。

10. **Milvus 3.0 写法对齐** —— 融合排序器从 `RRFRanker` 类改为 `Function(function_type=FunctionType.RERANK, params={"reranker": "rrf"})`（文档当前口径）；`delete_by_expr` 从 `ids=` 改为 `filter=`（**这是真 bug**：`ids` 收到字符串会当作单个主键值，按 doc_id 删除一直静默删不掉任何数据）；schema 迁移从不存在的 `client.add_field` 改为 `client.add_collection_field`（**也是真 bug**，此前被 `except Exception` 兜住，迁移路径从未生效）。

### 仍然存在的问题

- `parsers.rotation_on` 仍只写进 metadata，内置 MinerU 引擎未消费（`indexing/parsers/router.py`）。
- `catalog.auto_filter_on` / `auto_tag_on` 的工厂函数仍无人调用（见 §3.4）。
- 桥服务自身的并发 / 队列 / 缓存参数（`bridge` 段）在模块导入时读入常量，热更新不覆盖，仍需重启。

---

## 1. 阶段与执行顺序

### 1.1 入库阶段 `indexing/ingest.py`

| # | 阶段 | 代码位置 | 生产状态 |
|---|---|---|---|
| 1 | 解析 parse | `ingest.py:291` | 生效（引擎可插拔） |
| 2 | 清洗 clean | `ingest.py:300-304` | **死码**，`cleaner` 从未注入 |
| 3 | 预切分 strategy | `ingest.py:307-310` → `_segment:441-452` | 生效（按 doc_type 自动选） |
| 4 | 切分模式决策（doc 级一次） | `ingest.py:317-319` | 生效但被冻结在 `auto` |
| 5 | 逐段切分 | `ingest.py:320-330` | 生效 |
| 6 | tenant/dataset 标记 | `ingest.py:342-347` | 生效 |
| 7 | Contextual Retrieval | `ingest.py:350` | **死码**，`contextualizer` 从未注入 |
| 8 | 嵌入 embed | `ingest.py:384-386` | 生效 |
| 9 | 写入 Milvus | `ingest.py:391` | 生效 |
| 10 | 图索引构建 | `ingest.py:397-405` | **死码**，`graph_builder` 从未注入 |

### 1.2 检索阶段 `retrieval/pipeline.py:172-474`

| # | 阶段 | 代码位置 | 开关 | 生产状态 |
|---|---|---|---|---|
| 0 | 租户解析 | `:197` | 无（强制） | 生效 |
| 1 | 复杂度门控 + 改写 | `:202-227` | `complexity_gate_on` | **生效** |
| 2 | 意图路由 | `:226` | 无 | 生效但下游无消费者 |
| 3a | HyDE | `:236-249` | `hyde_on` | 死（未注入） |
| 3b | 查询嵌入 | `:251-253` | 无 | 生效 |
| 4 | 租户 + ACL 过滤表达式 | `:258-274` | `acl_filter_on` | **生效** |
| 5 | 混合检索（取 `top_k*2`） | `:276-285` | ~~`hybrid_search_on`~~ | **无条件执行** |
| 6 | SubQueries 扇出 | `:296-358` | `subqueries_on` | 死（未注入） |
| 7 | Stepback | `:363-388` | `stepback_on` | 死（未注入） |
| 8 | MMR 多样性 | `:393-400` | `source_diversity` | **生效** |
| 9 | 图谱分支 | `:405-415`,`:479-543` | `graph_retrieval_on` + 路由 | 死（未注入，永远降级） |
| 10 | 重排 | `:420-437` | ~~`rerank_on`~~ | **无条件执行** |
| 11 | SentenceWindow 父块回取 | `:446-462` | `sentence_window_on` | 死（未注入） |
| 12 | 裁剪到 `top_k` | `:467` | 无 | 生效 |

### 1.3 生成与验证阶段

- 生成：`generation/generator.py`，受熔断保护（`rag.py:152-179`）。
- 引用验证三层（`verify/verifier.py`）：L1 存在性 `:253-271` → L2 文本哈希 `:276-311` → L3 蕴含判定 `:357-412`。
- 弃权门（`verify/abstention.py:53-82`）：双阈值 `retrieval_score_threshold=0.3` / `entailment_score_threshold=0.6`，在 `rag.py:267`（检索后）和 `rag.py:309`（验证后）各判一次。

**验证层不可配置**：`CitationVerifier` 的 `entailment_mode` / `strict` / `sample_ratio` 在 `rag.py:126-128` 全部**硬编码**为 `"llm"` / `True` / `1.0`，`Settings` 里根本没有 `verify` 段。降本用的抽样（`sample_ratio`）和跳过（`entailment_mode="skip"`）实现完整、smoke 测试覆盖，但配置层够不到。**结果是每次问答都要对每条声明做一次完整的裁判 LLM 调用**——这是系统里最大的一笔隐性开销。

---

## 2. 策略关系矩阵

### 2.1 互斥（单选，UI 应为 Radio / Segmented）

| 组 | 可选值 | 代码依据 |
|---|---|---|
| 向量化服务来源 | `api` / `local` | `core/embedding.py:217-218`，`ProviderRegistry.create` 单选 |
| 重排服务来源 | `api` / `local` | `core/reranker.py:305-306` |
| 来源多样性 | `off` / `group_only` / `group_mmr` | `retrieval/pipeline.py:260`,`:394` |
| 文档切分模式 | `auto` / `recursive` / `parent_child` / `qa` | `indexing/chunking_router.py:60-73`，**每篇文档只决策一次**，三种切分器不可能在同一文档内共存 |
| 解析引擎 | `auto` / `mineru` / `docling` | `indexing/parsers/registry.py:116-140` |
| MinerU 调用方式 | `cli` / `http` | `indexing/parsers/plugins.py:16-22` |
| 各引擎运行模式 | mineru `free`/`paid`；docling `local`/`api` | `plugins.py:15`,`mineru_cli.py:74`,`docling_parser.py:56` |
| 编排执行方式 | 图编排 / 顺序执行 | `rag.py:371` `graph_engine_on` |

> 注意：`source_diversity` 没有「只要 MMR 不要分组」这个选项——`group_mmr` = Milvus 分组 **加** 客户端 MMR。`mmr_lambda` 在非 `group_mmr` 时完全惰性。

### 2.2 可共存（多选，UI 应为 Switch）

- **查询端三兄弟**：`hyde_on` + `subqueries_on` + `stepback_on` 可同时开启，互不冲突。HyDE 只替换稠密向量（`pipeline.py:252`），BM25 仍用原查询（`pipeline.py:280`）；SubQueries 与 Stepback 都是按 `chunk_id` 去重后**追加**候选（`_merge_extra:554-576`）。
- **解析增强四开关**：`router_on` / `pdf_inspector_on` / `tsr_on` / `rotation_on`。
- `acl_filter_on` 与租户隔离并存（租户过滤无条件启用，`pipeline.py:268-270`）。

### 2.3 依赖（前置条件，UI 应显示「需先启用 X」并禁用）

| 策略 | 前置条件 | 代码依据 |
|---|---|---|
| **SentenceWindow 父块回取** | 必须 `chunking_mode = parent_child` | 只有 `StructureAwareChunker` 写 `parent_chunk_id`（`chunker.py:144-155`）；`chunker_recursive.py:162` 和 `chunker_qa.py:159` 硬编码 `None`。用 recursive/qa 入库的文档，开了这个开关也是静默空转（`sentence_window.py:82-84` 取不到子块） |
| **图谱检索** | ①入库期建过图 ②路由命中 `vector_graph_rag`/`full` ③注入 `GraphRetriever` | `pipeline.py:406`,`:505`；`passage_ids` 要能回查到真实 chunk（`pipeline.py:521`） |
| **路由命中图谱目标** | 复杂度门控**没有**短路 | 门控短路时 `pipeline.py:210` 强制 `route="hybrid"`，图谱分支永不进入 |
| **PDF 快速通道** | 必须 `router_on` 同时开启 | `router.py:75` 是 AND 关系，任一关闭都禁用快速通道 |
| **TSR 表格还原** | 需要解析结果带 `layout` | MinerU 不返回 layout（`mineru_cli.py:109-118`），故 TSR 只在快速通道/Docling 上生效 |
| **MinerU OCR/表格/公式开关** | 仅 `provider = http` 时生效 | `mineru_http.py:96-98` 读；`mineru_cli.py:44-46` 不读。默认 `provider=cli`，这三个开关是惰性的 |
| **api 类服务** | 需配置对应 `api_key` | `reranker.py:281-285` 快速失败；`embedding.py:185` 延迟到请求时失败 |

### 2.4 相互削弱（可共存但会打架，UI 应给出提示）

| 组合 | 后果 | 代码依据 |
|---|---|---|
| 复杂度门控 + 图谱检索 | 门控对「短且具体」的查询短路 → 强制 hybrid → **图谱分支永不触发** | `rewrite.py:63-72` 判定 + `pipeline.py:210` |
| SubQueries/Stepback + `group_mmr` | 增强刚扩大候选，MMR 立刻截回 `top_k*2`，增益被削 | `pipeline.py:398` |
| SentenceWindow(`replace=True`) + 引用验证 | 父块替换命中子块，改变验证器看到的证据集；且 `replace` 根本没暴露成配置项 | `sentence_window.py:57`；`pipeline.py:446-462` 在重排后、裁剪前 |
| 图谱分支 + 重排失败 | 图谱 chunk 以 `score=0.0` 注入（`pipeline.py:605`），重排若抛 `RerankError` 则 0 分进入弃权门，可能误判为「知识库无相关内容」 | `pipeline.py:435`,`rag.py:264` |

**另一条隐性耦合**：`gate_skipped` 取自改写返回的 `changed` 而非「是否具体」（`pipeline.py:205`）。改写 LLM 挂掉时降级返回 `(query, False)`（`rewrite.py:113-115`），与「查询本来就具体」不可区分，两者都跳过路由。**即改写槽位的 LLM 故障会静默关掉图谱能力。**

---

## 3. 配置项生效状态审计

> 以下为 2026-08-20 装配修复**之后**的状态。

### 3.1 真正生效
`hybrid_search_on`、`rerank_on`（本轮补上判断）、`complexity_gate_on`、`acl_filter_on`（仅当调用方传了 `acl`）、`source_diversity` 及 `group_size`/`group_by_field`/`mmr_lambda`、`top_k`、两个弃权阈值、`graph_engine_on`、`hyde_on`、`subqueries_on`、`stepback_on`、`sentence_window_on`、`graph_retrieval_on`、`enhance_candidate_k`、`chunking_mode`、`simple_doc_max_chars`、`clean_on`、`contextual_on`、`graph_index_on`、整个 `graph` 段、`tenant.*`、`retry.*`、`circuit.*`、`observability.metrics_enabled`、`parsers.router_on`/`pdf_inspector_on`/`tsr_on`/`engine`/各引擎 `enabled`/`mode`/`priority`、`mineru.provider`、`catalog.db_path`/`db_url`、`embedding.provider`、`reranker.provider`。

### 3.2 声明了但代码从不读取（纯死项）
**已全部处理**：

| 字段 | 处理方式 |
|---|---|
| `milvus.ef` | **已接入**。HNSW 检索期候选宽度，下发到稠密分支检索参数；并对 `candidate_limit` 取上界，满足 Milvus 的 `ef >= limit` 约束（`top_k` 调大时自动抬高，不再直接报错） |
| `mineru.page_limit` | **已接入**。在 `DocumentRouter.parse` 解析前拦截；页数来自 pdf-inspector 分类阶段顺带产出的 `page_count`，不额外解析一遍。`<=0` 表示不限制 |
| `observability.tracing_enabled` | **已接入**。`core.tracing` 新增进程级开关，关闭后 `current_trace()` 全程返回 None，各阶段 span 记录与 span→metrics 同步一并短路；用户可见的 `QueryResult.traces` 不受影响 |
| `embedding.dim` | **已移除**。与 `milvus.dim` 重复且从不生效，留着只会误导（真正决定集合维度的是后者） |
| `milvus.consistency_level` | **已移除**。从未下发给任何 Milvus 调用；如需按级别读写应在 search / query 调用处显式传参并针对目标版本验证后再放开 |
| `parsers.rotation_on` | **保留但如实标注**。它确实会写进解析元数据供 vision 引擎消费，只是内置 MinerU 引擎尚未消费；配置说明已改为写明这一点，不再宣称「择优校正」 |

### 3.3 仅在非默认路径下生效
`mineru.enable_ocr` / `enable_table` / `enable_formula` —— 只有 `mineru.provider = http` 时才被读；默认 `cli` 下惰性。

### 3.4 工厂函数存在但无人调用（**未修复**）
`catalog.auto_filter_on`（`retrieval/auto_filter.py`）、`catalog.auto_tag_on`（`indexing/auto_tagger.py`）。两个模块的 docstring 都声称「已注入管线」，与实际不符。

### 3.5 验证层（本轮新增可配置）
新增 `verify` 段：`entailment_mode`（`llm` / `skip` / `nli` 预留）、`strict`、`sample_ratio`，两个构造点（`rag._build_pipeline` 与 `verify.create_verifier`）统一经 `resolve_verify_settings()` 读取。该函数对非法值兜底为最严格（`llm` + 全量），保证配置写错只会退化为「更慢但更严」，不会让服务起不来。

**这是系统里最大的一笔隐性开销**：`llm` 模式下每条声明都要调一次裁判模型。`skip` 可完全省掉，但同时会关闭验证阶段的弃权判定（没有蕴含分数产出），只剩检索分数一道闸——降本与抗幻觉需要显式权衡，设置页已就此给出提示。

### 3.6 LLM 槽位实际消费情况
`LlmSlotsSettings` 定义 **11 个槽位**（docstring 已在本轮更正）。

- **有真实调用**：`rewrite`、`router_llm`、`generation`、`judge`，以及本轮接通后按开关触发的 `hyde`、`subqueries`、`stepback`、`contextual`、`triplet`
- **仍无消费者**：`classifier`（自动打标签）、`metadata_filter`（自动过滤）——对应的两个工厂函数无人调用

---

## 4. 剩余修复路径

装配缺口已于 2026-08-20 修复（见 §0）。同日第二轮补齐了：配置热更新、`verify` 段可配置、`milvus.ef` / `mineru.page_limit` / `observability.tracing_enabled` 接入、`embedding.dim` 与 `milvus.consistency_level` 移除。

**仍待处理：**

1. `retrieval/auto_filter.py` 与 `indexing/auto_tagger.py` 的工厂函数无人调用，两个 `catalog.*_on` 开关仍是空的。接入前需要先明确「按 dataset 的元数据 schema 从哪来」，属于设计问题而非接线问题。
2. `parsers.rotation_on` 只写进解析元数据，内置 MinerU 引擎未消费。要么在 vision 引擎侧真正实现方向校正，要么删掉该开关。
3. `mineru.enable_ocr` / `enable_table` / `enable_formula` 仅 `provider=http` 生效，默认 `cli` 下惰性——设置页应据此做条件禁用（同 §2.3 的依赖表达）。
4. 桥服务自身的并发 / 队列 / 缓存参数（`bridge` 段）仍需重启才生效：它们在模块导入时读入常量，热更新不覆盖。

**关于热更新的边界**：`/api/config/update` 现在会调用 `rag.reset_pipeline(close=False)` 与 `documents.reset_ingest_pipelines()`。前者只置空单例引用、不关闭旧组件的连接——在途请求仍持有旧组件，可安全跑完，代价是极少数情况下短暂多留一个 Milvus 连接。后者用代次计数器，让各入库工作线程在下次取用时自行重建。

**这段描述在 2026-08-22 之前是"设计意图"而不是事实**。拆卸与重建的机器一直都在、也一直正确，但链路上缺了一环：`Settings` 的取值来源**只有** `os.environ`（`config.settings.Rag4cEnvSource`），而写入只落到了 `.env` 文件。于是缓存确实清了、管线确实重建了，然后照着一模一样的旧 `os.environ` 又装配了一遍——症状与"什么都没做"完全一致，而接口对每一次保存都回 `hot_reloaded: true`。现在补上了三件事：

1. 把本次改动的键写进 `os.environ`（只写改动的键，不做整份 `load_dotenv(override=True)`——后者会把容器 / CI 注入的真实环境变量按文件内容一并覆盖）；
2. 写入路径改用 `config.settings.resolve_env_file()`，与读取方看同一个文件。从前写死项目根 `.env`，而读取优先 `cwd/.env` 并支持 `RAG4C_ENV_FILE`：只要服务不是从项目根启动，配置页就在写一个没人读的文件；
3. `hot_reloaded` 改为**回读核实**——重建后从新的 `get_settings()` 把每个改动路径读回来比对，一致才算数，做不到的列进响应的 `needs_restart`。这个字段从前的含义只是"重建那段没抛异常"，与生效与否无关，它正是靠这一点在缺陷存在的整段时间里一直报成功的。

回归测试见 `tests/test_config_hot_reload.py`（14 条）。反向验证：还原缺陷后 8 条转红，其中带空格的字符串字段专门盯着 `.env` 字面量（要引号）与 `os.environ` 裸值（不能带引号）两套序列化不许混用。

---

## 5. 对设置页设计的要求

1. **按阶段组织，不要平铺表格**。管线是有顺序的：入库（解析 → 清洗 → 切分 → 上下文增强 → 嵌入 → 图索引）、检索（门控 → 改写 → 路由 → 查询增强 → 召回 → 多样性 → 图谱 → 重排 → 父块回取）、生成验证（生成 → 三层验证 → 弃权）。设置项应挂在它所属的阶段上。
2. **互斥用单选、共存用开关**，见 §2.1 / §2.2。切分模式、多样性策略、服务来源这些绝不能做成多个独立开关。
3. **依赖关系要显式表达**：开启 SentenceWindow 时若切分模式不是 `parent_child`，必须提示「当前切分模式下不会生效」；开启图谱检索时若未建图，同理。
4. **相互削弱要给提示**，见 §2.4，尤其是复杂度门控与图谱检索的冲突。
5. **诚实标注生效状态**。§3.2/§3.3/§3.4 里的配置项，在修好之前不应该在 UI 上表现得像能用——否则用户改了没反应，比不给这个开关更糟。
6. **说明配置何时生效**：目前除 `graph_engine_on` 外都需重启后端。

---

*生成时间：2026-08-20。若后续修改了 `rag.py::_build_pipeline` 或 `server/documents.py::_get_ingest_pipeline` 的参数，本文档 §0 与 §3 需同步更新。*
