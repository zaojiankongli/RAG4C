from __future__ import annotations

from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
)


def test_fixed_workspace_permission_model_v1_is_stable() -> None:
    from core.enterprise_workspace_authorization import (
        PERMISSION_MODEL_VERSION,
        permission_matrix_fingerprint,
        workspace_permission_model,
    )

    assert PERMISSION_MODEL_VERSION == 1
    assert workspace_permission_model() == {
        "owner": {
            "dataset_role": "manager",
            "permissions": [
                KNOWLEDGE_AUDIT,
                KNOWLEDGE_DELETE,
                KNOWLEDGE_MANAGE,
                KNOWLEDGE_READ,
                KNOWLEDGE_WRITE,
            ],
        },
        "admin": {
            "dataset_role": "manager",
            "permissions": [
                KNOWLEDGE_AUDIT,
                KNOWLEDGE_DELETE,
                KNOWLEDGE_MANAGE,
                KNOWLEDGE_READ,
                KNOWLEDGE_WRITE,
            ],
        },
        "editor": {
            "dataset_role": "editor",
            "permissions": [KNOWLEDGE_DELETE, KNOWLEDGE_READ, KNOWLEDGE_WRITE],
        },
        "viewer": {
            "dataset_role": "viewer",
            "permissions": [KNOWLEDGE_READ],
        },
    }
    first = permission_matrix_fingerprint()
    assert first == permission_matrix_fingerprint()
    assert len(first) == 64


def _authorization_engine(tmp_path):
    from sqlalchemy.orm import Session
    from tests.test_enterprise_workspace_authorization_migration import (
        engine_for,
        seed_0026,
        sqlite_url,
        upgrade_0027,
    )
    from models.orm import (
        Account,
        Dataset,
        TenantMember,
        TenantWorkspaceDataset,
        TenantWorkspaceMember,
    )

    url = sqlite_url(tmp_path / "workspace-authorization-core.db")
    seed_0026(url)
    upgrade_0027(url)
    engine = engine_for(url)
    with Session(engine) as session:
        session.add(Account(id="viewer-a", name="Viewer A", email="viewer-a@stage17.test"))
        session.flush()
        session.add(
            TenantMember(
                account_id="viewer-a", tenant_id="tenant-a", role="member", status="active"
            )
        )
        session.flush()
        session.add(
            Dataset(
                id="dataset-stage17",
                tenant_id="tenant-a",
                name="Stage17 Dataset",
                owner_id="owner-a",
                status="active",
                acl_mode="dataset_acl",
                acl_revision=1,
            )
        )
        session.flush()
        session.add(
            TenantWorkspaceMember(
                tenant_id="tenant-a",
                workspace_id="workspace-default-tenant-a",
                account_id="viewer-a",
                role="editor",
                status="active",
                revision=1,
                created_by="owner-a",
                updated_by="owner-a",
            )
        )
        session.add(
            TenantWorkspaceDataset(
                tenant_id="tenant-a",
                workspace_id="workspace-default-tenant-a",
                dataset_id="dataset-stage17",
                binding_kind="primary",
                active_primary_slot="primary",
                status="active",
                revision=1,
                created_by="owner-a",
                updated_by="owner-a",
            )
        )
        session.commit()
    return engine


def test_shadow_never_grants_and_enforced_unions_permissions(tmp_path) -> None:
    from sqlalchemy import text
    from core.enterprise_access_control import evaluate_dataset_permissions
    from core.knowledge_permissions import KNOWLEDGE_READ, KNOWLEDGE_WRITE

    engine = _authorization_engine(tmp_path)
    try:
        shadow = evaluate_dataset_permissions(
            engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
        )
        assert shadow.effective_permissions == frozenset()
        assert shadow.workspace_authorization["state"] == "workspace_authorization_shadow"
        assert KNOWLEDGE_WRITE in shadow.workspace_authorization["candidate_permissions"]
        assert shadow.workspace_authorization["granted_permissions"] == []

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_workspace_authorization_policies "
                    "SET mode='enforced', revision=revision+1, enforced_at=CURRENT_TIMESTAMP, "
                    "enforced_by='owner-a', disabled_at=NULL, disabled_by=NULL "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a'"
                )
            )
        enforced = evaluate_dataset_permissions(
            engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
        )
        assert KNOWLEDGE_READ in enforced.effective_permissions
        assert KNOWLEDGE_WRITE in enforced.effective_permissions
        assert enforced.dataset_role == "editor"
        assert enforced.workspace_authorization["state"] == "workspace_authorization_enforced"
    finally:
        engine.dispose()


