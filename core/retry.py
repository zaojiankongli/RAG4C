"""通用重试工具（指数退避 + 随机抖动 + 可重试异常分类）。

为 LangGraph 图编排 / LLM / Milvus 等易瞬态失败调用提供兜底：

- :class:`RetryPolicy`：重试策略（次数 / 退避 / 抖动 / 可重试异常分类），
  可由 :meth:`RetryPolicy.from_settings` 从 ``config.settings.RetrySettings`` 构造。
- :func:`retry_call`：函数级重试入口。
- :func:`retryable`：装饰器工厂，把任意函数包装为自动重试版本。

约定：

- 只在异常匹配 ``retry_on`` 时重试，其余异常立即原样抛出。
- 退避公式 ``delay = min(base * factor ** (attempt - 1), max)``，
  再叠加均匀抖动 ``[-jitter * delay, +jitter * delay]`` 防止惊群。
- 耗尽 ``max_attempts`` 次后**原样抛出**最后一次异常（不包装，
  调用方按原有异常类型处理即可），重试过程可通过
  :func:`retry_stats` 观测（线程安全计数器）。

阶段命名约定（配合 core.metrics）：``retry.<name>.attempts``。

本模块零第三方依赖（仅标准库），可离线导入。
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# 进程级重试统计（线程安全计数器，供观测 / 测试 / 埋点使用）
# ---------------------------------------------------------------------------

_stats_lock = threading.Lock()
_stats: dict[str, int] = {
    "total_attempts": 0,
    "total_failures": 0,
    "total_retries": 0,
    # 因预算不足而**主动放弃**的重试。与 total_failures 分开计，是因为两者
    # 该引出的动作不同：失败多说明上游有问题，放弃多说明超时预算配小了。
    "total_deadline_aborts": 0,
}


def _bump_stats(key: str) -> None:
    """线程安全地累加一项重试统计。"""
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + 1


def retry_stats() -> dict[str, int]:
    """返回进程级重试统计快照（线程安全）。

    统计项：
    - ``total_attempts``：目标函数被调用的总次数
    - ``total_failures``：抛异常的总次数
    - ``total_retries``  ：实际发生的重试（sleep）次数
    - ``total_deadline_aborts``：因剩余预算不足而放弃重试的次数
    """
    with _stats_lock:
        return dict(_stats)


def reset_retry_stats() -> None:
    """清空进程级重试统计（测试 / 统计周期切换时使用）。"""
    # 按现有键归零，而不是照抄一份键名清单：抄的那份必然会漏掉后加的项
    # （total_deadline_aborts 刚加上时就漏了），漏掉的键在 reset 之后彻底
    # 消失，之后 retry_stats() 里就再也看不到它——一个静默的观测盲区。
    with _stats_lock:
        for key in _stats:
            _stats[key] = 0


# ---------------------------------------------------------------------------
# 重试策略
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """重试策略参数。

    Attributes:
        max_attempts: 最大尝试次数（含首次），1 表示不重试。
        base_delay: 首次失败后的退避基准（秒）。
        max_delay: 单次退避上限（秒）。
        jitter: 随机抖动比例（0~1），实际延迟在
            ``[delay * (1 - jitter), delay * (1 + jitter)]`` 内均匀取值。
        backoff_factor: 指数退避倍数
            （``delay = min(base * factor ** (attempt - 1), max)``）。
        retry_on: 可重试异常类型元组，仅匹配其中的异常才会触发重试。
        min_attempt_s: 「值得再试一次」所需的最小剩余预算（秒）。仅在调用方
            用 :class:`deadline` 声明了截止时间时才起作用。默认 1 秒——它不是
            对某次调用真实耗时的估计（那不可知），而是一条「剩这么点时间就
            别折腾了」的下限；设成 0 会退化成「哪怕只剩 1 毫秒也再发一次」。
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: float = 0.1
    backoff_factor: float = 2.0
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    min_attempt_s: float = 1.0

    def __post_init__(self) -> None:
        """规范化参数：允许 ``retry_on`` 传单个异常类；抖动比例限定 [0, 1]。"""
        if isinstance(self.retry_on, type):
            self.retry_on = (self.retry_on,)
        if self.jitter < 0.0:
            self.jitter = 0.0
        elif self.jitter > 1.0:
            self.jitter = 1.0

    @classmethod
    def from_settings(cls, settings: "Any") -> "RetryPolicy":
        """从 ``config.settings.RetrySettings`` 构造（鸭子类型读取，零硬依赖）。

        Args:
            settings: :class:`config.settings.RetrySettings` 实例，
                或任何含同名字段的对象。

        Usage:
            from config.settings import get_settings
            policy = RetryPolicy.from_settings(get_settings().retry)
        """
        return cls(
            max_attempts=int(settings.max_attempts),
            base_delay=float(settings.base_delay),
            max_delay=float(settings.max_delay),
            jitter=float(settings.jitter),
            backoff_factor=float(settings.backoff_factor),
        )


