# Stage 22 Notification Center / Alert Routing Gap Analysis

- **日期：** 2026-08-29
- **阶段：** Stage 22
- **研究范围：** Notification Center、Alert routing、Stage 21 Alert Inbox、TenantAuditEvent、Approval inbox、Workspace scope bar 通知入口、API/client/router/app shell、TDesign 组件复用
- **研究模式：** research only
- **实现边界：** 本阶段禁止实现代码、禁止迁移、禁止生产写入、禁止修改受保护路径
- **本阶段唯一写入：** `D:\program_project\python_project\RAG4C\docs\research\2026-08-29-stage22-notification-center-gap-analysis.md`

## 1. 结论先行

当前工作树已经拥有三个可复用的事实源，但还没有真正的全局 Notification Center authority：

1. **Stage 21 Alert Inbox 已真实存在**，是 Tenant/Dataset/Release/Channel 维度的质量运营告警工作面，拥有 Alert 生命周期、revision、source Observation、幂等与审计边界；它不是用户级通知收件箱。
2. **TenantAuditEvent 已真实存在**，是租户级追加式审计事实，拥有 sequence 游标、actor snapshot、before/after snapshot 和 action/resource 过滤；它不是通知投递事实，也没有用户 read/unread 状态。
3. **Approval inbox 已真实存在**，可以按 `pending_for_me` 读取待审批事项，并支持审批详情、批准、拒绝、取消和一次性执行授权边界；它是审批任务中心，不是通用通知中心。
4. **WorkspaceScopeBar 的通知图标只是已预留的条件渲染空壳**。组件支持 `onOpenNotifications`，但 App shell 没有传入回调；当前 App 的可访问性测试反而明确断言通知与审计按钮尚不存在。
5. 当前没有统一的通知表、通知收件人、通知 receipt、通知 unread count、server-composed notification feed、routing policy 或全局通知 route。

因此，Stage 22 不应从“把现有三种接口在前端并行请求后拼成一张列表”开始。那样会得到看起来像 Notification Center、但无法证明完整性、租户隔离、未读语义和分页一致性的空壳。

**推荐结论：** Stage 22 的最小可信范围应是一个由服务端组合的、Tenant-safe 的通知投影，只纳入两类已经能够定义清楚受众的来源：

- `quality_alert`：来自 Stage 21 的质量告警，按照 Dataset 权限与固定系统路由投影；
- `approval_pending_for_me`：来自 Approval authority、且服务端确认当前操作者确实是有效审批人的待审批事项。

`TenantAuditEvent` 在 Stage 22 MVP 中只作为审计详情和 handoff 目标，不直接成为通知行；Recertification Job 作为质量告警详情中的关联事实，不单独伪造用户通知。通知的 `read/unread` 必须有独立的 per-user receipt，不能复用 Alert 的 `acknowledged`，也不能复用 Approval 的业务状态。

## 2. 证据与当前状态

本盘点基于当前工作树和既有 Stage 20/21 设计、测试及验收产物完成。当前工作树包含前序阶段的大量未提交累计修改；本阶段不 reset、checkout、clean、全树格式化，也不根据工作树状态推断生产已执行。

关键依据包括：

- Stage 21 设计明确写出 Quality Operations 是 Releases 内的局部工作面，不宣称存在全局 Notification Center；并明确禁止在 Stage 21 暴露全局通知 route。
- Stage 21 的 API 已挂载质量运营的 SLO Policy、Scan Schedule、Scan Run、Observation、Alert、Recertification Job 读取和生命周期接口。
- Stage 21 的前端验收已证明 Alert Inbox、Quality Operations、Drawer、Dialog、read-only、direct/hash 路由和 TDesign 交互可以在受控 fixture 下工作；这证明了可复用的 UI/状态模式，不证明全局通知 authority 已存在。
- 当前没有任何文件或 migration 表名包含真正的 `tenant_notifications`、`notification_receipts` 或 routing policy authority。

## 3. 现有模块盘点

### 3.1 Stage 21 Alert Inbox：真实能力与边界

#### 已有能力

后端：

- `D:\program_project\python_project\RAG4C\models\orm.py` 中的 `DatasetReleaseQualityAlert` 对应 `dataset_release_quality_alerts`。
- 记录 Tenant、Dataset、Release、Channel、Release role、Alert type、severity、status、revision、source Observation、occurrence count 和生命周期时间。
- Alert 的状态为：
  - `open`
  - `acknowledged`
  - `resolved`
  - `suppressed`
- `active_alert_key` 为当前业务周期的唯一身份；resolved 后清除 active identity，后续 breach 创建新的 Alert row。
- `D:\program_project\python_project\RAG4C\core\enterprise_release_quality_alerts.py` 已提供：
  - `list_quality_alerts`
  - `get_quality_alert`
  - `acknowledge_quality_alert`
  - `resolve_quality_alert`
  - `suppress_quality_alert`
  - Observation 驱动的 create/update/resolve helper。
