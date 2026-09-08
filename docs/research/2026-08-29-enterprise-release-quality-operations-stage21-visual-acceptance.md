# Stage 21 Quality Operations Visual Acceptance

- **验收日期**：2026-08-29（Asia/Shanghai）
- **目标 revision**：`0031_enterprise_release_quality_operations`
- **down revision**：`0030_enterprise_release_quality_certification`
- **运行 ID**：`20260829T041747140Z`
- **captured_at**：`2026-08-29T04:19:47.912Z`
- **结果**：`passed`

## 1. 验收边界

本验收只验证当前仓库中的 Stage 21 前端 UI 与受控 loopback contract，不执行真实生产动作。

明确禁止并确认未执行：

- 真实 production API、数据库连接、migration/backfill；
- 真实 Scan、Observation 写入、Alert 持久化；
- 真实 Certification、Waiver、Approval、Promotion、Rollback、Pin；
- 真实 source sync、ingestion、restore、delete；
- 外部网络访问。

所有质量运营响应均由内存 controlled fixture 提供，所有服务仅绑定 loopback：

```text
Frontend Vite:  http://127.0.0.1:5187
API fixture:    http://127.0.0.1:5188
```

API loopback 同时提供受控 managed harness 的静态页面和 Vite module proxy；WebSocket 仅用于受控 Vite HMR handshake，不连接外部服务。

## 2. 工具与浏览器

验收使用真实 `playwright-core` Chromium，不使用 jsdom、截图伪造或 Playwright test runner 替代真实浏览器。

```text
npx:             11.17.0
playwright-core: 1.62.1
Chromium:        C:\Users\饶策\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe
```

执行入口：

```text
output/playwright/enterprise-release-quality-operations-stage21/stage21_acceptance.mjs
```

脚本启动受控 API loopback；前端 Vite loopback 使用当前已运行的开发服务器。可通过以下 PowerShell 入口执行：

```powershell
.\output\playwright\enterprise-release-quality-operations-stage21\run-stage21-acceptance.ps1
```

## 3. 矩阵与场景

基础矩阵共 **12 个真实页面运行**，每个运行均生成一张 fresh PNG：

```text
direct/hash × light/dark × 1440/375/280
```

对应 viewport：

```text
1440 × 900
375  × 812
280  × 720
```

每个基础矩阵运行都验证：

- Knowledge Base deep-link scope 验证；
- Releases resource navigation；
- `Release Center | Quality Operations` 工作区切换；
- workspace Tabs 的键盘 `ArrowRight` 切换与 focus；
- `SLO Horizon Rail`；
- 桌面 `PrimaryTable` 与移动端单列 cards；
- 质量运营 authority 行及详情入口；
- Drawer focus、五个 Tab、键盘激活、Escape、focus return；
- 无水平溢出与敏感 sentinel 渗漏。

额外场景共 **13 个**：

```text
baseline
healthy
24h-critical
7d-warning
expired
unavailable
alert-open
alert-ack
recert-pending
recert-ready
recert-failed
read-only
drawer-five-tabs
```

场景覆盖事实：

- `healthy`：Healthy horizon；
- `24h-critical`：24 hours / Critical；
- `7d-warning`：7 days / Warning；
- `expired`：Expired / Critical；
- `unavailable`：Quality authority unavailable；
- `alert-open` / `alert-ack`：Alert Inbox 的 `open` 与 `acknowledged`；
- `recert-pending` / `recert-ready` / `recert-failed`：Recertification Job 三种非终态/失败投影；
- `read-only`：mutation controls 不显示、mutation request 数为 0；
- 其他业务场景通过同一 Stage 21 `QualityOperationsCenter` 的 controlled managed harness 验证 `Governed` 与对应 action controls 可用；
- `drawer-five-tabs`：五个详情 Tab 和 Drawer 键盘生命周期。

## 4. Tencent/TDesign 取向的交互验收

验收复用了腾讯知识库中“持久任务、状态显式、报告/汇总分层、后台运营记录集中”的信息架构，并以当前 TDesign React UI 为事实来源。

### 4.1 SLO Horizon Rail

Rail 的六个状态入口均由真实页面 DOM 读取：

```text
EXPIRED | 24 HOURS | 7 DAYS | 30 DAYS | HEALTHY | UNAVAILABLE
```

baseline fixture 同时投影了五个有意义 horizon（30 days 为 0），避免以单一演示卡片伪造状态覆盖。

### 4.2 Drawer

每个基础矩阵页面都从真实 authority 行打开一个 Drawer，并检查：

```text
Overview | Timeline | Certification | Alert | Recertification
```

检查内容：

- Drawer 打开后将焦点送入真实 Drawer body；
- `Tab` 键后焦点仍在 Drawer root 内；
- 通过键盘 `Enter` 激活五个 Tab，并逐一检查 `aria-selected`；
- `Escape` 关闭 Drawer；
- 关闭后焦点返回原 authority action；
- Portal Drawer 不嵌套第二个 Drawer；
- 不渲染 raw idempotency key、query、body、Judgment note、ticket 或 credential。

