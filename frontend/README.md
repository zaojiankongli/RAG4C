# RAG4C Console —— Tauri 桌面控制台

RAG4C 企业级 RAG 系统的桌面前端。基于 **Tauri 2 + React 18 + TypeScript + Vite + TDesign React 1**，
通过 HTTP 调用项目根目录的 FastAPI 桥服务（`server/app.py`）完成真实问答与评测；
知识库导航新增知识概览、知识组织、数据来源和检索调试；文档页提供状态/类型/解析引擎导航、选择态批量操作、统一导入向导和文档—切片—详情三栏切片工作区。
桥服务离线时一般业务页可降级为演示数据；运行历史明确显示不可用/legacy 状态，不注入演示 run。

## 企业级能力

| 维度 | 实现 |
|---|---|
| 设计系统 | 设计 token（CSS 变量）+ TDesign token 与应用组件层统一管理；**明暗双主题**（跟随系统 / 手动切换，localStorage 持久化，`meta theme-color` 同步） |
| 导航架构 | hash 路由（URL 深链 + 前进后退）；**页面保活**（切换不丢状态）；次级页面 React.lazy 按需加载 + Suspense 骨架 |
| 引用 UX（RAG 后） | 答案 **Markdown 渲染**（GFM 表格/代码）；行内 `[N]` 引用 hover 预览、点击联动、键盘可达；信任徽标（ok/exists_only/stale/unsupported）+ 引用汇总徽标；**复制答案 / 重新生成**；弃权提示 |
| 提问体验（RAG 前） | 欢迎页能力卡片；**SSE 流式问答**（阶段事件 + 逐 token 渲染 + 光标动画）；离线演示同样模拟流式；**停止生成在服务端真正生效**；清空对话、滚动到底、消息时间戳与复制 |
| 可视化（RAG 中） | **React Flow + DOM**：服务端/实时运行的执行流程、并行 wave/idle gap/retry 时间线、脱敏事件表与安全 Knowledge 事实；ECharts 继续承担知识图谱、监控与评估图表 |
| 运行历史与 Ops | Runs health/list/detail/events 客户端、运行 sidebar、cursor 分页、long-poll 重连、retention/gap/memory-only/legacy banners、Monitor run_id 深链 |
| 可靠性 | API 客户端统一超时 / 取消 / 结构化错误（ApiError + 服务端 JSON 错误契约）；桥服务连接状态全局 Context；页面级 ErrorBoundary；离线演示数据兜底 |
| 健康联动 | 左侧栏 **服务三级状态**（ok/degraded/down 徽标）+ 组件探活明细（Milvus/嵌入/重排/LLM 槽位）+ **熔断器开合标签**；查询缓存命中徽标 |
| 无障碍 | 图标按钮 aria-label、可交互条目 button 语义 + 键盘操作、`focus-visible` 焦点环、`aria-live` 状态播报、`prefers-reduced-motion` 降级 |
| 工程化 | ESLint flat config + Prettier；TypeScript strict；路由懒加载 + React/ECharts/Markdown 精确分包；`npm run build` 全量校验 |

## 五个页面

| 页面 | 功能 |
|---|---|
| **问答控制台** | 聊天式提问（**SSE 流式**）；Markdown 答案 + `[N]` 引用标记（hover 预览 / 点击联动 / 键盘可达）；来源证据卡片（信任徽标 + 证据片段深链）；弃权提示；链路追踪；ACL 过滤；停止生成 / 重新生成 / 复制 / 缓存命中标记 |
| **链路可视化** | 运行历史 sidebar + 执行流程 / 时间线 / 事件 / 资料关联四标签页；当前 live run 优先实时投影，历史 run 使用服务端 canonical events；保留知识图谱浏览器 |
| **运行监控** | 指标/趋势/熔断 + active/stuck/slow/errors/cancelled 五组运行关注面板；链接以稳定 run_id 深链到 Visualize；Runs API 404 时才使用 legacy 最近提问，disabled/unauthorized/unavailable 均显式展示 |
| **评测中心** | dry-run / 真实管线评测：六项指标 + 逐用例明细 + 历史报告 + JSON 导出 + 运行预期管理 |
| **配置中心** | **解析引擎面板（插拔式）**：引擎选择（自动/显式）、每引擎 启用开关 + 免费/付费模式 + 优先级、付费凭据缺失告警、路由与增强开关、入库切分路由；另有全部配置段表格（值 / 环境变量 / 来源），跨段搜索，编辑保存到 `.env`（密钥字段只读，待保存计数提醒） |

## 目录结构

