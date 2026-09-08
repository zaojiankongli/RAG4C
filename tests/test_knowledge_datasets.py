from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext


def _audit(actor: str = "owner-1", request: str = "request-1") -> AuditContext:
    return AuditContext(actor_id=actor, request_id=request, request_ip="127.0.0.1")


def _repository(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog
    from core.knowledge_datasets import KnowledgeDatasetRepository
    from models.orm import Account, Dataset, Tenant, TenantMember

    url = f"sqlite:///{(tmp_path / 'knowledge-datasets.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Keep this repository suite on the complete pre-0028 Dataset lifecycle.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE app_dataset_references"))
        connection.execute(text("DROP TABLE dataset_workspace_ownerships"))
        connection.execute(
            text("UPDATE alembic_version SET version_num='0027_enterprise_workspace_authorization'")
        )

    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant 1"),
                Tenant(id="tenant-2", name="Tenant 2"),
                Account(id="owner-1", name="Owner 1", email="owner1@example.com"),
                Account(id="editor-1", name="Editor 1", email="editor1@example.com"),
                Account(id="owner-2", name="Owner 2", email="owner2@example.com"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(account_id="owner-1", tenant_id="tenant-1", role="owner"),
                TenantMember(account_id="editor-1", tenant_id="tenant-1", role="editor"),
                TenantMember(account_id="owner-2", tenant_id="tenant-2", role="owner"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="Knowledge 1"),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="Knowledge 2"),
                Dataset(id="dataset-3", tenant_id="tenant-2", name="Knowledge 3"),
            ]
        )
        session.commit()
    return engine, KnowledgeDatasetRepository(engine)


def test_get_list_and_update_profile_are_scoped_revisioned_and_audited(tmp_path: Path) -> None:
    from core.knowledge_datasets import DatasetProfileNotFound
    from models.orm import KnowledgeAuditEvent

    engine, repository = _repository(tmp_path)
    initial = repository.get_profile("tenant-1", "dataset-1")
    assert initial.profile_revision == 1
    assert initial.visibility == "private"
    assert initial.profile_json == {}
    assert initial.parser_policy == {}
    assert initial.graph_enabled is False
    assert initial.qa_enabled is True
    assert initial.status == "active"

    updated = repository.update_profile(
        "tenant-1",
        "dataset-1",
        expected_revision=1,
        audit=_audit(),
        name="Enterprise Knowledge",
        description="Authoritative knowledge profile",
        owner_id="owner-1",
        visibility="tenant",
        profile_json={"domain": "support", "labels": ["trusted"]},
        parser_policy={"engine": "mineru", "ocr": True},
        chunk_policy={"mode": "parent_child", "size": 800},
        retrieval_policy={"top_k": 12, "rerank": True},
        retention_policy={"days": 365},
        metadata_policy={"required": ["department"]},
        default_language="zh-CN",
        graph_enabled=True,
        qa_enabled=False,
    )

    assert updated.profile_revision == 2
    assert updated.owner_id == "owner-1"
    assert updated.visibility == "tenant"
    assert updated.parser_policy == {"engine": "mineru", "ocr": True}
    assert updated.graph_enabled is True
    assert updated.qa_enabled is False
    assert updated.updated_at >= updated.created_at
    assert [item.id for item in repository.list_profiles("tenant-1")] == [
        "dataset-1",
        "dataset-2",
    ]
    with pytest.raises(DatasetProfileNotFound):
        repository.get_profile("tenant-1", "dataset-3")

    with Session(engine) as session:
        event_row = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.dataset_id == "dataset-1",
                KnowledgeAuditEvent.action == "dataset.profile.update",
            )
        )
        assert event_row is not None
        assert event_row.actor_id == "owner-1"
        assert event_row.before_snapshot["profile_revision"] == 1
        assert event_row.after_snapshot["profile_revision"] == 2
        assert event_row.after_snapshot["owner_id"] == "owner-1"
    engine.dispose()


