# 目录库切 PostgreSQL + 修复"测试挂住"（2026-09-28）

## 1. 两个真问题的定位

### 问题 A：整条测试套件"挂住"（Round 1 就登记过）

根因不是测试写得慢，是**目录库不可达 + 连接池探活等满超时**：

- `core/catalog.py` 的远程库引擎带 `pool_pre_ping=True`，**每次取连接都会先探活**；
- 探活超时用的是驱动默认（pymysql 10 秒），此前没有任何配置项能调；
- 套件里几十次取连接 × 10 秒 = "跑了十几分钟才到 13%"。

改法：`catalog.connect_timeout_s`（默认 10 秒，生产行为不变）+ `tests/conftest.py`
在测试会话里压到 1 秒。**实测**：3 次探活 34s → 7s（约 5 倍）；语义完全不变
（连不上就是连不上，早知道晚知道是同一个结论）。

顺带修了一个 PG 专属坑：psycopg2 的 `connect_timeout` **只接受整数**，传 `5.0`
会直接报 `invalid integer value "5.0"`。`_connect_args_for()` 按方言分发
（PG 取整、MySQL 取浮点）。

### 问题 B：切到 PG 后建表失败

`CHECK (force_full IN (0, 1))` 在 PG 上直接报错：
`operator does not exist: boolean = integer`。全仓只有这一处布尔 CHECK，
但它在三个地方各写了一遍（ORM / Alembic 迁移 / schema 校验表）。

改法：方言差异收进 `core/dialects.py`（`DialectSpec.boolean_literals` +
`boolean_check_sql()`），三处统一从这里取：

- `models/orm.py`：按方言 `ddl_if` 下发（PG 用 `true/false`，MySQL/MariaDB/SQLite 用 `1/0`）；
- `catalog_migrations/versions/0014_source_schedules.py`：用 `op.get_bind().dialect.name` 取；
- `core/catalog_schema.py::_canonical_check_sql`：`true/false` 与 `1/0` 归一化后比对，
  否则同一个约束在 PG 上会被判成"缺失"。

修完 `scripts/check_services.py` 在 PG 上 **4/4 全绿**
（嵌入 1024 维 / LLM 正常 / 重排判别 0.98 vs 0.00 / 目录库连接与幂等写入正常）。

## 2. PG 接入方式（推荐后端）

- 仓库本来就把 PG 当一等公民：`core/dialects.py` 有 `postgresql` spec（别名 `postgres`）、
  只读方言、触发器/函数校验、UTC 时钟都有 PG 侧实现；部署脚本在 `docs/postgres-deploy/`
  （`postgres:17.8-alpine`，宿主 5433）。
- 新增：`pyproject.toml` 的 `postgres`（psycopg2-binary）与 `mysql`（pymysql）两个可选依赖；
  新增 `config/.env.postgres` 示例（`RAG4C_ENV_FILE=config/.env.postgres` 加载）。
- 本机实测：虚拟机 `192.168.100.128:5433` 是 **PostgreSQL 17.8**；已在其中创建 `rag4c` 库
  （`CREATE DATABASE rag4c ENCODING=UTF8 LC_COLLATE=C LC_CTYPE=C TEMPLATE=template0`）。

## 3. 顺带查出来的"白花钱"缺陷（重要）

开启 Contextual Retrieval 后实测：**2 个片段耗时 113 秒，返回空**。

- 根因：入库期三个槽位（contextual / triplet / classifier）**刻意**留在本机 Ollama
  （`.env` 注释写明了：按 chunk 逐个调用太贵）。本机没起 Ollama 时，调用会把重试
  耗满（113 秒）才失败，而失败被 `_call_batch` 静默降级成空列表。
- 表现：增强开了、文档慢了两分钟、上下文一条没生成——**不报错、不告警**，纯白花时间。
- 处理：**不动那个刻意的部署决策**（撤回了我一开始补 provider_ref 的改动），
  改为让降级**可见**：`indexing/contextual.py` 在整批降级时打 WARNING 并记指标
  `contextual.batches_degraded`。实测已生效：
  `WARNING Contextual Retrieval 未产出任何上下文：请求 1 个片段、1 批，全部降级。`

## 4. 入库台账（Round 4）在 PG 上跑通了

- `indexing/ingest.py::add_document` 用 `usage_scope()` 包住，收尾走
  `core.llm_usage.finish(scope="ingest")`（指标 `usage.tokens.total` /
  `usage.saved_tokens.total` / `usage.calls` / `usage.failures` + 一条日志）。
- 真实验证（PG + Milvus + 真实嵌入）：`INFO LLM 用量[ingest]: calls=0 total_tokens=0.0 …`
  （本次没开增强所以是 0，链路与字段都正常）；入库 1 个切片 14.2s。
- 配套测试：`finish()` 指标/容错、入库入口的源码守卫、contextual 降级可见性。

## 5. 变更清单与回滚

| 文件 | 改动 |
|---|---|
| `config/settings.py` | `catalog.connect_timeout_s`（默认 10） |
| `core/catalog.py` | `_connect_args_for()`（方言感知）+ `_remote_engine_kwargs(url)` |
| `tests/conftest.py` | 新增：测试会话把连接超时压到 1s |
| `core/dialects.py` | `boolean_literals` + `boolean_check_sql()` |
| `models/orm.py` / `catalog_migrations/versions/0014…` | 布尔 CHECK 按方言下发 |
| `core/catalog_schema.py` | `_canonical_check_sql` 归一化 true/false ↔ 1/0 |
| `indexing/ingest.py` | 入库台账（usage_scope + finish） |
| `indexing/contextual.py` | 整批降级不再静默（warning + 指标） |
| `pyproject.toml` / `config/.env.postgres` | PG 依赖与配置示例 |

回滚：以上均为可独立回退的小改动；`catalog.connect_timeout_s` 默认 10 秒
（与改动前一致），布尔 CHECK 在 MySQL/SQLite 上的产出文本与改动前逐字一致。

## 6. 下一步

1. 入库台账还差"按文档"粒度落到库里（现在只有指标 + 日志），需要时在
   `documents` 表加用量列；
2. contextual 慢且静默的问题，下一步可加"端点不可用即快速失败"（别耗满重试），
   并把入库期槽位改成可一键切换（Ollama ←→ 云端）的配置档；
3. 生产语料（Milvus `rag4c_chunks` 9723 行）还没有黄金标注，评测仍跑在文档代理语料上。
