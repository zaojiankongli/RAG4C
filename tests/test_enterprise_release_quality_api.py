from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor


class RecordingQualityService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.failure: Exception | None = None

    def _record(self, name: str, engine: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, engine, kwargs))
        if self.failure is not None:
            raise self.failure
        if name.startswith("list_"):
            return {"items": [], "count": 0, "next_cursor": None}
        if name == "get_release_quality_gate":
            return {
                "state": "blocked",
                "reason": "certification_required",
                "policy": None,
                "certification": None,
                "waiver": None,
            }
        if name == "create_quality_gate_policy":
            return {"policy": {"id": "policy-1", "revision": 1}}
        if name == "update_quality_gate_policy":
            return {"policy": {"id": kwargs["policy_id"], "revision": 2}}
        if name == "create_quality_baseline":
            return {"baseline": {"id": "baseline-1", "baseline_revision": 1}}
        if name == "get_quality_baseline":
            return {"baseline": {"id": kwargs["baseline_id"]}, "items": []}
        if name == "certify_release":
            return {"certification": {"id": "certification-1", "status": "passed"}}
        if name == "request_quality_waiver":
            return {
                "state": "approval_required",
                "operation": "release_quality_waiver",
                "resource_id": kwargs["release_id"],
                "approval_request_id": "approval-waiver-1",
            }
        return {"state": "applied", "resource_id": kwargs.get("policy_id")}

    def __getattr__(self, name: str):
        allowed = {
            "list_quality_gate_policies",
            "create_quality_gate_policy",
            "update_quality_gate_policy",
            "list_quality_baselines",
            "create_quality_baseline",
            "get_quality_baseline",
            "certify_release",
            "list_release_certifications",
            "get_release_quality_gate",
            "request_quality_waiver",
        }
        if name not in allowed:
            raise AttributeError(name)

        def operation(engine: Any, **kwargs: Any) -> dict[str, Any]:
            return self._record(name, engine, kwargs)

        return operation


def _actor(role: str = "owner") -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role=role,
        request_id="request-quality-api",
        request_ip="127.0.0.1",
    )


def _policy_body() -> dict[str, Any]:
    return {
        "name": "Production quality",
        "scope_type": "channel",
        "scope_value": "channel-production",
        "channel_id": "channel-production",
        "min_experiment_count": 1,
        "min_judged_result_count": 2,
        "min_judgment_coverage_bps": 10_000,
        "min_exact_agreement_bps": 10_000,
        "min_mean_score_milli": 2_000,
        "max_conflicting_results": 0,
        "require_all_experiments_completed": True,
        "require_no_degraded_results": True,
        "max_certification_age_minutes": 1_440,
        "reason": "protect production",
    }


def _client(service: RecordingQualityService) -> TestClient:
    from server.enterprise_release_quality_api import build_enterprise_release_quality_router

    app = FastAPI()
    app.include_router(
        build_enterprise_release_quality_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: _actor(),
        )
    )
    return TestClient(app)


