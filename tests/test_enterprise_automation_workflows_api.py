from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor


@dataclass
class Result:
    body: dict[str, Any]
    status: int = 200


READ_OPERATIONS = {
    "get_automation_summary",
    "list_automation_rules",
    "get_automation_rule",
    "list_automation_rule_revisions",
    "list_automation_runs",
    "get_automation_run",
    "list_automation_action_requests",
    "list_automation_events",
}


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, operation: str) -> Callable[..., Result]:
        def call(engine: Any, **kwargs: Any) -> Result:
            self.calls.append((operation, engine, kwargs))
            if operation == "get_automation_summary":
                return Result(
                    {
                        "tenant_id": kwargs["tenant_id"],
                        "state": "ready",
                        "active_rule_count": 1,
                        "paused_rule_count": 0,
                        "failed_run_count": 0,
                        "pending_request_count": 0,
                        "as_of": "2026-08-29T12:00:00.000000Z",
                        "reason_code": None,
                    }
                )
            if operation in {
                "list_automation_rules",
                "list_automation_rule_revisions",
                "list_automation_runs",
                "list_automation_action_requests",
                "list_automation_events",
            }:
                return Result({"items": [], "next_cursor": None, "invalid_item_count": 0})
            if operation == "get_automation_rule":
                return Result(
                    {
                        "rule": {
                            "id": kwargs["rule_id"],
                            "tenant_id": kwargs["tenant_id"],
                            "name": "Rule",
                            "status": "draft",
                            "revision": 1,
                            "current_revision_id": None,
                            "workspace_id": None,
                            "dataset_id": None,
                            "priority": 100,
                            "created_at": "2026-08-29T12:00:00.000000Z",
                            "created_by": "owner-a",
                            "updated_at": "2026-08-29T12:00:00.000000Z",
                            "updated_by": "owner-a",
                            "archived_at": None,
                        },
                        "current_revision": None,
                        "recent_runs": [],
                    }
                )
            if operation == "get_automation_run":
                return Result(
                    {
                        "run": {
                            "id": kwargs["run_id"],
                            "tenant_id": kwargs["tenant_id"],
                            "rule_id": "rule-a",
                            "rule_revision_id": "revision-a",
                            "trigger_event_id": "event-a",
                            "trigger_event_digest": "b" * 64,
                            "status": "completed",
                            "condition_matched": True,
                            "action_count": 0,
                            "requested_count": 0,
                            "rejected_count": 0,
                            "started_at": "2026-08-29T12:00:00.000000Z",
                            "completed_at": "2026-08-29T12:00:00.000000Z",
                            "safe_error_code": None,
                            "safe_error": None,
                        },
                        "action_requests": [],
                        "events": [],
                    }
                )
            return Result(
                {
                    "state": "applied",
                    "operation": operation,
                    "resource_id": "resource-a",
                    "revision": 1,
                    "message": None,
                    "retryable": False,
                }
            )

        return call


def actor() -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role="owner",
        request_id="request-stage25",
        request_ip="127.0.0.1",
    )


def client(service: Any | None = None) -> tuple[TestClient, Any]:
    from server.enterprise_automation_workflows_api import (
        build_enterprise_automation_workflows_router,
    )

    target = service or RecordingService()
    app = FastAPI()
    app.include_router(
        build_enterprise_automation_workflows_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=target,
            actor_dependency=lambda: actor(),
        )
    )
    return TestClient(app), target


def real_client(engine: Any) -> tuple[TestClient, Any]:
    from core import enterprise_automation_workflows_service
    from server.enterprise_automation_workflows_api import (
        build_enterprise_automation_workflows_router,
    )

    app = FastAPI()
    app.include_router(
        build_enterprise_automation_workflows_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            service=enterprise_automation_workflows_service,
            actor_dependency=lambda: actor(),
        )
    )
    return TestClient(app), enterprise_automation_workflows_service


def key() -> dict[str, str]:
    return {"Idempotency-Key": "stage25-idempotency-key"}