# ---------------------------------------------------------------------------
# 请求级截止时间（thread-local）
# ---------------------------------------------------------------------------
#
# 解决的是一条纯算术上的自相矛盾：LLM 单次 timeout=120s、max_attempts=3，
# 最坏耗时 120×3 + 退避 ≈ 361 秒，而 HTTP 总闸 QUERY_TIMEOUT_S 只有 240 秒。
# 也就是说上游一旦开始超时，工作线程必然会在客户端早已放弃之后，继续为一个
# 没人等的答案空转两分钟——过载时这正是把服务拖垮的那部分负载。
#
# 用 thread-local 而不是 contextvars：整条 RAG 链路是同步代码，在
# ``run_in_executor`` 派发的那一个线程里从头跑到尾，thread-local 天然贴合。
# 而 contextvars 不会自动跨 ``run_in_executor`` 传播，用它反倒要在每个派发点
# 手动 copy_context，得不偿失。
#
# 用 thread-local 也意味着**不需要改那 14 个调用点**：在线程入口设一次，
# 底下所有 retry_call 自动受约束。这与 provider_ref 那次的教训一致——
# 需要每个调用点记得的东西，迟早会被忘掉。

_deadline_state = threading.local()


def set_deadline(monotonic_at: float | None) -> None:
    """设置当前线程的截止时刻（``time.monotonic()`` 坐标系），None 表示不限。"""
    _deadline_state.at = monotonic_at


def get_deadline() -> float | None:
    """取当前线程的截止时刻；未设置时返回 None。"""
    return getattr(_deadline_state, "at", None)


def remaining_budget() -> float | None:
    """距截止还剩多少秒；未设置截止时返回 None，已超时返回 0。"""
    at = get_deadline()
    if at is None:
        return None
    return max(0.0, at - time.monotonic())


class deadline:  # noqa: N801 - 当上下文管理器用，小写更自然
    """在 ``with`` 范围内为本线程设置截止时间，退出时恢复原值。

    嵌套时**取更早的那个**：内层不该能延长外层给的预算，否则一个子步骤就能
    突破整个请求的时限，截止时间也就形同虚设了。

    Usage:
        with deadline(seconds=200):
            answer_query(...)
    """

    def __init__(self, seconds: float) -> None:
        self._want = time.monotonic() + max(0.0, seconds)
        self._prev: float | None = None

    def __enter__(self) -> "deadline":
        self._prev = get_deadline()
        at = self._want if self._prev is None else min(self._prev, self._want)
        set_deadline(at)
        return self

    def __exit__(self, *exc: object) -> None:
        set_deadline(self._prev)


class DeadlineExceeded(TimeoutError):
    """预算耗尽：继续重试已无意义。

    继承 ``TimeoutError`` 而不是自成一家，是为了让既有的 ``except TimeoutError``
    分支不必改动就能兜住它——这类超时对调用方而言语义本来就相同。
    """


# ---------------------------------------------------------------------------
# 退避与重试
# ---------------------------------------------------------------------------