当前 source 已修复 Drawer ArrowRight 的异步 focus callback：组件在 synthetic event 释放前捕获 `tabsRoot`。本轮在每个基础矩阵中真实执行五次 `ArrowRight`：`Overview → Timeline → Certification → Alert → Recertification → Overview`，每一步同时验证 `aria-selected=true` 与 DOM focus 已移动到目标 Tab；最终 page errors 为 0。侧车仍保留 `Tab` focus containment、Escape 与 focus return 门禁，没有忽略或过滤 ArrowRight 错误。

## 5. 安全、网络和布局门禁

每个页面运行都安装 controlled request guard：

- 非 `127.0.0.1:5187` / `127.0.0.1:5188` 请求被阻断并计入 unknown request；
- API path 使用显式 allow-list；
- `OPTIONS` CORS preflight 也必须成功；
- `HTTP >= 400` 计入 unexpected failed request；
- `requestfailed` 中仅允许 React StrictMode 重新挂载产生的 `net::ERR_ABORTED`；其他失败均保留具体 method/path/error；
- request body、response body、URL query 与页面 DOM 均检查 protected sentinel；结果 JSON 不写入原始 request body。

fresh run 的汇总事实：

```text
console errors:              0
page errors:                 0
unknown requests:            0
unexpected failed requests:  0
horizontal overflow:         0
sensitive leak failures:     0
focus/drawer failures:       0
source binding changed:      false
production actions:          false
external network allowed:    false
```

read-only 场景：

```text
mutation request count:      0
mutation controls visible:   false
```

managed 业务场景：

```text
12/12 non-read-only scenario surfaces operable
business controls verified:  12/12
```

managed Dialog controls：

```text
alert-open      -> resolve Dialog   -> POST /resolve
alert-ack       -> suppress Dialog  -> POST /suppress
recert-pending  -> cancel Dialog    -> POST /cancel
```

三条 Dialog 流程均验证 modal 可访问性、只读 revision/status authority、reason/comment/suppressedUntil 控件、提交按钮、单次 controlled mutation、Idempotency-Key 存在且不写入产物、payload 字段白名单、Dialog 关闭和目标状态刷新。

## 6. Source-bound 与 Artifact 证据

Source binding 使用 SHA-256 对当前 Stage 21 前后端、scheduler/security critical fixes、read service functions、recertification list、readiness data integrity、API cancel idempotency、0031 migration、ORM/catalog、全部 Quality Operations frontend/dialog integration 与相关 tests 共 63 个文件做 before/after 绑定。旧 manifest 因 source 变化已作废，本轮运行前后 aggregate 相同：

```text
before: a85d68a5657798c1f5491eddf96ff6e9383b7338a292c0b3b61089483f76f3be
after:  a85d68a5657798c1f5491eddf96ff6e9383b7338a292c0b3b61089483f76f3be
```

产物：

```text
matrix PNG:       12
scenario PNG:     13
managed PNG:      13
Dialog PNG:        3
fresh PNG total:   41
required files:   41
```

manifest 与 result：

```text
output/playwright/enterprise-release-quality-operations-stage21/stage21-artifact-manifest.json
output/playwright/enterprise-release-quality-operations-stage21/stage21-browser-result.json
```

result SHA-256（写入 manifest）：

```text
7e6d367d0ad4efa7be58e011845ba120dc4290848b3fe46ccdeb1766d2398fed
```

Manifest 对每张 PNG 验证：

- basename-only path；
- exists；
- `captured_this_run=true`；
- `fresh=true`；
- bytes > 0；
- lower-case SHA-256；
- capture id 唯一；
- required file 集合完整。

## 7. 独立 Gate 证据

主 runner 内置 gate：

```text
status: passed
failures: []
```

独立复核：

```powershell
node output/playwright/enterprise-release-quality-operations-stage21\stage21_gate.mjs `
  output/playwright/enterprise-release-quality-operations-stage21\stage21-browser-result.json `
  output/playwright/enterprise-release-quality-operations-stage21\stage21-artifact-manifest.json
```

结果：

```json
{
  "status": "passed",
  "failures": []
}
```

Gate regression tests：

```powershell
node --test output/playwright/enterprise-release-quality-operations-stage21\test_stage21_gate.mjs
```

结果：

```text
ℹ tests 6
ℹ pass 6
ℹ fail 0
ℹ skipped 0
```

Gate tests 保留 fail-closed 覆盖：

- 缺失矩阵身份；
- 缺失场景生命周期证据；
- stale/duplicate/path-unsafe artifact；
- 非 loopback origin/错误端口；
- production action flag；
- 外部网络 flag；
- fresh result 与独立 gate 不一致。

## 8. 结论

截至 **2026-08-29**，Stage 21 visual acceptance sidecar 已基于最新 source 重新完成并通过：12 个基础矩阵、13 个状态/交互场景、41 个 fresh PNG；所有矩阵的 Drawer ArrowRight 五步 focus/selection 均通过且 page errors 为 0；独立 `stage21_gate` 为 `passed`，`node --test` 为 6/6。本侧车未修改 frontend/backend/core/migration/source。




