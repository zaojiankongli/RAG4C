from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_notification_subscriptions": "TenantNotificationSubscription",
    "tenant_notifications": "TenantNotification",
    "tenant_notification_recipients": "TenantNotificationRecipient",
    "tenant_notification_receipts": "TenantNotificationReceipt",
    "tenant_notification_events": "TenantNotificationEvent",
}


def test_notification_center_orm_models_exist_with_enterprise_constraints() -> None:
    for table_name, class_name in TABLES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, class_name
        table = orm.Base.metadata.tables[table_name]
        assert table is model.__table__
        assert any(isinstance(item, UniqueConstraint) for item in table.constraints)
        assert any(isinstance(item, CheckConstraint) for item in table.constraints)
        assert any(isinstance(item, Index) for item in table.indexes)
        for fk in (item for item in table.constraints if isinstance(item, ForeignKeyConstraint)):
            assert tuple(fk.column_keys)[0] == "tenant_id", (table_name, fk.name)


def _check(table_name: str, name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == name
    )
    return str(constraint.sqltext.compile(compile_kwargs={"literal_binds": True})).casefold()


def test_notification_center_orm_declares_tenant_member_target_and_receipt_identity() -> None:
    member = orm.Base.metadata.tables["tenant_members"]
    uniques = {
        tuple(item.columns.keys())
        for item in member.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert ("tenant_id", "account_id") in uniques

    recipient = orm.Base.metadata.tables["tenant_notification_recipients"]
    receipt = orm.Base.metadata.tables["tenant_notification_receipts"]
    recipient_identity = next(
        item
        for item in recipient.constraints
        if isinstance(item, UniqueConstraint) and item.name == "uq_notification_recipients_identity"
    )
    assert tuple(recipient_identity.columns.keys()) == (
        "tenant_id",
        "notification_id",
        "account_id",
    )
    receipt_fk = next(
        item
        for item in receipt.constraints
        if isinstance(item, ForeignKeyConstraint)
        and item.referred_table.name == "tenant_notification_recipients"
    )
    assert tuple(receipt_fk.column_keys) == ("tenant_id", "notification_id", "account_id")


def test_notification_center_orm_is_body_free_and_has_exact_lifecycles() -> None:
    notification = orm.Base.metadata.tables["tenant_notifications"]
    forbidden = {
        "query",
        "result_body",
        "judgment_note",
        "approval_ticket",
        "email",
        "webhook_url",
        "token",
        "credential",
        "raw_payload",
    }
    assert forbidden.isdisjoint(notification.c.keys())
    assert "source_kind" in _check("tenant_notifications", "ck_tenant_notifications_source_scope")
    assert "target_route_code" in _check("tenant_notifications", "ck_tenant_notifications_route")

    subscription = _check(
        "tenant_notification_subscriptions", "ck_notification_subscriptions_lifecycle"
    )
    assert "archived_at" in subscription
    assert "active_subscription_key" in subscription
    receipt = _check("tenant_notification_receipts", "ck_notification_receipts_lifecycle")
    assert "status='unread'" in receipt
    assert "status='read'" in receipt
    assert "status='archived'" in receipt
    event = _check("tenant_notification_events", "ck_notification_events_hash_chain")
    assert "sequence" in event
    assert "previous_event_digest" in event
