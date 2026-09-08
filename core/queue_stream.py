"""基于 Redis Streams 的跨副本请求准入队列。

**先说清楚它不解决什么。**

单进程内，``server/app.py`` 的 ``asyncio.Semaphore(32)`` + ``_pending`` 计数
已经是完备的准入控制，本模块在那种部署下**纯属多余**——多一次网络往返、
多一个可挂的组件、多一份运维负担。所以默认 ``queue_backend="memory"``，
本模块整体处于关闭状态。

它挣钱的场景只有一个：**多副本**。这时进程内信号量各管各的，N 个副本
就是 N×32 并发打向同一套 Milvus / 嵌入 / LLM 配额，"32 并发"这个数字失去
意义；``/readyz`` 报出来的 pending 也只是本副本的局部视图。换成 Streams 后：

1. **共享准入**：所有副本抢同一条流，全局并发 = 消费者总数，配额可控；
2. **崩溃恢复**：worker 进程被 OOM kill 时，它已 XREADGROUP 但未 XACK 的
   任务留在 PEL 里，别的副本用 XAUTOCLAIM 捡回来重做——这是 Streams 相对
   ``LPUSH/BRPOP`` 唯一实质性的优势。裸 List 一旦 BRPOP 弹出，进程一死
   任务就人间蒸发；
3. **全局可见深度**：XLEN / XPENDING 是集群级真值，不是某个副本的臆测。

**代价要认**：多一跳 XADD + 结果回传轮询（热路径上约几十毫秒），任务载荷
要能 JSON 序列化，且请求语义从"本进程直接算"变成"托给某个副本算"——
调试链路变长。一次问答实测 ~5.6s，几十毫秒的开销可以忽略，但如果哪天
链路变成 50ms 级，这个队列就该拆掉。

契约：**本模块所有公开出口都不抛异常**。Redis 没配 / 连不上 / 命令失败，
一律返回"未激活"（``Admission.state == "inactive"``、``is_active() is False``），
调用方据此原样走进程内信号量那条老路——降级是默认行为，不是异常分支。

键空间（全部经 ``redis_client.key()`` 加前缀）::

    <prefix>q:query        请求流（Stream，MAXLEN 近似裁剪）
    <prefix>res:<job_id>   结果回传（String，带 TTL，取走即删）

生产者 / 消费者协议::

    submit()  --XADD-->  流  --XREADGROUP-->  consume_once()/Worker
        ^                                            |
        +---- 轮询 res:<job_id> <---- SET+TTL --------+
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from core import redis_client
from core.observability import get_logger

_logger = get_logger(__name__)

#: 消费者组名。写死而非配置项：改组名等于换一条独立队列，滚动升级时会
#: 出现两组 worker 各抢各的，是"能配但不该配"的选项。
GROUP = "workers"

#: 流的键名分段（与 core/redis_client.py 文档里登记的键空间一致）。
_STREAM_PARTS = ("q", "query")

#: XAUTOCLAIM 的最小空闲时长。必须**大于一次任务的最坏耗时**，否则会把
#: 正在老老实实干活的 worker 手里的任务抢走，变成重复执行。默认按查询总闸
#: （240s）再放一倍余量。
DEFAULT_MIN_IDLE_MS = 300_000

#: 结果键 TTL。等待方就在线上等，取走即删，TTL 只是给"等待方已超时离场"
#: 的孤儿结果兜底，不需要长。
DEFAULT_RESULT_TTL_S = 300.0

# 与 server/app.py 的默认值保持一致。这里重读环境变量而不是 import
# server.app：那会造成 core -> server 的反向依赖，且把 FastAPI 拖进
# 任何只想用队列的进程（比如纯 worker）。
_DEFAULT_MAX_CONCURRENT = int(os.environ.get("RAG4C_QUERY_MAX_CONCURRENT", "32"))
_DEFAULT_QUEUE_MAX = int(os.environ.get("RAG4C_QUERY_QUEUE_MAX", "128"))

# 进程级"该流的消费者组已确认存在"缓存。XGROUP CREATE 是幂等的，但每次
# submit 都发一遍等于把热路径的往返数翻倍。
_group_lock = threading.Lock()
_group_ready: set[str] = set()


# ---------------------------------------------------------------------------
# 配置与工具
# ---------------------------------------------------------------------------

def _config() -> Any:
    from config.settings import get_settings

    return get_settings().redis


def stream_key() -> str:
    """当前配置下的流键名（含前缀）。"""
    return redis_client.key(*_STREAM_PARTS)


def consumer_name() -> str:
    """本进程的消费者名：``<主机名>-<pid>``。

    XPENDING / XINFO CONSUMERS 里直接能看出是哪台机器的哪个进程卡住了；
    用随机 UUID 的话，排查时只能对着一串十六进制发呆。
    """
    try:
        host = socket.gethostname() or "unknown"
    except Exception:
        host = "unknown"
    return f"{host}-{os.getpid()}"


def _text(value: Any) -> str:
    """把 Redis 回来的 bytes / 其它标量统一成 str。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None:
        return ""
    return str(value)


