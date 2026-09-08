# Stage 25 腾讯自动化 / 工作流 UI 参考

> **Research only / UI reference**
>
> **采集日期：** 2026-08-30  
> **证据来源：** `output/playwright/tencent-stage25-automation-research/` 已采集的本地截图  
> **研究范围：** 腾讯云智能体开发平台（ADP）、WeData 运行与告警、自动化助手（TAT）和审批中心。  
> **安全边界：** 本文只总结截图可见的信息架构、交互模式和风险，不继续浏览，不调用外部服务，不修改业务源、迁移、ORM、API、前端组件或测试。
>
> **截图数量说明：** 用户口径为 36 张；对目录实际枚举得到 `01`–`38` 共 38 个 PNG。为保持证据可追溯，附录完整列出目录中的 38 个文件；正文将它们视为同一采集批次，其中 `37`–`38` 是额外的审批详情/动作补充截图，不把数量差异当作产品结论。

## 1. 结论速览

这批截图共同呈现的不是“一个工作流画布”，而是一套企业控制台式的 **资源—实例—证据—治理** 信息架构：

```text
产品/工作空间入口
  -> 资源列表与筛选
  -> 行级状态与受限操作
  -> 实例/申请详情
  -> 日志、血缘、变更差异或审批时间线
  -> 可验证的下一步
```

最值得 RAG4C 借鉴的五件事：

1. **把自动化对象化。** 规则、运行、请求、日志和审批不是一张“运行中”的卡片，而是可以被列表、筛选、深链和历史追踪的持久对象。
2. **把状态放到第一视觉层。** 列表先显示执行状态、审批状态或告警状态，再显示操作；状态决定哪些按钮存在，而不是仅决定颜色。
3. **把详情做成证据工作面。** WeData 的尝试历史、阶段时间线、日志、DAG/列表血缘和节点详情，与 ADP 的申请信息、变更差异和审批流程互相补充，形成“为什么如此”的证据链。
4. **规则创建使用受限表单。** WeData 的监控对象、条件、周期、连续次数、通知方式和审批中心的范围、审批人、通知方式都可枚举；它们不是任意代码编辑器。
5. **画布与执行权限必须分离。** ADP 自由画布适合 Agent/节点编排，但不应直接成为 RAG4C 的自动化执行权限面；RAG4C 应采用 `WHEN → IF → REQUEST → EVIDENCE` 的受限请求编排。

RAG4C 的核心转译结论：

```text
WHEN    只接收明确来源权威产生的事件
IF      只执行有限条件 schema 的纯判断
REQUEST 只生成 allow-list 内、可幂等、可审计的请求
EVIDENCE 记录 revision、digest、时间、状态、关联对象与事件链
```

不应照搬的两类能力：

- **自由画布的任意连线/任意节点执行：** 容易把视觉结构误当成安全策略，隐藏循环、边界和权限。
- **Shell、Python、SQL、URL、Webhook、Prompt、凭据或动态函数的任意执行：** 会把一个受治理的规则中心退化成远程脚本平台，破坏 Tenant 隔离、幂等、审批和知识血缘。

## 2. 证据与阅读方式

### 2.1 证据分组

- `01`–`05`：自动化助手产品概览、批量执行命令、命令状态，以及 ADP 工作流/知识库文档。
- `06`–`11`：ADP Agent 配置、Agent 调试、引用 Agent、节点配置、工作流调试。
- `12`–`19`：WeData 周期/事件实例、实例详情、执行日志、实例血缘和操作入口。
- `20`–`25`：WeData 告警文档、监控对象、条件、告警规则列表和通知动作。
- `26`–`38`：ADP 审批中心入口、规则列表/创建、应用发布门禁、申请详情、流程与审批动作。

### 2.2 证据等级

本文使用以下措辞边界：

- **截图可见：** 直接从截图中的标题、控件、标签、列名、状态或操作菜单总结。
- **UI 推断：** 根据多个页面的共同结构提炼出的设计模式，不等同于腾讯内部 API 或数据库契约。
- **RAG4C 建议：** 结合 `CONTEXT.md`、Stage 25 设计稿和现有 Tenant/Revision/Digest 约束提出的本项目方案，不是对腾讯实现的断言。

截图中的账号、任务名和应用名存在模糊处理或示例化；本文不复述个人身份、真实密钥或具体业务数据。

## 3. ADP：Agent / 工作流编排与调试

### 3.1 信息架构

ADP 的截图呈现出两层入口：

1. **平台级入口：** 左侧按“应用开发、知识库、Widget、插件市场、模型广场、提示词模板、应用模板、原子能力、平台管理”等产品域分组；审批中心从账号/企业管理入口进入，而不是混在单个 Agent 的局部设置里。
2. **应用/Agent 工作面：** 应用列表、Skill/Agent 详情、工作流编辑器、调试面板和企业共享/审批设置各自是独立工作面；编辑、调试、发布/共享和审批不是同一个状态。

ADP 文档侧又体现了“文档中心 → 产品域 → 章节 → 右侧目录”的层级：左侧树用于定位产品范围，面包屑用于确认上下文，右侧目录用于页面内跳转。对 RAG4C 来说，这对应“Knowledge Base/Task/Automation 主上下文 + 局部 Tabs/Drawer”，而不是把所有能力堆到一个 Dashboard。

### 3.2 工作流创建路径

截图可见的 Agent/工作流流程大致是：

```text
选择或配置 Agent
  -> 单 Agent / 全局调试
  -> 在工作流画布中引用已有 Agent
  -> 配置节点参数
  -> 调试工作流
  -> 再查看 Agent 工具执行结果
```

具体 UI 形态：