```
frontend/
├── src/
│   ├── main.tsx              # 入口（渲染 AppProviders）
│   ├── AppProviders.tsx      # Provider 组合层（主题/语言/AntApp/连接状态）
│   ├── App.tsx               # 布局：hash 路由 / 页面保活 / Sider 折叠 / 主题切换
│   ├── styles.css            # 设计 token（CSS 变量，明暗双主题）+ 全局样式
│   ├── theme/
│   │   ├── tokens.ts         # 品牌常量 + TDesign/CSS token（明暗主题）
│   │   ├── useThemeMode.ts   # 主题状态 hook（持久化 + 系统偏好）
│   │   └── useIsDark.ts      # 暗色订阅（ECharts 等非 React 体系同步配色）
│   ├── context/
│   │   └── ConnectionContext.tsx  # 桥服务在线状态全局共享
│   ├── charts/
│   │   └── EChart.tsx        # ECharts 封装（init/dispose/resize/事件/按需注册）
│   ├── types/rag.ts          # 与 models/schemas.py 对应的 TS 类型
│   ├── api/
│   │   ├── client.ts         # HTTP 客户端（超时/取消/ApiError）
│   │   └── mock.ts           # 演示数据（离线兜底）
│   ├── components/
│   │   ├── PageHeader.tsx    # 统一页头（h2 层级 + 说明 + 操作区）
│   │   ├── ErrorBoundary.tsx # 页面级错误边界
│   │   ├── SystemSidebar.tsx # 左侧栏（连接状态/配置/ACL/示例）
│   │   ├── AnswerCard.tsx    # 回答卡片（Markdown + 引用 UX 核心）
│   │   ├── ChatSkeleton.tsx  # 回答骨架屏
│   │   ├── PhaseStatus.tsx   # 请求阶段状态（真实耗时驱动）
│   │   ├── PipelineFlow.tsx  # RAG 链路流程图（ECharts 双路径 + 回放）
│   │   └── GraphExplorer.tsx # 知识图谱力导向图（ECharts）
│   └── pages/                # 问答/可视化/监控/评测/配置（次级页面懒加载）
├── scripts/screenshot.mjs    # Playwright 截图验收脚本（.shots/ 输出）
├── src-tauri/                # Tauri 2 壳（Rust 侧保持最小化）
├── eslint.config.js          # ESLint 9 flat config
└── package.json
```

## 运行

前置：Node ≥ 20、Rust ≥ 1.77（Tauri 2）、Windows 需 WebView2（Win10/11 自带）。

```bash
cd frontend
npm install

# 浏览器预览（快速看 UI，桥服务离线时为演示数据）
npm run dev          # http://localhost:1420

# Tauri 桌面应用（开发模式）
npm run tauri dev

# 打包安装程序（Windows MSI/NSIS）
npm run tauri build

# 质量检查
npm run lint         # ESLint
npm run format       # Prettier
node scripts/screenshot.mjs   # 视觉验收截图（需系统 Edge）
```

### 对接真实 RAG 链路

1. 先按项目根目录 README 安装依赖并配置 `.env`（Milvus / Ollama 等）；
2. 启动桥服务：

   ```bash
   uv run uvicorn server.app:app --host 127.0.0.1 --port 8000
   ```

3. 前端左侧栏状态变为「真实链路」即可提问 / 评测 / 看图谱。
   桥服务地址默认 `http://localhost:8000`，可在「设置 → 后端服务地址」修改
   （存本机 localStorage，不写入 `.env`；留空恢复默认）。打包后的桌面端没有
   开发者工具，因此不要再依赖控制台里的 `localStorage.setItem`。

## Runs 历史的数据优先级与恢复

- 选择当前 live run 时，typed events 投影优先；typed 不可用时才使用既有 coarse phase/trace 投影。选择其他 run 时使用服务端 history topology + canonical events。
- 同一 live run 的本地有界 raw-event buffer 立即可见；服务端页补齐缺失 seq。只有服务端事件从 1 到 `latest_seq` 连续完整时，服务端快照才成为 authoritative；不同 run 的 buffer 永不合并。
- events 默认每页 200，终态 1000 events 会以 0/200/400/600/800 cursor 连续补齐；活动 run 使用最长 25000ms long-poll。409 gap 从 `earliest_available_seq - 1` 恢复并标记 partial；已知 run 后续 404 标记 retention expired。
- 四个 detail tabs：`process`、`timeline`、`events`、`knowledge`。URL 独立保存 `run/node/tab/view/follow`，支持 direct path 与 hash 部署、前进后退及 Monitor 深链。
- banners 独立表达 disabled、unauthorized、legacy、unavailable、Registry degraded、reconnecting、live transport desync、partial history、memory-only、partial/unavailable persistence、retention expired；状态不以颜色为唯一信息。
- Monitor 并行请求 active/stuck/slow/errors/cancelled。临时 network/5xx 失败保留最后真实数据并标记 reconnecting；永久 disabled/401/403/404 按能力状态切换，绝不以 demo history 冒充服务端记录。