def test_removed_workspace_member_revokes_enforced_contribution_immediately(tmp_path) -> None:
    from sqlalchemy import text
    from core.enterprise_access_control import evaluate_dataset_permissions
    from core.knowledge_permissions import KNOWLEDGE_WRITE

    engine = _authorization_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_workspace_authorization_policies SET mode='enforced', "
                    "revision=revision+1, enforced_at=CURRENT_TIMESTAMP, enforced_by='owner-a' "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a'"
                )
            )
        assert (
            KNOWLEDGE_WRITE
            in evaluate_dataset_permissions(
                engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
            ).effective_permissions
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_workspace_members SET status='removed', revision=revision+1, "
                    "removed_at=CURRENT_TIMESTAMP, removed_by='owner-a' "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a' "
                    "AND account_id='viewer-a'"
                )
            )
        decision = evaluate_dataset_permissions(
            engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
        )
        assert KNOWLEDGE_WRITE not in decision.effective_permissions
        assert decision.workspace_authorization["workspace_roles"] == []
    finally:
        engine.dispose()


def test_policy_mode_change_is_revision_fenced_audited_and_idempotent(tmp_path) -> None:
    from sqlalchemy import text
    from core.enterprise_workspace_authorization import (
        change_workspace_authorization_mode,
        permission_matrix_fingerprint,
    )

    engine = _authorization_engine(tmp_path)
    try:
        first = change_workspace_authorization_mode(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            workspace_id="workspace-default-tenant-a",
            expected_workspace_revision=1,
            expected_policy_revision=1,
            target_mode="enforced",
            expected_permission_model_version=1,
            permission_matrix_fingerprint=permission_matrix_fingerprint(),
            reason="Shadow evidence approved",
            request_id="stage17-mode-change",
            request_ip="127.0.0.1",
            idempotency_key="stage17-mode-change",
        )
        replay = change_workspace_authorization_mode(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            actor_role="owner",
            workspace_id="workspace-default-tenant-a",
            expected_workspace_revision=1,
            expected_policy_revision=1,
            target_mode="enforced",
            expected_permission_model_version=1,
            permission_matrix_fingerprint=permission_matrix_fingerprint(),
            reason="Shadow evidence approved",
            request_id="stage17-mode-change",
            request_ip="127.0.0.1",
            idempotency_key="stage17-mode-change",
        )
        assert first == replay
        assert first["authorization_policy"]["mode"] == "enforced"
        assert first["authorization_policy"]["revision"] == 2
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_audit_events "
                        "WHERE action='workspace.authorization.mode.changed'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_new_workspace_receives_shadow_policy_in_same_transaction(tmp_path) -> None:
    from sqlalchemy import text
    from core.enterprise_workspace_control import create_workspace

    engine = _authorization_engine(tmp_path)
    try:
        created = create_workspace(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            code="stage17-new",
            name="Stage17 New",
            environment="testing",
            reason="Create governed workspace",
            idempotency_key="stage17-new-workspace",
            request_id="stage17-new-workspace",
        )
        workspace_id = created.body["workspace"]["id"]
        with engine.connect() as connection:
            policy = (
                connection.execute(
                    text(
                        "SELECT mode, permission_model_version, revision FROM "
                        "tenant_workspace_authorization_policies "
                        "WHERE tenant_id='tenant-a' AND workspace_id=:workspace_id"
                    ),
                    {"workspace_id": workspace_id},
                )
                .mappings()
                .one()
            )
        assert dict(policy) == {"mode": "shadow", "permission_model_version": 1, "revision": 1}
    finally:
        engine.dispose()