- Alert 读取会重新校验 identity、digest、source Observation、生命周期和持久化 comment；不安全 comment fail closed。
- 系统扫描器的 create/update/resolve 已写入 `TenantAuditEvent`，actor 使用固定的 `system:quality-scanner`。

前端：

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-release-quality-operations\api\operationsApi.ts` 统一了 Stage 21 Alert/Job API 请求、Tenant header、Authorization、cursor、error projection 和 mutation Idempotency-Key。
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-release-quality-operations\model\operationsModel.ts` 已定义 Quality authority、Alert、Recertification Job 的安全投影。
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-release-quality-operations\hooks\useReleaseQualityOperations.ts` 已有：
  - summary、Alert、Job 首次并行读取；
  - AbortSignal；
  - Dataset/Tenant context generation fence；
  - stale response 防护；
  - mutation 串行队列；
  - retry 时保持相同 Idempotency-Key；
  - `partial`、`empty`、`error`、`unavailable` 状态。
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-release-quality-operations\components\QualityOperationsCenter.tsx` 已有 SLO Horizon Rail、At-risk authority 表、移动卡片、Alert Inbox 和 Recertification queue。
- `QualityOperationsDetailDrawer.tsx` 已有单一 Drawer、Overview/Timeline/Certification/Alert/Recertification 五个 Tab、focus return、Escape 和只读事实展示。
- `QualityOperationsMutationDialogs.tsx` 已有 acknowledge、resolve、suppress、queue、cancel 的 TDesign Dialog 协调模式。

#### 不能直接充当 Notification Center 的原因

- Alert 是 **Dataset-scoped 的质量业务对象**，不是 tenant-wide user message。
- Alert 的 `acknowledged` 表示运维人员改变了告警业务生命周期，不表示当前用户已经读过通知；一个用户 acknowledge 后，其他用户仍需要有各自的 read receipt 语义。
- Alert 没有 recipient/account、read_at、unread count、dismissed/read receipt、投递时间、目标路由、标题模板或通知分类字段。
- `fetchQualityAlerts` 只支持 status/severity/cursor/limit，当前没有面向用户的 unread/recipient 查询。
- Hook 保存了 `nextCursor`，但对外没有独立的 `loadMoreAlerts` / `loadMoreJobs` 读取能力；现有 Alert Inbox 主要展示首屏集合，不能直接作为完整的全局分页收件箱。
- `QualityOperationsCenter` 的 Alert Inbox 位于 Releases 资源内的 Quality Operations 页面，不在 App shell 全局可达。
- 当前 Drawer 的 Timeline/Observations 是明确的 unavailable 边界：Workspace 传入的 `observations` 和 `auditFacts` 为空，Hook 的 lazy load 也返回 unavailable。不能把当前 Timeline 视觉壳误认为完整通知历史。
- Alert mutation 具有 `knowledge.manage` 语义；Notification Center 的 mark-as-read 不应要求用户拥有质量告警管理权限，也不应从通用通知行直接执行 acknowledge/resolve。

**判定：** Stage 21 Alert Inbox 是 Stage 22 的首要 source adapter 和详情 handoff 目标，但不是 Notification authority 本身。

### 3.2 TenantAuditEvent：真实审计事实与边界

#### 已有能力

- `D:\program_project\python_project\RAG4C\models\orm.py` 中的 `TenantAuditEvent` 对应 `tenant_audit_events`。
- 主要字段包括：
  - tenant；
  - sequence 主键；
  - event id；
  - actor id/name/email snapshot；
  - action；
  - resource type/id；
  - target account；
  - before/after snapshot；
  - request id/IP；
  - occurred_at。
- `D:\program_project\python_project\RAG4C\core\enterprise_directory.py` 的 `list_tenant_audit_events` 使用 Tenant scope 和 descending sequence keyset pagination。
- `D:\program_project\python_project\RAG4C\server\enterprise_admin_api.py` 的 `/api/enterprise/audit-events` 受 `knowledge.audit` 保护，支持 actor、action、resource、target、request、sequence cursor 等过滤。
- 系统 Alert create/update/resolve、审批、成员、Workspace、Release、Compliance 等多个 domain mutation 会在自己的事务中写入 TenantAuditEvent。
- 读取时会执行安全 snapshot projection；审计快照不是任意 raw body 的回显通道。

#### 不能直接充当 Notification Center 的原因

- TenantAuditEvent 没有 `dataset_id` 字段。它只能可靠表达 Tenant scope，不能由 `resource_id` 猜出 Dataset scope。
- 它没有 recipient、user read state、severity、notification category、dedupe key、target route 或 expiry。
- 审计事实的完整性要求是 append-only 和可追溯；通知中心的需求是面向操作者的可处理队列，两者生命周期不同。
- `knowledge.audit` 权限不应被隐式转换为“可以收到所有通知”；反之，拥有 `knowledge.read` 的用户也不一定可以读取全租户审计。
- 系统扫描器写入的 Audit request identity 可能为空字符串；Notification Center 不能把 request id 当作必有的用户可见关联键。

