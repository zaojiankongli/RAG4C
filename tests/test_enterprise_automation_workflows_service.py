from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import ast
import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from core import catalog_schema as catalog_manifest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.orm import (
    Account,
    Tenant,
    TenantAutomationActionRequest,
    TenantAutomationEvent,
    TenantAutomationRule,
    TenantAutomationRuleRevision,
    TenantAutomationRun,
    TenantAutomationSourceCursor,
    TenantMember,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


MODULE = "core.enterprise_automation_workflows_service"
NOW = datetime(2026, 8, 29, 12, 0, 0)
UTC = timezone.utc
TRIGGERS = (
    "task_failed",
    "task_source_stale",
    "source_sync_failed",
    "release_quality_alert_opened",
    "release_recertification_blocked",
    "approval_request_terminal",
)


def service() -> Any:
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        raise AssertionError(f"Stage25 service module is missing: {exc}") from exc


def operation(name: str) -> Callable[..., Any]:
    value = getattr(service(), name, None)
    assert callable(value), f"Stage25 service operation is missing: {name}"
    return value


def body(value: Any) -> dict[str, Any]:
    projected = getattr(value, "body", value)
    assert isinstance(projected, dict)
    return projected


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def common(tenant_id: str = "tenant-a", actor_id: str = "owner-a") -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "account_id": actor_id,
        "request_id": f"request-stage25-{tenant_id}",
        "request_ip": "127.0.0.1",
        "now": NOW,
    }


def revision_fields(
    *,
    trigger_code: str = "task_failed",
    condition_code: str = "always",
    condition_params: dict[str, Any] | None = None,
    action_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "trigger_code": trigger_code,
        "condition_code": condition_code,
        "condition_params": condition_params or {},
        "action_plan": action_plan
        or [
            {
                "action_code": "notify_operator",
                "params": {
                    "category": "automation",
                    "severity": "error",
                    "title": "Automation matched",
                },
            }
        ],
    }


def create_rule(
    engine: Any,
    *,
    name: str = "Failed task alert",
    idempotency_key: str = "create-rule-1",
    **overrides: Any,
) -> dict[str, Any]:
    payload = {
        **common(),
        "name": name,
        "reason": "create a bounded automation rule",
        "idempotency_key": idempotency_key,
        **revision_fields(),
    }
    payload.update(overrides)
    return body(operation("create_automation_rule")(engine, **payload))


def trigger_event(
    *,
    tenant_id: str = "tenant-a",
    trigger_code: str = "task_failed",
    source_stream_id: str = "task-stream-a",
    source_event_id: str = "task-event-a",
    sequence: int = 1,
    status: str = "failed",
    action_required: bool = True,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "trigger_code": trigger_code,
        "source_stream_id": source_stream_id,
        "source_event_id": source_event_id,
        "source_event_digest": digest(
            f"{tenant_id}:{trigger_code}:{source_stream_id}:{source_event_id}:{sequence}"
        ),
        "sequence": sequence,
        "status": status,
        "severity": "error",
        "action_required": action_required,
        "attempt_number": 3,
        "max_attempts": 3,
        "source_current": True,
        "occurred_at": (NOW + timedelta(seconds=sequence)).replace(tzinfo=UTC).isoformat(),
        "safe_facts": {
            "task_id": "task-a",
            "reason_code": "worker_failed",
        },
    }


def add_tenant_b(engine: Any) -> None:
    with Session(engine) as session:
        session.add(Tenant(id="tenant-b", name="Tenant B", plan="enterprise", status="active"))
        session.add(Account(id="owner-b", name="Owner B", email="owner-b@stage25.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="owner-b",
                tenant_id="tenant-b",
                role="owner",
                status="active",
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                updated_by="owner-b",
            )
        )
        session.commit()


