---
feature: qa-faq-ops
status: delivered
updated: 2026-09-20
branch: compose/qa-faq-ops
commits: 879b359..working-tree  # compose workspace uncommitted delivery tree
---

# QA/FAQ 运营闭环 + 治理前端增强

## Report

**What was built** — 在现有 `QAKnowledge` 权威上补齐 WeKnora 式 FAQ 运营面，而不另起产品：Alembic `0037_qa_faq_ops` 增加 `content_hash` / `import_batch_id` / `origin=import` 与表 `qa_negative_questions`；repository/API 提供 `POST /qa/import`（content-hash 去重、默认 pending 不可检索）、`POST /qa/batch/{review,expire,restore}`（逐条 CAS、部分失败 HTTP 200 + failed[]）、`GET /qa/export`（json/csv）、反例问 CRUD。`update_qa` 在问题/答案变更时重算 `content_hash`，避免导入去重漂移。前端治理页扩展多选批量条、导入对话框（CSV/JSON）、按筛选导出、反例问维护、空态 CTA，并修复 onboarding 二次弹出与主按钮对比度（`rag4c.onboarding.v1` + `--color-on-primary` / `--control-min-h`）。

**Verification** — compose 工作区 `D:\program_project\python_project\RAG4C-compose-qa-faq-ops`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_knowledge_content_qa_ops.py tests/test_knowledge_content.py tests/test_knowledge_content_api.py::test_qa_import_batch_and_negative_question_apis` | **PASS** 20 passed（含 hash 重算与权限 403 矩阵） |
| `pytest tests/test_catalog_schema.py tests/test_knowledge_content_migration.py` | **PASS** 65 passed（HEAD=`0037_qa_faq_ops`） |
| `pytest tests/test_knowledge_content_api.py::test_bridge_preserves_structured_knowledge_errors` | **PRE-EXISTING** FileNotFoundError `data/rag4c.db`（compose clone 无本地 catalog 文件） |
| `tsc --noEmit` + `vite build` | **PASS**（build ~38s） |
| `eslint src/governance src/onboarding ...` | **PASS** 0 error / 2 react-refresh warnings（导出常量，非阻塞） |
| `vitest` governance+onboarding+KnowledgeGovernancePage | **PASS** 48/48 |
| `vitest src/governance src/onboarding src/pages/KnowledgeGovernancePage.test.tsx src/theme` | **PASS** 13 files / 74 tests |
| `scripts/run-vitest-full.mjs` | **未完成** — 10min timeout，无汇总（compose 全量 vitest 不作为可靠门禁） |
| Playwright `:1421` onboarding | **PASS** 跳过写入 `rag4c.onboarding.v1=1` 与 legacy `rag4c.onboarding_done=1`；再访 overview 不再出现欢迎弹窗 |

独立评审：首轮 partial（关键项 `update_qa` 未重算 hash）→ 已修复并补测试；复审 success，无剩余 critical。

**Journey log**
1. `git worktree add` 被沙箱拒绝（共享 ref store）→ 改用独立 clone `RAG4C-compose-qa-faq-ops` @ `compose/qa-faq-ops`。
2. 批量路由必须注册在 `/qa/{qa_id}/*` **之前**，否则 `/qa/batch/review` 被单条 review 吃掉并 422。
3. SQLite 迁移须用 `batch_alter_table` 重写 `ck_qa_knowledge_origin`（否则 import 撞 CHECK）；import 逐条 `begin_nested` 防会话污染。
4. catalog HEAD 前进到 0037 时，各企业 capability 的 revision 白名单与 `_REVISION_ORDER_FOR_CAPABILITY` 必须同步挂上新 revision，否则 readiness 误报 unavailable。
5. 全量 vitest 在 compose 工作区 10min 超时；实用 FE 门禁 = tsc + vite build + 定向/治理面 vitest。

## [S1] Problem
Playwright 实测 + WeKnora 对比：RAG4C 已有 QA 权威与审核门控，但运营仍是单条点击；无批量导入/导出、content-hash 去重、反例问；治理空态与首次 onboarding 阻断企业控制台体验。

## [S2] Design
见初版设计；交付偏差：
- 迁移号实际为 **0037**（clone 基线无未提交的 0037 企业反馈迁移）。
- import 响应字段为 `created[]`（非 `items[]`）；未实现 `default_review_status`（固定 pending，符合不变式 7）。
- CSV 导出用全宽逗号替代字段内逗号，偏运维可读而非 RFC 机器往返。
- 检索管线**未**消费反例问（S3 边界保持）。

## [S3] Out of Scope
检索 negative 过滤、Wiki/MCP/存储后端/会话权威、IA 合并、全站配置页清扫、主工作区未提交 R7 WIP。

## Tasks
- [x] T1: Alembic 0037 + catalog_schema + ORM（content_hash/import_batch_id/origin=import/qa_negative_questions）— acceptance: schema 期望与 ORM 一致，迁移可 upgrade（covers: S2.2）
- [x] T2: repository + API：import/batch/export/negative CRUD/list payload — acceptance: 新 API 单测绿，既有 qa 单测不回归（covers: S2.3; depends: T1）
- [x] T3: 前端 API/model/hook 接线 batch+import+export+negative — acceptance: governanceApi/useQAGovernance 测试绿（covers: S2.5; depends: T2）
- [x] T4: QAGovernancePanel 批量条/导入导出对话框/反例问 UI + 空态 CTA + onboarding 跳过与按钮对比度 — acceptance: 面板测试覆盖批量与导入结果；Playwright 复测 onboarding 不重复阻断（covers: S2.5; depends: T3）
- [x] T5: 门禁验收 — acceptance: 后端 qa/content/catalog 测试、tsc/build、治理面 vitest 全绿；全量 vitest 超时记为未完成；bridge 测试 data/rag4c.db 缺失记 PRE-EXISTING（covers: S2.6; depends: T4）

## Workspace override

- `git worktree add` 在本会话被沙箱拒绝（共享 ref store 保护）。
- 采用独立 clone 工作区：`D:\program_project\python_project\RAG4C-compose-qa-faq-ops`，分支 `compose/qa-faq-ops`，基线 `879b359`。
- 主工作区未提交改动 **未** 迁入；交付树目前 **未 commit**（待用户选择合并方式后再提交）。