#### 当前审计实现还存在的接口分裂

当前工作树有两套审计事实接口：

1. `TenantAuditEvent`：`/api/enterprise/audit-events`，Tenant-wide，企业管理审计。
2. `KnowledgeAuditEvent`：`/api/knowledge-bases/{dataset_id}/audit-events`，Dataset-scoped，知识治理审计。

两者使用不同表、不同 service、不同 response shape 和同名相近的“audit”概念。Stage 22 不应在客户端把它们无标签地合并成“活动通知”；若未来需要审计活动投影，必须在 response 中保留 `source_type`、authority scope 和权限来源。

前端也存在一处可收敛的重复边界：

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-admin\hooks\useEnterpriseAdminWorkspace.ts` 暴露 audit state 和 `loadMoreAudit`；
- `D:\program_project\python_project\RAG4C\frontend\src\pages\EnterpriseAdminPage.tsx` 的 `AuditSurface` 又维护了 page-local `useEnterpriseAudit`；
- 默认管理页调用 `useEnterpriseAdminWorkspace(scope, { auditEnabled: false })`，随后由 `AuditSurface` 自己读取同一 `/api/enterprise/audit-events`。

这不是 Stage 22 立即重构的理由，但说明 Notification Center 不应再复制第三套 audit loader。

**判定：** TenantAuditEvent 是 Notification detail 的审计证据和 audit handoff source，不是 MVP 通知行 source。

### 3.3 Approval inbox：真实任务中心与边界

#### 已有能力

后端：

- `D:\program_project\python_project\RAG4C\core\enterprise_approval_control.py` 提供：
  - approval policy list/detail；
  - approval request list/detail；
  - `mine`；
  - `pending_for_me`；
  - approve/reject/cancel；
  - execution ticket consume；
  - expiration、revision、幂等和 Audit。
- `D:\program_project\python_project\RAG4C\server\enterprise_approval_api.py` 已挂载：
  - `GET /api/enterprise/approvals/requests`
  - `GET /api/enterprise/approvals/requests/{request_id}`
  - `POST /api/enterprise/approvals/requests/{request_id}/approve`
  - `POST /api/enterprise/approvals/requests/{request_id}/reject`
  - `POST /api/enterprise/approvals/requests/{request_id}/cancel`
  - `POST /api/enterprise/approvals/requests/{request_id}/consume-ticket`
- Approval request 的 status 是业务状态：`pending`、`approved`、`rejected`、`cancelled`、`expired`、`executing`、`executed`、`execution_failed`。
- `pending_for_me` 会由服务端依据当前 actor 与 approver eligibility 判断，不应由前端自行推断。
- Approval request detail 能展示 policy、approvers、decisions、process、revision 和 execution adapter boundary。
- 一次性 execution ticket 只在 mutation delivery 中短暂存在，前端模型明确不把它附着到持久 request projection。

前端：

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-approval\api\enterpriseApprovalApi.ts` 已封装 query、auth header、mutation Idempotency-Key 和 response projection。
- `useEnterpriseApproval.ts` 已有：
  - request/policy 并行加载；
  - abort 和 sequence fence；
  - detail lazy load；
  - mutation retry 保留相同 key；
  - `pending` / `mine` 筛选；
  - approve/reject/cancel 与执行授权边界。
- `EnterpriseApprovalCenter.tsx` 已有 TDesign PrimaryTable、移动卡片、详情 Drawer、Process/Facts Tab、审批 Dialog。
- `/enterprise/approvals` 已有 direct/hash route、request query deep link 和清理 query 的 helper。

#### 不能直接充当全局通知 source 的原因

- Approval request endpoint 是 Tenant-wide，但没有统一的 `dataset_id` 或 Workspace foreign-key 查询参数。
- 前端 `ApprovalFilters.workspaceId` 和 `query` 是首屏结果上的 local filter；组件明确说明服务端未提供关键词/工作空间过滤参数。它不能证明跨大量结果的完整过滤。
- Approval request 的 `status` 和 `my_approval` 是审批业务语义，不是通知 read/unread 语义。
- 审批操作需要 revision、资格和可能的执行授权；通用通知列表不应直接消费 ticket，也不应绕过 Approval Center 的 detail authority。
- Approval API 的 `EnterpriseScope.datasetId` 是可选的，而 Stage 21 `OperationsApiScope.datasetId` 是必填的；两者不能直接拼成一个没有明确定义的全局 scope。

