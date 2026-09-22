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
| **诊断投影 parser_meta（本轮新增）** | 类型判定（JSON 标量过、容器挡）取代三处按名字点菜的键清单 | `tests/test_ingest_meta_extensibility.py`（7 条，3 路变异验证） |
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
| 11 | ~~身份吊销 `kind`~~ **本轮已完成**，见 §J | — | 0（注册即全认；栅栏由注册期拒绝而非约定） | 否 | 否 | 已落 `core/identity_revocations.py` |
| 12 | 自动化 trigger/condition/action 码 | `enterprise_automation_workflows_api.py:225,233,241,556-563,568-598`；`service.py:68,2107-2291,2322,2307` | 3–4 | **是**（`orm.py:5588`） | openapi `:6371,6415` | `TRIGGER_ADAPTER_ORDER` 改为从注册表推导 |
| 13 | 文档排序 `sort` / 游标耦合 | `core/catalog.py:1304-1312,1486,1628-1639` | 3 | 否 | openapi 枚举 `:8327,8330` + 前端字面量 | `SortSpec(sql_columns, keyset_capable)`，消掉 `:1639` 特例 |
| 14 | ~~PDF 分类 → 引擎路由 `pdf_type`~~ **本轮已完成**，见 §K | — | 0（加一类分类结果一行声明） | 否 | `parser_meta` 自由串（新增 `route_reason`） | 已落 `indexing/pdf_type_routing.py` |
| 15 | ~~就绪探测的方言证明~~ **本轮已完成**，见 §I | — | 0（注册即全认） | 否 | 否 | 已落 `core/read_only_dialects.py`；`knowledge_consistency_api.py:1180` 经核是**写事务栅栏**不是只读证明，刻意没并进来 |
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
- **`sources/github_repo.py:47` 的 `_DEFAULT_EXTENSIONS`**（做轴 #1 时被 grep 到，看着像第 6 份
  扩展名清单，其实不是）：那是抓仓库时的**内容筛选默认值**，故意比"系统能入格式"窄得多 ——
  它只收文本类文档（`.md/.mdx/.markdown/.adoc/.rst/.txt`），把 `.pdf/.docx/.xlsx` 排除在外，
  因为那些要走 parser 且体积成本完全不同。改成 `importable_extensions()` 的投影会让默认抓取
  范围一夜之间扩大到整个二进制类别，那不是统一，是改产品行为。
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
   的断言是 `p95_ms < 1.0`（**毫秒**）。我同时跑三个子 agent 时读到 **3.008ms（红）**，
   隔离重跑 3 次全过 —— 那三个读数 **1.47 / 1.98 / 1.59 是 pytest 报告的整条用例墙钟
   秒数，不是 p95 读数**（原写法漏了单位说明，被 2026-09-22 的独立评审按 p95 误读成
   "把 1.47ms 的失败说成全过"；这是我的表述缺陷，不是结论缺陷）。同日复核：机器空载时
   连跑 3 次 `1 passed in 1.56s / 1.16s / 1.27s`，全过；评审在我跑 vitest+tsc 期间跑到的
   6/6 红正是这条记录的又一次复现。所以这类用例的读数只在机器空闲时可信 —— 报红之前
   先确认没有别的进程在跑，别按一次污染读数去改代码；**引用读数时必须带上它量的是什么**。

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

## H. 第三轮独立评审（审 `23503c9` / `80ad1dc` / `b84fe0a`）查出并已修的九条

评审判 **PASS-WITH-FIXES**（三个提交各一条），我逐条回源码核对后**全部成立**，没有一条是
误报。处置与证据：

