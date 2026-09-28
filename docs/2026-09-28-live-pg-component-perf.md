# 实机测量（PostgreSQL 为目录库）：链路修通 + 逐组件性能（2026-09-28，Round 10）

> 本轮全部读数以**真机真依赖**为准：PG 17.8 目录库（`192.168.100.128:5433`）、
> Milvus 3.0（同机 19530）、真实 bge-m3 嵌入 + bge-reranker-v2-m3 重排 + 真实 LLM 生成。
> 隔离环境：目录库 `rag4c_live_bench`、集合 `rag4c_live_bench`、后端 `:8011`、
> 前端 `:1420`；**没有**动过生产集合与 `.env`。

## 1. PG 这条链路上查出来的缺陷（逐个改完并复验）

| # | 缺陷 | 真机症状 | 处理 |
|---|---|---|---|
| 1 | 迁移里 4 处布尔列默认值写成整数（`server_default=sa.text("0"/"1")`） | `migrate upgrade` 在 PG 上 `DatatypeMismatch: column "is_default_serving" is of type boolean but default expression is of type integer`，**整条迁移链过不去** | 改 `sa.false()/sa.true()`（实测三种方言渲染：PG `false/true`、MySQL `false/true`、sqlite `0/1`）；仓库其余几十处本来就是这个写法，整数是漏网 |
| 2 | 目录自检只认 MySQL 拼法 | 新建 PG 库升级到 head 后报 **308 条 schema_issues**（其中 221 条是"missing or invalid check"）——约束其实都在（PG 侧 462 个 CHECK） | 见 §2 的归一化 |
| 3 | PG 把 `x BETWEEN a AND b` 回显成 `x >= a AND x <= b` | 清单两种拼法都写过，单向替换会偏袒一边 | 比对侧改成"形态集合"（原样 + 折回 BETWEEN），两种都认 |
| 4 | PG 触发器的逻辑在函数体里，检查器只读 `pg_get_triggerdef` | 2 条"invalid trigger"假报，而 `schema_mode=verify` 让**目录拒写**，入库链路整体卡死 | 核对"触发器 + 它 EXECUTE 的函数体"合起来的文本 |
| 5 | `_connect_args_for` 给 sqlite 也透传 `connect_timeout` | `TypeError: Connection() got an unexpected keyword argument` —— verify 模式下 sqlite 目录库起不来（2 条红测试的真实根因，且**不在我改动的那个文件里**） | 补 sqlite 分支 + 新增驱动参数栅栏用例（含"塞错参数确实会抛"的反证） |
| 6 | 种子脚本 `tenant_workspaces` 的 `is_default` 在裸 SQL 里写 `1` | PG 上报 `column "is_default" is of type boolean but expression is of type integer`，注册权威补不上 | 改成绑定参数（驱动各自渲染） |
| 7 | `eval/retrieval_eval/milvus_store.py::delete()` 结果解析 | pymilvus 返回 `OmitZeroDict`，`int(res or 0)` 抛 `TypeError` | 按 `delete_count` 取，取不到算 0，真实条数由调用方复查 |
| 8 | QA 权威检索把**全域检索口径**当成一个叫 `default` 的知识库（`list_qa_retrieval_bundle(tenant, dataset_id or "default")`） | 前端问答页默认不选知识库 → `dataset_id` 为空 → 查一个不存在的数据集行 → `ContentNotFound`。**FAQ 权威在 UI 默认口径下永远不生效**（三种方言都一样，不是 PG 独有） | 空 `dataset_id` = 本租户全域（与 `QueryRequest.dataset_id`、`build_dataset_filter` 同口径），只在该口径下跳过 `_dataset()` 校验并去掉数据集过滤；租户维度仍强制 |
| 9 | `query.qa_retrieval.catalog_error` 把"数据集未建/未授权"和"读目录失败"混成一个信号 | 监控页这张卡片的文案是"QA 目录读取失败"（warning 色），一次普通的全域问答就点亮它——本轮我就是被它误导，先去查了一遍 PG 方言 | `ContentNotFound` 归为预期空包（调用方记 `no_bundle`），`catalog_error` 只在真读失败时计数 |
| 10 | 裸 SQL 拿布尔列跟整数**比较**：`WHERE ... AND retrieval_enabled=1`（`scripts/enterprise_catalog_upgrade.py:1604`） | MySQL/sqlite 收下，PG 直接 `operator does not exist: boolean = integer`；而同一份仓库在 CHECK 里本来就会写方言无关的 `NOT retrieval_enabled`（`models/orm.py:6034`） | 改成 `AND retrieval_enabled`；并补第三条通道的整类栅栏（见 §7 待办 1）——此前只扫了 DDL 默认值与 INSERT 值，比较这一类没人管 |
| 11 | `RetrievalTrace` 的 stale 引用列表用 `key={link.chunkId}` 当 React key | 浏览器控制台刷 **363 次** `Warning: Encountered two children with the same key` —— 两条 stale 引用命中同一条证据时 key 就撞了（QA 权威打通后更容易出现：`qa::qa-…` 会被多个结论同时引用）。单测此前每条 stale 都取自不同 chunkId，所以一直全绿 | key 换成 `${link.chunkId}:${linkIndex}`（重复引用是合法数据，撞的是 key）；补一条会复现该警告的用例，并让**探针把控制台错误判红** |

