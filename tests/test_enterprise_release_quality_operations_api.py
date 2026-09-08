from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.enterprise_knowledge_base_releases import ServiceResult
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor


class ServiceFailure(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class RecordingService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.failure: Exception | None = None
        self.next_result: Any = None

    def __getattr__(self, operation: str) -> Callable[..., Any]:
        def call(engine: Any, **kwargs: Any) -> Any:
            self.calls.append((operation, engine, kwargs))
            if self.failure is not None:
                raise self.failure
            if self.next_result is not None:
                result = self.next_result
                self.next_result = None
                return result
            if operation.startswith("list_") or operation == "get_quality_operations_summary":
                return {"items": [], "count": 0, "next_cursor": None}
            if operation == "enqueue_due_quality_scans":
                return []
            return {
                "state": "applied",
                "operation": operation,
                "resource_id": kwargs.get("policy_id"),
            }

        return call


def _actor(role: str = "owner") -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role=role,
        request_id="request-quality-operations-api",
        request_ip="127.0.0.1",
    )


def _client(
    *,
    actor: KnowledgeActor | None = None,
    actor_dependency: Callable[..., Any] | None = None,
    slo: RecordingService | None = None,
    scheduler: RecordingService | None = None,
    alerts: RecordingService | None = None,
    recertification: RecordingService | None = None,
    summary: RecordingService | None = None,
) -> tuple[TestClient, dict[str, RecordingService]]:
    from server.enterprise_release_quality_operations_api import (
        build_enterprise_release_quality_operations_router,
    )

    services = {
        "slo": slo or RecordingService("slo"),
        "scheduler": scheduler or RecordingService("scheduler"),
        "alerts": alerts or RecordingService("alerts"),
        "recertification": recertification or RecordingService("recertification"),
        "summary": summary or RecordingService("summary"),
    }
    app = FastAPI()
    dependency = actor_dependency or (lambda: actor or _actor())
    app.include_router(
        build_enterprise_release_quality_operations_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            slo_service=services["slo"],
            scheduler_service=services["scheduler"],
            alert_service=services["alerts"],
            recertification_service=services["recertification"],
            summary_service=services["summary"],
            actor_dependency=dependency,
        )
    )
    return TestClient(app), services


def _slo_create_body() -> dict[str, Any]:
    return {
        "name": "Production release quality SLO",
        "scope_type": "global",
        "scope_value": "*",
        "channel_id": None,
        "certification_warning_minutes": 10_080,
        "certification_critical_minutes": 1_440,
        "waiver_warning_minutes": 720,
        "max_open_alerts": 10,
        "auto_queue_recertification": True,
        "require_passing_certification": True,
        "allow_active_waiver": True,
        "reason": "protect the serving release",
    }


def _schedule_create_body() -> dict[str, Any]:
    return {
        "dataset_id": "dataset-a",
        "slo_policy_id": "slo-policy-a",
        "interval_seconds": 3_600,
        "reason": "run the serving quality scan hourly",
    }


def _recertification_create_body() -> dict[str, Any]:
    return {
        "release_id": "release-a",
        "channel_id": "channel-production",
        "release_role": "active",
        "baseline_id": "baseline-a",
        "policy_id": "quality-policy-a",
        "expected_policy_revision": 1,
        "slo_policy_id": "slo-policy-a",
        "expected_slo_policy_revision": 1,
        "trigger": "manual",
        "expected_manifest_digest": "a" * 64,
        "expected_evidence_digest": "b" * 64,
        "expected_channel_revision": 1,
        "reason": "operator requested a governed recertification",
    }


