# Stage 20 Quality Certification UI Reference — Tencent / TDesign

**日期：** 2026-08-28  
**状态：** Stage 20 design sidecar；仅设计，不是实现契约  
**适用范围：** Knowledge Base workspace → Releases → Certification  
**生产边界：** 本 sidecar 不执行 migration、backfill、Release capture、promotion、rollback、Application pin、Approval execution、source sync、restore、delete 或外部发布。

## 1. 目的与设计结论

Stage 20 的单一任务是让操作者能够回答一个可审计的问题：

> 这个不可变 Knowledge Base Release，是否已经用足够新、足够完整、可追溯的质量证据通过发布门？

Certification 不是第二个检索实验运行器，也不是 Application 配置编辑器。它是 **Release-linked read workspace**：固定绑定一个 `release_id + manifest_digest + dataset_id + channel_id`，汇总已有检索证据、可复用的 Application evaluation 任务、Online testing 结果、发布门和正式豁免记录。

推荐结论：

- 保留 Stage 19 的主资源导航，Certification 只作为 `Releases` 内部的局部工作面，不增加主侧栏入口；
- 保留 Stage 19 的 `Configured / Candidate / Effective / Serving` 权威分层，Certification 只认证 Candidate 或指定的不可变 Release，不认证“当前配置”这个模糊对象；
- 使用 TDesign 的 Tabs、PrimaryTable、Descriptions、Tag、Alert、Drawer、Dialog、Select、Loading、Empty 和 Icons；不做营销式 Hero，不做统计卡墙；
- 用一条克制的 **Certification authority rail** 表达 `Baseline → Evidence → Gates → Waivers → Decision`，它是页面唯一的结构性视觉记忆点；
- Certification 页面本身默认只读。测试执行、应用配置、Release 发布和回滚仍回到各自的权威工作面；
- 任何未返回、过期、scope 不匹配、digest 不匹配或无法证明的内容，显示 `unavailable / stale / incomplete`，不得补演示数据。

## 2. 参考证据与观察结果

### 2.1 Fresh Tencent Playwright 观察

本轮使用真实浏览器 fresh 打开腾讯云智能体开发平台知识库召回测试文档：

```text
https://cloud.tencent.com/document/product/1759/135538
```

可复现的观察结果：

- 召回测试被定义为知识库评测工具，与应用中的知识库配置直接分离；在召回测试中调整的检索与召回规则只对本次测试生效，不改写应用已有配置；
- 页面提供输入问题、按知识库配置检索/召回规则、查看召回结果、历史召回记录和对比测试等独立工作面；
- 历史记录支持查看和搜索，并明确说明最多保留半年；
- 对比测试支持同一问题下最多三组检索配置并行返回结果；进入对比模式后，知识库选择和基础设置保持不变，每组只调整自己的检索/召回规则；
- 结果没有命中时有明确的“无召回结果”状态，而不是空白或伪造成功；
- 召回结果侧重切片内容、相似度、来源、类型和原文入口，符合“先证明证据，再讨论发布”的操作顺序。

腾讯知识库与设置参考：

```text
https://cloud.tencent.com/document/product/1759/123551
https://cloud.tencent.com/document/product/1759/123554
```

这些页面呈现了持久 Knowledge Base 上下文、按操作域拆分设置、文档/问答/数据库等知识资产，以及内容状态约束配置的企业控制台式层级。

### 2.2 项目已验证的 Tencent 范式

本 sidecar 同时采用项目已验证的三条 Tencent/TDesign 产品范式：

1. **Recall testing 与 Application config 分离。** Recall testing 可以拥有临时测试配置、历史记录和多配置比较；它不等于应用当前的 serving 配置。
2. **Application evaluation 使用可复用 Dataset / Task。** 评测素材与任务可以复用；不同 scoring mode 由服务端定义并在结果中显式说明，页面不凭客户端猜测分数。
3. **Online testing 分成 Config / Orchestration / Test。** 配置快照、编排/流量控制和测试结果分别可追踪；只有验证通过的配置才有资格进入 publishing 流程，Certification 不绕过 Release Control Plane 直接发布。

