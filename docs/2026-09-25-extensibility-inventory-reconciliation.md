# Extensibility inventory reconciliation handoff（2026-09-25）

## 目的

在完成 Axis #12 condition/action Strategy + Registry 后，重新复读
`docs/compose/spec/backend-extensibility-inventory.md`，把顶部开放轴表与后文已经落地的实现对齐，避免下一位继续被过期状态误导。

## 已对齐

- **Axis #5 approval action_type**：Python 控制面已由 §AK 的有序规则表收口；规则命中可叠加，保持 waiver 与 publish/rollback 的顺序和交叉校验。新增可持久化 action 仍需独立 DB CHECK/migration/API 合同变更。
- **Axis #9 gate_reason → alert_type**：已由 §AB 的有序 matcher/规则表收口；现有 `alert_type` 存储词汇没有扩展。
- **Axis #12 trigger/condition/action**：已由 §BR 收口；runtime-only strategy 不等价于可持久化新 code。

## 保持开放且刻意不实现

Axis #3 已完成 handler、revision、attempt、target producer、durable-delete、dead-letter、reader、document/report observation fence 与 repair adapter，但以下仍缺权威契约：

1. Graph comparator；
2. Catalog-deleted target enumeration；
3. 可验证的 Catalog mutation generation / target snapshot authority；
4. target-specific atomic repair。

当前 `core/graph_store.py` 的 Graph entity/relation 结构没有可靠的 dataset-scoped snapshot 维度。不能为了“扩展性”把租户字段或当前 Catalog identity 猜作 Graph authority，也不能把 chunk reader/repair registry 伪装成完整对账能力。

## 本片范围

只更新 inventory 顶部状态、§BS 和 planning progress；没有修改业务代码、数据库 schema、migration、API/OpenAPI、授权或前端。下一位只有在 Catalog/target authority 契约明确后，才能继续 Axis #3 剩余边界。

## 验证

- `git diff --check`：通过。
- 通过现有 Axis #3/#5/#9/#12 handoff、plan、实现与测试路径交叉复读确认；未把“已完成 Python 层”误写成“允许新增持久化词汇”。