**判定：** Approval inbox 是 Stage 22 的第二个 source adapter。MVP 只推荐投影服务端确认的 `pending_for_me` 事项，并通过已有 `/enterprise/approvals?request=...` deep link 回到 Approval authority。

### 3.4 WorkspaceScopeBar 的通知图标：明确的空壳

`D:\program_project\python_project\RAG4C\frontend\src\ui\enterprise\WorkspaceScopeBar.tsx` 的 Props 已经预留：

- `onOpenNotifications?: () => void`
- `onOpenAudit?: () => void`

当回调存在时，组件会分别渲染带有 `HistoryIcon` 和 `NotificationIcon` 的 TDesign `Button`，并提供：

- `aria-label="打开审计日志"`
- `aria-label="打开通知中心"`
- `title="审计日志"`
- `title="通知中心"`

但该入口存在以下事实：

- 当前组件没有 `Badge`、unread count、loading、unavailable、permission 或 stale 状态。
- 当前通知按钮只有 click callback，没有 notification id、scope、route 或 focus target contract。
- `D:\program_project\python_project\RAG4C\frontend\src\App.tsx` 的实际 `WorkspaceScopeBar` 调用没有传 `onOpenNotifications`，也没有传 `onOpenAudit`。
- `D:\program_project\python_project\RAG4C\frontend\src\App.a11y.test.tsx` 的测试明确断言当前页面不应出现“打开审计日志”和“打开通知中心”按钮，因为对应页面尚不存在。
- `D:\program_project\python_project\RAG4C\frontend\src\ui\enterprise\EnterprisePrimitives.test.tsx` 只证明在单独传入 handler 时组件可以渲染并调用按钮；它不是 App shell integration evidence。

**结论：** 这是可以复用的视觉/可访问性入口，不是已接通的全局通知能力。Stage 22 只有在服务端 capability、unread authority 和 route 都准备好后，才应把该按钮接到 App shell；不能先显示一个没有 count 或目标的 bell。

### 3.5 API/client：有请求基础设施，没有通知数据层

#### 可复用部分

`D:\program_project\python_project\RAG4C\frontend\src\api\client.ts` 提供：

- `request<T>`；
- base URL 读取；
- JSON content type；
- 15 秒默认 timeout；
- outer AbortSignal；
- `ApiError` 的 `timeout`、`aborted`、`http`、`network` 分类；
- HTTP response body 的安全 message 提取。

这些能力适合 Notification Center 的 read endpoint 和 receipt endpoint。

#### 现有缺口与冲突

- 没有全局 notification cache/store/provider。
- 没有 ETag/If-None-Match、long-poll、SSE、WebSocket、cursor checkpoint 或 server push contract。
- 没有统一的 cross-source pagination；每个 domain hook 都自己管理 AbortController、load state 和 stale fence。
- `enterpriseApprovalApi.ts`、`enterpriseAdminApi.ts`、`operationsApi.ts` 各自重复构造 Tenant/Authorization header 和 Idempotency-Key；类型分别使用 `EnterpriseScope`、`OperationsApiScope` 等相近但不相同的 scope。
- `request<T>` 本身不保存请求、不会生成通知身份，也不理解 read receipt。它只能作为底层 HTTP transport。
- Stage 21 operations hook 的 mutation chain 可以复用为 receipt mutation 的安全基础，但 Notification Center 的 mark-as-read 不应直接套用质量告警 mutation 权限。

**推荐：** Stage 22 只新增一个 notification-specific read/receipt adapter，复用 `request<T>`、`ApiError`、AbortSignal 和现有 scope projection；不要在研究后顺手做全仓 auth/client 重构。

### 3.6 Router / App shell：没有 Notification PageKey 或全局 provider

当前 App route：

- `D:\program_project\python_project\RAG4C\frontend\src\run\appRoute.ts` 的 `PageKey` 只有 overview、query、documents、taxonomy、sources、retrieval-lab、visualize、eval、monitor、consistency、enterprise、knowledge-bases、knowledge-base-workspace、config。
- 没有 `notifications` 或 `notification-center` PageKey。
- `parsePageLocation` 以路径第一段匹配 PageKey；未显式注册的 `/enterprise/notifications` 会先落到 `enterprise` 顶层。
- `EnterpriseAdminPage` 当前只对 OIDC callback、Workspace route、Approval route、邀请 token 做专门判断；没有 Notification route guard。
- 这意味着仅增加一个 URL，不会自动得到可达页面。按当前代码推断，`/enterprise/notifications` 若没有显式 route precedence，可能被当作企业管理默认页处理。

当前 Provider stack：