以上模式是信息架构参考，不复制腾讯品牌、商标、私有 markup、图片或源码。

## 3. 当前 RAG4C 基线：复用，不重造

### 3.1 Stage 19 Knowledge Base / Release workspace

当前持久工作台入口为：

```text
/enterprise/knowledge-base?dataset={dataset_id}&section=releases
```

现有实现已经提供 Certification 应复用的外壳与交互约束：

- `frontend/src/pages/EnterpriseKnowledgeBaseWorkspacePage.tsx` 负责 workspace scope、`readOnly` 和 Releases surface；
- `frontend/src/enterprise-knowledge-base-shell/KnowledgeBaseResourceShell.tsx` 负责单一页面标题、Knowledge Base 上下文、资源 Tabs、移动端带可见标签的 Select、权威事实入口；
- `frontend/src/enterprise-knowledge-base-release/components/KnowledgeBaseReleaseCenter.tsx` 负责 Channel selector、Configured/Candidate/Effective/Serving summary、readiness、history 和主操作；
- `frontend/src/enterprise-knowledge-base-release/components/ReleaseDetailDrawer.tsx` 已形成 Manifest / Readiness / Impact / Audit 的单一 Drawer 习惯；
- `frontend/src/enterprise-knowledge-base-shell/KnowledgeBaseDrawerCoordinator.tsx` 与 context 已建立工作台级 Drawer 互斥、Release header context 和 focus return 边界；
- 桌面 Release history 使用 TDesign `PrimaryTable`，窄屏使用优先级明确的紧凑记录行；
- `null`、`0`、`blocked`、`unavailable`、`drifted` 和 `retired` 已有不同语义，Certification 不得把它们合并成一个“失败”或一个“空列表”。

Stage 19 的视觉验收证据在：

```text
output/playwright/enterprise-knowledge-base-release-stage19/
docs/research/2026-08-28-enterprise-knowledge-base-release-stage19-visual-acceptance.md
```

### 3.2 现有 Retrieval Quality read models

以下路径是 Stage 19/20 的只读上游事实，本 sidecar 不修改它们：

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

当前只读模型已经足以支撑第一版 Recall evidence adapter：

- `Experiment` 有 `tenant_id`、`dataset_id`、`query`、`query_hash`、`strategy_snapshot`、`result_snapshot`、`evidence_lineage`、`latency_ms`、`status`、`run_id`、`created_by` 和 `created_at`；
- `RunResponse` 带 `dataset_serving_generation` 和同一轮的 `RunItem[]`，可以把多配置比较绑定到同一个服务代次；
- `VariantView` 已投影出策略 revision、route、Top K、混合检索/重排/GraphRAG 等开关、耗时、结果数、降级状态、证据排名、分值、来源、文档/切片版本和内容哈希；
- `Agreement` 已提供多人判断、冲突结果、一致率、标签计数和平均分，可作为操作者判断证据；
- `useExperimentHistory` 已支持状态、运行 ID、查询哈希和 opaque cursor 历史读取，并在 scope 或请求变化时取消旧请求；
- `useExperimentDetail` 已并行读取实验详情与 agreement，detail 读取失败时保持真实错误状态。

既有 API 形态为：

```text
GET  /api/knowledge-bases/{dataset_id}/retrieval-experiments
GET  /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}
GET  /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/agreement
POST /api/knowledge-bases/{dataset_id}/retrieval-experiments/run
POST/PATCH .../{experiment_id}/judgments
```

这些 read models 目前**不是** Certification contract：它们没有 Application evaluation Dataset/Task、Online testing orchestration 或 Certification gate/waiver 生命周期。因此 Stage 20 应在 protected retrieval-quality 路径之外增加适配层和新的 Release-linked read model，而不是偷偷改变现有 Experiment 语义。

## 4. 页面放置与信息架构

### 4.1 入口

保持 Knowledge Base resource shell 的主 Tabs 不变：

