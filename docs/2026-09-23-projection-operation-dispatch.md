# Projection Operation Dispatch：fail-closed 基线（2026-09-23）

## 本次范围

为投影写入 handler 增加持久化 `operation` 值的入口校验。这里只收紧未知操作的错误边界，不把 `target_store × operation` 全轴改造成插件注册表；后者仍需独立设计，因为它跨 handler、revision、attempt、投影栅栏和删除生命周期。

## 为什么要做

独立审查发现 `ProjectionHandlers.handle_milvus` / `handle_graph` 只特殊处理 `delete_document`，其它值会进入重建/投影路径。`IndexOperation.operation` 是可持久化字符串，目前没有 DB CHECK。未知或拼错值因而可能触发 embedding、图预处理和投影写入，而非在入口失败。

WeKnora 本机参考：
- `C:\Users\饶策\Desktop\me\WeKnora-0.8.0\internal\application\service\retriever\factory.go`：将实例构造和使用路径分开，并区分可分类的构造错误。
- `...\internal\infrastructure\docparser\engine_registry.go`：按能力注册本地策略并合并远端发现能力。

本次只借鉴“边界先判明、错误可分类、扩展点集中可读”的设计意图。未照搬 WeKnora 未知引擎回退远端的宽松策略；RAG4C 投影写路径必须 fail closed。

## 实现

- 新增 `UnsupportedProjectionOperation(ValueError)`，让调用方可以分类处理无效持久化动作。
- `ProjectionHandlers._require_supported_operation` 在 Milvus/graph handler 的第一步检查值，保证未知值在读取 chunk、embedding、查询/写入外部存储前被拒绝。
- 当前允许值来自实际生产与既有用例：`upsert`、`reconcile`、`delete`、`delete_document`。其中 `reconcile` 由 `indexing/reconciler.py` 生产；复跑全文件时发现该值之前未被独立审查结论列全，故补入白名单后重新跑完回归。
- 未修改数据库 CHECK、OpenAPI、删除语义、任务重试语义或 desired/indexed revision fence。

## 测试与验收

新增 `tests/test_projection_handlers.py::test_unknown_projection_operation_fails_before_external_side_effects`，分别覆盖 Milvus 与 graph 两个 handler：将持久行和传入 operation 同步设为未知值，断言分类异常且 embedder、Milvus、graph builder 均无调用。

实跑：
- TDD 初始红：有效值集遗漏 `reconcile`，投影 handler 文件中 8 个既有测试失败。经核对 `indexing/reconciler.py` 后补入有效值，再跑全文件。
- `.venv/Scripts/python.exe -m pytest tests/test_projection_handlers.py -q`：**14 passed**（416 个既有 `utcnow` 弃用警告）。
- 反向验证：临时移除两个入口检查，目标测试 **2 failed**（均为 `DID NOT RAISE`）；用 `finally` 还原文件并字节确认 `source restored: True`。

## Code review

第一次子 agent 对当前代码只读审查指出应 fail closed，但初步列出的有效值不完整。全量回归及时发现并修正了 `reconcile`。代码改动后的独立 review 结果待追加；review 期间如有修复，必须重跑相关测试。

## 后续交接

这不是 `backend-extensibility-inventory.md` 中轴 #3 的“已完成”状态。后续若要注册新的投影目标/操作，必须分别核对：
1. 投影 handler 行为派发；
2. worker revision/attempt 完成推进；
3. state machine 的生产路径；
4. 删除计划与 durable delete；
5. tenant/generation/revision fence 与 stale retry。

推荐下一切片先做扩展注册设计和真实 worker 路径测试，再考虑新增目标；不得仅因本地 handler 接受新目标就声称端到端可扩展。投影栅栏与 MySQL 权威数据边界始终优先。

## 独立复核结论

第二轮子 agent 对第一片当前实现复核：**PASS**。未发现 blocker/high/medium/nit；确认 `reconcile`、worker 异常边界、租户/revision fence、删除语义、持久值测试和文件格式均无问题。后续注册策略切片的 reviewer 发现并帮助收口“策略调用形状须注册期校验”，修复和验证详见 `docs/2026-09-23-projection-operation-registry.md`。


## 后续注册化切片

在 fail-closed 边界上，投影 operation 入口已进一步收成 `core.providers.ProviderRegistry` 策略派发；详见 `docs/2026-09-23-projection-operation-registry.md`。本文件保留第一片的行为边界记录。策略注册形状与 ValueError 分类是独立 review 后的修正，也记在注册化交接文档中。
