# 腾讯企业工作空间与知识库控制台视觉研究

**观察日期：** 2026-08-27
**时区：** Asia/Shanghai
**研究用途：** RAG4C Stage 16 Enterprise Workspace Control Plane 的信息架构与视觉校准
**浏览器证据：** `output/playwright/tencent-workspace-reference-2026-08-27/`

## 1. 研究边界

本次仅使用真实浏览器读取腾讯云公开文档页面，没有登录腾讯云、没有打开控制台会话、没有填写或提交表单，也没有触发创建、删除、授权、反馈或其它外部副作用。

本地仅保存公开页面的全页截图、Playwright accessibility snapshot、导航结果和控制台错误摘要，用于团队内部的设计研究。没有单独下载腾讯商标、图标、控制台原图、字体或其它受版权限制素材；RAG4C 不应在产品中嵌入这些截图或复制腾讯页面 markup。

合法借鉴范围限于通用的信息架构、层级关系、密度、交互模式和企业控制台语言。不得逐像素复制，不得使用腾讯名称、品牌资产、专有图标或原页面文案作为 RAG4C UI。

## 2. 页面可访问性

以下五个页面在未登录状态下均可直接访问，Playwright 导航、accessibility snapshot 和全页截图均成功：

1. 企业、工作空间与权限概述
   `https://cloud.tencent.com/document/product/1759/122569`
   证据：`enterprise-workspace-permissions-overview.png`、`enterprise-workspace-permissions-overview.snapshot.yml`
2. 企业管理
   `https://cloud.tencent.com/document/product/1759/122570`
   证据：`enterprise-management.png`、`enterprise-management.snapshot.yml`
3. 工作空间
   `https://cloud.tencent.com/document/product/1759/122576`
   证据：`workspace-management.png`、`workspace-management.snapshot.yml`
4. 平台端用户权限
   `https://cloud.tencent.com/document/product/1759/122574`
   证据：`platform-user-permissions.png`、`platform-user-permissions.snapshot.yml`
5. 什么是知识库
   `https://cloud.tencent.com/document/product/1759/123551`
   证据：`knowledge-base-overview.png`、`knowledge-base-overview.snapshot.yml`

机器可读结果见 `accessibility-result.json`，五项均为：

```text
accessible: true
screenshot_created: true
navigation_exit: 0
snapshot_exit: 0
screenshot_exit: 0
```

公开文档会向未登录身份探针发起请求，浏览器控制台记录了 `kiki.cloud.tencent.com/api/whoami` 的 `401`。该请求未阻止正文、目录和公开示例图加载，因此可访问性结论仍为成功；这些控制台错误是腾讯公开站点自身的未登录探针结果，不是 RAG4C 页面质量证据。

## 3. 研究对象与页面任务

RAG4C Workspace Center 的主要用户是企业平台所有者、租户管理员、Workspace 管理员和知识库治理人员。

页面的单一核心任务是：**证明当前 Tenant 下有哪些真实 Workspace、每个 Workspace 处于什么生命周期、绑定了哪些成员和 Knowledge Base，以及下一项变更是否安全可执行。**

因此页面不能变成营销 Dashboard，也不能用 Workspace 角色暗示尚未接通的 Dataset 权限。

## 4. 可借鉴的信息架构

### 4.1 企业、Workspace、权限是分层概念

公开概述将企业、工作空间、用户权限和数据权限拆成不同层级：

- Enterprise 管理企业用户和企业级角色；
- Workspace 表示隔离的协作区域；
- Workspace member 决定谁进入当前空间；
- 功能权限和数据权限在专门的权限面中配置；
- Workspace 与资源数据存在明确归属关系。

对 RAG4C 的直接启发是：Tenant、Workspace membership、Dataset binding 和 Dataset ACL 必须分别展示，不能把四者压缩成一个“角色”字段。

### 4.2 Workspace 列表以资源管理为中心

