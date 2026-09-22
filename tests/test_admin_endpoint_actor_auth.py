"""Admin endpoint knowledge-actor gates (unit)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server import security
from server.documents import _optional_admin_write_actor
from server.security import assert_admin_actor_tenant, is_admin_path, require_actor_on_admin_writes


class _SecSettings:
    def __init__(self, require_actor: bool) -> None:
        self.knowledge_security = SimpleNamespace(require_actor_on_admin_writes=require_actor)


def _request(host: str = "127.0.0.1", authorization: str | None = None) -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/documents/ingest",
        "headers": headers,
        "client": (host, 1),
        "query_string": b"",
    }
    return Request(scope)  # type: ignore[arg-type]


def test_is_admin_path_covers_document_writes() -> None:
    assert is_admin_path("/api/documents/doc-1/reindex") is True
    assert is_admin_path("/api/knowledge-bases/ds-1/documents/batch-delete") is True
    assert is_admin_path("/api/knowledge-bases/ds-1/documents/doc-1/delete") is True
    assert is_admin_path("/api/documents/doc-1") is False


def test_require_actor_flag_reads_settings(monkeypatch) -> None:
    monkeypatch.setattr(security, "_settings", lambda: _SecSettings(True))
    assert require_actor_on_admin_writes() is True
    monkeypatch.setattr(security, "_settings", lambda: _SecSettings(False))
    assert require_actor_on_admin_writes() is False


def test_assert_admin_actor_tenant_binds_and_rejects() -> None:
    assert assert_admin_actor_tenant("tenant-a", "tenant-a") == "tenant-a"
    assert assert_admin_actor_tenant("tenant-a", None) == "tenant-a"
    assert assert_admin_actor_tenant("tenant-a", "") == "tenant-a"
    with pytest.raises(HTTPException) as exc:
        assert_admin_actor_tenant("tenant-a", "tenant-b")
    assert exc.value.status_code == 403


def test_optional_actor_loopback_without_token_when_require_false(monkeypatch) -> None:
    import server.documents as documents_mod

    monkeypatch.setattr(documents_mod, "require_actor_on_admin_writes", lambda: False)
    monkeypatch.setattr(documents_mod, "is_loopback_or_test", lambda r: True)
    actor = asyncio.run(_optional_admin_write_actor(_request()))
    assert actor is None


def test_optional_actor_requires_token_when_flag_true(monkeypatch) -> None:
    import server.documents as documents_mod

    monkeypatch.setattr(documents_mod, "require_actor_on_admin_writes", lambda: True)

    async def _dep(request):
        raise HTTPException(
            status_code=401,
            detail={"code": "admin_auth_required", "message": "需要有效的 Bearer 凭据"},
        )

    monkeypatch.setattr(documents_mod, "require_knowledge_permission", lambda *_a, **_k: _dep)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_optional_admin_write_actor(_request()))
    assert exc.value.status_code == 401


def test_enforce_dataset_write_uses_body_dataset(monkeypatch) -> None:
    import server.documents as documents_mod

    seen: list[str | None] = []

    class _Actor:
        tenant_id = "tenant-a"
        account_id = "owner-a"
        role = "owner"

    async def _dep(request):
        return _Actor()

    def _require(permission, resolver=None, **_kwargs):
        seen.append(None if resolver is None else resolver(_request()))
        return _dep

    monkeypatch.setattr(documents_mod, "require_knowledge_permission", _require)
    actor = asyncio.run(documents_mod._enforce_dataset_write(_request(), "ds-target"))
    assert actor.tenant_id == "tenant-a"
    assert seen == ["ds-target"]


def test_reindex_empty_document_tenant_is_rejected(monkeypatch) -> None:
    """fail-closed：tenant_id 为空的文档不得被任意 actor 重索引。"""
    from server import documents as documents_mod

    class _Actor:
        tenant_id = "tenant-a"
        account_id = "owner-a"
        role = "owner"

    monkeypatch.setattr(
        documents_mod.catalog,
        "get_document",
        lambda _id: {"id": "d1", "tenant_id": "", "dataset_id": "ds-1", "file_path": "x.md"},
    )
    monkeypatch.setattr(documents_mod, "require_actor_on_admin_writes", lambda: True)

    async def _run():
        req = documents_mod.ReindexRequest(force=False)
        with pytest.raises(HTTPException) as exc:
            await documents_mod.reindex("d1", req, _request(), _Actor())
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "admin_tenant_mismatch"

    asyncio.run(_run())