def rule_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Task failure notification",
        "workspace_id": "workspace-a",
        "dataset_id": "dataset-a",
        "priority": 10,
        "trigger_code": "task_failed",
        "condition_code": "always",
        "condition_params": {},
        "action_plan": [
            {
                "action_code": "notify_operator",
                "params": {
                    "category": "automation",
                    "severity": "error",
                    "title": "Automation matched",
                },
            }
        ],
        "reason": "create a bounded operator automation",
    }
    body.update(overrides)
    return body


def revision_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_revision": 2,
        "expected_definition_digest": "a" * 64,
        "trigger_code": "task_failed",
        "condition_code": "status_is",
        "condition_params": {"status": "failed"},
        "action_plan": [
            {
                "action_code": "notify_operator",
                "params": {
                    "category": "operations",
                    "severity": "error",
                    "title": "Task failure needs attention",
                },
            }
        ],
        "reason": "publish a bounded immutable revision",
    }
    body.update(overrides)
    return body


def fence_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_revision": 2,
        "expected_definition_digest": "a" * 64,
        "reason": "change the rule state with a current fence",
    }
    body.update(overrides)
    return body


def trigger_event(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "trigger_code": "task_failed",
        "source_stream_id": "task-stream-a",
        "source_event_id": "task-event-a",
        "source_event_digest": "b" * 64,
        "sequence": 3,
        "status": "failed",
        "severity": "error",
        "action_required": True,
        "attempt_number": 3,
        "max_attempts": 3,
        "source_current": False,
        "occurred_at": "2026-08-29T12:00:00Z",
        "safe_facts": {"task_id": "task-a"},
    }
    body.update(overrides)
    return body


def test_approved_routes_are_exposed_without_generic_ingest_or_execute() -> None:
    api, _ = client()
    paths = set(api.app.openapi()["paths"])
    expected = {
        "/api/enterprise/automations/summary",
        "/api/enterprise/automations/rules",
        "/api/enterprise/automations/rules/{rule_id}",
        "/api/enterprise/automations/rules/{rule_id}/revisions",
        "/api/enterprise/automations/rules/{rule_id}/preview",
        "/api/enterprise/automations/rules/{rule_id}/activate",
        "/api/enterprise/automations/rules/{rule_id}/pause",
        "/api/enterprise/automations/runs",
        "/api/enterprise/automations/runs/{run_id}",
        "/api/enterprise/automations/action-requests",
        "/api/enterprise/automations/events",
    }
    assert paths == expected
    assert not any("ingest" in path or "execute" in path for path in paths)


def test_routes_separate_read_and_mutation_engines_and_forward_actor_context() -> None:
    api, service = client()

    assert api.get("/api/enterprise/automations/summary").status_code == 200
    assert api.get("/api/enterprise/automations/rules/rule-a").status_code == 200
    assert (
        api.post("/api/enterprise/automations/rules", headers=key(), json=rule_body()).status_code
        == 200
    )
    assert (
        api.post(
            "/api/enterprise/automations/rules/rule-a/revisions",
            headers=key(),
            json=revision_body(),
        ).status_code
        == 200
    )

    read_calls = [call for call in service.calls if call[0] in READ_OPERATIONS]
    mutation_calls = [call for call in service.calls if call[0] not in READ_OPERATIONS]
    assert read_calls and all(engine == "read-engine" for _, engine, _ in read_calls)
    assert mutation_calls and all(engine == "mutation-engine" for _, engine, _ in mutation_calls)
    kwargs = mutation_calls[0][2]
    assert kwargs["tenant_id"] == "tenant-a"
    assert kwargs["actor_id"] == "owner-a"
    assert kwargs["account_id"] == "owner-a"
    assert kwargs["request_id"] == "request-stage25"
    assert kwargs["request_ip"] == "127.0.0.1"
    assert kwargs["idempotency_key"] == "stage25-idempotency-key"


