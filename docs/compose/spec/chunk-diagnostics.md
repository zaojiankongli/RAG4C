---
feature: chunk-diagnostics
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 1b611ed..working-tree
---

# Chunk Diagnostics（切分路由可解释性）

## Report

**What was built** — 切分路由从「只记 mode」升级为可解释诊断：`ChunkingRouter.explain_decision` 输出 mode + `reason_code` + 中文 `reason` + facts（`doc_type/text_chars/layout_blocks/simple_max_chars/configured_mode`）；`decide()` 委托同一判定表，路由行为不变。入库 `ingest_meta` 持久化 `chunking_reason` / `chunking_reason_code` / `chunking_decision` 到 `parser_meta`。Parse Context 面板展示「决策理由 / 决策依据」；Documents 画像用 title/aria 携带理由，列表显示理由代码标签。历史文档无 reason 时诚实显示「未记录」，不编造。

**Verification** — 主仓 `D:\program_project\python_project\RAG4C`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_chunk_diagnostics.py` | **PASS** 3 |
| `tsc --noEmit` | **PASS** |
| `vitest` chunkDiagnostics + ParseInterventionWorkspace | **PASS** 7 |
| 独立评审 | **success**，无 critical |

**Journey log**
1. WeKnora 对比结论：RAG4C 默认切分质量更好，但诊断可解释性弱——本切片补 reason 而非改路由阈值。
2. `parser_meta` 经 `state_machine` `_collect_meta(ingest_meta)` 落库，无需新迁移。
3. facts 字段以代码为准：`simple_max_chars`（spec 曾写 threshold，已对齐）。
4. React 侧诊断格式化集中在 `chunkDiagnostics.ts`，Documents 与 Parse Pane 共用，避免两套文案漂移。

## [S1] Problem

入库已把实际 `chunking_mode` 写入 `parser_meta`，前端能显示模式名，但**说不清为什么**：

- `ChunkingRouter.decide` 只返回模式字符串，阈值/版面/doc_type 证据未落库；
- Documents 画像与 Parse Context 无法回答「为何这篇走 parent_child / recursive」；
- 对照 WeKnora preview，RAG4C 缺操作员可读的决策理由。

## [S2] Design

### 2.1 路由解释 `indexing/chunking_router.py`

`explain_decision(doc_type, text, layout) -> dict`：

| 字段 | 含义 |
|---|---|
| `mode` | recursive / parent_child / qa |
| `configured_mode` | 构造时的 mode |
| `reason_code` | `explicit_mode` / `table_doc_type` / `simple_short_no_layout` / `complex_or_structured` |
| `reason` | 中文一句话（含关键数值） |
| `facts` | `doc_type`, `text_chars`, `layout_blocks`, `simple_max_chars`, `configured_mode` |

`decide()` = `explain_decision()["mode"]`。

### 2.2 入库元信息

`ingest_meta()` 增加 `chunking_reason` / `chunking_reason_code` / `chunking_decision`，经 catalog `parser_meta` 持久化。

### 2.3 前端展示

ParseContextPane：切分策略 + 决策理由 + 决策依据；Documents 画像：reason 在 title/aria，理由代码作紧凑标签。

### 2.4 测试

router 四种 reason_code 与 decide 一致；ingest_meta 字段；FE formatChunkingDecision；Parse workspace 显示理由。

## [S3] Out of Scope

入库后重切/预览 API；改 auto 阈值本身；WeKnora 多 tier 校验链。

## Tasks

- [x] T1: explain_decision + 单测 — acceptance: 路由表与 reason_code 单测绿（covers: S2.1）
- [x] T2: ingest_meta 持久化字段 — acceptance: ingest_meta 含 reason/decision（covers: S2.2）
- [x] T3: 前端诊断展示 — acceptance: pane/page 显示理由，vitest/tsc 绿（covers: S2.3）
- [x] T4: 门禁 + 评审 + finalize — acceptance: pytest + tsc/vitest PASS，spec delivered（depends: T3）

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
