# Notification Route Adapter：receipt 路由形状可扩展（2026-09-23）

## 本次改动

将 `core/enterprise_notification_receipts.py::_safe_route` 从两组 `source_kind × target_route_code` 硬编码分支收成 adapter registry：

- 新增 `core/notification_route_adapters.py`，通过现有 `core.providers.ProviderRegistry` 按 `source_kind:route_code` 查找转换策略；未新增第二套注册内核或自动发现。
- Adapter 接收 `NotificationRouteContext`（来源 kind、路由码、租户、原通知行、经 key 检查的 route params），将数据库中的旧/新 params 形状适配为固定、安全的 UI route。
- 原 quality alert 与 approval 两组分支体拆为 `_adapt_quality_alert_route` / `_adapt_approval_route`，调用顺序、租户校验、dataset/source id 对齐、允许键集合、目标 path/query/href 保持。
- 未注册组合只捕获 `UnknownProviderError` 并转为 `NotificationReceiptUnavailable`；malformed route key 使用独立异常分类；adapter 内部其它错误不会被误当成未注册。
- 宿主验证 adapter 结果：必须返回精确 DTO 字段、code 与注册 route code 一致，path 限制在无 `..` 的 `/enterprise/...` 相对路径，query key/value 经过安全校验；忽略 adapter 提供的 `href` 并由宿主 canonicalize，阻止策略误回传外部跳转链接。
- 注册期验证 key 格式、重复项、callable 和单 context 参数签名。
- 没改数据库 CHECK、API/OpenAPI、通知物化、回执 handoff、鉴权或业务状态。

## WeKnora 与本仓边界

参考 WeKnora 的 parser engine registry 能力选择，以及本仓 extensibility standard 中 Adapter 模式的限定：外部形状需要翻译成稳定内部 DTO。这里持久 route JSON → 安全前端 route 是明确的 Adapter，不是把每个字符串都注册化。未知来源/路由组合必须 fail-closed；没有照搬允许未知 parser 回远端的策略。

## 验收证据

- TDD 起步：先写动态 adapter 测试，模块不存在时 import collection 失败；实现后真实 `_safe_route` 调用动态注册项通过。
- `tests/test_notification_route_adapters.py tests/test_notification_receipt_kinds.py tests/test_enterprise_notification_receipts.py`：**20 passed**；覆盖动态路径、重复/错误签名、source-kind 与 route-code 错配拒绝、未知 key 拒绝、旧版内置 route 形状以及 receipt 全行为（87 条既有 datetime deprecation warnings）。
- `ruff check core/notification_route_adapters.py core/enterprise_notification_receipts.py tests/test_notification_route_adapters.py tests/test_notification_receipt_kinds.py`：通过；`git diff --check`：通过。
- 反向验证：暂时将 `_safe_route` 的 adapter dispatch 改为直接拒绝；`test_registered_adapter_is_selected_by_the_real_safe_route_path` 按预期失败（1 failed / 4 deselected），栈在 dispatch 入口；用 `finally` 还原并确认 `source restored: True`。
- `ruff check` 与 `git diff --check` 实测结果写在 progress / 最终回归部分。

## 独立代码审查

Reviewer 首轮指出 adapter 输出在宿主缺少 path/href 安全校验（medium），并指出 malformed key 异常分类与原 fail-closed 类别不一致（nit）。已加 adapter DTO 与路径/query 校验、宿主 href canonicalization 和 invalid-key 类型。复核又发现策略本身抛 `UnknownProviderError` 会被误当成“未注册”；已在 `ProviderRegistry` 增加只做 lookup 的 `get_factory`，宿主先 resolve 并捕获未注册，再在 catch 范围外调用策略，保持策略异常原样传播；新增 `test_adapter_internal_unknown_provider_error_is_not_treated_as_missing_key`。

最终独立 reviewer 对 host path/query/href 约束、invalid-key 分类及 `get_factory` 的异常边界复核：**PASS，无剩余 findings**。复核时 8 条 adapter 用例通过；本地后续合并运行 53 条 provider/route/receipt 用例全绿。

## 后续边界

本片只关闭扩展轴 #8 的 route adapter 分支；前端 `notificationHandoffTarget` 目前也维护 source-kind/path allowlist，因此要引入新的可导航通知类型，仍须同步增加前端导航适配器与测试；单独增加后端 adapter 不会让 UI 自动导航。notification source projection 与 receipt-kind handoff 是已有独立注册点，`_notification_payload` 的类别/数据一致性校验、存储 CHECK 及 notification materializer 仍维持各自契约，不因 adapter 可注册就自动开放数据库值集。
