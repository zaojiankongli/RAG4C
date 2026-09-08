from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import String, Table, create_engine, event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.orm import Account, Base, Dataset, Tenant, TenantMember

REVISION = "0026_enterprise_workspace_control"
TENANT_A = "tenant-workspace-a"
TENANT_B = "tenant-workspace-b"
NOW = datetime(2026, 8, 27, 16, 0, 0)


def _workspace_tables() -> tuple[Table, Table, Table]:
    from models.orm import TenantWorkspace, TenantWorkspaceDataset, TenantWorkspaceMember

    return (
        TenantWorkspace.__table__,
        TenantWorkspaceMember.__table__,
        TenantWorkspaceDataset.__table__,
    )


WORKSPACES, WORKSPACE_MEMBERS, WORKSPACE_DATASETS = _workspace_tables()


def _engine(*, revision: str = REVISION):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )
    _seed(engine)
    return engine


def _seed(engine: Any) -> None:
    joined = NOW - timedelta(days=30)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, name="Workspace Tenant A", status="active"),
                Tenant(id=TENANT_B, name="Workspace Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@workspace.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@workspace.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@workspace.test"),
                Account(id="viewer-a", name="Viewer A", email="viewer-a@workspace.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@workspace.test"),
            ]
        )
        # The Dataset owner composite FK depends on both the Account and the
        # TenantMember row. Persist each dependency layer before moving on.
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-a",
                    tenant_id=TENANT_A,
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="admin-a",
                    tenant_id=TENANT_A,
                    role="admin",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="editor-a",
                    tenant_id=TENANT_A,
                    role="editor",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="viewer-a",
                    tenant_id=TENANT_A,
                    role="member",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="owner-b",
                    tenant_id=TENANT_B,
                    role="owner",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Dataset(
                    id="dataset-a",
                    tenant_id=TENANT_A,
                    name="Dataset A",
                    owner_id="owner-a",
                    status="active",
                    visibility="private",
                    created_at=joined,
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id=TENANT_A,
                    name="Dataset B",
                    owner_id="owner-a",
                    status="active",
                    visibility="private",
                    created_at=joined,
                ),
            ]
        )
        session.commit()


def _create_workspace(engine: Any, actor_id: str = "owner-a", **overrides: Any):
    from core import enterprise_workspace_control as control

    values = {
        "tenant_id": TENANT_A,
        "actor_id": actor_id,
        "code": "ops",
        "name": "Operations",
        "description": "Primary operations workspace",
        "environment": "production",
        "is_default": False,
        "reason": "创建 Workspace 测试",
        "idempotency_key": f"create-{actor_id}-{overrides.get('code', 'ops')}",
        "request_id": "request-workspace-test",
        "request_ip": "127.0.0.1",
        "now": NOW,
    }
    values.update(overrides)
    return control.create_workspace(engine, **values)


def _workspace_row(engine: Any, workspace_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                WORKSPACES.select().where(
                    WORKSPACES.c.tenant_id == TENANT_A,
                    WORKSPACES.c.id == workspace_id,
                )
            )
            .mappings()
            .one()
        )


def _rows(engine: Any, table: Table) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(table.select()).mappings()]


def test_missing_0026_fails_closed_for_reads_and_mutations() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine(revision="0025_enterprise_approval_control")
    try:
        with pytest.raises(control.WorkspaceMigrationRequired) as error:
            control.list_workspaces(engine, tenant_id=TENANT_A, actor_id="owner-a")
        assert error.value.code == "workspace_migration_required"
        with pytest.raises(control.WorkspaceMigrationRequired):
            _create_workspace(engine)
    finally:
        engine.dispose()


