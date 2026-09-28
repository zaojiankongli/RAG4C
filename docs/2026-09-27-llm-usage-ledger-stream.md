# 流式（SSE）路径接入用量台账（2026-09-27，Round 3）

## 1. 本轮目标

| 项 | 内容 |
|---|---|
| 优化什么 | Round 2 的台账只覆盖了非流式 `/api/query`；**流式（SSE）才是用户主要交互路径**，那边还是零数据 |
| 当前基线 | `rag_stream.answer_query_stream` 完全没有台账（Round 2 §7 遗留项 P1） |
| 验收标准 | ① done 事件带上 `result.usage`，结构与非流式一致；② 生成器被逐段消费时台账不丢；③ 并发多条流不串账；④ 既有流式测试全绿 |
| 涉及模块 | `core/llm_usage.py`、`rag_stream.py`、测试 |
| 本轮不做 | 不改事件语义、不改前端（前端类型 Round 2 已对齐） |

## 2. 关键难点与设计

难点：SSE 的生成器是**被驱动方逐段消费**的，Starlette 会在线程池里推进同步
生成器，**每一段可能在不同的线程/上下文里恢复**。contextvar 的值不会跟着走，
于是直接用 `with usage_scope(): yield from gen` 的结果是：第一段有账、后面全丢。

方案：`iter_with_usage(gen, ledger)` —— 一个薄 wrapper，**每次恢复后重新绑定**
台账再继续推进内层生成器：

```python
token = _USAGE_VAR.set(ledger)      # 首次推进前就挂上（第一段也算）
for event in gen:
    yield event
    _USAGE_VAR.set(ledger)          # 让出后可能换了上下文：恢复即重挂
```

- 内层代码永远紧跟着一次 `set()` 执行 → 无论被哪个线程恢复，看到的是同一个台账；
- 每条流持有一个独立 ledger → 并发不串账（有测试钉住）；
- 台账统一挂在 **done** 事件上，而不是内层 4 处 `QueryResult` 构造点——
  靠"每个构造点都记得挂"必然漏。

对外：`answer_query_stream` 变成薄壳，真正逻辑搬到 `_answer_stream_inner`，
调用方（server / 测试）签名不变。

## 3. 验证结果

- 新增 3 条测试（跨 yield 不丢账 / 并发不串账 / done 事件带 usage），
  台账测试合计 **13 条全绿**；`ruff` 全绿。
- 流式回归：`test_stream_contract.py` **10 passed**、`test_stream_observability.py`
  **29 passed**（两者都是分钟级，本机慢，不是回归）。
- 结构与 `/api/query` 一致：`{calls, cached_calls, failures, prompt_tokens,
  completion_tokens, total_tokens, saved_*, cost, cost_priced,
  unpriced_total_tokens, by_slot[]}`。

## 4. 已知限制与遗留

1. 流式路径的 `saved_*`（缓存省下的 token）依赖槽位缓存开关；默认 TTL 为 0 时恒为 0，属预期。
2. 入库路径（triplet / contextual / classifier）仍没有 `usage_scope` 包裹，
   只有全局指标，没有按任务归拢。→ 下一轮。
3. 本机跑流式相关测试很慢（单文件 1.5~2 分钟），根因仍是外部依赖不可达时的等待，
   与 Round 1 §8 记录的是同一件事。

## 5. 下一步

| # | 事项 | 依据 |
|---|---|---|
| P1 | 入库路径按任务开 `usage_scope`（一篇文档入库花多少 token） | 入库调用量随片段数线性放大，是最容易失控的成本项 |
| P2 | 用台账 + Round 1 的检索评测做成本-质量权衡（HyDE / 子查询 / Stepback 各多花多少、换回多少召回） | 用户目标里的"预算规划与成本权衡" |
| P3 | 修全量测试在外部依赖不可达时挂起（15 分钟只跑 13%） | 反馈循环被拖死 |
