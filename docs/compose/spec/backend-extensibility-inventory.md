---
feature: backend-extensibility-inventory
status: delivered
updated: 2026-09-23
branch: main
commits: 1d2e2e6, a50c697, b93e50d, fa32684, b532304, 4cdd051, 4d842e0, 0a825f6, fa52c4c,
  c5c02ee, 227f0f4
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
| **`parser_meta` 字符串键筛选/分面（本轮新增）** | 共享 helper 三件套：一个键一行声明，不再一个键一条内联分支（`engine` 与 `chunking_reason_code` 走同一条路） | `tests/test_document_catalog_api.py`（新增 4 条 + 分面/游标/旧库拒绝断言），落地记录见 §R |
| **可查看来源后缀（补登记，落地记录见 §S）** | 声明式规格表 `SourcePreviewSpec`：一个后缀一行，content type / 可否 inline / 大小上限同源；HTTP 层与取文件层都只查表 | `tests/test_source_preview_registry.py`（36 条）+ `tests/test_knowledge_source_preview_api.py`（14 条）+ `frontend/src/parse-intervention/components/SourcePreview.test.tsx`（10 条） |

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
| 7 | ~~告警操作 acknowledge/suppress/resolve~~ **本轮已完成**，见 §M | — | 0（注册即全认；两条槽位栅栏 + 列名核对齐模型） | 否 | 否 | 已落 `core/quality_alert_operations.py` |
| 8 | 通知 `source_kind` | `notification_center.py:465,480,489-496,615,678-695`、`receipts.py:258-324,519`、`materializer.py:572,746` | ~13 | **是**（`orm.py:4183,4203-4210`） | **不在 OpenAPI 里**（`approval_pending_for_me` 命中 0 次）→ 前端契约抓不到 | 声明式规格表 + 每 kind 两个 callable |
| 9 | `gate_reason → alert_type` 派生 | `alerts.py:1102-1133`（7 条顺序 if），`_ALERT_TYPES:71-80`，CHECK `orm.py:3799` | 3 | **是** | 否 | 有序 matcher 表，保末尾两条启发式的位置 |
| 10 | 身份 provider `oidc/saml` | `enterprise_identity_control.py:972-1008`，消费 `:1030,1040,1086,1269` | 3 | **是**（`orm.py:1220` + 字段组合 CHECK `0021:171`） | openapi 2 hits | Adapter（每 provider 一份字段形状）；**登录凭据信任形状，须 fail closed** |
| 11 | ~~身份吊销 `kind`~~ **本轮已完成**，见 §J | — | 0（注册即全认；栅栏由注册期拒绝而非约定） | 否 | 否 | 已落 `core/identity_revocations.py` |
| 12 | 自动化 trigger/condition/action 码 | `enterprise_automation_workflows_api.py:225,233,241,556-563,568-598`；`service.py:68,2107-2291,2322,2307` | 3–4 | **是**（`orm.py:5588`） | openapi `:6371,6415` | `TRIGGER_ADAPTER_ORDER` 改为从注册表推导 |
| 13 | ~~文档排序 `sort` / 游标耦合~~ **本轮已完成**，见 §L | — | 0（新增一个排序一行声明；**第二个 keyset 排序被注册期拒绝**） | 否 | openapi 枚举未动（加排序仍需同步枚举，见 §L 的诚实边界） | 已落 `core/document_sorts.py` |
| 14 | ~~PDF 分类 → 引擎路由 `pdf_type`~~ **本轮已完成**，见 §K | — | 0（加一类分类结果一行声明） | 否 | `parser_meta` 自由串（新增 `route_reason`） | 已落 `indexing/pdf_type_routing.py` |
| 15 | ~~就绪探测的方言证明~~ **本轮已完成**，见 §I | — | 0（注册即全认） | 否 | 否 | 已落 `core/read_only_dialects.py`；`knowledge_consistency_api.py:1180` 经核是**写事务栅栏**不是只读证明，刻意没并进来 |
| 16 | ~~审计导出格式~~ **本轮已完成**，见 §N | — | 0（注册即全认；认不出的存储行改判完整性失败） | 否 | 否 | 已落 `core/audit_export_formats.py` |
| 17 | **capability 状态生产者**（本轮 §U-2 新登记，未动手） | `core/catalog_schema.py`：11 个 `inspect_*_capability` 名字 / 18 个 def（其中 7 个名字是「冻结-重绑」垫片，**不是缺陷**，见 §U-2）；状态字面量另有 11 处 `return "not_available", ()` 与 61 处 `"unavailable"` | 当前 ≈2（通用垫片 + 各 producer 自己的 ladder 一行） | 否 | 否：`state` 是自由字符串，消费侧三处都已 total（见 §D-1 更正） | 声明表 + 单一 ladder 实现；**硬约束：必须保住 `_KNOWLEDGE_SERVING_ORIGINAL_*` 冻结版的行为等价**，风险与 §D-6 同源 |

**本轮之后仍为"待做"的原因**：5、6、8、9、10、12 六条要改数据库 CHECK → 按红线必须单独成切片；
3 号虽无迁移但直接压在投影栅栏上，风险最高，需要独立设计与评审。 17 号是新登记的一条，不在「顺手可修」里：每次改动都要同时证明与冻结版行为等价，而收益只是少抄一处 ladder。

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

> **本轮（2026-09-22 夜）已把本表逐条复读了一遍**，结论如下；普查是在 `a50c697` / `4cdd051` 等
> 修复**之前**做的，而修的时候没回来销账，所以有两条已经修完却仍列为开放缺陷。
>
> | 条 | 复读结论 |
> |---|---|
> | 1 授权 fail-open | **已修**（`a50c697`）：共享分类器 + 三个消费点都拒绝未知状态 |
> | 2 `.html` 贴错标签 | 普查时已自证**不成立**，按原行为留住（行内已记） |
> | 3 CSV 入不了库 | **已修**（行内已记） |
> | 4 approver_kind 两套策略 | **真实但降级**：两条 CHECK 把 `approver_kind` 与 `approver_ref=account_id/group_id` 钉死，所以"一行坏数据让人没资格"不可触达；剩纵深防御 + 一致性债务（详见行内，含我本轮一个被证伪的假设） |
> | 5 `RagExecutor` 漏值 | **已修**：`Literal[tuple(RAG_EXECUTORS)]` 从单一声明集派生 |
> | 6 `_ALLOWED_KINDS` + SourceKind else fail-open | **已修**（`4cdd051`，行内已记） |
> | 7 `info` 严重度是死的 | **仍在**：`core/enterprise_notification_center.py:24` 仍收 `{info,warning,critical}` |
> | 8 mineru 两个分派器 | **本轮没定位到**：普查给的 `mineru/base.py` / `mineru/plugins.py` 在本仓不存在（`find` 无命中）。要么路径记错，要么模块已搬；**不要按这条排活**，先重新定位 |
> | 9 `gate_state` 别名 | **仍在，且普查低估**：是 5 处不是 2 处（3 处转换含筛选入参那一处，2 处并列两种拼写），详见行内 |
> | 10 `state_mode` | **仍在，面已数清**：词表 2 处 + 三处成员判断（`:247`/`:258`/`:339`）可安全收成声明表；`server/source_dispatcher.py:238` 那处硬编码是**行为变更**，留在片外等裁定 |
> | 11 `risk_tier` 写三遍 | 词表确在 `catalog_migrations/versions/0029-0031` 与 CHECK 里 → **需要迁移**，维持原判（单独成切片） |
> | 12 通知 `source_kind` 不在契约 | **未核实**：`server/` 里没 grep 到通知侧的 `source_kind` Literal，而 `openapi.json` 有 `source_kinds`（复数）三处 schema。普查那句需要先重定位再说 |
> | 13 落库 doc_type 词汇不一致 | 需要产品裁定，维持原判 |
>
> 抄一条给自己和下一位：**普查记的是短文件名**（`receipts.py` / `materializer.py` / `base.py`），
> 实际路径带前缀（`core/enterprise_notification_*.py`），而 `rag_topology.py` 在**仓库根**不在 `core/`。
> grep 不到不等于已删除。