缺陷 1 与 6 是**同一类**（布尔值写成整数），我第一遍只扫了 DDL 默认值，漏了运行期裸 SQL 里的 INSERT —— 整类扫描的范围当时判窄了，这条记在 §7 待办里。

### 缺陷 8/9 的变异反打（9/9 全部先红再绿）

`scripts/bench/mutate_qa_bundle_guard.py`：逐个把守卫改坏，配对的测试**必须**变红，
源文件在 `finally` 里还原（本轮实测：`restored: knowledge_content.py=True, qa_retrieval.py=True, rag.py=True`）。

| # | 变异 | 配对红测试 | 结果 |
|---|------|-----------|------|
| 1 | 未建数据集又当成目录故障 | `test_missing_dataset_is_not_reported_as_catalog_failure` | 抓到（6.5s） |
| 2 | 真读失败被并进"预期空包"分支 | `test_catalog_read_failure_still_reports_catalog_error` | 抓到（1.3s） |
| 3 | 加载器把空口径还原成 `"default"` | `test_loader_passes_global_scope_through` | 抓到（8.5s） |
| 4 | 口径恒判为具名 | `test_empty_dataset_scope_spans_the_tenant_not_a_dataset_named_default` | 抓到（9.4s） |
| 5 | 口径恒判为全域（具名不再过滤） | `test_named_scope_still_limits_to_one_dataset` | 抓到（7.2s） |
| 6 | 租户过滤放宽到别的租户 | `test_empty_dataset_scope_never_reaches_another_tenant` | 抓到（6.1s） |
| 7 | acl 守卫整个关掉 | `test_global_scope_with_acl_does_not_inject_faq` | 抓到（1.3s） |
| 8 | acl 守卫不看是否全域 | `test_named_scope_with_acl_still_loads_that_dataset_bundle` | 抓到（1.2s） |
| 9 | **入口不再把 acl 传下去** | `test_answer_sequential_blocks_acl_filtered_global_qa` | 抓到（0.9s） |

第 9 条是评审点出来的：单元级断言只覆盖 `apply_qa_retrieval` 自己的分支，
`rag.py` 忘传参数时那批测试**照样全绿**，所以补了一条从 `rag._answer_sequential` 入口打的用例。
另外第 6 条的锚点第一次写成单行 `QAKnowledge.tenant_id == tenant_id,`，在文件里命中 4 次被脚本自己拒了
——改成带上下文的多行锚点才有效；这正是"变异脚本必须先 assert 落点唯一"的价值。

## 2. 目录自检的方言归一化收成一处声明

原来 `_canonical_check_sql`（整句比对）和 `_parenless_sql`（片段比对）是两条流水线，后者**不做**方言归一化，
于是 PG 的四种拼法差异各自撞墙：

- `= ANY ((ARRAY['a','b'])::text[])` → `IN ('a','b')`（`<> ALL` → `NOT IN`）
- `status::text`、`'x'::character varying` 这类类型标注
- PG 的 `length()` 与 MySQL 的 `char_length()`
- 布尔字面量 `true/false` 与 `1/0`

做法：新增 `_strip_dialect_decorations()`（单一声明，两个比对入口共用）+
`_between_fold_variants()` / `_parenless_forms()`（形态集合），并把散在 **13 处**的
片段/整句比对收进 `_check_fragments_match()` 与 `_check_sql_equal()` 两个入口。

