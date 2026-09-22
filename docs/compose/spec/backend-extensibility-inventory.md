---
feature: backend-extensibility-inventory
status: delivered
updated: 2026-09-22
branch: main
commits: —
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

`core/embedding.py` / `core/llm.py` / `core/reranker.py` 经核实**本就已经到位**：
全部经 `ProviderRegistry`，`server/` 与 `config/` 里没有任何残留 `if provider ==`；
加一个 provider 的成本是 1 次注册 + 1 个类 = 0 处分支修改。三者不该合并出公共基类
（embedding 要批量切分与缓存变体、LLM 要熔断与 slot cache 与 response_format 降级、
reranker 要处理 HTTP 状态，共性只有名字）。

## B. 开放轴清单（按 价值 = 免改分支数 × 新增可能性 ÷ 风险 排序）

| # | 轴 | 站点（VERIFIED file:line 摘要） | 加一个实现要改几处 N | 迁移? | 契约? | 建议模式 |
|---|-----|--------------------------------|--------------------|-------|-------|----------|
| 1 | 文档类型 ↔ 能力（扩展名→doc_type→解析能力→策略→切分模式） | `indexing/parsers/base.py:28-41`、`docling_parser.py:49-52`、`ingest.py:59-72,79-81,431,698-709`、`strategies.py:286-292,310`、`chunking_router.py:38,144` | 3–5（五份平行字面量集合，互不派生） | 否 | `doc_type?: string`，非枚举 | 声明式能力规格表，四个集合成其投影 |
| 2 | 运行事件 `event_type` 的行为分区 | `run_events.py:50-61`、`run_registry.py:1172,1184-1201,1203,1216-1236,1238,1247,1258-1262`、`run_ops.py:1163,1173,1185-1189,1213,1224-1228,1253` | 8（同一套 terminal 集合被手写 3 遍） | 否 | `frontend/src/types/rag.ts:47-62` 手工镜像 `Literal` | 声明式规格表（family/phase/effect） |
| 3 | 投影目标 × 操作（`target_store` × `operation`） | `projection_handlers.py:36-40,125-132,208,460`、`index_worker.py:110-121,153,161,184,228-231,253,257`、`state_machine.py:531-542`、`document_deletion.py:43,1068,1392,1474,1491-1750,1728`、`knowledge_consistency_api.py:60-77` | 7–9（新目标）/ 4–5（新操作） | 否（无 CHECK） | 一致性响应里出现，前端仅当展示串 | Strategy+Registry，键 `store:operation`；**碰投影栅栏，风险最高** |
| 4 | 来源连接器 `SourceKind` | `knowledge_sources_api.py:65,80,456-462,474-508,831-837`（`:80` 的 `_ALLOWED_KINDS` **定义了但从未被引用**） | 4（registry 本身已 0 改，是 HTTP 层把保证打穿） | 否 | `openapi.ts:7103,7142,7166` | 给现有 `SourcePlugin` 加 `config_model`/`preflight` 两个字段 |
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
2. **`.html` 解析成功却被贴错标签**：`docling_parser.py:51` 接受 `.html`，但它不在
   `base.SUPPORTED_EXTENSIONS` / `_EXTENSION_DOC_TYPES` / `_PLAINTEXT_EXTENSIONS` 里
   → `resolved_doc_type=None` → `ingest.py:719` 启发式 → 标成 `"txt"`。
3. **CSV→qa 切分模式对真实上传不可达**：`chunking_router.py:38` 把 `csv` 路由到 `qa`，
   但没有任何扩展名会产出 `doc_type="csv"`（`ingest.py:59-72` 无 `.csv`，也不在
   `SUPPORTED_EXTENSIONS`）→ 该模式对真 CSV 是死的。
4. **approver_kind 未知值两套策略**：`materializer.py:356` raise，`receipts.py:466-490` 没有 `else`
   静默返回 `False` → 一行坏数据让人"没资格"且毫无信号。
5. **`RagExecutor` 枚举漏一个值**：`rag_topology.py:24` 只列 3 个，而 `:574` 会发
   `executor="cache_replay"`，`server/app.py:1505-2043` 五处在判它。
6. **`_ALLOWED_KINDS`（`knowledge_sources_api.py:80`）定义后从未被引用**：值集的第三份死拷贝。
7. **`info` 严重度是死的**：`center.py:24` 接受 `{info,warning,critical}`，但没有任何生产者能发出
   `info`（质量告警走 2 值 `_observation_alert_severity`，审批硬编码 `warning`）。
8. **`mineru.provider` 有两个分派器**（`base.py:167-177` 与 `plugins.py:16-22`），前者不传 `mode`，
   只被 smoke 脚本用到；只在一边加 provider 会在另一边静默失效。
9. **`gate_state` 存储值 `passing`、对外 `passed`，映射手写两遍**（`scheduler.py:2816`、`:3235`）。
10. **`sources/runner.py:178` 重declare 了 `state_mode` 白名单**，且 `source_dispatcher.py:238`
    硬编码 `state_mode="database"` 绕过设置。
11. **`risk_tier {low,medium,high}` 写三遍**且抛不同异常类型。
12. **通知 `source_kind` 不在 OpenAPI 也不在前端类型里**：契约层对它是瞎的，加 kind 抓不到。

## E. 判据覆盖缺口

`tests/` 里没有任何测试引用 `EMBEDDING_PROVIDERS` / `LLM_PROVIDERS` / `RERANKER_PROVIDERS`
（grep 零命中）——这三条轴的"零改分支"目前没有常驻守卫，与 `storage_backends` 的
`test_registering_a_provider_needs_no_edit_to_shared_paths` 不对等。属于便宜就该补的债。
