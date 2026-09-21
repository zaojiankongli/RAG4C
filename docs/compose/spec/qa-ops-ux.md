---
feature: qa-ops-ux
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: e0bebde..working-tree
---

# QA Ops UX（治理空态 CTA + 一致性深链）

## Report

**What was built** — QAGovernancePanel 空态在存在筛选时显示「清除筛选」→ `setFilters({})`；ConsistencyPage QA 权威区块增加「打开 QA 治理」链接 `#/governance`。无筛选时不显示清除 CTA。

**Verification** — `vitest` QAGovernancePanel + ConsistencyPage **PASS** 32（含 clear-filters 与 `打开 QA 治理` href 断言）；`tsc` PASS。

**Journey log**
1. 深链必须在测试里断言 href，否则 vitest 总数不能证明 AC。
2. 误覆盖测试文件后从 git HEAD 恢复再追加，比手工重写整文件更安全。

## Tasks
- [x] T1: 空态 CTA + 深链 + 测试 (covers: S2)

## Workspace
- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