def test_owner_must_be_a_member_of_the_same_tenant_in_repository_and_database(
    tmp_path: Path,
) -> None:
    from core.knowledge_datasets import DatasetProfileConflict

    engine, repository = _repository(tmp_path)
    with pytest.raises(DatasetProfileConflict, match="same tenant"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            owner_id="owner-2",
        )

    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(text("UPDATE datasets SET owner_id='owner-2' WHERE id='dataset-1'"))
            session.commit()
        session.rollback()
    assert repository.get_profile("tenant-1", "dataset-1").owner_id is None
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("profile_json", ["not", "an", "object"], "JSON object"),
        ("parser_policy", {"auth": {"api_key": "leak"}}, "secret-like"),
        ("chunk_policy", {"nested": [{"accessToken": "leak"}]}, "secret-like"),
        ("retrieval_policy", {"password": "leak"}, "secret-like"),
        ("retention_policy", {"client-secret": "leak"}, "secret-like"),
        ("metadata_policy", {"SECRET": "leak"}, "secret-like"),
    ],
)
def test_profile_json_fields_require_objects_and_reject_recursive_secret_keys(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match=message):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            **{field: value},
        )
    assert repository.get_profile("tenant-1", "dataset-1").profile_revision == 1
    engine.dispose()


def test_profile_updates_use_atomic_revision_cas_across_threads(tmp_path: Path) -> None:
    from core.knowledge_datasets import DatasetProfileConflict, KnowledgeDatasetRepository
    from models.orm import KnowledgeAuditEvent

    engine, _repository_instance = _repository(tmp_path)
    barrier = Barrier(2)

    def update_name(name: str) -> tuple[str, int | str]:
        repository = KnowledgeDatasetRepository(engine)
        barrier.wait(timeout=5)
        try:
            profile = repository.update_profile(
                "tenant-1",
                "dataset-1",
                expected_revision=1,
                audit=_audit(actor="owner-1", request=f"request-{name}"),
                name=name,
            )
            return ("ok", profile.profile_revision)
        except DatasetProfileConflict as exc:
            return ("conflict", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update_name, ["first", "second"]))

    assert [kind for kind, _ in outcomes].count("ok") == 1
    assert [kind for kind, _ in outcomes].count("conflict") == 1
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action == "dataset.profile.update")
            )
            == 1
        )
    engine.dispose()


def test_dataset_status_state_machine_is_revisioned_terminal_and_audited(tmp_path: Path) -> None:
    from core.knowledge_datasets import DatasetProfileConflict
    from models.orm import KnowledgeAuditEvent

    engine, repository = _repository(tmp_path)
    archived = repository.archive("tenant-1", "dataset-1", expected_revision=1, audit=_audit())
    assert archived.status == "archived"
    assert archived.profile_revision == 2
    assert archived.archived_at is not None
    assert archived.archived_by == "owner-1"

    restored = repository.restore(
        "tenant-1", "dataset-1", expected_revision=2, audit=_audit(request="restore")
    )
    assert restored.status == "active"
    assert restored.profile_revision == 3
    assert restored.archived_at is None
    assert restored.archived_by is None

    disabled = repository.disable(
        "tenant-1", "dataset-1", expected_revision=3, audit=_audit(request="disable")
    )
    assert disabled.status == "disabled"
    assert disabled.profile_revision == 4

    with pytest.raises(DatasetProfileConflict, match="archived"):
        repository.restore(
            "tenant-1", "dataset-1", expected_revision=4, audit=_audit(request="invalid")
        )
    with pytest.raises(DatasetProfileConflict, match="active"):
        repository.archive(
            "tenant-1", "dataset-1", expected_revision=4, audit=_audit(request="invalid-2")
        )

    with Session(engine) as session:
        actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action)
                .where(KnowledgeAuditEvent.dataset_id == "dataset-1")
                .order_by(KnowledgeAuditEvent.sequence)
            )
        )
    assert actions == ["dataset.archive", "dataset.restore", "dataset.disable"]
    engine.dispose()


