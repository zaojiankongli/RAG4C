"""结构化日志（query_id 贯穿）+ OpenTelemetry 可选导出（惰性导入）。

- :func:`setup_logging`：按 ``config.settings.ObservabilitySettings`` 配置
  根 logger，Handler 格式化器自动附加 ``[query_id]``（从 contextvar 读取，
  无绑定会话时显示 ``-``）；幂等，重复调用不会叠加 Handler。
- :func:`get_logger`：返回 ``rag4c.<name>`` 命名空间的 logger。
- :func:`bind_query_id` / :func:`unbind_query_id`：会话级查询 ID 绑定
  （contextvar，asyncio 并发下互不串扰）。
- :func:`init_otel`：惰性导出器。仅当 ``otel_endpoint`` 非空时尝试
  ``import opentelemetry.*``；导入失败或未配置则降级 no-op 并返回 False，
  成功返回 True。实际 export 为占位 stub（用 logging 记录），不真正连网
  ——环境无 OTel 后端。
- :func:`setup_observability`：一键入口 = setup_logging + init_otel +
  注册 tracing span observer（span 记录时同步进 core.metrics，
  key 为 ``span.<name>``）。

本模块顶层不 import opentelemetry / langfuse 等第三方库，
opentelemetry 仅在 :func:`init_otel` 内惰性尝试导入，可离线导入。
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Optional

# 当前查询 ID：供日志格式化器读取，无绑定会话时为 "-"
_query_id_var: ContextVar[str] = ContextVar("rag4c_query_id", default="-")

# 日志格式：日期时间 [query_id] 级别 名称: 消息
_LOG_FORMAT = "%(asctime)s [%(query_id)s] %(levelname)s %(name)s: %(message)s"


class _QueryIdFormatter(logging.Formatter):
    """格式化前把当前 contextvar 中的 query_id 注入日志记录。"""

    def format(self, record: logging.LogRecord) -> str:
        record.query_id = _query_id_var.get()
        return super().format(record)


class _QueryIdHandler(logging.StreamHandler):
    """带 ``[query_id]`` 格式化的 StreamHandler（同时用于幂等检测）。"""

    def __init__(self, stream: Optional[Any] = None) -> None:
        super().__init__(stream if stream is not None else sys.stderr)
        self.setFormatter(_QueryIdFormatter(_LOG_FORMAT))


# init_otel 的幂等记忆：None=未初始化，False=no-op 降级，True=已启用
_otel_initialized: Optional[bool] = None


def setup_logging(settings: Any) -> None:
    """按 ObservabilitySettings 配置根 logger（幂等）。

    - 根 logger 级别设为 ``settings.log_level``（如 "INFO"）。
    - 若根 logger 尚无 :class:`_QueryIdHandler`，则添加一个（stderr 输出，
      格式含 ``[query_id]``）；重复调用不会叠加 Handler。
    - 若根 logger 已存在其他 Handler（如既有冒烟脚本的 basicConfig），
      不增不减，避免重复输出破坏既有行为。

    Args:
        settings: :class:`config.settings.ObservabilitySettings` 实例，
            或任何含 ``log_level`` 字段的对象。
    """
    level_name = str(getattr(settings, "log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, _QueryIdHandler) for h in root.handlers):
        if not root.handlers:
            root.addHandler(_QueryIdHandler())
        else:
            # 根 logger 已有外部 Handler：不动它们，避免日志重复输出
            logging.getLogger(__name__).debug(
                "根 logger 已存在其他 Handler，跳过 [query_id] Handler 安装"
            )


def get_logger(name: str) -> logging.Logger:
    """返回 ``rag4c.<name>`` 命名空间的 logger（便于统一过滤）。"""
    return logging.getLogger(f"rag4c.{name}")


@contextmanager
def bind_query_id(query_id: str) -> Iterator[None]:
    """绑定查询 ID 到当前上下文（contextvar），退出时自动恢复。

    Usage:
        with bind_query_id("query-123"):
            logger.info("开始检索")   # 日志中出现 [query-123]
    """
    token: Token = _query_id_var.set(query_id)
    try:
        yield
    finally:
        _query_id_var.reset(token)


@contextmanager
def unbind_query_id() -> Iterator[None]:
    """清除当前上下文绑定的查询 ID（恢复为 ``-``），退出时自动恢复。

    用于局部遮盖外层已绑定的 query_id（如后台任务日志不希望串 ID）。
    """
    token: Token = _query_id_var.set("-")
    try:
        yield
    finally:
        _query_id_var.reset(token)


def current_query_id() -> str:
    """返回当前上下文的查询 ID（无绑定会话时为 ``-``）。"""
    return _query_id_var.get()


def init_otel(settings: Any) -> bool:
    """惰性初始化 OpenTelemetry 导出器（占位 stub），幂等。

    - ``settings.otel_endpoint`` 为空 -> 不启用，返回 False。
    - opentelemetry 不可导入 -> 降级 no-op（记 warning），返回 False。
    - 成功 -> 标记启用并返回 True；export 用 logging 记录占位，
      不真正连网（环境无 OTel 后端）。

    Args:
        settings: :class:`config.settings.ObservabilitySettings` 实例，
            或任何含 ``otel_endpoint`` / ``otel_service_name`` 字段的对象。

    Returns:
        bool: 是否启用 OTel 导出。
    """
    global _otel_initialized
    if _otel_initialized is not None:
        return _otel_initialized
    endpoint = getattr(settings, "otel_endpoint", None)
    if not endpoint:
        _otel_initialized = False
        return False
    try:
        # 惰性导入：opentelemetry 未安装时走 ImportError 降级分支
        from opentelemetry import trace  # noqa: F401
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
        from opentelemetry.sdk.trace.export import (  # noqa: F401
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        get_logger(__name__).warning(
            "opentelemetry 未安装，OTel 导出降级为 no-op（endpoint=%s）", endpoint
        )
        _otel_initialized = False
        return False
    # 占位导出：不真正连网（环境无 OTel 后端），仅用日志记录启用状态
    service = getattr(settings, "otel_service_name", "rag4c")
    get_logger(__name__).info(
        "OTel 导出已启用（占位 stub，service=%s, endpoint=%s）", service, endpoint
    )
    _otel_initialized = True
    return True


def _observe_span(name: str, duration_ms: float) -> None:
    """span -> 进程内指标的同步回调（``span.<name>`` 耗时直方图）。

    **必须是模块级函数，不能是闭包。** 这里踩过一次很贵的坑：原来它定义在
    ``_register_span_observer`` 内部，而 ``register_span_observer`` 的去重是
    ``if fn not in _span_observers`` ——闭包每次调用都是一个新函数对象，
    身份永不相等，去重形同虚设。偏偏 ``answer_query`` 每次问答都调一遍
    ``setup_observability``（rag.py:502，注释还写着"幂等"），于是观察者列表
    **每问一次就长一个**。

    后果不是"多占点内存"这么轻：第 N 次问答的每个 span 要回调 N 个观察者，
    单次开销随已服务请求数线性增长，整体是 O(n²)。实测 920 次问答后，
    9 个阶段一共记了 43 万条指标（≈ 920 × 平均 460 个观察者），吞吐从
    21.8 QPS 一路衰减到 6.1 QPS，且不会自愈——只有重启才能恢复。这正是
    压测里"每轮都比上一轮慢"的真凶。

    改成模块级函数后身份恒定，去重真正生效，注册多少次都只有一个。
    """
    from core.metrics import get_metrics

    try:
        get_metrics().observe(f"span.{name}", duration_ms)
    except Exception:
        # 指标埋点失败不得影响追踪主流程
        pass


def _register_span_observer() -> None:
    """注册 tracing span observer：span 记录时同步进进程内指标。

    key 为 ``span.<name>``（耗时毫秒直方图）；埋点异常被吞掉，
    不影响追踪主流程；注册按函数身份幂等（重复调用不叠加）。
    """
    from core.tracing import register_span_observer

    register_span_observer(_observe_span)


def setup_observability(settings: Any) -> None:
    """一键初始化可观测性：日志 + OTel + 追踪开关 + span observer。

    依次执行：
    1. :func:`setup_logging`（幂等）
    2. :func:`init_otel`（惰性，缺失依赖自动降级）
    3. 按 ``settings.tracing_enabled`` 设置内部链路追踪总开关；关闭后
       ``current_trace()`` 全程返回 None，各阶段 span 记录与下面的
       span->metrics 同步一并短路（零成本）。
    4. 注册 span observer（``settings.metrics_enabled=True`` 时），
       span 完成即同步进 :func:`core.metrics.get_metrics`，
       key 为 ``span.<name>``。

    Args:
        settings: :class:`config.settings.ObservabilitySettings` 实例。
    """
    setup_logging(settings)
    init_otel(settings)
    from core.tracing import set_tracing_enabled

    set_tracing_enabled(bool(getattr(settings, "tracing_enabled", True)))
    if getattr(settings, "metrics_enabled", True):
        _register_span_observer()


__all__ = [
    "setup_logging",
    "get_logger",
    "bind_query_id",
    "unbind_query_id",
    "current_query_id",
    "init_otel",
    "setup_observability",
]