1. **授权路径 fail-open** —— **已修（`a50c697`）；本节先前把它留在"开放缺陷"里是一笔过期的账**。
   现在的形状：`core/catalog_capability.py` 声明 `CAPABILITY_STATES` 全集 +
   `require_safe_capability_state`（**构造上 total**：不在集合里的状态一律 raise，不会静默扩大
   放行条件）。三个授权敏感消费点逐一读过是否闭合：`enterprise_access_control.py:255` 走共享
   分类器；`enterprise_workspace_authorization.py:178-183` 只放行 `not_available`、其余非 `ready`
   一律 raise；`enterprise_workspace_control.py:636` 判 `!= "ready"` 就抛。**第四种状态在三处都
   不能放行**，这一条已无 fail-open 面；剩下的只是"三处各写一遍、报错文案不同"的可读性债务。
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
4. **approver_kind 未知值两套策略** —— **本轮复读：仍然真实，但优先级要下调，且普查那句话里
   有一半是猜错的**。
   两套策略确认：`core/enterprise_notification_materializer.py:350-357` 是
   `account / role / group` 加 `else: raise`；`core/enterprise_notification_receipts.py:464-490`
   三个 `if` 之后**没有 else**，未知 kind 落到 `return False`。
   **但"一行坏数据就能让人没资格"这件事被约束挡住了**：`models/orm.py` 里
   `TenantApprovalPolicyApprover` 带
   `CheckConstraint("approver_kind IN ('account','role','group')")`，还带一条
   `(kind='account' AND approver_ref=account_id) OR (kind='group' AND approver_ref=group_id) OR …`
   的形态约束。**顺带证伪了我自己本轮的一个假设**：materializer 读 `row.account_id or row.approver_ref`
   而 receipts 只读 `row.approver_ref`，看着像"两边认不同列 → 谁有资格可以不一致"，
   但那条 `approver_ref=account_id` 的 CHECK 正好把这两者钉成恒等（与 §D-6 那个
   `source_type` **没有** CHECK 所以第四值真能进来的情形相反）。
   所以这条是**纵深防御 + 一致性债务**（未知值该在一处判死，而不是一个 raise 一个静默 False），
   不是可触达的授权缺陷；与 §D-1/§D-5 一样，它排在"顺手可修"档，不排在"优先级高于架构"档。
5. **`RagExecutor` 枚举漏一个值** —— **已修**（本轮复读时发现，账没跟上）。现在
   `rag_topology.py:24-27` 是 `RagExecutor: TypeAlias = Literal[tuple(RAG_EXECUTORS)]`，
   从 `core/run_events.py` 那一份声明的 executor 集合派生，源码注释就写着"曾经手写三个值而漏掉
   `cache_replay`"。**同 §U-1 那一课：这条也停在"开放缺陷"里，会把下一位的预算引到已经做完的事上。**
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
9. **`gate_state` 存储值 `passing`、对外 `passed`** —— **曾仍在、现已收口（见 §V）**；普查记的"两遍"是低估。
   本轮读码数出来是 **5 处**知道这个别名，分两种性质：
   * 三处**做转换**（各写一遍方向相反的三元式）：
     `core/enterprise_release_quality_scheduler.py:2816`（写库：`passed → passing`）、
     `:3235`（出参：`passing → passed`）、`:3286-3287`（筛选入参：`passed → passing`）。
     第三处普查漏了 —— 它是最容易漏的一类：转换不只在"读/写实体"上，还在**查询谓词的入参**上。
   * 两处**同时列两种拼写**当合法词表：
     `core/enterprise_release_quality_alerts.py:81` 与
     `server/enterprise_release_quality_operations_api.py:50` 都写
     `{"passing","passed","waived","not_required","blocked","unavailable"}`。
   **形状**（已实现，见 §V）：一对常量 +
   `to_storage()` / `to_public()`（未知值 raise，不静默透传）+ 一个 `GATE_STATES_PUBLIC`；
   三处转换改调函数，两处词表改成从同一声明派生
   （`GATE_STATES_PUBLIC | {GATE_PASSED_STORAGE}`）。
   常驻守卫三件：别名字面值钉死、未知值必须 raise、源码扫描禁止再出现
   `"passing" if` / `"passed" if` 这种就地三元式（与 `tests/test_chunk_writers.py` 同型）。
10. **`state_mode` 词表重declare + 派发处硬编码** —— **仍在，本轮把面数清；两半的风险不同，
    必须分开做**。普查那两句都对但都只说了一半。
    词表这一侧（**安全**，纯形状改造）：
    * `config/settings.py:1027` 是设置侧真值：`Literal["json","dual","database"] = "json"`；
    * `sources/runner.py:178` 把同一集合又写成一份运行时白名单；
    * `:247` 与 `:339` 问 `in {"dual","database"}`（"这个模式写不写 ledger"），
      `:258` 问 `in {"json","dual"}`（"写不写 JSON"）—— **这两问才是真正的轴**：
      加第 4 个模式要记得在三个地方各自补一遍成员，漏一个就是静默少写一侧状态。
    建议形状：每个模式一条声明（`writes_json` / `writes_ledger` 两个布尔），runner 改成问声明；
    设置侧的 `Literal` 与声明表由一条常驻守卫对账（同一手法见 §G）。
    派发那一侧（**是行为变更，不是重构**）：`server/source_dispatcher.py:238` 现在无条件
    `state_mode="database"`。改成读设置会让"设置里写着 json/dual 的部署"**在升级后换一条状态写入路径**
    —— 与 §D-2 里"登记 `.html` 会真的改切分"同一类：形状上看是修一致性，语义上是换契约。
    **所以：第一片只做词表 + 三处成员判断收口，硬编码那一行留在片外，等一次明确裁定。**
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
ag4c-verify`）：7 个变异各自 turn 红（下列七条；上一版写成"8 个"，比列举多一个）——
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
的 `RevocationKindSpec` 之后把三元链的 `else`（会把任何没预期的 kind 当成 SCIM token 撤销 ——
换表、换审计动作、还是一次真写入）换成未声明一律 `ValueError`。
**修正上一版的说法**：那是纵深防御，不是"真实的静默 fail-open"。`_simple_state_mutation`
只有两个调用方，`kind` 是 `Literal[...]` 且两处都是字面量，HTTP 层从不把请求里的值当 kind 传
进来 —— 那个 `else` 在生产里够不到。第四轮评审点名之后核过：三个事实全部成立。

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
  `UNKNOWN_PDF_TYPE_ROUTE`，并且理由随决策一起进 `parser_meta`（新键 `route_reason`）。
  **修正上一版的说法**：落地那一条提交里 `route_reason` 一个消费者都没有（`git grep` 只命中
  声明与测试），"操作员能在诊断面看见"当时是假的。`23cfbe6` 才补上消费方：前端解析上下文
  多一行"引擎判由"，并在降级时把整块提示换成告警色。
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
  脚本与输出在仓外 `%TEMP%
