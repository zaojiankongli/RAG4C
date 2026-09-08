from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.knowledge_auth import KnowledgeActor


@dataclass
class Result:
    body: dict[str, Any]
    status: int = 200


TIME = "2026-08-30T12:00:00.000000Z"
DIGEST = "a" * 64


def actor() -> KnowledgeActor:
    return KnowledgeActor(
        account_id="owner-a",
        tenant_id="tenant-a",
        role="owner",
        request_id="request-stage26",
        request_ip="127.0.0.1",
    )


def profile() -> dict[str, Any]:
    return {
        "id": "profile-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset_id": "dataset-a",
        "name": "Operations Serving",
        "normalized_name": "operations serving",
        "status": "active",
        "active_profile_key": "dataset-a",
        "revision": 3,
        "current_policy_revision_id": "policy-a",
        "current_snapshot_id": "snapshot-a",
        "created_at": TIME,
        "created_by": "owner-a",
        "updated_at": TIME,
        "updated_by": "owner-a",
        "archived_at": None,
        "archived_by": None,
    }


def policy() -> dict[str, Any]:
    return {
        "id": "policy-a",
        "tenant_id": "tenant-a",
        "profile_id": "profile-a",
        "revision": 1,
        "max_source_staleness_seconds": 3600,
        "max_parse_lag_seconds": 1800,
        "max_index_lag_seconds": 1800,
        "max_failed_document_count": 0,
        "max_pending_index_count": 0,
        "require_current_release": True,
        "require_passing_certification": True,
        "policy_digest": DIGEST,
        "created_at": TIME,
        "created_by": "owner-a",
    }


def stage_facts() -> list[dict[str, Any]]:
    from core.enterprise_knowledge_serving import canonical_knowledge_serving_stage_fact

    return [
        canonical_knowledge_serving_stage_fact(
            {
                "id": f"stage-{code}",
                "tenant_id": "tenant-a",
                "profile_id": "profile-a",
                "snapshot_id": "snapshot-a",
                "stage_code": code,
                "sequence": sequence,
                "state": "ready",
                "item_count": 1,
                "ready_count": 1 if code == "source" else 0,
                "warning_count": 0,
                "pending_count": 0,
                "error_count": 0,
                "lag_seconds": 0,
                "expected_revision": 1,
                "observed_revision": 1,
                "expected_digest": DIGEST,
                "observed_digest": DIGEST,
                "safe_error_code": None,
                "safe_error": None,
                "observed_at": TIME,
            }
        )
        for sequence, code in enumerate(("source", "parse", "chunk", "index", "serve"), 1)
    ]


def stage_fact() -> dict[str, Any]:
    return stage_facts()[0]


