"""进程内性能指标采集器（计数器 / 直方图 / P50/P95/P99 / 错误率）。

为 LangGraph 图编排与各阶段（检索 / 生成 / 验证）提供零依赖的性能埋点：

- :func:`get_metrics`：进程级单例 :class:`MetricsRegistry`。
- :meth:`MetricsRegistry.incr`：计数器（如 ``graph.invoke`` 调用次数）。
- :meth:`MetricsRegistry.observe`：直方图采样（耗时 / 延迟，毫秒），
  内部以蓄水池抽样维护样本列表（上限 :data:`MAX_SAMPLES` 条防内存膨胀），
  并维护 count / sum / min / max。
- :meth:`MetricsRegistry.timing`：上下文管理器，进入计时、退出时
  observe 毫秒耗时。
- :meth:`MetricsRegistry.snapshot`：输出
  ``{count, sum, mean, min, max, p50, p95, p99}``，
  错误指标（名称以 ``.errors`` 结尾）额外输出 ``error_rate``
  （错误计数 ÷ 同名基础指标计数，基础指标缺失时尝试 ``<base>.total``）。

阶段命名约定：
    - ``retrieval.total`` / ``retrieval.errors``：检索阶段
    - ``generate.llm`` / ``generate.llm.errors``：生成（LLM 调用）
    - ``graph.invoke`` / ``graph.invoke.errors``：图编排一次执行
    - ``verify.total`` / ``verify.errors``：验证阶段
    - ``retry.<name>.attempts``：某调用点的重试次数（配合 core.retry）

分位数（P50/P95/P99）使用**最近秩线性插值**手写实现（与 numpy 默认
``method="linear"`` 等价）：numpy 可用时自动委托（纯加速），
缺失时纯 Python 兜底，不构成硬依赖。

本模块零第三方依赖（仅标准库），可离线导入。
"""
from __future__ import annotations

import math
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

# 单指标直方图样本上限：样本满后以蓄水池抽样（第 n 个样本以 MAX_SAMPLES/n
# 概率替换既有样本）继续吸收新值，任意时刻 samples 都是全量历史的无偏
# 随机子集，内存占用恒定 O(MAX_SAMPLES)。
MAX_SAMPLES = 10000

# numpy 可选加速的惰性探测缓存：None=未探测，True/False=探测结果。
# 首次调用 _percentile 时探测一次并缓存，后续快照不再重复 try import
# （即使是确认没有 numpy 的环境，也只白试一次）。
_NUMPY_AVAILABLE: Optional[bool] = None


def _percentile(sorted_samples: list[float], q: float) -> float:
    """最近秩线性插值分位数（numpy ``method="linear"`` 等价，纯 Python 手写）。

    样本数 n 时，秩位置 ``h = q * (n - 1)``，
    在 ``sorted[floor(h)]`` 与 ``sorted[ceil(h)]`` 之间线性插值。
    numpy 可用时委托给 ``np.percentile(method="linear")`` 作为加速路径，
    结果完全一致。

    可用性探测结果缓存于模块级 :data:`_NUMPY_AVAILABLE`，只执行一次。
    """
    global _NUMPY_AVAILABLE
    n = len(sorted_samples)
    if n == 0:
        return 0.0
    if _NUMPY_AVAILABLE is None:
        # 首次调用探测 numpy（可选加速）；结果永久缓存，缺失则走纯 Python
        try:
            import numpy as np  # noqa: F401  可选加速
        except ImportError:
            _NUMPY_AVAILABLE = False
        else:
            _NUMPY_AVAILABLE = True
    if _NUMPY_AVAILABLE:
        # 已确认可用；import 命中 sys.modules 缓存，开销可忽略
        import numpy as np

        return float(np.percentile(sorted_samples, q * 100.0, method="linear"))
    if n == 1:
        return sorted_samples[0]
    h = q * (n - 1)
    lo = int(math.floor(h))
    if lo >= n - 1:
        return sorted_samples[-1]
    frac = h - lo
    return sorted_samples[lo] * (1.0 - frac) + sorted_samples[lo + 1] * frac