def test_audit_failure_rolls_back_profile_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models.orm import KnowledgeAuditEvent

    engine, repository = _repository(tmp_path)

    def fail_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(repository, "_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            visibility="public",
        )

    current = repository.get_profile("tenant-1", "dataset-1")
    assert current.profile_revision == 1
    assert current.visibility == "private"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
    engine.dispose()


def test_mutations_require_a_valid_audit_context(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="AuditContext"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=None,  # type: ignore[arg-type]
            visibility="tenant",
        )
    current = repository.get_profile("tenant-1", "dataset-1")
    assert current.profile_revision == 1
    assert current.visibility == "private"
    engine.dispose()


def test_policy_json_normalizes_keys_and_allows_safe_token_fields_and_secret_refs(
    tmp_path: Path,
) -> None:
    engine, repository = _repository(tmp_path)
    updated = repository.update_profile(
        "tenant-1",
        "dataset-1",
        expected_revision=1,
        audit=_audit(),
        retrieval_policy={
            "Max Tokens": 128,
            "Tokenizer": "bge-m3",
            "TOKEN-budget": 4096,
            "token_count": 12,
            "CredentialRef": "secret://rag4c/retrieval/main",
            "Nested Policy": [{"Display Name": "safe"}],
        },
    )

    assert updated.retrieval_policy == {
        "max_tokens": 128,
        "tokenizer": "bge-m3",
        "token_budget": 4096,
        "token_count": 12,
        "credential_ref": "secret://rag4c/retrieval/main",
        "nested_policy": [{"display_name": "safe"}],
    }
    engine.dispose()


@pytest.mark.parametrize(
    "invalid_value",
    [
        {"nested": ("tuple",)},
        {"nested": {"set"}},
        {"nested": object()},
        {1: "non-string key"},
        {"number": float("nan")},
        {"number": float("inf")},
    ],
)
def test_policy_json_rejects_non_json_tree_values(
    tmp_path: Path,
    invalid_value: object,
) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="JSON"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            parser_policy=invalid_value,  # type: ignore[arg-type]
        )
    assert repository.get_profile("tenant-1", "dataset-1").profile_revision == 1
    engine.dispose()


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "api-key",
        "AccessToken",
        "refresh token",
        "PASSWORD",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "clientSecret",
        "private-key",
        "bearer",
        "cookie",
        "session id",
        "sessionKey",
        "access-key",
        "signing key",
        "encryptionKey",
        "credential",
        "pa\u0301ssword",
        "secr\u0435t",
    ],
)
def test_policy_json_rejects_exact_normalized_secret_keys(
    tmp_path: Path,
    sensitive_key: str,
) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="secret-like"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            metadata_policy={sensitive_key: "leak"},
        )
    engine.dispose()


@pytest.mark.parametrize(
    "reference",
    [
        "https://vault.example/secret",
        "secret://user:password@vault/path",
        "vault://user@vault/path",
        "secret://",
        ("secret://vault/path",),
    ],
)
def test_credential_ref_requires_safe_secret_or_vault_uri(
    tmp_path: Path,
    reference: object,
) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="credential_ref"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            parser_policy={"credential_ref": reference},
        )
    engine.dispose()


def test_policy_key_normalization_rejects_collisions(tmp_path: Path) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="duplicate normalized key"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=1,
            audit=_audit(),
            chunk_policy={"Chunk Size": 800, "chunk_size": 1000},
        )
    engine.dispose()


@pytest.mark.parametrize("expected_revision", [True, "1", 1.0, 0, -1, None])
def test_expected_revision_must_be_an_exact_positive_integer(
    tmp_path: Path,
    expected_revision: object,
) -> None:
    engine, repository = _repository(tmp_path)
    with pytest.raises(ValueError, match="exact positive integer"):
        repository.update_profile(
            "tenant-1",
            "dataset-1",
            expected_revision=expected_revision,  # type: ignore[arg-type]
            audit=_audit(),
            visibility="tenant",
        )
    assert repository.get_profile("tenant-1", "dataset-1").profile_revision == 1
    engine.dispose()