def _backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """计算第 ``attempt`` 次失败后的退避延迟（含抖动），单位秒。

    ``attempt`` 从 1 起：``delay = min(base * factor ** (attempt - 1), max)``，
    抖动为 ``delay * uniform(-jitter, jitter)`` 的均匀偏移，结果不小于 0。
    """
    delay = min(
        policy.base_delay * (policy.backoff_factor ** (attempt - 1)),
        policy.max_delay,
    )
    if policy.jitter > 0.0:
        # 抖动是统计学用途（打散重试时刻），不是密钥生成——刻意不换 secrets。
        delay *= 1.0 + random.uniform(-policy.jitter, policy.jitter)
    return max(0.0, delay)


def retry_call(
    fn: Callable[..., Any],
    *args: Any,
    policy: Optional[RetryPolicy] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    **kwargs: Any,
) -> Any:
    """按策略调用 ``fn(*args, **kwargs)``，失败时指数退避重试。

    Args:
        fn: 目标函数。
        policy: 重试策略；为 None 时使用 :class:`RetryPolicy` 默认值
            （max_attempts=3, base_delay=0.5, max_delay=30.0,
            jitter=0.1, backoff_factor=2.0, retry_on=(Exception,)）。
        on_retry: 每次决定重试前的埋点回调
            ``on_retry(attempt, exc, delay)``：
            - ``attempt``：本次失败的尝试序号（1 起）
            - ``exc``    ：刚捕获的可重试异常
            - ``delay``  ：即将 sleep 的秒数（含抖动）
            回调自身抛出的异常会被吞掉，不影响重试流程。

    Returns:
        ``fn`` 首次成功调用的返回值。

    Raises:
        耗尽重试次数后原样抛出最后一次异常（不包装），
        便于调用方按原有异常类型处理。
    """
    policy = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        _bump_stats("total_attempts")
        try:
            result = fn(*args, **kwargs)
            return result
        except policy.retry_on as exc:
            _bump_stats("total_failures")
            if attempt >= policy.max_attempts:
                raise
            delay = _backoff_delay(policy, attempt)

            # 预算检查：只有"睡完还剩得下一次尝试"才值得重试。
            #
            # 判据是 delay + min_attempt_s 而不是只看 delay：睡到还剩 0.1 秒
            # 才醒过来再发一次注定超时的请求，是把一次失败拖成两次，纯亏。
            # 没设截止时间时（离线脚本、入库任务）budget 为 None，行为与从前
            # 完全一致——这个机制只在有人明确声明预算时才生效。
            budget = remaining_budget()
            if budget is not None and budget <= delay + policy.min_attempt_s:
                _bump_stats("total_deadline_aborts")
                raise DeadlineExceeded(
                    f"重试预算耗尽：剩余 {budget:.1f}s，不足以退避 {delay:.1f}s "
                    f"后再尝试一次（第 {attempt} 次失败：{type(exc).__name__}: {exc}）"
                ) from exc

            if on_retry is not None:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:
                    # 埋点回调异常不得影响重试主流程
                    pass
            _bump_stats("total_retries")
            time.sleep(delay)


def retryable(
    policy: Optional[RetryPolicy] = None,
    *,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
) -> Callable[[F], F]:
    """装饰器工厂：把任意函数包装为自动重试版本。

    Args:
        policy: 重试策略；None 时使用 :class:`RetryPolicy` 默认值。
        on_retry: 透传给 :func:`retry_call` 的埋点回调。

    Usage:
        @retryable(RetryPolicy(max_attempts=3, jitter=0.0))
        def call_llm(prompt: str) -> str:
            ...
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return retry_call(fn, *args, policy=policy, on_retry=on_retry, **kwargs)

        return cast(F, wrapper)

    return decorator


__all__ = [
    "RetryPolicy",
    "retry_call",
    "retryable",
    "retry_stats",
    "reset_retry_stats",
    "deadline",
    "DeadlineExceeded",
    "set_deadline",
    "get_deadline",
    "remaining_budget",
]
