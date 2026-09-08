# RAG4C 运行观测事实层设计规格

> **状态：已批准并完成第一批实施（2026-08-23）。** 最终证据：后端 381 个测试通过，前端 47 个测试通过，前端 Lint/Build 与触及后端文件 Ruff 均通过。

## 实施结果补充

- 第一批 typed run facts 只覆盖主产品路径 `POST /api/query/stream`。
- `POST /api/query` REST 仍作为兼容与非流式回退接口；第一批不为它创建后端 typed run。前端会在 SSE 失败后用本地终态收敛流程图。
- typed 事实通过独立 `type="run_event"` SSE 帧发送；旧 `phase/token/done/error` 帧保持兼容。
- typed 缓冲区采用 256 条有界队列。溢出后停止声称历史完整，并发送一次 `run_event_desync`，包含 `run_id`、首个缺失 `expected_seq` 与 `typed_buffer_overflow` 原因。
- 前端收到 desync 后停止消费后续 typed facts，但仍允许 legacy `done`、REST fallback 或用户取消收敛终态。
- 第二批能力（REST/LangGraph parity、持久化 RunRegistry、EvalReport v2）仍未实施，需要重新审批。

## 1. 目标

为 RAG4C 建立一份后端权威的、引擎无关的运行事实，使下列界面成为同一事实的不同投影：

- 问答页紧凑进度；
- 回答过程实时流程图；
- 运行历史、慢请求、错误和卡住运行；
- 后续评估 case 到真实运行路径的关联。

第一批必须解决：

1. 前端不再依靠固定节点或中文 trace 正则猜测当前执行位置。
2. 每个请求拥有后端 `run_id`、严格递增 `seq` 和恰好一个终态。
3. 动态配置在运行开始时冻结成不可变拓扑快照。
4. 顺序流式管线、可选检索插件、缓存回放、排队、取消和超时使用同一事件语义。
5. 观测不能改变答案、引用、弃权、缓存和旧 SSE 行为。

## 2. 保留的 RAG4C 特色

本改造不得：

- 把流式默认路径迁移到 LangGraph；
- 删除或弱化 RAG4C 顺序流式编排；
- 删除 Provider 注册表、策略开关或组件注入；
- 删除 HyDE、SubQueries、Stepback、图检索、重排、Sentence Window；
- 改变二轮证据补救、降级或弃权语义；
- 把 L1/L2/L3 三层核验压缩成通用 agent tool 事件；
- 把 Waku 的内部节点名称当成 RAG4C 公共协议。

LangGraph 第一批不做插桩，只保留现有行为；后续通过 adapter 映射到同一语义节点。

## 3. 非目标

第一批不实现：

- 正式运行历史数据库和跨进程 RunRegistry；
- EvalReport v2、baseline、compare 和 release gate；
- LangGraph 事件 parity；
- 每个 token 的长期观测事件；
- 完整 query、prompt、答案、ACL 或文档正文的持久化；
- 可视化流程编辑器。

## 4. 总体架构

```text
HTTP /api/query/stream
        │
        ▼
server.app owns RunEventSequencer + topology snapshot
        │
        ├── cache / single-flight / queue / timeout / cancellation events
        │
        ▼
rag_stream.answer_query_stream(..., observer=...)
        │
        ├── retrieval / abstention / generation / verification / retry events
        │
        ▼
RetrievalPipeline.run()
        │
        ├── gate / rewrite / route / plugin / graph / rerank / window events
        │
        ▼
RunEventSink fan-out
        ├── SSE run_event frame
        ├── optional in-memory collector for tests
        └── future registry / logs / eval linkage
```

关键所有权：

- `server/app.py` 创建 run、冻结拓扑并拥有唯一 terminal。
- `rag_stream.py` 只报告业务生命周期，不在 server-owned 模式发 terminal。
- `retrieval/pipeline.py` 通过请求级 observer 报告内部节点；不改变 `run()` 的业务返回值。
- `core/tracing.py` 继续负责 span 和 metrics，只提供可选 observer adapter，不成为第二套事实源。

## 5. 运行事件信封

创建 `core/run_events.py`。

```python
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
EventAttributes = dict[str, JsonValue]

class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    run_id: str
    seq: int = Field(ge=1)
    occurred_at: datetime
    elapsed_ms: float = Field(ge=0)
    topology_id: str
    topology_revision: str
    type: Literal[
        "run.started",
        "node.started",
        "node.completed",
        "node.failed",
        "node.skipped",
        "node.cancelled",
        "route.selected",
        "retry.started",
        "retry.completed",
        "retry.failed",
        "retry.skipped",
        "degraded",
        "run.completed",
        "run.failed",
        "run.cancelled",
    ]
    node_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    duration_ms: float | None = Field(default=None, ge=0)
    attributes: EventAttributes = Field(default_factory=dict)
    error: EventError | None = None
```

