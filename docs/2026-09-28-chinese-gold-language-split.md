# 中文标注 4→44 条 + 按语料语言分组复测混合检索（2026-09-28，Round 9）

## 0. 本轮回答的是 Round 8 §6 的第 2、4 条

Round 8 留下的两件事是一件事：中文语料只有 4 条标注，所以"混合检索更差"这个结论
**从没在中文上单独看过**。本轮先把中文标注补起来，再把"按语料语言分组"做成读数的
一部分，而不是临时切一次子集。第 1 条（生产泡够时间看 P95/弃权率）本轮做不了，
第 3 条（降级率告警阈值）留给下一轮。

## 1. 先纠正上一轮登记的两处事实（都回库实测过）

| 上一轮的说法 | 实测 | 后果 |
|---|---|---|
| 「中文一半的语料还没被充分测到」 | 生产集合 `rag4c_chunks` **9723 行 / 646 篇文档**里，CJK 占比 >8% 的只有 **121 行 = 1.24%**，分布在 **26 篇文档**；其中 ≥250 字的仅 **63 行** | 中文子集的天花板就是几十条量级，不是"一半"。补到 44 条已经吃掉了可用料的大半（63 条 ≥250 字的候选里，代码输出型片段不能当 gold） |
| 不可答用例「公司年假的申请流程…」标着"语料不涉及人事制度" | `text like "%年假%"` 命中 **2 条**中文切片，内容就是年假制度与报销流程（两份内容相同，4-gram 重合度 **0.940**） | 这条问题其实**有答案**，当不可答用例用会把一次正确检索记成噪声。已换成实测 `like` 命中 0 条的公积金主题，并在 notes 里写明原委 |

顺带发现：生产集合里混着这两份**演示文档**（RAG4C 企业知识库：报销 / 年假 / 混合检索 / 引用验证）。
本轮**没有**拿它们做标注——把演示语料标成 gold 等于给它发合格证，见 §11 的"不要引入 demo 数据"。
但它是否该留在生产集合里，是个要裁定的问题（见 §7 第 1 条）。

## 2. 语言维度收成一条声明，不是一堆 `if`

`eval/retrieval_eval/gold.py`：

- `CORPUS_LANGUAGES = ("en", "zh")`、`CJK_RATIO_FOR_ZH = 0.08`、`CJK_START/CJK_END` —— 判定只有一处定义；
- `GoldCase.corpus_language` 新字段（默认"未声明"）；
- `cjk_ratio()` / `detect_corpus_language()` —— 只按字符数判，不分词；
- `validate_corpus_languages(cases, texts)` —— **拿真实原文**核对声明，四种红法都点名到用例：
  未声明 / 词表外 / 与原文不符 / 一条用例内部 gold 切片语言就不一致（该拆用例，不该硬归一组）。
  **拿不到原文也算红**：核对不了就不能默认声明没错。

`runner.py` 在 `corpus_source == "milvus"` 分支里、**任何检索之前**跑这条校验；
不可答用例没有 gold 切片，语言维度对它无意义，显式跳过。
顺手把 `gold_version` 从写死的字符串改成读 `gold_production.DATASET_VERSION`（原来两处各写一份，会漂）。

`store.py` 侧新增 `MilvusVectorStore.query_texts(ids)`（按主键取原文，只查标注用到的那些）。

报告与摘要新增 `by_language` 分组，**每组带条数**：整轮宏平均会把"某一组只有 44 条"
这件事抹平，分组数字要自己能看出样本量。

## 3. 中文标注：83 → 123 条可答（中文 4 → 44 条）

63 条候选逐条读原文写问题，最终 40 条新用例。三类 gold 集合的口径都写在 notes 里：

| 形态 | 条数 | 判定依据 |
|---|---|---|
| 单片独立回答 | 34 | 原文里那句话单独就能答完这个问题 |
| **真重复对**（≥0.65 重合） | 1 组 2 片 | `zh_cpu_simd_requirement_check`：分布式/单机版 Docker 安装页共用同一段 SIMD 前提，4-gram 重合 **0.824** → 返回任意一片都算答对 |
| 多片各自独立回答（非重复） | 2 组（3 片 + 2 片） | `zh_ivf_flat_probe_mechanism`（三片两两重合 <0.25）、`zh_normalization_definition`（术语表/距离计算/产品 FAQ 各有一份完整定义）、`zh_shard_routing_by_pk_hash` |

