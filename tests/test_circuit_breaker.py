from __future__ import annotations

import threading
import time

import pytest

from core.circuit import CircuitBreaker, CircuitConfig, CircuitOpenError, get_circuit

# 这个文件守的是熔断器的**语义**，不是它的 API。
#
# 熔断器的契约是"连续失败 N 次才跳闸"。从前 `_transition` 先写 `self._state = state`
# 再 `if prev == state: return`，把 closed -> closed 这条最常见的路径（一次成功打断
# 了连续失败）整个短路掉了，`self._failures = 0` 永不执行。于是它实际实现的是
# "**累计**失败 N 次跳闸"，且计数器永不衰减。
#
# 这不是偶发误判，是必然：任何有瞬时错误率的健康上游，跑够久一定把自己熔断，
# 而且熔断是永久的——冷却结束进半开、探测成功回 closed，可 _failures 还挂在阈值上，
# 下一次失败立刻又开。
#
# 所以下面的测试大多不是"检查某个函数返回值"，而是**把一段真实的时间线走完**。


def cfg(**kw) -> CircuitConfig:
    base = dict(failure_threshold=3, cooldown_s=30.0, half_open_probe=1)
    base.update(kw)
    return CircuitConfig(**base)


# --------------------------------------------------------------------------- #
# 核心回归：连续 vs 累计
# --------------------------------------------------------------------------- #

def test_scattered_failures_among_many_successes_do_not_trip() -> None:
    """2000 次成功中散布 4 次失败，**不得**熔断。

    阈值是 3，散布的失败共 4 次 —— 按"累计"语义早就跳闸了两轮。
    按"连续"语义，每次失败都被紧随其后的成功清零，一次都不该开。
    """
    cb = CircuitBreaker("t-scattered", cfg(failure_threshold=3))

    for round_no in range(4):
        cb.record_failure()
        assert cb.state() == "closed", f"第 {round_no + 1} 次孤立失败就跳闸了"
        for _ in range(500):
            cb.record_success()
        assert cb.stats()["failures"] == 0, "一次成功之后计数器必须归零"

    assert cb.state() == "closed"
    assert cb.stats()["open_count"] == 0, "健康上游把自己熔断了"
    assert cb.allow() is True


def test_one_success_resets_a_partial_failure_streak() -> None:
    # 阈值 3：连挂 2 次 + 1 次成功 + 再连挂 2 次 = 从未连续 3 次 -> 不该开。
    cb = CircuitBreaker("t-reset", cfg(failure_threshold=3))

    cb.record_failure()
    cb.record_failure()
    assert cb.stats()["failures"] == 2
    cb.record_success()
    assert cb.stats()["failures"] == 0, "这一句正是从前被 `if prev == state: return` 吃掉的"

    cb.record_failure()
    cb.record_failure()
    assert cb.state() == "closed"


def test_genuinely_consecutive_failures_still_trip() -> None:
    # 修完"不该开的不开"，别把"该开的"也修没了。
    cb = CircuitBreaker("t-trip", cfg(failure_threshold=3))

    cb.record_failure()
    cb.record_failure()
    assert cb.state() == "closed", "差一次就跳闸，说明阈值算错了"
    cb.record_failure()
    assert cb.state() == "open"
    assert cb.allow() is False
    assert cb.stats()["open_count"] == 1


def test_recovery_does_not_leave_a_hair_trigger() -> None:
    """闭合之后必须是**干净**的闭合，不是"再挂一次就开"。

    这是旧实现最阴的后果：熔断器在半开探测成功后回到 closed，看板显示恢复正常，
    但 _failures 还停在阈值上 —— 下一次孤立失败立刻重新跳闸，如此往复。
    """
    cb = CircuitBreaker("t-recover", cfg(failure_threshold=3, cooldown_s=0.05))

    for _ in range(3):
        cb.record_failure()
    assert cb.state() == "open"

    time.sleep(0.06)
    assert cb.allow() is True          # 冷却结束，放行探测
    assert cb.state() == "half_open"
    cb.record_success()                # 探测成功
    assert cb.state() == "closed"
    assert cb.stats()["failures"] == 0, "闭合了却还带着旧计数 = 一触即发"

    cb.record_failure()
    assert cb.state() == "closed", "恢复后的第一次孤立失败不该立刻重新跳闸"


# --------------------------------------------------------------------------- #
# 半开：探测额度与令牌泄漏
# --------------------------------------------------------------------------- #

def test_half_open_admits_only_the_probe_budget() -> None:
    cb = CircuitBreaker("t-probe", cfg(failure_threshold=1, cooldown_s=0.05,
                                       half_open_probe=2))
    cb.record_failure()
    assert cb.state() == "open"
    time.sleep(0.06)

    assert cb.allow() is True          # 探测 1（open -> half_open）
    assert cb.state() == "half_open"
    assert cb.allow() is True          # 探测 2
    assert cb.allow() is False, "额度是 2，第 3 个请求不该被放行"


