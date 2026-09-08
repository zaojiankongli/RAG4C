from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import re
from typing import Any, Mapping, Sequence
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.orm import (
    Account,
    Tenant,
    TenantAuditEvent,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantScimGroupLink,
    TenantScimToken,
    TenantScimUserLink,
)

ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
REVISION = "0022_scim_provisioning_data_plane"
_FILTER_RE = re.compile(r'^\s*(userName|displayName|externalId)\s+eq\s+"([^"]{1,256})"\s*$')
_ETAG_RE = re.compile(r'^W/"([1-9][0-9]*)"$')


class ScimError(RuntimeError):
    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


@dataclass(frozen=True)
class ScimTokenContext:
    id: str
    tenant_id: str
    prefix: str
    scopes: frozenset[str]
    actor_id: str
    actor_name: str
    actor_email: str


def token_digest(raw: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-scim-token:v1\x00")
    digest.update(raw.encode("utf-8"))
    return digest.hexdigest()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds") + ("Z" if value.tzinfo is None else "")


def _etag(revision: int) -> str:
    return f'W/"{int(revision)}"'


def _version(if_match: str | None) -> int:
    if if_match is None:
        raise ScimError(428, "If-Match header is required")
    match = _ETAG_RE.fullmatch(if_match.strip())
    if match is None:
        raise ScimError(400, "If-Match must be a weak SCIM version ETag", "invalidVers")
    return int(match.group(1))


def _ensure_0022(session: Session) -> None:
    try:
        revisions = tuple(session.execute(select_from_revision()).scalars())
    except Exception as exc:
        raise ScimError(503, "SCIM provisioning schema requires 0022") from exc
    if revisions != (REVISION,):
        raise ScimError(503, "SCIM provisioning schema requires 0022")
    required = {
        "tenant_scim_tokens",
        "tenant_scim_user_links",
        "tenant_scim_group_links",
        "tenant_members",
        "tenant_groups",
        "tenant_group_members",
        "tenant_audit_events",
    }
    bind = session.connection()
    from sqlalchemy import inspect

    if required - set(inspect(bind).get_table_names()):
        raise ScimError(503, "SCIM provisioning schema requires 0022")


def select_from_revision():
    from sqlalchemy import text

    return text("SELECT version_num FROM alembic_version")


def _ip_hash(key: bytes, ip: str) -> str:
    return hmac.new(key, str(ip or "").encode("utf-8"), hashlib.sha256).hexdigest()


def authenticate(
    session: Session,
    raw_token: str | None,
    *,
    required_scope: str | None,
    now: datetime,
    ip_hash_key: bytes,
    request_ip: str,
) -> ScimTokenContext:
    _ensure_0022(session)
    if not raw_token:
        raise ScimError(401, "A valid SCIM bearer token is required")
    digest = token_digest(raw_token)
    token = session.scalar(
        select(TenantScimToken).where(TenantScimToken.token_hash == digest).with_for_update()
    )
    if token is None or not hmac.compare_digest(str(token.token_hash), digest):
        raise ScimError(401, "A valid SCIM bearer token is required")
    if token.status != "active" or token.expires_at <= now:
        raise ScimError(401, "SCIM bearer token is expired or revoked")
    scopes_value = token.scopes or []
    if isinstance(scopes_value, dict):
        scopes = frozenset(str(item) for item in scopes_value.get("items", []))
    else:
        scopes = frozenset(str(item) for item in scopes_value)
    if required_scope is not None and required_scope not in scopes:
        raise ScimError(403, f"SCIM token lacks required scope: {required_scope}")
    actor = session.execute(
        select(Account.name, Account.email)
        .join(TenantMember, TenantMember.account_id == Account.id)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(
            Account.id == token.issued_by,
            TenantMember.tenant_id == token.tenant_id,
            TenantMember.status == "active",
            Tenant.status == "active",
        )
    ).one_or_none()
    if actor is None:
        raise ScimError(401, "SCIM token issuer is no longer an active tenant member")
    token.last_used_at = now
    token.last_used_ip_hash = _ip_hash(ip_hash_key, request_ip)
    token.use_count = int(token.use_count or 0) + 1
    token.updated_at = now
    session.flush()
    return ScimTokenContext(
        id=str(token.id),
        tenant_id=str(token.tenant_id),
        prefix=str(token.token_prefix),
        scopes=scopes,
        actor_id=str(token.issued_by),
        actor_name=str(actor.name or ""),
        actor_email=str(actor.email or ""),
    )


def _write_audit(
    session: Session,
    *,
    token: ScimTokenContext,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip_hash: str,
    target_account_id: str | None = None,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=token.tenant_id,
            actor_id=token.actor_id,
            actor_name_snapshot=token.actor_name,
            actor_email_snapshot=token.actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            target_account_id=target_account_id,
            before_snapshot=dict(before) if before is not None else None,
            after_snapshot=dict(after) if after is not None else None,
            request_id=str(request_id or "")[:128],
            request_ip=request_ip_hash,
            occurred_at=now,
        )
    )
    session.flush()