def test_request_models_are_strict_and_timestamp_is_timezone_aware() -> None:
    from server.enterprise_release_quality_operations_api import (
        AlertSuppressRequest,
        SloPolicyCreateRequest,
    )

    with pytest.raises(ValidationError):
        SloPolicyCreateRequest(**{**_slo_create_body(), "unexpected": True})
    with pytest.raises(ValidationError):
        SloPolicyCreateRequest(**{**_slo_create_body(), "max_open_alerts": "10"})
    with pytest.raises(ValidationError):
        SloPolicyCreateRequest(**{**_slo_create_body(), "allow_active_waiver": 1})

    with pytest.raises(ValidationError):
        AlertSuppressRequest(
            expected_revision=1,
            suppressed_until="2026-08-29T12:00:00",
        )
    with pytest.raises(ValidationError):
        AlertSuppressRequest(
            expected_revision=1,
            suppressed_until=1_756_464_000,
        )
    valid = AlertSuppressRequest(
        expected_revision=1,
        suppressed_until="2026-08-29T12:00:00Z",
    )
    assert isinstance(valid.suppressed_until, datetime)
    assert valid.suppressed_until.tzinfo is not None


def test_all_approved_routes_forward_to_the_right_service_and_engine() -> None:
    client, services = _client()
    mutation_headers = {"Idempotency-Key": "operations-key-1"}

    assert (
        client.get(
            "/api/enterprise/release-quality/slo-policies",
            params={"scope_type": "global", "scope_value": "*", "cursor": "opaque-v1", "limit": 7},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/release-quality/slo-policies",
            headers=mutation_headers,
            json=_slo_create_body(),
        ).status_code
        == 201
    )
    assert (
        client.patch(
            "/api/enterprise/release-quality/slo-policies",
            headers=mutation_headers,
            json={
                "policy_id": "slo-policy-a",
                "expected_revision": 1,
                "certification_warning_minutes": 8_640,
                "reason": "tighten the serving horizon",
            },
        ).status_code
        == 200
    )

    assert (
        client.get(
            "/api/enterprise/release-quality/scan-schedules",
            params={"dataset_id": "dataset-a", "cursor": "opaque-schedule", "limit": 9},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/release-quality/scan-schedules",
            headers=mutation_headers,
            json=_schedule_create_body(),
        ).status_code
        == 201
    )
    assert (
        client.patch(
            "/api/enterprise/release-quality/scan-schedules",
            headers=mutation_headers,
            json={
                "dataset_id": "dataset-a",
                "schedule_id": "schedule-a",
                "expected_revision": 1,
                "interval_seconds": 7_200,
                "reason": "reduce scan pressure",
            },
        ).status_code
        == 200
    )

    assert (
        client.get(
            "/api/enterprise/release-quality/scan-runs",
            params={"dataset_id": "dataset-a", "status": "pending", "limit": 5},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/release-quality/scan-runs/run-a/cancel",
            headers=mutation_headers,
            json={"dataset_id": "dataset-a", "reason": "obsolete run"},
        ).status_code
        == 200
    )

    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-operations/summary"
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-observations",
            params={"severity": "critical", "limit": 3},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts",
            params={
                "status": "open",
                "severity": "critical",
                "alert_type": "certification_expired",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/acknowledge",
            headers=mutation_headers,
            json={"expected_revision": 1, "comment": "triaged by release operations"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/resolve",
            headers=mutation_headers,
            json={"expected_revision": 2},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/suppress",
            headers=mutation_headers,
            json={
                "expected_revision": 3,
                "suppressed_until": "2026-08-30T12:00:00Z",
                "comment": "maintenance window",
            },
        ).status_code
        == 200
    )

    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/recertification-jobs",
            params={"status": "pending", "limit": 4},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/recertification-jobs",
            headers=mutation_headers,
            json=_recertification_create_body(),
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/recertification-jobs/job-a/cancel",
            headers=mutation_headers,
            json={"expected_status": "pending", "reason": "duplicate operator request"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-operations/scan",
            headers=mutation_headers,
            json={"limit": 12},
        ).status_code
        == 202
    )

    read_calls = [
        *services["slo"].calls,
        *services["scheduler"].calls,
        *services["alerts"].calls,
        *services["recertification"].calls,
        *services["summary"].calls,
    ]
    assert any(
        name == "list_quality_slo_policies" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "create_quality_slo_policy" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "update_quality_slo_policy" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "list_quality_scan_schedules" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "create_quality_scan_schedule" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "update_quality_scan_schedule" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "list_quality_scan_runs" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "cancel_quality_scan_run" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    cancel_call = next(
        kwargs
        for name, engine, kwargs in read_calls
        if name == "cancel_quality_scan_run" and engine == "mutation-engine"
    )
    assert cancel_call["idempotency_key"] == "operations-key-1"
    assert cancel_call["request_id"] == "request-quality-operations-api"
    assert any(
        name == "get_quality_operations_summary" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "list_quality_observations" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "list_quality_alerts" and engine == "read-engine" for name, engine, _ in read_calls
    )
    assert any(
        name == "acknowledge_quality_alert" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "resolve_quality_alert" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "suppress_quality_alert" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "list_recertification_jobs" and engine == "read-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "queue_recertification_job" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "cancel_recertification_job" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )
    assert any(
        name == "enqueue_due_quality_scans" and engine == "mutation-engine"
        for name, engine, _ in read_calls
    )

    slo_list_kwargs = next(
        kwargs for name, _, kwargs in services["slo"].calls if name == "list_quality_slo_policies"
    )
    assert slo_list_kwargs["cursor"] == "opaque-v1"
    assert slo_list_kwargs["limit"] == 7
    assert slo_list_kwargs["tenant_id"] == "tenant-a"
    schedule_create_kwargs = next(
        kwargs
        for name, _, kwargs in services["scheduler"].calls
        if name == "create_quality_scan_schedule"
    )
    assert schedule_create_kwargs["dataset_id"] == "dataset-a"
    assert schedule_create_kwargs["actor_id"] == "owner-a"
    assert schedule_create_kwargs["idempotency_key"] == "operations-key-1"


def test_every_mutation_requires_idempotency_key() -> None:
    client, _ = _client()
    assert (
        client.post(
            "/api/enterprise/release-quality/slo-policies",
            json=_slo_create_body(),
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/enterprise/release-quality/scan-schedules",
            json={
                "dataset_id": "dataset-a",
                "schedule_id": "schedule-a",
                "expected_revision": 1,
                "interval_seconds": 3_600,
                "reason": "change schedule",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/resolve",
            json={"expected_revision": 1},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-operations/scan",
            json={"limit": 1},
        ).status_code
        == 422
    )


def test_owner_admin_and_dataset_manage_read_permissions_are_separate() -> None:
    editor_client, services = _client(actor=_actor("editor"))
    assert (
        editor_client.get("/api/enterprise/knowledge-bases/dataset-a/quality-alerts").status_code
        == 200
    )
    assert (
        editor_client.post(
            "/api/enterprise/release-quality/slo-policies",
            headers={"Idempotency-Key": "editor-slo"},
            json=_slo_create_body(),
        ).status_code
        == 403
    )
    assert (
        editor_client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/resolve",
            headers={"Idempotency-Key": "editor-alert"},
            json={"expected_revision": 1},
        ).status_code
        == 403
    )
    assert all(name == "list_quality_alerts" for name, _, _ in services["alerts"].calls)


def test_invalid_cursor_and_limit_fail_at_the_http_boundary() -> None:
    client, _ = _client()
    assert (
        client.get(
            "/api/enterprise/release-quality/slo-policies",
            params={"limit": 0},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-alerts",
            params={"limit": 201},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/release-quality/scan-runs",
            params={"dataset_id": "dataset-a", "cursor": ""},
        ).status_code
        == 422
    )


def test_safe_errors_and_explicit_service_status_are_preserved() -> None:
    service = RecordingService("slo")
    client, _ = _client(slo=service)
    service.failure = ServiceFailure(409, "quality_slo_conflict", "SLO revision conflict")
    conflict = client.get("/api/enterprise/release-quality/slo-policies")
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {"code": "quality_slo_conflict", "message": "SLO revision conflict"}
    }

    service.failure = RuntimeError("credential=raw-secret should never reach the client")
    unavailable = client.get("/api/enterprise/release-quality/slo-policies")
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": {
            "code": "enterprise_release_quality_operations_unavailable",
            "message": "Enterprise Release Quality Operations 服务暂不可用",
        }
    }
    assert "raw-secret" not in unavailable.text

    service.failure = None
    service.next_result = ServiceResult({"state": "accepted"}, 207)
    explicit = client.post(
        "/api/enterprise/release-quality/slo-policies",
        headers={"Idempotency-Key": "explicit-status"},
        json=_slo_create_body(),
    )
    assert explicit.status_code == 207
    assert explicit.json() == {"state": "accepted"}


