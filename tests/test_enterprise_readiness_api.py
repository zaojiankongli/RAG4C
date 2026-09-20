from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.catalog_schema as catalog_schema_manifest
from core.catalog_schema import CatalogSchemaState, HEAD_REVISION


ALL_CAPABILITY_GROUPS = [
    "catalog_core",
    "identity_integrity",
    "ingestion_audit",
    "durable_index_operations",
    "source_sync_ledger",
    "source_identity",
    "immutable_chunk_authority",
    "knowledge_governance",
    "knowledge_content",
    "dataset_profile",
    "durable_document_deletion",
    "retrieval_quality",
    "source_control",
    "source_scheduling",
    "document_catalog_indexes",
    "enterprise_membership",
    "enterprise_access_graph",
    "organization_membership",
    "dataset_acl_control",
    "tenant_invitation_lifecycle",
    "enterprise_identity_federation",
    "scim_provisioning_data_plane",
    "enterprise_audit_compliance",
    "oidc_sso_runtime",
    "enterprise_approval_control",
    "enterprise_workspace_control",
    "enterprise_workspace_authorization",
    "enterprise_knowledge_base_registry",
    "enterprise_knowledge_base_releases",
    "enterprise_release_quality_certification",
    "enterprise_release_quality_operations",
    "enterprise_notification_center",
    "enterprise_content_recovery",
    "enterprise_task_operations",
    "enterprise_automation_workflows",
    "enterprise_knowledge_serving_reliability",
    "enterprise_knowledge_operations_feedback",
    "enterprise_qa_faq_operations",
    "enterprise_storage_backends",
    "enterprise_answer_evidence_facts",
]

ENTERPRISE_MEMBERSHIP_ISSUE_FRAGMENTS = [
    "tenant_members.status",
    "tenant_members.revision",
    "tenant_members.updated_at",
    "tenant_members.updated_by",
    "tenant_members.suspended_at",
    "tenant_members.suspended_by",
    "tenant_members.ck_tenant_members_role",
    "tenant_members.ck_tenant_members_status",
    "tenant_members.ck_tenant_members_revision_positive",
    "tenant_members.ix_tenant_members_tenant_status_role_id",
    "tenant_members.ix_tenant_members_tenant_account_status",
    "tenant_audit_events.",
]


def readiness_api() -> ModuleType:
    try:
        return importlib.import_module("server.enterprise_readiness_api")
    except ModuleNotFoundError:
        pytest.fail("server.enterprise_readiness_api is missing")


def _client(
    *,
    state: CatalogSchemaState | None = None,
    engine_error: Exception | None = None,
    inspection_error: Exception | None = None,
    read_only_proof: Any | None = None,
) -> TestClient:
    api = readiness_api()
    engine = object()

    def engine_provider() -> object:
        if engine_error is not None:
            raise engine_error
        return engine

    def schema_inspector(candidate: Any) -> CatalogSchemaState:
        if inspection_error is not None:
            raise inspection_error
        if candidate is not engine:
            raise AssertionError("router did not inspect the injected engine")
        if state is None:
            raise AssertionError("test did not provide a schema state")
        return state

    app = FastAPI()
    app.include_router(
        api.build_enterprise_readiness_router(
            engine_provider=engine_provider,
            schema_inspector=schema_inspector,
            read_only_proof=read_only_proof or (lambda _engine: True),
        )
    )
    return TestClient(app)


def test_current_verified_head_is_ready_and_allows_mutations() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="current",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": HEAD_REVISION,
        "status": "ready",
        "missing_capability_groups": [],
        "mutations_safe": True,
    }


