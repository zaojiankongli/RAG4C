---
feature: retrieval-stage-pipeline
status: delivered
updated: 2026-09-22
branch: main
commits: 0c5a6a4..e4f6f8d（`0c5a6a4` 阶段化 + 装配点同源、`8766b79` 常驻守卫与注册期判死、`e4f6f8d` docstring 指向注册表）
---

# 切片 S-RS：检索管线阶段化（借鉴 WeKnora 的组链，不借它的真源模型）

> 前置：`chunk-lifecycle-writers`（同族扩展点手法，已在 chunk 写路径验证过一次）。
> 本切片是"后端改成可扩展式设计"的第二块，也是用户点名要借鉴 WeKnora 的"检索流程"那一块。

## [S1] Problem

### S1.1 一条 787 行的方法

`retrieval/pipeline.py` 共 1455 行，其中 `RetrievalPipeline.run()` 占 **508–1294 行**。
14 个阶段全部内联，每个阶段各自手写一遍同一套仪式：

```
if not p.<flag>:
    _skip_node("<name>", "disabled")
elif self.<component> is None:
    _skip_node("<name>", "unavailable")
if p.<flag> and self.<component> is not None:
    _start_node("<name>", ...)
    t0 = time.perf_counter()
    try: ...
    except Exception as exc: traces.append(...); _fail_node(...); _degraded(...)
    else: _complete_node(...)
    self._add_span(<name>, ms, traces)
```

`sentence_window`（`:1198-1242`）是这段形状的完整样本：6 个节点、约 45 行仪式，其中真正的
业务只有 `self.sentence_window.expand(candidates)` 加一道防御性租户二次过滤。

后果是可量化的：加一个阶段要在 `run()` 里插一大段；`_start_node/_skip_node/_fail_node`
的用法在每个阶段各写一遍，因此**任何一处漏写就会静默少一个 span 或少一条降级痕迹**——
而"降级必须可见"是这个产品的安全边界（见 `docs/审查报告与优化计划.md` 与 README 的降级语义）。

### S1.2 装配点同样硬编码

`rag.py:96-196 _build_optional_components` 用 `components = {"hyde": None, "subqueries": None, ...}`
逐个 `_try(name, factory)`，新增一个可选策略要改这个字典、改 `RetrievalPipeline.__init__`
的 15 个参数（`:235-268`，全是 `Any` 类型、无 Protocol）、再改 `run()` 的内联分支。三处。

### S1.3 WeKnora 的对应做法，以及不能照搬的部分

WeKnora（`C:\Users\饶策\Desktop\me\WeKnora-0.8.0`）用组链表达同一件事：
`types/chat_manage.go:287-316` 的 `PipelineBuilder.Add/AddIf` + 14 个 `EventType`，
每个阶段是 `chat_pipeline/` 下一个独立文件（`query_understand.go` / `search_parallel.go` /
`rerank.go` / `merge.go` / `merge_expand.go` / `filter_top_k.go` / `into_chat_message.go`），
用 `ActivationEvents()` 声明订阅、`next()` 可短路，`KnowledgeQAByEvent` 逐阶段包 span。
值得抄的是三点：**阶段的可选性用 `AddIf` 显式表达**、**每阶段一个文件一份输入输出**、
**span 与降级由驱动器统一包**。

明确不抄：它的 `ChatManage` 是上百字段的可变巨型结构、靠 `Clone()` 深拷贝护并发
（`types/chat_manage.go`），阶段之间靠共享可变状态传值。本切片用**只读输入 + 显式累积状态**，
不把阶段做成互相改同一对象。它检索分数直接进下游、无"分数量纲会随降级改变"的自觉，
而本仓有 `RetrievalResult.reranked` 这条硬语义（见 S2.4），**必须保留、不得弱化**。

## [S2] Design

### S2.1 端口与注册表（新文件 `retrieval/stages.py`）

**阶段清单与 span 名从源码提取，不得凭空命名**（`retrieval/pipeline.py` 行号为 2026-09-22 实测）：

