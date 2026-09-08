"""Reflection-backed enterprise approval lifecycle control plane."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import base64
import hashlib
import hmac
import json
import re
from typing import Any, Callable
import uuid

from sqlalchemy import MetaData, Table, and_, exists, func, inspect, or_, select, text, update
from sqlalchemy.orm import Session

from core.catalog_schema import (
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECK_FRAGMENTS,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_COLUMNS,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_FOREIGN_KEYS,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_INDEXES,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_NOT_NULL,
    ENTERPRISE_APPROVAL_CONTROL_REQUIRED_UNIQUES,
    _canonical_check_sql,
    _known_catalog_revisions,
    _normalized_sql,
)
from core.enterprise_tenant_idempotency import (
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from models.orm import (
    Tenant,
    TenantAuditEvent,
    TenantDocumentLegalHold,
    TenantDocumentPurgeRequest,
    TenantDocumentRecycleEntry,
)

_REVISION = "0025_enterprise_approval_control"


def _revision_order(value: str) -> int:
    prefix, separator, _suffix = value.partition("_")
    return int(prefix) if separator and prefix.isdigit() else -1


_SUPPORTED_REVISIONS = frozenset(
    revision
    for revision in _known_catalog_revisions()
    if _revision_order(revision) >= _revision_order(_REVISION)
)
_ACTION_TYPES = frozenset(
    {
        "catalog_upgrade",
        "membership_bootstrap",
        "dataset_acl_disable",
        "member_role_change",
        "identity_provider_disable",
        "audit_retention_execute",
        "workspace_authorization_mode_change",
        "dataset_workspace_transfer",
        "knowledge_base_release_publish",
        "knowledge_base_release_rollback",
        "knowledge_base_release_quality_waiver",
        "document_purge",
    }
)
_APPROVER_KINDS = frozenset({"account", "role", "group"})
_ROLES = frozenset({"owner", "admin", "editor", "member"})
_STATUSES = frozenset(
    {
        "pending",
        "approved",
        "rejected",
        "cancelled",
        "expired",
        "executing",
        "executed",
        "execution_failed",
    }
)
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:secret|password|passwd|credential|authorization|access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|token|ticket|invite[_-]?link|invitation[_-]?link|cookie|private[_-]?key|client[_-]?secret|connection[_-]?string|database[_-]?url|db[_-]?url|dsn|jdbc)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"sk_(?:live|test)[-_][^\s]+|pat[_-][^\s]+|ghp_[^\s]+|github_pat_[^\s]+|"
    r"xox[baprs]-[^\s]+|bearer\s+[^\s]+|"
    r"(?:secret|password|passwd|credential|authorization|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|token|invite[_-]?link|invitation[_-]?link)\s*[:=]\s*[^\s]+|"
    r"code\s*[:=]\s*[^\s]+|state\s*[:=]\s*[^\s]+"
    r")"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\+[a-z0-9_]+)?|mongodb(?:\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\/\/[^\s]+"
)
_URL_USERINFO_RE = re.compile(r"(?i)\bhttps?:\/\/[^\s/@:]+:[^\s/@]+@[^\s]+")
_INVITATION_LINK_RE = re.compile(
    r"(?i)\bhttps?:\/\/[^\s]+\/(?:invite|invitation|invitations)\/accept(?:\/|\?|#)[^\s]*"
)
_JWT_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_REDACTED = "[REDACTED]"


class ApprovalError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ApprovalMigrationRequired(ApprovalError):
    def __init__(self) -> None:
        super().__init__(
            "approval_migration_required",
            "审批中心需要先完成 0025_enterprise_approval_control 数据库迁移",
            503,
        )


class ApprovalForbidden(ApprovalError):
    pass


class ApprovalNotFound(ApprovalError):
    def __init__(self, message: str = "审批资源不存在") -> None:
        super().__init__("approval_resource_not_found", message, 404)


class ApprovalValidation(ApprovalError):
    pass


class ApprovalConflict(ApprovalError):
    pass


class ApprovalExecutionAdapterNotConnected(ApprovalError):
    def __init__(self) -> None:
        super().__init__(
            "execution_adapter_not_connected",
            "审批已完成授权，但下游执行适配器尚未接入；未执行任何下游动作",
            503,
        )


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


@dataclass(frozen=True, slots=True)
class ApprovalExecutionFact(Mapping[str, Any]):
    """Opaque-in-practice facts issued only after the approval ticket is claimed."""

    tenant_id: str
    approval_request_id: str
    execution_id: str
    request_revision: int
    execution_revision: int
    action_type: str
    resource_type: str
    resource_id: str
    snapshot_hash: str
    workspace_revision: int | None = None
    policy_revision: int | None = None
    from_mode: str | None = None
    target_mode: str | None = None
    permission_model_version: int | None = None
    permission_matrix_fingerprint: str | None = None
    reason: str = ""
    profile_revision: int | None = None
    dataset_profile_revision: int | None = None
    expected_dataset_profile_revision: int | None = None
    ownership_revision: int | None = None
    expected_ownership_revision: int | None = None
    source_workspace_id: str | None = None
    target_workspace_id: str | None = None
    source_workspace_revision: int | None = None
    target_workspace_revision: int | None = None
    expected_source_workspace_revision: int | None = None
    expected_target_workspace_revision: int | None = None
    release_id: str | None = None
    release_number: int | None = None
    manifest_digest: str | None = None
    mutation_generation: int | None = None
    channel_id: str | None = None
    channel_revision: int | None = None
    serving_generation: int | None = None
    workspace_id: str | None = None
    policy_id: str | None = None
    policy_digest: str | None = None
    quality_gate_revision: int | None = None
    quality_gate_digest: str | None = None
    quality_evidence_digest: str | None = None
    evidence_digest: str | None = None
    waiver_expires_at: str | None = None
    requested_expires_at: str | None = None
    quality_gate_state: str | None = None
    quality_gate_reason: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "tenant_id",
            "approval_request_id",
            "execution_id",
            "action_type",
            "resource_type",
            "resource_id",
            "snapshot_hash",
            "reason",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} is required for ApprovalExecutionFact")
        for field in ("request_revision", "execution_revision"):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if self.execution_revision != self.request_revision + 1:
            raise ValueError("execution_revision must immediately follow request_revision")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.snapshot_hash):
            raise ValueError("snapshot_hash must be a SHA-256 hex digest")

        if self.action_type == "workspace_authorization_mode_change":
            for field in ("workspace_revision", "policy_revision", "permission_model_version"):
                value = getattr(self, field)
                if type(value) is not int or value < 1:
                    raise ValueError(f"{field} is required for Workspace authorization fact")
            for field in ("from_mode", "target_mode", "permission_matrix_fingerprint"):
                value = getattr(self, field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field} is required for Workspace authorization fact")

        if self.action_type == "dataset_workspace_transfer":
            for field in (
                "profile_revision",
                "dataset_profile_revision",
                "expected_dataset_profile_revision",
                "ownership_revision",
                "expected_ownership_revision",
                "source_workspace_revision",
                "target_workspace_revision",
                "expected_source_workspace_revision",
                "expected_target_workspace_revision",
            ):
                value = getattr(self, field)
                if type(value) is not int or value < 1:
                    raise ValueError(f"{field} is required for Dataset Workspace transfer fact")
            if (
                len(
                    {
                        self.profile_revision,
                        self.dataset_profile_revision,
                        self.expected_dataset_profile_revision,
                    }
                )
                != 1
            ):
                raise ValueError("Dataset profile revision aliases must agree")
            if len({self.ownership_revision, self.expected_ownership_revision}) != 1:
                raise ValueError("Ownership revision aliases must agree")
            for field in ("source_workspace_id", "target_workspace_id"):
                value = getattr(self, field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field} is required for Dataset Workspace transfer fact")
            if self.source_workspace_id == self.target_workspace_id:
                raise ValueError("source and target Workspace must differ")
            if (
                self.expected_source_workspace_revision != self.source_workspace_revision
                or self.expected_target_workspace_revision != self.target_workspace_revision
            ):
                raise ValueError("Workspace revision aliases must agree")

        if self.action_type in {
            "knowledge_base_release_publish",
            "knowledge_base_release_rollback",
            "knowledge_base_release_quality_waiver",
        }:
            for field in (
                "release_number",
                "channel_revision",
                "profile_revision",
                "mutation_generation",
                "ownership_revision",
                "workspace_revision",
                "serving_generation",
            ):
                value = getattr(self, field)
                minimum = 0 if field == "serving_generation" else 1
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{field} is required for Knowledge Base Release fact")
            for field in (
                "release_id",
                "manifest_digest",
                "channel_id",
                "workspace_id",
            ):
                value = getattr(self, field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field} is required for Knowledge Base Release fact")
            if not re.fullmatch(r"[0-9a-f]{64}", str(self.manifest_digest)):
                raise ValueError("manifest_digest must be a lowercase SHA-256 digest")

        if self.action_type == "knowledge_base_release_quality_waiver":
            for field in ("policy_revision", "quality_gate_revision"):
                value = getattr(self, field)
                if type(value) is not int or value < 1:
                    raise ValueError(f"{field} is required for Quality Waiver fact")
            for field in ("policy_id", "policy_digest", "quality_gate_digest"):
                value = getattr(self, field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field} is required for Quality Waiver fact")
            if not re.fullmatch(r"[0-9a-f]{64}", str(self.policy_digest)):
                raise ValueError("policy_digest must be a lowercase SHA-256 digest")
            if not re.fullmatch(r"[0-9a-f]{64}", str(self.quality_gate_digest)):
                raise ValueError("quality_gate_digest must be a lowercase SHA-256 digest")
            if self.quality_gate_revision != self.channel_revision:
                raise ValueError("quality_gate_revision must match channel_revision")
            evidence_values = [
                value
                for value in (self.quality_evidence_digest, self.evidence_digest)
                if value is not None
            ]
            if len(set(evidence_values)) > 1:
                raise ValueError("quality evidence digest aliases must agree")
            if evidence_values and not re.fullmatch(r"[0-9a-f]{64}", str(evidence_values[0])):
                raise ValueError("quality_evidence_digest must be a lowercase SHA-256 digest")
            expiry_values = [
                value
                for value in (self.waiver_expires_at, self.requested_expires_at)
                if value is not None
            ]
            if len(set(expiry_values)) != 1:
                raise ValueError("waiver expiry aliases must agree")
            if not expiry_values or not isinstance(expiry_values[0], str):
                raise ValueError("waiver_expires_at is required for Quality Waiver fact")
            parsed_expiry = _datetime(expiry_values[0])
            if parsed_expiry is None:
                raise ValueError("waiver_expires_at must be an ISO datetime")
            canonical_expiry = _iso(parsed_expiry)
            if canonical_expiry != expiry_values[0]:
                raise ValueError("waiver_expires_at must be canonical")
            if self.quality_gate_state is not None and self.quality_gate_state != "blocked":
                raise ValueError("quality_gate_state must be blocked for Quality Waiver fact")
            if self.quality_gate_reason is not None and not self.quality_gate_reason.strip():
                raise ValueError("quality_gate_reason is invalid")

    def _mapping(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "approval_request_id": self.approval_request_id,
            "request_id": self.approval_request_id,
            "approval_execution_id": self.execution_id,
            "execution_id": self.execution_id,
            "request_revision": self.request_revision,
            "execution_revision": self.execution_revision,
            "action_type": self.action_type,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "snapshot_hash": self.snapshot_hash,
            "reason": self.reason,
        }
        profile_revision = next(
            (
                value
                for value in (
                    self.profile_revision,
                    self.dataset_profile_revision,
                    self.expected_dataset_profile_revision,
                )
                if value is not None
            ),
            None,
        )
        if profile_revision is not None:
            values.update(
                {
                    "profile_revision": profile_revision,
                    "dataset_profile_revision": profile_revision,
                    "expected_profile_revision": profile_revision,
                    "expected_dataset_profile_revision": profile_revision,
                }
            )
        ownership_revision = next(
            (
                value
                for value in (self.ownership_revision, self.expected_ownership_revision)
                if value is not None
            ),
            None,
        )
        if ownership_revision is not None:
            values["ownership_revision"] = ownership_revision
            values["expected_ownership_revision"] = ownership_revision
        if self.source_workspace_id is not None:
            values["source_workspace_id"] = self.source_workspace_id
        if self.target_workspace_id is not None:
            values["target_workspace_id"] = self.target_workspace_id
        if self.source_workspace_revision is not None:
            values["source_workspace_revision"] = self.source_workspace_revision
            values["expected_source_workspace_revision"] = (
                self.expected_source_workspace_revision
                if self.expected_source_workspace_revision is not None
                else self.source_workspace_revision
            )
        if self.target_workspace_revision is not None:
            values["target_workspace_revision"] = self.target_workspace_revision
            values["expected_target_workspace_revision"] = (
                self.expected_target_workspace_revision
                if self.expected_target_workspace_revision is not None
                else self.target_workspace_revision
            )
        if self.workspace_revision is not None:
            values["workspace_revision"] = self.workspace_revision
        if self.policy_revision is not None:
            values["policy_revision"] = self.policy_revision
        if self.from_mode is not None:
            values["from_mode"] = self.from_mode
        if self.target_mode is not None:
            values["target_mode"] = self.target_mode
        if self.permission_model_version is not None:
            values["permission_model_version"] = self.permission_model_version
        if self.permission_matrix_fingerprint is not None:
            values["permission_matrix_fingerprint"] = self.permission_matrix_fingerprint
        for field in (
            "release_id",
            "release_number",
            "manifest_digest",
            "mutation_generation",
            "channel_id",
            "channel_revision",
            "serving_generation",
            "workspace_id",
        ):
            value = getattr(self, field)
            if value is not None:
                values[field] = value
        if self.policy_id is not None:
            values["policy_id"] = self.policy_id
        if self.policy_digest is not None:
            values["policy_digest"] = self.policy_digest
        if self.quality_gate_revision is not None:
            values["quality_gate_revision"] = self.quality_gate_revision
        if self.quality_gate_digest is not None:
            values["quality_gate_digest"] = self.quality_gate_digest
        evidence_digest = self.quality_evidence_digest or self.evidence_digest
        if evidence_digest is not None:
            values["quality_evidence_digest"] = evidence_digest
            values["evidence_digest"] = evidence_digest
        expiry = self.waiver_expires_at or self.requested_expires_at
        if expiry is not None:
            values["waiver_expires_at"] = expiry
            values["requested_expires_at"] = expiry
        if self.quality_gate_state is not None:
            values["quality_gate_state"] = self.quality_gate_state
        if self.quality_gate_reason is not None:
            values["quality_gate_reason"] = self.quality_gate_reason
        return values

    def __getitem__(self, key: str) -> Any:
        return self._mapping()[key]

    def __iter__(self):
        return iter(self._mapping())

    def __len__(self) -> int:
        return len(self._mapping())


@dataclass(frozen=True)
class _Actor:
    tenant_id: str
    account_id: str
    role: str
    name: str
    email: str


def _table(session: Session, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=session.connection())


def _value(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError):
        return default


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"
    return str(value)


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ApprovalValidation("approval_request_invalid", f"{field} 必须是字符串", 422)
    result = value.strip()
    if (not result and not allow_empty) or len(result) > maximum:
        raise ApprovalValidation("approval_request_invalid", f"{field} 无效", 422)
    if any(ord(char) < 32 and char not in "\t\n\r" for char in result):
        raise ApprovalValidation("approval_request_invalid", f"{field} 无效", 422)
    return result


def _positive_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ApprovalValidation(
            "approval_request_invalid", f"{field} 必须在 {minimum} 到 {maximum} 之间", 422
        )
    return value


def _redact_text(value: str) -> str:
    if (
        _SECRET_VALUE_RE.search(value)
        or _DATABASE_URL_RE.search(value)
        or _URL_USERINFO_RE.search(value)
        or _INVITATION_LINK_RE.search(value)
        or _JWT_LIKE_RE.search(value)
    ):
        return _REDACTED
    return value


def _redact_snapshot(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        raise ApprovalValidation("approval_snapshot_invalid", "snapshot 嵌套层级过深", 422)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _clean(raw_key, "snapshot key", 128)
            result[key] = (
                _redact_snapshot(raw_value, depth=depth + 1)
                if key.casefold() == "authorization_policy"
                else _REDACTED
                if _SECRET_KEY_RE.search(key)
                else _redact_snapshot(raw_value, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise ApprovalValidation("approval_snapshot_invalid", "snapshot 数组过大", 422)
        return [_redact_snapshot(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 4096:
            raise ApprovalValidation("approval_snapshot_invalid", "snapshot 字符串过长", 422)
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ApprovalValidation("approval_snapshot_invalid", "snapshot 含不支持的值类型", 422)


def sanitize_reason(value: Any) -> str:
    """Normalize a free-form reason and redact credential-shaped values."""

    return _redact_text(_clean(value, "reason", 512))


def _safe_reason(value: Any) -> str:
    return sanitize_reason(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


_APPROVAL_REQUIRED_CHECK_FRAGMENTS = {
    table: dict(fragments)
    for table, fragments in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_CHECK_FRAGMENTS.items()
}
_APPROVAL_REQUEST_CHECKS = _APPROVAL_REQUIRED_CHECK_FRAGMENTS.setdefault(
    "tenant_approval_requests", {}
)
_APPROVAL_REQUEST_CHECKS["ck_tenant_approval_requests_status"] = tuple(
    set(_APPROVAL_REQUEST_CHECKS.get("ck_tenant_approval_requests_status", ())) | {"executing"}
)
_APPROVAL_REQUEST_CHECKS["ck_tenant_approval_requests_executing_evidence"] = (
    "status <> 'executing'",
    "ticket_consumed_at is not null",
    "execution_ticket_hash is not null",
)

_STAGE18_APPROVAL_ACTION_CHECK_SQL = (
    "action_type IN ("
    "'catalog_upgrade','membership_bootstrap','dataset_acl_disable','member_role_change',"
    "'identity_provider_disable','audit_retention_execute','workspace_authorization_mode_change',"
    "'dataset_workspace_transfer')"
)
for _approval_table in ("tenant_approval_policies", "tenant_approval_requests"):
    _action_check = (
        "ck_tenant_approval_policies_action_type"
        if _approval_table == "tenant_approval_policies"
        else "ck_tenant_approval_requests_action_type"
    )
    _configured = _APPROVAL_REQUIRED_CHECK_FRAGMENTS[_approval_table].get(_action_check, ())
    if "dataset_workspace_transfer" not in _configured:
        _APPROVAL_REQUIRED_CHECK_FRAGMENTS[_approval_table][_action_check] = (
            *_configured,
            "dataset_workspace_transfer",
        )


def _ensure_0025(connection: Any) -> None:
    required_tables = {
        "accounts",
        "tenants",
        "tenant_members",
        "tenant_groups",
        "tenant_group_members",
        "tenant_audit_events",
        "tenant_control_mutation_requests",
        "tenant_approval_policies",
        "tenant_approval_policy_approvers",
        "tenant_approval_requests",
        "tenant_approval_decisions",
        "alembic_version",
    }
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if required_tables - tables:
            raise ApprovalMigrationRequired()
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if len(revisions) != 1 or revisions[0] not in _SUPPORTED_REVISIONS:
            raise ApprovalMigrationRequired()
        current_revision = revisions[0]
        for name, expected in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_COLUMNS.items():
            actual = {str(item["name"]) for item in inspector.get_columns(name)}
            if expected - actual:
                raise ApprovalMigrationRequired()
            actual_nullable = {
                str(item["name"]): bool(item.get("nullable"))
                for item in inspector.get_columns(name)
            }
            if any(
                actual_nullable.get(column, True)
                for column in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_NOT_NULL[name]
            ):
                raise ApprovalMigrationRequired()

        for name, expected in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_UNIQUES.items():
            actual = {
                item.get("name"): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(name)
            }
            if any(
                actual.get(constraint_name) != columns
                for constraint_name, columns in expected.items()
            ):
                raise ApprovalMigrationRequired()

        for name, expected in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_FOREIGN_KEYS.items():
            actual = {
                item.get("name"): (
                    tuple(item.get("constrained_columns") or ()),
                    item.get("referred_table"),
                    tuple(item.get("referred_columns") or ()),
                )
                for item in inspector.get_foreign_keys(name)
            }
            if any(
                actual.get(constraint_name) != contract
                for constraint_name, contract in expected.items()
            ):
                raise ApprovalMigrationRequired()

        for name, expected in _APPROVAL_REQUIRED_CHECK_FRAGMENTS.items():
            actual = {
                item.get("name"): _normalized_sql(item.get("sqltext"))
                for item in inspector.get_check_constraints(name)
            }
            for constraint_name, configured_fragments in expected.items():
                fragments = configured_fragments
                if current_revision not in {
                    "0027_enterprise_workspace_authorization",
                    "0028_enterprise_knowledge_base_registry",
                } and constraint_name in {
                    "ck_tenant_approval_policies_action_type",
                    "ck_tenant_approval_requests_action_type",
                }:
                    fragments = tuple(
                        fragment
                        for fragment in fragments
                        if fragment != "workspace_authorization_mode_change"
                    )
                sql = actual.get(constraint_name, "")
                expected_sql = (
                    _STAGE18_APPROVAL_ACTION_CHECK_SQL
                    if current_revision == "0028_enterprise_knowledge_base_registry"
                    and constraint_name
                    in {
                        "ck_tenant_approval_policies_action_type",
                        "ck_tenant_approval_requests_action_type",
                    }
                    else ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION.get(
                        current_revision, {}
                    )
                    .get(name, {})
                    .get(constraint_name)
                )
                if expected_sql is not None:
                    if not sql or _canonical_check_sql(sql) != _canonical_check_sql(expected_sql):
                        raise ApprovalMigrationRequired()
                    continue
                normalized_fragments = tuple(_normalized_sql(fragment) for fragment in fragments)
                if not sql or any(fragment not in sql for fragment in normalized_fragments):
                    raise ApprovalMigrationRequired()

        for name, expected in ENTERPRISE_APPROVAL_CONTROL_REQUIRED_INDEXES.items():
            actual = {
                item.get("name"): tuple(item.get("column_names") or ())
                for item in inspector.get_indexes(name)
            }
            if any(actual.get(index_name) != columns for index_name, columns in expected.items()):
                raise ApprovalMigrationRequired()
    except ApprovalMigrationRequired:
        raise
    except Exception as exc:
        raise ApprovalMigrationRequired() from exc


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    if str(session.get_bind().dialect.name) != "sqlite":
        with session.begin():
            yield
        return
    connection = session.connection()
    connection.exec_driver_sql("PRAGMA busy_timeout=30000")
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        session.rollback()
        raise
    else:
        session.commit()


@contextmanager
def tenant_authority_transaction(session: Session) -> Iterator[None]:
    """Run a tenant control-plane write transaction with the shared SQLite fence."""

    with _transaction(session):
        yield


def _actor(session: Session, tenant_id: str, actor_id: str) -> _Actor:
    members = _table(session, "tenant_members")
    accounts = _table(session, "accounts")
    tenants = _table(session, "tenants")
    row = (
        session.execute(
            select(
                members.c.role,
                members.c.status,
                accounts.c.name,
                accounts.c.email,
                tenants.c.status.label("tenant_status"),
            )
            .select_from(
                members.join(accounts, accounts.c.id == members.c.account_id).join(
                    tenants, tenants.c.id == members.c.tenant_id
                )
            )
            .where(members.c.tenant_id == tenant_id, members.c.account_id == actor_id)
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or str(_value(row, "status", "")).casefold() != "active"
        or str(_value(row, "tenant_status", "")).casefold() != "active"
    ):
        raise ApprovalForbidden("approval_forbidden", "当前身份不是该租户的 active member", 403)
    role = str(_value(row, "role", "")).casefold()
    if role not in _ROLES:
        raise ApprovalMigrationRequired()
    return _Actor(
        tenant_id, actor_id, role, str(_value(row, "name", "")), str(_value(row, "email", ""))
    )


def _require_manager(actor: _Actor) -> None:
    if actor.role not in {"owner", "admin"}:
        raise ApprovalForbidden(
            "approval_manager_required", "只有 owner/admin 可以管理审批规则", 403
        )


def _require_requester(actor: _Actor, requester_id: str) -> None:
    if actor.account_id != requester_id:
        raise ApprovalForbidden("approval_requester_required", "只有申请人可以取消该审批申请", 403)


def _audit(
    session: Session,
    *,
    actor: _Actor,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            actor_name_snapshot=actor.name[:128],
            actor_email_snapshot=actor.email[:256],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            target_account_id=None,
            before_snapshot=_redact_snapshot(dict(before)) if before is not None else None,
            after_snapshot=_redact_snapshot(dict(after)) if after is not None else None,
            request_id=str(request_id or "")[:128],
            request_ip=str(request_ip or "")[:64],
            occurred_at=now,
        )
    )
    session.flush()


def _mutation_context(engine: Any, tenant_id: str, actor_id: str, key: str):
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, key)
    return idempotency_key_lock(tenant_id, actor_id, digest), engine_serialization_lock(engine)


def _reserve(
    session: Session,
    *,
    actor: _Actor,
    key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
):
    reservation = reserve_tenant_mutation(
        session,
        tenant_id=actor.tenant_id,
        actor_id=actor.account_id,
        raw_idempotency_key=key,
        request_hash=request_hash,
        operation=operation,
        resource_type=resource_type,
    )
    if reservation.replay is not None:
        return reservation, ServiceResult(
            reservation.replay.response, reservation.replay.http_status
        )
    return reservation, None


def _encode_cursor(value: datetime, item_id: str) -> str:
    raw = json.dumps({"time": _iso(value), "id": item_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode((value + padding).encode()).decode())
        timestamp = _datetime(decoded["time"])
        item_id = _clean(decoded["id"], "cursor", 128)
        if timestamp is None:
            raise ValueError
        return timestamp, item_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApprovalValidation("approval_cursor_invalid", "分页 cursor 无效", 422) from exc


def _keyset_clause(
    session: Session,
    time_column: Any,
    id_column: Any,
    timestamp: datetime,
    item_id: str,
):
    dialect = str(session.get_bind().dialect.name)
    if dialect == "sqlite":
        normalized_time = func.strftime("%Y-%m-%d %H:%M:%f", time_column)
        cursor_time = (
            timestamp.strftime("%Y-%m-%d %H:%M:%S.") + f"{timestamp.microsecond // 1000:03d}"
        )
        return or_(
            normalized_time < cursor_time,
            and_(normalized_time == cursor_time, id_column < item_id),
        )
    if dialect in {"mysql", "mariadb"}:
        normalized_time = func.date_format(time_column, "%Y-%m-%d %H:%i:%s.%f")
        return or_(
            normalized_time < timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            and_(
                normalized_time == timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), id_column < item_id
            ),
        )
    return or_(
        time_column < timestamp,
        and_(time_column == timestamp, id_column < item_id),
    )


def _page(
    rows: list[Mapping[str, Any]], *, limit: int, time_field: str
) -> tuple[list[Mapping[str, Any]], str | None]:
    visible = rows[:limit]
    if len(rows) <= limit or not visible:
        return visible, None
    last = visible[-1]
    timestamp = _datetime(_value(last, time_field))
    if timestamp is None:
        return visible, None
    return visible, _encode_cursor(timestamp, str(_value(last, "id")))


def _approver_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "kind": str(_value(row, "approver_kind")),
        "ref": str(_value(row, "approver_ref")),
        "account_id": _value(row, "account_id"),
        "group_id": _value(row, "group_id"),
        "status": str(_value(row, "status")),
        "revision": int(_value(row, "revision", 1)),
    }


def _policy_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "name": str(_value(row, "name")),
        "action_type": str(_value(row, "action_type")),
        "resource_scope": _value(row, "resource_scope"),
        "active_scope_key": _value(row, "active_scope_key"),
        "status": str(_value(row, "status")),
        "required_approvals": int(_value(row, "required_approvals", 1)),
        "request_expiry_minutes": int(_value(row, "request_expiry_minutes", 15)),
        "revision": int(_value(row, "revision", 1)),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by")),
        "disabled_at": _iso(_value(row, "disabled_at")),
        "disabled_by": _value(row, "disabled_by"),
    }


def _request_status(row: Mapping[str, Any], now: datetime | None) -> str:
    status = str(_value(row, "status"))
    expires_at = _datetime(_value(row, "expires_at"))
    return (
        "expired"
        if status == "pending" and now is not None and expires_at is not None and expires_at <= now
        else status
    )


def _request_payload(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    snapshot = _redact_snapshot(_json_value(_value(row, "snapshot_json", {})))
    created_at = _iso(_value(row, "created_at"))
    return {
        "id": str(_value(row, "id")),
        "policy_id": str(_value(row, "policy_id")),
        "requester_id": str(_value(row, "requester_id")),
        "action_type": str(_value(row, "action_type")),
        "resource_type": str(_value(row, "resource_type")),
        "resource_id": str(_value(row, "resource_id")),
        "snapshot": snapshot,
        "snapshot_hash": str(_value(row, "payload_hash")),
        "reason": str(_value(row, "reason")),
        "status": _request_status(row, now),
        "required_approvals": int(_value(row, "required_approvals", 1)),
        "received_approvals": int(_value(row, "received_approvals", 0)),
        "created_at": created_at,
        "expires_at": _iso(_value(row, "expires_at")),
        "rejected_at": _iso(_value(row, "rejected_at")),
        "rejected_by": _value(row, "rejected_by"),
        "cancelled_at": _iso(_value(row, "cancelled_at")),
        "cancelled_by": _value(row, "cancelled_by"),
        "executed_at": _iso(_value(row, "executed_at")),
        "executed_by": _value(row, "executed_by"),
        "execution_failed_at": _iso(_value(row, "execution_failed_at")),
        "execution_failed_by": _value(row, "execution_failed_by"),
        "execution_error": _redact_text(str(_value(row, "execution_error")))
        if _value(row, "execution_error") is not None
        else None,
        "revision": int(_value(row, "revision", 1)),
        "updated_at": _iso(_value(row, "updated_at")),
    }


def _expire_pending_request(
    session: Session,
    request_table: Table,
    row: Mapping[str, Any],
    *,
    actor: _Actor,
    request_id: str,
    request_id_header: str,
    request_ip: str,
    now: datetime,
    reservation: Any,
) -> ServiceResult | None:
    if str(_value(row, "status")) != "pending":
        return None
    expires_at = _datetime(_value(row, "expires_at"))
    if expires_at is None or expires_at > now:
        return None
    current_revision = int(_value(row, "revision", 1))
    result = session.execute(
        update(request_table)
        .where(
            request_table.c.tenant_id == actor.tenant_id,
            request_table.c.id == request_id,
            request_table.c.revision == current_revision,
            request_table.c.status == "pending",
        )
        .values(
            status="expired",
            revision=current_revision + 1,
            updated_at=now,
            updated_by=actor.account_id,
        )
    )
    if result.rowcount != 1:
        raise ApprovalConflict(
            "approval_revision_conflict", "审批申请 revision 已变化，请刷新后重试", 409
        )
    expired_row = (
        session.execute(
            select(request_table).where(
                request_table.c.tenant_id == actor.tenant_id,
                request_table.c.id == request_id,
            )
        )
        .mappings()
        .one()
    )
    expired_payload = {
        "detail": {
            "code": "approval_request_expired",
            "message": "审批申请已过期，不能继续操作",
        },
        "request": _request_payload(expired_row, now=now),
        "execution": {"state": "expired"},
    }
    _audit(
        session,
        actor=actor,
        action="approval.request.expired",
        resource_type="tenant_approval_request",
        resource_id=request_id,
        before=_request_payload(row, now=None),
        after=expired_payload["request"],
        request_id=request_id_header,
        request_ip=request_ip,
        now=now,
    )
    complete_tenant_mutation(
        session,
        reservation,
        response_for_replay=expired_payload,
        http_status=409,
        resource_id=request_id,
    )
    return ServiceResult(expired_payload, 409)


def _decision_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "approver_id": str(_value(row, "approver_id")),
        "decision": str(_value(row, "decision")),
        "comment": str(_value(row, "comment", "")),
        "decided_at": _iso(_value(row, "decided_at")),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": _value(row, "created_by"),
        "revision": int(_value(row, "revision", 1)),
    }


def _active_scope_key(action_type: str, resource_scope: str | None) -> str:
    configured_scope = resource_scope or "global"
    raw = f"{action_type}|{configured_scope}"
    if len(raw) <= 512:
        return raw
    digest = hashlib.sha256()
    digest.update(b"rag4c:enterprise-approval-active-scope:v1\x00")
    digest.update(raw.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _normalize_approvers(
    session: Session, tenant_id: str, raw: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw or len(raw) > 50:
        raise ApprovalValidation(
            "approval_approvers_invalid", "approvers 至少包含一条且不超过 50 条", 422
        )
    members = _table(session, "tenant_members")
    groups = _table(session, "tenant_groups")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ApprovalValidation("approval_approvers_invalid", "approver 必须是对象", 422)
        kind = _clean(item.get("kind", item.get("approver_kind")), "approver.kind", 16).casefold()
        reference = _clean(item.get("ref", item.get("approver_ref")), "approver.ref", 128)
        if kind not in _APPROVER_KINDS or (kind, reference.casefold()) in seen:
            raise ApprovalValidation("approval_approvers_invalid", "approver 类型或重复项无效", 422)
        seen.add((kind, reference.casefold()))
        if kind == "role":
            reference = reference.casefold()
            if reference not in _ROLES:
                raise ApprovalValidation("approval_approvers_invalid", "approver role 无效", 422)
        elif kind == "account":
            exists = session.scalar(
                select(members.c.account_id).where(
                    members.c.tenant_id == tenant_id,
                    members.c.account_id == reference,
                    members.c.status == "active",
                )
            )
            if exists is None:
                raise ApprovalValidation(
                    "approval_approver_invalid",
                    "account approver 必须是当前租户 active member",
                    422,
                )
        else:
            exists = session.scalar(
                select(groups.c.id).where(
                    groups.c.tenant_id == tenant_id,
                    groups.c.id == reference,
                    groups.c.status == "active",
                )
            )
            if exists is None:
                raise ApprovalValidation(
                    "approval_approver_invalid", "group approver 必须是当前租户 active group", 422
                )
        result.append(
            {
                "kind": kind,
                "ref": reference,
                "account_id": reference if kind == "account" else None,
                "group_id": reference if kind == "group" else None,
            }
        )
    return result


def _scope_matches(scope: Any, resource_type: str, resource_id: str) -> bool:
    if scope is None or str(scope).strip() == "":
        return True
    expected = f"{resource_type}:{resource_id}"
    configured = str(scope).strip()
    if configured in {
        "global",
        "*",
        "wildcard",
        f"{resource_type}:global",
        f"{resource_type}:wildcard",
        f"{resource_type}:wildcard:global",
        f"{resource_type}/*",
        f"{resource_type}/wildcard/global",
    }:
        return True
    if configured.endswith("*"):
        return expected.startswith(configured[:-1])
    return configured in {expected, resource_id}


def _scope_priority(scope: Any, resource_type: str, resource_id: str) -> int:
    """Prefer the narrowest matching policy over wildcard/global rules."""

    configured = str(scope or "").strip()
    expected = f"{resource_type}:{resource_id}"
    if configured == expected:
        return 0
    if configured == resource_id:
        return 1
    if configured.endswith("*") and configured not in {"*", "global*"}:
        return 2
    return 3


def _lock_tenant_approval_authority(session: Session, tenant_id: str) -> None:
    tenant = session.execute(
        select(Tenant.id).where(Tenant.id == tenant_id).with_for_update()
    ).scalar_one_or_none()
    if tenant is None:
        raise ApprovalNotFound("企业租户不存在")


def resolve_active_approval_policy_in_session(
    session: Session,
    *,
    tenant_id: str,
    action_type: str,
    resource_type: str,
    resource_id: str,
    lock_for_update: bool = False,
    tenant_already_locked: bool = False,
) -> dict[str, Any] | None:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_action = _clean(action_type, "action_type", 64)
    clean_resource_type = _clean(resource_type, "resource_type", 64)
    clean_resource_id = _clean(resource_id, "resource_id", 256)
    if clean_action not in _ACTION_TYPES:
        raise ApprovalValidation("approval_action_invalid", "action_type 不在允许列表", 422)
    connection = session.connection()
    try:
        available_tables = set(inspect(connection).get_table_names())
        # ORM-only legacy test/catalog shapes can contain the mapped approval
        # tables without an Alembic stamp.  They are not an active 0025 control
        # plane and must preserve the pre-approval member mutation contract.
        if {"tenant_approval_policies", "alembic_version"} - available_tables:
            return None
    except Exception as exc:
        raise ApprovalMigrationRequired() from exc
    _ensure_0025(connection)
    if lock_for_update and not tenant_already_locked:
        _lock_tenant_approval_authority(session, clean_tenant)
    policies = _table(session, "tenant_approval_policies")
    statement = select(policies).where(
        policies.c.tenant_id == clean_tenant,
        policies.c.action_type == clean_action,
        policies.c.status == "active",
    )
    if lock_for_update:
        statement = statement.with_for_update()
    rows = list(session.execute(statement).mappings())
    matches = [
        row
        for row in rows
        if _scope_matches(row.get("resource_scope"), clean_resource_type, clean_resource_id)
    ]
    if not matches:
        return None
    selected = min(
        matches,
        key=lambda row: (
            _scope_priority(row.get("resource_scope"), clean_resource_type, clean_resource_id),
            -int(_value(row, "revision", 1)),
            str(_value(row, "id")),
        ),
    )
    payload = _policy_payload(selected)
    payload["matched_scope"] = _value(selected, "resource_scope")
    return payload


def resolve_active_approval_policy(
    engine: Any,
    *,
    tenant_id: str,
    action_type: str,
    resource_type: str,
    resource_id: str,
) -> dict[str, Any] | None:
    """Resolve one active policy without exposing approval-control internals."""
    try:
        if "tenant_approval_policies" not in set(inspect(engine).get_table_names()):
            return None
    except Exception as exc:
        raise ApprovalMigrationRequired() from exc
    with Session(engine) as session:
        return resolve_active_approval_policy_in_session(
            session,
            tenant_id=tenant_id,
            action_type=action_type,
            resource_type=resource_type,
            resource_id=resource_id,
        )


# Public aliases make the policy-resolution contract discoverable to routers
# and to future consumers without coupling them to the private helper names.
find_active_approval_policy = resolve_active_approval_policy
get_active_approval_policy = resolve_active_approval_policy


def _active_approvers(session: Session, tenant_id: str, policy_id: str) -> list[Mapping[str, Any]]:
    table = _table(session, "tenant_approval_policy_approvers")
    return list(
        session.execute(
            select(table).where(
                table.c.tenant_id == tenant_id,
                table.c.policy_id == policy_id,
                table.c.status == "active",
            )
        ).mappings()
    )


def _eligible(session: Session, *, tenant_id: str, policy_id: str, account_id: str) -> bool:
    members = _table(session, "tenant_members")
    member = (
        session.execute(
            select(members.c.role, members.c.status).where(
                members.c.tenant_id == tenant_id, members.c.account_id == account_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if member is None or str(_value(member, "status", "")).casefold() != "active":
        return False
    role = str(_value(member, "role", "")).casefold()
    groups = _table(session, "tenant_groups")
    group_members = _table(session, "tenant_group_members")
    for row in _active_approvers(session, tenant_id, policy_id):
        kind = str(_value(row, "approver_kind"))
        reference = str(_value(row, "approver_ref"))
        if kind == "account" and reference == account_id:
            return True
        if kind == "role" and reference.casefold() == role:
            return True
        if kind == "group":
            exists = session.scalar(
                select(group_members.c.id)
                .select_from(
                    group_members.join(
                        groups,
                        (groups.c.id == group_members.c.group_id)
                        & (groups.c.tenant_id == group_members.c.tenant_id),
                    )
                )
                .where(
                    group_members.c.tenant_id == tenant_id,
                    group_members.c.group_id == reference,
                    group_members.c.account_id == account_id,
                    group_members.c.status == "active",
                    groups.c.status == "active",
                )
            )
            if exists is not None:
                return True
    return False


def _pending_for_me_clause(
    session: Session,
    request_table: Table,
    *,
    actor: _Actor,
    now: datetime | None,
):
    approvers = _table(session, "tenant_approval_policy_approvers")
    groups = _table(session, "tenant_groups")
    group_members = _table(session, "tenant_group_members")
    group_membership = exists(
        select(group_members.c.id)
        .select_from(
            group_members.join(
                groups,
                (groups.c.tenant_id == group_members.c.tenant_id)
                & (groups.c.id == group_members.c.group_id),
            )
        )
        .where(
            group_members.c.tenant_id == actor.tenant_id,
            group_members.c.account_id == actor.account_id,
            group_members.c.group_id == approvers.c.approver_ref,
            group_members.c.status == "active",
            groups.c.status == "active",
        )
    )
    eligible = exists(
        select(approvers.c.id)
        .where(
            approvers.c.tenant_id == actor.tenant_id,
            approvers.c.policy_id == request_table.c.policy_id,
            approvers.c.status == "active",
            or_(
                and_(
                    approvers.c.approver_kind == "account",
                    approvers.c.approver_ref == actor.account_id,
                ),
                and_(
                    approvers.c.approver_kind == "role",
                    approvers.c.approver_ref == actor.role,
                ),
                and_(approvers.c.approver_kind == "group", group_membership),
            ),
        )
        .correlate(request_table)
    )
    conditions = [request_table.c.status == "pending", eligible]
    if now is not None:
        conditions.append(request_table.c.expires_at > now)
    return and_(*conditions)


def list_approval_policies(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ServiceResult:
    limit = _positive_int(limit, "limit", 1, 200)
    clean_status = _clean(status, "status", 16).casefold() if status is not None else None
    if clean_status is not None and clean_status not in {"active", "disabled"}:
        raise ApprovalValidation("approval_filter_invalid", "status 无效", 422)
    with Session(engine) as session:
        _ensure_0025(session.connection())
        _actor(session, tenant_id, actor_id)
        table = _table(session, "tenant_approval_policies")
        statement = select(table).where(table.c.tenant_id == tenant_id)
        if clean_status is not None:
            statement = statement.where(table.c.status == clean_status)
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            timestamp, item_id = decoded
            statement = statement.where(
                _keyset_clause(session, table.c.created_at, table.c.id, timestamp, item_id)
            )
        rows = list(
            session.execute(
                statement.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
            ).mappings()
        )
        visible, next_cursor = _page(rows, limit=limit, time_field="created_at")
        return ServiceResult(
            {
                "items": [_policy_payload(row) for row in visible],
                "next_cursor": next_cursor,
                "count": len(visible),
            }
        )


def get_approval_policy(
    engine: Any, *, tenant_id: str, actor_id: str, policy_id: str
) -> ServiceResult:
    clean_id = _clean(policy_id, "policy_id", 64)
    with Session(engine) as session:
        _ensure_0025(session.connection())
        _actor(session, tenant_id, actor_id)
        policies = _table(session, "tenant_approval_policies")
        row = (
            session.execute(
                select(policies).where(policies.c.tenant_id == tenant_id, policies.c.id == clean_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApprovalNotFound("审批规则不存在")
        approvers = _active_approvers(session, tenant_id, clean_id)
        return ServiceResult(
            {
                "policy": _policy_payload(row),
                "approvers": [_approver_payload(item) for item in approvers],
            }
        )


def list_approval_requests(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    status: str | None = None,
    action_type: str | None = None,
    mine: bool = False,
    pending_for_me: bool = False,
    cursor: str | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> ServiceResult:
    limit = _positive_int(limit, "limit", 1, 200)
    clean_status = _clean(status, "status", 32).casefold() if status is not None else None
    if clean_status is not None and clean_status not in _STATUSES:
        raise ApprovalValidation("approval_filter_invalid", "status 无效", 422)
    clean_action = _clean(action_type, "action_type", 64) if action_type is not None else None
    with Session(engine) as session:
        _ensure_0025(session.connection())
        actor = _actor(session, tenant_id, actor_id)
        table = _table(session, "tenant_approval_requests")
        statement = select(table).where(table.c.tenant_id == tenant_id)
        if clean_status is not None:
            if clean_status == "expired" and now is not None:
                statement = statement.where(
                    or_(
                        table.c.status == "expired",
                        and_(table.c.status == "pending", table.c.expires_at <= now),
                    )
                )
            elif clean_status == "pending" and now is not None:
                statement = statement.where(table.c.status == "pending", table.c.expires_at > now)
            else:
                statement = statement.where(table.c.status == clean_status)
        if clean_action is not None:
            statement = statement.where(table.c.action_type == clean_action)
        if mine:
            statement = statement.where(table.c.requester_id == actor_id)
        if pending_for_me:
            statement = statement.where(
                _pending_for_me_clause(session, table, actor=actor, now=now)
            )
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            timestamp, item_id = decoded
            statement = statement.where(
                _keyset_clause(session, table.c.created_at, table.c.id, timestamp, item_id)
            )
        rows = list(
            session.execute(
                statement.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
            ).mappings()
        )
        visible, next_cursor = _page(rows, limit=limit, time_field="created_at")
        return ServiceResult(
            {
                "items": [_request_payload(row, now=now) for row in visible],
                "next_cursor": next_cursor,
                "count": len(visible),
            }
        )


def get_approval_request(
    engine: Any, *, tenant_id: str, actor_id: str, request_id: str, now: datetime | None = None
) -> ServiceResult:
    clean_id = _clean(request_id, "request_id", 64)
    with Session(engine) as session:
        _ensure_0025(session.connection())
        _actor(session, tenant_id, actor_id)
        requests = _table(session, "tenant_approval_requests")
        row = (
            session.execute(
                select(requests).where(requests.c.tenant_id == tenant_id, requests.c.id == clean_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApprovalNotFound("审批申请不存在")
        policies = _table(session, "tenant_approval_policies")
        policy = (
            session.execute(
                select(policies).where(
                    policies.c.tenant_id == tenant_id, policies.c.id == row["policy_id"]
                )
            )
            .mappings()
            .one_or_none()
        )
        approvers = _active_approvers(session, tenant_id, str(row["policy_id"]))
        decisions = _table(session, "tenant_approval_decisions")
        decision_rows = list(
            session.execute(
                select(decisions)
                .where(decisions.c.tenant_id == tenant_id, decisions.c.request_id == clean_id)
                .order_by(decisions.c.decided_at.asc(), decisions.c.id.asc())
            ).mappings()
        )
        return ServiceResult(
            {
                "request": _request_payload(row, now=now),
                "policy": _policy_payload(policy) if policy else None,
                "approvers": [_approver_payload(item) for item in approvers],
                "decisions": [_decision_payload(item) for item in decision_rows],
                "execution": {
                    "state": "execution_adapter_not_connected"
                    if row["status"] == "approved"
                    else "awaiting_approval"
                },
            }
        )


def _validate_policy_inputs(
    name: Any, action_type: Any, scope: Any, required: Any, expiry: Any
) -> tuple[str, str, str | None, int, int]:
    clean_name = _clean(name, "name", 128)
    clean_action = _clean(action_type, "action_type", 64)
    if clean_action not in _ACTION_TYPES:
        raise ApprovalValidation("approval_action_invalid", "action_type 不在允许列表", 422)
    clean_scope = (
        _clean(scope, "resource_scope", 512, allow_empty=True) if scope is not None else None
    )
    clean_scope = clean_scope or None
    return (
        clean_name,
        clean_action,
        clean_scope,
        _positive_int(required, "required_approvals", 1, 5),
        _positive_int(expiry, "request_expiry_minutes", 15, 10080),
    )


def create_approval_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    name: str,
    action_type: str,
    resource_scope: str | None,
    required_approvals: int,
    request_expiry_minutes: int,
    approvers: Sequence[Mapping[str, Any]],
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_name, clean_action, clean_scope, threshold, expiry = _validate_policy_inputs(
        name, action_type, resource_scope, required_approvals, request_expiry_minutes
    )
    safe_reason = _safe_reason(reason)
    request_hash = tenant_request_hash(
        operation="approval.policy.create",
        path_identity={"tenant_id": tenant_id},
        body={
            "name": clean_name,
            "action_type": clean_action,
            "resource_scope": clean_scope,
            "required_approvals": threshold,
            "request_expiry_minutes": expiry,
            "approvers": list(approvers),
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_manager(actor)
            _lock_tenant_approval_authority(session, tenant_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="approval.policy.create",
                resource_type="tenant_approval_policy",
            )
            if replay is not None:
                return replay
            policy_table = _table(session, "tenant_approval_policies")
            active_scope_key = _active_scope_key(clean_action, clean_scope)
            duplicate = session.scalar(
                select(policy_table.c.id).where(
                    policy_table.c.tenant_id == tenant_id,
                    policy_table.c.active_scope_key == active_scope_key,
                    policy_table.c.status == "active",
                )
            )
            if duplicate is not None:
                raise ApprovalConflict(
                    "approval_policy_active_conflict",
                    "同一租户 action/resource scope 已有 active 审批规则",
                    409,
                )
            normalized = _normalize_approvers(session, tenant_id, approvers)
            if len(normalized) < threshold:
                raise ApprovalValidation(
                    "approval_approvers_insufficient",
                    "approver 数量不能少于 required_approvals",
                    422,
                )
            policy_id = f"approval-policy-{uuid.uuid4().hex}"
            values = {
                "id": policy_id,
                "tenant_id": tenant_id,
                "name": clean_name,
                "action_type": clean_action,
                "resource_scope": clean_scope,
                "active_scope_key": active_scope_key,
                "status": "active",
                "required_approvals": threshold,
                "request_expiry_minutes": expiry,
                "revision": 1,
                "created_at": now,
                "created_by": actor_id,
                "updated_at": now,
                "updated_by": actor_id,
                "disabled_at": None,
                "disabled_by": None,
            }
            session.execute(policy_table.insert().values(**values))
            approver_table = _table(session, "tenant_approval_policy_approvers")
            for item in normalized:
                session.execute(
                    approver_table.insert().values(
                        id=f"approval-approver-{uuid.uuid4().hex}",
                        tenant_id=tenant_id,
                        policy_id=policy_id,
                        approver_kind=item["kind"],
                        approver_ref=item["ref"],
                        account_id=item["account_id"],
                        group_id=item["group_id"],
                        status="active",
                        revision=1,
                        created_at=now,
                        created_by=actor_id,
                        updated_at=now,
                        updated_by=actor_id,
                    )
                )
            row = (
                session.execute(
                    select(policy_table).where(
                        policy_table.c.tenant_id == tenant_id, policy_table.c.id == policy_id
                    )
                )
                .mappings()
                .one()
            )
            payload = {
                "policy": _policy_payload(row),
                "approvers": [
                    _approver_payload(item)
                    for item in _active_approvers(session, tenant_id, policy_id)
                ],
            }
            _audit(
                session,
                actor=actor,
                action="approval.policy.created",
                resource_type="tenant_approval_policy",
                resource_id=policy_id,
                before=None,
                after={**payload, "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=201,
                resource_id=policy_id,
            )
            return ServiceResult(payload, 201)


def update_approval_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    policy_id: str,
    expected_revision: int,
    name: str | None,
    resource_scope: str | None,
    required_approvals: int | None,
    request_expiry_minutes: int | None,
    approvers: Sequence[Mapping[str, Any]] | None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_id = _clean(policy_id, "policy_id", 64)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    clean_name = _clean(name, "name", 128) if name is not None else None
    clean_scope = (
        _clean(resource_scope, "resource_scope", 512, allow_empty=True)
        if resource_scope is not None
        else None
    )
    threshold = (
        _positive_int(required_approvals, "required_approvals", 1, 5)
        if required_approvals is not None
        else None
    )
    expiry = (
        _positive_int(request_expiry_minutes, "request_expiry_minutes", 15, 10080)
        if request_expiry_minutes is not None
        else None
    )
    if (
        clean_name is None
        and resource_scope is None
        and threshold is None
        and expiry is None
        and approvers is None
    ):
        raise ApprovalValidation("approval_request_invalid", "至少提供一个需要更新的字段", 422)
    safe_reason = _safe_reason(reason)
    request_hash = tenant_request_hash(
        operation="approval.policy.update",
        path_identity={"tenant_id": tenant_id, "policy_id": clean_id},
        body={
            "revision": revision,
            "name": clean_name,
            "resource_scope": clean_scope,
            "required_approvals": threshold,
            "request_expiry_minutes": expiry,
            "approvers": list(approvers) if approvers is not None else None,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_manager(actor)
            _lock_tenant_approval_authority(session, tenant_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="approval.policy.update",
                resource_type="tenant_approval_policy",
            )
            if replay is not None:
                return replay
            policies = _table(session, "tenant_approval_policies")
            current = (
                session.execute(
                    select(policies)
                    .where(policies.c.tenant_id == tenant_id, policies.c.id == clean_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise ApprovalNotFound("审批规则不存在")
            if int(current["revision"]) != revision:
                raise ApprovalConflict(
                    "approval_revision_conflict", "审批规则 revision 已变化，请刷新后重试", 409
                )
            if str(current["status"]) != "active":
                raise ApprovalConflict("approval_policy_terminal", "disabled 审批规则不能更新", 409)
            effective_scope = (
                (clean_scope or None) if resource_scope is not None else current["resource_scope"]
            )
            active_scope_key = _active_scope_key(str(current["action_type"]), effective_scope)
            duplicate = session.scalar(
                select(policies.c.id).where(
                    policies.c.tenant_id == tenant_id,
                    policies.c.active_scope_key == active_scope_key,
                    policies.c.status == "active",
                    policies.c.id != clean_id,
                )
            )
            if duplicate is not None:
                raise ApprovalConflict(
                    "approval_policy_active_conflict",
                    "同一租户 action/resource scope 已有 active 审批规则",
                    409,
                )
            new_threshold = (
                threshold if threshold is not None else int(current["required_approvals"])
            )
            active_rows = _active_approvers(session, tenant_id, clean_id)
            normalized = (
                _normalize_approvers(session, tenant_id, approvers)
                if approvers is not None
                else [
                    {"kind": str(row["approver_kind"]), "ref": str(row["approver_ref"])}
                    for row in active_rows
                ]
            )
            if len(normalized) < new_threshold:
                raise ApprovalValidation(
                    "approval_approvers_insufficient",
                    "approver 数量不能少于 required_approvals",
                    422,
                )
            values = {
                "name": clean_name if clean_name is not None else current["name"],
                "resource_scope": effective_scope,
                "active_scope_key": active_scope_key,
                "required_approvals": new_threshold,
                "request_expiry_minutes": expiry
                if expiry is not None
                else current["request_expiry_minutes"],
                "revision": revision + 1,
                "updated_at": now,
                "updated_by": actor_id,
            }
            session.execute(
                update(policies)
                .where(
                    policies.c.tenant_id == tenant_id,
                    policies.c.id == clean_id,
                    policies.c.revision == revision,
                )
                .values(**values)
            )
            approver_table = _table(session, "tenant_approval_policy_approvers")
            desired = {(item["kind"], item["ref"]): item for item in normalized}
            for existing in session.execute(
                select(approver_table).where(
                    approver_table.c.tenant_id == tenant_id, approver_table.c.policy_id == clean_id
                )
            ).mappings():
                key = (str(existing["approver_kind"]), str(existing["approver_ref"]))
                should_be_active = key in desired
                session.execute(
                    update(approver_table)
                    .where(approver_table.c.id == existing["id"])
                    .values(
                        status="active" if should_be_active else "disabled",
                        revision=int(existing["revision"]) + 1,
                        updated_at=now,
                        updated_by=actor_id,
                    )
                )
            existing_keys = {
                (str(row["approver_kind"]), str(row["approver_ref"]))
                for row in session.execute(
                    select(approver_table).where(
                        approver_table.c.tenant_id == tenant_id,
                        approver_table.c.policy_id == clean_id,
                    )
                ).mappings()
            }
            for item in normalized:
                if (item["kind"], item["ref"]) not in existing_keys:
                    session.execute(
                        approver_table.insert().values(
                            id=f"approval-approver-{uuid.uuid4().hex}",
                            tenant_id=tenant_id,
                            policy_id=clean_id,
                            approver_kind=item["kind"],
                            approver_ref=item["ref"],
                            account_id=item["account_id"],
                            group_id=item["group_id"],
                            status="active",
                            revision=1,
                            created_at=now,
                            created_by=actor_id,
                            updated_at=now,
                            updated_by=actor_id,
                        )
                    )
            row = (
                session.execute(
                    select(policies).where(
                        policies.c.tenant_id == tenant_id, policies.c.id == clean_id
                    )
                )
                .mappings()
                .one()
            )
            payload = {
                "policy": _policy_payload(row),
                "approvers": [
                    _approver_payload(item)
                    for item in _active_approvers(session, tenant_id, clean_id)
                ],
            }
            _audit(
                session,
                actor=actor,
                action="approval.policy.updated",
                resource_type="tenant_approval_policy",
                resource_id=clean_id,
                before=_policy_payload(current),
                after={**payload, "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=clean_id,
            )
            return ServiceResult(payload)


def disable_approval_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    policy_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_id = _clean(policy_id, "policy_id", 64)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    safe_reason = _safe_reason(reason)
    request_hash = tenant_request_hash(
        operation="approval.policy.disable",
        path_identity={"tenant_id": tenant_id, "policy_id": clean_id},
        body={"revision": revision, "reason": safe_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_manager(actor)
            _lock_tenant_approval_authority(session, tenant_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="approval.policy.disable",
                resource_type="tenant_approval_policy",
            )
            if replay is not None:
                return replay
            policies = _table(session, "tenant_approval_policies")
            current = (
                session.execute(
                    select(policies)
                    .where(policies.c.tenant_id == tenant_id, policies.c.id == clean_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise ApprovalNotFound("审批规则不存在")
            if int(current["revision"]) != revision:
                raise ApprovalConflict(
                    "approval_revision_conflict", "审批规则 revision 已变化，请刷新后重试", 409
                )
            if str(current["status"]) != "active":
                raise ApprovalConflict("approval_policy_terminal", "审批规则已经 disabled", 409)
            session.execute(
                update(policies)
                .where(
                    policies.c.tenant_id == tenant_id,
                    policies.c.id == clean_id,
                    policies.c.revision == revision,
                )
                .values(
                    status="disabled",
                    active_scope_key=None,
                    revision=revision + 1,
                    disabled_at=now,
                    disabled_by=actor_id,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            approver_table = _table(session, "tenant_approval_policy_approvers")
            session.execute(
                update(approver_table)
                .where(
                    approver_table.c.tenant_id == tenant_id,
                    approver_table.c.policy_id == clean_id,
                    approver_table.c.status == "active",
                )
                .values(
                    status="disabled",
                    revision=approver_table.c.revision + 1,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            row = (
                session.execute(
                    select(policies).where(
                        policies.c.tenant_id == tenant_id, policies.c.id == clean_id
                    )
                )
                .mappings()
                .one()
            )
            payload = {
                "policy": _policy_payload(row),
                "approvers": [
                    _approver_payload(item)
                    for item in _active_approvers(session, tenant_id, clean_id)
                ],
            }
            _audit(
                session,
                actor=actor,
                action="approval.policy.disabled",
                resource_type="tenant_approval_policy",
                resource_id=clean_id,
                before=_policy_payload(current),
                after={**payload, "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=clean_id,
            )
            return ServiceResult(payload)


def _validate_document_purge_recovery_authority(
    session: Session,
    *,
    tenant_id: str,
    actor: _Actor,
    resource_type: str,
    resource_id: str,
    snapshot: Mapping[str, Any],
    now: datetime,
) -> None:
    _require_manager(actor)
    if resource_type != "document_recycle_entry":
        raise ApprovalValidation(
            "approval_resource_type_invalid",
            "document_purge requires document_recycle_entry resource",
            422,
        )
    required = {
        "recycle_entry_id",
        "document_id",
        "dataset_id",
        "entry_revision",
        "purge_eligible_at",
        "retention_days_snapshot",
        "legal_hold_count",
    }
    if set(snapshot) != required:
        raise ApprovalValidation(
            "approval_snapshot_invalid",
            "document_purge snapshot schema is invalid",
            422,
        )
    entry_id = _clean(snapshot.get("recycle_entry_id"), "recycle_entry_id", 64)
    if entry_id != resource_id:
        raise ApprovalConflict(
            "approval_resource_scope_mismatch",
            "document_purge resource does not match recycle entry",
            409,
        )
    entry = session.scalar(
        select(TenantDocumentRecycleEntry).where(
            TenantDocumentRecycleEntry.tenant_id == tenant_id,
            TenantDocumentRecycleEntry.id == entry_id,
        )
    )
    if entry is None:
        raise ApprovalNotFound("recycle entry does not exist")
    if (
        str(entry.status) != "recycled"
        or int(entry.revision)
        != _positive_int(snapshot.get("entry_revision"), "entry_revision", 1, 2_147_483_647)
        or str(entry.document_id) != _clean(snapshot.get("document_id"), "document_id", 64)
        or str(entry.dataset_id) != _clean(snapshot.get("dataset_id"), "dataset_id", 64)
        or int(entry.retention_days_snapshot)
        != _positive_int(
            snapshot.get("retention_days_snapshot"), "retention_days_snapshot", 1, 3650
        )
    ):
        raise ApprovalConflict(
            "approval_snapshot_stale",
            "document_purge recovery snapshot is stale",
            409,
        )
    if type(snapshot.get("legal_hold_count")) is not int or snapshot["legal_hold_count"] != 0:
        raise ApprovalConflict(
            "approval_legal_hold_active",
            "document_purge requires zero legal holds",
            409,
        )
    active_holds = int(
        session.scalar(
            select(func.count())
            .select_from(TenantDocumentLegalHold)
            .where(
                TenantDocumentLegalHold.tenant_id == tenant_id,
                TenantDocumentLegalHold.recycle_entry_id == entry_id,
                TenantDocumentLegalHold.status == "active",
            )
        )
        or 0
    )
    if active_holds:
        raise ApprovalConflict(
            "approval_legal_hold_active",
            "document_purge is blocked by legal hold",
            409,
        )
    purge_eligible_at = entry.purge_eligible_at
    if purge_eligible_at is None or now < purge_eligible_at:
        raise ApprovalConflict(
            "approval_retention_not_elapsed",
            "document_purge retention period has not elapsed",
            409,
        )
    supplied_eligible = str(snapshot.get("purge_eligible_at") or "")
    persisted_eligible = purge_eligible_at.replace(tzinfo=None).isoformat(timespec="microseconds")
    if supplied_eligible.replace("Z", "").replace("+00:00", "") != persisted_eligible:
        raise ApprovalConflict(
            "approval_snapshot_stale",
            "document_purge eligibility snapshot is stale",
            409,
        )


def _validate_document_purge_decision_authority(
    session: Session, *, tenant_id: str, approval_request_id: str
) -> None:
    purge = session.scalar(
        select(TenantDocumentPurgeRequest).where(
            TenantDocumentPurgeRequest.tenant_id == tenant_id,
            TenantDocumentPurgeRequest.approval_request_id == approval_request_id,
        )
    )
    if purge is None or str(purge.status) != "pending_approval":
        raise ApprovalConflict(
            "approval_recovery_authority_stale",
            "document_purge request is not bound to active Recovery authority",
            409,
        )
    entry = session.scalar(
        select(TenantDocumentRecycleEntry).where(
            TenantDocumentRecycleEntry.tenant_id == tenant_id,
            TenantDocumentRecycleEntry.id == purge.recycle_entry_id,
        )
    )
    if (
        entry is None
        or str(entry.status) != "purge_requested"
        or int(entry.revision) != int(purge.expected_entry_revision) + 1
        or str(entry.document_id) != str(purge.document_id)
        or str(entry.dataset_id) != str(purge.dataset_id)
    ):
        raise ApprovalConflict(
            "approval_recovery_authority_stale",
            "document_purge Recovery Entry changed after request creation",
            409,
        )
    holds = int(
        session.scalar(
            select(func.count())
            .select_from(TenantDocumentLegalHold)
            .where(
                TenantDocumentLegalHold.tenant_id == tenant_id,
                TenantDocumentLegalHold.recycle_entry_id == entry.id,
                TenantDocumentLegalHold.status == "active",
            )
        )
        or 0
    )
    if holds:
        raise ApprovalConflict(
            "approval_legal_hold_active",
            "document_purge became blocked by legal hold",
            409,
        )


def create_approval_request(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    policy_id: str,
    resource_type: str,
    resource_id: str,
    snapshot: Mapping[str, Any],
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_policy_id = _clean(policy_id, "policy_id", 64)
    clean_resource_type = _clean(resource_type, "resource_type", 64)
    clean_resource_id = _clean(resource_id, "resource_id", 256)
    if not isinstance(snapshot, Mapping):
        raise ApprovalValidation("approval_snapshot_invalid", "snapshot 必须是对象", 422)
    safe_snapshot = _redact_snapshot(snapshot)
    try:
        json.dumps(safe_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ApprovalValidation("approval_snapshot_invalid", "snapshot 不能序列化", 422) from exc
    safe_reason = _safe_reason(reason)
    snapshot_hash = hashlib.sha256(
        json.dumps(
            safe_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    request_hash = tenant_request_hash(
        operation="approval.request.create",
        path_identity={
            "tenant_id": tenant_id,
            "policy_id": clean_policy_id,
            "resource_type": clean_resource_type,
            "resource_id": clean_resource_id,
        },
        body={"snapshot": safe_snapshot, "reason": safe_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="approval.request.create",
                resource_type="tenant_approval_request",
            )
            if replay is not None:
                return replay
            policies = _table(session, "tenant_approval_policies")
            policy = (
                session.execute(
                    select(policies)
                    .where(policies.c.tenant_id == tenant_id, policies.c.id == clean_policy_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if policy is None:
                raise ApprovalNotFound("审批规则不存在")
            if str(policy["status"]) != "active":
                raise ApprovalConflict(
                    "approval_policy_disabled", "审批规则已 disabled，不能提交申请", 409
                )
            if (
                str(policy["action_type"]) == "dataset_workspace_transfer"
                and clean_resource_type != "knowledge_base"
            ):
                raise ApprovalValidation(
                    "approval_resource_type_invalid",
                    "dataset_workspace_transfer 只允许 knowledge_base resource",
                    422,
                )
            if not _scope_matches(policy["resource_scope"], clean_resource_type, clean_resource_id):
                raise ApprovalConflict(
                    "approval_resource_scope_mismatch", "申请资源不在审批规则 scope 内", 409
                )
            if str(policy["action_type"]) == "document_purge":
                _validate_document_purge_recovery_authority(
                    session,
                    tenant_id=tenant_id,
                    actor=actor,
                    resource_type=clean_resource_type,
                    resource_id=clean_resource_id,
                    snapshot=safe_snapshot,
                    now=now,
                )
            request_table = _table(session, "tenant_approval_requests")
            approval_id = f"approval-request-{uuid.uuid4().hex}"
            expires_at = now + timedelta(minutes=int(policy["request_expiry_minutes"]))
            session.execute(
                request_table.insert().values(
                    id=approval_id,
                    tenant_id=tenant_id,
                    policy_id=clean_policy_id,
                    requester_id=actor_id,
                    action_type=str(policy["action_type"]),
                    resource_type=clean_resource_type,
                    resource_id=clean_resource_id,
                    snapshot_json=safe_snapshot,
                    payload_hash=snapshot_hash,
                    reason=safe_reason,
                    status="pending",
                    required_approvals=int(policy["required_approvals"]),
                    received_approvals=0,
                    idempotency_key=idempotency_key,
                    execution_ticket_hash=None,
                    ticket_issued_at=None,
                    ticket_consumed_at=None,
                    expires_at=expires_at,
                    rejected_at=None,
                    rejected_by=None,
                    rejection_comment=None,
                    cancelled_at=None,
                    cancelled_by=None,
                    executed_at=None,
                    executed_by=None,
                    execution_failed_at=None,
                    execution_failed_by=None,
                    execution_error=None,
                    revision=1,
                    created_at=now,
                    created_by=actor_id,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            row = (
                session.execute(
                    select(request_table).where(
                        request_table.c.tenant_id == tenant_id, request_table.c.id == approval_id
                    )
                )
                .mappings()
                .one()
            )
            payload = {
                "request": _request_payload(row, now=now),
                "execution": {
                    "state": "awaiting_approval",
                    "adapter": "execution_adapter_not_connected",
                },
            }
            _audit(
                session,
                actor=actor,
                action="approval.request.created",
                resource_type="tenant_approval_request",
                resource_id=approval_id,
                before=None,
                after={**payload["request"], "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=201,
                resource_id=approval_id,
            )
            return ServiceResult(payload, 201)


def _ticket_digest(tenant_id: str, request_id: str, ticket: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:enterprise-approval-execution-ticket:v1\x00")
    for value in (tenant_id, request_id, ticket):
        raw = str(value).encode("utf-8")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _new_ticket() -> str:
    return f"rag4c-approval-ticket-{uuid.uuid4().hex}.{uuid.uuid4().hex}"


def _replay_without_ticket(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    execution = result.get("execution")
    if isinstance(execution, dict) and "ticket" in execution:
        execution.pop("ticket", None)
        execution["state"] = "ticket_already_issued"
    return result


def _execution_id(tenant_id: str, request_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:enterprise-approval-execution:v1\x00")
    for value in (tenant_id, request_id):
        raw = str(value).encode("utf-8")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return f"approval-execution-{digest.hexdigest()}"


def _workspace_execution_fact(
    *,
    tenant_id: str,
    approval_request_id: str,
    request_revision: int,
    execution_revision: int,
    resource_id: str,
    snapshot: Any,
    snapshot_hash: Any,
    reason: str,
) -> ApprovalExecutionFact:
    if not isinstance(snapshot, Mapping):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Workspace approval snapshot 无效", 409
        )
    required = {
        "workspace_revision": snapshot.get("workspace_revision"),
        "policy_revision": snapshot.get("policy_revision"),
        "permission_model_version": snapshot.get("permission_model_version"),
    }
    if any(type(value) is not int or value < 1 for value in required.values()):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Workspace approval snapshot revision 无效", 409
        )
    from_mode = snapshot.get("from_mode")
    target_mode = snapshot.get("target_mode")
    fingerprint = snapshot.get("permission_matrix_fingerprint")
    workspace_id = snapshot.get("workspace_id")
    if (
        not isinstance(workspace_id, str)
        or workspace_id.strip() != resource_id
        or not isinstance(from_mode, str)
        or not isinstance(target_mode, str)
        or not isinstance(fingerprint, str)
        or not isinstance(snapshot_hash, str)
        or len(snapshot_hash) != 64
        or snapshot.get("reason") != reason
    ):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Workspace approval snapshot binding 无效", 409
        )
    return ApprovalExecutionFact(
        tenant_id=tenant_id,
        approval_request_id=approval_request_id,
        execution_id=_execution_id(tenant_id, approval_request_id),
        request_revision=request_revision,
        execution_revision=execution_revision,
        action_type="workspace_authorization_mode_change",
        resource_type="tenant_workspace",
        resource_id=resource_id,
        snapshot_hash=snapshot_hash,
        workspace_revision=required["workspace_revision"],
        policy_revision=required["policy_revision"],
        from_mode=from_mode.strip().casefold(),
        target_mode=target_mode.strip().casefold(),
        permission_model_version=required["permission_model_version"],
        permission_matrix_fingerprint=fingerprint.strip().casefold(),
        reason=reason,
    )


def _dataset_workspace_transfer_execution_fact(
    *,
    tenant_id: str,
    approval_request_id: str,
    request_revision: int,
    execution_revision: int,
    resource_id: str,
    snapshot: Any,
    snapshot_hash: Any,
    reason: str,
) -> ApprovalExecutionFact:
    """Bind a claimed transfer request to immutable Dataset/Workspace facts."""

    if not isinstance(snapshot, Mapping):
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval snapshot 无效",
            409,
        )
    if (
        type(request_revision) is not int
        or request_revision < 1
        or type(execution_revision) is not int
        or execution_revision != request_revision + 1
    ):
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval revision 无效",
            409,
        )

    def text_value(*keys: str) -> str:
        values = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                "Dataset Workspace transfer approval snapshot binding 无效",
                409,
            )
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != 1:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                "Dataset Workspace transfer approval snapshot binding 无效",
                409,
            )
        return normalized[0]

    def positive_value(*keys: str) -> int:
        values = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
        if not values or any(type(value) is not int or value < 1 for value in values):
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                "Dataset Workspace transfer approval snapshot revision 无效",
                409,
            )
        if len(set(values)) != 1:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                "Dataset Workspace transfer approval snapshot revision 无效",
                409,
            )
        return values[0]

    dataset_id = text_value("dataset_id")
    source_workspace_id = text_value("source_workspace_id")
    target_workspace_id = text_value("target_workspace_id")
    if dataset_id != resource_id or source_workspace_id == target_workspace_id:
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval snapshot scope 无效",
            409,
        )
    snapshot_reason = snapshot.get("reason")
    if not isinstance(snapshot_reason, str) or snapshot_reason.strip() != reason:
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval reason 不匹配",
            409,
        )
    if not isinstance(snapshot_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_hash):
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval snapshot hash 无效",
            409,
        )
    calculated_hash = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not hmac.compare_digest(snapshot_hash.casefold(), calculated_hash):
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Dataset Workspace transfer approval snapshot hash 不匹配",
            409,
        )

    profile_revision = positive_value(
        "profile_revision",
        "dataset_profile_revision",
        "expected_profile_revision",
        "expected_dataset_profile_revision",
    )
    ownership_revision = positive_value("ownership_revision", "expected_ownership_revision")

    def optional_positive_value(*keys: str) -> int | None:
        present = [snapshot.get(key) for key in keys if snapshot.get(key) is not None]
        if not present:
            return None
        if any(type(value) is not int or value < 1 for value in present) or len(set(present)) != 1:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                "Dataset Workspace transfer approval Workspace revision 无效",
                409,
            )
        return present[0]

    source_workspace_revision = positive_value(
        "source_workspace_revision", "expected_source_workspace_revision"
    )
    target_workspace_revision = positive_value(
        "target_workspace_revision", "expected_target_workspace_revision"
    )
    return ApprovalExecutionFact(
        tenant_id=tenant_id,
        approval_request_id=approval_request_id,
        execution_id=_execution_id(tenant_id, approval_request_id),
        request_revision=request_revision,
        execution_revision=execution_revision,
        action_type="dataset_workspace_transfer",
        resource_type="knowledge_base",
        resource_id=resource_id,
        snapshot_hash=snapshot_hash.casefold(),
        reason=reason,
        profile_revision=profile_revision,
        dataset_profile_revision=profile_revision,
        ownership_revision=ownership_revision,
        expected_dataset_profile_revision=profile_revision,
        expected_ownership_revision=ownership_revision,
        source_workspace_id=source_workspace_id,
        target_workspace_id=target_workspace_id,
        source_workspace_revision=source_workspace_revision,
        target_workspace_revision=target_workspace_revision,
        expected_source_workspace_revision=source_workspace_revision,
        expected_target_workspace_revision=target_workspace_revision,
    )


def _knowledge_base_release_execution_fact(
    *,
    tenant_id: str,
    approval_request_id: str,
    request_revision: int,
    execution_revision: int,
    resource_id: str,
    action_type: str,
    snapshot: Any,
    snapshot_hash: Any,
    reason: str,
) -> ApprovalExecutionFact:
    if action_type not in {
        "knowledge_base_release_publish",
        "knowledge_base_release_rollback",
    }:
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Release approval action 无效", 409
        )
    if not isinstance(snapshot, Mapping):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Release approval snapshot 无效", 409
        )

    def text_value(field: str, maximum: int = 128) -> str:
        value = snapshot.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                f"Release approval snapshot {field} 无效",
                409,
            )
        return value.strip()

    def int_value(field: str, minimum: int = 1) -> int:
        value = snapshot.get(field)
        if type(value) is not int or value < minimum:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                f"Release approval snapshot {field} 无效",
                409,
            )
        return value

    dataset_id = text_value("dataset_id", 64)
    release_id = text_value("release_id", 64)
    manifest_digest = text_value("manifest_digest", 64).casefold()
    channel_id = text_value("channel_id", 128)
    workspace_id = text_value("workspace_id", 128)
    if (
        dataset_id != resource_id
        or snapshot.get("action_type") != action_type
        or snapshot.get("reason") != reason
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_digest)
        or not isinstance(snapshot_hash, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_hash)
    ):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Release approval snapshot binding 无效", 409
        )
    return ApprovalExecutionFact(
        tenant_id=tenant_id,
        approval_request_id=approval_request_id,
        execution_id=_execution_id(tenant_id, approval_request_id),
        request_revision=request_revision,
        execution_revision=execution_revision,
        action_type=action_type,
        resource_type="knowledge_base",
        resource_id=resource_id,
        snapshot_hash=snapshot_hash.casefold(),
        reason=reason,
        release_id=release_id,
        release_number=int_value("release_number"),
        manifest_digest=manifest_digest,
        mutation_generation=int_value("mutation_generation", 0),
        channel_id=channel_id,
        channel_revision=int_value("channel_revision"),
        profile_revision=int_value("profile_revision"),
        ownership_revision=int_value("ownership_revision"),
        workspace_id=workspace_id,
        workspace_revision=int_value("workspace_revision"),
        serving_generation=int_value("serving_generation", 0),
    )


def _knowledge_base_release_quality_waiver_execution_fact(
    *,
    tenant_id: str,
    approval_request_id: str,
    request_revision: int,
    execution_revision: int,
    resource_id: str,
    snapshot: Any,
    snapshot_hash: Any,
    reason: str,
) -> ApprovalExecutionFact:
    if not isinstance(snapshot, Mapping):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver approval snapshot 无效", 409
        )

    def text_value(field: str, maximum: int = 128) -> str:
        value = snapshot.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                f"Quality Waiver approval snapshot {field} 无效",
                409,
            )
        return value.strip()

    def int_value(field: str, minimum: int = 1) -> int:
        value = snapshot.get(field)
        if type(value) is not int or value < minimum:
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                f"Quality Waiver approval snapshot {field} 无效",
                409,
            )
        return value

    def digest_value(field: str) -> str:
        value = text_value(field, 64).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ApprovalConflict(
                "approval_execution_fact_invalid",
                f"Quality Waiver approval snapshot {field} 无效",
                409,
            )
        return value

    dataset_id = text_value("dataset_id", 64)
    release_id = text_value("release_id", 64)
    release_number = int_value("release_number")
    manifest_digest = digest_value("manifest_digest")
    channel_id = text_value("channel_id", 128)
    channel_revision = int_value("channel_revision")
    quality_gate_revision = snapshot.get("quality_gate_revision", channel_revision)
    if type(quality_gate_revision) is not int or quality_gate_revision != channel_revision:
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver quality gate revision 无效", 409
        )
    policy_id = text_value("policy_id", 64)
    policy_revision = int_value("policy_revision")
    policy_digest = digest_value("policy_digest")
    quality_gate_digest = digest_value("quality_gate_digest")
    evidence_values = [
        value
        for value in (snapshot.get("quality_evidence_digest"), snapshot.get("evidence_digest"))
        if value is not None
    ]
    if len(set(evidence_values)) > 1:
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver evidence digest 不一致", 409
        )
    quality_evidence_digest = None
    if evidence_values:
        quality_evidence_digest = str(evidence_values[0]).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", quality_evidence_digest):
            raise ApprovalConflict(
                "approval_execution_fact_invalid", "Quality Waiver evidence digest 无效", 409
            )
    expiry_values = [
        value
        for value in (snapshot.get("waiver_expires_at"), snapshot.get("requested_expires_at"))
        if value is not None
    ]
    if len(set(expiry_values)) != 1 or not expiry_values or not isinstance(expiry_values[0], str):
        raise ApprovalConflict("approval_execution_fact_invalid", "Quality Waiver expiry 无效", 409)
    expiry = expiry_values[0]
    parsed_expiry = _datetime(expiry)
    if parsed_expiry is None or _iso(parsed_expiry) != expiry:
        raise ApprovalConflict(
            "approval_execution_fact_invalid",
            "Quality Waiver expiry 不是 canonical ISO datetime",
            409,
        )
    if (
        dataset_id != resource_id
        or snapshot.get("tenant_id") != tenant_id
        or snapshot.get("resource_id") != resource_id
        or snapshot.get("resource_type") != "knowledge_base"
        or snapshot.get("action_type") != "knowledge_base_release_quality_waiver"
        or snapshot.get("reason") != reason
        or not isinstance(snapshot_hash, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_hash)
    ):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver approval snapshot binding 无效", 409
        )
    calculated_hash = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not hmac.compare_digest(snapshot_hash.casefold(), calculated_hash):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver approval snapshot hash 不匹配", 409
        )
    for field, minimum in (
        ("profile_revision", 1),
        ("ownership_revision", 1),
        ("workspace_revision", 1),
        ("mutation_generation", 0),
        ("serving_generation", 0),
    ):
        int_value(field, minimum)
    workspace_id = text_value("workspace_id", 128)
    gate_state = snapshot.get("quality_gate_state")
    if gate_state is not None and gate_state != "blocked":
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver quality gate state 无效", 409
        )
    gate_reason = snapshot.get("quality_gate_reason")
    if gate_reason is not None and (not isinstance(gate_reason, str) or not gate_reason.strip()):
        raise ApprovalConflict(
            "approval_execution_fact_invalid", "Quality Waiver quality gate reason 无效", 409
        )
    return ApprovalExecutionFact(
        tenant_id=tenant_id,
        approval_request_id=approval_request_id,
        execution_id=_execution_id(tenant_id, approval_request_id),
        request_revision=request_revision,
        execution_revision=execution_revision,
        action_type="knowledge_base_release_quality_waiver",
        resource_type="knowledge_base",
        resource_id=resource_id,
        snapshot_hash=snapshot_hash.casefold(),
        reason=reason,
        policy_id=policy_id,
        policy_revision=policy_revision,
        policy_digest=policy_digest,
        quality_gate_revision=quality_gate_revision,
        quality_gate_digest=quality_gate_digest,
        quality_evidence_digest=quality_evidence_digest,
        evidence_digest=quality_evidence_digest,
        waiver_expires_at=expiry,
        requested_expires_at=expiry,
        quality_gate_state=gate_state,
        quality_gate_reason=gate_reason,
        release_id=release_id,
        release_number=release_number,
        manifest_digest=manifest_digest,
        mutation_generation=int(snapshot["mutation_generation"]),
        channel_id=channel_id,
        channel_revision=channel_revision,
        profile_revision=int(snapshot["profile_revision"]),
        ownership_revision=int(snapshot["ownership_revision"]),
        workspace_id=workspace_id,
        workspace_revision=int(snapshot["workspace_revision"]),
        serving_generation=int(snapshot["serving_generation"]),
    )


def _safe_execution_error(error: BaseException) -> str:
    message = _redact_text(str(error).strip()) or "execution adapter failed"
    return message[:512]


def _safe_adapter_result(value: Any) -> Any:
    """Return a bounded, replay-safe projection of a consumer result.

    Consumers are trusted application code, not a reason to persist arbitrary
    downstream envelopes.  Credential-like fields are removed entirely so the
    generic tenant idempotency ledger can safely retain the final response.
    """

    safe = _redact_snapshot(value)
    sensitive_names = {
        "access_token",
        "api_key",
        "authorization",
        "authorization_header",
        "cookie",
        "database_url",
        "id_token",
        "invite_token",
        "raw_token",
        "refresh_token",
        "secret",
        "token",
        "token_hash",
    }

    structural_names = {"authorization_policy"}

    def project(item: Any) -> Any:
        if isinstance(item, Mapping):
            projected: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key = str(raw_key)
                if key.casefold() not in structural_names and (
                    key.casefold() in sensitive_names or _SECRET_KEY_RE.search(key)
                ):
                    continue
                projected[key] = project(raw_value)
            return projected
        if isinstance(item, list):
            return [project(child) for child in item]
        return item

    projected = project(safe)
    try:
        encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ApprovalConflict(
            "approval_adapter_result_invalid", "下游执行结果无法安全返回", 502
        ) from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ApprovalConflict("approval_adapter_result_invalid", "下游执行结果过大", 502)
    return projected


def _recover_release_adapter_result_in_session(
    session: Session, payload: Mapping[str, Any]
) -> Any | None:
    action = str(payload.get("action_type") or "")
    operation = {
        "knowledge_base_release_publish": "promote_release",
        "knowledge_base_release_rollback": "rollback_channel_release",
        "knowledge_base_release_quality_waiver": "grant_quality_waiver",
    }.get(action)
    if operation is None:
        return None
    tenant_id = str(payload.get("tenant_id") or "")
    actor_id = str(payload.get("consumer_actor_id") or "")
    execution_id = str(payload.get("execution_id") or "")
    if not tenant_id or not actor_id or not execution_id:
        return None
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, execution_id)
    tables = set(inspect(session.connection()).get_table_names())
    if "tenant_control_mutation_requests" not in tables:
        return None
    ledger = _table(session, "tenant_control_mutation_requests")
    row = (
        session.execute(
            select(ledger).where(
                ledger.c.tenant_id == tenant_id,
                ledger.c.actor_id == actor_id,
                ledger.c.idempotency_key == digest,
                ledger.c.operation == operation,
                ledger.c.status == "completed",
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    response = _json_value(_value(row, "response_json", {}))
    http_status = _value(row, "http_status")
    if not isinstance(response, Mapping) or type(http_status) is not int:
        return None
    if not 200 <= http_status <= 299:
        return None
    return _safe_adapter_result(response)


def _recover_release_adapter_result(engine: Any, payload: Mapping[str, Any]) -> Any | None:
    with Session(engine) as session:
        return _recover_release_adapter_result_in_session(session, payload)


def decide_approval_request(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    decision: str,
    expected_revision: int,
    comment: str,
    idempotency_key: str,
    request_id_header: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_request_id = _clean(request_id, "request_id", 64)
    clean_decision = _clean(decision, "decision", 16).casefold()
    if clean_decision not in {"approved", "rejected"}:
        raise ApprovalValidation("approval_decision_invalid", "decision 无效", 422)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    clean_comment = _clean(comment, "comment", 500, allow_empty=clean_decision == "approved")
    if clean_decision == "rejected" and not clean_comment:
        raise ApprovalValidation("approval_comment_required", "reject 必须填写 comment", 422)
    request_hash = tenant_request_hash(
        operation=f"approval.request.{clean_decision}",
        path_identity={"tenant_id": tenant_id, "request_id": clean_request_id},
        body={"revision": revision, "comment": clean_comment},
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation=f"approval.request.{clean_decision}",
                resource_type="tenant_approval_request",
            )
            if replay is not None:
                return replay
            request_table = _table(session, "tenant_approval_requests")
            row = (
                session.execute(
                    select(request_table)
                    .where(
                        request_table.c.tenant_id == tenant_id,
                        request_table.c.id == clean_request_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ApprovalNotFound("审批申请不存在")
            expired_result = _expire_pending_request(
                session,
                request_table,
                row,
                actor=actor,
                request_id=clean_request_id,
                request_id_header=request_id_header,
                request_ip=request_ip,
                now=now,
                reservation=reservation,
            )
            if expired_result is not None:
                return expired_result
            current_status = str(row["status"])
            if current_status != "pending":
                raise ApprovalConflict(
                    "approval_request_terminal", "审批申请已经进入终态，不能再次决策", 409
                )
            if int(row["revision"]) != revision:
                raise ApprovalConflict(
                    "approval_revision_conflict", "审批申请 revision 已变化，请刷新后重试", 409
                )
            if str(row["action_type"]) == "document_purge":
                _validate_document_purge_decision_authority(
                    session, tenant_id=tenant_id, approval_request_id=clean_request_id
                )
            if str(row["requester_id"]) == actor_id:
                raise ApprovalForbidden(
                    "approval_self_decision_forbidden", "申请人不能审批自己的申请", 403
                )
            if not _eligible(
                session, tenant_id=tenant_id, policy_id=str(row["policy_id"]), account_id=actor_id
            ):
                raise ApprovalForbidden(
                    "approval_approver_ineligible",
                    "当前成员不在该审批规则的 eligible approver 范围内",
                    403,
                )
            decision_table = _table(session, "tenant_approval_decisions")
            previous = (
                session.execute(
                    select(decision_table).where(
                        decision_table.c.tenant_id == tenant_id,
                        decision_table.c.request_id == clean_request_id,
                        decision_table.c.approver_id == actor_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None:
                raise ApprovalConflict(
                    "approval_decision_duplicate", "同一 approver 只能提交一次不可变决策", 409
                )
            decision_id = f"approval-decision-{uuid.uuid4().hex}"
            session.execute(
                decision_table.insert().values(
                    id=decision_id,
                    tenant_id=tenant_id,
                    request_id=clean_request_id,
                    approver_id=actor_id,
                    decision=clean_decision,
                    comment=clean_comment,
                    decided_at=now,
                    revision=1,
                    created_at=now,
                    created_by=actor_id,
                )
            )
            received = int(row["received_approvals"]) + (1 if clean_decision == "approved" else 0)
            next_status = (
                "rejected"
                if clean_decision == "rejected"
                else ("approved" if received >= int(row["required_approvals"]) else "pending")
            )
            values: dict[str, Any] = {
                "status": next_status,
                "received_approvals": received,
                "revision": revision + 1,
                "updated_at": now,
                "updated_by": actor_id,
            }
            raw_ticket: str | None = None
            if clean_decision == "rejected":
                values.update(
                    {
                        "rejected_at": now,
                        "rejected_by": actor_id,
                        "rejection_comment": clean_comment,
                    }
                )
            elif next_status == "approved":
                raw_ticket = _new_ticket()
                values.update(
                    {
                        "execution_ticket_hash": _ticket_digest(
                            tenant_id, clean_request_id, raw_ticket
                        ),
                        "ticket_issued_at": now,
                    }
                )
            session.execute(
                update(request_table)
                .where(
                    request_table.c.tenant_id == tenant_id,
                    request_table.c.id == clean_request_id,
                    request_table.c.revision == revision,
                )
                .values(**values)
            )
            updated = (
                session.execute(
                    select(request_table).where(
                        request_table.c.tenant_id == tenant_id,
                        request_table.c.id == clean_request_id,
                    )
                )
                .mappings()
                .one()
            )
            decision_row = (
                session.execute(select(decision_table).where(decision_table.c.id == decision_id))
                .mappings()
                .one()
            )
            execution: dict[str, Any] = {
                "state": "ticket_issued"
                if raw_ticket
                else ("rejected" if next_status == "rejected" else "awaiting_approval"),
                "adapter": "execution_adapter_not_connected",
            }
            if raw_ticket is not None:
                execution["ticket"] = raw_ticket
            payload = {
                "request": _request_payload(updated, now=now),
                "decision": _decision_payload(decision_row),
                "execution": execution,
            }
            _audit(
                session,
                actor=actor,
                action=f"approval.request.{clean_decision}",
                resource_type="tenant_approval_request",
                resource_id=clean_request_id,
                before=_request_payload(row, now=now),
                after={**payload["request"], "decision": payload["decision"]},
                request_id=request_id_header,
                request_ip=request_ip,
                now=now,
            )
            replay_payload = _replay_without_ticket(payload)
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=replay_payload,
                http_status=200,
                resource_id=clean_request_id,
            )
            return ServiceResult(payload)


def cancel_approval_request(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id_header: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_request_id = _clean(request_id, "request_id", 64)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    safe_reason = _safe_reason(reason)
    request_hash = tenant_request_hash(
        operation="approval.request.cancel",
        path_identity={"tenant_id": tenant_id, "request_id": clean_request_id},
        body={"revision": revision, "reason": safe_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0025(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="approval.request.cancel",
                resource_type="tenant_approval_request",
            )
            if replay is not None:
                return replay
            request_table = _table(session, "tenant_approval_requests")
            row = (
                session.execute(
                    select(request_table)
                    .where(
                        request_table.c.tenant_id == tenant_id,
                        request_table.c.id == clean_request_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ApprovalNotFound("审批申请不存在")
            expired_result = _expire_pending_request(
                session,
                request_table,
                row,
                actor=actor,
                request_id=clean_request_id,
                request_id_header=request_id_header,
                request_ip=request_ip,
                now=now,
                reservation=reservation,
            )
            if expired_result is not None:
                return expired_result
            current_status = str(row["status"])
            if current_status != "pending":
                raise ApprovalConflict(
                    "approval_request_terminal",
                    "只有 pending 审批申请可以取消",
                    409,
                )
            if int(row["revision"]) != revision:
                raise ApprovalConflict(
                    "approval_revision_conflict",
                    "审批申请 revision 已变化，请刷新后重试",
                    409,
                )
            _require_requester(actor, str(row["requester_id"]))
            session.execute(
                update(request_table)
                .where(
                    request_table.c.tenant_id == tenant_id,
                    request_table.c.id == clean_request_id,
                    request_table.c.revision == revision,
                    request_table.c.status == "pending",
                )
                .values(
                    status="cancelled",
                    cancelled_at=now,
                    cancelled_by=actor_id,
                    revision=revision + 1,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            updated = (
                session.execute(
                    select(request_table).where(
                        request_table.c.tenant_id == tenant_id,
                        request_table.c.id == clean_request_id,
                    )
                )
                .mappings()
                .one()
            )
            payload = {
                "request": _request_payload(updated, now=now),
                "cancellation": {"reason": safe_reason},
            }
            _audit(
                session,
                actor=actor,
                action="approval.request.cancelled",
                resource_type="tenant_approval_request",
                resource_id=clean_request_id,
                before=_request_payload(row),
                after={**payload["request"], "cancellation_reason": safe_reason},
                request_id=request_id_header,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=clean_request_id,
            )
            return ServiceResult(payload)


def consume_approval_ticket(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    ticket: str,
    expected_revision: int,
    action_type: str,
    resource_type: str,
    resource_id: str,
    idempotency_key: str,
    request_id_header: str,
    request_ip: str,
    now: datetime,
    execution_adapter: Callable[[Mapping[str, Any]], Any] | None = None,
) -> ServiceResult:
    clean_request_id = _clean(request_id, "request_id", 64)
    clean_ticket = _clean(ticket, "ticket", 256)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    clean_action = _clean(action_type, "action_type", 64)
    clean_resource_type = _clean(resource_type, "resource_type", 64)
    clean_resource_id = _clean(resource_id, "resource_id", 256)
    request_hash = tenant_request_hash(
        operation="approval.ticket.consume",
        path_identity={"tenant_id": tenant_id, "request_id": clean_request_id},
        body={
            "revision": revision,
            "action_type": clean_action,
            "resource_type": clean_resource_type,
            "resource_id": clean_resource_id,
            "ticket_digest": _ticket_digest(tenant_id, clean_request_id, clean_ticket),
        },
    )
    key_lock, _ = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    adapter_payload: dict[str, Any] | None = None
    claim_revision = revision + 1
    with key_lock:
        with engine_serialization_lock(engine):
            with Session(engine) as session:
                with _transaction(session):
                    _ensure_0025(session.connection())
                    actor = _actor(session, tenant_id, actor_id)
                    if clean_action in {
                        "dataset_acl_disable",
                        "member_role_change",
                        "workspace_authorization_mode_change",
                        "dataset_workspace_transfer",
                        "knowledge_base_release_publish",
                        "knowledge_base_release_rollback",
                        "knowledge_base_release_quality_waiver",
                    } and actor.role not in {"owner", "admin"}:
                        raise ApprovalForbidden(
                            "approval_manager_required",
                            "只有 tenant owner/admin 可以消费该执行授权",
                            403,
                        )
                    request_table = _table(session, "tenant_approval_requests")
                    recovery_row = (
                        session.execute(
                            select(request_table)
                            .where(
                                request_table.c.tenant_id == tenant_id,
                                request_table.c.id == clean_request_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if recovery_row is None:
                        raise ApprovalNotFound("审批申请不存在")
                    if str(recovery_row["status"]) == "executing" and clean_action in {
                        "knowledge_base_release_publish",
                        "knowledge_base_release_rollback",
                        "knowledge_base_release_quality_waiver",
                    }:
                        if (
                            int(recovery_row["revision"]) != claim_revision
                            or str(recovery_row["action_type"]) != clean_action
                            or str(recovery_row["resource_type"]) != clean_resource_type
                            or str(recovery_row["resource_id"]) != clean_resource_id
                            or recovery_row["ticket_consumed_at"] is None
                        ):
                            raise ApprovalConflict(
                                "approval_execution_state_conflict",
                                "审批执行状态已变化，不能安全恢复执行",
                                409,
                            )
                        expected_digest = str(recovery_row["execution_ticket_hash"] or "")
                        if not expected_digest or not hmac.compare_digest(
                            expected_digest,
                            _ticket_digest(tenant_id, clean_request_id, clean_ticket),
                        ):
                            raise ApprovalConflict(
                                "approval_ticket_invalid", "execution ticket 无效", 409
                            )
                        recovery_adapter_payload = {
                            "tenant_id": tenant_id,
                            "action_type": clean_action,
                            "consumer_actor_id": actor.account_id,
                            "execution_id": _execution_id(tenant_id, clean_request_id),
                        }
                        recovered_result = _recover_release_adapter_result_in_session(
                            session, recovery_adapter_payload
                        )
                        if recovered_result is None:
                            raise ApprovalConflict(
                                "approval_execution_recovery_pending",
                                "审批执行结果尚不可恢复，请等待 reconciliation",
                                409,
                            )
                        completed_row = dict(recovery_row)
                        completed_row.update(
                            {
                                "status": "executed",
                                "executed_at": now,
                                "executed_by": actor_id,
                                "revision": claim_revision + 1,
                                "updated_at": now,
                                "updated_by": actor_id,
                            }
                        )
                        session.execute(
                            update(request_table)
                            .where(
                                request_table.c.tenant_id == tenant_id,
                                request_table.c.id == clean_request_id,
                                request_table.c.status == "executing",
                                request_table.c.revision == claim_revision,
                            )
                            .values(
                                status="executed",
                                executed_at=now,
                                executed_by=actor_id,
                                revision=claim_revision + 1,
                                updated_at=now,
                                updated_by=actor_id,
                            )
                        )
                        payload = {
                            "request": _request_payload(completed_row, now=now),
                            "execution": {
                                "state": "executed",
                                "result": recovered_result,
                                "recovered": True,
                            },
                        }
                        control = _table(session, "tenant_control_mutation_requests")
                        approval_digest = tenant_idempotency_key_digest(
                            tenant_id, actor_id, idempotency_key
                        )
                        recovered_ledger = session.execute(
                            update(control)
                            .where(
                                control.c.tenant_id == tenant_id,
                                control.c.actor_id == actor_id,
                                control.c.idempotency_key == approval_digest,
                                control.c.request_hash == request_hash,
                                control.c.operation == "approval.ticket.consume",
                                control.c.status == "pending",
                            )
                            .values(
                                status="completed",
                                response_json=payload,
                                http_status=200,
                                resource_id=clean_request_id,
                                completed_at=now,
                            )
                        )
                        if recovered_ledger.rowcount != 1:
                            raise ApprovalConflict(
                                "approval_execution_recovery_ledger_missing",
                                "审批执行 receipt 已存在，但 consume ledger 无法恢复",
                                409,
                            )
                        _audit(
                            session,
                            actor=actor,
                            action="approval.ticket.reconciled",
                            resource_type="tenant_approval_request",
                            resource_id=clean_request_id,
                            before=_request_payload(recovery_row, now=None),
                            after=payload["request"],
                            request_id=request_id_header,
                            request_ip=request_ip,
                            now=now,
                        )
                        return ServiceResult(payload, 200)

                    reservation, replay = _reserve(
                        session,
                        actor=actor,
                        key=idempotency_key,
                        request_hash=request_hash,
                        operation="approval.ticket.consume",
                        resource_type="tenant_approval_request",
                    )
                    if replay is not None:
                        return replay
                    row = (
                        session.execute(
                            select(request_table)
                            .where(
                                request_table.c.tenant_id == tenant_id,
                                request_table.c.id == clean_request_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        raise ApprovalNotFound("审批申请不存在")
                    if str(row["status"]) != "approved":
                        raise ApprovalConflict(
                            "approval_ticket_not_consumable",
                            "当前审批申请没有可消费的 approved ticket",
                            409,
                        )
                    if int(row["revision"]) != revision:
                        raise ApprovalConflict(
                            "approval_revision_conflict",
                            "审批申请 revision 已变化，请刷新后重试",
                            409,
                        )
                    if (
                        str(row["action_type"]) != clean_action
                        or str(row["resource_type"]) != clean_resource_type
                        or str(row["resource_id"]) != clean_resource_id
                    ):
                        raise ApprovalConflict(
                            "approval_ticket_scope_mismatch", "execution ticket scope 不匹配", 409
                        )
                    expected_digest = str(row["execution_ticket_hash"] or "")
                    if not expected_digest or not hmac.compare_digest(
                        expected_digest, _ticket_digest(tenant_id, clean_request_id, clean_ticket)
                    ):
                        raise ApprovalConflict(
                            "approval_ticket_invalid", "execution ticket 无效", 409
                        )
                    if execution_adapter is None:
                        raise ApprovalExecutionAdapterNotConnected()
                    request_snapshot = _redact_snapshot(
                        _json_value(_value(row, "snapshot_json", {}))
                    )
                    requester_id = str(_value(row, "requester_id"))
                    execution_fact = None
                    if clean_action == "workspace_authorization_mode_change":
                        execution_fact = _workspace_execution_fact(
                            tenant_id=tenant_id,
                            approval_request_id=clean_request_id,
                            request_revision=revision,
                            execution_revision=claim_revision,
                            resource_id=clean_resource_id,
                            snapshot=request_snapshot,
                            snapshot_hash=_value(row, "payload_hash"),
                            reason=_safe_reason(_value(row, "reason", "")),
                        )
                    elif clean_action == "dataset_workspace_transfer":
                        execution_fact = _dataset_workspace_transfer_execution_fact(
                            tenant_id=tenant_id,
                            approval_request_id=clean_request_id,
                            request_revision=revision,
                            execution_revision=claim_revision,
                            resource_id=clean_resource_id,
                            snapshot=request_snapshot,
                            snapshot_hash=_value(row, "payload_hash"),
                            reason=_safe_reason(_value(row, "reason", "")),
                        )
                    elif clean_action in {
                        "knowledge_base_release_publish",
                        "knowledge_base_release_rollback",
                    }:
                        execution_fact = _knowledge_base_release_execution_fact(
                            tenant_id=tenant_id,
                            approval_request_id=clean_request_id,
                            request_revision=revision,
                            execution_revision=claim_revision,
                            resource_id=clean_resource_id,
                            action_type=clean_action,
                            snapshot=request_snapshot,
                            snapshot_hash=_value(row, "payload_hash"),
                            reason=_safe_reason(_value(row, "reason", "")),
                        )
                    elif clean_action == "knowledge_base_release_quality_waiver":
                        execution_fact = _knowledge_base_release_quality_waiver_execution_fact(
                            tenant_id=tenant_id,
                            approval_request_id=clean_request_id,
                            request_revision=revision,
                            execution_revision=claim_revision,
                            resource_id=clean_resource_id,
                            snapshot=request_snapshot,
                            snapshot_hash=_value(row, "payload_hash"),
                            reason=_safe_reason(_value(row, "reason", "")),
                        )
                    adapter_payload = {
                        "tenant_id": tenant_id,
                        "approval_request_id": clean_request_id,
                        "request_id": clean_request_id,
                        "execution_id": _execution_id(tenant_id, clean_request_id),
                        "execution_revision": claim_revision,
                        "action_type": clean_action,
                        "resource_type": clean_resource_type,
                        "resource_id": clean_resource_id,
                        "consumer_actor_id": actor.account_id,
                        "consumer_actor_role": actor.role,
                        "consumer": {"actor_id": actor.account_id, "role": actor.role},
                        "requester_id": requester_id,
                        "request_snapshot": request_snapshot,
                        "snapshot": request_snapshot,
                        "reason": _safe_reason(_value(row, "reason", "")),
                        "request_evidence": {
                            "request_id": str(request_id_header or "")[:128],
                            "request_ip": str(request_ip or "")[:64],
                        },
                    }
                    if clean_action == "knowledge_base_release_quality_waiver":
                        adapter_payload["execution_now"] = now
                    if execution_fact is not None:
                        adapter_payload["approval_execution_fact"] = execution_fact
                    claim = session.execute(
                        update(request_table)
                        .where(
                            request_table.c.tenant_id == tenant_id,
                            request_table.c.id == clean_request_id,
                            request_table.c.revision == revision,
                            request_table.c.status == "approved",
                        )
                        .values(
                            status="executing",
                            ticket_consumed_at=now,
                            revision=claim_revision,
                            updated_at=now,
                            updated_by=actor_id,
                        )
                    )
                    if claim.rowcount != 1:
                        raise ApprovalConflict(
                            "approval_revision_conflict",
                            "审批申请 revision 已变化，请刷新后重试",
                            409,
                        )
        if adapter_payload is None:
            raise ApprovalExecutionAdapterNotConnected()
        try:
            adapter_result = execution_adapter(adapter_payload)
            safe_adapter_result = _safe_adapter_result(adapter_result)
        except Exception as exc:
            safe_adapter_result = _recover_release_adapter_result(engine, adapter_payload)
            if safe_adapter_result is None:
                execution_error = _safe_execution_error(exc)
                with engine_serialization_lock(engine):
                    with Session(engine) as session:
                        with _transaction(session):
                            _ensure_0025(session.connection())
                            request_table = _table(session, "tenant_approval_requests")
                            executing_row = (
                                session.execute(
                                    select(request_table)
                                    .where(
                                        request_table.c.tenant_id == tenant_id,
                                        request_table.c.id == clean_request_id,
                                    )
                                    .with_for_update()
                                )
                                .mappings()
                                .one_or_none()
                            )
                            if executing_row is None:
                                raise ApprovalNotFound("审批申请不存在")
                            if (
                                str(executing_row["status"]) != "executing"
                                or int(executing_row["revision"]) != claim_revision
                            ):
                                raise ApprovalConflict(
                                    "approval_execution_state_conflict",
                                    "审批执行状态已变化，不能安全完成执行",
                                    409,
                                )
                            failed_update = session.execute(
                                update(request_table)
                                .where(
                                    request_table.c.tenant_id == tenant_id,
                                    request_table.c.id == clean_request_id,
                                    request_table.c.status == "executing",
                                    request_table.c.revision == claim_revision,
                                )
                                .values(
                                    status="execution_failed",
                                    execution_failed_at=now,
                                    execution_failed_by=actor_id,
                                    execution_error=execution_error,
                                    revision=claim_revision + 1,
                                    updated_at=now,
                                    updated_by=actor_id,
                                )
                            )
                            if failed_update.rowcount != 1:
                                raise ApprovalConflict(
                                    "approval_execution_state_conflict",
                                    "审批执行状态已变化，不能安全完成执行",
                                    409,
                                )
                            failed_row = dict(
                                session.execute(
                                    select(request_table).where(
                                        request_table.c.tenant_id == tenant_id,
                                        request_table.c.id == clean_request_id,
                                    )
                                )
                                .mappings()
                                .one()
                            )
                            payload = {
                                "request": _request_payload(failed_row, now=now),
                                "execution": {
                                    "state": "execution_failed",
                                    "code": "approval_execution_failed",
                                },
                            }
                            _audit(
                                session,
                                actor=actor,
                                action="approval.ticket.execution_failed",
                                resource_type="tenant_approval_request",
                                resource_id=clean_request_id,
                                before=_request_payload(executing_row, now=None),
                                after=payload["request"],
                                request_id=request_id_header,
                                request_ip=request_ip,
                                now=now,
                            )
                            complete_tenant_mutation(
                                session,
                                reservation,
                                response_for_replay=payload,
                                http_status=502,
                                resource_id=clean_request_id,
                            )
                            return ServiceResult(payload, 502)

        with engine_serialization_lock(engine):
            with Session(engine) as session:
                with _transaction(session):
                    _ensure_0025(session.connection())
                    request_table = _table(session, "tenant_approval_requests")
                    executing_row = (
                        session.execute(
                            select(request_table)
                            .where(
                                request_table.c.tenant_id == tenant_id,
                                request_table.c.id == clean_request_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if executing_row is None:
                        raise ApprovalNotFound("审批申请不存在")
                    if (
                        str(executing_row["status"]) != "executing"
                        or int(executing_row["revision"]) != claim_revision
                    ):
                        raise ApprovalConflict(
                            "approval_execution_state_conflict",
                            "审批执行状态已变化，不能安全完成执行",
                            409,
                        )
                    completed_update = session.execute(
                        update(request_table)
                        .where(
                            request_table.c.tenant_id == tenant_id,
                            request_table.c.id == clean_request_id,
                            request_table.c.status == "executing",
                            request_table.c.revision == claim_revision,
                        )
                        .values(
                            status="executed",
                            executed_at=now,
                            executed_by=actor_id,
                            revision=claim_revision + 1,
                            updated_at=now,
                            updated_by=actor_id,
                        )
                    )
                    if completed_update.rowcount != 1:
                        raise ApprovalConflict(
                            "approval_execution_state_conflict",
                            "审批执行状态已变化，不能安全完成执行",
                            409,
                        )
                    completed_row = dict(
                        session.execute(
                            select(request_table).where(
                                request_table.c.tenant_id == tenant_id,
                                request_table.c.id == clean_request_id,
                            )
                        )
                        .mappings()
                        .one()
                    )
                    execution: dict[str, Any] = {"state": "executed"}
                    if safe_adapter_result is not None:
                        execution["result"] = safe_adapter_result
                    payload = {
                        "request": _request_payload(completed_row, now=now),
                        "execution": execution,
                    }
                    _audit(
                        session,
                        actor=actor,
                        action="approval.ticket.consumed",
                        resource_type="tenant_approval_request",
                        resource_id=clean_request_id,
                        before=_request_payload(executing_row, now=None),
                        after=payload["request"],
                        request_id=request_id_header,
                        request_ip=request_ip,
                        now=now,
                    )
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay=payload,
                        http_status=200,
                        resource_id=clean_request_id,
                    )
                    return ServiceResult(payload)


__all__ = [
    "ApprovalConflict",
    "ApprovalError",
    "ApprovalExecutionAdapterNotConnected",
    "ApprovalExecutionFact",
    "_knowledge_base_release_execution_fact",
    "_knowledge_base_release_quality_waiver_execution_fact",
    "ApprovalForbidden",
    "ApprovalMigrationRequired",
    "ApprovalNotFound",
    "ApprovalValidation",
    "ServiceResult",
    "sanitize_reason",
    "cancel_approval_request",
    "consume_approval_ticket",
    "create_approval_policy",
    "create_approval_request",
    "decide_approval_request",
    "disable_approval_policy",
    "get_approval_policy",
    "get_approval_request",
    "list_approval_policies",
    "list_approval_requests",
    "update_approval_policy",
]