| # | 成立的事实 | 处置 | 反向验证（把修复改回旧写法） |
|---|---|---|---|
| F1 | `unregister_run_event_type` 把参数交给内核（内核按 `strip().lower()` 存键），自己却按原样 `_BY_NAME.pop`。用规范外的写法撤回 → 一份存储被清空、另一份仍活着，`resolve` 继续命中而枚举已不认识它 | `core/run_event_taxonomy.py` 撤回时归一一次再用 | 改回 `_BY_NAME.pop(event_type, None)` → `test_withdrawing_with_a_non_canonical_spelling_moves_both_stores` 红 |
| F6 | 我上一轮把 parser_meta 从"按名字点菜"改成"按类型投影"时，让**后写的解析器键盖掉了管线自己的诊断键**：一个 parser 只要肯写 `chunking_mode`，就能改写诊断面上唯一能看见切分事实的那个键 | `meta.setdefault` —— 解析器只补齐，管线键先写者胜 | 两处退回 `meta[key]=value` → `test_a_parser_cannot_rewrite_the_pipelines_own_diagnostics` 红 |
| F5 | `test_a_new_scalar_field_reaches_the_db_without_editing_ingest` 手工拼 `_last_parsed_metadata`，只证明了 `ingest_meta` 认它，没证明生产方能把它送到那里、也没证明字符串化后过得了 catalog 的解码（判据 §3.1：字节不变 ≠ 改了也不生效，反过来说**手搭输入也不等于真生产方接得上**） | 该用例改为真解析器 → `parse_and_chunk` → `ingest_meta` → `json.dumps` → `_coerce_document_parser_meta` / `_document_management_values` 逐跳断言 | 摘掉投影循环即红（上一轮 M9 已验） |
| F3 | 热路径 23 倍回归是**行为等价**的：`_BY_NAME.get(x) if x in RUN_EVENT_TYPE_SPECS.names() else None` 语义完全正确，95/95 全绿，那条 p95 预算用例也区分不出来 | 新增形状守卫 `test_the_hot_path_looks_up_in_its_own_index_not_the_kernel_menu`，并在用例里**如实写明它不是行为证据** | 套用上面那个等价写法 → 该守卫红；同时先断言计数桩真的接到了 `names()`，否则这条守卫是空跑（第一版就是空跑，被自检抓出来） |
| F4 | rollup 装饰词的用例断言 `status == spec.rollup_status(t)` —— 拿被测方法比自己，`rollup_status` 忽略 `rollup_prefix` 也只有 1 条红 | 改成按字面钉住 `completed` / `retry_completed` / `retry_running` / `retry_unknown`，并断言 node 与 retry 同后缀不许装饰出同一个词 | 让 `rollup_status` 忽略前缀 → 2 条红 |
| F8 | QA 深链的 `stamp` 一旦置起就不再复核：定位成功后那条 QA 被改到不满足筛选、或被别人过期掉，`focusedQaId` 仍留着而提示为 null —— 一个既不标蓝也不提示的第三种状态，且永久 | 命中后每次列表变化都复核：消失→清标记+给提示并清 stamp，回来→重新定位 | 退回 `if(...===stamp)return` → `test_...从结果里消失时如实退回未命中` 红 |
| F9 | 深链监听两条路只测了一条（用例在 render **前**改 hash，走的是初始 state 读取）。摘掉 `popstate`/`hashchange` 任一 `addEventListener`，21/21 全绿 | 各补一条"页面挂着时"的用例 | 分别摘掉两个监听 → 各自那条红 2 行 |
| F2 | 我的台账把 pytest 的**整条用例墙钟秒数**写在"阈值 1.0ms"旁边且没标单位，读起来就像"1.47ms 的失败被我写成全过" | 补单位说明 + 本次空载复测读数；结论（负载下会撒谎）不变 | —— 属于表述缺陷，见 §G-3 |
| F7 | 评审主动核了五个生产方的 `metadata` 字面量：没有凭证形状进来（`api_key`/`api_base` 不进 parser metadata）。真正的风险就是 F6 | 无需额外处置 | —— |

**评审还点名了两条刻意不动**：M6（关掉 local_dir 的本地重复检查仍全绿，但内核照样拒，
判为良性）；`resolve_run_event_type` 对未知类型继续 fail-soft（改前改后都不抛，§A 已记）。

## I. 轴 #15（只读证明的方言表）落地记录

`server/enterprise_readiness_api.py` 原先把"怎么把连接开成只读"和"怎么证明它是只读"
写成两份手写清单：`prove_read_only_engine` 里一张 `backend -> mechanism` 字典 + 一条
`if backend == "postgresql"` / mysql 两条语句的 ladder，`_non_sqlite_read_only_engine_options`
里又一串 `if backend == ...`。加一种方言要改的正是**决定"能不能对现网声称只读"的那个函数**，
而那里最危险的错法是静默的：字典里没有这条方言时 `expected_mechanism` 是 `None`，
旧代码用 `!=` 比较所以是 fail-closed，可一次不留神的改写就会把它变成"证明不了 = 证明得了"。