def snapshot() -> dict[str, Any]:
    from core.enterprise_knowledge_serving import canonical_knowledge_serving_snapshot

    return canonical_knowledge_serving_snapshot(
        {
            "id": "snapshot-a",
            "tenant_id": "tenant-a",
            "profile_id": "profile-a",
            "policy_revision_id": "policy-a",
            "observation_key": "observation-a",
            "state": "ready",
            "source_count": 1,
            "ready_source_count": 1,
            "stale_source_count": 0,
            "active_document_count": 1,
            "failed_document_count": 0,
            "pending_index_count": 0,
            "expected_serving_generation": 1,
            "observed_serving_generation": 1,
            "current_release_id": "release-a",
            "current_certification_id": "cert-a",
            "stage_count": 5,
            "ready_stage_count": 5,
            "blocked_stage_count": 0,
            "snapshot_digest": None,
            "as_of": TIME,
            "created_at": TIME,
            "created_by": "owner-a",
        },
        stage_facts=stage_facts(),
        evidence_links=[],
    )


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, operation: str) -> Callable[..., Result]:
        def call(engine: Any, **kwargs: Any) -> Result:
            self.calls.append((operation, engine, kwargs))
            if operation == "get_serving_summary":
                return Result(
                    {
                        "tenant_id": "tenant-a",
                        "dataset_id": kwargs["dataset_id"],
                        "profile_id": "profile-a",
                        "profile_name": "Operations Serving",
                        "profile_status": "active",
                        "state": "ready",
                        "as_of": TIME,
                        "snapshot_id": "snapshot-a",
                        "snapshot_digest": snapshot()["snapshot_digest"],
                        "policy_revision": 1,
                        "serving_generation": 1,
                        "source_count": 1,
                        "ready_source_count": 1,
                        "stale_source_count": 0,
                        "active_document_count": 1,
                        "failed_document_count": 0,
                        "pending_index_count": 0,
                        "stage_count": 5,
                        "ready_stage_count": 5,
                        "blocked_stage_count": 0,
                        "current_release_id": "release-a",
                        "current_certification_id": "cert-a",
                        "stage_facts": stage_facts(),
                        "reason_code": None,
                    }
                )
            if operation == "get_serving_profile":
                return Result(
                    {
                        "profile": profile(),
                        "current_policy": policy(),
                        "current_snapshot": snapshot(),
                    }
                )
            if operation == "list_serving_snapshots":
                return Result(
                    {
                        "items": [snapshot()],
                        "count": 1,
                        "next_cursor": None,
                        "invalid_item_count": 0,
                    }
                )
            if operation == "get_serving_snapshot":
                return Result(
                    {
                        "snapshot": snapshot(),
                        "stage_facts": stage_facts(),
                        "evidence_links": [],
                        "events": [],
                    }
                )
            if operation == "list_serving_stage_facts":
                return Result(
                    {
                        "items": [stage_fact()],
                        "count": 1,
                        "next_cursor": None,
                        "invalid_item_count": 0,
                    }
                )
            if operation == "list_serving_events":
                return Result(
                    {"items": [], "count": 0, "next_cursor": None, "invalid_item_count": 0}
                )
            if operation == "create_serving_profile":
                return Result(
                    {
                        "state": "applied",
                        "operation": operation,
                        "resource_id": "profile-a",
                        "revision": 1,
                        "message": None,
                        "retryable": False,
                        "profile": profile(),
                    },
                    status=201,
                )
            if operation == "create_serving_policy_revision":
                return Result(
                    {
                        "state": "applied",
                        "operation": operation,
                        "resource_id": "policy-a",
                        "revision": 2,
                        "message": None,
                        "retryable": False,
                        "policy": policy(),
                    },
                    status=201,
                )
            if operation == "activate_serving_policy":
                return Result(
                    {
                        "state": "applied",
                        "operation": operation,
                        "resource_id": "profile-a",
                        "revision": 3,
                        "message": None,
                        "retryable": False,
                        "profile": profile(),
                        "policy": policy(),
                    }
                )
            if operation == "preview_serving_profile":
                return Result(
                    {
                        "preview": True,
                        "state": "ready",
                        "policy_revision": 1,
                        "stage_facts": [
                            {
                                **stage_fact(),
                                "stage_code": code,
                                "sequence": sequence,
                                "id": f"stage-{code}",
                            }
                            for sequence, code in enumerate(
                                ("source", "parse", "chunk", "index", "serve"), 1
                            )
                        ],
                        "blockers": [],
                    }
                )
            raise AssertionError(f"unexpected operation: {operation}")

        return call


def ready_readiness() -> dict[str, Any]:
    return {
        "status": "ready",
        "missing_capability_groups": [],
        "mutations_safe": True,
    }


def client(
    service: Any | None = None,
    *,
    readiness_provider: Callable[[], Any] | None = None,
    read_engine_provider: Callable[[], Any] | None = None,
    mutation_engine_provider: Callable[[], Any] | None = None,
) -> tuple[TestClient, Any]:
    from server.enterprise_knowledge_serving_api import build_enterprise_knowledge_serving_router

    target = service or RecordingService()
    app = FastAPI()
    app.include_router(
        build_enterprise_knowledge_serving_router(
            read_engine_provider=read_engine_provider or (lambda: "read-engine"),
            mutation_engine_provider=mutation_engine_provider or (lambda: "mutation-engine"),
            service=target,
            actor_dependency=lambda: actor(),
            readiness_provider=readiness_provider or ready_readiness,
        )
    )
    return TestClient(app), target


def key(value: str = "stage26-key") -> dict[str, str]:
    return {"Idempotency-Key": value}


def policy_body(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "expected_profile_revision": 1,
        "expected_policy_digest": None,
        "max_source_staleness_seconds": 3600,
        "max_parse_lag_seconds": 1800,
        "max_index_lag_seconds": 1800,
        "max_failed_document_count": 0,
        "max_pending_index_count": 0,
        "require_current_release": True,
        "require_passing_certification": True,
        "reason": "define a bounded serving policy",
    }
    value.update(overrides)
    return value


def test_approved_stage26_routes_are_exact_and_have_no_observe_record_execute_surface() -> None:
    api, _ = client()
    paths = set(api.app.openapi()["paths"])
    assert paths == {
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/summary",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/revisions",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/activate",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots/{snapshot_id}",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/stage-facts",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/events",
        "/api/enterprise/knowledge-bases/{dataset_id}/serving/preview",
    }
    assert not any(any(word in path for word in ("observe", "record", "execute")) for path in paths)


