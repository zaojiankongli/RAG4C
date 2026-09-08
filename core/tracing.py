"""轻量级链路追踪。

为未来 OpenTelemetry / Langfuse 接入预留的 contextvars 基础设施：

- :class:`TraceContext`：一次查询的追踪上下文，用列表收集 span（name, duration_ms）。
- :func:`current_trace`：读取当前线程/异步上下文的 TraceContext。
- :func:`current_run_observer`：读取当前请求绑定的 RunObserver。
- :func:`trace_session`：上下文管理器，进入时创建 TraceContext，并独立绑定
  trace 与可选 observer，退出时自动解绑。
- :func:`register_span_observer` / :func:`clear_span_observers`：span 观察者
  挂钩，:meth:`TraceContext.add_span` 记录 span 时同步回调
  ``fn(name, duration_ms)``（用于把 span 同步进 core.metrics 等旁路；
  观察者抛出的异常会被吞掉，不影响主流程）。

后续模块（检索 / 生成 / 验证）只需：
    with trace_session(query_id) as trace:
        t0 = time.perf_counter()
        ... 业务 ...
        trace.add_span("retrieval", (time.perf_counter() - t0) * 1000)

未来替换为 OpenTelemetry 时，把 ``add_span`` 内部实现改为创建 span 即可，
调用方 API 不变。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator, Optional

if TYPE_CHECKING:
    from core.run_events import RunObserver

# contextvars 保证 asyncio 并发下各任务的追踪互不串扰
_trace_var: ContextVar[Optional["TraceContext"]] = ContextVar(
    "rag4c_trace_context", default=None
)
_run_observer_var: ContextVar[RunObserver | None] = ContextVar(
    "rag4c_run_observer", default=None
)

# span 观察者列表：add_span 时逐个回调 fn(name, duration_ms)，
# 观察者异常一律吞掉（旁路埋点不得影响追踪主流程）
_span_observers: list[Callable[[str, float], None]] = []

#: 观察者数量告警阈值。取 32 而不是 8：留足正常装配的余量，同时远低于
#: "已经拖慢服务"的量级，够早也够安静。只在恰好等于时告警一次，避免刷屏。
_OBSERVER_WARN_AT = 32


@dataclass
class TraceContext:
    """一次查询的追踪上下文。

    Attributes:
        query_id: 查询标识（如 ``trace-<uuid>``）。
        spans: 已记录的 span 列表，元素为 ``(name, duration_ms)``。
        start_monotonic: 创建时刻的 ``time.monotonic()``，用于计算总耗时。
    """

    query_id: str
    spans: list[tuple[str, float]] = field(default_factory=list)
    start_monotonic: float = field(default_factory=time.monotonic)

    def add_span(self, name: str, duration_ms: float) -> None:
        """记录一个 span（按调用顺序追加），并同步通知 span 观察者。

        观察者签名 ``fn(name: str, duration_ms: float)``
        （见 :func:`register_span_observer`）；观察者抛出的任何异常
        都会被吞掉，不影响主流程（追踪记录始终成功）。
        """
        self.spans.append((name, duration_ms))
        for observer in _span_observers:
            try:
                observer(name, duration_ms)
            except Exception:
                # 旁路观察者（如 metrics 同步）异常不得影响追踪主流程
                pass

    def total_ms(self) -> float:
        """该追踪自创建以来的总耗时（毫秒）。"""
        return (time.monotonic() - self.start_monotonic) * 1000.0

    def as_list(self) -> list[str]:
        """把追踪转成便于写入 QueryResult.traces 的字符串列表。"""
        return [f"{name}:{ms:.2f}ms" for name, ms in self.spans]


def current_trace() -> Optional[TraceContext]:
    """返回当前上下文绑定的 TraceContext（无则返回 None）。"""
    return _trace_var.get()


def current_run_observer() -> RunObserver | None:
    """返回当前上下文绑定的运行事件观察者（无则返回 None）。"""
    return _run_observer_var.get()


# 追踪总开关（由 core.observability.setup_observability 依配置设置）。
# 关闭时 trace_session 不再绑定 TraceContext，current_trace() 全程返回 None，
# 于是各阶段的 span 记录与 span->metrics 同步自然短路（零成本）；独立的
# RunObserver 绑定不受影响，QueryResult.traces 里的处理过程说明也不受影响。
_tracing_enabled: bool = True


def set_tracing_enabled(enabled: bool) -> None:
    """设置追踪总开关（幂等，进程级）。"""
    global _tracing_enabled
    _tracing_enabled = bool(enabled)


def is_tracing_enabled() -> bool:
    """当前是否启用内部链路追踪。"""
    return _tracing_enabled


@contextmanager
def trace_session(
    query_id: str, *, observer: RunObserver | None = None
) -> Iterator[TraceContext]:
    """进入一次追踪会话：创建并绑定 TraceContext，退出时解绑。

    ``set_tracing_enabled(False)`` 时仍返回一个可用的 TraceContext（调用方
    无需分支），但**不绑定** trace 上下文——因此 :func:`current_trace` 返回
    None。RunObserver 使用独立的 contextvar，仍会按请求绑定。

    Usage:
        with trace_session("query-123") as trace:
            trace.add_span("embed", 1.5)
    """
    ctx = TraceContext(query_id=query_id)
    observer_token: Token = _run_observer_var.set(observer)
    try:
        if not _tracing_enabled:
            yield ctx
            return
        trace_token: Token = _trace_var.set(ctx)
        try:
            yield ctx
        finally:
            _trace_var.reset(trace_token)
    finally:
        _run_observer_var.reset(observer_token)


def register_span_observer(fn: Callable[[str, float], None]) -> None:
    """注册 span 观察者：每次 :meth:`TraceContext.add_span` 都会调用。

    Args:
        fn: 回调，签名 ``fn(name: str, duration_ms: float)``。
            重复注册同一函数对象会被忽略（幂等），便于
            :func:`core.observability.setup_observability` 多次调用。

    Usage:
        def sync_to_metrics(name: str, duration_ms: float) -> None:
            get_metrics().observe(f"span.{name}", duration_ms)
        register_span_observer(sync_to_metrics)
    """
    if fn not in _span_observers:
        _span_observers.append(fn)
        # 观察者数量是有上界的：它们由模块装配代码注册，正常不超过个位数。
        # 一旦越过阈值，几乎可以断定是**注册泄漏**——最典型的写法是把回调
        # 定义成闭包再反复注册，闭包每次都是新对象，上面那行 in 判断永远
        # 不成立（core/observability.py 就这么干过一次）。
        #
        # 这种泄漏本身不报错，只是让每个 span 的回调开销随注册次数线性增长，
        # 表现为"服务越跑越慢、重启就好"，极难定位。所以这里宁可吵一点：
        # 与其让它安静地把进程拖死，不如在日志里留一行能直接定位的证据。
        if len(_span_observers) == _OBSERVER_WARN_AT:
            import logging

            logging.getLogger(__name__).warning(
                "span 观察者已达 %d 个，疑似注册泄漏（回调用了闭包？）。"
                "最近注册：%r",
                _OBSERVER_WARN_AT,
                getattr(fn, "__qualname__", fn),
            )


def clear_span_observers() -> None:
    """清空全部 span 观察者（测试 / 卸载时使用）。"""
    _span_observers.clear()


__all__ = [
    "TraceContext",
    "current_trace",
    "current_run_observer",
    "trace_session",
    "set_tracing_enabled",
    "is_tracing_enabled",
    "register_span_observer",
    "clear_span_observers",
]
