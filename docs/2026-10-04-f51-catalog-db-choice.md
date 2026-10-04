# F5.1 目录库选型：决策材料

日期：2026-10-04
状态：**建议采纳 PostgreSQL**（依据充分；但生产切换是部署决策，本文只给依据）
背景：MySQL `192.168.100.128:3307` 未启动，F3.5 门禁被阻塞。切 PG 期间顺带取齐了选型需要的实测数据。

## 一句话

**PG 在本仓的方言层已是「一等公民」而非 MySQL-降级；实测连接性能优秀
（热连接 p50 0.24ms）；迁移链 0001→0039 全绿。选 PG。**

## 四个维度的实测

### 1. 方言层：PG 是声明式注册的，MySQL 才是手写分支多的那个

`core/dialects.py` 的 `BUILTIN_DIALECT_SPECS` 里三者平级：

| key | label | aliases | 并发锁 | UTC 时钟 | boolean 字面量 |
|---|---|---|---|---|---|
| `sqlite` | SQLite | — | ✗ | ✓ | — |
| `mysql` | MySQL / MariaDB | `mariadb` | ✓ | ✓ | — |
| `postgresql` | PostgreSQL | `postgres` | ✓ | ✓ | `("true","false")` |

PG 甚至比 MySQL **多一项** `boolean_literals`——因为 PG 的 `BOOLEAN` 真值
字面量与 MySQL 不同（`1/0` vs `true/false`），而这一项已被抽象进 `DialectSpec`。

`config/.env.postgres` 里写的那句「切换引擎不再依赖 58 处
`dialect in {"mysql", ...}` 那种手写法」是准确的：方言能力在 `DialectSpec`
里声明，PG 与 MySQL 都是注册进来的。

### 2. 迁移链：0001→0039 全绿，PG 特有对象齐全

`python scripts/migrate_catalog.py upgrade` 从空库跑到 head，
**39 个迁移全部成功**，包括那些 `create_all` 建不出来的东西：

- 企业级不可变触发器（`rag4c_automation_immutable`、
  `rag4c_retrieval_experiments_immutable` 等触发器函数）
- 租户隔离触发器（`trg_tenant_*_no_update` / `_no_delete`）
- 事件前置触发器（`trg_tenant_knowledge_serving_events_*`）
- 检查约束、外键、唯一约束（`ck_tenant_oidc_*`、`fk_tenant_sso_sessions_*`、
  `uq_tenant_oidc_subject_*` 等）

这一条比「方言支持」更有说服力：**PG 上这些对象能建出来，说明迁移脚本里的
PG 分支是活的**，不是写完就没跑过的死代码。

### 3. 连接性能（远程，192.168.100.128:5433）

| 指标 | 实测 |
|---|---|
| 冷连接（含 TCP + 认证） | 39.1ms |
| 热连接 p50（20 次） | **0.24ms** |
| 热连接 p95 | 0.55ms |
| 事务往返 | 0.8ms |

远程跨机却能到这个数，说明连接池工作正常（`pool_pre_ping` + 复用）。
`RAG4C_CATALOG_CONNECT_TIMEOUT_S=5`（`.env.postgres` 的值）足够——
不可达时 5 秒内快速失败，不会挂死。

### 4. 工具链齐备

`docs/postgres-deploy/` 里有 `docker-compose.yml`（postgres:17.8-alpine、
宿主 5433 → 容器 5432）、`conf/`（调优片段）、`init/`（只读账号与超时兜底）。
本机实测的 17.8 与该配置一致。

## 反对意见与反驳

| 可能的反对 | 实际情况 |
|---|---|
| 「MySQL 是生产在用的，换库风险大」 | 本机 MySQL **未启动**（3307 超时；3306 上是别的业务库 dkd/tlias/zjkl，没有 `rag4c`）。也就是说本地根本没有在用的 MySQL 可保住。 |
| 「PG 上 117 张表是无章的 head 形状」 | 是——但**空库重建 + 跑迁移**比逐个补 60+ 个触发器/约束可靠得多，且实测 20 秒内完成。 |
| 「SQLite 更简单」 | SQLite 不支持并发锁（`real_concurrent_locking=False`），企业级多租户场景不合适。 |
| 「迁移脚本里可能有 MySQL-only 分支没测到」 | 39 个迁移在 PG 上全跑通了，包括 0021~0039 的企业级部分。 |

## 建议的生产切换步骤（若采纳）

1. `.env` / `config/.env.bench` 的 `RAG4C_CATALOG_DB_URL` 改成
   `postgresql+psycopg2://<user>:<pw>@<host>:5433/rag4c`（本次已改，本机在用）；