- **Agent 配置面板：** 上方显示 Agent 协同方式/工作流编排，下方按卡片组织模型、转交描述、提示词、插件等配置；提示词和工具调用是同一个可编辑对象的组成部分。
- **Agent 调试：** 左侧是对话与运行过程，右侧是“实时观测”或工具执行返回的结构化 JSON；调试菜单可切换全局调试、单 Agent 调试和多个 Agent 的调试对象。
- **引用 Agent：** 画布左侧节点面板包含参数提取、选项卡、大模型、知识检索、插件、工具、代码、工作流、Agent、变量聚合等节点类型；选择 Agent 后从候选列表中“引用”，而不是在画布内复制一份完整 Agent 定义。
- **节点配置：** 中间是节点连线与画布，右侧是选中节点的属性面板，顶部提供设置/调试等入口；节点可查看 Agent 名称、描述、模型、提示词、插件和输出变量。
- **工作流调试：** 编辑器仍保留画布和节点选中态，调试是一个明确的操作入口，而不是保存后自动运行。

### 3.3 列表密度与状态

ADP 工作流本身不是密集表格，而是“左侧节点库 + 中央拓扑 + 右侧属性”的三栏编辑器。它的密度来自可见配置项和节点关系，而不是大量资源行。调试结果则采用对话气泡、工具执行标签和 JSON 输出并排展示，强调“输入—工具—输出”的局部证据。

截图没有给出完整的 ADP 工作流运行状态枚举；可以确认的是：

- 编辑态和调试态有明显入口区分；
- 工具执行返回包含 `Code`、`Msg`、`Data` 等结构化字段；
- Agent/工具结果可以在调试面板中展开查看；
- 企业共享设置存在“启用/未启用”和提交审批提示，说明“配置已保存”与“已被企业范围使用”不是同一事实。

### 3.4 对 RAG4C 的借鉴与边界

**可以借鉴：**

- 左资源库、中间流程、右属性的空间分工；
- 节点/步骤有稳定名称、描述和输出变量；
- 调试结果把过程和结构化返回同时展示；
- 引用已有 Agent/能力，而不是复制定义；
- 发布/企业共享前进入独立审批门。

**不可直接照搬：**

- 自由拖拽、任意连线和任意节点类型不能等同于可执行规则；
- “代码”“工具”“插件”“Agent”节点如果允许用户自行注入脚本、URL、Prompt 或凭据，会越过 RAG4C 的请求边界；
- 图上连通不等于权限、Tenant、版本或 source revision 校验通过；
- 画布调试的结构化输出不应成为知识真相，必须回到 RAG4C 的 Source → Parse → Chunk → Projection → Retrieval → Citation → Answer → Evaluation Lifeline。

## 4. 自动化助手（TAT）：批量命令、实例选择与状态机

### 4.1 信息架构

自动化助手文档把能力分成：

- 产品概述；
- 批量执行命令；
- 交互式会话管理；
- 公共命令库；
- 操作指南、命令状态、上传文件、托管实例以及执行命令与本地执行的差异。

这说明 TAT 的核心对象是 **命令执行任务**，而不是一次性的按钮点击。文档、控制台和实例日志形成连续的操作路径：创建/选择命令 → 选择实例 → 提交执行 → 查询状态 → 查看日志/退出码。

### 4.2 批量执行的列表与操作

截图可见的批量执行模式有两个入口：控制台批量执行和 API 批量执行。控制台路径包含：

1. 在个人命令或命令列表中进入执行；
2. 选择实例所在地域，并在行操作中进入执行；
3. 在“选择执行实例”列表中多选实例；
4. 检查实例状态、可用区、实例类型、主/内网地址、实例计费模式、所属项目、标签和客户端状态；
5. 提交执行；完成后回到命令所在行的日志/执行详情。

这是典型的“批量选择 + 受限动作”模式：表格承载目标集合，勾选决定作用范围，按钮决定动作；并没有把命令执行做成画布或聊天指令。

### 4.3 状态模型

命令状态表是本批截图中最清楚的状态证据之一：

- `PENDING`：等待下发，命令正在等待系统下发；
- `DELIVERING`：下发中，命令正在下发给选中的执行实例；
- `DELIVER_DELAYED`：延时下发；
- `DELIVER_FAILED`：下发失败；
- `RUNNING`：执行中；
- `TIMEOUT`：命令超时，命令未在设置的时间内完成；
- `SUCCESS`：命令成功，但截图明确提示仍需结合实际输出（Output）和退出码（ExitCode）判断结果是否符合预期；
- `TASK_TIMEOUT`：命令执行超时，持续执行的任务被自动终止；
- `FAILED`：命令无法执行或执行失败。

这组状态包含三个有价值的层次：

```text
等待/投递阶段  -> 执行阶段 -> 结果阶段
PENDING         -> RUNNING  -> SUCCESS / FAILED / TIMEOUT
DELIVERING      ->          -> DELIVER_FAILED / TASK_TIMEOUT
```

尤其要保留 `SUCCESS ≠ 业务正确` 这个语义。自动化请求成功落地，只能证明系统执行链完成，不能自动证明 RAG4C 的知识、索引或评测结果正确。

### 4.4 对 RAG4C 的借鉴

- 将 `requested / dispatched / applied / rejected / expired` 分开，不用一个“成功”吞掉请求、执行和业务效果；
- 详情中保留执行输出摘要、退出码/安全错误和目标 revision/digest；
- 批量操作必须显示选中数量、Tenant/资源范围和动作门禁；
- 所有“重试/取消/终止”按钮根据当前状态服务端决定是否存在；
- 任意命令内容、原始输出、凭据和远程地址都不能进入通用自动化规则的可执行参数。

## 5. WeData：实例运营、日志、血缘与告警规则

### 5.1 信息架构

WeData 截图体现出一个完整的运行运营工作面：

