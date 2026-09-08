from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.enterprise_notification_center import (
    NotificationAuthorityInvalid,
    canonical_assignment_digest,
    canonical_notification_digest,
    canonical_notification_event,
    canonical_notification_key,
    project_notification_payload,
    project_notification_route,
    project_notification_source,
)

UTC = timezone.utc


def test_digest_is_domain_separated_deterministic_and_utc_microsecond_precise() -> None:
    first = canonical_notification_digest(
        "notification",
        {
            "b": 2,
            "a": 1,
            "occurred_at": "2026-08-29T20:00:00.123456+08:00",
        },
    )
    second = canonical_notification_digest(
        "notification",
        {
            "occurred_at": datetime(2026, 8, 29, 12, 0, 0, 123456, tzinfo=UTC),
            "a": 1,
            "b": 2,
        },
    )
    assert first == second
    assert first != canonical_notification_digest("notification-event", {"a": 1, "b": 2})

    with pytest.raises(NotificationAuthorityInvalid):
        canonical_notification_digest("notification", {"unsafe": float("nan")})


def quality_source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source_kind": "quality_alert",
        "tenant_id": "tenant-a",
        "source_id": "alert-a",
        "source_revision": 4,
        "source_dataset_id": "dataset-a",
        "source_digest": "a" * 64,
        "severity": "critical",
        "event_semantic": "opened",
        "occurred_at": "2026-08-29T20:00:00.123456+08:00",
        "safe_facts": {
            "release_id": "release-a",
            "channel_id": "channel-a",
            "release_role": "active",
            "alert_type": "certification_expired",
            "occurrence_count": 2,
        },
    }
    value.update(overrides)
    return value


def approval_source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source_kind": "approval_pending_for_me",
        "tenant_id": "tenant-a",
        "source_id": "approval-a",
        "source_revision": 2,
        "source_digest": "b" * 64,
        "severity": "warning",
        "event_semantic": "pending",
        "occurred_at": "2026-08-29T12:00:00.000001Z",
        "safe_facts": {
            "action_type": "release_promote",
            "policy_id": "approval-policy-a",
            "step": 1,
        },
    }
    value.update(overrides)
    return value


def test_project_source_allows_only_two_source_kinds_and_normalizes_safe_authority() -> None:
    projected = project_notification_source(quality_source())
    assert projected["source_kind"] == "quality_alert"
    assert projected["category"] == "quality"
    assert projected["source_dataset_id"] == "dataset-a"
    assert projected["source_revision"] == 4
    assert projected["severity"] == "critical"
    assert projected["occurred_at"] == "2026-08-29T12:00:00.123456Z"
    assert projected["safe_facts"] == {
        "alert_type": "certification_expired",
        "channel_id": "channel-a",
        "occurrence_count": 2,
        "release_id": "release-a",
        "release_role": "active",
    }

    approval = project_notification_source(approval_source())
    assert approval["source_kind"] == "approval_pending_for_me"
    assert approval["category"] == "approval"
    assert approval["source_dataset_id"] is None

    with pytest.raises(NotificationAuthorityInvalid, match="source_kind"):
        project_notification_source(quality_source(source_kind="audit_event"))

    with pytest.raises(NotificationAuthorityInvalid, match="source_dataset_id"):
        project_notification_source(approval_source(source_dataset_id="dataset-a"))


def test_source_projection_rejects_non_exact_revision_and_invalid_digest() -> None:
    with pytest.raises(NotificationAuthorityInvalid, match="exact integer"):
        project_notification_source(quality_source(source_revision=True))
    with pytest.raises(NotificationAuthorityInvalid, match="SHA-256"):
        project_notification_source(quality_source(source_digest="not-a-digest"))
    with pytest.raises(NotificationAuthorityInvalid, match="severity"):
        project_notification_source(quality_source(severity="notice"))


def test_project_route_enforces_exact_route_parameter_schemas() -> None:
    quality_route = project_notification_route(
        "knowledge_quality_operations",
        {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "release_id": "release-a",
            "channel_id": "channel-a",
            "alert_id": "alert-a",
        },
    )
    assert quality_route == {
        "target_route_code": "knowledge_quality_operations",
        "target_route_params_json": {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "release_id": "release-a",
            "channel_id": "channel-a",
            "alert_id": "alert-a",
        },
    }

    approval_route = project_notification_route(
        route_code="enterprise_approval",
        params={"tenant_id": "tenant-a", "approval_request_id": "approval-a"},
    )
    assert approval_route["target_route_code"] == "enterprise_approval"

    with pytest.raises(NotificationAuthorityInvalid, match="route"):
        project_notification_route("/enterprise/notifications", {})
    with pytest.raises(NotificationAuthorityInvalid, match="exact"):
        project_notification_route(
            "enterprise_approval",
            {"tenant_id": "tenant-a", "approval_request_id": "approval-a", "extra": "x"},
        )
    with pytest.raises(NotificationAuthorityInvalid, match="parameter"):
        project_notification_route(
            "knowledge_quality_operations",
            {
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "release_id": "release-a",
                "channel_id": "channel-a",
            },
        )