def _safe_block_ms(block_ms: int) -> int:
    """把 BLOCK 时长压到 socket_timeout 以内。

    这是个必踩的坑：``XREADGROUP BLOCK 5000`` 在 ``socket_timeout=1.0`` 的
    连接上，服务端还在阻塞等消息，客户端已经先超时抛 ``TimeoutError`` 了——
    表现为"worker 一直报错但 Redis 一切正常"。取 80% 留出往返余量。
    """
    try:
        budget = float(_config().socket_timeout_s)
    except Exception:
        budget = 1.0
    cap = max(50, int(budget * 1000 * 0.8))
    return max(0, min(int(block_ms), cap))


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Job:
    """一条被消费到的任务。"""

    entry_id: str                 # 流内条目 ID（XACK / XDEL 用）
    job_id: str                   # 业务任务 ID（结果键用）
    payload: dict[str, Any]       # 提交时的载荷
    enqueued_at: float            # 入队时刻（unix 秒，生产者时钟）
    result_ttl_s: float = DEFAULT_RESULT_TTL_S  # 生产者指定的结果键 TTL
    reclaimed: bool = False       # 是否由 XAUTOCLAIM 捡回（= 上一个 worker 没干完）

    @property
    def queued_s(self) -> float:
        """从入队到被取出的等待时长（秒）。跨机时钟不同步可能为负，故取下限 0。"""
        return max(0.0, time.time() - self.enqueued_at)


@dataclass(frozen=True)
class Admission:
    """``submit()`` 的结果。调用方按 ``state`` 分支，不需要 try/except。

    - ``done``     ：任务已被某个 worker 执行完，``result`` 有效；
    - ``rejected`` ：队列已满，调用方应返回 429（对应内存态 ``_inc_pending``
      返回 False 的那条路）；
    - ``timeout``  ：等待超时，调用方应返回 504；
    - ``failed``   ：worker 执行时抛了异常，``detail`` 是脱敏后的摘要；
    - ``inactive`` ：队列未启用 / Redis 不可用，**调用方应退回进程内信号量**。
    """

    state: str
    job_id: str = ""
    result: Any = None
    detail: str = ""
    waited_s: float = 0.0

    @property
    def ok(self) -> bool:
        """是否拿到了结果。"""
        return self.state == "done"

    @property
    def should_fallback(self) -> bool:
        """是否应退回进程内准入控制（而不是把错误抛给用户）。"""
        return self.state == "inactive"


# ---------------------------------------------------------------------------
# 激活判定与消费者组
# ---------------------------------------------------------------------------

def is_active() -> bool:
    """队列是否真正可用（backend=redis 且连得上）。任何异常都算不可用。"""
    try:
        if str(_config().queue_backend).strip().lower() != "redis":
            return False
        if not redis_client.is_configured():
            return False
        return redis_client.get_client() is not None
    except Exception:
        return False


