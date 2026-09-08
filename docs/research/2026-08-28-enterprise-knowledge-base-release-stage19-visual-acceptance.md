# Stage 19 Enterprise Knowledge Base Release 视觉验收

**日期：** 2026-08-28  
**目标 revision：** `0029_enterprise_knowledge_base_releases`  
**结果：** Passed

## 1. 验收范围

本轮在 loopback-only、内存 fixture 环境完成 Knowledge Base Release Control Plane 的真实浏览器验收。没有连接生产后端，也没有执行真实 migration、default-channel backfill、Release capture、promotion、rollback、Application pin、approval execution、source sync、restore 或 destruction。

矩阵覆盖：

```text
direct / hash
× light / dark
× 1440×900 / 375×812 / 280×720
= 12 个场景
```

## 2. 视觉结论

桌面端保持腾讯企业控制台式的克制信息架构：

- Knowledge Base resource shell 只拥有一个页面标题；
- TDesign Tabs 承载资源导航；
- Release 控制中心以 Channel summary、不可变 Release history、detail Drawer 三层结构呈现；
- Configured / Candidate / Effective / Serving 明确分栏；
- drift、readiness、serving generation 和 Channel revision 使用真实状态标签，不伪造空值；
- Release history 使用 TDesign PrimaryTable。

移动端：

- 使用带可见标签的 `知识库资源` Select；
- 使用带可见标签的 `发布 Channel` Select；
- Release history 切换为 cards，不保留桌面表格；
- 280px 下 Drawer tabs 使用 TDesign 自带可滚动导航，没有页面级横向溢出；
- 主操作保持单一的 `生成候选`。

## 3. 交互证据

受控 fixture 验证：

- Capture：revision fence 与 Idempotency-Key 请求完整，返回 sanitized `applied`；
- Promote：返回 sanitized `approval_required`，页面仅展示“已提交审批”，不展示原始 ticket；
- Rollback：返回 sanitized `applied`；
- Detail Drawer：打开后焦点进入 dialog，Escape 关闭，焦点回到原触发按钮；
- Impact / Audit：仅在对应 Tab 激活后发起请求；
- Readiness：显示服务端返回的 `document_index_drift` blocker，而不是推测状态。

## 4. 安全与完整性

结果文件确认：

- `console_error_count = 0`；
- `page_error_count = 0`；
- `unknown_request_count = 0`；
- `unexpected_failed_request_count = 0`；
- `all_no_horizontal_overflow = true`；
- raw ticket、fixture secret、Idempotency-Key 在 DOM/storage/console/result JSON 中均不可见；
- 35 个截图均为本次运行 fresh artifact；
- 文件名、capture ID 均无重复；
- 每个截图均记录 byte count 和 SHA-256。

## 5. 证据位置

```text
output/playwright/enterprise-knowledge-base-release-stage19/
```

关键文件：

- `stage19-browser-result.json`
- `stage19-artifact-manifest.json`
- `stage19-controlled-fixture.json`
- `stage19_gate.py`
- `test_stage19_gate.py`
- `run-stage19-acceptance.ps1`


## Source binding

Artifact manifest records `git_sha`, `source_tree_sha256`, Stage 19 source file count, and SHA-256 values for `uv.lock` and `frontend/package-lock.json`. Any Stage 19 source or lockfile change requires a fresh browser run.
