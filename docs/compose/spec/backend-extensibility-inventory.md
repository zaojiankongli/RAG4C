---
feature: backend-extensibility-inventory
status: delivered
updated: 2026-09-22
branch: main
commits: 1d2e2e6, a50c697, b93e50d, fa32684, b532304, 4cdd051, 4d842e0
---

# 后端扩展轴清单（"每个部分"的可核对版本）

判据与模式选择见 `backend-extensibility-standard.md`。本文件是四路并行普查（通知/运行事件、
企业控制面、索引摄取、API 与模型面）加上我自己一轮 grep 聚类的合并结果。
每条都带 `file:line`，可直接抽查；标 VERIFIED 的是读过代码，INFERRED 是推断。

体量基线（实跑）：后端 core 85 文件/93561 行、server 43/27687、indexing 28/7108、
retrieval 12/4606、models 3/9344、config 2/1473；字面量等值分派点 247。
**普查结论的主轴**：这 247 处里绝大多数是数据库谓词与状态机转移，属于刻意闭合；真正的开放轴是下表这些。

## A. 已完成（本轮之前 + 本轮）

| 扩展轴 | 形态 | 常驻守卫 |
|--------|------|----------|
| 切片写模式 off/shadow/active | Strategy+Registry+Template Method | `tests/test_chunk_writers.py`（12 条，含源码扫描） |
| 切分模式 recursive/parent_child/qa | Registry + 声明式路由规则表 | `tests/test_chunking_mode_registry.py`（6 条） |
| 对象存储 provider | 声明式规格表 `StorageProviderSpec` | `tests/test_storage_provider_registry.py`（7 条） |
| 检索阶段 + 可选策略 | Strategy+Registry+Template Method+Adapter | `tests/test_retrieval_stage_registry.py`（6 条，含宿主文件逐字节不变） |
| **SQL 方言（本轮新增）** | 声明式规格表 `DialectSpec` + 失败即抛 | `tests/test_dialect_registry.py`（8 条） |
| **文档类型 ↔ 能力（本轮新增）** | 声明式规格表 `DocTypeSpec`，五个消费点实时查表 | `tests/test_doc_type_registry.py`（18 条，含 5 路反向验证 + 宿主逐字节不变） |
| **provider 三家族（本轮新增守卫）** | 已是 `ProviderRegistry`，本轮补判据 | `tests/test_provider_registry_extensibility.py`（33 条，15 个宿主文件逐字节不变） |
| **来源连接器 kind（本轮新增）** | 注册表持有 `config_model` + `preflight`，HTTP 层按 kind 派发 | `tests/test_source_kind_registry.py`（10 条，含 4 路反向验证） |
| **运行事件类型分区（本轮新增）** | 声明式规格表 `RunEventTypeSpec`，两个 reducer 查表派发 | `tests/test_run_event_taxonomy_registry.py`（45 条，含 5 路反向验证 + import 期一致性栅栏） |

`core/embedding.py` / `core/llm.py` / `core/reranker.py` 经核实**本就已经到位**：
全部经 `ProviderRegistry`，`server/` 与 `config/` 里没有任何残留 `if provider ==`；
加一个 provider 的成本是 1 次注册 + 1 个类 = 0 处分支修改。三者不该合并出公共基类
（embedding 要批量切分与缓存变体、LLM 要熔断与 slot cache 与 response_format 降级、
reranker 要处理 HTTP 状态，共性只有名字）。

## B. 开放轴清单（按 价值 = 免改分支数 × 新增可能性 ÷ 风险 排序）