| # | span 节点 id | 出现处 | 开关 / 前置 |
|---|---|---|---|
| 1 | `complexity_gate` | `:548` start / `:550` skip `disabled` | `p.complexity_gate_on` |
| 2 | `rewrite` | `:587` 或 `:606` start（两条来源）/ `:580` skip `gate_simple` | gate 判为简单时整体跳过 |
| 3 | `route` | `:610` / `:582` skip `gate_simple` | 同上 |
| — | *prefetch 组* | `:639 _start_enhancers`，节点 id 取自 `self._ENHANCER_NODE_IDS` | hyde / subqueries / stepback 三路并发发起 |
| 4 | `plugin.hyde.expand` | `:643`/`:645` skip，`:678` complete | `p.hyde_on` |
| 5 | `embed` | `:680`（属性 `source=hyde\|query`） | 恒开 |
| 6 | `search` | `:750`（属性 `mode=hybrid\|dense`） | `p.hybrid_search_on` 决定有无 BM25 支 |
| 7 | `plugin.subqueries.expand` | `:797`/`:799` skip，扇出 `:827-870` | 一次批量嵌入 + N 路并发 + 整批共用 deadline |
| 8 | `plugin.stepback.expand` | `:944`/`:946` skip | |
| 9 | `diversity` | `:1007` skip `off`，`:1009` start（`mode=p.source_diversity`） | |
| 10 | `graph.retrieve` | `:1034` start / `:1064` skip（带 `graph_reason`） | `p.graph_retrieval_on` + 路由目标 |
| 11 | `rerank` | `:1101` start / `:1103` skip（`disabled`/`unavailable`/`circuit_open`） | 产出 `reranked` |
| 12 | `sentence_window` | `:1199`/`:1201` skip，`:1203` start，`:1210-1216` 租户二次过滤 | |
| 13 | `dataset_scope` | `:1257` start / `:1273` skip `unscoped` | 唯一天花板式收口，必须在 10/12 之后 |
| 14 | `truncate` | `:1275`（无 skip 分支） | 恒开 |

**因此两条硬约束**：
- 节点 id 已带命名空间（`plugin.<name>.expand`、`graph.retrieve`）。阶段注册必须**原样保留这些 id**，
  因为 `frontend/src/strategy/traceParse.ts` 的 `STAGE_LABELS` 按名解析、`server/app.py` 与 run-events
  消费方也按名读。改一个名字 = 静默改前端阶段面板。
- `complexity_gate` 的"简单查询"路径会**连带跳过 `rewrite` 与 `route`**（skip reason 都是
  `gate_simple`），所以 enablement 不是每个阶段独立布尔 —— 端口需要一个
  `enabled_on(state)` 谓词，而不只是 `flag` 字段。

```python
class RetrievalStage(Protocol):
    node_id: str                    # 上表原样保留
    order: int                      # 显式序号，避免依赖 dict 插入顺序
    def eligible(self, state, ctx) -> str | None: ...   # None=执行；否则 skip reason
    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome: ...
```

`RETRIEVAL_STAGES: ProviderRegistry[StageDeps, RetrievalStage]` —— 复用
`core/providers.py`，与 chunk writer 同一手法。注册表按 `order` 排序后驱动。

`StageOutcome = ok | skipped(reason) | degraded(reason)`，`degraded` 必须携带
`trace`（写进 `traces` 的中文说明）与 `error_type`，因为 `_fail_node` 需要它们。
驱动器统一负责 `_start_node / _skip_node / _complete_node / _fail_node / _degraded /
_add_span`，**阶段自己不再触碰 run events**。这样"漏记降级"从"每人可能漏"变成"结构上不可能"。

### S2.2 状态载体

`RetrievalState`（frozen dataclass + 显式 `with_*` 派生）持有
`query / search_query / candidates / route / reranked / traces / dense_cosines`。
禁止阶段就地改 `candidates` 列表 —— 现在 `run()` 里 `candidates = [...]` 的每次重绑都变成
一次显式派生，谁在什么之后改了什么因此可读、可测。

`dense_cosines` 保持**现算方法**而不是字段（`pipeline.py:202-210` 的理由仍然成立：
chunks 在 sentence_window 展开、租户收口、裁剪之后还会变，早拷贝一份会让弃权判定看到已被剔除的
分数）。阶段化不能把它变成快照字段。

### S2.3 并发预取不是"另一个阶段"，必须显式建模