```text
Overview | Documents | Taxonomy | Sources | Governance | Releases
```

进入 `Releases` 后，在 Release Center header 下增加一组局部 TDesign Tabs：

```text
Release control | Certification
```

建议将视图状态编码到 canonical query/hash 中，例如：

```text
/enterprise/knowledge-base?dataset={dataset_id}&section=releases&view=certification
```

这不是第二套导航，也不把 Certification 伪装成独立资源。移动端沿用 Stage 19 的带可见标签 `Select`：

```text
Releases view: Release control / Certification
```

### 4.2 桌面布局草图

```text
┌ Knowledge Base resource shell: one title / scope / channel / release context ┐
│ Overview | Documents | Taxonomy | Sources | Governance | Releases              │
├ Releases view:  Release control | Certification                                ┤
│ CERTIFICATION / RELEASE 42                 [Certified / Blocked / Incomplete]    │
│ 客服知识库 · UAT 验证 · manifest digest … · refresh · open Release detail       │
├ Certification authority rail: Baseline → Evidence → Gates → Waivers → Decision ┤
│ BASELINE BAND: Release / Channel / Serving G / Manifest / Readiness / revisions  │
├───────────────────────────────────────┬──────────────────────────────────────────┤
│ Evidence ledger                       │ Gate rail / decision                      │
│ Recall / Evaluation / Online filters │ required gate rows, state, reason        │
│ dense PrimaryTable                    │ final decision + next safe action        │
├───────────────────────────────────────┴──────────────────────────────────────────┤
│ Waiver ledger: explicit scope, approval, expiry, status, audit                    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

这是“权威带 + ledger + gate rail”，不是四个或八个统计卡片。数值只在它能解释 Release 是否可认证时出现。

### 4.3 移动布局草图

```text
Knowledge Base / Releases
[Releases view: Certification ▼]
Certification · Release 42                         [Blocked]
客服知识库 · UAT 验证

Baseline
Release / Channel / Serving generation / Manifest
Readiness / Profile / Ownership / Workspace revisions

Decision
[存在阻断] 2 个必需 Gate 未通过
[查看阻断原因] [打开 Release 详情]

Evidence
[类型: 全部 ▼] [状态: 全部 ▼]
Recall · experiment-…       完成 · G3 · 2026/08/28
Evaluation · task-…         待运行 · —
Online · test-…             已验证 · config r8

Gates
1. Manifest integrity       通过
2. Retrieval evidence       不完整
3. Application evaluation   待补证据