```text
运维/实例入口
  -> 周期实例 | 事件实例
  -> 过滤与列表
  -> 行操作：查看日志 / 查看血缘 / 更多
  -> 实例详情
      -> 执行日志 | 实例血缘 | 操作记录
```

告警是另一条并列但相互关联的资源路径：

```text
任务告警
  -> 监控对象
  -> 告警条件
  -> 告警方式
  -> 告警规则列表 / 告警信息列表
```

因此，运行实例、告警规则和告警信息是不同事实层；告警不是把实例详情复制一份，而是对运行事实的可配置观察。

### 5.2 列表密度

WeData 的实例列表是本批截图中最密集的页面类型：

- 顶部先放批量按钮：终止、重跑、置成功；
- 下一行放任务名称/ID、时区、计划调度时间、开始/结束时间、负责人、文件夹等筛选；
- 表头同时承载任务名称、计划调度时间、执行状态、实例类型、负责人、所属工作流、任务类型、开始时间、结束时间和操作；
- 右侧操作区保持稳定，常见入口是“查看日志、查看血缘、更多”；
- 底部显示总条数、每页条数、分页；截图可见大数据量分页（例如数百页），说明该页面以长期运营列表为目标，而不是展示少量最近运行。

视觉上是“浅灰表头 + 细分隔线 + 低饱和状态点 + 蓝色文本操作”的控制台密度。重要的是，密集并不意味着无层次：当前筛选、批量操作、行操作、分页和详情入口分别占据固定区域。

### 5.3 状态与可操作性

截图可见的执行状态包括：

- `等待上游（诊断）`，旁边有告警提示图标；
- `跳过运行`；
- `成功`；
- 失败/失败重试/正在终止等状态在详情图例和阶段条中出现。

行级“更多”菜单示例包含：终止、重跑、置成功、紧急去除依赖、任务开发等。它说明操作不是固定工具栏，而是按当前实例上下文提供；批量按钮也与选中行联动。

RAG4C 应把“紧急去除依赖”“置成功”视为高风险动作：它们不能只因页面有按钮就可执行，必须有权限、原因、二次确认、审计和 source authority 的明确接线。

### 5.4 实例详情：尝试历史与阶段时间线

WeData 详情把一个实例拆成多个证据面：

- **左侧执行历史：** 第 5 次、第 4 次、第 3 次等历史执行；每次带运行时间、运行类型和状态，例如失败、失败重试、成功运行或运行暂停。
- **顶部阶段条：** `等待事件/上游 → 等待运行 → 任务运行`，每一阶段显示耗时/起止时间；失败阶段用红色标记，成功阶段用绿色标记。
- **任务日志 / 运行代码：** 日志与运行代码是同一实例详情中的并列 Tab，而不是跳到不可追踪的外部页面。
- **控制动作：** 任务详情、终止、更多等入口位于详情顶部；日志页面也显示日志大小、下载日志、刷新频率和自动刷新开关。

这种结构对 RAG4C 很重要：一次运行可能有多次 attempt，attempt 的失败不能覆盖历史；当前运行的阶段、总状态和原始日志也不能混为一个布尔值。

### 5.5 日志工作面

截图显示的日志能力包括：

- 日志级别筛选；
- 刷新频率选择；
- 手动刷新；
- 自动刷新开关；
- 日志行号、时间戳、级别、线程/类名和消息；
- 在日志中查找，带查找框和结果导航；
- 右侧缩略图/ minimap；
- 日志大小和下载日志；
- 在某些实例上同时显示执行 ID、TaskType ID、Cluster ID、进程命令、退出码和 worker 服务信息。

这是一种“可操作的日志”，不是一次性文本 dump。RAG4C 可以借鉴筛选、查找、下载和 attempt 历史，但必须对原始日志做安全投影，防止 token、凭据、原始文档内容、请求体和内部地址进入规则详情或截图。

### 5.6 血缘与节点详情

实例血缘提供两种查看模式：

- `DAG 模式`：用有向图显示上游、下游、跨工作流节点和结束节点；节点颜色/图标表达成功、失败等状态；
- `列表模式`：适合按行查看节点，而不是在大图中定位。

图面提供缩放、适配/搜索等控制；当节点被选中时，右侧或浮层展示任务 ID、所属项目/文件夹、工作流、负责人、任务类型、节点类型、节点状态、开始时间和结束时间。`For-each` 场景还显示“已循环 1/6 次”的实例列表，可以选择具体循环并查看子节点实例日志。

对 RAG4C 的对应关系是：

```text
Knowledge Lifeline graph = 证据关系图
Automation rule graph    = 规则/请求关系图
```

二者不能混淆。自动化规则可以引用一个 Release Quality 告警或 Task Event，但不能把画布连线冒充知识来源、chunk revision 或 projection 状态。

### 5.7 告警规则创建

WeData 告警创建是最适合参考的“受限规则表单”：

1. **监控对象：** 单选任务或项目；
2. **配置方式：** 按任务配置、按工作流配置、按项目配置；
3. **添加对象：** 目标表列出序号、任务/工作流/项目名称、负责人、周期类型或配置任务数量，并提供查看、配置白名单、删除等操作；
4. **告警条件：** 每行包含指标选择、阈值、单位/数量、周期、连续满足次数等字段；截图示例表达了“超过某百分比，且累计实例数超过某数量，连续若干周期满足”的组合条件；支持添加条件；
5. **告警方式：** 邮件、短信、微信、电话、企业微信、HTTP、企业微信群、飞书群、钉钉群、Slack 群、Teams 群等多个通道，并可配置/测试 WebHook。

可借鉴的是“目标—条件—通道”的分段表单、条件行可增删、以及对连续周期和计数的明确表达。不可照搬的是开放式 WebHook 和多通道任意地址：RAG4C 的自动化只应生成经过 allow-list 校验的通知请求，真正投递由 Notification authority 负责。

