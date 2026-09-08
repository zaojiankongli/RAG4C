# Stage 26 腾讯知识库 / 腾讯云企业控制台 UI 参考

> **Research only / UI reference**  
> **采集日期：** 2026-08-30  
> **研究范围：** 腾讯云智能体开发平台（ADP）知识库、知识管理、文档管理、知识库设置、知识库召回测试，以及未登录状态下的腾讯云/ADP 控制台入口。  
> **安全边界：** 只读访问公开页面；未登录、未填写、未提交表单，未点击会产生外部副作用的动作；不修改应用源码、后端或 protected `retrieval-quality` 路径。

## 1. 结论速览

Stage 25 已经建立了 RAG4C 的“资源—运行—请求—证据”自动化工作面。Stage 26 最值得继续借鉴的不是另一个自由画布，而是一个以知识资产可靠性为中心的企业运营页：

```text
知识运营入口
  -> 知识库 / 文档 / 问答 / 数据库
  -> 筛选与批量操作
  -> 行级状态和门禁
  -> 详情抽屉：版本、处理、索引、来源、过期
  -> 检索测试 / 历史 / 对比
  -> 一致性、质量和治理证据
```

在当前仓库没有独立 Stage 26 功能 spec 的前提下，最自然的 UI 签名是：

```text
Knowledge Reliability & Retrieval Console
知识可靠性与检索运营中心

Summary Strip
  活跃知识 | 处理中 | 失效/即将到期 | 索引或检索风险
Scope Rail
  知识库 -> 文档 | 问答 | 数据库
Filter Row
  分类 | 来源 | 状态 | 启用/停用 | 到期窗口 | 标签 | 更新时间
Primary Surface
  Desktop dense table / Mobile cards
Detail Surface
  Document/QA/Source detail drawer + revision/attempt/evidence timeline
Quality Sidecar
  Query | KB scope | retrieval profile | result chunks | history | compare
```

这是对 `CONTEXT.md` 中 Knowledge Lifeline、Document Version、Projection Fence、Consistency Console 的 UI 转译建议；不是对腾讯内部 API 或数据库契约的断言。

## 2. 证据目录

### 2.1 仓库已有 Stage 25 基线

- Stage 25 UI 研究报告：`docs/research/2026-08-30-stage25-tencent-automation-workflow-ui-reference.md`
- Stage 25 公开截图目录：`output/playwright/tencent-stage25-automation-research/`
- Stage 25 当前 RAG4C 桌面基线：`output/playwright/enterprise-automation-workflows-stage25/direct-light-1440.png`
- Stage 25 当前 RAG4C 移动基线：`output/playwright/enterprise-automation-workflows-stage25/direct-light-375.png`
- 当前领域上下文：`CONTEXT.md`

Stage 25 的关键结论继续有效：状态先于操作；详情页应解释事实；运行、请求、审批和证据必须是可追踪对象；规则使用受限表单；不可把自由画布或任意脚本执行当作安全策略。

### 2.2 本轮公开页面

