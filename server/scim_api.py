from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hmac
import hashlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import enterprise_scim as scim

EngineProvider = Callable[[], Any]
NowProvider = Callable[[], datetime]
KeyProvider = Callable[[], bytes]


def _json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers=headers, media_type="application/scim+json"
    )


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, scim.ScimError):
        body: dict[str, Any] = {
            "schemas": [scim.ERROR_SCHEMA],
            "status": str(exc.status),
            "detail": exc.detail,
        }
        if exc.scim_type:
            body["scimType"] = exc.scim_type
        return _json(body, exc.status)
    if isinstance(exc, IntegrityError):
        return _json(
            {
                "schemas": [scim.ERROR_SCHEMA],
                "status": "409",
                "detail": "SCIM resource violates a uniqueness constraint",
                "scimType": "uniqueness",
            },
            409,
        )
    return _json(
        {
            "schemas": [scim.ERROR_SCHEMA],
            "status": "503",
            "detail": "SCIM provisioning service is unavailable",
        },
        503,
    )


def _bearer(request: Request) -> str | None:
    value = request.headers.get("authorization")
    if not value:
        return None
    scheme, _, credential = value.partition(" ")
    return credential.strip() if scheme.casefold() == "bearer" and credential.strip() else None


def _request_id(request: Request) -> str:
    return str(request.headers.get("x-request-id") or "")[:128]


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:128]
    return str(request.client.host if request.client else "")[:128]


def _ip_digest(key: bytes, request: Request) -> str:
    return hmac.new(key, _request_ip(request).encode("utf-8"), hashlib.sha256).hexdigest()


