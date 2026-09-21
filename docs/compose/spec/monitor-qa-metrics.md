---
feature: monitor-qa-metrics
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 281e58b..working-tree
---

# Monitor QA Metrics

## Report

**What was built** — 监控保护信号列表增加 FAQ 检索三项：`query.qa_retrieval.hit` / `no_match` / `catalog_error`，与后端 `apply_qa_retrieval` 埋点键一致；MonitorPage 既有投影自动渲染。

**Verification** — `vitest monitorProjection.test.ts` **PASS** 10；`tsc` PASS。评审 live PASS。

**Journey log**
1. 只改 PROJECTION_DEFS 即可上监控面，无需改页面布局。

## Tasks

- [x] T1: 保护信号 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