按 Round 7 的教训，"同文档相邻切片"**不算**等价 gold——那是接续内容不是重复，撑大分母只会压低 Recall。
重合度用 4-gram Jaccard 现算，不是眼估。

154 个 gold 切片全部在生产集合里存在（`validate_against_ids` 无缺失），
语言声明与原文核对 **0 处不符**。

## 4. 读数（ef=256、候选 30、bge-m3 + bge-reranker-v2-m3、真实 Milvus 只读）

全量 123 条：

| 策略 | recall@5 | recall@10 | precision@5 | MRR@10 | NDCG@10 | p50 | 降级 |
|---|---|---|---|---|---|---|---|
| 稠密（无重排） | 0.768 | 0.832 | 0.171 | 0.589 | 0.644 | 149ms | 0 |
| **稠密 + 重排** | **0.868** | **0.913** | 0.208 | **0.775** | **0.802** | 565ms | 0 |
| 稠密 + BM25(RRF) + 重排 | 0.835 | 0.852 | 0.197 | 0.752 | 0.768 | 535ms | 0 |

**按语料语言分组**（这才是本轮要看的东西）：

| 组 | 条数 | 策略 | recall@5 | recall@10 | MRR@10 | NDCG@10 |
|---|---|---|---|---|---|---|
| 英文 | 79 | 稠密 | 0.700 | 0.757 | 0.512 | 0.568 |
| 英文 | 79 | 稠密+重排 | 0.817 | 0.884 | 0.696 | **0.735** |
| 英文 | 79 | 混合+重排 | 0.766 | 0.783 | 0.660 | 0.679（**-0.056**） |
| 中文 | 44 | 稠密 | 0.890 | 0.966 | 0.727 | 0.780 |
| 中文 | 44 | 稠密+重排 | 0.958 | 0.966 | 0.917 | 0.923 |
| 中文 | 44 | 混合+重排 | 0.958 | **0.977** | 0.917 | **0.927**（+0.004） |

**结论：混合检索"更差"集中在英文语料那一组。** 中文那组里混合不再吃亏——
NDCG +0.004、recall@10 +0.011（recall@5 持平）。前几轮"混合持续更差 -7%"是把
两组混在一起平均的结果：样本 96% 是英文，所以那个数字基本就是英文组的数字。

一个解释假设（**前提已核、因果未证**）：生产集合的 `text` 字段实测
`enable_analyzer=true, analyzer_params={"type":"chinese"}`（另有 BM25 索引
`k1=1.2 b=0.75`，稠密侧 `HNSW M=16 efConstruction=200 COSINE`，两侧 9723 行都已建完）。
英文文档走中文分词会把关键词切坏，稀疏分支于是只给英文语料添噪声——这一步是推断，
**还没证**。要证它，最直接的一条是同一批英文用例换 `analyzer_params={"type":"standard"}`
的稀疏字段再测一次；另一条是反过来看英文组里混合**命中而稠密漏掉**的个案，是否都是专有名词/参数名。

生产默认是 `hybrid_search_on = True`（`config/settings.py:713`），也就是说英文知识库
正在为这个默认值付 NDCG -7.6%。注意边界：评测里的 `hybrid_rrf` ≠ 生产管线全貌
（生产还有改写、路由、标量过滤），这条是强信号，不是线上实测。

## 5. 复现性回看：只算 Round 8 那 83 条

从本轮报告的逐用例结果里重取 Round 8 的用例子集，与 `final80-ef256.json` 逐项比：

| 策略 | NDCG@10 本轮（同 83 条） | Round 8 | delta |
|---|---|---|---|
| 稠密 | 0.580 | 0.580 | -0.000 |
| 稠密+重排 | 0.741 | 0.742 | -0.001 |
| 混合+重排 | 0.689 | 0.689 | -0.000 |

其余四项指标同样在 ±0.002 内。扩标注没有改动旧用例的 gold，所以这一列**是**可比的；
全量数字与上一轮不可比（分母从 83 变 123），报告里两组数都在，别混着引。

## 6. 中文组里没找全的个案（标注站不站得住，看这里）

| 用例 | recall@10 | 判读 |
|---|---|---|
| `zh_ecommerce_scenarios` | 0.0（重排与混合都漏） | **真 miss，不是标错**。gold 原文写的是"电子商务：以图搜图、以商品搜商品…"，问题措辞用的是"电商"。中文 BM25 也吃不到，因为词形不同（电商 ≠ 电子商务）。改措辞就能刷高这一条，但那是调数字不是调检索，本轮不动 |
| `zh_shard_routing_by_pk_hash` | 0.5（dense_rerank） | 两片 gold 只捞回一片（架构篇或术语表片），多 gold 用例按设计就该这样扣分 |

