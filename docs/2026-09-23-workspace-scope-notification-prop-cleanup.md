# App 通知工具栏冗余回调清理（2026-09-23）

## 变更

`App` 向 `WorkspaceScopeBar` 同时传入 `notificationControl={<NotificationBell ... />}` 和 `onOpenNotifications`。组件在有 `notificationControl` 时直接渲染该节点，只有未提供该节点时才使用 `onOpenNotifications` 生成默认按钮；因此 App 里的回调 prop 不会被读取，是重复且易误导的接线。

本片仅移除 `App.tsx` 的冗余 `onOpenNotifications` prop。保留 `WorkspaceScopeBar` 自己的回调 fallback 与类型，因为它们是组件的独立兼容能力；未改通知权限、摘要加载、Bell 渲染或 Drawer 行为，也不包含 AppLayout 提取等结构性改造。

## 验收

使用现有真实集成路径覆盖：
- `App.notifications.test.tsx`：能力未就绪时 Bell 不展示；authority ready 后展示 Bell；点击 Bell 打开全局 Drawer；通知 detail/handoff 路径保持有效。
- `WorkspaceScopeBar.stage16.test.tsx` 与 `WorkspaceScopeBar.stage18.test.tsx`：workspace selector 的服务端选项和不可用态保持原样。

本次只清理一个调用点，不改公开组件 API，不增加实现镜像测试。

## 后续

不触及其他 App 外壳拆分或通知 source materializer 行为；如要改 WorkspaceScopeBar fallback 语义，应单独审查其所有调用方。

验证结果：`App.notifications.test.tsx` + WorkspaceScopeBar Stage16/18 共 **3 files / 6 tests passed**；`eslint frontend/src/App.tsx` 通过；`npm --prefix frontend run build`（tsc + Vite，7119 modules）通过；`git diff --check` 通过。

独立子 agent review：代码路径 PASS。Reviewer 指出测试说明高估了 detail 覆盖；已把文档收窄为实际验证的 Drawer 挂载与安全 handoff，并明确该测试不覆盖 detail drawer 内部交互。