Waivers
没有有效 Waiver，或显示明确的过期/待审批行
```

280px 下仍使用带标签的 Select、可换行的 definition rows 和单列 ledger rows；不保留桌面宽表，也不把每行包装成独立视觉卡片。

## 5. Baseline：认证对象必须先固定

Baseline 是页面的第一条真实事实，不是装饰性摘要。至少显示：

- `Release number`、`release_id`、`status`；
- `Channel name/code`、risk tier、Channel revision；
- `manifest_digest` 与 `readiness_fingerprint` 的安全缩略值；
- `Configured / Candidate / Effective / Serving` 对照，明确当前认证对象是哪一个；
- `dataset_serving_generation` / serving generation；
- Dataset profile revision、mutation generation、ownership revision、Workspace revision；
- baseline captured time、created by、entry count；
- Release readiness：`ready / blocked / unavailable`、blocker count 和安全 reason。

Baseline 规则：

1. Certification 必须绑定不可变 `release_id` 和 manifest digest；Channel 当前指向发生变化时，原 Certification 不得静默改指向新 Release。
2. `null` 表示 authority 未返回，`0` 表示服务端明确返回零；二者不可互换。
3. Baseline revision 与当前 Dataset/Workspace/Serving authority 不一致时，显示 `stale`，保留原值和冲突字段，不显示“已认证”。
4. Release 被 `retired`、scope 不匹配或 manifest entry 不完整时，认证状态最多为 `blocked/unavailable`，不能由客户端降级为 warning。
5. 打开 Release detail 使用现有单一 Drawer coordinator，不再同时打开另一个 Drawer。

## 6. Evidence：三种来源，三条责任边界

Evidence 区使用一个 dense ledger，支持 `source type`、状态、时间、作者、服务代次和 config/task 引用筛选。默认排序是 server-owned 的最新有效证据，不由客户端按字符串猜测。每行只提供一个主动作：`查看证据详情`。

### 6.1 Recall / Retrieval evidence

显示为 `Recall` 或 `Retrieval`，不要写成“应用已评测”。引用现有 Retrieval Quality read models：

- experiment/run ID、query hash（安全显示，不强制展示完整 query）；
- `dataset_serving_generation`、strategy revision、route、Top K、hybrid/rerank/graph 开关；
- comparison group：同一问题下的实际 variant 数量、各 variant 名称和状态；
- result count、no-hit、degraded、latency；
- rank、score、source type、document/chunk ID、document/content revision、content hash；
- judgment count、agreement rate、conflict count、label counts 和 mean score（有返回才展示）；
- created by、created at、evidence freshness、scope and digest match。

腾讯文档观察到最多三组并行召回配置；RAG4C 当前 read model 的实际 variant 数量由服务端返回，Certification 不应在 UI 中擅自截断或把 `2/3/4` 组重算成一个“最佳配置”。如果未来产品策略限制比较组数量，应在 server contract 中明确并在页面显示限制原因。

Recall evidence 的操作边界：

- Certification 可以跳转或打开只读证据详情；
- 不在 Certification 中复制 Recall composer；
- 不把 Recall 临时配置写回 Dataset/Application config；
- 没有 hit、实验失败、证据行被安全投影丢弃时，显示真实 `no-hit / failed / unavailable`，不显示 pass。

### 6.2 Application evaluation evidence

Application evaluation 使用可复用 Dataset / Task 引用，不把一次临时 query 冒充完整评测。概念上至少需要：

- evaluation dataset ID、版本或 snapshot digest、样本数量；
- reusable task ID、任务版本、任务 owner、执行时间；
- application/config snapshot ID，与被测 Release 或 config fingerprint 的绑定关系；
- scoring mode、metric label、observed value、denominator、threshold（如有）和 server-owned result；
- pass/fail/incomplete、失败样本计数、异常/缺样本数、评测器版本；
- evidence artifact ID、created by、created at、freshness 和 scope。

Scoring mode 可以由服务端扩展，例如二值规则、分级 rubric、模型评审或组合评分；UI 只渲染服务端返回的 mode、公式说明、单位、阈值和结果。客户端不能用 `mean_score` 或样本数自行推导“评测通过”。

Application evaluation 的操作边界：

- Dataset / Task 是可复用的评测资产，但在 Certification 页面中只选择或引用，不直接编辑其内容；
- Application config 与 Knowledge Base Release 分开显示；
- 配置变化会使 evidence 进入 `stale` 或 `scope_mismatch`，不能继续沿用旧结果；
- 没有真实评测任务或结果时显示 `待补证据`，不展示示例分数。

### 6.3 Online testing evidence

Online testing 采用三个明确的证据层级：

```text
Config snapshot → Orchestration / traffic plan → Test result / verification
```

每一层都要能回到真实 ID/digest：

- Config：被测检索/生成配置、revision、config fingerprint；
- Orchestration：测试比例、流量范围、时间窗、灰度规则、参与应用/Channel；
- Test：样本量、结果状态、错误/降级计数、观察窗口、验证者、验证时间；
- publish eligibility：服务端返回的 `eligible / blocked / unavailable` 与 reason；
- promotion handoff：如有，只显示 Release Control/Approval request 引用，不显示 raw ticket，不直接执行 publish。

Online evidence 的三层必须分开渲染。一个“已通过线上测试”Tag 不能掩盖 Config 或 Orchestration 缺失。只有 config、orchestration、test 三者 scope 和 revision 都匹配时，才允许 gate 进入 `pass`。

## 7. Gates：把事实转换为可解释的发布门

Gate rail 使用紧凑 TDesign 表格或 definition ledger，而不是仪表盘卡片。每行至少有：

```text
code
label
required
state
source evidence IDs
observed_at
fresh_until / freshness
threshold summary (server-owned)
reason
next safe action
```

推荐的初始 Gate 集合：

1. `manifest_integrity`：Release manifest、entries、digest、Tenant/Dataset scope 完整且一致；
2. `release_readiness`：Stage 19 readiness 为 `ready`，无 active index/source/projection drift；
3. `recall_evidence`：存在匹配该 Release baseline 的 Recall evidence；
4. `application_evaluation`：指定 reusable Dataset/Task 的真实评测结果满足服务端规则；
5. `online_test`：Config、Orchestration、Test 三层证据完整且通过；
6. `approval_policy`：若目标 Channel 或发布动作需要审批，审批状态与 scope/revision 匹配；
7. `evidence_freshness`：所有 required evidence 在有效窗口内；
8. `waiver_policy`：所有豁免均有明确 scope、审批、有效期和允许的 gate code。

Gate state 必须明确区分：

```text
pass        服务端明确通过
fail        服务端明确不通过
pending     任务或审批正在进行
incomplete  缺少所需证据
stale       证据存在但已超 freshness/revision
waived      有效豁免覆盖，但不是 pass
unavailable 无法证明，或 authority 读取失败
```

`waived` 永远不改写为绿色 `pass`。最终决策建议使用：

```text
certified              所有 required gates = pass
certified_with_waiver  required gate 被有效 waiver 覆盖，仍显式显示 waiver 数量
blocked                存在 fail / expired waiver / stale required evidence
incomplete             尚未具备完成认证所需的全部证据
unavailable             authority 或服务端结果无法证明
expired                 认证本身超过有效期
```

若发布策略不允许 `certified_with_waiver` 进入某些 Channel，页面必须显示“认证结论”和“发布资格”两个不同字段，不能合并为一个标签。

## 8. Waivers：可审计的例外，不是隐藏阻断

Waiver ledger 是正式记录，不是 Gate reason 的自由文本。每条 Waiver 至少显示：

- waiver ID、scope（Tenant/Dataset/Release/Channel/Gate code）；
- 被覆盖的具体 gate；
- reason、风险说明和限制条件；
- approval request ID、审批状态、审批人或角色；
- created by、created at、expires at；
- revoked/expired 状态、revoked by、revoked at；
- 当前是否仍覆盖本 Release manifest digest 和 revision。

交互规则：

- 只读用户可以查看 waiver 事实，但所有申请、撤回、批准、撤销按钮保持 disabled；
- 任何“申请 Waiver”都必须是独立的后续 Dialog/API/Approval 流程，不能在勾选框后立即让 Gate 变绿；
- 过期、撤销、scope/digest 不匹配的 Waiver 显示为历史事实，不能继续覆盖 Gate；
- Waiver 详情打开 Certification detail 的当前 Tab，不再嵌套第二个 Drawer；
- raw approval ticket、secret、原始 `Idempotency-Key` 和未经投影的异常文本禁止进入 DOM、storage、console、截图和结果 JSON。

## 9. Certification detail：一个 Drawer，五个可验证 Tab

Certification detail 使用工作台级 Drawer coordinator，与 Stage 19 Release detail 共用“一个 Drawer”原则。打开 Certification detail 前关闭或替换 Release detail；Dialog 可以覆盖 Drawer，但不能再开第二个 Drawer。

建议 Tab：

```text
Summary | Baseline | Evidence | Gates | Waivers | Audit
```

如果 280px 宽度不足，Tab 使用 TDesign 的可滚动导航，但仍保持完整 `tablist / tab / tabpanel` 语义；不改成不可访问的横向按钮串。

### Summary

- Certification ID、Release number、Channel、最终决策；
- required/pass/fail/pending/waived/incomplete gate counts；
- evidence freshness summary；
- 一句安全的下一步，例如“补齐 Application evaluation task-17”，不显示内部异常堆栈。

### Baseline

- 完整 manifest/authority facts；
- revision fence、generation、digest、scope；
- 与当前 Channel/Serving 的差异；
- `stale` 时列出 expected/current 字段。

### Evidence

- Recall、Application evaluation、Online 三类证据的完整来源链；
- evidence item 的 server-owned metric/scoring mode；
- 源实验/任务/测试的只读详情和回链；
- 无结果、失败、被过滤的行要保留计数和 reason，不能伪装成不存在。

### Gates

- 每个 gate 的 state、required、证据 ID、observed time、threshold summary 和 reason；
- blocker 可展开，但展开内容必须仍是安全投影；
- Gate 结果变化时用 `role="status"` 或 `role="alert"` 明确通知。

### Waivers / Audit

- Waivers 显示生效范围与过期时间；
- Audit 显示 certification snapshot、证据绑定、gate recompute、waiver 和审批事件；
- Audit request ID 可以安全缩略，原始 token/ticket 永不显示。

Drawer 交互要求：

- 打开后焦点进入 dialog；
- `Escape` 关闭；
- 关闭后焦点返回触发按钮；
- Tab、ArrowLeft/ArrowRight、Home/End 可完成 Tab 操作；
- `aria-labelledby`、`aria-controls`、`aria-selected` 和 `tabpanel` ID 成对存在；
- 加载、错误、空结果、unavailable 均拥有可读文本；
- 背景内容不可被键盘继续操作，实际 focus trap 与 `aria-modal` 语义一致。

## 10. TDesign / 视觉语言约束

### 推荐组件映射

- `PrimaryTable`：桌面 Evidence ledger、Gate ledger、Waiver ledger；
- `Descriptions` 或语义 `dl`：Baseline 与 detail facts；
- `Tabs`：Releases 局部视图、Certification detail；
- `Tag`：决策、Gate state、evidence source type、freshness；
- `Alert`：阻断、stale/unavailable、审批或权限提示；
- `Drawer`：Certification detail；
- `Dialog`：未来的申请 Waiver/重新认证确认，不承担发布动作；
- `Select`：移动 Releases view、evidence type、状态筛选，始终带可见 label；
- `Loading` / `Empty`：真实异步和真实空态；
- TDesign Icons：证据链、锁定、时间、检查、警告和审计语义。

### 视觉取向

- 延续 Stage 19 的浅色/深色 tokens、蓝色主色、细边框、紧凑留白和 mono ID；
- 主视觉使用一条细 authority rail 和状态 Tag，不使用渐变背景、发光徽章或大面积插画；
- 桌面信息密度高但有清晰分隔线；移动端优先保留 `状态、对象、时间、下一步` 四项；
- ID、digest、revision 在窄屏使用安全缩略 + 可访问的完整值，不以横向溢出换取“完整展示”；
- Uiverse/Morphicons 只在 TDesign 缺少合适的状态图形时作为静态 fallback，不能替换 TDesign 的交互组件，也不能成为认证状态的唯一信息载体。

## 11. 概念 read model 与数据库边界

以下是 Stage 20 的概念形状，不是已批准的 API/ORM 名称；落地时必须由 server-owned contract 固化：

```json
{
  "certification_id": "certification-42",
  "tenant_id": "tenant-a",
  "dataset_id": "dataset-prod",
  "release_id": "release-42",
  "manifest_digest": "sha256:…",
  "channel_id": "channel-uat",
  "channel_revision": 2,
  "status": "certified_with_waiver",
  "decision_revision": 3,
  "baseline": {
    "profile_revision": 12,
    "mutation_generation": 18,
    "ownership_revision": 7,
    "workspace_revision": 7,
    "serving_generation": 3,
    "readiness_state": "ready",
    "readiness_fingerprint": "sha256:…"
  },
  "evidence_summary": {
    "recall": {"count": 3, "state": "ready"},
    "application_evaluation": {"count": 1, "state": "ready"},
    "online": {"count": 1, "state": "pending"}
  },
  "gates": [],
  "waivers": [],
  "audit": {"next_cursor": null},
  "generated_at": "2026-08-28T09:00:00Z"
}
```

建议未来在 `0029` 之后单独引入 Release-linked certification persistence，至少考虑：

```text
knowledge_base_certifications
certification_evidence_refs
certification_gates
certification_waivers
certification_events
```

数据库不变量：

- 每个 certification row 以 Tenant-leading composite scope 绑定 Dataset、Release 和 Channel；
- certification snapshot 保存 manifest digest、readiness fingerprint、evidence digest 和 decision revision；
- evidence reference 是不可变引用，不能随着源 Experiment/Task/Online result 更新而静默改写；
- gate recompute、waiver、approval 和 decision 使用 append-only events；
- Certification 与 Release 的关系是精确 Release 关系，不以“Channel 当前 effective release”替代；
- 重复请求使用 tenant-scoped idempotency，重放返回原始安全投影；
- raw query body、credential URL、secret、token、ticket 和原始异常消息不进入 certification facts；
- freshness、expiry、revocation 和 scope mismatch 是服务端事实，客户端只投影，不自行决定通过；
- 任意跨 Tenant、Dataset、Release、Channel 或 Application reference 错配必须 fail closed。

推荐未来只读 API 形态：

```text
GET /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certification
GET /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certification/evidence
GET /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certification/audit
```

未来 mutation（申请 Waiver、重新计算、提交认证）必须独立于 read API，并具有 revision fence、Approval policy、幂等键和审计；本 sidecar 不实现这些动作。

## 12. Protected Retrieval Quality integration contract

Stage 20 只允许在 protected Retrieval Quality 模块之上增加只读 adapter：

```text
Retrieval Quality Experiment / Agreement
        ↓ safe projection + scope/digest validation
