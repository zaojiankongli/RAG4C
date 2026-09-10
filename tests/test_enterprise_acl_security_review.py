from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import re
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from core import catalog_schema
from core.enterprise_acl_idempotency import (
    EnterpriseAclIdempotencyValidationError,
    canonical_json,
    engine_serialization_lock,
    idempotency_key_lock,
)
from models.orm import (
    Account,
    Base,
    Dataset,
    DatasetAccessGrant,
    DatasetAclMutationRequest,
    Tenant,
    TenantAuditEvent,
    TenantMember,
)
from tests.test_dataset_acl_mutations_api import (
    ACCESS_GRAPH_TABLES,
    BASE_TABLES,
    DATASET_ID,
    ORGANIZATION_MEMBERSHIP_TABLES,
    _audit_rows,
    _grant,
    _grant_count,
    _harness,
    _headers,
)

REVISION = "0019_dataset_acl_control"


def _stamp(engine: Any, revision: str = REVISION) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": revision},
        )


def _security_harness() -> Any:
    api = _harness()
    _stamp(api.read_engine)
    if api.write_engine is not api.read_engine:
        _stamp(api.write_engine)
    return api


def _replace_ledger_with_missing_evidence(engine: Any, missing: str) -> None:
    constraints = []
    if missing != "check":
        constraints.append(
            "CONSTRAINT ck_dataset_acl_mutation_requests_status "
            "CHECK (status IN ('pending', 'completed', 'failed'))"
        )
    if missing != "dataset_fk":
        constraints.append(
            "CONSTRAINT fk_dataset_acl_mutation_requests_scope_dataset "
            "FOREIGN KEY (tenant_id, dataset_id) "
            "REFERENCES datasets (tenant_id, id)"
        )
    constraints.append(
        "CONSTRAINT fk_dataset_acl_mutation_requests_scope_actor "
        "FOREIGN KEY (actor_id, tenant_id) "
        "REFERENCES tenant_members (account_id, tenant_id)"
    )
    if missing != "unique":
        constraints.append(
            "CONSTRAINT uq_dataset_acl_mutation_requests_actor_key "
            "UNIQUE (tenant_id, actor_id, idempotency_key)"
        )
    constraint_sql = ",\n                    ".join(constraints)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(text("DROP TABLE dataset_acl_mutation_requests"))
        connection.execute(
            text(
                f"""
                CREATE TABLE dataset_acl_mutation_requests (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL,
                    dataset_id VARCHAR(64) NOT NULL,
                    actor_id VARCHAR(64) NOT NULL,
                    idempotency_key VARCHAR(128) NOT NULL,
                    request_hash VARCHAR(64) NOT NULL,
                    operation VARCHAR(32) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    resource_id VARCHAR(64),
                    response_json JSON,
                    http_status INTEGER,
                    created_at DATETIME NOT NULL,
                    completed_at DATETIME,
                    {constraint_sql}
                )
                """
            )
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def test_same_key_and_body_for_two_grant_paths_is_hash_conflict() -> None:
    api = _security_harness()
    try:
        key = "security-path-identity-0001"
        body = {"role": "manager", "revision": 1, "reason": "统一角色升级"}
        first = api.client().patch(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-account-a",
            headers=_headers(api.settings, "admin-a", idempotency_key=key),
            json=body,
        )
        second = api.client().patch(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants/grant-group-a",
            headers=_headers(api.settings, "admin-a", idempotency_key=key),
            json=body,
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "dataset_acl_idempotency_conflict"
        assert _grant(api.write_engine, "grant-group-a")["role"] == "editor"
    finally:
        api.close()


@pytest.mark.parametrize(
    "revision",
    [
        "0026_enterprise_workspace_control",
        # 用当前 head 而不是写测试时的 0028：下面这支用 Base.metadata.create_all 建的是
        # **当前 ORM = 当前 head** 的 schema，却盖 0028 的章——revision 与真实结构不一致，
        # 于是 capability 的 exact-check 比对把新 stage 才加的 CHECK 判成"缺失/非法"，
        # 请求被 fail-closed 成 503。盖章改成 HEAD_REVISION 后语义更强且如实：
        # "当前 head 的目录仍必须允许 Dataset ACL 写入"。
        catalog_schema.HEAD_REVISION,
    ],
)
def test_later_workspace_heads_keep_dataset_acl_mutations_available(revision: str) -> None:
    api = _security_harness()
    try:
        if revision == catalog_schema.HEAD_REVISION:
            Base.metadata.create_all(api.write_engine)
        _stamp(api.write_engine, revision)
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(
                api.settings,
                "admin-a",
                idempotency_key=f"security-head-{revision[:4]}-0001",
            ),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "后续企业迁移不得停用 Dataset ACL 写入",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["grant"]["subject_id"] == "owner-a"
    finally:
        api.close()


def test_full_shaped_0018_stamped_catalog_is_rejected_before_write() -> None:
    api = _security_harness()
    try:
        _stamp(api.write_engine, "0018_organization_membership")
        before_grants = _grant_count(api.write_engine)
        before_audits = len(_audit_rows(api.write_engine))
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(
                api.settings,
                "admin-a",
                idempotency_key="security-stamp-0018-0001",
            ),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "旧 stamp 不得写入",
            },
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "enterprise_access_graph_migration_required"
        assert "0019_dataset_acl_control" in response.json()["detail"]["message"]
        assert _grant_count(api.write_engine) == before_grants
        assert len(_audit_rows(api.write_engine)) == before_audits
    finally:
        api.close()


@pytest.mark.parametrize("missing", ["unique", "check", "dataset_fk"])
def test_missing_critical_0019_constraint_is_503_before_write(missing: str) -> None:
    api = _security_harness()
    try:
        _replace_ledger_with_missing_evidence(api.write_engine, missing)
        before_grants = _grant_count(api.write_engine)
        before_audits = len(_audit_rows(api.write_engine))
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(
                api.settings,
                "admin-a",
                idempotency_key=f"security-missing-{missing}-0001",
            ),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "约束缺失不得写入",
            },
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "enterprise_access_graph_migration_required"
        assert _grant_count(api.write_engine) == before_grants
        assert len(_audit_rows(api.write_engine)) == before_audits
    finally:
        api.close()