中途我自己踩了一次双重归一化：`_parenless_forms` 先把文本喂给 `_parenless_sql`
再走一遍归一化，导致 `col = false` 一侧变 `not col`、另一侧仍是 `col=0`，
sqlite 侧 6 条测红后改回单次流水线。

**读数**：隔离 PG 库 `inspect_catalog_schema` 从 308 → 2 → **0**，`status=current`；
`pytest tests/test_catalog_schema.py tests/test_dialect_registry.py` → **74 passed, 612.60s**
（本轮开始前那 2 条 verify-mode 红测——`get_engine` 在 verify 模式下不该建空 schema、
以及 stamped schema 应被接受——已随缺陷 5 一起消掉）。
**这条回归是 17:10 采集的用例，不含 17:18 才加的缺陷 10 栅栏**；那条单独跑
`tests/test_dialect_registry.py` → **11 passed**，并用变异反打确认它不是装饰：
把 `retrieval_enabled` 改回 `=1` 立刻红（`1 failed`），还原后 `restored True`。

## 3. 实机环境的真实门槛（不是代码 bug，是流程缺口）

按产品自己的入口跑一遍，才看清"为什么那份 9723 行的语料在实机上答不出东西"：

1. `sources/runner.py::_register` 在 `tenant_id` 为空时**直接 return**——只写 Milvus 投影、
   不写目录权威。于是 serving fence 之后把命中全量剔除（实测 `serving fence(milvus.initial)：剔除 16 条不可检索文档证据`）。
2. 走产品 API `POST /api/documents/ingest` 入库后，问答仍会安全弃权：
   `catalog schema is incomplete; dataset_workspace_ownerships.missing_for_dataset:default/kb-live-bench`。
   原因是注册权威表 `dataset_workspace_ownerships` **只有 0028 迁移做过一次性回填**，
   运行期没有任何代码为新建 dataset 写这行。补种入口是 `scripts/seed_workspace_channel_ownership.py`
   （它在 PG 上还会撞缺陷 6）。
3. 两条入库路径的鉴权姿态不一致：脚本路径不看 actor，API 路径要求
   `KnowledgeOps Actor Bearer`（`knowledge_security.require_actor_on_admin_writes=True`）。
   本轮只在**隔离** env 里关掉，生产 `.env` 未动。
4. 缺陷 8 的修法前提：问答链路上**知识库级授权本来就只到租户这一档**。
   `server/app.py:1311` 的 `/api/query`（和 `:1693` 的 `/api/query/stream`）函数签名里
   **没有任何 actor 依赖**，只有 `_serving_snapshot` 那道 serving fence；带 dataset resolver 的
   `require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))`
   挂在文档/管控类路由上（`server/document_catalog_api.py:17`、`server/documents.py:731` 等），
   那条路径才会调 `evaluate_dataset_permissions` 并按 `datasets.acl_mode` 走逐库 grant
   （`core/enterprise_access_control.py:381`）。
   所以"本租户全域"的 QA 包与问答链路现有口径同档，不新增暴露面。
5. 顺带量出来的两条**既有**缺口（比本轮修复更要紧，要单独裁定，本轮只登记）：
   - `/api/query` 无 actor 依赖 → 请求体里给任意 `dataset_id` 就能读该库内容，
     `dataset_acl` 模式的逐库 grant 在问答入口**不生效**。这是"问答入口 vs 管控入口"的
     鉴权姿态不一致，与本轮改动无关（改动前也读得到，只是 FAQ 那一路恰好查不到行）。
   - `qa_knowledge` 没有 acl 列，ACL 只在 `retrieval/stages.py:1214` 进 Milvus 表达式，
     QA 证据在其后合并 → **FAQ 绕过 chunk 级 acl**。本轮打通 dataset 轴后更容易命中。
6. **企业体检工具在 PG 上被自己的门禁拒了**：`enterprise_catalog_upgrade.py preflight --url <PG>`
   返回 `safe=false`，理由 `database backend must be MySQL`（`:449`）。`require_mysql` 是形参、
   默认 True，而**全文没有一处传 False**（`:414/:4372/:4583/:4732/:4842`）——
   可是同一个文件 `:605-677` 已经写好了 PG 的物理只读证明
   （`postgresql-default-transaction-read-only`）：**一半代码认 PG，门不让 PG 进**。
   本轮没动这个门：它是生产安全闸门，且 `backup-command` 确实只会打印 `mysqldump`（对 PG 是错的），
   所以正确修法是按**子命令**给后端白名单（preflight/plan/verify 可放 PG，备份类保持 MySQL），
   而不是把 `!= "mysql"` 一把改成 `not in {"mysql", "postgresql"}`。