## 6. 审批中心：规则治理、申请详情与审批时间线

### 6.1 信息架构

审批中心在 ADP 中具有独立的企业治理入口：

```text
应用开发 / 平台管理
  -> 企业管理
      -> 审批中心
          -> 审批列表
          -> 审批规则设置
          -> 审批申请详情
              -> 申请详情 | 审批流程
```

应用列表会暴露发布门禁状态：常规行操作有查看、调用、更多；处于审批中的应用会出现“撤回审批、审批进度”等上下文动作。这说明审批中心与应用发布列表是两个界面，但通过申请 ID/应用对象关联，而不是把审批过程嵌在一个不可复用的弹窗里。

### 6.2 审批规则列表密度

规则设置页面采用标准企业表格：

- 顶部有“创建审批规则”；
- 右侧提供状态筛选、规则关键词搜索和刷新；
- 表头包含审批规则名称、描述、审批类型、审批范围、审批人、通知方式、启用状态、层级/节点和操作；
- 行操作为编辑、删除等轻量管理动作；
- 审批列表则以空间、审批状态、审批类型、我的审批和搜索作为筛选条件，行内显示审批申请、类型、空间、审批状态、审批备注、当前审批人、最后审批时间、申请人和“申请详情”。

截图中的审批列表每页显示 15 条，底部保留总数与分页。这种密度比 WeData 实例表更克制，但字段顺序仍是“对象—范围—状态—当前责任人—时间—下一步”。

### 6.3 审批规则创建

截图可见的规则创建抽屉/侧面工作区包含以下层次：

1. **基本信息：** 规则名称、启用开关；
2. **审批类型：** 应用发布、Skill 企业共享发布等类型；
3. **审批范围：** 工作空间选择、应用/Skill 范围选择；截图示例支持“添加 1/10”一类的范围条目；
4. **审批人：** 企业用户角色、空间用户角色、指定用户三种来源；指定用户可多选并设置会签/或签等关系；
5. **通知方式：** 通知中心为默认方式，并列出短信、邮件、企业微信、钉钉、飞书、Webhook 等可配置入口；未授权/未配置通道会被置灰或显示配置指南；
6. **保存与取消：** 规则创建是显式提交，不是切换开关后立即生效。

这是一种“类型 + 范围 + 审批人 + 通知”的权限表单。对 RAG4C 来说，规则 revision 应保存这些选择的规范化快照和 digest；修改规则不应覆盖既有审批历史。

### 6.4 申请列表与发布门禁

应用发布列表截图显示：

- 应用有标准模式或 Multi-Agent 模式；
- 状态至少区分运行中、未上线以及新增审核中等门禁状态；
- 处于审核中的资源可显示撤回审批、审批进度；
- 常规资源仍保留查看、调用、更多等操作；
- 审批中心的列表可以跨不同申请类型按空间、状态、类型和“我的审批”筛选。

关键语义是：**提交审批不等于已发布，审批中不等于失败，撤回审批也不等于删除申请。** RAG4C 的 `requested`、`blocked`、`approved`、`rejected`、`expired` 必须分开，不能用一个 `active` 标签代替。

### 6.5 申请详情与变更差异

申请详情页以“待发布设置”为核心证据：

- 顶部显示审批类型、资源名称/版本、申请人、申请时间、所属空间、申请备注和变更说明；
- 变更列表按设置项、最后更新时间、变更状态、变更详情展示；
- 典型设置项包括应用名称、头像、欢迎语、示例问题、标准模式模型参数、多模态/多轮等配置；
- “修改”状态用颜色点或文字标识，右侧给出变更后的安全摘要；
- 审批人可在详情中通过、驳回，或先去体验/查看目标资源。

这种差异视图比只显示“有一个审批请求”更有用：它告诉审批人到底要批准什么。对 RAG4C，审批详情应该展示规则 revision、触发事件摘要、条件结果、目标资源和请求动作的 allow-list 摘要，而不是原始 prompt、文档正文、凭据或任意 payload。

### 6.6 审批时间线与责任人

审批流程截图展示垂直时间线：

```text
提交审批
  -> 审批 / 会签
      -> 指定审批人或空间创建员待审批
```

时间线节点包含提交时间、提交人、审批类型/会签关系和当前“审批中/待审批”状态。申请详情与流程是两个 Tab，分别回答“申请了什么”和“现在卡在哪一步”。

这对 RAG4C 的启示：

- `REQUEST` 需要单独的审批关联 ID；
- 每个审批节点要有责任人/角色、状态和时间；
- “当前审批人”与“历史审批人”要分开；
- 通过/驳回/撤回是动作事实，需要 actor、时间、理由和 revision fence；
- 审批完成后还要回到 action request 的目标 revision/digest 校验，不能凭一个“通过”按钮直接写源域状态。

## 7. 跨产品的可复用 UI 模式

### 7.1 信息架构：四层对象模型

四个产品域可抽象成四层：

1. **治理层：** 应用/工作空间/项目/规则/审批策略；
2. **定义层：** Agent、工作流节点、告警条件、审批范围和审批人；
3. **运行层：** 命令、周期实例、事件实例、审批申请、运行 attempt；
4. **证据层：** 输出、退出码、日志、DAG/血缘、变更差异、审批时间线和操作记录。

RAG4C 不应把这四层压成一张“自动化配置”表。页面导航也应保持：

```text
Automation Center
  ├─ Rules
  ├─ Runs
  ├─ Requests
  └─ Activity / Evidence
```

规则详情内部再展示 `Definition / Revisions / Runs / Requests / Events`，既可以快速定位，也不会把审计历史藏在画布后面。

### 7.2 列表密度：密集但可解释

建议沿用以下密度原则：