def test_known_older_revision_is_behind_and_reports_unavailable_capabilities() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0007_chunk_rev",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0007_chunk_rev",
        "status": "behind",
        "missing_capability_groups": [
            "knowledge_governance",
            "knowledge_content",
            "dataset_profile",
            "durable_document_deletion",
            "retrieval_quality",
            "source_control",
            "source_scheduling",
            "document_catalog_indexes",
            "enterprise_membership",
            "enterprise_access_graph",
            "organization_membership",
            "dataset_acl_control",
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_document_catalog_revision_is_behind_until_enterprise_membership_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0015_document_catalog_indexes",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0015_document_catalog_indexes",
        "status": "behind",
        "missing_capability_groups": [
            "enterprise_membership",
            "enterprise_access_graph",
            "organization_membership",
            "dataset_acl_control",
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_enterprise_membership_revision_is_behind_until_access_graph_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0016_enterprise_membership",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0016_enterprise_membership",
        "status": "behind",
        "missing_capability_groups": [
            "enterprise_access_graph",
            "organization_membership",
            "dataset_acl_control",
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_capability_revisions_keep_document_catalog_at_0015_and_membership_at_0016() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}

    assert capabilities["document_catalog_indexes"].revision == "0015_document_catalog_indexes"
    assert capabilities["document_catalog_indexes"].issue_fragments == (
        "documents.ix_documents_catalog_scope_updated",
        "documents.ix_documents_catalog_scope_status_updated",
        "documents.ix_documents_catalog_scope_doc_type_updated",
        "documents.ix_documents_catalog_scope_folder_updated",
    )
    assert capabilities["enterprise_membership"].revision == "0016_enterprise_membership"
    assert capabilities["enterprise_membership"].tables == frozenset({"tenant_audit_events"})
    assert set(capabilities["enterprise_membership"].issue_fragments) >= set(
        ENTERPRISE_MEMBERSHIP_ISSUE_FRAGMENTS
    )


@pytest.mark.parametrize("issue_fragment", ENTERPRISE_MEMBERSHIP_ISSUE_FRAGMENTS)
def test_enterprise_membership_manifest_issues_are_attributed_to_0016_capability(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_membership"]
    assert response.json()["mutations_safe"] is False


def test_missing_tenant_audit_events_table_is_attributed_to_0016_capability() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("tenant_audit_events",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_membership"]
    assert response.json()["mutations_safe"] is False


@pytest.mark.parametrize(
    ("failure_stage", "kwargs"),
    [
        ("engine", {"engine_error": RuntimeError("credentials leaked here")}),
        ("inspection", {"inspection_error": RuntimeError("database host leaked here")}),
    ],
)
def test_check_failures_are_unavailable_and_fail_closed(
    failure_stage: str,
    kwargs: dict[str, Exception],
) -> None:
    client = _client(**kwargs)

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503, failure_stage
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": None,
        "status": "unavailable",
        "missing_capability_groups": ALL_CAPABILITY_GROUPS,
        "mutations_safe": False,
    }
    assert "leaked" not in response.text


def test_incomplete_head_is_malformed_and_maps_missing_tables_to_capabilities() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("qa_knowledge", "retrieval_judgments"),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": HEAD_REVISION,
        "status": "malformed",
        "missing_capability_groups": ["knowledge_content", "retrieval_quality"],
        "mutations_safe": False,
    }


def test_contradictory_current_state_never_claims_ready() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0007_chunk_rev",
            head_revision=HEAD_REVISION,
            status="current",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ALL_CAPABILITY_GROUPS
    assert response.json()["mutations_safe"] is False


def test_unrecognized_application_head_is_malformed_and_fail_closed(monkeypatch) -> None:
    api = readiness_api()
    unknown_head = "0015_unmapped_enterprise_change"
    monkeypatch.setattr(api, "HEAD_REVISION", unknown_head)
    client = _client(
        state=CatalogSchemaState(
            revision=unknown_head,
            head_revision=unknown_head,
            status="current",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": unknown_head,
        "current_revision": unknown_head,
        "status": "malformed",
        "missing_capability_groups": ALL_CAPABILITY_GROUPS,
        "mutations_safe": False,
    }


def test_builder_requires_an_explicit_engine_provider() -> None:
    api = readiness_api()

    with pytest.raises(TypeError, match="engine_provider"):
        api.build_enterprise_readiness_router()


def test_main_application_mounts_enterprise_readiness_route() -> None:
    from server.app import app

    operation = app.openapi()["paths"]["/api/enterprise/readiness"]
    assert "get" in operation


def test_read_only_mysql_engine_provider_never_calls_schema_mutation_helpers() -> None:
    api = readiness_api()
    class Sentinel:
        pass

    sentinel = Sentinel()
    calls: list[tuple[str, dict[str, object]]] = []

    class CatalogSettings:
        db_url = "mysql+pymysql://readonly:secret@db.internal:3306/rag4c"
        db_path = "ignored.db"

    class Settings:
        catalog = CatalogSettings()

    def engine_factory(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return sentinel

    engine = api.create_read_only_catalog_engine(
        settings_provider=lambda: Settings(),
        engine_factory=engine_factory,
    )

    assert engine is sentinel
    assert calls == [
        (
            "mysql+pymysql://readonly:secret@db.internal:3306/rag4c",
            {
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "connect_args": {"init_command": "SET SESSION TRANSACTION READ ONLY"},
            },
        )
    ]


def test_read_only_mysql_engine_has_a_physical_read_only_proof() -> None:
    api = readiness_api()

    class CatalogSettings:
        db_url = "mysql+pymysql://readonly:secret@db.internal:3306/rag4c"
        db_path = "ignored.db"

    class Settings:
        catalog = CatalogSettings()

    class Sentinel:
        pass

    sentinel = Sentinel()

    engine = api.create_read_only_catalog_engine(
        settings_provider=lambda: Settings(),
        engine_factory=lambda _url, **_kwargs: sentinel,
    )

    assert api.prove_read_only_engine(engine) is False


def test_read_only_proof_requires_matching_dialect_and_runtime_state() -> None:
    api = readiness_api()

    class ResultConnection:
        def __init__(self, value: object):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def scalar(self, _statement: object) -> object:
            return self.value

    class Dialect:
        name = "postgresql"

    class Engine:
        dialect = Dialect()

        def __init__(self, value: object):
            self.value = value

        def connect(self):
            return ResultConnection(self.value)

    mismatch = Engine("on")
    setattr(
        mismatch,
        api._READ_ONLY_PROOF_ATTRIBUTE,
        api.ReadOnlyEngineProof(
            dialect="mysql", mechanism="mysql-session-transaction-read-only"
        ),
    )
    assert api.prove_read_only_engine(mismatch) is False

    disabled = Engine("off")
    setattr(
        disabled,
        api._READ_ONLY_PROOF_ATTRIBUTE,
        api.ReadOnlyEngineProof(
            dialect="postgresql", mechanism="postgresql-default-transaction-read-only"
        ),
    )
    assert api.prove_read_only_engine(disabled) is False

    enabled = Engine("on")
    setattr(
        enabled,
        api._READ_ONLY_PROOF_ATTRIBUTE,
        api.ReadOnlyEngineProof(
            dialect="postgresql", mechanism="postgresql-default-transaction-read-only"
        ),
    )
    assert api.prove_read_only_engine(enabled) is True


def test_read_only_postgresql_engine_uses_default_transaction_read_only() -> None:
    api = readiness_api()
    calls: list[tuple[str, dict[str, object]]] = []

    class CatalogSettings:
        db_url = "postgresql+psycopg://catalog:secret@db.internal:5432/rag4c"
        db_path = "ignored.db"

    class Settings:
        catalog = CatalogSettings()

    class Sentinel:
        pass

    sentinel = Sentinel()

    def engine_factory(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return sentinel

    engine = api.create_read_only_catalog_engine(
        settings_provider=lambda: Settings(),
        engine_factory=engine_factory,
    )

    assert engine is sentinel
    assert calls == [
        (
            "postgresql+psycopg://catalog:secret@db.internal:5432/rag4c",
            {
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "connect_args": {"options": "-c default_transaction_read_only=on"},
            },
        )
    ]
    assert api.prove_read_only_engine(engine) is False


def test_readiness_is_unavailable_when_non_sqlite_engine_has_no_read_only_proof() -> None:
    api = readiness_api()
    inspected = False

    class Dialect:
        name = "postgresql"

    class UnprovenEngine:
        dialect = Dialect()

    engine = UnprovenEngine()

    def inspector(_engine: Any) -> CatalogSchemaState:
        nonlocal inspected
        inspected = True
        return CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="current",
        )

    app = FastAPI()
    app.include_router(
        api.build_enterprise_readiness_router(
            engine_provider=lambda: engine,
            schema_inspector=inspector,
        )
    )

    response = TestClient(app).get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["mutations_safe"] is False
    assert inspected is False


def test_read_only_sqlite_provider_refuses_to_create_a_missing_database(tmp_path) -> None:
    api = readiness_api()
    missing = tmp_path / "missing" / "catalog.db"
    called = False

    class CatalogSettings:
        db_url = ""
        db_path = str(missing)

    class Settings:
        catalog = CatalogSettings()

    def engine_factory(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("engine factory must not be called for a missing sqlite database")

    with pytest.raises(FileNotFoundError, match="catalog database does not exist"):
        api.create_read_only_catalog_engine(
            settings_provider=lambda: Settings(),
            engine_factory=engine_factory,
        )

    assert called is False
    assert missing.exists() is False
    assert missing.parent.exists() is False


def test_standalone_preflight_app_exposes_readiness_without_main_lifespan() -> None:
    api = readiness_api()

    operation = api.app.openapi()["paths"]["/api/enterprise/readiness"]

    assert "get" in operation


def test_sqlite_db_url_is_forced_to_read_only_and_must_exist(tmp_path) -> None:
    api = readiness_api()
    database = tmp_path / "catalog.db"
    database.write_bytes(b"")
    calls: list[tuple[str, dict[str, object]]] = []
    sentinel = object()

    class CatalogSettings:
        db_url = f"sqlite:///{database.as_posix()}"
        db_path = "ignored.db"

    class Settings:
        catalog = CatalogSettings()

    def engine_factory(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return sentinel

    engine = api.create_read_only_catalog_engine(
        settings_provider=lambda: Settings(),
        engine_factory=engine_factory,
    )

    assert engine is sentinel
    assert calls == [
        (
            f"sqlite:///file:{database.as_posix()}?mode=ro&uri=true",
            {"connect_args": {"uri": True}},
        )
    ]


def test_sqlite_db_url_never_creates_a_missing_database(tmp_path) -> None:
    api = readiness_api()
    missing = tmp_path / "missing.db"
    called = False

    class CatalogSettings:
        db_url = f"sqlite:///{missing.as_posix()}"
        db_path = "ignored.db"

    class Settings:
        catalog = CatalogSettings()

    def engine_factory(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("missing sqlite URL must fail before engine creation")

    with pytest.raises(FileNotFoundError, match="catalog database does not exist"):
        api.create_read_only_catalog_engine(
            settings_provider=lambda: Settings(),
            engine_factory=engine_factory,
        )

    assert called is False
    assert missing.exists() is False


def test_enterprise_access_graph_capability_maps_the_0017_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}

    capability = capabilities["enterprise_access_graph"]

    assert capability.revision == "0017_enterprise_access_graph"
    assert capability.tables == frozenset(
        {
            "tenant_organization_units",
            "tenant_groups",
            "tenant_group_members",
            "dataset_access_grants",
            "tenant_invitations",
        }
    )
    assert {
        "tenant_organization_units.",
        "tenant_groups.",
        "tenant_group_members.",
        "dataset_access_grants.",
        "tenant_invitations.token_hash",
        "tenant_invitations.ck_tenant_invitations_status",
        "tenant_invitations.fk_tenant_invitations_scope_inviter",
        "tenant_invitations.ix_tenant_invitations_tenant_status_expires",
    } <= set(capability.issue_fragments)
    assert "tenant_invitations." not in capability.issue_fragments


def test_access_graph_revision_is_behind_until_organization_membership_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0017_enterprise_access_graph",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0017_enterprise_access_graph",
        "status": "behind",
        "missing_capability_groups": [
            "organization_membership",
            "dataset_acl_control",
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_dataset_acl_control_capability_maps_the_0019_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}

    capability = capabilities["dataset_acl_control"]

    assert capability.revision == catalog_schema_manifest.DATASET_ACL_CONTROL_REVISION
    assert capability.tables == catalog_schema_manifest.DATASET_ACL_CONTROL_REQUIRED_TABLES
    assert capability.issue_fragments == (
        catalog_schema_manifest.DATASET_ACL_CONTROL_ISSUE_FRAGMENTS
    )
    assert {
        "datasets.acl_mode",
        "datasets.acl_revision",
        "datasets.acl_enabled_at",
        "datasets.acl_enabled_by",
        "datasets.ck_datasets_acl_mode",
        "datasets.ck_datasets_acl_revision_positive",
        "dataset_acl_mutation_requests.",
    } <= set(capability.issue_fragments)


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "datasets.acl_mode",
        "datasets.acl_revision",
        "datasets.acl_enabled_at",
        "datasets.acl_enabled_by",
        "datasets.ck_datasets_acl_mode",
        "datasets.ck_datasets_acl_revision_positive",
        "dataset_acl_mutation_requests.",
        "dataset_acl_mutation_requests.ck_dataset_acl_mutation_requests_idempotency_key_length",
    ],
)
def test_dataset_acl_control_manifest_issues_are_attributed_to_0019_capability(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["dataset_acl_control"]
    assert response.json()["mutations_safe"] is False


def test_organization_membership_revision_is_behind_until_dataset_acl_control_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0018_organization_membership",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0018_organization_membership",
        "status": "behind",
        "missing_capability_groups": [
            "dataset_acl_control",
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_tenant_invitation_lifecycle_capability_maps_the_0020_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["tenant_invitation_lifecycle"]

    assert capability.revision == catalog_schema_manifest.TENANT_INVITATION_LIFECYCLE_REVISION
    assert capability.tables == catalog_schema_manifest.TENANT_INVITATION_LIFECYCLE_REQUIRED_TABLES
    assert capability.issue_fragments == (
        catalog_schema_manifest.TENANT_INVITATION_LIFECYCLE_ISSUE_FRAGMENTS
    )


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "tenant_invitations.pending_email_key",
        "tenant_invitations.last_sent_at",
        "tenant_invitations.send_count",
        "tenant_invitations.revoked_at",
        "tenant_invitations.revoked_by",
        "tenant_invitations.updated_by",
        "tenant_invitations.ck_tenant_invitations_pending_email_key",
        "tenant_control_mutation_requests.",
    ],
)
def test_invitation_lifecycle_damage_is_attributed_to_0020_capability(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["tenant_invitation_lifecycle"]
    assert response.json()["mutations_safe"] is False


def test_dataset_acl_control_revision_is_behind_until_invitation_lifecycle_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0019_dataset_acl_control",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0019_dataset_acl_control",
        "status": "behind",
        "missing_capability_groups": [
            "tenant_invitation_lifecycle",
            "enterprise_identity_federation",
            "scim_provisioning_data_plane",
            "enterprise_audit_compliance",
            "oidc_sso_runtime",
            "enterprise_approval_control",
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_enterprise_approval_control_capability_maps_0025_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_approval_control"]

    assert capability.revision == catalog_schema_manifest.ENTERPRISE_APPROVAL_CONTROL_REVISION
    assert capability.tables == catalog_schema_manifest.ENTERPRISE_APPROVAL_CONTROL_REQUIRED_TABLES
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_APPROVAL_CONTROL_ISSUE_FRAGMENTS
    )


def test_oidc_revision_is_behind_until_enterprise_approval_control_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0024_oidc_sso_runtime",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "behind"
    assert response.json()["missing_capability_groups"] == [
        "enterprise_approval_control",
        "enterprise_workspace_control",
        "enterprise_workspace_authorization",
        "enterprise_knowledge_base_registry",
        "enterprise_knowledge_base_releases",
        "enterprise_release_quality_certification",
        "enterprise_release_quality_operations",
        "enterprise_notification_center",
        "enterprise_content_recovery",
        "enterprise_task_operations",
        "enterprise_automation_workflows",
        "enterprise_knowledge_serving_reliability",
        "enterprise_knowledge_operations_feedback",
        "enterprise_qa_faq_operations",
        "enterprise_storage_backends",
        "enterprise_answer_evidence_facts",
    ]
    assert response.json()["mutations_safe"] is False


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "tenant_approval_policies.",
        "tenant_approval_policy_approvers.",
        "tenant_approval_requests.",
        "tenant_approval_decisions.",
    ],
)
def test_enterprise_approval_damage_is_attributed_to_0025_capability(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_approval_control"]
    assert response.json()["mutations_safe"] is False


def test_enterprise_workspace_control_capability_maps_0026_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_workspace_control"]

    assert capability.revision == catalog_schema_manifest.ENTERPRISE_WORKSPACE_CONTROL_REVISION
    assert capability.tables == catalog_schema_manifest.ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_TABLES
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_WORKSPACE_CONTROL_ISSUE_FRAGMENTS
    )


def test_approval_revision_is_behind_until_workspace_control_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0025_enterprise_approval_control",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0025_enterprise_approval_control",
        "status": "behind",
        "missing_capability_groups": [
            "enterprise_workspace_control",
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "tenant_workspaces.",
        "tenant_workspace_members.",
        "tenant_workspace_datasets.",
    ],
)
def test_workspace_control_damage_is_fail_closed_and_attributed_to_0026(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_workspace_control"]
    assert response.json()["mutations_safe"] is False


def test_missing_workspace_table_is_fail_closed_and_attributed_to_0026() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("tenant_workspaces",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_workspace_control"]
    assert response.json()["mutations_safe"] is False


def test_workspace_column_contract_damage_reports_malformed_readiness() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(
                "invalid length tenant_workspace_members.workspace_id: expected 128",
                "invalid type tenant_workspaces.revision: expected Integer",
                "non-nullable column tenant_workspace_datasets.active_primary_slot: expected nullable",
            ),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_workspace_control"]
    assert response.json()["mutations_safe"] is False


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "tenant_workspace_datasets.ck_tenant_workspace_datasets_active_primary_slot",
        "tenant_workspaces.ck_tenant_workspaces_lifecycle_evidence",
    ],
)
def test_workspace_bidirectional_check_damage_is_readiness_malformed(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid check {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_workspace_control"]
    assert response.json()["mutations_safe"] is False


def test_enterprise_workspace_authorization_capability_maps_0027_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_workspace_authorization"]

    assert (
        capability.revision == catalog_schema_manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION
    )
    assert (
        capability.tables
        == catalog_schema_manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_TABLES
    )
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_WORKSPACE_AUTHORIZATION_ISSUE_FRAGMENTS
    )


def test_workspace_control_revision_is_behind_until_authorization_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0026_enterprise_workspace_control",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0026_enterprise_workspace_control",
        "status": "behind",
        "missing_capability_groups": [
            "enterprise_workspace_authorization",
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


@pytest.mark.parametrize(
    "issue_fragment",
    [
        "tenant_workspace_authorization_policies.",
        "tenant_approval_policies.ck_tenant_approval_policies_action_type",
        "tenant_approval_requests.ck_tenant_approval_requests_action_type",
    ],
)
def test_workspace_authorization_damage_is_fail_closed_and_attributed_to_0027(
    issue_fragment: str,
) -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=(f"missing or invalid schema element {issue_fragment}",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    expected_groups = (
        ["enterprise_workspace_authorization"]
        if issue_fragment.startswith("tenant_workspace_authorization_policies.")
        else [
            "enterprise_workspace_authorization",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
        ]
    )
    assert response.json()["missing_capability_groups"] == expected_groups
    assert response.json()["mutations_safe"] is False


def test_missing_workspace_authorization_table_is_fail_closed_and_attributed_to_0027() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("tenant_workspace_authorization_policies",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_workspace_authorization"]
    assert response.json()["mutations_safe"] is False


def test_enterprise_knowledge_base_registry_capability_maps_0028_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_knowledge_base_registry"]

    assert (
        capability.revision == catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION
    )
    assert (
        capability.tables
        == catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_TABLES
    )
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_ISSUE_FRAGMENTS
    )


def test_registry_damage_is_fail_closed_and_attributed_to_0028() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=("missing or invalid unique apps.uq_apps_tenant_id",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_knowledge_base_registry"]
    assert response.json()["mutations_safe"] is False


def test_missing_registry_table_is_fail_closed_and_attributed_to_0028() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("dataset_workspace_ownerships",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == ["enterprise_knowledge_base_registry"]
    assert response.json()["mutations_safe"] is False


def test_0027_revision_is_behind_until_0028_registry_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0027_enterprise_workspace_authorization",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json() == {
        "expected_head": HEAD_REVISION,
        "current_revision": "0027_enterprise_workspace_authorization",
        "status": "behind",
        "missing_capability_groups": [
            "enterprise_knowledge_base_registry",
            "enterprise_knowledge_base_releases",
            "enterprise_release_quality_certification",
            "enterprise_release_quality_operations",
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
            "enterprise_knowledge_operations_feedback",
            "enterprise_qa_faq_operations",
            "enterprise_storage_backends",
            "enterprise_answer_evidence_facts",
        ],
        "mutations_safe": False,
    }


def test_enterprise_knowledge_base_releases_capability_maps_0029_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_knowledge_base_releases"]

    assert (
        capability.revision == catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION
    )
    assert capability.tables == catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_ISSUE_FRAGMENTS
    )
    assert list(capabilities).index("enterprise_knowledge_base_releases") == (
        list(capabilities).index("enterprise_knowledge_base_registry") + 1
    )


def test_release_schema_damage_is_fail_closed_and_attributed_to_0029() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            schema_issues=("missing column dataset_release_manifests.manifest_digest",),
        )
    )

    response = client.get("/api/enterprise/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == [
        "enterprise_knowledge_base_releases",
        "enterprise_knowledge_serving_reliability",
    ]
    assert response.json()["mutations_safe"] is False


def test_enterprise_release_quality_capability_maps_0030_contract() -> None:
    api = readiness_api()
    capabilities = {item.key: item for item in api._CAPABILITIES}
    capability = capabilities["enterprise_release_quality_certification"]
    assert capability.revision == "0030_enterprise_release_quality_certification"
    assert (
        capability.tables
        == catalog_schema_manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_TABLES
    )
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_ISSUE_FRAGMENTS
    )
    assert list(capabilities).index("enterprise_release_quality_certification") == (
        list(capabilities).index("enterprise_knowledge_base_releases") + 1
    )


def test_enterprise_release_quality_operations_capability_maps_0031_contract() -> None:
    api = readiness_api()
    capabilities = {item.key: item for item in api._CAPABILITIES}
    capability = capabilities["enterprise_release_quality_operations"]
    assert capability.revision == "0031_enterprise_release_quality_operations"
    assert (
        capability.tables
        == catalog_schema_manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES
    )
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_ISSUE_FRAGMENTS
    )
    assert list(capabilities).index("enterprise_release_quality_operations") == (
        list(capabilities).index("enterprise_release_quality_certification") + 1
    )


def test_enterprise_notification_center_capability_maps_0032_contract() -> None:
    api = readiness_api()
    capabilities = {item.key: item for item in api._CAPABILITIES}
    capability = capabilities["enterprise_notification_center"]
    assert capability.revision == "0032_enterprise_notification_center"
    assert (
        capability.tables == catalog_schema_manifest.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES
    )
    assert (
        capability.issue_fragments
        == catalog_schema_manifest.ENTERPRISE_NOTIFICATION_CENTER_ISSUE_FRAGMENTS
    )
    assert list(capabilities).index("enterprise_notification_center") == (
        list(capabilities).index("enterprise_release_quality_operations") + 1
    )


def test_enterprise_content_recovery_capability_maps_0033_contract() -> None:
    api = readiness_api()
    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    assert "enterprise_content_recovery" in api._ALL_CAPABILITY_GROUPS
    assert (
        capabilities["enterprise_content_recovery"].revision == "0033_enterprise_content_recovery"
    )
    assert capabilities["enterprise_content_recovery"].tables == frozenset(
        {
            "tenant_content_retention_policies",
            "tenant_document_recycle_entries",
            "tenant_document_legal_holds",
            "tenant_document_purge_requests",
            "tenant_document_recovery_events",
        }
    )


def test_content_recovery_damage_is_fail_closed_and_attributed_to_0033() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision=HEAD_REVISION,
            head_revision=HEAD_REVISION,
            status="incomplete",
            missing_tables=("tenant_document_recycle_entries",),
        )
    )
    response = client.get("/api/enterprise/readiness")
    assert response.status_code == 503
    assert response.json()["status"] == "malformed"
    assert response.json()["missing_capability_groups"] == [
        "enterprise_content_recovery",
        "enterprise_task_operations",
        "enterprise_automation_workflows",
    ]