结论：**入库 ≠ 可服务**。缺的是"workspace/ownership 注册"这一步。要么在 dataset 创建时
一并建权威行，要么在文档里把"新库必须先跑 seed 才服务"写成显式步骤——这是要裁定的产品决策，本轮只登记。

## 4. 逐组件消融（真实上游，隔离语料 5 篇 33 切片）

harness：`scripts/bench/component_ablation.py`。要点：

> **覆盖缺口更正**：这一版跑了 15 个变体，但**没有 `no_qa_retrieval`**——QA 权威这个可插拔组件
> 从未被消融过，原因是隔离语料库里一条 approved FAQ 都没有，关掉它 Δ 必然为 0，测了也是假的。
> 本轮补了 `scripts/bench/seed_live_faqs.py`（走 `create_qa` + `review_qa`，6 条 approved）之后
> 才加了 `no_qa_retrieval` 变体（`RAG4C_PIPELINE_QA_RETRIEVAL_ON=false`，已用 harness 自己的
> `_resolvable` 确认能解析到 `pipeline.qa_retrieval_on`）。
>
> **这次没能跑出它的 Δ**：harness 起 baseline 服务时子进程启动失败（退出码 3），
> 栈是 `server/app.py:680 lifespan → server/source_dispatcher.py:393 →
> core/source_sync_ledger.py:1252 → core/db_clock.py:27`，连的是 `192.168.100.128:3307`
> 的 MySQL（本机当前没开）。**我没有查清"为什么传了 `--env-file config/.env.live-bench`
> 的子进程会去连 MySQL"**——查的过程中还犯了一次"空日志 = 没报错"的错（我 20 秒就把探针进程
> 杀了，MySQL 连接超时根本没来得及写进日志）。按负责人的意思这条先放下。
> 因此 `no_qa_retrieval` 的性能 Δ **仍是空白**，不要把它当已测。
>
> QA 权威这一路本轮是靠产品 API + Playwright 验的（不是靠消融）：
> `query.qa_retrieval.hit` 从 0 → 4，`catalog_error` 归 0，界面↔服务端 8 项对账一致（§5.1/§5.2）。

- 每个变体**单独起一个服务进程**，用进程环境变量覆盖（读取顺序：进程 env > env 文件），
  不写任何配置文件（`/api/config/update` 那条路会落盘改 `.env`，测量不该动配置）；
- 记录**服务端真实看到的值**（`/api/config` 的 `env → value`），否则写错的键会被
  pydantic-settings 静默忽略，那个变体就退化成 baseline，Δ 是假的；
- 记录弃权/降级的**原因文本**，不只看布尔标志；
- 预热按**整轮**做：LLM 槽位与嵌入的缓存是 Redis 级、跨进程共享，只热一两条会让
  后面的变体走热路径、基线走冷路径。

### 冷路径第一轮（预热只 1 条，5 变体，n=4）

| 变体 | 端到端 p50 | 弃权率 | 平均引用 | 最贵阶段 |
|---|---|---|---|---|
| baseline | 58.7s | 0.00 | 19.75 | verify 25.8s · generate 20.3s |
| no_complexity_gate | 57.0s | 0.00 | 14.00 | verify 24.2s · generate 19.0s |
| no_hybrid | 2.4s | **1.00** | 0 | gate 1.6s · rerank 339ms |
| no_rerank | 5.6s | **1.00** | 0 | gate 5.1s · embed 166ms |
| no_endpoint_probe | 5.7s | **1.00** | 0 | gate 4.9s · embed 247ms |

单看这张表会得到"关掉 hybrid 省 56 秒"的错误结论——**它省的是"根本没答"**：
后三行弃权率 1.00，阶段耗时是弃权短路路径的耗时。而且后三行 `route=0ms`
提示 LLM 决策命中了跨进程 Redis 缓存，冷/热口径混在了一张表里。
所以这一轮**只作为问题发现**，不作为组件成本结论。

### 弃权原因的真相（我中途读错过一次，更正记在这里）

我一度以为"LLM 失败被报成检索失败"是可观测缺陷 —— **错了**。用 UTF-8 直接读 JSON
后，信号是 `弃权: 生成失败`，而同一行的阶段耗时是 `search 63ms / rerank 658ms`，
检索层完全正常。归因没坏，是我把终端乱码的文本读反了。真正的原因是上游：