def test_0026_schema_gate_accepts_nullable_active_primary_slot() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        with engine.connect() as connection:
            control._ensure_0026(connection)
            columns_by_table = {
                table_name: {
                    item["name"]: item for item in inspect(connection).get_columns(table_name)
                }
                for table_name in control._REQUIRED_NULLABLE
            }
        column = columns_by_table["tenant_workspace_datasets"]["active_primary_slot"]
        assert "active_primary_slot" in control._REQUIRED_COLUMNS["tenant_workspace_datasets"]
        assert "active_primary_slot" not in control._REQUIRED_NOT_NULL["tenant_workspace_datasets"]
        assert column["nullable"] is True
        for table_name, column_names in control._REQUIRED_NULLABLE.items():
            assert all(
                columns_by_table[table_name][name]["nullable"] is True for name in column_names
            )
    finally:
        engine.dispose()


def test_0026_schema_gate_fails_closed_when_active_primary_slot_is_missing() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    original_inspect = control.inspect
    try:
        real_inspector = inspect(engine)

        class MissingPrimarySlotInspector:
            def get_table_names(self):
                return real_inspector.get_table_names()

            def get_columns(self, table_name):
                columns = real_inspector.get_columns(table_name)
                if table_name == "tenant_workspace_datasets":
                    return [item for item in columns if item["name"] != "active_primary_slot"]
                return columns

            def get_unique_constraints(self, table_name):
                return real_inspector.get_unique_constraints(table_name)

            def get_foreign_keys(self, table_name):
                return real_inspector.get_foreign_keys(table_name)

            def get_check_constraints(self, table_name):
                return real_inspector.get_check_constraints(table_name)

            def get_indexes(self, table_name):
                return real_inspector.get_indexes(table_name)

        control.inspect = lambda _connection: MissingPrimarySlotInspector()
        with engine.connect() as connection:
            with pytest.raises(control.WorkspaceMigrationRequired) as error:
                control._ensure_0026(connection)
        assert "missing column tenant_workspace_datasets.active_primary_slot" in error.value.missing
    finally:
        control.inspect = original_inspect
        engine.dispose()


@pytest.mark.parametrize("malformation", ["type", "unique", "foreign_key", "check", "index"])
def test_0026_schema_gate_rejects_stamped_malformed_contract(
    monkeypatch: pytest.MonkeyPatch, malformation: str
) -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    original_inspect = control.inspect
    try:
        real_inspector = inspect(engine)

        class MalformedInspector:
            bind = real_inspector.bind

            def get_table_names(self):
                return real_inspector.get_table_names()

            def get_columns(self, table_name):
                columns = real_inspector.get_columns(table_name)
                if malformation == "type" and table_name == "tenant_workspaces":
                    columns = [dict(item) for item in columns]
                    for item in columns:
                        if item["name"] == "description":
                            item["type"] = String(256)
                return columns

            def get_unique_constraints(self, table_name):
                values = real_inspector.get_unique_constraints(table_name)
                return (
                    [] if malformation == "unique" and table_name == "tenant_workspaces" else values
                )

            def get_foreign_keys(self, table_name):
                values = real_inspector.get_foreign_keys(table_name)
                return (
                    []
                    if malformation == "foreign_key" and table_name == "tenant_workspace_members"
                    else values
                )

            def get_check_constraints(self, table_name):
                values = real_inspector.get_check_constraints(table_name)
                return (
                    []
                    if malformation == "check" and table_name == "tenant_workspace_datasets"
                    else values
                )

            def get_indexes(self, table_name):
                values = real_inspector.get_indexes(table_name)
                return (
                    [] if malformation == "index" and table_name == "tenant_workspaces" else values
                )

        monkeypatch.setattr(control, "inspect", lambda _connection: MalformedInspector())
        with engine.connect() as connection:
            with pytest.raises(control.WorkspaceMigrationRequired):
                control._ensure_0026(connection)
    finally:
        control.inspect = original_inspect
        engine.dispose()