def test_collection_cursor_limit_status_are_forwarded_without_reinterpretation() -> None:
    api, service = client()
    assert (
        api.get(
            "/api/enterprise/automations/rules?cursor=rule-cursor&limit=11&status=active"
        ).status_code
        == 200
    )
    assert (
        api.get(
            "/api/enterprise/automations/runs?cursor=run-cursor&limit=12&status=failed"
        ).status_code
        == 200
    )
    assert (
        api.get(
            "/api/enterprise/automations/action-requests?cursor=request-cursor&limit=13&status=requested"
        ).status_code
        == 200
    )
    assert (
        api.get(
            "/api/enterprise/automations/events?cursor=event-cursor&limit=14&event_type=run_started"
        ).status_code
        == 200
    )

    calls = {operation: kwargs for operation, _, kwargs in service.calls}
    assert calls["list_automation_rules"]["cursor"] == "rule-cursor"
    assert calls["list_automation_rules"]["limit"] == 11
    assert calls["list_automation_rules"]["status"] == "active"
    assert calls["list_automation_runs"]["cursor"] == "run-cursor"
    assert calls["list_automation_runs"]["limit"] == 12
    assert calls["list_automation_runs"]["status"] == "failed"
    assert calls["list_automation_action_requests"]["cursor"] == "request-cursor"
    assert calls["list_automation_action_requests"]["limit"] == 13
    assert calls["list_automation_action_requests"]["status"] == "requested"
    assert calls["list_automation_events"]["cursor"] == "event-cursor"
    assert calls["list_automation_events"]["limit"] == 14
    assert calls["list_automation_events"]["event_type"] == "run_started"


def test_rule_and_revision_payloads_are_strict_and_reject_unsafe_fields() -> None:
    api, _ = client()
    assert (
        api.post("/api/enterprise/automations/rules", headers=key(), json=rule_body()).status_code
        == 200
    )

    assert (
        api.post(
            "/api/enterprise/automations/rules",
            headers=key(),
            json=rule_body(query="status=failed"),
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/api/enterprise/automations/rules/rule-a/revisions",
            headers=key(),
            json=revision_body(condition_params={"query": "status=failed"}),
        ).status_code
        == 422
    )
    for unsafe_key in ("url", "sql", "webhook", "code"):
        params = {
            "category": "operations",
            "severity": "error",
            "title": "Task failure needs attention",
            unsafe_key: "https://example.test/execute" if unsafe_key == "url" else "unsafe",
        }
        response = api.post(
            "/api/enterprise/automations/rules/rule-a/revisions",
            headers=key(),
            json=revision_body(action_plan=[{"action_code": "notify_operator", "params": params}]),
        )
        assert response.status_code == 422, unsafe_key


def test_preview_accepts_only_a_safe_trigger_snapshot_and_never_executes_an_action() -> None:
    api, service = client()
    response = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event()},
    )
    assert response.status_code == 200
    assert [operation for operation, _, _ in service.calls] == ["preview_automation_rule"]
    assert service.calls[0][2]["trigger_event"]["source_event_id"] == "task-event-a"

    unsafe = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={
            **fence_body(),
            "trigger_event": trigger_event(safe_facts={"webhook": "https://example.test"}),
        },
    )
    assert unsafe.status_code == 422


def test_preview_binds_nested_trigger_tenant_and_rejects_cross_tenant_input() -> None:
    api, service = client()
    response = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event()},
    )
    assert response.status_code == 200
    assert service.calls[0][2]["trigger_event"]["tenant_id"] == "tenant-a"

    same_tenant = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event(tenant_id="tenant-a")},
    )
    assert same_tenant.status_code == 200
    assert service.calls[1][2]["trigger_event"]["tenant_id"] == "tenant-a"

    crossed = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event(tenant_id="tenant-b")},
    )
    assert crossed.status_code == 422


