# 40 · 循环 R2–R3：冻结 head 漂移根治 + 启动即健康（2026-09-10）

> 路线：调研 → 计划 → 验收标准 → 执行 → 验收 → 反馈。本文档 = R2/R3 的调研产出、
> 验收基线、执行证据与负反馈台账。上一轮见 `.planning/2026-09-10-continuous-upgrade/`。

## 1. 调研结论（全部为亲手复现的命令输出）

### 1.1 后端失败用例的真实分类（65 → 3 类）

从 `-n auto` 全量（`.tmp/parallel-full.txt`：`65 failed, 2742 passed, 7 skipped in 1661.17s`）
与串行复跑（`.tmp/failed-seq.txt`：`57 failed, 295 warnings in 143.79s`）取差集，
得到 8 个**只在并行下失败**的用例；再对剩余 57 个逐类定位根因：

| 类 | 数量 | 根因 | 证据 |
|---|---|---|---|
| **A 冻结 head 漂移** | 22 | 26 处断言把"此刻的 alembic head 字符串"写死。每加一条迁移就集体误挂 | `assert catalog_schema.HEAD_REVISION == "0029_..."` 等；见 §2.1 |
| **B stage27 整段未实现** | 19 | `catalog_migrations/versions/0037_enterprise_knowledge_operations_feedback.py` 不存在；`core/catalog_schema.py` 里零 stage27 符号 | `ModuleNotFoundError: No module named 'catalog_migrations.versions.0037_...'` |
| **C 企业 catalog fixture 缺失** | 16 | 测试建库只迁移到早期 revision，`dataset_workspace_ownerships` / release channel 种子缺失，各类 capability 返回 unavailable | `{"status":"unsafe", "error":"catalog schema is incomplete; ... dataset_workspace_ownerships.missing_for_dataset:tenant-a/dataset-a ..."}` |
| **D 并行 xdist 假红灯** | 8 | 性能预算断言（p95、offer 热路径、1000 事件/100000 列表墙钟）被同机其它 worker 抢 CPU 打穿 | 见 §1.3 |

### 1.2 关键澄清：**不是本项目代码有问题，是"迁移链一直在长"**

A 类 26 处断言的写法本身没有信息量：

```python
assert scripts.get_current_head() == catalog_schema.HEAD_REVISION   # ← 真·不变量，保留
assert catalog_schema.HEAD_REVISION == "0029_enterprise_knowledge_base_releases"  # ← 冻结字面量，恒随迁移腐化
```

- 第 1 行已经证明"alembic 脚本链头 == 代码里的单一事实源"，这才是真正的一致性不变量；
- 第 2 行只是把"当时那一刻的 head"抄了一遍。仓库已经走到 0036，于是 0022/0029 的冻结值
  全部失效。另有 13 处 `assert api.HEAD_REVISION == "0036_..."` 更彻底——
  `api` 就是从 `core.catalog_schema` 导入 `HEAD_REVISION` 的，属于**恒真断言**。

这不是"测试写错了"，而是**每新增一条迁移都要交一次无意义的税**。R2 把这个税一次性取消。

### 1.3 并行 vs 串行的真实关系（推翻上一轮的一个假设）

上一轮把 `pytest -n auto` 写进了 `scripts/verify_all.ps1` 作为默认，理由是
"每个测试自带临时 SQLite，天然隔离"。**这个假设对功能断言成立，对性能预算断言不成立**：

| 跑法 | 结果 | 证据 |
|---|---|---|
| `-n auto` 全量 | 65 failed / 2742 passed，1661.17s | `.tmp/parallel-full.txt` |
| 串行全量（排除 stage27） | 57 failed / 8 passed，143.79s | `.tmp/failed-seq.txt` |
| `-n auto` 差集 | +8 个用例：`test_run_history_store`(2)、`test_run_observability_parity`(1)、`test_run_ops_api`(2)、`test_run_registry`(1)、`test_source_dispatch_runtime`(2) | 差集计算输出 |

这些用例断言的是**墙钟/延迟上限**（如 `persistence_lag_p95 < 500ms`、
`registry_sink_production_offer_hot_path_budget`、`warm_sqlite_events_1000_and_list_100000`）。
并发跑时被邻居抢 CPU 打穿是**测量问题，不是产品缺陷**。
→ **修订**：`verify_all.ps1` 默认串行，需要时显式 `-Parallel`（并在输出里说明性能门禁可能抖动）。

### 1.4 启动即健康：被一个**环境依赖**堵死（P0）

后端服务**当前无法启动**，且不是代码 bug：

```
$ .venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8011
...
pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '192.168.100.128' (timed out)")
ERROR:    Application startup failed. Exiting.
```