| # | 轴 | 站点（VERIFIED file:line 摘要） | 加一个实现要改几处 N | 迁移? | 契约? | 建议模式 |
|---|-----|--------------------------------|--------------------|-------|-------|----------|
| 1 | ~~文档类型 ↔ 能力~~ **本轮已完成**，见 A 表与 §D 2/3 | — | 0（注册即全认） | 否 | `doc_type?: string`，非枚举 | 已落 `indexing/doc_types.py` |
| 2 | ~~运行事件 `event_type` 的行为分区~~ **本轮已完成**，见 A 表与 §F | — | 0（一行声明 family/phase/rollup/effects） | 否 | `frontend/src/types/rag.ts:47-62` 手工镜像 `Literal` | 已落 `core/run_event_taxonomy.py` |
| 3 | 投影目标 × 操作（`target_store` × `operation`） | `projection_handlers.py:36-40,125-132,208,460`、`index_worker.py:110-121,153,161,184,228-231,253,257`、`state_machine.py:531-542`、`document_deletion.py:43,1068,1392,1474,1491-1750,1728`、`knowledge_consistency_api.py:60-77` | 7–9（新目标）/ 4–5（新操作） | 否（无 CHECK） | 一致性响应里出现，前端仅当展示串 | Strategy+Registry，键 `store:operation`；**碰投影栅栏，风险最高** |
| 4 | ~~来源连接器 `SourceKind`~~ **本轮已完成**，见 A 表与 §D 6 | — | 0（注册 + 挂契约两处声明） | 否 | `openapi.ts:7103,7142,7166` | 已落 `SourcePlugin.config_model/preflight` + `attach_source_contract` |
| 5 | 审批 `action_type`（控制面校验） | `enterprise_approval_control.py:236,246,269-286,316-355,2742` | 5 + 2 处 ORM CHECK + 1 迁移 | **是**（CHECK 阶梯 `catalog_schema.py:2740-2787` 记录了 6 次加宽） | openapi 5 hits | 声明式字段要求表；**必须保留独立 `if`（publish/rollback ∪ waiver 有重叠）** |
| 6 | 任务 `source_kind` | API `enterprise_task_operations_api.py:26,39,64,77,81`；核心 `enterprise_task_operations{,_service}.py:20,65,138,143,1480,1489,1506-1522,335,996-1007`；ORM CHECK `orm.py:4980` | 8（含两处 `max_length=7`，第 9 个值会被 422 静默挡掉） | **是** | openapi `:6144,6961` | 三份 dict 合成一张 `TaskSourceKindSpec` |
| 7 | 告警操作 acknowledge/suppress/resolve | `enterprise_release_quality_alerts.py:703,747,756,767,782` | 2 | 否 | 否 | Strategy+Registry（每个值要行为） |
| 8 | 通知 `source_kind` | `notification_center.py:465,480,489-496,615,678-695`、`receipts.py:258-324,519`、`materializer.py:572,746` | ~13 | **是**（`orm.py:4183,4203-4210`） | **不在 OpenAPI 里**（`approval_pending_for_me` 命中 0 次）→ 前端契约抓不到 | 声明式规格表 + 每 kind 两个 callable |
| 9 | `gate_reason → alert_type` 派生 | `alerts.py:1102-1133`（7 条顺序 if），`_ALERT_TYPES:71-80`，CHECK `orm.py:3799` | 3 | **是** | 否 | 有序 matcher 表，保末尾两条启发式的位置 |
| 10 | 身份 provider `oidc/saml` | `enterprise_identity_control.py:972-1008`，消费 `:1030,1040,1086,1269` | 3 | **是**（`orm.py:1220` + 字段组合 CHECK `0021:171`） | openapi 2 hits | Adapter（每 provider 一份字段形状）；**登录凭据信任形状，须 fail closed** |
| 11 | 身份吊销 `kind` | `enterprise_identity_control.py:1538` 驱动 6 处三元（`1545-1547,1609-1627`），仪式其余全共享 | 6 | 否 | 否 | Template Method + 规格表；**双吊销栅栏不能被表绕过** |
| 12 | 自动化 trigger/condition/action 码 | `enterprise_automation_workflows_api.py:225,233,241,556-563,568-598`；`service.py:68,2107-2291,2322,2307` | 3–4 | **是**（`orm.py:5588`） | openapi `:6371,6415` | `TRIGGER_ADAPTER_ORDER` 改为从注册表推导 |
| 13 | 文档排序 `sort` / 游标耦合 | `core/catalog.py:1304-1312,1486,1628-1639` | 3 | 否 | openapi 枚举 `:8327,8330` + 前端字面量 | `SortSpec(sql_columns, keyset_capable)`，消掉 `:1639` 特例 |
| 14 | PDF 分类 → 引擎路由 `pdf_type` | `parsers/router.py:84-124,138` | 2–3 | 否 | `parser_meta` 自由串 | 声明式表（值选外部形状 = Adapter） |
| 15 | 就绪探测的方言证明 | `enterprise_readiness_api.py:499-505,514,518,540-556,580`、`knowledge_consistency_api.py:1180` | 4 | 否 | 否 | 每方言一份 spec（connect_args/mechanism/proof_sql） |
| 16 | 审计导出格式 | `enterprise_compliance_api.py:78`；`compliance.py:1087,1166,1225,1376-1379` | 4 | 否 | 否 | Strategy+Registry（可能性低，排最后） |