- 首行是页面标题、作用域/空间、只读或权威状态；
- 第二行是 1 组批量动作或创建动作；
- 第三行是搜索、时间、状态、范围、负责人等筛选；
- 表格主列保留对象名称、状态、时间和下一步；
- 右侧操作区固定，详情/日志/血缘/审批进度不要隐藏到不可发现的 hover；
- 复杂字段采用二级信息或 Drawer，而不是把所有 JSON 展开到表格；
- 大量数据用分页、可选每页条数和总数，空列表、读取失败、权限不足、部分不可用分别表达；
- 移动端把“对象—状态—时间—下一步—作用域”转换为可扫读卡片，不能仅缩小桌面表格。

不要复制腾讯颜色或私有控件，只复用“密集、克制、动作可见、状态可解释”的控制台语义。

### 7.3 状态：状态文字 + 证据 + 允许动作

RAG4C 可以采用如下状态分层；这不是腾讯原始枚举，而是基于截图模式和 Stage 25 既有设计的本项目建议：

```text
Rule lifecycle:
  draft -> active -> paused

Run lifecycle:
  started -> not_matched | requested | blocked | failed | completed

Action request lifecycle:
  requested -> dispatched -> applied
             -> rejected | expired

Evidence lifecycle:
  observed -> evaluated -> recorded
```

每个状态应同时带：

- 可读状态文字；
- 触发/更新时间；
- 安全原因或下一步；
- 可执行动作（若有）；
- 关联对象 ID、revision/digest 摘要；
- `unknown / stale / unavailable / malformed` 的独立表达。

例如：

- `requested`：已创建请求，但不能显示为已执行；
- `blocked`：显示阻断原因和需要人工处理的入口；
- `failed`：显示 safe error、attempt 和是否允许重试；
- `replayed`：显示已找到相同事件/幂等摘要，没有创建重复请求；
- `unavailable`：说明无法验证哪一个 authority，禁止提供绕过性按钮。

### 7.4 规则创建：分段表单优于 DSL/脚本

RAG4C Rule Builder 应采用四段式：

1. **WHEN：** 选择明确触发源与事件类型；
2. **IF：** 选择有限条件并填写校验过的标量参数；
3. **REQUEST：** 选择 1–4 个内置动作，配置目标引用和是否需要审批；
4. **EVIDENCE：** 预览将记录哪些安全字段、哪些 revision/digest 和哪些事件。

每段都应支持：

- 当前配置预览；
- 错误/缺失/不可用状态；
- 只读 authority 摘要；
- 服务器返回的 allow-list 选项；
- “保存草稿”“创建新 revision”“预览”而不是直接运行。

不提供：表达式输入框、任意 JSON payload、脚本编辑器、动态 URL、任意 WebHook、模块路径、任意 Prompt 或凭据输入。

### 7.5 详情、日志、血缘和审批时间线的拼接

建议把一个规则/运行详情 Drawer 组织成：

```text
Overview
  -> Definition / Revision
  -> Trigger event
  -> Condition result
  -> Action requests
  -> Evidence timeline
  -> Related Task / Release / Approval / Notification
```

- **Overview：** 规则名、当前 revision、生命周期、作用域、更新时间和安全摘要；
- **Definition/Revision：** 不可变定义，显示 digest 和创建人/时间；
- **Trigger event：** 来源 authority、事件类型、source ID、sequence/digest 和 observed time；
- **Condition result：** matched/not matched、条件参数摘要、评估时间；
- **Action requests：** 每一步的 action code、状态、目标 revision/digest、审批关联和 safe reason；
- **Evidence timeline：** `rule_created → trigger_observed → run_started → condition_not_matched/action_requested → run_completed/run_failed`；
- **Related：** 深链回 Task、Release Quality、Approval、Notification 等权威页面，不在自动化中心复制业务状态机。

日志可以提供行号、级别筛选、搜索、刷新/自动刷新和下载；血缘可以提供 DAG/列表两种模式。但两者都应是安全投影：不在浏览器展示原始 query、文档正文、token、credential、Idempotency-Key 或任意请求体。

## 8. RAG4C 的 WHEN → IF → REQUEST → EVIDENCE 设计

### 8.1 WHEN：来源权威事件适配器

`WHEN` 不接收客户端任意事件，也不开放通用 ingest endpoint。每个触发器绑定一个显式 adapter，读取一个已存在的 Tenant-scoped authority，返回规范化安全事件包。

建议的初始触发器：

- `task_failed`；
- `task_source_stale`；
- `source_sync_failed`；
- `release_quality_alert_opened`；
- `release_recertification_blocked`；
- `approval_request_terminal`。

事件包至少包含：Tenant、source kind/id、source revision/digest、事件序号/摘要、状态、severity/action-required 摘要、observed time 和关联对象 ID。通知事件不作为 v1 自身触发器，避免“通知失败 → 再发通知”的自激循环。

### 8.2 IF：受限、可验证的条件

条件只允许有限 code 和按 code 校验的参数：

- `always`；
- `status_is`；
- `action_required`；
- `severity_at_least`；
- `attempt_exhausted`；
- `source_is_stale`。

不引入 SQL、JavaScript、Python、正则脚本、通用表达式解释器或查询语言。条件 evaluator 应是纯函数：同一个 trigger event、rule revision 和参数，得到同一个 `matched / not_matched / blocked` 结果，并产生稳定的 condition digest。

### 8.3 REQUEST：有限动作与审批接线

初始动作建议保持在既有领域 authority 能接住的范围：

- `notify_operator`：生成 Notification request，不直接投递；
- `request_approval`：关联现有 Approval Control，不自己签发 ticket；
- `open_task_attention`：生成受限的 Task attention/request，不直接改 Task source row；
- `pause_rule`：唯一的本地元数据动作，也要 revision-fenced。

每个 action request 应带：

