"""C3 安全面统一：管理端点远程准入中间件回归测试。

覆盖 `server/security.py` 的准入语义：
- loopback（IPv4/IPv6）放行；
- testclient（单元测试）放行，避免破坏既有测试；
- 远程（非 loopback 非 testclient）默认 fail-closed；
- 未配置 operator token 时远程 403；配置后缺失/错误 Bearer 401，正确 Bearer 放行；
- 不信任 X-Forwarded-For 等代理头（不能伪造"本地"豁免）。
"""
from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from server.security import (
    _validate_operator_token,
    is_admin_path,
    is_loopback_or_test,
    require_admin_access,
)


def _make_request(
    host: str,
    headers: dict[str, str] | None = None,
) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/api/config/update",
        "headers": [],
        "client": (host, 50000),
    }
    if headers:
        scope["headers"] = [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()
        ]
    return Request(scope)  # type: ignore[arg-type]


class _Settings:
    """最小 settings 桩：含 run_history.ops_bearer_token。"""

    def __init__(self, token: str | None) -> None:
        self.run_history = type("RH", (), {"ops_bearer_token": _Secret(token)})()


class _Secret:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def get_secret_value(self) -> str | None:
        return self._value


def test_loopback_ipv4_and_ipv6_are_trusted() -> None:
    for host in ("127.0.0.1", "127.42.7.9", "::1"):
        assert is_loopback_or_test(_make_request(host)) is True


def test_testclient_is_trusted_for_unit_tests() -> None:
    assert is_loopback_or_test(_make_request("testclient")) is True


def test_remote_is_not_trusted() -> None:
    assert is_loopback_or_test(_make_request("10.0.0.2")) is False
    assert is_loopback_or_test(_make_request("192.168.1.50")) is False


def test_remote_cannot_forge_loopback_with_proxy_header() -> None:
    # 带 X-Forwarded-For: 127.0.0.1 的远程请求仍视为远程（不信任代理头）
    assert is_loopback_or_test(
        _make_request("10.0.0.2", {"X-Forwarded-For": "127.0.0.1"})
    ) is False


def test_bearer_token_validation() -> None:
    assert _validate_operator_token(
        _make_request("10.0.0.2", {"Authorization": "Bearer ops-secret"}), "ops-secret"
    ) is True
    assert _validate_operator_token(
        _make_request("10.0.0.2", {"Authorization": "Bearer wrong"}), "ops-secret"
    ) is False
    assert _validate_operator_token(_make_request("10.0.0.2"), "ops-secret") is False
    # Basic 前缀不是 Bearer
    assert _validate_operator_token(
        _make_request("10.0.0.2", {"Authorization": "Basic ops-secret"}), "ops-secret"
    ) is False


def test_remote_without_configured_token_is_forbidden(monkeypatch) -> None:
    from server import security

    monkeypatch.setattr(security, "_settings", lambda: _Settings(None))
    with pytest.raises(Exception) as exc_info:
        require_admin_access(_make_request("10.0.0.2"))
    assert exc_info.value.status_code == 403


def test_remote_with_missing_or_invalid_token_is_challenged(monkeypatch) -> None:
    from server import security

    monkeypatch.setattr(security, "_settings", lambda: _Settings("ops-secret"))
    for auth in (None, "", "Basic ops-secret", "Bearer wrong"):
        request = _make_request("10.0.0.2", {"Authorization": auth} if auth else None)
        with pytest.raises(Exception) as exc_info:
            require_admin_access(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_remote_with_valid_bearer_is_allowed(monkeypatch) -> None:
    from server import security

    monkeypatch.setattr(security, "_settings", lambda: _Settings("ops-secret"))
    # 不抛异常即视为放行
    require_admin_access(
        _make_request("10.0.0.2", {"Authorization": "Bearer ops-secret"})
    )


def test_loopback_admin_access_allowed_without_token() -> None:
    require_admin_access(_make_request("127.0.0.1"))


def test_admin_middleware_shortcircuits_remote_and_passes_local(monkeypatch) -> None:
    from server import security

    called = []
    monkeypatch.setattr(security, "_settings", lambda: _Settings(None))

    async def blocked(code: object, message: str, detail: object) -> object:  # pragma: no cover
        return JSONResponse(status_code=403)

    async def call_next(request: Request):
        called.append(request)
        return JSONResponse(status_code=200, content={"ok": True})

    mid = security.admin_access_middleware()
    # 远程：应被拒绝（不进入 call_next）
    remote = _make_request("10.0.0.2")
    result = asyncio_run(mid(remote, call_next))
    assert result.status_code == 403
    assert called == []

    # loopback：放行（进入 call_next）
    local = _make_request("127.0.0.1")
    result = asyncio_run(mid(local, call_next))
    assert result.status_code == 200
    assert len(called) == 1


def test_is_admin_path_matches_parameterized_document_writes() -> None:
    assert is_admin_path("/api/config/update") is True
    assert is_admin_path("/api/eval/run") is True
    assert is_admin_path("/api/documents/ingest") is True
    assert is_admin_path("/api/documents/ingest-folder") is True
    assert is_admin_path("/api/documents/doc-1/reindex") is True
    assert is_admin_path("/api/documents/reindex") is False
    assert is_admin_path("/api/documents/a/b/reindex") is False
    assert is_admin_path("/api/knowledge-bases/ds-1/documents/batch-delete") is True
    assert is_admin_path("/api/knowledge-bases/ds-1/documents/doc-1/delete") is True
    # 非管理读路径
    assert is_admin_path("/api/documents") is False
    assert is_admin_path("/api/documents/doc-1") is False
    assert is_admin_path("/api/health") is False
    # 过宽后缀不得误伤
    assert is_admin_path("/api/knowledge-bases/ds-1/documents/doc-1") is False
    assert is_admin_path("/api/knowledge-bases/ds-1/documents") is False
    assert is_admin_path("/api/knowledge-bases/ds-1/other/delete") is False
    assert is_admin_path("/api/documents/batch-delete") is False


def test_middleware_blocks_remote_parameterized_document_write(monkeypatch) -> None:
    from server import security

    monkeypatch.setattr(security, "_settings", lambda: _Settings(None))

    async def call_next(_request: Request):
        return JSONResponse(status_code=200, content={"ok": True})

    mid = security.admin_access_middleware()
    remote = _make_request("10.0.0.2")
    remote.scope["path"] = "/api/knowledge-bases/ds-1/documents/batch-delete"
    result = asyncio_run(mid(remote, call_next))
    assert result.status_code == 403


def test_assert_admin_actor_tenant_binds_and_rejects_mismatch() -> None:
    from server.security import assert_admin_actor_tenant
    from fastapi import HTTPException

    assert assert_admin_actor_tenant("tenant-a", "tenant-a") == "tenant-a"
    assert assert_admin_actor_tenant("tenant-a", None) == "tenant-a"
    assert assert_admin_actor_tenant("tenant-a", "") == "tenant-a"
    try:
        assert_admin_actor_tenant("tenant-a", "tenant-b")
        raise AssertionError("expected mismatch")
    except HTTPException as exc:
        assert exc.status_code == 403


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
