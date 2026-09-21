---
feature: config-qa-retrieval
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: b0cb8a1..b40ba52
---

# Config QA Retrieval

## Report

**What was built** — 策略/配置面暴露 FAQ 检索：召回组 `qa_retrieval_on` + `qa_match_min_score` / `qa_match_top_k`（依赖开关）；ConfigPage 与 `app.py` 字段说明中文同步。

**Verification** — `vitest configQaRetrieval` **PASS** 1；`tsc` PASS。评审 PASS。

**Journey log**
1. 后端开关先交付、配置面后补，避免无处调参。

## Tasks

- [x] T1: 策略面 + 文案 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