def install_trigger_registry(
    monkeypatch: pytest.MonkeyPatch,
    events: dict[str, list[dict[str, Any]]],
    calls: list[str] | None = None,
) -> None:
    target = service()
    calls = calls if calls is not None else []

    def make_adapter(trigger_code: str) -> Callable[..., list[dict[str, Any]]]:
        def adapter(*, session: Any, tenant_id: str, now: datetime) -> list[dict[str, Any]]:
            del session, now
            calls.append(trigger_code)
            return [
                dict(item)
                for item in events.get(trigger_code, [])
                if item.get("tenant_id") == tenant_id
            ]

        return adapter

    monkeypatch.setattr(
        target,
        "TRIGGER_ADAPTER_REGISTRY",
        {trigger_code: make_adapter(trigger_code) for trigger_code in TRIGGERS},
    )


def test_stage25_service_module_exists() -> None:
    assert Path("core/enterprise_automation_workflows_service.py").is_file()


def test_trigger_adapter_registry_is_explicit_and_injectable() -> None:
    target = service()
    assert tuple(target.TRIGGER_ADAPTER_ORDER) == TRIGGERS
    assert tuple(target.TRIGGER_ADAPTER_REGISTRY) == TRIGGERS
    assert all(callable(target.TRIGGER_ADAPTER_REGISTRY[key]) for key in TRIGGERS)


def test_create_rule_is_idempotent_and_persists_rule_revision_and_events(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        first = create_rule(engine)
        replay = create_rule(engine)
        assert first == replay
        assert first["state"] == "applied"
        assert first["resource_id"] == first["rule"]["id"]
        with Session(engine) as session:
            rule = session.get(TenantAutomationRule, first["resource_id"])
            assert rule is not None
            assert rule.tenant_id == "tenant-a"
            assert rule.status == "draft"
            assert rule.current_revision_id == first["revision"]["id"]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(TenantAutomationRuleRevision)
                    .where(TenantAutomationRuleRevision.tenant_id == "tenant-a")
                )
                == 1
            )
            events = list(
                session.scalars(
                    select(TenantAutomationEvent)
                    .where(TenantAutomationEvent.tenant_id == "tenant-a")
                    .order_by(TenantAutomationEvent.stream_key, TenantAutomationEvent.sequence)
                )
            )
            assert [event.event_type for event in events] == [
                "rule_created",
                "revision_created",
            ]
            assert events[0].previous_event_digest is None
            assert events[1].previous_event_digest == events[0].event_digest
    finally:
        engine.dispose()


def test_reads_are_tenant_isolated_and_stably_ordered(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    add_tenant_b(engine)
    try:
        first = create_rule(engine, name="Rule A", idempotency_key="rule-a")
        create_rule(engine, name="Rule B", idempotency_key="rule-b")
        body(
            operation("create_automation_rule")(
                engine,
                **common("tenant-b", "owner-b"),
                name="Tenant B Rule",
                reason="tenant scoped rule",
                idempotency_key="rule-b-tenant",
                **revision_fields(),
            )
        )
        page_one = body(operation("list_automation_rules")(engine, **common(), limit=50))
        page_two = body(operation("list_automation_rules")(engine, **common(), limit=50))
        assert page_one == page_two
        assert page_one["items"]
        assert {item["tenant_id"] for item in page_one["items"]} == {"tenant-a"}
        assert first["resource_id"] in {item["id"] for item in page_one["items"]}
        summary = body(operation("get_automation_summary")(engine, **common()))
        assert summary["tenant_id"] == "tenant-a"
        assert summary["active_rule_count"] == 0
        assert summary["paused_rule_count"] == 0
        assert summary["failed_run_count"] == 0
        assert summary["pending_request_count"] == 0
        tenant_b_page = body(
            operation("list_automation_rules")(engine, **common("tenant-b", "owner-b"), limit=50)
        )
        assert len(tenant_b_page["items"]) == 1
        assert tenant_b_page["items"][0]["tenant_id"] == "tenant-b"
    finally:
        engine.dispose()


def test_revision_digest_fence_activation_and_pause_are_manager_only(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        revision = created["revision"]
        next_revision = body(
            operation("create_rule_revision")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                expected_revision=1,
                expected_definition_digest=revision["definition_digest"],
                reason="add a second bounded revision",
                idempotency_key="create-revision-1",
                **revision_fields(
                    condition_code="status_is", condition_params={"status": "failed"}
                ),
            )
        )
        assert next_revision["revision"]["revision"] == 2
        with pytest.raises(Exception, match="revision|digest|stale|conflict"):
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=next_revision["revision"]["id"],
                expected_revision=1,
                expected_definition_digest=next_revision["revision"]["definition_digest"],
                reason="reject stale activation",
                idempotency_key="activate-stale",
            )
        activated = body(
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=next_revision["revision"]["id"],
                expected_revision=2,
                expected_definition_digest=next_revision["revision"]["definition_digest"],
                reason="activate the immutable revision",
                idempotency_key="activate-1",
            )
        )
        assert activated["rule"]["status"] == "active"
        assert activated["rule"]["current_revision_id"] == next_revision["revision"]["id"]
        with pytest.raises(Exception, match="digest|revision|stale|conflict"):
            operation("pause_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                expected_revision=2,
                expected_definition_digest=revision["definition_digest"],
                reason="reject stale pause",
                idempotency_key="pause-stale",
            )
        paused = body(
            operation("pause_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                expected_revision=2,
                expected_definition_digest=next_revision["revision"]["definition_digest"],
                reason="pause active rule",
                idempotency_key="pause-1",
            )
        )
        assert paused["rule"]["status"] == "paused"

        with Session(engine) as session:
            member = session.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == "tenant-a",
                    TenantMember.account_id == "owner-a",
                )
            )
            assert member is not None
            member.role = "member"
            session.commit()
        with pytest.raises(Exception, match="manage|manager|blocked|authorized"):
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=next_revision["revision"]["id"],
                expected_revision=2,
                expected_definition_digest=next_revision["revision"]["definition_digest"],
                reason="viewer cannot activate",
                idempotency_key="activate-viewer",
            )
    finally:
        engine.dispose()