@pytest.mark.parametrize(
    ("path", "body", "idempotency_key"),
    [
        (
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
            {
                "name": "Operations Serving",
                "workspace_id": "workspace-a",
                "reason": "create profile",
            },
            "gate-profile",
        ),
        (
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile/revisions",
            policy_body(),
            "gate-policy",
        ),
        (
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile/activate",
            {
                "policy_revision_id": "policy-a",
                "expected_profile_revision": 2,
                "expected_policy_digest": DIGEST,
                "reason": "activate policy",
            },
            "gate-activate",
        ),
    ],
)
def test_profile_policy_mutations_fail_closed_before_service_when_stage26_is_malformed(
    path: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> None:
    def should_not_resolve_mutation_engine() -> Any:
        pytest.fail("mutation engine must not be resolved when Stage26 is not ready")

    api, service = client(
        readiness_provider=lambda: {
            "status": "malformed",
            "missing_capability_groups": ["enterprise_knowledge_serving_reliability"],
            "mutations_safe": False,
        },
        mutation_engine_provider=should_not_resolve_mutation_engine,
    )

    response = api.post(path, headers=key(idempotency_key), json=body)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"
    assert service.calls == []


def test_reads_and_preview_fail_closed_before_service_when_stage26_is_malformed() -> None:
    def should_not_resolve_read_engine() -> Any:
        pytest.fail("read engine must not be resolved when Stage26 is not ready")

    api, service = client(
        readiness_provider=lambda: {
            "status": "malformed",
            "missing_capability_groups": ["enterprise_knowledge_serving_reliability"],
            "mutations_safe": False,
        },
        read_engine_provider=should_not_resolve_read_engine,
    )

    summary = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/summary")
    preview = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/preview",
        json={
            "profile_id": "profile-a",
            "expected_profile_revision": 3,
            "expected_policy_digest": DIGEST,
            "max_source_staleness_seconds": 30,
            "max_parse_lag_seconds": 60,
            "max_index_lag_seconds": 60,
            "max_failed_document_count": 1,
            "max_pending_index_count": 1,
            "require_current_release": False,
            "require_passing_certification": False,
            "reason": "preview policy",
        },
    )

    assert summary.status_code == 503
    assert preview.status_code == 503
    assert service.calls == []


def test_router_without_readiness_provider_is_fail_closed() -> None:
    from server.enterprise_knowledge_serving_api import build_enterprise_knowledge_serving_router

    app = FastAPI()
    service = RecordingService()
    app.include_router(
        build_enterprise_knowledge_serving_router(
            read_engine_provider=lambda: "read-engine",
            mutation_engine_provider=lambda: "mutation-engine",
            service=service,
            actor_dependency=lambda: actor(),
        )
    )

    response = TestClient(app).get(
        "/api/enterprise/knowledge-bases/dataset-a/serving/summary"
    )

    assert response.status_code == 503
    assert service.calls == []


class MutatingResponseService:
    def __init__(self, operation: str, mutate: Callable[[dict[str, Any]], None]) -> None:
        self.operation = operation
        self.mutate = mutate
        self.calls: list[str] = []
        self.base = RecordingService()

    def __getattr__(self, operation: str) -> Callable[..., Result]:
        delegate = getattr(self.base, operation)

        def call(engine: Any, **kwargs: Any) -> Result:
            result = delegate(engine, **kwargs)
            self.calls.append(operation)
            if operation == self.operation:
                self.mutate(result.body)
            return result

        return call


@pytest.mark.parametrize(
    ("field", "value"),
    [("tenant_id", "tenant-b"), ("dataset_id", "dataset-b")],
)
def test_response_rejects_summary_scope_mismatch(
    field: str, value: str
) -> None:
    service = MutatingResponseService(
        "get_serving_summary", lambda body: body.__setitem__(field, value)
    )
    api, _ = client(service=service)

    response = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/summary")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"
    assert service.calls == ["get_serving_summary"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("count", "1"),
        ("count", 9_007_199_254_740_992),
        ("invalid_item_count", True),
        ("invalid_item_count", -1),
        ("invalid_item_count", 2),
    ],
)
def test_response_rejects_invalid_page_counts(field: str, value: Any) -> None:
    service = MutatingResponseService(
        "list_serving_snapshots", lambda body: body.__setitem__(field, value)
    )
    api, _ = client(service=service)

    response = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/snapshots")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"