def parse_filter(value: str | None, *, resource: str) -> tuple[str, str] | None:
    if value is None or not value.strip():
        return None
    match = _FILTER_RE.fullmatch(value)
    if match is None:
        raise ScimError(400, "Only exact eq filters are supported", "invalidFilter")
    field, expected = match.groups()
    allowed = {"externalId", "displayName"}
    if resource == "User":
        allowed.add("userName")
    if field not in allowed:
        raise ScimError(400, "Filter attribute is not supported", "invalidFilter")
    return field, expected


def page_bounds(start_index: int, count: int) -> tuple[int, int]:
    if start_index < 1 or count < 1 or count > 100:
        raise ScimError(400, "startIndex/count are outside supported bounds", "invalidValue")
    return start_index - 1, count


def _user_row(session: Session, tenant_id: str, resource_id: str) -> tuple[Any, ...] | None:
    return session.execute(
        select(TenantScimUserLink, Account, TenantMember)
        .join(Account, Account.id == TenantScimUserLink.account_id)
        .join(
            TenantMember,
            (TenantMember.account_id == TenantScimUserLink.account_id)
            & (TenantMember.tenant_id == TenantScimUserLink.tenant_id),
        )
        .where(TenantScimUserLink.tenant_id == tenant_id, TenantScimUserLink.id == resource_id)
    ).one_or_none()


def user_payload(
    link: TenantScimUserLink, account: Account, membership: TenantMember
) -> dict[str, Any]:
    return {
        "schemas": [USER_SCHEMA],
        "id": str(link.id),
        "externalId": str(link.external_id),
        "userName": str(link.user_name),
        "displayName": str(account.name),
        "active": membership.status == "active",
        "meta": {
            "resourceType": "User",
            "created": _iso(link.created_at),
            "lastModified": _iso(link.updated_at),
            "version": _etag(link.revision),
            "location": f"/scim/v2/Users/{link.id}",
        },
    }


def get_user(session: Session, tenant_id: str, resource_id: str) -> dict[str, Any]:
    row = _user_row(session, tenant_id, resource_id)
    if row is None:
        raise ScimError(404, "SCIM User was not found")
    return user_payload(*row)


