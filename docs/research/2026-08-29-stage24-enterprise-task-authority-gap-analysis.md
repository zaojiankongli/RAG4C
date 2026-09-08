# Stage 24 Enterprise Task Authority Gap Analysis

日期：2026-08-29

## 已有源任务真账

- `document_ingest_attempts`
- `index_operations`
- `source_sync_runs`
- `document_delete_batches` / `document_delete_operations`
- `tenant_audit_export_jobs`
- `tenant_release_quality_scan_runs`
- `dataset_release_recertification_jobs`
- Run Registry / Redis queue metrics

## 当前缺口

- 状态命名不统一；
- 任务游标和筛选分散；
- safe error、attempt、lease 和 retry/cancel 规则不同；
- Monitor 关注运行查询，不覆盖全部知识运营后台任务；
- Documents、Sources、Compliance、Quality 页面各自展示局部任务；
- 没有统一的 Tenant 级任务搜索、Attention Board、详情抽屉和 Saved View；
- 没有 source revision/digest fence 来证明统一视图仍然对应当前源任务；
- 跨域 action 不能通过一个无约束的通用按钮执行。

## 决策

Stage 24 创建统一 Task Projection authority，但不替换任何源任务表。源表继续是业务状态机真账；Task Center 只保存可验证投影、统一事件、操作者 action request 和 reconciliation 证据。

首期 source kinds：

```text
document_ingest
index_operation
source_sync
document_delete
audit_export
release_quality_scan
release_recertification
```

首期动作：

- safe source handoff；
- retry request；
- cancel request；
- acknowledge attention；
- 只有显式 adapter 支持的 source kind 才能执行 retry/cancel；
- 不允许通用反射调用任意源服务。

## 不纳入

- 交互式 RAG query run；
- Redis 原始 payload；
- 自动执行所有 retry/cancel；
- 替换现有任务状态机；
- 外部通知投递。