**本轮之后仍为"待做"的原因**：5、6、8、9、10、12 六条要改数据库 CHECK → 按红线必须单独成切片；
3 号虽无迁移但直接压在投影栅栏上，风险最高，需要独立设计与评审。

## C. 故意不转（这一节和上表同等重要）

- **生命周期 `status` 族**（Rule/Task/Alert/Dataset/Invitation/Grant/Schedule/Workspace ~30 套）：
  有 DB CHECK 与转移约束的刻意状态机；`active/archived/removed` 反复出现是词汇巧合，不是同一个概念。
- **授权 `disabled|shadow|enforced`** 与 **`off|shadow|active`** 三条 rollout 梯（settings 的
  `schema_mode`/`ingest_ledger_mode`/`chunk_authority_mode`）：2–3 元的单向迁移梯，每个值不带行为，
  改成注册表只是搬字符串。**前一轮评审把它们排除在守卫之外是对的。**
- **角色层级**（tenant `owner/admin/editor/member`、workspace `+viewer`）：能力阶梯不是 provider；
  各消费点已 fail closed（`workspace_control.py:536` raise、`permissions_for_role` 返回空集）。
  三张并行权限表（`_ROLE_MODEL`/`_DATASET_ROLE_PERMISSIONS`/`_ROLE_PERMISSIONS`）是重复异味，不是扩展轴。
- **`binding_kind`、`subject_type`、`FolderMode`、`mutation∈{edit,delete}`、`ledger_mode` 梯、
  各 attempt/span 状态机、解析引擎内部 `mode`**：2 值闭合或由模式自身定义。
- **`_schema_type_matches` 的 `kind`**（`enterprise_workspace_control.py:505-519`）——我上一轮点名怀疑的那处：
  核实后是**模式漂移校验器**，不是类型系统；`519 return False` 让未知 kind 报 `invalid type` 从而 fail closed，
  第 5 个值只在真的新增 SQL 类型时出现，而那时迁移和规格行本来都要改。改成注册表只会新增一层
  "唯一失败方式是让模式校验静默通过"的间接。
- **provider 内核不合并公共基类**；`sources/registry.py`、`indexing/parsers/registry.py`、
  `core/graph_store_registry.py` 已经正确。
- **响应形状投影**（`enterprise_knowledge_serving_api.py:861-1013` 等按端点分叉的 `operation ==`）：
  不是一个可切换概念，包成 handler registry 只加间接。
- **解析器插件本身已可插拔**（`registry.py:72-140` 迭代已注册插件，无引擎名分支；
  `config/settings.py:982 plugins: dict[str, ParserEngineSettings]` 正是为此存在）：不需要动。

## D. 普查顺带挖出的真实缺陷（优先级高于架构）

1. **授权路径 fail-open**：`capability_state` 无声明集合、三处手写谓词不一致，
   `enterprise_access_control.py:254` 只判 `== "unavailable"` 才 raise，
   任何第四种状态会**直接落进** `evaluate_workspace_authorization`。修法是共享分类器 + `raise` 兜底。
