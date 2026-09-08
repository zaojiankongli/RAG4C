"""Stage 7 RED contracts for Dataset ACL mode and idempotent mutations.

This module is intentionally test-only.  It specifies the 0019 boundary before
production code exists: Dataset ACL mode is durable, ACL mode changes are
serialized on the Dataset row, and every ACL grant mutation is replay-safe.

The tests use the existing Stage 6 FastAPI harness with signed Actor tokens and
short-lived temporary SQLite engines.  ``_install_stage7_schema`` only creates
the wished-for 0019 shape inside the temporary test database; it does not add
production models or migrations.  No real database URL, migration, backup, or
external service is used.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Session

from core.enterprise_access_control import evaluate_dataset_permissions
from core.knowledge_permissions import KNOWLEDGE_READ
from models.orm import (
    Account,
    Dataset,
    DatasetAccessGrant,
    DatasetAclMutationRequest,
    TenantAuditEvent,
    TenantMember,
)
from tests.test_dataset_acl_mutations_api import (
    BASE_TIME,
    DATASET_ID,
    _audit_rows,
    _grant_count,
    _harness,
    _headers,
)


ACL_MIGRATION_REQUIRED_CODE = "enterprise_access_graph_migration_required"
IDEMPOTENCY_CONFLICT_CODE = "dataset_acl_idempotency_conflict"
DISABLE_AUDIT_ACTION = "dataset_access_control.disabled"
LEDGER_TABLE = "dataset_acl_mutation_requests"
FORBIDDEN_LEDGER_TERMS = (
    "authorization",
    "bearer",
    "cookie",
    "raw_token",
    "token_hash",
    "access_token",
    "database_url",
    "mysql://",
)


def _install_stage7_schema(engine: Any) -> None:
    """Install/stamp the exact ORM-owned 0019 mutation contract in the test DB."""

    dataset_columns = {str(column["name"]) for column in inspect(engine).get_columns("datasets")}
    required_dataset_columns = {
        "acl_mode",
        "acl_revision",
        "acl_enabled_at",
        "acl_enabled_by",
    }
    assert required_dataset_columns <= dataset_columns
    DatasetAclMutationRequest.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('0019_dataset_acl_control')")
        )


def _stage7_harness(*, install_schema: bool = True) -> Any:
    harness = _harness()
    if install_schema:
        _install_stage7_schema(harness.read_engine)
    else:
        with harness.write_engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {LEDGER_TABLE}"))
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text(
                    "INSERT INTO alembic_version(version_num) "
                    "VALUES ('0018_organization_membership')"
                )
            )
    return harness


def _reset_dataset(engine: Any, *, acl_mode: str = "tenant_role", acl_revision: int = 1) -> None:
    """Make a deterministic no-grant or explicitly ACL-enabled fixture."""

    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM dataset_access_grants "
                "WHERE tenant_id = 'tenant-a' AND dataset_id = 'dataset-a'"
            )
        )
        connection.execute(text("DELETE FROM tenant_audit_events WHERE tenant_id = 'tenant-a'"))
        if "acl_mode" in {
            str(column["name"]) for column in inspect(connection).get_columns("datasets")
        }:
            connection.execute(
                text(
                    "UPDATE datasets SET acl_mode = :acl_mode, "
                    "acl_revision = :acl_revision "
                    "WHERE tenant_id = 'tenant-a' AND id = 'dataset-a'"
                ),
                {"acl_mode": acl_mode, "acl_revision": acl_revision},
            )


def _add_same_tenant_dataset(engine: Any, dataset_id: str = "dataset-c") -> None:
    with Session(engine) as session:
        if session.scalar(select(Dataset).where(Dataset.id == dataset_id)) is None:
            session.add(
                Dataset(
                    id=dataset_id,
                    tenant_id="tenant-a",
                    name="Second enterprise dataset",
                    description="Stage 7 actor-isolation fixture",
                    status="active",
                    owner_id="owner-a",
                    visibility="private",
                    created_at=BASE_TIME,
                )
            )
            session.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE datasets SET acl_mode = 'tenant_role', acl_revision = 1 "
                "WHERE tenant_id = 'tenant-a' AND id = :dataset_id"
            ),
            {"dataset_id": dataset_id},
        )


def _add_cross_tenant_owner(engine: Any) -> None:
    """Give tenant-b an owner and a shared subject so the body can be identical."""

    with Session(engine) as session:
        if session.scalar(select(Account).where(Account.id == "owner-b")) is None:
            session.add(
                Account(
                    id="owner-b",
                    name="Owner B",
                    email="owner-b@example.test",
                    created_at=BASE_TIME,
                )
            )
        if (
            session.scalar(
                select(TenantMember).where(
                    TenantMember.account_id == "owner-b",
                    TenantMember.tenant_id == "tenant-b",
                )
            )
            is None
        ):
            session.add(
                TenantMember(
                    account_id="owner-b",
                    tenant_id="tenant-b",
                    role="owner",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                )
            )
        if (
            session.scalar(
                select(TenantMember).where(
                    TenantMember.account_id == "owner-a",
                    TenantMember.tenant_id == "tenant-b",
                )
            )
            is None
        ):
            session.add(
                TenantMember(
                    account_id="owner-a",
                    tenant_id="tenant-b",
                    role="member",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                )
            )
        session.flush()
        session.execute(
            text(
                "UPDATE datasets SET owner_id = 'owner-b' "
                "WHERE tenant_id = 'tenant-b' AND id = 'dataset-b'"
            )
        )
        session.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE datasets SET acl_mode = 'tenant_role', acl_revision = 1 "
                "WHERE tenant_id = 'tenant-b' AND id = 'dataset-b'"
            )
        )


def _headers_with_key(
    settings: Any,
    actor_id: str,
    *,
    tenant_id: str = "tenant-a",
    key: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    headers = _headers(
        settings,
        actor_id,
        tenant_id,
        request_id=request_id,
    )
    if key is not None:
        headers["Idempotency-Key"] = key
    else:
        headers.pop("Idempotency-Key", None)
    return headers


def _dataset_state(
    engine: Any, *, tenant_id: str = "tenant-a", dataset_id: str = DATASET_ID
) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT acl_mode, acl_revision FROM datasets "
                    "WHERE tenant_id = :tenant_id AND id = :dataset_id"
                ),
                {"tenant_id": tenant_id, "dataset_id": dataset_id},
            )
            .mappings()
            .one()
        )
        return dict(row)


def _grant_count_for(engine: Any, *, tenant_id: str, dataset_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset_access_grants "
                    "WHERE tenant_id = :tenant_id AND dataset_id = :dataset_id"
                ),
                {"tenant_id": tenant_id, "dataset_id": dataset_id},
            ).scalar_one()
        )


def _audit_count_for(engine: Any, *, tenant_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT COUNT(*) FROM tenant_audit_events WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            ).scalar_one()
        )


def _ledger_columns(engine: Any) -> set[str]:
    if LEDGER_TABLE not in inspect(engine).get_table_names():
        return set()
    return {str(column["name"]) for column in inspect(engine).get_columns(LEDGER_TABLE)}


def _ledger_rows(engine: Any, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
    if LEDGER_TABLE not in inspect(engine).get_table_names():
        return []
    predicate = "WHERE tenant_id = :tenant_id" if tenant_id is not None else ""
    parameters = {"tenant_id": tenant_id} if tenant_id is not None else {}
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT id, tenant_id, actor_id, dataset_id, operation, "
                "idempotency_key, request_hash, status, resource_id, "
                "response_json, http_status, created_at, completed_at "
                f"FROM {LEDGER_TABLE} {predicate} "
                "ORDER BY created_at, id"
            ),
            parameters,
        )
        return [dict(row) for row in result.mappings()]


def _assert_error(response: Any, status_code: int, code: str | None = None) -> None:
    assert response.status_code == status_code, response.text
    if code is not None:
        payload = response.json()
        assert payload["detail"]["code"] == code, response.text


def _create_body(
    *, subject_id: str = "owner-a", role: str = "viewer", reason: str = "阶段七授权"
) -> dict[str, str]:
    return {
        "subject_type": "account",
        "subject_id": subject_id,
        "role": role,
        "reason": reason,
    }


def _mutation_request(
    client: Any,
    operation: str,
    *,
    headers: dict[str, str],
    dataset_id: str = DATASET_ID,
) -> Any:
    if operation == "create":
        return client.post(
            f"/api/knowledge-bases/{dataset_id}/access-grants",
            headers=headers,
            json=_create_body(),
        )
    if operation == "role":
        return client.patch(
            f"/api/knowledge-bases/{dataset_id}/access-grants/grant-account-a",
            headers=headers,
            json={"role": "editor", "revision": 1, "reason": "阶段七变更"},
        )
    if operation == "revoke":
        return client.post(
            f"/api/knowledge-bases/{dataset_id}/access-grants/grant-account-a/revoke",
            headers=headers,
            json={"revision": 1, "reason": "阶段七撤销"},
        )
    if operation == "resume":
        return client.post(
            f"/api/knowledge-bases/{dataset_id}/access-grants/grant-revoked-a/resume",
            headers=headers,
            json={"revision": 4, "reason": "阶段七恢复"},
        )
    raise AssertionError(f"unknown mutation operation: {operation}")


def test_first_grant_create_locks_dataset_and_enables_acl_with_revision() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        client = api.client()
        before = _dataset_state(api.write_engine)
        statements: list[str] = []

        def capture(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            normalized = " ".join(statement.lower().split())
            if "datasets" in normalized or "dataset_access_grants" in normalized:
                statements.append(normalized)

        event.listen(api.write_engine, "before_cursor_execute", capture)
        try:
            response = client.post(
                f"/api/knowledge-bases/{DATASET_ID}/access-grants",
                headers=_headers_with_key(
                    api.settings,
                    "admin-a",
                    key="stage7-first-grant-0001",
                ),
                json=_create_body(),
            )
        finally:
            event.remove(api.write_engine, "before_cursor_execute", capture)

        assert response.status_code == 201, response.text
        after = _dataset_state(api.write_engine)
        assert after["acl_mode"] == "dataset_acl"
        assert after["acl_revision"] == before["acl_revision"] + 1
        dataset_update = next(
            index
            for index, statement in enumerate(statements)
            if "update datasets" in statement and "acl_mode" in statement
        )
        grant_insert = next(
            index
            for index, statement in enumerate(statements)
            if "insert into dataset_access_grants" in statement
        )
        assert dataset_update < grant_insert
    finally:
        api.close()


def test_last_active_grant_revoke_keeps_acl_mode_and_denies_unmatched_member() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine, acl_mode="dataset_acl", acl_revision=1)
        with Session(api.write_engine) as session:
            session.add(
                DatasetAccessGrant(
                    id="stage7-last-grant",
                    tenant_id="tenant-a",
                    dataset_id=DATASET_ID,
                    subject_type="account",
                    subject_id="member-a",
                    role="viewer",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                )
            )
            session.commit()

        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants/stage7-last-grant/revoke",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key="stage7-last-revoke-0001",
            ),
            json={"revision": 1, "reason": "撤销最后一条授权"},
        )

        assert response.status_code == 200, response.text
        state = _dataset_state(api.write_engine)
        assert state["acl_mode"] == "dataset_acl"
        assert state["acl_revision"] == 1
        decision = evaluate_dataset_permissions(
            api.write_engine,
            "tenant-a",
            "member-a",
            "member",
            DATASET_ID,
        )
        assert decision.enforcement_mode == "dataset_acl"
        assert not decision.allows(KNOWLEDGE_READ)
    finally:
        api.close()


def test_disable_acl_requires_tenant_owner_or_admin_not_dataset_manager() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine, acl_mode="dataset_acl", acl_revision=4)
        with Session(api.write_engine) as session:
            session.add(
                DatasetAccessGrant(
                    id="stage7-manager-only",
                    tenant_id="tenant-a",
                    dataset_id=DATASET_ID,
                    subject_type="account",
                    subject_id="editor-a",
                    role="manager",
                    status="active",
                    revision=1,
                    created_at=BASE_TIME,
                )
            )
            session.commit()
        before_state = _dataset_state(api.write_engine)
        before_audits = _audit_count_for(api.write_engine, tenant_id="tenant-a")

        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers_with_key(
                api.settings,
                "editor-a",
                key="stage7-disable-editor-0001",
            ),
            json={
                "expected_acl_revision": 4,
                "reason": "尝试由知识库管理员关闭 ACL",
            },
        )

        _assert_error(response, 403)
        assert _dataset_state(api.write_engine) == before_state
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == before_audits
    finally:
        api.close()


@pytest.mark.parametrize("actor_id", ["owner-a", "admin-a"])
def test_disable_acl_is_explicit_owner_admin_transition_and_audited(actor_id: str) -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine, acl_mode="dataset_acl", acl_revision=4)
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-control/disable",
            headers=_headers_with_key(
                api.settings,
                actor_id,
                key=f"stage7-disable-{actor_id}-0001",
            ),
            json={
                "expected_acl_revision": 4,
                "reason": "完成 ACL 迁移回退评审",
            },
        )

        assert response.status_code == 200, response.text
        assert _dataset_state(api.write_engine) == {
            "acl_mode": "tenant_role",
            "acl_revision": 5,
        }
        rows = _audit_rows(api.write_engine)
        assert len(rows) == 1
        assert rows[0].action == DISABLE_AUDIT_ACTION
        after = rows[0].after_snapshot
        if isinstance(after, str):
            after = json.loads(after)
        assert after["acl_mode"] == "tenant_role"
        assert after["acl_revision"] == 5
        assert after["reason"] == "完成 ACL 迁移回退评审"
    finally:
        api.close()


@pytest.mark.parametrize("operation", ["create", "role", "revoke", "resume"])
@pytest.mark.parametrize(
    "invalid_key",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("x" * 129, id="too-long"),
    ],
)
def test_every_grant_mutation_requires_idempotency_key_1_to_128(
    operation: str,
    invalid_key: str | None,
) -> None:
    api = _stage7_harness()
    try:
        before_grants = _grant_count(api.write_engine)
        before_audits = _audit_count_for(api.write_engine, tenant_id="tenant-a")
        headers = _headers_with_key(
            api.settings,
            "admin-a",
            key=invalid_key,
        )
        response = _mutation_request(api.client(), operation, headers=headers)

        _assert_error(response, 422)
        assert _grant_count(api.write_engine) == before_grants
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == before_audits
    finally:
        api.close()


def test_same_tenant_actor_key_and_hash_replays_exact_response_without_new_rows() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        client = api.client()
        body = _create_body(subject_id="owner-a", role="viewer", reason="幂等创建")
        key = "stage7-replay-same-body-0001"
        first = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key=key,
                request_id="stage7-replay-first",
            ),
            json=body,
        )
        grant_count_after_first = _grant_count(api.write_engine)
        audit_count_after_first = _audit_count_for(api.write_engine, tenant_id="tenant-a")
        ledger_count_after_first = len(_ledger_rows(api.write_engine, tenant_id="tenant-a"))

        second = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key=key,
                request_id="stage7-replay-retry",
            ),
            json=body,
        )

        assert first.status_code == 201, first.text
        assert second.status_code == first.status_code, second.text
        assert second.json() == first.json()
        assert _grant_count(api.write_engine) == grant_count_after_first
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == audit_count_after_first
        ledger_rows = _ledger_rows(api.write_engine, tenant_id="tenant-a")
        assert len(ledger_rows) == ledger_count_after_first == 1
        assert ledger_rows[0]["status"] == "completed"
    finally:
        api.close()


def test_same_key_with_different_request_hash_is_409_without_second_mutation() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        client = api.client()
        key = "stage7-replay-conflict-0001"
        first = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(api.settings, "admin-a", key=key),
            json=_create_body(role="viewer", reason="第一份请求"),
        )
        assert first.status_code == 201, first.text
        grant_count = _grant_count(api.write_engine)
        audit_count = _audit_count_for(api.write_engine, tenant_id="tenant-a")

        conflict = client.post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(api.settings, "admin-a", key=key),
            json=_create_body(role="editor", reason="不同请求体"),
        )

        _assert_error(conflict, 409, IDEMPOTENCY_CONFLICT_CODE)
        assert _grant_count(api.write_engine) == grant_count
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == audit_count
        assert len(_ledger_rows(api.write_engine, tenant_id="tenant-a")) == 1
    finally:
        api.close()


def test_same_key_isolated_by_actor_within_one_tenant() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        _add_same_tenant_dataset(api.write_engine)
        body = _create_body(subject_id="editor-a", role="viewer", reason="操作者隔离")
        key = "stage7-actor-scope-0001"
        first = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(api.settings, "admin-a", key=key),
            json=body,
        )
        second = api.client().post(
            "/api/knowledge-bases/dataset-c/access-grants",
            headers=_headers_with_key(api.settings, "owner-a", key=key),
            json=body,
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json() != second.json()
        assert (
            _grant_count_for(
                api.write_engine,
                tenant_id="tenant-a",
                dataset_id=DATASET_ID,
            )
            == 1
        )
        assert (
            _grant_count_for(
                api.write_engine,
                tenant_id="tenant-a",
                dataset_id="dataset-c",
            )
            == 1
        )
        assert len(_ledger_rows(api.write_engine, tenant_id="tenant-a")) == 2
    finally:
        api.close()


def test_same_key_isolated_by_tenant() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        _add_cross_tenant_owner(api.write_engine)
        body = _create_body(subject_id="owner-a", role="viewer", reason="租户隔离")
        key = "stage7-tenant-scope-0001"
        first = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(api.settings, "admin-a", key=key),
            json=body,
        )
        second = api.client().post(
            "/api/knowledge-bases/dataset-b/access-grants",
            headers=_headers_with_key(
                api.settings,
                "owner-b",
                tenant_id="tenant-b",
                key=key,
            ),
            json=body,
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert (
            _grant_count_for(
                api.write_engine,
                tenant_id="tenant-a",
                dataset_id=DATASET_ID,
            )
            == 1
        )
        assert (
            _grant_count_for(
                api.write_engine,
                tenant_id="tenant-b",
                dataset_id="dataset-b",
            )
            == 1
        )
        assert len(_ledger_rows(api.write_engine, tenant_id="tenant-a")) == 1
        assert len(_ledger_rows(api.write_engine, tenant_id="tenant-b")) == 1
    finally:
        api.close()


def test_concurrent_same_key_performs_one_mutation_and_replays_one_result() -> None:
    api = _stage7_harness()
    clients: list[Any] = []
    try:
        _reset_dataset(api.write_engine)
        clients = [api.client(), api.client()]
        body = _create_body(subject_id="owner-a", role="viewer", reason="并发幂等")
        key = "stage7-concurrent-key-0001"
        barrier = threading.Barrier(2)

        def send(client: Any, request_id: str) -> Any:
            barrier.wait(timeout=10)
            return client.post(
                f"/api/knowledge-bases/{DATASET_ID}/access-grants",
                headers=_headers_with_key(
                    api.settings,
                    "admin-a",
                    key=key,
                    request_id=request_id,
                ),
                json=body,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(send, clients[0], "stage7-concurrent-a"),
                pool.submit(send, clients[1], "stage7-concurrent-b"),
            ]
            results = [future.result(timeout=30) for future in futures]

        assert results[0].status_code == 201, results[0].text
        assert results[1].status_code == results[0].status_code, results[1].text
        assert results[1].json() == results[0].json()
        assert _grant_count(api.write_engine) == 1
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == 1
        completed = [
            row
            for row in _ledger_rows(api.write_engine, tenant_id="tenant-a")
            if row["status"] == "completed"
        ]
        assert len(completed) == 1
    finally:
        for client in clients:
            client.close()
        api.close()


def test_failed_transaction_does_not_leave_completed_idempotency_record() -> None:
    api = _stage7_harness()

    def reject_audit(_mapper: Any, _connection: Any, _target: TenantAuditEvent) -> None:
        raise RuntimeError("stage7 audit sink intentionally unavailable")

    event.listen(TenantAuditEvent, "before_insert", reject_audit)
    try:
        _reset_dataset(api.write_engine)
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key="stage7-rollback-0001",
            ),
            json=_create_body(reason="故意触发审计失败"),
        )
    finally:
        event.remove(TenantAuditEvent, "before_insert", reject_audit)

    try:
        assert response.status_code == 503, response.text
        assert _grant_count(api.write_engine) == 0
        assert _audit_rows(api.write_engine) == []
        assert not any(
            row["status"] == "completed"
            for row in _ledger_rows(api.write_engine, tenant_id="tenant-a")
        )
    finally:
        api.close()


def test_idempotency_ledger_never_stores_authorization_or_token_material() -> None:
    api = _stage7_harness()
    try:
        _reset_dataset(api.write_engine)
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key="stage7-ledger-safety-0001",
            ),
            json=_create_body(reason="账本不应保存凭据"),
        )
        assert response.status_code == 201, response.text

        columns = _ledger_columns(api.write_engine)
        assert columns
        assert not any(
            forbidden in column.casefold()
            for column in columns
            for forbidden in FORBIDDEN_LEDGER_TERMS
        )
        ledger_rows = _ledger_rows(api.write_engine)
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["status"] == "completed"
        serialized = json.dumps(ledger_rows, ensure_ascii=False)
        assert not any(term in serialized.casefold() for term in FORBIDDEN_LEDGER_TERMS)
        assert "Authorization" not in serialized
        assert "Bearer" not in serialized
    finally:
        api.close()


def test_missing_0019_returns_503_before_any_acl_write() -> None:
    api = _stage7_harness(install_schema=False)
    try:
        before_grants = _grant_count(api.write_engine)
        before_audits = _audit_count_for(api.write_engine, tenant_id="tenant-a")
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers_with_key(
                api.settings,
                "admin-a",
                key="stage7-migration-required-0001",
            ),
            json=_create_body(),
        )

        _assert_error(response, 503, ACL_MIGRATION_REQUIRED_CODE)
        assert _grant_count(api.write_engine) == before_grants
        assert _audit_count_for(api.write_engine, tenant_id="tenant-a") == before_audits
    finally:
        api.close()


__all__ = [
    "test_first_grant_create_locks_dataset_and_enables_acl_with_revision",
    "test_last_active_grant_revoke_keeps_acl_mode_and_denies_unmatched_member",
]