def list_users(
    session: Session,
    tenant_id: str,
    *,
    filter_value: str | None,
    start_index: int,
    count: int,
) -> dict[str, Any]:
    offset, limit = page_bounds(start_index, count)
    parsed = parse_filter(filter_value, resource="User")
    statement = (
        select(TenantScimUserLink, Account, TenantMember)
        .join(Account, Account.id == TenantScimUserLink.account_id)
        .join(
            TenantMember,
            (TenantMember.account_id == TenantScimUserLink.account_id)
            & (TenantMember.tenant_id == TenantScimUserLink.tenant_id),
        )
        .where(TenantScimUserLink.tenant_id == tenant_id)
        .order_by(TenantScimUserLink.id)
    )
    if parsed:
        field, value = parsed
        if field == "userName":
            statement = statement.where(TenantScimUserLink.user_name == value.casefold())
        elif field == "externalId":
            statement = statement.where(TenantScimUserLink.external_id == value)
        else:
            statement = statement.where(Account.name == value)
    rows = list(session.execute(statement))
    resources = [user_payload(*row) for row in rows[offset : offset + limit]]
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(rows),
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def create_user(
    session: Session,
    token: ScimTokenContext,
    body: Mapping[str, Any],
    *,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> dict[str, Any]:
    user_name = str(body.get("userName") or "").strip().casefold()
    display_name = str(body.get("displayName") or user_name).strip()
    external_id = str(body.get("externalId") or "").strip()
    active = bool(body.get("active", True))
    if not user_name or "@" not in user_name or not external_id or not display_name:
        raise ScimError(400, "userName, externalId and displayName are required", "invalidValue")
    if session.scalar(
        select(TenantScimUserLink.id).where(
            TenantScimUserLink.tenant_id == token.tenant_id,
            (TenantScimUserLink.user_name == user_name)
            | (TenantScimUserLink.external_id == external_id),
        )
    ):
        raise ScimError(409, "SCIM userName or externalId already exists", "uniqueness")
    account = session.scalar(select(Account).where(func.lower(Account.email) == user_name))
    if account is None:
        account = Account(id=f"account-scim-{uuid.uuid4().hex}", name=display_name, email=user_name)
        session.add(account)
        session.flush()
    membership = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == token.tenant_id,
            TenantMember.account_id == account.id,
        )
    )
    if membership is not None:
        raise ScimError(409, "Account already has tenant membership", "uniqueness")
    membership = TenantMember(
        tenant_id=token.tenant_id,
        account_id=account.id,
        role="member",
        status="active" if active else "suspended",
        revision=1,
        updated_by=token.actor_id,
    )
    session.add(membership)
    session.flush()
    link = TenantScimUserLink(
        id=f"scim-user-{uuid.uuid4().hex}",
        tenant_id=token.tenant_id,
        account_id=account.id,
        external_id=external_id,
        user_name=user_name,
        revision=1,
        last_provisioned_at=now,
        source_token_id=token.id,
        created_at=now,
        updated_at=now,
    )
    session.add(link)
    session.flush()
    payload = user_payload(link, account, membership)
    _write_audit(
        session,
        token=token,
        action="scim.user.created",
        resource_type="scim_user",
        resource_id=link.id,
        before=None,
        after={"external_id": external_id, "active": active, "revision": 1},
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        target_account_id=account.id,
        now=now,
    )
    return payload


