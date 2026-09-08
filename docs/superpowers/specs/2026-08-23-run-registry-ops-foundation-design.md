# RAG4C Route B：RunRegistry 与 Ops Foundation 架构设计规格

> **状态：已批准，已获得实施授权（2026-08-23）。** 用户已明确选择方案 B，并允许使用多 Agent 协作开发。本规格冻结 Route B 的实现边界；实现必须以本文和已完成的运行观测事实层为共同依据。

## 1. 文档目的

Route B 把已经完成的请求级 canonical `RunEvent` 从“只在当前 SSE 连接里短暂存在”提升为可查询、可恢复、可诊断的运行历史，同时不改变 RAG4C 的问答业务语义。

本阶段交付一条完整纵切：

```text
canonical RunEvent
        │
        ├── 现有 SSE run_event（保持不变）
        └── failure-silent RunRegistry sink
                    │
                    ├── 有界进程内运行投影
                    ├── 异步 stdlib SQLite WAL 历史
                    ├── Runs/Ops 查询 API
                    └── 前端历史、流程、时间线、事件、知识诊断
```

核心原则仍然是从 Waku 学到的“一次记录，多种投影”，但事实模型和诊断维度保持 RAG4C 自己的特点：顺序流式执行、插拔式检索、二轮证据补救、弃权、L1/L2/L3 核验、缓存和降级。

## 2. 与第一批事实层的关系

Route B 只消费已存在的事实，不建立第二套生命周期协议。

权威上游保持不变：

- `server/app.py` 仍拥有 `POST /api/query/stream` 的 run 创建、拓扑冻结和唯一 terminal；
- `core/run_events.py` 的 `RunEvent`、`RunEventSequencer` 和 `RunObserver` 仍是顺序与生命周期权威；
- `rag_topology.py` 仍是拓扑快照权威；
- `rag_stream.py` 与 `retrieval/pipeline.py` 仍只报告原有业务步骤；
- legacy SSE `phase/token/done/error` 与 typed SSE `run_event`、`run_event_desync` 的行为保持不变；
- Registry 失败不得改变答案、引用、弃权、缓存、single-flight、排队、超时、取消或 SSE。

Route B 不把数据库状态反写为 canonical 事件，也不伪造 `run.completed`、`run.failed` 或 `run.cancelled`。

## 3. 范围

### 3.1 必须完成

后端：

1. 有界、线程安全、进程内 `RunRegistry`。
2. 在内存和持久化之前执行的防御性脱敏与尺寸限制。
3. 独立文件 `data/run-history.sqlite3` 的异步 stdlib `sqlite3` WAL 存储。
4. boot/worker 身份、心跳、干净停机标记和重启恢复。
5. active、recent、slow、errors、cancelled、stuck 查询投影。
6. 运行列表、详情、有序事件和健康 API。
7. 签名 keyset cursor、事件 long-poll 和断线重连协议。
8. 本机回环默认访问控制、远程可选 Bearer token、严格租户作用域。
9. Registry/SQLite/持久化队列失败静默，健康状态可见。

前端：

1. 用服务端历史替换“只靠 metrics 最近成功查询”的主历史来源。
2. 运行详情四个标签页：过程、时间线、事件、知识。
3. DOM 实现的 wave/timeline，不依赖 canvas 才能理解或操作。
4. 可键盘操作、可读屏的原始事件表；“原始”只指脱敏后的 canonical JSON。
5. 明确展示保留期、历史缺口、持久化降级、SSE desync 和重连状态。
6. Monitor 的 slow/error/stuck/cancelled 注意项可以钻取到具体 run。
7. 旧后端或 Runs API 不可用时保留现有 coarse-phase / trace-inferred 兼容模式。

### 3.2 明确排除

本阶段不实施：

- `POST /api/query` REST typed-event parity；
- `rag_graph.py` 或 LangGraph canonical adapter；
- 顺序流与 LangGraph parity 测试；
- EvalReport v2、baseline、compare、release gate；
- eval case 到 run ID 的正式关联；
- token、Prompt、问题、答案或文档正文采集；
- 分布式事件总线、Kafka、Redis Streams 运行历史或外部 APM 替代品；
- 多租户终端用户授权体系；Route B 提供的是受保护的 operator API；
- 流程编辑器或新的执行引擎。

这些排除项不能作为实现 Route B 时的“顺手重构”。

## 4. 不变量

实施和测试必须同时证明以下不变量：

1. Registry 是 canonical facts 的消费者，不是事实源。
2. 同一 `run_id` 的 canonical `seq` 含义和第一批完全一致。
3. Registry 不得改变 sink fan-out 中其他 sink 的投递结果。
4. 任何 Registry 代码抛出的异常都不得越过 failure-silent 边界。
5. 业务线程不进行 SQLite 文件 I/O，也不等待持久化队列腾位。
6. SQLite 不可用时，问答和现有 SSE 照常工作。
7. Registry 不采集或保存原始 query、answer、prompt、token、ACL、chunk、文档正文、embedding 或完整异常消息。
8. 每次缓存回放仍是独立 run；不得按答案缓存键合并运行历史。
9. 服务重启后未终结的旧 run 只能被 Registry 投影为 `interrupted`，不能补造 canonical terminal。
10. 一个 API 请求只能读取一个解析后的 tenant scope；Route B 不提供跨租户合并列表。

## 5. 总体组件和所有权

### 5.1 后端模块边界

新增模块：

| 模块 | 责任 | 不负责 |
|---|---|---|
| `core/run_registry.py` | 脱敏、内存投影、状态机、容量控制、同 worker 事件通知 | HTTP、SQLite schema、业务执行 |
| `core/run_history_store.py` | stdlib SQLite schema/migration、异步 writer、查询、保留清理、boot/worker 心跳与恢复 | canonical lifecycle、HTTP 鉴权 |
| `server/run_ops.py` | FastAPI router、operator access、tenant scope、cursor、响应模型、long-poll | 问答执行、写入事实 |

修改模块：

| 模块 | 修改范围 |
|---|---|
| `config/settings.py` | 增加 `RunHistorySettings` 和根级 `run_history` 配置；秘密字段不暴露到配置 API |
| `server/app.py` | lifespan 启停、向现有 sink fan-out 注册 Registry、挂载 Runs router；不重写 query/stream 逻辑 |
| `core/metrics.py` 或现有 metrics 调用点 | 增加低基数 Registry 健康计数；不删除既有指标 |

不得把 RunRegistry 的全部实现继续塞进已经很大的 `server/app.py`。

### 5.2 前端模块边界

新增或抽取：