def test_activate_requires_revision_id_and_forwards_the_selected_revision() -> None:
    api, service = client()
    missing = api.post(
        "/api/enterprise/automations/rules/rule-a/activate",
        headers=key(),
        json=fence_body(),
    )
    assert missing.status_code == 422

    selected = api.post(
        "/api/enterprise/automations/rules/rule-a/activate",
        headers=key(),
        json={**fence_body(), "revision_id": "revision-a"},
    )
    assert selected.status_code == 200
    assert service.calls[-1][2]["revision_id"] == "revision-a"


def test_api_rejects_camel_case_unsafe_trigger_fact_keys() -> None:
    api, _ = client()
    for unsafe_key in ("sqlQuery", "webhookUrl", "accessToken"):
        response = api.post(
            "/api/enterprise/automations/rules/rule-a/preview",
            headers=key(),
            json={
                **fence_body(),
                "trigger_event": trigger_event(safe_facts={unsafe_key: "redacted"}),
            },
        )
        assert response.status_code == 422, unsafe_key


def test_real_service_http_contract_is_canonical_and_has_no_aliases_or_extras(
    tmp_path: Any,
) -> None:
    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, _ = _release_engine(tmp_path)
    try:
        api, _ = real_client(engine)
        outcome_keys = {
            "state",
            "operation",
            "resource_id",
            "revision",
            "message",
            "retryable",
        }
        rule_keys = {
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
        revision_keys = {
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
        page_keys = {"items", "next_cursor", "invalid_item_count"}

        created = api.post(
            "/api/enterprise/automations/rules",
            headers=key(),
            json=rule_body(),
        )
        assert created.status_code == 200
        assert set(created.json()) == outcome_keys
        rule_id = created.json()["resource_id"]

        detail = api.get(f"/api/enterprise/automations/rules/{rule_id}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert set(detail_body) == {"rule", "current_revision", "recent_runs"}
        assert set(detail_body["rule"]) == rule_keys
        assert set(detail_body["current_revision"]) == revision_keys
        assert detail_body["recent_runs"] == []

        rules = api.get("/api/enterprise/automations/rules?status=all")
        assert rules.status_code == 200
        assert set(rules.json()) == page_keys
        assert set(rules.json()["items"][0]) == rule_keys

        revisions = api.get(f"/api/enterprise/automations/rules/{rule_id}/revisions")
        assert revisions.status_code == 200
        assert set(revisions.json()) == page_keys
        assert set(revisions.json()["items"][0]) == revision_keys

        current_revision = detail_body["current_revision"]
        patch = api.patch(
            f"/api/enterprise/automations/rules/{rule_id}",
            headers={"Idempotency-Key": "stage25-update-key"},
            json={
                "expected_revision": detail_body["rule"]["revision"],
                "expected_definition_digest": current_revision["definition_digest"],
                "name": "Updated task failure notification",
                "priority": 20,
                "reason": "update bounded rule metadata",
            },
        )
        assert patch.status_code == 200
        assert set(patch.json()) == outcome_keys

        preview = api.post(
            f"/api/enterprise/automations/rules/{rule_id}/preview",
            headers={"Idempotency-Key": "stage25-preview-key"},
            json={
                "expected_revision": detail_body["rule"]["revision"],
                "expected_definition_digest": current_revision["definition_digest"],
                "trigger_event": trigger_event(),
                "reason": "preview bounded rule",
            },
        )
        assert preview.status_code == 200
        assert set(preview.json()) == outcome_keys

        activated = api.post(
            f"/api/enterprise/automations/rules/{rule_id}/activate",
            headers={"Idempotency-Key": "stage25-activate-key"},
            json={
                "revision_id": current_revision["id"],
                "expected_revision": detail_body["rule"]["revision"],
                "expected_definition_digest": current_revision["definition_digest"],
                "reason": "activate the selected immutable revision",
            },
        )
        assert activated.status_code == 200
        assert set(activated.json()) == outcome_keys

        summary = api.get("/api/enterprise/automations/summary")
        assert summary.status_code == 200
        assert set(summary.json()) == {
            "tenant_id",
            "state",
            "active_rule_count",
            "paused_rule_count",
            "failed_run_count",
            "pending_request_count",
            "as_of",
            "reason_code",
        }
    finally:
        engine.dispose()


def test_mutations_require_idempotency_key_and_complete_revision_digest_fence() -> None:
    api, _ = client()
    missing_key = api.post("/api/enterprise/automations/rules", json=rule_body())
    assert missing_key.status_code == 422

    missing_digest = api.post(
        "/api/enterprise/automations/rules/rule-a/activate",
        headers=key(),
        json=fence_body(expected_definition_digest=None),
    )
    assert missing_digest.status_code == 422

    missing_revision = api.post(
        "/api/enterprise/automations/rules/rule-a/pause",
        headers=key(),
        json=fence_body(expected_revision=None),
    )
    assert missing_revision.status_code == 422


def test_safe_error_projection_hides_internal_exception_details() -> None:
    class ExplodingService:
        def get_automation_summary(self, engine: Any, **kwargs: Any) -> Result:
            raise RuntimeError("sql=select secret_token from credentials at https://db.example")

    api, _ = client(ExplodingService())
    response = api.get("/api/enterprise/automations/summary")
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "enterprise_automation_workflows_unavailable",
            "message": "Enterprise Automation Workflows 服务暂不可用",
        }
    }
    assert "secret_token" not in response.text
    assert "https://" not in response.text


def test_safe_service_http_errors_are_projected_without_raw_payloads() -> None:
    class ConflictService:
        def activate_automation_rule(self, engine: Any, **kwargs: Any) -> Result:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "automation_revision_conflict",
                    "message": "revision fence mismatch",
                },
            )

    api, _ = client(ConflictService())
    response = api.post(
        "/api/enterprise/automations/rules/rule-a/activate",
        headers=key(),
        json={**fence_body(), "revision_id": "revision-a"},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "automation_revision_conflict", "message": "revision fence mismatch"}
    }


