"""Actor-bound authentication and authorization dependencies for KnowledgeOps APIs.

This module establishes the reusable foundation only. Existing routes deliberately
remain unmodified until the Task4B schema and API contracts are ready.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import inspect
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypeAlias

from fastapi import HTTPException
from sqlalchemy import inspect as sqlalchemy_inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import Request

from core.enterprise_access_control import (
    DatasetAccessControlUnavailable,
    evaluate_dataset_permissions,
)
from core.enterprise_acl_idempotency import engine_serialization_lock
from core.knowledge_governance import AuditContext
from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_READ,
    role_allows,
    validate_knowledge_permission,
)
from models.orm import Account, Dataset, Tenant, TenantMember, TenantSsoSession
from server.run_ops import (
    bearer_credential,
    client_is_loopback,
    resolve_request_tenant,
)

DatasetParameterResult: TypeAlias = str | None
DatasetParameterResolver: TypeAlias = Callable[
    [Request], DatasetParameterResult | Awaitable[DatasetParameterResult]
]

_TOKEN_DOMAIN = b"rag4c:knowledge-actor:v1\x00"
_TOKEN_FIELDS = frozenset({"sub", "tenant", "iat", "exp", "jti"})
_TOKEN_SESSION_FIELDS = _TOKEN_FIELDS | frozenset({"sid"})
_TOKEN_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_READABLE_DATASET_STATES = frozenset({"active", "archived"})
_PROXY_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
)


class _KnowledgeActorTokenError(ValueError):
    pass


@dataclass(frozen=True)
class _KnowledgeActorClaims:
    account_id: str
    tenant_id: str
    issued_at: int
    expires_at: int
    jti: str
    session_id: str | None


@dataclass(frozen=True)
class KnowledgeActor:
    """Database-verified actor attached to a KnowledgeOps request."""

    account_id: str
    tenant_id: str
    role: str
    request_id: str
    request_ip: str

    def to_audit_context(self) -> AuditContext:
        """Convert verified identity into governance audit input."""

        return AuditContext(
            actor_id=self.account_id,
            request_id=self.request_id,
            request_ip=self.request_ip,
        )


@dataclass(frozen=True)
class InvitationAcceptActor:
    """Signed account identity for invitation acceptance without membership."""

    account_id: str
    tenant_id: str
    email: str
    normalized_email: str
    request_id: str
    request_ip: str


def resolve_path_dataset(parameter_name: str = "dataset_id") -> DatasetParameterResolver:
    """Build a resolver for a dataset identifier stored in FastAPI path params."""

    name = str(parameter_name or "").strip()
    if not name:
        raise ValueError("dataset path parameter name is required")

    def resolve(request: Request) -> str | None:
        value = request.path_params.get(name)
        return str(value).strip() if value is not None else None

    return resolve


def _default_settings() -> Any:
    from config.settings import get_settings

    return get_settings()


def _settings(request: Request) -> Any:
    configured = getattr(request.app.state, "knowledge_auth_settings", None)
    return configured if configured is not None else _default_settings()


def _engine(request: Request) -> Any:
    configured = getattr(request.app.state, "knowledge_auth_engine", None)
    if configured is not None:
        return configured
    from core.catalog import get_engine

    return get_engine()


def _header_value(request: Request, name: str, *, max_length: int) -> str | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    value = raw.strip()
    if not value or len(value) > max_length:
        return None
    return value


def _request_id(request: Request) -> str:
    supplied = _header_value(request, "x-request-id", max_length=128)
    return supplied if supplied is not None else f"req-{uuid.uuid4().hex}"


def _request_ip(request: Request) -> str:
    client = request.client
    return str(client.host)[:128] if client is not None else ""


def _timestamp(value: int | datetime, *, field: str) -> int:
    if type(value) is int:
        timestamp = value
    elif isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        timestamp = int(value.timestamp())
    else:
        raise TypeError(f"{field} must be an integer timestamp or aware datetime")
    if timestamp < 0:
        raise ValueError(f"{field} must not be negative")
    return timestamp


def _ttl_seconds(value: int | timedelta) -> int:
    if type(value) is int:
        seconds = value
    elif isinstance(value, timedelta):
        total = value.total_seconds()
        if not total.is_integer():
            raise ValueError("ttl must resolve to whole seconds")
        seconds = int(total)
    else:
        raise TypeError("ttl must be integer seconds or timedelta")
    if seconds <= 0:
        raise ValueError("ttl must be greater than zero")
    return seconds


def _identity_claim(value: Any, *, field: str, max_length: int) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > max_length:
        raise _KnowledgeActorTokenError(f"invalid {field}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _KnowledgeActorTokenError(f"invalid {field}")
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_b64url(value: str) -> bytes:
    if not value or _TOKEN_PART.fullmatch(value) is None:
        raise _KnowledgeActorTokenError("invalid base64url")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise _KnowledgeActorTokenError("invalid base64url") from exc
    if _b64url(decoded) != value:
        raise _KnowledgeActorTokenError("non-canonical base64url")
    return decoded


def _knowledge_security(settings: Any) -> Any:
    security = getattr(settings, "knowledge_security", None)
    if security is None:
        raise RuntimeError("knowledge security settings are not configured")
    return security


def _issuer_secret(settings: Any) -> str:
    configured = getattr(_knowledge_security(settings), "actor_signing_secret", None)
    get_secret_value = getattr(configured, "get_secret_value", None)
    secret = get_secret_value() if callable(get_secret_value) else configured
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("knowledge actor issuer secret is not configured")
    return secret


def _actor_max_ttl(settings: Any) -> int:
    maximum = getattr(_knowledge_security(settings), "actor_max_ttl_s", None)
    if type(maximum) is not int or not 60 <= maximum <= 3600:
        raise RuntimeError("knowledge actor maximum ttl is not configured safely")
    return maximum


def issue_knowledge_actor_token(
    account_id: str,
    tenant_id: str,
    ttl: int | timedelta,
    now: int | datetime,
    *,
    settings: Any | None = None,
    session_id: str | None = None,
) -> str:
    """Issue a short-lived token signed by the dedicated KnowledgeOps secret."""

    issuer_settings = settings if settings is not None else _default_settings()
    subject = _identity_claim(account_id, field="sub", max_length=64)
    tenant = _identity_claim(tenant_id, field="tenant", max_length=64)
    issued_at = _timestamp(now, field="now")
    lifetime = _ttl_seconds(ttl)
    if lifetime > _actor_max_ttl(issuer_settings):
        raise ValueError("ttl exceeds configured maximum")
    expires_at = issued_at + lifetime
    payload = {
        "sub": subject,
        "tenant": tenant,
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    if session_id is not None:
        payload["sid"] = _identity_claim(session_id, field="sid", max_length=128)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_part = _b64url(canonical)
    signature = hmac.new(
        _issuer_secret(issuer_settings).encode("utf-8"),
        _TOKEN_DOMAIN + payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{_b64url(signature)}"


def _parse_actor_token(
    token: str, *, settings: Any, now: int | None = None
) -> _KnowledgeActorClaims:
    if not token or len(token) > 4096:
        raise _KnowledgeActorTokenError("invalid token envelope")
    parts = token.split(".")
    if len(parts) != 2:
        raise _KnowledgeActorTokenError("invalid token envelope")
    payload_part, signature_part = parts
    payload_bytes = _decode_b64url(payload_part)
    signature = _decode_b64url(signature_part)
    if len(signature) != hashlib.sha256().digest_size:
        raise _KnowledgeActorTokenError("invalid signature")
    expected_signature = hmac.new(
        _issuer_secret(settings).encode("utf-8"),
        _TOKEN_DOMAIN + payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise _KnowledgeActorTokenError("invalid signature")
    try:
        decoded = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _KnowledgeActorTokenError("invalid payload") from exc
    if type(decoded) is not dict or set(decoded) not in {_TOKEN_FIELDS, _TOKEN_SESSION_FIELDS}:
        raise _KnowledgeActorTokenError("invalid payload fields")
    recanonical = json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if recanonical != payload_bytes:
        raise _KnowledgeActorTokenError("non-canonical payload")

    account_id = _identity_claim(decoded["sub"], field="sub", max_length=64)
    tenant_id = _identity_claim(decoded["tenant"], field="tenant", max_length=64)
    jti = _identity_claim(decoded["jti"], field="jti", max_length=128)
    session_id = (
        _identity_claim(decoded["sid"], field="sid", max_length=128) if "sid" in decoded else None
    )
    issued_at = decoded["iat"]
    expires_at = decoded["exp"]
    if type(issued_at) is not int or type(expires_at) is not int:
        raise _KnowledgeActorTokenError("invalid token timestamps")
    current = int(time.time()) if now is None else now
    lifetime = expires_at - issued_at
    if (
        issued_at < 0
        or lifetime <= 0
        or lifetime > _actor_max_ttl(settings)
        or issued_at > current
        or expires_at <= current
    ):
        raise _KnowledgeActorTokenError("invalid token lifetime")
    return _KnowledgeActorClaims(
        account_id=account_id,
        tenant_id=tenant_id,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
        session_id=session_id,
    )


def _token_http_error(*, required: bool) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": (
                "knowledge_actor_token_required" if required else "knowledge_actor_token_invalid"
            ),
            "message": "需要有效的 KnowledgeOps Actor Bearer 凭据",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _has_proxy_marker(request: Request) -> bool:
    return any(request.headers.get(name) is not None for name in _PROXY_HEADERS)


def _is_direct_loopback(request: Request) -> bool:
    return client_is_loopback(request) and not _has_proxy_marker(request)


def _assert_actor_header(request: Request, account_id: str) -> None:
    raw = request.headers.get("x-rag4c-actor")
    if raw is None:
        return
    asserted = raw.strip()
    if not asserted or len(asserted) > 64 or asserted != account_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "knowledge_actor_mismatch",
                "message": "actor assertion 与签名身份不一致",
            },
        )


def _token_tenant(request: Request, claims: _KnowledgeActorClaims) -> str:
    header_tenant = _header_value(request, "x-rag4c-tenant", max_length=64)
    if not _is_direct_loopback(request) and header_tenant is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "tenant_required", "message": "远程访问必须提供 X-RAG4C-Tenant"},
        )
    if header_tenant is not None and header_tenant != claims.tenant_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "knowledge_tenant_mismatch",
                "message": "tenant assertion 与签名身份不一致",
            },
        )
    return claims.tenant_id


def _resolve_identity(
    request: Request,
    *,
    settings: Any,
    permission: str,
    allow_loopback_local_actor: str | None,
) -> tuple[str, str, str | None]:
    credential = bearer_credential(request)
    if credential is not None:
        try:
            claims = _parse_actor_token(credential, settings=settings)
        except (RuntimeError, _KnowledgeActorTokenError) as exc:
            raise _token_http_error(required=False) from exc
        _assert_actor_header(request, claims.account_id)
        return claims.account_id, _token_tenant(request, claims), claims.session_id

    if (
        permission == KNOWLEDGE_READ
        and allow_loopback_local_actor is not None
        and _is_direct_loopback(request)
    ):
        account_id = str(allow_loopback_local_actor).strip()
        if not account_id or len(account_id) > 64:
            raise _token_http_error(required=True)
        _assert_actor_header(request, account_id)
        return account_id, resolve_request_tenant(request, None, settings), None

    raise _token_http_error(required=True)


def _require_active_sso_session(
    session: Session,
    *,
    session_id: str | None,
    account_id: str,
    tenant_id: str,
) -> None:
    if session_id is None:
        return
    row = session.execute(
        select(
            TenantSsoSession.status,
            TenantSsoSession.expires_at,
            TenantSsoSession.account_id,
            TenantSsoSession.tenant_id,
        ).where(TenantSsoSession.id == session_id)
    ).one_or_none()
    now = datetime.fromtimestamp(int(time.time()), tz=timezone.utc).replace(tzinfo=None)
    if (
        row is None
        or str(row.account_id) != account_id
        or str(row.tenant_id) != tenant_id
        or str(row.status).casefold() != "active"
        or not isinstance(row.expires_at, datetime)
        or row.expires_at <= now
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "knowledge_sso_session_inactive",
                "message": "SSO session is no longer active",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


def _authorize_database_scope(
    request: Request,
    *,
    account_id: str,
    tenant_id: str,
    permission: str,
    dataset_id: str | None,
    session_id: str | None,
) -> str:
    try:
        engine = _engine(request)
        with engine_serialization_lock(engine):
            member_columns = {
                str(column.get("name"))
                for column in sqlalchemy_inspect(engine).get_columns("tenant_members")
            }
            with Session(engine) as session:
                _require_active_sso_session(
                    session,
                    session_id=session_id,
                    account_id=account_id,
                    tenant_id=tenant_id,
                )
                if "status" in member_columns:
                    membership = session.execute(
                        select(TenantMember.role, TenantMember.status, Tenant.status)
                        .join(Account, Account.id == TenantMember.account_id)
                        .join(Tenant, Tenant.id == TenantMember.tenant_id)
                        .where(
                            TenantMember.account_id == account_id,
                            TenantMember.tenant_id == tenant_id,
                        )
                    ).one_or_none()
                    if membership is not None:
                        role, member_status, tenant_status = membership
                    else:
                        role = member_status = tenant_status = None
                else:
                    legacy_membership = session.execute(
                        select(TenantMember.role, Tenant.status)
                        .join(Account, Account.id == TenantMember.account_id)
                        .join(Tenant, Tenant.id == TenantMember.tenant_id)
                        .where(
                            TenantMember.account_id == account_id,
                            TenantMember.tenant_id == tenant_id,
                        )
                    ).one_or_none()
                    if legacy_membership is not None:
                        role, tenant_status = legacy_membership
                        member_status = "active"
                    else:
                        role = member_status = tenant_status = None

                if role is None:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_membership_required",
                            "message": "actor 不是该租户的有效成员",
                        },
                    )
                if str(member_status).strip().casefold() != "active":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_membership_inactive",
                            "message": "当前租户成员已被暂停",
                        },
                    )
                if str(tenant_status).strip().casefold() != "active":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_tenant_inactive",
                            "message": "当前租户不可用于 KnowledgeOps 操作",
                        },
                    )

                normalized_role = str(role).strip().casefold()
                if dataset_id is None:
                    if not role_allows(normalized_role, permission):
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "code": "knowledge_permission_forbidden",
                                "message": "当前租户角色没有所需的知识库权限",
                            },
                        )
                    return normalized_role

                dataset_status = session.execute(
                    select(Dataset.status).where(
                        Dataset.id == dataset_id,
                        Dataset.tenant_id == tenant_id,
                    )
                ).scalar_one_or_none()
                if dataset_status is None:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_dataset_scope_forbidden",
                            "message": "知识库不属于当前租户作用域",
                        },
                    )
                normalized_status = str(dataset_status).strip().casefold()
                allowed_states = (
                    _READABLE_DATASET_STATES
                    if permission in {KNOWLEDGE_READ, KNOWLEDGE_AUDIT}
                    else frozenset({"active"})
                )
                if normalized_status not in allowed_states:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_dataset_inactive",
                            "message": "知识库当前状态不允许该操作",
                        },
                    )

                decision = evaluate_dataset_permissions(
                    engine,
                    tenant_id,
                    account_id,
                    normalized_role,
                    dataset_id,
                    session=session,
                )
                request.state.knowledge_dataset_access = decision.as_payload()
                if not decision.allows(permission):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "knowledge_permission_forbidden",
                            "message": "当前身份没有该知识库所需的权限",
                        },
                    )
                return normalized_role
    except HTTPException:
        raise
    except DatasetAccessControlUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_auth_unavailable",
                "message": "知识库身份与权限服务暂不可用",
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_auth_unavailable",
                "message": "知识库身份与权限服务暂不可用",
            },
        ) from exc


async def _resolve_dataset(
    resolver: DatasetParameterResolver | None, request: Request
) -> str | None:
    if resolver is None:
        return None
    resolved = resolver(request)
    if inspect.isawaitable(resolved):
        resolved = await resolved
    dataset_id = str(resolved).strip() if resolved is not None else ""
    if not dataset_id or len(dataset_id) > 64:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "knowledge_dataset_required",
                "message": "需要有效的知识库标识",
            },
        )
    return dataset_id


def require_invitation_accept_identity() -> Callable[[Request], Awaitable[InvitationAcceptActor]]:
    """Require a signed existing account, but deliberately no TenantMember row."""

    async def dependency(request: Request) -> InvitationAcceptActor:
        settings = _settings(request)
        account_id, tenant_id, session_id = _resolve_identity(
            request,
            settings=settings,
            permission=KNOWLEDGE_READ,
            allow_loopback_local_actor=None,
        )
        engine = _engine(request)
        try:
            with engine_serialization_lock(engine), Session(engine) as session:
                _require_active_sso_session(
                    session,
                    session_id=session_id,
                    account_id=account_id,
                    tenant_id=tenant_id,
                )
                tenant_status = session.scalar(select(Tenant.status).where(Tenant.id == tenant_id))
                if tenant_status is None or str(tenant_status).strip().casefold() != "active":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "invitation_accept_tenant_inactive",
                            "message": "当前租户不可用于接受邀请",
                        },
                    )
                account = session.execute(
                    select(Account.email).where(Account.id == account_id)
                ).one_or_none()
                if account is None:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "invitation_accept_account_required",
                            "message": "接受邀请需要有效账号",
                        },
                    )
                email = str(account.email or "").strip()
                if not email:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "invitation_accept_account_required",
                            "message": "接受邀请需要有效账号邮箱",
                        },
                    )
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "invitation_accept_identity_unavailable",
                    "message": "邀请身份服务暂不可用",
                },
            ) from exc
        actor = InvitationAcceptActor(
            account_id=account_id,
            tenant_id=tenant_id,
            email=email,
            normalized_email=email.casefold(),
            request_id=_request_id(request),
            request_ip=_request_ip(request),
        )
        request.state.invitation_accept_actor = actor
        return actor

    return dependency


def require_knowledge_permission(
    permission: str,
    dataset_resolver: DatasetParameterResolver | None = None,
    *,
    allow_loopback_local_actor: str | None = None,
) -> Callable[[Request], Awaitable[KnowledgeActor]]:
    """Create an actor-bound FastAPI dependency for one KnowledgeOps permission.

    Signed Bearer claims provide account and tenant identity. Actor and tenant
    headers are assertions only and cannot create or elevate identity. The sole
    token-free path is an explicitly configured, direct-loopback read actor.
    """

    required_permission = validate_knowledge_permission(permission)

    async def dependency(request: Request) -> KnowledgeActor:
        settings = _settings(request)
        account_id, tenant_id, session_id = _resolve_identity(
            request,
            settings=settings,
            permission=required_permission,
            allow_loopback_local_actor=allow_loopback_local_actor,
        )
        dataset_id = await _resolve_dataset(dataset_resolver, request)
        role = _authorize_database_scope(
            request,
            account_id=account_id,
            tenant_id=tenant_id,
            permission=required_permission,
            dataset_id=dataset_id,
            session_id=session_id,
        )
        actor = KnowledgeActor(
            account_id=account_id,
            tenant_id=tenant_id,
            role=role,
            request_id=_request_id(request),
            request_ip=_request_ip(request),
        )
        request.state.knowledge_actor = actor
        return actor

    return dependency


__all__ = [
    "DatasetParameterResolver",
    "InvitationAcceptActor",
    "KnowledgeActor",
    "issue_knowledge_actor_token",
    "require_invitation_accept_identity",
    "require_knowledge_permission",
    "resolve_path_dataset",
]