现在是一张声明表 `core/read_only_dialects.py`：`ReadOnlyDialectSpec(dialect, mechanism,
connect_args, live_probe_required, proof_statements, accepted_values)`。三条刻意的形状选择：

- **一份存储、精确拼写查表**。没有再挂 `ProviderRegistry` 内核（轴 #2 那次"两个存储不同步"
  的真缺陷在这里根本不存在）；`resolve_read_only_dialect` 只认 SQLAlchemy 自己那个小写的
  `dialect.name`，未注册一律 `None`，两个调用点都必须把 `None` 当作"证明不了"。
- **`connect_args` 空的声明不构成"开得出只读连接"**。靠 URI 证明自己的 sqlite 走的是
  另一条（重写 URL）路径，所以它在证明侧有声明、在开连接侧仍返回 `unsupported`。
  这条限制写在代码注释里，**没有**假装表能表达一切：要支持"改 URL 才算只读"的方言，
  还得给 spec 加一种 URL 重写形状，那是独立一次设计。
- **MySQL 两条探测语句的顺序是契约**（现代名在前、`tx_read_only` 兜底在后），所以按字面钉住。

判据与门禁读数（本机 `.venv`，`PYTHONPATH=.`）：

- 新守卫 `tests/test_read_only_dialect_registry.py`：**16 条**，走真行为 —— 注册一种
  `probekind` 方言后，宿主的 `prove_read_only_engine` 与 `_non_sqlite_read_only_engine_options`
  立刻认它（机制对得上+服务端答 yes → True；机制错 → False；答 no → False），
  而 `server/enterprise_readiness_api.py` **逐字节不变**。
- 行为等价（判据 §3.3，未改任何断言）：`tests/test_enterprise_readiness_api.py` 93 条 +
  `tests/test_enterprise_automation_workflows_readiness.py` 29 条 + 新守卫 16 条 =
  **138 passed in 156.50s**；`ruff check .` 全仓通过。
- 反向验证（§3.4，脚本与输出都放在仓外 `%TEMP%
ag4c-verify`）：8 个变异各自 turn 红 ——
  未注册方言改成"证明不了就算证明"（2 红）、宿主退回手写清单（2 红，其中一条是宿主守卫）、
  只删 `isinstance` 检查（1 红）、整个形状校验提前 return（7 红）、撤回不归一键名（1 红）、
  MySQL 两语句颠倒（2 红）、开引擎丢掉声明的 connect_args（1 红）。
  **第一轮有一个变异逃逸**：我只删了 `_validate_spec` 的 `isinstance` 分支却全绿 ——
  因为当时没有任何用例去注册一个非 spec 对象。补了 `test_only_a_real_declaration_can_be_registered`
  之后同一变异转红。这正是判据 §3.4 说的"破坏了要能说清哪条用例红"，而不是"我写了守卫"。

## J. 轴 #11（身份吊销 kind）落地记录，含一次我自己写出的假断言

`_simple_state_mutation` 里八处 `kind == "domain_revoke"` 三元回答"撤销这种资源有什么不
一样"（预留哪个 operation、锁哪张表、审计动作、响应键名、让出哪一列），其余仪式（幂等预留、
`SELECT … FOR UPDATE`、revision CAS、审计信封）全共享。收成 `core/identity_revocations.py`
的 `RevocationKindSpec` 之后顺手关掉一个**真实的静默 fail-open**：三元链的 `else` 会把任何
没预期的 kind 当成 SCIM token 撤销 —— 换了表、换了审计动作，还是一次真写入。现在未声明
一律 `ValueError`。

**栅栏做成注册期的，不是约定式的**：`status` / `revision` / `revoked_at` / `revoked_by` /
`updated_at` 这五列"就是撤销本身"，`release_values` 若返回其中任何一列，注册直接被拒。
旧写法里这个风险是隐形的（新 kind 若忘了 CAS 列就可能把撤销改成一次普通更新）。