`RunEvent` 不包含 token 文本。生成 token 继续使用旧 SSE `token` 帧；生成节点完成时只记录 `output_chars`、可用时记录 `token_count` 和 `first_token_ms`。

## 6. Sequencer 与安全 Observer

同一文件提供：

```python
class RunEventSink(Protocol):
    def __call__(self, event: RunEvent, /) -> None: ...

class RunEventSequencer:
    def start_run(...) -> RunEvent: ...
    def start_node(...) -> RunEvent: ...
    def complete_node(...) -> RunEvent: ...
    def fail_node(...) -> RunEvent: ...
    def skip_node(...) -> RunEvent: ...
    def cancel_node(...) -> RunEvent: ...
    def route_selected(...) -> RunEvent: ...
    def retry_started(...) -> RunEvent: ...
    def retry_completed(...) -> RunEvent: ...
    def degraded(...) -> RunEvent: ...
    def complete_run(...) -> RunEvent: ...
    def fail_run(...) -> RunEvent: ...
    def cancel_run(...) -> RunEvent: ...

class RunObserver:
    """业务代码使用的失败静默外观；内部生命周期错误只记录日志。"""
```

不变量：

- `run.started` 必须是 `seq=1`。
- seq 从 1 开始、无重复、严格递增。
- `elapsed_ms` 使用 monotonic clock，不能受系统时间回拨影响。
- 一个 node attempt 最多 start 一次。
- started node 必须由 completed、failed 或 cancelled 闭合。
- skipped node 不允许先 started。
- 非 repeatable node 只能 attempt 1。
- repeatable node attempt 连续增长。
- terminal 恰好一次，并且是最后一个 canonical event。
- terminal 后的事件被 observer 安全拒绝，不能影响问答。
- sink 异常被吞掉；记录 sink 和抛异常 sink 下的 QueryResult 必须相同。

## 7. 不可变拓扑

创建 `rag_topology.py`，不得依赖 LangGraph。

```python
@dataclass(frozen=True, slots=True)
class TopologyNode:
    id: str
    label: str
    group: Literal["input", "understand", "retrieve", "generate", "verify", "output", "extension"]
    description: str
    optional: bool = False
    repeatable: bool = False
    available: bool = True
    plugin: str | None = None
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class TopologyEdge:
    id: str
    source: str
    target: str
    kind: Literal["dependency", "conditional", "retry", "failure"]
    label: str | None = None

@dataclass(frozen=True, slots=True)
class RagTopology:
    id: str
    revision: str
    executor: str
    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]
```

核心节点使用 RAG 语义：

```text
receive
complexity_gate
rewrite
route
plugin.hyde
embed
search
plugin.subqueries
plugin.stepback
diversity
graph
rerank
sentence_window
gate.retrieval
generate
verify
gate.final
finalize
```

拓扑规则：

- 根据本次有效配置和已装配组件生成。
- 配置开启但组件不可用时保留节点，`available=False`，运行时发 skipped/degraded。
- 插件节点必须使用 `plugin.<name>.<operation>` 命名空间。
- 插件不得覆盖核心节点和核心边。
- 只有 `verify -> search` 的 retry 边允许形成第一批循环。
- canonical JSON 按 node ID、edge ID 排序；revision 使用 SHA-256 截断值。
- 每次 `run.started` 携带完整拓扑快照；运行中配置变化不改变该快照。
- 缓存回放使用独立 `cache_replay` 拓扑，不伪装成重新执行 RAG。

## 8. SSE 兼容

旧帧保持原样：

```text
phase
token
done
error
```

新增独立帧：

```json
{
  "type": "run_event",
  "event": {
    "schema_version": 1,
    "run_id": "run-...",
    "seq": 3,
    "type": "node.started",
    "node_id": "search",
    "attempt": 1,
    "topology_id": "rag.query",
    "topology_revision": "sha256:...",
    "occurred_at": "2026-08-22T12:00:00Z",
    "elapsed_ms": 12.4,
    "attributes": {}
  }
}
```

兼容约束：

- 过滤掉 `type=run_event` 后，旧事件类型、顺序和 payload 必须逐项等于改造前。
- 旧客户端当前会忽略未知类型，因此无需一次性升级。
- 新前端优先消费 typed events；未收到时继续使用 coarse phase 和 trace-inferred history。
- 不把 run event 写入 `QueryResult.traces`。
- 不把 run ID、seq 或 topology 存入答案缓存后原样回放；每个缓存请求创建新的 replay run。
- SSE 使用 `ensure_ascii=False`、`allow_nan=False`。

## 9. 插桩语义