def patch_user(
    session: Session,
    token: ScimTokenContext,
    resource_id: str,
    body: Mapping[str, Any],
    *,
    if_match: str | None,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> dict[str, Any]:
    row = _user_row(session, token.tenant_id, resource_id)
    if row is None:
        raise ScimError(404, "SCIM User was not found")
    link, account, membership = row
    expected = _version(if_match)
    if link.revision != expected:
        raise ScimError(412, "SCIM User version is stale")
    before = {
        "displayName": account.name,
        "active": membership.status == "active",
        "revision": link.revision,
    }
    operations = body.get("Operations")
    if not isinstance(operations, Sequence):
        raise ScimError(400, "PATCH Operations are required", "invalidValue")
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ScimError(400, "PATCH operation is invalid", "invalidValue")
        op = str(operation.get("op") or "").casefold()
        path = str(operation.get("path") or "")
        if op != "replace" or path not in {"displayName", "active"}:
            raise ScimError(400, "PATCH path is not supported", "invalidPath")
        if path == "displayName":
            value = str(operation.get("value") or "").strip()
            if not value:
                raise ScimError(400, "displayName cannot be empty", "invalidValue")
            account.name = value
        else:
            membership.status = "active" if bool(operation.get("value")) else "suspended"
            membership.revision += 1
            membership.updated_by = token.actor_id
            membership.updated_at = now
    link.revision += 1
    link.updated_at = now
    link.last_provisioned_at = now
    link.source_token_id = token.id
    session.flush()
    payload = user_payload(link, account, membership)
    _write_audit(
        session,
        token=token,
        action="scim.user.updated",
        resource_type="scim_user",
        resource_id=link.id,
        before=before,
        after={
            "displayName": account.name,
            "active": membership.status == "active",
            "revision": link.revision,
        },
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        target_account_id=account.id,
        now=now,
    )
    return payload


def delete_user(
    session: Session,
    token: ScimTokenContext,
    resource_id: str,
    *,
    if_match: str | None,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> None:
    row = _user_row(session, token.tenant_id, resource_id)
    if row is None:
        raise ScimError(404, "SCIM User was not found")
    link, account, membership = row
    expected = _version(if_match)
    if link.revision != expected:
        raise ScimError(412, "SCIM User version is stale")
    before = {"active": membership.status == "active", "revision": link.revision}
    membership.status = "suspended"
    membership.revision += 1
    membership.updated_by = token.actor_id
    membership.updated_at = now
    link.revision += 1
    link.updated_at = now
    link.last_provisioned_at = now
    link.source_token_id = token.id
    session.flush()
    _write_audit(
        session,
        token=token,
        action="scim.user.deactivated",
        resource_type="scim_user",
        resource_id=link.id,
        before=before,
        after={"active": False, "revision": link.revision},
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        target_account_id=account.id,
        now=now,
    )


def _group_row(session: Session, tenant_id: str, resource_id: str) -> tuple[Any, Any] | None:
    return session.execute(
        select(TenantScimGroupLink, TenantGroup)
        .join(
            TenantGroup,
            (TenantGroup.id == TenantScimGroupLink.group_id)
            & (TenantGroup.tenant_id == TenantScimGroupLink.tenant_id),
        )
        .where(TenantScimGroupLink.tenant_id == tenant_id, TenantScimGroupLink.id == resource_id)
    ).one_or_none()


def group_payload(
    session: Session, link: TenantScimGroupLink, group: TenantGroup
) -> dict[str, Any]:
    member_rows = session.execute(
        select(TenantScimUserLink.id)
        .join(
            TenantGroupMember,
            (TenantGroupMember.account_id == TenantScimUserLink.account_id)
            & (TenantGroupMember.tenant_id == TenantScimUserLink.tenant_id),
        )
        .where(
            TenantGroupMember.tenant_id == link.tenant_id,
            TenantGroupMember.group_id == group.id,
            TenantGroupMember.status == "active",
        )
        .order_by(TenantScimUserLink.id)
    ).scalars()
    members = [
        {"value": value, "$ref": f"/scim/v2/Users/{value}", "type": "User"} for value in member_rows
    ]
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(link.id),
        "externalId": str(link.external_id),
        "displayName": str(link.display_name),
        "members": members,
        "meta": {
            "resourceType": "Group",
            "created": _iso(link.created_at),
            "lastModified": _iso(link.updated_at),
            "version": _etag(link.revision),
            "location": f"/scim/v2/Groups/{link.id}",
        },
    }


def get_group(session: Session, tenant_id: str, resource_id: str) -> dict[str, Any]:
    row = _group_row(session, tenant_id, resource_id)
    if row is None or row[1].status != "active":
        raise ScimError(404, "SCIM Group was not found")
    return group_payload(session, *row)


def _member_accounts(
    session: Session, tenant_id: str, values: Sequence[Mapping[str, Any]]
) -> list[str]:
    accounts: list[str] = []
    for item in values:
        resource_id = str(item.get("value") or "")
        link = session.scalar(
            select(TenantScimUserLink).where(
                TenantScimUserLink.tenant_id == tenant_id,
                TenantScimUserLink.id == resource_id,
            )
        )
        if link is None:
            raise ScimError(400, "Group member does not belong to this tenant", "invalidValue")
        if link.account_id not in accounts:
            accounts.append(link.account_id)
    return accounts


def create_group(
    session: Session,
    token: ScimTokenContext,
    body: Mapping[str, Any],
    *,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> dict[str, Any]:
    display_name = str(body.get("displayName") or "").strip()
    external_id = str(body.get("externalId") or "").strip()
    if not display_name or not external_id:
        raise ScimError(400, "displayName and externalId are required", "invalidValue")
    if session.scalar(
        select(TenantScimGroupLink.id).where(
            TenantScimGroupLink.tenant_id == token.tenant_id,
            (TenantScimGroupLink.display_name == display_name)
            | (TenantScimGroupLink.external_id == external_id),
        )
    ):
        raise ScimError(409, "SCIM group already exists", "uniqueness")
    values = body.get("members") or []
    if not isinstance(values, Sequence):
        raise ScimError(400, "members must be an array", "invalidValue")
    account_ids = _member_accounts(session, token.tenant_id, values)
    group = TenantGroup(
        id=f"group-scim-{uuid.uuid4().hex}",
        tenant_id=token.tenant_id,
        name=display_name,
        normalized_name=display_name.casefold(),
        description="Provisioned by SCIM",
        status="active",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(group)
    session.flush()
    link = TenantScimGroupLink(
        id=f"scim-group-{uuid.uuid4().hex}",
        tenant_id=token.tenant_id,
        group_id=group.id,
        external_id=external_id,
        display_name=display_name,
        revision=1,
        last_provisioned_at=now,
        source_token_id=token.id,
        created_at=now,
        updated_at=now,
    )
    session.add(link)
    for account_id in account_ids:
        session.add(
            TenantGroupMember(
                tenant_id=token.tenant_id,
                group_id=group.id,
                account_id=account_id,
                status="active",
                created_at=now,
                created_by=token.actor_id,
            )
        )
    session.flush()
    payload = group_payload(session, link, group)
    _write_audit(
        session,
        token=token,
        action="scim.group.created",
        resource_type="scim_group",
        resource_id=link.id,
        before=None,
        after={"external_id": external_id, "display_name": display_name, "revision": 1},
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        now=now,
    )
    return payload


def list_groups(
    session: Session, tenant_id: str, *, filter_value: str | None, start_index: int, count: int
) -> dict[str, Any]:
    offset, limit = page_bounds(start_index, count)
    parsed = parse_filter(filter_value, resource="Group")
    statement = (
        select(TenantScimGroupLink, TenantGroup)
        .join(
            TenantGroup,
            (TenantGroup.id == TenantScimGroupLink.group_id)
            & (TenantGroup.tenant_id == TenantScimGroupLink.tenant_id),
        )
        .where(TenantScimGroupLink.tenant_id == tenant_id, TenantGroup.status == "active")
        .order_by(TenantScimGroupLink.id)
    )
    if parsed:
        field, value = parsed
        statement = statement.where(
            TenantScimGroupLink.display_name == value
            if field == "displayName"
            else TenantScimGroupLink.external_id == value
        )
    rows = list(session.execute(statement))
    resources = [group_payload(session, *row) for row in rows[offset : offset + limit]]
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(rows),
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def patch_group(
    session: Session,
    token: ScimTokenContext,
    resource_id: str,
    body: Mapping[str, Any],
    *,
    if_match: str | None,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> dict[str, Any]:
    row = _group_row(session, token.tenant_id, resource_id)
    if row is None or row[1].status != "active":
        raise ScimError(404, "SCIM Group was not found")
    link, group = row
    expected = _version(if_match)
    if link.revision != expected:
        raise ScimError(412, "SCIM Group version is stale")
    before = {"displayName": link.display_name, "revision": link.revision}
    operations = body.get("Operations")
    if not isinstance(operations, Sequence):
        raise ScimError(400, "PATCH Operations are required", "invalidValue")
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ScimError(400, "PATCH operation is invalid", "invalidValue")
        op = str(operation.get("op") or "").casefold()
        pth = str(operation.get("path") or "")
        if op == "replace" and pth == "displayName":
            name = str(operation.get("value") or "").strip()
            if not name:
                raise ScimError(400, "displayName cannot be empty", "invalidValue")
            link.display_name = name
            group.name = name
            group.normalized_name = name.casefold()
            group.revision += 1
        elif op == "add" and pth == "members":
            values = operation.get("value") or []
            account_ids = _member_accounts(session, token.tenant_id, values)
            for account_id in account_ids:
                membership = session.scalar(
                    select(TenantGroupMember).where(
                        TenantGroupMember.tenant_id == token.tenant_id,
                        TenantGroupMember.group_id == group.id,
                        TenantGroupMember.account_id == account_id,
                    )
                )
                if membership is None:
                    session.add(
                        TenantGroupMember(
                            tenant_id=token.tenant_id,
                            group_id=group.id,
                            account_id=account_id,
                            status="active",
                            created_at=now,
                            created_by=token.actor_id,
                        )
                    )
                else:
                    membership.status = "active"
        elif op == "remove":
            match = re.fullmatch(r'members\[value eq "([^"]+)"\]', pth)
            if match is None:
                raise ScimError(400, "PATCH path is not supported", "invalidPath")
            user_link = session.scalar(
                select(TenantScimUserLink).where(
                    TenantScimUserLink.tenant_id == token.tenant_id,
                    TenantScimUserLink.id == match.group(1),
                )
            )
            if user_link is None:
                raise ScimError(400, "Group member is invalid", "invalidValue")
            membership = session.scalar(
                select(TenantGroupMember).where(
                    TenantGroupMember.tenant_id == token.tenant_id,
                    TenantGroupMember.group_id == group.id,
                    TenantGroupMember.account_id == user_link.account_id,
                )
            )
            if membership is not None:
                membership.status = "removed"
        else:
            raise ScimError(400, "PATCH path is not supported", "invalidPath")
    link.revision += 1
    link.updated_at = now
    link.last_provisioned_at = now
    link.source_token_id = token.id
    session.flush()
    payload = group_payload(session, link, group)
    _write_audit(
        session,
        token=token,
        action="scim.group.updated",
        resource_type="scim_group",
        resource_id=link.id,
        before=before,
        after={"displayName": link.display_name, "revision": link.revision},
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        now=now,
    )
    return payload


def delete_group(
    session: Session,
    token: ScimTokenContext,
    resource_id: str,
    *,
    if_match: str | None,
    now: datetime,
    request_id: str,
    request_ip_hash: str,
) -> None:
    row = _group_row(session, token.tenant_id, resource_id)
    if row is None or row[1].status != "active":
        raise ScimError(404, "SCIM Group was not found")
    link, group = row
    expected = _version(if_match)
    if link.revision != expected:
        raise ScimError(412, "SCIM Group version is stale")
    group.status = "archived"
    group.revision += 1
    link.revision += 1
    link.updated_at = now
    link.last_provisioned_at = now
    link.source_token_id = token.id
    for membership in session.scalars(
        select(TenantGroupMember).where(
            TenantGroupMember.tenant_id == token.tenant_id,
            TenantGroupMember.group_id == group.id,
            TenantGroupMember.status == "active",
        )
    ):
        membership.status = "removed"
    session.flush()
    _write_audit(
        session,
        token=token,
        action="scim.group.archived",
        resource_type="scim_group",
        resource_id=link.id,
        before={"status": "active", "revision": expected},
        after={"status": "archived", "revision": link.revision},
        request_id=request_id,
        request_ip_hash=request_ip_hash,
        now=now,
    )
