"""OIDC Authorization Code + PKCE runtime for complete 0024 catalogs."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import ipaddress
import hmac
import json
import secrets
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse, urlsplit
import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import MetaData, Table, func, inspect, select, text, update
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock
from core.enterprise_identity_dns import DnspythonIdentityResolver
from models.orm import Account, Tenant, TenantAuditEvent, TenantMember

_REVISION = "0024_oidc_sso_runtime"
_OIDC_EXCHANGE_MARKER = "oidc_exchange_in_progress"
_REQUIRED_TABLES = {
    "accounts",
    "tenants",
    "tenant_members",
    "tenant_audit_events",
    "tenant_verified_domains",
    "tenant_identity_providers",
    "tenant_oidc_login_transactions",
    "tenant_oidc_subject_links",
    "tenant_sso_sessions",
    "alembic_version",
}
_REQUIRED_COLUMNS = {
    "tenant_oidc_login_transactions": {
        "id",
        "tenant_id",
        "provider_id",
        "state_digest",
        "nonce_digest",
        "pkce_verifier_ciphertext",
        "key_version",
        "redirect_uri",
        "expires_at",
        "status",
        "consumed_at",
        "failed_at",
        "error_code",
        "revision",
        "created_at",
        "updated_at",
    },
    "tenant_oidc_subject_links": {
        "id",
        "tenant_id",
        "provider_id",
        "account_id",
        "issuer",
        "subject_digest",
        "normalized_email",
        "revision",
        "last_login_at",
        "created_at",
        "updated_at",
    },
    "tenant_sso_sessions": {
        "id",
        "session_token_hash",
        "tenant_id",
        "account_id",
        "provider_id",
        "status",
        "expires_at",
        "last_seen_at",
        "ip_hash",
        "user_agent_hash",
        "revision",
        "revoked_at",
        "revoked_by",
        "created_at",
        "updated_at",
    },
}


class OidcRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class OidcRuntimeClient(Protocol):
    def build_authorization_url(
        self,
        *,
        provider: dict[str, Any],
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str: ...

    def exchange_and_validate(
        self,
        *,
        provider: dict[str, Any],
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
    ) -> Mapping[str, Any]: ...


class AuthlibOidcRuntimeClient:
    """Bounded production OIDC adapter with SSRF-safe endpoint resolution."""

    def __init__(
        self,
        *,
        secret_resolver: Callable[[str], str],
        resolver: Any | None = None,
        http_client_factory: Callable[..., Any] | None = None,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._resolver = resolver or DnspythonIdentityResolver()
        self._http_client_factory = http_client_factory
        self._timeout = max(1.0, min(float(timeout_seconds), 15.0))
        self._max_bytes = max(16_384, min(int(max_response_bytes), 4_194_304))
        self._metadata_cache: dict[str, dict[str, Any]] = {}

    def _client(self):
        try:
            import httpx
        except ImportError as exc:
            raise OidcRuntimeError(
                "oidc_runtime_client_unavailable",
                "OIDC runtime optional identity dependencies are unavailable",
                503,
            ) from exc
        factory = self._http_client_factory or httpx.Client
        return factory(timeout=self._timeout, follow_redirects=False)

    def _safe_endpoint(self, value: str) -> str:
        url = str(value or "").strip()
        parsed = urlsplit(url)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise OidcRuntimeError(
                "oidc_runtime_endpoint_unsafe",
                "OIDC runtime endpoint is unsafe",
                502,
            )
        host = parsed.hostname.rstrip(".").casefold()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            try:
                addresses = tuple(self._resolver.resolve_host_addresses(host))
            except Exception as exc:
                raise OidcRuntimeError(
                    "oidc_runtime_endpoint_unsafe",
                    "OIDC runtime endpoint DNS validation failed",
                    502,
                ) from exc
            if not addresses:
                raise OidcRuntimeError(
                    "oidc_runtime_endpoint_unsafe",
                    "OIDC runtime endpoint DNS returned no addresses",
                    502,
                )
            try:
                resolved = tuple(ipaddress.ip_address(address) for address in addresses)
            except ValueError as exc:
                raise OidcRuntimeError(
                    "oidc_runtime_endpoint_unsafe",
                    "OIDC runtime endpoint DNS returned an invalid address",
                    502,
                ) from exc
            if any(not address.is_global for address in resolved):
                raise OidcRuntimeError(
                    "oidc_runtime_endpoint_unsafe",
                    "OIDC runtime endpoint resolved outside the global network",
                    502,
                )
        else:
            if not literal.is_global:
                raise OidcRuntimeError(
                    "oidc_runtime_endpoint_unsafe",
                    "OIDC runtime endpoint is not globally routable",
                    502,
                )
        return url

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        endpoint = self._safe_endpoint(url)
        with self._client() as client:
            with client.stream(method, endpoint, **kwargs) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError as exc:
                        raise OidcRuntimeError(
                            "oidc_runtime_response_invalid",
                            "OIDC response Content-Length is invalid",
                            502,
                        ) from exc
                    if declared < 0 or declared > self._max_bytes:
                        raise OidcRuntimeError(
                            "oidc_runtime_response_too_large",
                            "OIDC response is too large",
                            502,
                        )
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunk) > self._max_bytes - len(content):
                        raise OidcRuntimeError(
                            "oidc_runtime_response_too_large",
                            "OIDC response is too large",
                            502,
                        )
                    content.extend(chunk)
        try:
            value = json.loads(bytes(content))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OidcRuntimeError(
                "oidc_runtime_response_invalid",
                "OIDC response is invalid",
                502,
            ) from exc
        if not isinstance(value, dict):
            raise OidcRuntimeError("oidc_runtime_response_invalid", "OIDC response is invalid", 502)
        return value

    def _json_get(self, url: str) -> dict[str, Any]:
        return self._request_json("GET", url, headers={"Accept": "application/json"})

    def _metadata(self, issuer: str) -> dict[str, Any]:
        cached = self._metadata_cache.get(issuer)
        if cached is not None:
            return cached
        metadata = self._json_get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        if str(metadata.get("issuer") or "").rstrip("/") != issuer.rstrip("/"):
            raise OidcRuntimeError("oidc_issuer_mismatch", "OIDC issuer metadata mismatch", 502)
        self._metadata_cache[issuer] = metadata
        return metadata

    def build_authorization_url(
        self,
        *,
        provider: dict[str, Any],
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        metadata = self._metadata(str(provider["issuer_url"]))
        endpoint = self._safe_endpoint(str(metadata.get("authorization_endpoint") or ""))
        scopes = provider.get("scopes") or ["openid", "email", "profile"]
        return (
            endpoint
            + "?"
            + urlencode(
                {
                    "client_id": provider["client_id"],
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(scopes),
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def exchange_and_validate(
        self,
        *,
        provider: dict[str, Any],
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
    ) -> Mapping[str, Any]:
        try:
            from authlib.jose import JsonWebKey, jwt
        except ImportError as exc:
            raise OidcRuntimeError(
                "oidc_runtime_client_unavailable",
                "OIDC runtime optional identity dependencies are unavailable",
                503,
            ) from exc
        metadata = self._metadata(str(provider["issuer_url"]))
        token_endpoint = str(metadata.get("token_endpoint") or "")
        jwks_uri = str(metadata.get("jwks_uri") or "")
        self._safe_endpoint(token_endpoint)
        self._safe_endpoint(jwks_uri)
        secret = self._secret_resolver(str(provider["secret_ref"]))
        token_payload = self._request_json(
            "POST",
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider["client_id"],
                "client_secret": secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        id_token = str(token_payload.get("id_token") or "")
        if not id_token:
            raise OidcRuntimeError("oidc_id_token_missing", "OIDC ID token missing", 403)
        jwks = self._json_get(jwks_uri)
        claims = jwt.decode(id_token, JsonWebKey.import_key_set(jwks))
        claims.validate()
        result = dict(claims)
        if str(result.get("nonce") or "") != nonce:
            raise OidcRuntimeError("oidc_nonce_mismatch", "OIDC nonce mismatch", 403)
        return {
            "issuer": result.get("iss"),
            "subject": result.get("sub"),
            "email": result.get("email"),
            "email_verified": result.get("email_verified", False),
            "audience": result.get("aud"),
            "authorized_party": result.get("azp"),
        }


@dataclass(frozen=True)
class StartResult:
    body: dict[str, Any]
    raw_state: str
    raw_nonce: str


@dataclass(frozen=True)
class CallbackResult:
    body: dict[str, Any]


@dataclass(frozen=True)
class _Provider:
    row: dict[str, Any]
    domain: dict[str, Any]


def derive_oidc_fernet_key(signing_secret: str, *, key_version: int) -> bytes:
    secret = str(signing_secret or "").encode("utf-8")
    if len(secret) < 16 or type(key_version) is not int or key_version < 1:
        raise ValueError("valid signing secret and key version are required")
    digest = hmac.new(
        secret,
        b"rag4c:oidc-pkce-fernet:v1\x00" + key_version.to_bytes(4, "big"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_pkce_verifier(verifier: str, key: bytes) -> str:
    return Fernet(key).encrypt(str(verifier).encode("utf-8")).decode("ascii")


def decrypt_pkce_verifier(ciphertext: str, key: bytes) -> str:
    try:
        return Fernet(key).decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise OidcRuntimeError(
            "oidc_pkce_decryption_failed", "OIDC PKCE evidence is invalid", 503
        ) from exc


def _digest(domain: bytes, *values: str) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _state_digest(value: str) -> str:
    return _digest(b"rag4c:oidc-state:v1\x00", value)


def _nonce_digest(value: str) -> str:
    return _digest(b"rag4c:oidc-nonce:v1\x00", value)


def _subject_digest(issuer: str, subject: str) -> str:
    return _digest(b"rag4c:oidc-subject:v1\x00", issuer, subject)


def _session_digest(value: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-sso-session:v1\x00")
    digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def _evidence_hash(signing_secret: str, domain: bytes, value: str) -> str:
    return hmac.new(
        signing_secret.encode("utf-8"), domain + value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


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


def _ensure_0024(connection: Any) -> None:
    try:
        inspector = inspect(connection)
        if _REQUIRED_TABLES - set(inspector.get_table_names()):
            raise OidcRuntimeError(
                "oidc_runtime_migration_required", "OIDC runtime requires 0024", 503
            )
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if revisions != (_REVISION,):
            raise OidcRuntimeError(
                "oidc_runtime_migration_required", "OIDC runtime requires 0024", 503
            )
        for table_name, expected in _REQUIRED_COLUMNS.items():
            actual = {str(item["name"]) for item in inspector.get_columns(table_name)}
            if expected - actual:
                raise OidcRuntimeError(
                    "oidc_runtime_migration_required", "OIDC runtime requires 0024", 503
                )
    except OidcRuntimeError:
        raise
    except Exception as exc:
        raise OidcRuntimeError(
            "oidc_runtime_migration_required", "OIDC runtime requires 0024", 503
        ) from exc


def _clean(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise OidcRuntimeError("oidc_request_invalid", f"{field} is invalid", 422)
    result = value.strip()
    if not result or len(result) > maximum or any(ord(character) < 32 for character in result):
        raise OidcRuntimeError("oidc_request_invalid", f"{field} is invalid", 422)
    return result


def _scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ["openid", "email", "profile"]
    result = [str(item) for item in value if str(item)]
    return result or ["openid", "email", "profile"]


def _provider(session: Session, *, tenant_id: str, provider_id: str) -> _Provider:
    providers = _table(session, "tenant_identity_providers")
    domains = _table(session, "tenant_verified_domains")
    provider = (
        session.execute(
            select(providers)
            .where(
                providers.c.id == provider_id,
                providers.c.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if provider is None or not (
        str(provider["provider_type"]) == "oidc"
        and str(provider["status"]) == "active"
        and str(provider.get("active_slot") or "") == "primary"
        and str(provider.get("validation_state") or "") == "valid"
    ):
        raise OidcRuntimeError(
            "oidc_provider_unavailable", "Active primary OIDC provider unavailable", 409
        )
    domain = (
        session.execute(
            select(domains).where(
                domains.c.id == provider["trusted_domain_id"],
                domains.c.tenant_id == tenant_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if domain is None or str(domain["status"]) != "verified":
        raise OidcRuntimeError(
            "oidc_domain_not_verified", "OIDC trusted domain is not verified", 409
        )
    payload = dict(provider)
    payload["scopes"] = _scopes(payload.get("scopes"))
    return _Provider(payload, dict(domain))


def _redirect_uri(value: str, allowlist: Sequence[str]) -> str:
    uri = _clean(value, "redirect_uri", 1024)
    if uri not in set(allowlist):
        raise OidcRuntimeError(
            "oidc_redirect_uri_forbidden", "OIDC redirect URI is not allowed", 422
        )
    parsed = urlparse(uri)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        parsed.username
        or parsed.password
        or parsed.fragment
        or (parsed.scheme != "https" and not (loopback and parsed.scheme == "http"))
    ):
        raise OidcRuntimeError(
            "oidc_redirect_uri_forbidden", "OIDC redirect URI is not allowed", 422
        )
    return uri


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
    after: Mapping[str, Any],
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=actor_name[:128],
            actor_email_snapshot=actor_email[:256],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            target_account_id=None,
            before_snapshot=None,
            after_snapshot=dict(after),
            request_id=str(request_id or "")[:128],
            request_ip=str(request_ip or "")[:64],
            occurred_at=now,
        )
    )
    session.flush()


def start_oidc_login(
    engine: Any,
    *,
    tenant_id: str,
    provider_id: str,
    redirect_uri: str,
    redirect_uri_allowlist: Sequence[str],
    oidc_client: OidcRuntimeClient,
    signing_secret: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> StartResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_provider = _clean(provider_id, "provider_id", 64)
    clean_redirect = _redirect_uri(redirect_uri, redirect_uri_allowlist)

    with engine_serialization_lock(engine), Session(engine) as session:
        with _transaction(session):
            _ensure_0024(session.connection())
            provider = _provider(session, tenant_id=clean_tenant, provider_id=clean_provider)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    key_version = 1
    encrypted_verifier = encrypt_pkce_verifier(
        verifier,
        derive_oidc_fernet_key(signing_secret, key_version=key_version),
    )
    authorization_url = oidc_client.build_authorization_url(
        provider=provider.row,
        redirect_uri=clean_redirect,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )
    login_id = f"oidc-login-{uuid.uuid4().hex}"
    expires_at = now + timedelta(minutes=10)

    with engine_serialization_lock(engine), Session(engine) as session:
        with _transaction(session):
            _ensure_0024(session.connection())
            current = _provider(session, tenant_id=clean_tenant, provider_id=clean_provider)
            if int(current.row["revision"]) != int(provider.row["revision"]) or int(
                current.domain["revision"]
            ) != int(provider.domain["revision"]):
                raise OidcRuntimeError(
                    "oidc_provider_unavailable",
                    "Active primary OIDC provider changed during login start",
                    409,
                )
            logins = _table(session, "tenant_oidc_login_transactions")
            session.execute(
                logins.insert().values(
                    id=login_id,
                    tenant_id=clean_tenant,
                    provider_id=clean_provider,
                    state_digest=_state_digest(state),
                    nonce_digest=_nonce_digest(nonce),
                    pkce_verifier_ciphertext=encrypted_verifier,
                    key_version=key_version,
                    redirect_uri=clean_redirect,
                    expires_at=expires_at,
                    status="pending",
                    consumed_at=None,
                    failed_at=None,
                    error_code=None,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            _audit(
                session,
                tenant_id=clean_tenant,
                actor_id="system:oidc",
                actor_name="OIDC Runtime",
                actor_email="",
                action="sso.oidc.login.started",
                resource_type="tenant_oidc_login_transaction",
                resource_id=login_id,
                after={
                    "provider_id": clean_provider,
                    "redirect_uri": clean_redirect,
                    "expires_at": expires_at.isoformat(),
                },
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )

    return StartResult(
        body={
            "authorization_url": authorization_url,
            "provider_id": clean_provider,
            "state": state,
            "expires_at": expires_at.isoformat() + "Z",
            "runtime": {"state": "oidc_runtime_ready"},
        },
        raw_state=state,
        raw_nonce=nonce,
    )


def _session_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "expires_at": row["expires_at"].isoformat() + "Z"
        if isinstance(row["expires_at"], datetime)
        else str(row["expires_at"]),
        "revoked_at": (
            row["revoked_at"].isoformat() + "Z"
            if isinstance(row.get("revoked_at"), datetime)
            else row.get("revoked_at")
        ),
    }


def _identity_error(exc: Exception) -> OidcRuntimeError:
    if isinstance(exc, OidcRuntimeError):
        return exc
    return OidcRuntimeError(
        "oidc_identity_validation_failed",
        "OIDC identity validation failed",
        403,
    )


def _validate_identity(provider: _Provider, identity: Mapping[str, Any]) -> tuple[str, str, str]:
    issuer = _clean(identity.get("issuer"), "issuer", 1024).rstrip("/")
    expected_issuer = str(provider.row["issuer_url"]).rstrip("/")
    if issuer != expected_issuer:
        raise OidcRuntimeError("oidc_issuer_mismatch", "OIDC issuer mismatch", 403)
    subject = _clean(identity.get("subject"), "subject", 512)
    email = _clean(identity.get("email"), "email", 256).casefold()
    if identity.get("email_verified") is not True:
        raise OidcRuntimeError("oidc_email_not_verified", "OIDC email is not verified", 403)
    audience = identity.get("audience")
    audiences = {str(item) for item in audience} if isinstance(audience, list) else {str(audience)}
    client_id = str(provider.row["client_id"])
    if client_id not in audiences:
        raise OidcRuntimeError("oidc_audience_mismatch", "OIDC audience mismatch", 403)
    if len(audiences) > 1 and str(identity.get("authorized_party") or "") != client_id:
        raise OidcRuntimeError(
            "oidc_authorized_party_mismatch",
            "OIDC authorized party mismatch",
            403,
        )
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain != str(provider.domain["normalized_domain"]).casefold():
        raise OidcRuntimeError(
            "oidc_email_domain_mismatch", "OIDC email domain is not trusted", 403
        )
    return issuer, subject, email


def _mark_failed(
    session: Session,
    *,
    login: Mapping[str, Any],
    code: str,
    now: datetime,
) -> None:
    logins = _table(session, "tenant_oidc_login_transactions")
    session.execute(
        update(logins)
        .where(logins.c.id == login["id"], logins.c.status == "pending")
        .values(
            status="failed",
            failed_at=now,
            error_code=code[:64],
            revision=int(login["revision"]) + 1,
            updated_at=now,
        )
    )


def callback_oidc_login(
    engine: Any,
    *,
    raw_state: str,
    raw_nonce: str | None,
    authorization_code: str,
    oidc_client: OidcRuntimeClient,
    signing_secret: str,
    issue_actor_token: Callable[[str, str, int, datetime, str], str],
    request_id: str,
    request_ip: str,
    user_agent: str,
    now: datetime,
) -> CallbackResult:
    state = _clean(raw_state, "state", 512)
    code = _clean(authorization_code, "code", 4096)
    nonce = str(raw_nonce or "").strip()
    deferred: OidcRuntimeError | None = None
    result: CallbackResult | None = None
    with Session(engine) as session:
        with _transaction(session):
            _ensure_0024(session.connection())
            logins = _table(session, "tenant_oidc_login_transactions")
            digest = _state_digest(state)
            login = (
                session.execute(
                    select(logins).where(logins.c.state_digest == digest).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            stored = str(login["state_digest"]) if login is not None else "0" * 64
            if login is None or not hmac.compare_digest(stored, digest):
                raise OidcRuntimeError("oidc_state_not_found", "OIDC state was not found", 404)
            status = str(login["status"])
            if status == "consumed":
                raise OidcRuntimeError(
                    "oidc_state_consumed", "OIDC state was already consumed", 409
                )
            if status != "pending":
                raise OidcRuntimeError("oidc_state_unavailable", "OIDC state is unavailable", 409)
            expires_at = login["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            if not isinstance(expires_at, datetime) or expires_at <= now:
                session.execute(
                    update(logins)
                    .where(logins.c.id == login["id"])
                    .values(status="expired", revision=int(login["revision"]) + 1, updated_at=now)
                )
                deferred = OidcRuntimeError("oidc_state_expired", "OIDC state expired", 410)
            elif str(login.get("error_code") or "") == _OIDC_EXCHANGE_MARKER:
                raise OidcRuntimeError("oidc_state_unavailable", "OIDC state is unavailable", 409)
            else:
                try:
                    if not nonce or not hmac.compare_digest(
                        str(login["nonce_digest"]), _nonce_digest(nonce)
                    ):
                        raise OidcRuntimeError("oidc_nonce_mismatch", "OIDC nonce mismatch", 403)
                    provider = _provider(
                        session,
                        tenant_id=str(login["tenant_id"]),
                        provider_id=str(login["provider_id"]),
                    )
                    verifier = decrypt_pkce_verifier(
                        str(login["pkce_verifier_ciphertext"]),
                        derive_oidc_fernet_key(
                            signing_secret,
                            key_version=int(login["key_version"]),
                        ),
                    )
                    claim_revision = int(login["revision"]) + 1
                    claimed = session.execute(
                        update(logins)
                        .where(
                            logins.c.id == login["id"],
                            logins.c.status == "pending",
                            logins.c.error_code.is_(None),
                            logins.c.revision == login["revision"],
                        )
                        .values(
                            error_code=_OIDC_EXCHANGE_MARKER,
                            revision=claim_revision,
                            updated_at=now,
                        )
                    )
                    if claimed.rowcount != 1:
                        raise OidcRuntimeError(
                            "oidc_state_unavailable", "OIDC state is unavailable", 409
                        )
                    session.commit()
                    session.close()
                    login = dict(login)
                    login["revision"] = claim_revision
                    login["error_code"] = _OIDC_EXCHANGE_MARKER
                    try:
                        identity = oidc_client.exchange_and_validate(
                            provider=provider.row,
                            code=code,
                            redirect_uri=str(login["redirect_uri"]),
                            code_verifier=verifier,
                            nonce=nonce,
                        )
                    except Exception as exc:
                        raise _identity_error(exc) from exc
                    login = (
                        session.execute(
                            select(logins).where(logins.c.id == login["id"]).with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        login is None
                        or str(login["status"]) != "pending"
                        or str(login.get("error_code") or "") != _OIDC_EXCHANGE_MARKER
                        or int(login["revision"]) != claim_revision
                    ):
                        raise OidcRuntimeError(
                            "oidc_state_unavailable", "OIDC state is unavailable", 409
                        )
                    issuer, subject, email = _validate_identity(provider, identity)
                    account = session.execute(
                        select(Account).where(func.lower(Account.email) == email)
                    ).scalar_one_or_none()
                    if account is None:
                        raise OidcRuntimeError(
                            "oidc_member_required",
                            "OIDC login requires an existing active tenant member",
                            403,
                        )
                    membership = session.execute(
                        select(TenantMember)
                        .where(
                            TenantMember.tenant_id == login["tenant_id"],
                            TenantMember.account_id == account.id,
                            TenantMember.status == "active",
                        )
                        .with_for_update()
                    ).scalar_one_or_none()
                    if membership is None:
                        raise OidcRuntimeError(
                            "oidc_member_required",
                            "OIDC login requires an existing active tenant member",
                            403,
                        )
                    subject_digest = _subject_digest(issuer, subject)
                    links = _table(session, "tenant_oidc_subject_links")
                    subject_link = (
                        session.execute(
                            select(links)
                            .where(
                                links.c.tenant_id == login["tenant_id"],
                                links.c.provider_id == login["provider_id"],
                                links.c.subject_digest == subject_digest,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    account_link = (
                        session.execute(
                            select(links)
                            .where(
                                links.c.tenant_id == login["tenant_id"],
                                links.c.provider_id == login["provider_id"],
                                links.c.account_id == account.id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if subject_link is not None and str(subject_link["account_id"]) != account.id:
                        raise OidcRuntimeError(
                            "oidc_subject_binding_conflict",
                            "OIDC subject is already bound to another account",
                            409,
                        )
                    if (
                        account_link is not None
                        and str(account_link["subject_digest"]) != subject_digest
                    ):
                        raise OidcRuntimeError(
                            "oidc_subject_binding_conflict",
                            "Account is already bound to another OIDC subject",
                            409,
                        )
                    if account_link is None:
                        session.execute(
                            links.insert().values(
                                id=f"oidc-subject-{uuid.uuid4().hex}",
                                tenant_id=login["tenant_id"],
                                provider_id=login["provider_id"],
                                account_id=account.id,
                                issuer=issuer,
                                subject_digest=subject_digest,
                                normalized_email=email,
                                revision=1,
                                last_login_at=now,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    else:
                        session.execute(
                            update(links)
                            .where(links.c.id == account_link["id"])
                            .values(
                                normalized_email=email,
                                last_login_at=now,
                                revision=int(account_link["revision"]) + 1,
                                updated_at=now,
                            )
                        )
                    raw_session_token = "rag4c_sso_" + secrets.token_urlsafe(32)
                    session_id = f"sso-session-{uuid.uuid4().hex}"
                    sessions = _table(session, "tenant_sso_sessions")
                    session_expires = now + timedelta(hours=8)
                    session.execute(
                        sessions.insert().values(
                            id=session_id,
                            session_token_hash=_session_digest(raw_session_token),
                            tenant_id=login["tenant_id"],
                            account_id=account.id,
                            provider_id=login["provider_id"],
                            status="active",
                            expires_at=session_expires,
                            last_seen_at=now,
                            ip_hash=_evidence_hash(
                                signing_secret, b"rag4c:oidc-ip:v1\x00", request_ip
                            ),
                            user_agent_hash=_evidence_hash(
                                signing_secret,
                                b"rag4c:oidc-ua:v1\x00",
                                user_agent,
                            ),
                            revision=1,
                            revoked_at=None,
                            revoked_by=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.execute(
                        update(logins)
                        .where(logins.c.id == login["id"], logins.c.status == "pending")
                        .values(
                            status="consumed",
                            consumed_at=now,
                            error_code=None,
                            revision=int(login["revision"]) + 1,
                            updated_at=now,
                        )
                    )
                    session_row = (
                        session.execute(select(sessions).where(sessions.c.id == session_id))
                        .mappings()
                        .one()
                    )
                    actor_token = issue_actor_token(
                        account.id, str(login["tenant_id"]), 900, now, session_id
                    )
                    _audit(
                        session,
                        tenant_id=str(login["tenant_id"]),
                        actor_id=account.id,
                        actor_name=account.name,
                        actor_email=account.email,
                        action="sso.oidc.login.succeeded",
                        resource_type="tenant_sso_session",
                        resource_id=session_id,
                        after={
                            "provider_id": str(login["provider_id"]),
                            "account_id": account.id,
                            "session": _session_payload(session_row),
                        },
                        request_id=request_id,
                        request_ip=request_ip,
                        now=now,
                    )
                    tenant = session.get(Tenant, str(login["tenant_id"]))
                    if tenant is None:
                        raise OidcRuntimeError(
                            "oidc_tenant_unavailable",
                            "OIDC tenant is unavailable",
                            403,
                        )
                    result = CallbackResult(
                        {
                            "status": "authenticated",
                            "session": _session_payload(session_row),
                            "session_token": raw_session_token,
                            "knowledge_actor_token": actor_token,
                            "actor_token": actor_token,
                            "actor": {
                                "id": account.id,
                                "name": account.name,
                                "email": account.email,
                                "role": membership.role,
                            },
                            "tenant": {
                                "id": str(login["tenant_id"]),
                                "name": tenant.name,
                            },
                            "provider": {
                                "id": str(provider.row["id"]),
                                "name": str(provider.row["name"]),
                            },
                        }
                    )
                except OidcRuntimeError as exc:
                    _mark_failed(session, login=login, code=exc.code, now=now)
                    _audit(
                        session,
                        tenant_id=str(login["tenant_id"]),
                        actor_id="system:oidc",
                        actor_name="OIDC Runtime",
                        actor_email="",
                        action="sso.oidc.login.failed",
                        resource_type="tenant_oidc_login_transaction",
                        resource_id=str(login["id"]),
                        after={"provider_id": str(login["provider_id"]), "error_code": exc.code},
                        request_id=request_id,
                        request_ip=request_ip,
                        now=now,
                    )
                    deferred = exc
                except Exception:
                    session.rollback()
                    if "claim_revision" in locals() and login is not None:
                        with _transaction(session):
                            cleanup_logins = _table(session, "tenant_oidc_login_transactions")
                            session.execute(
                                update(cleanup_logins)
                                .where(
                                    cleanup_logins.c.id == login["id"],
                                    cleanup_logins.c.status == "pending",
                                    cleanup_logins.c.error_code == _OIDC_EXCHANGE_MARKER,
                                )
                                .values(
                                    error_code=None,
                                    revision=claim_revision + 1,
                                    updated_at=now,
                                )
                            )
                    raise
    if deferred is not None:
        raise deferred
    if result is None:
        raise OidcRuntimeError("oidc_runtime_unavailable", "OIDC callback unavailable", 503)
    return result


def revoke_sso_session(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    session_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> dict[str, Any]:
    clean_session = _clean(session_id, "session_id", 64)
    clean_reason = _clean(reason, "reason", 512)
    if type(expected_revision) is not int or expected_revision < 1:
        raise OidcRuntimeError("oidc_request_invalid", "revision is invalid", 422)
    with engine_serialization_lock(engine), Session(engine) as session:
        with _transaction(session):
            _ensure_0024(session.connection())
            actor_row = session.execute(
                select(
                    TenantMember.role,
                    TenantMember.status,
                    Account.name,
                    Account.email,
                    Tenant.status,
                )
                .join(Account, Account.id == TenantMember.account_id)
                .join(Tenant, Tenant.id == TenantMember.tenant_id)
                .where(TenantMember.tenant_id == tenant_id, TenantMember.account_id == actor_id)
                .with_for_update()
            ).one_or_none()
            if (
                actor_row is None
                or str(actor_row.status).casefold() != "active"
                or str(actor_row[4]).casefold() != "active"
            ):
                raise OidcRuntimeError(
                    "oidc_session_revoke_forbidden", "Session revoke forbidden", 403
                )
            sessions = _table(session, "tenant_sso_sessions")
            current = (
                session.execute(
                    select(sessions)
                    .where(
                        sessions.c.id == clean_session,
                        sessions.c.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise OidcRuntimeError("oidc_session_not_found", "SSO session not found", 404)
            role = str(actor_row.role).casefold()
            if str(current["account_id"]) != actor_id and role not in {"owner", "admin"}:
                raise OidcRuntimeError(
                    "oidc_session_revoke_forbidden", "Session revoke forbidden", 403
                )
            if int(current["revision"]) != expected_revision:
                raise OidcRuntimeError(
                    "oidc_session_revision_conflict", "SSO session revision changed", 409
                )
            if str(current["status"]) != "active":
                raise OidcRuntimeError(
                    "oidc_session_state_conflict", "SSO session is not active", 409
                )
            session.execute(
                update(sessions)
                .where(sessions.c.id == clean_session, sessions.c.revision == expected_revision)
                .values(
                    status="revoked",
                    revision=expected_revision + 1,
                    revoked_at=now,
                    revoked_by=actor_id,
                    updated_at=now,
                )
            )
            updated_row = (
                session.execute(select(sessions).where(sessions.c.id == clean_session))
                .mappings()
                .one()
            )
            payload = {"session": _session_payload(updated_row)}
            _audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_name=str(actor_row.name or ""),
                actor_email=str(actor_row.email or ""),
                action="sso.session.revoked",
                resource_type="tenant_sso_session",
                resource_id=clean_session,
                after={**payload["session"], "reason": clean_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            return payload


__all__ = [
    "AuthlibOidcRuntimeClient",
    "CallbackResult",
    "OidcRuntimeClient",
    "OidcRuntimeError",
    "StartResult",
    "callback_oidc_login",
    "decrypt_pkce_verifier",
    "derive_oidc_fernet_key",
    "encrypt_pkce_verifier",
    "revoke_sso_session",
    "start_oidc_login",
]
