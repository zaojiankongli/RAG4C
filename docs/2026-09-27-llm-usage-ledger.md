# 单次问答 LLM 用量台账（2026-09-27，Round 2）

## 1. 本轮目标

| 项 | 内容 |
|---|---|
| 优化什么 | AI 调用侧的**可测量性**：单次问答花了多少次调用、多少 token、缓存省了多少、大概多少钱 |
| 当前基线 | 只有**进程累计计数**：`llm.tokens.{prompt,completion,total}`、`llm.calls`、`llm.cache.saved.*`（按 model + stream 打标签）。能回答"这一小时烧了多少"，回答不了"**这一问**花了多少"；`QueryResult` 里没有任何用量字段 |
| 验收标准 | ① 一次问答能在 `QueryResult.usage` 拿到台账（按槽位拆分 + 缓存省下的 token）；② 价格表可配置，没配就不编造金额；③ 不开台账时零影响；④ 离线测试常驻；⑤ 真实端点端到端验证 |
| 涉及模块 | 新增 `core/llm_usage.py`；改 `core/llm.py`、`rag.py`、`models/schemas.py`、`config/settings.py`，以及 15 处 `create_client` 调用点（补槽位名） |
| 本轮不做 | 不改任何调用/缓存/重试逻辑；不做计费或限流；流式（SSE）路径**未接**（见 §7） |

## 2. 设计

- **旁路 + contextvar**：台账经 `usage_scope()` 绑定在当前上下文，`record_call / record_cache_hit / record_failure` 一律包 try，异常全吞——埋点绝不能把一次成功的回答变成 500。
- **按（模型 × 槽位）记账**：多数部署里好几个槽位共用同一个模型（本仓 `.env` 就全是 `qwen3.7-max`），只按 model 打标签分不出 token 是谁烧的。所以给 `create_client(..., slot="generation")` 补了槽位名。
- **缓存省下的 token 是实测值**：`core/llm_cache` 早就真调那一次的用量存进缓存信封了，台账直接读，不用"命中率 × 拍脑袋平均值"。
- **价格表外置**：`llm.price_table`，形状 `{模型: {"prompt": 每 1K 单价, "completion": 每 1K 单价}}`，支持 `"*"` 兜底。**本仓不内置任何价格**——价格会变，写死就是在骗人；没配价的模型金额记 0，token 如实记进 `unpriced_total_tokens`。
- **挂载点只有一个**：`rag._attach_usage()`。`_answer_sequential` 里有 5 处 `QueryResult` 构造点，靠人记住每处都挂台账不现实。
- **顺带进全局指标**：`query.tokens.total` / `query.llm.calls` / `query.llm.failures`，于是"最近是不是变贵了"这类分布问题也有数。

## 3. 执行内容

新增：
- `core/llm_usage.py`（`UsageEntry` / `UsageLedger` / `usage_scope` / 三个旁路写入函数）
- `tests/test_llm_usage_ledger.py`（10 条离线测试）

改动：
- `core/llm.py`：`LLMClient.slot`、`create_client(..., slot=)`；`_record_usage` / `_record_cache_hit` 往台账同步；`chat()` 失败路径记 `record_failure`
- `rag.py`：`answer_query` 开 `usage_scope()`（图编排与顺序编排共用），出口统一 `_attach_usage()`；4 处 `create_client` 补槽位名
- `models/schemas.py`：`QueryResult.usage: dict`（默认空字典，老调用方零影响）
- `config/settings.py`：`LlmSlotsSettings.price_table`
- `frontend/src/types/rag.ts`：`QueryUsage` / `QueryUsageEntry` 类型（tsc 通过）
- 其余 11 处 `create_client` 调用点补槽位名（generation / judge / hyde / subqueries / stepback / metadata_filter / classifier / contextual / triplet）

## 4. 验证结果

- 离线测试 **10 条全绿**；LLM 相关回归（熔断 / provider 注册表 / generation 缓存 / 候选系数 / 检索评测）**77 passed**；`ruff` 全绿；前端 `tsc --noEmit` 通过。
- 真实端到端（DashScope `qwen3.7-max-2026-05-17`，一句话问答）：

```json
{"calls": 1, "prompt_tokens": 18, "completion_tokens": 424, "total_tokens": 442,
 "saved_total_tokens": 0, "cost_priced": true, "unpriced_total_tokens": 0,
 "by_slot": [{"model": "qwen3.7-max-2026-05-17", "slot": "generation", "calls": 1, ...}]}
```
  单次生成真实延迟 **7.3s**（与 `docs/性能实测与升级计划.md` 里 generate 6-9.5s 的量级一致）。
  注意：上面示例里的 `cost` 用的是**随手填的演示价**，不是真实价格——真实金额要配了 `price_table` 才有意义。

## 5. 成本与风险

- 成本：台账本身零额外 API 调用；只是把已有埋点的数再归拢一份。开销是每请求一个 dict。
- 风险：全部写入路径 fail-safe（异常吞掉、`usage` 退化为空字典）；不传 `slot` 也能正常工作；不开 `usage_scope()` 时 `current_ledger()` 为 None，写入静默跳过。
- 回滚：`git checkout -- core/llm.py rag.py models/schemas.py config/settings.py`；删除 `core/llm_usage.py` 与新测试。`QueryResult.usage` 默认空字典，即使代码留着、不开 scope 也等同没有。

## 6. 怎么用

```bash
# .env：给用到的模型配价（每 1K token 单价，货币单位自定）
RAG4C_LLM_PRICE_TABLE='{"qwen3.7-max-2026-05-17": {"prompt": 0.02, "completion": 0.06}}'
```

```python
result = answer_query("公司的报销流程是什么？")
print(result.usage["total_tokens"], result.usage["cost"], result.usage["by_slot"])
# /api/query 响应与 /api/query/stream 的 done 事件都会带上 usage 字段
```

## 7. 已知限制与遗留

1. **SSE 流式路径未接台账**：`rag_stream.answer_query_stream` 是生成器，contextvar 在生成器里会外溢到调用方上下文，并发流有串账风险。要接得先把生成器上下文理清（或改成显式传 ledger），本轮不冒险。→ 下一轮 P1。
2. `openapi.json` / `openapi.ts` 未重新生成：`QueryResult` 不在 OpenAPI 的 response_model 里（响应是手拼 dict），且重新生成会把**上一轮未提交改动**的字段一起混进去，故保持原样。前端类型已在 `types/rag.ts` 手工对齐。
3. 入库路径（triplet / contextual / classifier）也开了槽位名，但**没有 `usage_scope` 包着**，所以入库阶段的用量还没按任务归拢——只有全局指标。→ 下一轮 P2。
4. 价格表只做估算，不参与任何限流/预算硬门禁。

## 8. 下一步

| # | 事项 | 依据 |
|---|---|---|
| P1 | SSE 流式路径接台账（先解决生成器上下文隔离） | 流式是主用交互路径，现在没数据 |
| P2 | 入库路径按任务开 `usage_scope`（统计一篇文档入库花多少） | 入库调用量随片段数线性放大，是最容易失控的成本项 |
| P3 | 用台账数据回答"开 HyDE / 子查询 / Stepback 各多花多少 token、换来多少召回"，配 Round 1 的检索评测一起做成本-质量权衡 | 用户目标里的"预算规划与成本权衡" |
| P4 | 修全量测试在外部依赖不可达时挂起的问题（Round 1 §8） | 反馈循环被拖死 |
