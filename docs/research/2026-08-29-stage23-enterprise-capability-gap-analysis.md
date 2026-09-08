# Stage 23 企业能力缺口分析

日期：2026-08-29

## 候选 A：Enterprise Content Recovery Center

当前证据：

- `0011_durable_document_delete` 已具备可靠的物理删除批次和跨存储删除执行链；
- Documents UI 目前直接发起 durable delete；
- Document lifecycle 只有 active/expired/delete_requested/deleting/delete_failed/deleted；
- 没有 recycle、retention、legal hold、restore evidence 或 purge approval authority；
- Dataset 有 archive/restore，但 Document 没有可恢复删除；
- Audit retention 只管理审计数据，不管理知识内容生命周期。

价值：高。它补齐企业知识资产最明显的安全缺口，并能复用现有 durable delete、Approval Center、Audit Event、Notification Center。

风险：需要修改 Document lifecycle 和默认列表/检索可见性，必须严格 fail-closed。

## 候选 B：Enterprise Search & Discovery

当前证据：

- 已有全局知识搜索入口；
- Documents 支持 q 参数、状态和标签筛选；
- Query/RAG、检索调试、最近资产均已存在；
- 缺少 saved search、收藏和跨知识库搜索审计。

价值：中高，但更多是体验增强。若现在实现，容易重复现有 Query/Documents 能力，数据库新增价值不如恢复治理明确。

## 候选 C：Unified Task / Job Operations Center

当前证据：

- 已有 ingest attempts、index operations、source sync、delete operations、audit exports、quality scans、recertification jobs；
- 已有 Monitor 页面和 Notification Center；
- 缺少统一 job envelope 与跨域队列视图。

价值：高，但需要统一大量既有状态机和游标，跨模块耦合最大。适合作为恢复中心之后的 Stage 24/25。

## 对比结论

推荐顺序：

1. Enterprise Content Recovery Center；
2. Unified Task Operations Center；
3. Enterprise Search & Discovery。

## Stage 23 最小企业边界

- 仅覆盖 Document，不扩展 QA、Dataset 或外部对象；
- 新删除行为在 capability ready 时变为 recycle，不再从 Documents UI 直接永久删除；
- 永久清理只创建 approval-gated purge request，本阶段不自动消费 Approval Execution Ticket；
- 现有 durable delete 保留为后续获批 purge executor；
- 回收条目、法律保留、清除请求和事件全部 Tenant-leading；
- 回收后必须 `retrieval_enabled=false`；
- 恢复必须 revision fenced，并恢复原始 retrieval flag；
- 任何 capability/readiness 不确定均不允许回退到永久删除。

## 与 Stage 22 的衔接

Stage 23 暂不新增 Notification source kind，避免同时修改 0032 allow-list。后续可新增：

- retention_expiring；
- purge_approval_pending；
- legal_hold_changed。

Stage 23 UI 可直接安全 handoff 到现有 Approval Center，但不能在 Recovery Center 内批准或执行永久清理。
