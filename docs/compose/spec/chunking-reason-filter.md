---
feature: chunking-reason-filter
status: delivered
base: c5c02ee
commits: c5c02ee..227f0f4（第七轮评审返工在 227f0f4 之后，只动 inventory §S 记的四条，判据未变）
date: 2026-09-23
---

# 文档列表按切分判定筛选（切分诊断二期）

## 1. 为什么做

一期（`chunk-diagnostics`）已经能把"这篇为什么走 parent_child"写进
`documents.parser_meta.chunking_reason_code` 并在单篇画像上显示。但**列表上找不到**：
运维想问"这个库里到底有多少篇是被判成表格 QA 的""哪些篇还没记录判定"，只能一篇篇点。
WeKnora 的对齐点也正在这里——诊断信息只有能被**筛选和聚合**才算运营可用。

`docs/compose/智能体交接审查.md` §9 第 2 条登记的就是这件事，验收口径写的是
"前端筛选 + 后端字段/索引策略 + 定向测试"。

## 2. 设计

### 2.1 不做第二条内联分支，而是把已有那条抽成共享件

改之前，`core/catalog.py::_document_catalog_filter_criteria` 里 `engine` 的筛选是**内联**的：
一段 JSON 取值表达式 + 一段 `unknown` 的三值 or + 一段"旧库没这列就抛能力错"。
如果照抄一份给 `chunking_reason_code`，那么 `unknown` 会有两个定义、能力错会有两处措辞，
下一个键还要再抄一遍——这正是本仓扩展轴判据要消除的形状。

所以现在 `parser_meta` 上的字符串键筛选只有一对 helper，两个键共用：

| 符号 | 职责 |
|---|---|
| `_document_catalog_meta_key_expression(table, key)` | 取 `parser_meta[key]` 的字符串值（唯一一处 JSON 取值写法） |
| `_document_catalog_meta_facet_expression(table, available, key)` | 分面分组取值：缺列 → 常量 `unknown`；缺键/空串 → `coalesce(nullif(..,''), 'unknown')` |
| `_document_catalog_meta_key_criteria(table, available, *, key, value, capability)` | 筛选谓词；`unknown` 与具体值两条语义，以及旧库的能力错 |

旧名 `_document_catalog_engine_expression` **直接删除**，没有留兼容转发（唯一调用方
`summarize_documents` 同步改）。

**加第三个键的成本 = 一行声明**（criteria 里一个 `if` + facets 里一个表达式 + 一个返回键），
不需要新写 JSON 取值、不需要重新定义 `unknown`、不需要重新决定旧库怎么办。

### 2.2 `unknown` 的唯一定义（这条是语义，不是实现细节）

`unknown` **不是一个原因码**，而是"这一列/这一键还没写"的桶。由此推出两条必须分开的行为：

- 旧库（`parser_meta` 列不存在）上筛 `unknown` → **成立**，等于不加条件。因为"没写"在旧库上
  对所有行都为真，这不是猜测。
- 旧库上筛具体值（如 `table_doc_type`）→ **必须抛 `DocumentCatalogCapabilityError`**。
  旧库给不出答案；静默返回全部会让操作员以为"这个库没有表格 QA 文档"。

`chunking_reason_code` 与 `engine` 共用这段推理，所以两者的 `unknown` 不会漂移。

### 2.3 游标哈希必须带上新筛选

`_document_cursor_query_hash` 的载荷里加了 `"chunking_reason_code"`。这不是可选的：
游标是 keyset 分页的续读位置，**绑定发出它的那一组筛选**。漏掉新键就意味着
"在 A 判定下翻到第 3 页拿到的 cursor，可以拿去翻 B 判定的结果"——不报错，只是悄悄给出
另一批行。带上之后，跨筛选复用会以 `ValueError("cursor does not match query")` → HTTP 422 失败。

**部署影响（诚实写明）**：哈希载荷变了，所以**上线前发出的 cursor 在上线后一律 422**。
这是安全失败（拒绝续读），不是数据损坏；客户端清掉 cursor 回到第一页即可。

### 2.4 分面计数

`summarize_documents` 的 `facets` 里新增 `chunking_reasons`，与既有 `engines` 同一个
`_document_catalog_grouped_counts` + `sort_facets` 通路。前端类型
`DocumentCatalogSummaryResponse["facets"].chunking_reasons` 是**必填**而非可选——
所有夹具必须显式给出这一项，缺了就编译不过，避免"后端加了分面、前端永远读到 undefined"。

### 2.5 前端两条路都要通

- **modern catalog 路**（真后端）：`DocumentsPage.tsx` 新增 `chunkingReasonFilter` state →
  `DocumentPageQuery.chunking_reason_code` → `fetchDocumentPage` 的
  `appendNonDefaultDocumentFilter`（`"all"` 不进 URL）。选择留在 URL 参数
  `chunking_reason_code`，深链与刷新都不丢。