def build_scim_router(
    *,
    read_engine_provider: EngineProvider,
    mutation_engine_provider: EngineProvider,
    now_provider: NowProvider = datetime.utcnow,
    ip_hash_key_provider: KeyProvider,
) -> APIRouter:
    if not callable(read_engine_provider) or not callable(mutation_engine_provider):
        raise TypeError("SCIM engine providers must be callable")
    router = APIRouter(prefix="/scim/v2", tags=["scim-v2"])

    def execute(
        request: Request,
        scope: str | None,
        operation: Callable[[Session, scim.ScimTokenContext, datetime, str], Any],
    ) -> tuple[Any, dict[str, str]]:
        engine = mutation_engine_provider()
        now = now_provider()
        key = ip_hash_key_provider()
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    token = scim.authenticate(
                        session,
                        _bearer(request),
                        required_scope=scope,
                        now=now,
                        ip_hash_key=key,
                        request_ip=_request_ip(request),
                    )
                    result = operation(session, token, now, _ip_digest(key, request))
                return result, {}
        except Exception:
            raise

    @router.get("/ServiceProviderConfig")
    def service_provider_config(request: Request) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                None,
                lambda _s, _t, _n, _ip: {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
                    "patch": {"supported": True},
                    "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
                    "filter": {"supported": True, "maxResults": 100},
                    "changePassword": {"supported": False},
                    "sort": {"supported": False},
                    "etag": {"supported": True},
                    "authenticationSchemes": [
                        {
                            "type": "oauthbearertoken",
                            "name": "SCIM Bearer Token",
                            "description": "Tenant-scoped token issued by the RAG4C identity control plane",
                            "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                            "primary": True,
                        }
                    ],
                    "meta": {
                        "resourceType": "ServiceProviderConfig",
                        "location": "/scim/v2/ServiceProviderConfig",
                    },
                },
            )
            return _json(body)
        except Exception as exc:
            return _error(exc)

    @router.get("/ResourceTypes")
    def resource_types(request: Request) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                None,
                lambda _s, _t, _n, _ip: {
                    "schemas": [scim.LIST_SCHEMA],
                    "totalResults": 2,
                    "startIndex": 1,
                    "itemsPerPage": 2,
                    "Resources": [
                        {
                            "id": "User",
                            "name": "User",
                            "endpoint": "/Users",
                            "schema": scim.USER_SCHEMA,
                        },
                        {
                            "id": "Group",
                            "name": "Group",
                            "endpoint": "/Groups",
                            "schema": scim.GROUP_SCHEMA,
                        },
                    ],
                },
            )
            return _json(body)
        except Exception as exc:
            return _error(exc)

    @router.get("/Schemas")
    def schemas(request: Request) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                None,
                lambda _s, _t, _n, _ip: {
                    "schemas": [scim.LIST_SCHEMA],
                    "totalResults": 2,
                    "startIndex": 1,
                    "itemsPerPage": 2,
                    "Resources": [
                        {"id": scim.USER_SCHEMA, "name": "User", "attributes": []},
                        {"id": scim.GROUP_SCHEMA, "name": "Group", "attributes": []},
                    ],
                },
            )
            return _json(body)
        except Exception as exc:
            return _error(exc)

    @router.get("/Users")
    def users(
        request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100
    ) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                "users:read",
                lambda session, token, _now, _ip: scim.list_users(
                    session,
                    token.tenant_id,
                    filter_value=filter,
                    start_index=startIndex,
                    count=count,
                ),
            )
            return _json(body)
        except Exception as exc:
            return _error(exc)

    @router.post("/Users")
    async def create_user(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            body, _ = execute(
                request,
                "users:write",
                lambda session, token, now, ip: scim.create_user(
                    session,
                    token,
                    payload,
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return _json(
                body, 201, {"ETag": body["meta"]["version"], "Location": body["meta"]["location"]}
            )
        except Exception as exc:
            return _error(exc)

    @router.get("/Users/{resource_id}")
    def user(resource_id: str, request: Request) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                "users:read",
                lambda session, token, _now, _ip: scim.get_user(
                    session, token.tenant_id, resource_id
                ),
            )
            return _json(body, headers={"ETag": body["meta"]["version"]})
        except Exception as exc:
            return _error(exc)

    @router.patch("/Users/{resource_id}")
    async def patch_user(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            body, _ = execute(
                request,
                "users:write",
                lambda session, token, now, ip: scim.patch_user(
                    session,
                    token,
                    resource_id,
                    payload,
                    if_match=request.headers.get("if-match"),
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return _json(body, headers={"ETag": body["meta"]["version"]})
        except Exception as exc:
            return _error(exc)

    @router.delete("/Users/{resource_id}")
    def delete_user(resource_id: str, request: Request) -> Response:
        try:
            execute(
                request,
                "users:write",
                lambda session, token, now, ip: scim.delete_user(
                    session,
                    token,
                    resource_id,
                    if_match=request.headers.get("if-match"),
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return Response(status_code=204)
        except Exception as exc:
            return _error(exc)

    @router.get("/Groups")
    def groups(
        request: Request, filter: str | None = None, startIndex: int = 1, count: int = 100
    ) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                "groups:read",
                lambda session, token, _now, _ip: scim.list_groups(
                    session,
                    token.tenant_id,
                    filter_value=filter,
                    start_index=startIndex,
                    count=count,
                ),
            )
            return _json(body)
        except Exception as exc:
            return _error(exc)

    @router.post("/Groups")
    async def create_group(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            body, _ = execute(
                request,
                "groups:write",
                lambda session, token, now, ip: scim.create_group(
                    session,
                    token,
                    payload,
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return _json(
                body, 201, {"ETag": body["meta"]["version"], "Location": body["meta"]["location"]}
            )
        except Exception as exc:
            return _error(exc)

    @router.get("/Groups/{resource_id}")
    def group(resource_id: str, request: Request) -> JSONResponse:
        try:
            body, _ = execute(
                request,
                "groups:read",
                lambda session, token, _now, _ip: scim.get_group(
                    session, token.tenant_id, resource_id
                ),
            )
            return _json(body, headers={"ETag": body["meta"]["version"]})
        except Exception as exc:
            return _error(exc)

    @router.patch("/Groups/{resource_id}")
    async def patch_group(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            body, _ = execute(
                request,
                "groups:write",
                lambda session, token, now, ip: scim.patch_group(
                    session,
                    token,
                    resource_id,
                    payload,
                    if_match=request.headers.get("if-match"),
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return _json(body, headers={"ETag": body["meta"]["version"]})
        except Exception as exc:
            return _error(exc)

    @router.delete("/Groups/{resource_id}")
    def delete_group(resource_id: str, request: Request) -> Response:
        try:
            execute(
                request,
                "groups:write",
                lambda session, token, now, ip: scim.delete_group(
                    session,
                    token,
                    resource_id,
                    if_match=request.headers.get("if-match"),
                    now=now,
                    request_id=_request_id(request),
                    request_ip_hash=ip,
                ),
            )
            return Response(status_code=204)
        except Exception as exc:
            return _error(exc)

    return router


__all__ = ["build_scim_router"]
