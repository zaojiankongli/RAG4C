"""FastAPI routes for the OIDC Authorization Code + PKCE runtime."""

from __future__ import annotations

from datetime import datetime
import hmac
import ipaddress
import os
from typing import Any, Callable

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.enterprise_oidc_runtime import (
    AuthlibOidcRuntimeClient,
    OidcRuntimeError,
    callback_oidc_login,
    revoke_sso_session,
    start_oidc_login,
)
from server.knowledge_auth import (
    KnowledgeActor,
    issue_knowledge_actor_token,
    require_knowledge_permission,
)
from core.knowledge_permissions import KNOWLEDGE_READ

EngineProvider = Callable[[], Any]
ClientProvider = Callable[[], Any]
SecretProvider = Callable[[], str]
SettingsProvider = Callable[[], Any]
AllowlistProvider = Callable[[], tuple[str, ...] | list[str]]
NowProvider = Callable[[], datetime]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OidcStartRequest(_StrictModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    provider_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=1024)


class OidcCallbackRequest(_StrictModel):
    state: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=1, max_length=4096)


class SessionRevokeRequest(_StrictModel):
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=512)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, OidcRuntimeError):
        return HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "oidc_runtime_unavailable",
            "message": "OIDC runtime temporarily unavailable",
        },
    )


def _default_secret_resolver(reference: str) -> str:
    value = str(reference or "")
    if not value.startswith("env://"):
        raise OidcRuntimeError(
            "oidc_secret_resolver_unavailable",
            "Configured OIDC secret reference cannot be resolved by this runtime",
            503,
        )
    name = value.removeprefix("env://")
    secret = os.getenv(name, "")
    if not secret:
        raise OidcRuntimeError(
            "oidc_secret_resolver_unavailable", "OIDC client secret unavailable", 503
        )
    return secret


def _default_client() -> AuthlibOidcRuntimeClient:
    return AuthlibOidcRuntimeClient(secret_resolver=_default_secret_resolver)


def _default_signing_secret() -> str:
    from config.settings import get_settings

    configured = get_settings().knowledge_security.actor_signing_secret
    reveal = getattr(configured, "get_secret_value", None)
    return reveal() if callable(reveal) else str(configured)


def _default_settings() -> Any:
    from config.settings import get_settings

    return get_settings()


def _request_ip(request: Request) -> str:
    return str(request.client.host)[:128] if request.client is not None else ""


def _request_id(request: Request) -> str:
    supplied = str(request.headers.get("x-request-id") or "").strip()
    return supplied[:128] if supplied else "oidc-runtime-request"


