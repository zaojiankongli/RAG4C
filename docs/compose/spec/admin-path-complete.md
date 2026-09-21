---
feature: admin-path-complete
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 98185f0..working-tree
---

# Admin Path Complete（管理面写路径补全）

## Report

**What was built** — 远程 operator 中间件路径补全：`_ADMIN_PATHS` 增加 `/api/documents/batch-settings`、`batch-delete`；结构化规则覆盖 `{doc}/settings`、`{doc}/chunks/{chunk}`，并保留 `{doc}/reindex` 与知识库 delete。裸 `{doc}` GET 仍不入管理面。

**Verification** — 磁盘实测 `is_admin_path`：batch-settings/settings/chunks=True，bare doc=False；`pytest tests/test_admin_access_guard.py` **PASS** 13。评审首轮读到 stale tree 为 failed；以 live 复测为准（PASS）。

**Journey log**
1. 评审/验证必须对当前工作树重跑，不能沿用编辑前快照。
2. 路径级闸门无法区分 GET/DELETE；读路径刻意排除，避免远程误伤。

## Tasks

- [x] T1: is_admin_path 补全 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