工作空间公开示例采用典型企业资源列表：

- 页面标题和短说明在上方；
- 一个明确的主要创建操作；
- 白色表格承载名称、成员或状态等核心事实；
- 行尾使用紧凑文本操作；
- 新建和编辑使用居中 Dialog，而不是把整个页面改成表单；
- 默认 Workspace 和自定义 Workspace 的差异被明确说明；
- 删除操作附带不可恢复后果说明。

RAG4C 应将此模式翻译为 `Create workspace`、dense table、行级 `查看 / 编辑 / 归档`，并把 revision、environment、default、member count 和 Dataset binding count 作为权威列，而不是增加装饰性指标卡。

### 4.3 Workspace 详情按治理对象分区

腾讯工作空间文档将内容分为：

- 空间管理；
- 空间成员；
- 空间中的功能及数据。

Stage 16 可将其映射为右侧 Detail Drawer：

```text
Overview | Members | Knowledge bases | Permissions
```

- Overview：ID、code、environment、status、default、revision 和 lifecycle evidence；
- Members：真实 Workspace member、role、status、revision 和成员操作；
- Knowledge bases：primary/shared binding、Dataset ID、binding revision 和解除绑定操作；
- Permissions：Workspace 治理角色证据和明确的 `workspace_authorization_not_enforced`，不展示虚假的 Dataset effective permission。

### 4.4 成员与权限配置分离

平台端用户权限页面使用密集列表、分区表单、权限说明表和集中式 Dialog。可借鉴的不是具体权限枚举，而是：

- “成员是谁”与“成员能做什么”分开；
- 权限项按功能和数据维度组织；
- 操作前展示当前范围；
- 权限说明与实际控制保持相邻；
- 无权限、可查看、可编辑等状态使用明确文字而不是只依赖颜色。

Stage 16 只建立 Workspace role authority，尚未连接 Dataset authorization。因此 Permissions Tab 应使用 warning/limited evidence，而不是照搬“可查看/可编辑”权限矩阵。

### 4.5 Knowledge Base 是 Workspace 内的独立资源

知识库公开文档表现为先选资源、再进入密集详情：

- Knowledge Base 列表承担创建和资源选择；
- 编辑名称使用小型 Dialog；
- 删除前检查引用依赖；
- 进入详情后再管理文档、问答、数据库和设置；
- 资源状态与所属范围始终可见。

RAG4C 的 Workspace Drawer 因此只管理 binding，不应把完整 Knowledge Base 内容管理复制进 Drawer。点击 Knowledge Base 应跳转到对应知识工作台；解绑操作必须展示 primary/shared、Workspace、Dataset ID 和 revision evidence。

## 5. 视觉模式

### 5.1 可以借鉴

- 白色主内容面板置于浅灰应用画布；
- 固定产品导航和清晰的页面标题层级；
- 1px 中性边框、小圆角、很弱或没有常驻阴影；
- 蓝色仅用于当前选中态和主要操作；
- 高密度表格优先于卡片墙；
- 状态用 Tag + 文本组合；
- 表单与危险操作集中在 Dialog/Drawer；
- 说明和风险使用浅色 Alert 区块；
- 行为名称直接描述结果，例如“新建空间”“编辑空间”“删除空间”；
- 桌面保留多列证据，移动端只保留优先事实并进入详情。

### 5.2 RAG4C 的独立视觉签名

腾讯参考强调资源管理；RAG4C 应保留自己的 **Governance Evidence Strip**：

```text
Workspace authority | Active workspaces | Default workspace |
Primary dataset bindings | Catalog revision | Authorization state
```

该证据条不是营销数字，而是数据库权威的持续证明。`Authorization state` 必须直接显示：

```text
workspace_authorization_not_enforced
```

并附中文说明：Workspace 成员和角色已经持久化，但 Dataset ACL 仍由现有授权引擎执行。

### 5.3 建议 Token