def ensure_group(*, force: bool = False) -> bool:
    """确保流与消费者组存在（MKSTREAM）。已存在时返回 True。

    ``BUSYGROUP`` 是**正常情况**：每个副本启动都会创建一遍，先到的那个赢。
    只有它才算"已存在"，其它错误（连不上、类型冲突）一律算失败。

    这里也是 ``queue_backend`` 开关的**唯一执行点**：所有出口（fetch /
    reclaim / stats / submit）都要先过这道门，关掉开关就等于整条链路失效，
    不会出现"生产者走内存态、消费者却在啃 Redis"的半开状态。
    """
    if not is_active():
        return False
    stream = stream_key()
    if not force:
        with _group_lock:
            if stream in _group_ready:
                return True

    client = redis_client.get_client()
    if client is None:
        return False
    try:
        client.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            _logger.debug("XGROUP CREATE 失败：%s", exc)
            return False
    with _group_lock:
        _group_ready.add(stream)
    return True


def reset() -> None:
    """丢弃进程内的"组已就绪"缓存（配置变更 / 测试后调用）。"""
    with _group_lock:
        _group_ready.clear()


# ---------------------------------------------------------------------------
# 深度与统计
# ---------------------------------------------------------------------------

def _counts() -> Optional[tuple[int, int, int]]:
    """返回 (存活条目数, 未 ACK 数, 消费者数)；不可用时 None。

    "存活条目数"就是 XLEN——**前提是完成时 XACK 之后还要 XDEL**
    （见 :func:`complete`）。这是 Streams 最容易踩空的地方：XACK 只把条目
    从 PEL 摘掉，条目本身仍留在流里计入 XLEN，不 XDEL 的话 XLEN 是
    "历史累计提交量"而非队列深度，拿它做背压会在跑满 queue_max 条之后
    永久拒绝所有请求。
    """
    total = redis_client.call("xlen", stream_key())
    if total is None:
        return None
    unacked = 0
    consumers = 0
    info = redis_client.call("xpending", stream_key(), GROUP)
    if isinstance(info, dict):
        unacked = int(info.get("pending") or 0)
        consumers = len(info.get("consumers") or ())
    return int(total), unacked, consumers


def stats(
    *,
    max_concurrent: int | None = None,
    queue_max: int | None = None,
) -> dict[str, Any]:
    """状态快照。字段与 ``server/app.py:_queue_stats()`` 兼容，可直接顶替。

    ``pending`` 的语义对齐内存态："执行中 + 排队中"，即流内全部存活条目
    （未 ACK 的在执行，未投递的在排队）。差别只是内存态数的是本进程，
    这里数的是全集群——这正是换 Streams 想要的东西。
    """
    limit_conc = _DEFAULT_MAX_CONCURRENT if max_concurrent is None else int(max_concurrent)
    limit_queue = _DEFAULT_QUEUE_MAX if queue_max is None else int(queue_max)
    try:
        backend = str(_config().queue_backend).strip().lower()
    except Exception:
        backend = "memory"

    snapshot: dict[str, Any] = {
        "pending": 0,
        "max_concurrent": limit_conc,
        "queue_max": limit_queue,
        "backend": backend,
        "active": False,
        "queued": 0,
        "unacked": 0,
        "consumers": 0,
        "stream": "",
        "group": GROUP,
        "detail": "未启用（使用进程内信号量）",
    }
    if not is_active():
        if backend == "redis":
            snapshot["detail"] = "Redis 不可用（已退回进程内信号量）"
        return snapshot

    snapshot["stream"] = stream_key()
    if not ensure_group():
        snapshot["detail"] = "消费者组不可用（已退回进程内信号量）"
        return snapshot

    counts = _counts()
    if counts is None:
        snapshot["detail"] = "深度查询失败（已退回进程内信号量）"
        return snapshot

    total, unacked, consumers = counts
    snapshot.update(
        {
            "active": True,
            "pending": total,
            "queued": max(0, total - unacked),
            "unacked": unacked,
            "consumers": consumers,
            "detail": "ok",
        }
    )
    return snapshot