def test_preview_and_all_reads_are_zero_write(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        event = trigger_event()
        before: dict[str, int] = {}
        models = (
            TenantAutomationRule,
            TenantAutomationRuleRevision,
            TenantAutomationSourceCursor,
            TenantAutomationRun,
            TenantAutomationActionRequest,
            TenantAutomationEvent,
        )
        with Session(engine) as session:
            before = {
                model.__tablename__: int(
                    session.scalar(select(func.count()).select_from(model)) or 0
                )
                for model in models
            }
        preview = body(
            operation("preview_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                trigger_event=event,
                revision_id=created["revision"]["id"],
            )
        )
        assert preview["matched"] is True
        assert preview["side_effects_performed"] is False
        assert preview["dispatch_attempted"] is False
        body(operation("get_automation_summary")(engine, **common()))
        body(operation("list_automation_rules")(engine, **common(), limit=20))
        body(operation("get_automation_rule")(engine, **common(), rule_id=created["resource_id"]))
        body(
            operation("list_rule_revisions")(
                engine, **common(), rule_id=created["resource_id"], limit=20
            )
        )
        body(operation("list_automation_runs")(engine, **common(), limit=20))
        body(operation("list_action_requests")(engine, **common(), limit=20))
        body(operation("list_automation_events")(engine, **common(), limit=20))
        with Session(engine) as session:
            after = {
                model.__tablename__: int(
                    session.scalar(select(func.count()).select_from(model)) or 0
                )
                for model in models
            }
        assert after == before
    finally:
        engine.dispose()


def test_update_rule_is_revision_fenced_and_idempotent(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        current = body(
            operation("list_rule_revisions")(
                engine, **common(), rule_id=created["resource_id"], limit=20
            )
        )["items"][0]
        update_kwargs = {
            **common(),
            "rule_id": created["resource_id"],
            "expected_revision": 1,
            "expected_definition_digest": current["definition_digest"],
            "name": "Updated failed task alert",
            "priority": 20,
            "reason": "update bounded rule metadata",
            "idempotency_key": "update-rule-1",
        }
        updated = body(operation("update_automation_rule")(engine, **update_kwargs))
        replayed = body(operation("update_automation_rule")(engine, **update_kwargs))

        assert updated == replayed
        assert updated["state"] == "applied"
        assert updated["resource_id"] == created["resource_id"]
        assert updated["rule"]["name"] == "Updated failed task alert"
        assert updated["rule"]["priority"] == 20
        assert updated["revision"]["definition_digest"] == current["definition_digest"]

        detail = body(
            operation("get_automation_rule")(engine, **common(), rule_id=created["resource_id"])
        )
        assert detail["rule"]["name"] == "Updated failed task alert"
        assert detail["rule"]["priority"] == 20

        with pytest.raises(Exception, match="revision|digest|stale|conflict"):
            operation("update_automation_rule")(
                engine,
                **{
                    **update_kwargs,
                    "idempotency_key": "update-rule-stale",
                    "expected_revision": 2,
                },
            )
    finally:
        engine.dispose()


def test_list_rules_status_all_is_equivalent_to_unfiltered_reads(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        create_rule(engine, idempotency_key="status-all-a")
        create_rule(engine, name="Second rule", idempotency_key="status-all-b")
        all_rows = body(
            operation("list_automation_rules")(engine, **common(), status="all", limit=50)
        )
        unfiltered = body(
            operation("list_automation_rules")(engine, **common(), status=None, limit=50)
        )
        assert all_rows == unfiltered
    finally:
        engine.dispose()


def test_service_serializers_emit_the_frontend_canonical_wire_shapes() -> None:
    target = service()
    rule = SimpleNamespace(
        id="rule-a",
        tenant_id="tenant-a",
        name="Rule A",
        normalized_name="rule a",
        status="active",
        active_rule_key="rule a",
        revision=2,
        current_revision_id="revision-a",
        workspace_id=None,
        dataset_id=None,
        priority=100,
        created_at=NOW,
        created_by="owner-a",
        updated_at=NOW,
        updated_by="owner-a",
        archived_at=None,
        archived_by=None,
    )
    revision = SimpleNamespace(
        id="revision-a",
        tenant_id="tenant-a",
        rule_id="rule-a",
        revision=2,
        trigger_code="task_failed",
        condition_code="always",
        condition_params_json={},
        action_plan_json=[
            {
                "step_index": 0,
                "action_code": "notify_operator",
                "params": {"category": "automation", "severity": "error", "title": "Alert"},
            }
        ],
        definition_digest=digest("revision-a"),
        created_at=NOW,
        created_by="owner-a",
    )
    run = SimpleNamespace(
        id="run-a",
        tenant_id="tenant-a",
        rule_id="rule-a",
        rule_revision_id="revision-a",
        trigger_event_id="event-a",
        trigger_event_digest=digest("event-a"),
        status="completed",
        condition_matched=True,
        action_count=1,
        requested_count=1,
        rejected_count=0,
        idempotency_digest=digest("run-a"),
        started_at=NOW,
        completed_at=NOW,
        safe_error_code=None,
        safe_error=None,
        created_at=NOW,
        updated_at=NOW,
    )
    action = SimpleNamespace(
        id="action-a",
        tenant_id="tenant-a",
        run_id="run-a",
        rule_id="rule-a",
        step_index=0,
        action_code="notify_operator",
        status="requested",
        target_kind=None,
        target_id=None,
        target_revision=None,
        target_digest=None,
        idempotency_key_digest=digest("action-a"),
        safe_params_json={"category": "automation", "severity": "error", "title": "Alert"},
        safe_reason="matched",
        approval_request_id=None,
        notification_id=None,
        task_id=None,
        requested_at=NOW,
        dispatched_at=None,
        applied_at=None,
        rejected_at=None,
        expires_at=NOW + timedelta(days=1),
        created_at=NOW,
        updated_at=NOW,
    )
    event = SimpleNamespace(
        id="event-a",
        tenant_id="tenant-a",
        rule_id="rule-a",
        run_id=None,
        stream_key="rule:rule-a",
        sequence=1,
        event_type="rule_created",
        previous_event_digest=None,
        event_digest=digest("event-a"),
        actor_id="owner-a",
        request_id="request-a",
        safe_snapshot_json={"rule_revision": 1},
        occurred_at=NOW,
    )

    assert set(target._rule_body(rule, revision)) == {
        "id",
        "tenant_id",
        "name",
        "status",
        "revision",
        "current_revision_id",
        "workspace_id",
        "dataset_id",
        "priority",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
    }
    assert set(target._revision_body(revision)) == {
        "id",
        "tenant_id",
        "rule_id",
        "revision",
        "trigger_code",
        "condition_code",
        "condition_params_json",
        "action_plan_json",
        "definition_digest",
        "created_at",
        "created_by",
    }
    assert set(target._run_body(run)) == {
        "id",
        "tenant_id",
        "rule_id",
        "rule_revision_id",
        "trigger_event_id",
        "trigger_event_digest",
        "status",
        "condition_matched",
        "action_count",
        "requested_count",
        "rejected_count",
        "started_at",
        "completed_at",
        "safe_error_code",
        "safe_error",
    }
    assert set(target._action_body(action)) == {
        "id",
        "tenant_id",
        "run_id",
        "rule_id",
        "step_index",
        "action_code",
        "status",
        "target_kind",
        "target_id",
        "target_revision",
        "target_digest",
        "safe_params_json",
        "safe_reason",
        "approval_request_id",
        "notification_id",
        "task_id",
        "requested_at",
        "dispatched_at",
        "applied_at",
        "rejected_at",
        "expires_at",
    }
    assert set(target._event_body(event)) == {
        "id",
        "tenant_id",
        "rule_id",
        "run_id",
        "stream_key",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot_json",
        "occurred_at",
    }


def test_observe_persists_requested_actions_runs_events_and_cursor_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        body(
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=created["revision"]["id"],
                expected_revision=1,
                expected_definition_digest=created["revision"]["definition_digest"],
                reason="activate for observation",
                idempotency_key="activate-observe",
            )
        )
        calls: list[str] = []
        install_trigger_registry(monkeypatch, {"task_failed": [trigger_event()]}, calls)
        observed = body(
            operation("observe_automation_events")(
                engine,
                **common(),
                trigger_codes=["task_failed"],
                reason="observe fixed trigger adapters",
                idempotency_key="observe-1",
            )
        )
        assert observed["state"] == "applied"
        assert observed["observed_count"] == 1
        assert observed["run_count"] == 1
        assert observed["action_request_count"] == 1
        assert calls == ["task_failed"]
        with Session(engine) as session:
            run = session.scalar(
                select(TenantAutomationRun).where(TenantAutomationRun.tenant_id == "tenant-a")
            )
            action = session.scalar(
                select(TenantAutomationActionRequest).where(
                    TenantAutomationActionRequest.tenant_id == "tenant-a"
                )
            )
            cursor = session.scalar(
                select(TenantAutomationSourceCursor).where(
                    TenantAutomationSourceCursor.tenant_id == "tenant-a",
                    TenantAutomationSourceCursor.rule_id == created["resource_id"],
                )
            )
            events = list(
                session.scalars(
                    select(TenantAutomationEvent)
                    .where(
                        TenantAutomationEvent.tenant_id == "tenant-a",
                        TenantAutomationEvent.run_id == run.id,
                    )
                    .order_by(TenantAutomationEvent.sequence)
                )
            )
        assert run is not None and run.status == "requested"
        assert action is not None and action.status == "requested"
        assert action.notification_id is None
        assert action.approval_request_id is None
        assert action.task_id is None
        assert cursor is not None and cursor.status == "idle"
        assert cursor.last_sequence == 1
        assert [event.event_type for event in events] == [
            "run_started",
            "trigger_observed",
            "action_requested",
            "run_completed",
        ]
        assert all(
            event.sequence == index and (index == 1 or event.previous_event_digest)
            for index, event in enumerate(events, start=1)
        )
        assert catalog_manifest.inspect_enterprise_automation_workflows_capability(engine) == (
            "ready",
            (),
        )
    finally:
        engine.dispose()


def test_observe_replays_source_event_without_duplicate_run_action_or_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        body(
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=created["revision"]["id"],
                expected_revision=1,
                expected_definition_digest=created["revision"]["definition_digest"],
                reason="activate replay rule",
                idempotency_key="activate-replay",
            )
        )
        event = trigger_event()
        install_trigger_registry(monkeypatch, {"task_failed": [event]})
        first = body(
            operation("observe_automation_events")(
                engine,
                **common(),
                trigger_codes=["task_failed"],
                reason="observe once",
                idempotency_key="observe-replay-1",
            )
        )
        second = body(
            operation("observe_automation_events")(
                engine,
                **common(),
                trigger_codes=["task_failed"],
                reason="observe replay",
                idempotency_key="observe-replay-2",
            )
        )
        assert first["run_count"] == 1
        assert second["run_count"] == 0
        assert second["replayed_count"] >= 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TenantAutomationRun)) == 1
            assert (
                session.scalar(select(func.count()).select_from(TenantAutomationActionRequest)) == 1
            )
            assert session.scalar(select(func.count()).select_from(TenantAutomationEvent)) == 7
    finally:
        engine.dispose()


