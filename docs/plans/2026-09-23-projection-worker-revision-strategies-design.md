# Projection Worker Revision Strategy 设计（2026-09-23）

## 现状证据

投影 handler 已按 `target_store:operation` 使用策略注册，但 `IndexOperationWorker._advance_projection_revision` 仍按 `milvus_chunks` / `graph_*` 硬编码目标字段。新增目标若忘记在 Worker 登记 revision 写入，会在 handler 副作用后才发现无法推进版本；缺失的目标行为也没有注册期/副作用前失败边界。

## 本切片

只收口 worker 的投影 revision advancement：新增 `core.projection_revision_strategies`，复用唯一 `core.providers.ProviderRegistry`，以精确 `target_store` 注册一个版本推进策略；数据库的 target key 不做 lowercase/casefold alias。把当前 Milvus 与 graph SQL 逻辑原样搬成内置策略。Worker 在调用 handler 前解析策略；未知 target 在任何投影 handler 副作用前进入持久重试边界；成功后用已注册策略推进对应 revision。

本片**不**把 ingest-attempt role、生产端 target 集、durable delete stores 或 consistency summary 一并改写；那些是 #3 后续分片，注册 revision callback 不等于目标存储已端到端集成。`delete_document` / `finalize_document_delete` 仍走 durable-delete 自己的完成路径，不需要 revision advancement strategy。

## 不变量

- revision 写入仍受 `desired_index_revision` / `content_revision` 相等栅栏控制。
- Handler 仍必须先由 `_assert_current` 校验租户、claim owner、generation 与 desired revision。
- revision strategy 注册必须拒绝重复和形状不合法的 callback。
- 未注册 store 的 callback 必须在 handler 之前发现，不能先产生 Milvus/Graph 副作用再重试。

## 验收

- TDD 新增 Worker 真实路径用例：动态注册 target revision strategy，运行 durable worker，将自定义 target 的 revision 正确推进且操作成功。
- 未注册 target callback 的 Worker 用例确认 handler callback 次数为 0、操作进入 retry。
- 已有 Milvus/graph worker 与投影 fence/delete 用例保持行为等价。
- 反向验证移除 Worker 对注册策略的调用后，动态 target 用例必须失败。
- 独立子 agent review、定向 pytest、ruff、diff check 通过，并新增 implementation handoff 文档。
