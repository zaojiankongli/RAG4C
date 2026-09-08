# Stage 24 Unified Task Operations Database Security

日期：2026-08-29

## 推荐五表

```text
tenant_task_projections
tenant_task_operator_actions
tenant_task_events
tenant_task_saved_views
tenant_task_reconciliation_runs
```

### Task Projection

保存当前统一视图：Tenant、source kind/id/revision/digest、Dataset/Workspace scope、category、normalized status、progress、attempt、lease、safe error、route、source currentness 和 projection digest。

唯一身份：

```text
UNIQUE(tenant_id, source_kind, source_id)
```

它不是源任务真账。任何 source revision/digest 不一致都必须标记 stale/unavailable，而不能覆盖源任务。

### Operator Action

动作：retry/cancel/acknowledge。包含 expected source revision、idempotency digest、safe reason、requested/dispatched/applied/rejected/expired 生命周期。只有显式 adapter allow-list 可以执行。

### Task Event

append-only hash chain，记录 projection materialized、status changed、attention acknowledged、action requested/applied/rejected、source stale。

### Saved View

per-account active/archived Saved View，只允许安全 filter schema，不保存 query body、credential 或 URL。

### Reconciliation Run

记录一次 source scan 的开始/完成、各 source counts、created/updated/stale/invalid 数量、source inventory digest 和 safe error。Reconciliation 写统一 projection，但不修改源任务。

## 安全不变量

- 所有 FK Tenant-leading；
- source kinds/route params 严格 allow-list；
- 无 raw task payload、query、result body、note、token、credential；
- action 必须 source current、actor authorized、revision fenced；
- retry/cancel adapter 显式注册；
- reconciliation 单次事务内更新 projection/event；
- Readiness 重算 projection/event digest；
- downgrade 在任一新表非空时阻断；
- preflight 只读，不自动 reconcile 或执行 action。