- `D:\program_project\python_project\RAG4C\frontend\src\AppProviders.tsx` 只有 TDesign ConfigProvider、ConnectionProvider、KnowledgeWorkspaceProvider、KnowledgeBaseDrawerCoordinatorProvider 和 RunMonitorProvider。
- 没有 `NotificationProvider`、unread count context、scope generation context 或 global overlay coordinator。
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-knowledge-base-shell\KnowledgeBaseResourceShell.tsx` 的资源 section 只有 Overview、Documents、Taxonomy、Sources、Governance、Releases；Stage 21 Quality Operations 是 Releases 内的局部 tab，不是 shell-level resource。

#### 路由复用与重复风险

Approval 已有 canonical 的 `D:\program_project\python_project\RAG4C\frontend\src\enterprise-approval\approvalRoute.ts`，但工作树中还存在：

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-admin\memberRoleApprovalNavigation.ts` 的重复 Approval Center navigation helper；
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-access\components\DatasetAclDisableDialog.tsx` 的 hard-coded `pushState` Approval navigation。

Stage 22 的 notification target route 不应再复制这种字符串拼接。Notification item 应返回受服务端 allow-list 约束的 source target，由前端统一交给既有 route helper 或新的单一 notification route helper。

### 3.7 TDesign：可复用，但必须避开现有兼容层陷阱

依赖：

- `D:\program_project\python_project\RAG4C\frontend\package.json` 使用 `tdesign-react` 和 `tdesign-icons-react`。
- 当前企业模块已经直接使用 TDesign `Tabs`、`PrimaryTable`、`Tag`、`Alert`、`Drawer`、`Dialog`、`Select`、`Textarea`、`Timeline`、`Steps`、`Button`、`Empty`、`Loading` 等。
- 本地 TDesign package 还提供 `Badge`，适合承载服务端返回的 unread count，但当前 WorkspaceScopeBar 没有使用它。
- 已有 `NotificationIcon`、`HistoryIcon`、`RefreshIcon`、`SecuredIcon`、`ChevronRightIcon` 等图标复用。

必须注意：

- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx` 是面向旧调用方的兼容 facade。
- 该文件当前声明 `canRenderTDesign() => false`，Button、Card、Alert、Table、Layout、Menu 等部分路径会走 app-owned `rag-*` fallback，而不是直接渲染 TDesign。
- 企业 Stage 19/20/21 模块则大量直接 import `tdesign-react`，并依赖真实 Drawer/Dialog/Tabs 的 focus 和 Portal 行为。

**推荐：** Stage 22 Notification Center 的核心 surface 直接使用 TDesign React，复用 Stage 21 的 focus/Drawer/empty/error 模式；不要把全局通知 Drawer 塞进 `ui/index.tsx` 的兼容 fallback，也不要用 Uiverse/Morphicons 替代企业级 Badge、Drawer、Table、Dialog。Uiverse/Morphicons 只能在 TDesign 没有对应能力时作为非核心装饰补充，当前并无必要。

## 4. 缺口分类

### P0：没有 Notification authority

当前没有可回答以下问题的服务端事实：

- 这条通知属于哪个 Tenant、Dataset 或 Workspace？
- 它的 source 是 Quality Alert、Approval 还是 Audit？
- 当前用户为什么能看到它？
- 当前用户是否已读？何时已读？
- 同一 source event 是否已经投影过？如何去重？
- 点击后应跳转到哪个已验证的 authority route？
- unread count 是否是服务端完整 count，而不是当前首屏数组长度？

没有这些 authority 时，红点、未读数和“全部通知”都是未经证明的 UI。

### P0：Bell 没有接线

通知 icon 的组件能力存在，但 App shell 没有 callback、route、provider 或数据依赖，当前测试还要求它隐藏。因此现状是视觉预留，不是半成品 Notification Center。

### P1：三类 source scope 不兼容

- Quality Alert：Tenant + Dataset + Release/Channel。
- Approval request：Tenant-wide，当前 API 没有显式 Dataset query。
- TenantAuditEvent：Tenant-wide，且没有 Dataset column。

前端聚合会造成跨 Dataset 错配、过滤不完整或误把 resource id 当 scope。

### P1：业务状态被误用为通知状态

- Alert `acknowledged` ≠ 当前用户 read。
- Approval `approved`/`pending` ≠ 当前用户 read。
- Audit occurred ≠ 需要行动。
- Job `completed`/`failed` ≠ 一定要投递给所有用户。

Stage 22 必须拥有独立 `unread/read` 投影和 receipt 语义。

### P1：分页、计数和刷新不一致

现有各 source 使用不同 cursor、limit、默认页大小和 query shape；Stage21 Alert/Job hook 还没有向上暴露完整 load-more。若客户端 fan-out，无法安全计算全局 unread count，也无法让一个 cursor 代表三种源的全序。

### P1：目标 route 不统一

Approval 有 route helper，但存在重复 hard-code；Quality Operations 依赖 Knowledge Base Releases 局部 section；Audit 没有独立 page route；App route 没有 Notification PageKey。Notification item 不能直接存任意 URL 或从 resource id 拼 URL。

### P2：审计 authority 重复但未标注