- `tenant_id`、`rule_id`、`rule_revision_id`、`run_id`；
- `step_index`、`action_code`；
- `target_kind/id/revision/digest`；
- request fingerprint/idempotency digest；
- safe params 和 safe reason；
- approval request ID、notification ID 或 task action ID（若已关联）；
- requested/dispatched/applied/rejected/expired 时间。

自动化中心只创建 durable `requested` 事实，执行交给显式的 domain adapter 或人工审批。它不能直接执行 Task retry/cancel、source sync、notification delivery、Approval ticket consumption、external webhook 或源域状态更新。

### 8.4 EVIDENCE：两条链，不混账

RAG4C 至少保留两类互相引用但不互相冒充的证据：

1. **源域证据：** Task Event、Source Sync、Release Quality、Approval 等各自的 authority/event chain；
2. **自动化证据：** Rule revision、trigger observed、run、action request、审批关联和 automation event chain。

一条自动化事件链建议表达：

```text
rule_created
  -> revision_created
  -> revision_activated
  -> trigger_observed
  -> run_started
  -> condition_not_matched | action_requested
  -> run_completed | run_failed
```

对每个节点记录：sequence、previous digest、event digest、actor/request ID（若存在）、安全快照和 occurred time。前端不必展示全部内部 JSON，但必须能回答：

- 哪条规则的哪个 revision 触发了？
- 读到了哪个来源事实？
- 条件为什么匹配/不匹配？
- 产生了什么请求，目标 revision 是什么？
- 是否需要审批，当前由谁负责？
- 是否重复事件，是否避免了重复请求？

### 8.5 示例规则

```text
规则：Release 质量告警进入人工处理

WHEN
  release_quality_alert_opened
  source = Release Quality authority

IF
  severity_at_least(important)
  AND action_required = true

REQUEST
  1. notify_operator
  2. request_approval
  3. open_task_attention

EVIDENCE
  rule_revision_digest
  trigger_event_digest
  source release_id + manifest_digest + channel_id
  condition_digest
  three action request fingerprints
  approval_request_id / notification_id / task attention id
  automation event sequence
```

该示例的重点不是“自动修复质量问题”，而是把问题安全地送入既有 Notification、Approval、Task 和 Release Quality authority；任何自动化执行结果都必须回到这些 authority 产生新的可验证事实。

## 9. 不可照搬的自由画布与任意执行风险

### 9.1 自由画布风险

ADP 画布提供很好的编排可视化，但不适合作为 RAG4C 的规则真相，原因包括：

- **隐式控制流：** 连线、分支、循环、默认节点和错误分支可能不在一屏内，审批人难以判断完整执行语义；
- **图与权限脱节：** 一个节点在画布上可见，不代表当前用户、Tenant、目标资源或 source revision 有执行权；
- **视觉状态伪装：** 节点绿色可能只代表上次调试成功，不能代表当前 rule revision active 或目标 authority current；
- **循环与爆炸：** 任意循环、批量 fan-out、递归 Agent 或 for-each 会产生不可控成本、重复请求和难以审计的副作用；
- **版本不稳定：** 直接编辑画布容易覆盖历史定义，无法回答某次请求使用的到底是哪一个配置；
- **证据丢失：** 画布只表达意图，不天然保存 trigger digest、target revision、审批动作和幂等结果。

因此 RAG4C 可以有“流程预览图”，但它只读、由规范化 rule schema 投影生成，不能通过任意拖拽直接授予执行能力。

### 9.2 任意执行风险

禁止把以下字段开放给规则作者或客户端：

- Shell、PowerShell、Python、JavaScript、SQL 或其他脚本；
- 任意 URL、Webhook、HTTP body、HTTP header、重试策略或 DNS/内网地址；
- 模块路径、函数名、动态 import、反射类名；
- 任意 Prompt、模型参数、工具名或 Agent 运行计划；
- access token、secret、credential、cookie、ticket 或可逆编码；
- 原始 query、文档正文、chunk 内容、完整日志和未经投影的 response body；
- 任意工单/外部系统字段或客户端自定义 metadata。

WeData 的多通道通知和 WebHook 配置说明了“可配置”与“安全可执行”之间的边界：RAG4C 可以引用已注册的 Notification channel，但不能让 Rule Builder 接受一个任意的 WebHook 地址。TAT 的 `SUCCESS` 也说明执行成功不等于业务结果正确；RAG4C 更不能把“请求已生成”显示成“知识已修复”。

### 9.3 审批绕过风险

审批中心的截图展示了规则范围、审批人、会签/或签、通知方式、申请详情和时间线。RAG4C 应保留这些治理语义，但必须明确：

- 自动化只能关联现有 Approval Request；
- 自动化不保存明文 execution ticket，也不接受客户端自行提交的 ticket；
- 审批通过后仍需重新校验规则 revision、目标 source revision/digest、请求未过期和幂等键；
- 审批通过不等于 Task/Release/Source 已经成功变化；变化要由源域 authority 产生事实并回流。

## 10. 面向 RAG4C 的页面落地建议

### 10.1 自动化中心首屏

建议首屏只有一个明确的运营结构，不做统计卡墙：

```text
Header + Tenant / authority badge
Attention Board
WHEN -> IF -> REQUEST -> EVIDENCE rail
Rules | Runs | Requests | Activity
```

Attention Board 只放可操作的事实摘要：运行中、待审批、阻断、失败、过期/陈旧、重放。每项带对象、状态、时间、原因和深链。

### 10.2 Rule Builder

- 使用 TDesign 表单、Tabs、Descriptions、Tag、Alert、Drawer、Dialog、Select、Empty 和 Loading 等标准控件；
- 用分段表单代替自由画布；可以有只读关系预览，但不能拖拽生成任意动作；
- 每次修改创建新的 immutable revision；当前定义显示 digest；
- 预览只做读取和纯判断，不观察真实事件、不推进 cursor、不创建 Run/Request；
- 保存、激活、暂停、创建 revision 都是显式动作，并按当前 revision fence 做并发保护。