2. **`.html` 解析成功却被贴错标签** —— **核实不成立，已按原行为留住**。本表的
   `resolved_doc_type` 只喂 `_segment` 与切分路由，**落库那份 doc_type 走别的路**
   （`server/documents.py` 用请求里的 `doc_type`，`sources/runner.py` 用裸后缀），
   所以"标成 txt"没有 observable 面。反过来，把 `.html` 登记进表**会真的改切分**：
   过去查不到 doc_type → 落回文本启发式（解析产物是带 `#` 的 Markdown）→
   MarkdownStrategy；登记后变 ParagraphStrategy。实测同一份 HTML 从 2 段变 1 段。
   那不是修缺陷而是换契约，所以 `.html` 明确不登记，理由写在
   `test_html_is_deliberately_absent_because_registering_it_would_recut`。
   顺带浮出的真缺陷另立新一条，见 §D-13。
3. **真 CSV 根本入不了库，qa 切分模式因此是死的** —— **已修**。普查只看到"路由到 qa 但没人
   产 `doc_type="csv"`"，实际更糟：`.csv` 既不在 `SUPPORTED_EXTENSIONS`（MinerU 与 docling 都
   不认领），也不在 `_PLAINTEXT_EXTENSIONS`，而 `sources/runner.py:759` 调 `add_file` 时**不传
   doc_type** → 一条 CSV 直接 `IngestError`；`server/documents.py:78` 那份文件夹导入清单同样
   没有 `.csv`，所以 UI 上传路径把它算成 unsupported。修法是让 `.csv` 同时进
   「扩展名→doc_type」与「免 parser 直读」两侧，并把那份清单改成这张表的投影。
   这是本轮唯一的**入库结果变化**：CSV 过去两条路都失败，现在按逐行 QA 对切。
   另两处 parser 边界顺带修好：`_validate_file` 改问 `self.supports()`（不再拿 MinerU
   的清单当全管线闸门），`.text` 的不对称（可读但不产 doc_type）如实保留而不是顺手统一。
4. **approver_kind 未知值两套策略**：`materializer.py:356` raise，`receipts.py:466-490` 没有 `else`
   静默返回 `False` → 一行坏数据让人"没资格"且毫无信号。
5. **`RagExecutor` 枚举漏一个值**：`rag_topology.py:24` 只列 3 个，而 `:574` 会发
   `executor="cache_replay"`，`server/app.py:1505-2043` 五处在判它。
6. **`_ALLOWED_KINDS` 死拷贝 + SourceKind 的 else 分支 fail-open** —— **已修**。普查只记下
   "`:80` 的 `_ALLOWED_KINDS` 定义后从未被引用"；做的时候查出更严重的一半：HTTP 层的两处判断
   写成 `if kind == "local_dir": ... else: 按 GitHubRepoConfig 校验 + 按 GitHub 白名单放行`，
   而 `_update_source`（`:831`）的 kind 有一个分支来自**数据库列 `source_type`**
   （`models/orm.py:6425`，`String(64)`，**没有 CHECK 约束**）——不受请求模型的 `Literal` 保护。
   于是第四种值会被默默按 GitHub 的形状与 allowlist 放过。现在 kind 只经
   `source_contract()` 派发，未知或未挂契约一律拒绝；那张死拷贝集合删掉了，常驻守卫是
   `tests/test_source_kind_registry.py`。
7. **`info` 严重度是死的**：`center.py:24` 接受 `{info,warning,critical}`，但没有任何生产者能发出
   `info`（质量告警走 2 值 `_observation_alert_severity`，审批硬编码 `warning`）。
8. **`mineru.provider` 有两个分派器**（`base.py:167-177` 与 `plugins.py:16-22`），前者不传 `mode`，
   只被 smoke 脚本用到；只在一边加 provider 会在另一边静默失效。
9. **`gate_state` 存储值 `passing`、对外 `passed`，映射手写两遍**（`scheduler.py:2816`、`:3235`）。
10. **`sources/runner.py:178` 重declare 了 `state_mode` 白名单**，且 `source_dispatcher.py:238`
    硬编码 `state_mode="database"` 绕过设置。
