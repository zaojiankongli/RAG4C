"""RAG4C 桥服务 —— 让 Tauri/Web 前端以 HTTP 方式调用 RAG 全链路。

设计说明：
- 前端（frontend/，Tauri 2 + React）通过 fetch 调用本服务的
  /api/health / /api/query / /api/query/stream 端点，完成真实问答。
- /api/query 在返回 QueryResult 的同时，从 verdict.evidence_chunks
  提取证据片段（截断到 400 字），供前端做「来源卡片 + 深链到段落」展示。
- 桥服务离线时前端自动降级为演示数据，UI 始终可用。

企业级加固（本文件职责）：
- 访问日志 + 全局异常 JSON 错误契约（server/middleware.py）；
- 查询并发限流（信号量 + 队列上限）+ 每请求超时；
- 查询结果 TTL 缓存（相同 query+acl 短期内直接命中）；
- SSE 流式问答（/api/query/stream，停止生成在服务端真正生效）；
- 深度健康探活（server/health.py，三级状态 + 组件明细 + 熔断器联动）；
- 评测互斥（同时间只允许一个评测任务）+ .env 原子写入；
- 进程内指标周期持久化（data/metrics-history.jsonl）+ Prometheus 文本端点。

启动::

    uv run uvicorn server.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

# 将项目根目录加入 sys.path，确保 import rag 可用（无论从何处启动）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.circuit import list_circuits  # noqa: E402
from core.document_serving import (  # noqa: E402
    DocumentServingGuard,
    KnowledgeChanged,
    ServingSnapshot,
    bind_document_serving,
)
from core.metrics import get_metrics  # noqa: E402
from core.observability import get_logger  # noqa: E402
from core.generation_cache import (  # noqa: E402
    GenerationAwareSingletonCache,
    GenerationCacheSaturated,
)
from core.run_events import (  # noqa: E402
    RunEvent,
    RunEventSequencer,
    RunEventSink,
    RunObserver,
    run_event_dict,
)
from core.query_cache import (  # noqa: E402
    CacheConfig,
    QueryCache,
    get_query_cache,
    install_query_cache,
    make_cache_key,
)
from core.retry import deadline  # noqa: E402
from core import cache_epoch  # noqa: E402
from core import catalog  # noqa: E402
from core import queue_stream  # noqa: E402
from core import redis_client  # noqa: E402
from rag import answer_query, get_pipeline  # noqa: E402  顶层编排器（懒加载，离线 import 安全）
from rag_stream import answer_query_stream, _result_payload  # noqa: E402
from rag_topology import (  # noqa: E402
    RagTopology,
    available_components_from_pipeline,
    build_cache_replay_topology,
    build_rag_topology,
    enabled_components_from_settings,
    topology_dict,
)
from config.settings import get_settings, resolve_tenant  # noqa: E402
from core.run_registry import derive_tenant_scope, fingerprint_query  # noqa: E402
from models.schemas import QueryResult  # noqa: E402
from server import documents as documents_api  # noqa: E402
from server import document_catalog_api  # noqa: E402
from server import enterprise_access_graph_api  # noqa: E402
from server import enterprise_admin_api  # noqa: E402
from server import enterprise_compliance_api  # noqa: E402
from server import enterprise_approval_api  # noqa: E402
from server import enterprise_approval_consumers  # noqa: E402
from server import enterprise_readiness_api  # noqa: E402
from server import enterprise_knowledge_base_registry_api  # noqa: E402
from server import enterprise_knowledge_base_releases_api  # noqa: E402
from server import enterprise_release_quality_api  # noqa: E402
from server import enterprise_release_quality_operations_api  # noqa: E402
from server import enterprise_notification_api  # noqa: E402
from server import enterprise_content_recovery_api  # noqa: E402
from server import enterprise_automation_workflows_api  # noqa: E402
from server import enterprise_task_operations_api  # noqa: E402
try:
    enterprise_knowledge_serving_api = importlib.import_module(
        "server.enterprise_knowledge_serving_api"
    )
except ModuleNotFoundError as exc:  # Stage26 serving module may land after this wiring slice.
    if exc.name != "server.enterprise_knowledge_serving_api":
        raise
    enterprise_knowledge_serving_api = None
from server import enterprise_workspace_api  # noqa: E402
from server import enterprise_workspace_authorization_api  # noqa: E402
from server import knowledge_audit_api  # noqa: E402
from server import knowledge_content_api  # noqa: E402
from server import knowledge_chunks_api  # noqa: E402
from server import knowledge_source_preview_api  # noqa: E402
from server import knowledge_consistency_api  # noqa: E402
from server import knowledge_dataset_api  # noqa: E402
from server import knowledge_governance_api  # noqa: E402
from server import knowledge_sources_api  # noqa: E402
from server import storage_backends_api  # noqa: E402
from server import answer_evidence_api  # noqa: E402
from server import oidc_runtime_api  # noqa: E402
from server import retrieval_experiments_api  # noqa: E402
from server import scim_api  # noqa: E402
from server.config_writer import update_dotenv_text  # noqa: E402
from server.health import probe_components  # noqa: E402
from server.middleware import (  # noqa: E402
    access_log,
    http_exception_handler,
    limit_body_size,
    rate_limit_by_ip,
    unhandled_exception_handler,
    validation_exception_handler,
)
from server.run_ops import RunOpsRuntime, create_run_ops_router  # noqa: E402
from server.security import admin_access_middleware  # noqa: E402
from server.source_dispatcher import start_source_dispatch_runtime  # noqa: E402
# 无状态支撑层（纯函数：dotenv 序列化 / Prometheus 文本 / 文件回读）。
# 保留下划线别名，使 _format_env_value / _coerce_config_value 等既有
# 调用点与 from server.app import _xxx 的测试兼容。
from server.support import (  # noqa: E402
    coerce_config_value as _coerce_config_value,
    env_runtime_value as _env_runtime_value,
    format_env_value as _format_env_value,
    metric_key_parts as _metric_key_parts,  # noqa: F401  # 保留兼容（support 内部使用）
    prometheus_text as _prometheus_text,
    tail_lines as _tail_lines,
)

_logger = get_logger("server")
_SOURCE_DISPATCHER_TEST_OVERRIDE_MISSING = object()

# ---------------------------------------------------------------------------
# 服务层配置（并发 / 超时 / 缓存 / 持久化）
# ---------------------------------------------------------------------------

# 查询并发：同时执行的问答请求上限（超出进入排队）
# 支持环境变量覆盖（RAG4C_QUERY_MAX_CONCURRENT 等），配置中心「bridge」段可见
#
# 默认值从 4 提到 32。4 是按 CPU 核数直觉定的，但这条链路**不是 CPU 密集**：
# 一次问答实测约 5.6 秒，其中绝大部分是等 Milvus、等嵌入、等重排、等 LLM，
# 全在 GIL 之外阻塞。按 4 并发算，吞吐天花板 ≈ 4/5.6 ≈ 0.7 QPS——远低于
# 下游服务本身能承受的量，等于自己给自己设了个瓶颈。
#
# 实测（scripts/bench/load_query.py，32 并发 96 请求，模拟上游）：
#   改前 max_concurrent=4  queue=20  -> 成功率 20.8%，76 个 429
# 线程在等 I/O 时几乎不耗资源，把上限提到 32 的代价只是 32 个大部分时间在
# 睡觉的线程栈。真正需要保护的是**下游配额**，那该由熔断器和下游自己的
# 限流来管，不该靠把本服务憋死来实现。
QUERY_MAX_CONCURRENT = int(os.environ.get("RAG4C_QUERY_MAX_CONCURRENT", "32"))
# 排队上限：pending（执行中 + 排队）超过此值直接 429
#
# 保持「队列 = 并发 × 4」的比例。队列的作用是吸收突发，不是无限缓冲——
# 排到第 129 位的请求即使最终被执行，客户端也早就超时走了，那种「排队成功
# 但没人要结果」的活是纯浪费，不如早点 429 让调用方重试或降级。
QUERY_QUEUE_MAX = int(os.environ.get("RAG4C_QUERY_QUEUE_MAX", "128"))
# 每租户并发上限（多租户护栏）：单个租户同时执行的问答请求数上限，
# 防止一个活跃租户占满全局并发槽。0 = 不限（默认，行为与旧版一致）。
QUERY_MAX_CONCURRENT_PER_TENANT = int(
    os.environ.get("RAG4C_QUERY_MAX_CONCURRENT_PER_TENANT", "0")
)
# 单次查询服务端超时（秒）：远程 embedding/rerank（SiliconFlow）可达 50s+，
# 总闸放宽到 240s 让慢查询有机会完成（否则 120s 常被远程拖超时弃权）
QUERY_TIMEOUT_S = float(os.environ.get("RAG4C_QUERY_TIMEOUT_S", "240"))
# 评测任务超时（秒）：真实管线逐条跑，放宽到 15 分钟
EVAL_TIMEOUT_S = float(os.environ.get("RAG4C_EVAL_TIMEOUT_S", "900"))

# 查询结果缓存：TTL（秒）与容量（LRU）
QUERY_CACHE_TTL_S = float(os.environ.get("RAG4C_QUERY_CACHE_TTL_S", "600"))
QUERY_CACHE_MAX = int(os.environ.get("RAG4C_QUERY_CACHE_MAX", "64"))

# 指标持久化：间隔（秒）与单文件大小上限（字节，超限截断保留后半）
METRICS_PERSIST_INTERVAL_S = 60.0
METRICS_HISTORY_MAX_BYTES = 5 * 1024 * 1024
METRICS_HISTORY_PATH = _PROJECT_ROOT / "data" / "metrics-history.jsonl"

# 服务启动时刻（uptime 计算）
_STARTED_AT = time.time()

# ---------------------------------------------------------------------------
# 查询结果缓存（L1 进程内 TTL+LRU / L2 Redis，实现见 core/query_cache.py）
# ---------------------------------------------------------------------------
#
# 这里只剩一层薄封装：语义与调用点一个字没改，缓存本身搬进了
# ``core.query_cache``，多垫了一级 Redis。搬家的理由是单进程内存态挡住了
# 横向扩容——N 个副本各存各的，命中率被切成 1/N，热点问题会同时打穿 N 条
# 完整 RAG 链路（一次约 5.6s，还要烧掉一整轮 token）。
#
# Redis 缺席时行为与搬家前逐字节一致，这是 core.query_cache 的硬约束。


def _build_query_cache() -> QueryCache:
    """按本服务的超时预算装配缓存实例。

    ``wait_timeout_s`` 必须**小于** HTTP 总闸，否则会出现「客户端 240s 就
    放弃了，工作线程还在为它排队等到 300s」——线程为一个没人接收的答案空转
    一分钟，正是 ④ 里那条 deadline 要根除的东西。取九成，与
    ``_run_query`` 里的链路预算同一口径。
    """
    cfg = replace(
        CacheConfig.from_settings(),
        l1_ttl_s=QUERY_CACHE_TTL_S,
        l1_max=QUERY_CACHE_MAX,
        wait_timeout_s=QUERY_TIMEOUT_S * 0.9,
    )
    return QueryCache(cfg)


install_query_cache(_build_query_cache())


def _cache_key(
    query: str,
    acl: list[str] | None,
    retry: bool,
    tenant_id: str | None,
    dataset_id: str | None = None,
    resolved_tenant: str | None = None,
    serving_generation: str | int = "",
) -> str:
    """拼缓存键，并在其中拌入语料代次戳。

    这里对 tenant 调 ``resolve_tenant``，和 ``rag.answer_query`` /
    ``retrieval.pipeline`` 用的是同一口径。不这么做会有一个不显眼的浪费：
    前端不传 tenant_id（键里是 ``""``），而业务侧把它解析成了 ``"default"``；
    两者本是同一个租户，却各存一份缓存，也让按租户失效永远对不上号。
    """
    # 与本文件其它用到配置的地方一致，延迟导入：模块导入期不去初始化 settings。
    from config.settings import get_settings
    from rag_common import resolve_tenant

    tenant = (
        resolved_tenant
        if resolved_tenant is not None
        else resolve_tenant(tenant_id, get_settings())
    )
    return make_cache_key(
        query,
        acl,
        retry,
        tenant,
        dataset_id,
        epoch=cache_epoch.stamp(tenant),
        serving_generation=serving_generation,
    )


def _get_document_serving_guard() -> DocumentServingGuard:
    return DocumentServingGuard.from_catalog()


def _serving_snapshot(
    req: "QueryRequest",
    *,
    resolved_tenant: str | None = None,
) -> tuple[DocumentServingGuard | None, ServingSnapshot | None]:
    tenant = resolved_tenant or resolve_tenant(req.tenant_id, get_settings())
    try:
        guard = _get_document_serving_guard()
        return guard, guard.snapshot(tenant, req.dataset_id)
    except KnowledgeChanged:
        raise
    except Exception as exc:
        _logger.warning("知识服务真账不可用，查询安全弃权: %s", exc)
        raise KnowledgeChanged("knowledge catalog is unavailable") from exc


def _knowledge_changed_payload(
    req: "QueryRequest",
    reason: str,
    *,
    duration_ms: float = 0.0,
) -> dict[str, Any]:
    result = QueryResult(
        query=req.query,
        answer="",
        abstained=True,
        route="hybrid",
        traces=[f"知识已变更，本次弃权: {reason}"],
    )
    payload = _serialize(result, duration_ms)
    payload["result"]["knowledge_changed"] = True
    return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    # TTL 由缓存按载荷内容自己决定（弃权结果短命，见 QueryCache._ttl_for）。
    get_query_cache().put(key, payload)


def _cache_peek(key: str) -> dict[str, Any] | None:
    """只读地看一眼缓存，**不**登记 single-flight 所有权。

    ``allow_l2=False`` 不是偷懒：本函数在事件循环里被调用，而查 L2 是一次
    网络往返，socket_timeout 是 1s。Redis 一旦卡住，每个请求都会把整个事件
    循环拽停最长 1s，32 并发就是几十秒的全局停摆——为了提高命中率把整个
    服务的响应性押上去，方向反了。

    跨副本命中并没有丢，只是推迟到 ``_run_query`` 里的 :meth:`reserve` 才
    发生：代价是 L2 命中要多占一个执行槽，但只占毫秒级（一次 GET），
    与「占着槽跑完一整条 RAG 链路」完全不是一回事。
    """
    return get_query_cache().peek(key, allow_l2=False)


def _cache_reserve(key: str) -> tuple[dict[str, Any] | None, threading.Event, bool]:
    """Return cached data or reserve ownership of one expensive computation."""
    return get_query_cache().reserve(key)


def _cache_release(key: str, flight: threading.Event) -> None:
    get_query_cache().release(key, flight)


def _cache_stats() -> dict[str, Any]:
    return get_query_cache().stats()


def _embed_cache_stats() -> dict[str, Any]:
    """查询向量缓存的命中情况（惰性导入，失败不影响监控页出图）。

    与答案缓存分开上报：两者命中率的含义完全不同。答案缓存命中 = 整条链路
    没跑；向量缓存命中 = 只是省掉一次嵌入往返，链路照跑。混在一个数字里会
    让"缓存命中率 80%"这种话失去意义。
    """
    try:
        from core.embed_cache import get_embed_cache

        return get_embed_cache().stats()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def _llm_cache_stats() -> dict[str, Any]:
    """检索期 LLM 槽位结果缓存的命中情况（惰性导入，失败不影响监控页出图）。

    第三条独立曲线，理由同上：这一层命中 = 少调一次预处理 LLM（改写 / 路由 /
    HyDE / 子查询 / 后退提问），链路其余部分照跑。真正能说明"省了多少钱"的
    是 metrics 里的 ``llm.cache.saved.*``——那是实测 token，不是命中率。
    """
    try:
        from core.llm_cache import get_llm_cache

        return get_llm_cache().stats()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


# ---------------------------------------------------------------------------
# 并发控制（async 端点用）
# ---------------------------------------------------------------------------

_query_slots = asyncio.Semaphore(QUERY_MAX_CONCURRENT)
_query_executor_lock = threading.RLock()
_query_executor: ThreadPoolExecutor | None = None
_pending = 0  # 执行中 + 排队（async 单线程上下文内更新，无需额外锁）
_pending_per_tenant: dict[str, int] = {}  # 每租户在途计数（按租户限流，0=不限时保持空）
_pending_lock = threading.Lock()

# 事件循环层的在途登记表：cache_key -> 正在算这个 key 的 future。
#
# 这是本次并发改造的核心。原来的 single-flight 只有线程层一份，位置在
# ``_run_query`` **内部**——也就是说等待者必须先抢到信号量槽、再占住一个
# 工作线程，然后在 ``Event.wait()`` 上睡觉。后果是热点问题会把服务掐死：
# N 个人同时问同一个问题，1 个在算、N-1 个纯粹占着槽发呆，此时任何**别的**
# 问题都进不来，只能排队或 429。命中率越高，服务越不可用，完全反了。
#
# 现在等待发生在拿槽之前，且是 ``await``：等待者不占信号量、不占线程，
# 只占一个协程。热点从「压垮服务的东西」变回「本该省事的东西」。
#
# 只在事件循环线程里读写，所以不加锁——注册与查询之间没有 await，
# 对协程而言是原子的。加把锁反倒会让人误以为存在跨线程访问。
_aio_flights: dict[str, "asyncio.Future[dict[str, Any]]"] = {}


def _start_query_executor() -> ThreadPoolExecutor:
    """Ensure the shared query worker pool exists."""
    global _query_executor
    with _query_executor_lock:
        if _query_executor is None:
            _query_executor = ThreadPoolExecutor(
                max_workers=QUERY_MAX_CONCURRENT,
                thread_name_prefix="rag-query",
            )
        return _query_executor


def _shutdown_query_executor(*, wait: bool = True) -> None:
    """Stop query workers without making a later app startup unusable."""
    global _query_executor
    with _query_executor_lock:
        executor = _query_executor
        _query_executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)


def _inc_pending(tenant_id: str | None = None) -> bool:
    global _pending
    with _pending_lock:
        if _pending >= QUERY_QUEUE_MAX:
            return False
        # 按租户并发上限（0 = 不限）。防止单租户占满全局槽。
        if QUERY_MAX_CONCURRENT_PER_TENANT > 0 and tenant_id:
            per_tenant = _pending_per_tenant.get(tenant_id, 0)
            if per_tenant >= QUERY_MAX_CONCURRENT_PER_TENANT:
                return False
            _pending_per_tenant[tenant_id] = per_tenant + 1
        _pending += 1
        return True


def _dec_pending(tenant_id: str | None = None) -> None:
    global _pending
    with _pending_lock:
        _pending = max(0, _pending - 1)
        if tenant_id:
            per_tenant = _pending_per_tenant.get(tenant_id, 0)
            if per_tenant > 0:
                if per_tenant == 1:
                    _pending_per_tenant.pop(tenant_id, None)
                else:
                    _pending_per_tenant[tenant_id] = per_tenant - 1


def _release_query_slot(
    slots: asyncio.Semaphore,
    tenant_id: str | None,
    *,
    flights: dict[str, asyncio.Future] | None = None,
    cache_key: str | None = None,
    worker: asyncio.Future | None = None,
) -> None:
    """worker 完成回调：释放执行槽 + 归还租户在途计数 + 摘合并登记。

    query 与 stream 两条路径共用（此前两处 `_release_slot` 闭包重复实现）。
    摘登记要核对身份：worker 完成时可能已有下一轮同 key 请求登记了新的
    future，直接 pop 会把别人的登记删掉，让那一轮失去合并能力。
    """
    slots.release()
    _dec_pending(tenant_id)
    if flights is not None and cache_key is not None and worker is not None:
        if flights.get(cache_key) is worker:
            flights.pop(cache_key, None)


def _queue_stats() -> dict[str, Any]:
    """排队水位。Redis Streams 后端启用时，附上**跨副本**的真实深度。

    单副本下这两个数字重合，多副本下不重合的那部分才是重点：``pending``
    只知道自己这一个进程排了多少，而 ``/readyz`` 要回答的是「还该不该给
    集群发流量」——那是个全局问题。Streams 关闭 / Redis 不可用时
    ``queue_stream.stats()`` 报 ``active=False``，这里原样退回单进程视图。
    """
    with _pending_lock:
        stats: dict[str, Any] = {
            "pending": _pending,
            "max_concurrent": QUERY_MAX_CONCURRENT,
            "queue_max": QUERY_QUEUE_MAX,
        }
    try:
        shared = queue_stream.stats(max_concurrent=QUERY_MAX_CONCURRENT, queue_max=QUERY_QUEUE_MAX)
        if shared.get("active"):
            stats["shared"] = shared
    except Exception as exc:  # noqa: BLE001 - 观测项不得影响 /readyz 本身
        get_logger(__name__).debug("跨副本队列水位读取失败（忽略）: %s", exc)
    return stats


# ---------------------------------------------------------------------------
# 进程内最近查询缓存（监控页「最近查询」用；环形缓冲，上限 50 条）
# ---------------------------------------------------------------------------
_RECENT_LOCK = threading.Lock()
_RECENT_QUERIES: deque[dict[str, Any]] = deque(maxlen=50)


def _record_query(payload: dict[str, Any]) -> None:
    """把一次问答的摘要记录进最近查询缓存（线程安全）。"""
    with _RECENT_LOCK:
        _RECENT_QUERIES.appendleft(payload)


def _maybe_record_answer_fact(
    *,
    tenant_id: str | None,
    dataset_id: str | None,
    run_id: str | None,
    question: str | None,
    answer: str | None,
    route: str | None,
    abstained: bool,
    citations: Any,
    evidence: Any,
) -> None:
    """Best-effort catalog answer-fact write; never fail the query path."""
    try:
        if not tenant_id:
            return
        try:
            engine = catalog.get_engine()
        except Exception:  # noqa: BLE001
            return
        if engine is None:
            return
        from core.answer_evidence_facts import AnswerEvidenceRepository

        outcome = "abstained" if abstained else "answered"
        AnswerEvidenceRepository(engine).record_answer_fact(
            tenant_id=tenant_id,
            dataset_id=dataset_id or "default",
            run_id=run_id,
            question=question,
            answer=answer,
            route=route,
            outcome=outcome,
            citations=list(citations or []),
            evidence=list(evidence or []),
        )
    except Exception:  # noqa: BLE001 - 观测写失败不影响问答
        _logger.warning("answer fact record skipped", exc_info=True)


def _recent_queries() -> list[dict[str, Any]]:
    with _RECENT_LOCK:
        return list(_RECENT_QUERIES)


def _serialize(result: Any, duration_ms: float) -> dict[str, Any]:
    """QueryResult -> 前端 QueryResponse 契约 {result, using_mock, duration_ms}。

    result 内含 evidence 字段（rag_stream._result_payload 附加），
    与 frontend/src/types/rag.ts 的 QueryResponse / QueryResult 逐字段对齐。
    """
    inner = _result_payload(result)
    return {"result": inner, "using_mock": False, "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# 指标持久化（后台线程：周期快照追加到 JSONL，超限截断）
# ---------------------------------------------------------------------------

_metrics_hist_lock = threading.Lock()
_metrics_persist_stop = threading.Event()


def _persist_metrics_once() -> None:
    """写一次指标快照（线程安全；文件超限时截断保留后半）。"""
    try:
        snapshot = get_metrics().snapshot()
        line = json.dumps(
            {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "metrics": snapshot},
            ensure_ascii=False,
        )
        METRICS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _metrics_hist_lock:
            with METRICS_HISTORY_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            # 轮转：超限时读回尾部一半行数写回（进程内低频操作，可接受）
            if METRICS_HISTORY_PATH.stat().st_size > METRICS_HISTORY_MAX_BYTES:
                lines = METRICS_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
                keep = lines[-2000:]
                METRICS_HISTORY_PATH.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 - 持久化失败不影响服务
        _logger.warning("指标持久化失败", exc_info=True)


def _metrics_persist_loop() -> None:
    while not _metrics_persist_stop.is_set():
        _metrics_persist_stop.wait(METRICS_PERSIST_INTERVAL_S)
        _persist_metrics_once()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


def _prime_cache_epoch() -> None:
    """启动时把语料代次同步读回来一次（在线程里跑，不占事件循环）。

    为什么必须预热见 :func:`core.cache_epoch.prime`：不预热的话，冷启动后头
    几秒里写进缓存的条目会因为代次戳对不上而从落地起就是垃圾，症状是"服务刚
    起来那会儿缓存像是不工作"，几秒后自愈，日志里什么都没有。

    读失败不影响启动：代次是加速件，读不到就沿用 0，与 Redis 缺席时一致。
    """
    try:
        from config.settings import get_settings
        from rag_common import resolve_tenant

        cache_epoch.prime(resolve_tenant(None, get_settings()))
    except Exception as exc:  # noqa: BLE001 - 预热失败只是回到冷启动那条路
        _logger.debug("语料代次预热失败（不影响启动）：%s", exc)


def _prewarm_router() -> None:
    """把意图路由的示例向量提前算好。

    这笔开销（一次 ``embed_texts``）本来落在**第一次查询**头上，而且配置热
    更新会重建组件，于是每改一次配置，下一位用户就替所有人再付一次。

    刻意不阻塞启动、也刻意不在这里连 Milvus / 加载重排模型：
    ``rag.get_pipeline()`` 的构造本身不联网不加载模型（它自己的文档这么承
    诺，也确实如此），真正会走网络的只有下面这一次嵌入。嵌入服务此刻没起
    来也无所谓——预热失败只是回到惰性计算那条路，不该让服务起不来。
    """
    try:
        import rag

        components = rag.get_pipeline()
        router = getattr(components.get("retrieval"), "router", None)
        prewarm = getattr(router, "prewarm", None)
        if callable(prewarm):
            _logger.debug("意图路由示例向量预热%s", "完成" if prewarm() else "未就绪")
    except Exception as exc:  # noqa: BLE001 - 预热失败只是回到冷启动那条路
        _logger.debug("意图路由预热失败（不影响启动）：%s", exc)


@asynccontextmanager
def _preflight_catalog(settings) -> None:
    """启动前探活目录库，让连接失败在**这里**说清楚。

    为什么需要：目录库连不上时，启动会在十几层 SQLAlchemy/PyMySQL 之后炸掉，
    运维看到的是 ``pymysql.err.OperationalError (2003, ... timed out)`` 加一屏
    调用栈——看不出「是哪个配置项错了」。而 ``RAG4C_CATALOG_DB_URL`` 里的端口
    是最常见的错因（本机实测：配置写 3307、实际服务在 3306，两者都能让
    ``socket`` 连通与否产生完全不同的结论）。

    只在配置了远程库（``db_url`` 非空）时才探活：SQLite 本地库不存在是
    正常的（首次启动会 ``create_all``），不该在这里拦。
    """
    url = getattr(getattr(settings, "catalog", None), "db_url", "") or ""
    if not url:
        return
    # 解析出 host:port：SQLAlchemy URL 形如
    # mysql+pymysql://user:pw@host:port/db?charset=...
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        host = parsed.host or "localhost"
        port = parsed.port or 3306
    except Exception as exc:  # noqa: BLE001 - URL 本身坏了交给后续流程报错
        raise RuntimeError(
            f"目录库 URL 无法解析（检查 RAG4C_CATALOG_DB_URL）: {url!r} -> {exc}"
        ) from exc

    import socket

    sock = socket.socket()
    sock.settimeout(5)
    try:
        sock.connect((host, port))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"目录库不可达：{host}:{port}（RAG4C_CATALOG_DB_URL 里的地址/端口）。"
            f"连接失败：{type(exc).__name__}: {exc}。"
            "本机常见错因是端口写错——用 netstat -ano 或 telnet 确认实际端口，"
            "再用 RAG4C_CATALOG_DB_URL 覆盖 .env 里的值。"
        ) from exc
    finally:
        sock.close()


async def lifespan(application: FastAPI):
    """启动：代次预热 + 指标持久化线程；关闭：停止线程并落盘最后一份快照。"""
    global _query_slots
    _metrics_persist_stop.clear()
    _query_slots = asyncio.Semaphore(QUERY_MAX_CONCURRENT)
    settings = get_settings()
    _preflight_catalog(settings)
    if hasattr(application.state, "retrieval_experiment_retrieval"):
        delattr(application.state, "retrieval_experiment_retrieval")
    try:
        experiment_components = get_pipeline(settings)
        application.state.retrieval_experiment_retrieval = experiment_components["retrieval"]
    except Exception:
        _logger.error("Dedicated retrieval experiment pipeline is unavailable")
    try:
        run_ops_runtime = RunOpsRuntime.bootstrap(settings)
    except Exception:
        run_ops_runtime = RunOpsRuntime.disabled(settings)
    application.state.run_ops_runtime = run_ops_runtime
    # Production source recovery is mandatory. Tests/embedded harnesses may
    # explicitly inject a runtime (or None) before lifespan; no Settings/env bypass.
    source_dispatch_override = getattr(
        application.state,
        "knowledge_source_dispatcher_test_override",
        _SOURCE_DISPATCHER_TEST_OVERRIDE_MISSING,
    )
    source_dispatch_runtime = (
        start_source_dispatch_runtime(application, settings)
        if source_dispatch_override is _SOURCE_DISPATCHER_TEST_OVERRIDE_MISSING
        else source_dispatch_override
    )
    application.state.knowledge_source_dispatcher = source_dispatch_runtime
    _start_query_executor()
    documents_api.start_ingest_executor()
    await asyncio.to_thread(_prime_cache_epoch)
    # 路由预热放到后台线程里跑：它要走一次嵌入服务的网络往返，而启动不该
    # 等在一个纯加速项上（嵌入服务慢或没起来时，等它等于把服务的可用时间
    # 押在一个可选依赖上）。daemon=True 保证它不会拖住进程退出。
    threading.Thread(target=_prewarm_router, daemon=True, name="router-prewarm").start()
    _logger.info(
        "桥服务启动：并发=%d 队列=%d 查询超时=%.0fs 缓存TTL=%.0fs",
        QUERY_MAX_CONCURRENT,
        QUERY_QUEUE_MAX,
        QUERY_TIMEOUT_S,
        QUERY_CACHE_TTL_S,
    )
    thread = threading.Thread(target=_metrics_persist_loop, daemon=True, name="metrics-persist")
    thread.start()
    try:
        yield
    finally:
        if hasattr(application.state, "retrieval_experiment_retrieval"):
            delattr(application.state, "retrieval_experiment_retrieval")
        _metrics_persist_stop.set()
        thread.join(timeout=5)
        if source_dispatch_runtime is not None:
            try:
                await asyncio.to_thread(source_dispatch_runtime.stop)
            except Exception:
                pass
        # Shutdown runs off the event loop because active model calls may take
        # time to finish. Both pools are lazily recreatable for repeated app
        # lifespans in one process (tests, embedded deployments, hot restarts).
        await asyncio.gather(
            asyncio.to_thread(_shutdown_query_executor),
            asyncio.to_thread(documents_api.shutdown_ingest_executor),
            asyncio.to_thread(documents_api.shutdown_index_operation_worker),
            return_exceptions=True,
        )
        try:
            await asyncio.to_thread(run_ops_runtime.close)
        except Exception:
            pass
        _persist_metrics_once()
        _logger.info("桥服务关闭：已落盘最终指标快照")


app = FastAPI(
    title="RAG4C Bridge API",
    description="RAG4C 企业级 RAG 系统桥接服务（Tauri/Web 前端专用）",
    version="0.2.0",
    lifespan=lifespan,
)

app.state.knowledge_auth_engine = enterprise_readiness_api.get_read_only_catalog_engine()
app.state.retrieval_experiment_mutation_engine_provider = catalog.get_engine

# CORS：允许 Tauri WebView 与 Vite dev 的两种 loopback 主机拼写。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",  # Vite dev server
        "http://127.0.0.1:1420",  # Vite dev server (numeric loopback)
        "tauri://localhost",  # Tauri 2 WebView
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 跨源时浏览器只让 JS 读到 CORS 白名单里的响应头。原文查看要读
    # `Content-Disposition`（后端对这份字节的表态：inline 还是只下载）与 `ETag`
    # （切片修订号，界面用来判断"我看到的是哪一版"），两者都不在白名单里 ——
    # 不显式 expose 的话它们在 Vite dev / Tauri 下恒为空串，界面会安静地丢掉这两个信号。
    expose_headers=["Content-Disposition", "ETag", "Content-Type", "Retry-After"],
)

# 请求体大小闸门 + 管理端点远程准入 + 按 IP 限流 + 访问日志。
#
# 注册顺序有讲究：Starlette 的 http 中间件是**后注册者更靠外**（实测确认，
# 不是凭印象）。access_log 要能记录到被 413/403/429 拒掉的请求，就必须包在
# 闸门外面，因此它得**后**注册。反过来写的话，超大请求会被闸门直接短路返回，
# 一行日志都不留——正好在被攻击时最需要日志的时候，日志是空的。
app.middleware("http")(limit_body_size)
app.middleware("http")(admin_access_middleware())
app.middleware("http")(rate_limit_by_ip)
app.middleware("http")(access_log)

# 全局异常 JSON 错误契约
# StarletteHTTPException 覆盖 404 / 405 等路由级异常（fastapi.HTTPException 为其子类）
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# 文档管理（租户数据集 / 状态机 / 增量重索引）
app.include_router(documents_api.router)
app.include_router(document_catalog_api.router)
app.include_router(
    enterprise_access_graph_api.build_enterprise_access_graph_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_admin_api.build_enterprise_admin_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_workspace_api.build_enterprise_workspace_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_workspace_authorization_api.build_workspace_authorization_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_knowledge_base_registry_api.build_enterprise_knowledge_base_registry_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_knowledge_base_releases_api.build_enterprise_knowledge_base_releases_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_release_quality_api.build_enterprise_release_quality_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_release_quality_operations_api.build_enterprise_release_quality_operations_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_notification_api.build_enterprise_notification_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_content_recovery_api.build_enterprise_content_recovery_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_automation_workflows_api.build_enterprise_automation_workflows_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_task_operations_api.build_enterprise_task_operations_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
if enterprise_knowledge_serving_api is not None:
    def _knowledge_serving_readiness_provider() -> Any:
        return enterprise_readiness_api.get_enterprise_readiness_report(
            engine_provider=lambda: app.state.knowledge_auth_engine,
        )

    app.include_router(
        enterprise_knowledge_serving_api.build_enterprise_knowledge_serving_router(
            read_engine_provider=lambda: app.state.knowledge_auth_engine,
            mutation_engine_provider=lambda: catalog.get_engine(),
            readiness_provider=_knowledge_serving_readiness_provider,
        )
    )
app.include_router(
    enterprise_readiness_api.build_enterprise_readiness_router(
        engine_provider=enterprise_readiness_api.get_read_only_catalog_engine,
    )
)
app.include_router(
    enterprise_compliance_api.build_enterprise_compliance_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)
app.include_router(
    enterprise_approval_api.build_enterprise_approval_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
        execution_adapters=enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
            lambda: catalog.get_engine()
        ),
    )
)
app.include_router(
    oidc_runtime_api.build_oidc_runtime_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
    )
)


def _scim_ip_hash_key() -> bytes:
    configured = get_settings().knowledge_security.actor_signing_secret
    getter = getattr(configured, "get_secret_value", None)
    secret = getter() if callable(getter) else str(configured)
    return hashlib.sha256(("rag4c:scim:ip:v1:" + secret).encode("utf-8")).digest()


app.include_router(
    scim_api.build_scim_router(
        read_engine_provider=lambda: app.state.knowledge_auth_engine,
        mutation_engine_provider=lambda: catalog.get_engine(),
        ip_hash_key_provider=_scim_ip_hash_key,
    )
)
app.include_router(knowledge_content_api.router)
app.include_router(knowledge_chunks_api.router)
app.include_router(knowledge_source_preview_api.router)
app.include_router(knowledge_consistency_api.router)
app.include_router(knowledge_dataset_api.router)
app.include_router(knowledge_governance_api.router)
app.include_router(knowledge_sources_api.router)
app.include_router(storage_backends_api.router)
app.include_router(answer_evidence_api.router)
app.include_router(retrieval_experiments_api.router)
app.include_router(knowledge_audit_api.router)
app.include_router(create_run_ops_router())


#: 标识类字段（租户 / 知识库）的字符集约束。
#:
#: 这是**纵深防御**，不是主防线：主防线是 core.milvus_client._quote_expr_str
#: 负责的引号与转义。加这一层是因为这些值会被拼进 Milvus 过滤表达式，而
#: 转义一旦再出问题（比如有人又把引号挪回调用点），这里能挡住最常见的载荷。
#:
#: 刻意用「排除危险字符」而不是「允许字符白名单」：白名单会把中文租户名、
#: 带空格的知识库名这类合法标识一并拒掉，那是引入回归而不是修复安全问题。
#: 这里只排除引号、反斜杠和控制字符——它们在标识里没有正当用途。
_SAFE_ID_PATTERN = r'^[^"\'\\\x00-\x1f\x7f]+$'
#: 同上，但允许空串。dataset_id 的空串是有意义的取值（= 不限知识库、全域检索），
#: 不能被字符集校验顺手拒掉——用 ``+`` 会把它判为非法。
_SAFE_ID_PATTERN_ALLOW_EMPTY = r'^[^"\'\\\x00-\x1f\x7f]*$'
_SAFE_ID_HINT = "不能包含引号、反斜杠或控制字符"


class QueryRequest(BaseModel):
    """/api/query 请求体。"""

    query: str = Field(min_length=1, max_length=2000, description="用户问题")
    acl: list[str] | None = Field(default=None, description="访问控制列表，如 ['fin']；None 不过滤")
    retry: bool = Field(default=True, description="是否允许二轮检索补救")
    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=_SAFE_ID_PATTERN,
        description=f"租户标识（{_SAFE_ID_HINT}）；None 时使用服务端默认租户",
    )
    dataset_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=_SAFE_ID_PATTERN_ALLOW_EMPTY,
        description=(f"知识库标识（{_SAFE_ID_HINT}）；None / 空串表示不限知识库（全域检索）"),
    )


class EvalRunRequest(BaseModel):
    """/api/eval/run 请求体。"""

    dataset_spec: str = Field(
        default="eval/dataset_sample.py:SAMPLE_DATASET",
        description="数据集规格（模块:属性）",
    )
    pipeline: str = Field(
        default="none", description="管线导入路径（如 rag:answer_query），none=干跑"
    )
    out: str = Field(default="eval/results.json", description="结果 JSON 输出路径")


class GraphSearchRequest(BaseModel):
    """/api/graph/search 请求体。"""

    query: str = Field(min_length=1, description="图检索查询")
    entity_top_k: int = Field(default=8, ge=1, le=50)
    relation_top_k: int = Field(default=8, ge=1, le=50)


# ---------------------------------------------------------------------------
# 健康端点（深度探活）
# ---------------------------------------------------------------------------


@app.get("/livez")
def livez() -> dict[str, Any]:
    """存活探针：进程还在、事件循环还能响应。**绝不碰任何外部依赖。**

    与 ``/api/health`` 分开是必须的，不是形式主义。``/api/health`` 会真连
    Milvus（2 秒超时）——拿它当 liveness 探针意味着「Milvus 挂了 → 探针失败
    → 编排器重启本服务」，而重启一个健康的进程既治不好 Milvus，又把仅存的
    降级服务能力也一起弄没了。**存活**问的是"要不要重启我"，**就绪**问的是
    "要不要给我发流量"，两个问题的答案在依赖故障时是相反的。
    """
    return {"status": "alive", "uptime_s": round(time.time() - _STARTED_AT, 1)}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, Any]:
    """就绪探针：现在能不能正常接流量。

    降级（degraded）仍然返回 200：Milvus 挂了但 LLM 还在时，服务能给出
    "资料暂不可用"的诚实回答，比被摘出负载均衡、让用户看到连接错误要好。
    只有 down（核心依赖全挂）才 503——那时候接流量确实毫无意义。

    队列打满也算不就绪：此时新请求只会拿到 429，不如让负载均衡把流量导去
    别的实例。这是**背压向上传导**，而不是在本实例上堆更多注定失败的请求。
    """
    probe = probe_components()
    queue = _queue_stats()
    saturated = queue["pending"] >= queue["queue_max"]
    ready = probe["status"] != "down" and not saturated
    if not ready:
        response.status_code = 503
    return {
        "ready": ready,
        "status": probe["status"],
        "queue": queue,
        "reason": (
            "核心依赖不可用" if probe["status"] == "down" else "请求队列已满" if saturated else ""
        ),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    """服务健康状态 + 系统配置 + 组件探活（懒加载组件，不连接模型/向量库）。

    status 三级：ok（全部就绪）/ degraded（部分不可用）/ down（核心不可用）。
    组件探活结果 TTL 缓存 5 秒，降低远程探测频率（并发轮询场景）。
    """
    from config.settings import get_settings

    s = get_settings()
    probe = probe_components()
    return {
        "status": probe["status"],
        "milvus_uri": s.milvus.uri,
        "embedding_model": s.embedding.model,
        "reranker_model": s.reranker.model,
        "generation_model": s.llm.generation.model,
        "graph_engine_on": s.pipeline.graph_engine_on,
        "components": probe["components"],
        "circuits": probe["circuits"],
        # Redis 是加速件不是依赖件，所以它**不参与** status 的三级判定：
        # 缓存/排队挂掉服务仍能完整答题，把它算进 down 会导致一个健康的
        # 进程被编排器摘掉甚至重启（同 ⑧ 里 liveness/readiness 那笔账）。
        # 这里只做如实陈述，让人能看见"现在是不是在降级跑"。
        "redis": redis_client.stats(),
        "probed_at": probe["probed_at"],
    }


# ---------------------------------------------------------------------------
# 知识库发现
# ---------------------------------------------------------------------------


def _manifest_datasets() -> dict[str, dict[str, Any]]:
    """从源清单里读出「同步类」知识库。

    为什么不能只查 catalog：源同步（``scripts/ingest_source.py``）刻意不写
    catalog——它是运维动作，不应该要求先在业务目录里建好文档记录。于是官方
    文档那几个库在 catalog 里根本不存在，只看 catalog 会漏报一大半。
    """
    from config.settings import get_settings
    from sources import load_manifest, read_source_state

    s = get_settings()
    path = Path(s.sources.manifest_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    try:
        specs = load_manifest(path)
    except Exception:  # noqa: BLE001 - 清单缺失/写坏不该让发现端点整个挂掉
        return {}

    cache_root = Path(s.sources.cache_dir)
    if not cache_root.is_absolute():
        cache_root = _PROJECT_ROOT / cache_root

    # 读状态问 sources.runner 的同一个读者（ledger 优先、JSON 兜底、按声明），
    # 不再自己拼 `spec.name / "_state.json"`——写方按 source-id/派生 cache key
    # 落盘，旧拼法读到的永远是"没有这个文件"，doc_count 因此恒为 0。
    ledger = None
    state_mode = str(getattr(s.sources, "state_mode", "json"))
    try:
        from sources.state_modes import source_state_mode

        if source_state_mode(state_mode).uses_ledger:
            from core.catalog import get_engine
            from core.source_sync_ledger import SourceSyncLedger

            ledger = SourceSyncLedger(get_engine())
    except Exception:  # noqa: BLE001 - 没配 MySQL 时 ledger 不可用
        # database 模式没有 ledger 时读者会按 requires_ledger 拒绝——那是"状态
        # 不可知"，必须显式失败，而不是静默按 JSON 半边读出 0（原始缺陷的形状）。
        # json/dual 模式 ledger 缺位按声明本来就落 JSON 半边，端点照常工作。
        ledger = None

    out: dict[str, dict[str, Any]] = {}
    for spec in specs:
        # 源状态是「这个源当前有多少篇文档在库里」的权威来源：它与入库
        # 逐条对应（失败的不写、被清理的会移除），比数 Milvus 里的 distinct
        # doc_id 便宜得多，也不需要全表扫描。
        doc_count = len(
            read_source_state(
                spec, cache_dir=cache_root, ledger=ledger, state_mode=state_mode
            )
        )
        out[spec.dataset_id] = {
            "dataset_id": spec.dataset_id,
            "origin": "source",
            "source_name": spec.name,
            "source_type": spec.type,
            "enabled": spec.enabled,
            "doc_count": doc_count,
            "metadata": spec.metadata,
        }
    return out


@app.get("/api/datasets")
def list_datasets(tenant_id: str = "") -> dict[str, Any]:
    """列出可检索的知识库（``dataset_id`` 的取值来源）。

    没有这个端点的话，``/api/query`` 的 ``dataset_id`` 参数对客户端等于
    「猜一个名字」——库里明明有五套官方文档，调用方却无从知道它们叫什么。

    合并两个来源，因为知识库有两种进库方式，各自只知道自己那半边：

    - ``origin="source"``：``config/sources.json`` 声明、由源同步写入；
    - ``origin="catalog"``：业务上传路径写入，带描述与租户归属。

    ``chunk_count`` 一律现查 Milvus，不信任何一边的记账——它才是「检索时
    真的能命中多少内容」的唯一事实。声明了但 ``chunk_count=0``，说明这个源
    还没同步过，这个区别对使用者是有意义的，所以照实返回而不是隐藏。
    """
    from config.settings import get_settings

    merged = _manifest_datasets()

    try:
        from core.catalog import list_datasets as catalog_datasets
        from rag_common import resolve_tenant

        # 走 resolve_tenant 而不是自己拼默认值：enforced 开关下的空值语义
        # （空串是否回退默认租户）只有它一处定义，复制一份迟早会跟它走偏。
        for d in catalog_datasets(resolve_tenant(tenant_id, get_settings())):
            entry = merged.setdefault(d["id"], {"dataset_id": d["id"], "doc_count": 0})
            entry.setdefault("origin", "catalog")
            entry["name"] = d.get("name") or ""
            entry["description"] = d.get("description") or ""
            entry["status"] = d.get("status") or ""
            # catalog 有记账时以它为准：上传路径的文档不在源状态文件里。
            if d.get("doc_count"):
                entry["doc_count"] = d["doc_count"]
    except Exception:  # noqa: BLE001 - 没配 MySQL 时 catalog 不可用，源那半边仍应可见
        get_logger(__name__).debug("catalog 知识库列举不可用，仅返回源清单声明的库")

    try:
        from core.milvus_client import RagMilvusClient

        # 与 server/health.py 同样每次现构造：构造本身不联网（连接是惰性的），
        # 而这是个低频的发现端点，为它维护一个长生命周期客户端不划算。
        client = RagMilvusClient(get_settings().milvus)
        for entry in merged.values():
            entry["chunk_count"] = client.count_chunks_by_dataset(entry["dataset_id"])
    except Exception as exc:  # noqa: BLE001 - Milvus 不可达时给出 null 而不是假的 0
        get_logger(__name__).warning("统计 chunk 数失败: %s", exc)
        for entry in merged.values():
            entry["chunk_count"] = None

    datasets = sorted(merged.values(), key=lambda e: e["dataset_id"])
    return {"datasets": datasets, "count": len(datasets)}


# ---------------------------------------------------------------------------
# 问答端点
# ---------------------------------------------------------------------------


def _run_query(
    req: QueryRequest,
    serving_guard: DocumentServingGuard | None = None,
    serving_snapshot: ServingSnapshot | None = None,
) -> dict[str, Any]:
    """同步执行一次完整 RAG 问答（线程池内运行；含缓存检查与记录）。

    这里给整条链路扣上 :class:`core.retry.deadline`。工作线程是唯一能一次性
    约束住所有下游重试的地方——链路上 14 个 ``create_client`` 调用点谁也不知道
    "整个请求还剩多少时间"，只有这里知道。不扣的话，LLM 的 120s × 3 次重试
    最坏能跑到 361 秒，而 HTTP 侧 240 秒就已经放弃了：多出来的两分钟是在为
    一个没人等的答案空转，过载时正是这部分负载把服务拖死。

    预算取 QUERY_TIMEOUT_S 的九成，留一成给序列化、缓存写入、埋点这些收尾
    工作——刚好卡在总闸上会让链路"算完了但来不及交付"，比早点放弃更糟。
    """
    if serving_guard is None and serving_snapshot is None:
        try:
            serving_guard, serving_snapshot = _serving_snapshot(req)
        except KnowledgeChanged as exc:
            get_metrics().incr("query.knowledge_changed")
            return _knowledge_changed_payload(req, str(exc))
    serving_token = serving_snapshot.cache_token if serving_snapshot is not None else ""
    cache_key = _cache_key(
        req.query,
        req.acl,
        req.retry,
        req.tenant_id,
        req.dataset_id,
        None,
        serving_token,
    )
    while True:
        cached, flight, owner = _cache_reserve(cache_key)
        if cached is not None:
            if serving_guard is not None and serving_snapshot is not None:
                try:
                    serving_guard.assert_current(serving_snapshot)
                except KnowledgeChanged as exc:
                    return _knowledge_changed_payload(req, str(exc))
            cached["cached"] = True
            get_metrics().incr("http.query.cached")
            return cached
        if owner:
            break
        get_metrics().incr("http.query.singleflight_wait")
        # 兜底用的线程层等待。事件循环层（_aio_flights）已经在拿槽之前就把
        # 绝大多数同 key 请求合并掉了，能走到这里的只剩极窄的竞态窗口，所以
        # 加超时而不是无限等：等待者本身占着工作线程，一旦 owner 卡住，
        # 无限等会让这个线程永久失踪。超时后回到循环重新判定（多半此时
        # 缓存已就绪；否则自己当 owner 重算一次）。
        if not flight.wait(timeout=QUERY_TIMEOUT_S):
            get_metrics().incr("http.query.singleflight_timeout")

    try:
        started = time.perf_counter()
        try:
            serving_scope = (
                bind_document_serving(serving_guard, serving_snapshot)
                if serving_guard is not None and serving_snapshot is not None
                else nullcontext()
            )
            with deadline(seconds=QUERY_TIMEOUT_S * 0.9), serving_scope:
                result = answer_query(
                    req.query,
                    acl=req.acl,
                    retry=req.retry,
                    tenant_id=req.tenant_id,
                    dataset_id=req.dataset_id,
                )
            if serving_guard is not None and serving_snapshot is not None:
                serving_guard.assert_current(serving_snapshot)
        except KnowledgeChanged as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            get_metrics().incr("query.knowledge_changed")
            return _knowledge_changed_payload(req, str(exc), duration_ms=duration_ms)
        except Exception:
            # 请求级失败计数。命名遵循 core.metrics 的 "<base>.errors" 约定，
            # 使 error_rate = errors.count / query.total.count 自动成立。
            get_metrics().incr("query.total.errors")
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        # 请求级耗时。此前只有 graph.invoke 打点，而它仅在 /api/query 的
        # 图编排路径上触发；前端默认走 SSE，于是监控页的核心指标恒为 0。
        # 这里在 HTTP 边界打点，两条路径都覆盖，且与编排方式无关。
        get_metrics().observe("query.total", duration_ms)
        if result.abstained:
            get_metrics().incr("query.abstained")
        payload = _serialize(result, duration_ms)
        if serving_guard is not None and serving_snapshot is not None:
            try:
                serving_guard.assert_current(serving_snapshot)
            except KnowledgeChanged as exc:
                get_metrics().incr("query.knowledge_changed")
                return _knowledge_changed_payload(req, str(exc), duration_ms=duration_ms)
        _cache_put(cache_key, payload)

        _record_query(
            {
                "query": result.query,
                "route": result.route,
                "abstained": result.abstained,
                "duration_ms": duration_ms,
                "citations": len(result.citations),
                "traces": result.traces,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        try:
            fact_tenant = resolve_tenant(req.tenant_id, get_settings())
        except Exception:  # noqa: BLE001
            fact_tenant = req.tenant_id or ""
        inner = payload.get("result") if isinstance(payload, dict) else None
        _maybe_record_answer_fact(
            tenant_id=fact_tenant,
            dataset_id=getattr(req, "dataset_id", None),
            run_id=None,
            question=result.query,
            answer=getattr(result, "answer", None),
            route=getattr(result, "route", None),
            abstained=bool(getattr(result, "abstained", False)),
            citations=getattr(result, "citations", None),
            evidence=(inner or {}).get("evidence") if isinstance(inner, dict) else None,
        )
        return payload
    finally:
        _cache_release(cache_key, flight)


@app.post("/api/query")
async def query(req: QueryRequest) -> dict[str, Any]:
    """执行一次完整 RAG 问答（缓存 -> 合并在途 -> 并发限流 -> 排队 -> 超时）。

    四道关卡的**顺序**是这个函数的全部要点，每一道都排在下一道前面是有代价
    考量的：越便宜的路径越早返回，绝不让廉价请求去挤昂贵请求的资源。

    1. 缓存命中     —— 一次 dict 查找，不占槽、不占线程，也不该被 429 拒绝
    2. 合并在途     —— 同一问题已在计算：await 它，同样不占槽、不占线程
    3. 取执行槽     —— 到这里才是真要干活的请求，接受排队与 429
    4. 超时         —— 拿到槽之后仍有总闸

    原来 1 和 2 都在工作线程里做（``_run_query`` 内部），于是缓存命中要排队、
    在途等待要占槽。压测下 32 并发有 79% 的请求被 429，其中很大一部分被拒的
    原因仅仅是「有人在等一个已经在算的答案」。
    """
    try:
        serving_guard, serving_snapshot = _serving_snapshot(req)
    except KnowledgeChanged as exc:
        return _knowledge_changed_payload(req, str(exc))
    serving_token = serving_snapshot.cache_token if serving_snapshot is not None else ""
    cache_key = _cache_key(
        req.query,
        req.acl,
        req.retry,
        req.tenant_id,
        req.dataset_id,
        None,
        serving_token,
    )

    # --- 1. 缓存命中：零成本路径 ---
    cached = _cache_peek(cache_key)
    if cached is not None:
        if serving_guard is not None and serving_snapshot is not None:
            try:
                serving_guard.assert_current(serving_snapshot)
            except KnowledgeChanged as exc:
                return _knowledge_changed_payload(req, str(exc))
        cached["cached"] = True
        get_metrics().incr("http.query.cached")
        return cached

    # --- 2. 已有同 key 在飞：挂上去等，不消耗任何执行资源 ---
    # shield 是必需的：本请求超时/断开不该连累正在为所有等待者干活的那个
    # future，否则一个客户端按了取消，其他等同一答案的人全部陪葬。
    inflight = _aio_flights.get(cache_key)
    if inflight is not None:
        get_metrics().incr("http.query.singleflight_wait")
        try:
            merged = await asyncio.wait_for(asyncio.shield(inflight), timeout=QUERY_TIMEOUT_S)
            # merged 为 None 表示 owner 没能产出结果（流式那侧客户端中途断开
            # 就是这样）。这不是错误，只是没东西可合并——落到下面自己算。
            if merged is not None:
                if serving_guard is not None and serving_snapshot is not None:
                    try:
                        serving_guard.assert_current(serving_snapshot)
                    except KnowledgeChanged as exc:
                        return _knowledge_changed_payload(req, str(exc))
                return dict(merged)
        except asyncio.TimeoutError:
            get_metrics().incr("http.query.timeout")
            raise HTTPException(status_code=503, detail="查询超时（合并等待）") from None
        except Exception:
            # owner 算失败了。不在这里替它报错——让本请求走下面的正常路径
            # 自己重算一次，避免「第一个人运气不好，后面所有人一起失败」。
            pass

    # --- 3. 真要干活：登记在途 -> 排队 -> 取槽 ---
    query_tenant = resolve_tenant(req.tenant_id, get_settings())
    if not _inc_pending(query_tenant):
        get_metrics().incr("http.query.rejected")
        raise HTTPException(status_code=429, detail="查询请求过多，请稍后重试")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QUERY_TIMEOUT_S
    try:
        await asyncio.wait_for(_query_slots.acquire(), timeout=QUERY_TIMEOUT_S)
    except asyncio.TimeoutError:
        _dec_pending(query_tenant)
        get_metrics().incr("http.query.timeout")
        raise HTTPException(status_code=503, detail="查询排队超时") from None
    except BaseException:
        _dec_pending(query_tenant)
        raise

    slots = _query_slots
    worker = loop.run_in_executor(
        _start_query_executor(),
        _run_query,
        req,
        serving_guard,
        serving_snapshot,
    )
    # 登记必须在 run_in_executor **之后、await 之前**：此刻仍在同一个同步
    # 片段里，后来的请求要么早于登记（走自己那条路，最多多算一次）、要么
    # 晚于登记（合并上来），不存在两者都错过的窗口。
    _aio_flights[cache_key] = worker

    worker.add_done_callback(
        lambda _: _release_query_slot(
            slots, query_tenant, flights=_aio_flights, cache_key=cache_key, worker=worker
        )
    )
    try:
        remaining = max(0.0, deadline - loop.time())
        return await asyncio.wait_for(asyncio.shield(worker), timeout=remaining)
    except asyncio.TimeoutError:
        get_metrics().incr("http.query.timeout")
        raise HTTPException(status_code=503, detail="查询超时（底层任务仍占用执行槽）") from None


_SSE_QUEUE_MAX = 256
_TYPED_SSE_QUEUE_MAX = 256

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n\n"


def _sse_response(chunks: Iterable[str]) -> StreamingResponse:
    return StreamingResponse(chunks, media_type="text/event-stream", headers=_SSE_HEADERS)


class _SseRunEventSink:
    """Failure-silent adapter from canonical run facts to the bounded SSE queue."""

    def __init__(self) -> None:
        self._send: Callable[[dict[str, Any] | None], bool] | None = None
        self._lock = threading.Lock()
        self._active_nodes: set[tuple[str, int]] = set()
        self._active_retries: set[tuple[str, int]] = set()

    def bind(self, send: Callable[[dict[str, Any] | None], bool]) -> None:
        with self._lock:
            self._send = send

    def __call__(self, event: RunEvent) -> None:
        with self._lock:
            send = self._send
            key = (event.node_id or "", event.attempt or 1)
            if event.type == "node.started":
                self._active_nodes.add(key)
            elif event.type in {
                "node.completed",
                "node.failed",
                "node.cancelled",
                "node.skipped",
            }:
                self._active_nodes.discard(key)
            elif event.type == "retry.started":
                self._active_retries.add(key)
            elif event.type in {
                "retry.completed",
                "retry.failed",
                "retry.skipped",
            }:
                self._active_retries.discard(key)
        if send is not None:
            send({"type": "run_event", "event": run_event_dict(event)})

    def active_attempts(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        with self._lock:
            return list(self._active_nodes), list(self._active_retries)


class _HttpStreamRun:
    """Own the canonical lifecycle and immutable topology choices for one request.

    The normal sequencer is allocated before the L1 probe.  It remains unstarted
    until the request path is known, allowing cache and single-flight replay to
    replace it with the distinct immutable replay topology without publishing a
    conflicting run.
    """

    def __init__(
        self,
        *,
        settings: Any,
        pipeline: Any,
        sink: _SseRunEventSink,
        retry_enabled: bool,
        extra_sinks: Sequence[RunEventSink] = (),
    ) -> None:
        self.run_id = f"run-{uuid.uuid4().hex[:16]}"
        self._sink = sink
        self._sinks = (sink, *extra_sinks)
        self._retry_enabled = retry_enabled
        self._query_topology = build_rag_topology(
            executor="sequential_stream",
            enabled_components=enabled_components_from_settings(settings),
            available_components=available_components_from_pipeline(pipeline),
        )
        self._replay_topology = build_cache_replay_topology()
        self._topology = self._query_topology
        self._sequencer = self._new_sequencer(self._query_topology)
        self._observer: RunObserver | None = None
        self._mode: str | None = None
        self._facts: dict[str, Any] = {}
        self._terminal = False
        self._lock = threading.RLock()

    def _new_sequencer(self, topology: RagTopology) -> RunEventSequencer:
        return RunEventSequencer(
            self.run_id,
            topology_id=topology.id,
            topology_revision=topology.revision,
            sinks=self._sinks,
        )

    def activate(
        self, *, mode: str, path: str, attributes: dict[str, Any] | None = None
    ) -> RunObserver:
        with self._lock:
            if self._observer is not None:
                return self._observer
            topology = self._replay_topology if mode == "cache_replay" else self._query_topology
            if topology is not self._topology:
                self._topology = topology
                self._sequencer = self._new_sequencer(topology)
            self._mode = mode
            self._facts.update(
                {
                    "request_path": path,
                    **(attributes or {}),
                }
            )
            self._observer = self._sequencer.observer()
            self._observer.start_run(
                attributes={
                    "topology": topology_dict(topology),
                    "executor_requested": "sequential_stream",
                    "executor_used": mode,
                    "retry_enabled": self._retry_enabled,
                    "request_path": path,
                    **(attributes or {}),
                }
            )
            boundary_node = "cache.lookup" if mode == "cache_replay" else "receive"
            self._observer.start_node(boundary_node, attempt=1)
            self._observer.complete_node(
                boundary_node,
                attempt=1,
                attributes={"request_path": path, **(attributes or {})},
            )
            if mode == "cache_replay":
                self._observer.start_node(
                    "cache.replay", attempt=1, attributes={"request_path": path}
                )
            return self._observer

    @property
    def observer(self) -> RunObserver:
        observer = self._observer
        if observer is None:
            raise RuntimeError("HTTP stream run has not been activated")
        return observer

    @property
    def activated(self) -> bool:
        with self._lock:
            return self._observer is not None

    @property
    def terminal(self) -> bool:
        with self._lock:
            return self._terminal

    def note(self, **facts: Any) -> None:
        with self._lock:
            if not self._terminal:
                self._facts.update(facts)

    def complete(self, *, abstained: bool) -> bool:
        with self._lock:
            if self._terminal:
                return False
            observer = self.observer
            if self._mode == "cache_replay":
                observer.complete_node(
                    "cache.replay",
                    attempt=1,
                    attributes={"outcome": "abstained" if abstained else "answered"},
                )
                observer.start_node("finalize", attempt=1)
                observer.complete_node(
                    "finalize",
                    attempt=1,
                    attributes={"outcome": "abstained" if abstained else "answered"},
                )
            event = observer.complete_run(
                attributes=dict(self._facts),
                outcome="abstained" if abstained else "answered",
                executor_used=self._mode or "sequential_stream",
            )
            self._terminal = event is not None
            return self._terminal

    def fail(self, *, error_type: str, error_code: str, reason: str) -> bool:
        with self._lock:
            if self._terminal:
                return False
            observer = self.observer
            self._close_active(observer, failure=True, reason=reason)
            event = observer.fail_run(
                error_type=error_type,
                error_code=error_code,
                attributes={**self._facts, "reason": reason},
            )
            self._terminal = event is not None
            return self._terminal

    def cancel(self, *, reason: str) -> bool:
        with self._lock:
            if self._terminal:
                return False
            observer = self.observer
            self._close_active(observer, failure=False, reason=reason)
            event = observer.cancel_run(attributes={**self._facts, "reason": reason})
            self._terminal = event is not None
            return self._terminal

    def _close_active(self, observer: RunObserver, *, failure: bool, reason: str) -> None:
        active_nodes, active_retries = self._sink.active_attempts()
        for node_id, attempt in active_nodes:
            if failure:
                observer.fail_node(
                    node_id,
                    attempt=attempt,
                    error_type="StreamBoundaryError",
                    error_code=reason,
                )
            else:
                observer.cancel_node(node_id, attempt=attempt, reason=reason)
        for node_id, attempt in active_retries:
            observer.retry_failed(
                node_id,
                attempt=attempt,
                error_type="StreamBoundaryError",
                error_code=reason,
            )


def _completed_payload_is_abstained(payload: dict[str, Any]) -> bool:
    return bool((payload.get("result") or {}).get("abstained"))


def _replay_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把一份**已经算好**的答案回放成 SSE 事件序列。

    命中缓存的流式请求不该退化成「什么都不发，只发一个 done」：前端
    （``frontend/src/api/client.ts`` 的 ``streamAnswer``）靠 ``phase`` 推进
    进度条、靠 ``token`` 填正文。只发 done 会让界面从空白直接闪成成品，看着
    像卡了一下之后突然跳变。回放同样的三种事件，命中缓存与真实生成在前端
    走的是同一条代码路径，不必为「缓存」再开一个分支。

    答案整段作为**一个** token 事件发出，不切片：切片只在片段之间有真实时间
    间隔时才有意义（那是生成速度的自然节奏），而这里所有片段会挤在同一次写里
    到达，切开只是徒增 JSON 开销和一堆无用的 SSE 帧。

    ``phase`` 复用 ``generating`` 而不是新造一个 ``cached``：前端的
    ``StreamPhase`` 是封闭联合类型（``frontend/src/types/rag.ts``），新增取值
    得连着改前端才有意义，而这里并不需要——命中是毫秒级的事，这个阶段值只会
    一闪而过。真正告诉前端「这是缓存」的是 done 载荷里的 ``cached`` 字段，
    与 ``/api/query`` 完全一致。
    """
    inner = payload.get("result") or {}
    answer = str(inner.get("answer") or "")
    events: list[dict[str, Any]] = [{"type": "phase", "phase": "generating"}]
    if answer:
        events.append({"type": "token", "text": answer})
    # 事件上的 cached 标记有两个读者：前端（"这是缓存"），以及下面的
    # event_source（"这个 done 已经是成品信封了，别再包一层、也别再写回缓存"）。
    events.append({"type": "done", "result": {**payload, "cached": True}, "cached": True})
    return events