### 10.3 Run / Request / Evidence 详情

- `Run` 详情参考 WeData：左侧 attempt 历史，顶部阶段 rail，中间结果和安全日志；
- `Request` 详情参考审批中心：目标、动作、审批状态、责任人、更新时间、过期时间和变更/请求摘要；
- `Evidence` 详情参考 WeData 血缘：提供事件链列表和可选关系图，但不把图当成 source truth；
- 日志提供 level、搜索、刷新、下载和安全截断；
- 移动端使用卡片/抽屉，保留状态、作用域、revision、时间和下一步，不显示固定宽表。

### 10.4 必须保留的空态/异常态

- 真实空列表；
- 读取失败；
- authority unavailable；
- stale/malformed digest；
- read-only；
- pending approval；
- blocked request；
- replayed event；
- partially available evidence。

这些状态不能合并为“暂无数据”或“运行成功”。

## 11. 截图清单

以下链接均指向本地采集目录；文件名按目录实际枚举保留。

1. [01 `tencent-docs-automation-assistant-product-overview.png`](../../output/playwright/tencent-stage25-automation-research/01-tencent-docs-automation-assistant-product-overview.png) — 自动化助手产品概览：批量执行命令、交互式会话管理、公共命令库。
2. [02 `tat-batch-execute-commands-doc.png`](../../output/playwright/tencent-stage25-automation-research/02-tat-batch-execute-commands-doc.png) — 批量执行命令：前提条件、实例选择、控制台/API 两种入口和后续日志。
3. [03 `tat-command-status-and-state-table.png`](../../output/playwright/tencent-stage25-automation-research/03-tat-command-status-and-state-table.png) — 命令状态表：PENDING、DELIVERING、RUNNING、SUCCESS、FAILED、TIMEOUT 等。
4. [04 `adp-workflow-orchestration-doc.png`](../../output/playwright/tencent-stage25-automation-research/04-adp-workflow-orchestration-doc.png) — ADP 工作流编排文档：配置 Agent、调试 Agent、配置工作流、调试工作流。
5. [05 `adp-knowledge-base-document-overview.png`](../../output/playwright/tencent-stage25-automation-research/05-adp-knowledge-base-document-overview.png) — ADP 知识库文档：文档导入、解析/切分/索引相关工作面和步骤化说明。
6. [06 `adp-workflow-doc-agent-config.png`](../../output/playwright/tencent-stage25-automation-research/06-adp-workflow-doc-agent-config.png) — Agent 配置面板：模型、提示词、插件和协同方式。
7. [07 `adp-workflow-doc-agent-debug.png`](../../output/playwright/tencent-stage25-automation-research/07-adp-workflow-doc-agent-debug.png) — Agent 调试：对话过程与工具实时观测/结构化返回。
8. [08 `adp-workflow-doc-canvas.png`](../../output/playwright/tencent-stage25-automation-research/08-adp-workflow-doc-canvas.png) — 画布中的引用 Agent 弹窗和候选 Agent 列表。
9. [09 `adp-workflow-doc-node-config.png`](../../output/playwright/tencent-stage25-automation-research/09-adp-workflow-doc-node-config.png) — 工作流节点配置：左侧节点库、中间画布、右侧属性面板。
10. [10 `adp-workflow-doc-workflow-debug.png`](../../output/playwright/tencent-stage25-automation-research/10-adp-workflow-doc-workflow-debug.png) — 工作流调试入口与节点配置并置。
11. [11 `adp-workflow-doc-debug-agent.png`](../../output/playwright/tencent-stage25-automation-research/11-adp-workflow-doc-debug-agent.png) — 调试 Agent：对话/工具执行/实时返回。
12. [12 `wedata-period-instance-operations-doc.png`](../../output/playwright/tencent-stage25-automation-research/12-wedata-period-instance-operations-doc.png) — WeData 周期实例运营文档：列表、详情、日志、血缘和操作说明。
13. [13 `wedata-operations-entry.png`](../../output/playwright/tencent-stage25-automation-research/13-wedata-operations-entry.png) — 周期实例/事件实例入口、筛选、批量操作和行级操作。
14. [14 `wedata-cycle-instance-list.png`](../../output/playwright/tencent-stage25-automation-research/14-wedata-cycle-instance-list.png) — 周期实例列表：状态、负责人、工作流、时间和更多操作。
15. [15 `wedata-cycle-instance-detail.png`](../../output/playwright/tencent-stage25-automation-research/15-wedata-cycle-instance-detail.png) — 周期实例列表/详情场景：计划时间、跳过运行等状态和分页。
16. [16 `wedata-instance-execution-log.png`](../../output/playwright/tencent-stage25-automation-research/16-wedata-instance-execution-log.png) — 实例执行日志：attempt 历史、阶段时间线、日志/运行代码 Tab。
17. [17 `wedata-instance-lineage.png`](../../output/playwright/tencent-stage25-automation-research/17-wedata-instance-lineage.png) — 实例血缘：DAG/列表模式、节点状态、循环实例和节点详情。
18. [18 `wedata-operation-history.png`](../../output/playwright/tencent-stage25-automation-research/18-wedata-operation-history.png) — 任务日志/运行代码工作面及日志搜索、刷新、下载控件；操作记录 Tab 同时可见。
19. [19 `wedata-event-instance-list.png`](../../output/playwright/tencent-stage25-automation-research/19-wedata-event-instance-list.png) — 实例血缘/事件实例场景：DAG 节点状态与列表视图切换。
20. [20 `wedata-task-alerts-doc.png`](../../output/playwright/tencent-stage25-automation-research/20-wedata-task-alerts-doc.png) — WeData 任务告警文档：监控对象、告警条件、告警方式与告警信息。
21. [21 `wedata-alert-rule-entry.png`](../../output/playwright/tencent-stage25-automation-research/21-wedata-alert-rule-entry.png) — 监控对象按任务配置：任务名、负责人、周期类型和删除。
22. [22 `wedata-alert-rule-create.png`](../../output/playwright/tencent-stage25-automation-research/22-wedata-alert-rule-create.png) — 监控对象按工作流配置：工作流、配置任务数量、任务白名单。
23. [23 `wedata-alert-rule-list.png`](../../output/playwright/tencent-stage25-automation-research/23-wedata-alert-rule-list.png) — 监控对象按项目配置：项目、配置任务数量和任务白名单。
24. [24 `wedata-alert-rule-actions.png`](../../output/playwright/tencent-stage25-automation-research/24-wedata-alert-rule-actions.png) — 告警条件：指标、阈值、数量、周期、连续满足次数和添加条件。
25. [25 `wedata-alert-info-list.png`](../../output/playwright/tencent-stage25-automation-research/25-wedata-alert-info-list.png) — 告警方式：多渠道复选框与 WebHook 配置区。
26. [26 `adp-approval-center-doc.png`](../../output/playwright/tencent-stage25-automation-research/26-adp-approval-center-doc.png) — 审批中心文档：规则、申请列表、申请详情、审批流程和动作。
27. [27 `adp-approval-rule-list.png`](../../output/playwright/tencent-stage25-automation-research/27-adp-approval-rule-list.png) — 从企业管理进入审批中心的入口与应用开发列表。
28. [28 `adp-approval-rule-create.png`](../../output/playwright/tencent-stage25-automation-research/28-adp-approval-rule-create.png) — 审批规则设置列表：类型、范围、审批人、通知方式、启用状态和编辑/删除。
29. [29 `adp-approval-application-publish.png`](../../output/playwright/tencent-stage25-automation-research/29-adp-approval-application-publish.png) — 审批列表：空间、状态、类型、我的审批、当前审批人、时间和申请详情。
30. [30 `adp-approval-application-detail.png`](../../output/playwright/tencent-stage25-automation-research/30-adp-approval-application-detail.png) — 审批规则创建抽屉：审批类型、范围、审批人和通知方式。
31. [31 `adp-approval-status-list.png`](../../output/playwright/tencent-stage25-automation-research/31-adp-approval-status-list.png) — 应用发布列表：运行中、未上线、新增审核中以及撤回审批/审批进度。
32. [32 `adp-approval-publish-flow.png`](../../output/playwright/tencent-stage25-automation-research/32-adp-approval-publish-flow.png) — 审批流程时间线：提交审批、提交人、会签、审批中、待审批。
33. [33 `adp-approval-rejection-detail.png`](../../output/playwright/tencent-stage25-automation-research/33-adp-approval-rejection-detail.png) — 待发布设置变更差异：设置项、最后更新时间、变更状态和变更详情。
34. [34 `adp-approval-timeline.png`](../../output/playwright/tencent-stage25-automation-research/34-adp-approval-timeline.png) — 企业共享设置启用后的审批提示与申请说明。
35. [35 `adp-approval-reviewer-detail.png`](../../output/playwright/tencent-stage25-automation-research/35-adp-approval-reviewer-detail.png) — 审批中的资源提示：通过审批后可用、审批中不可修改等约束提示。
36. [36 `adp-approval-actions.png`](../../output/playwright/tencent-stage25-automation-research/36-adp-approval-actions.png) — 审批申请列表及“申请详情”入口。
37. [37 `adp-approval-application-history.png`](../../output/playwright/tencent-stage25-automation-research/37-adp-approval-application-history.png) — 申请详情：审批类型、资源信息、申请人/时间、空间、申请备注和变更说明。
38. [38 `adp-approval-reviewer-action.png`](../../output/playwright/tencent-stage25-automation-research/38-adp-approval-reviewer-action.png) — 审批详情中的通过/驳回动作和待发布设置变更清单。