| 模块 | 责任 |
|---|---|
| `frontend/src/types/runs.ts` | Runs API DTO、状态、完整性和持久化类型 |
| `frontend/src/api/runs.ts` | list/detail/events/health 请求、cursor 与 long-poll |
| `frontend/src/run/serverRunProjection.ts` | 从服务端 topology + events 重建 `FlowRunProjection`，复用现有 typed reducer |
| `frontend/src/run/useRunHistory.ts` | 分页、筛选、缓存、选择、重连、过期与竞态控制 |
| `frontend/src/components/RunHistorySidebar.tsx` | 服务端运行列表与注意状态 |
| `frontend/src/components/RunTimeline.tsx` | DOM wave/timeline 与表格替代视图 |
| `frontend/src/components/RunEventTable.tsx` | 可访问的脱敏事件表、详情和复制 |
| `frontend/src/components/RunKnowledgePanel.tsx` | 检索/核验/降级聚合，不显示内容文本 |

修改：

- `VisualizePage.tsx` 负责布局和四标签页编排，不自己实现协议解析；
- `MonitorPage.tsx` 增加注意运行列表和 drill-through；
- `RunMonitorContext.tsx` 保留当前 live run，不承担持久历史数据库职责；
- 现有 `RunFlowGraph.tsx` 和 `runProjection.ts` 继续复用；
- 新页面代码继续保持 route-level lazy chunk，不进入 Query 首屏主包。

### 5.3 请求上下文绑定

`RunEvent` 公共信封不增加 tenant 或 query 字段。`server/app.py` 在创建每个 HTTP stream run 时：

1. 用现有 `resolve_tenant` 得到业务 tenant；
2. 立即用 Registry scope key 计算 opaque `tenant_scope`，不保留原 tenant；
3. 仅在配置 fingerprint secret 时于服务边界计算 query HMAC；
4. 调用 `registry.bound_sink(tenant_scope, query_fingerprint)` 得到请求级 failure-silent sink；
5. 将该 sink 与现有请求级 SSE sink 一起传给同一个 `RunEventSequencer`。

bound sink 闭包只持有 opaque scope、可选 fingerprint 和 Registry 引用。第一个 `run.started` 建立内存 projection；其余事件按 `run_id`/`seq` 更新。这样不修改 canonical schema，也不需要保存等待匹配的原始请求上下文。

## 6. 配置模型与固定默认值

在 `config/settings.py` 增加根级 `RunHistorySettings`：

```python
class RunHistorySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    persistence_enabled: bool = True
    sqlite_path: str = "data/run-history.sqlite3"

    memory_max_active_runs: int = 128
    memory_max_recent_runs: int = 512
    memory_max_events_per_run: int = 1024
    memory_max_events_total: int = 32768
    memory_terminal_ttl_s: int = 21600

    max_event_json_bytes: int = 16384
    max_topology_json_bytes: int = 131072

    writer_queue_capacity: int = 8192
    writer_batch_size: int = 64
    writer_flush_ms: int = 100
    writer_shutdown_grace_ms: int = 2000

    retention_days: int = 30
    max_persisted_runs: int = 100000
    cleanup_interval_s: int = 600
    cleanup_batch_size: int = 1000

    heartbeat_interval_s: int = 5
    worker_stale_after_s: int = 30
    stuck_after_s: int = 300
    slow_threshold_ms: int = 30000

    api_default_page_size: int = 50
    api_max_page_size: int = 100
    events_max_page_size: int = 500
    long_poll_max_ms: int = 25000
    long_poll_max_clients: int = 64
    cursor_ttl_s: int = 3600

    ops_bearer_token: SecretStr | None = None
    fingerprint_secret: SecretStr | None = None
```

约束：

- 所有容量和时间配置在 Pydantic 层有上下界；非法值使应用配置加载失败，而不是运行中静默修正。
- `sqlite_path` 解析为项目根目录下的路径；默认且推荐路径固定为 `data/run-history.sqlite3`。
- 配置中心只显示非秘密字段。
- `ops_bearer_token` 和 `fingerprint_secret` 只能来自环境变量或 file secret，不得由 `/api/config` 读取、回显或写入 `.env`。
- 对应环境变量使用现有命名规则，例如 `RAG4C_RUN_HISTORY_ENABLED`、`RAG4C_RUN_HISTORY_OPS_BEARER_TOKEN` 和 `RAG4C_RUN_HISTORY_FINGERPRINT_SECRET`。

## 7. 身份、租户和可选指纹

### 7.1 Boot 与 worker

每个 Python worker 启动时创建：

- `boot_id`：UUID4，表示该进程本次启动；
- `worker_id`：UUID4，表示本次进程实例；
- `started_at`：UTC；
- `last_heartbeat_at`：每 5 秒更新；
- `state`：`starting | alive | stopped | stale`。

当前部署中一个 worker 对应一个 boot；两个字段仍分开保存，以便未来一个 boot 下管理多个执行 worker，而不迁移 run schema。

`run_id` 继续使用事实层已生成的值，不由 Registry 重写。

### 7.2 Tenant scope

Registry 不保存或返回原始 `tenant_id`。

应用第一次创建数据库时，在 `registry_meta` 生成 32 字节随机 `scope_key`。进程通过下式把 `resolve_tenant()` 的结果转为内部作用域：

```text
tenant_scope = base64url(
  HMAC-SHA256(scope_key, "tenant\0" + normalized_tenant_id)[0:18]
)
```

- `normalized_tenant_id` 使用现有 `resolve_tenant` 后的 UTF-8 值，不自行改变大小写或语义。
- persistence 禁用，或启动 identity bootstrap 无法打开数据库时，进程使用本次 boot 的随机 scope key；内部 `RegistryIdentity.tenant_scope_stability="boot"`，公开健康接口标记 `scope_stability="boot"`。
- 数据库可用时，所有 worker 共享持久化 key；内部 `RegistryIdentity.tenant_scope_stability="installation"`，公开健康接口标记 `scope_stability="installation"`。
- 一个 boot 内绝不从 boot key 热切换到 installation key。启动时若降级为 boot key，该 boot 的 Registry 保持 memory-only；恢复持久化需要重启 worker。这样不会出现同一 tenant 在一个 boot 内被映射成两个 scope。
- Runs API 必须先解析调用方 tenant，再计算 scope，然后在内存和 SQL 查询的第一层加入 scope 条件。
- 详情和事件 API 对 run 不存在及 scope 不匹配都返回相同的 404，避免 run ID 枚举泄露。
- Route B 不提供 `all tenants` 开关。

### 7.3 Query fingerprint

默认完全不保留问题等价标识。

只有 `fingerprint_secret` 明确配置时，`server/app.py` 在创建 Registry run 时计算：

```text
normalized_query = NFKC(query).strip()，并把连续 Unicode 空白折叠为一个空格
query_fingerprint = base64url(
  HMAC-SHA256(fingerprint_secret, "query\0" + normalized_query)[0:16]
)
```