11. **`risk_tier {low,medium,high}` 写三遍**且抛不同异常类型。
12. **通知 `source_kind` 不在 OpenAPI 也不在前端类型里**：契约层对它是瞎的，加 kind 抓不到。
13. **落库的 doc_type 词汇与管线词汇不一致**（做 §D-2 时浮出，**未修**）：
    `sources/runner.py:798` 用 `suffix.lstrip(".").lower()` 直接当 doc_type（于是写下
    `md` / `docx` / `xlsx`），而入库管线内部用的是这张表的名字（`markdown` / `word` /
    `excel`）。`server/documents.py:1315` 的重索引又把这个落库值当 `doc_type` 传回管线 ——
    等于绕过这张表，且 `md` 与 `markdown` 会选到不同策略（实测：`strategy_for("md")` 是
    ParagraphStrategy，`strategy_for("markdown")` 才是 MarkdownStrategy，所以一份由
    源同步进来的 markdown 文档，重一次索引切法就变了）。
    修法应是 `doc_type_for_extension(suffix)` 一处换，但它会改掉**已入库文档**重索引后的
    切分方式，属于迁移语义，需要单独切片与决定，不要顺手改。

## E. 判据覆盖缺口 —— 本轮已补

`tests/` 里曾没有任何测试引用 `EMBEDDING_PROVIDERS` / `LLM_PROVIDERS` / `RERANKER_PROVIDERS`
（grep 零命中）。现在 `tests/test_provider_registry_extensibility.py`（33 条）覆盖三家族：
注册探针即经 `create_*` 本身可选 + 15 个宿主文件逐字节不变、重复登记拒绝、注册期形状检查、
`available:` 清单由注册表派生、AST 扫描"模块内不得按 provider 名分支"、
`config/settings.py` 注释里的声明集合等于注册表。`scripts/smoke_providers.py` 早就在测这些事实，
但它在 `testpaths=["tests"]` 之外 —— 那正是 §E 当时无人 enforcing 的机械原因。

同一轮另修一条**预存红**：`tests/test_graph_store_registry.py:91` 断言 `create_graph_store`
出现在 `rag.py`，但图谱装配早已迁到 `retrieval/stages.py:2015`。我单独跑它时就是红的，而
`rag.py` 与 `core/graph_store_registry.py` 当时都不在工作树的改动里 → 红在 main 上，不是本轮引入。
宿主清单跟着改，判据不动。

## F. 独立评审推翻了我自己的哪些说法（这一节是给下一位的校准，不是检讨）

判据是"新增实现零改分支"，但**守住判据的测试本身也会作假**。轴 #1 提交后由独立评审
子 agent 查出四条，全部经我复核确认并已改：

1. **"实时查表"有一条断言是同义反复**。`assert ingest_module.doc_type_for_extension(x)`
   测的还是同一个函数对象，不是 ingest 真的在实时用它。把 ingest 的两处查询改回
   import 期快照，14 条守卫**全绿**。现已改成走真实路径：`parse_and_chunk` 之后断言
   `_last_chunk_decision == "qa"`，`discover_folder_documents` 之后断言文件被放行；
   重跑两处快照变异，各自立刻变红。
   **教训**：宿主"逐字节不变"只证明没改代码，**不证明改了也不生效**；判据必须由
   行为断言承担，字节检查只是配菜。
2. **`.html` 那条"缺陷"是我记错的**（见 §D-2 的更正）：登记它不会修好任何可见行为，
   反而会换掉 HTML 的切分策略。已撤回那一行，并把不登记的理由写成测试。
3. **`is_table_like_doc_type` 顺手做了大小写与空白归一 = 多出一个归一化点**。旧代码是
   精确成员判断，而 router 入口本来就自己 `.lower()`（`explain_decision` 的
   `normalized_type`）。表再归一一次会把"谁在归一化"变成两处。评审把它记成"放宽契约"，
   我复核后**修正这个说法**：因为 router 上游已经归一，`"Excel"` 改造前后都走 qa，
   这次改动没有可观察差异 —— 但 `" excel"`（带前导空格）这类值在表层仍不该被表兜住。
   已改回精确匹配并钉住两点：表层精确、router 层归一化照旧。
