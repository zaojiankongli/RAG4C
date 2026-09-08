from __future__ import annotations

import ast
from pathlib import Path

import pytest

try:
    from core.enterprise_automation_workflows import (
        AutomationAuthorityInvalid,
        AUTOMATION_ACTION_CODES,
        AUTOMATION_CONDITION_CODES,
        AUTOMATION_TRIGGER_CODES,
        canonical_automation_action_plan,
        canonical_automation_action_request_digest,
        canonical_automation_event,
        canonical_automation_event_digest,
        canonical_automation_rule_revision,
        canonical_automation_run_digest,
        canonical_automation_trigger_event,
        evaluate_automation_condition,
        preview_automation_rule,
        project_automation_source_route,
    )
except ModuleNotFoundError as exc:
    pytest.fail(f"Stage25 pure automation authority is missing: {exc}")

DIGEST = "a" * 64
TIME = "2026-08-30T00:00:00.000000Z"


def trigger_event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "trigger_code": "task_failed",
        "source_stream_id": "task-stream-a",
        "source_event_id": "task-event-a",
        "source_event_digest": DIGEST,
        "sequence": 7,
        "status": "failed",
        "severity": "error",
        "action_required": True,
        "attempt_number": 3,
        "max_attempts": 3,
        "source_current": True,
        "occurred_at": TIME,
        "safe_facts": {"task_id": "task-a", "error_code": "worker_failed"},
    }
    value.update(overrides)
    return value


def revision(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "tenant_id": "tenant-a",
        "rule_id": "rule-a",
        "rule_revision_id": "rule-revision-a",
        "revision": 1,
        "trigger_code": "task_failed",
        "condition_code": "attempt_exhausted",
        "condition_params": {"minimum_attempts": 3},
        "action_plan": [
            {
                "action_code": "notify_operator",
                "params": {
                    "category": "task_operations",
                    "severity": "error",
                    "title": "任务重试次数已耗尽",
                },
            }
        ],
        "created_at": TIME,
        "created_by": "owner-a",
    }
    value.update(overrides)
    return value


def test_allowlists_are_exact_and_exclude_generic_execution() -> None:
    assert AUTOMATION_TRIGGER_CODES == {
        "task_failed",
        "task_source_stale",
        "source_sync_failed",
        "release_quality_alert_opened",
        "release_recertification_blocked",
        "approval_request_terminal",
    }
    assert AUTOMATION_CONDITION_CODES == {
        "always",
        "status_is",
        "action_required",
        "severity_at_least",
        "attempt_exhausted",
        "source_is_stale",
    }
    assert AUTOMATION_ACTION_CODES == {
        "notify_operator",
        "request_approval",
        "open_task_attention",
        "pause_rule",
    }


def test_rule_revision_canonicalizes_definition_and_digest() -> None:
    result = canonical_automation_rule_revision(revision())
    assert result["trigger_code"] == "task_failed"
    assert result["condition_params"] == {"minimum_attempts": 3}
    assert len(result["definition_digest"]) == 64
    assert (
        canonical_automation_rule_revision(
            {**revision(), "definition_digest": result["definition_digest"]}
        )
        == result
    )
    with pytest.raises(AutomationAuthorityInvalid, match="definition_digest"):
        canonical_automation_rule_revision({**revision(), "definition_digest": "f" * 64})


def test_condition_schemas_are_trigger_aware_and_exact() -> None:
    valid = {
        "always": {},
        "status_is": {"status": "failed"},
        "action_required": {"value": True},
        "severity_at_least": {"severity": "warning"},
        "attempt_exhausted": {"minimum_attempts": 2},
        "source_is_stale": {"value": True},
    }
    for code, params in valid.items():
        canonical_automation_rule_revision(revision(condition_code=code, condition_params=params))
    with pytest.raises(AutomationAuthorityInvalid, match="condition"):
        canonical_automation_rule_revision(
            revision(condition_code="expression", condition_params={"query": "status == failed"})
        )
    with pytest.raises(AutomationAuthorityInvalid, match="condition"):
        canonical_automation_rule_revision(
            revision(condition_code="always", condition_params={"unexpected": True})
        )


