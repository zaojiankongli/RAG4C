"""Versioned schema management for the relational catalog."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Engine,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    and_,
    column as sql_column,
    literal,
    or_,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "0001_base"
DATASET_ACL_CONTROL_REVISION = "0019_dataset_acl_control"
TENANT_INVITATION_LIFECYCLE_REVISION = "0020_tenant_invitation_lifecycle"
ENTERPRISE_IDENTITY_FEDERATION_REVISION = "0021_enterprise_identity_federation"
SCIM_PROVISIONING_DATA_PLANE_REVISION = "0022_scim_provisioning_data_plane"
AUDIT_COMPLIANCE_REVISION = "0023_enterprise_audit_compliance"
OIDC_SSO_RUNTIME_REVISION = "0024_oidc_sso_runtime"
ENTERPRISE_APPROVAL_CONTROL_REVISION = "0025_enterprise_approval_control"
ENTERPRISE_WORKSPACE_CONTROL_REVISION = "0026_enterprise_workspace_control"
ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION = "0027_enterprise_workspace_authorization"
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION = "0028_enterprise_knowledge_base_registry"
ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION = "0029_enterprise_knowledge_base_releases"
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION = "0030_enterprise_release_quality_certification"
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION = "0031_enterprise_release_quality_operations"
ENTERPRISE_NOTIFICATION_CENTER_REVISION = "0032_enterprise_notification_center"
ENTERPRISE_CONTENT_RECOVERY_REVISION = "0033_enterprise_content_recovery"
ENTERPRISE_TASK_OPERATIONS_REVISION = "0034_enterprise_task_operations"
ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION = "0035_enterprise_automation_workflows"
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION = (
    "0036_enterprise_knowledge_serving_reliability"
)
QA_FAQ_OPS_REVISION = "0037_qa_faq_ops"
STORAGE_BACKENDS_REVISION = "0038_storage_backends"
ANSWER_EVIDENCE_FACTS_REVISION = "0039_answer_evidence_facts"
HEAD_REVISION = ANSWER_EVIDENCE_FACTS_REVISION
DATASET_ACL_CONTROL_REQUIRED_TABLES = frozenset({"dataset_acl_mutation_requests"})
DATASET_ACL_CONTROL_REQUIRED_COLUMNS = {
    "datasets": frozenset({"acl_mode", "acl_revision", "acl_enabled_at", "acl_enabled_by"}),
    "dataset_acl_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "resource_id",
            "response_json",
            "http_status",
            "created_at",
            "completed_at",
        }
    ),
}
DATASET_ACL_CONTROL_REQUIRED_NOT_NULL = {
    "datasets": frozenset({"acl_mode", "acl_revision"}),
    "dataset_acl_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "created_at",
        }
    ),
}
DATASET_ACL_CONTROL_REQUIRED_UNIQUES = {
    "dataset_acl_mutation_requests": {
        "uq_dataset_acl_mutation_requests_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
}
DATASET_ACL_CONTROL_REQUIRED_FOREIGN_KEYS = {
    "dataset_acl_mutation_requests": {
        "fk_dataset_acl_mutation_requests_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_acl_mutation_requests_scope_actor": (
            ("actor_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
}
DATASET_ACL_CONTROL_REQUIRED_CHECK_FRAGMENTS = {
    "datasets": {
        "ck_datasets_acl_mode": ("tenant_role", "dataset_acl"),
        "ck_datasets_acl_revision_positive": ("acl_revision > 0",),
    },
    "dataset_acl_mutation_requests": {
        "ck_dataset_acl_mutation_requests_status": (
            "pending",
            "completed",
            "failed",
        ),
        "ck_dataset_acl_mutation_requests_idempotency_key_length": (
            "length(idempotency_key)",
            "between 1 and 128",
        ),
    },
}
DATASET_ACL_CONTROL_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in DATASET_ACL_CONTROL_REQUIRED_CHECK_FRAGMENTS.items()
}
DATASET_ACL_CONTROL_REQUIRED_INDEXES = {
    "dataset_acl_mutation_requests": {
        "ix_dataset_acl_mutation_requests_tenant_dataset_status_created": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "id",
        ),
        "ix_dataset_acl_mutation_requests_tenant_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
}
DATASET_ACL_CONTROL_ISSUE_FRAGMENTS = (
    "datasets.acl_mode",
    "datasets.acl_revision",
    "datasets.acl_enabled_at",
    "datasets.acl_enabled_by",
    "datasets.ck_datasets_acl_mode",
    "datasets.ck_datasets_acl_revision_positive",
    "dataset_acl_mutation_requests.",
    "dataset_acl_mutation_requests.ck_dataset_acl_mutation_requests_idempotency_key_length",
)
TENANT_INVITATION_LIFECYCLE_REQUIRED_TABLES = frozenset({"tenant_control_mutation_requests"})
TENANT_INVITATION_LIFECYCLE_REQUIRED_COLUMNS = {
    "tenant_invitations": frozenset(
        {
            "pending_email_key",
            "last_sent_at",
            "send_count",
            "revoked_at",
            "revoked_by",
            "updated_by",
        }
    ),
    "tenant_control_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "resource_type",
            "resource_id",
            "status",
            "response_json",
            "http_status",
            "created_at",
            "completed_at",
        }
    ),
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_NOT_NULL = {
    "tenant_invitations": frozenset({"last_sent_at", "send_count", "updated_by"}),
    "tenant_control_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "resource_type",
            "status",
            "created_at",
        }
    ),
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_UNIQUES = {
    "tenant_invitations": {
        "uq_tenant_invitations_pending_email": ("tenant_id", "pending_email_key"),
        "uq_tenant_invitations_token_hash": ("tenant_id", "token_hash"),
    },
    "tenant_control_mutation_requests": {
        "uq_tenant_control_mutation_requests_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_FOREIGN_KEYS = {
    "tenant_invitations": {
        "fk_tenant_invitations_scope_revoker": (
            ("revoked_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_updater": (
            ("updated_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "tenant_control_mutation_requests": {
        "fk_tenant_control_mutation_requests_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_control_mutation_requests_actor": (
            ("actor_id",),
            "accounts",
            ("id",),
        ),
    },
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECK_FRAGMENTS = {
    "tenant_invitations": {
        "ck_tenant_invitations_send_count_positive": ("send_count > 0",),
        "ck_tenant_invitations_pending_email_key": (
            "status = 'pending'",
            "pending_email_key is not null",
            "pending_email_key = normalized_email",
            "status <> 'pending'",
            "pending_email_key is null",
        ),
        "ck_tenant_invitations_accepted_evidence": (
            "status <> 'accepted'",
            "accepted_at is not null",
            "accepted_by is not null",
        ),
        "ck_tenant_invitations_revoked_evidence": (
            "status <> 'revoked'",
            "revoked_at is not null",
            "revoked_by is not null",
        ),
    },
    "tenant_control_mutation_requests": {
        "ck_tenant_control_mutation_requests_status": (
            "pending",
            "completed",
            "failed",
        ),
        "ck_tenant_control_mutation_requests_idempotency_digest": ("length(idempotency_key) = 64",),
        "ck_tenant_control_mutation_requests_request_hash": ("length(request_hash) = 64",),
    },
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECK_FRAGMENTS.items()
}
TENANT_INVITATION_LIFECYCLE_REQUIRED_INDEXES = {
    "tenant_invitations": {
        "ix_tenant_invitations_tenant_status_updated": (
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    },
    "tenant_control_mutation_requests": {
        "ix_tenant_control_mutation_requests_tenant_status_created": (
            "tenant_id",
            "status",
            "created_at",
            "id",
        ),
        "ix_tenant_control_mutation_requests_tenant_actor_created": (
            "tenant_id",
            "actor_id",
            "created_at",
            "id",
        ),
        "ix_tenant_control_mutation_requests_tenant_resource_created": (
            "tenant_id",
            "resource_type",
            "resource_id",
            "created_at",
            "id",
        ),
    },
}
TENANT_INVITATION_LIFECYCLE_ISSUE_FRAGMENTS = (
    "tenant_invitations.pending_email_key",
    "tenant_invitations.last_sent_at",
    "tenant_invitations.send_count",
    "tenant_invitations.revoked_at",
    "tenant_invitations.revoked_by",
    "tenant_invitations.updated_by",
    "tenant_invitations.ck_tenant_invitations_pending_email_key",
    "tenant_invitations.ck_tenant_invitations_accepted_evidence",
    "tenant_invitations.ck_tenant_invitations_revoked_evidence",
    "tenant_invitations.ck_tenant_invitations_send_count_positive",
    "tenant_invitations.uq_tenant_invitations_pending_email",
    "tenant_invitations.uq_tenant_invitations_token_hash",
    "tenant_invitations.fk_tenant_invitations_scope_revoker",
    "tenant_invitations.fk_tenant_invitations_scope_updater",
    "tenant_invitations.ix_tenant_invitations_tenant_status_updated",
    "tenant_control_mutation_requests.",
)
BASELINE_CATALOG_TABLES = frozenset(
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
)
_DOCUMENT_LIFECYCLE_CHECK = (
    "lifecycle_state IN ('active', 'expired', 'delete_requested', "
    "'deleting', 'delete_failed', 'deleted')"
)

HEAD_CATALOG_TABLES = BASELINE_CATALOG_TABLES | frozenset(
    {
        "document_ingest_attempts",
        "document_ingest_spans",
        "index_operations",
        "index_dead_letters",
        "data_sources",
        "source_sync_runs",
        "source_schedules",
        "source_sync_items",
        "source_document_states",
        "chunk_heads",
        "chunk_revisions",
        "knowledge_folders",
        "knowledge_tags",
        "document_tags",
        "knowledge_audit_events",
        "tenant_audit_events",
        "tenant_organization_units",
        "tenant_organization_unit_members",
        "tenant_groups",
        "tenant_group_members",
        "dataset_access_grants",
        "tenant_invitations",
        "dataset_acl_mutation_requests",
        "tenant_control_mutation_requests",
        "document_versions",
        "qa_knowledge",
        "qa_alternative_questions",
        "qa_negative_questions",
        "storage_backends",
        "tenant_knowledge_answer_facts",
        "tenant_knowledge_answer_evidence_refs",
        "document_delete_batches",
        "document_delete_operations",
        "retrieval_experiments",
        "retrieval_judgments",
    }
)


_HEAD_REQUIRED_COLUMNS = {
    "tenant_organization_units": frozenset(
        {
            "id",
            "tenant_id",
            "parent_id",
            "name",
            "code",
            "status",
            "sort_order",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_organization_unit_members": frozenset(
        {
            "id",
            "tenant_id",
            "organization_unit_id",
            "account_id",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_groups": frozenset(
        {
            "id",
            "tenant_id",
            "name",
            "normalized_name",
            "description",
            "status",
            "revision",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_group_members": frozenset(
        {
            "id",
            "tenant_id",
            "group_id",
            "account_id",
            "status",
            "created_at",
            "created_by",
        }
    ),
    "dataset_access_grants": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
            "role",
            "status",
            "revision",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_invitations": frozenset(
        {
            "id",
            "tenant_id",
            "email",
            "normalized_email",
            "role",
            "status",
            "token_hash",
            "expires_at",
            "accepted_at",
            "accepted_by",
            "invited_by",
            "revision",
            "created_at",
            "updated_at",
        }
    )
    | TENANT_INVITATION_LIFECYCLE_REQUIRED_COLUMNS["tenant_invitations"],
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_COLUMNS[
        "tenant_control_mutation_requests"
    ],
    "tenant_members": frozenset(
        {
            "status",
            "revision",
            "updated_at",
            "updated_by",
            "suspended_at",
            "suspended_by",
        }
    ),
    "tenant_audit_events": frozenset(
        {
            "sequence",
            "id",
            "tenant_id",
            "actor_id",
            "actor_name_snapshot",
            "actor_email_snapshot",
            "action",
            "resource_type",
            "resource_id",
            "target_account_id",
            "before_snapshot",
            "after_snapshot",
            "request_id",
            "request_ip",
            "occurred_at",
        }
    ),
    "datasets": frozenset(
        {
            "profile_revision",
            "owner_id",
            "visibility",
            "profile_json",
            "parser_policy",
            "chunk_policy",
            "retrieval_policy",
            "retention_policy",
            "metadata_policy",
            "default_language",
            "graph_enabled",
            "qa_enabled",
            "archived_at",
            "archived_by",
            "updated_at",
            "mutation_generation",
            "serving_generation",
            "acl_mode",
            "acl_revision",
            "acl_enabled_at",
            "acl_enabled_by",
            "storage_backend_id",
        }
    ),
    "tenants": frozenset(
        {
            "default_storage_backend_id",
        }
    ),
    "dataset_acl_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "resource_id",
            "response_json",
            "http_status",
            "created_at",
            "completed_at",
        }
    ),
    "documents": frozenset(
        {
            "current_version_id",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
            "purge_after",
            "mutation_generation",
            "active_delete_operation_id",
            "deletion_requested_at",
            "deleted_at",
            "usage_released_at",
        }
    ),
    "document_ingest_attempts": frozenset({"attempt_kind", "document_generation"}),
    "index_operations": frozenset({"document_generation", "delete_operation_id"}),
    "data_sources": frozenset({"mutation_generation"}),
    "source_schedules": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "source_id",
            "revision",
            "status",
            "interval_seconds",
            "force_full",
            "next_run_at",
            "last_enqueued_at",
            "last_run_id",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        }
    ),
    "source_sync_runs": frozenset(
        {
            "source_generation",
            "dataset_generation",
            "pending_deletes",
            "idempotency_key",
            "request_hash",
            "retry_of_run_id",
            "execution_state",
            "execution_owner",
            "execution_lease_until",
            "execution_heartbeat_at",
            "execution_attempts",
            "execution_last_error",
            "execution_started_at",
            "execution_finished_at",
            "execution_next_attempt_at",
            "reservation_owner",
            "reservation_lease_until",
            "reservation_attempts",
            "schedule_id",
            "schedule_revision",
            "planned_at",
        }
    ),
    "source_document_states": frozenset(
        {"state", "document_generation", "delete_operation_id", "suppressed_at"}
    ),
    "document_delete_batches": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "idempotency_key",
            "request_hash",
            "actor_id",
            "request_id",
            "reason",
            "status",
            "requested_count",
            "accepted_count",
            "completed_count",
            "failed_count",
            "rejected_count",
            "created_at",
            "updated_at",
            "finished_at",
        }
    ),
    "document_delete_operations": frozenset(
        {
            "id",
            "batch_id",
            "request_index",
            "tenant_id",
            "dataset_id",
            "requested_document_id",
            "document_id",
            "expected_generation",
            "delete_generation",
            "attempt_id",
            "origin",
            "status",
            "result_code",
            "result_message",
            "chunk_manifest_count",
            "chunk_manifest_hash",
            "quota_chunk_count",
            "required_store_count",
            "completed_store_count",
            "failed_store_count",
            "requested_by",
            "request_id",
            "reason",
            "started_at",
            "finalized_at",
            "created_at",
            "updated_at",
        }
    ),
    "document_versions": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
            "source_identity",
            "source_hash",
            "parser_policy_snapshot",
            "parser_metadata",
            "source_content_ref",
            "created_by",
            "change_reason",
            "created_at",
        }
    ),
    "qa_knowledge": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "revision",
            "question",
            "answer",
            "origin",
            "review_status",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
            "source_document_id",
            "source_uri",
            "metadata",
            "created_by",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
            "content_hash",
            "import_batch_id",
        }
    ),
    "qa_alternative_questions": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "qa_id",
            "question",
            "normalized_hash",
            "created_by",
            "created_at",
        }
    ),
    "qa_negative_questions": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "qa_id",
            "question",
            "normalized_hash",
            "created_by",
            "created_at",
        }
    ),
    "storage_backends": frozenset(
        {
            "id",
            "tenant_id",
            "name",
            "provider",
            "config",
            "status",
            "source",
            "is_deleted",
            "deleted_at",
            "created_by",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_knowledge_answer_facts": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "run_id",
            "request_id_digest",
            "query_digest",
            "answer_digest",
            "evidence_chain_digest",
            "fact_digest",
            "safe_query_preview",
            "outcome_code",
            "route_code",
            "citation_count",
            "evidence_count",
            "observed_at",
        }
    ),
    "tenant_knowledge_answer_evidence_refs": frozenset(
        {
            "id",
            "tenant_id",
            "answer_fact_id",
            "seq",
            "chunk_id",
            "chunk_revision_id",
            "document_id",
            "citation_status",
            "evidence_digest",
            "created_at",
        }
    ),
    "retrieval_experiments": frozenset(
        {
            "sequence",
            "id",
            "tenant_id",
            "dataset_id",
            "query",
            "query_hash",
            "strategy_snapshot",
            "result_snapshot",
            "evidence_lineage",
            "latency_ms",
            "status",
            "created_by",
            "created_at",
            "run_id",
        }
    ),
    "retrieval_judgments": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
            "document_id",
            "chunk_id",
            "relevance_label",
            "score",
            "note",
            "revision",
            "created_by",
            "created_at",
        }
    ),
}
_HEAD_REQUIRED_NOT_NULL = {
    "tenant_organization_units": frozenset(
        {
            "id",
            "tenant_id",
            "name",
            "code",
            "status",
            "sort_order",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_organization_unit_members": frozenset(
        {
            "id",
            "tenant_id",
            "organization_unit_id",
            "account_id",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_groups": frozenset(
        {
            "id",
            "tenant_id",
            "name",
            "normalized_name",
            "description",
            "status",
            "revision",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_group_members": frozenset(
        {
            "id",
            "tenant_id",
            "group_id",
            "account_id",
            "status",
            "created_at",
            "created_by",
        }
    ),
    "dataset_access_grants": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
            "role",
            "status",
            "revision",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_invitations": frozenset(
        {
            "id",
            "tenant_id",
            "email",
            "normalized_email",
            "role",
            "status",
            "token_hash",
            "expires_at",
            "invited_by",
            "revision",
            "created_at",
            "updated_at",
        }
    )
    | TENANT_INVITATION_LIFECYCLE_REQUIRED_NOT_NULL["tenant_invitations"],
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_NOT_NULL[
        "tenant_control_mutation_requests"
    ],
    "tenant_members": frozenset({"status", "revision", "updated_at", "updated_by"}),
    "tenant_audit_events": frozenset(
        {
            "sequence",
            "id",
            "tenant_id",
            "actor_id",
            "actor_name_snapshot",
            "actor_email_snapshot",
            "action",
            "resource_type",
            "resource_id",
            "request_id",
            "request_ip",
            "occurred_at",
        }
    ),
    "datasets": frozenset(
        {
            "profile_revision",
            "visibility",
            "profile_json",
            "parser_policy",
            "chunk_policy",
            "retrieval_policy",
            "retention_policy",
            "metadata_policy",
            "default_language",
            "graph_enabled",
            "qa_enabled",
            "updated_at",
            "mutation_generation",
            "serving_generation",
            "acl_mode",
            "acl_revision",
        }
    ),
    "dataset_acl_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "created_at",
        }
    ),
    "documents": frozenset({"mutation_generation"}),
    "document_ingest_attempts": frozenset({"attempt_kind", "document_generation"}),
    "index_operations": frozenset({"document_generation"}),
    "data_sources": frozenset({"mutation_generation"}),
    "source_schedules": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "source_id",
            "revision",
            "status",
            "interval_seconds",
            "force_full",
            "next_run_at",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        }
    ),
    "source_sync_runs": frozenset(
        {
            "source_generation",
            "dataset_generation",
            "pending_deletes",
            "execution_state",
            "execution_owner",
            "execution_attempts",
            "execution_last_error",
            "reservation_owner",
            "reservation_attempts",
        }
    ),
    "source_document_states": frozenset({"state", "document_generation"}),
    "document_delete_batches": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "idempotency_key",
            "request_hash",
            "actor_id",
            "request_id",
            "reason",
            "status",
            "requested_count",
            "accepted_count",
            "completed_count",
            "failed_count",
            "rejected_count",
            "created_at",
            "updated_at",
        }
    ),
    "document_delete_operations": frozenset(
        {
            "id",
            "request_index",
            "tenant_id",
            "dataset_id",
            "requested_document_id",
            "origin",
            "status",
            "result_code",
            "result_message",
            "chunk_manifest_count",
            "chunk_manifest_hash",
            "quota_chunk_count",
            "required_store_count",
            "completed_store_count",
            "failed_store_count",
            "requested_by",
            "request_id",
            "reason",
            "created_at",
            "updated_at",
        }
    ),
    "retrieval_experiments": frozenset(
        {
            "sequence",
            "id",
            "tenant_id",
            "dataset_id",
            "query",
            "query_hash",
            "strategy_snapshot",
            "result_snapshot",
            "evidence_lineage",
            "latency_ms",
            "status",
            "created_by",
            "created_at",
        }
    ),
    "retrieval_judgments": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
            "relevance_label",
            "note",
            "revision",
            "created_by",
            "created_at",
        }
    ),
}

_HEAD_REQUIRED_UNIQUES = {
    "tenant_organization_units": {
        "uq_tenant_organization_units_scope_id": ("tenant_id", "id"),
        "uq_tenant_organization_units_tenant_code": ("tenant_id", "code"),
        "uq_tenant_organization_units_tenant_parent_name": (
            "tenant_id",
            "parent_id",
            "name",
        ),
    },
    "tenant_organization_unit_members": {
        "uq_tenant_organization_unit_members_unit_account": (
            "tenant_id",
            "organization_unit_id",
            "account_id",
        ),
    },
    "tenant_groups": {
        "uq_tenant_groups_scope_id": ("tenant_id", "id"),
        "uq_tenant_groups_tenant_normalized_name": (
            "tenant_id",
            "normalized_name",
        ),
    },
    "tenant_group_members": {
        "uq_tenant_group_members_group_account": (
            "tenant_id",
            "group_id",
            "account_id",
        ),
    },
    "dataset_access_grants": {
        "uq_dataset_access_grants_dataset_subject": (
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
        ),
    },
    "tenant_invitations": TENANT_INVITATION_LIFECYCLE_REQUIRED_UNIQUES["tenant_invitations"],
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_UNIQUES[
        "tenant_control_mutation_requests"
    ],
    "dataset_acl_mutation_requests": {
        "uq_dataset_acl_mutation_requests_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
    "tenant_members": {
        "uq_tenant_members_account_tenant": ("account_id", "tenant_id"),
    },
    "tenant_audit_events": {
        "uq_tenant_audit_events_id": ("id",),
    },
    "document_segments": {
        "uq_document_segments_document_seq": ("document_id", "seq"),
    },
    "metadata_fields": {
        "uq_metadata_fields_dataset_key": ("dataset_id", "key"),
    },
    "documents": {
        "uq_documents_dataset_source_uri_hash": ("dataset_id", "source_uri_hash"),
        "uq_documents_dataset_source_external": ("dataset_id", "source_id", "external_id"),
    },
    "data_sources": {
        "uq_data_sources_scope_id": ("tenant_id", "dataset_id", "id"),
    },
    "source_schedules": {
        "uq_source_schedules_source": ("source_id",),
        "uq_source_schedules_scope_id": ("tenant_id", "dataset_id", "source_id", "id"),
    },
    "source_sync_runs": {
        "uq_source_sync_runs_idempotency": (
            "source_id",
            "source_generation",
            "dataset_generation",
            "idempotency_key",
        ),
        "uq_source_sync_runs_retry_of": ("retry_of_run_id",),
    },
    "document_ingest_attempts": {
        "uq_ingest_attempt_scope_id": ("tenant_id", "dataset_id", "id"),
    },
    "chunk_heads": {
        "uq_chunk_heads_scope_id": ("tenant_id", "dataset_id", "id"),
    },
    "document_versions": {
        "uq_document_versions_scope_id": ("tenant_id", "dataset_id", "document_id", "id"),
        "uq_document_versions_document_revision": (
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
        ),
    },
    "qa_knowledge": {"uq_qa_knowledge_scope_id": ("tenant_id", "dataset_id", "id")},
    "qa_alternative_questions": {
        "uq_qa_alternatives_qa_normalized_hash": (
            "tenant_id",
            "dataset_id",
            "qa_id",
            "normalized_hash",
        )
    },
    "qa_negative_questions": {
        "uq_qa_negative_questions_qa_normalized_hash": (
            "tenant_id",
            "dataset_id",
            "qa_id",
            "normalized_hash",
        )
    },
    "storage_backends": {
        "uq_storage_backends_tenant_id": ("tenant_id", "id"),
    },
    "tenant_knowledge_answer_facts": {
        "uq_answer_facts_tenant_id": ("tenant_id", "id"),
    },
    "tenant_knowledge_answer_evidence_refs": {
        "uq_answer_evidence_refs_tenant_id": ("tenant_id", "id"),
    },
    "document_delete_batches": {
        "uq_document_delete_batches_scope_id": ("tenant_id", "dataset_id", "id"),
        "uq_document_delete_batches_idempotency": ("tenant_id", "dataset_id", "idempotency_key"),
    },
    "document_delete_operations": {
        "uq_document_delete_operations_scope_id": ("tenant_id", "dataset_id", "id"),
        "uq_document_delete_operations_generation": ("document_id", "delete_generation"),
        "uq_document_delete_operations_document_id": ("document_id", "id"),
        "uq_document_delete_operations_batch_index": ("batch_id", "request_index"),
    },
    "retrieval_experiments": {
        "uq_retrieval_experiments_id": ("id",),
        "uq_retrieval_experiments_scope_id": ("tenant_id", "dataset_id", "id"),
    },
    "retrieval_judgments": {
        "uq_retrieval_judgments_experiment_rank_judge": (
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
            "created_by",
        ),
    },
}
_HEAD_REQUIRED_FOREIGN_KEYS = {
    "tenant_organization_units": {
        "fk_tenant_organization_units_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_organization_units_scope_parent": (
            ("tenant_id", "parent_id"),
            "tenant_organization_units",
            ("tenant_id", "id"),
        ),
    },
    "tenant_organization_unit_members": {
        "fk_tenant_organization_unit_members_scope_unit": (
            ("tenant_id", "organization_unit_id"),
            "tenant_organization_units",
            ("tenant_id", "id"),
        ),
        "fk_tenant_organization_unit_members_scope_account": (
            ("account_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "tenant_groups": {
        "fk_tenant_groups_tenant": (("tenant_id",), "tenants", ("id",)),
    },
    "tenant_group_members": {
        "fk_tenant_group_members_scope_group": (
            ("tenant_id", "group_id"),
            "tenant_groups",
            ("tenant_id", "id"),
        ),
        "fk_tenant_group_members_scope_account": (
            ("account_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "dataset_access_grants": {
        "fk_dataset_access_grants_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
    },
    "tenant_invitations": {
        "fk_tenant_invitations_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_invitations_scope_inviter": (
            ("invited_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_acceptor": (
            ("accepted_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        **TENANT_INVITATION_LIFECYCLE_REQUIRED_FOREIGN_KEYS["tenant_invitations"],
    },
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_FOREIGN_KEYS[
        "tenant_control_mutation_requests"
    ],
    "dataset_acl_mutation_requests": {
        "fk_dataset_acl_mutation_requests_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_acl_mutation_requests_scope_actor": (
            ("actor_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "tenant_audit_events": {
        "fk_tenant_audit_events_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
    },
    "source_schedules": {
        "fk_source_schedules_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_source_schedules_scope_source": (
            ("tenant_id", "dataset_id", "source_id"),
            "data_sources",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_source_schedules_last_run": (("last_run_id",), "source_sync_runs", ("id",)),
    },
    "source_sync_runs": {
        "fk_source_sync_runs_retry_of": (("retry_of_run_id",), "source_sync_runs", ("id",)),
        "fk_source_sync_runs_schedule": (
            ("tenant_id", "dataset_id", "source_id", "schedule_id"),
            "source_schedules",
            ("tenant_id", "dataset_id", "source_id", "id"),
        ),
    },
    "datasets": {
        "fk_datasets_scope_owner_member": (
            ("owner_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "documents": {
        "fk_documents_scope_current_version": (
            ("tenant_id", "dataset_id", "id", "current_version_id"),
            "document_versions",
            ("tenant_id", "dataset_id", "document_id", "id"),
        ),
        "fk_documents_scope_active_delete_operation": (
            ("tenant_id", "dataset_id", "active_delete_operation_id"),
            "document_delete_operations",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "document_versions": {
        "fk_document_versions_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_document_versions_scope_document": (
            ("tenant_id", "dataset_id", "document_id"),
            "documents",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "qa_knowledge": {
        "fk_qa_knowledge_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_qa_knowledge_scope_document": (
            ("tenant_id", "dataset_id", "source_document_id"),
            "documents",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "qa_alternative_questions": {
        "fk_qa_alternatives_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_qa_alternatives_scope_qa": (
            ("tenant_id", "dataset_id", "qa_id"),
            "qa_knowledge",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "qa_negative_questions": {
        "fk_qa_negative_questions_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_qa_negative_questions_scope_qa": (
            ("tenant_id", "dataset_id", "qa_id"),
            "qa_knowledge",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "storage_backends": {
        "fk_storage_backends_tenant": (("tenant_id",), "tenants", ("id",)),
    },
    "index_operations": {
        "fk_index_operations_scope_delete_operation": (
            ("tenant_id", "dataset_id", "delete_operation_id"),
            "document_delete_operations",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "source_document_states": {
        "fk_source_document_states_scope_delete_operation": (
            ("doc_id", "delete_operation_id"),
            "document_delete_operations",
            ("document_id", "id"),
        ),
    },
    "document_delete_batches": {
        "fk_document_delete_batches_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
    },
    "retrieval_experiments": {
        "fk_retrieval_experiments_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
    },
    "retrieval_judgments": {
        "fk_retrieval_judgments_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_retrieval_judgments_scope_experiment": (
            ("tenant_id", "dataset_id", "experiment_id"),
            "retrieval_experiments",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_retrieval_judgments_scope_document": (
            ("tenant_id", "dataset_id", "document_id"),
            "documents",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_retrieval_judgments_scope_chunk": (
            ("tenant_id", "dataset_id", "chunk_id"),
            "chunk_heads",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
    "document_delete_operations": {
        "fk_document_delete_operations_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_document_delete_operations_scope_batch": (
            ("tenant_id", "dataset_id", "batch_id"),
            "document_delete_batches",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_document_delete_operations_scope_document": (
            ("tenant_id", "dataset_id", "document_id"),
            "documents",
            ("tenant_id", "dataset_id", "id"),
        ),
        "fk_document_delete_operations_scope_attempt": (
            ("tenant_id", "dataset_id", "attempt_id"),
            "document_ingest_attempts",
            ("tenant_id", "dataset_id", "id"),
        ),
    },
}
_HEAD_REQUIRED_CHECK_FRAGMENTS = {
    "tenant_organization_units": {
        "ck_tenant_organization_units_status": ("active", "archived"),
        "ck_tenant_organization_units_revision_positive": ("revision > 0",),
    },
    "tenant_organization_unit_members": {
        "ck_tenant_organization_unit_members_status": ("active", "removed"),
        "ck_tenant_organization_unit_members_revision_positive": ("revision > 0",),
    },
    "tenant_groups": {
        "ck_tenant_groups_status": ("active", "archived"),
        "ck_tenant_groups_revision_positive": ("revision > 0",),
    },
    "tenant_group_members": {
        "ck_tenant_group_members_status": ("active", "removed"),
    },
    "dataset_access_grants": {
        "ck_dataset_access_grants_subject_type": (
            "account",
            "group",
            "organization_unit",
        ),
        "ck_dataset_access_grants_role": ("viewer", "editor", "manager"),
        "ck_dataset_access_grants_status": ("active", "revoked"),
        "ck_dataset_access_grants_revision_positive": ("revision > 0",),
    },
    "tenant_invitations": {
        "ck_tenant_invitations_role": ("owner", "admin", "editor", "member"),
        "ck_tenant_invitations_status": (
            "pending",
            "accepted",
            "revoked",
            "expired",
        ),
        "ck_tenant_invitations_revision_positive": ("revision > 0",),
        **TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECK_FRAGMENTS["tenant_invitations"],
    },
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECK_FRAGMENTS[
        "tenant_control_mutation_requests"
    ],
    "tenant_members": {
        "ck_tenant_members_role": ("owner", "admin", "editor", "member"),
        "ck_tenant_members_status": ("active", "suspended"),
        "ck_tenant_members_revision_positive": ("revision > 0",),
    },
    "source_schedules": {
        "ck_source_schedules_revision_positive": ("revision > 0",),
        "ck_source_schedules_status": ("active", "paused", "archived"),
        "ck_source_schedules_interval": ("interval_seconds >= 300", "interval_seconds <= 604800"),
        "ck_source_schedules_force_full": ("force_full", "0", "1"),
    },
    "datasets": {
        "ck_datasets_profile_revision_positive": ("profile_revision > 0",),
        "ck_datasets_visibility": ("private", "tenant", "public"),
        "ck_datasets_status": ("active", "archived", "disabled"),
        "ck_datasets_generations_nonnegative": (
            "mutation_generation >= 0",
            "serving_generation >= 0",
        ),
        "ck_datasets_acl_mode": ("tenant_role", "dataset_acl"),
        "ck_datasets_acl_revision_positive": ("acl_revision > 0",),
    },
    "dataset_acl_mutation_requests": {
        "ck_dataset_acl_mutation_requests_status": (
            "pending",
            "completed",
            "failed",
        ),
        "ck_dataset_acl_mutation_requests_idempotency_key_length": (
            "length(idempotency_key)",
            "between 1 and 128",
        ),
    },
    "documents": {
        "ck_documents_lifecycle_state": ("active", "expired", "delete_requested", "deleted"),
        "ck_documents_retrieval_lifecycle": ("active", "not retrieval_enabled"),
        "ck_documents_mutation_generation_nonnegative": ("mutation_generation >= 0",),
    },
    "document_versions": {"ck_document_versions_revision_positive": ("revision > 0",)},
    "document_ingest_attempts": {
        "ck_ingest_attempt_kind": ("document_delete", "chunk_mutation", "restore"),
        "ck_ingest_attempt_document_generation_nonnegative": ("document_generation >= 0",),
    },
    "index_operations": {
        "ck_index_operations_document_generation_nonnegative": ("document_generation >= 0",),
    },
    "data_sources": {
        "ck_data_sources_mutation_generation_nonnegative": ("mutation_generation >= 0",),
    },
    "source_sync_runs": {
        "ck_source_sync_runs_generations_counts": (
            "source_generation >= 0",
            "dataset_generation >= 0",
            "pending_deletes >= 0",
        ),
        "ck_source_sync_runs_idempotency_pair": (
            "idempotency_key is null",
            "request_hash is null",
            "idempotency_key is not null",
            "request_hash is not null",
        ),
        "ck_source_sync_runs_retry_not_self": ("retry_of_run_id", "<> id"),
        "ck_source_sync_runs_retry_trigger": ("trigger = 'retry'", "retry_of_run_id"),
        "ck_source_sync_runs_schedule_revision": ("schedule_revision", "> 0"),
        "ck_source_sync_runs_schedule_metadata": (
            "trigger = 'scheduled'",
            "schedule_id is not null",
            "schedule_revision is not null",
            "planned_at is not null",
            "trigger <> 'scheduled'",
            "schedule_id is null",
        ),
        "ck_source_sync_runs_execution_state": (
            "pending",
            "executing",
            "failed",
            "completed",
        ),
        "ck_source_sync_runs_execution_attempts": ("execution_attempts >= 0",),
        "ck_source_sync_runs_execution_owner": (
            "execution_state = 'executing'",
            "execution_owner",
            "execution_lease_until",
            "execution_heartbeat_at",
        ),
        "ck_source_sync_runs_reservation_attempts": ("reservation_attempts >= 0",),
        "ck_source_sync_runs_reservation_owner": (
            "reservation_owner",
            "reservation_lease_until",
        ),
    },
    "source_document_states": {
        "ck_source_document_states_state": (
            "active",
            "operator_suppressed",
            "upstream_absent",
            "delete_pending",
        ),
        "ck_source_document_states_document_generation_nonnegative": ("document_generation >= 0",),
    },
    "document_delete_batches": {
        "ck_document_delete_batches_status": (
            "preparing",
            "running",
            "completed",
            "partially_failed",
            "failed",
        ),
        "ck_document_delete_batches_counts": ("requested_count >= 0", "rejected_count >= 0"),
    },
    "document_delete_operations": {
        "ck_document_delete_operations_origin": (
            "operator",
            "source_sync",
            "dataset_reset",
            "retention",
        ),
        "ck_document_delete_operations_status": (
            "rejected",
            "queued",
            "projecting",
            "finalizing",
            "completed",
            "failed",
        ),
        "ck_document_delete_operations_counts": (
            "request_index >= 0",
            "chunk_manifest_count >= 0",
            "quota_chunk_count >= 0",
            "required_store_count >= 0",
            "completed_store_count >= 0",
            "failed_store_count >= 0",
            "completed_store_count + failed_store_count <= required_store_count",
        ),
        "ck_document_delete_operations_generations": ("expected_generation", "delete_generation"),
    },
    "retrieval_experiments": {
        "ck_retrieval_experiments_status": ("completed", "failed"),
        "ck_retrieval_experiments_latency_nonnegative": ("latency_ms >= 0",),
    },
    "retrieval_judgments": {
        "ck_retrieval_judgments_rank_positive": ("result_rank > 0",),
        "ck_retrieval_judgments_relevance_label": ("relevant", "partial", "irrelevant"),
        "ck_retrieval_judgments_score": ("score is null", "score >= 0", "score <= 3"),
        "ck_retrieval_judgments_revision_positive": ("revision > 0",),
    },
    "qa_knowledge": {
        "ck_qa_knowledge_revision_positive": ("revision > 0",),
        "ck_qa_knowledge_review_status": ("pending", "approved", "rejected"),
        "ck_qa_knowledge_lifecycle_state": ("active", "expired", "delete_requested", "deleted"),
        "ck_qa_knowledge_retrieval_lifecycle": ("active", "not retrieval_enabled"),
        "ck_qa_knowledge_review_retrieval": ("approved", "not retrieval_enabled"),
        "ck_qa_knowledge_origin": ("manual", "automatic", "import"),
    },
    "storage_backends": {
        "ck_storage_backends_provider": (
            "local",
            "minio",
            "s3",
            "cos",
            "oss",
            "tos",
            "obs",
        ),
        "ck_storage_backends_status": ("active", "disabled"),
        "ck_storage_backends_source": ("user", "env"),
        "ck_storage_backends_is_deleted": ("0", "1"),
    },
    "tenant_knowledge_answer_facts": {
        "ck_answer_facts_outcome": (
            "answered",
            "abstained",
            "cancelled",
            "failed",
            "cached",
        ),
        "ck_answer_facts_citation_count": ("citation_count >= 0",),
        "ck_answer_facts_evidence_count": ("evidence_count >= 0",),
    },
    "tenant_knowledge_answer_evidence_refs": {
        "ck_answer_evidence_seq_nonneg": ("seq >= 0",),
    },
}
_HEAD_REQUIRED_INDEXES = {
    "tenant_organization_units": {
        "ix_tenant_organization_units_tenant_parent_status": (
            "tenant_id",
            "parent_id",
            "status",
            "sort_order",
            "id",
        ),
        "ix_tenant_organization_units_tenant_status": (
            "tenant_id",
            "status",
            "sort_order",
            "id",
        ),
    },
    "tenant_organization_unit_members": {
        "ix_tenant_organization_unit_members_tenant_unit_status": (
            "tenant_id",
            "organization_unit_id",
            "status",
            "id",
        ),
        "ix_tenant_organization_unit_members_tenant_account_status": (
            "tenant_id",
            "account_id",
            "status",
            "id",
        ),
    },
    "tenant_groups": {
        "ix_tenant_groups_tenant_status_name": (
            "tenant_id",
            "status",
            "normalized_name",
            "id",
        ),
    },
    "tenant_group_members": {
        "ix_tenant_group_members_tenant_account": (
            "tenant_id",
            "account_id",
            "id",
        ),
        "ix_tenant_group_members_tenant_group": (
            "tenant_id",
            "group_id",
            "id",
        ),
    },
    "dataset_access_grants": {
        "ix_dataset_access_grants_tenant_dataset_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        "ix_dataset_access_grants_tenant_subject": (
            "tenant_id",
            "subject_type",
            "subject_id",
            "id",
        ),
    },
    "tenant_invitations": {
        "ix_tenant_invitations_tenant_status_expires": (
            "tenant_id",
            "status",
            "expires_at",
            "id",
        ),
        "ix_tenant_invitations_tenant_email": (
            "tenant_id",
            "normalized_email",
            "id",
        ),
        **TENANT_INVITATION_LIFECYCLE_REQUIRED_INDEXES["tenant_invitations"],
    },
    "tenant_control_mutation_requests": TENANT_INVITATION_LIFECYCLE_REQUIRED_INDEXES[
        "tenant_control_mutation_requests"
    ],
    "dataset_acl_mutation_requests": {
        "ix_dataset_acl_mutation_requests_tenant_dataset_status_created": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "id",
        ),
        "ix_dataset_acl_mutation_requests_tenant_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
    "tenant_members": {
        "ix_tenant_members_tenant_status_role_id": ("tenant_id", "status", "role", "id"),
        "ix_tenant_members_tenant_account_status": ("tenant_id", "account_id", "status"),
    },
    "tenant_audit_events": {
        "ix_tenant_audit_events_tenant_id": ("tenant_id",),
        "ix_tenant_audit_events_actor_id": ("actor_id",),
        "ix_tenant_audit_events_action": ("action",),
        "ix_tenant_audit_events_resource_type": ("resource_type",),
        "ix_tenant_audit_events_resource_id": ("resource_id",),
        "ix_tenant_audit_events_target_account_id": ("target_account_id",),
        "ix_tenant_audit_scope_sequence": ("tenant_id", "sequence"),
        "ix_tenant_audit_scope_time": ("tenant_id", "occurred_at", "sequence"),
        "ix_tenant_audit_actor_time": ("tenant_id", "actor_id", "occurred_at", "sequence"),
        "ix_tenant_audit_resource_time": (
            "tenant_id",
            "resource_type",
            "resource_id",
            "occurred_at",
            "sequence",
        ),
        "ix_tenant_audit_target_time": (
            "tenant_id",
            "target_account_id",
            "occurred_at",
            "sequence",
        ),
        "ix_tenant_audit_request": ("tenant_id", "request_id"),
    },
    "source_sync_runs": {
        "ix_source_sync_runs_schedule_planned": ("schedule_id", "schedule_revision", "planned_at"),
    },
    "source_schedules": {
        "ix_source_schedules_due": ("status", "next_run_at", "id"),
        "ix_source_schedules_status": ("tenant_id", "dataset_id", "status", "id"),
        "ix_source_schedules_scope": ("tenant_id", "dataset_id", "source_id"),
    },
    "datasets": {
        "ix_datasets_scope_status": ("tenant_id", "status", "id"),
        "ix_datasets_scope_visibility": ("tenant_id", "visibility", "id"),
        "ix_datasets_scope_owner": ("tenant_id", "owner_id", "id"),
        "ix_datasets_scope_updated": ("tenant_id", "updated_at", "id"),
    },
    "documents": {
        "ix_documents_current_version_id": ("current_version_id",),
        "ix_documents_scope_effective": (
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
        ),
        "ix_documents_scope_lifecycle": ("tenant_id", "dataset_id", "lifecycle_state", "id"),
        "ix_documents_scope_retrieval": ("tenant_id", "dataset_id", "retrieval_enabled", "id"),
        "ix_documents_active_delete_operation": ("active_delete_operation_id",),
        "ix_documents_catalog_scope_updated": ("tenant_id", "dataset_id", "updated_at", "id"),
        "ix_documents_catalog_scope_status_updated": (
            "tenant_id",
            "dataset_id",
            "status",
            "updated_at",
            "id",
        ),
        "ix_documents_catalog_scope_doc_type_updated": (
            "tenant_id",
            "dataset_id",
            "doc_type",
            "updated_at",
            "id",
        ),
        "ix_documents_catalog_scope_folder_updated": (
            "tenant_id",
            "dataset_id",
            "logical_folder_path",
            "updated_at",
            "id",
        ),
    },
    "document_ingest_attempts": {
        "ix_ingest_attempt_document_generation": (
            "document_id",
            "document_generation",
            "attempt_kind",
        ),
    },
    "index_operations": {
        "ix_index_operations_delete_status": ("delete_operation_id", "status"),
        "ix_index_operations_document_generation": ("document_id", "document_generation", "status"),
    },
    "source_document_states": {
        "ix_source_document_state_delete_operation": ("delete_operation_id",),
    },
    "document_delete_batches": {
        "ix_document_delete_batches_scope_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
        ),
    },
    "document_delete_operations": {
        "ix_document_delete_operations_batch_status": ("batch_id", "status"),
        "ix_document_delete_operations_document_status": ("document_id", "status"),
        "ix_document_delete_operations_scope_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
        ),
    },
    "retrieval_experiments": {
        "ix_retrieval_experiments_scope_sequence": ("tenant_id", "dataset_id", "sequence"),
        "ix_retrieval_experiments_scope_query_hash": (
            "tenant_id",
            "dataset_id",
            "query_hash",
            "sequence",
        ),
        "ix_retrieval_experiments_scope_status_time": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "sequence",
        ),
        "ix_retrieval_experiments_run_id": ("run_id",),
        "ix_retrieval_experiments_scope_run": ("tenant_id", "dataset_id", "run_id", "sequence"),
    },
    "retrieval_judgments": {
        "ix_retrieval_judgments_scope_experiment_rank": (
            "tenant_id",
            "dataset_id",
            "experiment_id",
            "result_rank",
        ),
        "ix_retrieval_judgments_scope_document": ("tenant_id", "dataset_id", "document_id"),
        "ix_retrieval_judgments_scope_chunk": ("tenant_id", "dataset_id", "chunk_id"),
    },
    "document_versions": {
        "ix_document_versions_scope_revision": (
            "tenant_id",
            "dataset_id",
            "document_id",
            "revision",
        ),
        "ix_document_versions_scope_hash": ("tenant_id", "dataset_id", "source_hash"),
    },
    "qa_knowledge": {
        "ix_qa_knowledge_scope_effective": (
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "retrieval_enabled",
            "effective_from",
            "expires_at",
        ),
        "ix_qa_knowledge_scope_review": ("tenant_id", "dataset_id", "review_status"),
        "ix_qa_knowledge_scope_content_hash": (
            "tenant_id",
            "dataset_id",
            "content_hash",
        ),
    },
    "qa_alternative_questions": {
        "ix_qa_alternatives_scope_qa": ("tenant_id", "dataset_id", "qa_id")
    },
    "qa_negative_questions": {
        "ix_qa_negative_questions_scope_qa": ("tenant_id", "dataset_id", "qa_id")
    },
    "storage_backends": {
        "ix_storage_backends_tenant_live": ("tenant_id", "is_deleted", "name"),
        "ix_storage_backends_tenant_provider": ("tenant_id", "provider"),
    },
    "tenant_knowledge_answer_facts": {
        "ix_answer_facts_scope_observed": ("tenant_id", "dataset_id", "observed_at"),
        "ix_answer_facts_tenant_run": ("tenant_id", "run_id"),
    },
    "tenant_knowledge_answer_evidence_refs": {
        "ix_answer_evidence_fact_seq": ("answer_fact_id", "seq"),
        "ix_answer_evidence_tenant_fact": ("tenant_id", "answer_fact_id"),
    },
}

_REQUIRED_RETRIEVAL_EXPERIMENT_TRIGGERS = frozenset(
    {
        "trg_retrieval_experiments_no_update",
        "trg_retrieval_experiments_no_delete",
    }
)


def _canonical_trigger_sql(value: str | None) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("`", "").replace('"', "").replace(";", " ").strip().casefold(),
    )


def _canonical_postgresql_function(value: str | None) -> str:
    sql = _canonical_trigger_sql(value)
    sql = re.sub(r"create or replace function", "create function", sql)
    sql = re.sub(
        r"create function (?:[a-z0-9_]+\.)?rag4c_retrieval_experiments_immutable\(\)",
        "create function rag4c_retrieval_experiments_immutable()",
        sql,
    )
    sql = re.sub(r"\$[a-z0-9_]*\$", "$$", sql)
    return sql


def _trigger_contract_issues(
    dialect: str,
    rows: list[tuple[Any, ...]],
    function_definition: str | None = None,
) -> tuple[str, ...]:
    issues: list[str] = []
    if dialect == "sqlite":
        actual = {str(row[0]).casefold(): _canonical_trigger_sql(row[1]) for row in rows}
        for event in ("update", "delete"):
            name = f"trg_retrieval_experiments_no_{event}"
            expected = _canonical_trigger_sql(
                f"CREATE TRIGGER {name} BEFORE {event.upper()} ON retrieval_experiments "
                "BEGIN SELECT RAISE(ABORT, 'retrieval_experiments are immutable'); END"
            )
            if actual.get(name) != expected:
                issues.append(f"missing or invalid immutable trigger retrieval_experiments.{name}")
        return tuple(issues)

    actual = {str(row[0]).casefold(): row for row in rows}
    mysql_action = _canonical_trigger_sql(
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'retrieval_experiments are immutable'"
    )
    postgres_action = _canonical_trigger_sql(
        "EXECUTE FUNCTION rag4c_retrieval_experiments_immutable()"
    )
    for event in ("update", "delete"):
        name = f"trg_retrieval_experiments_no_{event}"
        row = actual.get(name)
        if row is None or len(row) != 6:
            issues.append(f"missing or invalid immutable trigger retrieval_experiments.{name}")
            continue
        timing = str(row[1]).casefold()
        manipulation = str(row[2]).casefold()
        action = _canonical_trigger_sql(row[3])
        condition = _canonical_trigger_sql(row[4])
        enabled = str(row[5]).casefold()
        valid = timing == "before" and manipulation == event and not condition
        if dialect in {"mysql", "mariadb"}:
            valid = valid and enabled == "enabled" and action == mysql_action
        elif dialect == "postgresql":
            valid = valid and enabled == "o" and action == postgres_action
        else:
            valid = False
        if not valid:
            issues.append(f"missing or invalid immutable trigger retrieval_experiments.{name}")

    if dialect == "postgresql":
        expected_function = _canonical_postgresql_function(
            "CREATE FUNCTION rag4c_retrieval_experiments_immutable() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'retrieval_experiments are immutable'; END; $$"
        )
        if _canonical_postgresql_function(function_definition) != expected_function:
            issues.append(
                "missing or invalid immutable function rag4c_retrieval_experiments_immutable"
            )
    return tuple(issues)


def _retrieval_experiment_trigger_issues(inspector: Any) -> tuple[str, ...]:
    dialect = str(getattr(inspector.bind.dialect, "name", "")).casefold()
    bind = inspector.bind
    connection = bind.connect() if hasattr(bind, "connect") else bind
    should_close = connection is not bind
    try:
        if dialect == "sqlite":
            rows = list(
                connection.execute(
                    text(
                        "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name='retrieval_experiments'"
                    )
                )
            )
            return _trigger_contract_issues(dialect, rows)
        if dialect in {"mysql", "mariadb"}:
            rows = list(
                connection.execute(
                    text(
                        "SELECT trigger_name, action_timing, event_manipulation, "
                        "action_statement, action_condition, 'ENABLED' AS enabled_state "
                        "FROM information_schema.triggers WHERE trigger_schema = DATABASE() "
                        "AND event_object_table = 'retrieval_experiments'"
                    )
                )
            )
            return _trigger_contract_issues(dialect, rows)
        if dialect == "postgresql":
            rows = list(
                connection.execute(
                    text(
                        "SELECT t.trigger_name, t.action_timing, t.event_manipulation, "
                        "t.action_statement, t.action_condition, pgt.tgenabled "
                        "FROM information_schema.triggers t "
                        "JOIN pg_trigger pgt ON pgt.tgname = t.trigger_name "
                        "JOIN pg_class c ON c.oid = pgt.tgrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE t.event_object_schema = current_schema() "
                        "AND t.event_object_table = 'retrieval_experiments' "
                        "AND n.nspname = current_schema() AND NOT pgt.tgisinternal"
                    )
                )
            )
            function_definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "WHERE p.proname='rag4c_retrieval_experiments_immutable' "
                    "AND pg_function_is_visible(p.oid)"
                )
            ).scalar()
            return _trigger_contract_issues(dialect, rows, function_definition)
        return ("cannot prove retrieval experiment trigger semantics for dialect " + dialect,)
    except Exception as exc:
        return (f"cannot prove retrieval experiment trigger semantics: {type(exc).__name__}",)
    finally:
        if should_close:
            connection.close()


IDENTITY_FEDERATION_TABLES = frozenset(
    {"tenant_verified_domains", "tenant_identity_providers", "tenant_scim_tokens"}
)
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | IDENTITY_FEDERATION_TABLES
_HEAD_REQUIRED_COLUMNS.update(
    {
        name: frozenset(table.columns.keys())
        for name, table in __import__("models.orm", fromlist=["Base"]).Base.metadata.tables.items()
        if name in IDENTITY_FEDERATION_TABLES
    }
)
_HEAD_REQUIRED_NOT_NULL.update(
    {
        name: frozenset(column.name for column in table.columns if not column.nullable)
        for name, table in __import__("models.orm", fromlist=["Base"]).Base.metadata.tables.items()
        if name in IDENTITY_FEDERATION_TABLES
    }
)
_HEAD_REQUIRED_UNIQUES.update(
    {
        "tenant_verified_domains": {
            "uq_tenant_verified_domains_global_domain": ("normalized_domain",),
            "uq_tenant_verified_domains_scope_id": ("tenant_id", "id"),
        },
        "tenant_identity_providers": {
            "uq_tenant_identity_providers_active_slot": ("tenant_id", "active_slot")
        },
        "tenant_scim_tokens": {
            "uq_tenant_scim_tokens_active_name": ("tenant_id", "active_name_key"),
            "uq_tenant_scim_tokens_hash": ("tenant_id", "token_hash"),
        },
    }
)
_HEAD_REQUIRED_FOREIGN_KEYS.update(
    {
        "tenant_verified_domains": {
            "fk_tenant_verified_domains_tenant": (("tenant_id",), "tenants", ("id",))
        },
        "tenant_identity_providers": {
            "fk_tenant_identity_providers_domain": (
                ("tenant_id", "trusted_domain_id"),
                "tenant_verified_domains",
                ("tenant_id", "id"),
            )
        },
        "tenant_scim_tokens": {
            "fk_tenant_scim_tokens_tenant": (("tenant_id",), "tenants", ("id",))
        },
    }
)
_HEAD_REQUIRED_CHECK_FRAGMENTS.update(
    {
        "tenant_verified_domains": {
            "ck_tenant_verified_domains_status": ("pending", "verified", "revoked")
        },
        "tenant_identity_providers": {
            "ck_tenant_identity_providers_status": ("draft", "active", "disabled"),
            "ck_tenant_identity_providers_active_slot": ("active_slot", "primary"),
        },
        "tenant_scim_tokens": {
            "ck_tenant_scim_tokens_status": ("active", "revoked", "expired"),
            "ck_tenant_scim_tokens_active_name": ("active_name_key", "name"),
        },
    }
)
_HEAD_REQUIRED_INDEXES.update(
    {
        "tenant_verified_domains": {
            "ix_tenant_verified_domains_tenant_status_updated": (
                "tenant_id",
                "status",
                "updated_at",
                "id",
            )
        },
        "tenant_identity_providers": {
            "ix_tenant_identity_providers_tenant_status": (
                "tenant_id",
                "status",
                "updated_at",
                "id",
            )
        },
        "tenant_scim_tokens": {
            "ix_tenant_scim_tokens_tenant_status_expires": (
                "tenant_id",
                "status",
                "expires_at",
                "id",
            )
        },
    }
)
IDENTITY_FEDERATION_ISSUE_FRAGMENTS = tuple(
    f"{name}." for name in sorted(IDENTITY_FEDERATION_TABLES)
)


SCIM_PROVISIONING_TABLES = frozenset({"tenant_scim_user_links", "tenant_scim_group_links"})
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | SCIM_PROVISIONING_TABLES
_SCIM_ORM_TABLES = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _name in SCIM_PROVISIONING_TABLES:
    _table = _SCIM_ORM_TABLES[_name]
    _HEAD_REQUIRED_COLUMNS[_name] = frozenset(_table.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_name] = frozenset(c.name for c in _table.columns if not c.nullable)
_HEAD_REQUIRED_COLUMNS["tenant_scim_tokens"] = _HEAD_REQUIRED_COLUMNS[
    "tenant_scim_tokens"
] | frozenset({"last_used_ip_hash", "use_count"})
_HEAD_REQUIRED_NOT_NULL["tenant_scim_tokens"] = _HEAD_REQUIRED_NOT_NULL[
    "tenant_scim_tokens"
] | frozenset({"use_count"})
_HEAD_REQUIRED_UNIQUES.update(
    {
        "tenant_scim_user_links": {
            "uq_tenant_scim_user_links_external_id": ("tenant_id", "external_id"),
            "uq_tenant_scim_user_links_user_name": ("tenant_id", "user_name"),
            "uq_tenant_scim_user_links_account_id": ("tenant_id", "account_id"),
        },
        "tenant_scim_group_links": {
            "uq_tenant_scim_group_links_external_id": ("tenant_id", "external_id"),
            "uq_tenant_scim_group_links_display_name": ("tenant_id", "display_name"),
            "uq_tenant_scim_group_links_group_id": ("tenant_id", "group_id"),
        },
    }
)
_HEAD_REQUIRED_UNIQUES["tenant_scim_tokens"]["uq_tenant_scim_tokens_scope_id"] = ("tenant_id", "id")
_HEAD_REQUIRED_FOREIGN_KEYS.update(
    {
        "tenant_scim_user_links": {
            "fk_tenant_scim_user_links_account_id": (
                ("account_id", "tenant_id"),
                "tenant_members",
                ("account_id", "tenant_id"),
            ),
            "fk_tenant_scim_user_links_source_token": (
                ("tenant_id", "source_token_id"),
                "tenant_scim_tokens",
                ("tenant_id", "id"),
            ),
        },
        "tenant_scim_group_links": {
            "fk_tenant_scim_group_links_group_id": (
                ("tenant_id", "group_id"),
                "tenant_groups",
                ("tenant_id", "id"),
            ),
            "fk_tenant_scim_group_links_source_token": (
                ("tenant_id", "source_token_id"),
                "tenant_scim_tokens",
                ("tenant_id", "id"),
            ),
        },
    }
)
_HEAD_REQUIRED_CHECK_FRAGMENTS.update(
    {
        "tenant_scim_user_links": {
            "ck_tenant_scim_user_links_revision_positive": ("revision > 0",)
        },
        "tenant_scim_group_links": {
            "ck_tenant_scim_group_links_revision_positive": ("revision > 0",)
        },
    }
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_scim_tokens"]["ck_tenant_scim_tokens_usage_evidence"] = (
    "last_used_at is null",
    "not use_count",
    "last_used_ip_hash",
    "use_count>0",
)
_HEAD_REQUIRED_INDEXES.update(
    {
        "tenant_scim_user_links": {
            "ix_tenant_scim_user_links_tenant_updated": ("tenant_id", "updated_at", "id")
        },
        "tenant_scim_group_links": {
            "ix_tenant_scim_group_links_tenant_updated": ("tenant_id", "updated_at", "id")
        },
    }
)
SCIM_PROVISIONING_ISSUE_FRAGMENTS = tuple(f"{n}." for n in sorted(SCIM_PROVISIONING_TABLES)) + (
    "tenant_scim_tokens.last_used_ip_hash",
    "tenant_scim_tokens.use_count",
    "tenant_scim_tokens.ck_tenant_scim_tokens_usage_evidence",
)


AUDIT_COMPLIANCE_TABLES = frozenset(
    {"tenant_audit_retention_policies", "tenant_audit_legal_holds", "tenant_audit_export_jobs"}
)
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | AUDIT_COMPLIANCE_TABLES
_AC = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _n in AUDIT_COMPLIANCE_TABLES:
    _t = _AC[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(c.name for c in _t.columns if not c.nullable)
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}
_HEAD_REQUIRED_UNIQUES["tenant_audit_retention_policies"] = {
    "uq_tenant_audit_retention_policies_tenant": ("tenant_id",)
}
AUDIT_COMPLIANCE_ISSUE_FRAGMENTS = tuple(f"{n}." for n in sorted(AUDIT_COMPLIANCE_TABLES))


OIDC_RUNTIME_TABLES = frozenset(
    {"tenant_oidc_login_transactions", "tenant_oidc_subject_links", "tenant_sso_sessions"}
)
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | OIDC_RUNTIME_TABLES
_OT = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _n in OIDC_RUNTIME_TABLES:
    _t = _OT[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(c.name for c in _t.columns if not c.nullable)
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}
_HEAD_REQUIRED_UNIQUES["tenant_oidc_login_transactions"] = {
    "uq_tenant_oidc_login_state": ("state_digest",)
}
_HEAD_REQUIRED_UNIQUES["tenant_oidc_subject_links"] = {
    "uq_tenant_oidc_subject": ("tenant_id", "provider_id", "subject_digest"),
    "uq_tenant_oidc_account": ("tenant_id", "provider_id", "account_id"),
}
_HEAD_REQUIRED_UNIQUES["tenant_sso_sessions"] = {
    "uq_tenant_sso_session_token": ("session_token_hash",)
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_oidc_login_transactions"] = {
    "fk_tenant_oidc_login_provider": (
        ("tenant_id", "provider_id"),
        "tenant_identity_providers",
        ("tenant_id", "id"),
    )
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_oidc_subject_links"] = {
    "fk_tenant_oidc_subject_provider": (
        ("tenant_id", "provider_id"),
        "tenant_identity_providers",
        ("tenant_id", "id"),
    )
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_sso_sessions"] = {
    "fk_tenant_sso_sessions_provider": (
        ("tenant_id", "provider_id"),
        "tenant_identity_providers",
        ("tenant_id", "id"),
    )
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_oidc_login_transactions"] = {
    "ck_tenant_oidc_login_status": ("pending", "consumed", "failed", "expired")
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_oidc_subject_links"] = {}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_sso_sessions"] = {
    "ck_tenant_sso_sessions_status": ("active", "revoked", "expired")
}
_HEAD_REQUIRED_INDEXES["tenant_oidc_login_transactions"] = {
    "ix_tenant_oidc_login_tenant_status": ("tenant_id", "status", "expires_at", "id")
}
_HEAD_REQUIRED_INDEXES["tenant_oidc_subject_links"] = {
    "ix_tenant_oidc_subject_tenant_email": ("tenant_id", "normalized_email", "id")
}
_HEAD_REQUIRED_INDEXES["tenant_sso_sessions"] = {
    "ix_tenant_sso_sessions_tenant_status": ("tenant_id", "status", "expires_at", "id")
}
OIDC_RUNTIME_ISSUE_FRAGMENTS = tuple(f"{n}." for n in sorted(OIDC_RUNTIME_TABLES))


ENTERPRISE_APPROVAL_CONTROL_TABLES = frozenset(
    {
        "tenant_approval_policies",
        "tenant_approval_policy_approvers",
        "tenant_approval_requests",
        "tenant_approval_decisions",
    }
)
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_TABLES = ENTERPRISE_APPROVAL_CONTROL_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_APPROVAL_CONTROL_TABLES
_AA = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _n in ENTERPRISE_APPROVAL_CONTROL_TABLES:
    _t = _AA[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(c.name for c in _t.columns if not c.nullable)
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}

_HEAD_REQUIRED_UNIQUES["tenant_approval_policies"] = {
    "uq_tenant_approval_policies_scope_id": ("tenant_id", "id"),
    "uq_tenant_approval_policies_active_scope": ("tenant_id", "active_scope_key"),
}
_HEAD_REQUIRED_UNIQUES["tenant_approval_policy_approvers"] = {
    "uq_tenant_approval_policy_approvers_identity": (
        "tenant_id",
        "policy_id",
        "approver_kind",
        "approver_ref",
    ),
}
_HEAD_REQUIRED_UNIQUES["tenant_approval_requests"] = {
    "uq_tenant_approval_requests_scope_id": ("tenant_id", "id"),
    "uq_tenant_approval_requests_requester_key": (
        "tenant_id",
        "requester_id",
        "idempotency_key",
    ),
}
_HEAD_REQUIRED_UNIQUES["tenant_approval_decisions"] = {
    "uq_tenant_approval_decisions_request_approver": (
        "tenant_id",
        "request_id",
        "approver_id",
    ),
}

_HEAD_REQUIRED_FOREIGN_KEYS["tenant_approval_policies"] = {
    "fk_tenant_approval_policies_tenant": (("tenant_id",), "tenants", ("id",)),
    "fk_tenant_approval_policies_creator": (
        ("created_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_policies_updater": (
        ("updated_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_policies_disabler": (
        ("disabled_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_approval_policy_approvers"] = {
    "fk_tenant_approval_policy_approvers_scope_policy": (
        ("tenant_id", "policy_id"),
        "tenant_approval_policies",
        ("tenant_id", "id"),
    ),
    "fk_tenant_approval_policy_approvers_scope_account": (
        ("account_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_policy_approvers_scope_group": (
        ("tenant_id", "group_id"),
        "tenant_groups",
        ("tenant_id", "id"),
    ),
    "fk_tenant_approval_policy_approvers_creator": (
        ("created_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_policy_approvers_updater": (
        ("updated_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_approval_requests"] = {
    "fk_tenant_approval_requests_scope_policy": (
        ("tenant_id", "policy_id"),
        "tenant_approval_policies",
        ("tenant_id", "id"),
    ),
    "fk_tenant_approval_requests_scope_requester": (
        ("requester_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_creator": (
        ("created_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_updater": (
        ("updated_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_rejector": (
        ("rejected_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_canceller": (
        ("cancelled_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_executor": (
        ("executed_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_requests_failure_actor": (
        ("execution_failed_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_approval_decisions"] = {
    "fk_tenant_approval_decisions_scope_request": (
        ("tenant_id", "request_id"),
        "tenant_approval_requests",
        ("tenant_id", "id"),
    ),
    "fk_tenant_approval_decisions_scope_approver": (
        ("approver_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
    "fk_tenant_approval_decisions_creator": (
        ("created_by", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}

_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"] = {
    "ck_tenant_approval_policies_action_type": (
        "catalog_upgrade",
        "membership_bootstrap",
        "dataset_acl_disable",
        "member_role_change",
        "identity_provider_disable",
        "audit_retention_execute",
        "workspace_authorization_mode_change",
        "dataset_workspace_transfer",
    ),
    "ck_tenant_approval_policies_status": ("active", "disabled"),
    "ck_tenant_approval_policies_required_count": ("required_approvals between 1 and 5",),
    "ck_tenant_approval_policies_expiry_minutes": ("request_expiry_minutes between 15 and 10080",),
    "ck_tenant_approval_policies_revision_positive": ("revision > 0",),
    "ck_tenant_approval_policies_active_scope": (
        "active_scope_key is not null",
        "active_scope_key is null",
    ),
    "ck_tenant_approval_policies_disabled_evidence": (
        "disabled_at is not null",
        "disabled_by is not null",
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policy_approvers"] = {
    "ck_tenant_approval_policy_approvers_kind": ("account", "role", "group"),
    "ck_tenant_approval_policy_approvers_status": ("active", "disabled"),
    "ck_tenant_approval_policy_approvers_revision_positive": ("revision > 0",),
    "ck_tenant_approval_policy_approvers_reference": (
        "approver_kind='account'",
        "approver_kind='group'",
        "approver_kind='role'",
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"] = {
    "ck_tenant_approval_requests_action_type": (
        "catalog_upgrade",
        "membership_bootstrap",
        "dataset_acl_disable",
        "member_role_change",
        "identity_provider_disable",
        "audit_retention_execute",
        "workspace_authorization_mode_change",
        "dataset_workspace_transfer",
    ),
    "ck_tenant_approval_requests_status": (
        "pending",
        "approved",
        "rejected",
        "cancelled",
        "expired",
        "executed",
        "execution_failed",
    ),
    "ck_tenant_approval_requests_approval_counts": (
        "required_approvals between 1 and 5",
        "received_approvals between 0 and required_approvals",
    ),
    "ck_tenant_approval_requests_revision_positive": ("revision > 0",),
    "ck_tenant_approval_requests_payload_hash": ("length(payload_hash)=64",),
    "ck_tenant_approval_requests_idempotency_key_length": (
        "length(idempotency_key) between 1 and 128",
    ),
    "ck_tenant_approval_requests_ticket_hash": (
        "execution_ticket_hash is null",
        "length(execution_ticket_hash)=64",
    ),
    "ck_tenant_approval_requests_rejected_evidence": (
        "rejected_at is not null",
        "rejected_by is not null",
        "rejection_comment is not null",
    ),
    "ck_tenant_approval_requests_cancelled_evidence": (
        "cancelled_at is not null",
        "cancelled_by is not null",
    ),
    "ck_tenant_approval_requests_executed_evidence": (
        "executed_at is not null",
        "executed_by is not null",
        "execution_ticket_hash is not null",
    ),
    "ck_tenant_approval_requests_execution_failed_evidence": (
        "execution_failed_at is not null",
        "execution_failed_by is not null",
        "execution_error is not null",
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_decisions"] = {
    "ck_tenant_approval_decisions_decision": ("approved", "rejected"),
    "ck_tenant_approval_decisions_comment_length": ("length(comment) <= 500",),
    "ck_tenant_approval_decisions_rejection_comment": (
        "decision <> 'rejected'",
        "length(comment) between 1 and 500",
    ),
    "ck_tenant_approval_decisions_revision_positive": ("revision > 0",),
}

_HEAD_REQUIRED_INDEXES["tenant_approval_policies"] = {
    "ix_tenant_approval_policies_tenant_status_action": (
        "tenant_id",
        "status",
        "action_type",
        "updated_at",
        "id",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_approval_policy_approvers"] = {
    "ix_tenant_approval_policy_approvers_tenant_policy_status": (
        "tenant_id",
        "policy_id",
        "status",
        "approver_kind",
        "id",
    ),
    "ix_tenant_approval_policy_approvers_tenant_account": (
        "tenant_id",
        "account_id",
        "status",
        "id",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_approval_requests"] = {
    "ix_tenant_approval_requests_tenant_status_expiry": (
        "tenant_id",
        "status",
        "expires_at",
        "id",
    ),
    "ix_tenant_approval_requests_tenant_requester_status": (
        "tenant_id",
        "requester_id",
        "status",
        "created_at",
        "id",
    ),
    "ix_tenant_approval_requests_tenant_action_resource": (
        "tenant_id",
        "action_type",
        "resource_type",
        "resource_id",
        "id",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_approval_decisions"] = {
    "ix_tenant_approval_decisions_tenant_request_time": (
        "tenant_id",
        "request_id",
        "decided_at",
        "id",
    ),
    "ix_tenant_approval_decisions_tenant_approver_time": (
        "tenant_id",
        "approver_id",
        "decided_at",
        "id",
    ),
}

ENTERPRISE_APPROVAL_CONTROL_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECK_FRAGMENTS.items()
}
ENTERPRISE_APPROVAL_CONTROL_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_APPROVAL_CONTROL_TABLES
}
ENTERPRISE_APPROVAL_CONTROL_ISSUE_FRAGMENTS = tuple(
    f"{n}." for n in sorted(ENTERPRISE_APPROVAL_CONTROL_TABLES)
)

# Approval action CHECKs are part of the executable authority contract.  Keep
# their canonical SQL here so runtime gates validate the exact revision-aware
# predicate rather than merely searching for action-name fragments.
ENTERPRISE_APPROVAL_ACTION_TYPES_0025 = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
)
ENTERPRISE_APPROVAL_ACTION_TYPES_0027 = ENTERPRISE_APPROVAL_ACTION_TYPES_0025 + (
    "workspace_authorization_mode_change",
)
ENTERPRISE_APPROVAL_ACTION_TYPES_0028 = ENTERPRISE_APPROVAL_ACTION_TYPES_0027 + (
    "dataset_workspace_transfer",
)
ENTERPRISE_APPROVAL_ACTION_TYPES_0029 = ENTERPRISE_APPROVAL_ACTION_TYPES_0028 + (
    "knowledge_base_release_publish",
    "knowledge_base_release_rollback",
)
ENTERPRISE_APPROVAL_ACTION_TYPES_0030 = ENTERPRISE_APPROVAL_ACTION_TYPES_0029 + (
    "knowledge_base_release_quality_waiver",
)
ENTERPRISE_APPROVAL_ACTION_TYPES_0033 = ENTERPRISE_APPROVAL_ACTION_TYPES_0030 + ("document_purge",)


def _approval_action_check_sql(actions: tuple[str, ...]) -> str:
    return "action_type IN (" + ",".join(f"'{action}'" for action in actions) + ")"


ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION = {
    revision: {
        table: {
            "ck_tenant_approval_policies_action_type": _approval_action_check_sql(actions),
            "ck_tenant_approval_requests_action_type": _approval_action_check_sql(actions),
        }
        for table in ("tenant_approval_policies", "tenant_approval_requests")
    }
    for revision, actions in (
        (ENTERPRISE_APPROVAL_CONTROL_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0025),
        (ENTERPRISE_WORKSPACE_CONTROL_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0025),
        (ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0027),
        (ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0028),
        (ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0029),
        (ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0030),
        (ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0030),
        (ENTERPRISE_NOTIFICATION_CENTER_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0030),
        (ENTERPRISE_CONTENT_RECOVERY_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0033),
        (ENTERPRISE_TASK_OPERATIONS_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0033),
        (ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION, ENTERPRISE_APPROVAL_ACTION_TYPES_0033),
    )
}


ENTERPRISE_WORKSPACE_CONTROL_TABLES = frozenset(
    {
        "tenant_workspaces",
        "tenant_workspace_members",
        "tenant_workspace_datasets",
    }
)
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_TABLES = ENTERPRISE_WORKSPACE_CONTROL_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_WORKSPACE_CONTROL_TABLES
_AW = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _n in ENTERPRISE_WORKSPACE_CONTROL_TABLES:
    _t = _AW[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(c.name for c in _t.columns if not c.nullable)
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}

_HEAD_REQUIRED_UNIQUES["tenant_workspaces"] = {
    "uq_tenant_workspaces_scope_id": ("tenant_id", "id"),
    "uq_tenant_workspaces_tenant_code": ("tenant_id", "code"),
    "uq_tenant_workspaces_tenant_normalized_name": ("tenant_id", "normalized_name"),
    "uq_tenant_workspaces_active_default": ("tenant_id", "active_default_slot"),
}
_HEAD_REQUIRED_UNIQUES["tenant_workspace_members"] = {
    "uq_tenant_workspace_members_scope_account": (
        "tenant_id",
        "workspace_id",
        "account_id",
    ),
}
_HEAD_REQUIRED_UNIQUES["tenant_workspace_datasets"] = {
    "uq_tenant_workspace_datasets_scope_binding": (
        "tenant_id",
        "workspace_id",
        "dataset_id",
    ),
    "uq_tenant_workspace_datasets_active_primary": (
        "tenant_id",
        "dataset_id",
        "active_primary_slot",
    ),
}

_HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspaces"] = {
    "fk_tenant_workspaces_tenant": (("tenant_id",), "tenants", ("id",)),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspace_members"] = {
    "fk_tenant_workspace_members_scope_workspace": (
        ("tenant_id", "workspace_id"),
        "tenant_workspaces",
        ("tenant_id", "id"),
    ),
    "fk_tenant_workspace_members_scope_member": (
        ("account_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspace_datasets"] = {
    "fk_tenant_workspace_datasets_scope_workspace": (
        ("tenant_id", "workspace_id"),
        "tenant_workspaces",
        ("tenant_id", "id"),
    ),
    "fk_tenant_workspace_datasets_scope_dataset": (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ),
}

_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspaces"] = {
    "ck_tenant_workspaces_status": ("status in", "active", "archived"),
    "ck_tenant_workspaces_environment": (
        "environment in",
        "development",
        "testing",
        "production",
    ),
    "ck_tenant_workspaces_revision_positive": ("revision > 0",),
    "ck_tenant_workspaces_lifecycle_evidence": (
        "status = 'active'",
        "archived_at is null",
        "archived_by is null",
        "status = 'archived'",
        "archived_at is not null",
        "archived_by is not null",
    ),
    "ck_tenant_workspaces_active_default_slot": (
        "is_default",
        "active_default_slot",
        "status",
        "default",
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspace_members"] = {
    "ck_tenant_workspace_members_role": ("role in", "owner", "admin", "editor", "viewer"),
    "ck_tenant_workspace_members_status": ("status in", "active", "removed"),
    "ck_tenant_workspace_members_revision_positive": ("revision > 0",),
    "ck_tenant_workspace_members_removed_evidence": (
        "status",
        "removed_at",
        "removed_by",
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspace_datasets"] = {
    "ck_tenant_workspace_datasets_binding_kind": (
        "binding_kind in",
        "primary",
        "shared",
    ),
    "ck_tenant_workspace_datasets_status": ("status in", "active", "removed"),
    "ck_tenant_workspace_datasets_revision_positive": ("revision > 0",),
    "ck_tenant_workspace_datasets_active_primary_slot": (
        "status = 'active'",
        "binding_kind = 'primary'",
        "active_primary_slot is not null",
        "active_primary_slot = 'primary'",
        "status <> 'active'",
        "binding_kind <> 'primary'",
        "active_primary_slot is null",
    ),
    "ck_tenant_workspace_datasets_removed_evidence": (
        "status",
        "removed_at",
        "removed_by",
    ),
}

_HEAD_REQUIRED_INDEXES["tenant_workspaces"] = {
    "ix_tenant_workspaces_tenant_status_updated": (
        "tenant_id",
        "status",
        "updated_at",
        "id",
    ),
    "ix_tenant_workspaces_tenant_default": (
        "tenant_id",
        "status",
        "active_default_slot",
        "id",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_workspace_members"] = {
    "ix_tenant_workspace_members_tenant_workspace_status": (
        "tenant_id",
        "workspace_id",
        "status",
        "id",
    ),
    "ix_tenant_workspace_members_tenant_account_status": (
        "tenant_id",
        "account_id",
        "status",
        "id",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_workspace_datasets"] = {
    "ix_tenant_workspace_datasets_tenant_workspace_status": (
        "tenant_id",
        "workspace_id",
        "status",
        "id",
    ),
    "ix_tenant_workspace_datasets_tenant_dataset_status": (
        "tenant_id",
        "dataset_id",
        "status",
        "id",
    ),
}

ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECK_FRAGMENTS.items()
}
_ENTERPRISE_WORKSPACE_EXACT_CHECK_NAMES = {
    table_name: frozenset(checks)
    for table_name, checks in ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECK_FRAGMENTS.items()
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_EXACT_CHECK_SQL = {
    table_name: {
        str(constraint.name): str(constraint.sqltext)
        for constraint in _AW[table_name].constraints
        if constraint.name in names and getattr(constraint, "sqltext", None) is not None
    }
    for table_name, names in _ENTERPRISE_WORKSPACE_EXACT_CHECK_NAMES.items()
}
ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_WORKSPACE_CONTROL_TABLES
}
ENTERPRISE_WORKSPACE_CONTROL_ISSUE_FRAGMENTS = tuple(
    f"{n}." for n in sorted(ENTERPRISE_WORKSPACE_CONTROL_TABLES)
)


ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES = frozenset({"tenant_workspace_authorization_policies"})
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_TABLES = ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
for _n in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES:
    _t = _AW[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(
        column.name for column in _t.columns if not column.nullable
    )
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}

_HEAD_REQUIRED_UNIQUES["tenant_workspace_authorization_policies"] = {
    "uq_tenant_workspace_authorization_policies_scope_id": ("tenant_id", "id"),
    "uq_tenant_workspace_authorization_policies_scope_workspace": (
        "tenant_id",
        "workspace_id",
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspace_authorization_policies"] = {
    "fk_tenant_workspace_authorization_policies_tenant": (
        ("tenant_id",),
        "tenants",
        ("id",),
    ),
    "fk_tenant_workspace_authorization_policies_scope_workspace": (
        ("tenant_id", "workspace_id"),
        "tenant_workspaces",
        ("tenant_id", "id"),
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspace_authorization_policies"] = {
    "ck_tenant_workspace_auth_policies_mode": ("disabled", "shadow", "enforced"),
    "ck_tenant_workspace_auth_policies_model_version_positive": ("permission_model_version > 0",),
    "ck_tenant_workspace_auth_policies_revision_positive": ("revision > 0",),
    "ck_tenant_workspace_auth_policies_mode_evidence": (
        "mode = 'shadow'",
        "mode = 'enforced'",
        "mode = 'disabled'",
        "enforced_at is null",
        "enforced_at is not null",
        "disabled_at is null",
        "disabled_at is not null",
    ),
}
_HEAD_REQUIRED_INDEXES["tenant_workspace_authorization_policies"] = {
    "ix_tw_auth_policies_tenant_mode_updated": (
        "tenant_id",
        "mode",
        "updated_at",
        "id",
    ),
    "ix_tw_auth_policies_tenant_workspace_mode": (
        "tenant_id",
        "workspace_id",
        "mode",
        "id",
    ),
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table]
    for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_ISSUE_FRAGMENTS = ("tenant_workspace_authorization_policies.",)
ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_EXACT_CHECK_SQL = {
    "tenant_workspace_authorization_policies": {
        "ck_tenant_workspace_auth_policies_mode": "mode IN ('disabled','shadow','enforced')",
        "ck_tenant_workspace_auth_policies_model_version_positive": "permission_model_version > 0",
        "ck_tenant_workspace_auth_policies_revision_positive": "revision > 0",
        "ck_tenant_workspace_auth_policies_mode_evidence": (
            "(mode = 'shadow' AND enforced_at IS NULL AND enforced_by IS NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'enforced' AND enforced_at IS NOT NULL AND enforced_by IS NOT NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'disabled' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL "
            "AND enforced_at IS NULL AND enforced_by IS NULL)"
        ),
    }
}
# Preserve the old export's control-only meaning.  The authorization contract
# now has its own exact-check map and is merged explicitly by the head gate.


# This is the single complete capability contract consumed by the Stage 17
# evaluator and Workspace creation path.  It intentionally includes the
# Stage 16 tables and the approval/schema dependency tables needed to prove
# that a 0027 catalog is complete before it can grant or mutate authority.
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_TABLES = frozenset(
    {
        "alembic_version",
        "tenants",
        "tenant_members",
        "datasets",
        *ENTERPRISE_APPROVAL_CONTROL_TABLES,
        *ENTERPRISE_WORKSPACE_CONTROL_TABLES,
        *ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES,
    }
)
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_COLUMNS = {
    "alembic_version": frozenset({"version_num"}),
    "tenants": frozenset({"id", "status"}),
    "tenant_members": frozenset({"id", "account_id", "tenant_id", "role", "status", "revision"}),
    "datasets": frozenset({"id", "tenant_id"}),
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_COLUMNS,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_COLUMNS,
    "tenant_workspace_authorization_policies": ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_COLUMNS[
        "tenant_workspace_authorization_policies"
    ],
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_NOT_NULL = {
    "alembic_version": frozenset({"version_num"}),
    "tenants": frozenset({"id", "status"}),
    "tenant_members": frozenset({"id", "account_id", "tenant_id", "role", "status", "revision"}),
    "datasets": frozenset({"id", "tenant_id"}),
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_NOT_NULL,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_NOT_NULL,
    "tenant_workspace_authorization_policies": ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_NOT_NULL[
        "tenant_workspace_authorization_policies"
    ],
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_UNIQUES = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_UNIQUES,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_UNIQUES,
    **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_UNIQUES,
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_FOREIGN_KEYS = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_FOREIGN_KEYS,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_FOREIGN_KEYS,
    **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_FOREIGN_KEYS,
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_CHECK_FRAGMENTS = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECK_FRAGMENTS,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECK_FRAGMENTS,
    "tenant_workspace_authorization_policies": ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_CHECK_FRAGMENTS[
        "tenant_workspace_authorization_policies"
    ],
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_INDEXES = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_INDEXES,
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_INDEXES,
    **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_INDEXES,
}
ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_EXACT_CHECK_SQL = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[
        ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION
    ],
    **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_EXACT_CHECK_SQL,
    **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_EXACT_CHECK_SQL,
}


ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES = frozenset(
    {
        "dataset_workspace_ownerships",
        "app_dataset_references",
    }
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_TABLES = ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
_AR = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
for _n in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES:
    _t = _AR[_n]
    _HEAD_REQUIRED_COLUMNS[_n] = frozenset(_t.columns.keys())
    _HEAD_REQUIRED_NOT_NULL[_n] = frozenset(
        column.name for column in _t.columns if not column.nullable
    )
    _HEAD_REQUIRED_UNIQUES[_n] = {}
    _HEAD_REQUIRED_FOREIGN_KEYS[_n] = {}
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_n] = {}
    _HEAD_REQUIRED_INDEXES[_n] = {}

_HEAD_REQUIRED_UNIQUES["apps"] = {
    "uq_apps_tenant_id": ("tenant_id", "id"),
}
_HEAD_REQUIRED_UNIQUES["dataset_workspace_ownerships"] = {
    "uq_dataset_workspace_ownerships_scope_id": ("tenant_id", "id"),
    "uq_dataset_workspace_ownerships_scope_dataset": ("tenant_id", "dataset_id"),
}
_HEAD_REQUIRED_UNIQUES["app_dataset_references"] = {
    "uq_app_dataset_references_scope_id": ("tenant_id", "id"),
    "uq_app_dataset_references_active_slot": (
        "tenant_id",
        "app_id",
        "dataset_id",
        "active_slot",
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["dataset_workspace_ownerships"] = {
    "fk_dataset_workspace_ownerships_tenant": (("tenant_id",), "tenants", ("id",)),
    "fk_dataset_workspace_ownerships_scope_dataset": (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ),
    "fk_dataset_workspace_ownerships_scope_workspace": (
        ("tenant_id", "workspace_id"),
        "tenant_workspaces",
        ("tenant_id", "id"),
    ),
}
_HEAD_REQUIRED_FOREIGN_KEYS["app_dataset_references"] = {
    "fk_app_dataset_references_tenant": (("tenant_id",), "tenants", ("id",)),
    "fk_app_dataset_references_scope_app": (
        ("tenant_id", "app_id"),
        "apps",
        ("tenant_id", "id"),
    ),
    "fk_app_dataset_references_scope_dataset": (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["dataset_workspace_ownerships"] = {
    "ck_dataset_workspace_ownerships_revision_positive": ("revision > 0",),
}
_HEAD_REQUIRED_CHECK_FRAGMENTS["app_dataset_references"] = {
    "ck_app_dataset_references_reference_kind": ("reference_kind = 'knowledge'",),
    "ck_app_dataset_references_status": ("status in", "active", "removed"),
    "ck_app_dataset_references_revision_positive": ("revision > 0",),
    "ck_app_dataset_references_lifecycle_evidence": (
        "status = 'active'",
        "active_slot = 'active'",
        "removed_at is null",
        "removed_by is null",
        "status = 'removed'",
        "active_slot is null",
        "removed_at is not null",
        "removed_by is not null",
    ),
}
_HEAD_REQUIRED_INDEXES["dataset_workspace_ownerships"] = {
    "ix_dataset_workspace_ownerships_tenant_workspace_dataset": (
        "tenant_id",
        "workspace_id",
        "dataset_id",
    ),
    "ix_dataset_workspace_ownerships_tenant_updated_dataset": (
        "tenant_id",
        "updated_at",
        "dataset_id",
    ),
}
_HEAD_REQUIRED_INDEXES["app_dataset_references"] = {
    "ix_app_dataset_references_tenant_dataset_status": (
        "tenant_id",
        "dataset_id",
        "status",
        "id",
    ),
    "ix_app_dataset_references_tenant_app_status": (
        "tenant_id",
        "app_id",
        "status",
        "id",
    ),
    "ix_app_dataset_references_tenant_status_updated": (
        "tenant_id",
        "status",
        "updated_at",
        "id",
    ),
}

ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_UNIQUES = {
    "apps": _HEAD_REQUIRED_UNIQUES["apps"],
    **{table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES},
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table]
    for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_CHECK_FRAGMENTS.items()
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_ISSUE_FRAGMENTS = (
    "app_dataset_references.",
    "apps.uq_apps_tenant_id",
    "dataset_workspace_ownerships.",
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_EXACT_CHECK_SQL = {
    "app_dataset_references": {
        "ck_app_dataset_references_reference_kind": "reference_kind = 'knowledge'",
        "ck_app_dataset_references_status": "status IN ('active','removed')",
        "ck_app_dataset_references_revision_positive": "revision > 0",
        "ck_app_dataset_references_lifecycle_evidence": (
            "(status = 'active' AND active_slot = 'active' AND removed_at IS NULL "
            "AND removed_by IS NULL) OR (status = 'removed' AND active_slot IS NULL "
            "AND removed_at IS NOT NULL AND removed_by IS NOT NULL)"
        ),
    },
}

# The dedicated capability includes the parent scope tables and approval tables
# because a registry fact is not safe unless its tenant-safe references and
# transfer action gate are also present.
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_TABLES = frozenset(
    {
        "alembic_version",
        "accounts",
        "tenants",
        "tenant_members",
        "apps",
        "datasets",
        "tenant_workspaces",
        "tenant_workspace_datasets",
        "tenant_control_mutation_requests",
        "tenant_audit_events",
        *ENTERPRISE_APPROVAL_CONTROL_TABLES,
        *ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES,
    }
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_UNIQUES = {
    "apps": _HEAD_REQUIRED_UNIQUES["apps"],
    **{table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES},
}
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_FOREIGN_KEYS = (
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_FOREIGN_KEYS
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_CHECK_FRAGMENTS = (
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_CHECK_FRAGMENTS
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_INDEXES = (
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_INDEXES
)
ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_EXACT_CHECK_SQL = {
    **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION
    ],
    **ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_EXACT_CHECK_SQL,
}

# 0028 extends the executable approval-action predicate without changing the
# historical 0025/0027 capability exports.
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
    "ck_tenant_approval_policies_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0028
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
    "ck_tenant_approval_requests_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0028


ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES = frozenset(
    {
        "tenant_release_channels",
        "dataset_release_manifests",
        "dataset_release_entries",
        "dataset_release_events",
        "dataset_channel_releases",
    }
)
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_TABLES = ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES
_AR = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables


def _orm_table_contract(
    table_name: str,
) -> tuple[
    frozenset[str],
    frozenset[str],
    dict[str, tuple[str, ...]],
    dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]],
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    table = _AR[table_name]
    uniques: dict[str, tuple[str, ...]] = {}
    foreign_keys: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {}
    checks: dict[str, tuple[str, ...]] = {}
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name:
            uniques[str(constraint.name)] = tuple(column.name for column in constraint.columns)
        elif isinstance(constraint, ForeignKeyConstraint) and constraint.name:
            elements = tuple(constraint.elements)
            foreign_keys[str(constraint.name)] = (
                tuple(element.parent.name for element in elements),
                str(elements[0].column.table.name),
                tuple(element.column.name for element in elements),
            )
        elif isinstance(constraint, CheckConstraint) and constraint.name:
            checks[str(constraint.name)] = (str(constraint.sqltext),)
    indexes = {
        str(index.name): tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.name
    }
    return (
        frozenset(table.columns.keys()),
        frozenset(column.name for column in table.columns if not column.nullable),
        uniques,
        foreign_keys,
        checks,
        indexes,
    )


for _release_table_name in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES:
    (
        _release_columns,
        _release_not_null,
        _release_uniques,
        _release_foreign_keys,
        _release_checks,
        _release_indexes,
    ) = _orm_table_contract(_release_table_name)
    _HEAD_REQUIRED_COLUMNS[_release_table_name] = _release_columns
    _HEAD_REQUIRED_NOT_NULL[_release_table_name] = _release_not_null
    _HEAD_REQUIRED_UNIQUES[_release_table_name] = _release_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_release_table_name] = _release_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_release_table_name] = _release_checks
    _HEAD_REQUIRED_INDEXES[_release_table_name] = _release_indexes

# 0029 extends existing parent authorities additively; preserve Stage18's
# fragment-oriented contracts and append only the new Release facts.
_HEAD_REQUIRED_COLUMNS["datasets"] = _HEAD_REQUIRED_COLUMNS["datasets"] | frozenset(
    {"serving_release_id", "release_revision"}
)
_HEAD_REQUIRED_NOT_NULL["datasets"] = _HEAD_REQUIRED_NOT_NULL["datasets"] | frozenset(
    {"release_revision"}
)
_HEAD_REQUIRED_FOREIGN_KEYS["datasets"]["fk_datasets_scope_serving_release"] = (
    ("tenant_id", "serving_release_id"),
    "dataset_release_manifests",
    ("tenant_id", "id"),
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["datasets"]["ck_datasets_release_revision_positive"] = (
    "release_revision > 0",
)

_HEAD_REQUIRED_COLUMNS["app_dataset_references"] = _HEAD_REQUIRED_COLUMNS[
    "app_dataset_references"
] | frozenset({"release_mode", "release_channel_id", "pinned_release_id"})
_HEAD_REQUIRED_NOT_NULL["app_dataset_references"] = _HEAD_REQUIRED_NOT_NULL[
    "app_dataset_references"
] | frozenset({"release_mode"})
_HEAD_REQUIRED_FOREIGN_KEYS["app_dataset_references"].update(
    {
        "fk_app_dataset_references_scope_release_channel": (
            ("tenant_id", "release_channel_id"),
            "tenant_release_channels",
            ("tenant_id", "id"),
        ),
        "fk_app_dataset_references_scope_pinned_release": (
            ("tenant_id", "dataset_id", "pinned_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
    }
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["app_dataset_references"][
    "ck_app_dataset_references_release_binding"
] = (
    "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL "
    "AND pinned_release_id IS NULL) OR (release_mode = 'pinned' "
    "AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)",
)

_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
    "ck_tenant_approval_policies_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0029
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
    "ck_tenant_approval_requests_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0029

ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table]
    for table in (
        *sorted(ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES),
        "datasets",
        "app_dataset_references",
    )
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table]
    for table in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES.get(table, {})
    for table in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS.get(table, {})
    for table in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS.get(table, {})
    for table in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES.get(table, {})
    for table in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS
}
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_ISSUE_FRAGMENTS = (
    "tenant_release_channels.",
    "dataset_release_manifests.",
    "dataset_release_entries.",
    "dataset_release_events.",
    "dataset_channel_releases.",
    "datasets.serving_release_id",
    "datasets.release_revision",
    "app_dataset_references.release_",
    "app_dataset_references.pinned_release_id",
)
ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_EXACT_CHECK_SQL = {
    "app_dataset_references": {
        "ck_app_dataset_references_release_binding": (
            "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL "
            "AND pinned_release_id IS NULL) OR (release_mode = 'pinned' "
            "AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)"
        )
    }
}


ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES = frozenset(
    {
        "tenant_release_quality_gate_policies",
        "dataset_quality_baselines",
        "dataset_quality_baseline_items",
        "dataset_release_quality_certifications",
        "dataset_release_quality_certification_evidence",
        "dataset_release_quality_waivers",
        "dataset_release_quality_events",
    }
)
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_TABLES = (
    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
)
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
for _quality_table_name in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES:
    (
        _quality_columns,
        _quality_not_null,
        _quality_uniques,
        _quality_foreign_keys,
        _quality_checks,
        _quality_indexes,
    ) = _orm_table_contract(_quality_table_name)
    _HEAD_REQUIRED_COLUMNS[_quality_table_name] = _quality_columns
    _HEAD_REQUIRED_NOT_NULL[_quality_table_name] = _quality_not_null
    _HEAD_REQUIRED_UNIQUES[_quality_table_name] = _quality_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_quality_table_name] = _quality_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_quality_table_name] = _quality_checks
    _HEAD_REQUIRED_INDEXES[_quality_table_name] = _quality_indexes

# Keep the manifest's fragment contract literal and dialect-neutral. The
# quality capability performs the dialect-specific exact comparison below.
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_release_quality_gate_policies"][
    "ck_tenant_release_quality_gate_policies_active_scope_key"
] = (
    "status != 'active'",
    "scope_type = 'global'",
    "active_scope_key",
    "global:*",
    "scope_type = 'risk_tier'",
    "risk_tier:",
    "scope_type = 'channel'",
    "channel:",
    "scope_value",
)

_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
    "ck_tenant_approval_policies_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0030
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
    "ck_tenant_approval_requests_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0030

ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table]
    for table in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES
}
ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_ISSUE_FRAGMENTS = tuple(
    f"{table}." for table in sorted(ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES)
)


ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES = frozenset(
    {
        "tenant_release_quality_slo_policies",
        "tenant_release_quality_scan_schedules",
        "tenant_release_quality_scan_runs",
        "dataset_release_quality_observations",
        "dataset_release_quality_alerts",
        "dataset_release_recertification_jobs",
    }
)
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES = ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
for _operations_table_name in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES:
    (
        _operations_columns,
        _operations_not_null,
        _operations_uniques,
        _operations_foreign_keys,
        _operations_checks,
        _operations_indexes,
    ) = _orm_table_contract(_operations_table_name)
    _HEAD_REQUIRED_COLUMNS[_operations_table_name] = _operations_columns
    _HEAD_REQUIRED_NOT_NULL[_operations_table_name] = _operations_not_null
    _HEAD_REQUIRED_UNIQUES[_operations_table_name] = _operations_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_operations_table_name] = _operations_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_operations_table_name] = _operations_checks
    _HEAD_REQUIRED_INDEXES[_operations_table_name] = _operations_indexes

_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_release_quality_slo_policies"][
    "ck_tenant_release_quality_slo_policies_active_scope_key"
] = (
    "status != 'active'",
    "scope_type = 'global'",
    "global:*",
    "scope_type = 'risk_tier'",
    "risk_tier:",
    "scope_type = 'channel'",
    "channel:",
    "active_scope_key",
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["dataset_release_quality_alerts"]["ck_quality_alerts_active_key"] = (
    "status = 'resolved'",
    "active_alert_key is null",
    "status != 'resolved'",
    "dataset_id",
    "release_id",
    "channel_id",
    "release_role",
    "alert_type",
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["dataset_release_recertification_jobs"][
    "ck_dataset_release_recertification_jobs_active_key"
] = (
    "completed",
    "failed",
    "cancelled",
    "active_job_key is null",
    "pending",
    "claimed",
    "awaiting_evidence",
    "ready_to_certify",
    "dataset_id",
    "release_id",
    "channel_id",
    "release_role",
    "policy_id",
)

ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table]
    for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table]
    for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES
}
ENTERPRISE_RELEASE_QUALITY_OPERATIONS_ISSUE_FRAGMENTS = tuple(
    f"{table}." for table in sorted(ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES)
)


ENTERPRISE_NOTIFICATION_CENTER_TABLES = frozenset(
    {
        "tenant_notification_subscriptions",
        "tenant_notifications",
        "tenant_notification_recipients",
        "tenant_notification_receipts",
        "tenant_notification_events",
    }
)
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES = ENTERPRISE_NOTIFICATION_CENTER_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_NOTIFICATION_CENTER_TABLES
for _notification_table_name in ENTERPRISE_NOTIFICATION_CENTER_TABLES:
    (
        _notification_columns,
        _notification_not_null,
        _notification_uniques,
        _notification_foreign_keys,
        _notification_checks,
        _notification_indexes,
    ) = _orm_table_contract(_notification_table_name)
    _HEAD_REQUIRED_COLUMNS[_notification_table_name] = _notification_columns
    _HEAD_REQUIRED_NOT_NULL[_notification_table_name] = _notification_not_null
    _HEAD_REQUIRED_UNIQUES[_notification_table_name] = _notification_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_notification_table_name] = _notification_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_notification_table_name] = _notification_checks
    _HEAD_REQUIRED_INDEXES[_notification_table_name] = _notification_indexes

_HEAD_REQUIRED_UNIQUES["tenant_members"]["uq_tenant_members_tenant_account"] = (
    "tenant_id",
    "account_id",
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_notification_subscriptions"][
    "ck_notification_subscriptions_active_key"
] = (
    "status = 'archived'",
    "active_subscription_key is null",
    "status = 'active'",
    "account_id",
    "category",
    "active_subscription_key",
)

ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_NOTIFICATION_CENTER_TABLES
}
ENTERPRISE_NOTIFICATION_CENTER_ISSUE_FRAGMENTS = tuple(
    f"{table}." for table in sorted(ENTERPRISE_NOTIFICATION_CENTER_TABLES)
) + ("tenant_members.uq_tenant_members_tenant_account",)


ENTERPRISE_CONTENT_RECOVERY_TABLES = frozenset(
    {
        "tenant_content_retention_policies",
        "tenant_document_recycle_entries",
        "tenant_document_legal_holds",
        "tenant_document_purge_requests",
        "tenant_document_recovery_events",
    }
)
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_TABLES = ENTERPRISE_CONTENT_RECOVERY_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_CONTENT_RECOVERY_TABLES
for _recovery_table_name in ENTERPRISE_CONTENT_RECOVERY_TABLES:
    (
        _recovery_columns,
        _recovery_not_null,
        _recovery_uniques,
        _recovery_foreign_keys,
        _recovery_checks,
        _recovery_indexes,
    ) = _orm_table_contract(_recovery_table_name)
    _HEAD_REQUIRED_COLUMNS[_recovery_table_name] = _recovery_columns
    _HEAD_REQUIRED_NOT_NULL[_recovery_table_name] = _recovery_not_null
    _HEAD_REQUIRED_UNIQUES[_recovery_table_name] = _recovery_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_recovery_table_name] = _recovery_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_recovery_table_name] = _recovery_checks
    _HEAD_REQUIRED_INDEXES[_recovery_table_name] = _recovery_indexes

# 0033 extends the existing document lifecycle and Approval action contracts.
_HEAD_REQUIRED_CHECK_FRAGMENTS["documents"]["ck_documents_lifecycle_state"] = (
    "active",
    "expired",
    "recycled",
    "delete_requested",
    "deleting",
    "delete_failed",
    "deleted",
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
    "ck_tenant_approval_policies_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0033
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
    "ck_tenant_approval_requests_action_type"
] = ENTERPRISE_APPROVAL_ACTION_TYPES_0033
# Active-key expressions are compiled from SQLAlchemy columns, so keep the
# manifest fragments literal and dialect-neutral just like the earlier
# quality/notification active identity contracts.
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_document_recycle_entries"][
    "ck_tenant_document_recycle_entries_active_key"
] = (
    "status = 'recycled'",
    "status = 'restoring'",
    "status = 'purge_requested'",
    "status IN ('restored', 'purged', 'failed')",
    "active_recycle_key",
    "dataset_id",
    "document_id",
)
_HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_document_legal_holds"][
    "ck_tenant_document_legal_holds_active_key"
] = (
    "status = 'active'",
    "status = 'released'",
    "active_hold_key",
    "recycle_entry_id",
    "reason_code",
)

ENTERPRISE_CONTENT_RECOVERY_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_CHECK_FRAGMENTS = {
    table: _HEAD_REQUIRED_CHECK_FRAGMENTS[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table] for table in ENTERPRISE_CONTENT_RECOVERY_TABLES
}
ENTERPRISE_CONTENT_RECOVERY_ISSUE_FRAGMENTS = tuple(
    f"{table}." for table in sorted(ENTERPRISE_CONTENT_RECOVERY_TABLES)
) + (
    "documents.lifecycle_state",
    "tenant_approval_policies.ck_tenant_approval_policies_action_type",
    "tenant_approval_requests.ck_tenant_approval_requests_action_type",
)


ENTERPRISE_TASK_OPERATIONS_TABLES = frozenset(
    {
        "tenant_task_projections",
        "tenant_task_operator_actions",
        "tenant_task_events",
        "tenant_task_saved_views",
        "tenant_task_reconciliation_runs",
    }
)
ENTERPRISE_TASK_OPERATIONS_REQUIRED_TABLES = ENTERPRISE_TASK_OPERATIONS_TABLES
ENTERPRISE_TASK_SOURCE_KINDS = (
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
)
ENTERPRISE_TASK_CATEGORIES = ("documents", "indexing", "sources", "compliance", "quality")
ENTERPRISE_TASK_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
    "unavailable",
)
ENTERPRISE_TASK_ACTION_TYPES = ("retry", "cancel", "acknowledge")
ENTERPRISE_TASK_ACTION_STATUSES = (
    "requested",
    "dispatched",
    "applied",
    "rejected",
    "expired",
)
ENTERPRISE_TASK_EVENT_TYPES = (
    "materialized",
    "status_changed",
    "source_stale",
    "action_requested",
    "action_applied",
    "action_rejected",
    "attention_acknowledged",
)
ENTERPRISE_TASK_VIEW_STATUSES = ("active", "archived")
ENTERPRISE_TASK_RECONCILIATION_STATUSES = ("running", "completed", "failed")
ENTERPRISE_TASK_ROUTE_CODES = ENTERPRISE_TASK_CATEGORIES

ENTERPRISE_TASK_OPERATIONS_REQUIRED_COLUMNS = {
    "tenant_task_projections": frozenset(
        {
            "id",
            "tenant_id",
            "source_kind",
            "source_id",
            "source_revision",
            "source_digest",
            "dataset_id",
            "workspace_id",
            "category",
            "normalized_status",
            "action_required",
            "progress_percent",
            "attempt_number",
            "max_attempts",
            "lease_owner",
            "lease_until",
            "safe_error_code",
            "safe_error",
            "target_route_code",
            "target_route_params_json",
            "source_current",
            "projection_digest",
            "occurred_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_task_operator_actions": frozenset(
        {
            "id",
            "tenant_id",
            "task_id",
            "action_type",
            "status",
            "expected_source_revision",
            "expected_source_digest",
            "idempotency_key_digest",
            "actor_id",
            "request_id",
            "safe_reason",
            "result_code",
            "requested_at",
            "dispatched_at",
            "applied_at",
            "rejected_at",
            "expires_at",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_task_events": frozenset(
        {
            "id",
            "tenant_id",
            "task_id",
            "sequence",
            "event_type",
            "previous_event_digest",
            "event_digest",
            "actor_id",
            "request_id",
            "safe_snapshot_json",
            "occurred_at",
        }
    ),
    "tenant_task_saved_views": frozenset(
        {
            "id",
            "tenant_id",
            "account_id",
            "name",
            "normalized_name",
            "status",
            "active_view_key",
            "revision",
            "filters_json",
            "filter_digest",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "archived_at",
            "archived_by",
        }
    ),
    "tenant_task_reconciliation_runs": frozenset(
        {
            "id",
            "tenant_id",
            "status",
            "source_kinds_json",
            "source_inventory_digest",
            "source_count",
            "created_count",
            "updated_count",
            "stale_count",
            "invalid_count",
            "started_at",
            "completed_at",
            "safe_error_code",
            "safe_error",
            "created_at",
            "updated_at",
        }
    ),
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_NOT_NULL = {
    "tenant_task_projections": frozenset(
        {
            "id",
            "tenant_id",
            "source_kind",
            "source_id",
            "source_revision",
            "source_digest",
            "category",
            "normalized_status",
            "action_required",
            "attempt_number",
            "max_attempts",
            "source_current",
            "projection_digest",
            "occurred_at",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_task_operator_actions": frozenset(
        {
            "id",
            "tenant_id",
            "task_id",
            "action_type",
            "status",
            "expected_source_revision",
            "expected_source_digest",
            "idempotency_key_digest",
            "actor_id",
            "request_id",
            "safe_reason",
            "requested_at",
            "created_at",
            "updated_at",
        }
    ),
    "tenant_task_events": frozenset(
        {
            "id",
            "tenant_id",
            "task_id",
            "sequence",
            "event_type",
            "event_digest",
            "actor_id",
            "request_id",
            "safe_snapshot_json",
            "occurred_at",
        }
    ),
    "tenant_task_saved_views": frozenset(
        {
            "id",
            "tenant_id",
            "account_id",
            "name",
            "normalized_name",
            "status",
            "revision",
            "filters_json",
            "filter_digest",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_task_reconciliation_runs": frozenset(
        {
            "id",
            "tenant_id",
            "status",
            "source_kinds_json",
            "source_inventory_digest",
            "source_count",
            "created_count",
            "updated_count",
            "stale_count",
            "invalid_count",
            "started_at",
            "created_at",
            "updated_at",
        }
    ),
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_UNIQUES = {
    "tenant_task_projections": {
        "uq_tenant_task_projections_scope_id": ("tenant_id", "id"),
        "uq_tenant_task_projections_source": ("tenant_id", "source_kind", "source_id"),
    },
    "tenant_task_operator_actions": {
        "uq_tenant_task_operator_actions_scope_id": ("tenant_id", "id"),
        "uq_tenant_task_operator_actions_idempotency": (
            "tenant_id",
            "actor_id",
            "idempotency_key_digest",
        ),
    },
    "tenant_task_events": {
        "uq_tenant_task_events_scope_id": ("tenant_id", "id"),
        "uq_tenant_task_events_stream_sequence": ("tenant_id", "task_id", "sequence"),
    },
    "tenant_task_saved_views": {
        "uq_tenant_task_saved_views_scope_id": ("tenant_id", "id"),
        "uq_tenant_task_saved_views_active_key": ("tenant_id", "active_view_key"),
    },
    "tenant_task_reconciliation_runs": {
        "uq_tenant_task_reconciliation_runs_scope_id": ("tenant_id", "id"),
    },
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_FOREIGN_KEYS = {
    "tenant_task_projections": {
        "fk_tenant_task_projections_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_task_projections_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_tenant_task_projections_scope_workspace": (
            ("tenant_id", "workspace_id"),
            "tenant_workspaces",
            ("tenant_id", "id"),
        ),
    },
    "tenant_task_operator_actions": {
        "fk_tenant_task_operator_actions_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_task_operator_actions_task": (
            ("tenant_id", "task_id"),
            "tenant_task_projections",
            ("tenant_id", "id"),
        ),
        "fk_tenant_task_operator_actions_actor": (
            ("tenant_id", "actor_id"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
    },
    "tenant_task_events": {
        "fk_tenant_task_events_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_task_events_task": (
            ("tenant_id", "task_id"),
            "tenant_task_projections",
            ("tenant_id", "id"),
        ),
        "fk_tenant_task_events_actor": (
            ("tenant_id", "actor_id"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
    },
    "tenant_task_saved_views": {
        "fk_tenant_task_saved_views_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_task_saved_views_member": (
            ("tenant_id", "account_id"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
        "fk_tenant_task_saved_views_creator": (
            ("tenant_id", "created_by"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
        "fk_tenant_task_saved_views_updater": (
            ("tenant_id", "updated_by"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
        "fk_tenant_task_saved_views_archiver": (
            ("tenant_id", "archived_by"),
            "tenant_members",
            ("tenant_id", "account_id"),
        ),
    },
    "tenant_task_reconciliation_runs": {
        "fk_tenant_task_reconciliation_runs_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
    },
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS = {
    "tenant_task_projections": {
        "ck_tenant_task_projections_source_kind": ENTERPRISE_TASK_SOURCE_KINDS,
        "ck_tenant_task_projections_category": ENTERPRISE_TASK_CATEGORIES,
        "ck_tenant_task_projections_status": ENTERPRISE_TASK_STATUSES,
        "ck_tenant_task_projections_source_revision": ("source_revision > 0",),
        "ck_tenant_task_projections_action_required": ("action_required", "false", "true"),
        "ck_tenant_task_projections_progress": (
            "progress_percent",
            "between 0 and 100",
        ),
        "ck_tenant_task_projections_attempts": (
            "attempt_number >= 0",
            "max_attempts > 0",
            "attempt_number <= max_attempts",
        ),
        "ck_tenant_task_projections_lease": ("lease_owner", "lease_until"),
        "ck_tenant_task_projections_error": ("safe_error_code", "safe_error"),
        "ck_tenant_task_projections_route": (
            "target_route_code",
            "documents",
            "sources",
            "compliance",
            "quality",
        ),
        "ck_tenant_task_projections_digest": (
            "source_digest",
            "projection_digest",
            "length",
            "lower",
        ),
        "ck_tenant_task_projections_current": ("source_current", "false", "true"),
    },
    "tenant_task_operator_actions": {
        "ck_tenant_task_operator_actions_type": ENTERPRISE_TASK_ACTION_TYPES,
        "ck_tenant_task_operator_actions_status": ENTERPRISE_TASK_ACTION_STATUSES,
        "ck_tenant_task_operator_actions_revision": ("expected_source_revision > 0",),
        "ck_tenant_task_operator_actions_digest": (
            "expected_source_digest",
            "idempotency_key_digest",
            "length",
            "lower",
        ),
        "ck_tenant_task_operator_actions_reason": ("length(safe_reason)", "between 1 and 512"),
        "ck_tenant_task_operator_actions_lifecycle": (
            "requested",
            "dispatched",
            "applied",
            "rejected",
            "expired",
            "dispatched_at",
            "applied_at",
            "rejected_at",
            "expires_at",
        ),
    },
    "tenant_task_events": {
        "ck_tenant_task_events_sequence": ("sequence > 0",),
        "ck_tenant_task_events_type": ENTERPRISE_TASK_EVENT_TYPES,
        "ck_tenant_task_events_digests": (
            "event_digest",
            "previous_event_digest",
            "length",
            "lower",
        ),
        "ck_tenant_task_events_hash_chain": (
            "sequence=1",
            "previous_event_digest",
            "sequence>1",
        ),
    },
    "tenant_task_saved_views": {
        "ck_tenant_task_saved_views_status": ENTERPRISE_TASK_VIEW_STATUSES,
        "ck_tenant_task_saved_views_revision": ("revision > 0",),
        "ck_tenant_task_saved_views_name": (
            "length(name)",
            "length(normalized_name)",
            "between 1 and 128",
        ),
        "ck_tenant_task_saved_views_filters": (
            "filters_json",
            "filter_digest",
            "length",
            "lower",
        ),
        "ck_tenant_task_saved_views_active_key": (
            "active_view_key",
            "account_id",
            "normalized_name",
            "archived",
            "active",
        ),
        "ck_tenant_task_saved_views_lifecycle": (
            "active_view_key",
            "archived_at",
            "archived_by",
            "status",
        ),
    },
    "tenant_task_reconciliation_runs": {
        "ck_tenant_task_reconciliation_runs_status": ENTERPRISE_TASK_RECONCILIATION_STATUSES,
        "ck_tenant_task_reconciliation_runs_source_kinds": ("source_kinds_json",),
        "ck_tenant_task_reconciliation_runs_digest": (
            "length(source_inventory_digest)",
            "lower(source_inventory_digest)",
        ),
        "ck_tenant_task_reconciliation_runs_counts": (
            "source_count >= 0",
            "created_count >= 0",
            "updated_count >= 0",
            "stale_count >= 0",
            "invalid_count >= 0",
        ),
        "ck_tenant_task_reconciliation_runs_lifecycle_state": (
            "running",
            "completed",
            "failed",
            "completed_at",
        ),
        "ck_tenant_task_reconciliation_runs_lifecycle": (
            "safe_error_code",
            "safe_error",
        ),
    },
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECKS = {
    table: frozenset(checks)
    for table, checks in ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS.items()
}
ENTERPRISE_TASK_OPERATIONS_REQUIRED_INDEXES = {
    "tenant_task_projections": {
        "ix_tenant_task_projections_tenant_status_updated": (
            "tenant_id",
            "normalized_status",
            "updated_at",
            "id",
        ),
        "ix_tenant_task_projections_tenant_attention_updated": (
            "tenant_id",
            "action_required",
            "source_current",
            "updated_at",
            "id",
        ),
        "ix_tenant_task_projections_source_scope_updated": (
            "tenant_id",
            "source_kind",
            "source_current",
            "updated_at",
            "id",
        ),
    },
    "tenant_task_operator_actions": {
        "ix_tenant_task_operator_actions_tenant_status_requested": (
            "tenant_id",
            "status",
            "requested_at",
            "id",
        ),
        "ix_tenant_task_operator_actions_task_created": (
            "tenant_id",
            "task_id",
            "created_at",
            "id",
        ),
    },
    "tenant_task_events": {
        "ix_tenant_task_events_task_sequence": ("tenant_id", "task_id", "sequence", "id"),
        "ix_tenant_task_events_tenant_time": ("tenant_id", "occurred_at", "id"),
    },
    "tenant_task_saved_views": {
        "ix_tenant_task_saved_views_account_status": (
            "tenant_id",
            "account_id",
            "status",
            "updated_at",
            "id",
        ),
    },
    "tenant_task_reconciliation_runs": {
        "ix_tenant_task_reconciliation_runs_tenant_status_started": (
            "tenant_id",
            "status",
            "started_at",
            "id",
        ),
    },
}
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_TASK_OPERATIONS_TABLES
_HEAD_REQUIRED_COLUMNS.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_COLUMNS)
_HEAD_REQUIRED_NOT_NULL.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_NOT_NULL)
_HEAD_REQUIRED_UNIQUES.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_UNIQUES)
_HEAD_REQUIRED_FOREIGN_KEYS.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_FOREIGN_KEYS)
_HEAD_REQUIRED_CHECK_FRAGMENTS.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS)
_HEAD_REQUIRED_INDEXES.update(ENTERPRISE_TASK_OPERATIONS_REQUIRED_INDEXES)
ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS = (
    tuple(f"{table}." for table in sorted(ENTERPRISE_TASK_OPERATIONS_TABLES))
    + ENTERPRISE_CONTENT_RECOVERY_ISSUE_FRAGMENTS
    + ("tenant_members.uq_tenant_members_tenant_account",)
)


ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES = frozenset(
    {
        "tenant_automation_rules",
        "tenant_automation_rule_revisions",
        "tenant_automation_source_cursors",
        "tenant_automation_runs",
        "tenant_automation_action_requests",
        "tenant_automation_events",
    }
)
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_TABLES = ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
_AUTOMATION_METADATA = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_COLUMNS = {
    name: frozenset(_AUTOMATION_METADATA[name].c.keys())
    for name in ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
}
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_NOT_NULL = {
    name: frozenset(column.name for column in _AUTOMATION_METADATA[name].c if not column.nullable)
    for name in ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
}
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_UNIQUES = {
    name: {
        str(constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in _AUTOMATION_METADATA[name].constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    for name in ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
}
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_FOREIGN_KEYS = {
    name: {
        str(constraint.name): (
            tuple(constraint.column_keys),
            next(iter(constraint.elements)).column.table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in _AUTOMATION_METADATA[name].constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name
    }
    for name in ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
}
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS = {
    "tenant_automation_rules": {
        "ck_tenant_automation_rules_status": ("draft", "active", "paused", "archived"),
        "ck_tenant_automation_rules_revision": ("revision > 0",),
        "ck_tenant_automation_rules_priority": ("priority", "1000"),
        "ck_tenant_automation_rules_active_identity": (
            "active_rule_key",
            "normalized_name",
            "current_revision_id",
        ),
        "ck_tenant_automation_rules_lifecycle": ("archived_at", "archived_by"),
    },
    "tenant_automation_rule_revisions": {
        "ck_tenant_automation_rule_revisions_revision": ("revision > 0",),
        "ck_tenant_automation_rule_revisions_trigger": (
            "task_failed",
            "task_source_stale",
            "source_sync_failed",
            "release_quality_alert_opened",
            "release_recertification_blocked",
            "approval_request_terminal",
        ),
        "ck_tenant_automation_rule_revisions_condition": (
            "always",
            "status_is",
            "action_required",
            "severity_at_least",
            "attempt_exhausted",
            "source_is_stale",
        ),
        "ck_tenant_automation_rule_revisions_definition": (
            "condition_params_json",
            "action_plan_json",
        ),
        "ck_tenant_automation_rule_revisions_digest": (
            "length(definition_digest)",
            "lower(definition_digest)",
        ),
    },
    "tenant_automation_source_cursors": {
        "ck_tenant_automation_source_cursors_source_kind": (
            "task_failed",
            "task_source_stale",
            "source_sync_failed",
            "release_quality_alert_opened",
            "release_recertification_blocked",
            "approval_request_terminal",
        ),
        "ck_tenant_automation_source_cursors_status": ("idle", "claimed", "blocked"),
        "ck_tenant_automation_source_cursors_revision": ("last_sequence", "revision"),
        "ck_tenant_automation_source_cursors_digest": (
            "last_sequence",
            "last_event_digest",
        ),
        "ck_tenant_automation_source_cursors_lease": ("lease_owner", "lease_until"),
    },
    "tenant_automation_runs": {
        "ck_tenant_automation_runs_status": (
            "started",
            "not_matched",
            "requested",
            "completed",
            "failed",
            "blocked",
        ),
        "ck_tenant_automation_runs_condition": ("condition_matched",),
        "ck_tenant_automation_runs_counts": (
            "action_count",
            "requested_count",
            "rejected_count",
        ),
        "ck_tenant_automation_runs_digest": (
            "trigger_event_digest",
            "idempotency_digest",
        ),
        "ck_tenant_automation_runs_lifecycle": ("completed_at", "started"),
        "ck_tenant_automation_runs_error": ("safe_error_code", "safe_error"),
    },
    "tenant_automation_action_requests": {
        "ck_tenant_automation_action_requests_action": (
            "notify_operator",
            "request_approval",
            "open_task_attention",
            "pause_rule",
        ),
        "ck_tenant_automation_action_requests_status": (
            "requested",
            "dispatched",
            "applied",
            "rejected",
            "expired",
        ),
        "ck_tenant_automation_action_requests_step": ("step_index",),
        "ck_tenant_automation_action_requests_params": ("safe_params_json",),
        "ck_tenant_automation_action_requests_idempotency": ("idempotency_key_digest",),
        "ck_tenant_automation_action_requests_target_fence": (
            "target_revision",
            "target_digest",
        ),
        "ck_tenant_automation_action_requests_lifecycle": (
            "requested",
            "dispatched",
            "applied",
            "rejected",
            "expired",
            "dispatched_at",
            "applied_at",
            "rejected_at",
        ),
    },
    "tenant_automation_events": {
        "ck_tenant_automation_events_type": (
            "rule_created",
            "revision_created",
            "revision_activated",
            "rule_paused",
            "trigger_observed",
            "condition_not_matched",
            "run_started",
            "action_requested",
            "action_rejected",
            "run_completed",
            "run_failed",
        ),
        "ck_tenant_automation_events_sequence": ("sequence > 0",),
        "ck_tenant_automation_events_digest": (
            "event_digest",
            "previous_event_digest",
        ),
        "ck_tenant_automation_events_hash_chain": (
            "sequence",
            "previous_event_digest",
        ),
        "ck_tenant_automation_events_snapshot": ("safe_snapshot_json",),
    },
}
ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_INDEXES = {
    name: {
        str(index.name): tuple(column.name for column in index.columns)
        for index in _AUTOMATION_METADATA[name].indexes
        if index.name
    }
    for name in ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
}
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES
_HEAD_REQUIRED_COLUMNS.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_COLUMNS)
_HEAD_REQUIRED_NOT_NULL.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_NOT_NULL)
_HEAD_REQUIRED_UNIQUES.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_UNIQUES)
_HEAD_REQUIRED_FOREIGN_KEYS.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_FOREIGN_KEYS)
_HEAD_REQUIRED_CHECK_FRAGMENTS.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS)
_HEAD_REQUIRED_INDEXES.update(ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_INDEXES)
ENTERPRISE_AUTOMATION_WORKFLOWS_ISSUE_FRAGMENTS = (
    tuple(f"{table}." for table in sorted(ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES))
    + ENTERPRISE_TASK_OPERATIONS_ISSUE_FRAGMENTS
)


_CONTENT_RECOVERY_EVENT_INSERT_TRIGGER = "trg_tenant_document_recovery_events_validate_insert"
_CONTENT_RECOVERY_IMMUTABLE_TRIGGER_NAMES = frozenset(
    {
        "trg_tenant_document_recovery_events_no_update",
        "trg_tenant_document_recovery_events_no_delete",
    }
)
_CONTENT_RECOVERY_EVENT_TYPES = frozenset(
    {
        "recycled",
        "restored",
        "hold_applied",
        "hold_released",
        "purge_requested",
        "purge_approved",
        "purge_cancelled",
    }
)
_CONTENT_RECOVERY_ACTIVE_ENTRY_STATUSES = frozenset({"recycled", "restoring", "purge_requested"})
_CONTENT_RECOVERY_TERMINAL_ENTRY_STATUSES = frozenset({"restored", "purged", "failed"})


def _content_recovery_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    elif dialect == "postgresql":
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        for function_name, label in (
            ("rag4c_content_recovery_event_immutable", "immutable"),
            ("rag4c_content_recovery_event_validate", "insert"),
        ):
            exists = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname=:name)"),
                {"name": function_name},
            )
            if not exists:
                return (f"missing Content Recovery event {label} trigger function",)
    else:
        return ()

    issues: list[str] = []
    for name in sorted(_CONTENT_RECOVERY_IMMUTABLE_TRIGGER_NAMES):
        definition = definitions.get(name, "").casefold()
        if not definition:
            issues.append(f"missing Content Recovery immutable trigger {name}")
        elif name.endswith("no_update") and "update" not in definition:
            issues.append(f"invalid Content Recovery immutable trigger {name}")
        elif name.endswith("no_delete") and "delete" not in definition:
            issues.append(f"invalid Content Recovery immutable trigger {name}")
    insert_definition = definitions.get(_CONTENT_RECOVERY_EVENT_INSERT_TRIGGER, "").casefold()
    if not insert_definition:
        issues.append(
            "missing Content Recovery event insert trigger "
            + _CONTENT_RECOVERY_EVENT_INSERT_TRIGGER
        )
    else:
        for fragment in ("insert", "recycle_entry", "previous_event_digest", "sequence"):
            if fragment not in insert_definition:
                issues.append(
                    "invalid Content Recovery event insert trigger "
                    + _CONTENT_RECOVERY_EVENT_INSERT_TRIGGER
                )
                break
    return tuple(sorted(set(issues)))


def _content_recovery_boolean(value: Any) -> bool:
    if type(value) is bool:
        return value
    if type(value) is int and value in {0, 1}:
        return bool(value)
    raise ValueError("Content Recovery boolean is invalid")


def _content_recovery_json_object(value: Any) -> dict[str, Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, Mapping):
        raise ValueError("Content Recovery JSON value is not an object")
    return dict(decoded)


def _content_recovery_data_issues(connection: Any) -> tuple[str, ...]:
    """Recompute Stage 23 safe identities, snapshots and event-chain digests."""

    issues: list[str] = []
    digest = re.compile(r"^[0-9a-f]{64}$")
    unsafe = re.compile(
        r"(?i)(?:raw[ _-]+content|document[ _-]+body|raw[ _-]+metadata|metadata|query|"
        r"note|comment|ticket|token|credential|password|authorization|https?://|"
        r"mysql://|postgres(?:ql)?://|file://)"
    )
    try:
        policies = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_content_retention_policies")
            ).mappings()
        ]
        entries = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_document_recycle_entries")
            ).mappings()
        ]
        holds = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_document_legal_holds")
            ).mappings()
        ]
        purge_requests = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_document_purge_requests")
            ).mappings()
        ]
        events = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_document_recovery_events")
            ).mappings()
        ]
    except Exception as exc:
        return (f"Content Recovery data inspection failed: {exc.__class__.__name__}",)

    try:
        from core.enterprise_content_recovery import (
            canonical_purge_request_digest,
            canonical_recycle_key,
            canonical_recycle_snapshot,
            canonical_recovery_event,
        )
    except Exception as exc:
        return (f"Content Recovery canonical authority unavailable: {exc.__class__.__name__}",)

    policy_tenants: set[str] = set()
    for row in policies:
        tenant = str(row.get("tenant_id") or "")
        if tenant in policy_tenants:
            issues.append(f"duplicate Content Recovery retention policy {tenant}")
        policy_tenants.add(tenant)
        if str(row.get("status") or "") not in {"active", "paused"}:
            issues.append(f"invalid Content Recovery retention policy status {tenant}")
        retention = row.get("retention_days")
        if type(retention) is not int or not 1 <= retention <= 3650:
            issues.append(f"invalid Content Recovery retention days {tenant}")
        try:
            _content_recovery_boolean(row.get("auto_purge_enabled"))
        except ValueError:
            issues.append(f"invalid Content Recovery auto purge flag {tenant}")
        try:
            _content_recovery_boolean(row.get("purge_requires_approval"))
        except ValueError:
            issues.append(f"invalid Content Recovery approval flag {tenant}")
        if type(row.get("revision")) is not int or row.get("revision", 0) <= 0:
            issues.append(f"invalid Content Recovery retention revision {tenant}")

    entry_keys: set[tuple[str, str]] = set()
    entries_by_scope: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in entries:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        document = str(row.get("document_id") or "")
        entry_id = str(row.get("id") or "")
        status = str(row.get("status") or "")
        entries_by_scope[(tenant, dataset, entry_id)] = row
        active_status = status in _CONTENT_RECOVERY_ACTIVE_ENTRY_STATUSES
        active_key = row.get("active_recycle_key")
        try:
            expected_key = canonical_recycle_key(dataset, document)
        except Exception:
            expected_key = ""
            issues.append(f"invalid Content Recovery recycle identity {tenant}/{entry_id}")
        if active_status:
            if active_key != expected_key:
                issues.append(f"invalid Content Recovery active recycle key {tenant}/{entry_id}")
            if (tenant, str(active_key)) in entry_keys:
                issues.append(
                    f"duplicate Content Recovery active recycle key {tenant}/{active_key}"
                )
            entry_keys.add((tenant, str(active_key)))
        elif active_key is not None:
            issues.append(f"terminal Content Recovery entry has active key {tenant}/{entry_id}")
        if (
            status
            not in _CONTENT_RECOVERY_ACTIVE_ENTRY_STATUSES
            | _CONTENT_RECOVERY_TERMINAL_ENTRY_STATUSES
        ):
            issues.append(f"invalid Content Recovery entry status {tenant}/{entry_id}")
        if not digest.fullmatch(str(row.get("snapshot_digest") or "")):
            issues.append(f"invalid Content Recovery snapshot digest {tenant}/{entry_id}")
        if unsafe.search(str(row.get("safe_snapshot_json") or "")):
            issues.append(f"unsafe Content Recovery entry snapshot {tenant}/{entry_id}")
        try:
            stored_snapshot = row.get("safe_snapshot_json")
            if isinstance(stored_snapshot, str):
                stored_snapshot = json.loads(stored_snapshot)
            if not isinstance(stored_snapshot, Mapping):
                raise ValueError("stored recovery snapshot is not an object")
            canonical_recycle_snapshot(
                dict(stored_snapshot), snapshot_digest=row.get("snapshot_digest")
            )
        except Exception:
            issues.append(f"invalid canonical Content Recovery snapshot {tenant}/{entry_id}")

        document_row = (
            connection.execute(
                text(
                    "SELECT lifecycle_state, retrieval_enabled FROM documents "
                    "WHERE tenant_id=:tenant_id AND dataset_id=:dataset_id AND id=:document_id"
                ),
                {"tenant_id": tenant, "dataset_id": dataset, "document_id": document},
            )
            .mappings()
            .first()
        )
        if active_status:
            if document_row is None:
                issues.append(f"missing recycled Document {tenant}/{dataset}/{document}")
            elif document_row["lifecycle_state"] != "recycled" or bool(
                document_row["retrieval_enabled"]
            ):
                issues.append(f"recycled Document lifecycle mismatch {tenant}/{dataset}/{document}")
        elif (
            status == "restored"
            and document_row is not None
            and document_row["lifecycle_state"] == "recycled"
        ):
            issues.append(
                f"restored Content Recovery entry still has recycled Document {tenant}/{entry_id}"
            )

    hold_keys: set[tuple[str, str]] = set()
    active_hold_counts: dict[tuple[str, str, str], int] = {}
    for row in holds:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        document = str(row.get("document_id") or "")
        entry_id = str(row.get("recycle_entry_id") or "")
        hold_id = str(row.get("id") or "")
        status = str(row.get("status") or "")
        entry = entries_by_scope.get((tenant, dataset, entry_id))
        expected = f"{entry_id}:{row.get('reason_code') or ''}"
        if status == "active":
            if row.get("active_hold_key") != expected:
                issues.append(f"invalid Content Recovery active hold key {tenant}/{hold_id}")
            if (tenant, str(row.get("active_hold_key"))) in hold_keys:
                issues.append(f"duplicate Content Recovery active hold key {tenant}/{hold_id}")
            hold_keys.add((tenant, str(row.get("active_hold_key"))))
            active_hold_counts[(tenant, dataset, entry_id)] = (
                active_hold_counts.get((tenant, dataset, entry_id), 0) + 1
            )
        elif row.get("active_hold_key") is not None:
            issues.append(f"released Content Recovery hold has active key {tenant}/{hold_id}")
        if status not in {"active", "released"}:
            issues.append(f"invalid Content Recovery hold status {tenant}/{hold_id}")
        if status == "released" and (
            row.get("released_at") is None or row.get("released_by") is None
        ):
            issues.append(f"released Content Recovery hold lacks evidence {tenant}/{hold_id}")
        if entry is None:
            issues.append(f"orphan Content Recovery hold {tenant}/{hold_id}")
        elif entry.get("document_id") != document:
            issues.append(f"Content Recovery hold scope mismatch {tenant}/{hold_id}")
        if unsafe.search(str(row.get("safe_reason") or "")):
            issues.append(f"unsafe Content Recovery hold reason {tenant}/{hold_id}")

    for row in purge_requests:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        document = str(row.get("document_id") or "")
        entry_id = str(row.get("recycle_entry_id") or "")
        request_id = str(row.get("id") or "")
        status = str(row.get("status") or "")
        entry = entries_by_scope.get((tenant, dataset, entry_id))
        if entry is None:
            issues.append(f"orphan Content Recovery purge request {tenant}/{request_id}")
        else:
            if entry.get("document_id") != document:
                issues.append(
                    f"Content Recovery purge request scope mismatch {tenant}/{request_id}"
                )
            if row.get("expected_entry_revision") != entry.get("revision"):
                issues.append(
                    f"stale Content Recovery purge request revision {tenant}/{request_id}"
                )
            if row.get("legal_hold_count_snapshot") != active_hold_counts.get(
                (tenant, dataset, entry_id), 0
            ):
                issues.append(
                    f"Content Recovery purge hold snapshot mismatch {tenant}/{request_id}"
                )
        if (
            status in {"pending_approval", "approved"}
            and int(row.get("legal_hold_count_snapshot") or 0) != 0
        ):
            issues.append(f"Content Recovery purge request has active hold {tenant}/{request_id}")
        if unsafe.search(str(row.get("retention_snapshot_json") or "")):
            issues.append(f"unsafe Content Recovery purge snapshot {tenant}/{request_id}")
        if not digest.fullmatch(str(row.get("request_digest") or "")):
            issues.append(f"invalid Content Recovery request digest {tenant}/{request_id}")
        if not digest.fullmatch(str(row.get("idempotency_key_digest") or "")):
            issues.append(f"invalid Content Recovery idempotency digest {tenant}/{request_id}")
        approval_id = row.get("approval_request_id")
        if approval_id is not None:
            approval = connection.execute(
                text(
                    "SELECT action_type FROM tenant_approval_requests "
                    "WHERE tenant_id=:tenant_id AND id=:approval_request_id"
                ),
                {"tenant_id": tenant, "approval_request_id": approval_id},
            ).scalar()
            if approval != "document_purge":
                issues.append(f"invalid Content Recovery approval action {tenant}/{request_id}")
        if entry is not None:
            try:
                payload: dict[str, Any] = {
                    "tenant_id": tenant,
                    "recycle_entry_id": entry_id,
                    "dataset_id": dataset,
                    "document_id": document,
                    "recycle_generation": entry.get("recycle_generation"),
                    "entry_revision": row.get("expected_entry_revision"),
                    "purge_eligible_at": entry.get("purge_eligible_at"),
                    "retention_days_snapshot": entry.get("retention_days_snapshot"),
                    "legal_hold_count": row.get("legal_hold_count_snapshot"),
                    "request_digest": row.get("request_digest"),
                    "retention_snapshot_json": row.get("retention_snapshot_json"),
                }
                for key in (
                    "approval_request_id",
                    "requested_at",
                    "requested_by",
                    "expires_at",
                    "status",
                    "idempotency_key_digest",
                ):
                    if row.get(key) is not None:
                        payload[key] = row.get(key)
                expected_digest = canonical_purge_request_digest(payload)
                if expected_digest != row.get("request_digest"):
                    issues.append(
                        f"invalid canonical Content Recovery purge digest {tenant}/{request_id}"
                    )
            except Exception:
                issues.append(
                    f"invalid canonical Content Recovery purge request {tenant}/{request_id}"
                )

    streams: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in events:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        document = str(row.get("document_id") or "")
        entry_id = str(row.get("recycle_entry_id") or "")
        event_id = str(row.get("id") or "")
        stream = (tenant, dataset, document, entry_id)
        streams.setdefault(stream, []).append(row)
        if (tenant, dataset, entry_id) not in entries_by_scope:
            issues.append(f"orphan Content Recovery event {tenant}/{event_id}")
        if str(row.get("event_type") or "") not in _CONTENT_RECOVERY_EVENT_TYPES:
            issues.append(f"invalid Content Recovery event type {tenant}/{event_id}")
        if not digest.fullmatch(str(row.get("event_digest") or "")):
            issues.append(f"invalid Content Recovery event digest {tenant}/{event_id}")
        if unsafe.search(str(row.get("safe_snapshot_json") or "")):
            issues.append(f"unsafe Content Recovery event snapshot {tenant}/{event_id}")
        try:
            entry = entries_by_scope.get((tenant, dataset, entry_id))
            canonical = canonical_recovery_event(
                tenant_id=tenant,
                recycle_entry_id=entry_id,
                dataset_id=dataset,
                document_id=document,
                recycle_generation=entry.get("recycle_generation") if entry else None,
                sequence=row.get("sequence"),
                event_type=row.get("event_type"),
                previous_event_digest=row.get("previous_event_digest"),
                event_digest=row.get("event_digest"),
                actor_id=row.get("actor_id"),
                request_id=row.get("request_id"),
                safe_snapshot=_content_recovery_json_object(row.get("safe_snapshot_json")),
                occurred_at=row.get("occurred_at"),
            )
            if canonical.get("event_digest") != row.get("event_digest"):
                issues.append(
                    f"invalid canonical Content Recovery event digest {tenant}/{event_id}"
                )
        except Exception:
            issues.append(f"invalid canonical Content Recovery event {tenant}/{event_id}")

    for tenant, dataset, entry_id in entries_by_scope:
        document_id = str(entries_by_scope[(tenant, dataset, entry_id)].get("document_id") or "")
        if (tenant, dataset, document_id, entry_id) not in streams:
            issues.append(f"missing Content Recovery event stream {tenant}/{entry_id}")

    for stream, stream_events in streams.items():
        ordered = sorted(
            stream_events,
            key=lambda value: (int(value.get("sequence") or 0), str(value.get("id") or "")),
        )
        tenant, _dataset, _document, entry_id = stream
        if (
            not ordered
            or int(ordered[0].get("sequence") or 0) != 1
            or ordered[0].get("event_type") != "recycled"
            or ordered[0].get("previous_event_digest") is not None
        ):
            issues.append(f"invalid Content Recovery event stream start {tenant}/{entry_id}")
        for previous, current in zip(ordered, ordered[1:]):
            if int(current.get("sequence") or 0) != int(previous.get("sequence") or 0) + 1:
                issues.append(f"non-contiguous Content Recovery event stream {tenant}/{entry_id}")
            if current.get("previous_event_digest") != previous.get("event_digest"):
                issues.append(f"broken Content Recovery event hash chain {tenant}/{entry_id}")

    return tuple(sorted(set(issues)))


def _enterprise_content_recovery_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = ENTERPRISE_CONTENT_RECOVERY_TABLES - tables
    issues: list[str] = [f"missing table {name}" for name in sorted(missing_tables)]
    if missing_tables:
        return tuple(sorted(issues))

    for table in sorted(ENTERPRISE_CONTENT_RECOVERY_TABLES):
        actual_columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        required_columns = ENTERPRISE_CONTENT_RECOVERY_REQUIRED_COLUMNS[table]
        issues.extend(
            f"missing column {table}.{name}"
            for name in sorted(required_columns - set(actual_columns))
        )
        for name in ENTERPRISE_CONTENT_RECOVERY_REQUIRED_NOT_NULL[table]:
            if name in actual_columns and bool(actual_columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")
        actual_uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_CONTENT_RECOVERY_REQUIRED_UNIQUES[table].items():
            if actual_uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        actual_foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_CONTENT_RECOVERY_REQUIRED_FOREIGN_KEYS[table].items():
            if actual_foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        for name, contract in actual_foreign_keys.items():
            if contract[0] and contract[0][0] != "tenant_id":
                issues.append(f"non-tenant-leading foreign key {table}.{name}")
        actual_checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        for name, fragments in ENTERPRISE_CONTENT_RECOVERY_REQUIRED_CHECK_FRAGMENTS[table].items():
            sql = _parenless_sql(actual_checks.get(name))
            if not sql or any(_parenless_sql(fragment) not in sql for fragment in fragments):
                issues.append(f"missing or invalid check {table}.{name}")
        actual_indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_CONTENT_RECOVERY_REQUIRED_INDEXES[table].items():
            if actual_indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")

    document_checks = {
        str(item.get("name")): str(item.get("sqltext") or "")
        for item in inspector.get_check_constraints("documents")
        if item.get("name")
    }
    if "recycled" not in _normalized_sql(document_checks.get("ck_documents_lifecycle_state")):
        issues.append("missing documents recycled lifecycle state")
    for table in ("tenant_approval_policies", "tenant_approval_requests"):
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        actual = checks.get(f"ck_{table}_action_type")
        expected = ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[
            ENTERPRISE_CONTENT_RECOVERY_REVISION
        ][table][f"ck_{table}_action_type"]
        if not actual or _parenless_sql(actual) != _parenless_sql(expected):
            issues.append(f"missing or invalid {table} document_purge action check")

    issues.extend(_content_recovery_guard_issues(connection))
    issues.extend(_content_recovery_data_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_content_recovery_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage 23 Content Recovery authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        recovery_present = bool(ENTERPRISE_CONTENT_RECOVERY_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
            QA_FAQ_OPS_REVISION,
            STORAGE_BACKENDS_REVISION,
            ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not recovery_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0033, 0033 or 0034 revision",)
        issues = _enterprise_content_recovery_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Content Recovery schema inspection failed: {exc.__class__.__name__}",
        )


_TASK_EVENT_INSERT_TRIGGER = "trg_tenant_task_events_validate_insert"
_TASK_EVENT_IMMUTABLE_TRIGGER_NAMES = frozenset(
    {"trg_tenant_task_events_no_update", "trg_tenant_task_events_no_delete"}
)


def _task_event_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    elif dialect == "postgresql":
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        for function_name, label in (
            ("rag4c_task_event_immutable", "immutable"),
            ("rag4c_task_event_validate", "insert"),
        ):
            exists = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname=:name)"),
                {"name": function_name},
            )
            if not exists:
                return (f"missing Task Operations event {label} trigger function",)
    else:
        return ()

    issues: list[str] = []
    for name in sorted(_TASK_EVENT_IMMUTABLE_TRIGGER_NAMES):
        definition = definitions.get(name, "").casefold()
        if not definition:
            issues.append(f"missing Task Operations immutable trigger {name}")
        elif name.endswith("no_update") and "update" not in definition:
            issues.append(f"invalid Task Operations immutable trigger {name}")
        elif name.endswith("no_delete") and "delete" not in definition:
            issues.append(f"invalid Task Operations immutable trigger {name}")
    insert_definition = definitions.get(_TASK_EVENT_INSERT_TRIGGER, "").casefold()
    if not insert_definition:
        issues.append(f"missing Task Operations event insert trigger {_TASK_EVENT_INSERT_TRIGGER}")
    elif any(
        fragment not in insert_definition
        for fragment in ("insert", "materialized", "previous_event_digest", "sequence")
    ):
        issues.append(f"invalid Task Operations event insert trigger {_TASK_EVENT_INSERT_TRIGGER}")
    return tuple(sorted(set(issues)))


def _task_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None
    return None


def _task_operations_data_issues(connection: Any) -> tuple[str, ...]:
    """Validate Stage24 currentness fences, canonical digests and evidence chains."""

    from core.enterprise_task_operations import (
        TaskOperationsAuthorityError,
        canonical_task_digest,
        canonical_task_event,
        canonical_task_projection_digest,
    )

    issues: list[str] = []
    digest = re.compile(r"^[0-9a-f]{64}$")
    try:
        projections = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_task_projections")).mappings()
        ]
        actions = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_task_operator_actions")
            ).mappings()
        ]
        events = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_task_events")).mappings()
        ]
        views = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_task_saved_views")).mappings()
        ]
        runs = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_task_reconciliation_runs")
            ).mappings()
        ]
    except Exception as exc:
        return (f"Task Operations data inspection failed: {exc.__class__.__name__}",)

    category_by_source = {
        "document_ingest": "documents",
        "index_operation": "indexing",
        "source_sync": "sources",
        "document_delete": "documents",
        "audit_export": "compliance",
        "release_quality_scan": "quality",
        "release_recertification": "quality",
    }
    route_by_source = {
        "document_ingest": "documents",
        "index_operation": "documents",
        "source_sync": "sources",
        "document_delete": "documents",
        "audit_export": "compliance",
        "release_quality_scan": "quality",
        "release_recertification": "quality",
    }
    projections_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
    for row in projections:
        tenant, task_id = str(row.get("tenant_id") or ""), str(row.get("id") or "")
        label = f"{tenant}/{task_id}"
        projections_by_scope[(tenant, task_id)] = row
        source_kind = str(row.get("source_kind") or "")
        if row.get("category") != category_by_source.get(source_kind):
            issues.append(f"invalid Task Operations projection category {label}")
        route_code = row.get("target_route_code")
        if route_code is not None and route_code != route_by_source.get(source_kind):
            issues.append(f"invalid Task Operations projection route {label}")
        if not bool(row.get("source_current")) and row.get("normalized_status") != "unavailable":
            issues.append(f"stale Task Operations projection is not unavailable {label}")
        for field in ("source_digest", "projection_digest"):
            if digest.fullmatch(str(row.get(field) or "")) is None:
                issues.append(f"invalid Task Operations projection {field} {label}")
        params = _task_json_object(row.get("target_route_params_json"))
        if row.get("target_route_params_json") is not None and params is None:
            issues.append(f"invalid Task Operations projection route params {label}")
        try:
            digest_payload = {
                key: row.get(key)
                for key in (
                    "id",
                    "tenant_id",
                    "source_kind",
                    "source_id",
                    "source_revision",
                    "source_digest",
                    "dataset_id",
                    "workspace_id",
                    "category",
                    "normalized_status",
                    "progress_percent",
                    "attempt_number",
                    "max_attempts",
                    "lease_owner",
                    "lease_until",
                    "safe_error_code",
                    "safe_error",
                    "target_route_code",
                    "occurred_at",
                    "started_at",
                    "finished_at",
                    "updated_at",
                )
            }
            digest_payload["action_required"] = bool(row.get("action_required"))
            digest_payload["source_current"] = bool(row.get("source_current"))
            digest_payload["target_route_params_json"] = params
            if canonical_task_projection_digest(digest_payload) != row.get("projection_digest"):
                issues.append(f"invalid Task Operations projection digest {label}")
        except (TaskOperationsAuthorityError, TypeError, ValueError):
            issues.append(f"invalid canonical Task Operations projection {label}")

    for row in actions:
        tenant, action_id = str(row.get("tenant_id") or ""), str(row.get("id") or "")
        label = f"{tenant}/{action_id}"
        projection = projections_by_scope.get((tenant, str(row.get("task_id") or "")))
        if projection is None:
            issues.append(f"orphan Task Operations action {label}")
            continue
        if row.get("expected_source_revision") != projection.get("source_revision") or row.get(
            "expected_source_digest"
        ) != projection.get("source_digest"):
            issues.append(f"stale Task Operations action source fence {label}")
        if not bool(projection.get("source_current")) and row.get("status") in {
            "requested",
            "dispatched",
        }:
            issues.append(f"active Task Operations action targets stale source {label}")
        if digest.fullmatch(str(row.get("idempotency_key_digest") or "")) is None:
            issues.append(f"invalid Task Operations action idempotency digest {label}")

    streams: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in events:
        streams.setdefault(
            (str(row.get("tenant_id") or ""), str(row.get("task_id") or "")), []
        ).append(row)
    for (tenant, task_id), stream in streams.items():
        ordered = sorted(
            stream, key=lambda row: (int(row.get("sequence") or 0), str(row.get("id")))
        )
        label = f"{tenant}/{task_id}"
        if (
            not ordered
            or int(ordered[0].get("sequence") or 0) != 1
            or ordered[0].get("event_type") != "materialized"
            or ordered[0].get("previous_event_digest") is not None
        ):
            issues.append(f"invalid Task Operations event stream start {label}")
        for previous, current in zip(ordered, ordered[1:]):
            if int(current.get("sequence") or 0) != int(previous.get("sequence") or 0) + 1:
                issues.append(f"non-contiguous Task Operations event stream {label}")
            if current.get("previous_event_digest") != previous.get("event_digest"):
                issues.append(f"broken Task Operations event hash chain {label}")
        for row in ordered:
            snapshot = _task_json_object(row.get("safe_snapshot_json"))
            if snapshot is None:
                issues.append(f"invalid Task Operations event snapshot {label}")
                continue
            try:
                projection = projections_by_scope.get((tenant, task_id))
                if projection is None:
                    raise ValueError("event projection is missing")
                canonical_task_event(
                    {
                        "tenant_id": tenant,
                        "task_id": task_id,
                        "source_kind": projection.get("source_kind"),
                        "source_id": projection.get("source_id"),
                        "source_revision": projection.get("source_revision"),
                        "source_digest": projection.get("source_digest"),
                        "sequence": row.get("sequence"),
                        "event_type": row.get("event_type"),
                        "previous_event_digest": row.get("previous_event_digest"),
                        "event_digest": row.get("event_digest"),
                        "actor_id": row.get("actor_id"),
                        "request_id": row.get("request_id"),
                        "safe_snapshot": snapshot,
                        "occurred_at": row.get("occurred_at"),
                    }
                )
            except (TaskOperationsAuthorityError, TypeError, ValueError):
                issues.append(f"invalid canonical Task Operations event {label}/{row.get('id')}")

    allowed_filter_keys = {
        "source_kinds",
        "categories",
        "statuses",
        "action_required",
        "dataset_id",
        "workspace_id",
        "occurred_from",
        "occurred_to",
    }
    for row in views:
        tenant, view_id = str(row.get("tenant_id") or ""), str(row.get("id") or "")
        label = f"{tenant}/{view_id}"
        filters = _task_json_object(row.get("filters_json"))
        if filters is None or set(filters) - allowed_filter_keys:
            issues.append(f"invalid Task Operations saved view filters {label}")
            continue
        try:
            expected = canonical_task_digest("task-view-filters", filters)
        except (TaskOperationsAuthorityError, TypeError, ValueError):
            issues.append(f"invalid canonical Task Operations saved view filters {label}")
            continue
        if row.get("filter_digest") != expected:
            issues.append(f"invalid Task Operations saved view filter digest {label}")
        start, end = filters.get("occurred_from"), filters.get("occurred_to")
        if (start is None) != (end is None) or (start is not None and str(start) > str(end)):
            issues.append(f"invalid Task Operations saved view time range {label}")

    for row in runs:
        tenant, run_id = str(row.get("tenant_id") or ""), str(row.get("id") or "")
        label = f"{tenant}/{run_id}"
        source_kinds = row.get("source_kinds_json")
        if isinstance(source_kinds, str):
            try:
                source_kinds = json.loads(source_kinds)
            except (TypeError, ValueError):
                source_kinds = None
        if (
            not isinstance(source_kinds, list)
            or not source_kinds
            or len(source_kinds) > len(ENTERPRISE_TASK_SOURCE_KINDS)
            or len(set(source_kinds)) != len(source_kinds)
            or any(kind not in ENTERPRISE_TASK_SOURCE_KINDS for kind in source_kinds)
        ):
            issues.append(f"invalid Task Operations reconciliation source scope {label}")
        counts = [
            int(row.get(name) or 0)
            for name in (
                "source_count",
                "created_count",
                "updated_count",
                "stale_count",
                "invalid_count",
            )
        ]
        if any(value < 0 for value in counts):
            issues.append(f"invalid Task Operations reconciliation counts {label}")
        status = row.get("status")
        completed_at = row.get("completed_at")
        if (status == "running" and completed_at is not None) or (
            status in {"completed", "failed"} and completed_at is None
        ):
            issues.append(f"invalid Task Operations reconciliation lifecycle {label}")
        if status == "failed" and (
            row.get("safe_error_code") is None or row.get("safe_error") is None
        ):
            issues.append(f"failed Task Operations reconciliation lacks safe error {label}")
        if status != "failed" and (
            row.get("safe_error_code") is not None or row.get("safe_error") is not None
        ):
            issues.append(f"non-failed Task Operations reconciliation has safe error {label}")

    return tuple(sorted(set(issues)))


def _enterprise_task_operations_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = ENTERPRISE_TASK_OPERATIONS_TABLES - tables
    issues: list[str] = [f"missing table {name}" for name in sorted(missing_tables)]
    if missing_tables:
        return tuple(sorted(issues))

    for table in sorted(ENTERPRISE_TASK_OPERATIONS_TABLES):
        actual_columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        issues.extend(
            f"missing column {table}.{name}"
            for name in sorted(
                ENTERPRISE_TASK_OPERATIONS_REQUIRED_COLUMNS[table] - set(actual_columns)
            )
        )
        for name in ENTERPRISE_TASK_OPERATIONS_REQUIRED_NOT_NULL[table]:
            if name in actual_columns and bool(actual_columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")
        actual_uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_TASK_OPERATIONS_REQUIRED_UNIQUES[table].items():
            if actual_uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        actual_foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_TASK_OPERATIONS_REQUIRED_FOREIGN_KEYS[table].items():
            if actual_foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        for name, contract in actual_foreign_keys.items():
            if contract[0] and contract[0][0] != "tenant_id":
                issues.append(f"non-tenant-leading foreign key {table}.{name}")
        actual_checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        for name, fragments in ENTERPRISE_TASK_OPERATIONS_REQUIRED_CHECK_FRAGMENTS[table].items():
            sql = _parenless_sql(actual_checks.get(name))
            if not sql or any(_parenless_sql(fragment) not in sql for fragment in fragments):
                issues.append(f"missing or invalid check {table}.{name}")
        actual_indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_TASK_OPERATIONS_REQUIRED_INDEXES[table].items():
            if actual_indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")

    issues.extend(_task_event_guard_issues(connection))
    issues.extend(_task_operations_data_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_task_operations_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage24 Task Operations authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        task_present = bool(ENTERPRISE_TASK_OPERATIONS_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not task_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0034, 0034 or 0035 revision",)
        issues = _enterprise_task_operations_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Task Operations schema inspection failed: {exc.__class__.__name__}",
        )


_AUTOMATION_REVISION_IMMUTABLE_TRIGGERS = frozenset(
    {
        "trg_tenant_automation_rule_revisions_no_update",
        "trg_tenant_automation_rule_revisions_no_delete",
    }
)
_AUTOMATION_EVENT_IMMUTABLE_TRIGGERS = frozenset(
    {
        "trg_tenant_automation_events_no_update",
        "trg_tenant_automation_events_no_delete",
    }
)
_AUTOMATION_EVENT_INSERT_TRIGGER = "trg_tenant_automation_events_validate_insert"


def _automation_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    supported_dialects = {"sqlite", "mysql", "mariadb", "postgresql"}
    if dialect not in supported_dialects:
        return (f"unsupported Automation database dialect {dialect}",)

    automation_revision_table = "tenant_automation_rule_revisions"
    automation_event_table = "tenant_automation_events"
    definitions: dict[str, str] = {}
    function_definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{table} {timing} {event} {statement}"
            for name, table, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION,ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    else:
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        for function_name in ("rag4c_automation_immutable", "rag4c_automation_event_validate"):
            definition = connection.scalar(
                text(
                    "SELECT pg_get_functiondef(oid) FROM pg_proc "
                    "WHERE proname=:name ORDER BY oid LIMIT 1"
                ),
                {"name": function_name},
            )
            function_definitions[function_name] = "" if not definition else str(definition)

    issues: list[str] = []
    immutable_triggers = (
        _AUTOMATION_REVISION_IMMUTABLE_TRIGGERS | _AUTOMATION_EVENT_IMMUTABLE_TRIGGERS
    )
    for name in sorted(immutable_triggers):
        definition = definitions.get(name, "").casefold()
        table = automation_event_table if "events" in name else automation_revision_table
        operation = "update" if name.endswith("no_update") else "delete"
        if not definition:
            issues.append(f"missing Automation immutable trigger {name}")
            continue
        required = (table.casefold(), operation)
        if any(fragment not in definition for fragment in required):
            issues.append(f"invalid Automation immutable trigger {name}")
            continue
        if dialect == "postgresql":
            if "rag4c_automation_immutable" not in definition:
                issues.append(f"invalid Automation immutable trigger {name}")
        elif not any(marker in definition for marker in ("raise", "signal")):
            issues.append(f"invalid Automation immutable trigger {name}")

    insert = definitions.get(_AUTOMATION_EVENT_INSERT_TRIGGER, "").casefold()
    if not insert:
        issues.append(f"missing Automation event insert trigger {_AUTOMATION_EVENT_INSERT_TRIGGER}")
    else:
        required_insert = (
            automation_event_table.casefold(),
            "insert",
            "tenant_id",
            "stream_key",
            "previous_event_digest",
            "sequence",
        )
        if dialect == "postgresql":
            required_insert = required_insert + ("rag4c_automation_event_validate",)
        else:
            required_insert = required_insert + ("event_digest",)
        if any(fragment not in insert for fragment in required_insert):
            issues.append(
                f"invalid Automation event insert trigger {_AUTOMATION_EVENT_INSERT_TRIGGER}"
            )

    if dialect == "postgresql":
        for function_name, definition in function_definitions.items():
            if not definition:
                issues.append(f"missing Automation trigger function {function_name}")
        immutable_function = function_definitions.get("rag4c_automation_immutable", "").casefold()
        event_function = function_definitions.get("rag4c_automation_event_validate", "").casefold()
        if immutable_function and "raise exception" not in immutable_function:
            issues.append("invalid Automation trigger function rag4c_automation_immutable")
        if event_function and any(
            fragment not in event_function
            for fragment in (
                "tenant_id",
                "stream_key",
                "previous_event_digest",
                "sequence",
                "event_digest",
            )
        ):
            issues.append("invalid Automation trigger function rag4c_automation_event_validate")

    return tuple(sorted(set(issues)))


def _automation_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return None


def _automation_check_allowlist_values(sql: str | None, column_name: str) -> tuple[str, ...] | None:
    """Parse reflected IN or PostgreSQL ANY(ARRAY[]) enum checks exactly."""

    normalized = _normalized_sql(sql)
    match = re.search(
        rf"\b{re.escape(column_name)}\s+in\s*\(([^()]*)\)",
        normalized,
    )
    body: str | None = match.group(1) if match is not None else None
    if body is None:
        postgres = re.search(
            rf"(?:\(\s*)?\b{re.escape(column_name)}\b(?:\s*\))?"
            r"(?:\s*::\s*(?:character\s+varying|text))?\s*=\s*any\s*"
            r"\(\s*\(?\s*array\s*\[(.*?)\]\s*"
            r"(?:::\s*(?:character\s+varying|text)\s*\[\])?\s*\)?\s*\)",
            normalized,
        )
        body = postgres.group(1) if postgres is not None else None
    if body is None:
        return None
    values = tuple(re.findall(r"'([^']*)'", body))
    residue = re.sub(r"'[^']*'", "", body)
    residue = re.sub(r"::\s*(?:character\s+varying|text)(?:\s*\[\])?", "", residue)
    if not values or re.sub(r"[\s,()]", "", residue):
        return None
    return values


def _automation_exact_allowlist_issues(
    checks_by_table: Mapping[str, Mapping[str, str]],
) -> tuple[str, ...]:
    from core.enterprise_automation_workflows import (
        AUTOMATION_ACTION_CODES,
        AUTOMATION_CONDITION_CODES,
        AUTOMATION_EVENT_TYPES,
        AUTOMATION_TRIGGER_CODES,
    )

    specifications = (
        (
            "tenant_automation_rules",
            "ck_tenant_automation_rules_status",
            "status",
            frozenset({"draft", "active", "paused", "archived"}),
            "rule status",
        ),
        (
            "tenant_automation_source_cursors",
            "ck_tenant_automation_source_cursors_source_kind",
            "source_kind",
            AUTOMATION_TRIGGER_CODES,
            "trigger",
        ),
        (
            "tenant_automation_rule_revisions",
            "ck_tenant_automation_rule_revisions_trigger",
            "trigger_code",
            AUTOMATION_TRIGGER_CODES,
            "trigger",
        ),
        (
            "tenant_automation_rule_revisions",
            "ck_tenant_automation_rule_revisions_condition",
            "condition_code",
            AUTOMATION_CONDITION_CODES,
            "condition",
        ),
        (
            "tenant_automation_source_cursors",
            "ck_tenant_automation_source_cursors_status",
            "status",
            frozenset({"idle", "claimed", "blocked"}),
            "cursor status",
        ),
        (
            "tenant_automation_runs",
            "ck_tenant_automation_runs_status",
            "status",
            frozenset({"started", "not_matched", "requested", "completed", "failed", "blocked"}),
            "run status",
        ),
        (
            "tenant_automation_events",
            "ck_tenant_automation_events_type",
            "event_type",
            AUTOMATION_EVENT_TYPES,
            "event",
        ),
        (
            "tenant_automation_action_requests",
            "ck_tenant_automation_action_requests_action",
            "action_code",
            AUTOMATION_ACTION_CODES,
            "action",
        ),
        (
            "tenant_automation_action_requests",
            "ck_tenant_automation_action_requests_status",
            "status",
            frozenset({"requested", "dispatched", "applied", "rejected", "expired"}),
            "action status",
        ),
    )
    issues: list[str] = []
    for table, check_name, column_name, expected, label in specifications:
        sql = checks_by_table.get(table, {}).get(check_name)
        actual = _automation_check_allowlist_values(sql, column_name)
        if actual is None or len(actual) != len(expected) or set(actual) != set(expected):
            rendered = ",".join(actual) if actual is not None else "unparseable"
            issues.append(
                f"invalid exact Automation {label} allow-list {table}.{check_name}: {rendered}"
            )
    return tuple(issues)


def _automation_data_issues(connection: Any) -> tuple[str, ...]:
    from core.enterprise_automation_workflows import (
        AutomationAuthorityError,
        _identifier,
        canonical_automation_action_plan,
        canonical_automation_action_request_digest,
        canonical_automation_event,
        canonical_automation_rule_revision,
        canonical_automation_run_digest,
    )

    issues: list[str] = []
    digest_re = re.compile(r"^[0-9a-f]{64}$")
    target_kinds = frozenset(
        {"automation_rule", "task", "source", "approval_request", "source_event"}
    )
    sql_like_re = re.compile(
        r"(?i)(?:--|/\*|\*/|\b(?:select|insert|update|delete|drop|alter|create|union)\b\s+"
        r"(?:\*|[a-z_][a-z0-9_.]*))"
    )
    bearer_or_jwt_re = re.compile(
        r"(?i)(?:\bbearer\s+[a-z0-9._~+/=-]{16,}|\beyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
    )

    try:
        rules = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_automation_rules")).mappings()
        ]
        revisions = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_automation_rule_revisions")
            ).mappings()
        ]
        cursors = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_automation_source_cursors")
            ).mappings()
        ]
        runs = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_automation_runs")).mappings()
        ]
        actions = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_automation_action_requests")
            ).mappings()
        ]
        events = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_automation_events")).mappings()
        ]
    except Exception as exc:
        return (f"Automation data inspection failed: {exc.__class__.__name__}",)

    rule_by_scope = {
        (str(row.get("tenant_id") or ""), str(row.get("id") or "")): row for row in rules
    }
    revision_by_scope = {
        (str(row.get("tenant_id") or ""), str(row.get("id") or "")): row for row in revisions
    }
    canonical_revision_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
    for row in revisions:
        tenant = str(row.get("tenant_id") or "")
        revision_id = str(row.get("id") or "")
        label = f"{tenant}/{revision_id}"
        condition_params = _automation_json(row.get("condition_params_json"))
        action_plan = _automation_json(row.get("action_plan_json"))
        if not isinstance(condition_params, dict) or not isinstance(action_plan, list):
            issues.append(f"invalid bounded Automation definition {label}")
            continue
        normalized_actions = []
        for item in action_plan:
            if not isinstance(item, Mapping):
                normalized_actions.append(item)
                continue
            normalized_actions.append(
                {key: value for key, value in dict(item).items() if key != "step_index"}
            )
        try:
            canonical = canonical_automation_rule_revision(
                tenant_id=tenant,
                rule_id=str(row.get("rule_id") or ""),
                rule_revision_id=revision_id,
                revision=row.get("revision"),
                trigger_code=row.get("trigger_code"),
                condition_code=row.get("condition_code"),
                condition_params=condition_params,
                action_plan=normalized_actions,
                created_at=row.get("created_at"),
                created_by=row.get("created_by"),
                definition_digest=row.get("definition_digest"),
            )
            if canonical["definition_digest"] != row.get("definition_digest"):
                issues.append(f"invalid Automation definition digest {label}")
            else:
                canonical_revision_by_scope[(tenant, revision_id)] = canonical
        except (AutomationAuthorityError, TypeError, ValueError):
            issues.append(f"invalid bounded Automation action plan or definition digest {label}")

    for row in rules:
        tenant, rule_id = str(row.get("tenant_id") or ""), str(row.get("id") or "")
        label = f"{tenant}/{rule_id}"
        current = row.get("current_revision_id")
        revision = (
            revision_by_scope.get((tenant, str(current or ""))) if current is not None else None
        )
        rule_revision = row.get("revision")
        if type(rule_revision) is not int or rule_revision <= 0:
            issues.append(f"invalid Automation rule revision {label}")
        if current is not None and (
            revision is None
            or str(revision.get("rule_id")) != rule_id
            or type(revision.get("revision")) is not int
            or type(rule_revision) is not int
            or int(revision.get("revision") or 0) > int(rule_revision or 0)
        ):
            issues.append(f"Automation rule current revision mismatch {label}")
        elif row.get("status") == "active" and current is None:
            issues.append(f"active Automation rule current revision mismatch {label}")

    for row in cursors:
        label = f"{row.get('tenant_id')}/{row.get('id')}"
        sequence = row.get("last_sequence")
        event_digest = row.get("last_event_digest")
        if type(sequence) is not int or sequence < 0:
            issues.append(f"invalid Automation cursor sequence {label}")
        elif (sequence == 0 and event_digest is not None) or (
            sequence > 0 and digest_re.fullmatch(str(event_digest or "")) is None
        ):
            issues.append(f"invalid Automation cursor digest {label}")
        if (row.get("lease_owner") is None) != (row.get("lease_until") is None):
            issues.append(f"invalid Automation cursor lease {label}")

    run_by_scope = {
        (str(row.get("tenant_id") or ""), str(row.get("id") or "")): row for row in runs
    }
    actions_by_run_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in actions:
        actions_by_run_scope.setdefault(
            (str(row.get("tenant_id") or ""), str(row.get("run_id") or "")), []
        ).append(row)

    for row in runs:
        tenant = str(row.get("tenant_id") or "")
        run_id = str(row.get("id") or "")
        rule_id = str(row.get("rule_id") or "")
        revision_id = str(row.get("rule_revision_id") or "")
        label = f"{tenant}/{run_id}"
        status = str(row.get("status") or "")
        completed_at = row.get("completed_at")
        if (status == "started" and completed_at is not None) or (
            status != "started" and completed_at is None
        ):
            issues.append(f"invalid Automation run lifecycle {label}")

        count_values = {
            name: row.get(name) for name in ("action_count", "requested_count", "rejected_count")
        }
        if any(type(value) is not int for value in count_values.values()):
            issues.append(f"invalid Automation run counts {label}")
        else:
            counts = {name: int(value) for name, value in count_values.items()}
            if any(value < 0 for value in counts.values()) or (
                counts["requested_count"] + counts["rejected_count"] > counts["action_count"]
            ):
                issues.append(f"invalid Automation run counts {label}")
            action_rows = actions_by_run_scope.get((tenant, run_id), [])
            if counts["requested_count"] != len(action_rows) or (
                counts["action_count"] != counts["requested_count"] + counts["rejected_count"]
            ):
                issues.append(f"invalid Automation run action counts {label}")

        rule = rule_by_scope.get((tenant, rule_id))
        revision = revision_by_scope.get((tenant, revision_id))
        if rule is None or revision is None or str(revision.get("rule_id")) != rule_id:
            issues.append(f"Automation run ownership mismatch {label}")

        trigger_digest = row.get("trigger_event_digest")
        idempotency_digest = row.get("idempotency_digest")
        if digest_re.fullmatch(str(trigger_digest or "")) is None:
            issues.append(f"invalid Automation run trigger digest {label}")
        if digest_re.fullmatch(str(idempotency_digest or "")) is None:
            issues.append(f"invalid Automation run idempotency digest {label}")
        elif isinstance(trigger_digest, str):
            try:
                expected_digest = canonical_automation_run_digest(
                    {
                        "tenant_id": tenant,
                        "rule_id": rule_id,
                        "rule_revision_id": revision_id,
                        "trigger_event_digest": trigger_digest,
                    }
                )
            except (AutomationAuthorityError, TypeError, ValueError):
                issues.append(f"invalid canonical Automation run digest {label}")
            else:
                if expected_digest != idempotency_digest:
                    issues.append(f"invalid Automation run idempotency digest {label}")

    for row in actions:
        tenant = str(row.get("tenant_id") or "")
        action_id = str(row.get("id") or "")
        run_id = str(row.get("run_id") or "")
        rule_id = str(row.get("rule_id") or "")
        label = f"{tenant}/{action_id}"
        run = run_by_scope.get((tenant, run_id))
        if run is None or str(run.get("rule_id") or "") != rule_id:
            issues.append(f"Automation action ownership mismatch {label}")
        revision_id = str(run.get("rule_revision_id") or "") if run is not None else ""
        revision = revision_by_scope.get((tenant, revision_id))
        canonical_revision = canonical_revision_by_scope.get((tenant, revision_id))
        if revision is None or str(revision.get("rule_id") or "") != rule_id:
            issues.append(f"Automation action revision ownership mismatch {label}")

        step_index = row.get("step_index")
        action_code = row.get("action_code")
        safe_params = _automation_json(row.get("safe_params_json"))
        canonical_action: dict[str, Any] | None = None
        if type(step_index) is not int or step_index < 0:
            issues.append(f"invalid Automation action step {label}")
        if not isinstance(safe_params, dict):
            issues.append(f"invalid Automation action params {label}")
        else:
            try:
                canonical_action = canonical_automation_action_plan(
                    [{"action_code": action_code, "params": safe_params}]
                )[0]
            except (AutomationAuthorityError, TypeError, ValueError):
                issues.append(f"invalid canonical Automation action params {label}")
            else:
                if (
                    canonical_action["action_code"] != action_code
                    or canonical_action["params"] != safe_params
                ):
                    issues.append(f"invalid Automation action params {label}")

        if canonical_revision is not None and type(step_index) is int and step_index >= 0:
            action_plan = canonical_revision["action_plan"]
            if step_index >= len(action_plan):
                issues.append(f"Automation action step is not in revision {label}")
            elif (
                canonical_action is None
                or action_plan[step_index]["action_code"] != canonical_action["action_code"]
                or action_plan[step_index]["params"] != canonical_action["params"]
            ):
                issues.append(f"Automation action definition mismatch {label}")

        target_kind = row.get("target_kind")
        target_id = row.get("target_id")
        valid_target = isinstance(target_kind, str) and target_kind in target_kinds
        if valid_target:
            try:
                _identifier(target_id, "target_id", 128)
            except (AutomationAuthorityError, TypeError, ValueError):
                valid_target = False
        if not valid_target:
            issues.append(f"invalid Automation action target allow-list {label}")
        elif target_kind == "automation_rule" and target_id != rule_id:
            issues.append(f"Automation action target ownership mismatch {label}")
        elif target_kind == "automation_rule" and action_code != "pause_rule":
            issues.append(f"Automation action target/action mismatch {label}")

        for kind, field in (("task", "task_id"), ("approval_request", "approval_request_id")):
            reference = row.get(field)
            if reference is not None:
                try:
                    clean_reference = _identifier(reference, field, 128)
                except (AutomationAuthorityError, TypeError, ValueError):
                    issues.append(f"invalid Automation action reference {label}")
                else:
                    if target_kind != kind or clean_reference != target_id:
                        issues.append(f"Automation action target reference mismatch {label}")

        target_revision = row.get("target_revision")
        target_digest = row.get("target_digest")
        if (target_revision is None) != (target_digest is None):
            issues.append(f"invalid Automation action target fence {label}")
        elif target_revision is not None:
            if (
                type(target_revision) is not int
                or target_revision <= 0
                or not digest_re.fullmatch(str(target_digest or ""))
            ):
                issues.append(f"invalid Automation action target fence {label}")
            else:
                # v1 has no single target authority table for every allow-listed target kind;
                # refuse to bless a fence that readiness cannot re-prove from durable fields.
                issues.append(f"unproven Automation action target fence {label}")

        safe_reason = row.get("safe_reason")
        reason_valid = isinstance(safe_reason, str) and bool(safe_reason.strip())
        if reason_valid:
            try:
                if len(safe_reason.encode("utf-8")) > 512:
                    reason_valid = False
                canonical_automation_event(
                    tenant_id=tenant,
                    rule_id=rule_id,
                    run_id=run_id,
                    stream_key=f"readiness:{action_id}",
                    sequence=1,
                    event_type="run_started",
                    previous_event_digest=None,
                    actor_id="system:readiness",
                    request_id="readiness",
                    safe_snapshot={"safe_reason": safe_reason},
                    occurred_at=row.get("requested_at") or row.get("created_at"),
                )
            except (AutomationAuthorityError, TypeError, ValueError):
                reason_valid = False
        if (
            not reason_valid
            or sql_like_re.search(str(safe_reason or ""))
            or bearer_or_jwt_re.search(str(safe_reason or ""))
        ):
            issues.append(f"unsafe Automation action reason {label}")

        status = str(row.get("status") or "")
        dispatched, applied, rejected = (
            row.get("dispatched_at"),
            row.get("applied_at"),
            row.get("rejected_at"),
        )
        valid = (
            (status == "requested" and dispatched is None and applied is None and rejected is None)
            or (
                status == "dispatched"
                and dispatched is not None
                and applied is None
                and rejected is None
            )
            or (status == "applied" and applied is not None and rejected is None)
            or (status == "rejected" and rejected is not None and applied is None)
            or (status == "expired" and applied is None)
        )
        if not valid:
            issues.append(f"invalid Automation action lifecycle {label}")

        action_digest = row.get("idempotency_key_digest")
        if digest_re.fullmatch(str(action_digest or "")) is None:
            issues.append(f"invalid Automation action idempotency digest {label}")
        elif run is not None and canonical_action is not None:
            try:
                expected_action_digest = canonical_automation_action_request_digest(
                    {
                        "tenant_id": tenant,
                        "rule_id": rule_id,
                        "rule_revision_id": revision_id,
                        "trigger_event_digest": run.get("trigger_event_digest"),
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "action_code": action_code,
                        "step_index": step_index,
                        "safe_params": canonical_action["params"],
                    }
                )
            except (AutomationAuthorityError, TypeError, ValueError):
                issues.append(f"invalid canonical Automation action digest {label}")
            else:
                if expected_action_digest != action_digest:
                    issues.append(f"invalid Automation action idempotency digest {label}")

    for row in events:
        tenant = str(row.get("tenant_id") or "")
        run_id = row.get("run_id")
        if run_id is None:
            continue
        run = run_by_scope.get((tenant, str(run_id)))
        label = f"{tenant}/{row.get('id')}"
        if run is None:
            issues.append(f"orphan Automation event run ownership {label}")
        elif str(row.get("rule_id") or "") != str(run.get("rule_id") or ""):
            issues.append(f"Automation event run ownership mismatch {label}")

    streams: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in events:
        streams.setdefault(
            (str(row.get("tenant_id") or ""), str(row.get("stream_key") or "")), []
        ).append(row)
    for (tenant, stream_key), stream in streams.items():
        ordered = sorted(
            stream, key=lambda row: (int(row.get("sequence") or 0), str(row.get("id") or ""))
        )
        previous: str | None = None
        for index, row in enumerate(ordered, start=1):
            label = f"{tenant}/{stream_key}/{row.get('id')}"
            if (
                int(row.get("sequence") or 0) != index
                or row.get("previous_event_digest") != previous
            ):
                issues.append(f"broken Automation event hash chain {tenant}/{stream_key}")
            snapshot = _automation_json(row.get("safe_snapshot_json"))
            if not isinstance(snapshot, dict):
                issues.append(f"invalid Automation event snapshot {label}")
            else:
                try:
                    canonical_automation_event(
                        tenant_id=tenant,
                        rule_id=row.get("rule_id"),
                        run_id=row.get("run_id"),
                        stream_key=stream_key,
                        sequence=row.get("sequence"),
                        event_type=row.get("event_type"),
                        previous_event_digest=row.get("previous_event_digest"),
                        event_digest=row.get("event_digest"),
                        actor_id=row.get("actor_id"),
                        request_id=row.get("request_id"),
                        safe_snapshot=snapshot,
                        occurred_at=row.get("occurred_at"),
                    )
                except (AutomationAuthorityError, TypeError, ValueError):
                    issues.append(f"invalid canonical Automation event {label}")
            previous = str(row.get("event_digest") or "")
    return tuple(sorted(set(issues)))


def _enterprise_automation_workflows_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing = ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES - tables
    issues: list[str] = [f"missing table {table}" for table in sorted(missing)]
    if missing:
        return tuple(sorted(issues))
    checks_by_table: dict[str, dict[str, str]] = {}
    for table in sorted(ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES):
        columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        issues.extend(
            f"missing column {table}.{name}"
            for name in sorted(
                ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_COLUMNS[table] - set(columns)
            )
        )
        for name in ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_NOT_NULL[table]:
            if name in columns and bool(columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")
        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_UNIQUES[table].items():
            if uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_FOREIGN_KEYS[table].items():
            if foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        for name, contract in foreign_keys.items():
            if contract[0] and contract[0][0] != "tenant_id":
                issues.append(f"non-tenant-leading foreign key {table}.{name}")
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        checks_by_table[table] = checks
        for name, fragments in ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            sql = _parenless_sql(checks.get(name))
            if not sql or any(_parenless_sql(fragment) not in sql for fragment in fragments):
                issues.append(f"missing or invalid check {table}.{name}")
        indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_INDEXES[table].items():
            if indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")
    issues.extend(_automation_exact_allowlist_issues(checks_by_table))
    issues.extend(_automation_guard_issues(connection))
    issues.extend(_automation_data_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_automation_workflows_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage25 Automation authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        present = bool(ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision != ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION:
            if not present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0035 or 0035 revision",)
        issues = _enterprise_automation_workflows_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (f"Automation schema inspection failed: {exc.__class__.__name__}",)


_RELEASE_IMMUTABLE_TRIGGER_NAMES = frozenset(
    {
        "trg_dataset_release_manifests_no_update",
        "trg_dataset_release_manifests_no_delete",
        "trg_dataset_release_entries_no_update",
        "trg_dataset_release_entries_no_delete",
        "trg_dataset_release_events_no_update",
        "trg_dataset_release_events_no_delete",
    }
)


def _release_immutable_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    elif dialect == "postgresql":
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        function_exists = connection.scalar(
            text(
                "SELECT 1 FROM pg_proc WHERE proname="
                "'rag4c_dataset_release_content_immutable' LIMIT 1"
            )
        )
        if function_exists is None:
            return ("missing release immutable function rag4c_dataset_release_content_immutable",)
    else:
        return (f"unsupported immutable trigger inspection dialect {dialect}",)

    issues: list[str] = []
    for name in sorted(_RELEASE_IMMUTABLE_TRIGGER_NAMES):
        raw = definitions.get(name)
        if raw is None:
            issues.append(f"missing release immutable trigger {name}")
            continue
        sql = _normalized_sql(raw)
        operation = "update" if name.endswith("no_update") else "delete"
        required = [f"before {operation}"]
        if dialect == "sqlite":
            required.extend(("raise", "abort", "immutable"))
        elif dialect in {"mysql", "mariadb"}:
            required.extend(("signal sqlstate", "45000", "immutable"))
        else:
            required.extend(("execute function", "rag4c_dataset_release_content_immutable"))
        if any(fragment not in sql for fragment in required):
            issues.append(f"invalid release immutable trigger {name}")
    return tuple(issues)


def _release_data_authority_issues(connection: Any) -> tuple[str, ...]:
    issues: list[str] = []
    missing_defaults = list(
        connection.execute(
            text(
                "SELECT t.id FROM tenants AS t LEFT JOIN tenant_release_channels AS c "
                "ON c.tenant_id=t.id AND c.status='active' AND c.is_default_serving=true "
                "WHERE t.status='active' GROUP BY t.id "
                "HAVING COUNT(c.id) <> 1 ORDER BY t.id"
            )
        ).scalars()
    )
    issues.extend(
        f"tenant default release channel invalid {tenant_id}" for tenant_id in missing_defaults
    )
    for code in ("development", "testing", "production"):
        missing_code = list(
            connection.execute(
                text(
                    "SELECT t.id FROM tenants AS t WHERE t.status='active' "
                    "AND NOT EXISTS (SELECT 1 FROM tenant_release_channels AS c "
                    "WHERE c.tenant_id=t.id AND c.status='active' "
                    "AND c.normalized_code=:code) ORDER BY t.id"
                ),
                {"code": code},
            ).scalars()
        )
        issues.extend(
            f"tenant release channel missing {tenant_id}:{code}" for tenant_id in missing_code
        )
    mismatches = list(
        connection.execute(
            text(
                "SELECT d.tenant_id, d.id FROM datasets AS d "
                "JOIN tenant_release_channels AS c ON c.tenant_id=d.tenant_id "
                "AND c.status='active' AND c.is_default_serving=true "
                "LEFT JOIN dataset_channel_releases AS b ON b.tenant_id=d.tenant_id "
                "AND b.dataset_id=d.id AND b.channel_id=c.id AND b.status='active' "
                "WHERE COALESCE(d.serving_release_id, '') <> COALESCE(b.active_release_id, '') "
                "ORDER BY d.tenant_id, d.id"
            )
        ).all()
    )
    issues.extend(
        f"dataset serving release projection mismatch {tenant_id}:{dataset_id}"
        for tenant_id, dataset_id in mismatches
    )
    return tuple(issues)


def _enterprise_knowledge_base_release_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    for table in sorted(ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES):
        if table not in tables:
            issues.append(f"missing table {table}")
    if issues:
        return tuple(sorted(issues))
    required_tables = {
        *ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES,
        "datasets",
        "app_dataset_references",
    }
    for table in sorted(required_tables):
        actual_columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        for column in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_COLUMNS[table]:
            if column not in actual_columns:
                issues.append(f"missing column {table}.{column}")
        for column in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_NOT_NULL[table]:
            if column in actual_columns and actual_columns[column].get("nullable") is not False:
                issues.append(f"nullable required column {table}.{column}")
        actual_uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, columns in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_UNIQUES[table].items():
            if actual_uniques.get(name) != tuple(columns):
                issues.append(f"missing or invalid unique {table}.{name}")
        actual_foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, contract in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_FOREIGN_KEYS[
            table
        ].items():
            if actual_foreign_keys.get(name) != contract:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        actual_checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        exact_checks = ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_EXACT_CHECK_SQL.get(table, {})
        for name, fragments in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            raw_sql = actual_checks.get(name)
            expected_sql = exact_checks.get(name)
            if expected_sql is not None:
                if not raw_sql or _parenless_sql(raw_sql) != _parenless_sql(
                    expected_sql
                ):
                    issues.append(f"missing or invalid exact check {table}.{name}")
            elif not raw_sql or any(
                _parenless_sql(fragment) not in _parenless_sql(raw_sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")
        actual_indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, columns in ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_INDEXES[table].items():
            if actual_indexes.get(name) != tuple(columns):
                issues.append(f"missing or invalid index {table}.{name}")
    for table, constraint_name in (
        ("tenant_approval_policies", "ck_tenant_approval_policies_action_type"),
        ("tenant_approval_requests", "ck_tenant_approval_requests_action_type"),
    ):
        actual = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
        }
        raw_sql = actual.get(constraint_name, "")
        for action in ENTERPRISE_APPROVAL_ACTION_TYPES_0029:
            if action not in raw_sql:
                issues.append(f"missing release approval action {table}.{action}")
    issues.extend(_release_immutable_guard_issues(connection))
    issues.extend(_release_data_authority_issues(connection))
    issues.extend(
        _parent_authority_issues(
            connection,
            label="registry authority",
            inspector=inspect_enterprise_knowledge_base_registry_capability,
        )
    )
    issues.extend(
        _parent_authority_issues(
            connection,
            label="workspace authorization authority",
            inspector=inspect_workspace_authorization_capability,
        )
    )
    return tuple(sorted(set(issues)))


def inspect_enterprise_knowledge_base_release_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return the revision-aware Stage 19 Release authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        release_present = bool(ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
            ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
            ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not release_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0029, 0029 or 0033 revision",)
        issues = _enterprise_knowledge_base_release_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"knowledge base release schema inspection failed: {exc.__class__.__name__}",
        )


_QUALITY_IMMUTABLE_TABLES = ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES - {
    "tenant_release_quality_gate_policies"
}
_QUALITY_POLICY_ACTIVE_SCOPE_KEY_CHECK = "ck_tenant_release_quality_gate_policies_active_scope_key"


def _quality_policy_active_scope_key_expression():
    status = sql_column("status", String(16))
    scope_type = sql_column("scope_type", String(16))
    scope_value = sql_column("scope_value", String(128))
    active_scope_key = sql_column("active_scope_key", String(192))
    return or_(
        status != "active",
        and_(scope_type == "global", active_scope_key == "global:*"),
        and_(
            scope_type == "risk_tier",
            active_scope_key == literal("risk_tier:") + scope_value,
        ),
        and_(
            scope_type == "channel",
            active_scope_key == literal("channel:") + scope_value,
        ),
    )


def _quality_policy_active_scope_key_check_sql(connection: Any) -> str:
    return str(
        _quality_policy_active_scope_key_expression().compile(
            dialect=connection.dialect, compile_kwargs={"literal_binds": True}
        )
    )


def _quality_policy_expected_scope_key(scope_type: Any, scope_value: Any) -> str | None:
    if scope_type == "global" and scope_value == "*":
        return "global:*"
    if scope_type == "risk_tier" and scope_value in {"low", "medium", "high"}:
        return f"risk_tier:{scope_value}"
    if scope_type == "channel" and isinstance(scope_value, str) and scope_value:
        return f"channel:{scope_value}"
    return None


def _release_quality_policy_data_issues(connection: Any) -> tuple[str, ...]:
    issues: list[str] = []
    rows = connection.execute(
        text(
            "SELECT id, tenant_id, scope_type, scope_value, active_scope_key "
            "FROM tenant_release_quality_gate_policies "
            "WHERE status='active' "
            "ORDER BY tenant_id, scope_type, scope_value, id"
        )
    ).all()
    for policy_id, tenant_id, scope_type, scope_value, active_scope_key in rows:
        expected = _quality_policy_expected_scope_key(scope_type, scope_value)
        if expected is None or active_scope_key != expected:
            issues.append(f"invalid active quality policy canonical scope {tenant_id}/{policy_id}")
    duplicate_rows = connection.execute(
        text(
            "SELECT tenant_id, scope_type, scope_value, COUNT(*) "
            "FROM tenant_release_quality_gate_policies "
            "WHERE status='active' "
            "GROUP BY tenant_id, scope_type, scope_value "
            "HAVING COUNT(*) > 1 "
            "ORDER BY tenant_id, scope_type, scope_value"
        )
    ).all()
    for tenant_id, scope_type, scope_value, count in duplicate_rows:
        issues.append(
            f"duplicate active quality policy scope {tenant_id}/{scope_type}/{scope_value}:{count}"
        )
    return tuple(issues)


def _release_quality_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    targets: dict[str, str] = {}
    if dialect == "sqlite":
        rows = connection.execute(
            text("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'")
        ).all()
        definitions = {str(name): str(sql or "") for name, _target, sql in rows}
        targets = {str(name): str(target or "") for name, target, _sql in rows}
    elif dialect in {"mysql", "mariadb"}:
        rows = connection.execute(
            text(
                "SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, ACTION_TIMING, "
                "EVENT_MANIPULATION, ACTION_STATEMENT "
                "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
            )
        ).all()
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, _target, timing, event, statement in rows
        }
        targets = {
            str(name): str(target or "") for name, target, _timing, _event, _statement in rows
        }
    elif dialect == "postgresql":
        rows = connection.execute(
            text(
                "SELECT t.tgname, c.relname, pg_get_triggerdef(t.oid) "
                "FROM pg_trigger AS t "
                "JOIN pg_class AS c ON c.oid=t.tgrelid "
                "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE NOT t.tgisinternal AND n.nspname=current_schema()"
            )
        ).all()
        definitions = {str(name): str(definition or "") for name, _target, definition in rows}
        targets = {str(name): str(target or "") for name, target, _definition in rows}
        function_exists = connection.scalar(
            text("SELECT 1 FROM pg_proc WHERE proname='rag4c_release_quality_immutable' LIMIT 1")
        )
        if function_exists is None:
            return ("missing Release quality immutable function",)
    else:
        return (f"unsupported Release quality guard dialect {dialect}",)
    issues: list[str] = []
    for table in sorted(_QUALITY_IMMUTABLE_TABLES):
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            raw = definitions.get(name)
            if raw is None:
                issues.append(f"missing Release quality immutable trigger {name}")
                continue
            target = targets.get(name, "")
            if target.casefold() != table.casefold():
                issues.append(
                    f"invalid Release quality immutable trigger {name} "
                    f"target table {target or '<missing>'}"
                )
            sql = _normalized_sql(raw)
            required = [f"before {operation}"]
            if dialect == "sqlite":
                required.extend(("raise", "abort", "immutable"))
            elif dialect in {"mysql", "mariadb"}:
                required.extend(("signal sqlstate", "45000", "immutable"))
            else:
                required.extend(("execute function", "rag4c_release_quality_immutable"))
            if any(fragment not in sql for fragment in required):
                issues.append(f"invalid Release quality immutable trigger {name}")
    return tuple(issues)


def _enterprise_release_quality_certification_capability_issues(
    connection: Any,
) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    missing = ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES - tables
    issues.extend(f"missing table {table}" for table in sorted(missing))
    if missing:
        return tuple(issues)
    for table in sorted(ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES):
        columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        required_columns = ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_COLUMNS[table]
        issues.extend(
            f"missing column {table}.{name}" for name in sorted(required_columns - set(columns))
        )
        for name in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_NOT_NULL[table]:
            if name in columns and columns[name].get("nullable") is not False:
                issues.append(f"nullable required column {table}.{name}")
        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_UNIQUES[
            table
        ].items():
            if uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_FOREIGN_KEYS[
            table
        ].items():
            if foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        for name, fragments in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            sql = checks.get(name)
            expected_sql = (
                _quality_policy_active_scope_key_check_sql(connection)
                if table == "tenant_release_quality_gate_policies"
                and name == _QUALITY_POLICY_ACTIVE_SCOPE_KEY_CHECK
                else None
            )
            if expected_sql is not None:
                if not sql or _parenless_sql(sql) != _parenless_sql(expected_sql):
                    issues.append(f"missing or invalid exact check {table}.{name}")
            elif not sql or any(
                _parenless_sql(fragment) not in _parenless_sql(sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")
        indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_INDEXES[
            table
        ].items():
            if indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")
    for table in ("tenant_approval_policies", "tenant_approval_requests"):
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
        }
        if "knowledge_base_release_quality_waiver" not in checks.get(f"ck_{table}_action_type", ""):
            issues.append(f"missing quality waiver approval action {table}")
    issues.extend(_release_quality_policy_data_issues(connection))
    issues.extend(_release_quality_guard_issues(connection))
    issues.extend(
        _parent_authority_issues(
            connection,
            label="Release authority",
            inspector=inspect_enterprise_knowledge_base_release_capability,
        )
    )
    return tuple(sorted(set(issues)))


def inspect_enterprise_release_quality_certification_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage 20 Release quality authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        quality_present = bool(ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
            ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not quality_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", (
                "catalog is not at a known pre-0030, 0030, 0031 or 0033 revision",
            )
        issues = _enterprise_release_quality_certification_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Release quality schema inspection failed: {exc.__class__.__name__}",
        )


_OPERATIONS_OBSERVATION_TRIGGER_NAMES = frozenset(
    {
        "trg_dataset_release_quality_observations_no_update",
        "trg_dataset_release_quality_observations_no_delete",
    }
)


def _release_quality_operations_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    elif dialect == "postgresql":
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        function_exists = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='rag4c_quality_observation_immutable')"
            )
        )
        if not function_exists:
            return ("missing immutable quality observation trigger function",)
    if dialect not in {"sqlite", "mysql", "mariadb", "postgresql"}:
        return ()
    issues: list[str] = []
    for name in sorted(_OPERATIONS_OBSERVATION_TRIGGER_NAMES):
        definition = definitions.get(name, "").casefold()
        if not definition:
            issues.append(f"missing immutable quality observation guard {name}")
        elif "update" not in definition and name.endswith("no_update"):
            issues.append(f"invalid immutable quality observation guard {name}")
        elif "delete" not in definition and name.endswith("no_delete"):
            issues.append(f"invalid immutable quality observation guard {name}")
    return tuple(issues)


def _release_quality_operations_data_issues(connection: Any) -> tuple[str, ...]:
    """Recompute Stage 21 active identities and lease/scope invariants read-only."""

    issues: list[str] = []
    digest_pattern = re.compile(r"^[0-9a-f]{64}$")
    sensitive_pattern = re.compile(
        r"(?i)(?:ticket|query|body|credential|secret|token|password|authorization)\s*[:=]"
    )

    def rows(table: str) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]

    try:
        policies = rows("tenant_release_quality_slo_policies")
        schedules = rows("tenant_release_quality_scan_schedules")
        runs = rows("tenant_release_quality_scan_runs")
        observations = rows("dataset_release_quality_observations")
        alerts = rows("dataset_release_quality_alerts")
        jobs = rows("dataset_release_recertification_jobs")
    except Exception as exc:
        return (f"Release quality operations data inspection failed: {exc.__class__.__name__}",)

    active_scopes: set[tuple[str, str]] = set()
    policy_ids = {(str(row["tenant_id"]), str(row["id"])) for row in policies}
    for row in policies:
        tenant = str(row.get("tenant_id") or "")
        identifier = str(row.get("id") or "")
        status = str(row.get("status") or "")
        scope_type = str(row.get("scope_type") or "")
        scope_value = str(row.get("scope_value") or "")
        channel_id = row.get("channel_id")
        expected = (
            "global:*"
            if scope_type == "global" and scope_value == "*" and channel_id is None
            else f"risk_tier:{scope_value}"
            if scope_type == "risk_tier"
            and scope_value in {"low", "medium", "high"}
            and channel_id is None
            else f"channel:{scope_value}"
            if scope_type == "channel" and channel_id is not None and scope_value == str(channel_id)
            else None
        )
        active_key = row.get("active_scope_key")
        if (status == "active" and (expected is None or active_key != expected)) or (
            status != "active" and active_key is not None
        ):
            issues.append(f"invalid active SLO policy identity {tenant}/{identifier}")
        if status == "active" and isinstance(active_key, str):
            key = (tenant, active_key)
            if key in active_scopes:
                issues.append(f"duplicate active SLO policy identity {tenant}/{active_key}")
            active_scopes.add(key)
        if not isinstance(row.get("policy_digest"), str) or not digest_pattern.fullmatch(
            str(row.get("policy_digest") or "")
        ):
            issues.append(f"invalid SLO policy digest {tenant}/{identifier}")

    schedule_ids: set[tuple[str, str, str, str]] = set()
    active_schedule_keys: set[tuple[str, str, str]] = set()
    for row in schedules:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        identifier = str(row.get("id") or "")
        policy_id = str(row.get("slo_policy_id") or "")
        status = str(row.get("status") or "")
        active_slot = row.get("active_policy_slot")
        schedule_ids.add((tenant, dataset, identifier, policy_id))
        if (tenant, policy_id) not in policy_ids:
            issues.append(f"orphan quality schedule policy {tenant}/{dataset}/{identifier}")
        if (status == "active" and active_slot != policy_id) or (
            status != "active" and active_slot is not None
        ):
            issues.append(
                f"invalid active quality schedule identity {tenant}/{dataset}/{identifier}"
            )
        if status == "active":
            key = (tenant, dataset, policy_id)
            if key in active_schedule_keys:
                issues.append(
                    f"duplicate active quality schedule identity {tenant}/{dataset}/{policy_id}"
                )
            active_schedule_keys.add(key)

    run_ids: set[tuple[str, str, str]] = set()
    for row in runs:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        identifier = str(row.get("id") or "")
        schedule_id = str(row.get("schedule_id") or "")
        policy_id = str(row.get("slo_policy_id") or "")
        status = str(row.get("status") or "")
        owner = row.get("claim_owner")
        lease = row.get("claim_lease_until")
        heartbeat = row.get("heartbeat_at")
        finished = row.get("finished_at")
        run_ids.add((tenant, dataset, identifier))
        if (tenant, dataset, schedule_id, policy_id) not in schedule_ids:
            issues.append(f"orphan quality scan run schedule {tenant}/{dataset}/{identifier}")
        if status in {"claimed", "running"} and (owner is None or lease is None):
            issues.append(f"invalid quality scan run lease {tenant}/{dataset}/{identifier}")
        if status == "running" and heartbeat is None:
            issues.append(f"invalid quality scan run heartbeat {tenant}/{dataset}/{identifier}")
        if status in {"completed", "failed", "cancelled"} and (
            owner is not None or lease is not None or finished is None
        ):
            issues.append(f"invalid terminal quality scan run {tenant}/{dataset}/{identifier}")
        for field in ("idempotency_key_digest", "request_hash"):
            if not isinstance(row.get(field), str) or not digest_pattern.fullmatch(
                str(row.get(field) or "")
            ):
                issues.append(f"invalid quality scan run {field} {tenant}/{dataset}/{identifier}")

    observation_ids: set[tuple[str, str, str]] = set()
    for row in observations:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        identifier = str(row.get("id") or "")
        observation_ids.add((tenant, dataset, identifier))
        if (tenant, dataset, str(row.get("scan_run_id") or "")) not in run_ids:
            issues.append(f"orphan quality observation run {tenant}/{dataset}/{identifier}")
        if not isinstance(row.get("observation_digest"), str) or not digest_pattern.fullmatch(
            str(row.get("observation_digest") or "")
        ):
            issues.append(f"invalid quality observation digest {tenant}/{dataset}/{identifier}")

    active_alert_keys: set[tuple[str, str]] = set()
    for row in alerts:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        identifier = str(row.get("id") or "")
        status = str(row.get("status") or "")
        expected = ":".join(
            str(row.get(name) or "")
            for name in ("dataset_id", "release_id", "channel_id", "release_role", "alert_type")
        )
        active_key = row.get("active_alert_key")
        if (status == "resolved" and active_key is not None) or (
            status != "resolved" and active_key != expected
        ):
            issues.append(f"invalid active quality alert identity {tenant}/{dataset}/{identifier}")
        if status != "resolved" and isinstance(active_key, str):
            key = (tenant, active_key)
            if key in active_alert_keys:
                issues.append(f"duplicate active quality alert identity {tenant}/{active_key}")
            active_alert_keys.add(key)
        if (tenant, dataset, str(row.get("source_observation_id") or "")) not in observation_ids:
            issues.append(f"orphan quality alert observation {tenant}/{dataset}/{identifier}")
        for field in ("acknowledged_comment", "resolved_comment", "suppressed_comment"):
            value = row.get(field)
            if isinstance(value, str) and sensitive_pattern.search(value):
                issues.append(
                    f"unsafe quality alert comment {tenant}/{dataset}/{identifier}/{field}"
                )

    active_job_keys: set[tuple[str, str]] = set()
    cycle_keys: set[tuple[str, str]] = set()
    for row in jobs:
        tenant = str(row.get("tenant_id") or "")
        dataset = str(row.get("dataset_id") or "")
        identifier = str(row.get("id") or "")
        status = str(row.get("status") or "")
        expected = ":".join(
            str(row.get(name) or "")
            for name in ("dataset_id", "release_id", "channel_id", "release_role", "policy_id")
        )
        active_key = row.get("active_job_key")
        if (status in {"completed", "failed", "cancelled"} and active_key is not None) or (
            status not in {"completed", "failed", "cancelled"} and active_key != expected
        ):
            issues.append(
                f"invalid active recertification job identity {tenant}/{dataset}/{identifier}"
            )
        if status not in {"completed", "failed", "cancelled"} and isinstance(active_key, str):
            key = (tenant, active_key)
            if key in active_job_keys:
                issues.append(
                    f"duplicate active recertification job identity {tenant}/{active_key}"
                )
            active_job_keys.add(key)
        cycle = str(row.get("cycle_key") or "")
        if not digest_pattern.fullmatch(cycle):
            issues.append(f"invalid recertification cycle_key {tenant}/{dataset}/{identifier}")
        else:
            cycle_key = (tenant, cycle)
            if cycle_key in cycle_keys:
                issues.append(f"duplicate recertification cycle_key {tenant}/{cycle}")
            cycle_keys.add(cycle_key)
        if status == "claimed" and (
            row.get("claim_owner") is None or row.get("claim_lease_until") is None
        ):
            issues.append(f"invalid recertification job lease {tenant}/{dataset}/{identifier}")
        if status != "claimed" and (
            row.get("claim_owner") is not None or row.get("claim_lease_until") is not None
        ):
            issues.append(
                f"invalid recertification job owner state {tenant}/{dataset}/{identifier}"
            )

    return tuple(sorted(set(issues)))


def _enterprise_release_quality_operations_capability_issues(
    connection: Any,
) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    missing = ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES - tables
    issues.extend(f"missing table {table}" for table in sorted(missing))
    if missing:
        return tuple(sorted(set(issues)))
    for table in sorted(ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_TABLES):
        columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        for name in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS[table]:
            if name not in columns:
                issues.append(f"missing column {table}.{name}")
        for name in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_NOT_NULL[table]:
            if name not in columns or bool(columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")
        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_UNIQUES[table].items():
            if uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_FOREIGN_KEYS[
            table
        ].items():
            if foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        for name, fragments in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            sql = checks.get(name, "")
            if not sql or any(
                _parenless_sql(fragment) not in _parenless_sql(sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")
        indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_INDEXES[table].items():
            if indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")
    issues.extend(_release_quality_operations_guard_issues(connection))
    issues.extend(_release_quality_operations_data_issues(connection))
    issues.extend(
        _parent_authority_issues(
            connection,
            label="Release quality authority",
            inspector=inspect_enterprise_release_quality_certification_capability,
        )
    )
    return tuple(sorted(set(issues)))


def inspect_enterprise_release_quality_operations_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage 21 Quality Operations authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        operations_present = bool(ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not operations_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", (
                "catalog is not at a known pre-0031, 0031, 0032 or 0033 revision",
            )
        issues = _enterprise_release_quality_operations_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Release quality operations schema inspection failed: {exc.__class__.__name__}",
        )


_NOTIFICATION_EVENT_INSERT_TRIGGER = "trg_tenant_notification_events_validate_insert"


_NOTIFICATION_IMMUTABLE_TRIGGER_NAMES = frozenset(
    f"trg_{table}_no_{operation}"
    for table in (
        "tenant_notifications",
        "tenant_notification_recipients",
        "tenant_notification_events",
    )
    for operation in ("update", "delete")
)


def _notification_center_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(connection.dialect.name).casefold()
    definitions: dict[str, str] = {}
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    elif dialect in {"mysql", "mariadb"}:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }
    elif dialect == "postgresql":
        definitions = {
            str(name): str(definition or "")
            for name, definition in connection.execute(
                text("SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal")
            ).all()
        }
        exists = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='rag4c_notification_immutable')"
            )
        )
        if not exists:
            return ("missing Notification Center immutable trigger function",)
        event_validator_exists = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc "
                "WHERE proname='rag4c_notification_event_validate')"
            )
        )
        if not event_validator_exists:
            return ("missing Notification Center event insert trigger function",)
    if dialect not in {"sqlite", "mysql", "mariadb", "postgresql"}:
        return ()
    issues: list[str] = []
    for name in sorted(_NOTIFICATION_IMMUTABLE_TRIGGER_NAMES):
        definition = definitions.get(name, "").casefold()
        if not definition:
            issues.append(f"missing Notification Center immutable guard {name}")
        elif name.endswith("no_update") and "update" not in definition:
            issues.append(f"invalid Notification Center immutable guard {name}")
        elif name.endswith("no_delete") and "delete" not in definition:
            issues.append(f"invalid Notification Center immutable guard {name}")
    insert_definition = definitions.get(_NOTIFICATION_EVENT_INSERT_TRIGGER, "").casefold()
    if not insert_definition:
        issues.append(
            f"missing Notification Center event insert guard {_NOTIFICATION_EVENT_INSERT_TRIGGER}"
        )
    elif "insert" not in insert_definition:
        issues.append(
            f"invalid Notification Center event insert guard {_NOTIFICATION_EVENT_INSERT_TRIGGER}"
        )
    return tuple(issues)


def _notification_center_data_issues(connection: Any) -> tuple[str, ...]:
    issues: list[str] = []
    digest = re.compile(r"^[0-9a-f]{64}$")
    unsafe = re.compile(
        r"(?i)(?:query|body|note|ticket|email|token|password|credential|authorization|webhook)"
    )
    try:
        subscriptions = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_notification_subscriptions")
            ).mappings()
        ]
        notifications = [
            dict(row)
            for row in connection.execute(text("SELECT * FROM tenant_notifications")).mappings()
        ]
        recipients = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_notification_recipients")
            ).mappings()
        ]
        receipts = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_notification_receipts")
            ).mappings()
        ]
        events = [
            dict(row)
            for row in connection.execute(
                text("SELECT * FROM tenant_notification_events")
            ).mappings()
        ]
        active_members = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                text("SELECT tenant_id, account_id FROM tenant_members WHERE status='active'")
            ).all()
        }
    except Exception as exc:
        return (f"Notification Center data inspection failed: {exc.__class__.__name__}",)

    active_subscriptions: set[tuple[str, str]] = set()
    for row in subscriptions:
        tenant = str(row.get("tenant_id") or "")
        identifier = str(row.get("id") or "")
        expected = f"{row.get('account_id')}:{row.get('category')}"
        key = row.get("active_subscription_key")
        if (row.get("status") == "active" and key != expected) or (
            row.get("status") == "archived" and key is not None
        ):
            issues.append(f"invalid notification subscription identity {tenant}/{identifier}")
        if row.get("status") == "active" and isinstance(key, str):
            scoped = (tenant, key)
            if scoped in active_subscriptions:
                issues.append(f"duplicate notification subscription identity {tenant}/{key}")
            active_subscriptions.add(scoped)
        if (tenant, str(row.get("account_id") or "")) not in active_members:
            issues.append(f"inactive notification subscription member {tenant}/{identifier}")

    notification_ids: set[tuple[str, str]] = set()
    notification_keys: set[tuple[str, str]] = set()
    for row in notifications:
        tenant = str(row.get("tenant_id") or "")
        identifier = str(row.get("id") or "")
        notification_ids.add((tenant, identifier))
        key = str(row.get("notification_key") or "")
        source_digest = str(row.get("source_digest") or "")
        if not digest.fullmatch(key) or not digest.fullmatch(source_digest):
            issues.append(f"invalid notification digest {tenant}/{identifier}")
        scoped = (tenant, key)
        if scoped in notification_keys:
            issues.append(f"duplicate notification key {tenant}/{key}")
        notification_keys.add(scoped)
        source_kind = str(row.get("source_kind") or "")
        route = str(row.get("target_route_code") or "")
        dataset_id = row.get("source_dataset_id")
        if source_kind == "quality_alert":
            if dataset_id is None or route != "knowledge_quality_operations":
                issues.append(f"invalid quality notification route {tenant}/{identifier}")
        elif source_kind == "approval_pending_for_me":
            if dataset_id is not None or route != "enterprise_approval":
                issues.append(f"invalid approval notification route {tenant}/{identifier}")
        else:
            issues.append(f"invalid notification source kind {tenant}/{identifier}")
        for field in ("safe_facts_json", "target_route_params_json"):
            rendered = str(row.get(field) or "")
            if unsafe.search(rendered):
                issues.append(f"unsafe notification facts {tenant}/{identifier}/{field}")

    recipient_ids: set[tuple[str, str, str]] = set()
    for row in recipients:
        tenant = str(row.get("tenant_id") or "")
        notification_id = str(row.get("notification_id") or "")
        account_id = str(row.get("account_id") or "")
        identifier = str(row.get("id") or "")
        scoped = (tenant, notification_id, account_id)
        if scoped in recipient_ids:
            issues.append(
                f"duplicate notification recipient {tenant}/{notification_id}/{account_id}"
            )
        recipient_ids.add(scoped)
        if (tenant, notification_id) not in notification_ids:
            issues.append(f"orphan notification recipient {tenant}/{identifier}")
        if (tenant, account_id) not in active_members:
            issues.append(f"inactive notification recipient member {tenant}/{identifier}")
        if not digest.fullmatch(str(row.get("assignment_digest") or "")):
            issues.append(f"invalid notification assignment digest {tenant}/{identifier}")

    receipt_ids: set[tuple[str, str, str]] = set()
    for row in receipts:
        tenant = str(row.get("tenant_id") or "")
        notification_id = str(row.get("notification_id") or "")
        account_id = str(row.get("account_id") or "")
        identifier = str(row.get("id") or "")
        scoped = (tenant, notification_id, account_id)
        if scoped in receipt_ids:
            issues.append(f"duplicate notification receipt {tenant}/{notification_id}/{account_id}")
        receipt_ids.add(scoped)
        if scoped not in recipient_ids:
            issues.append(f"orphan notification receipt {tenant}/{identifier}")
        status = str(row.get("status") or "")
        read_at = row.get("read_at")
        archived_at = row.get("archived_at")
        if (
            (status == "unread" and (read_at is not None or archived_at is not None))
            or (status == "read" and (read_at is None or archived_at is not None))
            or (status == "archived" and archived_at is None)
        ):
            issues.append(f"invalid notification receipt lifecycle {tenant}/{identifier}")

    from core.enterprise_notification_center import canonical_notification_event

    streams: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in events:
        tenant = str(row.get("tenant_id") or "")
        notification_id = str(row.get("notification_id") or "")
        account_id = str(row.get("account_id") or "")
        identifier = str(row.get("id") or "")
        scoped = (tenant, notification_id, account_id)
        if scoped not in recipient_ids:
            issues.append(f"orphan notification event {tenant}/{identifier}")
        if not digest.fullmatch(str(row.get("event_digest") or "")):
            issues.append(f"invalid notification event digest {tenant}/{identifier}")
        snapshot = row.get("safe_snapshot_json")
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except (TypeError, ValueError):
                snapshot = None
        try:
            canonical_notification_event(
                {
                    "tenant_id": tenant,
                    "notification_id": notification_id,
                    "account_id": account_id,
                    "sequence": row.get("sequence"),
                    "event_type": row.get("event_type"),
                    "previous_event_digest": row.get("previous_event_digest"),
                    "event_digest": row.get("event_digest"),
                    "actor_id": row.get("actor_id"),
                    "request_id": row.get("request_id"),
                    "safe_snapshot": snapshot,
                    "occurred_at": row.get("occurred_at"),
                }
            )
        except Exception:
            issues.append(f"invalid canonical notification event digest {tenant}/{identifier}")
        streams.setdefault(scoped, []).append(row)
    for scoped in receipt_ids:
        if scoped not in streams:
            issues.append(f"missing notification event stream {'/'.join(scoped)}")
    for scoped, rows in streams.items():
        rows.sort(key=lambda item: int(item.get("sequence") or 0))
        previous: str | None = None
        for index, row in enumerate(rows, start=1):
            if (
                int(row.get("sequence") or 0) != index
                or (index == 1 and str(row.get("event_type") or "") != "materialized")
                or row.get("previous_event_digest") != previous
            ):
                issues.append(f"invalid notification event hash chain {'/'.join(scoped)}")
                break
            previous = str(row.get("event_digest") or "")
    return tuple(sorted(set(issues)))


def _enterprise_notification_center_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    missing = ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES - tables
    issues.extend(f"missing table {table}" for table in sorted(missing))
    if missing:
        return tuple(sorted(set(issues)))
    member_uniques = {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("tenant_members")
        if item.get("name")
    }
    if member_uniques.get("uq_tenant_members_tenant_account") != ("tenant_id", "account_id"):
        issues.append("missing or invalid unique tenant_members.uq_tenant_members_tenant_account")
    for table in sorted(ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_TABLES):
        columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        for name in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_COLUMNS[table]:
            if name not in columns:
                issues.append(f"missing column {table}.{name}")
        for name in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_NOT_NULL[table]:
            if name not in columns or bool(columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")
        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_UNIQUES[table].items():
            if uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")
        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_FOREIGN_KEYS[table].items():
            if foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        for name, fragments in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            sql = checks.get(name, "")
            if not sql or any(
                _parenless_sql(fragment) not in _parenless_sql(sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")
        indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_INDEXES[table].items():
            if indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")
    issues.extend(_notification_center_guard_issues(connection))
    issues.extend(_notification_center_data_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_notification_center_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage 22 Notification Center authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        notification_present = bool(ENTERPRISE_NOTIFICATION_CENTER_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            if not notification_present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0032, 0032 or 0033 revision",)
        issues = _enterprise_notification_center_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Notification Center schema inspection failed: {exc.__class__.__name__}",
        )


class CatalogSchemaError(RuntimeError):
    """Raised when the catalog schema cannot be safely used."""


@dataclass(frozen=True)
class CatalogSchemaState:
    revision: str | None
    head_revision: str
    status: Literal["empty", "unstamped", "current", "behind", "future", "incomplete"]
    missing_tables: tuple[str, ...] = ()
    schema_issues: tuple[str, ...] = ()


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "catalog_migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _head_revision(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise CatalogSchemaError("catalog migration history has no head revision")
    return head


def _known_catalog_revisions() -> frozenset[str]:
    config = _alembic_config("sqlite://")
    return frozenset(
        str(item.revision) for item in ScriptDirectory.from_config(config).walk_revisions()
    )


def _normalized_sql(value: str | None) -> str:
    sql = str(value or "").strip().casefold().replace("`", "").replace('"', "")
    # MySQL reflection prefixes character literals with their connection charset.
    sql = re.sub(
        r"_(?:utf8mb4|utf8|binary|latin1|ascii|ucs2|utf16le|utf16|utf32)(?=')",
        "",
        sql,
    )
    # Boolean columns reflect as numeric predicates on MySQL; normalize them to
    # the same semantic form used by the cross-dialect manifest fragments.
    sql = re.sub(r"\b0\s*=\s*([a-z_][a-z0-9_]*)\b", r"not \1", sql)
    sql = re.sub(r"\b([a-z_][a-z0-9_]*)\s*=\s*0\b", r"not \1", sql)
    sql = re.sub(
        r"\(([a-z_][a-z0-9_]*\s*\+\s*[a-z_][a-z0-9_]*)\)",
        r"\1",
        sql,
    )
    return re.sub(r"\s+", " ", sql)


def _strip_redundant_outer_parentheses(sql: str) -> str:
    while sql.startswith("(") and sql.endswith(")"):
        depth = 0
        balanced_at_end = True
        for index, char in enumerate(sql):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(sql) - 1:
                    balanced_at_end = False
                    break
        if not balanced_at_end or depth != 0:
            break
        sql = sql[1:-1].strip()
    return sql


def _parenless_sql(value: str | None) -> str:
    """去括号 + 去全部空白的 SQL 比较形态。

    MySQL 8.4 反射 CHECK 约束时保留多层冗余括号（``(((a) and (b)) or ...)``），
    且运算符两侧空格与迁移里的 fragment 写法不一致（``= 64`` vs ``=64``）。
    去掉全部括号与全部空白后，`AND/OR/=/IN/IS NULL/length()/lower()` 的
    语义序列不变（两侧做相同变换），同义约束即匹配。
    仅用于**同批迁移生成的约束**的自检比对（格式一致，变换安全）。
    """
    sql = _normalized_sql(value)
    # MySQL 反射把 != 规范化成 <>（同义），统一为 != 便于与 fragment 比对。
    sql = sql.replace("<>", "!=")
    # 去全部空白（含 = 两侧、函数调用内），再去括号——两侧同变换，
    # 函数调用括号被去掉也不影响匹配。
    sql = re.sub(r"\s+", "", sql)
    return sql.replace("(", "").replace(")", "").strip()


def _canonical_check_sql(value: str | None) -> str:
    sql = _normalized_sql(value)
    sql = re.sub(
        r"::(?:character varying|text|boolean|integer|timestamp without time zone|timestamp with time zone)",
        "",
        sql,
    )
    sql = re.sub(r"\bis_default\s*=\s*false\b", "not is_default", sql)
    sql = re.sub(r"\bis_default\s*=\s*true\b", "is_default", sql)
    return _strip_redundant_outer_parentheses(re.sub(r"\s+", " ", sql).strip())


def _dataset_profile_column_issues(
    columns: list[dict[str, Any]],
    *,
    dialect_name: str,
) -> tuple[str, ...]:
    by_name = {item["name"]: item for item in columns}
    issues: list[str] = []
    for name in (
        "profile_json",
        "parser_policy",
        "chunk_policy",
        "retrieval_policy",
        "retention_policy",
        "metadata_policy",
    ):
        column = by_name.get(name)
        if column is None:
            continue
        if not isinstance(column.get("type"), JSON):
            issues.append(f"invalid type datasets.{name}: expected JSON")
        if column.get("default") is not None:
            issues.append(f"unexpected server default datasets.{name}")

    revision = by_name.get("profile_revision")
    if revision is not None:
        revision_type = revision.get("type")
        if not isinstance(revision_type, Integer) or isinstance(revision_type, Boolean):
            issues.append("invalid type datasets.profile_revision: expected Integer")

    acl_revision = by_name.get("acl_revision")
    if acl_revision is not None:
        acl_revision_type = acl_revision.get("type")
        if not isinstance(acl_revision_type, Integer) or isinstance(acl_revision_type, Boolean):
            issues.append("invalid type datasets.acl_revision: expected Integer")

    for name in ("graph_enabled", "qa_enabled"):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        mysql_boolean = (
            dialect_name in {"mysql", "mariadb"}
            and column_type is not None
            and column_type.__class__.__name__.upper() == "TINYINT"
            and getattr(column_type, "display_width", None) == 1
        )
        if not isinstance(column_type, Boolean) and not mysql_boolean:
            issues.append(f"invalid type datasets.{name}: expected Boolean")

    for name, expected_length in (
        ("owner_id", 64),
        ("visibility", 16),
        ("default_language", 32),
        ("archived_by", 64),
        ("acl_mode", 16),
        ("acl_enabled_by", 64),
    ):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, String):
            issues.append(f"invalid type datasets.{name}: expected String")
        elif column_type.length != expected_length:
            issues.append(f"invalid length datasets.{name}: expected {expected_length}")

    for name in ("archived_at", "updated_at", "acl_enabled_at"):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, DateTime):
            issues.append(f"invalid type datasets.{name}: expected DateTime")
        elif dialect_name in {"mysql", "mariadb"} and getattr(column_type, "fsp", None) != 6:
            issues.append(f"invalid type datasets.{name}: expected DATETIME(6)")
    return tuple(sorted(issues))


def _dataset_acl_mutation_request_column_issues(
    columns: list[dict[str, Any]],
    *,
    dialect_name: str,
) -> tuple[str, ...]:
    by_name = {item["name"]: item for item in columns}
    issues: list[str] = []
    for name, expected_length in (
        ("id", 64),
        ("tenant_id", 64),
        ("dataset_id", 64),
        ("actor_id", 64),
        ("idempotency_key", 128),
        ("request_hash", 64),
        ("operation", 32),
        ("status", 16),
        ("resource_id", 64),
    ):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, String):
            issues.append(f"invalid type dataset_acl_mutation_requests.{name}: expected String")
        elif column_type.length != expected_length:
            issues.append(
                f"invalid length dataset_acl_mutation_requests.{name}: expected {expected_length}"
            )

    response_json = by_name.get("response_json")
    if response_json is not None and not isinstance(response_json.get("type"), JSON):
        issues.append("invalid type dataset_acl_mutation_requests.response_json: expected JSON")

    http_status = by_name.get("http_status")
    if http_status is not None:
        http_status_type = http_status.get("type")
        if not isinstance(http_status_type, Integer) or isinstance(http_status_type, Boolean):
            issues.append(
                "invalid type dataset_acl_mutation_requests.http_status: expected Integer"
            )

    for name in ("created_at", "completed_at"):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, DateTime):
            issues.append(f"invalid type dataset_acl_mutation_requests.{name}: expected DateTime")
        elif dialect_name in {"mysql", "mariadb"} and getattr(column_type, "fsp", None) != 6:
            issues.append(
                f"invalid type dataset_acl_mutation_requests.{name}: expected DATETIME(6)"
            )
    return tuple(sorted(issues))


def _enterprise_workspace_column_issues(
    table_name: str,
    columns: list[dict[str, Any]],
    *,
    dialect_name: str,
) -> tuple[str, ...]:
    """Validate the 0026 Workspace column contract across supported dialects.

    The generic manifest checks presence and NOT NULL columns.  Workspace has
    several deliberately nullable slot/evidence columns and a Boolean that is
    reflected as MySQL TINYINT(1), so those semantics need an explicit validator
    rather than relying on column names alone.
    """

    string_lengths: dict[str, dict[str, int]] = {
        "tenant_workspaces": {
            "id": 128,
            "tenant_id": 64,
            "code": 64,
            "name": 128,
            "normalized_name": 256,
            "description": 512,
            "status": 16,
            "environment": 16,
            "active_default_slot": 16,
            "created_by": 64,
            "updated_by": 64,
            "archived_by": 64,
        },
        "tenant_workspace_members": {
            "tenant_id": 64,
            "workspace_id": 128,
            "account_id": 64,
            "role": 16,
            "status": 16,
            "created_by": 64,
            "updated_by": 64,
            "removed_by": 64,
        },
        "tenant_workspace_datasets": {
            "tenant_id": 64,
            "workspace_id": 128,
            "dataset_id": 64,
            "binding_kind": 16,
            "active_primary_slot": 16,
            "status": 16,
            "created_by": 64,
            "updated_by": 64,
            "removed_by": 64,
        },
    }
    integer_columns: dict[str, tuple[str, ...]] = {
        "tenant_workspaces": ("revision",),
        "tenant_workspace_members": ("id", "revision"),
        "tenant_workspace_datasets": ("id", "revision"),
    }
    datetime_columns: dict[str, tuple[str, ...]] = {
        "tenant_workspaces": ("created_at", "updated_at", "archived_at"),
        "tenant_workspace_members": ("created_at", "updated_at", "removed_at"),
        "tenant_workspace_datasets": ("created_at", "updated_at", "removed_at"),
    }
    nullable_columns: dict[str, tuple[str, ...]] = {
        "tenant_workspaces": ("active_default_slot", "archived_at", "archived_by"),
        "tenant_workspace_members": ("removed_at", "removed_by"),
        "tenant_workspace_datasets": ("active_primary_slot", "removed_at", "removed_by"),
    }

    by_name = {str(item.get("name")): item for item in columns}
    issues: list[str] = []
    for name, expected_length in string_lengths.get(table_name, {}).items():
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, String):
            issues.append(f"invalid type {table_name}.{name}: expected String")
        elif getattr(column_type, "length", None) != expected_length:
            issues.append(f"invalid length {table_name}.{name}: expected {expected_length}")

    for name in integer_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, Integer) or isinstance(column_type, Boolean):
            issues.append(f"invalid type {table_name}.{name}: expected Integer")

    if table_name == "tenant_workspaces":
        column = by_name.get("is_default")
        if column is not None:
            column_type = column.get("type")
            mysql_tinyint_boolean = (
                dialect_name.casefold() in {"mysql", "mariadb"}
                and column_type is not None
                and column_type.__class__.__name__.casefold() == "tinyint"
                and (
                    getattr(column_type, "display_width", None) == 1
                    or getattr(column_type, "length", None) == 1
                )
            )
            if not isinstance(column_type, Boolean) and not mysql_tinyint_boolean:
                issues.append("invalid type tenant_workspaces.is_default: expected Boolean")

    for name in datetime_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, DateTime):
            issues.append(f"invalid type {table_name}.{name}: expected DateTime")
        elif (
            dialect_name.casefold() in {"mysql", "mariadb"}
            and getattr(column_type, "fsp", None) != 6
        ):
            issues.append(f"invalid type {table_name}.{name}: expected DATETIME(6)")

    for name in nullable_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is None:
            continue
        if column.get("nullable") is not True:
            issues.append(f"non-nullable column {table_name}.{name}: expected nullable")
    return tuple(sorted(issues))


def _enterprise_workspace_authorization_column_issues(
    columns: list[dict[str, Any]], *, dialect_name: str
) -> tuple[str, ...]:
    expected_strings = {
        "id": 64,
        "tenant_id": 64,
        "workspace_id": 128,
        "mode": 16,
        "created_by": 64,
        "updated_by": 64,
        "enforced_by": 64,
        "disabled_by": 64,
    }
    by_name = {str(item.get("name")): item for item in columns}
    issues: list[str] = []
    for name, length in expected_strings.items():
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, String) or getattr(column_type, "length", None) != length:
            issues.append(
                f"invalid length tenant_workspace_authorization_policies.{name}: expected {length}"
            )
    for name in ("permission_model_version", "revision"):
        column_type = by_name.get(name, {}).get("type")
        if column_type is not None and (
            not isinstance(column_type, Integer) or isinstance(column_type, Boolean)
        ):
            issues.append(
                f"invalid type tenant_workspace_authorization_policies.{name}: expected Integer"
            )
    for name in ("created_at", "updated_at", "enforced_at", "disabled_at"):
        column_type = by_name.get(name, {}).get("type")
        if column_type is None:
            continue
        if not isinstance(column_type, DateTime):
            issues.append(
                f"invalid type tenant_workspace_authorization_policies.{name}: expected DateTime"
            )
        elif (
            dialect_name.casefold() in {"mysql", "mariadb"}
            and getattr(column_type, "fsp", None) != 6
        ):
            issues.append(
                f"invalid type tenant_workspace_authorization_policies.{name}: expected DATETIME(6)"
            )
    for name in ("enforced_at", "enforced_by", "disabled_at", "disabled_by"):
        if name in by_name and by_name[name].get("nullable") is not True:
            issues.append(
                f"non-nullable column tenant_workspace_authorization_policies.{name}: expected nullable"
            )
    return tuple(sorted(issues))


def _enterprise_knowledge_base_registry_column_issues(
    table_name: str,
    columns: list[dict[str, Any]],
    *,
    dialect_name: str,
) -> tuple[str, ...]:
    """Validate the 0028 registry column contract across supported dialects."""

    string_lengths: dict[str, dict[str, int]] = {
        "dataset_workspace_ownerships": {
            "id": 64,
            "tenant_id": 64,
            "dataset_id": 64,
            "workspace_id": 128,
            "created_by": 64,
            "updated_by": 64,
        },
        "app_dataset_references": {
            "id": 64,
            "tenant_id": 64,
            "app_id": 64,
            "dataset_id": 64,
            "reference_kind": 16,
            "status": 16,
            "active_slot": 16,
            "created_by": 64,
            "updated_by": 64,
            "removed_by": 64,
            "request_id": 128,
        },
    }
    integer_columns = {
        "dataset_workspace_ownerships": ("revision",),
        "app_dataset_references": ("revision",),
    }
    datetime_columns = {
        "dataset_workspace_ownerships": ("created_at", "updated_at", "last_transfer_at"),
        "app_dataset_references": ("created_at", "updated_at", "removed_at"),
    }
    nullable_columns = {
        "dataset_workspace_ownerships": ("last_transfer_at",),
        "app_dataset_references": ("active_slot", "removed_at", "removed_by"),
    }

    by_name = {str(item.get("name")): item for item in columns}
    issues: list[str] = []
    for name, expected_length in string_lengths.get(table_name, {}).items():
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, String):
            issues.append(f"invalid type {table_name}.{name}: expected String")
        elif getattr(column_type, "length", None) != expected_length:
            issues.append(f"invalid length {table_name}.{name}: expected {expected_length}")

    for name in integer_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, Integer) or isinstance(column_type, Boolean):
            issues.append(f"invalid type {table_name}.{name}: expected Integer")

    dialect = dialect_name.casefold()
    for name in datetime_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is None:
            continue
        column_type = column.get("type")
        if not isinstance(column_type, DateTime):
            issues.append(f"invalid type {table_name}.{name}: expected DateTime")
        elif dialect in {"mysql", "mariadb"} and getattr(column_type, "fsp", None) != 6:
            issues.append(f"invalid type {table_name}.{name}: expected DATETIME(6)")

    for name in nullable_columns.get(table_name, ()):
        column = by_name.get(name)
        if column is not None and column.get("nullable") is not True:
            issues.append(f"non-nullable column {table_name}.{name}: expected nullable")
    return tuple(sorted(issues))


def _schema_connection(bind: Any, callback):
    """Run a schema inspection callback on a SQLAlchemy 2 Connection."""

    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return callback(connection)
    return callback(bind)


def _catalog_revision(connection: Any) -> str | None:
    """Return the single stamped catalog revision, or None when unstamped/ambiguous.

    "无修订号"是一个**合法状态**（stamp_existing_catalog 的输入就是无章的 head 形状目录），
    所以这里返回 None 而不是抛错；需要区分的调用方自己判断。
    """

    if "alembic_version" not in set(inspect(connection).get_table_names()):
        return None
    revisions = tuple(
        str(value)
        for value in connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
    )
    return revisions[0] if len(revisions) == 1 else None


def _parent_authority_issues(
    connection: Any,
    *,
    label: str,
    inspector: Any,
) -> tuple[str, ...]:
    """交叉校验父权威能力——**仅在目录有唯一修订号时**。

    为什么需要这个守卫：父权威能力 inspector 是**带修订门禁**的（它要判断"当前修订下
    应具备什么结构"），而这里调用它的是**结构检查器**（`*_capability_issues`）。
    未盖章的目录会拿到 "catalog is not at a known ... revision"，若直接折叠进来，
    就变成"结构缺陷"，把 `stamp_existing_catalog` 这条**专门用来给无章 head 目录盖章**
    的修复路径自己挡住（tests/test_knowledge_governance_migration.py::
    test_stamp_existing_head_schema_uses_actual_governance_head 钉死该行为）。

    未盖章时跳过父权威的**修订门禁**；父权威的**结构**仍由各 *_capability_issues
    自身的逐表逐列检查覆盖，安全边界没有被放松。
    """

    if _catalog_revision(connection) is None:
        return ()
    state, issues = inspector(connection)
    if state == "ready":
        return ()
    return tuple(f"parent {label}: {issue}" for issue in issues or (state,))


def _knowledge_base_registry_data_issues(connection: Any) -> tuple[str, ...]:
    """Return data-level ownership blockers for the 0028 registry authority."""

    issues: list[str] = []
    queries = (
        (
            "dataset_workspace_ownerships.missing_for_dataset",
            """
            SELECT d.tenant_id, d.id
            FROM datasets AS d
            LEFT JOIN dataset_workspace_ownerships AS o
              ON o.tenant_id = d.tenant_id AND o.dataset_id = d.id
            WHERE o.id IS NULL
            ORDER BY d.tenant_id, d.id
            LIMIT 20
            """,
        ),
        (
            "dataset_workspace_ownerships.ownership_primary_mismatch",
            """
            SELECT o.tenant_id, o.dataset_id, o.workspace_id
            FROM dataset_workspace_ownerships AS o
            LEFT JOIN tenant_workspace_datasets AS b
              ON b.tenant_id = o.tenant_id
             AND b.dataset_id = o.dataset_id
             AND b.workspace_id = o.workspace_id
             AND b.status = 'active'
             AND b.binding_kind = 'primary'
             AND b.active_primary_slot = 'primary'
            WHERE b.id IS NULL
            ORDER BY o.tenant_id, o.dataset_id, o.workspace_id
            LIMIT 20
            """,
        ),
        (
            "dataset_workspace_ownerships.primary_binding_without_matching_ownership",
            """
            SELECT b.tenant_id, b.dataset_id, b.workspace_id
            FROM tenant_workspace_datasets AS b
            LEFT JOIN dataset_workspace_ownerships AS o
              ON o.tenant_id = b.tenant_id
             AND o.dataset_id = b.dataset_id
             AND o.workspace_id = b.workspace_id
            WHERE b.status = 'active'
              AND b.binding_kind = 'primary'
              AND b.active_primary_slot = 'primary'
              AND o.id IS NULL
            ORDER BY b.tenant_id, b.dataset_id, b.workspace_id
            LIMIT 20
            """,
        ),
        (
            "dataset_workspace_ownerships.owner_workspace_inactive",
            """
            SELECT o.tenant_id, o.dataset_id, o.workspace_id
            FROM dataset_workspace_ownerships AS o
            JOIN tenant_workspaces AS w
              ON w.tenant_id = o.tenant_id AND w.id = o.workspace_id
            WHERE w.status <> 'active'
            ORDER BY o.tenant_id, o.dataset_id, o.workspace_id
            LIMIT 20
            """,
        ),
    )
    try:
        for prefix, query in queries:
            rows = connection.execute(text(query)).fetchall()
            issues.extend(
                f"{prefix}:{'/'.join('<empty>' if value is None else str(value) for value in row)}"
                for row in rows
            )
    except Exception as exc:
        return (f"dataset_workspace_ownerships.data_inspection_failed:{exc.__class__.__name__}",)
    return tuple(sorted(set(issues)))


def _knowledge_base_registry_capability_issues(
    connection: Any,
    *,
    approval_action_revision: str = ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    missing_tables = ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_TABLES - tables
    issues.extend(f"missing table {name}" for name in sorted(missing_tables))
    if missing_tables:
        return tuple(sorted(issues))

    for table, required in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_COLUMNS.items():
        actual_columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        issues.extend(
            f"missing column {table}.{name}" for name in sorted(required - set(actual_columns))
        )
        nullable = ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_NOT_NULL.get(table, ())
        issues.extend(
            f"nullable column {table}.{name}"
            for name in sorted(nullable)
            if name in actual_columns and bool(actual_columns[name].get("nullable", True))
        )

    dialect_name = str(getattr(connection.dialect, "name", "")).casefold()
    for table in sorted(ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES):
        issues.extend(
            _enterprise_knowledge_base_registry_column_issues(
                table,
                inspector.get_columns(table),
                dialect_name=dialect_name,
            )
        )

    for table, required in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_UNIQUES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
        }
        for name, columns in required.items():
            if actual.get(name) != tuple(columns):
                issues.append(f"missing or invalid unique {table}.{name}")

    for (
        table,
        required,
    ) in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_FOREIGN_KEYS.items():
        actual = {
            item.get("name"): (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
        }
        for name, contract in required.items():
            if actual.get(name) != contract:
                issues.append(f"missing or invalid foreign key {table}.{name}")

    required_checks = {
        **ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_CHECK_FRAGMENTS,
        "tenant_approval_policies": {
            "ck_tenant_approval_policies_action_type": (
                ENTERPRISE_APPROVAL_ACTION_TYPES_0033
                if approval_action_revision
                in {
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                    ENTERPRISE_TASK_OPERATIONS_REVISION,
                    ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
                QA_FAQ_OPS_REVISION,
                STORAGE_BACKENDS_REVISION,
                ANSWER_EVIDENCE_FACTS_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0030
                if approval_action_revision
                in {
                    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                    ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0029
                if approval_action_revision == ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0028
            ),
        },
        "tenant_approval_requests": {
            "ck_tenant_approval_requests_action_type": (
                ENTERPRISE_APPROVAL_ACTION_TYPES_0033
                if approval_action_revision
                in {
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                    ENTERPRISE_TASK_OPERATIONS_REVISION,
                    ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
                QA_FAQ_OPS_REVISION,
                STORAGE_BACKENDS_REVISION,
                ANSWER_EVIDENCE_FACTS_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0030
                if approval_action_revision
                in {
                    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                    ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0029
                if approval_action_revision == ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0028
            ),
        },
    }
    for table, required in required_checks.items():
        actual = {
            item.get("name"): item.get("sqltext") for item in inspector.get_check_constraints(table)
        }
        exact_required = dict(
            ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_EXACT_CHECK_SQL.get(table, {})
        )
        if table in {"tenant_approval_policies", "tenant_approval_requests"}:
            approval_actions = (
                ENTERPRISE_APPROVAL_ACTION_TYPES_0033
                if approval_action_revision
                in {
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                    ENTERPRISE_TASK_OPERATIONS_REVISION,
                    ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
                QA_FAQ_OPS_REVISION,
                STORAGE_BACKENDS_REVISION,
                ANSWER_EVIDENCE_FACTS_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0030
                if approval_action_revision
                in {
                    ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                    ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                    ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                    ENTERPRISE_CONTENT_RECOVERY_REVISION,
                }
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0029
                if approval_action_revision == ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION
                else ENTERPRISE_APPROVAL_ACTION_TYPES_0028
            )
            exact_required[
                "ck_tenant_approval_policies_action_type"
                if table == "tenant_approval_policies"
                else "ck_tenant_approval_requests_action_type"
            ] = _approval_action_check_sql(approval_actions)
        for name, fragments in required.items():
            raw_sql = actual.get(name)
            expected_sql = exact_required.get(name)
            if expected_sql is not None:
                if not raw_sql or _parenless_sql(raw_sql) != _parenless_sql(
                    expected_sql
                ):
                    issues.append(f"missing or invalid exact check {table}.{name}")
            elif not raw_sql or any(
                _parenless_sql(fragment) not in _parenless_sql(raw_sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")

    for table, required in ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_CAPABILITY_REQUIRED_INDEXES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
        }
        for name, columns in required.items():
            if actual.get(name) != tuple(columns):
                issues.append(f"missing or invalid index {table}.{name}")
    issues.extend(_knowledge_base_registry_data_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_knowledge_base_registry_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return the revision-aware Stage 18 database capability state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        registry_present = bool(ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        if revision not in {
            ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
            ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
            ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
            ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }:
            known_pre_0028 = revision in _known_catalog_revisions()
            if not registry_present and known_pre_0028:
                return "not_available", ()
            return "unavailable", (
                "catalog is not at a known pre-0028, 0028, 0029, 0030 or 0031 revision",
            )
        if not registry_present:
            return "unavailable", ("missing Stage 18 registry tables",)
        issues = _knowledge_base_registry_capability_issues(
            connection, approval_action_revision=revision
        )
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"knowledge base registry schema inspection failed: {exc.__class__.__name__}",
        )


def _workspace_authorization_capability_issues(
    connection: Any,
    *,
    approval_action_revision: str = ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    missing_tables = ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_TABLES - tables
    issues.extend(f"missing table {name}" for name in sorted(missing_tables))
    if missing_tables:
        return tuple(sorted(issues))

    for table, required in ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_COLUMNS.items():
        actual_columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        issues.extend(
            f"missing column {table}.{name}" for name in sorted(required - set(actual_columns))
        )
        nullable = ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_NOT_NULL.get(table, ())
        issues.extend(
            f"nullable column {table}.{name}"
            for name in sorted(nullable)
            if name in actual_columns and bool(actual_columns[name].get("nullable", True))
        )

    dialect_name = str(getattr(connection.dialect, "name", "")).casefold()
    for table in sorted(ENTERPRISE_WORKSPACE_CONTROL_TABLES):
        issues.extend(
            _enterprise_workspace_column_issues(
                table,
                inspector.get_columns(table),
                dialect_name=dialect_name,
            )
        )
    issues.extend(
        _enterprise_workspace_authorization_column_issues(
            inspector.get_columns("tenant_workspace_authorization_policies"),
            dialect_name=dialect_name,
        )
    )

    for table, required in ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_UNIQUES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
        }
        for name, columns in required.items():
            if actual.get(name) != tuple(columns):
                issues.append(f"missing or invalid unique {table}.{name}")

    for (
        table,
        required,
    ) in ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_FOREIGN_KEYS.items():
        actual = {
            item.get("name"): (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
        }
        for name, contract in required.items():
            if actual.get(name) != contract:
                issues.append(f"missing or invalid foreign key {table}.{name}")

    required_checks = {
        table: dict(required)
        for table, required in ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_CHECK_FRAGMENTS.items()
    }
    if approval_action_revision in {
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
        ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
        ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
        ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
        ENTERPRISE_NOTIFICATION_CENTER_REVISION,
        ENTERPRISE_CONTENT_RECOVERY_REVISION,
    }:
        approval_actions = (
            ENTERPRISE_APPROVAL_ACTION_TYPES_0033
            if approval_action_revision
            in {
                ENTERPRISE_CONTENT_RECOVERY_REVISION,
                ENTERPRISE_TASK_OPERATIONS_REVISION,
                ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
            QA_FAQ_OPS_REVISION,
            STORAGE_BACKENDS_REVISION,
            ANSWER_EVIDENCE_FACTS_REVISION,
            }
            else ENTERPRISE_APPROVAL_ACTION_TYPES_0030
            if approval_action_revision
            in {
                ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                ENTERPRISE_CONTENT_RECOVERY_REVISION,
            }
            else ENTERPRISE_APPROVAL_ACTION_TYPES_0029
            if approval_action_revision == ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION
            else ENTERPRISE_APPROVAL_ACTION_TYPES_0028
        )
        required_checks["tenant_approval_policies"]["ck_tenant_approval_policies_action_type"] = (
            approval_actions
        )
        required_checks["tenant_approval_requests"]["ck_tenant_approval_requests_action_type"] = (
            approval_actions
        )

    for table, required in required_checks.items():
        actual = {
            item.get("name"): item.get("sqltext") for item in inspector.get_check_constraints(table)
        }
        exact_required = {
            **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[
                approval_action_revision
            ],
            **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_EXACT_CHECK_SQL,
            **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_EXACT_CHECK_SQL,
        }.get(table, {})
        for name, fragments in required.items():
            raw_sql = actual.get(name)
            expected_sql = exact_required.get(name)
            if expected_sql is not None:
                if not raw_sql or _parenless_sql(raw_sql) != _parenless_sql(
                    expected_sql
                ):
                    issues.append(f"missing or invalid exact check {table}.{name}")
            elif not raw_sql or any(
                _parenless_sql(fragment) not in _parenless_sql(raw_sql) for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")

    for table, required in ENTERPRISE_WORKSPACE_AUTHORIZATION_CAPABILITY_REQUIRED_INDEXES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
        }
        for name, columns in required.items():
            if actual.get(name) != tuple(columns):
                issues.append(f"missing or invalid index {table}.{name}")
    return tuple(sorted(set(issues)))


def inspect_workspace_authorization_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    """Return the revision-aware Stage 17 capability state.

    ``not_available`` is reserved for catalogs that predate 0027 and do not
    contain the policy table.  A 0028 catalog remains compatible with the
    Workspace authorization authority; its approval action CHECK also includes
    the Stage 18 transfer action.  Every required table and critical structural
    predicate must be provable; otherwise the caller receives ``unavailable``.
    """

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        policy_present = "tenant_workspace_authorization_policies" in tables
        revisions = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        revision = revisions[0] if len(revisions) == 1 else None
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        supported_revisions = {
            ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
            ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
            ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
            ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
            ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
            ENTERPRISE_NOTIFICATION_CENTER_REVISION,
            ENTERPRISE_CONTENT_RECOVERY_REVISION,
            ENTERPRISE_TASK_OPERATIONS_REVISION,
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
            ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
        QA_FAQ_OPS_REVISION,
        STORAGE_BACKENDS_REVISION,
        ANSWER_EVIDENCE_FACTS_REVISION,
        }
        if revision not in supported_revisions:
            known_pre_0027 = revision in _known_catalog_revisions()
            unstamped_empty_policy = bool(
                revision is None
                and policy_present
                and int(
                    connection.scalar(
                        text("SELECT COUNT(*) FROM tenant_workspace_authorization_policies")
                    )
                    or 0
                )
                == 0
            )
            if known_pre_0027 or (
                revision is None and (not policy_present or unstamped_empty_policy)
            ):
                return "not_available", ()
            return "unavailable", (
                "catalog is not at a known pre-0027, 0027, 0028, 0029, 0030 or 0031 revision",
            )
        if not policy_present:
            return "unavailable", ("missing table tenant_workspace_authorization_policies",)
        issues = _workspace_authorization_capability_issues(
            connection,
            approval_action_revision=revision,
        )
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"workspace authorization schema inspection failed: {exc.__class__.__name__}",
        )


def _head_schema_issues(inspector: Any) -> tuple[str, ...]:
    tables = set(inspector.get_table_names())
    issues: list[str] = []
    for table, required in _HEAD_REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        actual = {item["name"] for item in inspector.get_columns(table)}
        issues.extend(f"missing column {table}.{name}" for name in sorted(required - actual))
    workspace_dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
    for table in sorted(ENTERPRISE_WORKSPACE_CONTROL_TABLES & tables):
        issues.extend(
            _enterprise_workspace_column_issues(
                table,
                inspector.get_columns(table),
                dialect_name=workspace_dialect_name,
            )
        )
    if "tenant_workspace_authorization_policies" in tables:
        issues.extend(
            _enterprise_workspace_authorization_column_issues(
                inspector.get_columns("tenant_workspace_authorization_policies"),
                dialect_name=workspace_dialect_name,
            )
        )
    registry_dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
    for table in sorted(ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES & tables):
        issues.extend(
            _enterprise_knowledge_base_registry_column_issues(
                table,
                inspector.get_columns(table),
                dialect_name=registry_dialect_name,
            )
        )
    if "datasets" in tables:
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        issues.extend(
            _dataset_profile_column_issues(
                inspector.get_columns("datasets"),
                dialect_name=dialect_name,
            )
        )
    if "dataset_acl_mutation_requests" in tables:
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        issues.extend(
            _dataset_acl_mutation_request_column_issues(
                inspector.get_columns("dataset_acl_mutation_requests"),
                dialect_name=dialect_name,
            )
        )
    if "source_schedules" in tables:
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        if dialect_name in {"mysql", "mariadb"}:
            columns = {item["name"]: item for item in inspector.get_columns("source_schedules")}
            for name in ("next_run_at", "last_enqueued_at", "created_at", "updated_at"):
                column_type = columns.get(name, {}).get("type")
                if not isinstance(column_type, DateTime) or getattr(column_type, "fsp", None) != 6:
                    issues.append(f"invalid type source_schedules.{name}: expected DATETIME(6)")
    if "source_sync_runs" in tables:
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        if dialect_name in {"mysql", "mariadb"}:
            columns = {item["name"]: item for item in inspector.get_columns("source_sync_runs")}
            for name in (
                "reservation_lease_until",
                "execution_lease_until",
                "execution_heartbeat_at",
                "execution_next_attempt_at",
                "execution_started_at",
                "execution_finished_at",
                "planned_at",
            ):
                column_type = columns.get(name, {}).get("type")
                if not isinstance(column_type, DateTime) or getattr(column_type, "fsp", None) != 6:
                    issues.append(f"invalid type source_sync_runs.{name}: expected DATETIME(6)")
    if "tenant_members" in tables or "tenant_audit_events" in tables:
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        if dialect_name in {"mysql", "mariadb"}:
            for table, names in (
                ("tenant_members", ("updated_at",)),
                ("tenant_audit_events", ("occurred_at",)),
            ):
                if table not in tables:
                    continue
                columns = {item["name"]: item for item in inspector.get_columns(table)}
                for name in names:
                    column_type = columns.get(name, {}).get("type")
                    if (
                        not isinstance(column_type, DateTime)
                        or getattr(column_type, "fsp", None) != 6
                    ):
                        issues.append(f"invalid type {table}.{name}: expected DATETIME(6)")
    if access_graph_tables := (
        set(_HEAD_REQUIRED_COLUMNS)
        & {
            "tenant_organization_units",
            "tenant_groups",
            "tenant_group_members",
            "dataset_access_grants",
            "tenant_invitations",
        }
        & tables
    ):
        dialect_name = str(getattr(inspector.bind.dialect, "name", ""))
        if dialect_name in {"mysql", "mariadb"}:
            datetime_columns = {
                "tenant_organization_units": ("created_at", "updated_at"),
                "tenant_groups": ("created_at", "updated_at"),
                "tenant_group_members": ("created_at",),
                "dataset_access_grants": ("created_at", "updated_at"),
                "tenant_invitations": (
                    "expires_at",
                    "accepted_at",
                    "created_at",
                    "updated_at",
                ),
            }
            for table in sorted(access_graph_tables):
                columns = {item["name"]: item for item in inspector.get_columns(table)}
                for name in datetime_columns[table]:
                    column_type = columns.get(name, {}).get("type")
                    if (
                        not isinstance(column_type, DateTime)
                        or getattr(column_type, "fsp", None) != 6
                    ):
                        issues.append(f"invalid type {table}.{name}: expected DATETIME(6)")
    for table, required in _HEAD_REQUIRED_NOT_NULL.items():
        if table not in tables:
            continue
        actual = {item["name"]: bool(item.get("nullable")) for item in inspector.get_columns(table)}
        issues.extend(
            f"nullable column {table}.{name}" for name in sorted(required) if actual.get(name, True)
        )
    for table, required in _HEAD_REQUIRED_UNIQUES.items():
        if table not in tables:
            continue
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
        }
        for name, columns in required.items():
            if actual.get(name) != columns:
                issues.append(f"missing or invalid unique {table}.{name}")
    for table, required in _HEAD_REQUIRED_FOREIGN_KEYS.items():
        if table not in tables:
            continue
        actual = {
            item.get("name"): (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
        }
        for name, contract in required.items():
            if actual.get(name) != contract:
                issues.append(f"missing or invalid foreign key {table}.{name}")
    for table, required in _HEAD_REQUIRED_CHECK_FRAGMENTS.items():
        if table not in tables:
            continue
        actual = {
            item.get("name"): item.get("sqltext") for item in inspector.get_check_constraints(table)
        }
        exact_required = {
            **ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[HEAD_REVISION].get(
                table, {}
            ),
            **ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_EXACT_CHECK_SQL.get(table, {}),
            **ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_EXACT_CHECK_SQL.get(table, {}),
            **ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_EXACT_CHECK_SQL.get(table, {}),
            **ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_EXACT_CHECK_SQL.get(table, {}),
        }
        for name, fragments in required.items():
            raw_sql = actual.get(name)
            sql = _parenless_sql(raw_sql)
            expected_sql = exact_required.get(name)
            if expected_sql is not None:
                if not raw_sql or _parenless_sql(raw_sql) != _parenless_sql(expected_sql):
                    issues.append(f"missing or invalid exact check {table}.{name}")
            elif not sql or any(
                _parenless_sql(fragment) not in sql for fragment in fragments
            ):
                issues.append(f"missing or invalid check {table}.{name}")
    for table, required in _HEAD_REQUIRED_INDEXES.items():
        if table not in tables:
            continue
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
        }
        for name, columns in required.items():
            if actual.get(name) != columns:
                issues.append(f"missing or invalid index {table}.{name}")
    if "retrieval_experiments" in tables:
        issues.extend(_retrieval_experiment_trigger_issues(inspector))
    if ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES <= tables:
        issues.extend(_schema_connection(inspector.bind, _knowledge_base_registry_data_issues))
    if ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES <= tables:
        issues.extend(
            _schema_connection(inspector.bind, _enterprise_knowledge_base_release_capability_issues)
        )
    if ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_release_quality_certification_capability_issues,
            )
        )
    if ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_release_quality_operations_capability_issues,
            )
        )
    if ENTERPRISE_NOTIFICATION_CENTER_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_notification_center_capability_issues,
            )
        )
    if ENTERPRISE_TASK_OPERATIONS_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_task_operations_capability_issues,
            )
        )
    if ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_automation_workflows_capability_issues,
            )
        )
    return tuple(sorted(set(issues)))


def inspect_catalog_schema(engine: Engine) -> CatalogSchemaState:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    revision: str | None = None
    if "alembic_version" in tables:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            revision = None if row is None else str(row[0])
    config = _alembic_config(str(engine.url))
    head = _head_revision(config)
    expected_tables = HEAD_CATALOG_TABLES if revision == head else BASELINE_CATALOG_TABLES
    missing = tuple(sorted(expected_tables - tables))
    schema_issues = _head_schema_issues(inspector) if revision == head and not missing else ()
    if not tables:
        status: CatalogSchemaState.__annotations__["status"] = "empty"
    elif missing or schema_issues:
        status = "incomplete"
    elif revision is None:
        status = "unstamped"
    elif revision == head:
        status = "current"
    else:
        known = {item.revision for item in ScriptDirectory.from_config(config).walk_revisions()}
        status = "behind" if revision in known else "future"
    return CatalogSchemaState(revision, head, status, missing, schema_issues)


def verify_catalog_schema(engine: Engine) -> CatalogSchemaState:
    state = inspect_catalog_schema(engine)
    if state.status == "current":
        return state
    if state.status == "empty":
        raise CatalogSchemaError("catalog schema is empty; run the migration upgrade command")
    if state.status == "unstamped":
        raise CatalogSchemaError("catalog schema is not stamped; run stamp-existing after audit")
    if state.status == "incomplete":
        details = []
        if state.missing_tables:
            details.append("missing tables: " + ", ".join(state.missing_tables))
        if state.schema_issues:
            details.append("schema manifest: " + "; ".join(state.schema_issues))
        raise CatalogSchemaError("catalog schema is incomplete; " + "; ".join(details))
    if state.status == "behind":
        raise CatalogSchemaError(
            f"catalog schema {state.revision} is behind application head {state.head_revision}"
        )
    raise CatalogSchemaError(
        f"catalog schema {state.revision} is newer than this application ({state.head_revision})"
    )


def upgrade_catalog(database_url: str, revision: str = "head") -> None:
    engine = create_engine(database_url)
    try:
        state = inspect_catalog_schema(engine)
        reaches_constraints = revision in {"head", HEAD_REVISION}
        if state.revision == BASELINE_REVISION and reaches_constraints:
            from core.catalog_integrity import (
                CatalogIntegrityError,
                ensure_no_catalog_duplicates,
            )

            try:
                ensure_no_catalog_duplicates(engine)
            except CatalogIntegrityError as exc:
                raise CatalogSchemaError(str(exc)) from exc
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(database_url), revision)


def _datetime6():
    return (
        DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _legacy_document_repair_columns() -> dict[str, Column[Any]]:
    return {
        "content_revision": Column(
            "content_revision", Integer(), nullable=False, server_default="0"
        ),
        "desired_index_revision": Column(
            "desired_index_revision", Integer(), nullable=False, server_default="0"
        ),
        "indexed_revision": Column(
            "indexed_revision", Integer(), nullable=False, server_default="0"
        ),
        "graph_revision": Column("graph_revision", Integer(), nullable=False, server_default="0"),
        "current_attempt_id": Column("current_attempt_id", String(64), nullable=True),
        "source_uri": Column("source_uri", String(1024), nullable=True),
        "source_uri_hash": Column("source_uri_hash", String(64), nullable=True),
        "external_id": Column("external_id", String(512), nullable=True),
        "logical_folder_path": Column("logical_folder_path", String(1024), nullable=True),
        "folder_id": Column("folder_id", String(64), nullable=True),
        "source_type": Column("source_type", String(64), nullable=True),
        "source_id": Column("source_id", String(64), nullable=True),
        "current_version_id": Column("current_version_id", String(64), nullable=True),
        "lifecycle_state": Column(
            "lifecycle_state", String(24), nullable=False, server_default="active"
        ),
        "retrieval_enabled": Column(
            "retrieval_enabled", Boolean(), nullable=False, server_default="1"
        ),
        "effective_from": Column("effective_from", _datetime6(), nullable=True),
        "expires_at": Column("expires_at", _datetime6(), nullable=True),
        "purge_after": Column("purge_after", _datetime6(), nullable=True),
        "mutation_generation": Column(
            "mutation_generation", BigInteger(), nullable=False, server_default="0"
        ),
        "active_delete_operation_id": Column(
            "active_delete_operation_id", String(64), nullable=True
        ),
        "deletion_requested_at": Column("deletion_requested_at", _datetime6(), nullable=True),
        "deleted_at": Column("deleted_at", _datetime6(), nullable=True),
        "usage_released_at": Column("usage_released_at", _datetime6(), nullable=True),
    }


def _legacy_dataset_profile_repair_columns() -> dict[str, Column[Any]]:
    return {
        "profile_revision": Column(
            "profile_revision", Integer(), nullable=False, server_default="1"
        ),
        "owner_id": Column("owner_id", String(64), nullable=True),
        "visibility": Column("visibility", String(16), nullable=False, server_default="private"),
        "profile_json": Column("profile_json", JSON(), nullable=True),
        "parser_policy": Column("parser_policy", JSON(), nullable=True),
        "chunk_policy": Column("chunk_policy", JSON(), nullable=True),
        "retrieval_policy": Column("retrieval_policy", JSON(), nullable=True),
        "retention_policy": Column("retention_policy", JSON(), nullable=True),
        "metadata_policy": Column("metadata_policy", JSON(), nullable=True),
        "default_language": Column(
            "default_language", String(32), nullable=False, server_default="zh-CN"
        ),
        "graph_enabled": Column("graph_enabled", Boolean(), nullable=False, server_default="0"),
        "qa_enabled": Column("qa_enabled", Boolean(), nullable=False, server_default="1"),
        "archived_at": Column("archived_at", _datetime6(), nullable=True),
        "archived_by": Column("archived_by", String(64), nullable=True),
        "updated_at": Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        "mutation_generation": Column(
            "mutation_generation", BigInteger(), nullable=False, server_default="0"
        ),
        "serving_generation": Column(
            "serving_generation", BigInteger(), nullable=False, server_default="0"
        ),
        "acl_mode": Column(
            "acl_mode", String(16), nullable=False, server_default=text("'tenant_role'")
        ),
        "acl_revision": Column("acl_revision", Integer(), nullable=False, server_default="1"),
        "acl_enabled_at": Column("acl_enabled_at", _datetime6(), nullable=True),
        "acl_enabled_by": Column("acl_enabled_by", String(64), nullable=True),
        "serving_release_id": Column("serving_release_id", String(64), nullable=True),
        "release_revision": Column(
            "release_revision", Integer(), nullable=False, server_default="1"
        ),
    }


def _unique_column_sets(inspector: Any, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints(table)
    }


def _repair_legacy_create_all_schema(engine: Engine) -> None:
    """Repair the one known partial state produced by legacy create_all.

    Legacy create_all can create every new table while leaving pre-existing tables
    untouched. We only repair missing additive Document columns and named identity
    constraints; any missing head table or other column drift is rejected.
    """
    from models.orm import Base

    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    missing_tables = expected_tables - actual_tables
    if missing_tables:
        raise CatalogSchemaError(
            "cannot repair partial catalog; missing head tables: "
            + ", ".join(sorted(missing_tables))
        )
    allowed_dataset_columns = _legacy_dataset_profile_repair_columns()
    actual_dataset_columns = {item["name"] for item in inspector.get_columns("datasets")}
    expected_dataset_columns = set(Base.metadata.tables["datasets"].columns.keys())
    unexpected_dataset_missing = (
        expected_dataset_columns - actual_dataset_columns - set(allowed_dataset_columns)
    )
    if unexpected_dataset_missing:
        raise CatalogSchemaError(
            "cannot repair partial catalog; unexpected missing dataset columns: "
            + ", ".join(sorted(unexpected_dataset_missing))
        )

    allowed_document_columns = _legacy_document_repair_columns()
    actual_document_columns = {item["name"] for item in inspector.get_columns("documents")}
    expected_document_columns = set(Base.metadata.tables["documents"].columns.keys())
    unexpected_missing = (
        expected_document_columns - actual_document_columns - set(allowed_document_columns)
    )
    if unexpected_missing:
        raise CatalogSchemaError(
            "cannot repair partial catalog; unexpected missing document columns: "
            + ", ".join(sorted(unexpected_missing))
        )

    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        missing_dataset_additive = [
            column
            for name, column in allowed_dataset_columns.items()
            if name not in actual_dataset_columns
        ]
        if missing_dataset_additive:
            with operations.batch_alter_table("datasets") as batch:
                for column in missing_dataset_additive:
                    batch.add_column(column)
        missing_dataset_json = [
            name
            for name in (
                "profile_json",
                "parser_policy",
                "chunk_policy",
                "retrieval_policy",
                "retention_policy",
                "metadata_policy",
            )
            if name not in actual_dataset_columns
        ]
        for name in missing_dataset_json:
            connection.execute(text(f"UPDATE datasets SET {name}='{{}}' WHERE {name} IS NULL"))
        if missing_dataset_json:
            with operations.batch_alter_table("datasets") as batch:
                for name in missing_dataset_json:
                    batch.alter_column(name, existing_type=JSON(), nullable=False)

        missing_additive = [
            column
            for name, column in allowed_document_columns.items()
            if name not in actual_document_columns
        ]
        if missing_additive:
            with operations.batch_alter_table("documents") as batch:
                for column in missing_additive:
                    batch.add_column(column)

        inspector = inspect(connection)
        unique_specs = (
            (
                "tenant_members",
                ("account_id", "tenant_id"),
                "uq_tenant_members_account_tenant",
            ),
            (
                "document_segments",
                ("document_id", "seq"),
                "uq_document_segments_document_seq",
            ),
            (
                "metadata_fields",
                ("dataset_id", "key"),
                "uq_metadata_fields_dataset_key",
            ),
            (
                "documents",
                ("dataset_id", "source_uri_hash"),
                "uq_documents_dataset_source_uri_hash",
            ),
            (
                "documents",
                ("dataset_id", "source_id", "external_id"),
                "uq_documents_dataset_source_external",
            ),
            (
                "datasets",
                ("tenant_id", "id"),
                "uq_datasets_tenant_id",
            ),
            (
                "documents",
                ("tenant_id", "dataset_id", "id"),
                "uq_documents_scope_id",
            ),
        )
        for table, columns, name in unique_specs:
            if columns in _unique_column_sets(inspector, table):
                continue
            with operations.batch_alter_table(table) as batch:
                batch.create_unique_constraint(name, list(columns))
            inspector = inspect(connection)

        index_specs = (
            ("datasets", "ix_datasets_scope_status", ["tenant_id", "status", "id"]),
            (
                "datasets",
                "ix_datasets_scope_visibility",
                ["tenant_id", "visibility", "id"],
            ),
            ("datasets", "ix_datasets_scope_owner", ["tenant_id", "owner_id", "id"]),
            (
                "datasets",
                "ix_datasets_scope_updated",
                ["tenant_id", "updated_at", "id"],
            ),
            ("documents", "ix_documents_current_attempt_id", ["current_attempt_id"]),
            ("documents", "ix_documents_source_id", ["source_id"]),
            ("documents", "ix_documents_folder_id", ["folder_id"]),
            ("documents", "ix_documents_current_version_id", ["current_version_id"]),
            ("documents", "ix_documents_lifecycle_state", ["lifecycle_state"]),
            ("documents", "ix_documents_retrieval_enabled", ["retrieval_enabled"]),
            ("documents", "ix_documents_expires_at", ["expires_at"]),
            ("documents", "ix_documents_purge_after", ["purge_after"]),
            (
                "documents",
                "ix_documents_scope_effective",
                [
                    "tenant_id",
                    "dataset_id",
                    "lifecycle_state",
                    "retrieval_enabled",
                    "effective_from",
                    "expires_at",
                ],
            ),
            (
                "documents",
                "ix_documents_catalog_scope_updated",
                ["tenant_id", "dataset_id", "updated_at", "id"],
            ),
            (
                "documents",
                "ix_documents_catalog_scope_status_updated",
                ["tenant_id", "dataset_id", "status", "updated_at", "id"],
            ),
            (
                "documents",
                "ix_documents_catalog_scope_doc_type_updated",
                ["tenant_id", "dataset_id", "doc_type", "updated_at", "id"],
            ),
            (
                "documents",
                "ix_documents_catalog_scope_folder_updated",
                [
                    "tenant_id",
                    "dataset_id",
                    "logical_folder_path",
                    "updated_at",
                    "id",
                ],
            ),
        )
        index_kwargs = {
            "ix_documents_catalog_scope_folder_updated": {
                "mysql_length": {"logical_folder_path": 191}
            }
        }
        for table, name, columns in index_specs:
            names = {item.get("name") for item in inspector.get_indexes(table)}
            if name not in names:
                operations.create_index(
                    name,
                    table,
                    columns,
                    unique=False,
                    **index_kwargs.get(name, {}),
                )
                inspector = inspect(connection)

        dataset_foreign_keys = {
            tuple(item.get("constrained_columns") or ())
            for item in inspector.get_foreign_keys("datasets")
        }
        if ("owner_id", "tenant_id") not in dataset_foreign_keys:
            with operations.batch_alter_table("datasets") as batch:
                batch.create_foreign_key(
                    "fk_datasets_scope_owner_member",
                    "tenant_members",
                    ["owner_id", "tenant_id"],
                    ["account_id", "tenant_id"],
                )
            inspector = inspect(connection)

        document_foreign_keys = {
            tuple(item.get("constrained_columns") or ())
            for item in inspector.get_foreign_keys("documents")
        }
        if ("tenant_id", "dataset_id", "folder_id") not in document_foreign_keys:
            with operations.batch_alter_table("documents") as batch:
                batch.create_foreign_key(
                    "fk_documents_scope_folder",
                    "knowledge_folders",
                    ["tenant_id", "dataset_id", "folder_id"],
                    ["tenant_id", "dataset_id", "id"],
                )
            inspector = inspect(connection)
            document_foreign_keys = {
                tuple(item.get("constrained_columns") or ())
                for item in inspector.get_foreign_keys("documents")
            }
        if (
            "tenant_id",
            "dataset_id",
            "id",
            "current_version_id",
        ) not in document_foreign_keys:
            with operations.batch_alter_table("documents") as batch:
                batch.create_foreign_key(
                    "fk_documents_scope_current_version",
                    "document_versions",
                    ["tenant_id", "dataset_id", "id", "current_version_id"],
                    ["tenant_id", "dataset_id", "document_id", "id"],
                )

        inspector = inspect(connection)
        dataset_check_names = {
            item.get("name") for item in inspector.get_check_constraints("datasets")
        }
        missing_dataset_checks = []
        if "ck_datasets_profile_revision_positive" not in dataset_check_names:
            missing_dataset_checks.append(
                ("ck_datasets_profile_revision_positive", "profile_revision > 0")
            )
        if "ck_datasets_visibility" not in dataset_check_names:
            missing_dataset_checks.append(
                (
                    "ck_datasets_visibility",
                    "visibility IN ('private', 'tenant', 'public')",
                )
            )
        if "ck_datasets_status" not in dataset_check_names:
            missing_dataset_checks.append(
                (
                    "ck_datasets_status",
                    "status IN ('active', 'archived', 'disabled')",
                )
            )
        if "ck_datasets_generations_nonnegative" not in dataset_check_names:
            missing_dataset_checks.append(
                (
                    "ck_datasets_generations_nonnegative",
                    "mutation_generation >= 0 AND serving_generation >= 0",
                )
            )
        if "ck_datasets_acl_mode" not in dataset_check_names:
            missing_dataset_checks.append(
                (
                    "ck_datasets_acl_mode",
                    "acl_mode IN ('tenant_role', 'dataset_acl')",
                )
            )
        if "ck_datasets_acl_revision_positive" not in dataset_check_names:
            missing_dataset_checks.append(
                (
                    "ck_datasets_acl_revision_positive",
                    "acl_revision > 0",
                )
            )
        if missing_dataset_checks:
            with operations.batch_alter_table("datasets") as batch:
                for name, condition in missing_dataset_checks:
                    batch.create_check_constraint(name, condition)

        inspector = inspect(connection)
        check_names = {item.get("name") for item in inspector.get_check_constraints("documents")}
        missing_checks = []
        if "ck_documents_lifecycle_state" not in check_names:
            missing_checks.append(("ck_documents_lifecycle_state", _DOCUMENT_LIFECYCLE_CHECK))
        if "ck_documents_retrieval_lifecycle" not in check_names:
            missing_checks.append(
                (
                    "ck_documents_retrieval_lifecycle",
                    "lifecycle_state = 'active' OR NOT retrieval_enabled",
                )
            )
        if "ck_documents_mutation_generation_nonnegative" not in check_names:
            missing_checks.append(
                (
                    "ck_documents_mutation_generation_nonnegative",
                    "mutation_generation >= 0",
                )
            )
        if missing_checks:
            with operations.batch_alter_table("documents") as batch:
                for name, condition in missing_checks:
                    batch.create_check_constraint(name, condition)

        inspector = inspect(connection)
        document_fk_names = {item.get("name") for item in inspector.get_foreign_keys("documents")}
        if "fk_documents_scope_active_delete_operation" not in document_fk_names:
            with operations.batch_alter_table("documents") as batch:
                batch.create_foreign_key(
                    "fk_documents_scope_active_delete_operation",
                    "document_delete_operations",
                    ["tenant_id", "dataset_id", "active_delete_operation_id"],
                    ["tenant_id", "dataset_id", "id"],
                )

        for table_name, constraint_name, target_table, local_columns, remote_columns in (
            (
                "index_operations",
                "fk_index_operations_scope_delete_operation",
                "document_delete_operations",
                ["tenant_id", "dataset_id", "delete_operation_id"],
                ["tenant_id", "dataset_id", "id"],
            ),
            (
                "source_document_states",
                "fk_source_document_states_scope_delete_operation",
                "document_delete_operations",
                ["doc_id", "delete_operation_id"],
                ["document_id", "id"],
            ),
        ):
            inspector = inspect(connection)
            fk_names = {item.get("name") for item in inspector.get_foreign_keys(table_name)}
            if constraint_name not in fk_names:
                with operations.batch_alter_table(table_name) as batch:
                    batch.create_foreign_key(
                        constraint_name, target_table, local_columns, remote_columns
                    )

        inspector = inspect(connection)
        document_indexes = {item.get("name") for item in inspector.get_indexes("documents")}
        for name, columns in (
            ("ix_documents_scope_lifecycle", ["tenant_id", "dataset_id", "lifecycle_state", "id"]),
            (
                "ix_documents_scope_retrieval",
                ["tenant_id", "dataset_id", "retrieval_enabled", "id"],
            ),
            ("ix_documents_active_delete_operation", ["active_delete_operation_id"]),
        ):
            if name not in document_indexes:
                operations.create_index(name, "documents", columns, unique=False)

    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        actual_columns = {item["name"] for item in inspector.get_columns(table_name)}
        missing = set(table.columns.keys()) - actual_columns
        if missing:
            raise CatalogSchemaError(
                f"partial catalog repair incomplete for {table_name}: " + ", ".join(sorted(missing))
            )
    manifest_issues = _head_schema_issues(inspector)
    if manifest_issues:
        raise CatalogSchemaError(
            "cannot repair partial catalog; head schema manifest: " + "; ".join(manifest_issues)
        )


def stamp_existing_catalog(database_url: str) -> None:
    engine = create_engine(database_url)
    target_revision = BASELINE_REVISION
    try:
        state = inspect_catalog_schema(engine)
        if state.status == "current":
            return
        if state.missing_tables:
            raise CatalogSchemaError(
                "cannot stamp incomplete catalog; missing tables: "
                + ", ".join(state.missing_tables)
            )
        if state.revision is not None:
            raise CatalogSchemaError(
                f"catalog is already stamped at unexpected revision {state.revision}"
            )
        tables = set(inspect(engine).get_table_names())
        head_only_tables = set(HEAD_CATALOG_TABLES) - set(BASELINE_CATALOG_TABLES)
        present_head_only = head_only_tables & tables
        if present_head_only and present_head_only != head_only_tables:
            raise CatalogSchemaError(
                "cannot stamp partial head-only catalog; missing tables: "
                + ", ".join(sorted(head_only_tables - present_head_only))
            )
        if present_head_only == head_only_tables:
            _repair_legacy_create_all_schema(engine)
            manifest_issues = _head_schema_issues(inspect(engine))
            if manifest_issues:
                raise CatalogSchemaError(
                    "cannot stamp incomplete head schema manifest: " + "; ".join(manifest_issues)
                )
            target_revision = HEAD_REVISION
    finally:
        engine.dispose()
    command.stamp(_alembic_config(database_url), target_revision)


def safe_database_label(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        return f"sqlite:///{url.database or ''}"
    host = url.host or ""
    port = f":{url.port}" if url.port else ""
    database = f"/{url.database}" if url.database else ""
    return f"{url.drivername}://{host}{port}{database}"


# ---------------------------------------------------------------------------
# Enterprise Knowledge Serving & Reliability (Stage 26)
# ---------------------------------------------------------------------------

ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION = "0036_enterprise_knowledge_serving_reliability"
ENTERPRISE_KNOWLEDGE_SERVING_REVISION = ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES = frozenset(
    {
        "tenant_knowledge_serving_profiles",
        "tenant_knowledge_serving_policy_revisions",
        "tenant_knowledge_serving_snapshots",
        "tenant_knowledge_serving_stage_facts",
        "tenant_knowledge_serving_evidence_links",
        "tenant_knowledge_serving_events",
    }
)
ENTERPRISE_KNOWLEDGE_SERVING_REQUIRED_TABLES = ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
_KNOWLEDGE_SERVING_METADATA = __import__("models.orm", fromlist=["Base"]).Base.metadata.tables


def _knowledge_serving_contract(table_name: str):
    return _orm_table_contract(table_name)


for _knowledge_serving_table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES:
    (
        _serving_columns,
        _serving_not_null,
        _serving_uniques,
        _serving_foreign_keys,
        _serving_checks,
        _serving_indexes,
    ) = _knowledge_serving_contract(_knowledge_serving_table)
    _HEAD_REQUIRED_COLUMNS[_knowledge_serving_table] = _serving_columns
    _HEAD_REQUIRED_NOT_NULL[_knowledge_serving_table] = _serving_not_null
    _HEAD_REQUIRED_UNIQUES[_knowledge_serving_table] = _serving_uniques
    _HEAD_REQUIRED_FOREIGN_KEYS[_knowledge_serving_table] = _serving_foreign_keys
    _HEAD_REQUIRED_CHECK_FRAGMENTS[_knowledge_serving_table] = _serving_checks
    _HEAD_REQUIRED_INDEXES[_knowledge_serving_table] = _serving_indexes

ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_COLUMNS = {
    table: _HEAD_REQUIRED_COLUMNS[table]
    for table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
}
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_NOT_NULL = {
    table: _HEAD_REQUIRED_NOT_NULL[table]
    for table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
}
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_UNIQUES = {
    table: _HEAD_REQUIRED_UNIQUES[table]
    for table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
}
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_FOREIGN_KEYS = {
    table: _HEAD_REQUIRED_FOREIGN_KEYS[table]
    for table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
}
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_INDEXES = {
    table: _HEAD_REQUIRED_INDEXES[table]
    for table in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
}

_KNOWLEDGE_SERVING_EVIDENCE_KIND_ROUTE_PAIRS = (
    ("source", "knowledge_sources"),
    ("source_sync_run", "knowledge_sources"),
    ("document", "knowledge_documents"),
    ("ingest_attempt", "knowledge_documents"),
    ("chunk_head", "knowledge_documents"),
    ("index_operation", "knowledge_indexing"),
    ("release", "knowledge_base_releases"),
    ("certification", "release_quality"),
    ("task", "enterprise_tasks"),
)
_KNOWLEDGE_SERVING_EVIDENCE_KIND_ROUTE_CHECK_SQL = " OR ".join(
    f"(evidence_kind='{kind}' AND route_code='{route}')"
    for kind, route in _KNOWLEDGE_SERVING_EVIDENCE_KIND_ROUTE_PAIRS
)

# The fragments are intentionally explicit: they are part of the capability
# contract rather than an incidental serialization of ORM expression objects.
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS = {
    "tenant_knowledge_serving_profiles": {
        "ck_tenant_knowledge_serving_profiles_status": ("draft", "active", "paused", "archived"),
        "ck_tenant_knowledge_serving_profiles_revision": ("revision > 0",),
        "ck_tenant_knowledge_serving_profiles_active_identity": (
            "active_profile_key",
            "dataset_id",
        ),
        "ck_tenant_knowledge_serving_profiles_active_policy": (
            "current_policy_revision_id",
            "active_profile_key",
        ),
        "ck_tenant_knowledge_serving_profiles_lifecycle": ("archived_at", "archived_by"),
    },
    "tenant_knowledge_serving_policy_revisions": {
        "ck_tenant_knowledge_serving_policy_revisions_thresholds": (
            "revision > 0",
            "max_source_staleness_seconds",
            "max_parse_lag_seconds",
            "max_index_lag_seconds",
            "max_failed_document_count",
            "max_pending_index_count",
        ),
        "ck_tenant_knowledge_serving_policy_revisions_digest": (
            "policy_digest",
            "lower(policy_digest)",
        ),
    },
    "tenant_knowledge_serving_snapshots": {
        "ck_tenant_knowledge_serving_snapshots_state": (
            "ready",
            "degraded",
            "blocked",
            "unavailable",
        ),
        "ck_tenant_knowledge_serving_snapshots_counts": (
            "stage_count=5",
            "source_count",
            "ready_stage_count",
            "blocked_stage_count",
        ),
        "ck_tenant_knowledge_serving_snapshots_digest": (
            "snapshot_digest",
            "lower(snapshot_digest)",
        ),
    },
    "tenant_knowledge_serving_stage_facts": {
        "ck_tenant_knowledge_serving_stage_facts_stage": ("stage_code", "sequence"),
        "ck_tenant_knowledge_serving_stage_facts_sequence": ("sequence between 1 and 5",),
        "ck_tenant_knowledge_serving_stage_facts_state": (
            "ready",
            "lagging",
            "blocked",
            "missing",
            "unavailable",
        ),
        "ck_tenant_knowledge_serving_stage_facts_counts": (
            "item_count",
            "ready_count",
            "warning_count",
            "pending_count",
            "error_count",
            "lag_seconds",
        ),
        "ck_tenant_knowledge_serving_stage_facts_error": ("safe_error_code", "safe_error"),
        "ck_tenant_knowledge_serving_stage_facts_digest": (
            "stage_digest",
            "expected_digest",
            "observed_digest",
        ),
    },
    "tenant_knowledge_serving_evidence_links": {
        "ck_tenant_knowledge_serving_evidence_links_kind": (
            "source",
            "source_sync_run",
            "document",
            "ingest_attempt",
            "chunk_head",
            "index_operation",
            "release",
            "certification",
            "task",
        ),
        "ck_tenant_knowledge_serving_evidence_links_route": (
            "knowledge_sources",
            "knowledge_documents",
            "knowledge_indexing",
            "knowledge_base_releases",
            "release_quality",
            "enterprise_tasks",
        ),
        "ck_tenant_knowledge_serving_evidence_links_kind_route": tuple(
            f"(evidence_kind='{kind}' and route_code='{route}')"
            for kind, route in _KNOWLEDGE_SERVING_EVIDENCE_KIND_ROUTE_PAIRS
        ),
        "ck_tenant_knowledge_serving_evidence_links_digest": (
            "resource_id",
            "resource_revision >= 1",
            "evidence_digest",
            "resource_digest",
        ),
    },
    "tenant_knowledge_serving_events": {
        "ck_tenant_knowledge_serving_events_type": (
            "profile_created",
            "policy_revision_created",
            "policy_activated",
            "snapshot_recorded",
            "stage_degraded",
            "stage_blocked",
            "service_recovered",
        ),
        "ck_tenant_knowledge_serving_events_sequence": ("sequence > 0",),
        "ck_tenant_knowledge_serving_events_digest": ("event_digest", "previous_event_digest"),
        "ck_tenant_knowledge_serving_events_hash_chain": ("sequence", "previous_event_digest"),
        "ck_tenant_knowledge_serving_events_snapshot": ("safe_snapshot_json",),
    },
}
_HEAD_REQUIRED_CHECK_FRAGMENTS.update(
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS
)
ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_ISSUE_FRAGMENTS = tuple(
    f"{table}." for table in sorted(ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES)
) + (
    "data_sources.",
    "source_sync_runs.",
    "document_ingest_attempts.",
    "chunk_heads.",
    "index_operations.",
    "tenant_release_channels.",
    "dataset_release_manifests.",
    "dataset_release_quality_certifications.",
    "tenant_task_projections.",
)
ENTERPRISE_KNOWLEDGE_SERVING_ISSUE_FRAGMENTS = (
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_ISSUE_FRAGMENTS
)
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | {"qa_negative_questions", "storage_backends"}
HEAD_CATALOG_TABLES = HEAD_CATALOG_TABLES | {
    "tenant_knowledge_answer_facts",
    "tenant_knowledge_answer_evidence_refs",
}
HEAD_REVISION = ANSWER_EVIDENCE_FACTS_REVISION

_KNOWLEDGE_SERVING_SUPPORTED_DIALECTS = frozenset({"sqlite", "mysql", "mariadb", "postgresql"})
_KNOWLEDGE_SERVING_STAGE_CODES = ("source", "parse", "chunk", "index", "serve")
_KNOWLEDGE_SERVING_STAGE_STATES = frozenset(
    {"ready", "lagging", "blocked", "missing", "unavailable"}
)
_KNOWLEDGE_SERVING_OVERALL_STATES = frozenset({"ready", "degraded", "blocked", "unavailable"})
_KNOWLEDGE_SERVING_EVIDENCE_KINDS = frozenset(
    {
        "source",
        "source_sync_run",
        "document",
        "ingest_attempt",
        "chunk_head",
        "index_operation",
        "release",
        "certification",
        "task",
    }
)
_KNOWLEDGE_SERVING_ROUTE_CODES = frozenset(
    {
        "knowledge_sources",
        "knowledge_documents",
        "knowledge_indexing",
        "knowledge_base_releases",
        "release_quality",
        "enterprise_tasks",
    }
)
_KNOWLEDGE_SERVING_EVENT_TYPES = frozenset(
    {
        "profile_created",
        "policy_revision_created",
        "policy_activated",
        "snapshot_recorded",
        "stage_degraded",
        "stage_blocked",
        "service_recovered",
    }
)
_KNOWLEDGE_SERVING_PROFILE_STATUSES = frozenset({"draft", "active", "paused", "archived"})
_KNOWLEDGE_SERVING_IMMUTABLE_TABLES = (
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
)


def _knowledge_serving_check_values(sql: str | None, column_name: str) -> tuple[str, ...] | None:
    return _automation_check_allowlist_values(sql, column_name)


def _knowledge_serving_exact_allowlist_issues(
    checks_by_table: Mapping[str, Mapping[str, str]],
) -> tuple[str, ...]:
    specifications = (
        (
            "tenant_knowledge_serving_profiles",
            "ck_tenant_knowledge_serving_profiles_status",
            "status",
            _KNOWLEDGE_SERVING_PROFILE_STATUSES,
            "profile status",
        ),
        (
            "tenant_knowledge_serving_snapshots",
            "ck_tenant_knowledge_serving_snapshots_state",
            "state",
            _KNOWLEDGE_SERVING_OVERALL_STATES,
            "snapshot state",
        ),
        (
            "tenant_knowledge_serving_stage_facts",
            "ck_tenant_knowledge_serving_stage_facts_state",
            "state",
            _KNOWLEDGE_SERVING_STAGE_STATES,
            "stage state",
        ),
        (
            "tenant_knowledge_serving_evidence_links",
            "ck_tenant_knowledge_serving_evidence_links_kind",
            "evidence_kind",
            _KNOWLEDGE_SERVING_EVIDENCE_KINDS,
            "evidence kind",
        ),
        (
            "tenant_knowledge_serving_evidence_links",
            "ck_tenant_knowledge_serving_evidence_links_route",
            "route_code",
            _KNOWLEDGE_SERVING_ROUTE_CODES,
            "route code",
        ),
        (
            "tenant_knowledge_serving_events",
            "ck_tenant_knowledge_serving_events_type",
            "event_type",
            _KNOWLEDGE_SERVING_EVENT_TYPES,
            "event type",
        ),
    )
    issues: list[str] = []
    for table, check_name, column, expected, label in specifications:
        actual = _knowledge_serving_check_values(
            checks_by_table.get(table, {}).get(check_name), column
        )
        if actual is None or len(actual) != len(expected) or set(actual) != set(expected):
            rendered = ",".join(actual) if actual is not None else "unparseable"
            issues.append(
                f"invalid exact Knowledge Serving {label} allow-list {table}.{check_name}: {rendered}"
            )

    actual_kind_route_sql = checks_by_table.get(
        "tenant_knowledge_serving_evidence_links", {}
    ).get("ck_tenant_knowledge_serving_evidence_links_kind_route")
    if actual_kind_route_sql is None or _parenless_sql(
        actual_kind_route_sql
    ) != _parenless_sql(_KNOWLEDGE_SERVING_EVIDENCE_KIND_ROUTE_CHECK_SQL):
        issues.append("invalid exact Knowledge Serving evidence kind-route allow-list")

    expected_stage_sql = (
        "(stage_code='source' AND sequence=1) OR (stage_code='parse' AND sequence=2) OR "
        "(stage_code='chunk' AND sequence=3) OR (stage_code='index' AND sequence=4) OR "
        "(stage_code='serve' AND sequence=5)"
    )
    actual_stage_sql = checks_by_table.get("tenant_knowledge_serving_stage_facts", {}).get(
        "ck_tenant_knowledge_serving_stage_facts_stage"
    )
    if actual_stage_sql is None or _parenless_sql(actual_stage_sql) != _parenless_sql(
        expected_stage_sql
    ):
        issues.append("invalid exact Knowledge Serving stage ordering allow-list")
    return tuple(issues)


def _knowledge_serving_postgresql_guard_issues(connection: Any) -> tuple[str, ...]:
    trusted_schema = str(
        connection.scalar(text("SELECT current_schema()")) or ""
    ).casefold()
    if not trusted_schema:
        return ("cannot prove Knowledge Serving trusted PostgreSQL schema",)

    rows = connection.execute(
        text(
            "SELECT t.tgname, n.nspname AS table_schema, c.relname AS table_name, "
            "p.oid AS function_oid, fn.nspname AS function_schema, "
            "p.proname AS function_name, t.tgenabled AS trigger_enabled, "
            "pg_get_triggerdef(t.oid) AS trigger_definition, "
            "pg_get_functiondef(p.oid) AS function_definition "
            "FROM pg_trigger AS t "
            "JOIN pg_class AS c ON c.oid=t.tgrelid "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "JOIN pg_proc AS p ON p.oid=t.tgfoid "
            "JOIN pg_namespace AS fn ON fn.oid=p.pronamespace "
            "WHERE NOT t.tgisinternal AND n.nspname=current_schema() "
            "ORDER BY t.tgname,p.oid"
        )
    ).all()
    records: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for row in rows:
        if len(row) < 9:
            issues.append("invalid Knowledge Serving PostgreSQL trigger catalog row")
            continue
        name = str(row[0])
        if name in records:
            issues.append(f"duplicate Knowledge Serving PostgreSQL trigger {name}")
        records[name] = {
            "table_schema": str(row[1] or "").casefold(),
            "table_name": str(row[2] or ""),
            "function_oid": row[3],
            "function_schema": str(row[4] or "").casefold(),
            "function_name": str(row[5] or ""),
            "trigger_enabled": str(row[6] or "").casefold(),
            "trigger_definition": _normalized_sql(row[7]),
            "function_definition": _normalized_sql(row[8]),
        }

    immutable_oids: set[str] = set()
    for table in _KNOWLEDGE_SERVING_IMMUTABLE_TABLES:
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            record = records.get(name)
            if record is None:
                issues.append(f"missing Knowledge Serving immutable trigger {name}")
                continue
            if record["trigger_enabled"] not in {"o", "a"}:
                issues.append(f"disabled Knowledge Serving immutable trigger {name}")
            if record["table_schema"] != trusted_schema:
                issues.append(
                    f"invalid Knowledge Serving immutable trigger {name} schema "
                    f"{record['table_schema'] or '<missing>'}"
                )
            if record["table_name"].casefold() != table.casefold():
                issues.append(
                    f"invalid Knowledge Serving immutable trigger {name} target table "
                    f"{record['table_name'] or '<missing>'}"
                )
            trigger_definition = record["trigger_definition"]
            if f"before {operation}" not in trigger_definition or "execute function" not in (
                trigger_definition
            ):
                issues.append(f"invalid Knowledge Serving immutable trigger {name} operation")
            if record["function_name"] != "rag4c_knowledge_serving_immutable":
                issues.append(f"invalid Knowledge Serving immutable trigger {name} function")
            if record["function_schema"] != trusted_schema:
                issues.append(
                    f"invalid Knowledge Serving immutable trigger {name} function schema "
                    f"{record['function_schema'] or '<missing>'}"
                )
            function_oid = record["function_oid"]
            if function_oid is None:
                issues.append(f"invalid Knowledge Serving immutable trigger {name} function OID")
            else:
                immutable_oids.add(str(function_oid))
            if "raise exception" not in record["function_definition"]:
                issues.append(
                    f"invalid Knowledge Serving immutable function body for trigger {name}"
                )

    if len(immutable_oids) > 1:
        issues.append("inconsistent Knowledge Serving immutable trigger function OID")

    insert_name = "trg_tenant_knowledge_serving_events_validate_insert"
    insert_record = records.get(insert_name)
    if insert_record is None:
        issues.append("missing Knowledge Serving event predecessor trigger")
    else:
        if insert_record["trigger_enabled"] not in {"o", "a"}:
            issues.append("disabled Knowledge Serving event predecessor trigger")
        if insert_record["table_schema"] != trusted_schema:
            issues.append("invalid Knowledge Serving event predecessor trigger schema")
        if insert_record["table_name"].casefold() != "tenant_knowledge_serving_events":
            issues.append("invalid Knowledge Serving event predecessor trigger target table")
        trigger_definition = insert_record["trigger_definition"]
        if "before insert" not in trigger_definition or "execute function" not in trigger_definition:
            issues.append("invalid Knowledge Serving event predecessor trigger operation")
        if insert_record["function_name"] != "rag4c_knowledge_serving_event_validate":
            issues.append("invalid Knowledge Serving event predecessor trigger function")
        if insert_record["function_schema"] != trusted_schema:
            issues.append("invalid Knowledge Serving event predecessor function schema")
        if insert_record["function_oid"] is None:
            issues.append("invalid Knowledge Serving event predecessor trigger function OID")
        function_body = insert_record["function_definition"]
        for fragment in (
            "raise exception",
            "profile_id",
            "stream_key",
            "previous_event_digest",
            "sequence",
            "event_digest",
            "new.previous_event_digest is null",
            "not exists",
            "e.sequence=new.sequence-1",
            "e.event_digest=new.previous_event_digest",
        ):
            if _normalized_sql(fragment) not in function_body:
                issues.append(f"invalid Knowledge Serving predecessor function: {fragment}")

    return tuple(sorted(set(issues)))


def _knowledge_serving_guard_issues(connection: Any) -> tuple[str, ...]:
    dialect = str(getattr(connection.dialect, "name", "")).casefold()
    if dialect not in _KNOWLEDGE_SERVING_SUPPORTED_DIALECTS:
        return (f"unsupported database dialect for Knowledge Serving: {dialect or 'unknown'}",)
    if dialect == "postgresql":
        try:
            return _knowledge_serving_postgresql_guard_issues(connection)
        except Exception as exc:
            return (
                "cannot prove Knowledge Serving PostgreSQL guard semantics: "
                f"{exc.__class__.__name__}: {exc}",
            )

    definitions: dict[str, str]
    if dialect == "sqlite":
        definitions = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).all()
        }
    else:
        definitions = {
            str(name): f"{timing} {event} {statement}"
            for name, timing, event, statement in connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()"
                )
            ).all()
        }

    issues: list[str] = []
    for table in _KNOWLEDGE_SERVING_IMMUTABLE_TABLES:
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            definition = _normalized_sql(definitions.get(name))
            if not definition:
                issues.append(f"missing Knowledge Serving immutable trigger {name}")
            elif operation not in definition:
                issues.append(f"invalid Knowledge Serving immutable trigger {name}")
    insert_definition = _normalized_sql(
        definitions.get("trg_tenant_knowledge_serving_events_validate_insert")
    )
    if not insert_definition:
        issues.append("missing Knowledge Serving event predecessor trigger")
    else:
        for fragment in ("insert", "profile_id", "previous_event_digest", "sequence"):
            if _normalized_sql(fragment) not in insert_definition:
                issues.append(f"invalid Knowledge Serving event predecessor trigger: {fragment}")
    return tuple(sorted(set(issues)))


def _knowledge_serving_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return None


def _knowledge_serving_row(row: Any) -> dict[str, Any]:
    from datetime import datetime, timezone

    value = dict(row)
    for field in ("created_at", "updated_at", "archived_at", "as_of", "observed_at", "occurred_at"):
        moment = value.get(field)
        if isinstance(moment, str) and moment.strip():
            try:
                moment = datetime.fromisoformat(moment.strip().replace("Z", "+00:00"))
            except ValueError:
                pass
        if moment is not None and not isinstance(moment, str) and hasattr(moment, "isoformat"):
            if getattr(moment, "tzinfo", None) is None:
                moment = moment.replace(tzinfo=timezone.utc)
            value[field] = moment.isoformat(timespec="microseconds").replace("+00:00", "Z")
    for field in ("require_current_release", "require_passing_certification"):
        if type(value.get(field)) is int and value[field] in {0, 1}:
            value[field] = bool(value[field])
    if "safe_snapshot_json" in value:
        value["safe_snapshot_json"] = _knowledge_serving_json(value["safe_snapshot_json"])
    return value


def _knowledge_serving_parent_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    try:
        issues = _head_schema_issues(inspector)
    except Exception as exc:
        return (f"parent catalog capability inspection failed: {exc.__class__.__name__}",)
    parent_fragments = (
        "data_sources.",
        "source_sync_runs.",
        "document_ingest_attempts.",
        "chunk_heads.",
        "index_operations.",
        "tenant_release_channels.",
        "dataset_release_manifests.",
        "dataset_release_quality_certifications.",
        "tenant_task_projections.",
    )
    return tuple(
        sorted(
            {issue for issue in issues if any(fragment in issue for fragment in parent_fragments)}
        )
    )


def _canonical_knowledge_serving_snapshot_for_readiness(
    value: Mapping[str, Any],
    *,
    stage_facts: list[dict[str, Any]],
    evidence_links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonicalize a snapshot with all child facts required by its digest contract."""

    from core.enterprise_knowledge_serving import canonical_knowledge_serving_snapshot

    return canonical_knowledge_serving_snapshot(
        value,
        stage_facts=stage_facts,
        evidence_links=evidence_links,
    )


def _knowledge_serving_data_issues(connection: Any) -> tuple[str, ...]:
    from core.enterprise_knowledge_serving import (
        canonical_knowledge_serving_evidence_link,
        canonical_knowledge_serving_event,
        canonical_knowledge_serving_policy,
        canonical_knowledge_serving_stage_fact,
    )

    issues: list[str] = []
    profiles = {
        (str(row["tenant_id"]), str(row["id"])): _knowledge_serving_row(row)
        for row in connection.execute(
            text("SELECT * FROM tenant_knowledge_serving_profiles ORDER BY tenant_id,id")
        )
        .mappings()
        .all()
    }
    policies: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (
        connection.execute(
            text("SELECT * FROM tenant_knowledge_serving_policy_revisions ORDER BY tenant_id,id")
        )
        .mappings()
        .all()
    ):
        value = _knowledge_serving_row(row)
        key = (str(value["tenant_id"]), str(value["id"]))
        try:
            canonical = canonical_knowledge_serving_policy(value)
            if canonical["policy_digest"] != value.get("policy_digest"):
                issues.append(
                    f"invalid canonical Knowledge Serving policy digest {key[0]}/{key[1]}"
                )
        except Exception:
            issues.append(f"invalid canonical Knowledge Serving policy {key[0]}/{key[1]}")
        policies[key] = value
        profile_key = (key[0], str(value.get("profile_id") or ""))
        if profile_key not in profiles:
            issues.append(f"orphan Knowledge Serving policy {key[0]}/{key[1]}")

    stage_by_snapshot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    stage_by_id: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in (
        connection.execute(
            text(
                "SELECT * FROM tenant_knowledge_serving_stage_facts "
                "ORDER BY tenant_id,snapshot_id,sequence,id"
            )
        )
        .mappings()
        .all()
    ):
        value = _knowledge_serving_row(row)
        tenant = str(value["tenant_id"])
        identifier = str(value["id"])
        try:
            canonical = canonical_knowledge_serving_stage_fact(value)
            if canonical["stage_digest"] != value.get("stage_digest"):
                issues.append(
                    f"invalid canonical Knowledge Serving stage digest {tenant}/{identifier}"
                )
        except Exception as exc:
            qualifier = "stage digest" if "digest" in str(exc).casefold() else "stage fact"
            issues.append(f"invalid canonical Knowledge Serving {qualifier} {tenant}/{identifier}")
        snapshot_id = str(value.get("snapshot_id") or "")
        profile_id = str(value.get("profile_id") or "")
        stage_by_snapshot.setdefault((tenant, snapshot_id), []).append(value)
        stage_by_id[(tenant, profile_id, snapshot_id, identifier)] = value

    # Evidence is canonicalized before snapshots so a future snapshot digest can
    # bind the sorted evidence digests instead of receiving an untrusted raw row.
    evidence_by_snapshot: dict[tuple[str, str], list[dict[str, Any]]] = {}
    evidence_rows: list[dict[str, Any]] = []
    invalid_evidence_snapshots: set[tuple[str, str]] = set()
    for row in (
        connection.execute(
            text(
                "SELECT * FROM tenant_knowledge_serving_evidence_links "
                "ORDER BY tenant_id,snapshot_id,id"
            )
        )
        .mappings()
        .all()
    ):
        value = _knowledge_serving_row(row)
        evidence_rows.append(value)
        tenant = str(value["tenant_id"])
        identifier = str(value["id"])
        snapshot_key = (tenant, str(value.get("snapshot_id") or ""))
        try:
            canonical = canonical_knowledge_serving_evidence_link(value)
            if canonical["evidence_digest"] != value.get("evidence_digest"):
                issues.append(
                    f"invalid canonical Knowledge Serving evidence digest {tenant}/{identifier}"
                )
            evidence_by_snapshot.setdefault(snapshot_key, []).append(canonical)
        except Exception:
            invalid_evidence_snapshots.add(snapshot_key)
            issues.append(
                f"invalid canonical Knowledge Serving evidence link {tenant}/{identifier}"
            )

    available_tables = set(inspect(connection).get_table_names())

    def _parent_dataset_map(table_name: str) -> dict[tuple[str, str], str]:
        if table_name not in available_tables:
            return {}
        try:
            return {
                (str(row["tenant_id"]), str(row["id"])): str(row["dataset_id"])
                for row in connection.execute(
                    text(f"SELECT tenant_id,id,dataset_id FROM {table_name}")
                )
                .mappings()
                .all()
            }
        except Exception as exc:
            issues.append(
                f"Knowledge Serving snapshot {table_name} ownership inspection failed: "
                f"{exc.__class__.__name__}"
            )
            return {}

    release_datasets = _parent_dataset_map("dataset_release_manifests")
    certification_datasets = _parent_dataset_map("dataset_release_quality_certifications")

    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for row in (
        connection.execute(
            text("SELECT * FROM tenant_knowledge_serving_snapshots ORDER BY tenant_id,as_of,id")
        )
        .mappings()
        .all()
    ):
        value = _knowledge_serving_row(row)
        tenant = str(value["tenant_id"])
        identifier = str(value["id"])
        key = (tenant, identifier)
        facts = sorted(
            stage_by_snapshot.get(key, []),
            key=lambda item: (
                int(item.get("sequence") or 0),
                str(item.get("stage_code") or ""),
                str(item.get("id") or ""),
            ),
        )
        canonical_facts: list[dict[str, Any]] = []
        for fact in facts:
            try:
                canonical_facts.append(canonical_knowledge_serving_stage_fact(fact))
            except Exception:
                pass
        canonical_facts.sort(
            key=lambda item: (
                int(item.get("sequence") or 0),
                str(item.get("stage_code") or ""),
                str(item.get("id") or ""),
            )
        )
        canonical_evidence = sorted(
            evidence_by_snapshot.get(key, []),
            key=lambda item: (
                str(item.get("evidence_digest") or ""),
                str(item.get("id") or ""),
            ),
        )
        facts_complete = len(facts) == 5 and len(canonical_facts) == 5
        if not facts_complete:
            issues.append(
                f"Knowledge Serving snapshot does not contain exactly five stage facts {tenant}/{identifier}"
            )
        evidence_complete = key not in invalid_evidence_snapshots
        if not evidence_complete:
            issues.append(
                f"Knowledge Serving snapshot has invalid evidence links {tenant}/{identifier}"
            )
        if facts_complete and evidence_complete:
            try:
                canonical = _canonical_knowledge_serving_snapshot_for_readiness(
                    value,
                    stage_facts=canonical_facts,
                    evidence_links=canonical_evidence,
                )
                if canonical["snapshot_digest"] != value.get("snapshot_digest"):
                    issues.append(
                        f"invalid canonical Knowledge Serving snapshot digest {tenant}/{identifier}"
                    )
            except Exception:
                issues.append(f"invalid canonical Knowledge Serving snapshot {tenant}/{identifier}")
        snapshots[key] = value
        profile_key = (tenant, str(value.get("profile_id") or ""))
        profile = profiles.get(profile_key)
        if profile is None:
            issues.append(f"orphan Knowledge Serving snapshot {tenant}/{identifier}")
        policy = policies.get((tenant, str(value.get("policy_revision_id") or "")))
        if policy is None:
            issues.append(f"orphan Knowledge Serving snapshot policy {tenant}/{identifier}")
        elif str(policy.get("profile_id") or "") != str(value.get("profile_id") or ""):
            issues.append(
                f"Knowledge Serving snapshot policy ownership mismatch {tenant}/{identifier}"
            )
        expected_dataset_id = str(profile.get("dataset_id") or "") if profile else ""
        release_id = value.get("current_release_id")
        if release_id is not None:
            release_key = (tenant, str(release_id))
            release_dataset_id = release_datasets.get(release_key)
            if release_dataset_id is None:
                issues.append(f"orphan Knowledge Serving snapshot release {tenant}/{identifier}")
            elif expected_dataset_id and release_dataset_id != expected_dataset_id:
                issues.append(
                    f"snapshot release dataset ownership mismatch {tenant}/{identifier}"
                )
        certification_id = value.get("current_certification_id")
        if certification_id is not None:
            certification_key = (tenant, str(certification_id))
            certification_dataset_id = certification_datasets.get(certification_key)
            if certification_dataset_id is None:
                issues.append(
                    f"orphan Knowledge Serving snapshot certification {tenant}/{identifier}"
                )
            elif expected_dataset_id and certification_dataset_id != expected_dataset_id:
                issues.append(
                    f"snapshot certification dataset ownership mismatch {tenant}/{identifier}"
                )
        for fact in facts:
            if str(fact.get("profile_id")) != str(value.get("profile_id")):
                issues.append(
                    f"Knowledge Serving stage/profile ownership mismatch {tenant}/{identifier}"
                )

    for value in evidence_rows:
        tenant = str(value["tenant_id"])
        identifier = str(value["id"])
        profile_id = str(value.get("profile_id") or "")
        snapshot_id = str(value.get("snapshot_id") or "")
        stage_fact_id = str(value.get("stage_fact_id") or "")
        profile_key = (tenant, profile_id)
        snapshot_key = (tenant, snapshot_id)
        stage_key = (tenant, profile_id, snapshot_id, stage_fact_id)
        snapshot = snapshots.get(snapshot_key)
        stage = stage_by_id.get(stage_key)
        if profile_key not in profiles:
            issues.append(f"orphan Knowledge Serving evidence profile {tenant}/{identifier}")
        if snapshot is None or stage is None:
            issues.append(f"orphan Knowledge Serving evidence link {tenant}/{identifier}")
        else:
            if str(snapshot.get("profile_id") or "") != profile_id:
                issues.append(
                    f"Knowledge Serving evidence/profile ownership mismatch {tenant}/{identifier}"
                )
            if stage.get("snapshot_id") != snapshot.get("id") or stage.get(
                "profile_id"
            ) != value.get("profile_id"):
                issues.append(
                    f"Knowledge Serving evidence ownership mismatch {tenant}/{identifier}"
                )
            profile = profiles.get(profile_key)
            expected_dataset_id = str(profile.get("dataset_id") or "") if profile else ""
            evidence_kind = str(value.get("evidence_kind") or "")
            resource_key = (tenant, str(value.get("resource_id") or ""))
            if evidence_kind == "release":
                resource_dataset_id = release_datasets.get(resource_key)
                if resource_dataset_id is None:
                    issues.append(f"orphan Knowledge Serving evidence release resource {tenant}/{identifier}")
                elif expected_dataset_id and resource_dataset_id != expected_dataset_id:
                    issues.append(
                        f"evidence release resource dataset ownership mismatch {tenant}/{identifier}"
                    )
            elif evidence_kind == "certification":
                resource_dataset_id = certification_datasets.get(resource_key)
                if resource_dataset_id is None:
                    issues.append(
                        f"orphan Knowledge Serving evidence certification resource {tenant}/{identifier}"
                    )
                elif expected_dataset_id and resource_dataset_id != expected_dataset_id:
                    issues.append(
                        f"evidence certification resource dataset ownership mismatch {tenant}/{identifier}"
                    )

    events_by_stream: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in (
        connection.execute(
            text(
                "SELECT * FROM tenant_knowledge_serving_events "
                "ORDER BY tenant_id,profile_id,stream_key,sequence,id"
            )
        )
        .mappings()
        .all()
    ):
        value = _knowledge_serving_row(row)
        tenant = str(value["tenant_id"])
        identifier = str(value["id"])
        try:
            canonical = canonical_knowledge_serving_event(value)
            if canonical["event_digest"] != value.get("event_digest"):
                issues.append(
                    f"invalid canonical Knowledge Serving event digest {tenant}/{identifier}"
                )
        except Exception:
            issues.append(f"invalid canonical Knowledge Serving event {tenant}/{identifier}")
        events_by_stream.setdefault(
            (tenant, str(value.get("profile_id") or ""), str(value.get("stream_key") or "")), []
        ).append(value)
        if (tenant, str(value.get("profile_id") or "")) not in profiles:
            issues.append(f"orphan Knowledge Serving event {tenant}/{identifier}")
        snapshot_id = value.get("snapshot_id")
        if snapshot_id is not None:
            snapshot = snapshots.get((tenant, str(snapshot_id)))
            if snapshot is None:
                issues.append(f"orphan Knowledge Serving event snapshot {tenant}/{identifier}")
            elif str(snapshot.get("profile_id") or "") != str(value.get("profile_id") or ""):
                issues.append(
                    f"Knowledge Serving event snapshot ownership mismatch {tenant}/{identifier}"
                )

    for stream, rows in events_by_stream.items():
        ordered = sorted(
            rows, key=lambda item: (int(item.get("sequence") or 0), str(item.get("id") or ""))
        )
        tenant, profile_id, stream_key = stream
        if (
            not ordered
            or int(ordered[0].get("sequence") or 0) != 1
            or ordered[0].get("event_type") != "profile_created"
            or ordered[0].get("previous_event_digest") is not None
        ):
            issues.append(
                f"invalid Knowledge Serving event stream start {tenant}/{profile_id}/{stream_key}"
            )
        for previous, current in zip(ordered, ordered[1:]):
            if int(current.get("sequence") or 0) != int(previous.get("sequence") or 0) + 1:
                issues.append(
                    f"non-contiguous Knowledge Serving event stream {tenant}/{profile_id}/{stream_key}"
                )
            if current.get("previous_event_digest") != previous.get("event_digest"):
                issues.append(
                    f"broken Knowledge Serving event hash chain {tenant}/{profile_id}/{stream_key}"
                )

    for key, profile in profiles.items():
        tenant, profile_id = key
        canonical_stream = (tenant, profile_id, f"profile:{profile_id}")
        profile_streams = [stream for stream in events_by_stream if stream[:2] == key]
        if canonical_stream not in events_by_stream:
            issues.append(f"missing canonical Knowledge Serving event stream {tenant}/{profile_id}")
        if any(stream != canonical_stream for stream in profile_streams):
            issues.append(f"non-canonical Knowledge Serving event stream {tenant}/{profile_id}")
        try:
            from core.enterprise_knowledge_serving import canonical_serving_profile

            canonical_serving_profile(_knowledge_serving_row(profile))
        except Exception:
            issues.append(f"invalid canonical Knowledge Serving profile {tenant}/{profile_id}")
        current_policy = profile.get("current_policy_revision_id")
        if current_policy is not None:
            policy = policies.get((tenant, str(current_policy)))
            if policy is None or str(policy.get("profile_id")) != str(profile_id):
                issues.append(
                    f"Knowledge Serving current policy pointer ownership mismatch {tenant}/{profile_id}"
                )
        current_snapshot = profile.get("current_snapshot_id")
        if current_snapshot is not None:
            snapshot = snapshots.get((tenant, str(current_snapshot)))
            if snapshot is None or str(snapshot.get("profile_id")) != str(profile_id):
                issues.append(
                    f"Knowledge Serving current snapshot pointer ownership mismatch {tenant}/{profile_id}"
                )
            elif (
                current_policy is not None and snapshot.get("policy_revision_id") != current_policy
            ):
                issues.append(
                    f"Knowledge Serving current policy/snapshot pointer mismatch {tenant}/{profile_id}"
                )

    return tuple(sorted(set(issues)))


def _enterprise_knowledge_serving_reliability_capability_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES - tables
    issues: list[str] = [f"missing table {name}" for name in sorted(missing_tables)]
    if missing_tables:
        return tuple(sorted(set(issues)))

    checks_by_table: dict[str, dict[str, str]] = {}
    for table in sorted(ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES):
        columns = {str(item.get("name")): item for item in inspector.get_columns(table)}
        required = ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_COLUMNS[table]
        issues.extend(f"missing column {table}.{name}" for name in sorted(required - set(columns)))
        for name in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_NOT_NULL[table]:
            if name in columns and bool(columns[name].get("nullable", True)):
                issues.append(f"nullable column {table}.{name}")

        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_UNIQUES[
            table
        ].items():
            if uniques.get(name) != tuple(expected):
                issues.append(f"missing or invalid unique {table}.{name}")

        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table")),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_FOREIGN_KEYS[
            table
        ].items():
            if foreign_keys.get(name) != expected:
                issues.append(f"missing or invalid foreign key {table}.{name}")
        for name, contract in foreign_keys.items():
            if contract[0] and contract[0][0] != "tenant_id":
                issues.append(f"non-tenant-leading foreign key {table}.{name}")

        checks = {
            str(item.get("name")): str(item.get("sqltext") or "")
            for item in inspector.get_check_constraints(table)
            if item.get("name")
        }
        checks_by_table[table] = checks
        for name, fragments in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_CHECK_FRAGMENTS[
            table
        ].items():
            sql = _parenless_sql(checks.get(name))
            if not sql or any(_parenless_sql(fragment) not in sql for fragment in fragments):
                issues.append(f"missing or invalid check {table}.{name}")

        indexes = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table)
            if item.get("name")
        }
        for name, expected in ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_INDEXES[
            table
        ].items():
            if indexes.get(name) != tuple(expected):
                issues.append(f"missing or invalid index {table}.{name}")

    issues.extend(_knowledge_serving_exact_allowlist_issues(checks_by_table))
    issues.extend(_knowledge_serving_guard_issues(connection))
    issues.extend(_knowledge_serving_data_issues(connection))
    issues.extend(_knowledge_serving_parent_issues(connection))
    return tuple(sorted(set(issues)))


def inspect_enterprise_knowledge_serving_reliability_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    """Return revision-aware Stage26 Knowledge Serving authority state."""

    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        dialect = str(getattr(connection.dialect, "name", "")).casefold()
        if dialect not in _KNOWLEDGE_SERVING_SUPPORTED_DIALECTS:
            return "unavailable", (
                f"unsupported database dialect for Knowledge Serving: {dialect or 'unknown'}",
            )
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        present = bool(ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        if len(revisions) > 1:
            return "unavailable", ("alembic_version contains multiple revisions",)
        revision = revisions[0] if revisions else None
        if revision != ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION:
            if not present and revision in _known_catalog_revisions():
                return "not_available", ()
            return "unavailable", ("catalog is not at a known pre-0036 or 0036 revision",)
        issues = _enterprise_knowledge_serving_reliability_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (
            f"Knowledge Serving schema inspection failed: {exc.__class__.__name__}",
        )


inspect_enterprise_knowledge_serving_capability = (
    inspect_enterprise_knowledge_serving_reliability_capability
)

# Extend the existing global schema manifest without making Stage26 readiness
# recursively call itself through the legacy parent checks.
_KNOWLEDGE_SERVING_LEGACY_HEAD_SCHEMA_ISSUES = _head_schema_issues


def _knowledge_serving_parent_issues(connection: Any) -> tuple[str, ...]:
    inspector = inspect(connection)
    try:
        issues = _KNOWLEDGE_SERVING_LEGACY_HEAD_SCHEMA_ISSUES(inspector)
    except Exception as exc:
        return (f"parent catalog capability inspection failed: {exc.__class__.__name__}",)
    parent_fragments = (
        "data_sources.",
        "source_sync_runs.",
        "document_ingest_attempts.",
        "chunk_heads.",
        "index_operations.",
        "tenant_release_channels.",
        "dataset_release_manifests.",
        "dataset_release_quality_certifications.",
        "tenant_task_projections.",
    )
    return tuple(
        sorted(
            {issue for issue in issues if any(fragment in issue for fragment in parent_fragments)}
        )
    )


# A later catalog head must continue to use the prior approval CHECK exact
# contract; that map is revision-keyed by design for Stage25 compatibility.
if HEAD_REVISION not in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION:
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[HEAD_REVISION] = (
        ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION[
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION
        ]
    )


def _head_schema_issues(inspector: Any) -> tuple[str, ...]:
    issues = list(_KNOWLEDGE_SERVING_LEGACY_HEAD_SCHEMA_ISSUES(inspector))
    tables = set(inspector.get_table_names())
    if ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES <= tables:
        issues.extend(
            _schema_connection(
                inspector.bind,
                _enterprise_knowledge_serving_reliability_capability_issues,
            )
        )
    return tuple(sorted(set(issues)))


ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES = (
    ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_TABLES
)

# Stage25 remains a valid capability when the catalog advances to Stage26.
_KNOWLEDGE_SERVING_LEGACY_AUTOMATION_CAPABILITY = inspect_enterprise_automation_workflows_capability


def inspect_enterprise_automation_workflows_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    def inspect_connection(connection: Any) -> tuple[str, tuple[str, ...]]:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        present = bool(ENTERPRISE_AUTOMATION_WORKFLOWS_TABLES & tables)
        revisions: tuple[str, ...] = ()
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
        if len(revisions) != 1:
            return "unavailable", ("alembic_version contains multiple revisions",) if len(
                revisions
            ) > 1 else ("catalog revision is missing",)
        revision = revisions[0]
        known = _known_catalog_revisions()
        if revision not in known:
            return "unavailable", ("catalog is not at a known revision",)
        if not present:
            return (
                "not_available",
                ()
                if revision != ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION
                else ("Automation tables are missing",),
            )
        if _REVISION_ORDER_FOR_CAPABILITY(revision) < _REVISION_ORDER_FOR_CAPABILITY(
            ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION
        ):
            return "unavailable", ("catalog is before 0035 Automation Workflows revision",)
        issues = _enterprise_automation_workflows_capability_issues(connection)
        return ("ready", ()) if not issues else ("unavailable", issues)

    try:
        return _schema_connection(bind, inspect_connection)
    except Exception as exc:
        return "unavailable", (f"Automation schema inspection failed: {exc.__class__.__name__}",)


def _REVISION_ORDER_FOR_CAPABILITY(revision: str) -> int:
    order = {
        value: index
        for index, value in enumerate(
            (
                BASELINE_REVISION,
                DATASET_ACL_CONTROL_REVISION,
                TENANT_INVITATION_LIFECYCLE_REVISION,
                ENTERPRISE_IDENTITY_FEDERATION_REVISION,
                SCIM_PROVISIONING_DATA_PLANE_REVISION,
                AUDIT_COMPLIANCE_REVISION,
                OIDC_SSO_RUNTIME_REVISION,
                ENTERPRISE_APPROVAL_CONTROL_REVISION,
                ENTERPRISE_WORKSPACE_CONTROL_REVISION,
                ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
                ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
                ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
                ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
                ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
                ENTERPRISE_NOTIFICATION_CENTER_REVISION,
                ENTERPRISE_CONTENT_RECOVERY_REVISION,
                ENTERPRISE_TASK_OPERATIONS_REVISION,
                ENTERPRISE_AUTOMATION_WORKFLOWS_REVISION,
                ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REVISION,
                QA_FAQ_OPS_REVISION,
                STORAGE_BACKENDS_REVISION,
                ANSWER_EVIDENCE_FACTS_REVISION,
            ),
        )
    }
    return order.get(revision, -1)


def _knowledge_serving_revision_compatible(
    bind: Any,
    original: Any,
    required_tables: frozenset[str],
    minimum_revision: str,
    issue_checker: Any,
    *,
    revision_aware: bool = False,
) -> tuple[str, tuple[str, ...]]:
    def probe(connection: Any) -> tuple[str, tuple[str, ...]] | None:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "alembic_version" not in tables:
            return None
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if len(revisions) != 1:
            return None
        revision = revisions[0]
        if revision not in _known_catalog_revisions() or _REVISION_ORDER_FOR_CAPABILITY(
            revision
        ) < _REVISION_ORDER_FOR_CAPABILITY(minimum_revision):
            return None
        # 能走到这里说明：revision 是已知修订，且**不早于**本能力的引入版本。
        # 也就是这张 schema 本该包含该能力的表。此时一张都找不到，是
        # 「迁移已盖章但表被删/未建」的损坏安装，必须 fail-closed。
        #
        # 曾经这里返回 "not_available"，后果是真实的越权：
        # 上层（如 core/enterprise_access_control.py）把 "not_available" 当作
        # "老库、按旧行为降级"的合法状态，于是「删除权限策略表」就能让权限检查
        # 静默放行——tests/test_stage17_authorization_security_regressions.py
        # 里两条测试正是钉死这个不变量的（DID NOT RAISE 即回归）。
        # legacy 库不会走到这里：上面的 revision 比较已经 return None 交给 original。
        if not (required_tables & tables):
            missing = ", ".join(sorted(required_tables - tables))
            return "unavailable", (f"required tables are missing: {missing}",)
        issues = (
            issue_checker(connection, revision) if revision_aware else issue_checker(connection)
        )
        return ("ready", ()) if not issues else ("unavailable", issues)

    result = _schema_connection(bind, probe)
    return original(bind) if result is None else result


_KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY = (
    inspect_enterprise_knowledge_base_registry_capability
)
_KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY = (
    inspect_enterprise_knowledge_base_release_capability
)
_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY = (
    inspect_enterprise_release_quality_certification_capability
)
_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY = (
    inspect_enterprise_release_quality_operations_capability
)
_KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY = (
    inspect_workspace_authorization_capability
)
_KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY = inspect_enterprise_task_operations_capability


def inspect_enterprise_knowledge_base_registry_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY,
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_TABLES,
        ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION,
        lambda connection, revision: _knowledge_base_registry_capability_issues(
            connection,
            approval_action_revision=revision,
        ),
        revision_aware=True,
    )


def inspect_enterprise_knowledge_base_release_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY,
        ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES,
        ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION,
        _enterprise_knowledge_base_release_capability_issues,
    )


def inspect_enterprise_release_quality_certification_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY,
        ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES,
        ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REVISION,
        _enterprise_release_quality_certification_capability_issues,
    )


def inspect_enterprise_release_quality_operations_capability(
    bind: Any,
) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY,
        ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES,
        ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION,
        _enterprise_release_quality_operations_capability_issues,
    )


def inspect_workspace_authorization_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY,
        ENTERPRISE_WORKSPACE_AUTHORIZATION_TABLES,
        ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION,
        lambda connection, revision: _workspace_authorization_capability_issues(
            connection,
            approval_action_revision=revision,
        ),
        revision_aware=True,
    )


def inspect_enterprise_task_operations_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    return _knowledge_serving_revision_compatible(
        bind,
        _KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY,
        ENTERPRISE_TASK_OPERATIONS_TABLES,
        ENTERPRISE_TASK_OPERATIONS_REVISION,
        _enterprise_task_operations_capability_issues,
    )