def test_quality_router_separates_read_and_mutation_engines_and_forwards_scope() -> None:
    service = RecordingQualityService()
    client = _client(service)
    headers = {"Idempotency-Key": "quality-mutation-key"}

    responses = [
        client.get(
            "/api/enterprise/release-quality/policies",
            params={"scope_type": "channel", "status": "active", "cursor": "policy-c", "limit": 25},
        ),
        client.post(
            "/api/enterprise/release-quality/policies",
            headers=headers,
            json=_policy_body(),
        ),
        client.patch(
            "/api/enterprise/release-quality/policies/policy-1",
            headers=headers,
            json={
                "expected_revision": 1,
                "name": "Production quality v2",
                "reason": "tighten policy",
            },
        ),
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-baselines",
            params={"cursor": "baseline-c", "limit": 30},
        ),
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-baselines",
            headers=headers,
            json={
                "name": "客服核心问题",
                "experiment_ids": ["experiment-quality-a"],
                "reason": "freeze reviewer evidence",
            },
        ),
        client.get("/api/enterprise/knowledge-bases/dataset-a/quality-baselines/baseline-1"),
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/certifications",
            headers=headers,
            json={
                "channel_id": "channel-production",
                "baseline_id": "baseline-1",
                "policy_id": "policy-1",
                "expected_policy_revision": 1,
                "expected_channel_revision": 1,
                "reason": "certify production candidate",
            },
        ),
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/certifications",
            params={"cursor": "cert-c", "limit": 40},
        ),
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/quality-gate",
            params={"channel_id": "channel-production"},
        ),
    ]

    assert [response.status_code for response in responses] == [
        200,
        201,
        200,
        200,
        201,
        200,
        201,
        200,
        200,
    ]
    assert service.calls == [
        (
            "list_quality_gate_policies",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "scope_type": "channel",
                "status": "active",
                "cursor": "policy-c",
                "limit": 25,
            },
        ),
        (
            "create_quality_gate_policy",
            "mutation-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                **_policy_body_without_reason(),
                "reason": "protect production",
                "request_id": "request-quality-api",
                "request_ip": "127.0.0.1",
                "idempotency_key": "quality-mutation-key",
            },
        ),
        (
            "update_quality_gate_policy",
            "mutation-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "policy_id": "policy-1",
                "expected_revision": 1,
                "name": "Production quality v2",
                "reason": "tighten policy",
                "request_id": "request-quality-api",
                "request_ip": "127.0.0.1",
                "idempotency_key": "quality-mutation-key",
            },
        ),
        (
            "list_quality_baselines",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "cursor": "baseline-c",
                "limit": 30,
            },
        ),
        (
            "create_quality_baseline",
            "mutation-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "name": "客服核心问题",
                "experiment_ids": ["experiment-quality-a"],
                "parent_baseline_id": None,
                "reason": "freeze reviewer evidence",
                "request_id": "request-quality-api",
                "request_ip": "127.0.0.1",
                "idempotency_key": "quality-mutation-key",
            },
        ),
        (
            "get_quality_baseline",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "baseline_id": "baseline-1",
            },
        ),
        (
            "certify_release",
            "mutation-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
                "channel_id": "channel-production",
                "baseline_id": "baseline-1",
                "policy_id": "policy-1",
                "expected_policy_revision": 1,
                "expected_channel_revision": 1,
                "reason": "certify production candidate",
                "request_id": "request-quality-api",
                "request_ip": "127.0.0.1",
                "idempotency_key": "quality-mutation-key",
            },
        ),
        (
            "list_release_certifications",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
                "cursor": "cert-c",
                "limit": 40,
            },
        ),
        (
            "get_release_quality_gate",
            "read-engine",
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "dataset_id": "dataset-a",
                "release_id": "release-1",
                "channel_id": "channel-production",
            },
        ),
    ]


def _policy_body_without_reason() -> dict[str, Any]:
    body = _policy_body()
    body.pop("reason")
    return body


def test_quality_router_enforces_strict_bodies_headers_and_keyset_queries() -> None:
    client = _client(RecordingQualityService())

    assert (
        client.post(
            "/api/enterprise/release-quality/policies",
            headers={"Idempotency-Key": "strict-policy"},
            json={**_policy_body(), "unexpected": True},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/release-quality/policies",
            headers={"Idempotency-Key": "strict-policy-bool"},
            json={**_policy_body(), "min_experiment_count": True},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/release-quality/policies",
            json=_policy_body(),
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/release-quality/policies",
            params={"cursor": "", "limit": 25},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/quality-baselines",
            params={"limit": 0},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/certifications",
            params={"limit": 201},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/quality-gate",
        ).status_code
        == 422
    )


def test_quality_router_validates_exact_policy_scope_and_baseline_selection() -> None:
    client = _client(RecordingQualityService())
    headers = {"Idempotency-Key": "scope-invalid"}

    invalid_scopes = [
        {"scope_type": "global", "scope_value": "global", "channel_id": None},
        {"scope_type": "risk_tier", "scope_value": "high", "channel_id": "channel-high"},
        {"scope_type": "channel", "scope_value": "channel-other", "channel_id": "channel-prod"},
    ]
    for scope in invalid_scopes:
        response = client.post(
            "/api/enterprise/release-quality/policies",
            headers=headers,
            json={**_policy_body(), **scope},
        )
        assert response.status_code == 422

    assert (
        client.post(
            "/api/enterprise/knowledge-bases/dataset-a/quality-baselines",
            headers=headers,
            json={
                "name": "empty baseline",
                "experiment_ids": [],
                "reason": "invalid",
            },
        ).status_code
        == 422
    )


