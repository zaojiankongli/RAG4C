---
feature: chunk-lifecycle-ui
status: planned
updated: 2026-09-22
branch: main
commits: —
---

# 切片 S-CL-UI：把切片生命周期露出来，并把答案接回切片

> 前置：`docs/compose/spec/chunk-lifecycle-writers.md`（后端已交付 `enabled` / `reason` /
> `GET …/revisions` / `POST …/revert` 与 `_projection` 的 `source_content` 等字段）。
> 本切片只做功能与信息架构，不做设计系统收敛、不做契约全量迁移。

## [S1] Problem

`frontend/src/parse-intervention/` 已经是真工作区，不是壳：三栏（上下文｜切片｜编辑）、
`Textarea` 就地编辑、`buildDiff` 行级 diff、CAS facts（Chunk ID / Expected Revision /
Document Revision / Projection Fence）、墓碑只读、orphan draft 冲突恢复、移动端 tablist
与 `role=tablist/tabpanel` 齐备。它缺的正是后端这一轮刚补齐的四件事，加上"到不了、回不去"：

1. `ChunkEditorPane.tsx` 里有两条自认缺口的 Alert，原文是
   「当前 API 不持久化修改原因」与「当前没有切片 Revision 历史接口」—— 现在 API 有了，Alert 必须换成真功能。
2. `enabled` 只能看不能用：`ChunkListPane` 用 Tag 显示，`ChunkEditorPane` 用它算 `readOnly`，没有任何控件能开合；
   写进墓碑之后（`state.submit()` 的按钮在 `selected?.enabled===false` 时禁用，`readOnly` 同理）就再也出不来。
3. 提交按钮要求 `state.reason.trim()` 非空，但原因今天不入库 —— 用户被要求填一个没有下游消费者的字段。
4. 答案与切片之间没有回路：证据面板与 `RetrievalTrace` 的 `stale`（文本已被改过）都跳不到被改的那个 chunk。
5. 整个工作区只能从文档页内全屏接管进入（`documents/documentParseRoute.ts` 的
   `/documents/parse?doc=<id>`），没有 `PageKey`、没有侧栏入口 —— 它是本产品的定义性体验
   （Knowledge Lifeline 上"人工干预"那一格），却是不可发现的。

## [S2] Design

### S2.1 API 客户端（`parse-intervention/api/parseInterventionApi.ts`）

沿用现有 `headers(scope)`（Bearer actor token + `X-RAG4C-Tenant`）与 `base(scope)`，新增/扩展四个函数：

| 函数 | 请求 |
|---|---|
| `patchParseChunk(scope, chunkId, text, expectedRevision, reason, signal)` | `PATCH …/{chunk}` body 加 `reason` |
| `setParseChunkEnabled(scope, chunkId, enabled, expectedRevision, reason, signal)` | 同 `PATCH`，body 只带 `{enabled, expected_revision, reason}` |
| `fetchParseChunkRevisions(scope, chunkId, signal)` | `GET …/{chunk}/revisions` |
| `revertParseChunk(scope, chunkId, targetRevision, expectedRevision, reason, signal)` | `POST …/{chunk}/revert` |

`tombstoneParseChunk` 改写成 `setParseChunkEnabled(false)` 的语法糖或直接删除（保留 `DELETE` 端点给
非工作区调用方）。**所有类型来自 `../../types/rag`**（本切片按手写类型惯例继续，不做 generated 迁移）。

### S2.2 状态层（`parse-intervention/hooks/useParseIntervention.ts`）

保留它现有的竞态防护（`keyRef` + `seqRef` + 双 `AbortController`）。新增：

- `setEnabled(next: boolean)`：走 `setParseChunkEnabled`，成功后把 head 换进 `chunks` 与 `selected`，
  刷新 `expectedRevision`，并复用现有 `receipt` 通道显示投影回执（`operation_ids`）。
  墓碑→启用必须让编辑器从 `readOnly` 回到可编辑，Alert 文案相应消失。
- `revisions` / `loadRevisions()` / `revertTo(revision)`：revert 走一次 mutation，成功即重载
  detail + revisions；失败按现有 409 处理路径归到 orphan draft 同一语义。
- 提交与启用/停用都要求非空 `reason`，与现有按钮禁用逻辑一致（不新增第二套校验口径）。