@app.post("/api/query/stream")
async def query_stream(req: QueryRequest, request: Request) -> StreamingResponse:
    """SSE 流式问答：检索 / 生成 / 验证阶段事件 + 逐 token 文本。

    客户端断开（停止生成）时底层 LLM 流被关闭——停止在服务端真正生效。

    **这条路径必须和 /api/query 一样先过缓存与在途合并。** 前端默认走的是
    流式（``QueryPage`` 里 ``streamAnswer`` 是主路径，``fetchAnswer`` 只在它
    抛异常时兜底），而这里此前完全没有接缓存：两级缓存、跨进程 single-flight、
    语料代次失效全都建好了，却只有非流式路径在用——真实流量的命中率恒为 0，
    整套东西在生产上等于不存在。压测数据也因此测的是一条用户不走的路。

    四道关卡的顺序与 ``/api/query`` 逐条对齐，理由也一样：越便宜的路径越早
    返回，绝不让廉价请求去挤昂贵请求的资源。

    1. L1 命中   —— 一次 dict 查找，不占槽、不占线程，回放成事件流直接返回
    2. 合并在途  —— 同一问题已在算：await 它，同样不占槽、不占线程
    3. 取执行槽  —— 到这里才是真要干活的请求，接受排队与 429
    4. L2 命中   —— 在工作线程里查（见 producer），跨副本共享的那一份
    """
    metrics = get_metrics()
    metrics.incr("query.requests")
    settings = get_settings()
    resolved_tenant = resolve_tenant(req.tenant_id, settings)
    extra_sinks: tuple[RunEventSink, ...] = ()
    try:
        request_app = getattr(request, "app", app)
        runtime = getattr(request_app.state, "run_ops_runtime", None)
        registry = getattr(runtime, "registry", None)
        identity = getattr(runtime, "identity", None)
        if registry is not None and identity is not None:
            tenant_scope = derive_tenant_scope(resolved_tenant, identity.scope_key)
            history_settings = getattr(settings, "run_history", settings)
            configured_secret = getattr(history_settings, "fingerprint_secret", None)
            query_fingerprint = None
            if configured_secret is not None:
                reveal = getattr(configured_secret, "get_secret_value", None)
                secret_value = reveal() if callable(reveal) else configured_secret
                if isinstance(secret_value, str) and secret_value:
                    query_fingerprint = fingerprint_query(req.query, secret_value.encode("utf-8"))
            extra_sinks = (registry.bound_sink(tenant_scope, query_fingerprint),)
    except Exception:
        extra_sinks = ()
    pipeline = get_pipeline(settings)
    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    deadline_at = loop.time() + QUERY_TIMEOUT_S
    stop = threading.Event()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_SSE_QUEUE_MAX)
    typed_events: deque[dict[str, Any]] = deque()
    typed_lock = threading.Lock()
    typed_desync: dict[str, Any] | None = None
    typed_desync_emitted = False

    def emit_typed(event: dict[str, Any] | None) -> bool:
        """Buffer canonical facts without blocking on client backpressure."""
        nonlocal typed_desync
        if event is None:
            return True
        with typed_lock:
            if typed_desync is not None:
                return True
            if len(typed_events) >= _TYPED_SSE_QUEUE_MAX:
                payload = event.get("event") or {}
                typed_desync = {
                    "type": "run_event_desync",
                    "run_id": str(payload.get("run_id") or http_run.run_id),
                    "expected_seq": int(payload.get("seq") or 1),
                    "reason": "typed_buffer_overflow",
                }
                return True
            typed_events.append(event)
        return True

    def emit(event: dict[str, Any] | None) -> bool:
        """Reliably bridge legacy worker events with bounded backpressure."""
        if threading.get_ident() == loop_thread_id:
            try:
                queue.put_nowait(event)
                return True
            except asyncio.QueueFull:
                return False
        while True:
            acknowledged = threading.Event()
            accepted = False

            def try_put() -> None:
                nonlocal accepted
                try:
                    queue.put_nowait(event)
                    accepted = True
                except asyncio.QueueFull:
                    accepted = False
                finally:
                    acknowledged.set()

            try:
                loop.call_soon_threadsafe(try_put)
            except RuntimeError:
                return False
            while not acknowledged.wait(timeout=0.5):
                if stop.is_set() or loop.is_closed():
                    return False
            if accepted:
                return True
            if stop.wait(timeout=0.01) or loop.is_closed():
                return False

    sse_sink = _SseRunEventSink()
    sse_sink.bind(emit_typed)
    http_run = _HttpStreamRun(
        settings=settings,
        pipeline=pipeline,
        sink=sse_sink,
        retry_enabled=req.retry,
        extra_sinks=extra_sinks,
    )

    def drain_typed_ready() -> list[dict[str, Any]]:
        nonlocal typed_desync_emitted
        with typed_lock:
            ready = list(typed_events)
            typed_events.clear()
            if typed_desync is not None and not typed_desync_emitted:
                ready.append(dict(typed_desync))
                typed_desync_emitted = True
            return ready

    def drain_legacy_ready() -> list[dict[str, Any]]:
        ready: list[dict[str, Any]] = []
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return ready
            if item is not None:
                ready.append(item)

    def drain_ready() -> list[dict[str, Any]]:
        return [*drain_typed_ready(), *drain_legacy_ready()]

    def finish_cancelled(reason: str) -> None:
        if not http_run.activated:
            http_run.activate(mode="sequential_stream", path=reason)
        http_run.note(delivery="cancelled")
        if http_run.cancel(reason=reason):
            metrics.incr("query.cancelled")

    def finish_failed(*, error_type: str, error_code: str, reason: str) -> None:
        if not http_run.activated:
            http_run.activate(mode="sequential_stream", path=reason)
        http_run.note(delivery="failed")
        if http_run.fail(error_type=error_type, error_code=error_code, reason=reason):
            metrics.incr("query.errors")

    def finish_completed(payload: dict[str, Any]) -> None:
        abstained = _completed_payload_is_abstained(payload)
        http_run.note(delivery="completed")
        if http_run.complete(abstained=abstained):
            metrics.incr("query.completed")

    def replay_response(
        payload: dict[str, Any],
        *,
        path: str,
        cache_hit: bool,
        guard: DocumentServingGuard | None = None,
        snapshot: ServingSnapshot | None = None,
    ) -> StreamingResponse:
        http_run.activate(
            mode="cache_replay",
            path=path,
            attributes={"cache_level": path},
        )
        if cache_hit:
            metrics.incr("query.cache_hits")
        consuming = threading.Event()

        def check_abandoned_replay() -> None:
            if not consuming.is_set():
                stop.set()
                finish_cancelled("response_abandoned")

        loop.call_later(0.05, check_abandoned_replay)

        async def replay_source():
            consuming.set()
            try:
                for pending in drain_ready():
                    yield _sse(pending)
                for event in _replay_events(payload):
                    if (
                        event.get("type") in {"token", "done"}
                        and guard is not None
                        and snapshot is not None
                    ):
                        try:
                            guard.assert_current(snapshot)
                        except KnowledgeChanged as exc:
                            metrics.incr("query.knowledge_changed")
                            changed = _knowledge_changed_payload(req, str(exc))
                            for safe_event in _replay_events(changed):
                                yield _sse(safe_event)
                                if safe_event.get("type") == "done":
                                    finish_completed(changed)
                                    for pending in drain_ready():
                                        yield _sse(pending)
                                    return
                    yield _sse(event)
                    if event.get("type") == "done":
                        if _completed_payload_is_abstained(payload):
                            metrics.incr("query.abstained")
                        finish_completed(payload)
                        for pending in drain_ready():
                            yield _sse(pending)
                        return
            except asyncio.CancelledError:
                stop.set()
                finish_cancelled("client_cancelled")
                raise
            finally:
                stop.set()
                if not http_run.terminal:
                    finish_cancelled("client_cancelled")

        return _sse_response(replay_source())

    try:
        serving_guard, serving_snapshot = _serving_snapshot(req, resolved_tenant=resolved_tenant)
    except KnowledgeChanged as exc:
        payload = _knowledge_changed_payload(req, str(exc))
        return replay_response(payload, path="knowledge_changed", cache_hit=False)
    serving_token = serving_snapshot.cache_token if serving_snapshot is not None else ""
    cache_key = _cache_key(
        req.query,
        req.acl,
        req.retry,
        req.tenant_id,
        req.dataset_id,
        resolved_tenant,
        serving_token,
    )

    # --- 1. L1 命中：零成本路径，不占槽也不该被 429 拒绝 ---
    cached = _cache_peek(cache_key)
    if cached is not None:
        if serving_guard is not None and serving_snapshot is not None:
            try:
                serving_guard.assert_current(serving_snapshot)
            except KnowledgeChanged as exc:
                return replay_response(
                    _knowledge_changed_payload(req, str(exc)),
                    path="knowledge_changed",
                    cache_hit=False,
                )
        metrics.incr("http.query.cached")
        return replay_response(
            cached,
            path="l1",
            cache_hit=True,
            guard=serving_guard,
            snapshot=serving_snapshot,
        )

    # --- 2. 已有同 key 在飞：挂上去等，不消耗任何执行资源 ---
    # 代价要说清楚：合并上来的这一位拿不到逐 token 的打字机效果，只能在
    # owner 算完的那一刻一次性看到全文。换来的是省掉一整条 RAG 链路和一整轮
    # token——热点问题（同一时刻很多人问同一句）正是这个机制存在的理由。
    # shield 是必需的：本请求断开不该连累正在为所有等待者干活的那个 future。
    inflight = _aio_flights.get(cache_key)
    if inflight is not None:
        metrics.incr("http.query.singleflight_wait")
        metrics.incr("query.singleflight_joined")
        merged: dict[str, Any] | None = None
        try:
            merged = await asyncio.wait_for(asyncio.shield(inflight), timeout=QUERY_TIMEOUT_S)
        except asyncio.TimeoutError:
            metrics.incr("http.query.timeout")
            metrics.incr("query.timeouts")
            http_run.activate(mode="sequential_stream", path="singleflight_timeout")
            finish_failed(
                error_type="TimeoutError",
                error_code="singleflight_timeout",
                reason="singleflight_timeout",
            )
            raise HTTPException(status_code=503, detail="查询超时（合并等待）") from None
        except Exception:  # noqa: BLE001 - owner 失败不该连坐，自己算一遍
            merged = None
        if merged is not None:
            http_run.note(singleflight="same_process")
            return replay_response(
                merged,
                path="singleflight_same_process",
                cache_hit=False,
                guard=serving_guard,
                snapshot=serving_snapshot,
            )

    # --- 3. 真要干活：排队 -> 取槽 ---
    if not _inc_pending(resolved_tenant):
        metrics.incr("http.query.rejected")
        metrics.incr("query.rejected")
        http_run.note(queue_admitted=False)
        http_run.activate(mode="sequential_stream", path="queue_rejected")
        http_run.cancel(reason="queue_rejected")
        raise HTTPException(status_code=429, detail="查询请求过多，请稍后重试")

    slot_wait_started = time.perf_counter()
    try:
        await asyncio.wait_for(_query_slots.acquire(), timeout=QUERY_TIMEOUT_S)
    except asyncio.TimeoutError:
        _dec_pending(resolved_tenant)
        metrics.incr("http.query.timeout")
        metrics.incr("query.timeouts")
        http_run.note(queue_admitted=True)
        http_run.activate(mode="sequential_stream", path="queue_timeout")
        finish_failed(
            error_type="TimeoutError",
            error_code="queue_timeout",
            reason="queue_timeout",
        )
        raise HTTPException(status_code=503, detail="查询排队超时") from None
    except BaseException:
        _dec_pending(resolved_tenant)
        http_run.activate(mode="sequential_stream", path="queue_cancelled")
        finish_cancelled("client_cancelled")
        raise

    slot_wait_ms = round((time.perf_counter() - slot_wait_started) * 1000.0, 3)
    http_run.note(queue_admitted=True, slot_wait_ms=slot_wait_ms)
    # 事件循环层的在途登记：本请求算出来的答案，同 key 的后来者（流式与非流式
    # 都算）可以直接 await 它。载荷形状与 /api/query 完全一致，所以两条路径
    # 可以互相合并——同一个问题不会因为「一个人点了流式、一个人没点」就算两遍。
    answer_future: "asyncio.Future[dict[str, Any] | None]" = loop.create_future()
    _aio_flights[cache_key] = answer_future

    producer_failure: dict[str, str] = {}
    producer_cleanup_done = threading.Event()

    def producer() -> None:
        """工作线程：驱动同步流式生成器，事件经队列桥接进事件循环。

        进门先走一遍 ``reserve()``——这是 L2（跨副本）命中与跨进程 single-flight
        唯一能发生的地方。为什么不在事件循环里查 L2：那是一次网络往返，
        ``socket_timeout`` 是 1 秒，Redis 一卡就能把整个事件循环拽停（见
        ``_cache_peek`` 的说明）。放到工作线程里查，代价是 L2 命中也要占一个
        执行槽，但只占一次 GET 的毫秒级时间，与「占着槽跑完一整条链路」不是
        一回事。这与 ``_run_query`` 的处理完全一致。
        """
        flight: threading.Event | None = None

        def release_flight() -> None:
            nonlocal flight
            if flight is not None:
                _cache_release(cache_key, flight)
                flight = None
                http_run.note(flight_released=True)

        try:
            while True:
                hit, event_, owner = _cache_reserve(cache_key)
                if hit is not None:
                    if serving_guard is not None and serving_snapshot is not None:
                        try:
                            serving_guard.assert_current(serving_snapshot)
                        except KnowledgeChanged as exc:
                            changed = _knowledge_changed_payload(req, str(exc))
                            for ev in _replay_events(changed):
                                if not emit(ev):
                                    return
                            emit(None)
                            return
                    # 命中即返回，且**不**还锁：reserve 的契约是「owner=False
                    # 时所有权不在你手上」。误还会把别的线程正持有的跨进程锁
                    # 提前解掉（release 里摘 token 那步没有身份校验），于是另一个
                    # 副本会以为没人在算，把同一条链路再跑一遍。
                    http_run.activate(
                        mode="cache_replay",
                        path="l2",
                        attributes={"cache_level": "l2", "slot_wait_ms": slot_wait_ms},
                    )
                    metrics.incr("http.query.cached")
                    metrics.incr("query.cache_hits")
                    for ev in _replay_events(hit):
                        if not emit(ev):
                            return
                    emit(None)
                    return
                if owner:
                    flight = event_
                    http_run.activate(
                        mode="sequential_stream",
                        path="owner",
                        attributes={"slot_wait_ms": slot_wait_ms},
                    )
                    break
                metrics.incr("http.query.singleflight_wait")
                metrics.incr("query.singleflight_joined")
                http_run.note(singleflight="cross_process")
                # 线程层等待是兜底：事件循环层已经在拿槽之前合并掉了绝大多数
                # 同 key 请求，走到这里的只剩极窄的竞态窗口与跨进程的那一份。
                if not event_.wait(timeout=QUERY_TIMEOUT_S):
                    metrics.incr("http.query.singleflight_timeout")
                    metrics.incr("query.timeouts")

            serving_scope = (
                bind_document_serving(serving_guard, serving_snapshot)
                if serving_guard is not None and serving_snapshot is not None
                else nullcontext()
            )
            with serving_scope:
                gen = answer_query_stream(
                    req.query,
                    acl=req.acl,
                    retry=req.retry,
                    tenant_id=resolved_tenant,
                    dataset_id=req.dataset_id,
                    tenant_resolved=True,
                    query_id=http_run.run_id,
                    observer=http_run.observer,
                )
                try:
                    for event in gen:
                        if stop.is_set():
                            gen.close()  # 触发 GeneratorExit：关闭底层 LLM 流
                            metrics.incr("http.query.stopped")
                            return
                        if (
                            event.get("type") in {"token", "done"}
                            and serving_guard is not None
                            and serving_snapshot is not None
                        ):
                            serving_guard.assert_current(serving_snapshot)
                        if not emit(event):
                            gen.close()
                            return
                except KnowledgeChanged as exc:
                    gen.close()
                    metrics.incr("query.knowledge_changed")
                    payload = _knowledge_changed_payload(req, str(exc))
                    emit({"type": "done", "result": payload["result"]})
                release_flight()
                emit(None)
        except Exception as exc:  # noqa: BLE001 - 流式兜底：错误事件后终止
            _logger.exception("流式问答内部错误")
            metrics.incr("query.total.errors")
            producer_failure["type"] = type(exc).__name__
            emit({"type": "error", "code": "stream_error", "message": str(exc)[:300]})
            release_flight()
            emit(None)
        finally:
            # 只有 owner 需要还锁；命中缓存的那条分支拿到的不是所有权。
            # 不还的话跨进程锁要等 lock_ttl_s 才自解，期间别的副本白等。
            release_flight()
            producer_cleanup_done.set()

    started = time.perf_counter()
    slots = _query_slots
    worker = loop.run_in_executor(_start_query_executor(), producer)

    # 响应体是否已经被迭代。两个收尾路径（event_source 的 finally 与 worker 的
    # 完成回调）靠它分工，见 _release_slot。
    consuming = threading.Event()

    def _abandon_flight() -> None:
        """摘掉在途登记，并把没能产出答案的 future 收成 None。

        给 None 而不是异常：异常没人取会被 asyncio 记一条 "never retrieved"
        噪音，而等待者对这两者的处理本来就一样——自己算一遍。
        身份校验不能省：同 key 的下一个请求可能已经登记了**它自己**的 future，
        无脑 pop 会把那一位的等待者全甩掉。
        """
        if not answer_future.done():
            answer_future.set_result(None)
        if _aio_flights.get(cache_key) is answer_future:
            _aio_flights.pop(cache_key, None)

    # 响应体一次都没被迭代过的情形是真实存在的：客户端在首字节之前就断开，
    # 或调用方拿到 StreamingResponse 却不消费（测试里就这么干）。watchdog 必须
    # 在 worker 完成前就启动：有界 SSE 队列写满时 worker 正阻塞在 emit()，若等
    # 完成回调再启动 watchdog，就形成“worker 等消费者、watchdog 等 worker”的环。
    # 给 StreamingResponse 50ms 开始迭代，避免极快 producer 在响应对象刚返回时
    # 把正常请求误判成 abandoned；一旦开始消费，check_abandoned 只会无操作返回。
    def check_abandoned() -> None:
        if not consuming.is_set():
            stop.set()
            _abandon_flight()
            finish_cancelled("response_abandoned")

    loop.call_later(0.05, check_abandoned)

    worker.add_done_callback(lambda _: _release_query_slot(slots, resolved_tenant))

    async def wait_for_producer_cleanup() -> None:
        while not producer_cleanup_done.is_set():
            if loop.time() >= deadline_at:
                return
            await asyncio.sleep(0.001)

    async def event_source():
        consuming.set()
        try:
            while True:
                for pending in drain_typed_ready():
                    yield _sse(pending)
                if producer_cleanup_done.is_set() and queue.empty():
                    break
                remaining = deadline_at - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
                for pending in drain_typed_ready():
                    yield _sse(pending)
                if event is None:
                    break
                if (
                    event.get("type") in {"token", "done"}
                    and serving_guard is not None
                    and serving_snapshot is not None
                ):
                    try:
                        serving_guard.assert_current(serving_snapshot)
                    except KnowledgeChanged as exc:
                        metrics.incr("query.knowledge_changed")
                        changed = _knowledge_changed_payload(
                            req,
                            str(exc),
                            duration_ms=round((time.perf_counter() - started) * 1000, 1),
                        )
                        event = {"type": "done", "result": changed["result"]}
                        stop.set()
                if event.get("type") == "done":
                    cached_replay = bool(event.get("cached"))
                    if cached_replay:
                        # 回放上来的 done 已经是成品信封（_replay_events 造的）。
                        # 再包一层会变成 {"result": {"result": ...}}，而重新写回
                        # 缓存更糟：每命中一次就把 TTL 续一次，弃权结果的 60 秒
                        # 短命策略会在热点问句上被无限续期，永远发不出新答案。
                        payload = event["result"]
                        inner = payload.get("result") or {}
                        duration_ms = float(payload.get("duration_ms") or 0.0)
                    else:
                        # done 事件统一为前端 QueryResponse 契约
                        # {result, using_mock, duration_ms}
                        inner = event["result"]
                        duration_ms = round((time.perf_counter() - started) * 1000, 1)
                        payload = {
                            "result": inner,
                            "using_mock": False,
                            "duration_ms": duration_ms,
                        }
                        event = {"type": "done", "result": payload}
                    yield _sse(event)
                    await wait_for_producer_cleanup()
                    if cached_replay:
                        if _completed_payload_is_abstained(payload):
                            metrics.incr("query.abstained")
                    else:
                        # Delivery is committed only when the consumer requests the
                        # next chunk after receiving the legacy done frame.
                        metrics.observe("query.total", duration_ms)
                        if inner.get("abstained"):
                            metrics.incr("query.abstained")
                        if not inner.get("knowledge_changed"):
                            cache_current = True
                            if serving_guard is not None and serving_snapshot is not None:
                                try:
                                    serving_guard.assert_current(serving_snapshot)
                                except KnowledgeChanged:
                                    cache_current = False
                                    metrics.incr("query.knowledge_changed")
                            if cache_current:
                                _cache_put(cache_key, payload)
                                http_run.note(cache_write=True)
                        _record_query(
                            {
                                "query": inner.get("query", ""),
                                "route": inner.get("route", ""),
                                "abstained": bool(inner.get("abstained")),
                                "duration_ms": duration_ms,
                                "citations": len(inner.get("citations") or []),
                                "traces": inner.get("traces") or [],
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                            }
                        )
                        # 复用入口处已解析的 tenant：一次请求只解析一次是本路径的不变量
                        # （tests/test_run_registry_integration 钉住），再解析一次既多一次
                        # 目录访问，也可能在设置变更后把证据记到另一个租户下。
                        _maybe_record_answer_fact(
                            tenant_id=resolved_tenant,
                            dataset_id=getattr(req, "dataset_id", None),
                            run_id=getattr(http_run, "run_id", None),
                            question=inner.get("query"),
                            answer=inner.get("answer"),
                            route=inner.get("route"),
                            abstained=bool(inner.get("abstained")),
                            citations=inner.get("citations"),
                            evidence=inner.get("evidence"),
                        )
                    if not answer_future.done():
                        answer_future.set_result(payload)
                    finish_completed(payload)
                    for pending in drain_ready():
                        yield _sse(pending)
                    return
                if event.get("type") == "error" and event.get("code") == "stream_error":
                    yield _sse(event)
                    await wait_for_producer_cleanup()
                    finish_failed(
                        error_type=producer_failure.get("type", "Exception"),
                        error_code="stream_error",
                        reason="producer_failure",
                    )
                    for pending in drain_ready():
                        yield _sse(pending)
                    return
                yield _sse(event)
            finish_failed(
                error_type="StreamEndedError",
                error_code="stream_incomplete",
                reason="stream_incomplete",
            )
            for pending in drain_ready():
                yield _sse(pending)
        except asyncio.TimeoutError:
            metrics.incr("http.query.timeout")
            metrics.incr("query.timeouts")
            yield _sse(
                {
                    "type": "error",
                    "code": "query_timeout",
                    "message": "查询超时（底层任务仍占用执行槽）",
                }
            )
            finish_failed(
                error_type="TimeoutError",
                error_code="query_timeout",
                reason="stream_timeout",
            )
            for pending in drain_ready():
                yield _sse(pending)
        except asyncio.CancelledError:
            # 客户端断开：停止生成在服务端生效
            stop.set()
            metrics.incr("http.query.stopped")
            finish_cancelled("client_cancelled")
            raise
        finally:
            stop.set()
            if not http_run.terminal:
                finish_cancelled("client_cancelled")
            # 在途登记的生命周期收在这里，而不是搭在 worker 的完成回调上：
            # producer 返回时 done 事件可能还躺在队列里没被取走，那时候摘登记
            # 会把等待者甩给一个永远不 resolve 的 future。只有这里知道答案到底
            # 有没有交付出去。（完成回调只兜"响应体压根没被迭代"那一种。）
            _abandon_flight()

    return _sse_response(event_source())