Certification evidence reference
        ↓ release baseline match + freshness gate
Certification gate
        ↓ server-owned decision
Release publish eligibility
```

禁止：

- 修改 `frontend/src/retrieval-quality/**` 的合同、投影、hook、CSS 或测试来迎合 Certification；
- 修改 `core/retrieval_experiment_runner.py` 或 `server/retrieval_experiments_api.py` 以偷偷增加 Certification 语义；
- 在 Certification 页面复刻 `RetrievalComposer` 或直接调用 run endpoint；
- 用历史实验的“看起来相似”字段替代 exact `dataset_id / serving_generation / strategy/config fingerprint` 绑定；
- 将 operator judgment 自动推断成 Application evaluation pass；
- 将 Recall evidence 自动推断成 Online test 或 publish approval。

优先放置新的适配代码于受保护目录之外，例如未来的：

```text
frontend/src/enterprise-knowledge-base-release-certification/
core/enterprise_knowledge_base_certification.py
server/enterprise_knowledge_base_certification_api.py
```

这些路径仅为后续实现建议，本轮不创建、不修改。

## 13. Accessibility、响应式与状态验收

### 13.1 Desktop

- 1440px 重点验证完整 baseline、Evidence ledger、Gate rail、Waiver ledger 和 detail Drawer；
- 表格列按 `state → object → source → observed time → action` 排序，digest/revision 放在次级信息；
- 页面只拥有一个资源 shell `h1`，Certification 使用一个局部 `h2`，detail Drawer 采用有意义的标题；
- 颜色不是状态唯一表达，Tag 必须同时有文本；
- Gate blocker 和证据不足状态必须在首屏可发现，不埋在 hover。

### 13.2 375px / 280px

- 不显示桌面宽表；转成有明确字段标签的 ledger rows；
- Evidence source type、status、Release、Channel、时间和下一步不被隐藏；
- 复杂 ID 可换行或安全缩略；不出现页面级 horizontal overflow；
- TDesign Select 有可见 label，外层不增加不可操作的 `role="combobox"` 或额外 focus stop；
- Drawer 变成近全宽/全宽，Tabs 可滚动且可用键盘；
- `loading / incomplete / blocked / unavailable / certified_with_waiver` 都有可见文本和辅助技术语义；
- reduced motion 下不依赖动画显示 gate 结果。

### 13.3 Keyboard / assistive technology

- 局部 Tabs 完整实现 ArrowLeft/ArrowRight/Home/End、roving tabindex、`aria-controls` 和 `tabpanel`；
- table 使用真实 header/scope，行操作的 accessible name 包含 Release、证据或 Gate 标识；
- 异步刷新使用 `role="status" aria-live="polite"`，错误使用 `role="alert"`；
- Drawer 使用真实 dialog 语义、focus trap、ESC 和 focus return；
- disabled mutation 在 read-only、scope 未验证、stale fence、unavailable authority 时是真 disabled，不只是视觉置灰；
- 完整错误文本先做安全投影，避免 raw ticket、token、password、credential URL、Idempotency-Key 出现在可访问树。

## 14. Playwright / visual acceptance 计划

Stage 20 实现后沿用 Stage 19 的浏览器矩阵：

```text
direct / hash
× light / dark
× 1440×900 / 375×812 / 280×720
= 12 个基础场景
```

至少补充这些状态截图：

```text
desktop certification-ready.png
desktop certification-blocked.png
desktop certification-with-waiver.png
desktop certification-detail-gates.png
desktop certification-detail-evidence.png
mobile-375 certification-ledger.png
mobile-280 certification-detail.png
```

受控 fixture 应覆盖：

- Release 42 + matched Recall evidence + ready gates；
- Recall history with multiple comparison variants；
- reusable Application evaluation Dataset/Task with two server-defined scoring modes；
- Online Config/Orchestration/Test 分层，其中一个 scope mismatch；
- blocked readiness、stale evidence、expired waiver、pending approval、unavailable authority；
- malformed evidence count，验证 UI 不把 invalid rows 伪装成真实空列表。

浏览器验收门：

- 只允许 loopback/controlled fixture API；
- no production write action；
- console/page/unknown/unexpected request 均为零；
- no horizontal overflow；
- Certification、Gate rail、Evidence ledger、Waiver ledger、detail Drawer 有可见 bounding rect；
- Enter/Arrow/Home/End/Tab/Escape/focus return 全通过；
- DOM、storage、console、result JSON 和截图中不可见 raw secret/ticket/token/Idempotency-Key；
- artifact manifest 绑定当前 source tree SHA、lockfile hash 和 fixture revision；
- 源码或 lockfile 变化后必须重新生成 fresh artifacts，旧截图不能替代当前证据。

## 15. 明确不做的事情

- 不把 Certification 变成主侧栏的新资源；
- 不复制 Recall composer、Application config editor 或 Online test runner；
- 不在客户端定义统一“质量分数”或根据不同 scoring mode 自行加权；
- 不把 waiver 当作隐式 pass；
- 不把 Channel 当前指向的 Release 当作 Certification 的隐式对象；
- 不在 Certification 中直接 Capture、Promote、Rollback、Pin 或执行 Approval；
- 不修改 protected Retrieval Quality paths；
- 不执行任何生产动作。

## 16. Stage 20 后续实现顺序

```text
1. Certification read model + exact Release/manifest binding
2. Recall evidence adapter（只读 Experiment / Agreement projection）
3. reusable Application evaluation Dataset/Task evidence contract
4. Online Config / Orchestration / Test evidence contract
5. server-owned Gate recompute + freshness + waiver facts
6. Releases 内 Certification view（desktop/mobile/accessibility）
7. Certification detail Drawer + audit / approval handoff
8. controlled Playwright evidence + source-bound artifact manifest
9. Database/security review + full Stage 7–20 regression
```

本 sidecar 的最终设计判断：Stage 19 提供“什么 Release 可以被发布”的权威对象，Stage 20 只补上“为什么这个 Release 值得被发布”的可复现证据。两者通过 exact manifest digest、revision fence、server-owned gates 和 append-only audit 相连，既沿用腾讯知识库的清晰工作面，也保持 RAG4C 的不可变、可审计和 fail-closed 特色。