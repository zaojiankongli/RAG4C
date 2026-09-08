from __future__ import annotations

from datetime import datetime, timedelta
import importlib
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.orm import Account, TenantMember, TenantNotificationSubscription
from test_enterprise_notification_receipts import _seed_engine

NOW = datetime(2026, 8, 29, 12, 0, 0)
SUBSCRIPTION_MODULE = "core.enterprise_notification_subscriptions"


def _module() -> Any:
    try:
        return importlib.import_module(SUBSCRIPTION_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 22 Task 4 RED contract: {SUBSCRIPTION_MODULE} is required: {exc}")


def _api(name: str) -> Callable[..., Any]:
    operation = getattr(_module(), name, None)
    if not callable(operation):
        pytest.fail(f"Stage 22 Task 4 RED contract: {SUBSCRIPTION_MODULE}.{name} is required")
    return operation


def _body(value: Any) -> dict[str, Any]:
    body = getattr(value, "body", value)
    assert isinstance(body, dict)
    return body


def _add_member(session: Session, account_id: str, role: str = "member") -> None:
    session.add(Account(id=account_id, name=account_id, email=f"{account_id}@test.invalid"))
    session.flush()
    session.add(
        TenantMember(
            account_id=account_id,
            tenant_id="tenant-a",
            role=role,
            status="active",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
            updated_by=account_id,
        )
    )


def _seed_subscription(
    engine: Any,
    *,
    subscription_id: str,
    account_id: str,
    category: str = "quality",
    status: str = "active",
    preference: str = "subscribed",
    minimum_severity: str = "warning",
    muted_until: datetime | None = None,
    revision: int = 1,
) -> None:
    with Session(engine) as session:
        session.add(
            TenantNotificationSubscription(
                id=subscription_id,
                tenant_id="tenant-a",
                account_id=account_id,
                category=category,
                status=status,
                preference=preference,
                active_subscription_key=(
                    f"{account_id}:{category}" if status == "active" else None
                ),
                revision=revision,
                minimum_severity=minimum_severity,
                muted_until=muted_until,
                created_at=NOW,
                created_by=account_id,
                updated_at=NOW,
                updated_by=account_id,
                archived_at=NOW if status == "archived" else None,
                archived_by=account_id if status == "archived" else None,
            )
        )
        session.commit()


def test_subscription_reads_are_actor_scoped_and_filter_active_archived(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        with Session(engine) as session:
            _add_member(session, "admin-a", role="admin")
            session.commit()
        _seed_subscription(engine, subscription_id="sub-owner-quality", account_id="owner-a")
        _seed_subscription(
            engine,
            subscription_id="sub-member-approval",
            account_id="member-a",
            category="approval",
        )
        _seed_subscription(
            engine,
            subscription_id="sub-owner-archived",
            account_id="owner-a",
            category="approval",
            status="archived",
        )
        list_subscriptions = _api("list_notification_subscriptions")
        owner = _body(list_subscriptions(engine, tenant_id="tenant-a", actor_id="owner-a", now=now))
        active = _body(
            list_subscriptions(
                engine, tenant_id="tenant-a", actor_id="owner-a", status="active", now=now
            )
        )
        archived = _body(
            list_subscriptions(
                engine, tenant_id="tenant-a", actor_id="owner-a", status="archived", now=now
            )
        )
        assert {row["account_id"] for row in owner["items"]} == {"owner-a"}
        assert {row["id"] for row in active["items"]} == {"sub-owner-quality"}
        assert {row["id"] for row in archived["items"]} == {"sub-owner-archived"}
    finally:
        engine.dispose()


def test_update_subscription_is_actor_only_revision_fenced_and_idempotent(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_subscription(engine, subscription_id="sub-owner-quality", account_id="owner-a")
        _seed_subscription(engine, subscription_id="sub-member-quality", account_id="member-a")
        update = _api("update_notification_subscription")
        kwargs = dict(
            tenant_id="tenant-a",
            actor_id="owner-a",
            subscription_id="sub-owner-quality",
            expected_revision=1,
            preference="muted",
            minimum_severity="critical",
            muted_until=now + timedelta(hours=2),
            reason="quiet non-critical quality signals",
            idempotency_key="subscription-update-1",
            request_id="subscription-request-1",
            now=now,
        )
        first = _body(update(engine, **kwargs))
        replay = _body(update(engine, **kwargs))
        assert first == replay
        assert first["operation"] == "update_notification_subscription"
        assert first["resource_id"] == "sub-owner-quality"
        assert first["retryable"] is False
        with pytest.raises(Exception, match="revision"):
            update(
                engine,
                **{
                    **kwargs,
                    "expected_revision": 1,
                    "idempotency_key": "subscription-stale-1",
                    "request_id": "subscription-stale-request",
                },
            )
        with pytest.raises(Exception, match="subscription|scope|actor"):
            update(
                engine,
                **{
                    **kwargs,
                    "subscription_id": "sub-member-quality",
                    "idempotency_key": "subscription-cross-account",
                    "request_id": "subscription-cross-account-request",
                },
            )
        with Session(engine) as session:
            row = session.get(TenantNotificationSubscription, "sub-member-quality")
            assert row is not None and row.revision == 1 and row.preference == "subscribed"
    finally:
        engine.dispose()


def test_subscription_can_archive_and_restore_with_canonical_active_key(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_subscription(engine, subscription_id="sub-owner-quality", account_id="owner-a")
        update = _api("update_notification_subscription")
        archived = _body(
            update(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                subscription_id="sub-owner-quality",
                expected_revision=1,
                status="archived",
                preference="subscribed",
                minimum_severity="warning",
                muted_until=None,
                reason="archive preference",
                idempotency_key="subscription-archive-1",
                request_id="subscription-archive-request",
                now=now,
            )
        )
        assert archived["state"] == "applied"
        with Session(engine) as session:
            archived_row = session.get(TenantNotificationSubscription, "sub-owner-quality")
            assert archived_row is not None
            assert archived_row.status == "archived"
            assert archived_row.active_subscription_key is None
        restored = _body(
            update(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                subscription_id="sub-owner-quality",
                expected_revision=2,
                status="active",
                preference="muted",
                minimum_severity="warning",
                muted_until=now + timedelta(hours=1),
                reason="restore quiet preference",
                idempotency_key="subscription-restore-1",
                request_id="subscription-restore-request",
                now=now,
            )
        )
        assert restored["state"] == "applied"
        with Session(engine) as session:
            restored_row = session.get(TenantNotificationSubscription, "sub-owner-quality")
            assert restored_row is not None
            assert restored_row.status == "active"
            assert restored_row.active_subscription_key == "owner-a:quality"
            assert restored_row.archived_at is None
            assert restored_row.archived_by is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("preference", "minimum_severity", "muted_until"),
    [
        ("muted", "warning", None),
        ("subscribed", "warning", NOW + timedelta(hours=1)),
        ("subscribed", "notice", None),
        ("muted", "critical", NOW - timedelta(minutes=1)),
    ],
)
def test_subscription_rejects_invalid_preference_severity_and_mute_horizon(
    tmp_path: Path, preference: str, minimum_severity: str, muted_until: datetime | None
) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_subscription(engine, subscription_id="sub-owner-quality", account_id="owner-a")
        with pytest.raises(Exception, match="preference|severity|muted|future"):
            _api("update_notification_subscription")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                subscription_id="sub-owner-quality",
                expected_revision=1,
                preference=preference,
                minimum_severity=minimum_severity,
                muted_until=muted_until,
                reason="invalid preference fixture",
                idempotency_key=f"subscription-invalid-{preference}-{minimum_severity}",
                request_id="subscription-invalid-request",
                now=now,
            )
    finally:
        engine.dispose()


def test_subscription_mutation_audits_safe_snapshot_without_raw_reason(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_subscription(engine, subscription_id="sub-owner-quality", account_id="owner-a")
        _api("update_notification_subscription")(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            subscription_id="sub-owner-quality",
            expected_revision=1,
            preference="muted",
            minimum_severity="critical",
            muted_until=now + timedelta(hours=1),
            reason="change preference with a safe operator explanation",
            idempotency_key="subscription-audit-1",
            request_id="subscription-audit-request",
            account_id="owner-a",
            request_ip="127.0.0.20",
            now=now,
        )
        from models.orm import TenantAuditEvent

        with Session(engine) as session:
            audit = session.scalar(
                select(TenantAuditEvent)
                .where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.resource_type == "tenant_notification_subscription",
                )
                .order_by(TenantAuditEvent.sequence.desc())
            )
        assert audit is not None
        assert audit.action == "notification.subscription.updated"
        assert "safe operator explanation" not in repr(audit.after_snapshot)
        assert audit.after_snapshot["preference"] == "muted"
        assert audit.request_ip == "127.0.0.20"
    finally:
        engine.dispose()