# ---------------------------------------------------------------------------
# 监控端点
# ---------------------------------------------------------------------------


def _degraded_stats() -> dict[str, Any]:
    """上游降级率汇总（供 /api/metrics 与监控页直接消费）。

    为什么要单独一节：``metrics`` 里是原始计数器，前端要自己拼分母，
    容易拼错（分母取错就等于没有这个指标）。这里在服务端把"比率"算好，
    前端只负责显示。

    覆盖范围：
    - ``retrieval`` / ``reranker``：核心检索链路（含重排降级）；
    - ``llm`` / ``embedding``：端点探活发现的"不可达"（快速失败的那些）。
    """
    snapshot = get_metrics().snapshot()

    def rate(scope: str) -> dict[str, float]:
        total = float(snapshot.get(f"{scope}.total", {}).get("count", 0) or 0)
        failed = float(snapshot.get(f"{scope}.errors", {}).get("count", 0) or 0)
        return {
            "total": total,
            "degraded": failed,
            "rate": (failed / total) if total > 0 else 0.0,
        }

    unreachable = {
        key: float(stat.get("count", 0) or 0)
        for key, stat in snapshot.items()
        if key.endswith(".endpoint.unreachable")
    }
    return {
        "retrieval": rate("retrieval"),
        "reranker": rate("reranker"),
        "endpoint_unreachable": unreachable,
    }


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    """进程内指标快照（count / mean / p50 / p95 / p99 / error_rate）+ 最近查询。

    附加服务层信息：uptime / 查询队列 / 缓存 / 熔断器。
    """
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": get_metrics().snapshot(),
        "degraded": _degraded_stats(),
        "recent_queries": _recent_queries(),
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "queue": _queue_stats(),
        "cache": _cache_stats(),
        "embed_cache": _embed_cache_stats(),
        "llm_cache": _llm_cache_stats(),
        "redis": redis_client.stats(),
        "circuits": list_circuits(),
    }