def test_response_rejects_mutation_profile_outside_request_dataset_scope() -> None:
    service = MutatingResponseService(
        "create_serving_profile",
        lambda body: body["profile"].__setitem__("dataset_id", "dataset-b"),
    )
    api, _ = client(service=service)

    response = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
        headers=key("semantic-profile"),
        json={
            "name": "Operations Serving",
            "workspace_id": "workspace-a",
            "reason": "create profile",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"


def test_response_rejects_stage_fact_counter_above_item_count() -> None:
    service = MutatingResponseService(
        "get_serving_summary",
        lambda body: body["stage_facts"][0].__setitem__("ready_count", 2),
    )
    api, _ = client(service=service)

    response = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/summary")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"


@pytest.mark.parametrize(
    ("operation", "path", "mutate"),
    [
        (
            "get_serving_profile",
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
            lambda body: body["profile"].__setitem__("tenant_id", "tenant-b"),
        ),
        (
            "get_serving_profile",
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
            lambda body: body["current_policy"].__setitem__("profile_id", "profile-b"),
        ),
        (
            "get_serving_snapshot",
            "/api/enterprise/knowledge-bases/dataset-a/serving/snapshots/snapshot-a",
            lambda body: body["stage_facts"][0].__setitem__("snapshot_id", "snapshot-b"),
        ),
        (
            "get_serving_snapshot",
            "/api/enterprise/knowledge-bases/dataset-a/serving/snapshots/snapshot-a",
            lambda body: body["events"].append(
                {
                    "id": "event-a",
                    "tenant_id": "tenant-a",
                    "profile_id": "profile-a",
                    "snapshot_id": "snapshot-b",
                    "stream_key": "profile-a",
                    "sequence": 1,
                    "event_type": "profile_created",
                    "previous_event_digest": None,
                    "event_digest": DIGEST,
                    "actor_id": "owner-a",
                    "request_id": "request-stage26",
                    "safe_snapshot": {},
                    "occurred_at": TIME,
                }
            ),
        ),
    ],
)
def test_response_rejects_profile_snapshot_ownership_mismatch(
    operation: str, path: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    service = MutatingResponseService(operation, mutate)
    api, _ = client(service=service)

    response = api.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_serving_unavailable"


def test_reads_use_read_engine_and_strictly_validate_wire_contract() -> None:
    api, service = client()
    response = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/summary")
    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    assert service.calls[-1][0] == "get_serving_summary"
    assert service.calls[-1][1] == "read-engine"
    detail = api.get("/api/enterprise/knowledge-bases/dataset-a/serving/snapshots/snapshot-a")
    assert detail.status_code == 200
    assert len(detail.json()["stage_facts"]) == 5


def test_mutations_require_idempotency_and_use_mutation_engine() -> None:
    api, service = client()
    missing = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
        json={
            "name": "Operations Serving",
            "workspace_id": "workspace-a",
            "reason": "create profile",
        },
    )
    assert missing.status_code == 422
    created = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
        headers=key("profile-create"),
        json={
            "name": "Operations Serving",
            "workspace_id": "workspace-a",
            "reason": "create profile",
        },
    )
    assert created.status_code == 201
    assert created.json()["profile"]["id"] == "profile-a"
    assert service.calls[-1][0] == "create_serving_profile"
    assert service.calls[-1][1] == "mutation-engine"
    policy_response = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/profile/revisions",
        headers=key("policy-create"),
        json=policy_body(),
    )
    assert policy_response.status_code == 201
    assert policy_response.json()["policy"]["policy_digest"] == DIGEST
    activated = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/profile/activate",
        headers=key("policy-activate"),
        json={
            "policy_revision_id": "policy-a",
            "expected_profile_revision": 2,
            "expected_policy_digest": DIGEST,
            "reason": "activate bounded policy",
        },
    )
    assert activated.status_code == 200
    assert service.calls[-1][0] == "activate_serving_policy"
    assert service.calls[-1][2] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "request_id": "request-stage26",
        "idempotency_key": "policy-activate",
        "dataset_id": "dataset-a",
        "policy_revision_id": "policy-a",
        "expected_profile_revision": 2,
        "expected_policy_digest": DIGEST,
        "reason": "activate bounded policy",
    }