- **legacy / mock 路**：`documentWorkspaceModel.ts` 的 `filterDocuments` 增加同名判据，
  `documentChunkingReason()` 读 `parser_meta?.chunking_reason_code`，空/缺都归 `unknown`，
  与后端 2.2 同义。

侧栏分面显示的是**中文判定名**（复用 `CHUNK_REASON_CODE_LABELS`），未记录那桶显示"待记录"。
原因码是给 SQL 用的，不是给人读的；界面不漏裸码。

筛选键 ↔ URL 参数名的对照从一串嵌套三元改成了一张表 `DOCUMENT_FILTER_PARAMETERS`
（只列不同名的那几个，其余同名直通）。

## 3. 明确不做（Out of Scope）

- **不做 generated column + index**。见 §5 诚实边界。
- 不改切分路由阈值，不改 `reason_code` 的取值集合（那是一期的判定表）。
- 不给 `chunking_reason`（中文长句）做筛选——筛选用码，长句仍只在画像里展示。

## 4. 验证

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=. pytest tests/test_document_catalog_api.py tests/test_document_sort_registry.py tests/test_catalog_capability.py tests/test_document_catalog_indexes.py -q` | **61 passed / 69.00s** |
| `.venv\Scripts\python.exe -m ruff check .` | **All checks passed!** |
| `cd frontend && npx tsc --noEmit` | **退出 0** |
| `npx vitest run src/pages/DocumentsPage.workspace.test.tsx` | **37 passed** |
| `scripts/export_openapi.py` + `npm run types:gen` | openapi.json **+87 / -0**、openapi.ts **+55 / -0**（纯增量） |

### 4.1 反向变异（把修复改回旧写法，必须变红）

脚本 `%TEMP%\mutate_catalog.py`：改 `core/catalog.py` 字节 → 跑
`tests/test_document_catalog_api.py` → 立即还原；还原后与改前 **sha256 相同**，且复跑 23 passed。

| 变异 | 结果 | 变红的用例 |
|---|---|---|
| M1 筛选分支失效（`if chunking_reason_code != "all"` → `if False`） | **CAUGHT** | `test_list_documents_page_filters_by_chunking_reason_code`、`test_document_catalog_api_exposes_chunking_reason_code_filter`、`test_chunking_reason_code_filter_refuses_schema_without_parser_meta` |
| M2 游标哈希漏掉新键 | **CAUGHT** | `test_chunking_reason_code_cursor_cannot_page_a_different_filter` |
| M3 旧库无 `parser_meta` 时不再抛能力错、静默返回全部 | **CAUGHT** | `test_chunking_reason_code_filter_refuses_schema_without_parser_meta` |
| M4 `summary` 不再吐 `chunking_reasons` 分面 | **CAUGHT** | `test_summarize_documents_builds_authoritative_counts_facets_and_recent`、`test_document_catalog_api_exposes_chunking_reason_code_filter` |

4/4 转红。口径沿用既有约定：**这只证明本节列出的变异集会被抓住**，不等于"没有等价变异能溜过"。

## 5. 诚实边界

1. **这是无索引的 JSON 键扫描**。谓词落在 `parser_meta[key]` 上，MySQL 侧走不到索引，
   只靠同一 WHERE 里的 `tenant_id` + `dataset_id`（有索引）把扫描面收窄到一个库内。
   单库文档量级下可用；要变成 generated column + 二级索引是**独立的一条迁移切片**
   （需要新的 `catalog_migrations/versions/`、`tests/head_catalog.py` 时代夹具声明与
   capability 白名单同步），本片没做，也不该混进来。
2. **`chunking_reasons` 分面对旧库返回单个 `unknown` 桶**，不是报错。这是有意的：
   分面是"看见现状"，筛选具体值才是"要求答案"，两者的失败模式必须不同（见 2.2）。
3. **前端 legacy 路的 `unknown` 判定只看 `parser_meta`**，不区分"列不存在"与"键为空"。
   它跑在已有文档数组上，拿不到 schema 能力信息，因此不做能力错——这条路由本来就是降级路径。

## 6. Journey log（含两处不属于本片、但被本片查出的问题）

1. **上一条 source-preview 切片根本没重新导出契约**：`frontend/src/types/generated/openapi.json`
   里搜不到那个端点。`npm run types:gen` 只做 json → ts，事实源 json 要靠
   `scripts/export_openapi.py` 单独生成；只跑 `types:gen` 会得到"退出 0、diff 为空"的假绿。
   本片重跑导出后，source-preview 端点与 `chunking_reason_code` 一起进来（因此 +87/+55）。
2. **同一切片留下 8 条预存红**：`DocumentsPage.workspace.test.tsx` 的
   `vi.mock(".../parseInterventionApi")` 没导出 `fetchDocumentSource`，而 HEAD 的
   `SourcePreview.tsx` 已 import 并调用它 → `[vitest] No "fetchDocumentSource" export is defined`。
   用 `git show HEAD:...` 静态证实是 HEAD 就红，不是本片引入。已补导出并给一个安静的默认拒绝
   （这些用例考的是切片编辑与导航，不该因读不到原文而失败，也不该走进需要
   `URL.createObjectURL` 的成功分支），该文件 37 passed。
3. **全量前端的跑法本身是个坑**：`npx vitest run src` 用默认高并发，289 个文件互抢 CPU，
   `src/knowledge/KnowledgeWorkspaceContext.test.tsx` 在 `waitFor` 上超时红而单跑绿。
   仓里被认可的全量跑法是 `npm test`（`scripts/run-vitest-full.mjs`：UI 每批 2 文件、
   workers=1、heap 4GB）。已写进交接文档 §6。
4. **`server/document_catalog_api.py` 与 `tests/test_document_catalog_api.py` 在 HEAD 是混合行尾**
   （分别有 17 / 60 行是 LF-only），本片编辑把它们归一成 CRLF。所以 raw numstat（30/28、193/60）
   比 `--ignore-cr-at-eol`（13/11、133/0）大得多。语义改动是纯增量，行尾归一是附带的。
5. **顺带发现的一处死代码 —— 返工条已删除**：`core/catalog.py` 的 `_document_engine()` 全仓无
   调用方（`grep -rn "document_engine"` 排除 `__pycache__` 后只命中它自己的定义，连字符串形态
   的动态取用都没有）。它是 Python 侧的 `engine` 取值 + `unknown` 兜底，与本片抽出的 SQL 侧
   `_document_catalog_meta_key_expression` 是同一语义的两份实现——**留着就是将来 `unknown`
   漂移的第二个源头**。本片登记、第八轮评审（N1）之后删掉，删后 61 条 catalog 套件复跑全绿。
6. **本片留下的一个真缺陷：第 7 个筛选键只接了一半（第八轮评审 B1，已修）**。
   `DocumentsFilterState` 的键要在五处各写一遍：URL 解析、参数名对照、单键写入、"URL 变了回灌
   state"、"有没有筛选生效"。本片只在前三处加了 `chunkingReason`，于是：
   * 前进/后退到一条只有 `chunking_reason_code` 变化的 URL → 六个键逐个比完，没人发现筛选变了，
     整条回灌不跑，界面按旧值继续筛选而 URL 说的是另一套；
   * 与其它键同时变化 → 六个 setter 被回灌，这一项仍被吞；
   * **只按切分判定时"清除筛选"那颗按钮根本不出现**（它的可见性也按六项手写判断），操作员没有
     退回全部的入口。
   修法不是补那三行，而是把"键集合"交给类型：`documentFilterState: DocumentsFilterState` 与
   `documentFilterSetters: Record<keyof DocumentsFilterState, …>` 两张表 + 按表遍历，
   于是**少一个键是编译错误而不是漏测**；回灌效果原先手写的十个依赖项也一并换成"值放快照 ref"，
   连"忘了加进 deps"这一类都没有落点了。`clearAllDocumentFilters` 与可见性判断改为按
   `DEFAULT_DOCUMENT_FILTERS` / `documentFilterState` 的键集合走。
   反向验证 4/4 转红（`%TEMP%\mutate_round8_b1.py`）：回灌退回六项清单 → 新用例红；可见性退回
   六项 → 找不到"清除筛选"按钮；单键写退回 if/else 链 → `facet-item` 少了 `is-active`；
   清空退回手写六次调用 → 新用例红。
7. **另补一条跨语言守卫（第八轮评审 N3）**：后端四个判定码与前端 `CHUNK_REASON_CODE_LABELS`
   过去没有任何东西对账，第 5 个判定码落地时表现为分面里冒出一个裸 `snake_case`。
   `frontend/src/parse-intervention/model/chunkReasonLabels.source.test.ts` 用正则读
   `indexing/chunking_router.py` 真文件，双向钉（后端发的都有名 / 有名的是后端还在发的），
   与 `appNav.source.test.ts` 同型。
   未处理：N2（legacy 路径 trim+lowercase 与 SQL 精确比较不对称）当前无产地能触发它——四个码都是
   小写 snake_case 且由 `str(reason_code)` 直写——如实登记，不顺手改切分语义。

## Tasks

- [x] T1: 抽出 `parser_meta` 键筛选/分面共享 helper，`engine` 改走它 — acceptance: 既有 engine 用例全绿且旧符号已删除（covers: §2.1）
- [x] T2: `chunking_reason_code` 进 criteria + 游标哈希 + summary 分面 — acceptance: 4 条新用例绿，M1–M4 变异全红（covers: §2.2–2.4）
- [x] T3: API 查询参数 + 契约重新导出 — acceptance: HTTP 层用例绿，openapi.json 含新参数（covers: §2.4）
- [x] T4: 前端两条路 + URL 持久化 + 中文分面标签 — acceptance: `tsc` 0、`DocumentsPage.workspace` 37 passed（covers: §2.5）
- [x] T5: 门禁 + 反向变异 + 文档收口 — acceptance: 61 passed、ruff 干净、spec 与交接文档同步（depends: T1–T4）
