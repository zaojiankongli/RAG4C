"""Read-only enterprise catalog readiness API.

The router is dependency-injected so callers and tests can supply an engine provider
and schema inspector.  A check that cannot prove the catalog is exactly at the
application head is always fail-closed for mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, FastAPI, Response, status
from pydantic import BaseModel, ConfigDict

from config.settings import get_settings
from core.catalog_schema import (
    CatalogSchemaState,
    DATASET_ACL_CONTROL_ISSUE_FRAGMENTS,
    DATASET_ACL_CONTROL_REQUIRED_TABLES,
    DATASET_ACL_CONTROL_REVISION,
    TENANT_INVITATION_LIFECYCLE_ISSUE_FRAGMENTS,
    TENANT_INVITATION_LIFECYCLE_REQUIRED_TABLES,
    TENANT_INVITATION_LIFECYCLE_REVISION,
    ENTERPRISE_IDENTITY_FEDERATION_REVISION,
    HEAD_REVISION,
    IDENTITY_FEDERATION_ISSUE_FRAGMENTS,
    IDENTITY_FEDERATION_TABLES,
    SCIM_PROVISIONING_DATA_PLANE_REVISION,
    SCIM_PROVISIONING_ISSUE_FRAGMENTS,
    SCIM_PROVISIONING_TABLES,
    AUDIT_COMPLIANCE_REVISION,
    AUDIT_COMPLIANCE_ISSUE_FRAGMENTS,
    AUDIT_COMPLIANCE_TABLES,
    OIDC_SSO_RUNTIME_REVISION,
    OIDC_RUNTIME_ISSUE_FRAGMENTS,
    OIDC_RUNTIME_TABLES,
    ENTERPRISE_APPROVAL_CONTROL_REVISION,
    ENTERPRISE_APPROVAL_CONTROL_ISSUE_FRAGMENTS,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_TABLES,
    ENTERPRISE_WORKSPACE_CONTROL_REVISION,
    ENTERPRISE_WORKSPACE_CONTROL_ISSUE_FRAGMENTS,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_TABLES,
    ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
    ENTERPRISE_WORKSPACE_AUTHORIZATION_ISSUE_FRAGMENTS,
    ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_TABLES,
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_ISSUE_FRAGMENTS,
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_TABLES,
    ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
    ENTERPRISE_KNOWLEDGE_BASE_RELEASE_ISSUE_FRAGMENTS,
    ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES,
    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_ISSUE_FRAGMENTS,
    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_TABLES,
    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_ISSUE_FRAGMENTS,
    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES,
    ENTERPRISE_NOTIFICATION_CENTER_REVISION,
    ENTERPRISE_NOTIFICATION_CENTER_ISSUE_FRAGMENTS,
    ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES,
    ENTERPRISE_CONTENT_RECOVERY_REVISION,
    ENTERPRISE_CONTENT_RECOVERY_ISSUE_FRAGMENTS,
    ENTERPRISE_CONTENT_RECOVERY_REQUIRED_TABLES,
    ENTERPRISE_TASK_OPERATIONS_REVISION,
    ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS,
    ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES,
    ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
    ENTERPRISE_AUTOMATION_WORKFLOWS_ISSUE_FRAGMENTS,
    ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_TABLES,
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_ISSUE_FRAGMENTS,
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES,
    ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION,
    ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_ISSUE_FRAGMENTS,
    ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_TABLES,
    QA_FAQ_OPS_REVISION,
    QA_FAQ_OPS_ISSUE_FRAGMENTS,
    QA_FAQ_OPS_REQUIRED_TABLES,
    STORAGE_BACKENDS_REVISION,
    STORAGE_BACKENDS_ISSUE_FRAGMENTS,
    STORAGE_BACKENDS_REQUIRED_TABLES,
    ANSWER_EVIDENCE_FACTS_REVISION,
    ANSWER_EVIDENCE_FACTS_ISSUE_FRAGMENTS,
    ANSWER_EVIDENCE_FACTS_REQUIRED_TABLES,
    inspect_catalog_schema,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ReadinessStatus = Literal["ready", "behind", "unavailable", "malformed"]
EngineProvider = Callable[[], Any]
SchemaInspector = Callable[[Any], CatalogSchemaState]
ReadOnlyProofProvider = Callable[[Any], bool]


@dataclass(frozen=True)
class ReadOnlyEngineProof:
    """Static proof attached to an engine created for inspection-only access."""

    dialect: str
    mechanism: str
    guaranteed: bool = True


@dataclass(frozen=True)
class _Capability:
    key: str
    revision: str
    tables: frozenset[str] = frozenset()
    issue_fragments: tuple[str, ...] = ()


_CAPABILITIES = (
    _Capability(
        "catalog_core",
        "0001_base",
        frozenset(
            {
                "accounts",
                "apps",
                "datasets",
                "document_segments",
                "documents",
                "metadata_fields",
                "tenant_members",
                "tenants",
                "workflows",
            }
        ),
    ),
    _Capability(
        "identity_integrity",
        "0002_identity",
        issue_fragments=(
            "tenant_members.uq_tenant_members_account_tenant",
            "document_segments.uq_document_segments_document_seq",
            "metadata_fields.uq_metadata_fields_dataset_key",
        ),
    ),
    _Capability(
        "ingestion_audit",
        "0003_ingest",
        frozenset({"document_ingest_attempts", "document_ingest_spans"}),
    ),
    _Capability(
        "durable_index_operations",
        "0004_index_ops",
        frozenset({"index_operations", "index_dead_letters"}),
    ),
    _Capability(
        "source_sync_ledger",
        "0005_source_sync",
        frozenset(
            {
                "data_sources",
                "source_sync_runs",
                "source_sync_items",
                "source_document_states",
            }
        ),
    ),
    _Capability(
        "source_identity",
        "0006_source_id",
        issue_fragments=(
            "documents.uq_documents_dataset_source_uri_hash",
            "documents.uq_documents_dataset_source_external",
        ),
    ),
    _Capability(
        "immutable_chunk_authority",
        "0007_chunk_rev",
        frozenset({"chunk_heads", "chunk_revisions"}),
    ),
    _Capability(
        "knowledge_governance",
        "0008_governance",
        frozenset(
            {
                "knowledge_folders",
                "knowledge_tags",
                "document_tags",
                "knowledge_audit_events",
            }
        ),
    ),
    _Capability(
        "knowledge_content",
        "0009_content",
        frozenset({"document_versions", "qa_knowledge", "qa_alternative_questions"}),
    ),
    _Capability(
        "dataset_profile",
        "0010_dataset_profile",
        issue_fragments=(
            "datasets.profile_revision",
            "datasets.owner_id",
            "datasets.visibility",
            "datasets.profile_json",
            "datasets.parser_policy",
            "datasets.chunk_policy",
            "datasets.retrieval_policy",
            "datasets.retention_policy",
            "datasets.metadata_policy",
            "datasets.default_language",
            "datasets.graph_enabled",
            "datasets.qa_enabled",
            "datasets.archived_at",
            "datasets.archived_by",
            "datasets.updated_at",
            "datasets.ck_datasets_profile_revision_positive",
            "datasets.ck_datasets_visibility",
            "datasets.ck_datasets_status",
            "datasets.fk_datasets_scope_owner_member",
            "datasets.ix_datasets_scope_status",
            "datasets.ix_datasets_scope_visibility",
            "datasets.ix_datasets_scope_owner",
            "datasets.ix_datasets_scope_updated",
        ),
    ),
    _Capability(
        "durable_document_deletion",
        "0011_durable_delete",
        frozenset({"document_delete_batches", "document_delete_operations"}),
        issue_fragments=(
            "documents.lifecycle_state",
            "documents.mutation_generation",
            "datasets.mutation_generation",
            "datasets.serving_generation",
            "datasets.ck_datasets_generations_nonnegative",
        ),
    ),
    _Capability(
        "retrieval_quality",
        "0012_retrieval_experiments",
        frozenset({"retrieval_experiments", "retrieval_judgments"}),
        issue_fragments=("retrieval_experiments.", "retrieval_judgments."),
    ),
    _Capability(
        "source_control",
        "0013_source_control",
        issue_fragments=(
            "data_sources.mutation_generation",
            "source_sync_runs.source_generation",
            "source_sync_runs.dataset_generation",
            "source_document_states.state",
        ),
    ),
    _Capability(
        "source_scheduling",
        "0014_source_schedules",
        frozenset({"source_schedules"}),
        issue_fragments=(
            "source_sync_runs.schedule_id",
            "source_sync_runs.schedule_revision",
            "source_sync_runs.planned_at",
            "source_schedules.",
        ),
    ),
    _Capability(
        "document_catalog_indexes",
        "0015_document_catalog_indexes",
        issue_fragments=(
            "documents.ix_documents_catalog_scope_updated",
            "documents.ix_documents_catalog_scope_status_updated",
            "documents.ix_documents_catalog_scope_doc_type_updated",
            "documents.ix_documents_catalog_scope_folder_updated",
        ),
    ),
    _Capability(
        "enterprise_membership",
        "0016_enterprise_membership",
        frozenset({"tenant_audit_events"}),
        issue_fragments=(
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
        ),
    ),
    _Capability(
        "enterprise_access_graph",
        "0017_enterprise_access_graph",
        frozenset(
            {
                "tenant_organization_units",
                "tenant_groups",
                "tenant_group_members",
                "dataset_access_grants",
                "tenant_invitations",
            }
        ),
        issue_fragments=(
            "tenant_organization_units.",
            "tenant_groups.",
            "tenant_group_members.",
            "dataset_access_grants.",
            "tenant_invitations.email",
            "tenant_invitations.normalized_email",
            "tenant_invitations.role",
            "tenant_invitations.status",
            "tenant_invitations.token_hash",
            "tenant_invitations.expires_at",
            "tenant_invitations.accepted_at",
            "tenant_invitations.accepted_by",
            "tenant_invitations.invited_by",
            "tenant_invitations.revision",
            "tenant_invitations.created_at",
            "tenant_invitations.updated_at",
            "tenant_invitations.ck_tenant_invitations_role",
            "tenant_invitations.ck_tenant_invitations_status",
            "tenant_invitations.ck_tenant_invitations_revision_positive",
            "tenant_invitations.fk_tenant_invitations_tenant",
            "tenant_invitations.fk_tenant_invitations_scope_inviter",
            "tenant_invitations.fk_tenant_invitations_scope_acceptor",
            "tenant_invitations.ix_tenant_invitations_tenant_status_expires",
            "tenant_invitations.ix_tenant_invitations_tenant_email",
        ),
    ),
    _Capability(
        "organization_membership",
        "0018_organization_membership",
        frozenset({"tenant_organization_unit_members"}),
        issue_fragments=("tenant_organization_unit_members.",),
    ),
    _Capability(
        "dataset_acl_control",
        DATASET_ACL_CONTROL_REVISION,
        DATASET_ACL_CONTROL_REQUIRED_TABLES,
        issue_fragments=DATASET_ACL_CONTROL_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "tenant_invitation_lifecycle",
        TENANT_INVITATION_LIFECYCLE_REVISION,
        TENANT_INVITATION_LIFECYCLE_REQUIRED_TABLES,
        issue_fragments=TENANT_INVITATION_LIFECYCLE_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_identity_federation",
        ENTERPRISE_IDENTITY_FEDERATION_REVISION,
        IDENTITY_FEDERATION_TABLES,
        issue_fragments=IDENTITY_FEDERATION_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "scim_provisioning_data_plane",
        SCIM_PROVISIONING_DATA_PLANE_REVISION,
        SCIM_PROVISIONING_TABLES,
        issue_fragments=SCIM_PROVISIONING_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_audit_compliance",
        AUDIT_COMPLIANCE_REVISION,
        AUDIT_COMPLIANCE_TABLES,
        issue_fragments=AUDIT_COMPLIANCE_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "oidc_sso_runtime",
        OIDC_SSO_RUNTIME_REVISION,
        OIDC_RUNTIME_TABLES,
        issue_fragments=OIDC_RUNTIME_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_approval_control",
        ENTERPRISE_APPROVAL_CONTROL_REVISION,
        ENTERPRISE_APPROVAL_CONTROL_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_APPROVAL_CONTROL_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_workspace_control",
        ENTERPRISE_WORKSPACE_CONTROL_REVISION,
        ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_WORKSPACE_CONTROL_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_workspace_authorization",
        ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
        ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_WORKSPACE_AUTHORIZATION_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_knowledge_base_registry",
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_knowledge_base_releases",
        ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
        ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES,
        issue_fragments=ENTERPRISE_KNOWLEDGE_BASE_RELEASE_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_release_quality_certification",
        ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
        ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_release_quality_operations",
        ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
        ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_RELEASE_QUALITY_OPERATIONS_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_notification_center",
        ENTERPRISE_NOTIFICATION_CENTER_REVISION,
        ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_NOTIFICATION_CENTER_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_content_recovery",
        ENTERPRISE_CONTENT_RECOVERY_REVISION,
        ENTERPRISE_CONTENT_RECOVERY_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_CONTENT_RECOVERY_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_task_operations",
        ENTERPRISE_TASK_OPERATIONS_REVISION,
        ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_automation_workflows",
        ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
        ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_AUTOMATION_WORKFLOWS_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_knowledge_serving_reliability",
        ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_knowledge_operations_feedback",
        ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION,
        ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_TABLES,
        issue_fragments=ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_ISSUE_FRAGMENTS,
    ),
    # QA/FAQ 运维线并入后 head 前进到 0039；这三段必须注册，否则
    # _REVISION_INDEX[HEAD_REVISION] is None，_evaluate_readiness 的所有"ready/behind"分支
    # 都要求 head_index 非空，一个已升级到 head 的库会被永久判成 malformed（fail-closed，
    # 但等于权威接口不可用）。注册是纯加法：不改判定语义，只让新链尾有对应能力组。
    _Capability(
        "enterprise_qa_faq_operations",
        QA_FAQ_OPS_REVISION,
        QA_FAQ_OPS_REQUIRED_TABLES,
        issue_fragments=QA_FAQ_OPS_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_storage_backends",
        STORAGE_BACKENDS_REVISION,
        STORAGE_BACKENDS_REQUIRED_TABLES,
        issue_fragments=STORAGE_BACKENDS_ISSUE_FRAGMENTS,
    ),
    _Capability(
        "enterprise_answer_evidence_facts",
        ANSWER_EVIDENCE_FACTS_REVISION,
        ANSWER_EVIDENCE_FACTS_REQUIRED_TABLES,
        issue_fragments=ANSWER_EVIDENCE_FACTS_ISSUE_FRAGMENTS,
    ),
)
_REVISION_INDEX = {capability.revision: index for index, capability in enumerate(_CAPABILITIES)}
_ALL_CAPABILITY_GROUPS = tuple(capability.key for capability in _CAPABILITIES)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_READ_ONLY_PROOF_ATTRIBUTE = "_rag4c_read_only_proof"
_POSTGRES_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"
_MYSQL_READ_ONLY_INIT_COMMAND = "SET SESSION TRANSACTION READ ONLY"


def _attach_read_only_proof(engine: Any, proof: ReadOnlyEngineProof) -> Any:
    """Attach a non-user-controlled proof marker without wrapping the SQLAlchemy engine."""

    try:
        setattr(engine, _READ_ONLY_PROOF_ATTRIBUTE, proof)
    except (AttributeError, TypeError):
        # A test double or a proxy may be immutable.  The caller still receives the
        # original engine, but readiness will fail closed because it cannot prove it.
        pass
    return engine


def prove_read_only_engine(engine: Any) -> bool:
    """Verify the configured proof against the actual dialect and live session state."""

    proof = getattr(engine, _READ_ONLY_PROOF_ATTRIBUTE, None)
    backend = str(getattr(getattr(engine, "dialect", None), "name", "")).lower()
    expected_mechanism = {
        "sqlite": "sqlite-uri-mode-ro",
        "postgresql": "postgresql-default-transaction-read-only",
        "mysql": "mysql-session-transaction-read-only",
        "mariadb": "mysql-session-transaction-read-only",
    }.get(backend)
    if (
        not isinstance(proof, ReadOnlyEngineProof)
        or proof.guaranteed is not True
        or proof.dialect != backend
        or proof.mechanism != expected_mechanism
    ):
        return False
    if backend == "sqlite":
        return True
    try:
        with engine.connect() as connection:
            if backend == "postgresql":
                value = connection.scalar(text("SHOW transaction_read_only"))
                return str(value or "").strip().casefold() in {"on", "true", "1"}
            for statement in (
                "SELECT @@session.transaction_read_only",
                "SELECT @@session.tx_read_only",
            ):
                try:
                    value = connection.scalar(text(statement))
                    return str(value or "").strip().casefold() in {"on", "true", "1"}
                except Exception:
                    continue
    except Exception:
        return False
    return False


def _non_sqlite_read_only_engine_options(
    parsed_url: Any,
) -> tuple[dict[str, Any], ReadOnlyEngineProof]:
    backend = str(parsed_url.get_backend_name()).lower()
    common = {"pool_pre_ping": True, "pool_recycle": 3600}
    if backend == "postgresql":
        return (
            {
                **common,
                "connect_args": {"options": _POSTGRES_READ_ONLY_OPTIONS},
            },
            ReadOnlyEngineProof(
                dialect="postgresql",
                mechanism="postgresql-default-transaction-read-only",
            ),
        )
    if backend in {"mysql", "mariadb"}:
        return (
            {
                **common,
                "connect_args": {"init_command": _MYSQL_READ_ONLY_INIT_COMMAND},
            },
            ReadOnlyEngineProof(
                dialect=backend,
                mechanism="mysql-session-transaction-read-only",
            ),
        )
    return common, ReadOnlyEngineProof(
        dialect=backend,
        mechanism="unsupported",
        guaranteed=False,
    )


def create_read_only_catalog_engine(
    *,
    settings_provider: Callable[[], Any] = get_settings,
    engine_factory: Callable[..., Any] = create_engine,
) -> Any:
    """Create an inspection-only engine without schema creation, upgrade, or repair."""

    catalog_settings = settings_provider().catalog
    database_url = str(getattr(catalog_settings, "db_url", "") or "").strip()
    if database_url:
        parsed_url = make_url(database_url)
        if parsed_url.get_backend_name() != "sqlite":
            engine_options, proof = _non_sqlite_read_only_engine_options(parsed_url)
            return _attach_read_only_proof(
                engine_factory(database_url, **engine_options),
                proof,
            )
        raw_database_path = str(parsed_url.database or "")
        if raw_database_path.startswith("file:"):
            raw_database_path = raw_database_path.removeprefix("file:").split("?", 1)[0]
        database_path = Path(raw_database_path)
    else:
        database_path = Path(str(getattr(catalog_settings, "db_path", "data/rag4c.db")))
    if not database_path.is_absolute():
        database_path = _PROJECT_ROOT / database_path
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"catalog database does not exist: {database_path}")

    sqlite_uri = f"sqlite:///file:{database_path.as_posix()}?mode=ro&uri=true"
    return _attach_read_only_proof(
        engine_factory(sqlite_uri, connect_args={"uri": True}),
        ReadOnlyEngineProof(dialect="sqlite", mechanism="sqlite-uri-mode-ro"),
    )


@lru_cache(maxsize=1)
def get_read_only_catalog_engine() -> Any:
    """Return the process-wide inspection engine used by readiness probes."""

    return create_read_only_catalog_engine()


class EnterpriseReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_head: str
    current_revision: str | None
    status: ReadinessStatus
    missing_capability_groups: list[str]
    mutations_safe: bool


def _report(
    readiness_status: ReadinessStatus,
    *,
    current_revision: str | None,
    missing_capability_groups: tuple[str, ...],
) -> EnterpriseReadinessResponse:
    return EnterpriseReadinessResponse(
        expected_head=HEAD_REVISION,
        current_revision=current_revision,
        status=readiness_status,
        missing_capability_groups=list(missing_capability_groups),
        mutations_safe=readiness_status == "ready",
    )


def _groups_after_revision(revision: str | None) -> tuple[str, ...]:
    index = _REVISION_INDEX.get(revision or "")
    if index is None:
        return _ALL_CAPABILITY_GROUPS
    return tuple(capability.key for capability in _CAPABILITIES[index + 1 :])


def _groups_for_damage(state: CatalogSchemaState) -> tuple[str, ...]:
    damaged: set[str] = set(_groups_after_revision(state.revision))
    missing_tables = set(state.missing_tables)
    issues = tuple(str(issue) for issue in state.schema_issues)

    workspace_action_checks = (
        "tenant_approval_policies.ck_tenant_approval_policies_action_type",
        "tenant_approval_requests.ck_tenant_approval_requests_action_type",
    )
    workspace_action_damage = any(
        fragment in issue for fragment in workspace_action_checks for issue in issues
    )
    for capability in _CAPABILITIES:
        if capability.tables & missing_tables:
            damaged.add(capability.key)
        if capability.key == "enterprise_approval_control" and workspace_action_damage:
            continue
        if any(fragment in issue for fragment in capability.issue_fragments for issue in issues):
            damaged.add(capability.key)
    if workspace_action_damage:
        damaged.add("enterprise_workspace_authorization")
    if "enterprise_content_recovery" in damaged:
        damaged.add("enterprise_task_operations")
    if "enterprise_task_operations" in damaged:
        damaged.add("enterprise_automation_workflows")

    if not damaged:
        return _ALL_CAPABILITY_GROUPS
    return tuple(capability.key for capability in _CAPABILITIES if capability.key in damaged)


def _evaluate_readiness(
    engine_provider: EngineProvider,
    schema_inspector: SchemaInspector,
    *,
    read_only_proof: ReadOnlyProofProvider | None = None,
) -> EnterpriseReadinessResponse:
    try:
        engine = engine_provider()
        if read_only_proof is not None and not read_only_proof(engine):
            return _report(
                "unavailable",
                current_revision=None,
                missing_capability_groups=_ALL_CAPABILITY_GROUPS,
            )
        state = schema_inspector(engine)
    except Exception:
        return _report(
            "unavailable",
            current_revision=None,
            missing_capability_groups=_ALL_CAPABILITY_GROUPS,
        )

    if not isinstance(state, CatalogSchemaState):
        return _report(
            "malformed",
            current_revision=None,
            missing_capability_groups=_ALL_CAPABILITY_GROUPS,
        )

    current_revision = state.revision if isinstance(state.revision, str) else None
    head_matches = state.head_revision == HEAD_REVISION
    head_index = _REVISION_INDEX.get(HEAD_REVISION)
    clean_manifest = not state.missing_tables and not state.schema_issues

    if (
        state.status == "current"
        and head_index is not None
        and head_matches
        and current_revision == HEAD_REVISION
        and clean_manifest
    ):
        return _report("ready", current_revision=current_revision, missing_capability_groups=())

    if (
        state.status == "behind"
        and head_index is not None
        and head_matches
        and current_revision in _REVISION_INDEX
        and _REVISION_INDEX[current_revision] < head_index
        and clean_manifest
    ):
        return _report(
            "behind",
            current_revision=current_revision,
            missing_capability_groups=_groups_after_revision(current_revision),
        )

    internally_contradictory = state.status == "current" or head_index is None
    missing = (
        _ALL_CAPABILITY_GROUPS
        if internally_contradictory or not head_matches
        else _groups_for_damage(state)
    )
    return _report(
        "malformed",
        current_revision=current_revision,
        missing_capability_groups=missing,
    )


def get_enterprise_readiness_report(
    *,
    engine_provider: EngineProvider = get_read_only_catalog_engine,
    schema_inspector: SchemaInspector = inspect_catalog_schema,
    read_only_proof: ReadOnlyProofProvider | None = prove_read_only_engine,
) -> EnterpriseReadinessResponse:
    """Evaluate readiness for internal route gates without constructing an HTTP response."""

    return _evaluate_readiness(
        engine_provider,
        schema_inspector,
        read_only_proof=read_only_proof,
    )


def build_enterprise_readiness_router(
    *,
    engine_provider: EngineProvider,
    schema_inspector: SchemaInspector = inspect_catalog_schema,
    read_only_proof: ReadOnlyProofProvider | None = prove_read_only_engine,
) -> APIRouter:
    """Build the future-mountable, read-only enterprise readiness router."""

    enterprise_router = APIRouter(prefix="/api/enterprise", tags=["enterprise-readiness"])

    if read_only_proof is not None and not callable(read_only_proof):
        raise TypeError("read_only_proof must be callable")

    @enterprise_router.get(
        "/readiness",
        response_model=EnterpriseReadinessResponse,
        responses={503: {"model": EnterpriseReadinessResponse}},
    )
    def get_enterprise_readiness(response: Response) -> EnterpriseReadinessResponse:
        report = _evaluate_readiness(
            engine_provider,
            schema_inspector,
            read_only_proof=read_only_proof,
        )
        if report.status != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return report

    return enterprise_router


app = FastAPI(
    title="RAG4C Enterprise Readiness API",
    description="Read-only catalog schema preflight service.",
    version="0.1.0",
)
app.include_router(
    build_enterprise_readiness_router(
        engine_provider=get_read_only_catalog_engine,
    )
)
