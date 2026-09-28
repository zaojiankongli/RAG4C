# Notification Receipt Route Adapter 设计（2026-09-23）

## 证据与范围

`docs/compose/spec/backend-extensibility-inventory.md` 的通知开放轴 #8 已收 source 投影与 receipt handoff；`core/enterprise_notification_receipts.py::_safe_route` 仍对 `(source_kind, target_route_code)` 两对硬编码，并将持久化 route params 转成安全 UI route。该逻辑符合 Adapter 定义：外部形状需翻译为内部稳定路由。

本切片只提取 `_safe_route` 两个转换器，不改通知物化、数据库 CHECK、receipt mutation、授权和 handoff。

## 设计

- 新增 `core/notification_route_adapters.py`，基于唯一注册内核 `core.providers.ProviderRegistry` 按 `source_kind:route_code` 分派。
- Adapter 接收冻结上下文（source kind、route code、tenant id、notification row、经键检查的原始 params），返回 `(safe_params, safe_route)`。
- 注册期拒绝重复 key、格式非法、非 callable 及无法接收一个 context 的函数；未知组合保持 fail-closed。
- 内置 quality-alert / approval 转换逻辑搬入两个 Adapter 函数，不改变字段白名单、legacy route param 兼容、租户/dataset/source ID 关联校验或固定 route path。
- 保留 `_safe_route` 对 route param mapping 与 string key 的共同预检；Adapter 只处理已通过外壳检查的数据。

## 不采用

- 不把 route adapter 混入 `NotificationSourceKindSpec`：来源规格描述数据投影形状，安全导航转换是独立行为轴。
- 不使用未知 route 默认值或泛化 `/enterprise/<value>` 路径。
- 不放宽存储 CHECK 或公开 API；进程内 adapter 注册不代表值能持久化。

## 验收

新增测试覆盖动态注册真实 `_safe_route` 路径、未知/错配拒绝、注册期拒绝重复/错误签名，以及内置旧参数形状。所有 receipt 测试保持通过。暂时禁用 dispatch 后动态路由测试应红，并在 finally 后还原生产源文件。新增 UI 路由需同时扩展前端 `notificationHandoffTarget` allowlist，不把后端注册误当作端到端新增完成。
