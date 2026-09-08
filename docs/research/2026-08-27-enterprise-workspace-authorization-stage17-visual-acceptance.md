# Stage17 Enterprise Workspace Authorization 视觉与受控验收说明

**日期：** 2026-08-27
**时区：** Asia/Shanghai
**目标 revision：** `0027_enterprise_workspace_authorization`
**适用页面：** `/enterprise/workspaces` 与 `#/enterprise/workspaces`

## 1. 目的与边界

本说明把 Stage 17 的 Permissions Rollout Center 转换为可重复、可审计的浏览器验收契约。验收仅使用本地前端和受控 API fixture，不连接生产数据库，不执行真实 migration、policy backfill、Enforced 激活、approval execution、downgrade、restore 或外部发布。

验收必须证明：

- Workspace Authorization 与既有 Tenant Role / Dataset ACL 权限并列展示，不重定义 `enforcement_mode`；
- `disabled`、`shadow`、`enforced` 三种模式视觉语义明确，状态机文案为 `disabled -> shadow -> enforced`；
- Shadow 只展示 `would_grant_permissions`，不得把候选权限伪装成 `effective_permissions`；
- Enforced 的贡献必须带 Workspace、policy revision、member role、binding 和 `permission_model_version` 证据；
- 模式变更对 revision、scope、reason、impact 和 approval 状态保持诚实；
- raw execution ticket 不进入 DOM、浏览器持久化、console、截图或结果 JSON。

## 2. 视觉层级

### 2.1 Permissions Rollout 权威条

Workspace 详情的“权限 / Permissions”页签顶部应优先展示：

1. 当前 mode：Disabled / Shadow / Enforced；
2. policy revision；
3. `permission_model_version`；
4. active Workspace member 数；
5. active Dataset binding 数；
6. matching approval policy；
7. catalog revision `0027_enterprise_workspace_authorization`。

状态 token 必须允许换行；280px 视口不得依赖水平滚动查看 revision、fingerprint 或状态。

### 2.2 Role Matrix

桌面使用紧凑 TDesign 表格，移动端使用优先级卡片。版本 1 固定映射：

- owner/admin → manager → read/write/delete/manage/audit；
- editor → editor → read/write/delete；
- viewer → viewer → read。

不得出现可编辑的 tenant-custom role matrix，也不得把 Workspace role 标注为 Tenant role。

### 2.3 Impact Preview

Impact 必须并列展示：

- Tenant role；
- Dataset ACL role 和 matched grants；
- Workspace roles 与 contributing Workspaces；
- current effective permissions；
- shadow candidate / `would_grant_permissions`；
- enforced granted delta；
- warnings / unavailable evidence。

Shadow 场景中 current effective 与 would-grant 必须有清晰分隔；颜色不能成为唯一信息载体。

### 2.4 Mode Dialog

Dialog/Drawer 必须显示：

- Workspace ID 与 Workspace revision；
- policy revision；
- from mode / target mode；
- `permission_model_version`；
- `permission_matrix_fingerprint`；
- matching approval policy；
- 影响摘要和必填 reason；
- direct mutation 或 approval request 的真实下一步。

375px 使用全宽模式对话框；280px 每行只保留一个主动作，宽度不得超过 viewport。

## 3. 截图矩阵

基础矩阵共 12 张，每个组合均打开 Workspace detail 的 Permissions 页签，并保留既有文件名：

| Route | Theme | Viewport | Base file |
| --- | --- | --- | --- |
| direct | light | 1440×900 | `direct-light-1440.png` |
| direct | dark | 1440×900 | `direct-dark-1440.png` |
| hash | light | 1440×900 | `hash-light-1440.png` |
| hash | dark | 1440×900 | `hash-dark-1440.png` |
| direct | light | 375×812 | `direct-light-375.png` |
| direct | dark | 375×812 | `direct-dark-375.png` |
| hash | light | 375×812 | `hash-light-375.png` |
| hash | dark | 375×812 | `hash-dark-375.png` |
| direct | light | 280×720 | `direct-light-280.png` |
| direct | dark | 280×720 | `direct-dark-280.png` |
| hash | light | 280×720 | `hash-light-280.png` |
| hash | dark | 280×720 | `hash-dark-280.png` |

附加交互截图：

- `direct-light-1440-permissions.png`：desktop light，Permissions 页签由键盘 Enter 打开后的证据；
- `direct-light-1440-impact.png`：desktop light，Impact Preview 完整表；
- `hash-dark-1440-mode-dialog.png`：desktop dark hash，Shadow → Enforced approval mode dialog；
- `direct-light-375-mode-dialog.png`：375 light，mode dialog 全宽与 reason 字段；
- `direct-dark-280-mode-dialog.png`：280 dark，mode dialog 单动作布局、长 fingerprint 换行。

交互文件名格式为：

```text
{route}-{theme}-{width}-permissions.png
{route}-{theme}-{width}-impact.png
{route}-{theme}-{width}-mode-dialog.png
```

`stage17-artifact-manifest.json` 必须列出 12 个基础截图与全部交互截图，并区分 `exists` 与 `captured_this_run`；旧文件存在不能替代本次采集。

## 4. 机器可读验收门