ag4c-verify`。

**没假装做到的部分**：加一个**引擎**仍然要动两处（`ENGINE_NAMES` 一项 + `parse` 里的分发），
因为引擎是真解析器对象而不是标签；这条轴免改的是"新增一类分类结果"。

## L. 轴 #13（文档目录排序）落地记录，含一次"探针选错方向"的自纠

原先关于排序的事实散在**四处**，必须彼此一致：一个接受名字的 frozenset、一张建 ORDER BY
表达式的梯、一张挑方向的梯（两个 `asc` 加一个"其余按 desc"的 `else`）、以及游标路径里五处
写死的 `"updated_at_desc"`。真正会**静默出错**的是挑方向那张梯：新加一个*升序*排序若忘了改它，
它会掉进 `else` 反着翻页 —— 返回的还是一页看着完全正常的文档，只是 next_cursor 与翻页语义
已经坏了，没有任何地方报错。

`core/document_sorts.py` 的 `DocumentSortSpec(sort, direction, expression, keyset_cursor)`
把这四处收成一行声明。**keyset 游标刻意保持单一实现者**：谓词写的是 `<`（降序），而游标里编码的
那个值现在由声明自己交出（`cursor_value`，内建那行取 `updated_at or created_at` 的 coalesce），
所以第二个 `keyset_cursor=True` 不是"暂时没做"而是
**会翻错页**，于是注册期直接拒绝，并说明要加第二个得同时改谓词与取值两处。这跟轴 #11 的
栅栏同源：**能把"做不到的事"变成报错，就不要让它变成看起来成功的错答案。**

门禁读数（本机 `.venv`，`PYTHONPATH=.`）：

- 新守卫 `tests/test_document_sort_registry.py` **12 条 / 5.18s**。判据是行为性的：注册
  **两个新名字**共用同一表达式、只有声明的 direction 不同，翻页结果必须相反；
  同时 `core/catalog.py` **逐字节不变**。另有真游标往返（limit=1 拿 next_cursor 再取下一页，
  断言第二页不同）与"格式正确但排序没声明游标能力 → 照样拒"。
- 行为等价（未改任何既有断言）：`list_documents_page` 的全部六个消费方套件
  `test_document_catalog_api`(19) + `test_document_delete_api_v2` + `test_document_management_settings`
  + `test_document_source_identity` + `test_documents_folder_ingest` + `test_phase7_usage_accounting`
  = 该条数字当时是错的（44+34=78，而六个套件只有 66 条），已由 §O 的实测读数取代：
  六个消费方套件连同四张表的全部守卫在 `ruff check .` 通过后一并重跑，见 §O 第一条。
- 反向验证 5 个变异全部转红：方向退回按名字硬编码（2 红）、允许第二个 keyset 排序（2 红）、
  游标解码退回比字面量（1 红，宿主守卫）、表达式梯退回硬编码（2 红）、去掉游标能力闸门（1 红）。
- **第一版探针选错了方向**：我原本注册的是 `created_at_desc`（降序），而旧写法"其余按 desc"
  恰好也对，于是"方向退回硬编码"这个变异**全绿**。换成"一个新升序 + 一个新降序共用表达式、
  断言两者相反"才把它抓住。与 §J 同一条教训：**探针要挑那个只有被改机制能答对的取值**。

**没做到的部分（诚实边界）**：对外可见的排序枚举仍在 OpenAPI 契约与前端字面量里
（`openapi.ts:8327,8330` 一类），所以"新增一个排序"仍然是**后端 0 处宿主分支 + 契约 1 处枚举**。
这条轴消掉的是会静默翻错页的那部分，不是跨栈成本为 0。

## M. 轴 #7（质量告警操作）落地记录

`_mutate_alert` 原先用一条 `if/elif/else` 把每个操作的全部差异写在一起：允许从哪些状态出发、
盖哪几列、让出哪几列、审计动作、回执文案。共享仪式（revision CAS、`session.flush()`、
来源再校验、before/after 审计信封、幂等重放）本来就是对的，也正是**不该再被逐操作重写**的部分。

两处原先是"实现细节"、其实是**不变量**，现在挪到注册期判定：

1. 目标状态是终态 ⇒ 必须让出 `active_alert_key`。那一列是"同一指纹只允许一条活告警"的
   部分唯一槽位：终态却不释放，要么把同一指纹的下一条告警永远堵死，要么让已解决的告警占着
   活跃位。
2. 目标状态非终态 ⇒ 不许让出 `active_alert_key`。活跃中就把槽位释放，等于允许同一指纹
   并存两条活告警。

再加一道这一族特有的核对：`configure_alert_model(hasattr 模型列)` 在宿主 import 时把**已声明**
的列全部对模型重查一遍，之后每条新注册也当场查。为什么需要它：SQLAlchemy 实例上 `setattr`
一个模型没有的名字**不报错**，只在 flush 时丢掉 —— 那会造出一类"回执 200、审计也写了、
但什么都没改"的操作。列名也不靠命名约定推导：抑制没有 `suppressed_at`，只有
`suppressed_until`，用"目标状态 + 后缀"生成列名会直接写出模型上不存在的列。

门禁读数（本机 `.venv`，`PYTHONPATH=.`）：

- 新守卫 `tests/test_quality_alert_operation_registry.py` **19 条 / 13.02s**，含判据的
  行为半边：注册 `defer_quality_alert`（目标 suppressed、署名列、释放列集都是新声明）后，
  **未改一行的仪式**真把它执行出来 —— `status=200`、`body["alert"]["status"]=="suppressed"`、
  `revision` 由 1 推到 2、DB 里 `suppressed_by/comment/until` 与 `active_alert_key` 都在，
  而 `core/enterprise_release_quality_alerts.py` **逐字节不变**；撤掉声明后同一操作再调用即拒。
- 行为等价（未改任何既有断言）：`test_enterprise_release_quality_alerts.py` +
  `test_enterprise_release_quality_operations_api.py` = **16 passed in 70.54s**（宿主二次
  改动后重跑一遍仍 16 过）；`ruff check .` 全仓通过。
- 反向验证 5 个变异，**全部由新守卫自己抓住**（不需要拿既有套件当证据）：仪式只改状态不写
  声明列（2 红）、终态必须释放槽位不拦（1 红）、非终态不许释放不拦（1 红）、列不存在也照收
  （1 红）、未声明操作被默认接住（1 红）。脚本与输出在仓外 `%TEMP%
