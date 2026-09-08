"""Stage 14 RED/GREEN contracts for approval-gated Dataset ACL disable.

The harness upgrades a real temporary SQLite catalog through the production
0025 migration and seeds a real Dataset row.  No production database or live
ACL mutation is ever used.
"""

from __future__ import annotations

from datetime import datetime
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, text

from tests.test_enterprise_approval_api import (
    NOW,
    SETTINGS,
    TENANT_A,
    _engine as _approval_engine,
    _headers as _approval_headers,
)

DATASET_ID = "dataset-stage14"
ACTION = "dataset_acl_disable"
RESOURCE_TYPE = "knowledge_base"


class _Missing:
    def __getattr__(self, name: str) -> Any:
        pytest.fail(f"Stage 14 implementation is missing: {name}")


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return _Missing()


def _seed_dataset(engine: Any, *, revision: int = 7, mode: str = "dataset_acl") -> None:
    with engine.begin() as connection:
        datasets = Table("datasets", MetaData(), autoload_with=connection)
        values = {
            "id": DATASET_ID,
            "tenant_id": TENANT_A,
            "name": "Stage 14 Knowledge Base",
            "description": "approval-gated ACL fixture",
            "status": "active",
            "profile_revision": 1,
            "owner_id": "owner-a",
            "visibility": "private",
            "acl_mode": mode,
            "acl_revision": revision,
            "profile_json": {},
            "parser_policy": {},
            "chunk_policy": {},
            "retrieval_policy": {},
            "retention_policy": {},
            "metadata_policy": {},
            "default_language": "zh-CN",
            "graph_enabled": False,
            "qa_enabled": True,
            "mutation_generation": 0,
            "serving_generation": 0,
            "doc_count": 0,
            "chunk_count": 0,
            "created_at": NOW,
            "updated_at": NOW,
        }
        available = {str(column.name) for column in datasets.columns}
        connection.execute(
            datasets.insert().values(
                {key: value for key, value in values.items() if key in available}
            )
        )


def _set_dataset(engine: Any, *, revision: int = 7, mode: str = "dataset_acl") -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE datasets SET acl_mode=:mode, acl_revision=:revision "
                "WHERE tenant_id=:tenant_id AND id=:dataset_id"
            ),
            {"mode": mode, "revision": revision, "tenant_id": TENANT_A, "dataset_id": DATASET_ID},
        )


def _dataset(engine: Any) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT id, tenant_id, acl_mode, acl_revision FROM datasets "
                    "WHERE tenant_id=:tenant_id AND id=:dataset_id"
                ),
                {"tenant_id": TENANT_A, "dataset_id": DATASET_ID},
            )
            .mappings()
            .one()
        )
        return dict(row)


def _audit_rows(engine: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT action, resource_type, resource_id, before_snapshot, after_snapshot "
                    "FROM tenant_audit_events WHERE tenant_id=:tenant_id ORDER BY sequence"
                ),
                {"tenant_id": TENANT_A},
            ).mappings()
        ]


def _client(
    engine: Any,
    *,
    adapter_registry: dict[str, Any] | None = None,
    now: datetime = NOW,
) -> TestClient:
    approval_api = _module("server.enterprise_approval_api")
    access_api = _module("server.enterprise_access_graph_api")
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    common = {
        "read_engine_provider": lambda: engine,
        "mutation_engine_provider": lambda: engine,
    }
    app.include_router(access_api.build_enterprise_access_graph_router(**common))
    approval_kwargs = {**common, "now_provider": lambda: now}
    if adapter_registry is not None:
        approval_kwargs["execution_adapters"] = adapter_registry
    app.include_router(approval_api.build_enterprise_approval_router(**approval_kwargs))
    return TestClient(app)


def _headers(actor: str, *, key: str) -> dict[str, str]:
    return _approval_headers(actor, tenant=TENANT_A, key=key)


