"""RAG4C 桥服务的安全中间件与依赖。

目标（C3）：把 run_ops 已验证的 operator access / 远程 bearer 校验，泛化到
**管理型端点**（配置写、评测运行、文档写操作等），使其在远程访问时默认
fail-closed；本地 loopback（前端开发 / Tauri 桌面端）保持免认证的既有体验。

边界：
- 远程准入：``require_admin_access`` 中间件 + :func:`is_admin_path`；
- 业务层：``require_actor_on_admin_writes`` 控制 ingest/reindex/config/eval
  是否强制 Knowledge Actor，并将写操作租户绑定到 actor（见 documents/app）。
"""
from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from server.run_ops import (
    bearer_credential,
    client_is_loopback,
    configured_operator_token,
)

#: 管理型路径（远程访问必须 operator 凭证）。仅收录**真实存在**的全局写路径。
_ADMIN_PATHS = frozenset(
    {
        "/api/config/update",
        "/api/eval/run",
        "/api/documents/ingest",
        "/api/documents/ingest-folder",
        "/api/documents/batch-settings",
        "/api/documents/batch-delete",
    }
)


def is_admin_path(path: str) -> bool:
    """管理型写路径判定：精确白名单 + 结构化文档写路由。

    - ``/api/documents/ingest|ingest-folder|batch-settings|batch-delete``
    - ``/api/documents/{doc}/reindex|settings``
    - ``/api/documents/{doc}/chunks/{chunk}``
    - ``/api/knowledge-bases/{ds}/documents/batch-delete``
    - ``/api/knowledge-bases/{ds}/documents/{doc}/delete``

    裸 ``/api/documents/{doc}``（GET 详情）**不**划入管理面。
    """
    raw = (path or "").split("?", 1)[0]
    if not raw:
        return False
    if raw in _ADMIN_PATHS:
        return True
    if raw.startswith("/api/documents/"):
        parts = raw[len("/api/documents/") :].split("/")
        if len(parts) == 2 and parts[0] and parts[0] not in ("batch-settings", "batch-delete"):
            if parts[1] in ("reindex", "settings"):
                return True
        if len(parts) == 3 and parts[0] and parts[1] == "chunks" and parts[2]:
            return True
    if raw.startswith("/api/knowledge-bases/") and "/documents/" in raw:
        tail = raw.split("/documents/", 1)[1]
        if tail == "batch-delete":
            return True
        parts = tail.split("/")
        if len(parts) == 2 and parts[0] and parts[1] == "delete":
            return True
    return False


def is_loopback_or_test(request: Request) -> bool:
    """可信本地：真实 loopback 直连，或单元测试客户端（testclient）。

    刻意**不**信任 ``X-Forwarded-For`` 等代理头——远程攻击者可以伪造它们，
    把它们当"本地"会绕过本闸门。loopback 判定基于 request.client 直连地址。
    ``testclient`` 是 Starlette TestClient 使用的客户端主机名（非回环 IP，
    ``client_is_loopback`` 会因无法解析为 IP 而返回 False），测试里要把所有
    本地场景视为可信，故显式放行。
    """
    if client_is_loopback(request):
        return True
    client = request.client
    if client is not None and str(client.host) == "testclient":
        return True
    return False


def _validate_operator_token(request: Request, configured: str) -> bool:
    provided = bearer_credential(request)
    if provided is None:
        return False
    try:
        return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))
    except Exception:  # noqa: BLE001 - 任何异常一律视为未授权
        return False


def _settings() -> Any:
    """获取进程级 Settings（与 run_ops / knowledge_auth 一致的事实源）。"""
    from config.settings import get_settings

    return get_settings()


def require_admin_access(request: Request) -> None:
    """管理端点准入（fail-closed）：可信本地放行；远程必须 Bearer operator token。

    在 ``server.app`` 中以中间件方式挂载，命中 :func:`is_admin_path` 才生效。
    无 operator token 配置时，远程访问管理端点直接 403（与 run_ops 语义一致）。
    """
    if is_loopback_or_test(request):
        return
    configured = configured_operator_token(_settings())
    if configured is None:
        # 未配置 token：远程不可管理（fail-closed）
        raise _forbidden()
    if not _validate_operator_token(request, configured):
        raise _unauthorized()


def _forbidden() -> Exception:
    from fastapi import HTTPException

    return HTTPException(
        status_code=403,
        detail={"code": "admin_access_forbidden", "message": "远程管理操作未启用"},
    )


def _unauthorized() -> Exception:
    from fastapi import HTTPException

    return HTTPException(
        status_code=401,
        detail={"code": "admin_auth_required", "message": "需要有效的 Bearer 凭据"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_actor_on_admin_writes() -> bool:
    """业务层是否强制 Knowledge Actor。"""
    try:
        return bool(_settings().knowledge_security.require_actor_on_admin_writes)
    except Exception:  # noqa: BLE001
        return True


def assert_admin_actor_tenant(actor_tenant: str, requested_tenant: str | None) -> str:
    """管理写：将请求租户收敛到 actor 租户；不一致则拒绝。"""
    actor_t = (actor_tenant or "").strip()
    requested = (requested_tenant or "").strip() or actor_t
    if not actor_t:
        raise _forbidden()
    if requested and requested != actor_t:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail={
                "code": "admin_tenant_mismatch",
                "message": "请求租户与身份凭据不一致",
            },
        )
    return actor_t


def admin_access_middleware(make_error: Callable[[int, str, dict], object] | None = None):
    """HTTP 中间件工厂：对管理路径做远程准入控制。

    用纯函数返回，便于在 app 中 ``app.middleware("http")(...)`` 注册。
    """

    async def middle(request: Request, call_next: Callable):
        if is_admin_path(request.url.path):
            try:
                require_admin_access(request)
            except Exception as exc:  # HTTPException 或其它
                status = getattr(exc, "status_code", 403)
                detail = getattr(exc, "detail", None) or {"code": "admin_access_denied"}
                message = detail.get("message", "拒绝") if isinstance(detail, dict) else str(detail)
                if isinstance(detail, dict) and "code" in detail:
                    body = {"error": detail}
                else:
                    body = {"error": {"code": f"admin_{status}", "message": message}}
                return JSONResponse(
                    status_code=status,
                    content=body,
                    headers=getattr(exc, "headers", None),
                )
        return await call_next(request)

    return middle


__all__ = [
    "admin_access_middleware",
    "assert_admin_actor_tenant",
    "is_admin_path",
    "is_loopback_or_test",
    "require_actor_on_admin_writes",
    "require_admin_access",
]