def test_0026_schema_gate_rejects_tautological_critical_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    real_inspector = inspect(engine)

    class TautologicalInspector:
        bind = real_inspector.bind

        def __getattr__(self, name: str):
            return getattr(real_inspector, name)

        def get_check_constraints(self, table_name: str):
            checks = [dict(item) for item in real_inspector.get_check_constraints(table_name)]
            if table_name == "tenant_workspace_datasets":
                for item in checks:
                    if item.get("name") == "ck_tenant_workspace_datasets_active_primary_slot":
                        item["sqltext"] = f"({item['sqltext']}) OR 1 = 1"
            return checks

    monkeypatch.setattr(control, "inspect", lambda _connection: TautologicalInspector())
    try:
        with engine.connect() as connection:
            with pytest.raises(control.WorkspaceMigrationRequired) as error:
                control._ensure_0026(connection)
        assert any("exact check" in item for item in error.value.missing)
    finally:
        engine.dispose()


def test_non_sqlite_schema_gate_is_never_permanently_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    monkeypatch.setattr(control, "_schema_cache_marker", lambda _connection: None)
    try:
        control._schema_cache_store(engine, None)
        assert control._schema_cache_hit(engine, None) is False
    finally:
        engine.dispose()


def test_0026_schema_gate_rejects_nullable_columns_changed_to_not_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    original_inspect = control.inspect
    try:
        real_inspector = inspect(engine)

        class NonNullableInspector:
            def get_table_names(self):
                return real_inspector.get_table_names()

            def get_columns(self, table_name):
                columns = [dict(item) for item in real_inspector.get_columns(table_name)]
                for item in columns:
                    if item["name"] in control._REQUIRED_NULLABLE.get(table_name, ()):
                        item["nullable"] = False
                return columns

            def get_unique_constraints(self, table_name):
                return real_inspector.get_unique_constraints(table_name)

            def get_foreign_keys(self, table_name):
                return real_inspector.get_foreign_keys(table_name)

            def get_check_constraints(self, table_name):
                return real_inspector.get_check_constraints(table_name)

            def get_indexes(self, table_name):
                return real_inspector.get_indexes(table_name)

        monkeypatch.setattr(control, "inspect", lambda _connection: NonNullableInspector())
        with engine.connect() as connection:
            with pytest.raises(control.WorkspaceMigrationRequired) as error:
                control._ensure_0026(connection)
        assert any("non-nullable column" in item for item in error.value.missing)
        assert any("active_primary_slot" in item for item in error.value.missing)
        assert any("archived_at" in item for item in error.value.missing)
        assert any("removed_by" in item for item in error.value.missing)
    finally:
        control.inspect = original_inspect
        engine.dispose()


def test_description_and_casefolded_name_boundaries_fail_before_database_truncation() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        with pytest.raises(control.WorkspaceValidation):
            _create_workspace(engine, description="d" * 513)
        with pytest.raises(control.WorkspaceValidation):
            _create_workspace(engine, name="\u0390" * 86)
    finally:
        engine.dispose()


