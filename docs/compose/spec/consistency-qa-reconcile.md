---
feature: consistency-qa-reconcile
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 5738cfe..working-tree
---

# Consistency QA Reconcile（一致性控制台 QA 权威对账）

## Report

**What was built** — 一致性摘要增加 **Catalog-only** `qa_authority`：total / effective_retrieval（approved+active+启用+生效窗口）/ pending_review / rejected / expired / retrieval_disabled（**仅** approved+active+禁检索，不含 pending/rejected）。计数失败时 `qa_authority=null` 并 **warning 日志**，不编造 0。ConsistencyPage 新增「QA 检索权威（Catalog）」区块，分列可检索 / 待审核 / 过审未启用 / 失效+驳回，文案明确不投影 Milvus。前端 `normalizeQaAuthority` 要求完整计数字段，缺字段返回 null。

**Verification** — 主仓 `D:\program_project\python_project\RAG4C`：

| 命令 | 结果 |
|---|---|
| `pytest tests/test_consistency_qa_authority.py` | **PASS** 1 |
| `tsc --noEmit` | **PASS** |
| `vitest` consistencyModel + ConsistencyPage | **PASS** 18 |
| 独立评审 critical | **已修**：retrieval_disabled 桶重叠、失败无日志、UI 双计、normalize 零填 |

**Journey log**
1. pending/rejected 因 CHECK 约束天然是 `retrieval_enabled=false`，不能算进「过审未启用」。
2. Soft-fail 字段必须带日志，否则违反「生产失败可见」。
3. 操作员 UI 桶要互斥可读，不能把 expired+disabled+rejected 混成一个「不可检索」。
4. 前端缺字段返回 null 比零填更诚实。

## [S1] Problem

一致性控制台只报文档投影漂移，QA 权威（可检索 FAQ 等）不在对账视图。

## [S2] Design

见 Report；summary 载荷 `qa_authority` + FE QA 权威区块；catalog_only 语义。

## [S3] Out of Scope

QA 投影进 Milvus；对 QA 的 repair/死信。

## Tasks

- [x] T1: summary qa_authority 计数 + 模型 — covers: S2.1
- [x] T2: 前端 model + ConsistencyPage 展示 — covers: S2.2
- [x] T3: 门禁 + 评审 critical 修复 + finalize — depends: T2

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
