"""Enterprise identity federation control-plane transactions for complete 0021 catalogs."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import ipaddress
import json
import re
import secrets
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit
import uuid

from sqlalchemy import MetaData, Table, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.enterprise_identity_dns import (
    IdentityDnsResolverUnavailable,
    IdentityTxtResolver,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    TenantMutationReservation,
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from models.orm import Account, Tenant, TenantAuditEvent, TenantMember
from core.identity_revocations import (
    PROTECTED_COLUMNS,
    RevocationKindSpec,
    register_revocation_kind,
    resolve_revocation_kind,
)

IdentityRole = Literal["owner", "admin", "editor", "member"]
_REVISION = "0021_enterprise_identity_federation"
_REQUIRED_TABLES = {
    "accounts",
    "tenants",
    "tenant_members",
    "tenant_audit_events",
    "tenant_control_mutation_requests",
    "tenant_verified_domains",
    "tenant_identity_providers",
    "tenant_scim_tokens",
    "alembic_version",
}
_COLUMNS = {
    "tenant_verified_domains": {
        "id",
        "tenant_id",
        "normalized_domain",
        "status",
        "verification_method",
        "challenge_token",
        "txt_host",
        "txt_value",
        "revision",
        "last_checked_at",
        "verified_at",
        "verified_by",
        "revoked_at",
        "revoked_by",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    },
    "tenant_identity_providers": {
        "id",
        "tenant_id",
        "name",
        "provider_type",
        "status",
        "active_slot",
        "trusted_domain_id",
        "issuer_url",
        "client_id",
        "secret_ref",
        "scopes",
        "entity_id",
        "sso_url",
        "metadata_url",
        "certificate_fingerprint",
        "validation_state",
        "last_validated_at",
        "validation_error",
        "metadata_hash",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "activated_at",
        "activated_by",
        "disabled_at",
        "disabled_by",
    },
    "tenant_scim_tokens": {
        "id",
        "tenant_id",
        "name",
        "active_name_key",
        "token_hash",
        "token_prefix",
        "status",
        "scopes",
        "expires_at",
        "last_used_at",
        "revision",
        "issued_at",
        "issued_by",
        "revoked_at",
        "revoked_by",
        "created_at",
        "updated_at",
    },
}
_UNIQUES = {
    "tenant_verified_domains": {
        "uq_tenant_verified_domains_global_domain": ("normalized_domain",),
        "uq_tenant_verified_domains_scope_id": ("tenant_id", "id"),
    },
    "tenant_identity_providers": {
        "uq_tenant_identity_providers_active_slot": ("tenant_id", "active_slot"),
    },
    "tenant_scim_tokens": {
        "uq_tenant_scim_tokens_active_name": ("tenant_id", "active_name_key"),
        "uq_tenant_scim_tokens_hash": ("tenant_id", "token_hash"),
    },
}
_CHECKS = {
    "tenant_verified_domains": {
        "ck_tenant_verified_domains_status": ("pending", "verified", "revoked"),
        "ck_tenant_verified_domains_method": ("dns_txt",),
        "ck_tenant_verified_domains_revision_positive": ("revision > 0",),
    },
    "tenant_identity_providers": {
        "ck_tenant_identity_providers_type": ("oidc", "saml"),
        "ck_tenant_identity_providers_status": ("draft", "active", "disabled"),
        "ck_tenant_identity_providers_validation": ("unchecked", "valid", "invalid", "unavailable"),
        "ck_tenant_identity_providers_revision_positive": ("revision > 0",),
        "ck_tenant_identity_providers_active_slot": ("active_slot", "primary"),
        "ck_tenant_identity_providers_type_fields": ("provider_type",),
    },
    "tenant_scim_tokens": {
        "ck_tenant_scim_tokens_status": ("active", "revoked", "expired"),
        "ck_tenant_scim_tokens_hash_length": ("length(token_hash)", "64"),
        "ck_tenant_scim_tokens_revision_positive": ("revision > 0",),
        "ck_tenant_scim_tokens_active_name": ("active_name_key", "name"),
    },
}
_FOREIGN_KEYS = {
    "tenant_verified_domains": {
        "fk_tenant_verified_domains_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_verified_domains_creator": (
            ("created_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_verified_domains_updater": (
            ("updated_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_verified_domains_verifier": (
            ("verified_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_verified_domains_revoker": (
            ("revoked_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "tenant_identity_providers": {
        "fk_tenant_identity_providers_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_identity_providers_domain": (
            ("tenant_id", "trusted_domain_id"),
            "tenant_verified_domains",
            ("tenant_id", "id"),
        ),
    },
    "tenant_scim_tokens": {
        "fk_tenant_scim_tokens_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_scim_tokens_issuer": (
            ("issued_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_scim_tokens_revoker": (
            ("revoked_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
}
_INDEXES = {
    "tenant_verified_domains": {
        "ix_tenant_verified_domains_tenant_status_updated": (
            "tenant_id",
            "status",
            "updated_at",
            "id",
        )
    },
    "tenant_identity_providers": {
        "ix_tenant_identity_providers_tenant_status": ("tenant_id", "status", "updated_at", "id")
    },
    "tenant_scim_tokens": {
        "ix_tenant_scim_tokens_tenant_status_expires": ("tenant_id", "status", "expires_at", "id")
    },
}
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_SECRET_RE = re.compile(
    r"(?i)(sk_(?:live|test)_|github_pat_|gh[pousr]_|glpat-|AKIA[0-9A-Z]{16}|"
    r"bearer\s+|password\s*[=:]|api[-_ ]?key\s*[=:])"
)
_ALLOWED_SECRET_SCHEMES = {"vault", "env", "kms", "secret"}
_ALLOWED_SCIM_SCOPES = {"users:read", "users:write", "groups:read", "groups:write"}


class IdentityControlError(RuntimeError):
    pass


class IdentityMigrationRequired(IdentityControlError):
    pass


class IdentityControlUnavailable(IdentityControlError):
    pass


class IdentityForbidden(IdentityControlError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class IdentityNotFound(IdentityControlError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class IdentityConflict(IdentityControlError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class IdentityRevisionConflict(IdentityConflict):
    def __init__(self, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("identity_revision_conflict", "身份控制面资源已发生变化，请刷新后重试")


class IdentityValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _clean(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise IdentityValidationError(
            "identity_request_invalid",
            f"{field} must contain between 1 and {maximum} characters",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise IdentityValidationError("identity_request_invalid", f"{field} is invalid")
    return result


def _positive(value: Any, field: str, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise IdentityValidationError(
            "identity_request_invalid", f"{field} must be a positive integer"
        )
    return value


def _domain(value: Any) -> str:
    raw = _clean(value, "domain", 253).rstrip(".").casefold()
    try:
        normalized = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise IdentityValidationError("identity_domain_invalid", "域名格式无效") from exc
    if not _DOMAIN_RE.fullmatch(normalized):
        raise IdentityValidationError("identity_domain_invalid", "域名格式无效")
    if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
        raise IdentityValidationError("identity_domain_invalid", "域名不能指向本地网络")
    return normalized


def _reason(value: Any) -> str:
    result = _clean(value, "reason", 512)
    return "[REDACTED]" if _SECRET_RE.search(result) else result


def _normalized_sql(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _ensure_0021(connection: Any) -> None:
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if _REQUIRED_TABLES - tables:
            raise IdentityMigrationRequired("identity federation schema is incomplete")
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if revisions != (_REVISION,):
            raise IdentityMigrationRequired("identity federation schema is not stamped 0021")
        for table_name, expected in _COLUMNS.items():
            actual = {str(item["name"]) for item in inspector.get_columns(table_name)}
            if expected - actual:
                raise IdentityMigrationRequired(
                    f"identity federation columns are incomplete for {table_name}"
                )
        for table_name, expected in _UNIQUES.items():
            reflected = list(inspector.get_unique_constraints(table_name))
            actual_by_name = {
                str(item.get("name")): tuple(item.get("column_names") or ())
                for item in reflected
                if item.get("name")
            }
            actual_columns = {tuple(item.get("column_names") or ()) for item in reflected}
            if any(
                actual_by_name.get(name) != columns and columns not in actual_columns
                for name, columns in expected.items()
            ):
                raise IdentityMigrationRequired(
                    f"identity federation unique constraints are incomplete for {table_name}"
                )
        for table_name, expected in _CHECKS.items():
            actual = {
                str(item.get("name")): _normalized_sql(item.get("sqltext"))
                for item in inspector.get_check_constraints(table_name)
            }
            for name, fragments in expected.items():
                sql = actual.get(name, "")
                if not sql or any(fragment not in sql for fragment in fragments):
                    raise IdentityMigrationRequired(
                        f"identity federation check is incomplete: {name}"
                    )
        for table_name, expected in _FOREIGN_KEYS.items():
            reflected = list(inspector.get_foreign_keys(table_name))
            actual_by_name = {
                str(item.get("name")): (
                    tuple(item.get("constrained_columns") or ()),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or ()),
                )
                for item in reflected
                if item.get("name")
            }
            actual_contracts = {
                (
                    tuple(item.get("constrained_columns") or ()),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or ()),
                )
                for item in reflected
            }
            if any(
                actual_by_name.get(name) != contract and contract not in actual_contracts
                for name, contract in expected.items()
            ):
                raise IdentityMigrationRequired(
                    f"identity federation foreign keys are incomplete for {table_name}"
                )
        for table_name, expected in _INDEXES.items():
            actual = {
                str(item.get("name")): tuple(item.get("column_names") or ())
                for item in inspector.get_indexes(table_name)
            }
            if any(actual.get(name) != columns for name, columns in expected.items()):
                raise IdentityMigrationRequired(
                    f"identity federation indexes are incomplete for {table_name}"
                )
    except IdentityMigrationRequired:
        raise
    except Exception as exc:
        raise IdentityControlUnavailable("identity schema inspection failed") from exc


def _table(session: Session, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=session.connection())


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    if str(session.get_bind().dialect.name) != "sqlite":
        with session.begin():
            yield
        return
    connection = session.connection()
    connection.exec_driver_sql("PRAGMA busy_timeout=30000")
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        session.rollback()
        raise
    else:
        session.commit()


def _actor(session: Session, tenant_id: str, actor_id: str) -> tuple[IdentityRole, str, str]:
    result = session.execute(
        select(TenantMember.role, TenantMember.status, Account.name, Account.email, Tenant.status)
        .join(Account, Account.id == TenantMember.account_id)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.account_id == actor_id)
        .with_for_update()
    ).one_or_none()
    if result is None or str(result.status).casefold() != "active":
        raise IdentityForbidden("identity_control_forbidden", "当前身份无权管理身份联邦")
    if str(result[4]).casefold() != "active":
        raise IdentityForbidden("identity_control_forbidden", "当前租户不可用")
    role = str(result.role).casefold()
    if role not in {"owner", "admin", "editor", "member"}:
        raise IdentityControlUnavailable("tenant role is invalid")
    return role, str(result.name or "")[:128], str(result.email or "")[:256]  # type: ignore[return-value]


def _require_manager(role: str) -> None:
    if role not in {"owner", "admin"}:
        raise IdentityForbidden("identity_control_forbidden", "当前身份无权管理身份联邦")


def _require_owner(role: str) -> None:
    if role != "owner":
        raise IdentityForbidden("identity_owner_required", "该操作仅租户 owner 可执行")


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    actor_email: str,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
) -> None:
    event = TenantAuditEvent(
        id=f"tenant-audit-{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name_snapshot=actor_name,
        actor_email_snapshot=actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        target_account_id=None,
        before_snapshot=(dict(before) if before is not None else None),
        after_snapshot=(dict(after) if after is not None else None),
        request_id=request_id,
        request_ip=request_ip,
        occurred_at=datetime.utcnow(),
    )
    session.add(event)
    session.flush()


def _context(engine: Any, tenant_id: str, actor_id: str, key: str):
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, key)
    return idempotency_key_lock(tenant_id, actor_id, digest), engine_serialization_lock(engine)


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
) -> TenantMutationReservation:
    return reserve_tenant_mutation(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        raw_idempotency_key=key,
        request_hash=request_hash,
        operation=operation,
        resource_type=resource_type,
    )


def _replay(reservation: TenantMutationReservation) -> dict[str, Any] | None:
    if reservation.replay is None:
        return None
    response = reservation.replay.response
    error = response.get("error")
    if isinstance(error, Mapping):
        raise IdentityConflict(
            str(error.get("code") or "identity_state_conflict"),
            str(error.get("message") or "身份控制面操作失败"),
        )
    return response


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else value.isoformat()


def _domain_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "normalized_domain": str(row["normalized_domain"]),
        "status": str(row["status"]),
        "verification_method": str(row["verification_method"]),
        "dns_txt_host": str(row["txt_host"]),
        "dns_txt_value": str(row["txt_value"]),
        "revision": int(row["revision"]),
        "last_checked_at": _iso(row["last_checked_at"]),
        "verified_at": _iso(row["verified_at"]),
        "revoked_at": _iso(row["revoked_at"]),
    }


def _provider_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": str(row["name"]),
        "provider_type": str(row["provider_type"]),
        "status": str(row["status"]),
        "trusted_domain_id": str(row["trusted_domain_id"]),
        "validation_state": str(row["validation_state"]),
        "revision": int(row["revision"]),
        "secret_configured": bool(row["secret_ref"]),
    }


def _scim_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    scopes = row["scopes"]
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": str(row["name"]),
        "token_prefix": str(row["token_prefix"]),
        "status": str(row["status"]),
        "scopes": list(scopes),
        "expires_at": _iso(row["expires_at"]),
        "last_used_at": _iso(row["last_used_at"]),
        "revision": int(row["revision"]),
        "issued_at": _iso(row["issued_at"]),
        "revoked_at": _iso(row["revoked_at"]),
    }


def _safe_https_url(value: Any, resolver: IdentityTxtResolver) -> str:
    raw = _clean(value, "url", 1024)
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise IdentityValidationError("identity_endpoint_unsafe", "身份端点必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise IdentityValidationError("identity_endpoint_unsafe", "身份端点不能包含 userinfo")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise IdentityValidationError("identity_endpoint_unsafe", "身份端点不能指向本地网络")
    try:
        literal = ipaddress.ip_address(host)
        addresses = (str(literal),)
    except ValueError:
        try:
            addresses = resolver.resolve_host_addresses(host)
        except Exception as exc:
            raise IdentityDnsResolverUnavailable("identity endpoint DNS is unavailable") from exc
    if not addresses:
        raise IdentityDnsResolverUnavailable("identity endpoint DNS returned no addresses")
    try:
        parsed_addresses = tuple(ipaddress.ip_address(item) for item in addresses)
    except ValueError as exc:
        raise IdentityValidationError("identity_endpoint_unsafe", "身份端点地址无效") from exc
    if any(not address.is_global for address in parsed_addresses):
        raise IdentityValidationError("identity_endpoint_unsafe", "身份端点不能指向私有或保留网络")
    return raw


def _secret_ref(value: Any) -> str:
    raw = _clean(value, "secret_ref", 512)
    if _SECRET_RE.search(raw):
        raise IdentityValidationError(
            "identity_secret_ref_invalid", "必须提供 secret_ref 而非原始密钥"
        )
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in _ALLOWED_SECRET_SCHEMES:
        raise IdentityValidationError("identity_secret_ref_invalid", "secret_ref scheme 不受支持")
    if not parsed.netloc and not parsed.path:
        raise IdentityValidationError("identity_secret_ref_invalid", "secret_ref 无效")
    return raw


def _scim_token() -> str:
    suffix = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    return f"rag4c_scim_{suffix}"


def _scim_digest(raw: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-scim-token:v1\x00")
    digest.update(raw.encode("utf-8"))
    return digest.hexdigest()


def list_domains(engine: Any, *, tenant_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        _ensure_0021(session.connection())
        table = _table(session, "tenant_verified_domains")
        rows = session.execute(
            select(table).where(table.c.tenant_id == tenant_id).order_by(table.c.updated_at.desc())
        ).mappings()
        return {
            "items": [_domain_payload(row) for row in rows],
            "count": session.scalar(
                select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
            )
            or 0,
        }


def list_providers(engine: Any, *, tenant_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        _ensure_0021(session.connection())
        table = _table(session, "tenant_identity_providers")
        rows = session.execute(
            select(table).where(table.c.tenant_id == tenant_id).order_by(table.c.updated_at.desc())
        ).mappings()
        items = [_provider_payload(row) for row in rows]
        return {"items": items, "count": len(items), "runtime": {"state": "not_connected"}}


def list_scim_tokens(engine: Any, *, tenant_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        _ensure_0021(session.connection())
        table = _table(session, "tenant_scim_tokens")
        rows = session.execute(
            select(table).where(table.c.tenant_id == tenant_id).order_by(table.c.created_at.desc())
        ).mappings()
        items = [_scim_payload(row) for row in rows]
        return {"items": items, "count": len(items), "data_plane": {"state": "not_connected"}}


# Domain, provider and SCIM mutations share this transaction pattern.
def _mutation_session(engine: Any, tenant_id: str, actor_id: str, key: str):
    key_lock, engine_lock = _context(engine, tenant_id, actor_id, key)
    return key_lock, engine_lock, Session(engine, expire_on_commit=False)


def create_domain(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    domain: str,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    normalized = _domain(domain)
    safe_reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    request_hash = tenant_request_hash(
        operation="identity.domain.create",
        path_identity={"tenant_id": tenant_id},
        body={"domain": normalized, "reason": safe_reason},
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation="identity.domain.create",
                    resource_type="tenant_verified_domain",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                table = _table(session, "tenant_verified_domains")
                if (
                    session.scalar(
                        select(table.c.id).where(table.c.normalized_domain == normalized)
                    )
                    is not None
                ):
                    raise IdentityConflict("identity_domain_already_claimed", "该域名已被声明")
                now = datetime.utcnow()
                challenge = secrets.token_urlsafe(24)
                domain_id = f"identity-domain-{uuid.uuid4().hex}"
                session.execute(
                    table.insert().values(
                        id=domain_id,
                        tenant_id=tenant_id,
                        normalized_domain=normalized,
                        status="pending",
                        verification_method="dns_txt",
                        challenge_token=challenge,
                        txt_host=f"_rag4c-verify.{normalized}",
                        txt_value=f"rag4c-verification={challenge}",
                        revision=1,
                        last_checked_at=None,
                        verified_at=None,
                        verified_by=None,
                        revoked_at=None,
                        revoked_by=None,
                        created_at=now,
                        created_by=actor_id,
                        updated_at=now,
                        updated_by=actor_id,
                    )
                )
                row = session.execute(select(table).where(table.c.id == domain_id)).mappings().one()
                payload = {"domain": _domain_payload(row)}
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action="tenant_domain.created",
                    resource_type="tenant_verified_domain",
                    resource_id=domain_id,
                    before=None,
                    after={**payload["domain"], "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=201,
                    resource_id=domain_id,
                )
                return payload
    except (
        IdentityControlError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except IntegrityError as exc:
        raise IdentityConflict("identity_domain_already_claimed", "该域名已被声明") from exc
    except Exception as exc:
        raise IdentityControlUnavailable("domain create failed") from exc


def verify_domain(
    engine: Any,
    *,
    resolver: IdentityTxtResolver,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    domain_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    domain_id = _clean(domain_id, "domain_id", 64)
    revision = _positive(expected_revision, "revision")
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    safe_reason = _clean(reason, "reason", 512)
    with Session(engine) as read_session:
        _ensure_0021(read_session.connection())
        table = _table(read_session, "tenant_verified_domains")
        preview = read_session.execute(
            select(table.c.txt_host, table.c.txt_value).where(
                table.c.id == domain_id, table.c.tenant_id == tenant_id
            )
        ).one_or_none()
    if preview is None:
        raise IdentityNotFound("identity_domain_not_found", "域名不存在")
    try:
        txt_values = resolver.resolve_txt(str(preview.txt_host))
    except Exception as exc:
        raise IdentityDnsResolverUnavailable("DNS TXT resolver unavailable") from exc
    matched = str(preview.txt_value) in set(txt_values)
    request_hash = tenant_request_hash(
        operation="identity.domain.verify",
        path_identity={"tenant_id": tenant_id, "domain_id": domain_id},
        body={"revision": revision, "reason": safe_reason},
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    deferred_error: IdentityConflict | None = None
    result_payload: dict[str, Any] | None = None
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation="identity.domain.verify",
                    resource_type="tenant_verified_domain",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                table = _table(session, "tenant_verified_domains")
                current = (
                    session.execute(
                        select(table)
                        .where(table.c.id == domain_id, table.c.tenant_id == tenant_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise IdentityNotFound("identity_domain_not_found", "域名不存在")
                if int(current["revision"]) != revision:
                    raise IdentityRevisionConflict(revision, int(current["revision"]))
                now = datetime.utcnow()
                values = {
                    "last_checked_at": now,
                    "updated_at": now,
                    "updated_by": actor_id,
                    "revision": revision + 1,
                }
                action = "tenant_domain.verification_checked"
                if matched:
                    values.update(status="verified", verified_at=now, verified_by=actor_id)
                    action = "tenant_domain.verified"
                session.execute(
                    update(table)
                    .where(table.c.id == domain_id, table.c.revision == revision)
                    .values(**values)
                )
                row = session.execute(select(table).where(table.c.id == domain_id)).mappings().one()
                payload = {"domain": _domain_payload(row)}
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action=action,
                    resource_type="tenant_verified_domain",
                    resource_id=domain_id,
                    before=_domain_payload(current),
                    after={**payload["domain"], "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                if not matched:
                    error_response = {
                        "error": {
                            "code": "identity_domain_verification_failed",
                            "message": "DNS TXT challenge 未匹配",
                        }
                    }
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay=error_response,
                        http_status=409,
                        resource_id=domain_id,
                    )
                    deferred_error = IdentityConflict(
                        "identity_domain_verification_failed",
                        "DNS TXT challenge 未匹配",
                    )
                else:
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay=payload,
                        http_status=200,
                        resource_id=domain_id,
                    )
                    result_payload = payload
        if deferred_error is not None:
            raise deferred_error
        assert result_payload is not None
        return result_payload
    except (
        IdentityControlError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except Exception as exc:
        raise IdentityControlUnavailable("domain verify failed") from exc


def revoke_domain(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    domain_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    return _simple_state_mutation(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        resource_id=domain_id,
        expected_revision=expected_revision,
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        kind="domain_revoke",
    )


def _provider_values(
    provider_type: str, data: Mapping[str, Any], resolver: IdentityTxtResolver
) -> dict[str, Any]:
    if provider_type == "oidc":
        issuer = _safe_https_url(data.get("issuer_url"), resolver)
        client_id = _clean(data.get("client_id"), "client_id", 256)
        secret_ref = _secret_ref(data.get("secret_ref"))
        scopes = data.get("scopes") or ["openid"]
        if not isinstance(scopes, Sequence) or isinstance(scopes, (str, bytes)):
            raise IdentityValidationError("identity_request_invalid", "scopes must be a list")
        scope_values = sorted({_clean(item, "scope", 64) for item in scopes})
        return {
            "issuer_url": issuer,
            "client_id": client_id,
            "secret_ref": secret_ref,
            "scopes": " ".join(scope_values),
            "entity_id": None,
            "sso_url": None,
            "metadata_url": None,
            "certificate_fingerprint": None,
        }
    if provider_type == "saml":
        entity_id = _clean(data.get("entity_id"), "entity_id", 512)
        sso_url = _safe_https_url(data.get("sso_url"), resolver)
        metadata = data.get("metadata_url")
        metadata_url = _safe_https_url(metadata, resolver) if metadata else None
        fingerprint = _clean(data.get("certificate_fingerprint"), "certificate_fingerprint", 128)
        return {
            "issuer_url": None,
            "client_id": None,
            "secret_ref": None,
            "scopes": None,
            "entity_id": entity_id,
            "sso_url": sso_url,
            "metadata_url": metadata_url,
            "certificate_fingerprint": fingerprint,
        }
    raise IdentityValidationError("identity_request_invalid", "provider_type is invalid")


def create_provider(
    engine: Any,
    *,
    resolver: IdentityTxtResolver,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    name: str,
    provider_type: str,
    trusted_domain_id: str,
    configuration: Mapping[str, Any],
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    provider_name = _clean(name, "name", 128)
    kind = _clean(provider_type, "provider_type", 16).casefold()
    domain_id = _clean(trusted_domain_id, "trusted_domain_id", 64)
    safe_reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    values = _provider_values(kind, configuration, resolver)
    request_hash = tenant_request_hash(
        operation="identity.provider.create",
        path_identity={"tenant_id": tenant_id},
        body={
            "name": provider_name,
            "provider_type": kind,
            "trusted_domain_id": domain_id,
            **values,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation="identity.provider.create",
                    resource_type="tenant_identity_provider",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                domains = _table(session, "tenant_verified_domains")
                domain = (
                    session.execute(
                        select(domains).where(
                            domains.c.id == domain_id, domains.c.tenant_id == tenant_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if domain is None:
                    raise IdentityNotFound("identity_domain_not_found", "可信域名不存在")
                table = _table(session, "tenant_identity_providers")
                now = datetime.utcnow()
                provider_id = f"identity-provider-{uuid.uuid4().hex}"
                session.execute(
                    table.insert().values(
                        id=provider_id,
                        tenant_id=tenant_id,
                        name=provider_name,
                        provider_type=kind,
                        status="draft",
                        active_slot=None,
                        trusted_domain_id=domain_id,
                        validation_state="valid",
                        last_validated_at=now,
                        validation_error=None,
                        metadata_hash=hashlib.sha256(
                            json.dumps(values, sort_keys=True).encode()
                        ).hexdigest(),
                        revision=1,
                        created_at=now,
                        created_by=actor_id,
                        updated_at=now,
                        updated_by=actor_id,
                        activated_at=None,
                        activated_by=None,
                        disabled_at=None,
                        disabled_by=None,
                        **values,
                    )
                )
                row = (
                    session.execute(select(table).where(table.c.id == provider_id)).mappings().one()
                )
                payload = {
                    "provider": _provider_payload(row),
                    "runtime": {"state": "not_connected"},
                }
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action="tenant_identity_provider.created",
                    resource_type="tenant_identity_provider",
                    resource_id=provider_id,
                    before=None,
                    after={**payload["provider"], "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=201,
                    resource_id=provider_id,
                )
                return payload
    except (
        IdentityControlError,
        IdentityDnsResolverUnavailable,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except Exception as exc:
        raise IdentityControlUnavailable("provider create failed") from exc


def update_provider(
    engine: Any,
    *,
    resolver: IdentityTxtResolver,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    provider_id: str,
    expected_revision: int,
    configuration: Mapping[str, Any],
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    return _provider_mutation(
        engine,
        resolver=resolver,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        provider_id=provider_id,
        expected_revision=expected_revision,
        configuration=configuration,
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        operation="update",
    )


def activate_provider(engine: Any, **kwargs: Any) -> dict[str, Any]:
    return _provider_mutation(engine, operation="activate", configuration={}, **kwargs)


def disable_provider(engine: Any, **kwargs: Any) -> dict[str, Any]:
    return _provider_mutation(engine, operation="disable", configuration={}, **kwargs)


def _provider_mutation(
    engine: Any,
    *,
    resolver: IdentityTxtResolver | None = None,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    provider_id: str,
    expected_revision: int,
    configuration: Mapping[str, Any],
    reason: str,
    idempotency_key: str,
    request_id: str,
    operation: Literal["update", "activate", "disable"],
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    provider_id = _clean(provider_id, "provider_id", 64)
    revision = _positive(expected_revision, "revision")
    safe_reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    request_hash = tenant_request_hash(
        operation=f"identity.provider.{operation}",
        path_identity={"tenant_id": tenant_id, "provider_id": provider_id},
        body={"revision": revision, "configuration": dict(configuration), "reason": safe_reason},
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                if operation in {"activate", "disable"}:
                    _require_owner(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation=f"identity.provider.{operation}",
                    resource_type="tenant_identity_provider",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                table = _table(session, "tenant_identity_providers")
                current = (
                    session.execute(
                        select(table)
                        .where(table.c.id == provider_id, table.c.tenant_id == tenant_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise IdentityNotFound("identity_provider_not_found", "身份提供方不存在")
                if int(current["revision"]) != revision:
                    raise IdentityRevisionConflict(revision, int(current["revision"]))
                now = datetime.utcnow()
                values: dict[str, Any]
                if operation == "update":
                    if resolver is None:
                        raise IdentityDnsResolverUnavailable("resolver unavailable")
                    current_scopes = str(current["scopes"] or "").split()
                    merged_configuration = {
                        "issuer_url": current["issuer_url"],
                        "client_id": current["client_id"],
                        "secret_ref": current["secret_ref"],
                        "scopes": current_scopes,
                        "entity_id": current["entity_id"],
                        "sso_url": current["sso_url"],
                        "metadata_url": current["metadata_url"],
                        "certificate_fingerprint": current["certificate_fingerprint"],
                    }
                    merged_configuration.update(dict(configuration))
                    values = _provider_values(
                        str(current["provider_type"]), merged_configuration, resolver
                    )
                    values.update(
                        validation_state="valid",
                        last_validated_at=now,
                        validation_error=None,
                        metadata_hash=hashlib.sha256(
                            json.dumps(values, sort_keys=True).encode()
                        ).hexdigest(),
                        updated_at=now,
                        updated_by=actor_id,
                        revision=revision + 1,
                    )
                    action = "tenant_identity_provider.updated"
                elif operation == "activate":
                    domains = _table(session, "tenant_verified_domains")
                    domain = session.execute(
                        select(domains.c.status).where(
                            domains.c.id == current["trusted_domain_id"],
                            domains.c.tenant_id == tenant_id,
                        )
                    ).scalar_one_or_none()
                    if domain != "verified":
                        raise IdentityConflict("identity_domain_not_verified", "可信域名尚未验证")
                    if str(current["validation_state"]) != "valid":
                        raise IdentityConflict(
                            "identity_provider_not_valid", "身份提供方配置尚未验证"
                        )
                    values = {
                        "status": "active",
                        "active_slot": "primary",
                        "activated_at": now,
                        "activated_by": actor_id,
                        "disabled_at": None,
                        "disabled_by": None,
                        "updated_at": now,
                        "updated_by": actor_id,
                        "revision": revision + 1,
                    }
                    action = "tenant_identity_provider.activated"
                else:
                    values = {
                        "status": "disabled",
                        "active_slot": None,
                        "disabled_at": now,
                        "disabled_by": actor_id,
                        "updated_at": now,
                        "updated_by": actor_id,
                        "revision": revision + 1,
                    }
                    action = "tenant_identity_provider.disabled"
                result = session.execute(
                    update(table)
                    .where(table.c.id == provider_id, table.c.revision == revision)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise IdentityRevisionConflict(revision, int(current["revision"]))
                row = (
                    session.execute(select(table).where(table.c.id == provider_id)).mappings().one()
                )
                payload = {
                    "provider": _provider_payload(row),
                    "runtime": {"state": "not_connected"},
                }
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action=action,
                    resource_type="tenant_identity_provider",
                    resource_id=provider_id,
                    before=_provider_payload(current),
                    after={**payload["provider"], "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=200,
                    resource_id=provider_id,
                )
                return payload
    except (
        IdentityControlError,
        IdentityDnsResolverUnavailable,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except IntegrityError as exc:
        raise IdentityConflict(
            "identity_provider_active_conflict", "租户已有 active primary provider"
        ) from exc
    except Exception as exc:
        raise IdentityControlUnavailable("provider mutation failed") from exc


def issue_scim_token(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    name: str,
    scopes: Sequence[str],
    expires_in_days: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    token_name = _clean(name, "name", 128)
    days = _positive(expires_in_days, "expires_in_days", 365)
    safe_reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    if isinstance(scopes, (str, bytes)) or not scopes:
        raise IdentityValidationError("identity_request_invalid", "scopes must be a non-empty list")
    normalized = sorted({_clean(scope, "scope", 64).casefold() for scope in scopes})
    if set(normalized) - _ALLOWED_SCIM_SCOPES:
        raise IdentityValidationError("identity_scim_scope_invalid", "SCIM scope 不受支持")
    request_hash = tenant_request_hash(
        operation="identity.scim.issue",
        path_identity={"tenant_id": tenant_id},
        body={
            "name": token_name,
            "scopes": normalized,
            "expires_in_days": days,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation="identity.scim.issue",
                    resource_type="tenant_scim_token",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                table = _table(session, "tenant_scim_tokens")
                raw = _scim_token()
                now = datetime.utcnow()
                token_id = f"scim-token-{uuid.uuid4().hex}"
                expires = now + timedelta(days=days)
                session.execute(
                    table.insert().values(
                        id=token_id,
                        tenant_id=tenant_id,
                        name=token_name,
                        active_name_key=token_name,
                        token_hash=_scim_digest(raw),
                        token_prefix=raw[:16],
                        status="active",
                        scopes=normalized,
                        expires_at=expires,
                        last_used_at=None,
                        revision=1,
                        issued_at=now,
                        issued_by=actor_id,
                        revoked_at=None,
                        revoked_by=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                row = session.execute(select(table).where(table.c.id == token_id)).mappings().one()
                facts = _scim_payload(row)
                replay_response = {
                    "token": facts,
                    "delivery": {"state": "token_already_issued"},
                    "data_plane": {"state": "not_connected"},
                }
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action="tenant_scim_token.issued",
                    resource_type="tenant_scim_token",
                    resource_id=token_id,
                    before=None,
                    after={**facts, "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=replay_response,
                    http_status=201,
                    resource_id=token_id,
                )
                return {
                    "token": facts,
                    "delivery": {"state": "manual_token_required", "scim_token": raw},
                    "data_plane": {"state": "not_connected"},
                }
    except (
        IdentityControlError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except IntegrityError as exc:
        raise IdentityConflict("identity_scim_name_conflict", "已有同名 active SCIM token") from exc
    except Exception as exc:
        raise IdentityControlUnavailable("SCIM token issue failed") from exc


def revoke_scim_token(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    token_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    return _simple_state_mutation(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        resource_id=token_id,
        expected_revision=expected_revision,
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        kind="scim_revoke",
    )


def _simple_state_mutation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    resource_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    kind: Literal["domain_revoke", "scim_revoke"],
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    resource_id = _clean(resource_id, "resource_id", 64)
    revision = _positive(expected_revision, "revision")
    safe_reason = _clean(reason, "reason", 512)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    spec = resolve_revocation_kind(kind)
    if spec is None:
        # 原先这三行按 kind 二选一：没预期的 kind 会被静默当成 SCIM token 撤销 ——
        # 换掉表、换掉审计动作。表在这里拒绝，而不是猜一个。
        raise ValueError(f"未声明的撤销 kind：{kind!r}")
    operation = spec.operation
    resource_type = spec.resource_type
    table_name = spec.table_name
    request_hash = tenant_request_hash(
        operation=operation,
        path_identity={"tenant_id": tenant_id, "resource_id": resource_id},
        body={"revision": revision, "reason": safe_reason},
    )
    key_lock, engine_lock, session = _mutation_session(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, session:
            with _transaction(session):
                _ensure_0021(session.connection())
                role, actor_name, actor_email = _actor(session, tenant_id, actor_id)
                _ = actor_role
                _require_manager(role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=request_hash,
                    operation=operation,
                    resource_type=resource_type,
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                table = _table(session, table_name)
                current = (
                    session.execute(
                        select(table)
                        .where(table.c.id == resource_id, table.c.tenant_id == tenant_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise IdentityNotFound("identity_resource_not_found", "身份资源不存在")
                if int(current["revision"]) != revision:
                    raise IdentityRevisionConflict(revision, int(current["revision"]))
                if str(current["status"]) == "revoked":
                    raise IdentityConflict("identity_state_conflict", "资源已经撤销")
                now = datetime.utcnow()
                values = {
                    "status": "revoked",
                    "revision": revision + 1,
                    "revoked_at": now,
                    "revoked_by": actor_id,
                    "updated_at": now,
                }
                # 栅栏由仪式把住，不由声明的自查把住：注册期只拿一个合成 actor 试调一次
                # release_values，任何按参数（或按调用次数）分支的声明都能在第二次调用里给出
                # status/revision —— 那会把一次"撤销"写成"没撤销"或推翻 CAS。写侧要挡的不止
                # 那五列：tenant_id/id 之类"这行是谁的"若被声明改写，行会被搬到别的租户而审计
                # 仍记请求租户。这里第二次调用的结果同样过一遍禁写列，且直接拒绝而不是丢弃。
                released = dict(spec.release_values(actor_id) or {})
                blocked = sorted(set(released) & PROTECTED_COLUMNS)
                if blocked:
                    raise ValueError(
                        f"{spec.kind}: release_values 试图写撤销仪式或行身份拥有的列 {blocked}；"
                        "注册期核过一次不代表运行时也一样"
                    )
                values.update(released)
                session.execute(
                    update(table)
                    .where(table.c.id == resource_id, table.c.revision == revision)
                    .values(**values)
                )
                row = (
                    session.execute(select(table).where(table.c.id == resource_id)).mappings().one()
                )
                facts = spec.project(row)
                payload = {spec.payload_key: facts}
                action = spec.audit_action
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    before=spec.project(current),
                    after={**facts, "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=200,
                    resource_id=resource_id,
                )
                return payload
    except (
        IdentityControlError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except Exception as exc:
        raise IdentityControlUnavailable("identity state mutation failed") from exc


__all__ = [
    "IdentityConflict",
    "IdentityControlError",
    "IdentityControlUnavailable",
    "IdentityForbidden",
    "IdentityMigrationRequired",
    "IdentityNotFound",
    "IdentityRevisionConflict",
    "IdentityValidationError",
    "activate_provider",
    "create_domain",
    "create_provider",
    "disable_provider",
    "issue_scim_token",
    "list_domains",
    "list_providers",
    "list_scim_tokens",
    "revoke_domain",
    "revoke_scim_token",
    "update_provider",
    "verify_domain",
]


# 两种可撤销的身份资源各自声明"哪里不一样"；仪式（幂等预留、FOR UPDATE、revision CAS、
# 审计信封）全在 _simple_state_mutation 里共享。release_values 的差别本身就是一道栅栏：
# 域名撤销要落 updated_by，SCIM token 撤销必须让出 active_name_key 那个"同名只允许一个
# 生效"的唯一槽位，否则轮换出来的新 token 建不出来。写 status/revision/revoked_* 在注册期
# 就被拒绝，写 UPDATE 之前仪式还会再核一次（那一列就是撤销本身）。
def register_builtin_revocation_kinds() -> None:
    """Declare the built-in kinds. Replace rather than refuse, so a second instantiation of
    this module (``importlib.reload`` or a copy-load under another name) re-declares its own
    builtins instead of failing at import."""
    register_revocation_kind(
        RevocationKindSpec(
            kind="domain_revoke",
            operation="identity.domain.revoke",
            resource_type="tenant_verified_domain",
            table_name="tenant_verified_domains",
            payload_key="domain",
            audit_action="tenant_domain.revoked",
            project=_domain_payload,
            release_values=lambda actor_id: {"updated_by": actor_id},
        ),
        replace=True,
    )
    register_revocation_kind(
        RevocationKindSpec(
            kind="scim_revoke",
            operation="identity.scim.revoke",
            resource_type="tenant_scim_token",
            table_name="tenant_scim_tokens",
            payload_key="token",
            audit_action="tenant_scim_token.revoked",
            project=_scim_payload,
            release_values=lambda _actor_id: {"active_name_key": None},
        ),
        replace=True,
    )


register_builtin_revocation_kinds()