def test_action_plan_is_bounded_exact_and_rejects_unsafe_execution_fields() -> None:
    plan = canonical_automation_action_plan(revision()["action_plan"])
    assert len(plan) == 1
    with pytest.raises(AutomationAuthorityInvalid, match="1 to 4"):
        canonical_automation_action_plan([])
    with pytest.raises(AutomationAuthorityInvalid, match="1 to 4"):
        canonical_automation_action_plan(revision()["action_plan"] * 5)
    for field, value in {
        "url": "https://example.com",
        "webhook": "https://example.com/hook",
        "sql": "DELETE FROM tenants",
        "code": "import os",
        "token": "secret-token",
        "query": "status=failed",
    }.items():
        with pytest.raises(AutomationAuthorityInvalid, match="forbidden|unsafe|unsupported"):
            canonical_automation_action_plan(
                [{"action_code": "notify_operator", "params": {field: value}}]
            )


def test_trigger_event_is_tenant_scoped_digest_fenced_and_safe() -> None:
    event = canonical_automation_trigger_event(trigger_event())
    assert event["tenant_id"] == "tenant-a"
    assert event["source_event_digest"] == DIGEST
    assert event["source_current"] is True
    with pytest.raises(AutomationAuthorityInvalid, match="digest"):
        canonical_automation_trigger_event(trigger_event(source_event_digest="bad"))
    with pytest.raises(AutomationAuthorityInvalid, match="forbidden|unsafe"):
        canonical_automation_trigger_event(trigger_event(safe_facts={"document_body": "secret"}))


def test_condition_evaluation_is_pure_and_deterministic() -> None:
    event = canonical_automation_trigger_event(trigger_event())
    assert evaluate_automation_condition("always", {}, event) is True
    assert evaluate_automation_condition("status_is", {"status": "failed"}, event) is True
    assert evaluate_automation_condition("action_required", {"value": True}, event) is True
    assert (
        evaluate_automation_condition("severity_at_least", {"severity": "warning"}, event) is True
    )
    assert (
        evaluate_automation_condition("attempt_exhausted", {"minimum_attempts": 3}, event) is True
    )
    assert evaluate_automation_condition("source_is_stale", {"value": True}, event) is False


def test_preview_returns_bounded_requests_without_execution() -> None:
    result = preview_automation_rule(revision(), trigger_event())
    assert result["matched"] is True
    assert result["action_requests"] == canonical_automation_action_plan(revision()["action_plan"])
    assert result["side_effects_performed"] is False
    assert result["dispatch_attempted"] is False
    no_match = preview_automation_rule(
        revision(condition_code="status_is", condition_params={"status": "running"}),
        trigger_event(),
    )
    assert no_match["matched"] is False
    assert no_match["action_requests"] == []


def test_event_and_request_digests_are_canonical() -> None:
    event = canonical_automation_event(
        tenant_id="tenant-a",
        rule_id="rule-a",
        run_id="run-a",
        stream_key="run:run-a",
        sequence=1,
        event_type="run_started",
        previous_event_digest=None,
        actor_id="system:automation",
        request_id="request-a",
        safe_snapshot={"condition_matched": True},
        occurred_at=TIME,
    )
    assert event["event_digest"] == canonical_automation_event_digest(
        {key: value for key, value in event.items() if key != "event_digest"}
    )
    assert len(canonical_automation_action_request_digest({"action_code": "notify_operator"})) == 64
    assert len(canonical_automation_run_digest({"rule_id": "rule-a"})) == 64


def test_event_chain_requires_valid_first_and_previous_digest() -> None:
    with pytest.raises(AutomationAuthorityInvalid, match="first event"):
        canonical_automation_event(
            tenant_id="tenant-a",
            rule_id="rule-a",
            run_id="run-a",
            stream_key="run:run-a",
            sequence=1,
            event_type="action_requested",
            previous_event_digest=None,
            actor_id="system:automation",
            request_id="request-a",
            safe_snapshot={},
            occurred_at=TIME,
        )
    with pytest.raises(AutomationAuthorityInvalid, match="previous"):
        canonical_automation_event(
            tenant_id="tenant-a",
            rule_id="rule-a",
            run_id="run-a",
            stream_key="run:run-a",
            sequence=2,
            event_type="action_requested",
            previous_event_digest=None,
            actor_id="system:automation",
            request_id="request-a",
            safe_snapshot={},
            occurred_at=TIME,
        )