## 12. 参考的 RAG4C 本地基线

本文的 RAG4C 转译仅依赖仓库内已有事实，不扩展为业务实现：

- `CONTEXT.md`：Knowledge Lifeline、Document Version、Projection Fence、Task/Approval/Release Quality 等领域边界；
- `docs/superpowers/specs/2026-08-30-enterprise-automation-workflows-design.md`：Stage 25 的 `WHEN → IF → REQUEST → EVIDENCE`、初始 trigger/condition/action 和受限执行边界；
- `docs/research/2026-08-30-stage25-enterprise-automation-database-security.md`：Tenant-leading、immutable revision/event、幂等、source fence、Approval/Task 接线和 fail-closed 约束。

本文不是这些设计稿的替代实现契约，也不要求在本轮修改任何业务源。

## 13. 最终判断

腾讯 ADP 提供了 Agent/工作流编排、引用、调试和发布审批的产品化外壳；WeData 提供了高密度实例运营、attempt 历史、阶段时间线、日志和 DAG/列表血缘；自动化助手提供了清晰的异步命令状态机；审批中心提供了规则范围、审批人、通知方式、申请差异和流程责任链。

RAG4C 应组合这些 **交互语义**，而不是复制某个页面：

```text
密集列表负责找对象
状态门禁负责限制动作
详情工作面负责解释事实
日志/血缘/时间线负责恢复因果
WHEN -> IF -> REQUEST -> EVIDENCE 负责把自动化变成可审计请求
```

最终边界必须保持：规则可配置，但不可任意执行；画布可预览，但不可绕过 authority；审批可关联，但不可替代源域状态机；请求可追踪，但不能伪装成知识已修复。