- 只把 fingerprint 传给 Registry，原 query 不进入 Registry API。
- fingerprint 只用于同问题运行分组，不可作为缓存键或授权条件。
- 未配置 secret 时 JSON 字段省略，不使用可离线字典攻击的裸 SHA-256。
- 更换 secret 后历史 fingerprint 不可比较，这是预期的隐私边界。

## 8. 脱敏与尺寸边界

### 8.1 执行顺序

每个事件进入 Registry 时必须按以下顺序处理：

```text
RunEvent
  → 复制为普通 JSON
  → envelope allowlist
  → attributes/error/topology allowlist
  → 长度与集合上限
  → JSON bytes 上限
  → 内存投影
  → 非阻塞提交持久化队列
```

脱敏必须发生在内存写入和队列提交之前。任何检查失败时丢弃该 Registry 副本、增加安全计数并把 run 的完整性标记为 partial；不得影响上游 RunEvent 或其他 sinks。

### 8.2 禁止字段

无论事件来源为何，Registry 和 SQLite 都禁止保存：

- query、question、answer、response text；
- system/user prompt、token 文本和模型原始输出；
- ACL 内容；
- 原始 tenant/dataset 标识；
- document/chunk 正文、chunk ID 列表、document path；
- embedding、向量、完整 score 数组；
- API key、Bearer token、cookie、Authorization header；
- 完整异常消息、stack trace 或任意对象 `repr`。

对未知 key 使用拒绝策略，不使用“删掉几个危险 key 后其余全存”的策略。

### 8.3 允许字段

信封允许：

```text
schema_version, run_id, seq, occurred_at, elapsed_ms,
topology_id, topology_revision, type, node_id, attempt,
duration_ms, attributes, error
```

普通 attributes 允许的键：

```text
outcome, reason, request_path, cache_level, mode, executor,
executor_requested, executor_used, retry_enabled, delivery,
singleflight, queue_admitted, flight_released, cache_write,
route, effective, effective_route, fallback_route, configured_route,
component, source, enabled, available, skipped, abstained,
recoverable, degraded, changed, rewrite_required, output_present,
scoped, supported, missing_evidence, entailment_evaluated,
target_attempt, last_attempt, attempt_count, top_k,
chunk_count, candidate_count, requested_count, input_count,
output_count, source_count, citation_count, valid_count,
invalid_count, subquery_count, successful_count, failure_count,
added_count, added_chunk_count, removed_count, output_chars,
token_count, queue_wait_ms, slot_wait_ms, confidence, score_bucket,
topology
```

规则：

- 枚举/string 最长 96 个 Unicode code points；超长值替换为 `"[redacted:length]"`，不截取原文前缀。
- 数值必须有限；计数非负；布尔保持布尔。
- 数组最多 32 项，对象最多 64 个键，最大嵌套深度 4。
- `error` 只保留当前 `EventError.type`、`code`、`recoverable`；不增加 message。
- `topology` 只允许 `id/revision/executor/nodes/edges` 以及现有 topology schema 的结构字段。
- topology 的 node/edge label、group、kind 和枚举属性最长 128 code points；不接受运行时内容字段。
- topology 必须作为整体通过结构、allowlist、尺寸和 revision 一致性校验，不对其中字段做替换或截断。失败时拒绝该 `run.started` 的 Registry 事件副本，只用安全信封建立 `topology unavailable` 的 partial summary；不得返回与 `topology_revision` 不匹配的伪快照。
- 单事件序列化后最多 16 KiB；拓扑最多 128 KiB。超过上限的普通事件不持久化且标记 partial。

### 8.4 日志

Registry 自身日志只包含：`run_id` 的后 8 位、boot/worker 短 ID、状态码、计数和异常类型。不得记录事件 JSON、cursor、tenant 参数、token 或 fingerprint。

## 9. 内存 RunRegistry

### 9.1 数据结构

进程内维护：

- `active_by_id`：最多 128 个 active run；
- `recent_by_id` + LRU 顺序：最多 512 个 terminal/interrupted summary；
- `events_by_run`：每 run 最多 1024 个事件，全局最多 32768 个事件；
- `Condition`/版本号：供同 worker long-poll 唤醒；
- topology 快照与 node rollup；
- Registry 健康和 drop 计数。

所有 mutation 在一把短持有时间的 `threading.RLock` 下完成；JSON 序列化、SQLite 和 HTTP 响应构建不得在锁内执行。

### 9.2 容量策略

1. active run 不因 recent LRU 或 terminal TTL 被淘汰。
2. active 达到 128 时，新 run 不进入 Registry，增加 `run_registry.runs_dropped_capacity`；业务继续。
3. terminal run 从 active 移入 recent，超过 512 时淘汰最旧 terminal 的内存投影；SQLite 历史不受影响。
4. terminal 内存投影保留 6 小时，之后可在周期清理中删除。
5. 每 run 超过 1024 事件时保留 `run.started` 和最新 1023 条，记录 `earliest_available_seq` 并设 `memory_history_complete=false`。
6. 全局超过 32768 事件时，优先释放最旧 terminal run 的事件正文但保留 summary；仍不足时对最旧 active run 使用同样的首条加尾部 ring 策略并标记 partial。
7. 如果 SQLite 保存了缺失区间，API 从 SQLite 补齐；只有内存和 SQLite 都没有时才向客户端报告 history gap。

### 9.3 投影状态机

Canonical 生命周期：

```text
unseen → running → completed
                 → failed
                 → cancelled
```

Registry 扩展投影：

```text
running ── worker lost / restart recovery ──→ interrupted

terminal/interrupted ── TTL or LRU ──→ memory-evicted
terminal/interrupted ── retention ──→ persistence-expired
```

约束：

- `interrupted` 是 Registry summary 状态，不是新的 `RunEvent.type`。
- 收到 canonical terminal 后，后续事件不修改 summary，复用第一批 terminal 不变量。
- seq gap、重复 seq、run/topology mismatch 会使 `event_integrity=partial`，但原始合法事件仍按 seq 保存。
- `stuck` 是查询时派生 attention，不是持久状态：`status=running`、worker 心跳仍 alive 且 `now-updated_at >= 300s`。
- `slow` 是派生 attention：terminal 的 `elapsed_ms >= 30000`，或 active 当前运行时间达到该阈值。

### 9.4 Run summary schema

所有时间均为 UTC RFC 3339；SQLite 内部用 Unix microseconds。