`hyde / subqueries / stepback` 由 `_start_enhancers`（`:376-417`）**提前并发发起**，
在检索主路径之后由 `_take_enhancer`（`:418`）取回；`subqueries` 的扇出又是**一次批量嵌入 +
N 路并发 hybrid_search + 整批共用一个 deadline**（`:827-870`）。这是刻意设计：
两个线程池分开是有理由的（`:269-279` 注释），共用会让两个无关旋钮互相绑住。

所以阶段模型要多一个概念而不是撒谎：`RetrievalStage.prefetch(ctx) -> Future | None`，
驱动器先向所有声明 prefetch 的阶段发起，再按 order 顺序驱动；`search` 之后的阶段才取用。
若把 prefetch 摊进各阶段的 `run()`，会把"先生成、后扇出、再合并"的时序改成串行，
直接放大端到端延迟 —— 这是不能犯的错。

### S2.4 三条不得回退的语义（每条都要有反向验证）

1. **`reranked` 决定分数量纲。** `run()` 返回前把 `reranked` 传入 `RetrievalResult`
   （`:1288-1294`），弃权闸门据此决定检索阈值是否参与判定（`:1162-1168` 的两条 trace 分别
   对应"改用稠密余弦"与"阈值本轮不参与"）。重构后 rerank 阶段必须仍然能产出
   `reranked=True/False` 三种成因（disabled / unavailable / circuit_open / 分数数量不符 / 抛错），
   且**只有走完 reranker 且长度一致才算 True**。反向验证：把 rerank 阶段的 `reranked` 恒置 True，
   `tests/test_abstention_paths.py` 必须变红。
2. **降级可见。** 每个阶段的 `degraded` 都要同时产出 run event、trace 文案与 metrics
   （`_report_rerank_degraded` 是样本）。反向验证：摘掉一处 `_degraded` 调用，相应观测测试变红。
3. **作用域收口不可绕过。** 图谱分支与 sentence_window 父块回取都按 id 直取、绕过
   `filter_expr`，所以 `dataset_scope` 阶段（`:1256-1271`）是唯一的兜底收口，
   且必须排在它们之后、裁剪之前；`sentence_window` 内那道租户二次过滤（`:1210-1216`）
   也要留在能拿到 tenant 的地方。`order` 必须把这两点钉住，不能靠注册顺序侥幸。

### S2.5 迁移策略：先搬，不顺手改

本切片**只做等价搬迁**：阶段顺序、阈值语义、超时/取消、线程池、trace 文案逐字保持。
不在同一提交里改融合算法、不改 rerank 阈值策略、不动 `retrieval_experiments` 的对比口径
（结构改动与功能改动分离，是 `docs/前后端再设计与优化方案.md` §2.2 原则 2 的明文）。
搬迁后新增阶段的成本应当是"一个文件 + 一次注册"，这一点用守卫测试钉住：
测试内注册一个 probe 阶段并断言它在指定 `order` 处被执行、且 `run()` 未被修改。

## [S3] Out of Scope

融合算法（RRF 权重/归一化）改动、rerank 阈值策略改动、`server/app.py` 内查询/生成侧编排、
`rag_topology.py` 图可视化契约、多 worker 全局限流、WeKnora 的 `providerAdapter` 式模型适配层
（本仓 LLM provider 已有注册表，无同类缺口）。

## Tasks

- [x] R1: 特征化基线 —— acceptance: 记录 `tests/test_abstention_paths.py`、`test_phase8_dense_cosine.py`、
  `test_run_observability_parity.py`、`test_stream_observability.py`、`test_phase5_concurrency.py`、
  `test_qa_retrieval_wiring.py`、`test_auto_filter_wiring.py` 改动前实跑读数（covers: S2.4）
- [ ] R2:（**部分**，未打勾的原因见文末交付记录）`retrieval/stages.py` 端口 + 状态载体 + 驱动器（含统一 span/降级仪式、prefetch 组）
  —— acceptance: 驱动器单测覆盖 ok/skipped/degraded 三态与 order 排序（covers: S2.1; covers: S2.3; depends: R1）
- [x] R3: 逐阶段迁移（14 个），每迁一个跑一次 R1 清单 —— acceptance: 全部读数与基线逐条相同；
  `run()` 从 787 行降到编排骨架（目标 < 120 行）（covers: S2.2; covers: S2.5; depends: R2）