def test_preview_is_zero_write_route_and_rejects_extra_or_unsafe_fields() -> None:
    api, service = client()
    response = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/preview",
        json={
            "profile_id": "profile-a",
            "expected_profile_revision": 3,
            "expected_policy_digest": DIGEST,
            "max_source_staleness_seconds": 30,
            "max_parse_lag_seconds": 60,
            "max_index_lag_seconds": 60,
            "max_failed_document_count": 1,
            "max_pending_index_count": 1,
            "require_current_release": False,
            "require_passing_certification": False,
            "reason": "preview policy",
        },
    )
    assert response.status_code == 200
    assert response.json()["preview"] is True
    assert service.calls[-1][0] == "preview_serving_profile"
    assert service.calls[-1][1] == "read-engine"
    assert service.calls[-1][2] == {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "request_id": "request-stage26",
        "dataset_id": "dataset-a",
        "profile_id": "profile-a",
        "expected_profile_revision": 3,
        "expected_policy_digest": DIGEST,
        "max_source_staleness_seconds": 30,
        "max_parse_lag_seconds": 60,
        "max_index_lag_seconds": 60,
        "max_failed_document_count": 1,
        "max_pending_index_count": 1,
        "require_current_release": False,
        "require_passing_certification": False,
        "reason": "preview policy",
    }
    unsafe = api.post(
        "/api/enterprise/knowledge-bases/dataset-a/serving/preview",
        json={
            "profile_id": "profile-a",
            "expected_profile_revision": 3,
            "expected_policy_digest": DIGEST,
            "max_source_staleness_seconds": 30,
            "max_parse_lag_seconds": 60,
            "max_index_lag_seconds": 60,
            "max_failed_document_count": 1,
            "max_pending_index_count": 1,
            "require_current_release": False,
            "require_passing_certification": False,
            "reason": "preview policy",
            "raw_payload": "SELECT * FROM documents",
        },
    )
    assert unsafe.status_code == 422


def test_stage_facts_query_accepts_only_canonical_stage_codes() -> None:
    api, service = client()
    for code in ("source", "parse", "chunk", "index", "serve"):
        response = api.get(
            "/api/enterprise/knowledge-bases/dataset-a/serving/stage-facts",
            params={"stage_code": code},
        )
        assert response.status_code == 200, response.text
        assert service.calls[-1][2]["stage_code"] == code
    invalid = api.get(
        "/api/enterprise/knowledge-bases/dataset-a/serving/stage-facts",
        params={"stage_code": "ready"},
    )
    assert invalid.status_code == 422


def test_real_service_to_api_integration_uses_temporary_sqlite(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import enterprise_knowledge_serving_service
    from test_enterprise_knowledge_serving_service import _engine, _install_adapters, _stage_tables

    engine, _ = _engine(tmp_path)
    _install_adapters(monkeypatch)
    try:
        app = FastAPI()
        app.include_router(
            __import__(
                "server.enterprise_knowledge_serving_api",
                fromlist=["build_enterprise_knowledge_serving_router"],
            ).build_enterprise_knowledge_serving_router(
                read_engine_provider=lambda: engine,
                mutation_engine_provider=lambda: engine,
                service=enterprise_knowledge_serving_service,
                actor_dependency=lambda: actor(),
                readiness_provider=ready_readiness,
            )
        )
        live = TestClient(app)
        created = live.post(
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile",
            headers=key("real-profile"),
            json={
                "name": "Operations Serving",
                "workspace_id": "workspace-a",
                "reason": "create profile",
            },
        )
        assert created.status_code == 201, created.text
        created_policy = live.post(
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile/revisions",
            headers=key("real-policy"),
            json=policy_body(),
        )
        assert created_policy.status_code == 201, created_policy.text
        current_digest = created_policy.json()["policy"]["policy_digest"]
        activated = live.post(
            "/api/enterprise/knowledge-bases/dataset-a/serving/profile/activate",
            headers=key("real-policy-activate"),
            json={
                "policy_revision_id": created_policy.json()["policy"]["id"],
                "expected_profile_revision": 2,
                "expected_policy_digest": current_digest,
                "reason": "activate policy before preview",
            },
        )
        assert activated.status_code == 200, activated.text
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        tables = _stage_tables()[1]
        with Session(engine) as session:
            before = {
                name: session.scalar(select(func.count()).select_from(table))
                for name, table in tables.items()
            }
        preview = live.post(
            "/api/enterprise/knowledge-bases/dataset-a/serving/preview",
            json={
                "profile_id": created.json()["profile"]["id"],
                "expected_profile_revision": 3,
                "expected_policy_digest": current_digest,
                "max_source_staleness_seconds": 30,
                "max_parse_lag_seconds": 60,
                "max_index_lag_seconds": 60,
                "max_failed_document_count": 1,
                "max_pending_index_count": 1,
                "require_current_release": False,
                "require_passing_certification": False,
                "reason": "preview transient serving policy",
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["preview"] is True
        with Session(engine) as session:
            after = {
                name: session.scalar(select(func.count()).select_from(table))
                for name, table in tables.items()
            }
        assert before == after
        summary = live.get("/api/enterprise/knowledge-bases/dataset-a/serving/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["tenant_id"] == "tenant-a"
    finally:
        engine.dispose()