def test_payload_composes_source_route_and_exact_flags_into_body_free_projection() -> None:
    payload = project_notification_payload(
        source=quality_source(),
        route=project_notification_route(
            "knowledge_quality_operations",
            {
                "tenant_id": "tenant-a",
                "dataset_id": "dataset-a",
                "release_id": "release-a",
                "channel_id": "channel-a",
                "alert_id": "alert-a",
            },
        ),
        action_required=True,
        mandatory=False,
        title_code="quality_alert_open",
        summary_code="quality_alert_requires_attention",
    )
    assert len(payload["notification_key"]) == 64
    assert payload["notification_key"] == canonical_notification_key(
        "quality_alert", "alert-a", 4, "opened"
    )
    assert payload["category"] == "quality"
    assert payload["action_required"] is True
    assert payload["mandatory"] is False
    assert payload["occurred_at"] == "2026-08-29T12:00:00.123456Z"
    assert payload["target_route_code"] == "knowledge_quality_operations"
    assert payload["target_route_params_json"]["alert_id"] == "alert-a"
    assert payload["safe_facts_json"]["alert_type"] == "certification_expired"
    assert payload["target_route_code"] == "knowledge_quality_operations"
    assert payload["target_route_params_json"]["alert_id"] == "alert-a"
    assert payload["safe_facts_json"]["alert_type"] == "certification_expired"

    with pytest.raises(NotificationAuthorityInvalid, match="exact boolean"):
        project_notification_payload(
            source=quality_source(),
            route=project_notification_route(
                "knowledge_quality_operations",
                {
                    "tenant_id": "tenant-a",
                    "dataset_id": "dataset-a",
                    "release_id": "release-a",
                    "channel_id": "channel-a",
                    "alert_id": "alert-a",
                },
            ),
            action_required=1,
            mandatory=False,
            title_code="quality_alert_open",
            summary_code="quality_alert_requires_attention",
        )

    with pytest.raises(NotificationAuthorityInvalid, match="category"):
        project_notification_payload(
            source=quality_source(),
            route=project_notification_route(
                "knowledge_quality_operations",
                {
                    "tenant_id": "tenant-a",
                    "dataset_id": "dataset-a",
                    "release_id": "release-a",
                    "channel_id": "channel-a",
                    "alert_id": "alert-a",
                },
            ),
            category="approval",
            action_required=True,
            mandatory=False,
            title_code="quality_alert_open",
            summary_code="quality_alert_requires_attention",
        )


def test_canonical_notification_key_supports_revision_or_cycle_and_is_unambiguous() -> None:
    revision_key = canonical_notification_key(
        source_kind="quality_alert",
        source_id="alert-a",
        source_revision=4,
        event_semantic="opened",
    )
    assert len(revision_key) == 64
    assert revision_key == canonical_notification_key(
        source_kind="quality_alert",
        source_id="alert-a",
        source_revision=4,
        event_semantic="opened",
    )
    assert revision_key != canonical_notification_key(
        source_kind="quality_alert",
        source_id="alert-a",
        source_revision=4,
        event_semantic="escalated",
    )
    cycle_key = canonical_notification_key(
        "approval_pending_for_me",
        "approval-a",
        cycle_key="approval-cycle-a",
        event_type="pending",
    )
    assert len(cycle_key) == 64
    assert (
        canonical_notification_key(
            {
                "source_kind": "quality_alert",
                "source_id": "alert-a",
                "source_revision": 4,
                "event_semantic": "opened",
            }
        )
        == revision_key
    )

    with pytest.raises(NotificationAuthorityInvalid, match="exactly one"):
        canonical_notification_key("quality_alert", "alert-a", 4, "opened", cycle_key="cycle-a")
    with pytest.raises(NotificationAuthorityInvalid, match="exact integer"):
        canonical_notification_key("quality_alert", "alert-a", True, "opened")


def test_assignment_digest_is_deterministic_and_requires_exact_assignment_facts() -> None:
    first = canonical_assignment_digest(
        {
            "tenant_id": "tenant-a",
            "notification_id": "notification-a",
            "account_id": "account-a",
            "recipient_reason": "dataset_owner",
            "mandatory": True,
        }
    )
    second = canonical_assignment_digest(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        recipient_reason="dataset_owner",
        mandatory=True,
    )
    assert first == second
    assert first == canonical_assignment_digest(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        recipient_reason="dataset_owner",
        mandatory=True,
    )
    assert first != canonical_assignment_digest(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        recipient_reason="dataset_owner",
        mandatory=False,
    )
    with pytest.raises(NotificationAuthorityInvalid, match="exact boolean"):
        canonical_assignment_digest(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            recipient_reason="dataset_owner",
            mandatory=1,
        )


