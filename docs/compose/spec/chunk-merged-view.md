---
feature: chunk-merged-view
status: delivered
updated: 2026-09-22
branch: main
commits: 546531b
---

# 切片 S-MV：整篇合并视图（补齐 WeKnora 的"查看"那一半）

> 前置：`chunk-lifecycle-writers`（已交付）与 `chunk-lifecycle-ui`（在途）。
> 本切片是"文档分开后可编辑**和查看**"里"查看"的剩余部分：编辑面与 per-chunk
> `source_content` 对比已到位，但操作员仍然看不到"这篇文档现在整体是什么"。

## [S1] Problem

WeKnora 的文档视图有三种模式（`frontend/src/components/doc-content.vue` 的
`viewMode: 'chunks' | 'merged' | 'preview'`）：切片列表、**由切片还原的整篇合并视图**、
原文预览。RAG4C 现在只有第一种加上 per-chunk 的"原始 vs 当前"对比。

结果是一个具体的运营困境：人工改完若干切片之后，操作员**没有任何地方能看到这篇文档现在的
整体样子**，也就无法判断"我这几处编辑连起来读是否通顺、父子块是否重复、墓碑是否留下了破洞"。
`ParsedContextPane.tsx` 里至今写着「当前没有原始文档预览能力」。

## [S2] Design

### S2.1 合并视图不需要新端点，也不需要文件伺服

已核实（2026-09-22 重读 `core/chunk_catalog.py:130-161`）：

- 排序：`list_document_heads_page` 的 SQL 就是
  `order_by(ChunkHead.chunk_index, ChunkHead.id)`（`core/chunk_catalog.py:152`）。
- 父块排除：不是"前端要记得过滤"，而是**服务端 SQL 条件**
  `ChunkHead.chunk_role != "parent"`（`core/chunk_catalog.py:137`）——列表响应里根本不会有父块，
  所以合并视图不可能误把父子各拼一遍。
- 墓碑纳入：同一个 `include_disabled` 参数（`core/chunk_catalog.py:138-139`）已经能把
  `enabled=false` 的头一起取出来；工作区默认就带 `includeDisabled:true` 拉列表。
- `source_content` 是 `ChunkHead` 的实体列（`models/orm.py:6928`），但**只在单切片读取里返回**，
  列表响应刻意不带（见 `frontend/src/types/rag.ts` 的 `DocumentChunkItem.source_content?` 注释）。
  所以"这一处被人工改过"的标记不能靠合并视图自己比对，只能沿用列表已给出的
  `content_revision > 1` / `edit_source` 一类的既有事实，或对该切片单独取详情。
  **不要为了合并视图去改列表响应**——那会让每页多带全文两遍。

所以"按权威顺序拼回整篇"是纯前端聚合。

**唯一的真实代价是分页**：合并视图要完整，必须把该文档所有页都载进来
（`PAGE_SIZE=100`，`hasMore`/`loadMore` 已在 `useParseIntervention` 里）。因此
未载全时**必须**显示"当前合并视图只包含前 N / 共 M 个切片"并给出继续载入入口，
禁止让人误以为看到的是整篇。

**明确不做整篇 `preview`**（原文文件预览）：那需要文件伺服与权限模型，是另一件事，
和"由 ChunkHead 拼出的当前态"不是一回事，两者不能互相冒充 —— 合并视图回答的是
"知识当前是什么"，原文预览回答的是"解析器当初看到了什么"。

### S2.2 拼装规则（必须写死在模型层并有测试）

1. 只取 `chunk_role != "parent"` 的头（父块与子块同拼会重复正文；子块才是投影与检索单位，
   与 `list_projection_candidates`（`core/chunk_catalog.py:396-411`）的取法保持一致）。
2. 顺序取服务端给的 `chunk_index` 次序，前端不再按 `seq` 重排 —— 两套排序会分叉。
3. 墓碑（`enabled=false`）默认**以占位形式显示但不计入正文**，并明确标注"此处已被人工停用"，
   让破洞可见而不是静默消失（invariant #4：失败与缺口要可见）。