### 9.1 `server/app.py`

必须覆盖：

- 请求进入；
- L1/L2 cache lookup 和 replay；
- same-process / cross-process single-flight；
- queue admission、slot wait、429、503；
- owner 计算；
- stream timeout、producer failure、客户端取消；
- cache write、flight release；
- 唯一 terminal。

服务层不得记录 cache key、完整 query、ACL 或 tenant 原值。

### 9.2 `rag_stream.py`

必须覆盖：

- retrieval attempt 1；
- retrieval abstention gate；
- generation / verification attempt 1；
- second-round retry、retrieval/generation/verification attempt 2；
- generation/verifier failure 的 recoverable degraded；
- final abstention gate；
- answered 或 abstained result。

保留原 phase/token/done 行为。

### 9.3 `retrieval/pipeline.py`

必须覆盖：

- complexity gate、rewrite、route；
- HyDE、embed、hybrid search；
- SubQueries、Stepback、diversity；
- graph branch 和 effective route；
- rerank、Sentence Window、dataset scope、truncate；
- optional node skipped；
- recoverable degraded 和 fallback 原因枚举。

并行插件线程不直接写 sequencer；父线程在 Future 完成后按接受顺序发事件。

## 10. 指标口径修正

第一批新增而不删除旧指标：

```text
query.requests
query.completed
query.errors
query.abstained
query.cache_hits
query.singleflight_joined
query.rejected
query.cancelled
query.timeouts
```

定义：

- requests：进入 HTTP query 端点的请求。
- completed：成功交付 answered 或 abstained QueryResponse。
- errors：没有形成 QueryResponse 的内部失败。
- cancelled：客户端取消或响应未消费。
- timeouts：排队、single-flight 或 stream deadline 超时。
- cache_hits：L1/L2 答案缓存直接返回。
- rejected：队列满或服务主动拒绝。

旧 `query.total` 与 `query.total.errors` 第一批保留，标记为兼容指标；前端不再用它们计算正式错误率。

## 11. 隐私和性能

事件 attributes 只允许：

- 枚举、布尔、计数、耗时、attempt、route、confidence、chunk_count；
- 标准化 reason code 和 error type；
- 低基数 executor/component 名称。

禁止：

- query、prompt、token 文本、答案、完整异常消息；
- ACL 列表、原始 tenant/dataset 值；
- 文档正文、chunk 文本、embedding、score 数组；
- 任意 Pydantic/ORM/Exception 自定义对象。

第一批 sink 必须同步、无阻塞；SSE sink 只写入当前请求的有界队列。未来持久化使用独立异步 journal，不在业务线程写磁盘。

## 12. 前端迁移

新增 `StreamHandlers.onRunEvent`；`streamAnswer` 解析 `type=run_event`。

前端 run store：

- 以后端 run ID 和 seq 为准；
- `run.started` 替换 coarse topology 为 typed topology；
- 拒绝 run ID 不匹配、seq 倒退或 terminal 后事件；
- 动态节点仅来自 topology 或后续 `node.registered` 扩展；
- 未收到 typed event 时保持现有兼容投影。

## 13. 测试门禁

必须新增：

- `tests/test_run_events.py`
- `tests/test_rag_topology.py`
- `tests/test_stream_observability.py`
- `tests/test_sse_run_event_compat.py`

必须扩展：

- `tests/test_stream_contract.py`
- retrieval pipeline 现有 fake/stub 测试
- 前端 run reducer 和 stream client 测试

最高优先级回归：同一组桩组件分别使用无 observer、记录 observer、抛异常 observer，断言：

- QueryResult 完全相等；
- 旧 SSE 事件过滤后完全相等；
- 缓存载荷完全相等；
- 只有观测 journal 不同。

## 14. 第一批审批范围

获批后允许修改：

- 新建 `core/run_events.py`
- 新建 `rag_topology.py`
- 修改 `core/tracing.py`
- 修改 `rag_stream.py`
- 修改 `retrieval/pipeline.py`
- 修改 `server/app.py`
- 修改前端 typed event adapter/store/visualization
- 新增和扩展上述测试
- 修正并新增请求计数指标

明确不允许顺带修改：

- `rag_graph.py` 执行图和 LangGraph 行为；
- EvalReport 后端结构；
- 正式持久化和数据库；
- Provider 与策略默认值；
- 问答、弃权、重试和缓存业务语义。

## 15. 延后批次

第二批重新审批：

- LangGraph adapter 与 sequential parity；
- RunRegistry、历史、slow/errors/stuck 接口；
- 运行事件持久化、脱敏和保留策略；
- EvalReport v2、评测历史、baseline/compare/gates；
- eval case 与 run ID 关联；
- L1/L2/L3 原生 case 结果。