def test_same_idempotency_key_with_different_reason_conflicts_and_audits_reason() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        created = _create_workspace(
            engine, reason="创建原因 A", idempotency_key="reason-create-key"
        )
        workspace_id = created.body["workspace"]["id"]
        with pytest.raises(control.WorkspaceIdempotencyConflict):
            _create_workspace(
                engine,
                reason="创建原因 B",
                idempotency_key="reason-create-key",
            )

        control.update_workspace(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            expected_revision=1,
            name="Updated",
            reason="更新原因 A",
            idempotency_key="reason-update-key",
            request_id="reason-update-a",
            request_ip="127.0.0.1",
            now=NOW,
        )
        with pytest.raises(control.WorkspaceIdempotencyConflict):
            control.update_workspace(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=workspace_id,
                expected_revision=1,
                name="Updated",
                reason="更新原因 B",
                idempotency_key="reason-update-key",
                request_id="reason-update-b",
                request_ip="127.0.0.1",
                now=NOW,
            )

        control.add_workspace_member(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            account_id="editor-a",
            role="editor",
            reason="成员原因 A",
            idempotency_key="reason-member-key",
            request_id="reason-member-a",
            request_ip="127.0.0.1",
            now=NOW,
        )
        with pytest.raises(control.WorkspaceIdempotencyConflict):
            control.add_workspace_member(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=workspace_id,
                account_id="editor-a",
                role="editor",
                reason="成员原因 B",
                idempotency_key="reason-member-key",
                request_id="reason-member-b",
                request_ip="127.0.0.1",
                now=NOW,
            )

        control.bind_workspace_dataset(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            dataset_id="dataset-a",
            binding_kind="shared",
            reason="绑定原因 A",
            idempotency_key="reason-binding-key",
            request_id="reason-binding-a",
            request_ip="127.0.0.1",
            now=NOW,
        )
        with pytest.raises(control.WorkspaceIdempotencyConflict):
            control.bind_workspace_dataset(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=workspace_id,
                dataset_id="dataset-a",
                binding_kind="shared",
                reason="绑定原因 B",
                idempotency_key="reason-binding-key",
                request_id="reason-binding-b",
                request_ip="127.0.0.1",
                now=NOW,
            )

        audit_rows = _rows(
            engine,
            __import__("models.orm", fromlist=["TenantAuditEvent"]).TenantAuditEvent.__table__,
        )
        reasons = {
            str((row["after_snapshot"] or {}).get("reason"))
            for row in audit_rows
            if isinstance(row.get("after_snapshot"), dict)
        }
        assert {"创建原因 A", "更新原因 A", "成员原因 A", "绑定原因 A"} <= reasons
    finally:
        engine.dispose()


def test_list_filters_keep_count_and_cursor_within_the_same_filtered_set() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        testing_one = _create_workspace(
            engine, code="testing-one", name="Testing One", environment="testing"
        )
        _create_workspace(
            engine, code="production-one", name="Production One", environment="production"
        )
        _create_workspace(engine, code="testing-two", name="Testing Two", environment="testing")
        filtered = control.list_workspaces(
            engine,
            tenant_id=TENANT_A,
            actor_id="viewer-a",
            environment="testing",
            limit=1,
        )
        assert filtered.body["count"] == 2
        assert len(filtered.body["items"]) == 1
        assert filtered.body["next_cursor"]
        next_page = control.list_workspaces(
            engine,
            tenant_id=TENANT_A,
            actor_id="viewer-a",
            environment="testing",
            cursor=filtered.body["next_cursor"],
            limit=1,
        )
        assert next_page.body["count"] == 2
        assert next_page.body["items"][0]["environment"] == "testing"
        assert next_page.body["items"][0]["id"] != filtered.body["items"][0]["id"]

        member_result = control.add_workspace_member(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=testing_one.body["workspace"]["id"],
            account_id="editor-a",
            role="editor",
            reason="过滤成员",
            idempotency_key="filter-member",
            request_id="filter-member",
            request_ip="127.0.0.1",
            now=NOW,
        )
        members = control.list_workspace_members(
            engine,
            tenant_id=TENANT_A,
            actor_id="viewer-a",
            workspace_id=testing_one.body["workspace"]["id"],
            role="editor",
            limit=10,
        )
        assert members.body["count"] == 1
        assert members.body["items"][0]["id"] == member_result.body["member"]["id"]

        control.bind_workspace_dataset(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=testing_one.body["workspace"]["id"],
            dataset_id="dataset-a",
            binding_kind="shared",
            reason="过滤绑定",
            idempotency_key="filter-binding",
            request_id="filter-binding",
            request_ip="127.0.0.1",
            now=NOW,
        )
        bindings = control.list_workspace_datasets(
            engine,
            tenant_id=TENANT_A,
            actor_id="viewer-a",
            workspace_id=testing_one.body["workspace"]["id"],
            binding_kind="shared",
            limit=10,
        )
        assert bindings.body["count"] == 1
        assert bindings.body["items"][0]["binding_kind"] == "shared"
    finally:
        engine.dispose()