```json
{
  "schema_version": 1,
  "run_id": "019...",
  "status": "running|completed|failed|cancelled|interrupted",
  "outcome": "answered|abstained|unknown",
  "started_at": "2026-08-23T08:00:00.000Z",
  "updated_at": "2026-08-23T08:00:03.100Z",
  "finished_at": null,
  "elapsed_ms": 3100.0,
  "boot_id": "uuid",
  "worker_id": "uuid",
  "topology_id": "rag.query",
  "topology_revision": "sha256:...",
  "executor": "sequential_stream",
  "last_seq": 12,
  "event_count": 12,
  "earliest_available_seq": 1,
  "current_node_ids": ["generation"],
  "failed_node_ids": [],
  "route": "hybrid",
  "degraded_count": 0,
  "retry_count": 0,
  "attention": ["slow"],
  "event_integrity": "complete|partial|unknown",
  "persistence_status": "pending|durable|partial|memory_only|unavailable",
  "interruption_reason": null,
  "query_fingerprint": "optional-HMAC-value"
}
```

`current_node_ids`、`failed_node_ids` 最多各 32 项；排序稳定。未配置 fingerprint 时字段不存在。响应不包含 `tenant_scope`。

## 10. SQLite WAL 持久化

### 10.1 文件和连接

- 使用 Python stdlib `sqlite3`，不引入新的数据库依赖。
- 数据库文件固定默认 `data/run-history.sqlite3`，不得复用 catalog 数据库。
- 每个 worker 的 writer 线程拥有一个写连接；API 读使用独立短生命周期读连接或线程局部只读连接。
- 初始化 PRAGMA：

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=1000;
PRAGMA temp_store=MEMORY;
```

### 10.2 Schema v1

```sql
CREATE TABLE registry_meta (
    key TEXT PRIMARY KEY,
    value BLOB NOT NULL
);

CREATE TABLE boots (
    boot_id TEXT PRIMARY KEY,
    started_at_us INTEGER NOT NULL,
    last_heartbeat_at_us INTEGER NOT NULL,
    stopped_at_us INTEGER,
    state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale'))
);

CREATE TABLE workers (
    worker_id TEXT PRIMARY KEY,
    boot_id TEXT NOT NULL REFERENCES boots(boot_id),
    started_at_us INTEGER NOT NULL,
    last_heartbeat_at_us INTEGER NOT NULL,
    stopped_at_us INTEGER,
    state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale'))
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    tenant_scope TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running','completed','failed','cancelled','interrupted')
    ),
    outcome TEXT NOT NULL CHECK (outcome IN ('answered','abstained','unknown')),
    started_at_us INTEGER NOT NULL,
    updated_at_us INTEGER NOT NULL,
    finished_at_us INTEGER,
    elapsed_ms REAL NOT NULL,
    topology_id TEXT NOT NULL,
    topology_revision TEXT NOT NULL,
    executor TEXT NOT NULL,
    topology_json TEXT NOT NULL,
    last_seq INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    earliest_available_seq INTEGER NOT NULL,
    current_node_ids_json TEXT NOT NULL,
    failed_node_ids_json TEXT NOT NULL,
    route TEXT,
    degraded_count INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    event_integrity TEXT NOT NULL CHECK (
        event_integrity IN ('complete','partial','unknown')
    ),
    interruption_reason TEXT,
    query_fingerprint TEXT
);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT,
    attempt INTEGER,
    occurred_at_us INTEGER NOT NULL,
    elapsed_ms REAL NOT NULL,
    duration_ms REAL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE INDEX idx_runs_scope_started
    ON runs(tenant_scope, started_at_us DESC, run_id DESC);
CREATE INDEX idx_runs_scope_status_started
    ON runs(tenant_scope, status, started_at_us DESC, run_id DESC);
CREATE INDEX idx_runs_worker_status
    ON runs(worker_id, status, updated_at_us);
CREATE INDEX idx_run_events_run_seq
    ON run_events(run_id, seq);
CREATE INDEX idx_workers_heartbeat
    ON workers(state, last_heartbeat_at_us);
