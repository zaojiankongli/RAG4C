# Projection Operation Strategy Registry（2026-09-23）

## 本次切片

在上一片 fail-closed 基线之上，把投影 handler 的 `target_store × operation` 入口改为基于本仓唯一注册内核 `core.providers.ProviderRegistry` 的策略注册。

- `core.providers.ProviderRegistry` 是唯一注册内核，没有新增第二套插件发现机制。
- 注册 key 为 `target_store:operation`，内置 Milvus/graph 各注册 `upsert`、`reconcile`、`delete`、`delete_document`。
- `ProjectionHandlers.handle_milvus` 和 `handle_graph` 只负责构造上下文并派发；原有 revision fence、租户校验、锁、embedding、Milvus/graph 写入逻辑仍在原私有方法中。
- 外部实现可以通过 `register_projection_operation(target_store, operation, strategy)` 增加策略，无需编辑 handler；`unregister_projection_operation` 用于测试/生命周期清理。
- 未知 key 仍抛 `UnsupportedProjectionOperation`，不触碰下游副作用。

## WeKnora 借鉴边界

WeKnora 的 `internal/application/service/retriever/factory.go` 将构造/选择边界集中在 factory，并区分 not-found/unavailable/forbidden 等错误；`internal/infrastructure/docparser/engine_registry.go` 按能力注册 engine。RAG4C 本片只吸收“集中选择、可分类错误、真实能力注册”的思想，不照搬其远端未知 engine 宽松回退；投影未知操作必须 fail-closed。

## 设计取舍

本片不是把所有投影生命周期一次性重写：`index_worker.py` 的 revision/attempt 推进、`state_machine.py` 的生产、`core/document_deletion.py` 的删除编排仍是独立边界。这样先让 handler 的选择轴可扩展，再为下一片真实 worker 路径扩展目标做准备，避免新增 handler 后遗漏栅栏。

## 验收

- 先写动态注册测试，初始红：`register_projection_operation` 尚不存在。
- 新增测试注册 `milvus_chunks:custom_probe`，由 `IndexOperationWorker.run_once` 领取持久 operation，经 `handle_milvus` 派发到动态策略，完成真实 Milvus projection 并由 worker 成功完成任务；测试 finally 注销。
- `tests/test_provider_registry_extensibility.py tests/test_projection_handlers.py`：**50 passed**，覆盖 provider 旧契约和投影新路径（warnings 均为既有 `utcnow` 弃用）。
- `.venv/Scripts/python.exe -m ruff check core/providers.py indexing/projection_handlers.py tests/test_projection_handlers.py`：通过。
- `git diff --check`：通过。
- 前一片反向验证仍有效：临时移除入口校验会使未知操作测试红；本片没有放宽未知值。

## 交接与后续

- 扩展新操作时：先注册策略，再补真实路径测试；不要直接在 `handle_*` 中添加新的 `if operation == ...`。
- 扩展新投影目标前，必须同步设计 worker revision/attempt 完成、生产端、删除路径、租户/generation/revision fence；仅注册 handler 不代表端到端完成。
- 复核中发现策略内部 `ValueError` 会被误报为“未知 key”；已把注册表未命中异常类型化为 `UnknownProviderError(ValueError)`，投影入口仅捕获该子类。已有外层 `ValueError` 兼容性保持，策略错误原样抛出。新增回归见 `test_registered_strategy_value_error_is_not_reported_as_unknown_operation`。
- 独立 review 首轮发现注册时只判断 callable，没有验证它能接收一个上下文参数（medium）。已在 `register_projection_operation` 用 `inspect.signature(strategy).bind(object())` 注册期判形状，零参数 lambda 现在注册即 `TypeError`；新增常驻断言。
- 独立 reviewer 对该修复复核结论 **PASS**，确认错误在注册期被拒绝。修复后重跑 `test_provider_registry_extensibility.py + test_projection_handlers.py`：50 passed；ruff 和 `git diff --check` 均通过。