def _cookie_secure(request: Request) -> bool:
    if request.url.scheme.casefold() == "https":
        return True
    host = str(request.url.hostname or "").rstrip(".").casefold()
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def build_oidc_runtime_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    oidc_client_provider: ClientProvider | None = None,
    signing_secret_provider: SecretProvider | None = None,
    actor_token_settings_provider: SettingsProvider | None = None,
    redirect_uri_allowlist_provider: AllowlistProvider | None = None,
    now_provider: NowProvider | None = None,
) -> APIRouter:
    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("engine providers must be callable")
    _ = read_engine_provider
    client_provider = oidc_client_provider or _default_client
    secret_provider = signing_secret_provider or _default_signing_secret
    settings_provider = actor_token_settings_provider or _default_settings

    def configured_redirect_allowlist() -> tuple[str, ...]:
        configured = settings_provider().knowledge_security.oidc_redirect_uri_allowlist
        return tuple(configured)

    allowlist_provider = redirect_uri_allowlist_provider or configured_redirect_allowlist
    clock = now_provider or datetime.utcnow
    require_actor = require_knowledge_permission(KNOWLEDGE_READ)
    router = APIRouter(tags=["oidc-runtime"])

    @router.post("/api/enterprise/sso/oidc/start", status_code=201)
    def oidc_start(
        body: OidcStartRequest,
        request: Request,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> JSONResponse:
        try:
            if body.tenant_id != actor.tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "oidc_tenant_scope_mismatch",
                        "message": "OIDC tenant scope does not match the authenticated actor",
                    },
                )
            result = start_oidc_login(
                mutation_engine_provider(),
                tenant_id=body.tenant_id,
                provider_id=body.provider_id,
                redirect_uri=body.redirect_uri,
                redirect_uri_allowlist=tuple(allowlist_provider()),
                oidc_client=client_provider(),
                signing_secret=secret_provider(),
                request_id=_request_id(request),
                request_ip=_request_ip(request),
                now=clock(),
            )
            response = JSONResponse(
                result.body,
                status_code=201,
                headers={"Cache-Control": "no-store"},
            )
            secure_cookie = _cookie_secure(request)
            response.set_cookie(
                "rag4c_oidc_state",
                result.raw_state,
                max_age=600,
                secure=secure_cookie,
                httponly=True,
                samesite="lax",
                path="/api/enterprise/sso/oidc/callback",
            )
            response.set_cookie(
                "rag4c_oidc_nonce",
                result.raw_nonce,
                max_age=600,
                secure=secure_cookie,
                httponly=True,
                samesite="lax",
                path="/api/enterprise/sso/oidc/callback",
            )
            return response
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/sso/oidc/callback")
    def oidc_callback(
        body: OidcCallbackRequest,
        request: Request,
        state_cookie: str | None = Cookie(default=None, alias="rag4c_oidc_state"),
        nonce_cookie: str | None = Cookie(default=None, alias="rag4c_oidc_nonce"),
    ) -> JSONResponse:
        try:
            cookie_state = str(state_cookie or "")
            state_matches = hmac.compare_digest(
                body.state.encode("utf-8"), cookie_state.encode("utf-8")
            )
            if not cookie_state or not state_matches:
                raise OidcRuntimeError(
                    "oidc_state_cookie_mismatch",
                    "OIDC state cookie is missing or does not match",
                    403,
                )
            settings = settings_provider()

            def issue_actor(
                account_id: str,
                tenant_id: str,
                ttl: int,
                now: datetime,
                session_id: str,
            ) -> str:
                return issue_knowledge_actor_token(
                    account_id,
                    tenant_id,
                    ttl,
                    int(now.timestamp()),
                    settings=settings,
                    session_id=session_id,
                )

            result = callback_oidc_login(
                mutation_engine_provider(),
                raw_state=body.state,
                raw_nonce=nonce_cookie,
                authorization_code=body.code,
                oidc_client=client_provider(),
                signing_secret=secret_provider(),
                issue_actor_token=issue_actor,
                request_id=_request_id(request),
                request_ip=_request_ip(request),
                user_agent=str(request.headers.get("user-agent") or "")[:512],
                now=clock(),
            )
            response = JSONResponse(result.body, headers={"Cache-Control": "no-store"})
            response.delete_cookie("rag4c_oidc_state", path="/api/enterprise/sso/oidc/callback")
            response.delete_cookie("rag4c_oidc_nonce", path="/api/enterprise/sso/oidc/callback")
            return response
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    @router.post("/api/enterprise/sso/sessions/{session_id}/revoke")
    def revoke_session(
        session_id: str,
        body: SessionRevokeRequest,
        request: Request,
        actor: KnowledgeActor = Depends(require_actor),
    ) -> dict[str, Any]:
        try:
            return revoke_sso_session(
                mutation_engine_provider(),
                tenant_id=actor.tenant_id,
                actor_id=actor.account_id,
                session_id=session_id,
                expected_revision=body.revision,
                reason=body.reason,
                request_id=actor.request_id or _request_id(request),
                request_ip=actor.request_ip or _request_ip(request),
                now=clock(),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _error(exc) from exc

    return router


__all__ = ["build_oidc_runtime_router"]