TenantAuditEvent 和 KnowledgeAuditEvent 都叫 audit，但 scope、表和权限不同。若 Notification Center展示“活动”，必须明确 source type 和 authority，否则读者无法判断证据来自哪张表。

### P2：TDesign 组件使用边界不统一

直接 TDesign 和 app-owned fallback 并存。若新 surface 混用，可能出现 Portal、focus、disabled、aria 和主题行为不一致；Stage 22 应把 Notification Center 限定在直接 TDesign 企业模块内。

## 5. 推荐 Stage 22 信息架构

### 5.1 产品层级

推荐采用两层体验，保持腾讯企业控制台式的“全局上下文 → 当前操作面 → 详情 authority”层级：

```text
App shell
└── WorkspaceScopeBar
    └── Notification bell + server unread count
        ├── Quick Notification Drawer（最近未读，最多一页安全投影）
        └── 查看全部通知
            └── /enterprise/notifications
                ├── Needs attention
                ├── Approval tasks
                └── Quality alerts
```

审计不作为第三个默认通知 tab；从通知详情或快速面板提供“查看审计事实” handoff，目标仍是管理审计或知识治理审计 authority。

### 5.2 Bell / Quick Drawer

Bell 只有在以下条件同时成立时才显示：

- Notification capability 为 `ready`；
- 当前 Tenant/actor scope 已验证；
- 服务端至少返回一次可用 unread summary；
- route 和 receipt mutation contract 已挂载。

展示规则：

- `Badge` 的 count 必须来自服务端 `unread_count`；不可用时显示无 count 的 disabled/unavailable 状态，不能显示 `0` 伪装健康。
- click 打开单一 TDesign Drawer 或等价的 TDesign Popup surface；不嵌套 Stage 21 Drawer。
- Quick Drawer 只展示安全摘要、source、scope、时间、read state 和 allow-listed handoff；不执行 Alert acknowledge/resolve，不消费 Approval ticket。
- “全部通知”使用 direct/hash route helper 进入完整页面，并保留 tenant/dataset/workspace 查询上下文。
- Scope 切换或 actor 切换时，Drawer 必须清空旧列表、abort 旧请求并等待新 scope generation，不能短暂展示上一个 Tenant 的 unread count。

### 5.3 Full Notification Center

推荐页面结构：

1. **Authority header**
   - Tenant；
   - 当前 Workspace/Dataset（若是已验证 dataset scope）；
   - 当前 actor；
   - generated_at；
   - 数据能力状态和 read-only 状态。
2. **Metric strip**
   - 未读总数；
   - Needs attention 数；
   - Approval tasks 数；
   - Quality alerts 数；
   - last synchronized at。
3. **状态筛选**
   - All；
   - Unread；
   - Needs attention；
   - Approval tasks；
   - Quality alerts。
4. **单一跨源列表**
   - 桌面用 TDesign `PrimaryTable` 或高密度列表；
   - 375px/280px 用优先级卡片；
   - 每行显示 source type、severity、safe title、scope、occurred_at、read state、target handoff。
5. **单一 Notification Detail Drawer**
   - Overview；
   - Source facts；
   - Authority；
   - Audit handoff；
   - Target action。
6. **真实状态面**
   - loading；
   - empty；
   - partial；
   - unavailable；
   - permission denied；
   - malformed item dropped with invalid count；
   - read receipt saving/error。

Notification Center 负责“发现、阅读、跳转”；Quality Operations Center 负责 Alert 业务生命周期；Approval Center 负责审批决定；Audit Surface 负责不可变审计浏览。四者不能因视觉上都像列表而合并 ownership。

### 5.4 推荐 source routing

#### `quality_alert`

- 只接收 Stage 21 服务端确认的 active quality authority 变化。
- 固定路由到拥有对应 Dataset read 权限、并满足质量运营可见性规则的 actor；Stage 22 MVP 不提供自定义 routing policy。
- 通知项引用 Alert id、Dataset、Release、Channel、Alert type、severity、source Observation digest 和 target route。
- acknowledge/resolve/suppress 仍 handoff 到 Quality Operations；Notification receipt 与 Alert lifecycle 分离。
- Alert source 无法重验证时，通知项进入 unavailable，不显示成健康或空列表。

#### `approval_pending_for_me`

- 只投影服务端 `pending_for_me` 且当前 actor 仍然 eligible 的请求。
- 目标 route 使用现有 Approval deep link：`/enterprise/approvals?request=<request_id>` 的 canonical helper。
- Notification Center 不保存或渲染 execution ticket，不代表批准已执行。
- request 过期、资格变化或 revision 冲突时，通知行应显示 stale/needs refresh，并回到 Approval Center，而不是在通知面板内直接操作。

#### `audit`

