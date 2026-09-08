# Stage 23 企业内容恢复数据库与安全设计研究

日期：2026-08-29

## 现有能力复用

### 0011 Durable Delete

复用：

- `document_delete_batches`；
- `document_delete_operations`；
- generation fence；
- projection/store deletion；
- idempotency；
- 批量执行结果。

Stage 23 不复制物理删除执行器。回收条目达到保留期、无 legal hold 且获得审批后，未来 executor 才能把 Document 转入现有 delete_requested 链路。

### 0025 Approval Control

复用：

- Tenant policy/request/decision/execution ticket；
- approver account/role/group；
- revision 和 snapshot digest；
- idempotency 和审计。

Stage 23 新增 `document_purge` action type，但 Recovery Center 只创建/关联 Approval Request，不消费 execution ticket。

### 0023 Audit Compliance

Audit retention 只管理 Tenant Audit Event 和 export，不应被误用为知识内容保留策略。Stage 23 需要独立 content retention policy。

## 推荐五表

```text
tenant_content_retention_policies
tenant_document_recycle_entries
tenant_document_legal_holds
tenant_document_purge_requests
tenant_document_recovery_events
```

### Retention Policy

Tenant 单例，包含：

- retention_days；
- purge_requires_approval；
- auto_purge_enabled；
- status；
- revision；
- updated_by/updated_at。

### Recycle Entry

核心身份：

```text
tenant_id + dataset_id + document_id + recycle_generation
```

包含：

- active_recycle_key；
- status：recycled/restoring/restored/purge_requested/purged/failed；
- original_retrieval_enabled；
- document_revision/generation fence；
- recycled_at、purge_eligible_at；
- recycled_by、restore/purge facts；
- safe snapshot digest；
- 不复制文档正文、切片、query、credential 或原始 metadata。

### Legal Hold

- Tenant/Dataset/Document/Recycle Entry composite FK；
- active/released；
- reason_code + safe reason；
- revision；
- hold/release actor/time；
- canonical active key，防止重复 active hold。

### Purge Request

- entry identity + expected revision；
- pending_approval/approved/cancelled/executed/expired；
- approval_request_id；
- request digest、idempotency key digest；
- retention/legal-hold snapshot；
- 禁止客户端提交 execution ticket。

### Recovery Event

- immutable append-only；
- sequence + previous_event_digest + event_digest；
- event types：recycled/restored/hold_applied/hold_released/purge_requested/purge_approved/purge_cancelled；
- canonical safe snapshot；
- DB insert guard验证前序链与 Entry 存在；
- Readiness 重新计算 canonical digest。

## Document lifecycle

Document 增加 `recycled`：

```text
active/expired -> recycled
recycled -> active
recycled -> delete_requested  # 仅未来批准后的 purge executor
```

约束：

- recycled 时 `retrieval_enabled=false`；
- active restore 时恢复 Entry 记录的原始 retrieval flag；
- recycled 文档默认不出现在 Document Catalog、检索和最近资产；
- restore 必须确认 Dataset、Workspace 和 ACL 当前仍有效。

## 安全不变量

- 所有 FK 以 tenant_id 开头；
- 所有 mutation actor-only；
- restore/recycle/hold/purge request 均 revision fenced；
- legal hold 和 retention 未满足时 purge fail-closed；
- no Receipt/Entry/Event 一致性问题；
- unsafe text/URL/token/ticket 不进入 safe snapshots；
- SQLite offline migration fail-closed；MySQL/PostgreSQL 提供离线 DDL；
- downgrade 在任一新表非空或存在 recycled Document 时阻断；
- preflight 只读，不自动回收、恢复、清除或创建审批。

## 结论

Stage 23 应建立“可恢复删除权威”，而不是重新实现物理删除。现有 0011 是最终 purge executor，0033 是它前面的企业治理层。
