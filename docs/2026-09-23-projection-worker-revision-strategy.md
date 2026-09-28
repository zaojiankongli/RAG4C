# Projection Worker Revision Strategy（2026-09-23）

## 切片范围

把 `IndexOperationWorker._advance_projection_revision` 按 target store 的 hard-coded `if/elif` 改为显式 `ProjectionRevisionStrategy` 注册。此次只覆盖 revision advancement，不宣称整个 projection store 生命周期已可插拔。

## 实现

- 新增 `core/projection_revision_strategies.py`，复用核心 `ProviderRegistry`；只接受精确规范 target key（不对存储值 trim/lowercase），并在注册时校验 callable 与单个 positional context。
- 将旧 Milvus `desired_index_revision → indexed_revision` 与 graph `content_revision → graph_revision` 的相等栅栏原样移入内置策略。
- Worker 对非 durable-delete operation 在调用 handler **之前**解析 target revision strategy；未注册或非规范大小写 key 会走持久 retry 错误边界，handler 不产生副作用。
- 解析出的 callback pin 在本次 claim 的 `IndexOperation` 实例上；handler 执行过程中即使 registry 被替换，`_advance_projection_revision` 仍复用 pinned callback，避免同一个副作用任务用新 lifecycle policy 提交。
- revision strategy 必须同步。注册时拒绝 coroutine/async-generator/generator function 及相应 callable object；同步函数若误返回 awaitable/generator，worker 关闭/取消并进入失败/重试，不会误标成功。
- 不继承原 `target_store.startswith("graph_")` 的隐式接纳行为；Worker 现在只接受显式注册的精确 target。当前唯一生产 target `graph_projection` 已内置注册；其它 `graph_*` 均在 handler 之前 fail/retry，以免未知目标越过 revision policy。
- 完成 handler 后 `_advance_projection_revision` 使用 pinned strategy 推进对应版本。`finalize_document_delete` 与 durable delete 完成路径不调用 revision strategy，保持旧语义。

## 测试与验证

新增 `tests/test_index_worker.py`：
- 动态注册 `custom_projection` revision strategy，通过真实 `IndexOperationWorker.run_once`，证明新 target 能推进 revision 并完成操作；
- 未注册及大小写变体 target policy 均必须在 handler 之前 fail/retry，并断言 handler 调用数为 0；
- registry 被 handler 替换时，本次 operation 使用预先 pin 的原 callback；
- 注册拒绝重复、非 callable、零参数 callable、coroutine、async-generator 与 generator callable；运行时拒绝同步函数误返回 generator。
- target store key 严格大小写匹配。旧 Worker 的 `startswith("graph_")` 隐式接受未知图目标已改为显式注册；当前 producer 仅声明 `graph_projection`，新图 store 需显式注册 revision strategy。
- `test_builtin_graph_revision_strategy_keeps_content_revision_fence` 覆盖内置 graph success path、graph_revision 推进及不误推进 indexed_revision；大小写变体与未知 graph-prefix 在 handler 前拒绝也有独立用例。

TDD 起步时自定义 store 经 Worker 成功推进了 revision、missing policy 也照常运行 handler 并成功；加入 registry + worker preflight 后，定向三测变绿。最终复验 `tests/test_index_worker.py + tests/test_projection_handlers.py + tests/test_document_delete_worker_v2.py`：**49 passed**（1156 条既有 datetime.utcnow 警告）；相关 Ruff 与 `git diff --check` 通过。反向验证临时禁用 Worker revision advancement 后，动态注册真实路径测试因 `indexed_revision` 仍为 0 而按预期失败；`indexing/index_worker.py` 已逐字节恢复。

## 独立代码审查

Reviewer 确认 pinning、unknown preflight、durable delete/finalize 排除及 attempt/stale/retry 行为；提出的 async callable、async-generator/generator callable 和 `graph_*` 隐式前缀问题均已处理。最终复核 **PASS**，针对注册拒绝、generator 返回、pinned strategy、graph revision 与未知 graph-prefix 的用例 5 passed；49 条相关回归另由本轮实跑通过。非阻塞注意：若同步 callback 返回一个已启动的 async-generator，当前同步 worker 能拒绝它，但无法 await 其 `aclose()`；operation 不会误标成功。

## 本片边界 / 后续

本片只收口 revision advancement。新 target store 仍须单独接入：
1. primary/secondary attempt readiness semantics（`_advance_attempt_lifecycle` 仍认识 milvus 与 graph）；
2. state machine 生产端的 store 选择；
3. durable deletion store list、删除处理器与 operation lifecycle；
4. consistency/readiness API 的目标投影事实。

增加 revision callback **不自动**代表新 store 已端到端支持 ingestion/delete/readiness。下一片须继续扩展轴 #3，而不是把本记录读成完成声明。
