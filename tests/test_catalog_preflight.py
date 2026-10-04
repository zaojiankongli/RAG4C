"""目录库启动前探活的守卫测试。

`server.app` 启动时就要 catalog。目录库连不上时，失败会在十几层
SQLAlchemy/PyMySQL 之后才浮现，运维看到的是
``pymysql.err.OperationalError (2003, ... timed out)`` 加一屏调用栈——
**看不出是哪个配置项错了**。

本机实测踩过这个坑：``.env`` 写 ``192.168.100.128:3307``，而实际服务在
``3306``（且 3306 上还没有 ``rag4c`` 库）。3307 超时、3306 通，但如果只看
"能不能连"，两种错因长得一模一样。

所以探活要做三件事，测试逐条钉住：
1. 只在配置了远程库时才探（SQLite 本地库不存在是正常的）；
2. URL 坏掉时报「URL 无法解析」而不是「连不上」——两种错因要能区分；
3. 连接失败时报出**主机:端口 + 配置项名 + 常见错因**，让人能直接动手。
"""
from __future__ import annotations

import socket

import pytest

from config.settings import CatalogSettings
from server.app import _preflight_catalog


class _Settings:
    def __init__(self, db_url: str) -> None:
        self.catalog = CatalogSettings(db_url=db_url)


# ---------------------------------------------------------------------------
# 1：只在配了远程库时探
# ---------------------------------------------------------------------------


def test_local_sqlite_is_never_probed() -> None:
    """SQLite 本地库不存在是正常的（首次启动 create_all），不该在这里拦。"""
    _preflight_catalog(_Settings(""))  # 不抛即通过


def test_unreachable_remote_db_names_host_port_and_setting() -> None:
    """连接失败要报出主机:端口 + 配置项名 + 常见错因。"""
    # 保留端口 1：几乎必定连不上，且不会真的发包到任何业务端口
    with pytest.raises(RuntimeError) as exc:
        _preflight_catalog(_Settings("mysql+pymysql://u:p@127.0.0.1:1/rag4c"))
    msg = str(exc.value)
    assert "127.0.0.1:1" in msg, "必须报出主机:端口"
    assert "RAG4C_CATALOG_DB_URL" in msg, "必须报出是哪个配置项"
    assert "端口" in msg, "必须提示端口是常见错因"


# ---------------------------------------------------------------------------
# 2：URL 坏了要与「连不上」区分
# ---------------------------------------------------------------------------


def test_broken_url_is_reported_as_parse_failure() -> None:
    """URL 本身坏掉时报「无法解析」，不能报成「不可达」。

    两者处置不同：解析失败要去修 URL 的写法，连不上要去查网络/端口。混成
    同一句话就等于把排查方向指错。
    """
    with pytest.raises(RuntimeError) as exc:
        _preflight_catalog(_Settings("这不是一个 URL"))
    assert "无法解析" in str(exc.value)
    assert "不可达" not in str(exc.value)


# ---------------------------------------------------------------------------
# 3：探活通过时不该抛
# ---------------------------------------------------------------------------


def test_reachable_host_passes(monkeypatch) -> None:
    """端口通就放行——探活不能自己变成阻塞源。

    做法是把 socket.connect 换成永远成功的桩，这样测试不依赖任何外部服务，
    也不会因为某天对方机器改了网络策略而变成一个 flaky 测试。
    """

    class _Sock:
        def settimeout(self, _t: float) -> None:
            pass

        def connect(self, addr: tuple[str, int]) -> None:
            self.addr = addr

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Sock())
    _preflight_catalog(_Settings("mysql+pymysql://u:p@127.0.0.1:3306/rag4c"))


def test_default_port_is_used_when_url_omits_it() -> None:
    """URL 里没写端口时用 MySQL 默认 3306——别去连 0 或报错。"""
    seen: dict[str, object] = {}

    class _Sock:
        def settimeout(self, _t: float) -> None:
            pass

        def connect(self, addr: tuple[str, int]) -> None:
            seen["addr"] = addr

        def close(self) -> None:
            pass

    import socket as _socket

    orig = _socket.socket
    try:
        _socket.socket = lambda *a, **k: _Sock()
        _preflight_catalog(_Settings("mysql+pymysql://u:p@db.internal/rag4c"))
    finally:
        _socket.socket = orig
    assert seen["addr"] == ("db.internal", 3306)
