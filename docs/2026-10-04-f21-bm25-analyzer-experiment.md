# F2.1 BM25 分析器假设实验：假设被证伪

日期：2026-10-04
计划条目：`docs/plans/2026-09-29-rag4c-master-plan.md` F2.1
数据：`eval/.cache/f21-before.json` / `f21-after.json`（评测集合 `rag4c_eval_chunks`，227 行，23 条可答 + 3 条不可答）

## 结论

**「英文子集 hybrid 追不上 dense_rerank 是因为 BM25 用错了分析器」这个假设不成立。**

在评测集合上把 `text` 字段的 `analyzer_params` 从 Milvus 默认（standard）
重建为 `{"type": "chinese"}` 之后，全部指标**逐位不变**：

| 策略 | 指标 | 重建前 | 重建后 | delta |
|---|---|---|---|---|
| dense_rerank | ndcg@10 | 0.7193 | 0.7193 | +0.0000 |
| dense_rerank | recall@10 | 0.8261 | 0.8261 | +0.0000 |
| dense_rerank | mrr@10 | 0.7251 | 0.7251 | +0.0000 |
| hybrid_rrf | ndcg@10 | 0.7135 | 0.7135 | +0.0000 |
| hybrid_rrf | recall@10 | 0.8261 | 0.8261 | +0.0000 |
| hybrid_rrf | mrr@10 | 0.7254 | 0.7254 | +0.0000 |

逐用例看：**26 条里只有 1 条的结果列表发生变化**，且 NDCG 未变（gold 的排名
区间没被跨过）。也就是说这次重建在检索质量上的影响接近于零。

计划明确写了「**假设被证伪也是有效结果**，直接记录」——本条即该记录。

## 重建确实生效了（排除"改了但没生效"）

一个 delta 全零的结果，第一反应应该是"改动没生效"，而不是"假设被证伪"。
以下三条证据排除了前者：

1. **schema 层确认**：`describe_collection` 返回
   `text.params = {'max_length': 65535, 'enable_analyzer': 'true', 'analyzer_params': '{"type":"chinese"}'}`。
   （注意 `analyzer_params` 在 `params` **嵌套字典**里，不在顶层——顶层读到
   `None` 是查法错了，不是没生效。这个坑让我先误判了一次"重建没成功"。）
2. **BM25 稀疏检索直接可用**：重建前后的集合上直接打
   `anns_field="sparse_vector", metric_type="BM25"`，中文查询
   `'RRF 融合'` / `'倒排索引'` 均正常返回 3 条结果。
3. **结果确实变了**：1 条用例的 top5 顺序变了。

## 那 BM25 之前坏在哪

有意思的是：**这个评测集合上 BM25 之前并没有坏。**

`config/settings.py` 里 `bm25_analyzer` 的注释记录了一个真实故障：standard
分析器按空白与标点切词，中文整句被切成极少数无用 token，导致中文查询 BM25
召回恒为 0，混合检索静默退化成纯稠密检索。注释里的实测是"standard top1=3/6，
chinese top1=6/6"。

本次实验没复现出这个差异，原因在评测语料本身：

- `rag4c_eval_chunks` 装的是 **RAG4C 仓库自己的文档**（227 行 / 8 个文档），
  而仓库文档大量是**中英混排的代码与技术说明**（中文字符占比中位 0.233，
  英文为主 22 条、中文为主 11 条）。这种语料里查询词本身多为英文标识符
  （`hybrid_search`、`RRFRanker`、`analyzer`），standard 分析器按空白切词
  本来就能切对。
- 真正"中文整句被切不开"的场景是**纯中文查询 + 纯中文文档**。本评测集
  不满足这个条件。

所以本实验的结论要严格限定口径：**它证伪的是"在这套中英混排的仓库文档上，
BM25 分析器是英文子集的瓶颈"，不是"BM25 分析器在纯中文语料上不重要"。**
后者已由 `config/settings.py` 注释里的实测（3/6 → 6/6）单独证实。

## 一个副产品：BM25 稀疏路的信号强度

对比"不可答用例的 top-score 均值"这个降级率指标：

| 策略 | 重建前 | 重建后 |
|---|---|---|
| dense_rerank | 0.3801 | 0.3801 |
| hybrid_rrf | 0.0191 | 0.0191 |

hybrid 的噪声置信度比 dense 低 20 倍（0.019 vs 0.380）。这说明 RRF 融合确实
把一路"噪声分低"的信号掺了进去——稀疏路对明显离题的问题给出了接近 0 的分。
这是 hybrid 在弃权判定上的一个正面特性，与本实验结论无关，但值得记下。

