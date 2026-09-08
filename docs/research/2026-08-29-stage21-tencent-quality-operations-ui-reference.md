# Stage 21 Tencent / TDesign Quality Operations Center UI Reference

**文件日期标签：** 2026-08-29（按任务指定路径）  
**证据窗口：** 已有 Playwright 运行记录覆盖 2026-08-28 UTC / 2026-08-29 Asia/Shanghai 的本地验收产物；Stage 19 Tencent 参考记录标注为 2026-08-28（Asia/Shanghai）。  
**状态：** Stage 21 UI research sidecar；信息架构与视觉契约，不是实现代码或后端 API 契约。  
**范围：** Knowledge Base workspace → Releases → Quality Operations。  
**生产边界：** 本文不执行 migration、scan、Certification、Waiver、Approval、Promotion、Rollback、Application pin、source sync、restore、delete 或外部发布；不修改前端代码。

---

## 1. 研究目标与设计判断

Stage 21 要补上的不是另一个统计 Dashboard，而是一个能够持续回答以下问题的企业运营工作面：

> 哪些 Release / Channel 的质量权威正在接近失效？当前证据是什么？下一步是确认告警、排队再认证，还是等待新的可验证事实？

核心设计判断：

- 沿用 Tencent/TDesign 的持久 Knowledge Base 上下文、资源分层和克制的控制台密度；
- 沿用 RAG4C 已有 Stage 19/20 的 Release、Manifest、Channel、Certification、Waiver 和审计权威，不在浏览器创造第二套事实；
- Quality Operations 是 **跨 Release / Channel 的运营读工作面**，不是 Retrieval Experiment runner、Application config editor 或发布控制面；
- 以一条 **SLO Horizon Rail** 作为唯一的结构性视觉记忆点：它表达时间风险和可操作的过滤，而不是装饰性 KPI 卡墙；
- 告警必须是可持久化的 Alert authority。没有完整的全局 Notification Center authority 之前，使用 Quality Operations 内的局部 Alert Inbox，不伪造全局通知能力；
- `unknown / stale / malformed / unavailable` 必须在 UI 上保持不可判定或阻断，不得被显示成健康、通过或“没有问题”。

---

## 2. 证据来源与可信边界

### 2.1 Tencent 官方页面与既有 Playwright 研究

本 sidecar 复用之前已经完成的真实浏览器研究，不复制腾讯源码、私有 markup、商标、图片或品牌资产。参考页面：

