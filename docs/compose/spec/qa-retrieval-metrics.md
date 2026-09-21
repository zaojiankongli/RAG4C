---
feature: qa-retrieval-metrics
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 058a151..working-tree
---

# QA Retrieval Metrics

## Report

**What was built** — `apply_qa_retrieval` 在互斥路径上埋点：`query.qa_retrieval.enabled_skip` / `no_bundle` / `no_match` / `hit`（配合既有 `catalog_error`）。运营可从 metrics 快照判断 FAQ 检索是否生效。

**Verification** — `pytest tests/test_qa_matcher.py` **PASS** 11（含四计数断言）。评审 live 复测 PASS。

**Journey log**
1. 埋点放在 apply 函数 early-return 上，调用方 rag/rag_stream 自动覆盖。
2. 计数路径互斥，无需在测试里硬拆多重计数。

## Tasks

- [x] T1: metrics 埋点 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