- MVP 不把 TenantAuditEvent 或 KnowledgeAuditEvent直接生成通知。
- 审计只作为通知详情中的证据链接，或在管理/治理页面中单独浏览。
- 如果未来需要 audit notification，必须先定义 action allow-list、受众、severity、dedupe 和 per-user receipt，不能按“每条审计事件一条通知”默认投递。

#### `recertification`

- MVP 不把每个 Job 状态独立投递给用户。
- Job 作为 Quality Alert/Quality Operations detail 的关联事实展示。
- 只有当后续明确规定受众、触发事件和 receipt 语义后，才考虑 `recertification_ready` 或 `recertification_failed` source type。

## 6. 最小可信数据与 API 范围

本节是 Stage 22 后续设计的目标 contract，不是本阶段的实现清单。

### 6.1 最小持久化 authority

如果产品要求真实 bell unread count，至少需要两类持久事实：

1. **Tenant notification envelope**
   - 记录 notification id、Tenant、明确的 scope type/scope id、source type、source id、source revision/digest、category、severity、safe title/code、target route code、occurred_at、dedupe identity 和 lifecycle/expiry；
   - Dataset-scoped source 必须显式带 Tenant-leading Dataset 关系；不能仅通过 `resource_id` 猜 scope；
   - 不保存 raw query、request body、comment、ticket、credential、原始 Idempotency-Key 或任意未验证的 URL；
   - 不能复制 Stage21 Alert 或 Approval request 的完整业务行，只保存可重验证的 source reference 和安全显示 projection。
2. **Per-user notification receipt**
   - 以 Tenant + notification + account 形成唯一身份；
   - 至少表达 `unread`/`read` 和 `read_at`；
   - mark-as-read 是用户级 receipt mutation，不改变 Alert、Approval 或 Audit 业务状态；
   - receipt 必须 Tenant-safe、revision-safe、幂等，并在 scope/actor 变化时不能被复用到另一个用户。

不推荐 Stage 22 MVP 新增可配置 routing policy、邮件订阅、Webhook delivery、push delivery 或跨租户管理员收件箱。这些会引入新的受众、重试、合规和外部副作用，超过“最小可信 Notification Center”的边界。

### 6.2 最小服务端 API 形状

推荐一个服务端组合读取入口，而不是客户端 fan-out：

```text
GET /api/enterprise/notifications
```

建议 query contract：

- `cursor`；
- `limit`；
- `state=all|unread`；
- `source=quality_alert|approval_pending_for_me`；
- 可选且服务端验证的 `dataset_id`；
- 可选 severity/category filter。

建议 response 至少包含：

- `items`；
- `unread_count`；
- `next_cursor`；
- `generated_at`；
- `capability` 或 safe unavailable evidence；
- `invalid_item_count`；
- 每个 item 的 `id`、`source_type`、`source_id`、`scope`、`severity`、safe title/code、`occurred_at`、`read_state`、`target_route_code`、source revision/digest reference。

Receipt mutation 可以采用独立的用户级入口，例如：

```text
POST /api/enterprise/notifications/{notification_id}/read
```

它只改变 receipt，不调用 Alert/Approval lifecycle mutation。是否支持 mark-unread、dismiss 或批量操作应等到 receipt authority 和审计语义明确后再决定；MVP 不需要为了“看起来像企业产品”预先增加这些动作。

### 6.3 为什么不推荐客户端聚合

客户端同时请求：

- Stage 21 `/quality-alerts`；
- Approval `/approvals/requests?pending_for_me=true`；
- Tenant Audit `/audit-events`；

然后自行排序和计算 unread，会有以下不可接受的缺陷：

- 三个 endpoint 的 cursor 不能组成一个全局 cursor；
- approval 和 audit 没有统一 Dataset scope；
- source 业务状态会被误读成 read state；
- 首屏 limit 会让 unread count 不完整；
- 某一个 source 503 时，客户端难以证明整体 count 的可信度；
- 不同权限 source 可能被错误合并或暴露 resource metadata；
- 同一事件在 Alert、Audit、Approval 中可能出现多次，没有 server-owned dedupe identity；
- 每个页面 hook 会重复请求、重复维护 AbortController 和 stale fence。

## 7. Stage 22 最小可信范围

### 必须纳入

1. 一个 Tenant-safe 的服务端组合 Notification read authority。
2. 明确的 source allow-list：`quality_alert` 与 `approval_pending_for_me`。
3. 明确的 unread count 和 per-user read receipt；若 receipt 尚未实现，则不得显示 unread badge。
4. cursor/limit/partial/unavailable/permission/malformed item 的 fail-closed contract。
5. WorkspaceScopeBar bell 的真实 App shell 接线，但只在 capability 和 count authority ready 时展示。
6. direct/hash `/enterprise/notifications` route 与浏览器刷新、返回、deep link 行为。
7. Quality Alert 与 Approval 的安全 handoff：
   - Quality → Releases / Quality Operations；
   - Approval → `/enterprise/approvals?request=...`。
