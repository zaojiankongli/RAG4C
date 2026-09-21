---
feature: admin-path-tighten
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: e0bebde..working-tree
---

# Admin Path Tighten

## Report

**What was built** — `is_admin_path` 改为结构化匹配：精确白名单仅保留真实全局写路径；知识库文档写仅认 `batch-delete` 与 `{doc}/delete` 两段结构；reindex 仅认 `/api/documents/{doc_id}/reindex`（拒绝 `/reindex` 自身与多级路径）。去掉过宽 `/delete` 后缀与错误字面量。

**Verification** — `pytest tests/test_admin_access_guard.py tests/test_admin_endpoint_actor_auth.py tests/test_graph_store_registry.py` **PASS** 19（含 reindex 正/负路径）。

**Journey log**
1. 过宽 suffix 会把无关路径划入管理面。
2. 结构化匹配必须做 **段数校验**，不能只 `endswith`。

## Tasks
- [x] T1: 收紧 is_admin_path + 测试 (covers: S2)

## Workspace
- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