def test_safe_source_routes_are_allowlisted() -> None:
    expected = {
        "task_failed": ("enterprise_tasks", "/enterprise/tasks"),
        "task_source_stale": ("enterprise_tasks", "/enterprise/tasks"),
        "source_sync_failed": ("knowledge_sources", "/sources"),
        "release_quality_alert_opened": ("release_quality", "/enterprise/knowledge-base"),
        "release_recertification_blocked": ("release_quality", "/enterprise/knowledge-base"),
        "approval_request_terminal": ("enterprise_approvals", "/enterprise/approvals"),
    }
    for trigger_code, (route_code, path) in expected.items():
        route = project_automation_source_route(trigger_event(trigger_code=trigger_code))
        assert route["code"] == route_code
        assert route["path"] == path
    with pytest.raises(AutomationAuthorityInvalid, match="route|trigger"):
        project_automation_source_route(trigger_event(trigger_code="custom_webhook"))


def test_module_is_pure_and_has_no_orm_or_dynamic_dispatch_imports() -> None:
    tree = ast.parse(Path("core/enterprise_automation_workflows.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith(("sqlalchemy", "models", "server")) for name in imports)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "eval" not in calls
    assert "exec" not in calls
    assert "__import__" not in calls



def test_forbidden_generic_field_names_are_case_format_insensitive() -> None:
    with pytest.raises(AutomationAuthorityInvalid, match="forbidden|unsafe"):
        canonical_automation_trigger_event(
            trigger_event(safe_facts={"sqlQuery": "SELECT 1"})
        )


def test_source_route_href_url_encodes_fact_values() -> None:
    route = project_automation_source_route(
        trigger_event(safe_facts={"task_id": "task-a&request=other"})
    )
    assert route["href"] == "/enterprise/tasks?task=task-a%26request%3Dother"

@pytest.mark.parametrize(
    "unsafe_key",
    (
        "apiKey",
        "api_key",
        "apikey",
        "authorizationHeader",
        "authorization_header",
        "authorizationheader",
        "header",
        "answer",
        "accessKey",
        "access_key",
        "accesskey",
    ),
)
def test_safe_mappings_reject_sensitive_key_spellings(unsafe_key: str) -> None:
    with pytest.raises(AutomationAuthorityInvalid, match="forbidden|unsafe"):
        canonical_automation_trigger_event(trigger_event(safe_facts={unsafe_key: "redacted"}))


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "SELECT * FROM users",
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET role = 'admin'",
        "DELETE FROM users",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN x",
        "CREATE TABLE users (id INT)",
        "GRANT SELECT ON users TO app",
        "REVOKE SELECT ON users FROM app",
        "EXEC xp_cmdshell 'whoami'",
        "UNION SELECT password FROM users",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "GRANT",
        "REVOKE",
        "EXEC",
        "UNION",
        "Bearer abc123",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
    ),
)
def test_safe_mappings_reject_sql_bearer_and_jwt_text(unsafe_text: str) -> None:
    with pytest.raises(AutomationAuthorityInvalid, match="unsafe|invalid"):
        canonical_automation_trigger_event(
            trigger_event(safe_facts={"reason_code": unsafe_text})
        )


def test_safe_mappings_enforce_recursive_bounds_and_utf8_budget() -> None:
    too_deep: object = "leaf"
    for _ in range(13):
        too_deep = {"nested": too_deep}
    too_many = {"items": list(range(65))}
    too_many_nested = {"left": list(range(32)), "right": list(range(32))}
    too_large = {f"fact_{index}": "x" * 500 for index in range(32)}

    for safe_facts in (too_deep, too_many, too_many_nested, too_large):
        with pytest.raises(AutomationAuthorityInvalid, match="safe|deep|items|bytes|large"):
            canonical_automation_trigger_event(trigger_event(safe_facts=safe_facts))


def test_existing_safe_snapshot_fields_remain_valid() -> None:
    event = canonical_automation_event(
        tenant_id="tenant-a",
        rule_id="rule-a",
        run_id="run-a",
        stream_key="run:run-a",
        sequence=1,
        event_type="run_started",
        previous_event_digest=None,
        actor_id="system:automation",
        request_id="request-a",
        safe_snapshot={
            "status": "started",
            "revision": 1,
            "reason_code": "automation_started",
        },
        occurred_at=TIME,
    )
    assert event["safe_snapshot"]["status"] == "started"