def test_router_constructs_distinct_permission_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import enterprise_release_quality_operations_api as api

    seen: list[tuple[str, bool]] = []

    def fake_permission(permission: str, resolver: Any = None) -> Callable[..., Any]:
        seen.append((permission, resolver is not None))
        return lambda: _actor()

    monkeypatch.setattr(api, "require_knowledge_permission", fake_permission)
    api.build_enterprise_release_quality_operations_router(
        read_engine_provider=lambda: "read-engine",
        mutation_engine_provider=lambda: "mutation-engine",
        slo_service=RecordingService("slo"),
        scheduler_service=RecordingService("scheduler"),
        alert_service=RecordingService("alerts"),
        recertification_service=RecordingService("recertification"),
        summary_service=RecordingService("summary"),
    )
    assert (KNOWLEDGE_READ, False) in seen
    assert (KNOWLEDGE_MANAGE, False) in seen
    assert (KNOWLEDGE_READ, True) in seen
    assert (KNOWLEDGE_MANAGE, True) in seen


def test_main_app_mounts_stage21_operations_routes() -> None:
    from server.app import app

    paths: set[str] = set()

    def collect(routes: Any) -> None:
        for route in routes:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                paths.add(path)
            nested = getattr(route, "routes", None)
            if nested is None:
                nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested is not None:
                collect(nested)

    collect(app.routes)
    expected = {
        "/api/enterprise/release-quality/slo-policies",
        "/api/enterprise/release-quality/scan-schedules",
        "/api/enterprise/release-quality/scan-runs",
        "/api/enterprise/release-quality/scan-runs/{run_id}/cancel",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-operations/summary",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-observations",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/acknowledge",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/resolve",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/suppress",
        "/api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs",
        "/api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs/{job_id}/cancel",
        "/api/enterprise/knowledge-bases/{dataset_id}/quality-operations/scan",
    }
    assert expected <= paths


def test_default_service_modules_expose_every_mounted_route_operation() -> None:
    import core.enterprise_release_quality_operation_mutations as slo
    import core.enterprise_release_quality_scheduler as scheduler
    import core.enterprise_release_quality_alerts as alerts
    import core.enterprise_release_quality_recertification as recertification

    for module, names in (
        (
            slo,
            ("list_quality_slo_policies", "create_quality_slo_policy", "update_quality_slo_policy"),
        ),
        (
            scheduler,
            (
                "list_quality_scan_schedules",
                "create_quality_scan_schedule",
                "update_quality_scan_schedule",
                "list_quality_scan_runs",
                "cancel_quality_scan_run",
                "get_quality_operations_summary",
                "list_quality_observations",
                "enqueue_due_quality_scans",
            ),
        ),
        (
            alerts,
            (
                "list_quality_alerts",
                "acknowledge_quality_alert",
                "resolve_quality_alert",
                "suppress_quality_alert",
            ),
        ),
        (
            recertification,
            (
                "list_recertification_jobs",
                "queue_recertification_job",
                "cancel_recertification_job",
            ),
        ),
    ):
        for name in names:
            assert callable(getattr(module, name, None)), f"{module.__name__}.{name}"