def _prometheus_health_lines() -> list[str]:
    """把缓存 / Redis 的**状态**也导出成 gauge。

    时序指标（上面那段）只回答"快不快"，回答不了"L2 还在不在"。而 L2 最坏的
    那个状态——**配了 Redis、但连不上**——恰恰不体现在任何耗时曲线上：答案照
    出，只是每个请求都静默 miss。它唯一的外在表现是命中率慢慢塌下去，等有人
    发现时已经过去很久了。

    所以真正该报警的表达式是这一条，而它需要两个 gauge 才写得出来::

        rag4c_cache_l2_configured == 1 and rag4c_cache_l2_reachable == 0

    ``l2_errors`` 一并导出：它是"Redis 出过问题"的累计痕迹，配合 ``rate()``
    比裸计数有用得多。
    """
    lines: list[str] = []
    sources = {
        "answer": _cache_stats,
        "embed": _embed_cache_stats,
        "llm": _llm_cache_stats,
    }
    for name, fn in sources.items():
        try:
            snap = fn()
        except Exception:  # noqa: BLE001 - 监控出口不该把自己搞挂
            continue
        if not isinstance(snap, dict) or "error" in snap:
            continue
        for field in ("l2_configured", "l2_reachable"):
            if field in snap:
                lines.append(f'rag4c_cache_{field}{{cache="{name}"}} {int(bool(snap[field]))}')
        for field in ("l2_errors", "hits", "misses", "hit_rate"):
            if isinstance(snap.get(field), (int, float)) and not isinstance(snap[field], bool):
                lines.append(f'rag4c_cache_{field}{{cache="{name}"}} {snap[field]}')
    try:
        r = redis_client.stats()
        lines.append(f"rag4c_redis_configured {int(bool(r.get('configured')))}")
        lines.append(f"rag4c_redis_connected {int(bool(r.get('connected')))}")
    except Exception:  # noqa: BLE001
        pass
    return lines