质量门禁（Route B Task 14，QA 2026-08-24）：`npm --prefix frontend test` 为 30 files / 228 tests，16.74s；ESLint clean；TypeScript + Vite production build 4125 modules，11.02s。浏览器 17 张 required PNG 覆盖 desktop/mobile、light/dark、状态/异常/banner；完整键盘链与 `prefers-reduced-motion` 已验证。稳定加载 axe：Visualize violations 0 / incomplete 3，Monitor violations 0 / incomplete 1；incomplete 为 Ant Tooltip 与 ReactFlow 第三方/manual 项，不作为 confirmed violation。

## Final review 可读性与安全更新（2026-08-24）

- ≥7 节点流程采用 3×3 deterministic grid；fit min zoom 0.72、global min zoom 0.5、drag pan enabled。1366 实测 9/9 nodes visible，font 14px。
- EventTable 外层是 `role=region/tabIndex=0` 的横向滚动区，table min-width 1312px，八列固定为 72/230/160/210/90/110/220/220px；技术列 nowrap，1366 下不逐字折叠。
- Monitor attention duration 使用统一 formatter，例如 `1234.567ms → 1.23s`。
- 受影响截图已重拍并人工验收：6 张 process、`visualize-events-1000-1366-light.png`、两张 Monitor attention。

## API 契约

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 服务状态 + 系统配置（Milvus URI / 嵌入 / 重排 / 生成模型 / 图编排开关） |
| `GET /api/runs/health` | Registry、memory、persistence/WAL、heartbeat、retention 与 scope stability；loopback 默认可读，远程受 Bearer + tenant 保护 |
| `GET /api/runs` | recent/active/slow/errors/stuck 列表，状态/时间/fingerprint 过滤和签名 keyset cursor；默认/最大 50/100 |
| `GET /api/runs/{run_id}` | 冻结 topology、summary、node rollup、history 完整性；未知和跨 scope 同为 404 |
| `GET /api/runs/{run_id}/events` | canonical seq 分页与 long-poll；默认 200/最大 500，wait 最大 25000ms，gap=409，poll cap=429 |
| `POST /api/query` | 问答。body：`{ "query", "acl"?, "retry"? }`，返回 `{ result, using_mock, duration_ms, cached? }`；相同 query+acl 10 分钟内命中服务端缓存 |
| `POST /api/query/stream` | SSE 流式问答：`phase`（retrieving/retrieved/generating/verifying/retrieving_again）+`token` 事件，`done` 事件携带与 /api/query 同构结果；断开即停止生成（服务端关闭 LLM 流） |
| `GET /api/metrics/history` | 持久化指标历史（data/metrics-history.jsonl 尾部快照，60s 粒度） |
| `GET /api/metrics/prometheus` | Prometheus 文本格式指标（接 Grafana 抓取） |
| `GET /api/metrics` | 进程内指标快照（count / mean / p50 / p95 / p99 / error_rate）+ 最近查询（50 条环形缓冲） |
| `POST /api/graph/search` | 实体 / 关系向量检索（图谱可视化）。body：`{ "query", "entity_top_k"?, "relation_top_k"? }` |
| `GET /api/graph/subgraph` | 按 `entity_ids` / `relation_ids` 取子图（`degree` 跳扩展） |
| `POST /api/eval/run` | 运行评测。body：`{ "dataset_spec"?, "pipeline"?, "out"? }`；`pipeline="none"` 为 dry-run，否则如 `"rag:answer_query"` |
| `GET /api/eval/datasets` | 可用评测数据集（扫描 eval/ 目录） |
| `GET /api/eval/results` | 最近一次评测报告（eval/results.json） |
| `DELETE /api/documents/{doc_id}` | 删除单篇文档及其向量片段、图谱派生数据和目录记录；处理中返回 409 |
| `POST /api/documents/batch-delete` | 批量删除最多 100 篇文档，逐项返回成功、忙碌、失败与不存在结果 |
| `GET /api/documents/metrics` | 聚合文档状态、解析/入库耗时、阶段分位数、引擎分布、慢文档与失败记录 |
| `GET /api/config` | 完整配置清单（14 段，脱敏；每字段含 env 变量名 / 来源 / 类型）+ `engines` 解析引擎注册表载荷（choice / active / plugins：name、describe、modes、enabled、mode、priority） |
| `POST /api/config/update` | 保存配置到 `.env`。body：`{ "updates": [{"path", "value"}, ...] }`；白名单路径 + 类型校验 + 密钥字段只读，写入后刷新进程缓存 |