## 7. 本轮没做、下一轮该做的

1. **裁定那两份演示文档该不该在生产集合里。** 它们在库里且可被检索命中，
   用户问到"年假"就会拿到演示答案——按不变量这是数据面问题，不是评测问题。
2. Round 8 §6 第 3 条：`degraded_rate` 告警阈值（如重排降级率 >5% 持续 5 分钟）。
3. Round 8 §6 第 1 条：ef=256 泡够时间后看真实查询 P95 与弃权率。
4. 证 §4 的分词器假设（换 standard 分析器重测英文组，或做混合命中的个案归因）。
5. 中文语料本身只有 1.24%，想让中文组有统计力，**要补的是中文文档入库，不是中文标注**。
6. 已知读数坑：不可答噪声的 `top_score 均值` 在 `hybrid_rrf` 下是 **0.014**，
   和稠密的 0.47 不是一个量纲（RRF 融合分 vs 余弦），**不能横向比**。本轮只标注这个事实，没改字段口径。

## 8. 变更清单

| 文件 | 改动 |
|---|---|
| `eval/retrieval_eval/gold.py` | 语言词表 + 阈值 + `corpus_language` 字段 + `validate_corpus_languages()` |
| `eval/retrieval_eval/gold_production.py` | 标注 86→126 条（中文 4→44）、83 条老用例补齐语言声明、换掉一条假不可答用例、版本 → `2026-09-28.production.2` |
| `eval/retrieval_eval/runner.py` | 检索前跑语言校验、`by_language` 分组、报告打印分组、gold 版本号改读声明 |
| `eval/retrieval_eval/milvus_store.py` | `query_texts()` |
| `tests/test_eval_retrieval.py` | 5 条新守卫（见 §9） |
| `eval/.cache/round9-126-ef256.json` | 本轮报告 |

## 9. 反向验证

离线套件 `tests/test_eval_retrieval.py` **23 passed**（改动前 18 条）；
相邻 5 个套件（`test_milvus_store` / `test_search_candidate_factor` / `test_eval_cli_gate` /
`test_eval_report_v2` / `test_eval_report_history`）**44 passed / 1 skipped**（集成用例要显式开关才跑）；
`ruff check` 干净。

变异反打 9 发，每发都指名红在哪条用例（跑前有绿基线预检，跑完在 finally 还原并复跑）：

| 变异 | 结果 | 红的用例 |
|---|---|---|
| 删掉分组收集那一行 | 守住 | `test_by_language_grouping_...` |
| 阈值 0.08 → 0.99 | 守住 | `test_cjk_ratio_splits_...` |
| 逐行结果不带语言标签 | 守住 | `test_by_language_grouping_...` |
| 「拿不到原文」改成放行 | 守住 | `test_language_validator_names_...` |
| 校验器整体空转 | 守住 | 同上 |
| 声明与原文不符也放行 | 守住 | 同上 |
| gold 内部不一致也不报 | 守住 | 同上 |
| 一条中文用例漏声明 | 守住 | `test_every_production_case_declares_...` |
| 所有用例塞进同一组 | 守住 | `test_by_language_grouping_...` |

实库那道闸也反打了一次：把 `zh_hnsw_param_ranges` 临时改成 `en`，`run_eval` 在任何检索前
抛 `生产标注声明的语料语言与原文不符：["zh_hnsw_param_ranges: 声明 'en'，但语料原文是 'zh'"]`；
还原后复跑通过。

## 10. 复现

```bash
export RAG4C_ENV_FILE=config/.env.postgres
export RAG4C_MILVUS_URI=http://192.168.100.128:19530
export RAG4C_MILVUS_COLLECTION=rag4c_chunks
export RAG4C_MILVUS_EF=256
python -m eval.run_retrieval_eval --corpus milvus --gold production --store milvus \
  --embedder api --strategies dense,dense_rerank,hybrid_rrf --candidates 30 \
  --sleep-ms 1500 --out eval/.cache/round9-126-ef256.json

# 只跑校验、不花钱检索（strategies 给空表，几十秒内返回）
PYTHONIOENCODING=utf-8 python -m pytest tests/test_eval_retrieval.py -q
```