def test_cursor_lease_blocks_live_owner_and_allows_expired_reclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        body(
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=created["revision"]["id"],
                expected_revision=1,
                expected_definition_digest=created["revision"]["definition_digest"],
                reason="activate lease rule",
                idempotency_key="activate-lease",
            )
        )
        event = trigger_event()
        with Session(engine) as session:
            session.add(
                TenantAutomationSourceCursor(
                    id="cursor-lease-a",
                    tenant_id="tenant-a",
                    rule_id=created["resource_id"],
                    source_kind="task_failed",
                    source_stream_id="task-stream-a",
                    last_sequence=0,
                    last_event_digest=None,
                    status="claimed",
                    lease_owner="other-worker",
                    lease_until=NOW + timedelta(minutes=5),
                    revision=1,
                    updated_at=NOW,
                )
            )
            session.commit()
        install_trigger_registry(monkeypatch, {"task_failed": [event]})
        blocked = body(
            operation("observe_automation_events")(
                engine,
                **common(),
                trigger_codes=["task_failed"],
                reason="respect live cursor lease",
                idempotency_key="observe-lease-blocked",
            )
        )
        assert blocked["blocked_count"] == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TenantAutomationRun)) == 0
        reclaimed = body(
            operation("observe_automation_events")(
                engine,
                **{**common(), "now": NOW + timedelta(minutes=10)},
                trigger_codes=["task_failed"],
                reason="reclaim expired cursor lease",
                idempotency_key="observe-lease-reclaim",
            )
        )
        assert reclaimed["run_count"] == 1
    finally:
        engine.dispose()