- [什么是知识库？](https://cloud.tencent.com/document/product/1759/123551)
- [什么是知识管理？](https://cloud.tencent.com/document/product/1759/123552)
- [文档概述](https://cloud.tencent.com/document/product/1759/112702)
- [知识库设置概览](https://cloud.tencent.com/document/product/1759/123554)
- [知识库召回测试](https://cloud.tencent.com/document/product/1759/135538)
- [腾讯云知识库控制台入口（未登录）](https://console.cloud.tencent.com/lkeap/knowledge)
- [ADP 控制台入口（未登录）](https://adp.cloud.tencent.com/adp/)

本轮截图和快照保存于：

`output/playwright/stage26-research/public-agent/`

重点截图：

- `stage26-01-knowledge-base-overview-desktop.png`：知识库文档中心桌面布局。
- `stage26-01-knowledge-base-overview-mobile-375.png`：知识库文档中心 375px 布局。
- `stage26-02-knowledge-management-desktop.png`：知识管理与应用—知识库引用关系。
- `stage26-03-documents-overview-desktop.png`：文档概述与文档生命周期说明。
- `stage26-03-documents-overview-mobile-375.png`：文档概述 375px 布局。
- `stage26-04-retrieval-test-desktop.png`：召回测试能力说明与功能表。
- `stage26-04-retrieval-test-full.png`：召回测试完整文档和内嵌测试台截图。
- `stage26-09-retrieval-test-mobile-375.png`：召回测试 375px 布局。
- `stage26-05-knowledge-settings-desktop.png`：知识库设置概览桌面布局。
- `stage26-05-embedded-batch-process.png`：知识库文档表格、分类树、筛选器和批量设置菜单。
- `stage26-05-embedded-document-list.png`：知识设置抽屉/对话框与后置工作区。
- `stage26-05-embedded-model-settings.png`：知识处理模型设置对话框。
- `stage26-05-embedded-doc-settings.png`：文档设置对话框。
- `stage26-05-embedded-classification.png`：到期时间设置与自定义日期控件。
- `stage26-05-embedded-source-display.png`：参考来源展示、当前/自定义链接与下载开关。
- `stage26-05-embedded-doc-detail.png`：导入文档工作流中的分类选择器。
- `stage26-06-console-knowledge-gate.png`：未登录知识库控制台入口的登录门禁。
- `stage26-07-adp-console-gate.png`：未登录 ADP 控制台入口的登录门禁。
- `stage26-08-documents-overview-mobile-280.png`：文档概述 280px 极窄布局。

## 3. 公开页面观察

### 3.1 文档中心的产品级 IA

腾讯云文档页面同时展示了三层导航：

1. **全局产品导航：** 腾讯云品牌、产品/解决方案/企业中心等全局入口，上方另有文档中心、入门中心、API 中心、SDK 中心、文档活动、我的反馈。
2. **产品文档树：** 左侧固定目录，以“动态与公告、产品简介、购买指南、快速入门、操作指南”为一级分组；操作指南下继续展开“应用开发、智能工作台、常用应用、知识库、Widget、工作流、连接器与工具”等产品域。
3. **页面内导航：** 右侧“本页面目录”只负责当前文档锚点定位，并不会取代产品级 IA。

可复用的不是腾讯云的具体文字，而是“产品域—功能域—当前页面”的三层心智模型。RAG4C 应把“知识运营、企业治理、自动化中心”保持为一级能力；文档、问答、数据库、处理与索引、检索测试则属于知识运营内部的二级导航或工作区 Tabs。

### 3.2 知识库与应用引用关系

公开知识库文档明确区分：

- 平台级知识库可以被应用引用；
- 应用内存在默认知识库；
- 应用可以在“应用 > 知识管理 > 添加知识库”中增加平台级知识库；
- 调整引用关系后需要重新发布应用；
- 被应用引用的知识库不能直接删除，需要先解除引用。

这给 Stage 26 一个重要的详情模式：知识库/文档详情不能只显示名称和状态，还要显示“被哪些应用或工作区引用、当前生效范围、变更是否需要发布/审批”的只读关系摘要。删除、停用、恢复类动作必须在关系存在时显示门禁原因，而不是只在提交时失败。

### 3.3 文档工作区：左侧分类树 + Tab + 工具栏 + 表格

内嵌的腾讯控制台截图是本轮最可复用的企业工作面：

- 左侧是应用级导航，知识库处于高亮态；
- 内容区顶部有“文档 / 问答 / 数据库”二级 Tabs；
- 文档区再分为“分类”树与主列表；分类树有搜索、全部分类、未分类等节点；
- 工具栏明确分层：主操作“导入”，受选择数量影响的“移动到分类”“批量设置”，右侧显示知识库字符用量、文档对比任务和设置入口；
- 筛选器独立成行，包含文档/关键词搜索、状态选择和刷新；
- 表格使用行复选框、文件类型图标、文档信息、字符/大小、状态和行级操作；
- 行级动作保持稳定的“设置 / 删除 / 更多”，状态在行内先于操作展示；
- 表格底部显示总数、每页条数和分页。

这一结构比“卡片墙”更适合 RAG4C，因为它同时支持长期运营、批量处理、状态筛选、对象定位和深链。

### 3.4 批量设置与单文档设置

批量设置菜单列出：

- 批量下载文档；
- 批量删除；
- 批量恢复（截图中呈现为受状态限制的弱化动作）；
- 文档标签设置；
- 到期时间设置；
- 参考来源设置。

单文档设置对话框进一步展示了可枚举表单：

- 文档标签；
- 永久有效 / 自定义到期时间；
- 是否展示参考来源；
- 文档分类；
- 完成 / 取消。

打开“展示参考来源”后，页面继续细分为“跳转当前文档链接 / 跳转自定义链接”和“允许下载文档”开关。这个层级值得借鉴：普通属性、生命周期属性和对外来源展示属性分组，而不是把所有字段挤在一个 JSON 编辑器里。

### 3.5 导入文档流程与处理设置

导入文档工作流把“上传文件”和“高级设置”分开：

- 上传区说明支持的文件格式与大小限制；
- 分类选择器用搜索框和树状分类选择，不要求用户输入路径字符串；
- 高级设置中处理范围、标签、到期时间、参考来源和分类都可配置；
- 知识处理模型使用独立设置对话框，按用途分别选择向量、文档解析、问答生成、Schema 生成模型；
- 操作以“下一步 / 完成 / 取消”明确收束，不把导入视为瞬时同步成功。

RAG4C 应把上传、解析、切分、索引投影和生效拆成可观测阶段；页面只显示安全摘要和 attempt/版本证据，不显示未经投影的原始正文、凭据或内部地址。

### 3.6 知识库设置与生命周期

公开文档把知识库设置组织成一个独立的设置域，并在右侧目录中继续分出：

- 知识处理模型设置；
- 检索与召回设置；
- 调整文档分类；
- 调整文档设置；
- 设置文档到期时间；
- 展示参考来源；
- 批量处理文档。

文档页面还明确了若干生命周期和运营状态，包括导入完成、人工申诉中、人工申诉失败、超量失效、超量失效恢复等，并列出搜索、下载、删除、重命名、启用/停用、解析切分干预等操作。修改后要等文档回到“导入完成”才在生效范围中生效。

对 RAG4C 的启示是：状态不能只有 `active/inactive`。至少要把 processing、ready/effective、disabled、expired、appeal/review、failed、restoring 等状态与下一步动作分开；“已保存”不能伪装成“已生效”。

### 3.7 检索测试：质量实验台的最小模型

“知识库召回测试”文档列出的能力可以直接转译为 Stage 26 的质量侧车：

- 选择知识库范围，一次最多选择 10 个知识库；
- 输入测试问题，单次最多 4000 字符；
- 按知识库配置检索策略、重排模型、文档/问答/数据库召回规则；
- 查看召回结果，包含召回片段内容、相似度分值、来源、类型和原文入口；
- 查看并搜索历史召回测试记录，文档说明最长保留半年；
- 以最多 3 组检索配置进行对比测试；
- 权限和模型资源是功能可用性的前置条件，资源不足时不能执行。

UI 上的关键不是“再做一个聊天框”，而是把测试输入、知识范围、规则配置、结果证据和历史/对比组织成一个可复现的实验面。对 RAG4C 来说，Query 应进入受控的测试会话/审计事实；结果应引用 chunk revision、source/document identity 和检索 profile，而不是只展示一段文本和一个分数。

### 3.8 移动端 / 窄屏行为

在 375px 和 280px 视口下，腾讯文档页面表现出稳定的收敛策略：

- 全局导航收敛为 Logo、搜索、用户和菜单图标；
- 产品标题进入第二行并在 280px 下允许换行；
- 左侧产品树隐藏到菜单入口，不把固定宽侧栏硬塞进视口；
- 面包屑换行；
- 页面内目录降为内容流中的锚点列表，并提供“展开全部”；
- 正文变成单列，提示块和表格示例按容器宽度缩放；
- 反馈/咨询浮动控件固定在右侧，不遮住标题和主操作。

Stage 25 已验证 375/280 的移动表面。Stage 26 应继续保持同一原则：移动端不是横向压缩桌面表格，而是把行变成卡片/摘要，保留状态、作用域、revision、更新时间、风险/下一步；筛选器进入抽屉或可折叠面板；详情以全屏 Drawer/Sheet 展开。

### 3.9 未登录控制台边界

直接访问 `https://console.cloud.tencent.com/lkeap/knowledge` 会跳转到腾讯云登录页；直接访问 `https://adp.cloud.tencent.com/adp/` 也会跳转到登录页。本轮没有尝试登录或使用任何登录方式，因此无法把已登录控制台的真实 IA、权限按钮或租户数据当作公开事实。

因此本报告将“实际控制台模式”限定为官方文档中的内嵌控制台截图，并把登录门禁截图作为研究边界证据，而不是产品 UI 参考。

## 4. 对 RAG4C 的可复用模式

### 4.1 导航与作用域

推荐保持 Stage 25 的左侧 IA，但把知识可靠性页放入现有“知识运营”域：

```text
知识运营
├─ 检索测试
├─ 回答过程
├─ 质量评测
├─ 任务中心
├─ 自动化中心
├─ 运行监控
├─ 一致性控制台   <- Stage 26 候选主入口
└─ 内容治理
```

页面内使用固定作用域条：Tenant / workspace / knowledge base / source。任何对象详情都要保留作用域面包屑，避免把跨租户、跨知识库的对象混在一个全局列表里。

### 4.2 指标摘要：小而可行动

不要复制营销式“指标卡墙”。建议只放四到六个能直接过滤或跳转的摘要：

- 活跃/可检索知识资产数；
- 处理中或阻塞中的 ingest/index attempt；
- 即将到期或已失效资产；
- desired revision 与 indexed/graph revision 存在漂移的对象；
- 最近检索测试失败或低置信度的对象；
- 待人工处理/待审批的治理请求。

每个摘要都应能点击进入对应筛选视图，并显示时间窗、作用域和数据是否完整；不可把“数据未加载”“authority unavailable”渲染成 0。

### 4.3 表格与筛选

桌面首屏优先使用密集表格，而不是大量卡片。推荐字段顺序：

```text
对象 / 类型 -> 状态 -> 来源 -> 当前版本 -> 期望/索引版本 -> 更新时间 -> 风险/原因 -> 操作
```

筛选按三段组织：

1. 快速状态 Tabs：全部、处理中、需关注、可检索、失效/过期、失败；
2. 结构筛选：知识库、分类、来源、类型、标签、启用状态；
3. 时间/关键词：名称或 ID 搜索、更新时间、到期窗口、处理 attempt。

批量操作必须显示选中数量和作用域，并依据状态门禁显示可用/禁用原因；危险操作不能因为按钮存在就被认为可执行。

### 4.4 详情 Drawer / 页面

建议沿用腾讯文档的“表格定位对象 + 抽屉完成上下文”的模式，但把 RAG4C 的证据链补齐：

```text
Overview
  -> Identity / Scope
  -> Lifecycle status
  -> Document version / parser policy
  -> Ingest attempts and stage spans
  -> Chunk head / revision summary
  -> Desired vs indexed/graph projection fence
  -> Source / expiry / reference display
  -> Related Task / Approval / Notification / Automation
  -> Event / evidence timeline
```

详情中的每个按钮都应回答“对哪个 revision、哪个 authority、哪个 scope 生效”。若当前证据不可用，显示 `unavailable` 或 `stale`，而不是提供绕过按钮。

### 4.5 检索测试侧车

检索测试不应抢占知识资产主列表的工作流。建议用二级入口或右侧 sidecar：

```text
Test Session
  Query
  Knowledge-base scope
  Retrieval profile
  Compare group A/B/C
  Result chunks + score + source + revision
  History / replay metadata
```

“运行测试”只产生可审计的测试事实，不修改生产知识库设置，不推进 source cursor，不直接更改 chunk head 或 projection。结果页面必须区分测试请求、召回结果和生产生效状态。

## 5. Stage 26 UI 签名建议

### Desktop

```text
RAG4C / 知识运营 / 一致性控制台

[租户 / 工作区 / 知识库作用域] [authority 状态] [刷新]

[可检索] [处理中] [需关注] [漂移] [失效/即将到期]

知识可靠性路径
SOURCE -> VERSION -> PROCESS -> INDEX -> RETRIEVE -> EVIDENCE

[全部] [处理中] [需关注] [可检索] [失效]
[知识库] [来源] [状态] [分类] [标签] [到期] [更新时间] [搜索]

┌─────────────────────────────────────────────────────────────┐
│ 对象 | 状态 | 来源 | 当前版本 | 索引版本 | 更新时间 | 风险 | 操作 │
├─────────────────────────────────────────────────────────────┤
│ ... row-level state-gated actions ...                      │
└─────────────────────────────────────────────────────────────┘

[对象详情 Drawer]
身份 / 生效范围 / 版本与摘要 / ingest attempt / projection fence /
来源与到期 / 质量与检索 / 关联任务 / 证据时间线
```

### Mobile 375 / 280

```text
顶部：Logo + 作用域摘要 + 菜单
正文：摘要卡纵向堆叠
      状态 Tabs 横向滚动
      筛选入口（Drawer）
      对象卡片：名称、类型、状态、版本、更新时间、风险
      详情：全屏 Sheet，Tabs 或折叠区组织证据
```

移动端必须保留：状态、作用域、revision/digest 摘要、更新时间、可执行下一步和不可用原因；可以隐藏非关键列，但不能隐藏状态和证据入口。

## 6. 不可照搬边界

1. **不能把文档中心左侧目录直接当作 RAG4C 产品 IA。** 文档站点的右侧页面目录、全局产品广告和帮助入口属于内容消费场景；RAG4C 需要以 Tenant/Workspace/Authority 为作用域。
2. **不能复制自由画布或任意执行。** Stage 25 已明确拒绝任意脚本、SQL、URL、Webhook、Prompt、凭据或动态函数；Stage 26 的检索/治理表单同样只能接收 allow-list 和可验证 schema。
3. **不能把“导入完成”或“测试有结果”当成生产生效。** 处理完成、索引投影完成、知识可检索、应用发布生效是不同事实，必须分状态展示。
4. **不能把批量动作做成无条件工具栏。** 删除、恢复、停用、重建索引、重新处理等动作要受 source authority、revision fence、审批和幂等约束；禁用动作要解释原因。
5. **不能把详情抽屉做成原始 JSON dump。** 只展示安全投影：document identity、version/revision、digest、状态、attempt、source ID、时间、关联对象和 safe error；不展示 token、credential、原始正文、完整 query 或任意 response body。
6. **不能从未登录页面推断已登录控制台权限。** 本轮实际控制台只观察到登录门禁；已登录控制台的按钮、租户、角色和数据需要受控 fixture 或明确授权环境验证。
7. **不能把移动端当成缩小后的桌面表格。** 280px/375px 要采用卡片、筛选抽屉和全屏详情，保证状态与证据路径可达。

## 7. 最终判断

腾讯知识库公开文档提供了三组高价值、可迁移到 RAG4C 的交互语义：

```text
产品级三层 IA
  -> 知识库/文档工作区的分类树 + Tab + 筛选 + 表格
  -> 设置/导入/来源/到期等受限表单
  -> 召回测试的范围、规则、结果、历史和对比
```

Stage 26 的重点应是把这些语义与 RAG4C 的 Knowledge Lifeline、Document Version、Projection Fence 和 Consistency Console 合并：

```text
找得到对象
  -> 看得到状态
  -> 解释得清版本与原因
  -> 追得到处理/索引/检索证据
  -> 在权限和 authority 允许时才提供下一步
```

这比复制腾讯云的颜色、文案或单个页面更重要，也能与 Stage 25 的 `WHEN -> IF -> REQUEST -> EVIDENCE` 保持同一套企业控制台语言。
