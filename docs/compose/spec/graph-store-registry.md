---
feature: graph-store-registry
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: e0bebde..working-tree
---

# Graph Store Factory/Registry

## Report

**What was built** — `core/graph_store_registry.py`：`GraphEngineType`、`GraphStoreFactory`、`GraphStoreRegistry`（单 active）、`NoopGraphStore`（禁用时 healthy/空检索）；`resolve_graph_engine_from_settings`：disabled→none，enabled 默认 milvus；**未知引擎 raise**（不静默 none）。业务装配可继续 `RagGraphStore`；registry 为统一入口，扩展 = factory case。

**Verification** — `pytest test_graph_store_registry + admin_*` **PASS** 19；工厂/注册表/互斥/unknown engine 单测绿。

**Journey log**
1. 未接线引擎必须显式报错，避免运维误以为图谱已开。
2. v1 Registry 保持进程级单 active，不搬向量库热切换复杂度。

## Tasks
- [x] T1: registry/factory/noop + 单测 (covers: S2)

## Workspace
- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