def test_enterprise_task_operations_capability_maps_0034_contract() -> None:
    api = readiness_api()
    from core import catalog_schema as manifest

    capabilities = {capability.key: capability for capability in api._CAPABILITIES}
    capability = capabilities["enterprise_task_operations"]
    assert capability.revision == "0034_enterprise_task_operations"
    assert capability.tables == manifest.ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES
    assert capability.issue_fragments == manifest.ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS
    assert list(capabilities).index("enterprise_task_operations") == (
        list(capabilities).index("enterprise_content_recovery") + 1
    )


def test_enterprise_knowledge_serving_capability_maps_0036_contract() -> None:
    api = readiness_api()
    capabilities = {item.key: item for item in api._CAPABILITIES}
    capability = capabilities["enterprise_knowledge_serving_reliability"]
    assert capability.revision == "0036_enterprise_knowledge_serving_reliability"
    assert (
        capability.tables
        == catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES
    )
    assert capability.issue_fragments == (
        catalog_schema_manifest.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_ISSUE_FRAGMENTS
    )
    assert list(capabilities).index("enterprise_knowledge_serving_reliability") == (
        list(capabilities).index("enterprise_automation_workflows") + 1
    )


def test_0035_revision_is_behind_until_0036_serving_reliability_is_applied() -> None:
    client = _client(
        state=CatalogSchemaState(
            revision="0035_enterprise_automation_workflows",
            head_revision=HEAD_REVISION,
            status="behind",
        )
    )
    response = client.get("/api/enterprise/readiness")
    assert response.status_code == 503
    assert response.json()["missing_capability_groups"] == [
        "enterprise_knowledge_serving_reliability",
        "enterprise_knowledge_operations_feedback",
        "enterprise_qa_faq_operations",
        "enterprise_storage_backends",
        "enterprise_answer_evidence_facts",
    ]
    assert response.json()["mutations_safe"] is False