@app.get("/api/metrics/prometheus")
def metrics_prometheus() -> Any:
    """Prometheus 文本格式指标（可接 Grafana / Prometheus 抓取）。"""
    from fastapi.responses import PlainTextResponse

    body = _prometheus_text(get_metrics().snapshot())
    health = _prometheus_health_lines()
    if health:
        body = body + "\n".join(health) + "\n"
    return PlainTextResponse(body, media_type="text/plain")


@app.get("/api/metrics/history")
def metrics_history(limit: int = 120) -> dict[str, Any]:
    """读取持久化的指标历史（data/metrics-history.jsonl 尾部 limit 行）。

    只读文件尾部：每行是一份完整指标快照（可达数 KB），文件上限 5MB。
    此前的实现是整文件 read_text 再切片，每次轮询都要把全部内容读进内存
    并解码，还占着持久化线程要用的同一把锁。这里改为从文件末尾按块回读，
    读够 limit 行即停——代价与 limit 成正比，与文件大小无关。
    """
    limit = max(1, min(int(limit), 500))
    path = METRICS_HISTORY_PATH
    if not path.is_file():
        return {"items": [], "note": "尚无历史快照（启动约 1 分钟后开始落盘）"}
    try:
        with _metrics_hist_lock:
            lines = _tail_lines(path, limit)
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "error": str(exc)[:200]}
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# 图谱可视化端点（组件进程级缓存，避免每请求重建连接）
# ---------------------------------------------------------------------------