```

`PRAGMA user_version=1` 在 schema transaction 成功后设置。

### 10.3 异步 writer

业务 sink 只执行 `put_nowait`，writer 线程：

- 最多收集 64 个 mutation 或等待 100ms 后提交一次 transaction；
- run summary 使用幂等 upsert；event 使用 `(run_id, seq)` 主键和内容一致性检查；
- 重复同内容视为幂等，重复主键但内容不同标记 integrity partial 并拒绝覆盖；
- 每次成功提交更新内存中的 durable watermark；
- SQLite locked/busy 使用 25ms、50ms、100ms、200ms、400ms 退避，单批最多 5 次；
- 持续失败时保留尚未提交的批次，只要总队列未超过 8192；
- 队列满时新 persistence mutation 被丢弃，相关 run 标记 `persistence_status=partial`，增加 drop counter；不阻塞业务；
- 关闭时最多等待 2000ms 排空，超时后把未落盘 run 标为 partial 并结束，不延迟应用无限退出。

### 10.4 保留和容量

每 600 秒执行一次小批清理：

1. 删除 `finished_at_us` 早于 30 天的 terminal/interrupted run；
2. 若总 run 数超过 100000，再按 `(finished_at_us, run_id)` 删除最旧 terminal/interrupted run；
3. 每 transaction 最多删除 1000 个 run；事件通过 cascade 删除；
4. `running` run 永不由 retention job 删除；先由 worker recovery 转为 `interrupted`；
5. 每日一次或删除超过 10000 条后执行被动 checkpoint；不在请求路径执行 `VACUUM`；
6. API 在响应中返回固定保留策略，前端不得暗示永久保存。

## 11. 启动、心跳与重启恢复

### 11.1 启动顺序

1. FastAPI lifespan 在应用进入 ready 之前执行 identity bootstrap：打开 SQLite、校验 schema/`PRAGMA quick_check`，在一个 transaction 中创建或读取 `scope_key`、`cursor_key`。
2. bootstrap 成功时创建 installation-scoped Registry 和 writer；bootstrap 失败时创建 boot-scoped memory-only Registry。启动失败不阻止 query 服务 ready，但 health 为 degraded。
3. 只有 Registry 的 scope identity 冻结后才把 sink 挂到 canonical fan-out，因此不会接受使用未决 scope 的事件。
4. bootstrap 成功时注册 boot 和 worker，并将 30 秒前仍为 `alive` 的 worker 标为 `stale`。
5. 把这些 stale worker 的 `running` run 投影更新为：

```text
status = interrupted
finished_at = recovery time
interruption_reason = worker_lost
event_integrity = partial
```

6. 启动 writer 和每 5 秒 heartbeat。
7. health 从 `initializing` 转为 `ok` 或 `degraded`；boot-scoped memory-only 模式本次启动不热切换到持久化。

恢复不能向 `run_events` 插入伪 terminal；详情响应通过 `interruption_reason` 解释中断。

### 11.2 干净停机

- 先停止接收新 Registry mutation；
- 等待现有 query 生命周期按应用原逻辑收敛；
- writer 最多 drain 2000ms；
- 标记当前 worker/boot `stopped`；
- 未收到 canonical terminal 的 run 标为 `interrupted`、reason=`process_shutdown`；
- 即使上述任一步失败，应用停机继续。

### 11.3 数据库损坏

启动 `quick_check` 失败时：

1. 关闭连接；
2. 在同目录把数据库及存在的 `-wal`、`-shm` 文件原子重命名为带 UTC 时间的 `.corrupt-*` 备份；
3. 创建新的 schema v1；
4. health 保持 `degraded` 并公开 `recovery_action="corrupt_db_rotated"`；
5. 不自动删除损坏副本，不把异常内容返回 API。

如果重命名或新建失败，持久化变为 unavailable，内存 Registry 继续。

### 11.4 Schema migration

- 所有 migration 按 `PRAGMA user_version` 顺序执行，并在单 transaction 内完成。
- Route B 首次只包含 v0→v1。
- 数据库版本高于代码支持版本时禁止写入，health=`degraded`、reason=`schema_too_new`，内存继续。
- migration 失败不部分提交；写入禁用，查询只在能安全读取当前 schema 时继续。
- 不导入旧 `/api/metrics.recent_queries`，因为它不含 canonical topology/events 且只覆盖成功结果。

## 12. Operator 访问与安全

### 12.1 网络访问规则

所有 `/api/runs*` 接口共享依赖：

1. 请求源为 `127.0.0.0/8` 或 `::1` 时默认允许；
2. 非回环来源只有配置了 `ops_bearer_token` 且 `Authorization: Bearer <token>` 通过 `hmac.compare_digest` 时允许；
3. 未配置 token 的远程请求返回 403；配置 token 后无效或缺失 token 返回 401，并带标准 `WWW-Authenticate: Bearer`；
4. 不信任 `X-Forwarded-For`，除非未来另行实现明确的 trusted-proxy 配置；Route B 不实现该配置；
5. 远程使用必须由部署层提供 HTTPS；应用本身不记录 token；
6. 现有 CORS allowlist 不为任意远程 origin 放宽。

`/api/runs/health` 也需要同一 operator access，但不需要 tenant 参数。

### 12.2 Tenant 请求规则

- `/api/runs` 接受 `tenant_id` query 参数；本地缺省时走现有 `resolve_tenant(None)`。
- 远程请求必须显式提供 `X-RAG4C-Tenant`；同时提供 header 和 query 且值不一致返回 400。
- `/api/runs/{run_id}` 和 `/events` 使用同样的 tenant 解析规则。
- API 响应不返回原始 tenant、tenant_scope、ACL 或 dataset ID。
- SQL 始终参数化，并包含 `tenant_scope = ?`。
- scope mismatch 与 unknown run 都返回：

```json
{"detail":{"code":"run_not_found","message":"运行不存在或不属于当前作用域"}}
```

## 13. Runs API

所有响应使用 `schema_version=1`，时间为 UTC RFC 3339，JSON `allow_nan=false`。

### 13.1 `GET /api/runs/health`

响应示例：

```json
{
  "schema_version": 1,
  "status": "ok",
  "enabled": true,
  "boot_id": "...",
  "worker_id": "...",
  "memory": {
    "active_runs": 2,
    "recent_runs": 138,
    "event_count": 6240,
    "dropped_runs": 0,
    "dropped_events": 0
  },
  "persistence": {
    "enabled": true,
    "state": "ready",
    "database": "run-history.sqlite3",
    "wal": true,
    "writer_queue_depth": 0,
    "last_commit_at": "2026-08-23T08:00:03.100Z",
    "commit_lag_ms": 12,
    "dropped_mutations": 0,
    "quick_check": "ok"
  },
  "heartbeat": {
    "interval_s": 5,
    "stale_after_s": 30,
    "last_heartbeat_at": "2026-08-23T08:00:05.000Z"
  },
  "retention": {
    "days": 30,
    "max_runs": 100000,
    "memory_terminal_ttl_s": 21600
  },
  "scope_stability": "installation"
}
```

HTTP 200 可返回 `status=degraded|disabled`，便于前端展示具体能力；只有 operator access 失败使用 401/403。

### 13.2 `GET /api/runs`

Query 参数：

| 参数 | 规则 |
|---|---|
| `tenant_id` | 本地可省略；远程使用 header |
| `view` | `active | recent | slow | errors | stuck`，默认 `recent` |
| `status` | 可重复：`running/completed/failed/cancelled/interrupted` |
| `slow_ms` | 仅 slow view；默认 30000，范围 1000–3600000 |
| `started_after` | 可选 UTC RFC 3339，含边界 |
| `started_before` | 可选 UTC RFC 3339，不含边界 |
| `fingerprint` | 仅 fingerprint 功能启用时可用 |
| `limit` | 默认 50，范围 1–100 |
| `cursor` | 签名 keyset cursor |

视图语义：

- `active`：`status=running`；
- `recent`：所有可见运行，按开始时间倒序；
- `slow`：terminal elapsed 或 active 当前 elapsed 达到 `slow_ms`；
- `errors`：`failed` 和 `interrupted`，保留两者区别；
- `stuck`：worker alive 且 running run 300 秒没有新事实；
- cancelled 通过 `status=cancelled` 明确筛选，不混入 errors。

稳定排序固定为 `(started_at_us DESC, run_id DESC)`。内存与 SQLite 结果按 `run_id` 去重，内存较新的 projection 覆盖 SQLite summary。

响应：

```json
{
  "schema_version": 1,
  "items": [],
  "next_cursor": null,
  "as_of": "2026-08-23T08:01:00.000Z",
  "source": "memory+sqlite",
  "history_state": "complete",
  "retention": {"days": 30, "max_runs": 100000}
}
```

### 13.3 签名 keyset cursor

Cursor 格式：

```text
base64url(canonical-json-payload) + "." + base64url(HMAC-SHA256(cursor_key, payload))
```

Payload：

```json
{
  "v": 1,
  "tenant_scope": "opaque",
  "filter_hash": "sha256-of-normalized-filter",
  "as_of_us": 1787472060000000,
  "last_started_at_us": 1787472000000000,
  "last_run_id": "...",
  "exp": 1787475600
}
```

- `cursor_key` 在 `registry_meta` 首次生成；无持久化时为 boot 随机 key。
- canonical JSON 使用排序 key、无空格 UTF-8。
- 签名用 `compare_digest` 校验。
- cursor 有效期 3600 秒。
- 首页确定 `as_of_us`，后续页沿用它；只返回 `started_at_us <= as_of_us` 的运行，slow/stuck 的时间判断也使用该时点。状态在分页过程中真实终结时允许更新，但并发插入的新 run 不进入旧分页窗口。
- tampered、过期、scope 不匹配或 filters 改变均返回 400，并分别使用 `cursor_invalid`、`cursor_expired`、`cursor_scope_mismatch`、`cursor_filter_mismatch` code。
- keyset 条件是 `(started_at_us, run_id) < (?, ?)`；并发插入更晚 run 不造成已翻页项目重复或跳过。

### 13.4 `GET /api/runs/{run_id}`

响应：

```json
{
  "schema_version": 1,
  "summary": {},
  "topology": {
    "id": "rag.query",
    "revision": "sha256:...",
    "executor": "sequential_stream",
    "nodes": [],
    "edges": []
  },
  "node_rollup": [],
  "history": {
    "event_integrity": "complete",
    "earliest_available_seq": 1,
    "last_seq": 24,
    "persistence_status": "durable"
  }
}
```

`node_rollup` 每项包含 `node_id`、attempt、status、started/finished elapsed、duration、degraded/retry reason code；不包含输入输出文本。

### 13.5 `GET /api/runs/{run_id}/events`

Query 参数：

| 参数 | 规则 |
|---|---|
| `tenant_id` | 同上 |
| `after_seq` | 默认 0，返回 `seq > after_seq` |
| `limit` | 默认 200，范围 1–500 |
| `wait_ms` | 默认 0，范围 0–25000 |

响应：

```json
{
  "schema_version": 1,
  "run_id": "...",
  "events": [],
  "after_seq": 24,
  "latest_seq": 24,
  "terminal": false,
  "timed_out": true,
  "history_state": "complete|partial|expired",
  "earliest_available_seq": 1,
  "persistence_status": "durable",
  "retry_after_ms": 500
}
```

读取策略：

1. 同 worker 先读内存，缺失区间再查 SQLite；
2. 跨 worker 直接查共享 SQLite；
3. `wait_ms>0` 且没有新事件、run 非 terminal 时进入 long-poll；
4. 同 worker 由 Registry condition 唤醒；跨 worker 每 250ms 检查 SQLite durable watermark；
5. 每 worker 最多 64 个并发 long-poll，超出返回 429 和 `Retry-After: 1`；
6. terminal 后立即返回，不等待；
7. `after_seq < earliest_available_seq - 1` 且 SQLite 也无法补齐时返回 HTTP 409：

```json
{
  "detail": {
    "code": "run_event_gap",
    "earliest_available_seq": 18,
    "latest_seq": 42
  }
}
```

404 表示从未找到、作用域不匹配或已经被 retention 清除。前端如果此前持有该 summary，应解释为“历史已过保留期或被容量清理”，而不是自动切换 demo 数据。

## 14. 完整性、desync 与重连语义

四种状态不得混为一谈：

1. `live_transport_desync`：当前 SSE 客户端收到第一批的 `run_event_desync`；只代表该连接丢 typed frame。
2. `registry_event_integrity=partial`：Registry 自身发现 seq/尺寸/容量缺口。
3. `persistence_status=partial|unavailable`：SQLite 历史可能不完整，但同 worker 内存可能完整。
4. `client_reconnecting`：前端 long-poll/HTTP 暂时失败，尚不能推断历史缺失。

Registry sink 直接消费 canonical events，因此 SSE 队列溢出不会自动令 Registry 历史 partial。相反，持久化队列溢出也不会改变当前 SSE `run_event_desync`。

前端重连算法：

```text
after_seq = 已接受的最大连续 seq
GET events?after_seq=...&wait_ms=25000
  success → 应用连续事件，立即继续 long-poll
  timeout → 立即继续 long-poll
  network/5xx → 500ms, 1s, 2s, 5s 上限退避 + 0–20% jitter
  409 gap → 标记历史不完整，刷新 detail，从 earliest seq 重新加载可用区间
  404 known run → 标记已过保留期
  terminal → 停止 long-poll