def test_head_revision_always_has_a_registered_capability() -> None:
    """链尾必须存在一个能力组，否则 readiness 整体不可用。

    这是 QA/FAQ 线并入时真实踩到的坑，不是假想守卫：HEAD_REVISION 前进到
    0039_answer_evidence_facts 而 _CAPABILITIES 止于 0037，于是
    _REVISION_INDEX[HEAD_REVISION] is None，而 _evaluate_readiness 里能返回
    ready / behind 的分支都要求 head_index 非空，最后一个"已升到 head"的库
    和一个"落后"的库会**同样**被判成 malformed（fail-closed，但等于权威接口废掉）。
    """
    api = readiness_api()

    assert catalog_schema_manifest.HEAD_REVISION in api._REVISION_INDEX


def test_catalog_exactly_at_head_reports_ready() -> None:
    """上一那条不变量的行为面：结构完整且已在 head 的目录必须是 ready。"""
    api = readiness_api()

    client = _client(
        state=CatalogSchemaState(
            revision=catalog_schema_manifest.HEAD_REVISION,
            head_revision=api.HEAD_REVISION,
            status="current",
        )
    )
    body = client.get("/api/enterprise/readiness").json()

    assert body["status"] == "ready"
    assert body["missing_capability_groups"] == []
    assert body["mutations_safe"] is True