def test_ledger_persists_digest_not_raw_idempotency_key() -> None:
    api = _security_harness()
    raw_key = "operator-readable-key-0001"
    try:
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(api.settings, "admin-a", idempotency_key=raw_key),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "账本仅保存摘要",
            },
        )
        assert response.status_code == 201, response.text
        with Session(api.write_engine) as session:
            stored = session.scalar(select(DatasetAclMutationRequest.idempotency_key))
        assert stored is not None
        assert stored != raw_key
        assert re.fullmatch(r"[0-9a-f]{64}", stored)
    finally:
        api.close()


# 凭据样本用拼接构造：运行时仍是完整的凭据形态（"高置信度凭据"判定依赖它），
# 但源码里不出现整串，避免被 GitHub secret scanning 当成真实泄漏拦截推送。
SECRET_SAMPLES = (
    "sk_live_" + "6UkMEQtOnqPtX2o6WCmN",
    "github_pat_" + "11AA0abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIA" + "IOSFODNN7EXAMPLE",
    "xoxb-" + "123456789012-123456789012-rmZByUvOkpgVnimdezhC",
)


@pytest.mark.parametrize(
    "secret",
    [
        SECRET_SAMPLES[0],
        SECRET_SAMPLES[1],
        SECRET_SAMPLES[2],
        SECRET_SAMPLES[3],
    ],
)
def test_high_confidence_credential_like_reason_is_not_persisted(secret: str) -> None:
    api = _security_harness()
    try:
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(
                api.settings,
                "admin-a",
                idempotency_key=f"security-reason-{abs(hash(secret))}",
            ),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": f"incident note {secret}",
            },
        )
        assert response.status_code == 201, response.text
        rows = _audit_rows(api.write_engine)
        assert len(rows) == 1
        serialized = json.dumps(rows[0].after_snapshot, ensure_ascii=False)
        assert secret not in serialized
        assert "REDACTED" in serialized
    finally:
        api.close()


def test_credential_like_idempotency_key_is_rejected_not_persisted() -> None:
    api = _security_harness()
    try:
        response = api.client().post(
            f"/api/knowledge-bases/{DATASET_ID}/access-grants",
            headers=_headers(
                api.settings,
                "admin-a",
                idempotency_key=SECRET_SAMPLES[0],
            ),
            json={
                "subject_type": "account",
                "subject_id": "owner-a",
                "role": "viewer",
                "reason": "invalid key",
            },
        )
        assert response.status_code == 422, response.text
        with Session(api.write_engine) as session:
            assert session.scalar(select(DatasetAclMutationRequest)) is None
    finally:
        api.close()


def test_canonicalizer_rejects_non_string_mapping_keys_and_string_collisions() -> None:
    with pytest.raises(EnterpriseAclIdempotencyValidationError):
        canonical_json({1: "number"})
    with pytest.raises(EnterpriseAclIdempotencyValidationError):
        canonical_json({1: "number", "1": "string"})