def depth() -> Optional[int]:
    """当前队列深度（执行中 + 排队中）；不可用时 None。"""
    counts = _counts()
    return None if counts is None else counts[0]


# ---------------------------------------------------------------------------
# 生产者
# ---------------------------------------------------------------------------

def submit(
    payload: Mapping[str, Any],
    *,
    timeout_s: float = 240.0,
    queue_max: int | None = None,
    result_ttl_s: float | None = None,
    poll_min_s: float = 0.01,
    poll_max_s: float = 0.2,
) -> Admission:
    """入队一个任务并**同步等待**结果。绝不抛异常。

    同步等待是刻意的：HTTP 端点的契约是"一问一答"，改成 202 + 轮询要动
    前端、动 SDK、动所有调用方，收益却只是省下一个挂起的协程。这里把
    异步队列包成同步调用，代价是等待方要占一个线程（调用方应在
    ``run_in_executor`` 里调，别堵住事件循环）。

    结果回传走 ``SET res:<job_id>`` + 轮询，而不是 Pub/Sub 或 per-job 流：

    - **Pub/Sub 不行**：worker 可能在等待方 SUBSCRIBE 完成之前就把结果发了，
      消息直接丢失，还得再补一次 GET 兜底——两套机制，复杂度翻倍；
    - **per-job 流 + XREAD BLOCK 不行**：``socket_timeout_s`` 默认 1.0s，
      长 BLOCK 会先被客户端超时打断（见 :func:`_safe_block_ms`），退化成
      轮询；而且每个任务留一个流对象要显式删，String 的 TTL 自己会过期；
    - 轮询从 10ms 起指数退避到 200ms。一次问答 ~5.6s，多等这点可忽略；
      真正的成本是空转的 GET，退避就是为了压住它。

    Args:
        payload: 任务载荷，必须能 JSON 序列化。
        timeout_s: 等待结果的总预算（秒）。到点后尽力 XDEL 掉自己那条
            未投递的条目——"排到最后终于被执行、但客户端早就走了"的活
            是纯浪费。
        queue_max: 深度上限，超过直接拒绝（调用方 429）。None 取
            ``RAG4C_QUERY_QUEUE_MAX``。
        result_ttl_s: 结果键 TTL。None 时取 max(超时预算, 默认值)。
        poll_min_s / poll_max_s: 轮询退避区间。
    """
    started = time.monotonic()
    limit = _DEFAULT_QUEUE_MAX if queue_max is None else int(queue_max)
    ttl = int(max(DEFAULT_RESULT_TTL_S, timeout_s) if result_ttl_s is None else result_ttl_s)

    if not is_active():
        return Admission("inactive", detail="队列未启用或 Redis 不可用")
    if not ensure_group():
        return Admission("inactive", detail="消费者组不可用")

    counts = _counts()
    if counts is None:
        return Admission("inactive", detail="深度查询失败")
    total = counts[0]
    if limit > 0 and total >= limit:
        # 拒绝而不是让流无限涨：满队列里排队的请求最终都会超时，
        # 与其让它们占着资源慢慢死，不如立刻 429 让调用方重试或降级。
        return Admission(
            "rejected",
            detail=f"队列已满（{total}/{limit}）",
            waited_s=time.monotonic() - started,
        )

    job_id = uuid.uuid4().hex
    try:
        body = json.dumps(dict(payload), ensure_ascii=False, default=str)
    except Exception as exc:
        return Admission("failed", job_id=job_id, detail=f"载荷无法序列化：{exc}")

    try:
        maxlen = int(_config().stream_maxlen)
    except Exception:
        maxlen = 10_000

    stream = stream_key()
    # MAXLEN 近似裁剪是**兜底**不是准入：queue_max（128）远小于
    # stream_maxlen（10000），正常情况下轮不到裁剪出手。它防的是
    # "消费者全挂 + 生产者不看深度"这类失控场景把内存吃光。
    #
    # ttl 随任务一起入队：只有生产者知道自己还愿意等多久，让 worker 拿
    # 一个全局默认值去写结果键，要么白占内存、要么在慢查询下提前过期。
    entry_id = redis_client.call(
        "xadd",
        stream,
        {
            "job": job_id,
            "ts": repr(time.time()),
            "ttl": repr(float(ttl)),
            "data": body,
        },
        maxlen=maxlen,
        approximate=True,
    )
    if entry_id is None:
        return Admission("inactive", job_id=job_id, detail="XADD 失败")

    res_key = redis_client.key("res", job_id)
    deadline = started + max(0.0, timeout_s)
    interval = max(0.001, poll_min_s)
    while True:
        raw = redis_client.call("get", res_key)
        if raw is not None:
            # 取走即删：结果只有一个等待方，留着只会占内存等 TTL。
            redis_client.call("delete", res_key)
            return _decode_result(job_id, raw, time.monotonic() - started)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # 尽力撤单。若已被投递给某个 worker，XDEL 只是从流里摘掉条目，
            # PEL 条目仍在，worker 干完 XACK 时会自然清掉——不会泄漏。
            redis_client.call("xdel", stream, _text(entry_id))
            return Admission(
                "timeout",
                job_id=job_id,
                detail=f"等待结果超时（{timeout_s:.0f}s）",
                waited_s=time.monotonic() - started,
            )
        time.sleep(min(interval, remaining))
        interval = min(interval * 1.6, max(poll_min_s, poll_max_s))


