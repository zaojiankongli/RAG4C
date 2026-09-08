from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import importlib
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_notification_center_capability
from core.enterprise_notification_center import (
    canonical_assignment_digest,
    canonical_notification_event,
)
from models.orm import (
    Account,
    TenantAuditEvent,
    TenantMember,
    TenantNotification,
    TenantNotificationEvent,
    TenantNotificationReceipt,
    TenantNotificationRecipient,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine

NOW = datetime(2026, 8, 29, 12, 0, 0)
RECEIPT_MODULE = "core.enterprise_notification_receipts"


def _module() -> Any:
    try:
        return importlib.import_module(RECEIPT_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 22 Task 4 RED contract: {RECEIPT_MODULE} is required: {exc}")


def _api(name: str) -> Callable[..., Any]:
    operation = getattr(_module(), name, None)
    if not callable(operation):
        pytest.fail(f"Stage 22 Task 4 RED contract: {RECEIPT_MODULE}.{name} is required")
    return operation


def _body(value: Any) -> dict[str, Any]:
    body = getattr(value, "body", value)
    assert isinstance(body, dict)
    return body


def _add_account_member(
    session: Session,
    *,
    account_id: str,
    role: str = "member",
    tenant_id: str = "tenant-a",
) -> None:
    session.add(Account(id=account_id, name=account_id, email=f"{account_id}@test.invalid"))
    session.flush()
    session.add(
        TenantMember(
            account_id=account_id,
            tenant_id=tenant_id,
            role=role,
            status="active",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
            updated_by=account_id,
        )
    )


def _notification_id(label: str) -> str:
    return f"notification-{label}"


def _seed_notification(
    engine: Any,
    *,
    notification_id: str = "notification-a",
    account_id: str = "owner-a",
    with_receipt: bool = True,
    source_kind: str = "quality_alert",
    source_revision: int = 1,
    source_digest: str | None = None,
    source_dataset_id: str | None = "dataset-a",
    route_params: dict[str, str] | None = None,
    severity: str = "warning",
    mandatory: bool = False,
) -> None:
    source_digest = source_digest or sha256(notification_id.encode()).hexdigest()
    route_params = route_params or {
        "dataset_id": "dataset-a",
        "section": "releases",
        "alert_id": notification_id,
    }
    category = "quality" if source_kind == "quality_alert" else "approval"
    route_code = (
        "knowledge_quality_operations" if source_kind == "quality_alert" else "enterprise_approval"
    )
    if source_kind != "quality_alert":
        source_dataset_id = None
        route_params = {"request_id": notification_id}

    with Session(engine) as session:
        notification = TenantNotification(
            id=notification_id,
            tenant_id="tenant-a",
            source_kind=source_kind,
            source_id=notification_id,
            source_revision=source_revision,
            source_dataset_id=source_dataset_id,
            category=category,
            severity=severity,
            action_required=True,
            mandatory=mandatory,
            notification_key=sha256(f"key:{notification_id}".encode()).hexdigest(),
            source_digest=source_digest,
            title_code="quality_attention" if category == "quality" else "approval_attention",
            summary_code="release_quality_attention"
            if category == "quality"
            else "approval_pending",
            safe_facts_json={"source_id": notification_id, "severity": severity},
            target_route_code=route_code,
            target_route_params_json=route_params,
            occurred_at=NOW,
            created_at=NOW,
            created_by="system:notification-materializer",
        )
        session.add(notification)
        session.flush()
        recipient_reason = "tenant_owner" if account_id == "owner-a" else "explicit_subscription"
        session.add(
            TenantNotificationRecipient(
                id=f"recipient-{notification_id}-{account_id}",
                tenant_id="tenant-a",
                notification_id=notification_id,
                account_id=account_id,
                recipient_reason=recipient_reason,
                mandatory=mandatory,
                assignment_digest=canonical_assignment_digest(
                    tenant_id="tenant-a",
                    notification_id=notification_id,
                    account_id=account_id,
                    recipient_reason=recipient_reason,
                    mandatory=mandatory,
                    assigned_at=NOW,
                ),
                assigned_at=NOW,
            )
        )
        session.flush()
        if with_receipt:
            session.add(
                TenantNotificationReceipt(
                    id=f"receipt-{notification_id}-{account_id}",
                    tenant_id="tenant-a",
                    notification_id=notification_id,
                    account_id=account_id,
                    status="unread",
                    revision=1,
                    read_at=None,
                    archived_at=None,
                    updated_at=NOW,
                )
            )
            session.flush()
            event = canonical_notification_event(
                tenant_id="tenant-a",
                notification_id=notification_id,
                account_id=account_id,
                sequence=1,
                event_type="materialized",
                previous_event_digest=None,
                actor_id="system:notification-materializer",
                request_id=f"materialize-{notification_id}",
                safe_snapshot={"status": "unread", "notification_id": notification_id},
                occurred_at=NOW,
            )
            session.add(
                TenantNotificationEvent(
                    id=f"event-{notification_id}-{account_id}-1",
                    tenant_id="tenant-a",
                    notification_id=notification_id,
                    account_id=account_id,
                    sequence=event["sequence"],
                    event_type=event["event_type"],
                    previous_event_digest=event["previous_event_digest"],
                    event_digest=event["event_digest"],
                    actor_id=event["actor_id"],
                    request_id=event["request_id"],
                    safe_snapshot_json=event["safe_snapshot"],
                    occurred_at=NOW,
                )
            )
        session.commit()


def _seed_engine(tmp_path: Path) -> tuple[Any, datetime]:
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        _add_account_member(session, account_id="member-a")
        session.commit()
    return engine, NOW


def _count(session: Session, model: Any) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_reads_are_per_account_and_summary_counts_only_existing_receipts(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-owner", account_id="owner-a")
        _seed_notification(engine, notification_id="notification-member", account_id="member-a")
        _seed_notification(
            engine,
            notification_id="notification-without-receipt",
            account_id="owner-a",
            with_receipt=False,
        )
        list_notifications = _api("list_notifications")
        get_summary = _api("get_notification_summary")
        owner_page = _body(
            list_notifications(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                status="all",
                now=now,
            )
        )
        member_page = _body(
            list_notifications(
                engine,
                tenant_id="tenant-a",
                actor_id="member-a",
                status="all",
                now=now,
            )
        )
        owner_summary = _body(
            get_summary(engine, tenant_id="tenant-a", actor_id="owner-a", now=now)
        )
        member_summary = _body(
            get_summary(engine, tenant_id="tenant-a", actor_id="member-a", now=now)
        )
        assert {item["notification"]["id"] for item in owner_page["items"]} == {
            "notification-owner"
        }
        assert {item["notification"]["id"] for item in member_page["items"]} == {
            "notification-member"
        }
        assert owner_summary["unread_count"] == 1
        assert member_summary["unread_count"] == 1
        assert owner_summary["unread_state"] == "count"
        assert all(item["receipt"]["account_id"] == "owner-a" for item in owner_page["items"])
    finally:
        engine.dispose()


def test_missing_receipt_is_unavailable_and_reads_do_not_materialize_or_mutate(
    tmp_path: Path,
) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(
            engine,
            notification_id="notification-no-receipt",
            account_id="owner-a",
            with_receipt=False,
        )
        get_notification = _api("get_notification")
        get_summary = _api("get_notification_summary")
        list_notifications = _api("list_notifications")
        with pytest.raises(Exception, match="unavailable"):
            get_notification(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-no-receipt",
                now=now,
            )
        with Session(engine) as session:
            before_receipts = _count(session, TenantNotificationReceipt)
        page = _body(
            list_notifications(
                engine, tenant_id="tenant-a", actor_id="owner-a", status="all", now=now
            )
        )
        summary = _body(get_summary(engine, tenant_id="tenant-a", actor_id="owner-a", now=now))
        assert page["items"] == []
        assert summary["unread_count"] == 0
        assert summary["unread_state"] == "zero"
        with Session(engine) as session:
            assert _count(session, TenantNotificationReceipt) == before_receipts == 0
    finally:
        engine.dispose()


def test_detail_and_summary_are_pure_and_include_safe_route_handoff_state(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-route")
        get_notification = _api("get_notification")
        with Session(engine) as session:
            before = {
                "receipts": _count(session, TenantNotificationReceipt),
                "events": _count(session, TenantNotificationEvent),
                "audits": _count(session, TenantAuditEvent),
            }
        detail = _body(
            get_notification(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-route",
                now=now,
            )
        )
        assert detail["receipt"]["status"] == "unread"
        assert detail["events"][0]["event_type"] == "materialized"
        assert detail["handoff"]["state"] == "unavailable"
        assert detail["handoff"]["reason_code"] in {
            "quality_source_unavailable",
            "dataset_access_unavailable",
        }
        assert detail["notification"]["target_route_params_json"]["dataset_id"] == "dataset-a"
        with Session(engine) as session:
            after = {
                "receipts": _count(session, TenantNotificationReceipt),
                "events": _count(session, TenantNotificationEvent),
                "audits": _count(session, TenantAuditEvent),
            }
        assert after == before
    finally:
        engine.dispose()


def test_forged_event_digest_fails_detail_and_readiness_authority(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-forged-event")
        with Session(engine) as session:
            first = session.scalar(
                select(TenantNotificationEvent).where(
                    TenantNotificationEvent.tenant_id == "tenant-a",
                    TenantNotificationEvent.notification_id == "notification-forged-event",
                    TenantNotificationEvent.account_id == "owner-a",
                    TenantNotificationEvent.sequence == 1,
                )
            )
            assert first is not None
            session.add(
                TenantNotificationEvent(
                    id="f" * 64,
                    tenant_id="tenant-a",
                    notification_id="notification-forged-event",
                    account_id="owner-a",
                    sequence=2,
                    event_type="marked_read",
                    previous_event_digest=str(first.event_digest),
                    event_digest="e" * 64,
                    actor_id="owner-a",
                    request_id="request-forged-event",
                    safe_snapshot_json={
                        "status": "read",
                        "revision": 2,
                        "receipt_id": "receipt-notification-forged-event-owner-a",
                    },
                    occurred_at=now + timedelta(minutes=1),
                )
            )
            session.commit()

        with pytest.raises(Exception, match="digest|event|authority|unavailable"):
            _api("get_notification")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-forged-event",
                now=now + timedelta(minutes=2),
            )

        state, issues = inspect_enterprise_notification_center_capability(engine)
        assert state == "unavailable"
        assert any(
            "canonical" in issue.casefold() or "digest" in issue.casefold() for issue in issues
        )
    finally:
        engine.dispose()


def test_read_mutations_are_revision_fenced_audited_and_hash_chained(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-lifecycle")
        read = _api("mark_notification_read")
        unread = _api("mark_notification_unread")
        archive = _api("archive_notification")
        first = _body(
            read(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-lifecycle",
                expected_revision=1,
                reason="review the quality signal",
                idempotency_key="receipt-read-1",
                request_id="request-read-1",
                account_id="owner-a",
                request_ip="127.0.0.10",
                now=now,
            )
        )
        assert first["state"] == "applied"
        assert first["operation"] == "mark_notification_read"
        assert first["resource_id"] == "receipt-notification-lifecycle-owner-a"
        assert first["retryable"] is False
        with pytest.raises(Exception, match="revision"):
            unread(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-lifecycle",
                expected_revision=1,
                reason="stale operator view",
                idempotency_key="receipt-unread-stale",
                request_id="request-unread-stale",
                now=now,
            )
        second = _body(
            unread(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-lifecycle",
                expected_revision=2,
                reason="return it to the unread queue",
                idempotency_key="receipt-unread-1",
                request_id="request-unread-1",
                account_id="owner-a",
                request_ip="127.0.0.10",
                now=now + timedelta(minutes=1),
            )
        )
        third = _body(
            archive(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-lifecycle",
                expected_revision=3,
                reason="archive the reviewed signal",
                idempotency_key="receipt-archive-1",
                request_id="request-archive-1",
                account_id="owner-a",
                request_ip="127.0.0.10",
                now=now + timedelta(minutes=2),
            )
        )
        assert second["state"] == "applied"
        assert third["state"] == "applied"
        with Session(engine) as session:
            receipt = session.get(
                TenantNotificationReceipt, "receipt-notification-lifecycle-owner-a"
            )
            events = list(
                session.scalars(
                    select(TenantNotificationEvent)
                    .where(
                        TenantNotificationEvent.tenant_id == "tenant-a",
                        TenantNotificationEvent.notification_id == "notification-lifecycle",
                        TenantNotificationEvent.account_id == "owner-a",
                    )
                    .order_by(TenantNotificationEvent.sequence)
                )
            )
            audits = list(
                session.scalars(
                    select(TenantAuditEvent)
                    .where(
                        TenantAuditEvent.tenant_id == "tenant-a",
                        TenantAuditEvent.resource_type == "tenant_notification_receipt",
                    )
                    .order_by(TenantAuditEvent.sequence)
                )
            )
        assert receipt is not None and receipt.status == "archived" and receipt.revision == 4
        assert [event.event_type for event in events] == [
            "materialized",
            "marked_read",
            "marked_unread",
            "archived",
        ]
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        assert all(
            events[index].previous_event_digest == events[index - 1].event_digest
            for index in range(1, len(events))
        )
        assert [audit.action for audit in audits] == [
            "notification.receipt.marked_read",
            "notification.receipt.marked_unread",
            "notification.receipt.archived",
        ]
        assert all("reason" not in (audit.after_snapshot or {}) for audit in audits)
        assert [audit.request_ip for audit in audits] == ["127.0.0.10"] * 3
    finally:
        engine.dispose()


def test_same_idempotency_key_replays_without_a_second_event_and_conflict_is_safe(
    tmp_path: Path,
) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-replay")
        read = _api("mark_notification_read")
        kwargs = dict(
            tenant_id="tenant-a",
            actor_id="owner-a",
            notification_id="notification-replay",
            expected_revision=1,
            reason="replay-safe receipt transition",
            idempotency_key="receipt-replay-1",
            request_id="request-replay-1",
            now=now,
        )
        first = _body(read(engine, **kwargs))
        replay = _body(read(engine, **kwargs))
        assert replay == first
        with Session(engine) as session:
            assert _count(session, TenantNotificationEvent) == 2
        with pytest.raises(Exception, match="idempotency"):
            read(
                engine,
                **{**kwargs, "expected_revision": 2, "request_id": "request-replay-conflict"},
            )
    finally:
        engine.dispose()


def test_bulk_read_is_bounded_to_200_and_uses_deterministic_identity_order(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        ids = [_notification_id(str(index)) for index in range(3)]
        for notification_id in ids:
            _seed_notification(engine, notification_id=notification_id)
        bulk = _api("bulk_mark_notifications_read")
        with pytest.raises(Exception, match="200"):
            bulk(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                items=[
                    {"notification_id": f"too-many-{index}", "expected_revision": 1}
                    for index in range(201)
                ],
                reason="bulk review",
                idempotency_key="bulk-too-many",
                request_id="bulk-too-many-request",
                now=now,
            )
        result = _body(
            bulk(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                items=[
                    {"notification_id": notification_id, "expected_revision": 1}
                    for notification_id in reversed(ids)
                ],
                reason="bulk review",
                idempotency_key="bulk-read-1",
                request_id="bulk-read-request",
                now=now,
            )
        )
        assert result["state"] == "applied"
        assert result["operation"] == "bulk_mark_notifications_read"
        assert result["resource_id"] is None
        with Session(engine) as session:
            rows = list(
                session.scalars(
                    select(TenantNotificationReceipt).where(
                        TenantNotificationReceipt.tenant_id == "tenant-a",
                        TenantNotificationReceipt.account_id == "owner-a",
                    )
                )
            )
        assert {row.status for row in rows} == {"read"}
        assert {row.revision for row in rows} == {2}
    finally:
        engine.dispose()


def test_cross_account_or_cross_tenant_receipt_mutation_is_rejected(tmp_path: Path) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(engine, notification_id="notification-owner-only", account_id="owner-a")
        read = _api("mark_notification_read")
        with pytest.raises(Exception):
            read(
                engine,
                tenant_id="tenant-a",
                actor_id="member-a",
                notification_id="notification-owner-only",
                expected_revision=1,
                reason="should not cross recipient scope",
                idempotency_key="cross-account-read",
                request_id="cross-account-request",
                now=now,
            )
        with pytest.raises(Exception):
            read(
                engine,
                tenant_id="tenant-b",
                actor_id="owner-a",
                notification_id="notification-owner-only",
                expected_revision=1,
                reason="should not cross tenant scope",
                idempotency_key="cross-tenant-read",
                request_id="cross-tenant-request",
                now=now,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "route_params",
    [
        {
            "dataset_id": "dataset-a",
            "section": "releases",
            "alert_id": "alert-a",
            "href": "https://bad",
        },
        {"dataset_id": "dataset-a", "section": "documents"},
    ],
)
def test_route_handoff_rejects_unsafe_or_malformed_route_facts(
    tmp_path: Path, route_params: dict[str, str]
) -> None:
    engine, now = _seed_engine(tmp_path)
    try:
        _seed_notification(
            engine, notification_id="notification-unsafe-route", route_params=route_params
        )
        with pytest.raises(Exception, match="unavailable|route"):
            _api("get_notification")(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                notification_id="notification-unsafe-route",
                now=now,
            )
    finally:
        engine.dispose()