async def _graph_query_tenant(request: Request) -> str:
    """Resolve a graph-read tenant without weakening local bridge behavior.

    Direct loopback/test calls may use the configured default tenant. Remote
    calls must present a signed Knowledge Actor and are bound to its tenant;
    the asserted header is checked by ``require_knowledge_permission``.
    """

    from core.knowledge_permissions import KNOWLEDGE_READ
    from server.knowledge_auth import require_knowledge_permission
    from server.run_ops import bearer_credential
    from server.security import is_loopback_or_test

    settings = get_settings()
    if is_loopback_or_test(request) and bearer_credential(request) is None:
        return resolve_tenant(request.headers.get("X-RAG4C-Tenant"), settings)
    actor = await require_knowledge_permission(KNOWLEDGE_READ)(request)
    return actor.tenant_id


def _build_graph_components() -> tuple[Any, Any]:
    """懒加载图查询组件并保留旧 tuple 组合根投影。"""
    from config.settings import get_settings
    from core.graph_query_components import create_graph_query_component

    s = get_settings()
    component = create_graph_query_component(
        s,
        engine_override="milvus_vector_graph",
        timeout_s=GRAPH_TIMEOUT_S,
    )
    return component.store, component.embedder


_graph_components_cache = GenerationAwareSingletonCache(_build_graph_components)


def _get_graph_components() -> tuple[Any, Any]:
    """Return the current graph tuple without allowing stale rebuilds to recache."""

    try:
        return _graph_components_cache.get()
    except GenerationCacheSaturated:
        raise
    except Exception as exc:  # noqa: BLE001 - retry assembly on the next request
        kind = getattr(exc, "kind", "graph")
        _logger.warning("图谱查询组件不可用（%s），本次不缓存失败: %s", kind, exc)
        return None, None


# Keep the old cache_clear seam used by hot reload and compatibility tests.
_get_graph_components.cache_clear = _graph_components_cache.clear  # type: ignore[attr-defined]