4. **"五份清单"我一开始只收了四份**：`server/documents.py` 的文件夹导入清单是第五份，
   于是"CSV 现在能入库"当时只对了一半（源同步路径通了，UI 上传仍算 unsupported）。
   现在它是这张表的投影，两条路一起通。

评审另外给出两条我接受的新守卫：**声明为"可直读"的格式必须自己证明是文本**
（`read_text(errors="replace")` 会把二进制糊成一整段 U+FFFD 且不报错，一行
`readable_without_parser=True` 就能把二进制静默灌进语料库 → 现在含 NUL 直接
`IngestError`）；**少一个尾逗号的 `(".typo")` 会在注册期点名**，而不是逐字符迭代后
报出指不到真因的"扩展名要带点"。

同轮还订正两处文字：provider 守卫的宿主数是 **15** 不是 14（提交信息里的数字是错的，
代码与测试都对）；`FOLDER_IMPORT_EXTENSIONS` 里 `.html` 缺席是**正确**的，不是漏洞。

## G. 轴 #2 落地时评审查出的两件事（以及一条测量教训）

1. **注册表内核不适合放在每事件路径上**（实现 agent 自己没发现，评审量出来的）。
   复用 `ProviderRegistry` 让一次 `event_type` 查表要走 `names()`（排一次序）+
   `create()`（strip/lower 后再排一次序）。我隔离量的查表单价：
   改造前 frozenset **0.057µs** → 走内核 **1.400µs（24.4×）** → 现在 taxonomy 自持一份
   精确拼写索引 **0.090µs（1.56×）**。`ProviderRegistry.create` 也顺手改成只在拒绝时
   才构造 available 清单。教训写进标准：**"统一扩展点内核"不是免费的方向**，热路径
   要按形状查表，内核为人体工程学费的归一化在每事件路径上就是税。
2. **源码扫描只拦"已声明的名字"= 假守卫**。一条
   `if event_type == "node.timed_out":`（用一个还没声明的拼写）能完全躲过原扫描。
   现在按形状拦：reducer 源码里 `family.name` 字面量一律不许出现（变异验：加进去 6 条用例红）。
3. **性能预算用例在并发负载下会撒谎**。`test_registry_sink_production_offer_hot_path_budget`
   阈值 1.0ms，我同时跑三个子 agent 时读到 **3.008ms（红）**，隔离重跑 3 次
   **1.47 / 1.98 / 1.59s 全过**。所以这类用例的读数只在机器空闲时可信 —— 报红之前
   先确认没有别的进程在跑，别按一次污染读数去改代码。

**仍未收的同型镜像副本**（评审点名，本轮刻意不做）：`server/app.py:1430-1446` 的
`_SseRunEventSink` 又写了一遍 node-terminal / retry-terminal 两个集合（它是第三个 reducer，
转过去是对的）；`_TOPOLOGY_GROUPS` / `_TOPOLOGY_EDGE_KINDS` 与 `TopologyGroup` /
`TopologyEdgeKind` 是同一 defect class 的另一组值。

**TS 侧的契约边界要说清**：`frontend/src/api/runEventValidation.ts:3-18` 抄了全部 15 个
事件类型，`frontend/src/api/runs.ts:40-45` 抄了 4 个 executor，
`frontend/src/run/serverRunProjection.ts:97-98,279-280` 与 `runProjection.ts:366-368` 也各有
一份。但 `BackendRunEventType` 是从 OpenAPI 生成的，TS 只校验**子集**合法性 ——
也就是说 Python 侧加一种事件类型，前端不会变红，只会静默不认它。这条决定了
"加事件类型"这件事的跨栈成本仍然不是 0，只是从 8 处分支降到 2 处声明。