```
POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions → 400
{"error":{"type":"Arrearage","code":"Arrearage","message":"Access denied, ... overdue-payment"}}
```

即 **DashScope 账号欠费**，gate/route/生成/判定四类槽位全不可用。按用户指示改用
`qwen3.8-flash`（同 key 实测 200、104 tokens 正常返回），bench 环境里 8 个
`RAG4C_LLM_*_MODEL` 全部指过去；嵌入与重排本来就跑在硅基流动
（日志实测 `api.siliconflow.cn/v1/embeddings` 与 `/v1/rerank` 均 200）。生产 `.env` 未动。

### 消融 harness 在免费额度模型上的局限

换模型后重跑，变体 2 的服务进程没起来、探针卡在重试里，我把它停了 ——
诚实的结论是：**这套"每变体起一个服务 + 端到端问答"的消融不适合在慢/限流的
免费额度模型上跑**（单请求 20–60s，一次四变体半小时起）。已确定可复用的部分是
harness 的三条纪律（进程环境覆盖不写配置文件、记录服务端实际生效值、记录弃权/降级原因），
组件成本表由两部分拼：

- **检索层组件**（hybrid / rerank / 候选系数 / 多样性 / 阶段延迟）：用评测线口径量，
  Round 9 已给出同机读数（dense p50 149ms、dense+rerank 565ms、hybrid+rerank 535ms）；
- **LLM 层组件**（gate / route / rewrite / verify / generate）：只有 §4 冷路径那轮的
  逐阶段毫秒可用（n=4，只作量级参考，不作精确 Δ）。

## 5. 前端实机（Playwright）

工具：`scripts/bench/live_ui_probe.py`（Chromium + `rag4c.base_url` 指向 `:8011`）。
两条**已经实测到**的交互事实，都是不上真浏览器看不到的：

1. **首次进入被「新手引导」模态层挡住**：`aria-label="新手引导"` 的 `role=dialog`
   覆盖整屏并拦截指针事件，探针在 `box.click()` 上重试 55 次不动。
   测量按"回头用户"口径预置 `localStorage['rag4c.onboarding.v1']='1'` 才绕得开。
   —— 对真实用户这是设计，但对任何自动化测量/回归脚本，它是必须先满足的前置条件。
2. **"元素可见"不等于"元素可点"**：问答输入框 `textarea.query-input` 首帧是 `disabled`
   的（`QueryPage.tsx` 的 `disabled={loading}`），探针用 `wait_for_selector(state="visible")`
   拿到句柄就 `click()`，Playwright 按 500ms 重试到超时，最后抛的是
   `Element is not attached to the DOM` —— **报错文本指向 DOM 挂载，真实原因是没等 enabled**。
   实测快照确认路由没变（`url` 始终是 `http://localhost:1420/#/query`），
   所以上一版把它记成"提问触发换路由销毁上下文"是给症状贴错了子系统标签，此处更正：
   真正确诊的上下文销毁原因是**下一条**——dev server 半路死了。
3. **前端 dev server 会在跑测中途退出，读数不会自己变红**：那一轮 `console_errors` 里
   38 条 `ws://localhost:1420 ERR_CONNECTION_REFUSED`，页面半渲染，于是
   监控页卡片 0 张、4 条问题里只有 1 条拿到首包读数，而"首包 p50=30.4 秒"看起来像性能数据。
   已给探针加**前置可达性检查**：前端不可达就直接退出码 2 报"起不来"，
   不让它冒充性能读数。

探针还确认了外壳渲染正常（截图 `output/bench/shots/01_shell.png`：侧栏四组导航、
问答页输入框、`服务已连接` 状态），以及一个**不是缺陷**的疑点：主题第三个按钮
文案是「八奈见」，源码与测试里就是这么定的（`ThemePicker.test.tsx`），没当成 bug 改。

首屏 / 首包 / 阶段耗时 / 界面↔服务端对账的读数见 §5.1–§5.3。

### 5.1 实机读数（`:8012` 进程从零计数开始，vite dev 全程可达）

报告 `output/bench/ui_probe_final.json`（`generated_at 17:25:24`）。
外壳加载 **3570.1ms**，导航条 DOM 内 3 项：`知识问答 / 回答过程 / 检索调试`。
4 条问题里 **3 条走完整轮**（`ui_done` = 187.7s / 227.5s / 25.6s），1 条超探针上限。