def test_half_open_probe_failure_reopens_with_a_fresh_cooldown() -> None:
    cb = CircuitBreaker("t-reopen", cfg(failure_threshold=1, cooldown_s=0.05))
    cb.record_failure()
    time.sleep(0.06)

    assert cb.allow() is True
    cb.record_failure()                # 探测失败
    assert cb.state() == "open"
    assert cb.allow() is False, "重新打开必须进入新一轮冷却，不能立刻再放行"


def test_leaked_half_open_token_is_reclaimed_instead_of_wedging_forever() -> None:
    """探测请求"人间蒸发"后，熔断器必须能自愈。

    额度是靠调用方回调归还的。若某个探测既没 record_success 也没 record_failure
    （调用方吞了异常、进程被杀、或用了 allow() 却忘记记账），令牌就永久泄漏。
    此时 half_open 比 open 还糟 —— open 冷却期满会自愈，half_open 会**永远**拒绝
    一切，且没有任何路径救得回来。所以在途令牌要有寿命。
    """
    cb = CircuitBreaker("t-leak", cfg(failure_threshold=1, cooldown_s=0.05))
    cb.record_failure()
    time.sleep(0.06)

    assert cb.allow() is True          # 探测被放行……
    # ……然后调用方消失了：既不记成功也不记失败。
    assert cb.allow() is False, "泄漏尚未超期，此时正常地拒绝"

    time.sleep(0.06)
    assert cb.allow() is True, "令牌泄漏后熔断器被永久焊死在 half_open"
    cb.record_success()
    assert cb.state() == "closed"


def test_entering_half_open_clears_stale_inflight() -> None:
    # open -> half_open 是一轮全新的探测，不能继承上一轮残留的在途计数。
    cb = CircuitBreaker("t-clear", cfg(failure_threshold=1, cooldown_s=0.05,
                                       half_open_probe=1))
    cb.record_failure()
    time.sleep(0.06)
    assert cb.allow() is True          # 探测被放行，随后蒸发
    cb.record_failure()                # 换个方式：显式失败，重新 open
    assert cb.state() == "open"

    time.sleep(0.06)
    assert cb.allow() is True, "新一轮半开必须重新给足额度"


# --------------------------------------------------------------------------- #
# run() 包装与并发
# --------------------------------------------------------------------------- #

def test_run_fast_fails_without_touching_the_dependency() -> None:
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("上游挂了")

    cb = CircuitBreaker("t-run", cfg(failure_threshold=2))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.run(boom)
    assert cb.state() == "open"

    before = len(calls)
    with pytest.raises(CircuitOpenError):
        cb.run(boom)
    assert len(calls) == before, "熔断打开后还在真调依赖，等于没熔断"


def test_run_success_resets_the_streak() -> None:
    cb = CircuitBreaker("t-run-ok", cfg(failure_threshold=3))
    with pytest.raises(RuntimeError):
        cb.run(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert cb.stats()["failures"] == 1
    assert cb.run(lambda: "ok") == "ok"
    assert cb.stats()["failures"] == 0


def test_concurrent_successes_do_not_trip_a_healthy_breaker() -> None:
    # 计数器由锁保护；并发下"成功清零"和"失败累加"交错也不该凭空跳闸。
    cb = CircuitBreaker("t-conc", cfg(failure_threshold=50))
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(400):
                cb.record_success()
        except BaseException as exc:  # noqa: BLE001 - 线程内异常要带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert cb.state() == "closed"
    assert cb.stats()["failures"] == 0


# --------------------------------------------------------------------------- #
# 全局注册表
# --------------------------------------------------------------------------- #

def test_breaker_registers_itself_for_health_probes() -> None:
    cb = CircuitBreaker("t-registry", cfg())
    assert get_circuit("t-registry") is cb


def test_same_name_registration_yields_the_latest_instance() -> None:
    # 同名覆盖是**有意**的（探活语义 = "最新实例"）。这里把它钉死，
    # 免得有人把它当 bug"修"成静默返回旧实例 —— 那才会让 /api/health
    # 报告一个已经没人用的熔断器的状态。
    first = CircuitBreaker("t-dup", cfg())
    second = CircuitBreaker("t-dup", cfg())
    assert get_circuit("t-dup") is second

    # 关键：覆盖只影响注册表这一个"看板视图"。管线持有的是自己的引用，
    # 两个实例的状态互不干扰。
    first.record_failure()
    first.record_failure()
    first.record_failure()
    assert first.state() == "open"
    assert second.state() == "closed", "注册表覆盖不该串改另一个实例的状态"
