---
feature: graph-registry-wiring
status: delivered
updated: 2026-09-20
branch: integration/qa-faq-ops
commits: 3f88abc..working-tree
---

# Graph Registry Wiring（装配点接入）

## Report

**What was built** — 生产图装配改走 registry：`assembly_graph_engine`（pipeline `graph_retrieval_on`/`graph_index_on` → milvus；否则 settings resolve，禁用 → none）+ `create_graph_store(settings, engine_override=…)` 进程缓存。`rag.py` / `server/documents.py` GraphBuilder / `server/app.py` `_get_graph_components` 均调用 `create_graph_store`；**查询图 API 用 `engine_override="milvus_vector_graph"`**，保持接线前 flag 无关的观测语义（flags 关闭时 `/api/graph/*` 仍读 Milvus）。`NoopGraphStore` 补齐 get_*_by_ids / passage / raw upsert / delete 等消费者方法，避免 AttributeError。

**Verification** — `pytest tests/test_graph_store_registry.py` **PASS** 8；`import rag, server.documents` PASS（`server.app` 本机缺 pymysql 为 PRE-EXISTING 环境问题）。

**Journey log**
1. 查询图端点接线前与 pipeline flag 无关；Noop 接管会造成静默空结果回归 → 必须 `engine_override`。
2. 生产 `GraphSettings` 无 `enabled` 字段；装配真相在 pipeline 开关。
3. Protocol/Noop 必须覆盖 GraphRetriever/GraphBuilder/app 实际调用的方法面。
4. 装配与查询可解析到不同 engine，registry 按 engine 缓存即可，不强制全局单 store。

## [S1] Problem

Registry 已建但生产仍硬编码 `RagGraphStore(milvus, graph)`。

## [S2] Design

见 Report；三处装配点 + 查询 API override + Noop 补全。

## [S3] Out of Scope

Neo4j/vgrag 适配器。

## Tasks

- [x] T1: create_graph_store + 三处装配替换 + 测试 (covers: S2)

## Workspace

- 主仓 `D:\program_project\python_project\RAG4C` / `integration/qa-faq-ops`