def test_concurrent_observation_with_same_idempotency_key_creates_one_fact_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        created = create_rule(engine)
        body(
            operation("activate_rule")(
                engine,
                **common(),
                rule_id=created["resource_id"],
                revision_id=created["revision"]["id"],
                expected_revision=1,
                expected_definition_digest=created["revision"]["definition_digest"],
                reason="activate concurrent rule",
                idempotency_key="activate-concurrent",
            )
        )
        install_trigger_registry(monkeypatch, {"task_failed": [trigger_event()]})

        def observe() -> dict[str, Any]:
            return body(
                operation("observe_automation_events")(
                    engine,
                    **common(),
                    trigger_codes=["task_failed"],
                    reason="concurrent observation",
                    idempotency_key="observe-concurrent",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: observe(), range(2)))
        assert results[0] == results[1]
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TenantAutomationRun)) == 1
            assert (
                session.scalar(select(func.count()).select_from(TenantAutomationActionRequest)) == 1
            )
    finally:
        engine.dispose()


def test_service_rejects_unsafe_rule_definition_and_has_no_dynamic_execution_surface() -> None:
    with pytest.raises(Exception, match="unsupported|unsafe|forbidden|action"):
        operation("create_automation_rule")(
            None,
            **common(),
            name="unsafe rule",
            reason="reject unsafe definition",
            idempotency_key="unsafe-rule",
            trigger_code="task_failed",
            condition_code="always",
            condition_params={"query": "status=failed"},
            action_plan=[
                {
                    "action_code": "notify_operator",
                    "params": {
                        "category": "automation",
                        "severity": "error",
                        "title": "https://unsafe.example",
                    },
                }
            ],
        )
    tree = ast.parse(Path("core/enterprise_automation_workflows_service.py").read_text("utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(
        name.startswith(("importlib", "requests", "httpx", "subprocess", "server"))
        for name in imports
    )
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"eval", "exec", "__import__", "getattr"}.isdisjoint(calls)