def test_64_character_tenant_deterministic_default_workspace_id_is_readable_and_writable() -> None:
    from core import enterprise_workspace_control as control
    from models.orm import TenantWorkspace

    engine = _engine()
    long_tenant = "t" * 64
    workspace_id = f"workspace-default-{long_tenant}"
    try:
        with Session(engine) as session:
            session.add(Tenant(id=long_tenant, name="Long Tenant", status="active"))
            session.add(Account(id="long-owner", name="Long Owner", email="long-owner@test"))
            session.flush()
            session.add(
                TenantMember(
                    account_id="long-owner",
                    tenant_id=long_tenant,
                    role="owner",
                    status="active",
                    revision=1,
                )
            )
            session.commit()
        with engine.begin() as connection:
            connection.execute(
                TenantWorkspace.__table__.insert().values(
                    id=workspace_id,
                    tenant_id=long_tenant,
                    code="default",
                    name="Long Default",
                    normalized_name="long default",
                    description="",
                    status="active",
                    environment="production",
                    is_default=True,
                    active_default_slot="default",
                    revision=1,
                    created_at=NOW,
                    created_by="long-owner",
                    updated_at=NOW,
                    updated_by="long-owner",
                )
            )
        listed = control.list_workspaces(
            engine,
            tenant_id=long_tenant,
            actor_id="long-owner",
            environment="production",
            limit=10,
        )
        assert workspace_id in {item["id"] for item in listed.body["items"]}
        updated = control.update_workspace(
            engine,
            tenant_id=long_tenant,
            actor_id="long-owner",
            workspace_id=workspace_id,
            expected_revision=1,
            name="Long Default Updated",
            reason="更新长 ID Workspace",
            idempotency_key="long-workspace-update",
            request_id="long-workspace-update",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert updated.body["workspace"]["id"] == workspace_id
        assert updated.body["workspace"]["name"] == "Long Default Updated"
    finally:
        engine.dispose()


def test_create_list_detail_and_workspace_authorization_boundary() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        created = _create_workspace(engine, is_default=True)
        assert created.status == 201
        workspace = created.body["workspace"]
        assert workspace["is_default"] is True
        assert "authorization" not in workspace

        members = control.list_workspace_members(
            engine, tenant_id=TENANT_A, actor_id="viewer-a", workspace_id=workspace["id"]
        )
        assert members.body["items"][0]["account_id"] == "owner-a"
        assert members.body["items"][0]["role"] == "owner"

        detail = control.get_workspace(
            engine, tenant_id=TENANT_A, actor_id="viewer-a", workspace_id=workspace["id"]
        )
        assert detail.body["workspace"]["id"] == workspace["id"]
        assert detail.body["workspace_authorization_not_enforced"] is True
        assert detail.body["members"][0]["account_id"] == "owner-a"
    finally:
        engine.dispose()


def test_create_is_idempotent_and_conflicts_on_reused_key() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        first = _create_workspace(engine, idempotency_key="workspace-create-replay")
        replay = _create_workspace(engine, idempotency_key="workspace-create-replay")
        assert replay.body == first.body
        assert replay.status == first.status
        assert len(_rows(engine, WORKSPACES)) == 1

        with pytest.raises(control.WorkspaceIdempotencyConflict):
            _create_workspace(
                engine,
                idempotency_key="workspace-create-replay",
                name="Different Name",
            )
    finally:
        engine.dispose()


def test_update_revision_fence_uniqueness_and_archive_default_protection() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        created = _create_workspace(engine, code="default", name="Default", is_default=True)
        workspace_id = created.body["workspace"]["id"]
        with pytest.raises(control.WorkspaceConflict) as error:
            control.update_workspace(
                engine,
                tenant_id=TENANT_A,
                actor_id="admin-a",
                workspace_id=workspace_id,
                expected_revision=99,
                name="Changed",
                code=None,
                description=None,
                environment=None,
                is_default=None,
                reason="更新 Workspace 测试",
                idempotency_key="workspace-update-stale",
                request_id="request-update-stale",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_revision_conflict"

        _create_workspace(engine, code="other", name="Other")
        with pytest.raises(control.WorkspaceConflict) as error:
            control.update_workspace(
                engine,
                tenant_id=TENANT_A,
                actor_id="admin-a",
                workspace_id=workspace_id,
                expected_revision=1,
                name="Other",
                code=None,
                description=None,
                environment=None,
                is_default=None,
                reason="更新 Workspace 测试",
                idempotency_key="workspace-update-duplicate-name",
                request_id="request-update-duplicate-name",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_name_conflict"

        archived = control.archive_workspace(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            expected_revision=1,
            reason="archive test",
            idempotency_key="workspace-archive-empty-default",
            request_id="request-archive-empty-default",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert archived.body["workspace"]["status"] == "archived"
        assert archived.body["workspace"]["is_default"] is False
    finally:
        engine.dispose()


def test_member_add_update_remove_and_last_owner_protection() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        workspace_id = _create_workspace(engine).body["workspace"]["id"]
        added = control.add_workspace_member(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            account_id="admin-a",
            role="admin",
            reason="加入 Workspace 测试",
            idempotency_key="workspace-member-add-admin",
            request_id="request-member-add-admin",
            request_ip="127.0.0.1",
            now=NOW,
        )
        member = added.body["member"]
        assert member["role"] == "admin"
        assert member["status"] == "active"

        updated = control.update_workspace_member(
            engine,
            tenant_id=TENANT_A,
            actor_id="admin-a",
            workspace_id=workspace_id,
            account_id="admin-a",
            expected_revision=1,
            role="editor",
            idempotency_key="workspace-member-update-admin",
            request_id="request-member-update-admin",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert updated.body["member"]["role"] == "editor"

        with pytest.raises(control.WorkspaceForbidden):
            control.remove_workspace_member(
                engine,
                tenant_id=TENANT_A,
                actor_id="admin-a",
                workspace_id=workspace_id,
                account_id="owner-a",
                expected_revision=1,
                reason="admin cannot remove owner",
                idempotency_key="workspace-member-remove-owner",
                request_id="request-member-remove-owner",
                request_ip="127.0.0.1",
                now=NOW,
            )

        with pytest.raises(control.WorkspaceConflict) as error:
            control.remove_workspace_member(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=workspace_id,
                account_id="owner-a",
                expected_revision=1,
                reason="last owner",
                idempotency_key="workspace-member-remove-last-owner",
                request_id="request-member-remove-last-owner",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_last_owner_protected"

        removed = control.remove_workspace_member(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            account_id="admin-a",
            expected_revision=2,
            reason="remove member",
            idempotency_key="workspace-member-remove-admin",
            request_id="request-member-remove-admin",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert removed.body["member"]["status"] == "removed"
    finally:
        engine.dispose()


def test_dataset_binding_primary_uniqueness_remove_and_archive_protection() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        first = _create_workspace(engine, code="first", name="First")
        second = _create_workspace(engine, code="second", name="Second")
        first_id = first.body["workspace"]["id"]
        second_id = second.body["workspace"]["id"]
        bound = control.bind_workspace_dataset(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=first_id,
            dataset_id="dataset-a",
            binding_kind="primary",
            reason="绑定主 Dataset 测试",
            idempotency_key="workspace-dataset-bind-first",
            request_id="request-dataset-bind-first",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert bound.body["binding"]["binding_kind"] == "primary"
        with pytest.raises(control.WorkspaceConflict) as error:
            control.bind_workspace_dataset(
                engine,
                tenant_id=TENANT_A,
                actor_id="admin-a",
                workspace_id=second_id,
                dataset_id="dataset-a",
                binding_kind="primary",
                reason="绑定主 Dataset 测试",
                idempotency_key="workspace-dataset-bind-second",
                request_id="request-dataset-bind-second",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_dataset_primary_conflict"

        with pytest.raises(control.WorkspaceConflict) as error:
            control.archive_workspace(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=first_id,
                expected_revision=1,
                reason="archive primary holder",
                idempotency_key="workspace-archive-primary-holder",
                request_id="request-archive-primary-holder",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_primary_dataset_protected"

        removed = control.remove_workspace_dataset(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=first_id,
            dataset_id="dataset-a",
            expected_revision=1,
            reason="move dataset",
            idempotency_key="workspace-dataset-remove-first",
            request_id="request-dataset-remove-first",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert removed.body["binding"]["status"] == "removed"
        archived = control.archive_workspace(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=first_id,
            expected_revision=1,
            reason="archive after detach",
            idempotency_key="workspace-archive-after-detach",
            request_id="request-archive-after-detach",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert archived.body["workspace"]["status"] == "archived"
    finally:
        engine.dispose()


def test_keyset_pagination_and_tenant_isolation() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        for index in range(3):
            _create_workspace(engine, code=f"page-{index}", name=f"Page {index}")
        first = control.list_workspaces(engine, tenant_id=TENANT_A, actor_id="viewer-a", limit=2)
        assert len(first.body["items"]) == 2
        assert first.body["next_cursor"]
        second = control.list_workspaces(
            engine,
            tenant_id=TENANT_A,
            actor_id="viewer-a",
            cursor=first.body["next_cursor"],
            limit=2,
        )
        assert {item["id"] for item in first.body["items"]}.isdisjoint(
            {item["id"] for item in second.body["items"]}
        )
        with pytest.raises(control.WorkspaceNotFound):
            control.get_workspace(
                engine,
                tenant_id=TENANT_B,
                actor_id="owner-b",
                workspace_id=first.body["items"][0]["id"],
            )
    finally:
        engine.dispose()


def test_audit_failure_rolls_back_workspace_mutation_and_ledger() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    original_audit = control._audit
    try:

        def fail_audit(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit unavailable")

        control._audit = fail_audit
        with pytest.raises(RuntimeError, match="audit unavailable"):
            _create_workspace(engine, idempotency_key="workspace-audit-rollback")
        assert _rows(engine, WORKSPACES) == []
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM tenant_control_mutation_requests")
                ).scalar_one()
                == 0
            )
    finally:
        control._audit = original_audit
        engine.dispose()


def test_workspace_member_manager_and_archived_protections() -> None:
    from core import enterprise_workspace_control as control

    engine = _engine()
    try:
        workspace_id = _create_workspace(engine).body["workspace"]["id"]
        with pytest.raises(control.WorkspaceForbidden):
            control.add_workspace_member(
                engine,
                tenant_id=TENANT_A,
                actor_id="viewer-a",
                workspace_id=workspace_id,
                account_id="editor-a",
                role="viewer",
                reason="加入 Workspace 测试",
                idempotency_key="workspace-member-viewer-add",
                request_id="request-workspace-member-viewer-add",
                request_ip="127.0.0.1",
                now=NOW,
            )
        archived = control.archive_workspace(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            workspace_id=workspace_id,
            expected_revision=1,
            reason="archive workspace",
            idempotency_key="workspace-archive-no-dataset",
            request_id="request-workspace-archive-no-dataset",
            request_ip="127.0.0.1",
            now=NOW,
        )
        assert archived.body["workspace"]["is_default"] is False
        with pytest.raises(control.WorkspaceConflict) as error:
            control.bind_workspace_dataset(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                workspace_id=workspace_id,
                dataset_id="dataset-a",
                binding_kind="shared",
                reason="绑定 Dataset 测试",
                idempotency_key="workspace-bind-archived",
                request_id="request-workspace-bind-archived",
                request_ip="127.0.0.1",
                now=NOW,
            )
        assert error.value.code == "workspace_archived"
    finally:
        engine.dispose()


__all__ = [name for name in globals() if name.startswith("test_")]