单条完整问答的阶段耗时（服务端 `span.*` p50）：

| 阶段 | p50 | 说明 |
|------|-----|------|
| `span.route` | 6181.6ms | 路由决策（走 LLM） |
| `span.gate` | 11221.9ms | 复杂度门控（走 LLM） |
| `span.embed` | **0.0ms** | 命中 Redis 嵌入缓存，见 §7 待办 4 |
| `span.search` | 52.8ms | Milvus 向量+布尔检索 |
| `span.rerank` | 377.2ms | bge-reranker-v2-m3 |
| `span.generate` | **59049.7ms** | qwen3.8-flash 生成 |
| `span.verify` | **102574.8ms** | 其中 `verify_l3` 102530.4ms（L2 只 24.0ms） |
| `query.total` | **143465.2ms** | 整答约 2.4 分钟 |
| 界面侧 | 首包 29696.0ms / 答完 187715.6ms | 首包 = 点击到 `/api/query/stream` 首个响应 |
| 引用 | DOM 内 14 / 47 条 | 答案文本 8789 / 32493 / 32651 字符 |

**这一张表就是本轮关于"可插拔组件实际性能"的主结论**：真正花钱的是**生成与 L3 核验**，
检索本体（embed+search+rerank）只有约 **0.43 秒**；再加上门控与路由这两个 LLM 阶段
（合计约 17 秒，**占整答的 12%，比整个检索过程贵 40 倍**）。
换嵌入模型、调 hybrid/RRF 动的是那 0.43 秒；要压端到端延迟得动 `verify_l3` 与 `generate`。
这也是 §7 里"换模型不是当前瓶颈"的量化依据。

同一轮里 `query.abstained=1`：4 条问题有 1 条弃权，`图编排回退 1 次`、
`跳过无效二轮检索 1 次` 同步计入——弃权路径依然真实存在（§4 的悬崖）。

QA 权威这一路的服务端计数器：`{"query.qa_retrieval.hit": 3.0, "query.qa_retrieval.no_match": 2.0}`——
**FAQ 权威在真 PG 上第一次真的命中了**（改动前是 `catalog_error: 1.0 + no_bundle: 1.0`，
永远查不到行）。监控页同步显示 `QA 权威命中 3 次` / `QA 目录读取失败 0 次`。
夹具由 `scripts/bench/seed_live_faqs.py` 走 `create_qa` + `review_qa` 种进去（6 条 approved），
不是裸 SQL 插行。

### 5.2 界面读数与 `/api/metrics` 的对账

对账的键**取自前端自己的声明表** `PROTECTION_DEFS`（`frontend/src/monitor/monitorProjection.ts:12-55`），
不是凭卡片文案猜。定稿一轮（`output/bench/ui_probe_monitoronly.json`，**非零值**下 8 项全对上）：

| 卡片 | 指标 | UI | API |
|------|------|----|-----|
| 系统选择弃权 | `query.abstained.count` | 1.0 | 1.0 |
| QA 权威命中 | `query.qa_retrieval.hit.count` | 4.0 | 4.0 |
| QA 无命中 | `query.qa_retrieval.no_match.count` | 2.0 | 2.0 |
| QA 目录读取失败 | `query.qa_retrieval.catalog_error.count` | 0.0 | 0.0 |
| 重排降级 | `retrieval.rerank.degraded.count` | 0.0 | 0.0 |
| 图编排回退 | `graph.fallback.count` | 1.0 | 1.0 |
| 跳过无效二轮检索 | `query.round2.skipped.count` | 1.0 | 1.0 |
| 实际计算累计 P95 | `query.total.p95` | 213447.74 | 213447.7 |

对账过程又揪出**探针的第二个竞态**：`.stat-card` 刚 attach 时计数器还全是 0
（`/api/metrics` 要排队，这一页先渲染骨架），所以 `wait_for_selector` 拿到就读会得到
"UI 0.0 vs API 4.0" 这种 mismatch。中间那一轮 `ui_probe_keycheck.json` 就是这么把 8 项读崩的，
而它同一轮报 `console_errors=0` —— 这次不是产品的问题，是探针的。
修法是**连续两次取到完全一样的卡片才算落定**（上限 90 秒，取不到就带 `error` 返回并判红）；
顺带加 `--monitor-only`，验这段逻辑不必再等一条几分钟的问答。