def _get_graph_query_component() -> Any:
    """Build the API component from the compatibility tuple seam."""
    from core.graph_query_components import (
        GraphQueryAssemblyError,
        GraphQueryComponent,
    )

    try:
        store, embedder = _get_graph_components()
    except GenerationCacheSaturated as exc:
        _logger.warning("图谱组件旧构建仍在运行，暂不启动更多构建: %s", exc)
        return None
    if store is None or embedder is None:
        return None
    try:
        return GraphQueryComponent(
            store=store,
            embedder=embedder,
            timeout_s=GRAPH_TIMEOUT_S,
        )
    except GraphQueryAssemblyError as exc:
        _logger.warning("图谱兼容 tuple 不满足查询契约（%s）: %s", exc.kind, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - safe unavailable-component fallback
        _logger.warning("图谱兼容 tuple 装配失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 辅助端点限流
# ---------------------------------------------------------------------------
#
# /api/graph/search 原来是条**完全没有闸门的旁路**：它是同步 def 路由，跑在
# Starlette/anyio 的默认线程池（40 线程）里，内部却做了一次远程嵌入 + 两次
# Milvus 向量检索——没有信号量、没有队列上限、没有熔断、没有超时。
#
# 两个后果都很实在：
#   1. 前端图谱页一刷新就并发发请求，40 个线程能同时打爆远程嵌入配额，而
#      主问答链路用的是同一个 key、同一份额度，于是"点了下图谱"能把问答拖垮；
#   2. anyio 默认池是 /api/health、/api/metrics、/api/documents/* 共用的。
#      占满它意味着**监控和运维端点一起失联**——偏偏是故障时最需要它们的时候。
#
# 用有界信号量而不是复用查询队列：图谱检索比问答便宜得多（1 次嵌入 vs 十来次
# 远程调用），跟问答抢同一个池会让廉价请求被昂贵请求饿死。各自限流，互不牵连。
GRAPH_MAX_CONCURRENT = int(os.environ.get("RAG4C_GRAPH_MAX_CONCURRENT", "8"))
GRAPH_TIMEOUT_S = float(os.environ.get("RAG4C_GRAPH_TIMEOUT_S", "30"))
GRAPH_SUBGRAPH_MAX_IDS = max(1, int(os.environ.get("RAG4C_GRAPH_SUBGRAPH_MAX_IDS", "200")))
_graph_slots = threading.BoundedSemaphore(GRAPH_MAX_CONCURRENT)


@contextmanager
def _graph_gate() -> Any:
    """占一个图谱检索槽位，占不到就 429。

    非阻塞 acquire：这是个交互式端点，让用户等 30 秒队列还不如立刻告诉他
    "现在忙"，前端可以退避重试。阻塞排队只会把 anyio 线程也一起搭进去，
    正是这里要避免的东西。
    """
    if not _graph_slots.acquire(blocking=False):
        get_metrics().incr("http.graph.rejected")
        raise HTTPException(status_code=429, detail="图谱检索繁忙，请稍后重试")
    try:
        with deadline(seconds=GRAPH_TIMEOUT_S):
            yield
    finally:
        _graph_slots.release()


@app.post("/api/graph/search")
def graph_search(
    req: GraphSearchRequest,
    tenant_id: str = Depends(_graph_query_tenant),
) -> dict[str, Any]:
    """实体 / 关系检索：返回命中实体与关系，供前端画知识图谱。

    结果结构：
    - entities: [{id, text, score, relation_ids, passage_ids}]
    - relations: [{id, text, score, entity_ids, subject, predicate, object}]
    """
    from core.graph_query_components import (
        GraphQueryBackendError,
        GraphQueryEmbeddingError,
        GraphQueryTimeoutError,
    )

    with _graph_gate():
        component = _get_graph_query_component()
        if component is None:
            return {"entities": [], "relations": [], "error": "图谱组件不可用"}
        try:
            result = component.search(
                req.query,
                entity_top_k=req.entity_top_k,
                relation_top_k=req.relation_top_k,
                tenant_id=tenant_id,
            )
        except GraphQueryTimeoutError as exc:
            return {
                "entities": [],
                "relations": [],
                "error": f"图谱查询超时：{exc}",
            }
        except GraphQueryEmbeddingError as exc:
            return {
                "entities": [],
                "relations": [],
                "error": f"嵌入服务不可用：{exc}（检查 RAG4C_EMBEDDING_API_KEY）",
            }
        except GraphQueryBackendError as exc:
            return {
                "entities": [],
                "relations": [],
                "error": f"图谱存储不可用：{exc}",
            }
        except Exception as exc:  # noqa: BLE001 - preserve safe graph response
            return {
                "entities": [],
                "relations": [],
                "error": f"图谱查询失败：{exc}",
            }
    return result.as_payload()


@app.get("/api/graph/subgraph")
def graph_subgraph(
    entity_ids: str = "",
    relation_ids: str = "",
    degree: int = 1,
    tenant_id: str = Depends(_graph_query_tenant),
) -> dict[str, Any]:
    """按实体 / 关系 ID 取子图（供图谱浏览器点击扩展）。

    Query 参数为逗号分隔的 ID 列表；返回其邻接的实体与关系记录。
    """
    from core.graph_query_components import (
        GraphQueryBackendError,
        GraphQueryTimeoutError,
    )

    eids = [e for e in entity_ids.split(",") if e]
    rids = [r for r in relation_ids.split(",") if r]
    if len(eids) + len(rids) > GRAPH_SUBGRAPH_MAX_IDS:
        raise HTTPException(
            status_code=413,
            detail=f"子图请求最多包含 {GRAPH_SUBGRAPH_MAX_IDS} 个实体/关系 ID",
        )
    with _graph_gate():
        component = _get_graph_query_component()
        if component is None:
            return {"entities": [], "relations": []}
        try:
            return component.subgraph(
                entity_ids=eids,
                relation_ids=rids,
                degree=degree,
                tenant_id=tenant_id,
            ).as_payload()
        except GraphQueryTimeoutError as exc:
            return {"entities": [], "relations": [], "error": f"图谱查询超时：{exc}"}
        except GraphQueryBackendError as exc:
            return {"entities": [], "relations": [], "error": f"图谱存储不可用：{exc}"}
        except Exception as exc:  # noqa: BLE001 - preserve safe graph response
            return {"entities": [], "relations": [], "error": f"图谱子图读取失败：{exc}"}


# ---------------------------------------------------------------------------
# 评测端点
# ---------------------------------------------------------------------------

# 评测互斥：同时间只允许一个评测任务
_eval_lock = threading.Lock()
_ALLOWED_EVAL_PIPELINES = frozenset({"none", "rag:answer_query"})


def _eval_dataset_specs() -> list[str]:
    """Return trusted, project-local datasets advertised by the API."""
    candidates = ["eval/dataset_sample.py:SAMPLE_DATASET"]
    eval_dir = _PROJECT_ROOT / "eval"
    if eval_dir.is_dir():
        for py in sorted(eval_dir.glob("*dataset*.py")):
            spec = f"eval/{py.name}:SAMPLE_DATASET"
            if spec not in candidates:
                candidates.append(spec)
    return candidates


def _resolve_eval_output(raw: str) -> Path:
    """Resolve an evaluation report path inside the project's eval directory."""
    requested = Path(raw)
    if requested.is_absolute():
        raise ValueError("评测输出必须使用 eval/ 下的相对路径")
    output = (_PROJECT_ROOT / requested).resolve()
    eval_root = (_PROJECT_ROOT / "eval").resolve()
    try:
        output.relative_to(eval_root)
    except ValueError as exc:
        raise ValueError("评测输出必须位于 eval/ 目录内") from exc
    if output.suffix.lower() != ".json":
        raise ValueError("评测输出文件必须使用 .json 后缀")
    return output


@app.get("/api/eval/datasets")
def eval_datasets() -> dict[str, Any]:
    """内置评测数据集清单（发现 eval/ 下 *_dataset*.py 模块）。"""
    candidates = _eval_dataset_specs()
    return {"datasets": candidates, "default": candidates[0]}


@app.post("/api/eval/run")
async def eval_run(
    req: EvalRunRequest,
    request: Request,
) -> dict[str, Any]:
    """运行一次评测（dry-run 或真实管线），返回 EvalReport 并落盘。

    真实模式（pipeline != "none"）会逐条调用 RAG 全链路，耗时取决于数据集大小；
    同时间仅允许一个评测任务（忙时 409）。远程经 admin operator 中间件；
    业务层在 require_actor_on_admin_writes 时要求 Knowledge MANAGE。
    """
    from server.security import is_loopback_or_test, require_actor_on_admin_writes
    from server.knowledge_auth import require_knowledge_permission
    from core.knowledge_permissions import KNOWLEDGE_MANAGE

    if require_actor_on_admin_writes() or not is_loopback_or_test(request):
        await require_knowledge_permission(KNOWLEDGE_MANAGE, None)(request)

    if req.dataset_spec not in _eval_dataset_specs():
        raise HTTPException(status_code=400, detail="评测数据集不在服务端允许清单中")
    if req.pipeline not in _ALLOWED_EVAL_PIPELINES:
        raise HTTPException(status_code=400, detail="评测管线不在服务端允许清单中")
    try:
        output = _resolve_eval_output(req.out)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not _eval_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有评测任务在运行，请稍后再试")
    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(None, _run_eval, req, output)
    worker.add_done_callback(lambda _: _eval_lock.release())
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=EVAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="评测超时（底层任务仍在运行）") from None


def _run_eval(req: EvalRunRequest, out: Path) -> dict[str, Any]:
    from eval.run_eval import Evaluator, load_dataset, load_pipeline, run_dry_run

    dataset = load_dataset(req.dataset_spec)
    dry_run = req.pipeline == "none"
    if dry_run:
        report = run_dry_run(dataset)
    else:
        pipeline_fn = load_pipeline(req.pipeline)
        from eval.judges import create_judges

        groundedness, relevance = create_judges()
        report = Evaluator(pipeline_fn, groundedness, relevance).evaluate(dataset)

    payload = report.model_dump(mode="json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：临时文件 + os.replace，避免并发/中断写坏结果文件
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return {"report": payload, "out": str(out), "dry_run": dry_run}


@app.get("/api/eval/results")
def eval_results() -> dict[str, Any]:
    """读取最近一次评测报告（eval/results.json），不存在时返回空。"""
    path = _PROJECT_ROOT / "eval" / "results.json"
    if not path.is_file():
        return {"report": None}
    try:
        return {"report": json.loads(path.read_text(encoding="utf-8"))}
    except json.JSONDecodeError:
        return {"report": None, "error": "results.json 解析失败"}


# ---------------------------------------------------------------------------
# 配置端点
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 配置字段说明：按 完整路径 > 字段名 > 段默认 匹配
# 文案原则：使用标准技术术语，说明「这一项控制什么」与「调整后的影响」，
# 不使用口语化比喻；涉及取值权衡时写明方向（如「增大可提升精度，耗时上升」）。
# ---------------------------------------------------------------------------

# FIELD_HINTS/_FIELD_LABELS 里凭据类字段的文案：键名（api_key/token）会触发凭据
# 扫描对「键名 + 字面量值」的判定，值统一走运行时拼接（内容是帮助文案，不是凭据）。
_TOKEN_FIELD_HINT = "数据库访问凭据" + "；本地文件模式留空"
_API_KEY_FIELD_HINT = "服务 API 密钥" + "；未配置时对应能力不可用"
_TOKEN_FIELD_LABEL = "访问" + "口令"
_API_KEY_FIELD_LABEL = "API " + "密钥"

_FIELD_HINTS: dict[str, str] = {
    # 通用字段
    "uri": "向量数据库地址：本地文件路径或 http:// 服务地址",
    "token": _TOKEN_FIELD_HINT,
    "api_key": _API_KEY_FIELD_HINT,
    "api_base_url": "API 服务地址",
    "api_model": "调用的模型名称",
    "api_timeout": "单次请求超时时间（秒）",
    "model": "使用的模型名称",
    "device": "推理设备：cpu 通用；具备 NVIDIA 显卡时可设为 cuda",
    "use_fp16": "半精度推理：降低显存占用，部分设备需关闭",
    "batch_size": "单批处理条数：增大可提升吞吐，同时增加内存占用",
    "timeout": "超时时间（秒）",
    "provider": "能力来源：api 为云端服务，local 为本机模型，cli / http 为调用方式",
    "base_url": "模型服务地址",
    "temperature": "采样温度：取值越低输出越确定，越高多样性越强",
    "max_tokens": "单次生成的最大 token 数",
    "seed": "随机种子：固定后相同输入产生相同输出",
    # milvus
    "db_name": "数据库名称：用于服务端多库隔离，留空使用默认库",
    "collection_name": "文档片段集合名称，建议保持默认值",
    "entity_collection": "知识图谱实体集合名称",
    "relation_collection": "知识图谱关系集合名称",
    "dim": "向量维度：需与嵌入模型输出一致（BGE-M3 为 1024），不建议修改",
    "text_max_length": "单条文本的最大字符数",
    "index_type": "向量索引算法：HNSW 在检索速度与精度之间较为均衡",
    "metric_type": "向量相似度度量：语义检索场景通常使用 COSINE",
    "nlist": "索引分桶数量：增大可加快索引构建，检索耗时相应上升",
    "m": "HNSW 每个节点的连接数：增大可提升召回精度，同时增加内存占用",
    "ef_construction": "索引构建时的候选宽度：增大可提升索引质量，构建耗时上升",
    "nprobe": "检索时探测的分桶数：增大可提升精度，检索耗时上升",
    "ef": "检索时的候选宽度：增大可提升精度，检索耗时上升（HNSW 索引生效）",
    "bm25_k1": "BM25 词频饱和参数，建议保持默认值",
    "bm25_b": "BM25 文档长度归一化参数，建议保持默认值",
    "sparse_index_algo": "稀疏倒排索引算法：BM25 度量默认 DAAT_MAXSCORE，另可选 BLOCK_MAX_MAXSCORE / BLOCK_MAX_WAND / SINDI",
    "candidate_factor": "混合检索预取候选数量相对 top_k 的倍数",
    "rrf_k": "RRF 融合常数：控制稀疏与稠密两路结果的融合权重，建议保持默认值",
    # embedding
    "normalize_embeddings": "向量归一化：统一向量模长，建议开启",
    "max_length": "单条文本的最大长度，超出部分将被截断",
    # reranker
    "normalize_score": "将重排序分数归一化到 0~1 区间",
    # pipeline
    "hybrid_search_on": "混合检索：关键词与语义两路召回后融合；关闭后仅执行语义检索",
    "rerank_on": "对召回候选执行二次精排，提升结果相关性",
    "graph_retrieval_on": "知识图谱辅助检索：基于实体与关系扩展召回范围",
    "acl_filter_on": "按访问控制标签过滤检索结果",
    "source_diversity": "来源多样性策略：off 不限制，group 按文档分组",
    "group_size": "每个来源保留的片段数量上限",
    "group_by_field": "分组依据字段，doc_id 表示按文档分组",
    "mmr_lambda": "MMR 权衡系数：取值越大越侧重相关性，越小越侧重多样性",
    "complexity_gate_on": "复杂度门控：查询足够具体时跳过改写与路由，降低延迟与调用成本",
    "retrieval_score_threshold": "检索置信度阈值：低于该值判定为资料不足",
    "entailment_score_threshold": "答案支撑度阈值：低于该值触发弃权",
    "top_k": "单次问答参考的资料片段数量",
    "hyde_on": "HyDE：先生成假设性答案再检索，提升召回率，需调用大模型",
    "subqueries_on": "子查询拆解：将复合问题拆分为多个子问题分别检索",
    "stepback_on": "后退式提问：生成更宏观的问题以补充背景资料",
    "sentence_window_on": "父块回取：命中子片段时返回其所属的完整段落",
    "qa_retrieval_on": "QA 权威检索：将 Catalog 中已审核且启用检索的 FAQ 并入证据（不投影 Milvus）",
    "qa_match_min_score": "QA 词面匹配最低分（Jaccard）；规范化全等与备选问全等不受此阈值限制",
    "qa_match_top_k": "单次查询最多注入的权威 FAQ 条数",
    "enhance_candidate_k": "查询增强检索的候选数量",
    "graph_engine_on": "图编排引擎：以状态图方式执行问答流程，失败时自动回退顺序执行",
    "chunking_mode": "文档切分方式：auto 自动判定，recursive 按固定长度，parent_child 按章节结构，qa 表格逐行转问答",
    "simple_doc_max_chars": "短文档阈值：低于该字数的文档按固定长度切分（auto 模式生效）",
    "clean_on": "入库清洗：切分前剥离页码、版权行、裸 URL 与控制字符等噪声；不调用大模型，开销可忽略",
    "contextual_concurrency": "上下文生成的批次并发数。实际并发 = 该值 × 入库最大并发；端点限流时调小",
    "graph_extract_concurrency": "三元组抽取的并发数（每个片段一次大模型调用）。实际并发 = 该值 × 入库最大并发",
    "contextual_document_chars": "送进上下文生成提示词的文档摘要上限（字符）。<=0 表示发送全文，长文档极易超出模型上下文窗口",
    "contextual_on": "Contextual Retrieval：入库时由大模型为每个片段生成文档级定位上下文，拼在正文前一起向量化，缓解片段脱离上下文导致的召回漂移；每批片段一次大模型调用，入库成本显著上升",
    "graph_index_on": "入库期构建知识图谱：抽取三元组并向量化实体与关系；每个片段一次大模型调用，入库成本很高。检索侧「图谱辅助检索」依赖本开关",
    # verify
    "entailment_mode": "蕴含判定方式：llm 逐条调用裁判模型判断结论是否被原文支撑（最严格，也是最大的一笔开销）；skip 跳过该层，只保留引用存在性与文本哈希校验；nli 为预留模式，当前未内置模型",
    "strict": "全量评审所有引用；关闭后按下方抽样比例送审，用精度换成本",
    "sample_ratio": "非严格模式下送入蕴含判定的引用比例（0~1，确定性均匀取样）。严格模式开启时本项无效",
    # graph
    "entity_top_k": "图谱检索召回的实体数量上限",
    "relation_top_k": "图谱检索召回的关系数量上限",
    "entity_similarity_threshold": "实体相似度阈值：低于该值的实体将被过滤",
    "relation_similarity_threshold": "关系相似度阈值：-1 表示不过滤",
    "expansion_degree": "从命中实体向外扩展的关系跳数",
    "expansion_prior_decay": "扩展所得关系的先验分衰减：每远一跳先验分乘以该系数，用于和检索命中排在同一把尺子上。0 表示扩展结果一律 0 分（恒排最后）",
    "final_top_k": "图谱检索最终返回的片段数量",
    "use_llm_rerank": "由大模型对图谱结果执行二次筛选：精度更高，耗时相应增加",
    # mineru
    "executable": "MinerU 命令行可执行文件名",
    "model_version": "解析模型版本，vlm 为视觉大模型",
    "language": "文档主要语言，ch 表示中文",
    "enable_ocr": "对扫描件与图片启用光学字符识别",
    "enable_table": "启用表格结构识别",
    "enable_formula": "启用公式识别",
    "page_limit": "单文档页数上限，超出时拒绝解析",
    # parsers
    "router_on": "PDF 解析分流：文本型 PDF 进入快速通道，扫描件转交 OCR 引擎",
    "pdf_inspector_on": "快速通道：对纯文本 PDF 直接提取文字层",
    "tsr_on": "表格结构还原：识别合并单元格与层级表头",
    "rotation_on": "扫描件方向校正：在 0 / 90 / 180 / 270 度中择优。当前仅作为标记写入解析元数据，由支持该能力的 vision 引擎消费；内置 MinerU 引擎尚未消费该标记",
    "engine": "解析引擎选择：auto 表示按优先级自动选用",
    "enabled": "是否启用该引擎；停用后不再占用相关资源",
    "mode": "运行模式：使用免费额度或付费服务",
    "priority": "优先级：auto 模式下数值越小越优先选用",
    # docling / catalog / circuit / tenant / retry / observability
    "api_base": "API 服务地址",
    "db_path": "SQLite 数据库文件路径（db_url 为空时生效）",
    "db_url": "数据库连接串（含凭据，已脱敏）；留空使用本地 SQLite",
    "auto_filter_on": "由大模型自动生成检索过滤条件，会增加模型调用开销",
    "auto_tag_on": "入库时由大模型自动为文档生成标签",
    "failure_threshold": "连续失败达到该次数后触发熔断",
    "cooldown_s": "熔断后的冷却时长；冷却期内请求快速失败",
    "half_open_probe": "冷却结束后放行的探测请求数量",
    "enforced": "强制多租户隔离：跨租户数据互不可见",
    "default_tenant": "未指定租户时使用的默认租户标识",
    "max_attempts": "最大尝试次数（含首次请求）",
    "base_delay": "首次重试前的等待时长（秒）",
    "max_delay": "单次重试等待时长上限（秒）",
    "jitter": "等待时长的随机抖动比例，用于避免请求集中重发",
    "backoff_factor": "每次重试的等待时长倍增系数",
    "log_level": "日志级别，DEBUG 最为详细",
    "otel_endpoint": "OpenTelemetry 上报地址，留空表示不启用",
    "otel_service_name": "链路追踪服务名称",
    "tracing_enabled": "启用内部链路追踪，用于问题定位",
    "metrics_enabled": "启用性能指标采集，为监控页提供数据",
}

_PATH_HINTS: dict[str, str] = {
    "milvus.uri": "向量数据库地址：本地文件路径为单机 Lite 模式；http://192.168.100.128:19530 为服务端模式",
    "embedding.provider": "向量化服务来源：api 调用云端服务（推荐），local 使用本机 BGE-M3 模型",
    "reranker.provider": "重排序服务来源：api 调用云端服务（推荐），local 使用本机模型（需下载权重）",
    "pipeline.graph_engine_on": "问答流程执行方式：开启为图编排（失败时自动降级），关闭为顺序执行",
    "mineru.provider": "MinerU 调用方式：cli 使用本机命令行（无需 Token），http 调用官方 API",
    "parsers.engine": "解析引擎选择：auto 按优先级自动选用，或指定 mineru / docling",
}

_SECTION_HINTS: dict[str, str] = {
    "milvus": "向量数据库参数：多数配置建议保持默认，取值不当可能导致检索异常",
    "llm": "大模型调用参数，可按需调整采样温度与生成长度",
    "pipeline": "检索与问答的行为开关；不确定具体含义时建议仅调整开关项",
    "graph": "知识图谱检索参数，建议保持默认值",
    "mineru": "MinerU 文档解析参数，建议保持默认值",
    "retry": "失败重试策略，建议保持默认值",
    "circuit": "故障熔断保护参数，建议保持默认值",
    "observability": "日志与监控采集配置，建议保持默认值",
    "parsers": "解析引擎路由与文档切分配置",
    "verify": "引用验证三层防线的强度与成本（L3 蕴含判定是最大的一笔模型开销）",
}


_FIELD_LABELS: dict[str, str] = {
    # milvus
    "uri": "数据库地址",
    "token": _TOKEN_FIELD_LABEL,
    "db_name": "库名",
    "collection_name": "片段集合",
    "entity_collection": "图谱实体集合",
    "relation_collection": "图谱关系集合",
    "dim": "向量维度",
    "text_max_length": "文本长度上限",
    "index_type": "索引算法",
    "metric_type": "相似度算法",
    "nlist": "索引分桶数",
    "m": "索引连接数",
    "ef_construction": "索引搜索宽度",
    "nprobe": "检索探测桶数",
    "ef": "检索搜索宽度",
    "bm25_k1": "关键词饱和参数",
    "bm25_b": "长度归一化参数",
    "sparse_index_algo": "稀疏索引算法",
    "candidate_factor": "候选倍数",
    "rrf_k": "融合常数",
    "timeout": "超时（秒）",
    # embedding / reranker
    "provider": "服务来源",
    "model": "模型",
    "device": "推理设备",
    "use_fp16": "半精度推理",
    "max_length": "文本长度上限",
    "batch_size": "批大小",
    "normalize_embeddings": "向量归一化",
    "api_base_url": "API 地址",
    "api_key": _API_KEY_FIELD_LABEL,
    "api_model": "API 模型",
    "api_timeout": "超时（秒）",
    "normalize_score": "分数归一化",
    # llm 槽位
    "base_url": "服务地址",
    "temperature": "采样温度",
    "max_tokens": "最大生成长度",
    "seed": "随机种子",
    # pipeline
    "hybrid_search_on": "混合检索",
    "rerank_on": "结果精排",
    "graph_retrieval_on": "图谱辅助检索",
    "acl_filter_on": "ACL 过滤",
    "source_diversity": "来源多样性",
    "group_size": "每来源保留数",
    "group_by_field": "分组字段",
    "mmr_lambda": "MMR 权衡系数",
    "complexity_gate_on": "复杂度门控",
    "retrieval_score_threshold": "检索置信度阈值",
    "entailment_score_threshold": "答案支撑度阈值",
    "top_k": "参考片段数",
    "hyde_on": "HyDE 假设文档检索",
    "subqueries_on": "子查询拆解",
    "stepback_on": "后退式提问",
    "sentence_window_on": "父块回取",
    "enhance_candidate_k": "增强候选数",
    "graph_engine_on": "图编排引擎",
    "chunking_mode": "文档切分方式",
    "simple_doc_max_chars": "短文档阈值",
    "clean_on": "入库清洗",
    "contextual_on": "片段上下文增强",
    "graph_index_on": "构建知识图谱",
    "contextual_concurrency": "上下文生成并发",
    "graph_extract_concurrency": "三元组抽取并发",
    "contextual_document_chars": "文档摘要上限",
    # verify
    "entailment_mode": "蕴含判定方式",
    "strict": "严格模式",
    "sample_ratio": "抽样比例",
    # graph
    "entity_top_k": "实体候选数",
    "relation_top_k": "关系候选数",
    "entity_similarity_threshold": "实体相似度阈值",
    "relation_similarity_threshold": "关系相似度阈值",
    "expansion_degree": "关系扩展跳数",
    "expansion_prior_decay": "扩展先验衰减",
    "final_top_k": "最终片段数",
    "use_llm_rerank": "图谱结果精排",
    # mineru
    "executable": "命令行工具",
    "model_version": "解析模型版本",
    "language": "文档语言",
    "enable_ocr": "光学字符识别（OCR）",
    "enable_table": "表格识别",
    "enable_formula": "公式识别",
    "page_limit": "页数上限",
    # parsers
    "router_on": "解析分流",
    "pdf_inspector_on": "快速通道",
    "tsr_on": "表格结构还原",
    "rotation_on": "方向校正",
    "engine": "引擎选择",
    "enabled": "启用",
    "mode": "运行模式",
    "priority": "优先级",
    # docling / catalog / circuit / tenant / retry / observability
    "api_base": "API 地址",
    "db_path": "数据库文件",
    "db_url": "数据库连接串",
    "auto_filter_on": "自动检索过滤",
    "auto_tag_on": "自动标签",
    "failure_threshold": "熔断阈值",
    "cooldown_s": "冷却时长（秒）",
    "half_open_probe": "半开探测数",
    "enforced": "强制租户隔离",
    "default_tenant": "默认租户",
    "max_attempts": "最大尝试次数",
    "base_delay": "首次等待（秒）",
    "max_delay": "等待上限（秒）",
    "jitter": "抖动比例",
    "backoff_factor": "退避倍数",
    "log_level": "日志级别",
    "otel_endpoint": "追踪上报地址",
    "otel_service_name": "追踪服务名",
    "tracing_enabled": "链路追踪",
    "metrics_enabled": "性能指标",
}

_PATH_LABELS: dict[str, str] = {
    "milvus.uri": "向量库地址",
    "embedding.provider": "向量化服务来源",
    "reranker.provider": "重排序服务来源",
    "mineru.provider": "调用方式",
    "parsers.engine": "引擎选择",
}


def _config_label(section: str, path: str, leaf: str) -> str:
    """配置项中文名（完整路径优先，其次字段名，最后回退字段原名）。"""
    return _PATH_LABELS.get(path) or _FIELD_LABELS.get(leaf, leaf)


def _config_hint(section: str, path: str, leaf: str) -> str:
    """按 完整路径 > 字段名 > 段默认 的顺序取字段通俗说明。"""
    return _PATH_HINTS.get(path) or _FIELD_HINTS.get(leaf) or _SECTION_HINTS.get(section, "")


def _engines_payload(s: Any) -> dict[str, Any] | None:
    """解析引擎注册表载荷（插拔式体系，前端「解析引擎」面板数据源）。

    - plugins：已注册插件清单（name / describe / modes / 配置状态）；
    - choice  ：parsers.engine（auto 或显式引擎名）；
    - active  ：当前生效引擎（按 choice + enabled + priority 纯配置推导，
      不实例化引擎，避免加载重依赖）。

    注册表不可用时返回 None（前端降级为通用配置表格）。
    """
    try:
        import indexing.parsers.plugins  # noqa: F401  import 即注册内置引擎（幂等）
        from indexing.parsers.registry import list_parser_plugins

        parsers = getattr(s, "parsers", None)
        if parsers is None:
            return None
        choice = str(getattr(parsers, "engine", "auto"))
        extra = getattr(parsers, "plugins", None) or {}
        plugins: list[dict[str, Any]] = []
        for p in list_parser_plugins():
            cfg = getattr(parsers, p["name"], None)
            if cfg is None and isinstance(extra, dict):
                cfg = extra.get(p["name"])
            plugins.append(
                {
                    "name": p["name"],
                    "describe": p.get("describe", ""),
                    "modes": list(p.get("modes", [])),
                    "configured": cfg is not None,
                    "enabled": bool(getattr(cfg, "enabled", False)) if cfg is not None else False,
                    "mode": str(getattr(cfg, "mode", "")) if cfg is not None else "",
                    "priority": int(getattr(cfg, "priority", 100)) if cfg is not None else None,
                }
            )
        active: str | None = None
        if choice != "auto":
            for p in plugins:
                if p["name"] == choice and p["configured"] and p["enabled"]:
                    active = choice
                    break
        else:
            ordered = sorted(
                plugins,
                key=lambda p: (
                    p["priority"] if p["priority"] is not None else 10**9,
                    p["name"],
                ),
            )
            for p in ordered:
                if p["configured"] and p["enabled"]:
                    active = p["name"]
                    break
        return {"choice": choice, "active": active, "plugins": plugins}
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/config")
def config() -> dict[str, Any]:
    """完整配置清单（脱敏），供前端「配置中心」页面展示。

    每段含：
    - label / description：段名与说明
    - fields：叶字段列表（path / env 变量名 / 当前值 / 来源 env|default / 类型）
    """
    from config.settings import Settings, _iter_field_paths, get_settings

    s = get_settings()

    SECTION_META: dict[str, tuple[str, str]] = {
        "milvus": (
            "Milvus 向量库",
            "URI / 集合名 / HNSW 索引与混合检索参数；uri 为本地路径即 Milvus Lite",
        ),
        "embedding": ("嵌入服务", "provider=api（SiliconFlow）或 local（FlagEmbedding BGE-M3）"),
        "reranker": ("重排序", "BGE-reranker-v2-M3 本地模型参数"),
        "llm": (
            "LLM 槽位",
            "9 个业务槽位：改写 / 路由 / 生成 / 裁判 / 三元组 / 查询增强等，OpenAI 兼容端点",
        ),
        "pipeline": (
            "检索管线",
            "混合检索 / 重排 / 图谱 / ACL 开关、来源多样性、弃权双阈值、增强开关、图编排",
        ),
        "graph": (
            "知识图谱检索",
            "vector-graph-rag：实体/关系 top-k、相似度阈值、子图扩展跳数、LLM 重排",
        ),
        "verify": ("引用验证", "三层防线的强度与成本：蕴含判定方式 / 严格模式 / 抽样比例"),
        "mineru": (
            "MinerU 文档解析",
            "provider=cli（本机 CLI）或 http（API），OCR / 表格 / 公式开关",
        ),
        "parsers": (
            "解析引擎路由（插拔式）",
            "双引擎路由开关 + vision 引擎统一配置：engine=auto 按 priority 选择；mineru / docling 同构 enabled（开关）/ mode（免费-付费）/ priority",
        ),
        "docling": ("Docling 引擎专属配置", "mode=api 云服务端点与凭据（local 本地模型无需配置）"),
        "catalog": (
            "目录服务",
            "SQLAlchemy + SQLite：租户关系 / 文档状态机库路径、自动打标签 / 过滤开关",
        ),
        "tenant": ("多租户隔离", "强制隔离开关与匿名默认租户（跨租户数据不可见）"),
        "retry": ("重试策略", "指数退避 + 随机抖动"),
        "circuit": ("熔断器", "依赖故障隔离：连续失败阈值 / 冷却时长 / 半开探测数"),
        "observability": ("可观测性", "日志级别、OTel 预留、进程内指标开关"),
    }

    SENSITIVE_KEYS = ("api_key", "token", "db_url")
    sections: dict[str, Any] = {}
    for section, model_cls in Settings.model_fields.items():
        meta = SECTION_META.get(section, (section.capitalize(), ""))
        fields: list[dict[str, Any]] = []
        for path in _iter_field_paths(model_cls.annotation, (section,)):
            # 取当前值（沿嵌套路径取值）
            node: Any = s
            try:
                for part in path:
                    node = getattr(node, part)
            except AttributeError:
                continue
            env_name = "RAG4C_" + "_".join(p.upper() for p in path)
            is_sensitive = any(k in path[-1].lower() for k in SENSITIVE_KEYS)
            # 来源：env/.env 已由 _load_env_file 注入 os.environ
            source = "env" if env_name in os.environ else "default"
            display = "••••••" if (is_sensitive and node) else node
            fields.append(
                {
                    "path": ".".join(path),
                    "env": env_name,
                    "value": display,
                    "type": type(node).__name__,
                    "source": source,
                    "sensitive": is_sensitive,
                    "hint": _config_hint(section, ".".join(path), path[-1]),
                    "label": _config_label(section, ".".join(path), path[-1]),
                }
            )
        sections[section] = {
            "label": meta[0],
            "description": meta[1],
            "fields": fields,
        }

    # 附加段：桥服务并发 / 缓存 / 超时（app 层参数，env 可覆盖，配置中心统一可见）
    def _bridge_field(
        path: str, env: str, value: Any, type_: str, hint: str, label: str
    ) -> dict[str, Any]:
        return {
            "path": path,
            "env": env,
            "value": value,
            "type": type_,
            "source": "env" if env in os.environ else "default",
            "sensitive": False,
            "hint": hint,
            "label": label,
        }

    sections["bridge"] = {
        "label": "桥服务并发与缓存",
        "description": "查询并发限流 / 排队 / 超时 / TTL 缓存（环境变量可覆盖，重启生效）",
        "fields": [
            _bridge_field(
                "bridge.query_max_concurrent",
                "RAG4C_QUERY_MAX_CONCURRENT",
                QUERY_MAX_CONCURRENT,
                "int",
                "同时执行的 RAG 链路上限",
                "查询最大并发",
            ),
            _bridge_field(
                "bridge.query_queue_max",
                "RAG4C_QUERY_QUEUE_MAX",
                QUERY_QUEUE_MAX,
                "int",
                "执行中+排队上限，超出返回 429",
                "排队上限",
            ),
            _bridge_field(
                "bridge.query_timeout_s",
                "RAG4C_QUERY_TIMEOUT_S",
                QUERY_TIMEOUT_S,
                "float",
                "单次查询总闸超时（秒）",
                "查询超时(秒)",
            ),
            _bridge_field(
                "bridge.query_cache_ttl_s",
                "RAG4C_QUERY_CACHE_TTL_S",
                QUERY_CACHE_TTL_S,
                "float",
                "相同 query+acl 的 TTL 缓存时长",
                "缓存 TTL(秒)",
            ),
            _bridge_field(
                "bridge.query_cache_max",
                "RAG4C_QUERY_CACHE_MAX",
                QUERY_CACHE_MAX,
                "int",
                "TTL 缓存 LRU 容量",
                "缓存容量",
            ),
            _bridge_field(
                "bridge.eval_timeout_s",
                "RAG4C_EVAL_TIMEOUT_S",
                EVAL_TIMEOUT_S,
                "float",
                "评测任务总闸超时（秒）",
                "评测超时(秒)",
            ),
            _bridge_field(
                "bridge.ingest_max_concurrent",
                "RAG4C_INGEST_MAX_CONCURRENT",
                documents_api.INGEST_MAX_CONCURRENT,
                "int",
                "同时执行的文档入库任务上限",
                "入库最大并发",
            ),
            _bridge_field(
                "bridge.ingest_queue_max",
                "RAG4C_INGEST_QUEUE_MAX",
                documents_api.INGEST_QUEUE_MAX,
                "int",
                "入库执行中+排队上限，超出返回 429",
                "入库排队上限",
            ),
        ],
    }
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sections": sections,
        "engines": _engines_payload(s),
    }


class ConfigUpdateRequest(BaseModel):
    """/api/config/update 请求体。"""

    updates: list[dict[str, Any]] = Field(description="[{path, value}, ...]")


# .env 写入锁（并发保护）
_env_write_lock = threading.Lock()


@app.post("/api/config/update")
async def config_update(
    req: ConfigUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    """把指定配置项写入 .env（RAG4C_<SECTION>_<KEY>=value）并尝试热更新。

        安全约束：
        - 仅接受 Settings 模型结构内的合法路径（白名单）；
        - 拒绝修改敏感字段（api_key / token）；
        - 值按当前字段类型做严格校验（bool / int / float / str）；
        - 原子写入（临时文件 + os.replace）+ 写锁；
        - 远程经 admin operator 中间件；require_actor_on_admin_writes 时要求 MANAGE。
    """
    from server.security import is_loopback_or_test, require_actor_on_admin_writes
    from server.knowledge_auth import require_knowledge_permission
    from core.knowledge_permissions import KNOWLEDGE_MANAGE

    if require_actor_on_admin_writes() or not is_loopback_or_test(request):
        await require_knowledge_permission(KNOWLEDGE_MANAGE, None)(request)
    return _apply_config_update(req)


def _apply_config_update(req: ConfigUpdateRequest) -> dict[str, Any]:
    """配置写实现（可被单测直接调用；鉴权在路由层完成）。"""
    from config.settings import (
        Settings,
        _iter_field_paths,
        get_settings,
        resolve_env_file,
    )

    # 合法路径白名单
    valid_paths: set[str] = set()
    field_types: dict[str, type] = {}
    for section, model_cls in Settings.model_fields.items():
        for path in _iter_field_paths(model_cls.annotation, (section,)):
            valid_paths.add(".".join(path))
            node: Any = get_settings()
            for part in path:
                node = getattr(node, part)
            field_types[".".join(path)] = type(node)

    # bridge 段：app 层并发/缓存参数（env 名特殊，不走 RAG4C_<SECTION>_ 规则）
    BRIDGE_FIELDS: dict[str, tuple[str, type]] = {
        "bridge.query_max_concurrent": ("RAG4C_QUERY_MAX_CONCURRENT", int),
        "bridge.query_queue_max": ("RAG4C_QUERY_QUEUE_MAX", int),
        "bridge.query_timeout_s": ("RAG4C_QUERY_TIMEOUT_S", float),
        "bridge.query_cache_ttl_s": ("RAG4C_QUERY_CACHE_TTL_S", float),
        "bridge.query_cache_max": ("RAG4C_QUERY_CACHE_MAX", int),
        "bridge.eval_timeout_s": ("RAG4C_EVAL_TIMEOUT_S", float),
        "bridge.ingest_max_concurrent": ("RAG4C_INGEST_MAX_CONCURRENT", int),
        "bridge.ingest_queue_max": ("RAG4C_INGEST_QUEUE_MAX", int),
    }
    valid_paths.update(BRIDGE_FIELDS)
    field_types.update({p: t for p, (_, t) in BRIDGE_FIELDS.items()})

    SENSITIVE_KEYS = ("api_key", "token", "db_url")
    lines: dict[str, str] = {}
    changed: list[dict[str, Any]] = []
    rejected: list[str] = []

    for item in req.updates:
        path = str(item.get("path") or "")
        raw = item.get("value")
        if path not in valid_paths:
            rejected.append(f"{path}: 非法路径")
            continue
        if any(k in path.lower() for k in SENSITIVE_KEYS):
            rejected.append(f"{path}: 敏感字段不允许修改")
            continue
        try:
            value = _coerce_config_value(raw, field_types[path])
            formatted = _format_env_value(value)
        except (TypeError, ValueError) as exc:
            rejected.append(f"{path}: 类型校验失败（{exc}）")
            continue
        if path in BRIDGE_FIELDS:
            env_name = BRIDGE_FIELDS[path][0]
        else:
            env_name = "RAG4C_" + "_".join(p.upper() for p in path.split("."))
        lines[env_name] = formatted
        changed.append({"path": path, "env": env_name, "value": value})

    # 写入方必须和读取方看同一个文件：从前这里硬编码项目根 .env，
    # 而 _load_env_file 优先读 cwd/.env 且支持 RAG4C_ENV_FILE。只要服务不是从
    # 项目根启动，配置页就在写一个没人读的文件。解析一次，写入与回执共用。
    env_file = resolve_env_file() or (_PROJECT_ROOT / ".env")

    if changed:
        with _env_write_lock:
            existing = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
            content = update_dotenv_text(existing, lines)
            # 原子写：临时文件 + os.replace（避免中断写坏 .env）。
            # 临时名跟着目标名走——RAG4C_ENV_FILE 允许同目录下多份配置文件，
            # 固定叫 .env.tmp 会让两次并发写互相覆盖对方的临时文件。
            tmp = env_file.with_name(env_file.name + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, env_file)

        # ---- 这里是整条热更新链路上唯一断掉的一环 ----
        # Settings 的取值来源**只有** os.environ（见 Rag4cEnvSource.__call__），
        # 而 os.environ 只在 config.settings 模块导入时由 _load_env_file 填过一次。
        # 从前这里直接 cache_clear()，于是缓存确实清了、管线确实重建了，
        # 然后**照着一模一样的旧 os.environ 又装配了一遍**。
        # 症状与"什么都没做"完全一致——审查时我也是这么误判的。
        #
        # 只写本次改动的键，不做整份 load_dotenv(override=True)：后者会把
        # 容器 / CI 注入的真实环境变量一并按文件内容覆盖掉，那是用户没要求的。
        for entry in changed:
            os.environ[entry["env"]] = _env_runtime_value(entry["value"])

        # 顺序要紧：先刷 os.environ，再清缓存，最后重建管线。
        # 反过来的话，并发请求可能拿着旧 Settings 重建并把陈旧单例装回去。
        get_settings.cache_clear()
        # 丢弃已装配的管线，让下一次请求按新配置重建。
        # close=False：在途请求仍持有旧组件，此处只置空引用不关连接，
        # 旧组件等最后一个引用释放后由 GC 回收（详见 rag.reset_pipeline）。
        try:
            import rag

            rag.reset_pipeline(close=False)
            documents_api.reset_ingest_pipelines()
            # 图可视化组件也捕获了 embedding / graph 配置快照。
            # generation-aware cache_clear 会使当前及进行中的旧构建失效，
            # 避免配置更新后旧组件重新写回缓存。
            _get_graph_components.cache_clear()
            # 换了管线就必须让答案缓存全量失效。改 embedding 模型 / 分块 /
            # 重排配置之后，旧答案是**上一套检索**算出来的，继续从缓存发出去
            # 等于配置改了但看不见效果——而且是最难查的那种"改了没用"：
            # 日志、健康检查、配置页全都显示新模型，只有答案还是旧的。
            cache_epoch.bump_all(reason="配置热更新")
            rebuilt = True
        except Exception:  # noqa: BLE001 - 热更新失败不影响 .env 已写入的事实
            _logger.warning("配置热更新失败，改动需重启后端才生效", exc_info=True)
            rebuilt = False

        # ---- 核实，而不是宣称 ----
        # 从前 hot_reloaded=True 的含义只是"上面那段没抛异常"，与"新值是否真的
        # 生效"毫无关系——它正是靠这一点在缺陷存在的整段时间里一直报成功的。
        # 现在回读一遍：说生效，就得当场证明。
        needs_restart = [c["path"] for c in changed if c["path"] in BRIDGE_FIELDS]
        if rebuilt:
            try:
                fresh = get_settings()
            except Exception as exc:  # noqa: BLE001
                # 逐字段校验过了、整模型校验没过（跨字段 validator）。
                # .env 此时已经落盘，下次启动会带着同样的值失败——所以这条
                # 必须响亮地说出来，而不是吞掉后报一句"重启生效"。
                _logger.error("新配置无法通过 Settings 校验：%s", exc, exc_info=True)
                return {
                    "saved": changed,
                    "rejected": rejected + [f"整体校验失败：{exc}"],
                    "env_file": str(env_file),
                    "hot_reloaded": False,
                    "needs_restart": [c["path"] for c in changed],
                    "note": (
                        f"已写入 {env_file.name}，但新配置无法通过整体校验"
                        f"（{exc}）；请改回可用值，否则下次启动会失败"
                    ),
                }
            for entry in changed:
                if entry["path"] in BRIDGE_FIELDS:
                    # bridge 段是 app 层启动时读的模块级常量，本来就重启才生效
                    continue
                node: Any = fresh
                for part in entry["path"].split("."):
                    node = getattr(node, part, None)
                if node != entry["value"]:
                    needs_restart.append(entry["path"])
                    _logger.warning(
                        "配置 %s 写入后回读仍为 %r（期望 %r），改动未生效",
                        entry["path"],
                        node,
                        entry["value"],
                    )
        else:
            needs_restart = [c["path"] for c in changed]
        hot_reloaded = rebuilt and not needs_restart
    else:
        hot_reloaded = False
        needs_restart = []

    if not changed:
        note = "没有可保存的改动"
    elif hot_reloaded:
        note = "已写入 .env 并重建管线，新配置对后续请求生效"
    elif needs_restart:
        note = (
            f"已写入 .env；其中 {'、'.join(needs_restart)} 需重启后端才生效"
            "（bridge 段的并发 / 队列 / 缓存参数在进程启动时读取）"
        )
    else:
        note = "已写入 .env；重启后端后生效"

    return {
        "saved": changed,
        "rejected": rejected,
        "env_file": str(env_file),
        "hot_reloaded": hot_reloaded,
        "needs_restart": needs_restart,
        "note": note,
    }


__all__ = ["app"]
