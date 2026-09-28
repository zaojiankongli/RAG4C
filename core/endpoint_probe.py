"""端点探活：远程依赖不可达时**快速失败**，而不是把整轮重试耗完。

为什么单独一个模块：LLM / 嵌入 / 重排是三条不同的调用链，各自都面对
"对面服务没起"的情况。探活逻辑若在三处各写一遍，早晚出现"某处忘了探、
于是那一路悄悄慢 113 秒"。

实测背景（2026-09-28）：入库期 LLM 槽位指向没启动的本机 Ollama 时，
一次调用要把重试策略耗满（113 秒）才失败，而失败还被上层静默降级成空结果——
外面看就是"功能开了、慢了两分钟、什么都没得到"。接入探活后 **113s → 2.05s**。

设计要点：

- **只做 TCP 握手**（默认 1 秒）。不用 HTTP 探活：各家的健康检查路径不统一，
  多一次完整往返反而更慢；握手已足够区分"服务没起"和"服务在但忙"。
- **只缓存失败结论**（`negative_ttl_s` 秒）。探活成功没有信息量——下一次调用
  本来就会真的连；缓存它只会多占一份状态。
- **fail-open 的边界**：探活关闭、地址解析不出（非 http/https）时一律放行，
  交给真实调用去报错，绝不让探活本身成为新的故障源。
"""
from __future__ import annotations

import time
from typing import Optional

from config.settings import get_settings

# (host, port) -> 失败结论的过期时刻（time.monotonic）
_BAD_ENDPOINTS: dict[tuple[str, int], float] = {}


def endpoint_target(base_url: str) -> Optional[tuple[str, int]]:
    """从 base_url 抽出 ``(host, port)``；非 http(s) / 解析不出主机时返回 None。"""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(base_url or "")
    except Exception:  # noqa: BLE001 - 地址再怪也只是放弃探活
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def probe_endpoint(base_url: str, *, kind: str = "endpoint") -> bool:
    """探活：True = 可以发起调用；False = 已知不可达，别再浪费一轮退避。"""
    cfg = get_settings().retry
    if not getattr(cfg, "endpoint_probe_on", True):
        return True
    target = endpoint_target(base_url)
    if target is None:
        return True

    now = time.monotonic()
    deadline = _BAD_ENDPOINTS.get(target)
    if deadline is not None and deadline > now:
        return False

    import socket

    timeout = float(getattr(cfg, "endpoint_probe_timeout_s", 1.0) or 1.0)
    try:
        with socket.create_connection(target, timeout=timeout):
            _BAD_ENDPOINTS.pop(target, None)
            return True
    except OSError as exc:
        ttl = float(getattr(cfg, "endpoint_probe_negative_ttl_s", 15.0) or 15.0)
        _BAD_ENDPOINTS[target] = now + ttl
        _report_unreachable(kind, target, exc, ttl)
        return False


def _report_unreachable(kind: str, target: tuple[str, int], exc: BaseException, ttl: float) -> None:
    """留痕：指标 + 一条 warning（埋点失败绝不影响主流程）。"""
    label = f"{target[0]}:{target[1]}"
    try:
        from core.metrics import get_metrics

        get_metrics().incr(f"{kind}.endpoint.unreachable", tags={"endpoint": label})
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.observability import get_logger

        get_logger(__name__).warning(
            "%s 端点不可达，快速失败（%.0fs 内不再重试）: %s（%s）", kind, ttl, label, exc
        )
    except Exception:  # noqa: BLE001
        pass


def assert_endpoint_reachable(base_url: str, error_cls: type[BaseException], *, kind: str) -> None:
    """探活不通过就抛 ``error_cls``（各模块传自己的异常类型，保持原有契约）。"""
    if probe_endpoint(base_url, kind=kind):
        return
    raise error_cls(
        f"{kind} 端点不可达（快速失败，未发起调用）: {base_url}。"
        "检查端点是否启动、地址端口是否正确；确认无误后可关掉探活"
        "（retry.endpoint_probe_on=false）恢复逐次重试行为。"
    )


def reset_endpoint_probe_cache() -> None:
    """清空失败缓存（测试用；服务刚恢复时也能立刻生效）。"""
    _BAD_ENDPOINTS.clear()


__all__ = [
    "assert_endpoint_reachable",
    "endpoint_target",
    "probe_endpoint",
    "reset_endpoint_probe_cache",
]
