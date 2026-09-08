# Stage 20 Enterprise Release Quality Certification 视觉验收

**本地验收日期：** 2026-08-29（Asia/Shanghai）  
**UTC 运行日期：** 2026-08-28  
**目标 revision：** `0030_enterprise_release_quality_certification`  
**结果：** Passed

## 1. 验收范围

本轮使用真实 Chromium / Playwright 访问当前本地前端构建产物，并在受控 loopback 环境中 mock Stage 19 Release API 与 Stage 20 Release Quality API。没有连接生产后端，也没有执行真实 migration、Certification、Waiver、Approval、Promotion、Rollback、Application pin、source sync、restore、delete 或外部发布。

前端由以下受控 loopback server 提供：

```text
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20_loopback_server.py
```

Playwright / Chromium 版本检查：

```text
npx --yes playwright --version
Version 1.62.1
```

## 2. 矩阵结果

覆盖完整矩阵：

```text
direct / hash
× light / dark
× 1440×900 / 375×812 / 280×720
= 12 个浏览器场景
```

每个矩阵场景均验证：

- Knowledge Base Release 控制中心可见；
- `Release Quality Gate` gate strip 可见；
- Certification Tab 可见并可打开；
- Release history 的质量状态标签可见；
- Desktop 使用 Release history table；
- 375px / 280px 使用 mobile cards 与资源 Select；
- Drawer 焦点进入、键盘 Tab/Home/ArrowRight 导航、Escape 关闭及焦点回收；
- 页面无横向 viewport overflow。

## 3. Stage 20 质量场景

以下 6 个质量场景在代表性矩阵视口中分别重新打开页面并由 mock API 返回对应的受控 authority：

- `no_policy_unavailable`：无 Policy 时 gate 为 `unavailable`，页面保持 fail-closed，不伪造认证事实；
- `failed_certification`：失败 Certification 显示失败指标与阻断状态；
- `passed_certification`：通过 Certification 显示 `passed`，Release history 显示 `Certified`；
- `approval_required_waiver`：申请质量豁免打开 Approval dialog，真实浏览器发送带 Idempotency-Key 的受控 POST，mock 返回 `approval_required`，页面不展示原始审批票据；
- `valid_waiver`：有效 Approval-backed waiver 显示 `waived`、`已使用审批豁免` 与 `Waived` history tag；
- `evidence_history`：Certification evidence/history 延迟读取成功，展示 `2 experiments / 12 judgments` 与历史 Certification；该只读证据流没有产生 POST、PATCH 或 DELETE。

## 4. 安全与完整性

最终浏览器结果：

- `console_error_count = 0`；
- `page_error_count = 0`；
- `unknown_request_count = 0`；
- `unexpected_failed_request_count = 0`；
- `all_no_horizontal_overflow = true`；
- raw ticket、secret、Idempotency-Key、result body、judgment note 均未出现在 DOM、storage、console、page error 或 result JSON；
- `production_actions_performed = false`；
- source binding 在运行前后保持不变。

本轮的 read-only 验收针对 Certification evidence/history read surface：验证只读浏览不会触发 mutation request。当前生产 App 路由没有暴露强制 `readOnly=true` 的 URL/actor switch，因此本报告不把该流扩展宣称为全局“只读身份”禁用控件验收；组件级 read-only contract 仍由现有前端测试覆盖。

## 5. Fresh artifacts 与 source binding

本轮生成 **31 个 fresh PNG**：

- 12 个 `release-center` 截图；
- 12 个 `release-detail-certification` 截图；
- 6 个质量场景、7 个质量证据截图，其中 Approval-required waiver 额外包含 dialog 与提交后截图，因此合计 31 个文件。

每个截图都记录：

- 相对 artifact path；
- capture ID；
- 文件 byte count；
- SHA-256；
- mtime（纳秒）；
- `captured_this_run = true`；
- `fresh = true`。

完整性检查：

- required screenshots complete；
- duplicate files = 0；
- duplicate capture IDs = 0；
- source-bound files = 106；
- source tree SHA-256 前后相同。

## 6. 证据位置

验收目录：

```text
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\
```

关键文件：

```text
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20-browser-result.json
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20-artifact-manifest.json
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20-controlled-fixture.json
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20_acceptance.py
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\stage20_gate.py
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\test_stage20_gate.py
D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\run-stage20-acceptance.ps1
```

## 7. 最终验证命令

Acceptance：

```powershell
& 'D:\program_project\python_project\RAG4C\output\playwright\enterprise-release-quality-stage20\run-stage20-acceptance.ps1'
```

结果：

```text
status = passed
exit_code = 0
```

Gate tests：

```powershell
uv run pytest -q output/playwright/enterprise-release-quality-stage20/test_stage20_gate.py
```

结果：

```text
6 passed
```

Python syntax smoke：

```powershell
uv run --no-sync python -m py_compile `
  output/playwright/enterprise-release-quality-stage20/stage20_acceptance.py `
  output/playwright/enterprise-release-quality-stage20/stage20_gate.py `
  output/playwright/enterprise-release-quality-stage20/stage20_loopback_server.py `
  output/playwright/enterprise-release-quality-stage20/test_stage20_gate.py
```

结果：退出码 `0`。

未执行 commit；未修改 Stage 19 artifacts、Stage 19 harness 或 frontend source。