def _decode_result(job_id: str, raw: Any, waited_s: float) -> Admission:
    """把结果键里的 JSON 解成 Admission。解不动就算 failed，不抛。"""
    try:
        body = json.loads(_text(raw))
    except Exception as exc:
        return Admission("failed", job_id=job_id, detail=f"结果解码失败：{exc}", waited_s=waited_s)
    if not isinstance(body, dict):
        return Admission("failed", job_id=job_id, detail="结果格式非法", waited_s=waited_s)
    if body.get("ok"):
        return Admission("done", job_id=job_id, result=body.get("result"), waited_s=waited_s)
    return Admission(
        "failed",
        job_id=job_id,
        detail=str(body.get("error") or "worker 执行失败"),
        waited_s=waited_s,
    )


# ---------------------------------------------------------------------------
# 消费者
# ---------------------------------------------------------------------------

def _parse_entries(entries: Any, *, reclaimed: bool) -> list[Job]:
    """把 XREADGROUP / XAUTOCLAIM 的条目列表解成 Job。坏条目跳过并记日志。"""
    jobs: list[Job] = []
    for item in entries or ():
        try:
            entry_id, fields = item
        except Exception:
            continue
        flat = {_text(k): _text(v) for k, v in (fields or {}).items()}
        try:
            payload = json.loads(flat.get("data") or "{}")
        except Exception:
            payload = {}
        try:
            ts = float(flat.get("ts") or 0.0)
        except Exception:
            ts = 0.0
        try:
            ttl = float(flat.get("ttl") or DEFAULT_RESULT_TTL_S)
        except Exception:
            ttl = DEFAULT_RESULT_TTL_S
        jobs.append(
            Job(
                entry_id=_text(entry_id),
                job_id=flat.get("job", ""),
                payload=payload if isinstance(payload, dict) else {"value": payload},
                enqueued_at=ts,
                result_ttl_s=ttl,
                reclaimed=reclaimed,
            )
        )
    return jobs


def fetch(consumer: str, *, count: int = 1, block_ms: int = 500) -> list[Job]:
    """XREADGROUP 取新任务（``>`` 游标）。失败 / 无任务时返回空列表。"""
    if not ensure_group():
        return []
    batches = redis_client.call(
        "xreadgroup",
        GROUP,
        consumer,
        {stream_key(): ">"},
        count=max(1, int(count)),
        block=_safe_block_ms(block_ms),
    )
    jobs: list[Job] = []
    for batch in batches or ():
        try:
            _name, entries = batch
        except Exception:
            continue
        jobs.extend(_parse_entries(entries, reclaimed=False))
    return jobs