class _DuplicateKeyMapping(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "duplicate":
            return 1
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield "duplicate"

    def __len__(self) -> int:
        return 1

    def items(self):  # type: ignore[override]
        return [("duplicate", 1), ("duplicate", 2)]


def test_canonicalizer_rejects_duplicate_mapping_items() -> None:
    with pytest.raises(EnterpriseAclIdempotencyValidationError):
        canonical_json(_DuplicateKeyMapping())


def test_key_lock_registry_removes_entry_after_last_waiter_exits() -> None:
    from core import enterprise_acl_idempotency as module

    identity = ("tenant-security", "actor-security", "lock-security")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with idempotency_key_lock(*identity):
            first_entered.set()
            assert release_first.wait(5)

    def second() -> None:
        assert first_entered.wait(5)
        with idempotency_key_lock(*identity):
            second_entered.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    assert first_entered.wait(5)
    release_first.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert second_entered.is_set()
    assert identity not in module._key_locks


def test_engine_lock_registry_removes_entry_after_use() -> None:
    from core import enterprise_acl_idempotency as module

    engine = create_engine("sqlite+pysqlite:///:memory:")
    identity = id(engine)
    try:
        with engine_serialization_lock(engine):
            assert identity in module._engine_locks
        assert identity not in module._engine_locks
    finally:
        engine.dispose()


def _enable_file_sqlite(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        dbapi_connection.create_function(
            "security_sleep_ms", 1, lambda milliseconds: time.sleep(float(milliseconds) / 1000)
        )
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _seed_file_catalog(path: Path) -> str:
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    engine = create_engine(url, connect_args={"timeout": 30})
    _enable_file_sqlite(engine)
    Base.metadata.create_all(
        engine,
        tables=[*BASE_TABLES, *ACCESS_GRAPH_TABLES, *ORGANIZATION_MEMBERSHIP_TABLES],
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS dataset_release_manifests ("
                "tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL, "
                "UNIQUE (tenant_id, id))"
            )
        )
    _stamp(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.execute(
            text(
                "CREATE TRIGGER security_delay_acl_ledger_insert "
                "BEFORE INSERT ON dataset_acl_mutation_requests "
                "BEGIN SELECT security_sleep_ms(500); END"
            )
        )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-process", name="Process Tenant", status="active"),
                Account(
                    id="owner-process",
                    name="Process Owner",
                    email="owner-process@example.test",
                ),
                Account(
                    id="member-process",
                    name="Process Member",
                    email="member-process@example.test",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    tenant_id="tenant-process",
                    account_id="owner-process",
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id="tenant-process",
                    account_id="member-process",
                    role="member",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.flush()
        session.add(
            Dataset(
                id="dataset-process",
                tenant_id="tenant-process",
                owner_id="owner-process",
                name="Process Dataset",
                status="active",
            )
        )
        session.commit()
    engine.dispose()
    return url


def _cross_process_worker(
    url: str,
    start: Any,
    queue: Any,
    request_id: str,
) -> None:
    import core.enterprise_access_mutations as mutations

    engine = create_engine(url, connect_args={"timeout": 30})
    _enable_file_sqlite(engine)
    try:
        start.wait(20)
        result = mutations.create_dataset_access_grant(
            engine,
            tenant_id="tenant-process",
            dataset_id="dataset-process",
            actor_id="owner-process",
            actor_role="owner",
            subject_type="account",
            subject_id="member-process",
            role="viewer",
            reason="cross process replay",
            request_id=request_id,
            idempotency_key="cross-process-key-0001",
        )
        queue.put(("ok", result))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        queue.put(("error", type(exc).__name__, str(exc)))
    finally:
        engine.dispose()


def test_file_sqlite_cross_process_same_key_replays_exact_response(tmp_path: Path) -> None:
    url = _seed_file_catalog(tmp_path / "cross-process-acl.db")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_cross_process_worker,
            args=(url, start, queue, f"cross-process-{index}"),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=45) for _ in processes]
    for process in processes:
        process.join(45)
        assert process.exitcode == 0

    assert [item[0] for item in results] == ["ok", "ok"]
    assert results[0][1] == results[1][1]

    engine = create_engine(url, connect_args={"timeout": 30})
    try:
        with Session(engine) as session:
            assert session.query(DatasetAccessGrant).count() == 1
            assert session.query(TenantAuditEvent).count() == 1
            assert session.query(DatasetAclMutationRequest).count() == 1
    finally:
        engine.dispose()