def _policy(
    client: TestClient,
    *,
    key: str,
    scope: str | None = f"{RESOURCE_TYPE}:{DATASET_ID}",
    threshold: int = 2,
    action_type: str = ACTION,
) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/policies",
        headers=_headers("admin-a", key=key),
        json={
            "name": "Disable Dataset ACL",
            "action_type": action_type,
            "resource_scope": scope,
            "required_approvals": threshold,
            "request_expiry_minutes": 60,
            "approvers": [
                {"kind": "account", "ref": "owner-a"},
                {"kind": "account", "ref": "admin-a"},
            ],
            "reason": "Stage 14 policy",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["policy"]


def _request(
    client: TestClient,
    policy_id: str,
    *,
    key: str,
    actor: str = "member-a",
    expected_revision: int = 7,
) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/requests",
        headers=_headers(actor, key=key),
        json={
            "policy_id": policy_id,
            "resource_type": RESOURCE_TYPE,
            "resource_id": DATASET_ID,
            "snapshot": {
                "dataset_id": DATASET_ID,
                "expected_acl_revision": expected_revision,
                "current_acl_mode": "dataset_acl",
                "database_url": "mysql://user:secret@db.internal/catalog",
            },
            "reason": "Disable ACL after governance review",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["request"]


def _approve_ticket(
    client: TestClient,
    request: dict[str, Any],
    *,
    key_prefix: str,
) -> tuple[dict[str, Any], str]:
    first = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers("admin-a", key=f"{key_prefix}-one"),
        json={"revision": 1, "comment": "一审"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    if first_body["request"]["status"] == "approved":
        return first_body["request"], first_body["execution"]["ticket"]
    final = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers("owner-a", key=f"{key_prefix}-two"),
        json={"revision": 2, "comment": "二审"},
    )
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["request"]["status"] == "approved"
    return body["request"], body["execution"]["ticket"]


def _engine(tmp_path: Path) -> Any:
    engine = _approval_engine(tmp_path)
    _seed_dataset(engine)
    return engine


def test_direct_disable_without_matching_policy_preserves_legacy_acl_mutation(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0025_enterprise_approval_control")
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers("admin-a", key="stage14-direct-no-policy"),
            json={"expected_acl_revision": 7, "reason": "无审批规则时沿用旧流程"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["dataset"]["acl_mode"] == "tenant_role"
        assert _dataset(engine)["acl_revision"] == 8
        assert [row["action"] for row in _audit_rows(engine)].count(
            "dataset_access_control.disabled"
        ) == 1
    finally:
        client.close()
        engine.dispose()


def test_matching_dataset_acl_policy_blocks_direct_disable_with_sanitized_evidence(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(client, key="stage14-policy-exact")
        response = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers("admin-a", key="stage14-direct-blocked"),
            json={"expected_acl_revision": 7, "reason": "尝试直接停用"},
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "dataset_acl_approval_required"
        assert detail["policy_id"] == policy["id"]
        assert detail["policy_revision"] == policy["revision"]
        assert detail["required_approvals"] == 2
        assert detail["request_expiry_minutes"] == 60
        assert "secret" not in json.dumps(detail, ensure_ascii=False).casefold()
        assert _dataset(engine)["acl_mode"] == "dataset_acl"
        assert not [
            row for row in _audit_rows(engine) if row["action"] == "dataset_access_control.disabled"
        ]
    finally:
        client.close()
        engine.dispose()


def test_wildcard_policy_resolves_and_full_approval_executes_dataset_consumer_with_two_audits(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    consumers = _module("server.enterprise_approval_consumers")
    payloads: list[dict[str, Any]] = []
    base_consumer = consumers.build_dataset_acl_disable_consumer(
        mutation_engine_provider=lambda: engine
    )

    def capture(payload: dict[str, Any]) -> Any:
        payloads.append(dict(payload))
        return base_consumer(payload)

    client = _client(engine, adapter_registry={ACTION: capture})
    try:
        policy = _policy(
            client,
            key="stage14-policy-wildcard",
            scope=f"{RESOURCE_TYPE}:*",
        )
        blocked = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers("admin-a", key="stage14-wildcard-block"),
            json={"expected_acl_revision": 7, "reason": "直接停用应被拦截"},
        )
        assert blocked.status_code == 409, blocked.text
        request = _request(client, policy["id"], key="stage14-request")
        detail = client.get(
            f"/api/enterprise/approvals/requests/{request['id']}",
            headers=_headers("admin-a", key="stage14-detail-adapter"),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["request"]["execution_adapter_status"] == "connected"
        assert detail.json()["execution"]["adapter"] == "connected"
        approved, ticket = _approve_ticket(client, request, key_prefix="stage14-approve")
        consumed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="stage14-consume-success"),
            json={
                "ticket": ticket,
                "revision": approved["revision"],
                "action_type": ACTION,
                "resource_type": RESOURCE_TYPE,
                "resource_id": DATASET_ID,
            },
        )
        assert consumed.status_code == 200, consumed.text
        body = consumed.json()
        assert body["request"]["status"] == "executed"
        assert body["execution"]["state"] == "executed"
        result_text = json.dumps(body["execution"], ensure_ascii=False)
        assert "mysql://user:secret" not in result_text
        assert "database_url" not in result_text.casefold()
        assert _dataset(engine)["acl_mode"] == "tenant_role"
        assert _dataset(engine)["acl_revision"] == 8
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["tenant_id"] == TENANT_A
        assert payload["approval_request_id"] == request["id"]
        assert payload["request_id"] == request["id"]
        assert payload["execution_id"].startswith("approval-execution-")
        assert payload["action_type"] == ACTION
        assert payload["resource_type"] == RESOURCE_TYPE
        assert payload["resource_id"] == DATASET_ID
        assert payload["requester_id"] == "member-a"
        assert payload["consumer_actor_id"] == "admin-a"
        assert payload["consumer_actor_role"] == "admin"
        assert payload["request_snapshot"]["dataset_id"] == DATASET_ID
        assert "secret" not in json.dumps(payload, ensure_ascii=False).casefold()
        assert payload["request_evidence"]["request_id"]
        assert payload["request_evidence"]["request_ip"]
        actions = [row["action"] for row in _audit_rows(engine)]
        assert "approval.ticket.consumed" in actions
        assert "dataset_access_control.disabled" in actions
    finally:
        client.close()
        engine.dispose()


def test_stale_acl_revision_marks_execution_failed_and_same_key_replays_without_duplicate_acl_call(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    consumers = _module("server.enterprise_approval_consumers")
    calls: list[dict[str, Any]] = []
    base_consumer = consumers.build_dataset_acl_disable_consumer(
        mutation_engine_provider=lambda: engine
    )

    def capture(payload: dict[str, Any]) -> Any:
        calls.append(dict(payload))
        return base_consumer(payload)

    client = _client(engine, adapter_registry={ACTION: capture})
    try:
        policy = _policy(
            client,
            key="stage14-policy-stale",
            threshold=1,
            scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
        )
        request = _request(client, policy["id"], key="stage14-request-stale")
        approved, ticket = _approve_ticket(client, request, key_prefix="stage14-approve-stale")
        _set_dataset(engine, revision=8, mode="dataset_acl")
        body = {
            "ticket": ticket,
            "revision": approved["revision"],
            "action_type": ACTION,
            "resource_type": RESOURCE_TYPE,
            "resource_id": DATASET_ID,
        }
        failed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="stage14-consume-stale"),
            json=body,
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["request"]["status"] == "execution_failed"
        assert _dataset(engine)["acl_mode"] == "dataset_acl"
        assert _dataset(engine)["acl_revision"] == 8
        assert len(calls) == 1
        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="stage14-consume-stale"),
            json=body,
        )
        assert replay.status_code == failed.status_code
        assert replay.json() == failed.json()
        assert len(calls) == 1
    finally:
        client.close()
        engine.dispose()


def test_unsupported_action_is_not_connected_before_approval_ticket_claim(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, adapter_registry={ACTION: lambda _payload: {"ok": True}})
    try:
        policy = _policy(
            client,
            key="stage14-policy-unsupported",
            action_type="catalog_upgrade",
            scope="catalog:*",
            threshold=1,
        )
        request_response = client.post(
            "/api/enterprise/approvals/requests",
            headers=_headers("member-a", key="stage14-request-unsupported"),
            json={
                "policy_id": policy["id"],
                "resource_type": "catalog",
                "resource_id": "catalog-main",
                "snapshot": {"catalog_id": "catalog-main"},
                "reason": "Unsupported action contract",
            },
        )
        assert request_response.status_code == 201, request_response.text
        request = request_response.json()["request"]
        approved, ticket = _approve_ticket(
            client,
            request,
            key_prefix="stage14-approve-unsupported",
        )
        response = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="stage14-consume-unsupported"),
            json={
                "ticket": ticket,
                "revision": approved["revision"],
                "action_type": "catalog_upgrade",
                "resource_type": "catalog",
                "resource_id": "catalog-main",
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "execution_adapter_not_connected"
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT status, revision, ticket_consumed_at FROM tenant_approval_requests "
                        "WHERE tenant_id=:tenant_id AND id=:request_id"
                    ),
                    {"tenant_id": TENANT_A, "request_id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row["status"] == "approved"
        assert row["revision"] == approved["revision"]
        assert row["ticket_consumed_at"] is None
    finally:
        client.close()
        engine.dispose()


def test_production_app_source_registers_dataset_acl_consumer_registry() -> None:
    source = Path("server/app.py").read_text(encoding="utf-8")
    assert "enterprise_approval_consumers" in source
    assert "build_enterprise_approval_execution_adapters" in source
    assert "execution_adapters" in source


def test_non_manager_cannot_claim_dataset_acl_execution_ticket(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, adapter_registry={ACTION: lambda _payload: {"ok": True}})
    try:
        policy = _policy(
            client,
            key="stage14-policy-consumer-role",
            threshold=1,
            scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
        )
        request = _request(client, policy["id"], key="stage14-request-consumer-role")
        approved, ticket = _approve_ticket(
            client,
            request,
            key_prefix="stage14-approve-consumer-role",
        )
        denied = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("member-a", key="stage14-consume-member"),
            json={
                "ticket": ticket,
                "revision": approved["revision"],
                "action_type": ACTION,
                "resource_type": RESOURCE_TYPE,
                "resource_id": DATASET_ID,
            },
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "approval_execution_forbidden"
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM tenant_approval_requests WHERE id=:id"),
                    {"id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row["status"] == "approved"
        assert row["revision"] == approved["revision"]
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize(
    "scope",
    ["global", "*", "knowledge_base:wildcard:global", "knowledge_base/*"],
)
def test_backend_global_scope_contract_matches_frontend_and_blocks_direct_disable(
    tmp_path: Path, scope: str
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        _policy(
            client,
            key="stage14-policy-global-" + scope.replace("/", "-").replace("*", "all"),
            threshold=1,
            scope=scope,
        )
        blocked = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers("admin-a", key="stage14-global-block"),
            json={"expected_acl_revision": 7, "reason": "全局规则必须阻断直接停用"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "dataset_acl_approval_required"
        assert _dataset(engine)["acl_mode"] == "dataset_acl"
    finally:
        client.close()
        engine.dispose()


def test_transactional_acl_gate_blocks_when_route_precheck_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(
            client,
            key="stage14-policy-transactional-gate",
            threshold=1,
            scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
        )
        monkeypatch.setattr(
            "server.enterprise_access_graph_api.resolve_active_approval_policy",
            lambda *_args, **_kwargs: None,
        )
        blocked = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers("admin-a", key="stage14-stale-route-precheck"),
            json={"expected_acl_revision": 7, "reason": "事务内必须重新检查审批规则"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "dataset_acl_approval_required"
        assert blocked.json()["detail"]["policy_id"] == policy["id"]
        assert _dataset(engine)["acl_mode"] == "dataset_acl"
    finally:
        client.close()
        engine.dispose()
