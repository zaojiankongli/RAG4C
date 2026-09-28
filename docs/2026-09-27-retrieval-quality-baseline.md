# 检索层质量基线与候选系数配置化（2026-09-27）

## 0. 先说一个事实差异（工作区 vs 代码仓）

本轮会话的工作区是 `D:/program_project/RAG4C`，但那里**只有 3 份 handoff 文档、
1 个前端测试文件和一份 clone 失败的日志，没有任何代码**，无法评测任何模块。
真正的代码仓在 `D:/program_project/python_project/RAG4C`（有 `core/` `retrieval/`
`eval/` `tests/` 与完整 git 历史，且工作区那 3 份文档是它的子集副本）。
按"文档与代码不一致时以代码为准"的口径，本轮全部改动落在**代码仓**，
本文同时同步一份到工作区 `docs/`（见文末"文档同步"）。

## 1. 本轮目标

| 项 | 内容 |
|---|---|
| 优化什么 | 检索模块的**召回率 / 准确率 / 相关性排序**的可测量性，以及一个被写死的关键旋钮 |
| 当前基线 | **没有任何检索层指标**——`eval/` 只测答案层（拒答率/幻觉率/有据性/相关性/引用失败率），检索质量全靠主观感觉 |
| 验收标准 | ① 一键命令跑出可复现的 Recall@K / P@K / MRR / NDCG；② 离线（零网络零成本）测试常驻；③ 真实模型基线数据落盘；④ 找出并配置化一个写死的关键参数 |
| 涉及模块 | `eval/`（新增评测包）、`retrieval/stages.py`、`config/settings.py` |
| 本轮不做 | 不动生产检索行为（默认参数保持原值）；不接 BM25 分支；不改弃权阈值；不碰未完成的扩展轴切片 |

## 2. 调研结论

1. **指标缺口**：`eval/run_eval.py` 的 6 个指标全部是答案层，检索层一个都没有。
   `docs/性能实测与升级计划.md` 里"真实质量对标需真实嵌入端点"这条遗留，
   现在其实具备条件了：`.env` 里 SiliconFlow（bge-m3 / bge-reranker-v2-m3）
   与 DashScope 的密钥都在，实测连通（401=可达）。
2. **环境约束**：`192.168.100.128` 的 Milvus / MySQL 不可达，Ollama 未启动，
   Docker 不可用，Milvus Lite **不支持 Windows** → 端到端检索跑不起来。
   所以评测用**本地 numpy 余弦向量库**替掉 Milvus 的检索算术，
   但**重排器用生产同一个**（真实 bge-reranker-v2-m3），排序收益因此可信。
3. **没有独立的"记忆模块"**：本仓没有 conversation memory 组件，
   最接近的是两级查询缓存 / 生成缓存 / LRU（`core/query_cache.py`、
   `core/two_level_cache.py`、`core/generation_cache.py`）。
   "记忆提取准确率 / 重要性权重 / 召回时机"这一组指标**在当前架构下无对象可测**，
   登记为后续议题（见 §8）。
4. **写死的旋钮**：`retrieval/stages.py` 向 Milvus 请求候选数用的是硬编码
   `top_k * 2`；而这件事恰好决定"重排能看到多少候选"，是质量/成本的核心旋钮。

## 3. 设计方案

新增 `eval/retrieval_eval/`（低耦合、可插拨、配置驱动）：

| 模块 | 职责 |
|---|---|
| `metrics.py` | 纯函数指标：Recall@K / P@K / MRR@K / NDCG@K（二值相关），无副作用 |
| `corpus.py` | 把仓库文档按标题+滚动窗口切成确定性语料（227 切片，带指纹） |
| `gold.py` | 人工标注评测集：23 条可答 + 3 条不可答，每条注明为什么算命中 |
| `store.py` | `LocalVectorStore`（numpy 余弦，实现生产 `hybrid_search` 端口）+ 嵌入磁盘缓存 + 离线伪嵌入 |
| `runner.py` | 策略注册表（`core.providers.ProviderRegistry`）+ 执行循环 + 报告/基线对比 |

关键取舍：

- **不支持的东西直接报错**（BM25 `query_text`、过滤表达式），不静默退化——
  静默退化会让指标好看但不可信。
- **策略是扩展轴**：新增策略只需 `STRATEGY_REGISTRY.register(...)`，
  执行循环零改动（有测试钉住这一点）。
- **语料是代理**：生产语料（98 篇文档）连不上，用仓库稳定文档代替，
  换语料必须重跑基线（指纹变了会强制失效标注校验）。

## 4. 执行内容

新增：
- `eval/retrieval_eval/{__init__,metrics,corpus,gold,store,runner}.py`
- `eval/run_retrieval_eval.py`（CLI：`--embedder api|hashing --strategies … --baseline … --fail-under …`）
- `tests/test_eval_retrieval.py`（18 条离线测试）
- `tests/test_search_candidate_factor.py`（6 条守卫测试）

改动（行为等价）：
- `config/settings.py`：`PipelineSettings.search_candidate_factor: int = 2`
- `retrieval/stages.py`：新增 `candidate_request_count(p)`，替换两处 `top_k * 2`
- `retrieval/pipeline.py`：文档串同步