2. 首次部署在**空库**上跑 `python scripts/migrate_catalog.py upgrade`，
   **不要**先 `create_all` 再 `stamp-existing`——那条路会遇到
   「表齐但 PG 特有对象全缺」的混合状态，`stamp-existing` 会报
   `cannot repair partial catalog`（它说的 partial 指触发器/函数/约束，不是表）；
3. 保留 `RAG4C_CATALOG_CONNECT_TIMEOUT_S=5`（测试/CI 可再压到 1~2 秒）；
4. 只读账号用 `docs/postgres-deploy/init/` 里那份，别用 `root`。

## 未验证的部分（诚实标注）

- **没有做 PG vs MySQL 的同条件 A/B**——MySQL 不可用，无法对照。所以
  「PG 性能更好/更差」这个命题**没有实测支撑**，只有「PG 性能足够好」这个实测。
- 62 条 `test_catalog_schema.py` 用了 `from sqlalchemy.dialects import mysql`，
  也就是说**这批测试测的是 MySQL 方言的建表路径**，在 PG 环境下跑不等于
  验证 PG。它们单条 6 秒（全套约 6 分钟），慢但通过。
  **PG 方言的建表路径目前没有专门的测试**——这是一个真实的测试缺口。
- 真实上游下的端到端延迟（P95）没测：`F3.5` 那批读数测的是弃权路径，
  详见 `docs/2026-10-04-f35-perf-gate.md`。

## 复现

```bash
# 方言注册
python -c "import core.dialects as d; print(d.BUILTIN_DIALECT_SPECS)"

# 迁移链（空库）
python scripts/migrate_catalog.py status
python scripts/migrate_catalog.py upgrade

# 连接性能
python -c "
import time; from sqlalchemy import create_engine, text
from config.settings import get_settings
e = create_engine(get_settings().catalog.db_url)
with e.connect() as c:
    ts = []
    for _ in range(20):
        t = time.perf_counter(); c.execute(text('SELECT 1')); ts.append((time.perf_counter()-t)*1000)
ts.sort(); print('p50', round(ts[10], 2), 'ms  p95', round(ts[18], 2), 'ms')
"
```

## 追记：本文的建议已在本地实施并验证（同日下午）

「建议采纳 PG」不是纸面建议——`.env` 与 `config/.env.bench` 都已经切到
PG 17.8（`postgresql+psycopg2://root:root1234@192.168.100.128:5433/rag4c`），
本地服务在 PG 下能正常启动（`/livez` 与 `/readyz` 都 200）。

### 实际执行与本文的差别

本文第五节写的切换步骤里有一条「首次部署在空库上跑 `upgrade`」——实测正是
这么做的，而且**这是唯一可行的路径**：

那个 PG 库原本是「混合状态」（117 张表在、PG 特有对象全缺）。两条路：
- `stamp-existing`：报 `cannot repair partial catalog`，列了 60+ 个缺失对象。
  它的 `_head_schema_issues` 只覆盖 baseline 时代的对象，补不了 0039 那一批；
- 逐个补 60+ 个触发器/约束/索引：可行但慢且易漏；
- **drop + create 空库 + `upgrade` 跑 0001→0039**：20 秒，全绿。

**动手前必须先确认库是空的**（`pg_stat_user_tables` 查 `n_live_tup`）——
「不误删有数据的库」是前提，不是事后补的检查。

### 一个此前没注意到的收益

切到 PG 之后，`_preflight_catalog`（今天上午加的启动探活）**立刻发挥了作用**：
跑 mock 门禁时它报

    RuntimeError: 目录库不可达：192.168.100.128:3307（RAG4C_CATALOG_DB_URL 里的
    地址/端口）。连接失败：TimeoutError: timed out。本机常见错因是端口写错

原因是 mock 模式读 `config/.env.bench`、真实上游模式读 `.env`——**两个文件
各有一份目录库配置**，我只改了一个。若没有探活，服务会在十几层 SQLAlchemy
之后炸掉、报错看不出是哪个配置项。

**教训**：同一个配置项存在于多个 env 文件时，改一处不够，而报错不会告诉你
漏了哪一处。这类问题应该由启动探活兜住，而不是靠人记得。

### 本文「未验证的部分」现在补上一条

原文说「没有做 PG vs MySQL 同条件 A/B」。这一条依然成立——MySQL 未启动，
无法对照。但补一个**间接证据**：PG 17.8 上 `migrate_catalog.py upgrade`
全量跑通，且建出了全部企业级触发器；MySQL 8.4 上这套迁移是否同样全绿，
本次无法验证（3307 不通）。**「PG 迁移链全绿」是事实，「PG 优于 MySQL」不是。**