- [知识库召回测试](https://cloud.tencent.com/document/product/1759/135538)
- [应用评测](https://cloud.tencent.com/document/product/1759/104208)
- [应用发布概述](https://cloud.tencent.com/document/product/1759/104209)
- [Agent 应用工作流程](https://cloud.tencent.com/document/product/1759/122549)
- [产品更新说明](https://cloud.tencent.com/document/product/1759/104191)
- [什么是知识库](https://cloud.tencent.com/document/product/1759/123551)
- [知识库设置概览](https://cloud.tencent.com/document/product/1759/123554)

对应的本地研究与截图证据：

- `docs/research/2026-08-29-tencent-quality-certification-stage20-reference.md`
- `docs/research/2026-08-29-stage20-quality-certification-ui-reference.md`
- `docs/research/2026-08-28-tencent-knowledge-base-stage19-reference.md`
- `output/playwright/tencent-knowledge-base-stage19-2026-08-28/`

这些证据用于提炼信息架构、状态表达、历史与比较、异步操作和安全边界；它们不是腾讯产品的视觉或代码复制许可证。

### 2.2 当前 RAG4C Stage 19/20 浏览器证据

现有 Stage 20 受控 loopback Playwright 产物：

- `output/playwright/enterprise-release-quality-stage20/stage20-browser-result.json`
- `output/playwright/enterprise-release-quality-stage20/stage20-artifact-manifest.json`
- `output/playwright/enterprise-release-quality-stage20/`
- `docs/research/2026-08-29-enterprise-release-quality-stage20-visual-acceptance.md`

可直接复用的事实：

- `direct/hash × light/dark × 1440/375/280` 共 12 个矩阵场景；
- 6 个质量场景：`no_policy_unavailable`、`failed_certification`、`passed_certification`、`approval_required_waiver`、`valid_waiver`、`evidence_history`；
- 31 个 fresh PNG；
- `keyboard_focus_all_passed = true`；
- `all_no_horizontal_overflow = true`；
- console error、page error、unknown request、unexpected failed request 均为 0；
- source binding 在运行期间保持不变；
- fixture 只使用 loopback/in-memory authority；未执行生产动作；
- DOM、storage、console、结果 JSON 与截图中不显示 raw ticket、secret、Idempotency-Key、result body 或 judgment note。

Stage 21 不能把上述 Stage 20 的“当前 Release 详情验收”误称为“跨 Release 质量运营中心已完成”。它只是可靠的视觉与交互基线。

---

## 3. Tencent 模式的 UI 转译

### 3.1 Knowledge Base 是持久工作上下文

既有 Playwright accessibility snapshot 显示，Tencent 文档将 Knowledge Base 作为持续存在的产品上下文，资源被分为文档、问答、数据库、知识库设置、Agentic RAG 和召回测试等操作域。页面结构是：全局产品上下文 → 当前产品 → 操作指南 → Knowledge Base → 当前资源，而不是每个功能都变成孤立的营销页面。

对 RAG4C 的转译：

- 继续使用现有 Knowledge Base resource shell；
- Quality Operations 归属于 `Releases` 局部工作面；
- Tenant、Workspace、Dataset、Environment、Channel、Actor 必须在页面首屏保持可辨识；
- 不新建独立的“质量产品”主侧栏入口，不让运营页面丢失 Release 上下文。

### 3.2 召回测试与 live 配置分离

Tencent 召回测试的研究记录表明，测试时修改的检索/召回规则只作用于本次测试，和应用已有 serving 配置分离；同时存在输入问题、召回结果、历史记录、搜索和多配置对比等独立工作面。无命中时显示明确的无结果状态，而不是空白或演示结果。

对 Stage 21 的约束：

- Quality Operations 只读消费 Stage 20 的 Experiment / Judgment / Certification authority；
- 不在 SLO 页面加入召回参数编辑器；
- 不把“当前 Channel 正在服务”推断成“已经有质量证明”；
- 当证据没有返回时，使用 `事实不可用` / `需要重新核验`，不使用健康色或空白卡片；
- 如果后续需要打开实验详情，应跳转到既有 Retrieval Quality 工作面，而不是复制一个 runner。

### 3.3 评测是可复用任务与可比较历史

Tencent 应用评测研究强调可复用的评测集/任务、不同的评分方式、结果对比和历史查看。评分方式由服务端定义，客户端不应根据一个总分猜测所有业务质量。

对 Stage 21 的转译：

- Quality Operations 展示 Certification / Baseline 的权威摘要、revision、digest、观察时间和有效期；
- 不在客户端重新加权 BPS、milli-score、评测模式或 reviewer judgment；
- “当前观察”与“历史 Observation”分层：当前状态用于行动，历史用于解释“何时开始接近失效”；
- 复认证 Job 是持久运营任务，不是第二个评测执行器；完成后仍调用 Stage 20 的显式 Certification 流程。

### 3.4 发布与运营是分离的生命周期

Tencent 发布研究记录体现了“测试/评测 → 发布 → 发布历史/还原/运营观察”的分层。发布不是一次性弹窗，完成后要能在历史中继续追踪，版本与操作记录也要可回看。

对 Stage 21 的转译：

- Quality Operations 可以显示某个 Release 是否临近失去发布资格，但不直接完成 Promotion、Rollback 或 Pin；
- Alert、Scan Run、Observation、Recertification Job 都应有真实生命周期和可查历史；
- 长任务使用 `queued / running / completed / failed / cancelled` 等明确状态；
- 运营页面提供“查看 Release 详情”“查看 Certification”“打开审批请求”等 handoff，不把不同 authority 合并成一张不可解释的状态卡。

### 3.5 全局通知能力必须有真实 authority

现有 `WorkspaceScopeBar` 只有在收到 `onOpenNotifications` 时才渲染通知按钮，App accessibility 测试也明确允许在没有通知路由时隐藏入口。当前仓库没有可以证明 Stage 21 已拥有完整全局 Notification Center 的独立 authority。

因此 Stage 21 的边界是：

- 首先建设 Quality Operations 内的 **Alert Inbox**；
- Alert Inbox 是按 Tenant / Dataset / Channel 过滤的业务资源，不冒充全局站内信；
- 不显示伪造的全局未读数、浏览器通知、邮件发送状态或铃铛红点；
- 后续若建立真正的 Notification Center，再通过明确的通知投影消费 Alert，而不是反向让 UI 生成告警。

---

## 4. 当前 Stage 19/20 UI 基线检查

### 4.1 Stage 19 Release Control Plane

当前实现路径：

- `frontend/src/enterprise-knowledge-base-release/components/KnowledgeBaseReleaseCenter.tsx`
- `frontend/src/enterprise-knowledge-base-release/components/ReleaseDetailDrawer.tsx`
- `frontend/src/enterprise-knowledge-base-release/knowledge-base-release.css`

已验证的可复用结构：

1. **Release Center header**：使用 `RELEASE CONTROL PLANE / GOVERNED` kicker、Knowledge Base/Workspace 上下文和只读/控制面标签；
2. **Channel summary**：通过 `Configured / Candidate / Effective / Serving` 四格表达配置、候选、有效和服务中的 authority；
3. **真实状态行**：当前一致性、Readiness、Serving generation、Channel revision、历史 Release；
4. **Release history**：桌面使用 TDesign `PrimaryTable`，移动端切换为 Release cards；
5. **Release Detail Drawer**：复用单个右侧 Drawer，Tabs 为 `Manifest / Readiness / Certification / Impact / Audit`；
6. **延迟读取**：Impact 与 Audit 只在 Tab 被激活后加载，避免打开详情就请求大证据；
7. **操作与证据分离**：发布/回滚按钮留在 Release 详情，Certification 作为独立 Tab，不把所有动作挤进一张表。

当前视觉基线是浅灰画布、白色容器、蓝色主行动色、细边框、轻阴影、短 kicker、数据/ID 使用 monospace；这比大面积渐变、浮夸插画或统计卡墙更符合企业控制台。

### 4.2 Stage 20 Release Quality UI

当前实现路径：

- `frontend/src/enterprise-release-quality/components/QualityGateStrip.tsx`
- `frontend/src/enterprise-release-quality/components/ReleaseQualityPanel.tsx`
- `frontend/src/enterprise-release-quality/release-quality.css`
- `frontend/src/enterprise-release-quality/hooks/useReleaseQuality.ts`
- `frontend/src/enterprise-release-quality/model/qualityModel.ts`

可复用结构：

- `QualityGateStrip` 在 Release Center 首屏展示质量门禁；
- `ReleaseQualityPanel` 使用 `CERTIFICATION AUTHORITY / IMMUTABLE` kicker；
- 四段 authority rail 为 `Policy → Baseline → Evidence → Publish Gate`；
- 桌面端使用 `PrimaryTable` 显示 `Metric / Required / Observed / Verdict`；
- 移动端切换为带可见字段标签的 metric cards；
- Certification history 使用 append-only 语义、时间和 Policy/Baseline revision；
- Certification / Waiver 使用 TDesign `Dialog`，提交前展示当前 Policy、Channel 和 Release 事实；
- `unavailable`、`blocked`、`passed`、`waived` 等状态使用文本标签和颜色共同表达；
- Stage 20 的 no-policy、failed、passed、approval-required、valid-waiver、evidence-history 场景均有受控浏览器证据。

### 4.3 Stage 21 应继承的实现护栏

当前 UI 检查显示 Stage 19/20 已经具备良好的壳层、状态标签和响应式分层，但 Stage 21 不应简单复制组件后堆叠页面。必须明确以下护栏：

- 一个顶层 Drawer 或 Dialog 只由 TDesign 组件拥有 dialog 语义；不要在其内部再加一个重复 `role="dialog"`；
- Drawer / Dialog 必须 focus entry、Tab/Shift+Tab trap、Escape 隔离和 focus return；
- `QualityGate` 为 `unavailable` 时不得从全租户 Policy 列表或历史 Certification 第一条记录推断“当前 authority”；
- Mutation 返回 `unavailable` 不能被显示为“已更新”；
- 页面切换 Release、Channel 或 Tenant 时，旧请求/旧 mutation 结果不得写入新上下文；
- Alert、Observation、Scan Run、Job 读取失败与真实空列表必须使用不同的视觉和辅助技术状态；
- 质量状态不得只显示内部英文 enum，用户界面使用统一中文语义，英文只放在 debug/详情层；
- Stage 21 使用与 Stage 20 相同的 fail-closed projector 原则，不在浏览器重建 digest、scope 或有效期 authority。

---

## 5. Quality Operations Center 信息架构

### 5.1 入口与局部导航

保留现有 Knowledge Base 资源导航，在 `Releases` 内增加局部二级切换：

```text
Release Center | Quality Operations
```

推荐入口：

```text
/enterprise/knowledge-base?dataset={dataset_id}&section=releases&view=quality-operations
```

该 URL 只是信息架构建议。最终路由必须复用现有 workspace scope、Tenant、Workspace、Dataset、Actor 和 `readOnly` fencing；如果服务端没有验证完整 scope，页面必须显示不可用并禁用 mutation。

### 5.2 页面单一任务

Quality Operations 首屏只围绕三个动作组织：

1. 找到即将失去质量资格的 Release / Channel；
2. 打开其证据、告警和生命周期，理解阻断原因；
3. 在权限与 authority 完整时确认 Alert、排队再认证 Job，或跳转到既有 Certification / Approval 工作面。

不在首屏同时放置“生成候选、发布、回滚、Pin、修改实验、创建 Waiver”等跨域操作。

### 5.3 桌面信息骨架

```text
┌ Quality Operations / SLO GOVERNED ─────────────────────────────┐
│ Knowledge Base · Workspace · Dataset · Environment · read-only │
│ Last scan 2026-08-28 14:20 UTC · next scan · policy R3        │
└───────────────────────────────────────────────────────────────┘

┌ SLO HORIZON RAIL ──────────────────────────────────────────────┐
│ EXPIRED │ < 24 HOURS │ ≤ 7 DAYS │ ≤ 30 DAYS │ HEALTHY         │
│    2    │     1      │    4     │    7      │    28           │
└───────────────────────────────────────────────────────────────┘

┌ Alert Inbox ───────────────┐  ┌ Scan / Job status ────────────┐
│ Open  · Critical · Warning │  │ Last run · Next run · Queue    │
│ Alert rows with filters    │  │ Running / Failed / Pending     │
└────────────────────────────┘  └────────────────────────────────┘

┌ At-risk Release Quality Authority ────────────────────────────┐
│ Release · Channel · Gate · Certification · Expires · Alert   │
│ Recertification · Owner · Action                              │
└───────────────────────────────────────────────────────────────┘
```

视觉重点是“风险时间地平线 + 一张可行动的权威表”，不是 8～12 个孤立 KPI 卡片。

### 5.4 内容层级

从上到下保持以下阅读顺序：

```text
当前上下文
→ 质量风险时间窗口
→ Alert Inbox / 扫描与 Job 运行态
→ At-risk Release 表
→ 选中 Release 的 Drawer / Timeline
```

标题、kicker 和辅助文案应让用户知道自己正在看的是：

- `Quality Operations`：跨对象的运营观察；
- `Release Quality Authority`：当前行的 server-owned 事实；
- `Recertification Job`：等待人工继续的持久任务；
- `Alert Inbox`：已持久化的告警生命周期。

---

## 6. SLO Horizon Rail 设计

### 6.1 视觉角色

SLO Horizon Rail 是 Stage 21 的唯一视觉记忆点。它把 Certification / Waiver 的时间有效性翻译成一条从失效到健康的水平轨道：

```text
EXPIRED ┃ < 24 HOURS ┃ ≤ 7 DAYS ┃ ≤ 30 DAYS ┃ HEALTHY
```

它不是纯装饰，也不是客户端动态倒计时：

- 每段是一个可聚焦、可点击的过滤按钮；
- 数字由服务端 Observation / Summary authority 返回；
- 点击后同步更新 Alert Inbox 与 At-risk 表的过滤器；
- 当前过滤条件有明显的 selected 状态和可清除入口；
- `authority unavailable` 不计入 Healthy，也不被吞到 Expired；它在轨道旁显示为独立的不可用告警。

### 6.2 时间语义

默认阈值可由 Tenant SLO Policy 定义，UI 只展示服务端返回的 bucket label 和 boundary：

- **已过期**：Certification 或 Waiver 已经超过有效时间；
- **24 小时内**：进入 critical horizon，需要优先处理；
- **7 天内**：warning horizon，需要排队或确认计划；
- **30 天内**：watch horizon，继续观察，不制造紧急感；
- **健康**：当前 Gate、Manifest、Certification/Waiver 和有效期均可证明；
- **事实不可用**：authority、scan 或 policy 无法验证，单独显示为 fail-closed 状态。

时间统一用 UTC 持久化、整数分钟计算，前端不根据本地时钟自行改变服务端严重度。可以展示用户本地时间作为辅助，但必须保留明确时区。

### 6.3 状态颜色与可访问性

推荐继续使用 TDesign 语义色：

- 品牌蓝 `#0052D9`：选中、链接、普通操作；
- 成功绿 `#2BA471`：健康、已通过；
- 橙色 `#ED7B2F`：watch/warning、豁免临近到期；
- 红色 `#D54941`：critical、已过期、已阻断；
- 中性灰：未选择、空列表、尚未扫描；
- 深色模式使用 TDesign dark tokens，不直接把浅色值复制到黑底。

颜色不是唯一信号。每段必须同时有：

- 可读文字；
- 数字；
- `aria-label`，例如“24 小时内，4 个质量权威”；
- selected / focus / disabled 的非颜色差异。

### 6.4 Loading、空、异常状态

- 初次扫描未返回：轨道显示 skeleton/`正在读取质量运营摘要`，不显示 0；
- 当前 Tenant 无策略：显示 `未配置 SLO 策略`，不能显示 Healthy；
- 真实无风险对象：显示 `当前没有需要处理的质量权威`，并说明最后一次成功扫描时间；
- 扫描失败：显示 `质量扫描不可用`、安全错误码和重试按钮，不沿用上一次数据为“当前健康”；
- 部分对象 invalid：显示有效数量与 invalid count，不能静默丢掉 invalid rows。

---

## 7. Alert Inbox 信息架构

### 7.1 告警对象

Alert Inbox 只展示持久化 Alert authority：

- `certification_expiring`
- `certification_expired`
- `certification_stale`
- `waiver_expiring`
- `waiver_expired`
- `quality_gate_blocked`
- `quality_authority_unavailable`

每条告警至少需要用户可理解的投影：

```text
严重度 · 告警类型 · Release · Channel · 首次发现 · 最近观察
当前状态 · 来源 Observation · 下一步
```

不要把 query、result body、reviewer note、raw ticket、secret 或 Idempotency-Key 放进 Inbox。

### 7.2 生命周期

```text
open → acknowledged → resolved
  └──────────────→ suppressed（仅在有明确有效期时）
```

- `open`：服务端观察到可行动风险；
- `acknowledged`：有权限的操作者确认已接手，必须记录 actor、时间和安全 comment；
- `resolved`：新的 Observation 已证明风险消失，或操作者完成了明确的修复动作；
- `suppressed`：仅表示在一个有限窗口内暂缓提醒，不表示 Gate 通过；
- 每次状态变化都需要 revision fence 和 append-only event；
- UI 不因点击“已读”就把告警变成 resolved。

### 7.3 Inbox 过滤与行动

桌面端过滤器：

```text
Channel | Severity | Alert type | Alert status | Release | 时间范围 | 搜索
```

建议：

- 搜索默认匹配 Release ID、Channel name、alert ID 的安全投影；
- 时间范围使用 TDesign `DateRangePicker`，显示服务端时区说明；
- 过滤器状态可复制到 URL，但 URL 不携带 raw ticket、token 或 Idempotency-Key；
- row action 只提供与当前状态匹配的动作：确认、解决、查看 Release、排队再认证；
- unavailable 告警不能直接从 Inbox 申请 Waiver 或模拟通过。

---

## 8. 桌面端 At-risk Release 表

### 8.1 列顺序

从最能支持决策的信息开始：

1. `Release / Channel`：Release number、safe short ID、Channel name；
2. `Gate`：中文状态、reason 摘要、risk tier；
3. `Certification`：认证状态、Policy/Baseline revision；
4. `Expires`：认证或豁免有效至、剩余时间 bucket；
5. `Alert`：最高严重度、open/acknowledged；
6. `Recertification`：无 Job、pending、running、failed、ready to certify、completed；
7. `Last observed`：观察时间与来源 Scan Run；
8. `Action`：查看详情、确认告警、排队再认证等最小操作集。

不要把 digest 放在主列抢占阅读空间。digest 在 Drawer 的 authority facts 中以安全缩略显示，并提供复制按钮时只复制 digest，不复制敏感上下文。

### 8.2 行视觉

- 行首使用状态 Tag + 中文状态，不只用颜色；
- `critical` 行使用极细红色边线或浅色背景提示，不使用大红色整行；
- `unavailable` 行使用中性/危险提示和明确原因，不伪装成过期；
- Release number 是主层级，ID、revision、digest 是 monospace 次级层级；
- Owner / actor 不在没有 authority 时猜测；
- Action 采用 TDesign Button / Dropdown，避免一行塞入 5 个同等级蓝色按钮；
- 表格使用真实 `<th>` / scope、稳定 row key 和可访问的行操作名称；
- 分页采用 opaque cursor，不把 cursor 原文展示给用户。

### 8.3 表格状态

- `loading`：TDesign `Loading` 或表格 loading，不清空已有 context header；
- `empty`：TDesign `Empty`，说明当前筛选条件和下一步；
- `error`：TDesign `Alert` / `PageState`，说明读取失败和安全重试；
- `invalid rows`：显示 `N 条事实无法核验`，允许打开安全诊断，不显示原始 JSON；
- `read-only`：不显示会误导用户的“保存/执行”主按钮，查看/复制/过滤仍可用。

---

## 9. 375px / 280px 响应式设计

### 9.1 375px：风险卡片优先

375px 不压缩桌面宽表，而是使用明确的风险 ledger card：

```text
┌ Release 42                 [已阻断]
│ UAT 验证 · Channel R2
│ Certification：未通过 · Policy R3
│ 有效期：2026-08-29 11:00（UTC）
│ Alert：认证即将过期 · Critical
│ Recertification：待排队
│ [查看详情]                 [排队再认证]
└───────────────────────────┘
```

要求：

- Release、Channel、Gate、Certification、Expiry、Alert、Job status 和下一步都必须可见；
- 状态用中文 Tag + 文本；
- ID 可以安全换行或缩略，但不能遮蔽对象身份；
- `查看详情` 为主行动，其他动作按状态显示；
- 过滤器在卡片上方按两列/单列堆叠，label 不隐藏；
- Alert Inbox 采用同样的 cards，不把移动端变成横向滚动表格。

### 9.2 280px：单列、全宽动作

280px 是压力测试，不是“把 1440px 缩小”：

- 页面内容单列；
- Horizon Rail 可变成垂直 bucket list 或纵向 segmented control，但仍保留顺序、数字和可聚焦语义；
- Release card 的事实字段逐行排列；
- Action buttons 全宽；
- Drawer body 不产生页面级横向溢出；
- digest、Release ID、时间允许换行；
- 不把关键字段放进 tooltip 才能看到；
- Tabs 可以在 Drawer 内水平滚动，但滚动区域自身必须有清晰 focus 和可访问名称；
- 不使用负 margin 或固定 min-width 把内容撑出 viewport。

### 9.3 与现有 Stage 19/20 响应式模式的关系

现有 Release Center 已在 `max-width: 768px` 隐藏桌面表、显示 cards，在 `max-width: 280px` 将 summary/status/card meta 转为单列，并对 Drawer body 禁止横向溢出。Stage 21 应复用这一断点思路和 token，不另造一套响应式系统。

---

## 10. Detail Drawer、Timeline 与 Dialogs

### 10.1 单 Drawer 原则

Quality Operations 从表格行打开详情时，应复用既有 Release Detail 的单一顶层 Drawer 语义；不要在 Release Detail Drawer 之上再叠加一个第二 Drawer。

推荐的 Stage 21 局部 Tabs：

```text
Overview | Timeline | Certification | Alert | Recertification
```

如果用户从 Release Center 已经打开同一 Release，Quality Operations 应切换同一个 Drawer 的状态，而不是创建两个相互遮挡的详情层。既有 `Manifest / Readiness / Certification / Impact / Audit` 仍属于 Release facts；Stage 21 tabs 属于运营 facts，两者通过明确的 deep link 或同一 Drawer 的上下文切换连接。

### 10.2 Overview

Overview 首屏展示：

- Release number / safe ID；
- Channel、risk tier、Channel revision；
- Gate state 与安全 reason；
- Certification / Waiver 的 ID、digest 缩略、valid until/expiry；
- 当前 SLO bucket 与 open Alert 数；
- 最近一次 Observation 时间、Scan Run 状态；
- 当前 Recertification Job 状态（若存在）。

不展示：

- query 原文；
- result body/content；
- judgment note；
- raw approval ticket；
- raw Idempotency-Key；
- 未经 projector 验证的 arbitrary JSON。

### 10.3 Timeline

Timeline 只显示服务端已有的事件或 Observation 投影，示例顺序：

```text
Observed
→ Warning opened
→ Alert acknowledged
→ Recertification queued
→ Evidence became ready
→ Certification completed
→ Alert resolved
```

每个节点需要：

- 时间（明确 UTC 或用户时区）；
- event/observation 中文名称；
- actor 或 system actor；
- safe object reference；
- 当前节点是否为 server-confirmed；
- 对应的 Revision / digest 摘要。

没有事件时显示 `尚无运营时间线`，不能根据当前状态虚构历史节点。Timeline 适合使用 TDesign `Timeline`；当需要明确“等待 → 运行 → 完成”阶段时，可在 Job 详情中使用 `Steps`，两者不要重复播报同一生命周期。

### 10.4 Alert Tab

Alert Tab 展示：

- 当前 Alert status、severity、type；
- source Observation；
- acknowledged/resolved actor、time、safe comment；
- suppression deadline（如有）；
- 当前可用 action。

确认与解决动作必须使用 TDesign `Dialog`：

- 显式说明这是状态变更，不是“已读”；
- Comment 必填/选填由服务端策略定义；
- 提交前展示 tenant、Release、Channel、Alert revision；
- 使用 Idempotency-Key，但从 UI 文案、DOM、日志和结果投影中隐藏；
- 关闭后 focus 回到原触发按钮；
- 如果 authority 变 stale，Dialog 关闭并显示安全冲突状态，不能成功 toast。

### 10.5 Recertification Tab 与 Dialog

Recertification Tab 展示：

- Job trigger；
- Job status、attempt、next attempt；
- expected Manifest/evidence digest（安全缩略）；
- expected Channel / Policy revision；
- last safe error；
- result Certification ID（完成后）；
- created/claimed/completed actor and time。

“排队再认证” Dialog 最小字段：

- Release / Channel context（只读）；
- Baseline / Policy revision（只读或从服务端候选中选择）；
- trigger reason（服务端枚举）；
- operator comment（安全长度限制）；
- expected authority snapshot；
- 提交按钮 `排队再认证`，不是 `立即认证`。

Job 只进入持久队列，不自动修改 Judgment、不自动发 Approval、不自动 Promotion。`ready_to_certify` 之后仍需要明确的 Certification 操作，并重新验证所有 digest/revision。

### 10.6 Certification Tab

Stage 20 已有 `Policy → Baseline → Evidence → Publish Gate` rail；Stage 21 在运营 Drawer 中优先复用这一投影，不复制第二套指标计算。新增的信息只包括：

- 当前 Certification valid until；
- 从哪个 Observation 观察到接近失效；
- 当前是否有 stale/blocked Alert；
- 对应 Recertification Job；
- “打开 Release Certification 详情”的 handoff。

---

## 11. Read-only、Keyboard、Dark Mode 与 Motion

### 11.1 Read-only contract

页面应支持明确的 `readOnly` / scope-not-verified 状态：

- 可以浏览、过滤、查看 Drawer、复制安全 ID/digest、打开既有详情；
- 不能 acknowledge、resolve、suppress、queue Job 或修改 SLO Policy；
- mutation buttons 必须是真 disabled，不只是透明度降低；
- 不展示“已保存”“已更新”等成功文案；
- read-only 表头或 context badge 使用 `只读事实`，同时说明原因；
- 后端返回 `readOnly` 或 authority unavailable 时，前端不通过本地猜测放开按钮。

### 11.2 Keyboard 与辅助技术

- Horizon Rail 每个 bucket 是按钮，支持 Tab、Enter、Space、明确 selected 状态；
- Filter Select/Input 有可见 label，不能让不可操作外层 div 充当额外 combobox；
- Tabs 使用 TDesign 语义并支持 ArrowLeft/ArrowRight/Home/End、roving tabindex、`aria-controls` 与 `tabpanel`；
- 表格使用真实 header/scope，行操作 accessible name 包含 Release 与 Alert/Gate；
- Alert Inbox 的 open/acknowledged/resolved 变化使用 `role="status" aria-live="polite"`；错误使用 `role="alert"`；
- Drawer 使用真实单一 dialog 语义、`aria-modal`、focus trap、Escape 和 focus return；
- Dialog 不冒泡 Escape 到父 Drawer；关闭 Dialog 后父 Drawer 仍保持打开；
- keyboard-only 用户能够从 Rail → Inbox → 表格行 → Drawer → Timeline → action 完成完整浏览；
- reduced-motion 下状态不能只靠动画闪烁或倒计时表达。

### 11.3 Dark mode

继续使用现有 TDesign dark bridge 和 CSS custom properties：

- 深色画布、稍亮容器、清晰边界；
- 通过/警告/危险 Tag 保持文本与对比度；
- 时间轨道不要只用高饱和红/橙大面积铺底；
- code/digest 使用独立的深色 code surface；
- `html[data-theme="dark"]` 设置 `color-scheme: dark`；
- `meta theme-color` 与主题同步，不让浏览器外壳永远是白色；
- focus ring 在 dark mode 仍可见；
- `prefers-reduced-motion: reduce` 禁用非必要过渡和装饰动画。

---

## 12. TDesign 组件与图标清单

仓库当前 `frontend/package.json` 使用：

- `tdesign-react` `^1.18.2`；
- `tdesign-icons-react` `^0.6.10`。

已通过当前安装包导出检查、且适合 Stage 21 的组件：

### 12.1 页面与容器

- `Card`：SLO rail、Alert Inbox、scan/job summary、mobile risk card；
- `Space`：操作间距，避免手写不一致 gap；
- `Descriptions`：Drawer authority facts；
- `Alert`：不可用、读取失败、revision conflict、权限提示；
- `Empty`：真实空列表和无风险结果；
- `Loading`：初次读取、扫描/Job 投影加载；
- `Typography`：标题、辅助说明和安全截断文案；

### 12.2 数据与过滤

- `PrimaryTable`：桌面 At-risk 表和 Alert Inbox 表；
- `Pagination`：opaque cursor / server page projection；
- `Tag`：Gate、severity、alert status、Job status、read-only；
- `Select`：Channel、severity、alert type、Job status；
- `Input`：Release/Channel/Alert 搜索；
- `DatePicker` / `DateRangePicker`：观察时间范围；
- `Dropdown`：行操作的低频动作；
- `Tooltip`：仅补充说明，不承载关键字段；
- `Badge`：仅在服务端确实返回未读/待处理投影时使用，不能自行计数；

### 12.3 生命周期与操作

- `Tabs`：Quality Operations 局部导航与 Drawer 局部 Tabs；
- `Timeline`：Observation/Alert/Job 生命周期；
- `Steps`：Recertification Job 的阶段投影；
- `Drawer`：单一顶层详情面；
- `Dialog`：acknowledge、resolve、suppress、queue Job；
- `Button`：查看、过滤、刷新、确认、解决、排队；
- `Form`：Dialog 字段和校验；
- `InputNumber`：只在 Policy 编辑面且服务端允许时使用；Quality Operations 默认只读，不放数字输入；
- `Checkbox`：明确的筛选/确认项，不能用来代替状态 Tag。

### 12.4 TDesign Icons

推荐以图标辅助文本，不让图标独立承担状态语义：

- 状态：`CheckCircleIcon`、`ErrorCircleIcon`、`InfoCircleIcon`、`TimeIcon`、`SecuredIcon`、`LockOnIcon`；
- 运营：`NotificationIcon`、`HistoryIcon`、`RefreshIcon`、`TaskCheckedIcon`、`ChartLineIcon`；
- 导航：`ChevronRightIcon`、`ChevronDownIcon`、`ArrowRightIcon`、`CloseIcon`；
- 操作：`SearchIcon`、`FilterIcon`、`LinkIcon`、`BrowseIcon`、`CloudDownloadIcon`、`PlayCircleIcon`、`PauseCircleIcon`、`EditIcon`、`MoreIcon`；
- 复制/安全事实可复用仓库已有 `CopyIcon`，但复制结果必须是安全 ID/digest projection。

Uiverse / Morphicons 只在 TDesign 缺少必要的非核心装饰时考虑，并且：

- 不替代 TDesign 的 Button、Tag、Dialog、Drawer、Table、Tabs 或状态图标；
- 不使用会改变焦点顺序、增加不可访问 SVG、遮挡文本或引入品牌误读的装饰；
- Stage 21 默认不需要第三方装饰组件，企业可信度来自信息层级、状态可证明和留白纪律。

---

## 13. 视觉 Token 与写作规范

### 13.1 Palette

继续使用既有 TDesign 语义 token，建议在 Quality Operations 层只声明语义变量，不在组件内散落 hex：

```text
Authority Blue     #0052D9
Operational Ink    #17233D
Verified Green     #2BA471
Expiry Amber       #ED7B2F
Critical Red       #D54941
Console Surface    #F5F7FA
Container          #FFFFFF
Border             #DFE6F0
Secondary Text     #53657D
```

Dark mode 由 TDesign dark token 映射，不直接反转颜色。圆角继续以 6～10px 为主，阴影保持 1px/2px 的轻层级；Quality Operations 不需要玻璃拟态或大面积渐变。

### 13.2 Typography

- 标题/正文：现有系统 UI stack、PingFang SC、Microsoft YaHei、Inter fallback；
- Kicker：10px、较高字重、letter spacing，表达操作域而不是装饰；
- ID、revision、digest、timestamp：现有 `--font-mono`，使用 tabular numerals；
- 说明文字：12～13px，保持 line-height，避免为了塞进卡片而使用 9px 正文；
- 状态文案使用主动、具体的中文：`已阻断`、`待确认`、`正在扫描`、`事实不可用`、`排队再认证`；
- 内部 enum 仅在详情的技术事实层显示，不能成为主标题。

### 13.3 文案原则

- `刷新扫描`，不要写“执行一下”；
- `查看 Release`，不要写“进入”；
- `确认告警`，不要写“已读”；
- `解决告警`，不要写“关闭”；
- `排队再认证`，不要写“立即认证”；
- `质量事实不可用`，不要写“暂无数据”掩盖 authority failure；
- `没有需要处理的质量权威`，只在 server-confirmed empty 时使用；
- error 文案说明发生了什么、下一步是什么，不暴露内部 stack、ticket、token 或 raw JSON。

---

## 14. Stage 21 状态与交互矩阵

Quality Operations 至少要有以下可见状态，且每一种都是真实 authority 的投影：

- **初次读取**：Rail、Inbox、表格显示 loading，保留上下文标题；
- **无 SLO Policy**：显示未配置策略；不默认 Healthy，也不自动生成 Job；
- **Healthy**：当前 Gate、Manifest、Certification/Waiver、有效期均可证明；
- **Certification warning**：进入 30d/7d/24h bucket，并可打开来源 Observation；
- **Certification expired**：critical/expired，Alert open；不能当成 blocked 的同义词；
- **Certification stale**：明确 `证据已过期，需要重新核验`；不能只显示时间过期；
- **Waiver expiring/expired**：显示审批豁免及 expiry；过期后不保留 waived 视觉；
- **Gate blocked**：显示阻断 reason、当前 Certification/Policy 事实和可行动 Job；不自动申请 Waiver；
- **Authority unavailable**：显示无法验证的范围和重试；所有绕过性 mutation disabled；
- **Scan running**：显示真实 Scan Run 状态、planned/claimed/next attempt；不将上次 completed 复制为当前结果；
- **Job pending/claimed/running**：显示队列和 claim 信息；不显示“认证成功”；
- **Job ready_to_certify**：显示需要操作者确认的下一步；不自动执行 Certification；
- **Job failed**：显示 safe error、attempt、next retry；允许重新排队的前提由服务端返回；
- **Alert acknowledged**：显示接手人和时间；Gate 仍保持原状态；
- **Alert resolved**：必须有新的 Observation 或明确修复事实；不能仅因点击按钮而 resolved；
- **真实空列表**：用 Empty 解释当前筛选范围和下一步；
- **读取异常**：用 Alert/PageState 与空列表分离；不能把 error 变成 empty。

---

## 15. Playwright 验收建议

Stage 21 实现后，继续使用 Stage 19/20 的 source-bound controlled browser 方法。建议产物目录：

```text
output/playwright/enterprise-release-quality-operations-stage21/
```

### 15.1 基础矩阵

```text
direct / hash
× light / dark
× 1440×900 / 375×812 / 280×720
= 12 个基础场景
```

### 15.2 业务场景

至少覆盖：

- `no_slo_policy`：未配置策略；
- `healthy_authority`：健康且未接近失效；
- `certification_30d_watch`；
- `certification_7d_warning`；
- `certification_24h_critical`；
- `certification_expired`；
- `stale_certification`；
- `waiver_expiring`；
- `quality_gate_blocked`；
- `quality_authority_unavailable`；
- `scan_running`；
- `recertification_pending` / `ready_to_certify` / `failed`；
- Alert `open` / `acknowledged` / `resolved`；
- 真空列表与读取失败。

### 15.3 浏览器门禁

- Rail、Alert Inbox、At-risk 表和 Drawer 在 1440px 都有可见 bounding rect；
- 375/280 使用 cards/单列布局，不出现页面级横向 overflow；
- Rail filter 可通过键盘操作，selected 状态可被辅助技术读取；
- Drawer/Dialogs 的 focus entry、Tab/Shift+Tab、Escape、focus return 通过；
- read-only 流没有 acknowledge/resolve/queue Job 的请求；
- mutation 流只连接 loopback controlled API，Idempotency-Key 不出现在 DOM、storage、console、截图或结果 JSON；
- 0 console error、0 page error、0 unknown request、0 unexpected failed request；
- 无 raw query、result body、judgment note、ticket、secret、credential URL；
- empty/error/unavailable 不被误判成 Healthy；
- artifact manifest 绑定当前 source tree SHA、lockfile hash 和 fixture revision；
- 源码、依赖或 fixture 变化后必须重新生成 fresh artifacts；旧截图不能替代当前证据。

---

## 16. 明确不做的事情

- 不新增主侧栏“质量运营”孤立资源；
- 不复制腾讯源码、私有 markup、品牌 logo 或专属视觉资产；
- 不把 Quality Operations 变成 Retrieval Experiment runner；
- 不在浏览器重算 Certification、Waiver、digest、scope 或 SLO severity；
- 不把 Alert Inbox 冒充全局 Notification Center；
- 不在 Stage 21 直接 Capture、Promote、Rollback、Pin 或执行 Approval；
- 不自动创建伪造 Baseline、Certification 或 Waiver；
- 不修改 protected Retrieval Quality paths；
- 不将 stale/unavailable/invalid authority 显示为健康；
- 不保存或展示 query、result body、judgment note、raw ticket、secret 或 Idempotency-Key；
- 不在 280px 以固定宽表或隐藏字段换取“看起来没有溢出”。

---

## 17. 最终设计结论

Stage 21 的企业级视觉不应来自更多装饰，而应来自更清晰的运营因果链：

```text
SLO Policy
→ Scan Run
→ Observation
→ Alert Inbox
→ Recertification Job
→ Stage 20 Certification
→ Release Quality Gate
```

Tencent 提供了“持久资源上下文、测试与 live config 分离、评测历史、发布运营分层”的信息架构参考；RAG4C 的差异化应体现在：

- Release / Manifest 是可审计的部署对象；
- Certification / Waiver 是带 revision、digest 和有效期的权威事实；
- Alert / Observation / Job 是可重放的运营事实；
- unknown、stale、malformed 一律 fail closed；
- TDesign 负责可信的企业控件，Horizon Rail 负责 Stage 21 的唯一记忆点；
- 在真实 Notification Center authority 出现前，Quality Operations 内的 Alert Inbox 是唯一诚实的通知边界。

因此，Stage 21 UI 应在现有 Release Center 上继续向“可持续运营”演进，而不是另起炉灶做一张漂亮但无法证明事实来源的监控大屏。