4. 分页是服务端游标式的（`offset/limit`），合并视图必须显式说明"当前只包含已载入的前 N 个切片"，
   并给出继续载入的入口；**禁止**让人误以为看到的是整篇。
5. ~~`source_content` 与当前 `text` 不同的行，提供"这一处被人工改过"的标记~~ —— 见 S2.1，
   列表不返回 `source_content`，合并视图**不做**逐行原文比对；改用过 `content_revision`
   与 `edit_source` 标出"这一处被人工动过（rev N）"，点开该切片再走既有的"原始 vs 当前"详情。

### S2.3 放置

在 `parse-intervention` 工作区里作为第四种视图（列表｜合并｜编辑的并列，或列表内的模式切换），
沿用现有 `role=tablist` / `tabpanel` 与键盘导航；不新增第三种弹层。
虚拟列表沿用 `ChunkListPane` 已有的实现方式，避免长文档一次性渲染整篇 DOM。

## [S3] Out of Scope

整篇原文文件预览（需文件伺服）、导出为 Markdown/PDF、跨文档全文检索视图、
编辑冲突的三向合并、按章节树折叠的大纲视图。

## Tasks

- [x] V1: `model/mergedDocument.ts` 纯函数（入参 `DocumentChunkItem[]`，出参分段 + 墓碑占位 + 人工改动标记）
  —— 2026-09-22 交付：`frontend/src/parse-intervention/model/mergedDocument.ts` +
  `mergedDocument.test.ts`，项目自带 vitest 3.2.7 实跑 **8 passed (8)**，`tsc --noEmit` 退出码 0。
  覆盖：服务端次序为准（不按 `seq` 重排）、父块排除、墓碑原位占位且不进正文、
  未载全/总数未知两种"不假装整篇"、`content_revision > 1` 才标人工改过、空列表不伪造正文。
- [x] V2: 视图接入与模式切换 —— 2026-09-22 交付：`components/ChunkMergedView.tsx`，挂在
  `ChunkListPane` 头部的「切片列表｜整篇合并」切换下（`aria-pressed` 标当前态）。
  选这个位置而不是新增第四栏：不改工作区 3 栏 grid，也不动 mobile tablist 的 `%3` 取模算术。
  `ChunkMergedView.test.tsx` 实跑 **6 passed (6)**：默认列表不提前渲染合并视图、部分载入时
  明说"仅包含已载入的前 N / M 个切片"与"下面的正文不是全文"、墓碑占位在原位可见且其正文
  不进合并、父块正文不出现且说明排除数量、每段可定位回对应切片、继续载入复用既有分页回调、
  载全后不再出现"仅包含"。
  注意一个已核实的行为边界：合并视图吃的是 `p.chunks`（服务端已按 `chunk_index` 排序的原始
  已载集合），**不是** `ChunkListPane` 里 `visible` 那份过滤后的列表 —— 筛选条件不参与合并，
  否则"整篇"会被筛成假象。
- [x] V3: 门禁 —— `tsc --noEmit` 退出码 0；`eslint` 对新增/改动的 8 个前端文件无输出；
  `vitest run src/parse-intervention src/documents src/run src/answer-evidence src/components src/pages`
  = **422 passed**（`EnterpriseAdminPage.stage15` 与 `RunEventTable` 各在并发负载下红过一次，
  单独重跑均绿，属既有负载敏感红）。
  **未做且本机做不了**：浏览器运行时观察。权威 MySQL 不可达，后端起不来就没有真数据，
  因此合并视图的视觉与滚动只验到 jsdom DOM 断言这一层，不等于有人看过界面。

## Workspace

- 依赖 `chunk-lifecycle-ui` 落地后再做，否则会与其在同文件冲突
- 服务端已保证的次序不要在前端重做一遍（两套排序是本仓反复出现过的漂移源）