- [x] R4: 装配点接注册表 —— acceptance: `rag.py:_build_optional_components` 的组件名与阶段
  `requires_component` 同源；新增可选策略不再改 `RetrievalPipeline.__init__` 签名（covers: S1.2; depends: R3）
- [x] R5: 三条反向验证 + 扩展性守卫 —— acceptance: S2.4 的三条各自"破坏即红"；probe 阶段注册即执行
  且 `run()` 源码未被改（covers: S2.4; covers: S2.5; depends: R4）
- [ ] R6:（**未做**）独立 review 子 agent 复审 —— acceptance: 无 critical（depends: R5）

## Workspace

- `.venv/Scripts/python.exe` + `PYTHONPATH=.`；检索相关套件不连 MySQL（Milvus/embedder 用假件），
  但 `test_phase5_concurrency.py` 对负载敏感，跑它时不要并行其他重活
- 前端 `strategy/traceParse.ts` 靠正则解析 `traces` 字符串（`STAGE_LABELS` 14 项），
  **trace 文案改动会静默改变前端阶段面板的显示**：本切片因此不得改文案，
  并把"阶段名 ↔ STAGE_LABELS 键"的一致性做成一条测试（后端阶段名列表与前端标签表比对）。


## 交付记录（2026-09-22 在本机复跑，不是引用历史日志）

**先记一条流程缺陷**：本切片代码早在 `0c5a6a4` 就已落地，但 frontmatter 一直停在
`status: planned` / `commits: —`，任务清单六条全是空格。验收清单 A3 要求的正是
"每条已交付切片有 delivered + commits 区间"，也就是说这一条对下一位智能体是**假阴性**
（它以为这块没做，可能重做一遍）。今天补上，并把复跑读数写在这里而不是抄历史。

今日实跑（`./.venv/Scripts/python.exe -m pytest … -q`，`PYTHONPATH=.`）：

```
R1 清单 7 个文件 + tests/test_retrieval_stage_registry.py
  123 passed in 394.56s (0:06:34)
run() 行数以 inspect 实测：55 行（spec 目标 < 120；改造前 508–1294 行 = 787 行）
retrieval.stages.RETRIEVAL_STAGES 类型：ProviderRegistry（注册表驱动，不是一串 if）
```

逐条对照 acceptance：

- **R1** 通过。但要说清它现在证的是什么：今天这 123 条是**改后**的实跑，"与改动前基线
  逐条相同"里的"改动前"那一半是实施当轮记的，今天没有回旧提交重测。
- **R2 未打勾**：`order` 与硬次序由 `test_hard_ordering_invariants_are_enforced_not_polite`
  钉住，注册期形状判死由 `test_registration_rejects_a_stage_that_does_not_fit_the_protocol`
  钉住；但 acceptance 里点名的"驱动器 **ok/skipped/degraded 三态**单测"没有写成一条
  独立用例 —— 三态目前只被特征化套件（`test_run_observability_parity.py`、
  `test_stream_observability.py`）间接覆盖。**间接覆盖不等于没有覆盖，但不满足本条自订的
  判据**，所以留空格，不当已交付。
- **R3** 通过：14 个阶段迁完，`run()` 只剩编排骨架，55 行为今日实测。
- **R4** 通过：装配点与阶段 `requires_component` 同源的守卫在 `tests/test_optional_strategy_assembly.py`，
  今日随 R1 清单一起绿。
- **R5** 通过：`test_new_stage_is_reached_without_editing_the_pipeline` 用 fixture 抓
  `retrieval/pipeline.py` 的字节，probe 阶段注册即被真执行且宿主逐字节不变；
  `test_pipeline_run_has_no_per_stage_branch_copy` 拦"把分支抄回宿主"。
- **R6 未做**：`0c5a6a4`/`8766b79`/`e4f6f8d` 三个提交**没有开过独立评审**。本轮三条评审
  分别覆盖 chunk 生命周期、轴 #1/#4、以及 `23503c9`/`80ad1dc`/`b84fe0a`，都不含这三个提交。
  登记为下一位的第一候选（判据按 §3.4：逐条变异改回旧写法，必须有一条具名用例红）。