上一轮（`ui_probe_pg.json`）那两处差 1（`QA 权威命中` UI=2/API=3、`图编排回退` UI=0/API=1）
则是**两端不同源**：最后一条在途问答在监控页读完之后才跑完，把计数器推前了。
现在在 DOM 读数的同一时刻抓 `api_at_monitor_read`。
这两类坑的表征一模一样——"界面读错" vs "我取数取早了"，**先排除后者再报前者**。
（最初把 8 项全读成 `missing` 还有第三个原因：我按 `LATENCY_CARDS` 的 label 去找卡片，
而那些 label 喂的是 ECharts canvas 图，DOM 里根本没有——能文本对账的只有
`实际计算累计 P95` 这一张。）

监控页另外两张卡片暴露的质量信号，留给下一轮定性：`引用失败率 100.0 %`（`verify.total=2`、
`verify.citations.failed=2`）与 `检索降级 66.7 % (2/3)`。

### 5.3 控制台错误必须判红（探针此前会放过真缺陷）

最终一轮刷了 **363 条**同一句 `Warning: Encountered two children with the same key, qa::qa-…`，
而**所有读数都正常**——"数字对得上"根本不覆盖这一类缺陷。
组件栈直指 `RetrievalTrace.tsx` 的 `key={link.chunkId}`（§1 缺陷 11）。
两处一起改：key 换成 `${link.chunkId}:${linkIndex}`，并让探针在 `console_errors` 非空时判红、
按去重后的类别报前 3 条。

## 6. 复现

```bash
# 隔离 PG 库 + 隔离集合（一次性）
createdb ... / RAG4C_ENV_FILE=config/.env.live-bench .venv/Scripts/python.exe scripts/migrate_catalog.py upgrade
RAG4C_ENV_FILE=config/.env.live-bench .venv/Scripts/python.exe scripts/seed_workspace_channel_ownership.py

# 入库（产品 API）
.venv/Scripts/python.exe - <<'PY'   # POST /api/documents/ingest {file_path, dataset_id:kb-live-bench}
PY

# 消融
RAG4C_ENV_FILE=config/.env.live-bench .venv/Scripts/python.exe scripts/bench/component_ablation.py \
  --env-file config/.env.live-bench --questions <问题集.json> \
  --variants baseline,no_hybrid,no_rerank --repeat 1 --warmup 1 --out output/bench/ablation_warm.json

# 前端实机（先确认 vite 在跑，探针会做可达性检查）
cd frontend && npm run dev            # :1420
RAG4C_ENV_FILE=config/.env.live-bench .venv/Scripts/python.exe -m uvicorn server.app:app \
  --host 127.0.0.1 --port 8012        # 每个测量进程都用干净计数器从零开始
.venv/Scripts/python.exe scripts/bench/live_ui_probe.py \
  --app-url http://localhost:1420 --api-base http://127.0.0.1:8012 \
  --questions eval/.cache/bench_questions.json \
  --out output/bench/ui_probe_pg.json --idle-timeout-s 420

# 复现缺陷 8：直接对着 PG 调 QA 包加载，打印真实 traceback
.venv/Scripts/python.exe scripts/bench/repro_qa_bundle.py --env-file config/.env.live-bench

# QA 权威这一路的实机夹具（走 create_qa + review_qa 的产品不变量，不是裸 SQL）
.venv/Scripts/python.exe scripts/bench/seed_live_faqs.py --dataset kb-live-bench

# 本轮三处语义改动的变异反打（9 个变异，各自配对红测试；源文件在 finally 里还原）
.venv/Scripts/python.exe scripts/bench/mutate_qa_bundle_guard.py
```

## 7. 待办（本轮没做完的）

1. 整类扫描补全：布尔值写成整数这件事，按"值/表达式进入 DB 的所有通道"清点，现在是四条——
   ① DDL 默认值、② DDL 渲染、③ 裸 SQL 的 INSERT 值（`test_boolean_columns_never_default_themselves_to_an_integer` /
   `test_boolean_default_renders_as_a_real_boolean_literal_per_dialect` /
   `test_raw_sql_never_writes_a_boolean_column_as_an_integer`），
   ④ 本轮补的**比较**通道（`test_raw_sql_never_compares_a_boolean_column_to_an_integer`，缺陷 10 就是从这条漏出来的）。
   **仍有一类没进栅栏**：迁移文件里 `sa.CheckConstraint("x IN (0,1)")` 这种 DDL 字面量——
   它要用同文件里的 `sa.Column(...)` 解析类型才能判，本轮没做。
   注意这条栅栏按"这条 SQL 涉及的表"取列类型交集，所以 `storage_backends.is_deleted IN (0,1)`
   这种**整数列**不会被误报（我先证伪了才没把它当缺陷改掉）。