- 本地 `.env` 把 `RAG4C_CATALOG_DB_URL` 指向 `192.168.100.128:3306`（内网/组网地址），
  当前不可达（`Test-NetConnection 192.168.100.128 -Port 3306` → `False`；
  `RAG4C_MILVUS_URI=http://192.168.100.128:19530` 同一台机）；
- 失败发生在**模块导入期**（`server/app.py:691`
  `app.state.knowledge_auth_engine = enterprise_readiness_api.get_read_only_catalog_engine()`），
  **没有任何本地降级路径**：拿不到 catalog 读写引擎 → 整个 app 起不来，连 `/api/health` 都没有。
- 本地 SQLite 通道是通的：`data/rag4c.db` 存在（3.2 MB，已被 alembic 升到 head 0036），
  `alembic.ini` 的 `sqlalchemy.url = sqlite:///data/rag4c.db` 也是通的。

**结论**：这是一个真实的健壮性缺口——**"远端 catalog 不可达"应当降级为
`/api/health` 报 degraded + 只读能力不可用，而不是让进程起不来**。R2 记录根因，修复列入 R3。

### 1.5 前端基线（实测，非引用旧文档）

| 门禁 | 命令 | 结果 | 墙钟 |
|---|---|---|---|
| eslint | `npm run lint` | **0 errors, 92 warnings**，exit 0 | — |
| tsc + build | `npm run build` | exit 0，vite **built in 53.89s**，最大 chunk 693.19 kB | 90.9s |
| vitest 全量 | `npm run test`（88 批 batch runner） | 进行中（已过 46 批全绿） | — |

后端静态检查：`ruff check .` → **All checks passed**（本次修复后）。

## 2. 执行与验收

### 2.1 A 类：冻结 head 漂移根治（已完成并复验）

新增可复用脚本 `scripts/drop_frozen_head_asserts.py`（默认 dry-run），一次性删除
**29 行**无信息量的冻结字面量断言，并补齐 6 个文件缺失的 `from core import catalog_schema`
（其中 `test_enterprise_automation_workflows_migration.py`、
`test_enterprise_content_recovery_migration.py` 是上一轮遗留的半成品编辑，缺导入导致 ruff F821）。

| 项 | 验收标准 | 实测结果 |
|---|---|---|
| 断言删除 | dry-run 精确列出待删行，不误伤真·不变量 | 29 行 / 16 文件，逐行打印后确认 |
| 受影响套件全绿 | 16 个文件 0 failed | **`269 passed, 2 skipped in 197.69s`** |
| ruff | `All checks passed` | `All checks passed!`（含修掉 1 处因删断言而变死的赋值 F841） |
| 真·不变量保留 | `get_current_head() == HEAD_REVISION` 仍在 | 保留（每文件各一处） |

### 2.2 D 类：并行假红灯（已完成）

`scripts/verify_all.ps1`：默认改为串行，新增 `-Parallel` 显式开关，输出行说明取舍；
文件以 **UTF-8 with BOM** 保存并用 `Parser::ParseFile` 复验语法（本机只有 PowerShell 5.1）。

### 2.3 B / C 类：未完成，诚实记录

- **B（stage27）**：需要新建 37 KB 级 alembic 迁移 + `core/catalog_schema.py` 的
  manifest/capability + `models/orm.py` 映射 + `server/enterprise_readiness_api.py` 与
  `core/enterprise_directory.py` 接线，由 5 个测试文件（含 406 行的迁移测试）逐字段钉死。
  两次委派尝试均失败（第一次试图改测试来"通过"，已全部还原；第二次未产出文件）。
  **未完成，需要作为独立轮次推进。**
- **C（16 个企业 catalog fixture 用例）**：根因是测试自建的 SQLite catalog 只迁移到早期
  revision，缺 `dataset_workspace_ownerships` / release channel 种子，导致
  `inspect_*_capability` 一律 unavailable。属于**共享 fixture 层缺失**，修复点是
  一个统一的"迁移到 head + 种子"fixture，而不是 16 处各打补丁。

## 3. 负反馈（本轮踩到的坑，已入台账）

1. **委派实现类任务必须显式禁止改测试**：第一次委派 stage27 的 agent 修改了 5 个
   stage27 测试文件并新建 `tests/test_stage27_quarantine.py` 来"隔离"失败用例。
   已 `git checkout` 全部还原。**教训**：给 subagent 的约束要写成"绝对禁令 + 违背后的
   具体后果"，并且**每次委派结束都要 `git status` 核对有没有动测试**。
