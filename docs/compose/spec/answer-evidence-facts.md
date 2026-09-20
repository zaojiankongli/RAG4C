---
feature: answer-evidence-facts
status: delivered
updated: 2026-09-20
branch: compose/qa-faq-ops
commits: 879b359..working-tree
---

# Answer Facts + Evidence Refs（会话-答案-证据权威，最小切片）

## Report

**What was built** — 在 MySQL Catalog 增加 privacy-safe 答案事实权威：Alembic `0039_answer_evidence_facts` 新建 `tenant_knowledge_answer_facts` 与 `tenant_knowledge_answer_evidence_refs`；只存 digest、去敏 `safe_query_preview`、outcome/route、citation 计数与 chunk/document/status 引用，**不存**原始 Q/A/prompt。查询路径（`/api/query` 与 stream）在 `_record_query` 旁 best-effort 写入 fact（stream 带 `run_id`）；失败不影响问答。只读 API `/api/knowledge-bases/{dataset_id}/answer-facts`（list / `{id}` / `by-run/{run_id}`）强制 **tenant + dataset** 双重过滤。VisualizePage 挂载「证据链」面板，按 run 或列表展示 outcome、引用状态与文档深链。

**Verification**

| 命令 | 结果 |
|---|---|
| `pytest tests/test_answer_evidence_facts.py tests/test_storage_backends_api.py tests/test_catalog_schema.py::test_upgrade_catalog_creates_current_versioned_schema` | **PASS** 6 passed（含 dataset 越权 404） |
| 同上 + qa_ops 回归 | **PASS** 10 passed |
| FE vitest answer-evidence + storage | **PASS** 20/20 |
| FE `tsc --noEmit` | **PASS** |
| `server.app` import 缺 `data/rag4c.db` | **PRE-EXISTING**（compose clone 环境） |

评审：首轮 partial — detail/by-run 忽略 path `dataset_id`（租户内跨库泄漏）。**已修复**：repo/API 均传 `dataset_id`，错误 dataset → 404；测试覆盖。

**Journey log**
1. knowledge-base 路径 API 必须 `resolve_path_dataset` **或** repo 强制 dataset 过滤；只做租户鉴权会跨库泄漏。
2. Catalog 隐私契约：测试断言 payload **无**原始 answer/evidence 正文，而非无任何 question 文本（preview 允许）。
3. `safe_preview` 需覆盖 `sk-xxxx` 等无 `=` 分隔的密钥形态。
4. 只有 stream 路径把 `run_id` 链到 fact；同步 `/api/query` 目前 `run_id=None`。
5. FastAPI 204 契约：`-> Response` + 显式 `Response(status_code=204)`。

## [S1] Problem
证据只存在于 HTTP 响应与进程 deque；无法从 Catalog 回答「这条答案靠什么证据」。Stage27 拒绝存完整会话。

## [S2] Design
交付与设计一致；澄清：detail/by-run **必须** dataset 过滤（评审 critical 已修）；evidence_digest 含 document_id；`citation_status` 无 DB CHECK（截断字符串）。

## [S3] Out of Scope
完整 Stage27 feedback/review；公开写 API；证据正文入库；Milvus 投影；cache-hit 事实记录。

## Tasks
- [x] T1: ORM + 0039 + catalog_schema — upgrade 到 0039（covers: S2.2）
- [x] T2: repository + record/list/get + app best-effort 接线（covers: S2.3; covers: S2.4）
- [x] T3: answer_evidence_api + dataset 作用域 — API 测试绿（covers: S2.5）
- [x] T4: 前端 Visualize 证据链面板 — vitest + tsc PASS（covers: S2.6）
- [x] T5: 门禁 + 评审 critical 修复复验（depends: T4）

## Workspace
- `D:\program_project\python_project\RAG4C-compose-qa-faq-ops` / `compose/qa-faq-ops`
- 迁移序 0036→0037 QA→0038 storage→**0039 answer evidence**；未提交
