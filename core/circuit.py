"""进程内熔断器（零依赖）。

为依赖外部资源的调用点（Milvus / LLM 各槽位）提供故障隔离：

- 状态机：closed -> open -> half_open -> closed。
- closed：连续失败达 failure_threshold 次后打开；
- open：冷却期（cooldown_s）内一律快速失败（抛 CircuitOpenError），
  不触达依赖，防止故障放大与请求堆积；
- half_open：冷却结束后放行最多 half_open_probe 个探测请求，
  成功即闭合，失败重新打开（再进一轮冷却）。
- 每次开合状态迁移都会记日志 + 进程内指标（circuit.<name>.opened /
  circuit.<name>.closed 计数），供 /api/health 与监控页联动。

用法::

    breaker = CircuitBreaker("milvus")
    try:
        result = breaker.run(client.hybrid_search, **kwargs)
    except CircuitOpenError:
        ...  # 熔断开启：快速降级（如弃权）

本模块零第三方依赖（仅标准库），可离线导入。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from core.observability import get_logger

_logger = get_logger(__name__)

R = TypeVar("R")

# ---------------------------------------------------------------------------
# 全局注册表：供 /api/health 深度探活读取各熔断器状态
# ---------------------------------------------------------------------------
_circuits: dict[str, "CircuitBreaker"] = {}
_circuits_lock = threading.Lock()


def register_circuit(breaker: "CircuitBreaker") -> None:
    """注册熔断器（同名覆盖，探活场景语义为「最新实例」）。"""
    with _circuits_lock:
        _circuits[breaker.name] = breaker


def get_circuit(name: str) -> "CircuitBreaker | None":
    """按名称取熔断器（未注册返回 None）。"""
    with _circuits_lock:
        return _circuits.get(name)


def list_circuits() -> dict[str, dict[str, Any]]:
    """全部熔断器状态快照（name -> stats）。"""
    with _circuits_lock:
        return {name: cb.stats() for name, cb in _circuits.items()}


class CircuitOpenError(RuntimeError):
    """熔断器处于打开状态（冷却期内拒绝请求）。"""

    def __init__(self, name: str, cooldown_s: float):
        self.name = name
        self.cooldown_s = cooldown_s
        super().__init__(
            f"熔断器 {name} 已打开（连续失败达阈值），冷却 {cooldown_s:.0f}s 内快速失败"
        )


@dataclass
class CircuitConfig:
    """熔断参数。"""

    failure_threshold: int = 3      # 连续失败多少次后打开
    cooldown_s: float = 30.0        # 打开状态的冷却时长（秒）
    half_open_probe: int = 1        # 半开状态允许的探测请求数

    @classmethod
    def from_settings(cls, settings: Any) -> "CircuitConfig":
        """从 config.settings（circuit 段）构造；字段缺失时用默认值。"""
        return cls(
            failure_threshold=int(getattr(settings, "failure_threshold", 3)),
            cooldown_s=float(getattr(settings, "cooldown_s", 30.0)),
            half_open_probe=int(getattr(settings, "half_open_probe", 1)),
        )


class CircuitBreaker:
    """线程安全的熔断器。"""

    def __init__(self, name: str, config: Optional[CircuitConfig] = None):
        self.name = name
        self.config = config or CircuitConfig()
        self._lock = threading.Lock()
        self._state = "closed"        # closed | open | half_open
        self._failures = 0            # 连续失败计数
        self._opened_at = 0.0         # 打开时刻（monotonic）
        self._half_open_at = 0.0      # 进入半开的时刻（monotonic），用于回收泄漏令牌
        self._half_open_inflight = 0  # 半开状态下在途探测数
        self._open_count = 0
        self._close_count = 0
        register_circuit(self)

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def state(self) -> str:
        """当前状态（closed / open / half_open）。"""
        with self._lock:
            return self._state

    def stats(self) -> dict[str, Any]:
        """状态统计快照（供 /api/health 与监控）。"""
        with self._lock:
            return {
                "state": self._state,
                "failures": self._failures,
                "open_count": self._open_count,
                "close_count": self._close_count,
            }

    # ------------------------------------------------------------------ #
    # 状态机
    # ------------------------------------------------------------------ #
    def _transition(self, state: str, record: bool = False) -> None:
        prev = self._state
        self._state = state
        if state == "closed" and record:
            # **清零必须在「同态提前返回」之前。**
            # closed -> closed 正是最常见的那一幕：连续失败被一次成功打断。
            # 从前这句写在下面的 elif 里，被 `if prev == state: return` 挡掉了，
            # 于是 record_success() 的清零永不执行，_failures 只增不减——
            # 熔断从「连续失败 N 次」变成了「**累计**失败 N 次」。
            #
            # 后果不是偶发误判，而是**必然**：一个 0.2% 错误率的健康上游，
            # 跑够久一定会把自己熔断，且计数器不衰减，熔断是永久性的。
            self._failures = 0
        if prev == state:
            return
        if state == "open":
            self._opened_at = time.monotonic()
            self._open_count += 1
            self._record_metric("opened")
            _logger.warning("熔断器 %s 打开（连续失败 %d 次），冷却 %.0fs",
                            self.name, self.config.failure_threshold,
                            self.config.cooldown_s)
        elif state == "half_open":
            self._half_open_at = time.monotonic()
            self._half_open_inflight = 0
        elif state == "closed" and record:
            self._close_count += 1
            self._record_metric("closed")
            _logger.info("熔断器 %s 闭合（探测成功），恢复放行", self.name)

    def _record_metric(self, event: str) -> None:
        try:
            from core.metrics import get_metrics
            get_metrics().incr(f"circuit.{self.name}.{event}")
        except Exception:  # 指标埋点失败不影响熔断主流程
            pass

    def allow(self) -> bool:
        """当前是否允许执行调用（False = 熔断打开，快速失败）。"""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self.config.cooldown_s:
                    # 冷却结束：进入半开（_transition 会把在途探测数清零）
                    self._transition("half_open")
                    self._half_open_inflight = 1
                    return True
                return False
            # half_open：仅放行探测额度内的请求
            if self._half_open_inflight < self.config.half_open_probe:
                self._half_open_inflight += 1
                return True
            # 额度已满。但"额度"是靠调用方回调 record_success/record_failure 归还的，
            # 一旦某个探测请求既没成功也没失败地消失（调用方吞了异常、进程被 kill、
            # 用的是 allow() 而非 run() 却忘了记账），令牌就永久泄漏。
            # 那之后 half_open 比 open 还糟：open 至少冷却期满会自愈，
            # half_open 却会**永远**拒绝一切，且没有任何路径能把它救回来。
            # 所以给在途令牌一个冷却期长度的寿命，超期即重开一轮探测。
            if time.monotonic() - self._half_open_at >= self.config.cooldown_s:
                _logger.warning("熔断器 %s 半开探测超时未回执（%d 个在途），回收令牌重新探测",
                                self.name, self._half_open_inflight)
                self._half_open_at = time.monotonic()
                self._half_open_inflight = 1
                return True
            return False

    def record_success(self) -> None:
        """记录一次成功调用。"""
        with self._lock:
            if self._state == "half_open":
                self._half_open_inflight = max(0, self._half_open_inflight - 1)
                self._transition("closed", record=True)
            elif self._state == "closed" and self._failures:
                self._transition("closed", record=True)

    def record_failure(self) -> None:
        """记录一次失败调用。"""
        with self._lock:
            self._failures += 1
            if self._state == "half_open":
                self._half_open_inflight = max(0, self._half_open_inflight - 1)
                # 探测失败：重新打开（进入新冷却）
                self._transition("open")
            elif self._failures >= self.config.failure_threshold:
                self._transition("open")

    # ------------------------------------------------------------------ #
    # 便捷包装
    # ------------------------------------------------------------------ #
    def run(self, fn: Callable[..., R], *args: Any, **kwargs: Any) -> R:
        """执行 fn(*args, **kwargs)，自动记录成功 / 失败；打开时快速失败。

        Raises:
            CircuitOpenError: 熔断打开（冷却期内）。
            其余异常原样抛出（调用方按原有异常类型处理）。
        """
        if not self.allow():
            raise CircuitOpenError(self.name, self.config.cooldown_s)
        try:
            result = fn(*args, **kwargs)
        except CircuitOpenError:
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


__all__ = [
    "CircuitBreaker",
    "CircuitConfig",
    "CircuitOpenError",
    "register_circuit",
    "get_circuit",
    "list_circuits",
]