ag4c-verify`。

顺带消掉的重复：`_ACTIVE_ALERT_STATUSES` 之前在宿主里自己抄了一份状态分区，现在指向表的
`ACTIVE_ALERT_STATUSES`，状态只有一处定义。

## N. 轴 #16（审计导出格式）落地记录，含两个逃逸变异逼出的两处真补强

四处判断原先必须彼此一致：接受名字的校验（连同那句给用户看的文案）、序列化器的 `if/else`、
文件名扩展名、下载响应的媒体类型。其中两处**会产出一个错的工件而不报错**：

- `_serialize_export` 的 `else` 出 CSV —— 一个没被识别的格式名会拿到 CSV 字节，而任务行上写着
  别的格式；
- 下载路径再用两个 `if format_name == "ndjson"` 从**存储行**推媒体类型与后缀 —— 存着不认识格式的
  任务会被当作 `.csv` / `text/csv` 发出去。
两处现在都是拒绝（后者报 `compliance_export_integrity_failed`）。

**两个变异第一轮逃逸，都指向真缺陷**（这正是 §3.4 的用途，不是走流程）：

1. `C 下载侧认不出就当成 csv` 全绿 —— 因为我只有"没有 `if format_name ==` 了"这种源码级断言，
   行为上那条拒绝**没有任何用例能触及**（要跑通下载得先造一个真导出任务 + 存储文件）。
   修法是把贴标签这一步抽成 `_download_format(format_name)` 纯函数，宿主调用它，于是
   `test_a_stored_row_with_an_unknown_format_is_refused_not_served_as_csv` 能直接把它判红；
   顺带这条测试也变成"注册新格式后下载侧实时认它"的宿主零改动证据。
2. `D 注册期形状判死关掉` 全绿 —— 是我变异写坏了（只把 `isinstance` 那一句改成永假，其余校验
   照常跑）。换成整个 `_validate` 首行 `return` 之后 6 条红。**记下来：判红之前先确认变异真的
   把机制拿掉了**，否则我会误判"这条守卫是好的"。

门禁读数（本机 `.venv`，`PYTHONPATH=.`）：新守卫
`tests/test_audit_export_format_registry.py` **15 条 / 1.16s**；行为等价未改任何既有断言
`test_enterprise_audit_compliance_api.py` + `..._migration.py` = **14 passed**；
`ruff check .` 全仓通过。反向验证 7 个变异（A 未知格式当 csv 2 红、B 扩展名退回硬编码 1 红、
C 1 红、D 6 红、E 丢 BOM 1 红，加两轮复跑）全部由新守卫抓住。

**没做的部分**：`AuditExportFormatSpec` 只声明"叫什么/怎么标/怎么产字节"，导出任务的
DB `CHECK (format IN ('ndjson','csv'))` 仍是写死的（属 §B 里"要迁移"那一族），所以真加一种格式
仍然需要一次 catalog 迁移把那一列放宽 —— 这条轴免掉的是四处代码判断，不是全部跨层成本。

---

## O. 第四轮独立评审（审 `bbb1029` 前后四张表）查出的十三条，逐条处置

评审 verdict 是 **FAIL**：13 个缺陷 + 4 处说法过头。下面每条都写"改了什么 / 哪条用例钉住 /
破坏它时谁红"。反向验证用仓外脚本 `%TEMP%\rag4c-review4\mutate3.py`、`mutate4.py`
（快照字节 → 单点替换 → 跑对应守卫 → 按 sha256 还原）：**24 个变异全部转红**。

> **这句话的边界（第五轮独立评审纠正，见 §Q）**："0 逃逸"只对**我自己列的那 24 个变异**成立。评审另起 26 个变异，其中 12 个在我这套守卫下保持绿色 —— 多数是同一位置的"等价变异"（断言钉的是标签而不是内容、参数化集合被删项只会少跑一条、够不到的分支被当成判据）。所以不要把本节的"0 逃逸"读成"这些守卫没有洞"。

| 发现 | 处置 | 钉住的用例 |
|---|---|---|
| **B(i)** 注册期只用合成 actor 试调一次 `release_values`，仪式写 UPDATE 前那次不再核 → 按参数/按调用次数分支的声明能推翻撤销 | 仪式自己把住合并（`core/enterprise_identity_control.py:1611`） | `test_the_fence_is_held_at_write_time_not_only_at_registration[by-actor\|by-call-count × status\|revoked_by]` |
| **A-1 / S6** 表达式分发退回"按名字点菜"的梯子，12 条守卫全绿（宿主守卫只禁了 `name_asc`） | 宿主守卫禁掉三个内建名的字面量比较 | `test_the_catalog_holds_no_copy_of_the_sort_ladders` |
| **B(ii)** 排序可以声明一份游标能力，而编码/谓词实现不了它（只在运行时炸，且理由文案说的是别人的名字） | `cursor_value` 成为 keyset 声明的必填项：ORDER BY 与游标取值同出一门；`replace=True` 不再被自己计数挡住 | `test_a_keyset_declaration_must_carry_the_value_it_pages_on`、`test_replacing_the_sort_that_holds_the_cursor_is_allowed` |
| **S2** `core/catalog.py` 编码处一句写死的名字比对没被转换（本次改动漏网的第五处） | 编码改为查表，且两处拒绝共用一句由表派生的文案 | `test_the_encoder_refuses_a_sort_that_never_declared_a_cursor` |
| **S3 / S4** 续游标与拒游标两处退回比字面量时全绿 | 摘掉内建、注册探针排序，走完整张表 | `test_a_newly_registered_cursor_sort_pages_with_its_own_cursor` |
| **E** `route_reason` 当时零消费者；`reason` 本身没被校验（长度/类型一律放行，直落 JSON 列） | 消费方在 `23cfbe6` 已补；注册期新增非空 + `REASON_MAX_CHARS` 上限 | `test_the_reason_is_a_bounded_operator_facing_string` |
| **P1 / P3 / P5** 分类器给的事实没跟进 metadata、fast 分支不再记录、记到别的键下 —— 全绿 | 断言 `parse` 之后 `metadata["router"]` 的完整内容 | `test_the_whole_decision_reaches_the_parse_metadata_not_just_the_engine[fast\|vision]` |
| **P2** 重名栅栏在 `pdf_type_routing` / `document_sorts` 两张表上没钉（判据 §3.2 只做到 4 张表里的 2 张） | 各补一条 | `test_a_duplicate_route_declaration_is_refused_without_an_explicit_replace`、`test_a_duplicate_sort_name_is_refused_without_an_explicit_replace` |
| **I1 / I2 / I3 / I3b / I6** 身份守卫从没看过落库的审计行与预留行，把 action / resource_type / before / after 换成字面量全绿 | 实时用例改为读 `tenant_audit_events` 与 `tenant_control_mutation_requests` 并按声明字段逐字比对 | `test_a_new_kind_is_honoured_live_by_the_shared_ceremony` |
| **R1 / R4** 只读证明里"机制不符"那条断言用的是没备答案的假连接，KeyError 先让它 False —— 判的是约束层不是栅栏 | 每一项单独破坏、其余全部答对 | `test_each_condition_of_the_proof_is_alone_enough_to_refuse_it` |
| **G** 内建注册写在宿主模块末尾、往导入进来的表里写，宿主被第二次实例化时炸在启动路径上（catalog / identity 两张表如此，另两张自注册没事） | 内建声明收进 `register_builtin_document_sorts()` / `register_builtin_revocation_kinds()`，以 `replace=True` 重放 | 两处同名 `test_the_builtin_declarations_survive_a_second_instantiation` |
| **C** `parse()` 里"不认识的引擎标签"这条 raise 够不到（无子类、`route()` 只会给校验过的标签） | **保留但不复写说法**：它是纵深防御，真正的栅栏是注册期 `engine in ENGINE_NAMES`；用例文案已改成这个口径 | `test_an_unknown_engine_label_fails_loudly_instead_of_running_vision` |
| **#15 形状缺口** `live_probe_required=False` + 无 `connect_args` 的声明等于凭空断言只读 | 新增 `proved_by_url` 显式声明"靠 URL 成立"，两者并存或三者皆空都拒绝 | `test_a_bad_declaration_dies_at_registration` 新增两行 |

**说法更正（评审 H 段，全部成立）**：§L 的六个套件读数写错、"两处写死"实为五处、§K 的
"操作员能看见"当时为假、§J 把够不到的分支说成"真实的静默 fail-open"、§I 数了 8 个变异却只
列了 7 条。正文已就地订正，不重述为"本来就对"。

**判据层面学到的一条，比任何单点修复都值钱**：一条断言必须处在**只有被测机制能让它通过**的
位置上。`_proved(PROBE, "guess")` 那条看起来在测"机制不符"，实际测的是假连接抛 KeyError；
所以删掉栅栏它照样绿。同类错误我这轮又抓到两处（审计行从没被读过、metadata 键名没被读过），
都属同一形状：**断言声明对象的字段，而不是声明落到现实里的那一处**。

### 本轮实测（不引用历史日志）

- 反向验证：`24 个变异全部转红`，无锚点歧义残留（三条歧义锚点在 `mutate4.py` 里加上下文后各红一条）。
- 回归：`? collected` → **365 passed / 0 skipped in 632.77s**（`-q --tb=short`，
  单进程无 xdist），覆盖文档目录六个消费方套件 + 身份/SSO + 就绪面两套 + 解析 metadata 三套 +
  `test_run_registry_integration` + 四张表的全部守卫。
- `ruff check .` 全仓通过；`git diff --numstat` 与 `--ignore-cr-at-eol` 一致（无行尾翻转）。

---

## Q. 第五轮独立评审（审 `94f2ce5` / `6727e1f` / `23cfbe6`）查出的七条，逐条处置

评审 verdict 又是 **FAIL**，并且给了一条我必须接住的方法论批评：**§O 的"0 逃逸"只对我自己列的
24 个变异成立**。它另起 26 个变异，12 个在我的守卫下保持绿色。逐条核对后确认它指出的七条全部
成立 —— 下面每条都写"改了什么 / 哪条用例钉住"。

| 发现 | 为什么我之前没看见 | 处置 |
|---|---|---|
| **A** 栅栏是五列黑名单，`{"tenant_id": 别的租户}` 仍能写进行身份列：行被搬走，审计却记请求租户 | 我只想了"撤销本身拥有哪几列"，没想"这一行是谁的" | 新增 `SCOPE_COLUMNS = {id, tenant_id, created_at}`，注册期与写侧都过 `PROTECTED_COLUMNS`；参数化扩到八个列名，`tenant_id` 也进写侧实时探针 |
| **B** 参数化跑在 `sorted(FENCE_COLUMNS)` 上：把 `"revision"` 从常量里删掉只会少跑一条 | 把"集合的内容"交给了集合本身 | `test_the_protected_column_sets_are_exactly_what_the_ceremony_owns` 钉两组字面值；同时删掉 `updated_at` 那条**够不到**的分支（它在 `FENCE_COLUMNS` 里已经先响） |
| **C** 审计与响应的内容没被钉：探针投影只读 `normalized_domain`，撤销改了什么它看不见 | 断言钉的是标签（键名/动作名），不是内容 | 探针投影改读 `status`/`revision`，并加一次同键**重放**断言（`response_for_replay` 被清空即红） |
| **D** `expression` 与 `cursor_value` 仍是同一个人写的两遍，错配能注册且**静默翻页错** | 我把"同出一门"写成了"不会错位"，而夹具让 created/updated 永远同步 | 夹具补两行 created/updated 顺序不一致的数据；新增按 `cursor_capable_sorts()` 全表 limit=1 走查；`core/catalog.py` 那段注释改成实话（一致性由这条走查证明，不由声明证明） |
| **E** 新加的 `reason` 校验挡在 `engine` 校验之前，一条探针少写 reason 就让引擎闸门"看起来"有效 | 我加检查时没重跑旧探针的通过理由 | 那条探针补上 `reason="探针"`，并把期望写成 `match="engine must be one of"` |
| **F** `route()` 里还剩两处写死的 `"engine": "vision"`（非 PDF / 路由关闭 / 分类失败） | 上一轮只把"分类器有结论"那一支收进表 | 三种"没有结论的情形"也成为表里的行；`_decided()` 统一查表；宿主守卫禁掉 `"engine": "vision"`/`"fast"` 字面量。另：`proved_by_url` 补上宿主侧行为用例（靠 URI 成立的方言在"怎么开"一侧仍报 unsupported） |
| **I** 前端"降级解析"警示的唯一产地是分类失败，而后端没有一条用例走到那里 | 我只验了 UI 会读这个键 | 新增后端用例：假分类器抛错 → `route()` 带 `fallback_reason` → `parse()` 把它记进 `metadata["router"]` |
| **H** 一条注释声称能区分"UPDATE 前拒绝"与"UPDATE 后回滚"，其实区分不了 | 顺手写的判据说明写过了头 | 改成补刀说明：行状态那两条断言才是判据 |

**反向验证（本轮两组变异，脚本在 `%TEMP%\rag4c-review5\`）**：
评审绿色项的复测 **12 个变异全部转红**；原文查看这一轴新起 **10 个变异全部转红**。
两组都按 sha256 还原、还原核对无差异。仍然沿用 §O 那条更正的口径：**这只是本节列出的变异集**，
不等于"没有等价变异能溜过"。

**给下一位的两条可复用教训**（已同步进用户记忆）：
1. 参数化跑在集合上时，另钉一条字面值断言，否则"少一项"表现为"少跑一条"。
2. 黑名单式栅栏要把"这一行是谁的"（id / tenant_id / created_at）与"这一行是什么状态"一起保护，
   或者干脆改成白名单。

---

## R. `parser_meta` 字符串键筛选/分面 落地记录（切分诊断二期）

**这条轴不在 B 表里**——它不是普查时按"字面量等值分派点"数出来的，而是做
`docs/compose/智能体交接审查.md` §9 第 2 条时顺手量出来的：`core/catalog.py` 的
`_document_catalog_filter_criteria` 里，`engine` 的筛选是**内联**的一整段
（JSON 取值 + `unknown` 三值 or + 旧库能力错）。要再加一个可筛选的 `parser_meta` 键，
照抄就是第二段同样的逻辑。

| | 改前 | 改后 |
|---|---|---|
| 加一个可筛选的 `parser_meta` 字符串键要改几处 | 3 段逻辑各抄一遍（取值写法 / `unknown` 语义 / 旧库失败模式），且都要记得同步游标哈希 | 1 行 criteria 声明 + 1 行 facet 表达式 + 1 个返回键 |
| `unknown` 的定义处数 | 每键各一份（会漂移） | 全仓一份（`_document_catalog_meta_key_criteria` / `_facet_expression`） |

落点：`core/catalog.py` 的 `_document_catalog_meta_key_expression` /
`_document_catalog_meta_facet_expression` / `_document_catalog_meta_key_criteria`。
旧符号 `_document_catalog_engine_expression` **删除**而非兼容转发（唯一调用方同步改）。
消费方两处：`_document_catalog_filter_criteria`（`engine`、`chunking_reason_code`）与
`summarize_documents`（`engines`、`chunking_reasons` 两个分面）。

**语义不变量（这条比代码重要）**：`unknown` 不是某个键的取值，而是"这一列/这一键还没写"的桶。
所以旧库（无 `parser_meta` 列）上筛 `unknown` 成立（等于不加条件，因为"没写"对所有行为真），
筛具体值必须 `DocumentCatalogCapabilityError` 显式拒绝——静默返回全部会让操作员读成
"这个库没有这类文档"。两个键共用这段推理，所以不会各漂一份。

**游标哈希**：新键进了 `_document_cursor_query_hash` 载荷，否则 A 筛选发出的 cursor 能续读
B 筛选的结果（不报错，只是给出另一批行）。代价是**载荷变了 → 上线前的 cursor 上线后一律 422**，
这是安全失败。

**反向验证**（脚本 `%TEMP%\mutate_catalog.py`，4/4 转红，还原后 sha256 与改前相同、复跑 23 passed）：
筛选分支失效 → 3 条红；游标哈希漏键 → `test_chunking_reason_code_cursor_cannot_page_a_different_filter` 红；
旧库不再抛能力错 → `test_chunking_reason_code_filter_refuses_schema_without_parser_meta` 红；
分面不吐 → 2 条红。用例名与读数详见 `chunking-reason-filter.md` §4.1。

**诚实边界**：
1. **无索引扫描**。谓词在 `parser_meta[key]` 上，MySQL 走不到索引，只靠同一 WHERE 里
   `tenant_id` + `dataset_id`（有索引）把扫描面收窄到一个库内。要 generated column + 索引
   是独立迁移切片（新 `catalog_migrations/versions/` + `tests/head_catalog.py` 夹具声明 +
   capability 白名单），本片没做。
2. **本片没有常驻的"宿主文件逐字节不变"守卫**。判据靠的是"两个键共用同一 helper"这个结构事实
   + 4 路变异；与 §I/§J/§K 那些带宿主守卫的轴相比弱一档。要补齐就是加一条
   "criteria/facet 里不许出现第二个 `parser_meta[` 字面量取值"的源码扫描守卫，
   与 `tests/test_chunk_writers.py` 同型。**登记为下一轮候选，不当场糊。**
3. 分面对旧库返回单个 `unknown` 桶而不是报错——分面是"看见现状"，筛选具体值才是"要求答案"，
   两者失败模式必须不同。


## S. 可查看来源后缀轴落地记录 + 第七轮独立评审（审 `0a825f6` / `fa52c4c` / `227f0f4`）八条处置

### S-1 这条轴本来就在表里，只是这张表一直没登记（评审 nit 7）

`0a825f6` 落的时候我只在 `docs/compose/智能体交接审查.md` §4 索引加了一行，**没进 A 表**，
所以从这份清单查"可查看来源"是查不到的（§I–§R 每条轴都有落地记录，唯独它没有）。补上：

| | 加一个可查看的后缀要改几处 |
|---|---|
| 改前 | content type、可否 inline、大小上限三处各自出现在 HTTP 层 / 取文件层 / 前端分类器，未知后缀靠一个默认值兜 |
| 改后 | `core/source_previews.py` 的 `BUILTIN_SOURCE_PREVIEWS` 加一行 `SourcePreviewSpec`；HTTP 层、取文件层、契约全查表 |

内建 13 个后缀 / 4 个 kind（pdf 1、image 5、text 4、office 3）。两个防漂移的注册期判死：
`inline_renderable=True` 但 media type 不在 `INLINE_SAFE_MEDIA_TYPES` → 拒（:156），
`media_type` 必须是小写 `type/subtype`（:150）。**未知后缀没有默认 content type**，
这是这条轴存在的第一理由：`application/octet-stream` + 浏览器自己猜 = 存进去的 `.html`
在控制台自己的源上跑脚本。第二理由是"按 family 贴标签"：5 个图像后缀是一个 kind、
5 个 content type，JPEG 发成 `image/png` 在 `nosniff` 下直接坏图。

常驻守卫：`tests/test_source_preview_registry.py`（36 条，含"注册一个后缀，
`core/source_preview_access.py` 逐字节不变"）、`tests/test_knowledge_source_preview_api.py`
（14 条）、`frontend/src/parse-intervention/components/SourcePreview.test.tsx`（10 条）。

### S-2 评审查出的四条 should-fix

| # | 发现 | 处置 |
|---|---|---|
| F2 | `fa52c4c` 的提交信息说"`openapi.ts` 已重新生成并随本条提交" —— **为假**：那份文件里`source-preview` 出现 0 次，`scripts/export_openapi.py --check` 在 HEAD 上 exit 1（`ADDED: [.../source-preview]`）。契约门禁当时只跑了后半段（json→ts），而事实源 `openapi.json` 从没重导过 | 已在 `227f0f4` 真正补上：先 `export_openapi.py` 再 `types:gen`，两份 generated 文件随该条提交。本轮复核读数：HEAD 上 json 含 `source-preview` ×2、ts ×1，`--check` exit 0。门禁口径写进 `智能体交接审查.md` §6（**契约是两步，只跑 `types:gen` 会假绿**）。这条同时是一句话写过头的教训：提交信息里的"已随本条提交"必须用 `git show --stat` 核 |
| F1 | 我写进 spec 的"kind 由 resolve 之后的路径决定"是**假**的：`locate_preview_source` 先从原始 `file_path` 取 spec（:87），再 `resolve()`（:97）。root 内一个 `report.pdf -> payload.html` 软链会拿到 `application/pdf` 却交出 HTML，界面把它塞进 iframe。越界检查拦不住它——它没跳出去 | spec 改从 `candidate` 取，落在越界检查**之后**（"跳出去了"这个安全信号要优先于"这个后缀不认得"）。两条新用例，见 S-3 |
| F3 | 前端"这三类既不渲染也不给链接"这道闸门**没有 DOM 守卫**：`previewKind` 的单测只证明分类器说 `download`，只改 JSX 让 `download` 长出一个 iframe，分类器一字不动 → 评审的变异 M5b 实测 **13 passed 存活** | 新增一条查真实节点的用例：三类各喂一份 blob，断言 `iframe`/`img`/`pre`/`a[href^="blob:"]` 全为 null、`querySelectorAll("a").length === 0`，同时断言"下载原文"仍在（挡住"缩成什么都不显示"） |
| F4 | `PROTECTED_COLUMNS` 只挡了撤销五列 + 行身份三列，**溯源列没挡**：声明返回 `{"created_by": ...}` 能过注册也过写侧，于是行的"谁建的"被改写而审计仍记真实请求者 —— 两条记录对上假话 | `PROVENANCE_COLUMNS = {created_by, verified_by, issued_by, issued_at}`（四列都在 `models/orm.py:1204/1208/1291/1292` 真实存在），并进 `PROTECTED_COLUMNS`，注册期与写侧双拦 |

### S-3 一条我自己加错的列，和一个"反向用例"的价值

F4 我按评审点名的三列动手时**自己多加了 `updated_by` 和 `verified_by`**。前者是错的：
`core/enterprise_identity_control.py:1707` 内建 `domain_revoke` 的 `release_values` 返回的
正是 `{"updated_by": actor_id}`（`tests/…:330` 钉着字面值）。把它列进禁区不会在注册期炸，
而会在**写侧**被仪式拒 —— 也就是线上才炸。已在源码注释里写明"这一列是 kind 拥有的一条"。

参数化用例跑在 `sorted(PROTECTED_COLUMNS)` 上，所以"少一项"表现为**少跑一条而不是失败**
（§Q 已经记过一次同样的坑）。这一条由 `test_the_protected_column_sets_are_exactly_what_the_ceremony_owns`
补三组字面值钉死。

F1 的两条用例是**成对**写的，这很值：
`test_a_symlink_inside_the_root_is_typed_by_the_bytes_it_reaches_not_by_its_own_name` 钉
"不能按名字贴标签"，而 `test_the_reverse_symlink_is_served_because_the_target_is_what_gets_read`
（link 名未登记、target 已登记 → 必须放行且 spec 来自 target）钉"判据是**交出去的字节有一个
登记类型**，不是路径里出现过链接"。只有前一条的话，`if Path(raw).is_symlink(): refuse` 这种
过修也算"修好了"——后端变异 M2（一律拒符号链接）实测确实被反向用例抓红。

### S-4 两条 nit 顺手修了（都改成"权威在后端"的形状）

- **nit 5**：`previewKind` 的 `text/` 前缀规则会给"将来登记成只下载的 text 型后缀"白送一个
  "新标签页打开"的 blob 链接。改成**表态优先**：`disposition !== "inline"` 直接落 `download`。
  后端 `inline = spec.inline_renderable and disposition != "attachment"`
  （`server/knowledge_source_preview_api.py:136`）已经把 Intent 写进 `Content-Disposition`，
  前端第二道闸门没有理由自己猜。`NEVER_RENDERED` 保留为第二道。
- **nit 6**：`download()` 在 `link.click()` 同 tick `revokeObjectURL`。评审说"jsdom 测不了"——
  实测**测得了**：把 `waitFor` 换成只冲微任务的循环（`waitFor` 自己会推进宏任务，会把
  "还没回收"看没），同 tick 不回收 / 跨一个宏任务恰好回收一次 / 卸载不重复回收，三条都断得住。
  泄漏也堵了：`pendingDownloads` 在卸载时兜底清。

### S-5 反向验证读数（脚本 `%TEMP%\mutate_round7.py` / `mutate_round7_fe.py`，均在仓外）

后端 5/5 转红：M1 spec 退回原始路径 → F1 两条红；M2 过修（一律拒符号链接）→ 反向用例红；
M3 `PROTECTED_COLUMNS` 并集去掉溯源集 → 字面值钉用例红；M4 注册期分支删除 → 8 条红
（含 4 条新参数化）；M5 把 `updated_by` 加回去 → 整模块 import 期就 error。
前端 5/5 转红：FM1（= 评审存活的 M5b）→ DOM 闸门红；FM2 去掉表态优先 → 分类器用例红；
FM3 同 tick 回收 → 时序用例红；FM4 永不回收 → 时序用例红；FM5 `download` 长出 blob 链接 →
DOM 闸门红。两组还原后均按字节断言与改前相同，复跑 75 passed / 91 passed。

### S-6 仍未处理（登记，不当场糊）

1. **nit 8**：`server/enterprise_readiness_api.py:573-577` 的 sqlite 只读证明把 host 写死。
   fail-closed、无安全后果，但它是 §I 那张方言表**外面**残留的一处方言判断。归到轴 #15 的
   延长线，不混进本轮。
2. **派发到 Content-Disposition 而不是后缀**：前端现在读 `disposition`，但 `previewKind` 仍
   自己判 `text/` 前缀挑渲染面。真正零猜的版本是后端在响应里给一个 `render_kind`。那要动契约，
   单独切片。
3. **放行与读取之间仍不是同一个句柄**。HTTP 层已经防住"文件被换大/换小"：读完比对
   `len(payload) != source.size` 就拒（`server/knowledge_source_preview_api.py:130-135`）。
   没防住的是**等字节数换内容**——`resolve()` → `stat()` → 再按路径打开，中间换成另一份同长度
   的文件，尺寸核对过、content type 说的却不是交出去的那份字节。收口办法是先 open 再
   `os.fstat` + 按 fd 读到底（或 `O_NOFOLLOW`）。这是端点硬化，不是这条轴的分支问题，
   且它与 F1 不同性：F1 是"按名字贴标签"，这一条是"两次系统调用之间的竞态"。

## T. 第八轮独立评审（审 `227f0f4`）：一条 blocker 查出的是我自己写下的"零改分支"没做到

评审 VERDICT 是 FAIL，只有一条 blocker，但它打在这轮判据本身上。

### T-1 B1：第 7 个筛选键只接了一半，而且我登记过这个风险却没去数消费点

判据是"新增实现零改分支"。`DocumentsFilterState` 的键要在**五处**各写一遍：URL 解析、参数名
对照、单键写入、URL 变了回灌 state、"有没有筛选生效"。本片只在前/中三处加了 `chunkingReason`，
于是（评审逐条给了 file:line，我复读源码确认全部为真）：

| 失败模式 | 现象 |
|---|---|
| 后退到只有 `chunking_reason_code` 变化的 URL | 六个键逐个比完，`filtersChanged` 是 `false`，**整条回灌不跑**：界面按旧值继续筛选，URL 说的是另一套 |
| 该键与其它键同时变化 | 六个 setter 被回灌，这一项仍被吞 |
| 后续任何一次别的键写 URL | 从 `route.params` 重写，活跃筛选**永久缺席**于分享/复制出去的 URL —— 直接推翻提交信息里"选择留在 URL"那句 |

**修法不是补那三行。** 补三行只是把这一处的五处对齐再走一遍，下一个键还会漏。改成让**类型当注册表**：
`documentFilterState: DocumentsFilterState` 与 `documentFilterSetters: Record<keyof
DocumentsFilterState, (value: string) => void>` 两张表，回灌 / 单键写 / 清空全部按表遍历 ——
**少一个键是编译错误而不是漏测**（`tsc --noEmit` 就是这道闸门）。顺带两件：

1. 回灌效果原先手写的十个依赖项换成"当前值放快照 ref"，于是**依赖数组里一个筛选键都没有**，
   "忘了加进 deps"这一类也没有落点了。
2. **我读码时查出评审没点名的同一形状第二处**：`清除筛选` 那颗按钮的可见性判断也是手写六项
   `!== "all"`，所以**只按切分判定时操作员没有退回全部的入口**。改成按 `documentFilterState`
   与 `DEFAULT_DOCUMENT_FILTERS` 比。

反向验证 4/4 转红（`%TEMP%\mutate_round8_b1.py`，还原后字节相同）：回灌退回六项清单 / 可见性
退回六项（表现为"找不到名为 清除全部文档筛选 的按钮"）/ 单键写退回 if/else 链（`facet-item`
少 `is-active`）/ 清空退回手写六次调用 —— 各自转红。新用例本身也钉了两侧 URL 方向。

### T-2 四条 non-blocking 的处置

| # | 处置 |
|---|---|
| N1 `_document_engine` 是 Python 侧第二份 `unknown` | **已删**。删前把"动态取用"这一条补核：`grep -rn document_engine` 排除 `__pycache__` 后只命中定义行本身，连字符串形态都没有。删后 catalog 四套件 61 passed、`ruff check .` 干净 |
| N2 legacy 路径 trim+lowercase 与 SQL 精确比较不对称 | **如实登记不修**。当前没有产地能触发：四个码都是小写 snake_case 且由 `str(reason_code)` 直写。要改的是比较语义，属于另一件事 |
| N3 判定码 ↔ 界面名没有对账 | **已补**跨语言守卫 `frontend/src/parse-intervention/model/chunkReasonLabels.source.test.ts`（正则读 `indexing/chunking_router.py` 真文件，双向钉 + 一条"不许空集自证"）。三路变异各点名一条红：丢中文名 → `每个后端会写下的判定码都有一个中文界面名`；留死名字 → `界面名表里没有后端已经不再产出的死名字`；正则失配 → 第一条自证用例 |
| N4 部署窗口：旧 cursor 422 会被"刷新"与 3s 轮询重复触发；`facets.chunking_reasons.map` 对新前端 + 旧后端会抛 | **登记，不当场糊**。422 那侧是暂态且改一次筛选即自愈，属所有 hash 失效型部署的共性；`map` 那侧真正的缺口是**没有写下"后端先于前端上线"这条约定**——写进交接文档比加一个 `?? []` 更对症，加兜底会把"契约已断"降级成看不见的空分面 |

### T-3 关于这次评审本身（两条要给下一位）

1. **派发的 subagent 报了"credit limit 失败"，但报告是完整的。** 通知说 agent failed，
   `review-round8.md` 却已 12 KB 全文落盘。所以**通知不等于产物**：先去看 artifact，再决定重派。
2. **它的纪律是第七轮该有的样子**：自陈"未执行任何测试、未跑任何写类 git 命令、
   `git status --porcelain` 为空"，唯一写入是 `$TEMP` 里的报告。对比 §S 记的第七轮就地变异 6 个
   文件，本轮的结论反而更可复核（它还独立复核了 `_scoped_document` 两条 scope 谓词在 HEAD 完好，
   推翻了我上一轮写进交接文档的一条指控 —— 见 §6 表末行的更正）。

## U. 读 §D-1 时顺带查出/更正的两条账

1. **§D-1 是一笔过期的账**：它写着"修法是共享分类器 + `raise` 兜底"，其实 `a50c697` 已经修完了。
   本轮逐复读三个消费点确认三处都拒绝未知状态，已就地更正该行。教训给下一位也给自己：
   **登记类文档里"未修"的断言，比"未修"这件事本身更需要及时核**——它会让下一位把已经闭住的
   安全面重新当缺陷排优先级，白花一片预算。
2. **`core/catalog_schema.py` 里 7 个 `inspect_*_capability` 名字各有两份定义**（11 个 distinct
   名字 / 18 个 def；`ast.unparse` 逐对比对确认同一名字的两份**不同**）。
   **这不是缺陷**：:10975-10990 先把当时那版实现赋给 `_KNOWLEDGE_SERVING_ORIGINAL_*_CAPABILITY`，
   随后才把公开名重绑到通用的 `_knowledge_serving_revision_compatible(...)` —— 刻意的
   "冻结-重绑"行为等价垫片。差点把它写成 §D-8 那一类"两个分派器只改一边会静默失效"，
   是查了 `_ORIGINAL_*` 的赋值点才没写错。
   **但它决定了这条轴的改法**：状态字面量除声明表外还散着 11 处 `return "not_available", ()` 与
   61 处 `"unavailable"` 字面量，且收成声明表时必须同时保住冻结版的行为等价（垫片每处都拿
   ORIGINAL 版做对照）。归为 §B 候选**轴 #17（capability 状态生产者）**，
   与 §D-6 同源风险，**不属于"顺手可修"**。

## V. §D-9 落地记录：一个别名只留一个主人（含一次"我以为会有缺口，结果没有"）

| | 改前 | 改后 |
|---|---|---|
| 加/改一档 gate 判定要动几处 | 6 处各自写（3 处方向相反的同款三元式 + 2 份并列两种拼写的词表 + 前端 1 处双拼分支） | 1 处声明（`core/release_quality_gate_states.py`）+ 两条对账守卫（请求侧静态 `Literal` 与 DB 的 `CHECK`）—— 请求侧那个 `Literal` 必须保持字面量形态好让 OpenAPI 生成，**没法从函数派生，所以靠守卫而不是编译期** |
| 未声明的状态来到边界 | 写侧原样透传给 SQL，由 DB 的 CHECK 在 flush 时报一句跟业务无关的错；筛选侧靠另一份手写集合 | 两个方向的转换函数都是 **total**：不在声明里就 raise，筛选侧把它翻成既有的 `QualityScheduleValidation`（400 类，不是 500） |

**这条轴真正的判据不是"少写两行"，而是新增的那道对账**：
`test_the_declared_storage_vocabulary_equals_what_the_database_allows` 把 Python 侧的存储集合
去等 `models/orm.py` 与 `catalog_migrations/versions/0031_...` 里那条
`CHECK (gate_state IN ('passing','waived','not_required','blocked','unavailable'))` 的 IN 清单。
**在此之前没有任何测试断言过"Python 词表 == 数据库约束"**，所以往一边加一档、另一边忘了，
表现是 flush 期一个看不懂的完整性错误。

顺带查出并修掉的死成员：`core/enterprise_release_quality_alerts.py` 校验的是**库里的行**
（:517），词表却把 `passed` 也列着 —— 那条 CHECK 永不产出它。改成 `gate_states_storage()` 后
它是一个**行为等价的收紧**（该值不可能出现，删掉不会拒掉任何真数据）。
反向验证：把手写列表装回去 → `test_the_alerts_vocabulary_validates_rows_so_it_must_be_the_storage_set` 红。

**前端那一处没在本片动**（登记而非顺手改）：
`QualityOperationsDetailDrawer.tsx:43/:326` 同时认 `passed` 与 `passing`，
因为契约的 `ObservationGateState` 确实并列着两种拼法。要收掉它得先裁一件事：
**对外契约留不留 `passing`**。留 → 前端那两处就是正确的宽容；不留 → 是破坏性契约变更，
必须单独成切片并同步界面。本片只做"两种拼法都由同一份声明派生"，不改对外承诺。

**反向验证 6/6 转红**（`%TEMP%\mutate_gate_states.py`，四个被改文件还原后按字节断言与改前相同）：

| 变异 | 转红的用例 |
|---|---|
| GM1 写侧退回内联三元式 | 回潮栅栏 `test_no_module_writes_the_alias_as_an_inline_ternary_again` |
| GM2 转换函数不再 total（未知值原样返回） | 10 条红（含参数化的"未声明值必须拒"） |
| GM3 存储词表混进对外拼法（与 CHECK 漂移） | 4 条红，其中一条就是新加的 CHECK 对账 |
| GM4 alerts 退回自己那份含死成员的列表 | alerts 那条专用用例 |
| GM5 请求侧 Literal 少一种拼法 | 字面量与声明的对账用例 |
| GM6 筛选侧退回原样透传 | `..._reject_tampered_cursor_and_filters` |

**GM6 是本轮一个值得记下的自我更正**：我原本判定"这条变异会存活"，理由是 fixture 只产出
`blocked`，两种拼写都筛不到行、断言会同样通过。实跑**转红** —— 抓住它的不是"别名命中非空行"
那半，而是**同一处转换同时是校验点**（透传之后 `stale` 不再被拒）。
所以两件事分开说清：① 筛选侧被接线这一点有 DB 级覆盖；② "别名两种拼各自命中同一批**非空**行"
这一格仍然只有单元级覆盖 —— 需要一个"健康 SLO"夹具，`_prepare_expired_execution_authority`
给不出，而想用它绕（UPDATE 成 `passing`）被表自己的 append-only 不可变触发器拒了。
该缺口写进用例 docstring，不当场糊。

**门禁实跑**：`tests/test_release_quality_gate_states.py` + `..._operations_reads.py` → 20 passed；
`ruff check tests/ core/release_quality_gate_states.py` 干净；release-quality 六套（alerts /
observation / operations_core / operations_migration / persistence / scan_execution）→ 50 passed。