继续使用 TDesign token，不硬编码或复制腾讯页面样式：

- primary：TDesign Brand / RAG4C `#0052D9`；
- canvas：`#F3F6F8`；
- ink：`#1D2129`；
- secondary：`#4E5969`；
- border：TDesign component border；
- success/warning/danger：只表示真实 lifecycle 或风险；
- radius：4–8 px；
- spacing：以 8 px 网格为主；
- shadow：只用于 Drawer、Dialog 等浮层。

TDesign React 应承担 Table、Drawer、Dialog、Tabs、Form、Tag、Alert、Select、Pagination、Loading 和 Empty；图标优先使用 TDesign Icons。

## 6. Stage 16 Workspace Center 建议结构

```text
┌ Page title / Create workspace ──────────────────────────────┐
├ Governance evidence strip ──────────────────────────────────┤
├ Authorization warning: workspace_authorization_not_enforced ┤
├ Status / Environment / Keyword filters ─────────────────────┤
├ Dense desktop table OR mobile priority cards ────────────────┤
└ Detail Drawer: Overview | Members | Knowledge bases | Permissions
```

桌面表格建议优先列：

```text
Workspace | Environment | Status | Default | Members |
Knowledge bases | Revision | Updated at | Actions
```

375px / 280px 卡片只保留：

```text
Name + status
Environment + default
Members + knowledge bases
Revision
View details
```

移动端不要横向压缩桌面表格，不要在卡片首屏放置多个危险操作；编辑、归档、成员和 binding 操作进入 Drawer/Dialog。

## 7. Shared Header Workspace Selector

Workspace selector 必须遵循以下诚实边界：

1. 只显示后端 `/api/enterprise/workspaces` 返回的 Workspace；
2. 不生成“默认生产空间”等演示选项；
3. `localStorage` 只保存已选择的 Workspace ID；
4. 已保存 ID 不在最新响应中时，清除或回退到后端返回的 default/首个可用 Workspace；
5. archived Workspace 可以按产品合同显示为不可选或带状态，不得伪装 active；
6. Selector 显示 Workspace scope，不把 actor role 显示成 Dataset permission；
7. 切换 Workspace 只改变前端上下文，真正读取和 mutation 仍由后端 Tenant/Workspace predicate 与 actor token 授权。

## 8. 危险操作语言

公开参考对删除 Workspace 和删除 Knowledge Base 都强调依赖与不可恢复后果。RAG4C Stage 16 使用 archive 而不是物理删除，仍需明确：

- 当前 Workspace ID、status、revision；
- 是否为 default Workspace；
- active primary Dataset binding 数量；
- 归档后不能继续添加成员或 Dataset；
- default Workspace 拥有 active primary binding 时服务端会拒绝归档；
- 所有 mutation 成功后重新读取权威事实。

UI 不应承诺客户端可以绕过 last-owner、default Workspace 或 revision fence。

## 9. 不应复制的内容

- 腾讯云 Logo、商标、产品名称和专有图标；
- 文档中的控制台截图、流程图和页面文案；
- 腾讯控制台 DOM、CSS class、图片 URL 或具体视觉资产；
- 腾讯具体角色名称、权限枚举或产品配额；
- 任何登录后控制台数据和客户信息。

本研究截图仅作内部设计校准，不进入 RAG4C 产品包或公开素材。

## 10. 证据清单

证据目录：`output/playwright/tencent-workspace-reference-2026-08-27/`

- `accessibility-result.json`：页面 URL、title、可访问性、命令退出码和截图状态；
- `*.navigation.txt`：Playwright 导航结果；
- `*.snapshot.yml`：公开页面 accessibility tree；
- `*.png`：公开页面全页截图；
- `*.console-errors.txt`：未登录身份探针等站点控制台错误；
- `*.screenshot.txt`：截图命令结果。

这些文件证明本次研究使用了真实浏览器，并且没有依赖搜索摘要或伪造控制台数据。