8. 一个直接使用 TDesign 的企业级 surface：Badge、Drawer、Tabs、PrimaryTable/卡片、Tag、Alert、Empty、Loading、Button、Tooltip。
9. Tenant isolation、actor eligibility、Dataset ACL、read receipt idempotency、stale scope generation、敏感字段 fail-closed 测试。
10. Playwright 至少覆盖 direct/hash、light/dark、1440/375/280、empty、partial、unavailable、read-only、scope switch、bell focus、Drawer Escape/focus return、deep link 和零敏感泄漏。

### 明确排除

- Email、SMS、Webhook、浏览器 Push、第三方通知服务。
- 可配置 routing rule、用户订阅偏好、通知渠道编排、升级策略和定时重试。
- 把每条 TenantAuditEvent 或 KnowledgeAuditEvent自动生成通知。
- 把每个 Recertification Job 自动生成独立通知。
- 在通知 Drawer 内直接 approve/reject、consume execution ticket、acknowledge/resolve Alert。
- 复制 Approval request、Quality Alert、TenantAuditEvent 的完整业务表作为第三套业务真相。
- 用本地数组长度伪造 unread count。
- 在未有 backend authority 时先显示红点、Badge 或“全部通知”页面。
- 修改 Stage 20 protected Retrieval Quality 实现或测试。

## 8. 受保护路径与本阶段修改边界

Stage 22 后续实现以及当前研究均必须保护以下路径：

```text
D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\**
D:\program_project\python_project\RAG4C\core\retrieval_experiment_runner.py
D:\program_project\python_project\RAG4C\server\retrieval_experiments_api.py
D:\program_project\python_project\RAG4C\tests\test_retrieval_experiment_runner.py
D:\program_project\python_project\RAG4C\tests\test_retrieval_experiments_api.py
```

此外，不能 reset、checkout、clean 或广泛格式化当前累计工作树。Stage 22 research-only 本轮不修改：

- `frontend/src/**` 任意代码；
- `server/**` 任意代码；
- `core/**` 任意代码；
- `models/**` 任意代码；
- `catalog_migrations/**`；
- tests、package、router、app shell 或 TDesign source。

本轮只写入本文档。

## 9. Stage 22 进入实现前的决策门

在批准任何实现任务前，必须先锁定以下决策：

1. Notification envelope 的 canonical identity 和 dedupe 规则。
2. Notification scope 的显式表达：Tenant、Dataset、Workspace 如何区分，不能从 resource id 推断。
3. `quality_alert` 的受众规则和 Dataset ACL 读取证明。
4. `approval_pending_for_me` 的 actor eligibility、过期和 revision 重新验证规则。
5. unread/read receipt 与 Alert acknowledged、Approval status 的严格分离。
6. `target_route_code` 的 allow-list 和 direct/hash route precedence。
7. `knowledge.read`、Dataset ACL、`knowledge.audit`、`knowledge.manage` 在列表、receipt、handoff、业务 mutation 中的权限边界。
8. 部分 source unavailable 时，`unread_count` 是整体 unavailable、部分可信还是带明确 confidence evidence；不能静默降为 0。
9. 是否必须新增两类持久表；如果不新增，则不能承诺跨会话 unread badge。
10. 只使用直接 TDesign 组件的 Notification Center 包边界，避免再次进入 `ui/index.tsx` compatibility fallback。

## 10. 最终研究判断

截至 **2026-08-29**：

- **Bell：** 已有 `NotificationIcon` 和可选 callback 的视觉预留，但 App shell 未接线，当前属于空壳。
- **Alert routing：** Stage 21 已有真实 Dataset 级 Alert authority 和生命周期，但没有用户受众、通知 receipt 或全局排序。
- **TenantAuditEvent：** 已有真实 Tenant 级审计事实和 API，但不是通知 source；与 `KnowledgeAuditEvent` 存在双 authority，必须保留 source label。
- **Approval inbox：** 已有真实 `pending_for_me` 任务中心和 deep link，但没有 read/unread receipt，且 Dataset/Workspace 过滤能力不足以直接作为全局聚合 source。
- **API/client/router/app shell：** 已有可靠 transport、Abort、错误和 direct/hash 路由模式，但没有通知数据层、provider、PageKey 或 server-composed endpoint。
- **TDesign：** 已有足够的企业级组件和 Stage21 可复用交互证据；新中心应直接使用 TDesign，不能把 Uiverse/Morphicons 或旧兼容 facade 当作核心 authority UI。

**Stage 22 最小可信产品不是“把 bell 点亮”，而是先建立可证明的通知 authority、独立 receipt 和 source-safe handoff；在这三项完成前，任何 Badge、红点、unread 数字或全局通知列表都应保持 unavailable/隐藏，而不是用空壳完成视觉验收。**