def test_quality_router_sanitizes_unexpected_service_errors() -> None:
    service = RecordingQualityService()
    service.failure = RuntimeError("database password=super-secret")
    response = _client(service).get("/api/enterprise/release-quality/policies")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "enterprise_release_quality_unavailable",
            "message": "Enterprise Release Quality 服务暂不可用",
        }
    }
    assert "super-secret" not in response.text


def test_quality_router_preserves_only_safe_domain_error_fields() -> None:
    class SafeDomainError(RuntimeError):
        status = 409
        code = "quality_policy_conflict"
        message = "quality policy revision conflict"

    service = RecordingQualityService()
    service.failure = SafeDomainError("raw secret must not be exposed")
    response = _client(service).post(
        "/api/enterprise/release-quality/policies",
        headers={"Idempotency-Key": "domain-error"},
        json=_policy_body(),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "quality_policy_conflict",
            "message": "quality policy revision conflict",
        }
    }
    assert "raw secret" not in response.text


def test_main_app_mounts_quality_routes() -> None:
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
    assert "/api/enterprise/release-quality/policies" in paths
    assert "/api/enterprise/release-quality/policies/{policy_id}" in paths
    assert "/api/enterprise/knowledge-bases/{dataset_id}/quality-baselines" in paths
    assert (
        "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certifications" in paths
    )
    assert (
        "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-gate" in paths
    )
    assert (
        "/api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-waivers"
        in paths
    )


def test_quality_router_builds_distinct_read_and_manage_permission_dependencies(
    monkeypatch,
) -> None:
    from server import enterprise_release_quality_api as api

    seen: list[tuple[str, bool]] = []

    def fake_permission(permission: str, resolver=None):
        seen.append((permission, resolver is not None))
        return lambda: _actor()

    monkeypatch.setattr(api, "require_knowledge_permission", fake_permission)
    api.build_enterprise_release_quality_router(
        read_engine_provider=lambda: "read-engine",
        mutation_engine_provider=lambda: "mutation-engine",
        service=RecordingQualityService(),
    )

    assert (KNOWLEDGE_READ, False) in seen
    assert (KNOWLEDGE_MANAGE, False) in seen
    assert (KNOWLEDGE_READ, True) in seen
    assert (KNOWLEDGE_MANAGE, True) in seen


def test_certification_route_preserves_explicit_existing_resource_status() -> None:
    from core.enterprise_knowledge_base_releases import ServiceResult

    class ExistingCertificationService(RecordingQualityService):
        def certify_release(self, engine: Any, **kwargs: Any) -> ServiceResult:
            self.calls.append(("certify_release", engine, kwargs))
            return ServiceResult(
                {
                    "state": "unchanged",
                    "operation": "release_quality_certify",
                    "resource_id": "certification-existing",
                },
                200,
            )

    response = _client(ExistingCertificationService()).post(
        "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/certifications",
        headers={"Idempotency-Key": "existing-certification"},
        json={
            "channel_id": "channel-production",
            "baseline_id": "baseline-1",
            "policy_id": "policy-1",
            "expected_policy_revision": 1,
            "expected_channel_revision": 1,
            "reason": "reuse evidence",
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == "unchanged"


def test_quality_waiver_route_requires_idempotency_and_forwards_revision_bound_expiry() -> None:
    service = RecordingQualityService()
    client = _client(service)
    path = "/api/enterprise/knowledge-bases/dataset-a/releases/release-1/quality-waivers"
    body = {
        "channel_id": "channel-production",
        "policy_id": "policy-1",
        "expected_policy_revision": 2,
        "expected_channel_revision": 4,
        "approval_policy_id": "approval-policy-1",
        "requested_expires_at": "2026-08-29T08:00:00Z",
        "reason": "temporary exception",
    }
    assert client.post(path, json=body).status_code == 422
    response = client.post(
        path,
        headers={"Idempotency-Key": "quality-waiver-request-key"},
        json=body,
    )
    assert response.status_code == 202
    name, engine, kwargs = service.calls[-1]
    assert name == "request_quality_waiver"
    assert engine == "mutation-engine"
    assert kwargs["dataset_id"] == "dataset-a"
    assert kwargs["release_id"] == "release-1"
    assert kwargs["channel_id"] == "channel-production"
    assert kwargs["expected_policy_revision"] == 2
    assert kwargs["expected_channel_revision"] == 4
    assert kwargs["requested_expires_at"].isoformat().startswith("2026-08-29T08:00:00")
