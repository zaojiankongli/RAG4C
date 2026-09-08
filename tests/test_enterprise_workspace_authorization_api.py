from __future__ import annotations
import time
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from server.knowledge_auth import issue_knowledge_actor_token
from tests.test_enterprise_workspace_api import _auth_engine


def _settings():
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("workspace-auth-api-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _headers(settings, *, key=None):
    token = issue_knowledge_actor_token(
        "owner-a", "tenant-a", 300, int(time.time()), settings=settings
    )
    result = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": "tenant-a",
        "X-RAG4C-Actor": "owner-a",
        "X-Request-ID": "stage17-api",
    }
    if key:
        result["Idempotency-Key"] = key
    return result


class Service:
    def __init__(self):
        self.calls = []
        self.failure = None

    def get_workspace_authorization_policy(self, engine, **kwargs):
        self.calls.append(("policy", engine, kwargs))
        if self.failure:
            raise self.failure
        return {
            "policy": {
                "id": "p",
                "tenant_id": "tenant-a",
                "workspace_id": kwargs["workspace_id"],
                "mode": "shadow",
                "permission_model_version": 1,
                "revision": 3,
                "created_at": None,
                "created_by": "owner-a",
                "updated_at": None,
                "updated_by": "owner-a",
                "enforced_at": None,
                "enforced_by": None,
                "disabled_at": None,
                "disabled_by": None,
            },
            "evidence": {
                "active_member_count": 1,
                "active_dataset_binding_count": 1,
                "catalog_revision": "0027_enterprise_workspace_authorization",
                "permission_matrix_fingerprint": "a" * 64,
                "matching_approval_policy": None,
                "latest_audit_event": None,
            },
        }

    def get_workspace_authorization_impact(self, engine, **kwargs):
        self.calls.append(("impact", engine, kwargs))
        return {
            "impact": {
                "dataset_id": kwargs["dataset_id"],
                "state": "workspace_authorization_shadow",
                "tenant_role": "owner",
                "dataset_acl_role": "manager",
                "matched_grants": [],
                "workspace_roles": [],
                "contributing_workspaces": [],
                "current_effective_permissions": ["knowledge.read"],
                "candidate_permissions": [],
                "would_grant_permissions": [],
                "granted_permissions": [],
                "warnings": [],
            }
        }

    def change_workspace_authorization_mode(self, engine, **kwargs):
        self.calls.append(("change", engine, kwargs))
        return {"authorization_policy": {"id": "p"}}


def _client(monkeypatch, service):
    from server import enterprise_workspace_authorization_api as api

    auth = _auth_engine()
    read = object()
    mutation = object()
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = auth
    app.state.knowledge_auth_settings = settings
    monkeypatch.setattr(api, "_service", lambda: service)
    app.include_router(
        api.build_workspace_authorization_router(
            read_engine_provider=lambda: read, mutation_engine_provider=lambda: mutation
        )
    )
    return TestClient(app), read, mutation, settings, auth


def test_policy_impact_and_mode_routes_are_actor_tenant_bound(monkeypatch):
    service = Service()
    client, read, mutation, settings, auth = _client(monkeypatch, service)
    try:
        assert (
            client.get(
                "/api/enterprise/workspaces/workspace-prod/authorization",
                headers=_headers(settings),
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/enterprise/workspaces/workspace-prod/authorization/impact",
                params={"dataset_id": "dataset-a"},
                headers=_headers(settings),
            ).status_code
            == 200
        )
        response = client.patch(
            "/api/enterprise/workspaces/workspace-prod/authorization",
            headers=_headers(settings, key="stage17-key"),
            json={"expected_revision": 3, "target_mode": "shadow", "reason": "Enable shadow"},
        )
        assert response.status_code == 200
        change = next(item for item in service.calls if item[0] == "change")
        assert change[1] is mutation
        assert change[2]["tenant_id"] == "tenant-a" and change[2]["actor_id"] == "owner-a"
        assert change[2]["expected_policy_revision"] == 3
        assert change[2]["target_mode"] == "shadow"
        assert change[2]["idempotency_key"] == "stage17-key"
    finally:
        client.close()
        auth.dispose()


def test_strict_mode_body_and_idempotency(monkeypatch):
    service = Service()
    client, _r, _m, settings, auth = _client(monkeypatch, service)
    try:
        assert (
            client.patch(
                "/api/enterprise/workspaces/workspace-prod/authorization",
                headers=_headers(settings),
                json={"expected_revision": 3, "target_mode": "shadow", "reason": "x"},
            ).status_code
            == 422
        )
        assert (
            client.patch(
                "/api/enterprise/workspaces/workspace-prod/authorization",
                headers=_headers(settings, key="k"),
                json={
                    "expected_revision": 3,
                    "target_mode": "shadow",
                    "reason": "x",
                    "is_default": True,
                },
            ).status_code
            == 422
        )
    finally:
        client.close()
        auth.dispose()


def test_approval_required_error_preserves_safe_policy_hint(monkeypatch):
    from core.enterprise_workspace_authorization import WorkspaceAuthorizationApprovalRequired

    service = Service()
    service.failure = WorkspaceAuthorizationApprovalRequired(
        {
            "id": "approval-policy",
            "name": "Workspace rollout",
            "required_approvals": 2,
            "secret": "no",
        }
    )
    client, _r, _m, settings, auth = _client(monkeypatch, service)
    try:
        response = client.get(
            "/api/enterprise/workspaces/workspace-prod/authorization", headers=_headers(settings)
        )
        assert response.status_code == 409
        assert response.json()["detail"]["approval_required"] == {
            "policy_id": "approval-policy",
            "policy_name": "Workspace rollout",
            "required_approvals": 2,
        }
        assert "secret" not in response.text
    finally:
        client.close()
        auth.dispose()