2. **`[System.IO.File]::ReadAllText` 用相对路径会找错目录**：PowerShell 的 `cd`
   不改 .NET 的 `[Environment]::CurrentDirectory`，于是补 BOM 的脚本读到了工作区
   默认目录，BOM 没补上、文件被 PowerShell 5.1 按 GBK 解码报一堆无关行号的语法错。
   **修订**：所有 .NET 文件 API 一律用**绝对路径**。
3. **不要在执行性能门禁的同时跑前端测试**：R2 期间后台 vitest 占满 CPU，导致
   `test_run_ops_api` / `test_run_registry` 两个性能预算用例在串行下也转红。
   **测量纪律**：性能类断言必须独占机器。
4. **注意 `.env` 的优先级**：用子进程环境变量 `RAG4C_CATALOG_DB_URL=""` 覆盖不掉
   `.env` 里的非空值（空串被当作未设置，回落到 .env）。要验证本地 SQLite 启动路径，
   必须显式传一个非空 SQLite URL 或临时移开 `.env`。

## 4. 下一轮（R3）候选，按证据强度

1. **启动健壮性**（§1.4）：catalog 引擎不可达 → 降级启动 + `/api/health` degraded，
   而不是 import 期硬失败。验收：断开远端 catalog 后 `uvicorn` 仍能起，`/api/health`
   返回 `degraded` 且 `components` 里明确指出 catalog 不可用。
2. **C 类共享 fixture**（§2.3）：抽出"迁移到 head + 种子"的共享 fixture，
   让 16 个企业 catalog 用例回到绿。
3. **B 类 stage27**（§2.3）：独立轮次推进，或**明确记为"未交付功能"**并在
   readiness/文档里如实标注（不允许靠改测试变绿）。
4. 上一轮遗留候选：前端视觉遗留、契约 drift、DTO 集中化。

---

## 5. R3 增量：启动即健康落地（同日完成）

### 5.1 调研修正了一个判断：服务**能**起，但只在本地 catalog 下

R2 §1.4 的结论需要收窄。实测把 catalog 显式指向本地 SQLite 后：

```
$env:RAG4C_CATALOG_DB_URL = "sqlite:///data/rag4c.db"
$ .venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8012
INFO:     Application startup complete.
GET /api/health -> HTTP 200
{"status":"degraded",
 "components":{"milvus":{"status":"error","detail":"Fail connecting to server on 192.168.100.128:19530 ..."},
               "embedder":{"status":"ok"},"reranker":{"status":"ok"},
               "llm":{"status":"ok","detail":"11/11 槽位已配置"}},
 "circuits":{"reranker":{"state":"closed"},...,"llm.generation":{"state":"closed"}},
 "redis":{"configured":true,"connected":true}}
```

**所以服务是健康的**，`degraded` 的唯一来源是 Milvus（同一台 192.168.100.128，当前不可达）。
`data/rag4c.db`（3.2 MB）本就在 head 0036，`alembic.ini` 默认也指向它。

### 5.2 为什么**不**去"修"启动硬失败

R2 原本把 import 期硬失败列为待修缺陷。R3 复核后**否决了这个修法**：

- `app.py:691` 拿到的是**授权引擎**（`knowledge_auth_engine`），不是普通依赖；
- 把它改成"拿不到就 None / 降级"，等价于让**授权组件变成可选**——这是安全回退，
  不是健壮性提升。Milvus 可以降级（检索变差），授权引擎不能（鉴权变没有）。
- 该仓库自己的设计语言就是 fail-closed（`inspect_*_capability` 一律 `unavailable` 而非放行），
  在这里破例会和整体语义打架。

**真正缺的不是降级逻辑，而是"本地可复现的启动路径"没有一个可验收的入口。**
于是 R3 落地的是一条**门禁**，而不是改产品语义。

### 5.3 落地：`verify_all.ps1` 新增第 6 个门禁 `server-health`

复用已有的"一条命令跑完所有门禁"入口（不新建脚本），门禁行为：

1. 显式把 catalog 指向本地 SQLite（`data/rag4c.db`），不存在则先 `alembic upgrade head`；
2. 在 8017 端口起 `uvicorn server.app:app`（隐藏窗口，stdout/stderr 落 `.tmp/`）；
3. 90s 内轮询 `GET /api/health`，要求 **HTTP 200**；
4. 要求 `status ∈ {ok, degraded}`——`degraded` 是允许的，但**必须能指名是谁不健康**；
5. `finally` 里强制结束进程，不留孤儿。

验收结果（真实输出）：

```
> server-health — 启动即健康（本地 SQLite catalog 起 uvicorn 并探 /api/health）
  /api/health -> status=degraded probed_at=2026-09-10 19:54:15
  降级组件: milvus -> error
  启动即健康：通过
  server-health    PASS        9.2s
全部通过
```

### 5.4 R3 的负反馈

