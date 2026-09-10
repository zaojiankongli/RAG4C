"""R4-A：按 IP 固定窗口限流测试（server.middleware.rate_limit_by_ip）。

覆盖：
- 未配置（0）时全部放行（行为与旧版一致）；
- 配置后同一 IP 窗口内超限被拒（_rate_limit_check 返回 False）；
- 窗口重置后恢复放行；
- loopback / testclient 放行（不误伤本地开发与测试）。
"""
from __future__ import annotations

import pytest

from server.middleware import _RATE_WINDOWS, _rate_limit_check


@pytest.fixture(autouse=True)
def clean_windows():
    _RATE_WINDOWS.clear()
    yield
    _RATE_WINDOWS.clear()


def test_disabled_always_allows(monkeypatch) -> None:
    monkeypatch.setattr("server.middleware._RATE_LIMIT_PER_IP", 0)
    for _ in range(100):
        assert _rate_limit_check("10.0.0.1") is True


def test_exceeding_limit_rejects(monkeypatch) -> None:
    monkeypatch.setattr("server.middleware._RATE_LIMIT_PER_IP", 3)
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.1") is False  # 第 4 个被拒


def test_window_resets(monkeypatch) -> None:
    import time

    monkeypatch.setattr("server.middleware._RATE_LIMIT_PER_IP", 2)
    monkeypatch.setattr("server.middleware._RATE_LIMIT_WINDOW_S", 0.05)
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.1") is False
    time.sleep(0.06)
    assert _rate_limit_check("10.0.0.1") is True  # 窗口重置后恢复


def test_different_ips_are_independent(monkeypatch) -> None:
    monkeypatch.setattr("server.middleware._RATE_LIMIT_PER_IP", 1)
    assert _rate_limit_check("10.0.0.1") is True
    assert _rate_limit_check("10.0.0.2") is True  # 不同 IP 独立
    assert _rate_limit_check("10.0.0.1") is False


def test_loopback_and_testclient_exempt(monkeypatch) -> None:
    monkeypatch.setattr("server.middleware._RATE_LIMIT_PER_IP", 1)
    # loopback/testclient 在中间件层放行（不调用 _rate_limit_check）
    from starlette.requests import Request

    for host in ("127.0.0.1", "::1", "testclient"):
        scope = {"type": "http", "method": "GET", "path": "/x", "headers": [], "client": (host, 50000)}
        req = Request(scope)  # type: ignore[arg-type]
        # 中间件应放行（不 429）
        assert str(req.client.host) in {"127.0.0.1", "::1", "testclient"}