`model/parseInterventionModel.ts` 增加 revision 行的视图模型（版本号、时间、编辑者、启停、
是否当前版）与"原始 vs 当前"的对比输入（`source_content` vs `text`），并把
`buildDiff` 复用到原始内容对比上，避免写第二套 diff。

### S2.3 界面

- `ChunkEditorPane.tsx`
  - 删掉两条过期 Alert，替换为：启用/停用开关（墓碑态显示「启用此切片」并解释后果）、
    Revision 历史入口、原始/当前内容切换。
  - 提交区保持单一主操作，避免和开关互相误触；破坏性动作用现有 `Popconfirm` 惯例。
- `ChunkListPane.tsx`
  - 列表行上的 `enabled` 从纯 Tag 变为可筛（墓碑默认藏，沿用已有"只看警告"式过滤交互）；
    不引入多选批量操作（out of scope）。
- 历史以抽屉/侧叠层呈现，不新造第三层弹层；每行提供「回滚到此版」，二次确认。
- 键盘与读屏：新增控件全部走 `ui/index.tsx` 现有 TDesign 兼容层，`aria-label` 齐备，
  触控目标沿用 `--control-min-h` 的 44px 契约（R7 §11.13 已量化），状态不只靠颜色。

### S2.4 可达性与答案↔切片回路

- `run/appRoute.ts`：`PageKey` 增一个键（沿用现有 `PAGE_KEYS` / `keyOf` /
  `navigationIntent` 三处一起改，hash 与 history 双模式都要能解析），
  深链形状 `…?doc=<id>&chunk=<id>`；`documents/documentParseRoute.ts` 的老链接保留为别名并迁移参数。
- `App.tsx` 侧栏「知识库」组内加入口；`pageNodes` 注册同一组件，避免第二份装配。
- `answer-evidence/components/AnswerEvidencePanel.tsx` 每条证据、`components/RetrievalTrace.tsx`
  的 `stale` 徽标，都加"在解析干预里查看这个切片"的深链；点进去必须选中那个 chunk
  （S2.2 的 `selectChunk` 接收初始 chunkId）。
- 反向也留一行：工作区里显示该切片当前是否被最近答案引用过，仅在已有数据能支撑时显示，
  不新造查询端点。

## [S3] Out of Scope

设计系统收敛（`ui/index.tsx` 的 82 处 `any`、`styles.css` 拆分）、`types/rag.ts` → generated 全量迁移、
合并/拆分/新增切片、批量启停、按文档调切分参数、整篇原文文件预览、E2E 断言 runner。

## Tasks

- [ ] U1: API 客户端四个函数 + `types/rag.ts` 字段 —— acceptance: 定向 vitest 断言 URL、method、body 形状与鉴权头（covers: S2.1）
- [ ] U2: hook 状态层 setEnabled / revisions / revertTo —— acceptance: 竞态与 409 路径沿用既有测试语义；墓碑→启用后 `readOnly` 转假（covers: S2.2; depends: U1）
- [ ] U3: 三个面板改造 + 两条过期 Alert 删除 —— acceptance: 源级守卫断言工作区不再出现「没有切片 Revision 历史接口」；a11y/44px 契约测试绿（covers: S2.3; depends: U2）
- [ ] U4: `PageKey` + 侧栏入口 + 老深链别名 —— acceptance: hash/history 双模式解析与前进后退测试（covers: S2.4; depends: U2）
- [ ] U5: 答案→切片深链（证据面板 + `stale` 徽标）—— acceptance: 点击后工作区选中该 chunk（covers: S2.4; depends: U4）
- [ ] U6: 门禁 —— acceptance: `tsc --noEmit`、`npm run check:contract`、`eslint`、定向 vitest（parse-intervention / answer-evidence / run 路由 / theme 契约）实跑读数写入 Report（depends: U3; U5）

## Workspace

- `frontend/`，命令见 `frontend/package.json`；全量 vitest 可能 >10min，默认跑定向套件 + tsc
- 视觉与主题契约不得回退：三主题 light/dark/anime、`prefers-reduced-motion`、skip-link、
  `PageState` 三态、README:8-21 的既有承诺表
- 已知失效文档，不要照抄：`frontend/README.md:33-70` 的目录结构、`UI_OPTIMIZATION_SUMMARY.md` 的配色