def _error_base(key: str) -> Optional[str]:
    """若 key 为 ``<base>.errors`` 形式的错误指标，返回其基础 key（保留标签部分）。

    非错误指标返回 None。
    """
    if "|" in key:
        name, tags = key.split("|", 1)
        if name.endswith(".errors"):
            return f"{name[: -len('.errors')]}|{tags}"
        return None
    if key.endswith(".errors"):
        return key[: -len(".errors")]
    return None


@dataclass
class _Metric:
    """单指标内部状态（仅允许在注册表锁内访问）。

    Attributes:
        count: 记录次数（计数器为调用次数，直方图为采样条数）。
        sum: 数值累加（直方图即总和，计数器即增量累加）。
        min / max: 已观测的最小 / 最大值。
        samples: 蓄水池样本列表（上限 :data:`MAX_SAMPLES` 条），样本满后
            随机替换既有样本，保证任意时刻都是全量历史的无偏子集。
        _sorted_cache: 排序结果缓存；仅当 ``_dirty`` 为 True 时在
            snapshot 中重建，避免连续快照重复全量排序。
        _dirty: 脏标记；record 写入样本后置 True，snapshot 重建缓存后清 False。
    """

    count: int = 0
    sum: float = 0.0
    min: float = math.inf
    max: float = -math.inf
    samples: list[float] = field(default_factory=list)
    _sorted_cache: list[float] = field(default_factory=list, repr=False)
    _dirty: bool = False

    def record(self, value: float) -> None:
        """记录一次观测：更新计数 / 累加 / 极值 / 蓄水池样本。

        样本未满时直接追加；满后以 ``MAX_SAMPLES / count`` 概率随机替换
        既有样本（``random.randrange(count) < MAX_SAMPLES`` 即等价实现），
        使 samples 始终是全部历史样本的无偏随机子集。
        """
        self.count += 1
        self.sum += value
        if value < self.min:
            self.min = value
        if value > self.max:
            self.max = value
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(value)
        else:
            j = random.randrange(self.count)  # 0 <= j < count（count 已含本次）
            if j < MAX_SAMPLES:
                self.samples[j] = value
        self._dirty = True