def reclaim(
    consumer: str,
    *,
    min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
    count: int = 8,
    start_id: str = "0-0",
) -> list[Job]:
    """XAUTOCLAIM：把死掉的 worker 手里的任务捡回来。

    **这是选 Streams 而不是 List 的唯一硬理由。** ``BRPOP`` 一弹出，任务就
    只存在于那个进程的内存里；进程被 kill -9 / OOM，任务连同它的等待方一起
    消失，等待方只能干等到超时。Streams 把"已投递未确认"显式建模成 PEL，
    别的副本据此接手。

    ``min_idle_ms`` 是唯一的判活依据，宁大勿小：设小于任务最坏耗时，就会
    把活人手里的任务抢走并发重做（下游被打两遍，结果还可能互相覆盖）。
    默认 5 分钟 = 查询总闸 240s 再留一倍余量。

    Redis 7 的 XAUTOCLAIM 返回 ``[游标, 条目, 已删除ID]``；这里只取一批
    （count 条），不循环追游标——每轮消费捡一点，避免单次调用被大量积压
    卡住不去处理新任务。
    """
    if not ensure_group():
        return []
    reply = redis_client.call(
        "xautoclaim",
        stream_key(),
        GROUP,
        consumer,
        max(0, int(min_idle_ms)),
        start_id=start_id,
        count=max(1, int(count)),
    )
    if not reply:
        return []
    try:
        entries = reply[1]
    except Exception:
        return []
    jobs = _parse_entries(entries, reclaimed=True)
    if jobs:
        _logger.warning(
            "XAUTOCLAIM 接管 %d 个孤儿任务（原 worker 疑似崩溃），consumer=%s",
            len(jobs), consumer,
        )
    return jobs


def complete(
    job: Job,
    result: Any = None,
    *,
    error: str | None = None,
    result_ttl_s: float | None = None,
) -> bool:
    """回写结果并 XACK + XDEL。返回是否全部成功（失败也不抛）。

    顺序是**先写结果、后确认**，不能反：若在两步之间崩溃，
    - 现在这个顺序：任务留在 PEL，被 XAUTOCLAIM 重做一遍（等待方早已拿到
      结果并删了键，重做的结果无人认领，靠 TTL 自然过期）——多算一次，但
      不丢；
    - 反过来先 XACK：任务从 PEL 消失，结果又没写成，等待方只能干等到超时
      ——丢任务。宁可重做，不可丢。

    XDEL 不是可选项：只 XACK 的话条目仍计入 XLEN，深度指标会单调上涨，
    背压逻辑（见 :func:`_counts`）会永久误判为满队列。

    TTL 默认沿用生产者随任务带过来的 ``job.result_ttl_s``——它才知道自己
    还愿意等多久；显式传参可覆盖。
    """
    ttl = int(max(1.0, job.result_ttl_s if result_ttl_s is None else result_ttl_s))
    body: dict[str, Any] = {"ok": error is None, "job": job.job_id}
    if error is None:
        body["result"] = result
    else:
        # 截断：异常信息可能夹带栈、路径、甚至下游返回的原文，
        # 它最终会流向 HTTP 响应。
        body["error"] = str(error)[:500]
    try:
        raw = json.dumps(body, ensure_ascii=False, default=str)
    except Exception as exc:
        raw = json.dumps({"ok": False, "job": job.job_id, "error": f"结果无法序列化：{exc}"})

    stream = stream_key()
    written = redis_client.call("set", redis_client.key("res", job.job_id), raw, ex=ttl)
    acked = redis_client.call("xack", stream, GROUP, job.entry_id)
    redis_client.call("xdel", stream, job.entry_id)
    return bool(written) and acked is not None