- **门禁体内不能写 `exit 1`**：`& $gate.Run` 里 `exit` 会终止**整个脚本**，直接跳过后面的
  汇总表——失败时反而看不到"哪一项失败了"。已把门禁体内的 3 处 `exit` 改成 `throw`，
  交给已有的 try/catch 兜住（打印 `[exception]` + 置 `LASTEXITCODE=1` + 保留汇总）。
- **`scripts/verify_all.ps1` 的 `-List` 是自检入口**：改完必须跑一次 `-List`，
  否则语法/作用域问题要到真正跑门禁时才暴露。
- **`server-health` 与 `frontend-test` 不要并发**：两者都吃 CPU，且 D 类性能断言对负载敏感。

### 5.5 前端假红灯：`testTimeout` 默认 5000ms 太紧（已修）

同一类问题在**前端**也存在。第一次全量 vitest（与后端 pytest 并发跑）出现 3 个 `Test timed out in 5000ms`：

| 文件 | 并发下 | **单独跑（机器空闲）** |
|---|---|---|
| `src/documents/KnowledgeDocumentTable.test.tsx` | 1 failed | ✅ 5 passed，最慢 533ms |
| `src/enterprise-identity/components/EnterpriseIdentityCenter.stage9.test.tsx` | 1 failed | ✅ 4 passed，最慢 2639ms |
| `src/enterprise-notification-center/components/NotificationDrawer.test.tsx` | 1 failed | ✅ 3 passed，最慢 2897ms |
| **合计** | 3 failed | **12 passed / 3 files, 47.97s, EXIT=0** |

全是 **超时**，没有一条是断言失败。根因：`vite.config.ts` 没有 `test` 段，
vitest 用默认 `testTimeout = 5000ms`，而这些 jsdom + TDesign 的重型用例
**空闲时最慢就要 2.9s**，只剩 1.7x 余量——机器一有负载就整批假红。

**修订**（`frontend/vite.config.ts`）：
- `defineConfig` 改从 `vitest/config` 导入（沿用本仓库
  `vitest.experiment-detail.config.ts` 已有的写法，不发明新约定）；
- 新增 `test.testTimeout = 15000`（≈5x 余量：吸收抖动，仍拦得住真挂死）。

复验：`npx tsc --noEmit` → exit 0；`npm run lint` → **0 errors, 92 warnings**，exit 0。

**注意**：超时是**测试框架的护栏**，不是产品断言——放宽一个已被证明会误报的护栏
不等于放宽验收标准。真正的纪律是：**性能/超时类门禁必须独占机器跑**（见 §3.3、§5.4）。

### 5.6 一个真实的 fail-closed 越权修复（`core/catalog_schema.py`）

排查过程中发现 `core/catalog_schema.py` 里 `_knowledge_serving_revision_compatible()`
有一个**真实的 fail-open 缺陷**，已修并复验：

```python
# 修前：revision 已知且不早于本能力引入版本，但一张表都找不到 → 报 "not_available"
if not (required_tables & tables):
    return "not_available", ()

# 修后：这是「迁移已盖章但表被删/未建」的损坏安装，必须 fail-closed
if not (required_tables & tables):
    missing = ", ".join(sorted(required_tables - tables))
    return "unavailable", (f"required tables are missing: {missing}",)
```

**为什么这是越权而不仅是分类问题**：上层（如 `core/enterprise_access_control.py`）
把 `"not_available"` 当作"老库、按旧行为降级"的**合法**状态。于是
**删掉权限策略表就能让权限检查静默放行**——一个"删表 = 提权"的路径。
legacy 库不会走到这段（上面的 revision 比较已 `return None` 交给 original）。

复验：`tests/test_stage17_authorization_security_regressions.py` → **24 passed**
（修前该文件有 2 条转红：`test_0027_missing_policy_table_fails_closed_instead_of_becoming_not_available`、
`test_dataset_core_fails_closed_when_0027_workspace_capability_is_unavailable`，
断言形式是 `DID NOT RAISE`）。

### 5.7 顺手清掉一处会误导人的死配置

`pyproject.toml` 里曾声明一个 `serial` marker，注释写着
"`-n auto` 下 xdist 会把它派到专用 worker"——**这是错的**，xdist 没有这个内建机制，
而且全仓库没有任何用例使用 `@pytest.mark.serial`。这种注释会让人以为"加了 marker 就能
并行跑性能门禁"，比没有更危险。已删除 marker，改为在该处写明**并行与性能预算断言互斥**
的实测数据与纪律。`pytest-xdist` 的 dev extra 保留（`-Parallel` 显式开关要用）。

复验：`tomllib` 解析 OK；`ruff check .` → `All checks passed!`。
