---
feature: qa-evidence-ui
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: fd6bea3..working-tree
---

# QA Evidence UI（答案证据链上的 QA 权威可见性）

## Report

**What was built** — 让 Knowledge Lifeline 在答案证据面板上可见：`chunk_id=qa::{id}` 与 API `source_kind/qa_id` 识别 QA 权威引用；面板显示「QA 权威」Tag、治理深链 `#/governance?qa=…`（对齐 `PAGE_KEYS.governance`）、摘要「QA 命中」计数；文档深链不对 `qa::` 伪 id 生成。后端 `fact_payload` 补 `evidence_digest`、`source_kind`、`qa_id`、`qa_revision`（`chunk_revision_id=qa-rev::{n}` 由 `metadata.qa_revision` 落库），并输出 `qa_evidence_count`。

**Verification** — 主仓 `D:\program_project\python_project\RAG4C`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_answer_evidence_facts.py tests/test_qa_retrieval_bundle.py` | **PASS** 5 |
| `tsc --noEmit` | **PASS** |
| `vitest` answer-evidence model/panel/api | **PASS** 17 / 3 files |
| 独立评审 critical（深链 `#/knowledge-governance` 非法路由） | **已修复** → `#/governance`；`qa_revision` 落库与 API 字段一并补齐后复测 |

**Journey log**
1. React 保留 prop 名 `ref`：EvidenceRefRow 不可用 `ref=` 传参，否则子组件拿到 undefined。
2. 治理页 shell 路由是 `/governance` / `PAGE_KEYS.governance`，不是组件名 `KnowledgeGovernancePage` 对应的 `knowledge-governance`。
3. evidence 载荷里 `qa_revision` 嵌在 `metadata.qa_revision`，record 时需展开写入 `chunk_revision_id=qa-rev:n` 才能持久化。
4. API 归一化会物化 `source_kind/qa_id/qa_revision`，FE 测试期望需同步，不能只测旧字段集合。

## [S1] Problem

`qa-retrieval-evidence` 已让审核后 FAQ 进入检索与 answer fact（`chunk_id=qa::{id}`），但 **Visualize / 答案证据面板**仍把 QA 引用画成普通 chunk：

- 无「QA 权威 / FAQ」标识，操作员看不出证据来自 Catalog FAQ 而非文档投影；
- 文档深链对 `qa::` 无意义，也缺少跳到治理页 QA 条目的路径；
- 摘要不统计 QA 命中，Knowledge Lifeline 在 UI 上断在证据列表。

## [S2] Design

### 2.1 来源识别（纯前端派生 + API 字段）

- chunk_id 以 `qa::` 前缀 → `source_kind=qa`，`qa_id` 为后缀；
- API `fact_payload` 的 evidence_refs 补齐 `evidence_digest` / `source_kind` / `qa_id` / `qa_revision`；
- 写入侧：citation/evidence 的 `metadata.qa_revision` → `chunk_revision_id=qa-rev:{n}`；
- 前端 `normalizeAnswerEvidenceRef` 兼容缺省：无 API 字段时仍用 `qa::` 前缀推断。

### 2.2 面板展示

| 区域 | 行为 |
|---|---|
| 证据行 Tag | kind=qa → 「QA 权威」 |
| 证据行 ID | 显示 `qa_id` + 可选 revision；document 深链仅在 kind=document 且非 `qa::` id |
| QA 深链 | `#/governance?qa={qaId}`（`PAGE_KEYS.governance`） |
| 摘要 | 「QA 命中」计数 |
| 无障碍 | `aria-label` 含 `source_kind=qa` 或 `document` |

### 2.3 测试

model 解析 / panel QA Tag 与深链 / backend fact_payload 字段（含 qa_revision）。

## [S3] Out of Scope

- 证据正文/FAQ 答案全文进面板；
- 治理页按 URL 参数自动展开 QA 行；
- 改 answer fact 写入语义。

## Tasks

- [x] T1: model 来源解析 + 事实载荷字段 — acceptance: unit 绿（covers: S2.1）
- [x] T2: AnswerEvidencePanel QA 可视化 — acceptance: panel 测试含 QA Tag/计数/深链（covers: S2.2）
- [x] T3: backend fact_payload 补字段 — acceptance: answer_evidence pytest 绿（covers: S2.1）
- [x] T4: 门禁 + 评审 + finalize — acceptance: vitest+tsc+pytest PASS，spec delivered（depends: T2）

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`（worktree 沙箱不可用 override 同前）