## 过程中修掉的四个真实缺陷

实验本身要"在评测集合上重建 BM25"，而这条路径此前**根本走不通**——
四个缺陷依次挡路，每一个都是「配了不生效」或「静默失效」型：

### 1. `--gold` 参数从未被消费（`eval/retrieval_eval/runner.py`）

`--corpus milvus` 会**隐式强制**切到 `PRODUCTION_GOLD`，`--gold docs` 传了
等于没传。参数进了配置、也写进了报告元信息，唯独 `runner` 从头到尾只 import
`gold.py` 的 `GOLD_SET`。

后果：拿评测集合跑实验时，只能用生产标注去对评测集合的 chunk id，必然报
"黄金标注已失效"——一条看起来像"环境配错了"的报错，实际是选择器没接上。
**在此之前所有标注为 `--gold production` 的实验其实都跑的是 docs 标注。**

修法：把 `gold` 与 `corpus_source` 拆成两个独立维度。

### 2. docs 标注 + Milvus 语料会 AttributeError（同上文件）

修好第 1 条后暴露：docs 分支调 `index.chunk_ids()`，那是 `LocalVectorStore`
的 API，`MilvusVectorStore` 根本没有该方法（它只有按主键批量查的
`query_ids`）。修法：按语料来源分两路校验，Milvus 侧只核对待用的主键
（生产库很大不能全量扫）。

### 3. `rebuild_collection.py` 写死的导出字段清单会随 schema 漂移

`_EXPORT_FIELDS` 是硬编码列表，漏了后加的 `document_revision` /
`content_revision`。后果比"指标不对"严重得多：流程是「导出 → drop → 重建 →
回填」，回填那一步每行都缺必填字段、整批被拒（`InsertMissedField`），
**而集合已经被 drop 了**。备份在、但恢复要绕路。

修法：`_export_fields()` 改为从集合 schema 动态取（排除不可写的
`sparse_vector`），`_normalise()` 改为按目标 schema 补齐缺失字段
（标量补空串、整型补 0）而不是只做白名单过滤。

### 4. `--collection` 没有真正改配置，导致在错误的集合上执行

新加的 `--collection` 只改了重建函数里的**局部变量**，而
`ensure_collection()` 与回填都从 `settings.milvus.collection_name` 取集合名。
于是评测集合被 drop 掉了，新集合却建在**生产集合的名字**上。

修法：改 `settings.milvus.collection_name` 本身（仅本进程内）。

### 附带修掉：`--restore-from` 的语义是「重建」而不是「追加」

恢复分支只 `ensure_collection()`（集合已存在时幂等跳过）然后 insert，
于是 analyzer 不会变（schema 属性改不了）、旧行还在，再 insert 一次就变成
2N 行——实测确实出现了 454 行（227×2）。

修法：恢复时先 drop再建，并在结束时核对集合实际行数与回填行数是否一致，
不一致就明确告警。

## 复跑方式

```bash
# 1. 重建前基线
python eval/run_retrieval_eval.py --store milvus --corpus milvus \
  --milvus-collection rag4c_eval_chunks --gold docs --embedder api \
  --strategies dense_rerank,hybrid_rrf --top-k 10 --candidates 30 \
  --out eval/.cache/f21-before.json

# 2. 重建评测集合（生产集合 rag4c_chunks 全程只读）
python scripts/rebuild_collection.py --collection rag4c_eval_chunks \
  --analyzer chinese --yes

# 3. 重建后测量（同一命令，只换 --out）
```

备份留在 `backups/rag4c_eval_chunks-20261003T212436Z.json`（227 行，
执行前自动生成）。

## 对后续任务的影响

- **F2.3（英文子集失败归因）** 现在是主线：英文 dense_rerank 0.735 vs
  中文 0.923 的差距**不是**分析器造成的，要往切分/嵌入/标注三个方向查。
  本实验把"检索器配置"这一项排除掉了。
- **生产集合 `rag4c_chunks` 的 analyzer 仍是 None**（standard）。按
  `config/settings.py` 注释里的实测，标准确会让中文查询 BM25 召回归零。
  但重建生产集合是**独立决策**（计划注明"F5 之后的单独决策"），本次未动。
  建议在 F5 之后安排一次生产集合重建，届时中文查询的 BM25 召回应有实测可证。
- **F2.2（默认检索策略决策）** 不受本实验影响：hybrid 与 dense_rerank 在这
  套语料上 NDCG 基本持平（0.7135 vs 0.7193，差 0.006），且 hybrid 的 p50
  延迟低 5.8 倍（454ms vs 2647ms）。
