# RAG4C · KnowledgeOps

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Milvus](https://img.shields.io/badge/Milvus-2.6%2F3.0-00A1FF?logo=milvus&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0%2B-4479A1?logo=mysql&logoColor=white)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

> **企业级 RAG 知识工程平台。**
> 它解决的问题不是"能不能答"，而是"这个答案凭什么能答"——每一条答案都可沿
> **Knowledge Lifeline（知识溯源链）** 回溯：引用 → 检索 → 索引投影 → 切块版本 →
> 文档版本 → 解析任务 → 数据源身份。

---

## 一、它是什么

RAG4C 是一套可自托管的企业知识中台，覆盖从文档接入到答案评测的完整闭环：

- **有据可依的生成**：答案强制携带 `[N]` 引用标记，越界编号在输出前剥离；
  引用经三层验证（存在性 / 文本哈希 / 蕴含判定）后才被认定为可信。
- **宁可弃权，不可编造**：检索分数 + 蕴含分数双阈值门控，低置信度直接弃权并给出原因，
  阈值可用评估集的不可答样本分位数离线校准。
- **MySQL 是唯一业务真账**：向量库与图谱都只是"投影"，不含任何业务事实；
  写入投影必须过 revision fence（版本栅栏），防止陈旧写入覆盖新数据。
- **企业级治理**：租户 / 工作区 / 组织单元多层授权、OIDC SSO、SCIM 用户同步、
  审批门控、知识库发布通道与质量认证、合规留存与审计导出。
- **运行可观测**：canonical RunEvent → 内存 Registry → 异步 SQLite WAL 落盘，
  配 long-poll 与 operator API，支撑前端的实时流程图与监控台。

---

## 二、架构总览

```
                       ┌──────────────────────────────────────────┐
   小程序 / Web / Tauri │  frontend/  React 18 + TS + TDesign      │
   （控制台）            │  Knowledge · Documents · Visualize ·      │
                        │  Monitor · Eval · RetrievalLab · Admin   │
                        └───────────────┬──────────────────────────┘
                                        │ REST + SSE（类型由 OpenAPI 生成）
┌───────────────────────────────────────▼───────────────────────────────────┐
│ server/   FastAPI 桥服务                                                    │
│  /api/query · /api/query/stream · /api/documents · /api/knowledge/*        │
│  /api/enterprise/*（SSO·审批·发布·合规）· /api/runs/*（观测）· /api/eval   │
│  统一错误契约 · 有界执行器 · 查询缓存 · 熔断器 · 远程 Bearer 准入            │
└───────┬──────────────────────────────────────────────────┬────────────────┘
        │                                                  │
┌───────▼───────────────────────────────────┐   ┌──────────▼─────────────────┐
│ rag.py  answer_query（LangGraph 编排）     │   │ RunRegistry → SQLite WAL   │
│  ├ retrieval/  改写·路由·混合检索·重排     │   │ 运行历史 / 恢复 / long-poll│
│  ├ generation/ [N] 引用生成                │   └───────────────────────────┘
│  ├ verify/     三层引用验证 + 双阈值弃权   │
│  └ indexing/   解析·清洗·切分·嵌入·入库    │
└───────┬───────────────────────────────────┘
        │
┌───────▼───────────────────────────────────────────────────────────────────┐
│ core/    Milvus 客户端 · Embedding/Rerank/LLM Provider 注册表 · 目录服务    │
│          追踪 · 指标 · 重试 · 熔断 · Redis 缓存 · 运行历史 · 企业能力模块    │
└───────┬──────────────────────────────────────────────────┬────────────────┘
        │                                                  │
   ┌────▼─────────────┐   ┌──────────────┐   ┌─────────────▼─────────────┐
   │ Milvus / Lite    │   │ MySQL 8 /    │   │ Ollama / OpenAI 兼容端点   │
   │ 稠密 + BM25 混合 │   │ SQLite 目录  │   │ BGE-M3 · BGE-reranker-v2  │
   │ 实体/关系图集合  │   │（唯一真账）  │   │ qwen2.5 等 9 个 LLM 槽位   │
   └──────────────────┘   └──────────────┘   └────────────────────────────┘
```

**单一事实源原则**：MySQL Catalog 拥有身份、生命周期、版本、操作、审计与权限；
Milvus 只拥有检索投影；图谱只拥有图投影；Redis 只拥有有界的瞬态状态；
运行历史只是运维证据，不构成知识事实。

---

## 三、技术栈

| 层 | 选型 |
|---|---|
| 后端框架 | FastAPI + Uvicorn（统一 JSON 错误契约，OpenAPI 为契约单一事实源） |
| 编排 | LangGraph 状态机（`rag_graph.py`），构建/执行失败自动回退顺序编排，两条路径行为一致 |
| 关系库 | MySQL 8（生产）/ SQLite（本地），SQLAlchemy 2.0 + Alembic（`catalog_migrations/`） |
| 向量库 | Milvus 2.6 / 3.0；稠密向量 + **Milvus 内置 BM25 Function** 稀疏检索，RRF 融合 |
| 嵌入 / 重排 | BGE-M3、BGE-reranker-v2-M3（FlagEmbedding），Provider 注册表可插拔 |
| LLM | 9 个槽位（rewrite / router / generation / judge / triplet / hyde / subqueries / stepback / contextual），OpenAI 兼容，默认指向本地 Ollama + qwen2.5，**零 API 成本跑通全链路** |
| 解析 | 可插拔引擎注册表：pdf-inspector（Fast，纯 Rust）、MinerU（CLI/HTTP）、Docling；TSR 表格结构识别 |
| 前端 | React 18 + TypeScript + Vite 6 + TDesign React + ECharts + ReactFlow，Tauri 2 桌面壳 |
| 观测 | 进程内指标（P50/P95/P99 蓄水池抽样）、contextvars 追踪、RunRegistry + SQLite WAL 运行历史 |

代码规模：283 个 Python 模块约 13.4 万行，232 个前端源文件，205 个后端测试文件，
36 个 Alembic 迁移，19+ 个离线冒烟脚本。

---

## 四、快速开始

### 1. 后端

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e ".[milvus-lite,embedding,llm,server,dev]"
```

按需增量安装 extras：

| extra | 用途 |
|---|---|
| `milvus-lite` | 本地 `.db` 文件模式跑 Milvus，零部署 |
| `milvus` | 连接 Milvus server |
| `embedding` | 本地 BGE-M3 + BGE-reranker-v2-M3 |
| `llm` | OpenAI 兼容 SDK（Ollama / vLLM / 网关均可） |
| `graph` | LangGraph 编排（默认路径） |
| `server` | FastAPI + Uvicorn 桥服务 |
| `redis` | 跨进程缓存与 single-flight（**可选**，不装则自动降级为进程内 LRU） |

### 2. 配置

```bash
cp config/.env.example .env
```

所有键以 `RAG4C_` 开头，嵌套段用下划线拼合。最小可用配置（全部指向本地，零成本）：

```bash
RAG4C_MILVUS_URI=./rag4c.db                        # 文件 → Milvus Lite；http(s):// → server
RAG4C_LLM_GENERATION_BASE_URL=http://localhost:11434/v1
RAG4C_LLM_GENERATION_MODEL=qwen2.5
```

MySQL 目录库（生产）：

```bash
RAG4C_CATALOG_DB_URL=mysql+pymysql://user:pass@host:3306/rag4c?charset=utf8mb4
python scripts/init_mysql_db.py                    # 幂等建库建表
```

> 含密码的 `db_url` 在 `/api/config` 中自动脱敏，且禁止通过 `/api/config/update` 修改。

### 3. 启动

```bash
# 后端（默认只监听本机，避免把配置/入库接口暴露到局域网）
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000

# 前端
cd frontend && npm install && npm run dev
```

Milvus server 模式：`docker compose up -d`（数据落在 `./volumes/`）。

### 4. 最小调用

```python
from rag import answer_query

result = answer_query("公司的报销流程是什么？", acl=["fin"])
result.answer        # 带 [N] 引用标记的答案
result.citations     # 逐条引用的验证状态（ok / exists_only / stale / unsupported）
result.abstained     # 是否触发弃权
result.verdict       # 证据块，供评测裁判消费
```

---

## 五、仓库结构

```
rag.py / rag_graph.py / rag_common.py / rag_stream.py   顶层编排（顺序 / 图 / 流式）
config/       pydantic-settings 配置（RAG4C_ 前缀，支持嵌套段）
models/       数据契约（pydantic v2）+ SQLAlchemy ORM（租户·文档·发布·合规等）
core/         基础设施：Milvus·Embedding·Rerank·LLM Provider·追踪·指标·重试·
              熔断·Redis·目录服务·运行历史·企业能力模块
indexing/     解析 → 清洗 → 预切分 → 切分 → 嵌入 → 入库 → 图谱构建 → 对账
retrieval/    查询改写 · 意图路由 · 混合检索 · 重排 · 来源多样性 · 图检索 · 句子窗口
generation/   有据可依的答案生成与引用解析
verify/       三层引用验证 + 事后引用指派 + 双阈值弃权
eval/         评测执行器 · 双裁判 · EvalReport v2（baseline 对比 / 发布门禁）
server/       FastAPI 桥服务 · 知识 API · 企业 API · 观测 API · 安全准入
catalog_migrations/  Alembic 版本化迁移
prompts/      版本化提示词模板（<slot>_v<N>.txt，行为性修改必须升版本）
scripts/      运维脚本与离线冒烟测试
tests/        pytest 回归（企业能力 / 迁移 / API / 一致性）
frontend/     React + Tauri 控制台
docs/         架构说明、策略矩阵、上线演练与验收记录
```

---

## 六、关键机制

### 检索：混合 + 路由 + 增强

- **混合检索**：BGE-M3 稠密向量 + Milvus 内置 BM25 稀疏检索，RRF 融合；
  应用层不生成也不写 sparse 向量（BM25 由 Milvus Function 完成）。
- **意图路由**：embedding 相似度为主，低置信度时由 LLM 兜底，两者取置信度高者；
  任一失败降级 `hybrid`，绝不抛错。
- **查询增强**（默认关闭，按需开）：HyDE、子查询、后退问题；任一失败静默降级。
- **图检索**：实体向量召回 → 子图扩展（按跳距衰减）→ 关系向量召回 → 可选 LLM 重排；
  六类情况（开关关闭 / 未注入 / 失败 / 无命中 / ACL 过滤后为空 / 与混合结果完全重合）
  一律降级为 `degraded=True` 保留混合结果。
- **句子窗口**：命中子块回卷父块（small-to-big），单层不级联，失败原样返回。

### 引用安全：三层防线

| 层 | 检查 | 失败语义 |
|---|---|---|
| L1 存在性 | `[N]` 编号能否映射到证据 | `unsupported` |
| L2 文本哈希 | Milvus 最新 chunk 的 `text_hash` 是否一致 | `stale`（证据已被改/删） |
| L3 蕴含判定 | judge 槽位逐条判定声明能否由证据推出 | 降级 `exists_only` |

免费模型无法做生成期约束解码，因此额外提供**事后引用指派**：
答案完全没有引用标记时，用 BGE-M3 向量余弦相似度（≥ 0.5）把每条声明指派到最相似证据，
再交 L3 把关。L3 支持全量 / 确定性抽样 / 跳过三档成本分层。

### 弃权：双阈值，不用 LLM 自我判断

```
无检索结果            → 弃权（知识库无相关内容）
最高检索分 < 阈值     → 弃权
蕴含分为空或 < 阈值   → 弃权（证据不足以支撑可靠声明）
否则                  → 不弃权
```

阈值可用 `calibrate_retrieval_threshold(unanswerable_scores, percentile)` 由评估集校准，
纯分数逻辑，可离线验证。

### 目录与投影栅栏

- 文档状态机：`waiting → parsing → splitting → indexing → completed / error`，
  状态落库支持重启续跑；增量重索引按 chunk `text_hash` 差量对比，未变 chunk 跳过嵌入。
- 手动编辑切块必须先生成不可变修订（revision），再改 head。
- 投影写入必须带 revision fence，陈旧写入被拒绝而不是覆盖。

### 运行观测

`POST /api/query/stream` 产生的 canonical RunEvent 进入 RunRegistry（脱敏、尺寸有界），
异步落盘 `data/run-history.sqlite3`（WAL），支持重启恢复、心跳标记 stale、
签名 cursor 分页与 long-poll：

- `GET /api/runs/health`、`GET /api/runs`、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/events`
- loopback 免 token；远程访问未配置 operator token 一律 403，配置后缺失/错误 Bearer 401
- Registry 不保存原始 query、answer、prompt、ACL、chunk 正文或 embedding；
  query fingerprint 仅在配置专用 HMAC secret 时才生成

---

## 七、企业能力

| 域 | 能力 |
|---|---|
| 身份与访问 | 租户 / 工作区 / 组织单元 / 用户组多层授权，OIDC SSO、SCIM 2.0 用户与组同步、域名验证、邀请生命周期 |
| 审批与合规 | 审批策略与多级审批人、审批门控的 ACL 变更、审计留存策略、法律保留（legal hold）、审计导出 |
| 知识库治理 | 知识库注册表、发布通道与版本清单、回滚、依赖分析、质量基线 / 观察 / 告警 / 复认证 / 豁免 |
| 运营 | 通知中心（订阅 + 回执 + 物化）、任务中心、自动化工作流（规则 / 预览 / 修订 / 运行）、知识服务可靠性 |
| 数据生命周期 | 内容恢复、合规留存执行与预览、删除作为持久化操作（非同步多库事务） |

---

## 八、评测

```bash
python -m eval.run_eval --dry-run                     # 离线干跑（桩裁判 + 占位管线）
python -m eval.run_eval --pipeline rag:answer_query   # 真实评测（需模型与向量库）
python -m eval.run_eval --dry-run --gate              # 评测 + release gate（未达标 exit 1）
python -m eval.run_eval --dry-run --gate --gate-thresholds gate.json   # 自定义质量墙阈值
python -m eval.run_eval --dry-run --baseline eval/baseline.json        # 与历史 baseline 对比
```

两个独立裁判（Groundedness / Relevance）均 evidence-only、严格 JSON 输出；
解析失败显式返回 `score=None` 而不是静默记 0 分。指标：拒答率、过度拒答率、
幻觉率、平均有据性、平均相关性、引用失败率。

EvalReport v2 提供发布门禁与历史追踪能力：

```python
from eval.run_eval import (
    as_v2, save_report, load_report, compare_reports, release_gate,
    save_report_history, list_report_history,
)

v2 = as_v2(report, dataset_spec="eval/dataset_sample.py:SAMPLE_DATASET", pipeline_spec="rag:answer_query")
save_report(v2, "eval/baseline.json")
diff = compare_reports(v2, load_report("eval/baseline.json"))   # 逐指标 delta + 方向语义
gate = release_gate(v2)                                         # 固定阈值质量墙（可自定义阈值）
save_report_history(v2)                                         # 存入历史目录（时间戳命名）
history = list_report_history()                                 # 列出历史（按时间倒序）
```

---

## 九、测试与门禁

```bash
python scripts/run_tests.py          # 串行跑全部 smoke_*.py（--filter / --list 可用）
python -m pytest -q                  # 后端回归
cd frontend && npm run lint && npm test && npm run build
python scripts/export_openapi.py --check    # 契约漂移门禁
python scripts/bench/run_full_gate.py       # 完整链路性能门禁（起服务 + 三负载压测 + 阈值判定）
```

离线冒烟脚本不依赖网络、模型与向量库，覆盖基础、入库、检索、生成、验证、评测、
增强、图检索、韧性（图/顺序路径一致性差分）、目录、文档、解析器、TSR、
元数据双通道、切分路由、引擎注册表、Provider、并发与桥服务。

契约单一事实源：`scripts/export_openapi.py` 导出 OpenAPI，前端类型由
`openapi-typescript` 生成到 `frontend/src/types/generated/`；
`npm run types:gen` 重新生成、`npm run check:contract` 校验漂移。

完整链路性能门禁（`scripts/bench/run_full_gate.py`）：一键起 mock 上游 + 被测服务，
对 distinct / hotspot / mixed 三种负载压测（并发 32 / 总量 96），按阈值判定
（QPS / P95 / 成功率），任一未达标 exit 1。阈值校准历史与测量前提
（环境空闲时运行）见脚本注释与 `docs/性能实测与升级计划.md`。

---

## 十、已知限制

- 真实链路（`--pipeline rag:answer_query`）需要 Ollama 与 Milvus 实例，离线环境未执行，属上线前验收项。
- `entailment_mode="nli"` 仅预留（抛 `NliNotImplemented`），当前用 LLM 判定。
- Docling 的 api 模式为预留；local 模式依赖重（torch + 数百 MB 模型），默认关闭。
- 尚无：REST typed-event 与 LangGraph canonical 的完全对齐、多租户终端管理、
  运行历史永久保留、原始事件正文采集。

---

## 许可证

Apache-2.0。详见 [LICENSE](LICENSE)。
