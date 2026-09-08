# Stage 22 Enterprise Notification Center 视觉验收

- 验收日期：2026-08-29
- 目标版本：`0032_enterprise_notification_center`
- 验收方式：source-bound、loopback-only、in-memory controlled Playwright
- 最终状态：`passed`

## 受控边界

- 前端：`http://127.0.0.1:5187`
- API：Playwright 对 `http://127.0.0.1:5188` 进行受控响应投影；不连接真实服务。
- 未执行生产 Migration、Notification materialization、Receipt/Subscription 生产 mutation、Alert/Approval mutation 或外部通知投递。
- 外部网络：禁用；未知请求会直接阻断 gate。
- 源文件：运行前后 SHA-256 聚合一致。

## 最终证据

- Matrix：`12/12` 通过。
- Scenarios：`15/15` 通过。
- Fresh PNG：`27/27`。
- Console errors：`0`。
- Page errors：`0`。
- Unknown requests：`0`。
- Request failures：`0`。
- Sensitive leaks：`0`。
- Horizontal overflow：`0` 个场景。
- Gate failures：`[]`。

## Matrix

已覆盖：

- direct / hash；
- light / dark；
- `1440×900`、`375×812`、`280×720`；
- 每个矩阵项均验证 Notification Center、准确未读可访问名称、`99+` 视觉封顶、桌面表格/移动卡片互斥，以及无水平溢出。

## 业务场景

- `baseline`：passed，截图 `scenario-baseline.png`。
- `quality-handoff`：passed，截图 `scenario-quality-handoff.png`。
- `approval-handoff`：passed，截图 `scenario-approval-handoff.png`。
- `mark-read`：passed，截图 `scenario-mark-read.png`。
- `bulk-read`：passed，截图 `scenario-bulk-read.png`。
- `archive`：passed，截图 `scenario-archive.png`。
- `muted-optional`：passed，截图 `scenario-muted-optional.png`。
- `mandatory-critical`：passed，截图 `scenario-mandatory-critical.png`。
- `stale`：passed，截图 `scenario-stale.png`。
- `unavailable`：passed，截图 `scenario-unavailable.png`。
- `partial`：passed，截图 `scenario-partial.png`。
- `empty`：passed，截图 `scenario-empty.png`。
- `read-only`：passed，截图 `scenario-read-only.png`。
- `scope-switch`：passed，截图 `scenario-scope-switch.png`。
- `drawer-keyboard`：passed，截图 `scenario-drawer-keyboard.png`。

覆盖内容包括：Quality/Approval 安全 handoff、单条已读、批量已读、归档、可选通知静音、mandatory critical override、stale、unavailable、partial、empty、read-only、Workspace scope switch，以及 Bell/Drawer 键盘激活、Escape 和焦点返回。

## 可复现命令

```powershell
& 'D:\program_project\python_project\RAG4C\output\playwright\enterprise-notification-center-stage22\run-stage22-acceptance.ps1'
```

```powershell
node 'output/playwright/enterprise-notification-center-stage22/stage22_gate.mjs'
node --test 'output/playwright/enterprise-notification-center-stage22/test_stage22_gate.mjs'
```

## 关键产物

- `output/playwright/enterprise-notification-center-stage22/stage22-browser-result.json`
- `output/playwright/enterprise-notification-center-stage22/stage22-artifact-manifest.json`
- `output/playwright/enterprise-notification-center-stage22/stage22-controlled-fixture.json`
- `output/playwright/enterprise-notification-center-stage22/stage22_acceptance.mjs`
- `output/playwright/enterprise-notification-center-stage22/stage22_gate.mjs`
- `output/playwright/enterprise-notification-center-stage22/test_stage22_gate.mjs`
- `output/playwright/enterprise-notification-center-stage22/run-stage22-acceptance.ps1`

本验收只证明当前源绑定版本在受控 loopback 场景下满足 Stage 22 UI、安全与响应式门禁，不代表执行过任何生产动作。