```

页面隐藏时将 `wait_ms` 保持 25000，但不做频繁无等待刷新；重新可见立即发一次请求。切换 run 或卸载页面必须 AbortController 取消旧 long-poll，旧响应以 request generation 拒绝。

## 15. 前端信息架构

### 15.1 运行历史侧栏

每行只显示：

- 状态、开始时间、耗时；
- run ID 短标识；
- route/executor；
- answered/abstained；
- slow/degraded/retry/stuck/interrupted 徽章；
- 可选 query fingerprint 的短分组标识。

服务端历史不显示问题或答案预览。当前浏览器发起的 live run 可以在本地 UI 显示当前 query，但该文本不得被混入 Runs API 缓存或持久状态。

侧栏筛选：全部、运行中、慢运行、失败、中断、已取消、卡住；与 URL query 同步：

```text
/visualize?run=<run_id>&tab=process&view=errors
```

### 15.2 过程标签页

- 使用 run detail 的冻结 topology 和 events，通过现有 typed reducer 重放。
- completed/active/skipped/failed/degraded/retry 语义与实时图一致。
- 历史 typed run 标记“后端权威历史”；旧 metrics trace 标记“追踪推断”。
- 节点检查器显示结构化 reason、attempt、duration 和计数，不显示内容。

### 15.3 时间线标签页

时间线必须用语义 DOM/CSS 构建，不把 ECharts/canvas 作为唯一表达：

- 每个 `(node_id, attempt)` 由 `node.started` 到 completed/failed/skipped/cancelled 构成 interval；
- 缺开始或结束事件显示 open/partial；
- intervals 按开始时间、node ID、attempt 稳定排序；
- 互相重叠的 interval 形成一个 concurrency wave；重叠关系取传递闭包；
- 每个 wave 显示开始、结束、wall time、节点数和最大并发；
- wave 内用 CSS grid 展示可聚焦的条带；条带长度按 run wall-clock 比例，最小可见宽度 6px；
- retry 使用同一 node 的 attempt badge，不复制无穷拓扑；
- idle gap 超过 250ms 时显示间隙段；
- 提供等价的“节点、attempt、开始、结束、耗时、状态、wave”表格。

点击条带与流程图节点双向联动；键盘 Enter/Space 可选择，Tab 顺序按开始时间。

### 15.4 事件标签页

必须使用原生 `<table>` 或语义等价组件：

| 列 | 内容 |
|---|---|
| Seq | canonical seq |
| 时间 | elapsed 与 occurred_at |
| 类型 | event type |
| 节点 | node label + ID |
| Attempt | 数字或空 |
| 耗时 | duration_ms |
| 摘要 | 允许的枚举/计数 |
| 详情 | `<details><summary>` + `<pre>` 脱敏 JSON |

支持 event type、node、attempt、failed/degraded/retry 筛选。复制按钮只复制 API 已返回的脱敏 JSON；复制成功/失败通过非打断式 live region 提示。

### 15.5 知识标签页

知识标签页不是文档内容浏览器，只投影运行事实中已经存在的安全聚合：

- route 与 effective route；
- HyDE/SubQueries/Stepback/graph/rerank/window 是否启用、跳过或降级；
- candidate/chunk/source/citation 数量；
- 第一轮与第二轮 attempt 对比；
- L1/L2/L3 节点状态、耗时和可证明的聚合计数；
- fallback/degraded reason code；
- 缺字段时显示“本次运行未记录该项”，不按 0 推断。

不得通过额外 API 拉取 chunk 正文、问题、答案或 ACL 来“丰富”该标签页。

### 15.6 保留与降级状态

页面顶部使用互斥/可组合 Banner：

- “正在从服务端恢复运行历史”；
- “正在重连，保留最后一次成功数据”；
- “当前 SSE 连接已失步，服务端历史仍完整”；
- “服务端历史存在缺口”；
- “持久化不可用，当前仅有本 worker 内存历史”；
- “该运行已超过保留期”；
- “旧后端不支持 Runs API，正在使用追踪推断”。

网络失败不能用 demo run 覆盖真实历史。

## 16. Monitor drill-through

Monitor 继续展示系统级聚合，但增加“需要关注的运行”：

- active/stuck；
- 最近 slow；
- failed/interrupted；
- cancelled；
- degraded/retry 较多的运行。

每项携带稳定 `run_id`，点击进入：

```text
/visualize?run=<id>&tab=timeline
/visualize?run=<id>&tab=events
```

Monitor 不再依赖 `recent_queries` 的数组下标作为身份。`/api/metrics.recent_queries` 仍保留给旧后端兼容，但新版 attention 列表来自 `/api/runs`。

## 17. 兼容和渐进增强

前端启动时探测 `/api/runs/health`：

| 情况 | 行为 |
|---|---|
| 200 + ok/degraded | 启用 server history；按 health 展示能力 |
| 200 + disabled | 使用 current live + legacy recent，并显示未启用 |
| 401/403 | 不降级为 demo；显示无 Ops 权限 |
| 404 | 视为旧后端，使用现有 metrics/trace-inferred history |
| network/5xx | 保留最近成功结果并重连；首次无结果时显示不可用 |

当前 live run 的优先级：

1. SSE typed events；
2. 同 run 的 server history 补齐；
3. coarse phase；
4. REST fallback 的本地终态。

历史 run 的优先级：

1. Runs API typed topology/events；
2. 旧 metrics trace-inferred；
3. 不生成虚构 demo history。

不同来源必须用现有 `topologySource` 或扩展后的明确标签展示，不能静默合并成“权威”。

## 18. Failure-silent 行为

### 18.1 Sink 边界

`RunRegistrySink.__call__(event)` 顶层捕获所有异常，只允许：

- rate-limited 日志；
- `run_registry.sink_errors` 计数；
- health 状态降级。

禁止：

- 向 sequencer 抛异常；
- 等待 SQLite；
- 修改 event；
- 调用业务组件；
- 中断 SSE fan-out。

### 18.2 故障矩阵

| 故障 | Registry 行为 | Query/SSE 行为 |
|---|---|---|
| 脱敏拒绝事件 | 丢 Registry 副本、run partial | 不变 |
| active 容量满 | 不跟踪新 run、计数 | 不变 |
| writer queue 满 | 内存继续、persistence partial | 不变 |
| SQLite locked | 有界重试、health degraded | 不变 |
| SQLite 不可写/磁盘满 | memory-only、drop 计数 | 不变 |
| SQLite schema 太新 | 禁止写、可安全时只读 | 不变 |
| SQLite 损坏 | 保留损坏副本、重建或 memory-only | 不变 |
| API 查询失败 | 503 安全错误 | query endpoints 不变 |
| long-poll 容量满 | Ops API 429 | query endpoints 不变 |
| Registry sink 自身异常 | 捕获并计数 | 不变 |

API 503 body 只返回低基数 code，例如 `run_registry_unavailable`，不返回异常文本或数据库路径。

## 19. 指标

增加以下低基数指标：

```text
run_registry.runs_accepted
run_registry.runs_dropped_capacity
run_registry.events_accepted
run_registry.events_rejected_redaction
run_registry.events_dropped_capacity
run_registry.seq_gaps
run_registry.sink_errors
run_history.mutations_enqueued
run_history.mutations_dropped
run_history.commits
run_history.commit_errors
run_history.recovered_interrupted_runs
run_history.retention_deleted_runs
run_ops.requests
run_ops.errors
run_ops.long_poll_active
run_ops.long_poll_rejected
```

直方图：

```text
run_history.commit_ms
run_history.commit_batch_size
run_history.persistence_lag_ms
run_ops.request_ms
```

标签只能使用固定 endpoint/view/status/result；不得用 run_id、tenant、fingerprint、route 或 error message 作为指标 label。

## 20. 测试策略

### 20.1 单元测试

`tests/test_run_registry.py`：

- canonical lifecycle 投影；
- duplicate/out-of-order/gap/terminal 后事件；
- active/recent/event 容量与 TTL；
- topology 和 node rollup；
- stuck/slow/errors/cancelled 派生；
- throwing sink failure-silent；
- query/answer/ACL/chunk/API key/异常消息 sentinel 全部不进入内存。

`tests/test_run_history_store.py`：

- schema v1 和 PRAGMA；
- batch upsert、幂等 event、冲突内容拒绝；
- writer queue 饱和、locked、disk/unwritable 模拟；
- clean shutdown drain；
- 30 天/100000 容量清理；
- restart 后 recent/detail/events 可读；
- stale worker recovery 为 interrupted 且不伪造 canonical terminal；
- 两个 worker 对同一 WAL 文件并发写/读；
- corrupt DB rotation 和 schema-too-new；
- raw sensitive sentinel 不进入 SQLite 文件或查询结果。

`tests/test_run_ops_api.py`：

- loopback 默认允许；
- remote 无 token 403、错误 token 401、正确 token 允许；
- token 不在 health/config/log；
- tenant 默认解析、显式 scope、cross-tenant 404；
- list 五种 view 和 cancelled status；
- keyset pagination 在并发插入下无重复/跳过；
- cursor tamper/expiry/scope/filter mismatch；
- detail schema；
- events after_seq/limit/terminal/timeout/gap；
- long-poll 唤醒、跨 worker durable poll、64 客户端上限；
- API 失败不影响 query endpoint。

### 20.2 业务兼容回归

扩展现有 parity 测试，对同一桩管线分别运行：

1. 无 Registry；
2. 正常 Registry + SQLite；
3. Registry sink 每次抛异常；
4. SQLite 永久失败；
5. writer queue 饱和。

逐项断言：

- `QueryResult` 完全相等；
- legacy SSE 过滤 typed 后完全相等；
- typed SSE 完全相等；
- cache payload 完全相等；
- HTTP status 与错误 code 完全相等；
- 只有 Registry history/health 不同。

### 20.3 前端测试

- Runs API runtime validation；
- list pagination 与 stale response rejection；
- URL deep link；
- current live 与 server history 去重；
- server typed event replay；
- long-poll abort/reconnect/backoff/gap/terminal；
- 404 old backend 与 401/403 权限差异；
- persistence/desync/reconnect banner；
- timeline interval、wave、idle gap、retry 和 partial interval；
- Event table filter、键盘、复制和读屏文本；
- Knowledge 缺字段不按 0 推断；
- Monitor drill-through stable run ID；
- 不在失败时注入 demo history。

### 20.4 视觉和可访问性

至少验收：

- 1440×900、1366×768、375×812；
- light/dark；
- active、completed、failed、cancelled、interrupted、stuck；
- 并行 wave、二轮 retry、degraded、history gap、reconnecting、retention expired；
- 200、1000 个事件时表格虚拟化或分页仍可操作；
- 仅键盘完成筛选、选择 run、切 tab、选节点、展开 JSON；
- 图形之外有完整文本/表格等价信息；
- 状态不只依赖颜色；
- 计时器不以 200ms 频率打扰 live region。

## 21. 性能预算

在本地 release 测试中必须满足：

- Registry sink P95 自耗时低于 1ms，P99 低于 2ms；测试不含 SQLite I/O，因为业务线程不得进行 I/O。
- 单事件脱敏后不超过 16 KiB，topology 不超过 128 KiB。
- 1000 条 event 的 detail/events 分页响应在 warm SQLite 下 P95 低于 100ms。
- `GET /api/runs?limit=50` 在 100000 run 数据集上 P95 低于 150ms。
- writer 默认 batch 下持久化 lag 正常 P95 低于 500ms。
- 前端一次只渲染当前事件页，默认 200 条；不得一次把 30 天所有 events 装入 DOM。
- long-poll 不占用 query executor；Ops 并发上限独立。

性能失败只能优化 Registry/Ops，不得通过减少 canonical 业务事件或改变问答执行来达标。

## 22. 验收标准

Route B 完成必须有当前状态证据证明：

### 后端事实与历史

- [ ] 现有 streaming run 自动进入 Registry，无需业务组件感知 Registry。
- [ ] active/recent/slow/errors/stuck/cancelled 都能通过 API 正确查询。
- [ ] 运行详情返回冻结 topology、node rollup 和完整性元数据。
- [ ] 事件按 canonical seq 分页并可 long-poll 续接。
- [ ] SQLite 使用独立 `data/run-history.sqlite3` 和 WAL。
- [ ] 服务重启后 terminal 历史仍可读，旧 active 被标记 interrupted。
- [ ] 两个 worker 共享 SQLite 时 tenant 过滤、分页和 durable 读取正确。
- [ ] retention 与容量有界，active 不被 retention 直接删除。

### 安全与隐私

- [ ] 内存、SQLite、API 和日志均无 raw query/answer/prompt/token/ACL/chunk/异常消息。
- [ ] query fingerprint 只有配置 HMAC secret 时存在。
- [ ] 默认只有 loopback 可访问 Ops；远程必须 Bearer token。
- [ ] secret 不由配置 API 回显。
- [ ] 每个 Runs 请求只有一个 tenant scope，跨 tenant 用相同 404 隐藏。
- [ ] cursor 签名、过期、filter/scope 绑定均有测试。

### Failure-silent 与兼容

- [ ] Registry、writer、SQLite、long-poll 故障不改变 QueryResult、cache 或 SSE。
- [ ] 原有 `phase/token/done/error/run_event/run_event_desync` 顺序与 payload 兼容。
- [ ] REST 和 LangGraph 没有被偷偷改成半成品 typed adapter。
- [ ] Registry disabled 时现有产品功能和旧前端 fallback 正常。

### 前端 Ops

- [ ] Visualize 使用服务端历史并提供过程/时间线/事件/知识四标签页。
- [ ] timeline 是可访问 DOM，事件有可访问表格和脱敏 JSON。
- [ ] current live、server typed、legacy inferred 来源明确。
- [ ] desync、partial、memory-only、reconnecting、expired 状态明确且不互相误判。
- [ ] Monitor 可以按稳定 run ID 钻取 slow/error/stuck/cancelled。
- [ ] URL 可恢复 run、tab、view；迟到响应不能污染当前选择。
- [ ] 旧后端、无权限、网络失败不会被 demo 数据掩盖。

### 验证门禁

- [ ] 后端全量 pytest 通过，并包含上述新测试。
- [ ] 触及 Python 文件 Ruff 通过。
- [ ] 前端全量测试、ESLint、production build 通过。
- [ ] 桌面/移动、light/dark 和关键异常状态完成浏览器截图验收。
- [ ] 独立代码审查没有剩余 Critical 或 Important 问题。
- [ ] 实施文档更新项目内副本，并同步更新用户要求的桌面详细文档。

## 23. 实施顺序约束

后续实施计划应按以下可独立验收的纵切拆分：

1. 配置、脱敏、内存 Registry 和状态机；
2. SQLite schema、writer、heartbeat、restart recovery 和 retention；
3. operator access、tenant scope、signed cursor 和四个 API；
4. server sink/lifespan 集成与完整业务 parity；
5. 前端 Runs client、历史 sidebar 和兼容探测；
6. server event replay、过程图和 long-poll 重连；
7. DOM timeline、事件表、知识面板；
8. Monitor drill-through、可访问性、性能和全量验收；
9. 更新最终项目文档与桌面副本。

每个纵切使用测试先行，并在进入下一纵切前完成规格符合性和代码质量复核。多 Agent 可以并行处理无共享写入的测试、前端组件或审查任务，但 `core/run_registry.py`、SQLite schema、API DTO 和前端 DTO 的公共契约必须由主实现线统一裁决，避免并行分叉。

## 24. 最终裁决摘要

Route B 的目的不是再造一个执行引擎，也不是把 Waku 页面照搬到 RAG4C。它把第一批已经可靠的 canonical run facts 延伸成运维基础设施：

- 内存给当前 worker 低延迟投影；
- SQLite WAL 给重启恢复和多 worker 可见性；
- Runs API 给历史、slow/error/stuck 和 reconnect 提供稳定契约；
- 前端用过程图、DOM 时间线、脱敏事件表和知识聚合解释同一运行；
- 隐私、租户和 failure-silent 是数据进入 Registry 前的硬边界；
- RAG4C 的业务编排、缓存、弃权、核验和现有 SSE 保持原样。

本规格已获得实施授权。任何扩大到 REST/LangGraph parity、EvalReport v2 或跨租户管理面的工作，仍需在 Route B 完成后单独设计和审批。