def test_notification_event_builds_deterministic_hash_chain_and_rejects_forgery() -> None:
    first = canonical_notification_event(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        sequence=1,
        event_type="materialized",
        previous_event_digest=None,
        actor_id="system:notification-materializer",
        request_id="materialize-a",
        safe_snapshot={"status": "unread", "revision": 1},
        occurred_at="2026-08-29T20:00:00.123456+08:00",
    )
    assert first["event_digest"]
    assert len(first["event_digest"]) == 64
    assert first["previous_event_digest"] is None
    assert first["occurred_at"] == "2026-08-29T12:00:00.123456Z"

    second = canonical_notification_event(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        sequence=2,
        event_type="marked_read",
        previous_event_digest=first["event_digest"],
        actor_id="account-a",
        request_id="receipt-a",
        safe_snapshot={"status": "read", "revision": 2},
        occurred_at="2026-08-29T12:01:00.000000Z",
    )
    assert second["previous_event_digest"] == first["event_digest"]
    assert second["event_digest"] != first["event_digest"]
    assert second == canonical_notification_event(
        tenant_id="tenant-a",
        notification_id="notification-a",
        account_id="account-a",
        sequence=2,
        event_type="marked_read",
        previous_event_digest=first["event_digest"],
        actor_id="account-a",
        request_id="receipt-a",
        safe_snapshot={"revision": 2, "status": "read"},
        occurred_at="2026-08-29T12:01:00+00:00",
    )

    with pytest.raises(NotificationAuthorityInvalid, match="previous"):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=1,
            event_type="materialized",
            previous_event_digest="a" * 64,
            actor_id="system:notification-materializer",
            request_id="materialize-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )
    with pytest.raises(NotificationAuthorityInvalid, match="event_digest"):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=1,
            event_type="materialized",
            previous_event_digest=None,
            actor_id="system:notification-materializer",
            request_id="materialize-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
            event_digest="f" * 64,
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"query": "customer password=secret"},
        {"body": "raw result body"},
        {"note": "reviewer note"},
        {"ticket": "opaque-ticket"},
        {"email": "person@example.com"},
        {"token": "bearer topsecret"},
        {"credential": "secret://value"},
        {"source_url": "https://example.com/private"},
    ],
)
def test_source_route_payload_and_event_fail_closed_for_unsafe_content(
    unsafe: dict[str, str],
) -> None:
    with pytest.raises(NotificationAuthorityInvalid):
        project_notification_source(quality_source(safe_facts=unsafe))

    with pytest.raises(NotificationAuthorityInvalid):
        canonical_notification_digest("unsafe", unsafe)

    with pytest.raises(NotificationAuthorityInvalid):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=1,
            event_type="materialized",
            previous_event_digest=None,
            actor_id="system:notification-materializer",
            request_id="event-a",
            safe_snapshot=unsafe,
            occurred_at="2026-08-29T12:00:00Z",
        )


def test_event_requires_nonempty_request_identity() -> None:
    with pytest.raises(NotificationAuthorityInvalid, match="request_id"):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=1,
            event_type="materialized",
            previous_event_digest=None,
            actor_id="system:notification-materializer",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )


def test_safe_fact_values_reject_raw_query_body_note_and_ticket_text() -> None:
    for value in (
        "select private customer records",
        "raw result body",
        "private reviewer note",
        "opaque-ticket-123",
    ):
        with pytest.raises(NotificationAuthorityInvalid):
            project_notification_source(quality_source(safe_facts={"details": value}))


def test_event_type_and_snapshot_are_strict_and_module_has_no_orm_import() -> None:
    with pytest.raises(NotificationAuthorityInvalid, match="event_type"):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=1,
            event_type="opened",
            previous_event_digest=None,
            actor_id="account-a",
            request_id="event-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )
    with pytest.raises(NotificationAuthorityInvalid, match="SHA-256"):
        canonical_notification_event(
            tenant_id="tenant-a",
            notification_id="notification-a",
            account_id="account-a",
            sequence=2,
            event_type="marked_read",
            previous_event_digest="bad",
            actor_id="account-a",
            request_id="event-a",
            safe_snapshot={},
            occurred_at="2026-08-29T12:00:00Z",
        )

    import ast
    from pathlib import Path

    tree = ast.parse(Path("core/enterprise_notification_center.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_names = {
        alias.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in imported_modules
    )
    assert "orm" not in imported_names
