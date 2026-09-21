---
feature: docs-parser-reason-ui
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 38550fe..working-tree
---

# Docs Parser Reason UI

## Report

**What was built** — 文档列表「解析画像」在模式名外可见展示切分理由代码与完整中文理由；历史文档无诊断字段时只显示模式，不编造。aria 拼 mode + reasonCodeLabel + reason + facts。

**Verification** — `vitest` DocumentsParserReason + chunkDiagnostics **PASS** 5；`tsc` PASS。评审 critical（aria 与断言不一致）已改为 aria 含 reasonCodeLabel。

**Journey log**
1. 可见 UI 与 aria 文案必须同一契约，测试锁定同一函数输出。

## Tasks

- [x] T1: 画像展示理由 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