def test_main_app_mounts_stage25_automation_routes() -> None:
    from server.app import app

    paths = set(app.openapi()["paths"])
    assert {
        "/api/enterprise/automations/summary",
        "/api/enterprise/automations/rules",
        "/api/enterprise/automations/rules/{rule_id}",
        "/api/enterprise/automations/rules/{rule_id}/revisions",
        "/api/enterprise/automations/rules/{rule_id}/preview",
        "/api/enterprise/automations/rules/{rule_id}/activate",
        "/api/enterprise/automations/rules/{rule_id}/pause",
        "/api/enterprise/automations/runs",
        "/api/enterprise/automations/runs/{run_id}",
        "/api/enterprise/automations/action-requests",
        "/api/enterprise/automations/events",
    } <= paths


def test_api_rejects_sensitive_key_spellings_sql_bearer_jwt_and_oversized_maps() -> None:
    api, _ = client()
    for unsafe_key in (
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
    ):
        response = api.post(
            "/api/enterprise/automations/rules/rule-a/preview",
            headers=key(),
            json={**fence_body(), "trigger_event": trigger_event(safe_facts={unsafe_key: "redacted"})},
        )
        assert response.status_code == 422, unsafe_key

    for unsafe_text in (
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
    ):
        response = api.post(
            "/api/enterprise/automations/rules/rule-a/preview",
            headers=key(),
            json={
                **fence_body(),
                "trigger_event": trigger_event(safe_facts={"reason_code": unsafe_text}),
            },
        )
        assert response.status_code == 422, unsafe_text

    nested_item_count = {"left": list(range(32)), "right": list(range(32))}
    response = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event(safe_facts=nested_item_count)},
    )
    assert response.status_code == 422

    oversized = {f"fact_{index}": "x" * 500 for index in range(32)}
    response = api.post(
        "/api/enterprise/automations/rules/rule-a/preview",
        headers=key(),
        json={**fence_body(), "trigger_event": trigger_event(safe_facts=oversized)},
    )
    assert response.status_code == 422