`stage17-browser-result.json` 使用 `stage17-browser-result.v2`，至少包含：

```json
{
  "schema_version": "stage17-browser-result.v2",
  "stage": 17,
  "target_revision": "0027_enterprise_workspace_authorization",
  "status": "blocked",
  "exit_code": 2,
  "console_error_count": 0,
  "page_error_count": 0,
  "unknown_request_count": 0,
  "failed_request_count": 0,
  "unexpected_failed_request_count": 0,
  "all_no_horizontal_overflow": true,
  "permissions_rollout_visible": true,
  "impact_visible": true,
  "mode_dialog_visible": true,
  "keyboard_focus_all_passed": true,
  "raw_ticket_visible": false,
  "raw_ticket_persisted": false,
  "raw_ticket_result_json": false,
  "artifact_manifest": "output/playwright/enterprise-workspace-authorization-stage17/stage17-artifact-manifest.json"
}
```

示例中的 `blocked` / `exit_code: 2` 是当前 UI 尚未满足证据门时的合法结果，不是预置值；实际 status 必须由脚本按观测结果计算。每个场景还需记录：route、theme、viewport、URL、mode、policy revision、model version、overflow 指标、console/page/unknown request 明细、所有 failed request 的精确分类、surface 可见性与 bounding rect、Enter/Tab/Escape/focus return 结果、截图文件名和关键可见文案。

以下任一项不满足都不得为 `passed`：

- console error、page error；
- unknown API request；
- 未命中精确 allowlist 的 failed request；
- horizontal overflow；
- Permissions / Impact / mode dialog 缺失、不可见、无 bounding rect 或越出 viewport；
- Enter、Tab、Escape、打开聚焦或关闭后 focus return 任一失败；
- raw ticket 出现在 DOM、HTML、localStorage、sessionStorage、console/page log 或结果 JSON；
- 所需截图未在当前 run 捕获。

### 4.1 状态与 exit code

- `passed` → `0`：所有场景和 artifact gate 通过；
- `blocked` → `2`：应用尚未 ready 或任一验收门失败；
- `failed` → `1`：浏览器/fixture/序列化等 harness 无法生成可信结果。

`run-stage17-acceptance.ps1` 显式传播内层 exit code，不能把 `blocked` 或 `failed` 转成成功。

## 5. 安全探针

### 5.1 Error/Network

- console error = 0；
- page error = 0；
- unknown API request = 0；
- 非预期 failed request = 0；
- 所有 API 都由受控 fixture 的 method + 完整 URL 明确处理，未匹配请求必须记为 unknown 并让验收失败；
- `net::ERR_ABORTED` 只允许在 `stage17_gate.py` 的精确三元匹配（method、完整 URL、failure）下归类为 React effect cancellation；每条 allowlist 还必须带精确 expected reason。任何 URL、请求类型、失败文本或原因不匹配都 fail closed。

### 5.2 Overflow 与几何

每个场景计算：

```text
documentElement.scrollWidth > documentElement.clientWidth
OR body.scrollWidth > documentElement.clientWidth
```

结果必须为 `false`。Permissions、Impact 与 mode dialog 不能只用 `count()`：必须调用 `is_visible()`，读取 bounding rect，并校验尺寸和 viewport 边界；mode dialog 不得越出 viewport。

### 5.3 Raw Ticket

受控 approval fixture 可以向内存流程返回一次性测试 ticket，以验证前端消费边界；验收结束前必须扫描：

- `document.documentElement.innerText`；
- `document.documentElement.innerHTML`；
- localStorage；
- sessionStorage；
- 全部 console messages 与 page errors；
- 截图结果 JSON。

不得出现 `rag4c-approval-ticket-`、`opaque-ticket-` 或 fixture 中的完整 ticket 值。mutation response 可以在内存探针中确认 `ticket_present: true`，但结果 JSON 只能记录布尔值或 request id，不得保存原值。所有日志与错误写入结果前必须经过敏感值扫描/脱敏。

## 6. 执行前稳定门

在采集截图前必须同时满足：

- 本地 Catalog Schema head 已为 `0027_enterprise_workspace_authorization`；
- `tenant_workspace_authorization_policies` migration/Core/API 测试通过；
- 前端存在 Stage17 authorization/impact API、Permissions rollout、impact 和 mode dialog 测试；
- `/enterprise/workspaces` 与 hash route 均可打开；
- 前端 build 通过；
- 受控 fixture 与当前 API contract 对齐；
- 工作区没有由验收 Worker 引入的生产代码、migration 或 ORM 修改。

任一条件未满足时，run 必须返回真实 `blocked` 或 `failed`，不生成冒充完成态的结果；旧截图可以保留，但 manifest 的 `captured_this_run` 必须为 `false`。

## 7. 输出目录

所有资产仅写入：

```text
output/playwright/enterprise-workspace-authorization-stage17/
```

目录应包含：

- `run-stage17-acceptance.ps1`；
- `stage17-acceptance.py` 与 `stage17_gate.py`；
- `test_stage17_gate.py`；
- 受控 fixture / browser probe；
- 12 张基础矩阵截图和附加交互截图；
- `stage17-browser-result.json`；
- `stage17-artifact-manifest.json`；
- `README.md`（执行命令、环境、阻塞或结论）。