门禁读数（本机 `.venv`，`PYTHONPATH=.`）：

- 新守卫 `tests/test_identity_revocation_registry.py` **23 条 / 2.53s**，其中两条是
  **真走一遍仪式**：注册 `probe_revoke` 后 `_simple_state_mutation` 立刻按声明锁表、
  按声明出响应、按声明让出列，而 `core/enterprise_identity_control.py` **逐字节不变**；
  另一条把声明里的 `table_name` 换成 scim 表，同一个 id 就 `IdentityNotFound` ——
  证明表是从声明来的，不是从 kind 猜的。
- 行为等价（判据 §3.3，未改任何既有断言）：
  `tests/test_enterprise_identity_federation_api.py` + `tests/test_oidc_sso_runtime_api.py`
  = **39 passed in 65.57s**；`ruff check .` 全仓通过。
- 反向验证 7 个变异，第一轮 **D 逃逸**：把 `values.update(spec.release_values(actor_id))`
  改成 `pass`（仪式彻底不听声明）居然 23 条全绿 —— 因为我断言的是 `updated_by`，而
  **建域名时那一列就已经是 owner-a**，断言区分不出"仪式写了"和"本来就写着"。换成让探针
  写一列只有声明能改动的 `txt_value` 并断言其值，同一变异立刻转红。
  教训与 §3.4 同源：**断言必须选在"只有被改的那个机制会让它成立"的位置上**，
  否则写测试的人会以为自己钉住了，而变异跑会当场拆穿。

## K. 轴 #14（PDF 分类 → 引擎路由）落地记录

`DocumentRouter.route` 原先是一条三枝梯（`text_based`→fast、`mixed`→vision、其余→vision），
`DocumentRouter.parse` 又是一条 `if engine == "fast" / else vision`。两处各自有一个"没说出口的
决定"：

- **没预期的分类结果**落进 `else`，今天恰好是较重的引擎，但那是分支顺序的巧合，没有任何东西
  阻止下一次编辑把便宜引擎放进 `else`。现在保守落点是表里的一个值
  `UNKNOWN_PDF_TYPE_ROUTE`，并且理由随决策一起进 `parser_meta`（新键 `route_reason`，
  操作员能在诊断面看见"这本为什么走 vision"）。
- **不认识的引擎标签**在 `parse` 里静默按 vision 跑。现在直接抛 `MineruParserError`：
  标签必须是真跑得起来的那两个之一，注册期也照样拒（`engine not in ENGINE_NAMES`）。

**这条轴此前完全没有用例覆盖**（`grep -rln DocumentRouter\|create_document_parser tests/` 只
命中新加的这份与一条 run-registry 集成套件），所以新守卫同时是把旧行为第一次钉住：
四条内建分类结果的引擎、混合型走 vision 的 MVP 理由，都按字面写进了断言。

判据与门禁（本机 `.venv`，`PYTHONPATH=.`）：

- `tests/test_pdf_type_routing_registry.py` **15 条 / 0.26s**：注册 `vector_heavy → fast` 之后
  `route()` 与 `parse()` 立刻照声明办事（真调 fake parser，断言 fast 被调用 1 次、vision 0 次），
  而 `indexing/parsers/router.py` **逐字节不变**；撤掉声明后同一本回到保守落点。
- 行为等价（未改任何既有断言）：`test_ingest_meta_extensibility / test_doc_type_registry /
  test_chunk_diagnostics` = **26 passed**，`test_run_registry_integration`（引用 `router_on`）
  = **28 passed in 246.91s**；`ruff check .` 全仓通过。
- 反向验证 5 个变异全部转红：宿主退回分支梯（3 红）、未声明分类改成走便宜引擎（2 红）、
  注册期不校验（6 红）、允许拼错的引擎标签（2 红）、去掉"不认识的引擎标签要报错"（1 红）。
  脚本与输出在仓外 `%TEMP%ag4c-verify`。

**没假装做到的部分**：加一个**引擎**仍然要动两处（`ENGINE_NAMES` 一项 + `parse` 里的分发），
因为引擎是真解析器对象而不是标签；这条轴免改的是"新增一类分类结果"。
