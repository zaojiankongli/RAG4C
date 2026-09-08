"""Tenant-scoped default Release Channel provisioning."""

from __future__ import annotations

from datetime import datetime
from hashlib import md5

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.orm import TenantReleaseChannel

DEFAULT_RELEASE_CHANNELS = (
    ("development", "Development", "low", 10, False),
    ("testing", "Testing", "medium", 20, False),
    ("production", "Production", "high", 30, True),
)


def default_release_channel_id(tenant_id: str, code: str) -> str:
    tenant_bytes = tenant_id.encode("utf-8")
    payload = (
        b"rag4c:tenant-release-channel:v2:"
        + str(len(tenant_bytes)).encode("ascii")
        + b":"
        + tenant_bytes
        + b":"
        + code.encode("utf-8")
    )
    return "release-channel-" + md5(payload, usedforsecurity=False).hexdigest()


def ensure_default_release_channels_in_session(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str = "system:tenant-release-channel-provisioner",
    now: datetime | None = None,
) -> list[TenantReleaseChannel]:
    existing = list(
        session.scalars(
            select(TenantReleaseChannel)
            .where(TenantReleaseChannel.tenant_id == tenant_id)
            .order_by(TenantReleaseChannel.promotion_order, TenantReleaseChannel.id)
        )
    )
    by_code = {str(row.normalized_code).casefold(): row for row in existing}
    active_default = next(
        (row for row in existing if row.status == "active" and bool(row.is_default_serving)),
        None,
    )
    current = now or datetime.utcnow()
    for code, name, risk_tier, promotion_order, is_default in DEFAULT_RELEASE_CHANNELS:
        if code in by_code:
            continue
        make_default = bool(is_default and active_default is None)
        row = TenantReleaseChannel(
            id=default_release_channel_id(tenant_id, code),
            tenant_id=tenant_id,
            code=code,
            normalized_code=code,
            name=name,
            status="active",
            risk_tier=risk_tier,
            promotion_order=promotion_order,
            is_default_serving=make_default,
            active_default_slot="default" if make_default else None,
            revision=1,
            created_at=current,
            created_by=actor_id,
            updated_at=current,
            updated_by=actor_id,
        )
        session.add(row)
        existing.append(row)
        by_code[code] = row
        if make_default:
            active_default = row
    session.flush()
    return existing


__all__ = [
    "DEFAULT_RELEASE_CHANNELS",
    "default_release_channel_id",
    "ensure_default_release_channels_in_session",
]