2. §3 的裁定：dataset 创建时是否应自动建 workspace ownership 权威行。
7. **消融 harness 的 baseline 起不来**（本机 MySQL 未开时）：传了
   `--env-file config/.env.live-bench` 的子进程仍去连 MySQL，栈见 §4。
   MySQL 开起来之后要么它自然好，要么这是一个"第二个引擎绕开 `RAG4C_CATALOG_DB_URL`"的真缺陷
   ——**这条还没定性**，别按"环境问题"归档。定性方法：让子进程自己打印
   `catalog.get_engine().url` 与 `app.state.knowledge_auth_engine.url` 两个值再比。
3. ~~Playwright 前端实测读数~~ **已清**：见 §5.1（首包/阶段耗时）、§5.2（界面↔服务端 8/8 对上）、
   §5.3（控制台判红）。留下的前端跟进：
   - `key={业务 id}` 这一类**只扫了 `RetrievalTrace` 一处**。同类形状（`.map` 里用非唯一业务字段当 key）
     在 `frontend/src` 别处有没有，本轮没做整类扫描；探针现在会把控制台警告判红，下次跑到就知道。
   - 导航条 DOM 内只有 3 项（`知识问答/回答过程/检索调试`），是侧栏分组折叠的形态，
     不是缺陷；但**"整个 shell 只挂 3 个入口"这件事我没验过**，要判得先展开各分组再数。
4. `embed` 阶段实测 0.03–257ms 波动：命中 Redis 嵌入缓存时接近 0，这没错，但
   阶段耗时读数要么标注缓存命中、要么分冷热两栏，否则会被读成"嵌入不花钱"。
5. 模型侧：**上一版写的"硅基流动已上线 JEPA"是错的，本轮直接核对了它的在线模型清单**。
   `GET /v1/models` 返回 98 个模型，把每个条目的完整 JSON 拼起来搜 `jepa` 命中 **0 次**；
   其中向量/重排家族 14 个，全是 bge 系与 Qwen3 系：
   `BAAI/bge-m3`（我们在用）、`BAAI/bge-reranker-v2-m3`（我们在用）、
   `BAAI/bge-large-{en,zh}-v1.5`、`Qwen/Qwen3-Embedding-{0.6B,4B,8B}`、
   `Qwen/Qwen3-Reranker-{0.6B,4B,8B}`、`Qwen/Qwen3-VL-Embedding-8B`、`Qwen/Qwen3-VL-Reranker-8B`、
   `Pro/BAAI/bge-{m3,reranker-v2-m3}`。
   结论有两层：
   - JEPA 是**自监督预训练目标**，不是可服务的 embedding 端点，托管 API 那侧拿不到；
     真要用得自己训，成本与本轮"实测打磨"不是一个量级，因此**撤掉**这个候选。
   - 同一个插槽上有真正可测的升级轴：`Qwen3-Embedding-8B`（换嵌入模型只改
     `RAG4C_EMBEDDING_API_MODEL` 一个键，是现成的可插拔组件），以及
     `Qwen3-VL-Embedding-8B` 对多模态切片。下一轮值得做的是**同一份 gold 上换嵌入模型**的
     对照，而不是等 JEPA。
   - 但挡在质量前面仍然是"hybrid/rerank 一关就 100% 弃权"这个悬崖（§4），
     弃权路径不修，换嵌入模型的读数会被弃权吞掉，测不出差异。
6. 独立评审提的两条跟进（都同意，未在本轮改）：
   - **FAQ 绕过 chunk 级 acl**：`qa_knowledge` 无 acl 列。本轮先用"全域 + 带 acl 就不注入"
     收口（`retrieval/qa_retrieval.py` 的 `query.qa_retrieval.acl_scope_skip`），
     长期解要把 FAQ 的 acl 归属建起来（继承 `source_document_id` 那份文档的 acl）。
   - **全域包 `limit` 的排序偏置**：`(created_at, id)` 升序 + 500 上限，租户内活跃 FAQ
     超过 500 条时，**新建知识库的问答会被系统性挤掉**，且没有 truncated 指标。
     要么提额并加截断计数，要么改成按知识库分桶限额。本轮只在代码注释里写明语义，没改。