class MetricsRegistry:
    """线程安全的进程内指标注册表。

    指标标识 = 名称 + 标签：有标签时序列化为 ``name|k1=v1,k2=v2``
    （标签键排序保证确定性）；全局默认标签通过 :meth:`set_tag_defaults`
    设置，显式传入的标签覆盖同键默认值。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}
        self._default_tags: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 标签与标识
    # ------------------------------------------------------------------

    def _key(self, name: str, tags: Optional[dict[str, Any]] = None) -> str:
        """把名称 + 标签（合并默认标签）序列化为指标唯一 key。"""
        merged = dict(self._default_tags)
        if tags:
            merged.update({str(k): str(v) for k, v in tags.items()})
        if not merged:
            return name
        suffix = ",".join(f"{k}={merged[k]}" for k in sorted(merged))
        return f"{name}|{suffix}"

    def set_tag_defaults(self, tags: dict[str, Any]) -> None:
        """合并设置全局默认标签（如 ``{"service": "rag4c"}``），线程安全。

        显式传入的标签优先级高于同名默认标签。
        """
        with self._lock:
            self._default_tags.update({str(k): str(v) for k, v in tags.items()})

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------

    def incr(self, name: str, tags: Optional[dict[str, Any]] = None, value: float = 1.0) -> None:
        """计数器累加（默认 +1），线程安全。

        每次调用同时作为一次 ``value`` 采样记录，便于 snapshot 给出
        计数器的 count/sum/min/max 与 p50/p95/p99（增量分布）。
        """
        self.observe(name, value, tags=tags)

    def observe(self, name: str, value: float, tags: Optional[dict[str, Any]] = None) -> None:
        """直方图采样：记录一次 ``value``（耗时 / 延迟，毫秒），线程安全。"""
        with self._lock:
            key = self._key(name, tags)
            metric = self._metrics.get(key)
            if metric is None:
                metric = _Metric()
                self._metrics[key] = metric
            metric.record(float(value))

    @contextmanager
    def timing(self, name: str, tags: Optional[dict[str, Any]] = None) -> Iterator[None]:
        """上下文管理器：记录代码块执行耗时（毫秒）并 observe。

        Usage:
            with get_metrics().timing("generate.llm"):
                ... 业务 ...
        """
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, (time.perf_counter() - t0) * 1000.0, tags=tags)

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, float]]:
        """导出全部指标快照（线程安全，返回副本）。

        每项结构：``{count, sum, mean, min, max, p50, p95, p99}``；
        错误指标（名称以 ``.errors`` 结尾）额外含 ``error_rate``：
        错误计数 ÷ 同名基础指标计数；基础指标缺失时尝试 ``<base>.total``。
        """
        with self._lock:
            items = list(self._metrics.items())
        # 分母计数只统计非错误指标自身（错误指标的 base key 与其同名，
        # 不能把错误计数混入分母）
        totals: dict[str, int] = {}
        for key, metric in items:
            if _error_base(key) is None:
                totals[key] = totals.get(key, 0) + metric.count
        out: dict[str, dict[str, float]] = {}
        for key, metric in items:
            if metric._dirty:
                # 有新样本写入过：重建排序缓存并清脏；否则复用上次结果
                # （GIL 保证 record 与这里的读写不会并发交错，最多重复排一次）
                metric._sorted_cache = sorted(metric.samples)
                metric._dirty = False
            samples = metric._sorted_cache
            entry: dict[str, float] = {
                "count": float(metric.count),
                "sum": metric.sum,
                "mean": metric.sum / metric.count if metric.count else 0.0,
                "min": metric.min if metric.count else 0.0,
                "max": metric.max if metric.count else 0.0,
                "p50": _percentile(samples, 0.50),
                "p95": _percentile(samples, 0.95),
                "p99": _percentile(samples, 0.99),
            }
            base = _error_base(key)
            if base is not None:
                total = totals.get(base)
                if total is None:
                    total = totals.get(base + ".total", 0)
                entry["error_rate"] = metric.count / total if total > 0 else 0.0
            out[key] = entry
        return out

    def record_outcome(self, scope: str, *, degraded: bool = False, tags: Optional[dict[str, Any]] = None) -> None:
        """记一次调用结果：总数 +（降级时）降级数。

        命名必须贴合 snapshot 的约定：``<scope>.errors`` 结尾才会被自动算成
        ``error_rate``，分母取 ``<scope>.total``。所以这里只写这两个名字，
        "降级率"不需要任何额外计算就能出现在 /api/metrics 里。
        （曾写成 ``<scope>.degraded.errors``，那时 base 变成 ``<scope>.degraded``，
        找不到分母，error_rate 恒为 0 —— 命名不对，指标就是死的。）

        为什么不只记降级数：没有分母就只有"降级了几次"，看不出是 1/10 还是
        1/1000 —— 后者是抖动，前者是故障。
        """
        try:
            self.incr(f"{scope}.total", tags=tags)
            if degraded:
                self.incr(f"{scope}.errors", tags=tags)
        except Exception:  # noqa: BLE001 - 埋点不得影响主链路
            pass


    def reset(self) -> None:
        """清空全部指标（全局默认标签保留），线程安全。"""
        with self._lock:
            self._metrics.clear()


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------

_metrics_singleton: Optional[MetricsRegistry] = None
_singleton_lock = threading.Lock()


def get_metrics() -> MetricsRegistry:
    """返回进程级 :class:`MetricsRegistry` 单例（双重检查锁，线程安全）。"""
    global _metrics_singleton
    if _metrics_singleton is None:
        with _singleton_lock:
            if _metrics_singleton is None:
                _metrics_singleton = MetricsRegistry()
    return _metrics_singleton


__all__ = [
    "MetricsRegistry",
    "get_metrics",
    "MAX_SAMPLES",
]