## 5. 验证结果（真实 bge-m3 + bge-reranker-v2-m3，227 切片 / 23 可答 + 3 不可答）

| 策略 | recall@5 | recall@10 | P@5 | MRR@10 | NDCG@10 | 延迟 p50 |
|---|---|---|---|---|---|---|
| dense（无重排） | 0.797 | 0.833 | 0.217 | 0.609 | 0.658 | 124ms（含真实嵌入） |
| dense + 重排（候选 20） | 0.783 | 0.783 | 0.226 | 0.721 | 0.706 | 397ms |
| **dense + 重排（候选 30）** | 0.783 | **0.826** | 0.226 | **0.725** | **0.719** | 443ms |
| dense + 重排（候选 50） | 0.739 | 0.826 | 0.209 | 0.723 | 0.713 | 758ms |
| dense + MMR（λ=0.7） | 0.659 | 0.812 | 0.191 | 0.580 | 0.622 | ~1ms |

- 离线伪嵌入（CI 用）：dense NDCG@10 = 0.306 / MRR@10 = 0.288 —— 只有字面相似性，
  **不能与真实嵌入横向比**，只用来验证链路。
- 重排收益：**MRR +19%、NDCG +9.3%**，代价 rerank p50 442ms / p95 577ms / p99 895ms。
- 候选池：20→30 有收益（NDCG +1.8%、recall@10 +5.5%），30→50 **反而变差**
  且延迟翻倍 → 3 倍附近是甜点，5 倍不划算。
- MMR 多样性在本评测集上是**负收益**（-5.5% NDCG）：gold 常集中在同一篇文档，
  文档级去重正好把正确段落排掉。默认 `source_diversity="off"` 是对的。
- 不可答用例首条余弦均值：无重排 0.433 / 重排后 0.380（重排顺带压低了噪声置信度）。
- 测试：新增 **24 条全绿**；`ruff` 全绿；检索相关回归
  （`test_retrieval_stage_registry` / `test_optional_strategy_assembly` /
  `test_phase6_graph_retrieval` / `test_qa_retrieval_*` / 删除栅栏）**全通过**。

## 6. 成本

- 一次性：227 切片嵌入（已落盘缓存 `eval/.cache/`）+ 23 条查询嵌入。
- 每轮重排：23 次调用 × 30 条文档。缓存命中后重跑**零嵌入成本**，
  只有重排是每轮真实付费。
- 配置改动本身零成本（默认 2 = 改造前行为）。

## 7. 风险与回滚

- 风险：`candidate_request_count()` 用 `getattr` 兜底，字段缺失/非法值退回默认 2，
  **不会因为一个旋钮让检索失败**（有测试）。
- 回滚：`git checkout -- config/settings.py retrieval/stages.py retrieval/pipeline.py`
  即可恢复硬编码行为；评测包是纯新增，删除 `eval/retrieval_eval/` 与两个测试文件即可，
  不侵入任何生产路径。

## 8. 已知限制与遗留

1. 语料是仓库文档的**代理**，不等于生产语料；换语料/换嵌入模型必须重跑基线。
2. 本地向量库只有稠密分支，**BM25 混合分支未纳入评测**（生产默认开）。
3. 多样性阶段的 `mmr_select(k=top_k*2)` 仍是硬编码（语义不同，本轮未动）。
4. 无"记忆模块"可测：相关指标（提取准确率/重要性权重/召回时机）
   在当前架构下没有对象，需要产品层面先定义。
5. **全量测试在本机跑不动**：外部依赖（Milvus/MySQL）不可达时，
   `pytest tests/ -n 4` 跑了 15 分钟只到 13%（大量连接超时堆积）。
   这会拖垮反馈循环，建议下一轮优先处理（跳过/降级外部依赖或加超时）。

## 9. 下一步（按优先级）

| # | 事项 | 依据 |
|---|---|---|
| P1 | 给依赖外部服务的测试加"不可达即跳过/降级"或全局超时，把全量测试拉回分钟级 | 本轮实测 15min/13% |
| P2 | 在生产配置里试 `search_candidate_factor=3`，用同一评测复测并看端到端延迟 | 本轮 NDCG +1.8% / recall@10 +5.5% |
| P3 | 给单次问答补 token/成本台账（现在只有全局 `llm.tokens.*` 计数，无法回答"这一问花了多少"） | 用户优先级 1：Token 消耗与单次成本 |
| P4 | 把 BM25 分支接进评测（本地实现一个稀疏检索器），测真实"混合"收益 | 生产默认 hybrid_search_on |
| P5 | 继续未完成的后端扩展轴（durable deletion / consistency projection） | `.planning/2026-09-23-extensible-backend/task_plan.md` |

## 10. 文档同步

本文同时复制一份到工作区 `D:/program_project/RAG4C/docs/`，
让在工作区里接手的下一个会话也能看到同一份基线（工作区本身没有代码可执行）。

复现命令::

    # 真实模型基线（需要能连 .env 里的嵌入/重排端点）
    python -m eval.run_retrieval_eval --embedder api --strategies dense,dense_rerank --candidates 30 \
        --out eval/.cache/retrieval-report-api.json --baseline eval/.cache/retrieval-baseline.json

    # 零成本离线自测（CI）
    python -m eval.run_retrieval_eval --embedder hashing --strategies dense --no-rerank