def consume_once(
    handler: Callable[[dict[str, Any]], Any],
    *,
    consumer: str | None = None,
    count: int = 1,
    block_ms: int = 500,
    min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
    result_ttl_s: float | None = None,
) -> int:
    """跑一轮消费：先捡孤儿，再取新任务。返回本轮处理的任务数。

    **先 reclaim 后 fetch** 是有意的：反过来的话，只要新任务源源不断，
    孤儿任务就永远排在后面，等待方会一直等到超时——恢复能力形同虚设。
    """
    who = consumer or consumer_name()
    if not is_active():
        return 0

    jobs = reclaim(who, min_idle_ms=min_idle_ms, count=count)
    if not jobs:
        jobs = fetch(who, count=count, block_ms=block_ms)

    done = 0
    for job in jobs:
        try:
            value = handler(job.payload)
        except Exception as exc:  # handler 的异常是业务失败，不是队列故障
            _logger.warning("任务 %s 执行失败：%s", job.job_id, exc)
            complete(job, error=f"{type(exc).__name__}: {exc}", result_ttl_s=result_ttl_s)
        else:
            complete(job, value, result_ttl_s=result_ttl_s)
        done += 1
    return done


class Worker:
    """后台线程消费循环。``start()`` / ``stop()``，异常不外泄。

    刻意做成线程而非 asyncio 任务：handler 跑的是同步 RAG 链路（阻塞在
    Milvus / LLM 的网络 I/O 上），塞进事件循环只会把它堵死。想跑多个并发
    消费者就起多个 Worker，每个用不同的 ``consumer`` 名。
    """

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Any],
        *,
        consumer: str | None = None,
        count: int = 1,
        block_ms: int = 500,
        min_idle_ms: int = DEFAULT_MIN_IDLE_MS,
        result_ttl_s: float | None = None,
        idle_sleep_s: float = 0.5,
        name: str = "rag4c-queue-worker",
    ) -> None:
        self.handler = handler
        self.consumer = consumer or consumer_name()
        self.count = count
        self.block_ms = block_ms
        self.min_idle_ms = min_idle_ms
        self.result_ttl_s = result_ttl_s
        self.idle_sleep_s = idle_sleep_s
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processed = 0

    @property
    def processed(self) -> int:
        """本 worker 累计处理的任务数。"""
        return self._processed

    def alive(self) -> bool:
        """线程是否在跑。"""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> "Worker":
        """启动后台线程（重复调用无副作用）。"""
        if self.alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """停机：置停止位并等线程退出（超时即放弃，守护线程随进程结束）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                handled = consume_once(
                    self.handler,
                    consumer=self.consumer,
                    count=self.count,
                    block_ms=self.block_ms,
                    min_idle_ms=self.min_idle_ms,
                    result_ttl_s=self.result_ttl_s,
                )
            except Exception:  # 兜底：循环本身绝不能被任何异常打断
                _logger.warning("消费循环异常（继续）", exc_info=True)
                handled = 0
            self._processed += handled
            if not handled:
                # 空转时睡一下。XREADGROUP 的 BLOCK 已经吸收了大部分空转，
                # 但 Redis 挂掉时 fetch 会立即返回空，这里必须兜住，
                # 否则会变成一个满速重连的忙循环。
                self._stop.wait(self.idle_sleep_s)


# ---------------------------------------------------------------------------
# 维护
# ---------------------------------------------------------------------------

def destroy_stream() -> bool:
    """删除消费者组与流本身。**仅供测试 / 运维手工清理。**

    生产路径永远不该调它：流里可能还躺着别的副本正在等结果的任务，删掉
    等于让那些请求全部超时。放在这里是因为冒烟测试必须能清干净自己造的
    键——但它只动本前缀下的这一个键，不是 FLUSHDB。
    """
    stream = stream_key()
    redis_client.call("xgroup_destroy", stream, GROUP)
    deleted = redis_client.call("delete", stream)
    reset()
    return deleted is not None


__all__ = [
    "GROUP",
    "DEFAULT_MIN_IDLE_MS",
    "DEFAULT_RESULT_TTL_S",
    "Admission",
    "Job",
    "Worker",
    "complete",
    "consume_once",
    "consumer_name",
    "depth",
    "destroy_stream",
    "ensure_group",
    "fetch",
    "is_active",
    "reclaim",
    "reset",
    "stats",
    "stream_key",
    "submit",
]
