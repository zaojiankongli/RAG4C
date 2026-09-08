"""深度健康探活（/api/health 数据源）。

三级状态与组件明细：

- status: ok | degraded | down
  - ok       关键组件全部就绪
  - degraded 部分组件不可用（服务仍可对外，能力受限，如 Milvus 挂 / 熔断打开）
  - down     核心依赖全部不可用
- components: 每组件 {status, detail, latency_ms?}
  - milvus：真实连接 + 集合存在性 + 行数（只读探测，无创建副作用）
  - embedder / reranker：provider 与模型配置（不加载模型、不联网）
  - llm 各槽位：配置完整性（不主动发请求，避免消耗额度）
  - circuits：熔断器状态（retrieval / llm.generation）
- 探活结果进程内缓存 5s（PROBE_TTL_S），避免探测风暴；任何探测异常
  只记录、不抛出。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from config.settings import get_settings
from core.circuit import list_circuits
from core.observability import get_logger

_logger = get_logger("server.health")

PROBE_TTL_S = 5.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_probe_inflight: threading.Event | None = None


def _probe_milvus(settings: Any) -> dict[str, Any]:
    """Milvus 只读探活：连接 + 集合存在性 + 行数。

    server 模式 fail-fast：直连 pymilvus（2s 超时、不重试），
    避免 VM 不可达时探活被重试拖慢（曾导致前端误判「离线」）。
    """
    try:
        from core.milvus_client import RagMilvusClient

        t0 = time.perf_counter()
        uri = settings.milvus.uri
        if "://" in uri:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": uri, "timeout": 2.0}
            if settings.milvus.token:
                kwargs["token"] = settings.milvus.token
            raw = MilvusClient(**kwargs)
        else:
            client = RagMilvusClient(settings.milvus)
            raw = client._ensure_client()
        has_chunks = bool(raw.has_collection(settings.milvus.collection_name))
        row_count = None
        if has_chunks:
            try:
                stats = raw.get_collection_stats(settings.milvus.collection_name)
                row_count = int(stats.get("row_count", 0))
            except Exception:
                row_count = None
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if has_chunks:
            rows = f" 行数 {row_count}" if row_count is not None else " 行数不可用"
            detail = f"uri={settings.milvus.uri} 集合 {settings.milvus.collection_name}{rows}"
        else:
            detail = (
                f"uri={settings.milvus.uri} 集合 {settings.milvus.collection_name} "
                "不存在（首次入库时自动创建）"
            )
        return {
            "status": "ok" if has_chunks else "empty",
            "detail": detail,
            "latency_ms": latency_ms,
            "row_count": row_count,
        }
    except Exception as exc:  # noqa: BLE001 - 探活失败只记录
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _probe_llm_slots(settings: Any) -> dict[str, Any]:
    """LLM 槽位配置完整性（不发起真实请求）。"""
    # 遍历 LLM_SLOT_NAMES 而不是 model_fields：后者还包含 providers
    # （具名提供商的 dict，不是槽位），于是它会被当成"第 12 个槽位"，
    # getattr 取不到 base_url/model 就报 unconfigured，对外表现为永远的
    # "11/12 槽位已配置"——一个永远修不好的假故障。
    from config.settings import LLM_SLOT_NAMES

    slots: dict[str, Any] = {}
    ok_count = 0
    total = 0
    for name in LLM_SLOT_NAMES:
        total += 1
        slot = getattr(settings.llm, name)
        configured = bool(getattr(slot, "base_url", None) or getattr(slot, "model", None))
        if configured:
            ok_count += 1
        slots[name] = {
            "status": "ok" if configured else "unconfigured",
            "model": getattr(slot, "model", ""),
            "base_url": getattr(slot, "base_url", "") or "(默认端点)",
        }
    return {
        "status": "ok" if ok_count else "unconfigured",
        "detail": f"{ok_count}/{total} 槽位已配置",
        "slots": slots,
    }


def _probe_embedder(settings: Any) -> dict[str, Any]:
    try:
        from core.embedding import create_embedder

        create_embedder(settings.embedding)
        return {
            "status": "ok",
            "detail": (
                f"provider={settings.embedding.provider} model={settings.embedding.model}"
                f"（构造成功，模型懒加载）"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _probe_reranker(settings: Any) -> dict[str, Any]:
    try:
        from core.reranker import create_reranker

        create_reranker(settings.reranker)
        return {
            "status": "ok",
            "detail": f"model={settings.reranker.model}（构造成功，模型懒加载）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}


def probe_components(force: bool = False) -> dict[str, Any]:
    """深度探活（结果缓存 PROBE_TTL_S，force=True 跳过缓存）。

    Returns:
        {status, components, circuits, probed_at}；任何组件探测异常只降级
        组件状态，不影响响应。
    """
    global _probe_inflight
    now = time.monotonic()
    with _lock:
        if not force and _cache["payload"] is not None and now - _cache["at"] < PROBE_TTL_S:
            return _cache["payload"]
        if _probe_inflight is not None:
            # 过期瞬间只允许一个真实探测；已有快照时立即返回旧值。
            if _cache["payload"] is not None:
                return _cache["payload"]
            waiter = _probe_inflight
            owner = False
        else:
            waiter = threading.Event()
            _probe_inflight = waiter
            owner = True

    if not owner:
        waiter.wait(timeout=3.0)
        with _lock:
            if _cache["payload"] is not None:
                return _cache["payload"]
        return {
            "status": "down",
            "components": {},
            "circuits": list_circuits(),
            "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    try:
        settings = get_settings()
        components: dict[str, Any] = {
            "milvus": _probe_milvus(settings),
            "embedder": _probe_embedder(settings),
            "reranker": _probe_reranker(settings),
            "llm": _probe_llm_slots(settings),
        }
        circuits = list_circuits()

    # 状态分级：
    # - milvus error / 任一关键熔断打开 -> degraded
    # - milvus error 且 generation 熔断打开 -> down
        milvus_bad = components["milvus"]["status"] == "error"
        generation_open = circuits.get("llm.generation", {}).get("state") == "open"
        retrieval_open = circuits.get("retrieval", {}).get("state") == "open"

        if milvus_bad and generation_open:
            status = "down"
        elif milvus_bad or generation_open or retrieval_open:
            status = "degraded"
        else:
            status = "ok"

        payload = {
            "status": status,
            "components": components,
            "circuits": circuits,
            "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with _lock:
            _cache["at"] = time.monotonic()
            _cache["payload"] = payload
        return payload
    finally:
        with _lock:
            completed = _probe_inflight
            _probe_inflight = None
            if completed is not None:
                completed.set()
