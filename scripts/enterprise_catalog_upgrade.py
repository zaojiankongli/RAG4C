"""Guarded RAG4C catalog upgrade planning and execution helper.

The command is deliberately conservative. It never invokes mysqldump, never
restores a backup, and never performs a rollback automatically. An upgrade is
dry-run unless --execute is explicitly supplied together with the backup
checksum and the expected current revision.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
from typing import Any

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url

from core import catalog_schema
from core.enterprise_release_quality_evidence import canonical_quality_digest

HEAD_REVISION = catalog_schema.HEAD_REVISION
BASELINE_REVISION = catalog_schema.BASELINE_REVISION
HEAD_CATALOG_TABLES = frozenset(catalog_schema.HEAD_CATALOG_TABLES)
BASELINE_CATALOG_TABLES = frozenset(catalog_schema.BASELINE_CATALOG_TABLES)
KNOWN_ROLES = frozenset({"owner", "admin", "editor", "member"})
SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
TARGET_LABELS = frozenset({"test", "development", "staging", "production"})
STAGE19_TARGET_REVISION = "0029_enterprise_knowledge_base_releases"
STAGE20_TARGET_REVISION = "0030_enterprise_release_quality_certification"

STAGE21_TARGET_REVISION = "0031_enterprise_release_quality_operations"
RELEASE_QUALITY_OPERATIONS_TABLES = (
    "tenant_release_quality_slo_policies",
    "tenant_release_quality_scan_schedules",
    "tenant_release_quality_scan_runs",
    "dataset_release_quality_observations",
    "dataset_release_quality_alerts",
    "dataset_release_recertification_jobs",
)
STAGE21_OPERATIONS_TABLES = RELEASE_QUALITY_OPERATIONS_TABLES

STAGE22_TARGET_REVISION = "0032_enterprise_notification_center"
NOTIFICATION_CENTER_TABLES = (
    "tenant_notification_subscriptions",
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_receipts",
    "tenant_notification_events",
)
STAGE23_TARGET_REVISION = "0033_enterprise_content_recovery"
CONTENT_RECOVERY_TABLES = tuple(sorted(catalog_schema.ENTERPRISE_CONTENT_RECOVERY_REQUIRED_TABLES))
STAGE24_TARGET_REVISION = "0034_enterprise_task_operations"
TASK_OPERATIONS_TABLES = (
    "tenant_task_projections",
    "tenant_task_operator_actions",
    "tenant_task_events",
    "tenant_task_saved_views",
    "tenant_task_reconciliation_runs",
)
STAGE25_TARGET_REVISION = "0035_enterprise_automation_workflows"
AUTOMATION_WORKFLOWS_TABLES = tuple(
    sorted(catalog_schema.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_TABLES)
)
STAGE25_AUTOMATION_TABLES = AUTOMATION_WORKFLOWS_TABLES
STAGE26_TARGET_REVISION = "0036_enterprise_knowledge_serving_reliability"
KNOWLEDGE_SERVING_RELIABILITY_TABLES = tuple(
    sorted(catalog_schema.ENTERPRISE_KNOWLEDGE_SERVING_RELIABILITY_REQUIRED_TABLES)
)
STAGE26_KNOWLEDGE_SERVING_TABLES = KNOWLEDGE_SERVING_RELIABILITY_TABLES
RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS = {
    table: frozenset(catalog_schema.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS[table])
    for table in RELEASE_QUALITY_OPERATIONS_TABLES
}
RELEASE_QUALITY_OPERATIONS_DEPENDENCY_REQUIRED_COLUMNS = {
    "tenants": frozenset({"id"}),
    "datasets": frozenset({"id", "tenant_id"}),
    "tenant_release_channels": frozenset({"id", "tenant_id"}),
    "dataset_release_manifests": frozenset({"id", "tenant_id", "dataset_id"}),
    "tenant_release_quality_gate_policies": frozenset({"id", "tenant_id"}),
    "dataset_quality_baselines": frozenset({"id", "tenant_id", "dataset_id"}),
    "dataset_release_quality_certifications": frozenset({"id", "tenant_id", "dataset_id"}),
    "dataset_release_quality_waivers": frozenset({"id", "tenant_id", "dataset_id"}),
}
RELEASE_QUALITY_OPERATIONS_GUARD_NAMES = (
    "trg_dataset_release_quality_observations_no_update",
    "trg_dataset_release_quality_observations_no_delete",
)
RELEASE_QUALITY_OPERATIONS_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
RELEASE_QUALITY_OPERATIONS_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
RELEASE_QUALITY_OPERATIONS_NON_TERMINAL_JOB_STATUSES = frozenset(
    {"pending", "claimed", "awaiting_evidence", "ready_to_certify"}
)
RELEASE_AUTHORITY_TABLES = (
    "tenant_release_channels",
    "dataset_release_manifests",
    "dataset_release_entries",
    "dataset_release_events",
    "dataset_channel_releases",
)
RELEASE_QUALITY_TABLES = (
    "tenant_release_quality_gate_policies",
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
)
RELEASE_QUALITY_WAIVER_ACTION = "knowledge_base_release_quality_waiver"
RELEASE_QUALITY_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "tenant_release_quality_gate_policies": frozenset(
        {
            "id",
            "tenant_id",
            "scope_type",
            "scope_value",
            "channel_id",
            "active_scope_key",
            "status",
            "revision",
            "policy_digest",
            "max_certification_age_minutes",
        }
    ),
    "dataset_quality_baselines": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "baseline_revision",
            "parent_baseline_id",
            "experiment_count",
            "query_count",
            "baseline_digest",
        }
    ),
    "dataset_quality_baseline_items": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "ordinal",
            "experiment_id",
            "experiment_sequence",
            "query_hash",
            "experiment_serving_generation",
            "strategy_digest",
            "result_digest",
            "evidence_digest",
            "judgment_digest",
        }
    ),
    "dataset_release_quality_certifications": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "release_id",
            "baseline_id",
            "policy_id",
            "policy_revision",
            "release_manifest_digest",
            "release_mutation_generation",
            "release_serving_generation",
            "status",
            "experiment_count",
            "completed_experiment_count",
            "degraded_experiment_count",
            "query_count",
            "judged_result_count",
            "total_result_count",
            "judgment_count",
            "judgment_coverage_bps",
            "multi_judged_results",
            "unanimous_results",
            "conflicting_results",
            "exact_agreement_bps",
            "mean_score_milli",
            "failed_rule_count",
            "policy_snapshot_json",
            "summary_json",
            "evidence_digest",
            "certification_digest",
            "valid_until",
        }
    ),
    "dataset_release_quality_certification_evidence": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "certification_id",
            "baseline_item_id",
            "experiment_id",
            "ordinal",
            "status",
            "result_count",
            "judged_result_count",
            "judgment_count",
            "multi_judged_results",
            "unanimous_results",
            "conflicting_results",
            "exact_agreement_bps",
            "mean_score_milli",
            "experiment_digest",
            "judgment_digest",
            "safe_facts_json",
        }
    ),
    "dataset_release_quality_waivers": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "policy_id",
            "policy_revision",
            "release_manifest_digest",
            "approval_request_id",
            "approval_execution_id",
            "valid_from",
            "expires_at",
            "waiver_digest",
        }
    ),
    "dataset_release_quality_events": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "certification_id",
            "waiver_id",
            "event_type",
            "event_sequence",
            "state",
            "previous_event_digest",
            "event_digest",
            "approval_request_id",
            "approval_execution_id",
            "safe_snapshot_json",
        }
    ),
}
RELEASE_QUALITY_DEPENDENCY_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "tenant_release_channels": frozenset(
        {"id", "tenant_id", "status", "risk_tier", "is_default_serving", "active_default_slot"}
    ),
    "dataset_release_manifests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "manifest_digest",
            "mutation_generation",
            "serving_generation",
        }
    ),
    "datasets": frozenset({"id", "tenant_id"}),
    "tenant_approval_policies": frozenset({"action_type"}),
    "tenant_approval_requests": frozenset({"action_type"}),
}
DEFAULT_RELEASE_CHANNEL_CODES = ("development", "testing", "production")
RELEASE_SCHEMA_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "tenant_release_channels": frozenset(
        {
            "id",
            "tenant_id",
            "code",
            "normalized_code",
            "status",
            "is_default_serving",
            "active_default_slot",
            "revision",
        }
    ),
    "dataset_release_manifests": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "release_number",
            "profile_revision",
            "ownership_revision",
            "workspace_id",
            "workspace_revision",
            "mutation_generation",
            "serving_generation",
            "readiness_state",
            "entry_count",
            "blocker_count",
        }
    ),
    "dataset_release_entries": frozenset({"id", "tenant_id", "dataset_id", "release_id"}),
    "dataset_release_events": frozenset(
        {"id", "tenant_id", "dataset_id", "release_id", "event_type"}
    ),
    "dataset_channel_releases": frozenset(
        {"id", "tenant_id", "dataset_id", "channel_id", "status", "active_release_id", "revision"}
    ),
    "app_dataset_references": frozenset(
        {
            "id",
            "tenant_id",
            "app_id",
            "dataset_id",
            "status",
            "active_slot",
            "release_mode",
            "release_channel_id",
            "pinned_release_id",
        }
    ),
    "datasets": frozenset({"id", "tenant_id", "status", "graph_enabled", "serving_release_id"}),
}
RELEASE_READINESS_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "tenant_workspaces": frozenset({"id", "tenant_id", "status", "revision"}),
    "dataset_workspace_ownerships": frozenset(
        {"tenant_id", "dataset_id", "workspace_id", "revision"}
    ),
    "documents": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "lifecycle_state",
            "retrieval_enabled",
            "current_version_id",
            "desired_index_revision",
            "indexed_revision",
            "graph_revision",
        }
    ),
    "document_versions": frozenset(
        {"id", "tenant_id", "dataset_id", "document_id", "revision", "source_hash"}
    ),
    "index_operations": frozenset({"id", "tenant_id", "dataset_id", "document_id", "status"}),
    "data_sources": frozenset({"id", "tenant_id", "dataset_id", "status"}),
    "source_sync_runs": frozenset(
        {"id", "tenant_id", "dataset_id", "source_id", "status", "execution_state"}
    ),
}
ACTIVE_INDEX_OPERATION_STATUSES = frozenset({"pending", "retry", "retrying", "claimed", "running"})
ACTIVE_SOURCE_SYNC_STATUSES = frozenset({"pending", "running", "retrying"})
ACTIVE_SOURCE_EXECUTION_STATES = frozenset({"pending", "executing"})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DATABASE_RE = re.compile(r"^[A-Za-z0-9_$-]+$")
_MAX_SAFE_ARGUMENT_LENGTH = 4096


class SafetyGateError(RuntimeError):
    """An operation was stopped by an explicit safety gate."""


@dataclass(frozen=True)
class UrlSafetyReport:
    safe: bool
    label: str
    backend: str | None
    host: str | None
    database: str | None
    target_label: str | None
    has_password: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "label": self.label,
            "backend": self.backend,
            "host": self.host,
            "database": self.database,
            "target_label": self.target_label,
            "has_password": self.has_password,
            "reasons": list(self.reasons),
        }


def _single_line(value: str, field_name: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise SafetyGateError(f"{field_name} contains an unsafe control character")
    if len(value) > _MAX_SAFE_ARGUMENT_LENGTH:
        raise SafetyGateError(f"{field_name} is too long")
    return value


def _credential_free_label(url: URL) -> str:
    host = url.host or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{url.port}" if url.port else ""
    database = f"/{url.database}" if url.database else ""
    return f"{url.drivername}://{host}{port}{database}"


def inspect_url_safety(
    raw_url: str,
    *,
    target_label: str | None = None,
    require_mysql: bool = True,
    require_target_label: bool = True,
) -> UrlSafetyReport:
    """Parse a database URL and return a credential-free safety report."""

    raw_url = str(raw_url or "")
    if not raw_url.strip():
        return UrlSafetyReport(
            False,
            "<missing-url>",
            None,
            None,
            None,
            target_label,
            False,
            ("database URL is required",),
        )
    try:
        _single_line(raw_url, "database URL")
        url = make_url(raw_url)
    except Exception:
        return UrlSafetyReport(
            False,
            "<invalid-url>",
            None,
            None,
            None,
            target_label,
            False,
            ("database URL cannot be parsed",),
        )

    backend = url.get_backend_name()
    database = url.database
    reasons: list[str] = []
    if require_mysql and backend != "mysql":
        reasons.append("database backend must be MySQL")
    if require_mysql or backend != "sqlite":
        if not url.host:
            reasons.append("database host is required")
        if not database:
            reasons.append("database name is required")
        elif database.casefold() in SYSTEM_DATABASES:
            reasons.append("system database target is forbidden")
        elif not _DATABASE_RE.fullmatch(database):
            reasons.append("database name contains unsafe identifier characters")
    elif not database:
        reasons.append("SQLite database path is required")

    normalized_target = target_label.strip().casefold() if target_label else None
    if require_target_label and not normalized_target:
        reasons.append("target safety label is required")
    elif normalized_target and normalized_target not in TARGET_LABELS:
        reasons.append("target safety label is not recognized")

    return UrlSafetyReport(
        not reasons,
        _credential_free_label(url),
        backend,
        url.host,
        database,
        normalized_target,
        url.password is not None,
        tuple(reasons),
    )


def _script_directory(database_url: str = "sqlite://") -> ScriptDirectory:
    return ScriptDirectory.from_config(catalog_schema._alembic_config(database_url))


def _resolve_revision(script: ScriptDirectory, revision: str) -> str:
    if revision == "head":
        resolved = script.get_current_head()
        if not resolved:
            raise SafetyGateError("migration history has no head revision")
        return resolved
    item = script.get_revision(revision)
    if item is None:
        raise SafetyGateError(f"unknown migration revision: {revision}")
    return item.revision


def _migration_chain(
    script: ScriptDirectory,
    start_revision: str,
    target_revision: str,
) -> list[Any]:
    start = _resolve_revision(script, start_revision)
    target = _resolve_revision(script, target_revision)
    if start == target:
        return []
    chain: list[Any] = []
    current = script.get_revision(target)
    visited: set[str] = set()
    while current is not None and current.revision != start:
        if current.revision in visited:
            raise SafetyGateError("migration history contains a cycle")
        visited.add(current.revision)
        chain.append(current)
        parent = current.down_revision
        if isinstance(parent, tuple):
            raise SafetyGateError("migration path has multiple parents; manual review required")
        current = script.get_revision(parent) if parent else None
    if current is None or current.revision != start:
        raise SafetyGateError(f"migration target {target} is not a descendant of {start}")
    return list(reversed(chain))


def build_migration_plan(
    *,
    start_revision: str = "0007_chunk_rev",
    target_revision: str = "head",
    maintenance_window_minutes: int | None = None,
) -> dict[str, Any]:
    """Build a migration plan from local Alembic files only."""

    script = _script_directory()
    start = _resolve_revision(script, start_revision)
    target = _resolve_revision(script, target_revision)
    steps: list[dict[str, Any]] = []
    for order, item in enumerate(_migration_chain(script, start, target), start=1):
        parent = item.down_revision
        parent_value: str | list[str] | None
        if isinstance(parent, tuple):
            parent_value = list(parent)
        else:
            parent_value = parent
        summary = (item.doc or "").strip().splitlines()[0] if item.doc else "未提供迁移说明"
        steps.append(
            {
                "order": order,
                "revision": item.revision,
                "down_revision": parent_value,
                "file": Path(item.path).name,
                "summary": summary,
                "rollback_point": "人工核验本步 revision、应用健康和备份证据后再继续",
            }
        )
    window = (
        max(30, 15 + len(steps) * 8)
        if maintenance_window_minutes is None
        else maintenance_window_minutes
    )
    if window < 1:
        raise SafetyGateError("maintenance window must be positive")
    return {
        "from_revision": start,
        "to_revision": target,
        "head_revision": HEAD_REVISION,
        "maintenance_window_minutes": window,
        "steps": steps,
        "automatic_rollback": False,
        "manual_rollback_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _read_revision(engine: Engine) -> str | None:
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables:
        return None
    with engine.connect() as connection:
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
    return None if row is None else str(row[0])


def read_catalog_revision(engine: Engine) -> str | None:
    """Read the Alembic revision without modifying the database."""

    return _read_revision(engine)


_READ_ONLY_PROOF_ATTRIBUTE = "_rag4c_read_only_proof"


def _mark_read_only_preflight_engine(engine: Engine, *, dialect: str, mechanism: str) -> Engine:
    try:
        setattr(
            engine,
            _READ_ONLY_PROOF_ATTRIBUTE,
            {"dialect": dialect, "mechanism": mechanism, "guaranteed": True},
        )
    except (AttributeError, TypeError):
        # The preflight can still read through an immutable test double, but a
        # real non-SQLite engine must be created by this function with proof.
        pass
    return engine


def _preflight_engine_read_only_proven(engine: Engine) -> bool:
    """Require a physical read-only proof for injected non-SQLite engines."""

    backend = str(getattr(getattr(engine, "dialect", None), "name", "")).lower()
    if backend == "sqlite":
        # Existing SQLite tests deliberately inject a setup engine; internally
        # created SQLite preflight engines are still opened with mode=ro below.
        return True
    proof = getattr(engine, _READ_ONLY_PROOF_ATTRIBUTE, None)
    proof_dialect = proof.get("dialect") if isinstance(proof, Mapping) else getattr(proof, "dialect", None)
    proof_mechanism = (
        proof.get("mechanism") if isinstance(proof, Mapping) else getattr(proof, "mechanism", None)
    )
    guaranteed = (
        proof.get("guaranteed") if isinstance(proof, Mapping) else getattr(proof, "guaranteed", False)
    )
    expected_mechanism = (
        "postgresql-default-transaction-read-only"
        if backend == "postgresql"
        else "mysql-session-transaction-read-only"
        if backend in {"mysql", "mariadb"}
        else None
    )
    if guaranteed is not True or proof_dialect != backend or proof_mechanism != expected_mechanism:
        return False
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


def _create_read_only_preflight_engine(database_url: str) -> Engine:
    """Create the inspection engine used by preflight without SQLite file creation."""

    parsed = make_url(database_url)
    backend = str(parsed.get_backend_name()).lower()
    if backend == "postgresql":
        return _mark_read_only_preflight_engine(
            create_engine(
                database_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args={"options": "-c default_transaction_read_only=on"},
            ),
            dialect="postgresql",
            mechanism="postgresql-default-transaction-read-only",
        )
    if backend in {"mysql", "mariadb"}:
        return _mark_read_only_preflight_engine(
            create_engine(
                database_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args={"init_command": "SET SESSION TRANSACTION READ ONLY"},
            ),
            dialect=backend,
            mechanism="mysql-session-transaction-read-only",
        )
    if backend != "sqlite":
        raise SafetyGateError(
            f"non-SQLite preflight cannot prove physical read-only access for dialect {backend}"
        )

    raw_database = str(parsed.database or "")
    if not raw_database or raw_database == ":memory:":
        raise SafetyGateError("SQLite preflight requires an existing file; in-memory databases are not physically read-only")
    if raw_database.startswith("file:"):
        raw_database = raw_database.removeprefix("file:").split("?", 1)[0]
    database_path = Path(raw_database).expanduser()
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise SafetyGateError(f"SQLite preflight database does not exist: {database_path}")

    sqlite_uri = f"sqlite:///file:{database_path.as_posix()}?mode=ro&uri=true"
    return _mark_read_only_preflight_engine(
        create_engine(
            sqlite_uri,
            connect_args={"uri": True},
            pool_pre_ping=True,
        ),
        dialect="sqlite",
        mechanism="sqlite-uri-mode-ro",
    )


def _schema_status(
    tables: set[str], revision: str | None, head_revision: str
) -> tuple[str, list[str], list[str]]:
    required_head = set(HEAD_CATALOG_TABLES) | {"alembic_version"}
    missing_head = sorted(required_head - tables)
    missing_baseline = sorted(set(BASELINE_CATALOG_TABLES) - tables)
    if not tables:
        return "empty", missing_head, missing_baseline
    if revision is None:
        return "unstamped", missing_head, missing_baseline
    if revision == head_revision:
        return ("incomplete" if missing_head else "current"), missing_head, missing_baseline
    if revision in {item.revision for item in _script_directory().walk_revisions()}:
        return "behind", missing_head, missing_baseline
    return "future", missing_head, missing_baseline


def _role_owner_report(engine: Engine, tables: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    roles: dict[str, Any] = {
        "checked": False,
        "known": sorted(KNOWN_ROLES),
        "counts": {},
        "unknown": [],
    }
    owners: dict[str, Any] = {
        "checked": False,
        "active_tenants": 0,
        "ownerless_active_tenants": 0,
    }
    if "tenant_members" not in tables:
        return roles, owners

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT role, COUNT(*) AS role_count FROM tenant_members GROUP BY role")
        ).all()
    counts: dict[str, int] = {}
    for raw_role, raw_count in rows:
        role = str(raw_role or "").strip().casefold()
        counts[role] = counts.get(role, 0) + int(raw_count)
    roles.update(
        {
            "checked": True,
            "counts": dict(sorted(counts.items())),
            "unknown": sorted(role for role in counts if role not in KNOWN_ROLES),
        }
    )
    if "tenants" not in tables:
        return roles, owners

    tenant_columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
    has_status = "status" in tenant_columns
    query = "SELECT id" + (", status" if has_status else "") + " FROM tenants"
    with engine.connect() as connection:
        tenant_rows = connection.execute(text(query)).all()
        active_count = 0
        ownerless_count = 0
        for row in tenant_rows:
            status = str(row[1]).strip().casefold() if has_status else "active"
            if has_status and status != "active":
                continue
            active_count += 1
            count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM tenant_members "
                    "WHERE tenant_id = :tenant_id AND LOWER(TRIM(role)) = 'owner'"
                ),
                {"tenant_id": row[0]},
            ).scalar_one()
            ownerless_count += int(count == 0)
    owners.update(
        {
            "checked": True,
            "active_tenants": active_count,
            "ownerless_active_tenants": ownerless_count,
        }
    )
    return roles, owners


def _invitation_preflight_report(engine: Engine, tables: set[str]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checked": False,
        "duplicate_pending": [],
        "duplicate_token_hashes": [],
    }
    if "tenant_invitations" not in tables:
        return report
    columns = {column["name"] for column in inspect(engine).get_columns("tenant_invitations")}
    required = {"tenant_id", "normalized_email", "status", "token_hash"}
    if not required <= columns:
        return report
    with engine.connect() as connection:
        pending_rows = connection.execute(
            text(
                "SELECT tenant_id, normalized_email, COUNT(*) AS invitation_count "
                "FROM tenant_invitations WHERE status='pending' "
                "GROUP BY tenant_id, normalized_email HAVING COUNT(*) > 1 "
                "ORDER BY tenant_id, normalized_email LIMIT 20"
            )
        ).all()
        token_rows = connection.execute(
            text(
                "SELECT tenant_id, token_hash, COUNT(*) AS token_count "
                "FROM tenant_invitations GROUP BY tenant_id, token_hash "
                "HAVING COUNT(*) > 1 ORDER BY tenant_id, token_hash LIMIT 20"
            )
        ).all()
    report.update(
        {
            "checked": True,
            "duplicate_pending": [
                {
                    "tenant_id": str(row[0]),
                    "normalized_email": str(row[1]),
                    "count": int(row[2]),
                }
                for row in pending_rows
            ],
            "duplicate_token_hashes": [
                {"tenant_id": str(row[0]), "token_hash": str(row[1]), "count": int(row[2])}
                for row in token_rows
            ],
        }
    )
    return report


def check_disk_target(path: str | Path, *, min_free_bytes: int = 0) -> dict[str, Any]:
    """Inspect a target directory without creating it or a probe file."""

    if min_free_bytes < 0:
        raise SafetyGateError("min_free_bytes cannot be negative")
    target = Path(path).expanduser()
    _single_line(str(target), "disk target")
    exists = target.exists()
    is_dir = target.is_dir() if exists else False
    parent = target if is_dir else target.parent
    parent_exists = parent.exists()
    parent_is_dir = parent.is_dir() if parent_exists else False
    writable = bool(parent_exists and parent_is_dir and os.access(parent, os.W_OK))
    free_bytes: int | None = None
    if parent_is_dir:
        try:
            free_bytes = int(shutil.disk_usage(parent).free)
        except OSError:
            free_bytes = None
    reasons: list[str] = []
    if not parent_exists:
        reasons.append("target parent does not exist")
    elif not parent_is_dir:
        reasons.append("target parent is not a directory")
    if exists and not is_dir:
        reasons.append("target exists but is not a directory")
    if not writable:
        reasons.append("target parent is not writable")
    if free_bytes is None:
        reasons.append("free disk space could not be checked")
    elif free_bytes < min_free_bytes:
        reasons.append("free disk space is below the configured minimum")
    return {
        "path": str(target),
        "target_exists": exists,
        "target_is_directory": is_dir,
        "parent": str(parent),
        "parent_exists": parent_exists,
        "writable": writable,
        "free_bytes": free_bytes,
        "min_free_bytes": min_free_bytes,
        "safe": not reasons,
        "reasons": reasons,
    }


def _knowledge_base_registry_preflight_report(engine: Engine, tables: set[str]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checked": False,
        "dataset_count": None,
        "primary_ownership_candidate_count": None,
        "missing_primary_dataset_ids": [],
        "ambiguous_primary_dataset_ids": [],
        "active_shared_association_count": None,
        "ownership_count": None,
        "missing_ownership_dataset_ids": [],
        "ownership_primary_mismatch_dataset_ids": [],
        "app_count": None,
        "active_application_reference_count": None,
        "orphan_application_reference_ids": [],
        "dataset_workspace_transfer_approval_count": None,
        "blockers": [],
    }
    required = {"datasets", "tenant_workspace_datasets"}
    if not required <= tables:
        return report
    report["checked"] = True
    with engine.connect() as connection:
        dataset_keys = [
            (str(row["tenant_id"]), str(row["id"]))
            for row in connection.execute(
                text("SELECT tenant_id, id FROM datasets ORDER BY tenant_id, id")
            ).mappings()
        ]
        primary_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, COUNT(*) AS count "
                    "FROM tenant_workspace_datasets "
                    "WHERE status='active' AND binding_kind='primary' "
                    "AND active_primary_slot='primary' "
                    "GROUP BY tenant_id, dataset_id ORDER BY tenant_id, dataset_id"
                )
            ).mappings()
        )
        primary_counts = {
            (str(row["tenant_id"]), str(row["dataset_id"])): int(row["count"])
            for row in primary_rows
        }
        missing_primary = [
            f"{tenant_id}:{dataset_id}"
            for tenant_id, dataset_id in dataset_keys
            if primary_counts.get((tenant_id, dataset_id), 0) == 0
        ]
        ambiguous_primary = [
            f"{tenant_id}:{dataset_id}"
            for (tenant_id, dataset_id), count in primary_counts.items()
            if count != 1
        ]
        report.update(
            dataset_count=len(dataset_keys),
            primary_ownership_candidate_count=sum(
                1 for count in primary_counts.values() if count == 1
            ),
            missing_primary_dataset_ids=missing_primary[:20],
            ambiguous_primary_dataset_ids=ambiguous_primary[:20],
            active_shared_association_count=int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_workspace_datasets "
                        "WHERE status='active' AND binding_kind='shared'"
                    )
                ).scalar_one()
            ),
        )
        if "dataset_workspace_ownerships" in tables:
            ownership_rows = list(
                connection.execute(
                    text(
                        "SELECT o.tenant_id, o.dataset_id, o.workspace_id, "
                        "b.workspace_id AS primary_workspace_id "
                        "FROM dataset_workspace_ownerships o "
                        "LEFT JOIN tenant_workspace_datasets b ON b.tenant_id=o.tenant_id "
                        "AND b.dataset_id=o.dataset_id AND b.status='active' "
                        "AND b.binding_kind='primary' AND b.active_primary_slot='primary'"
                    )
                ).mappings()
            )
            ownership_by_dataset = {
                (str(row["tenant_id"]), str(row["dataset_id"])): row for row in ownership_rows
            }
            missing_ownership = [
                f"{tenant_id}:{dataset_id}"
                for tenant_id, dataset_id in dataset_keys
                if (tenant_id, dataset_id) not in ownership_by_dataset
            ]
            mismatched = [
                f"{row['tenant_id']}:{row['dataset_id']}"
                for row in ownership_rows
                if row["primary_workspace_id"] is None
                or str(row["workspace_id"]) != str(row["primary_workspace_id"])
            ]
            report.update(
                ownership_count=len(ownership_rows),
                missing_ownership_dataset_ids=missing_ownership[:20],
                ownership_primary_mismatch_dataset_ids=sorted(set(mismatched))[:20],
            )
        if "apps" in tables:
            report["app_count"] = int(
                connection.execute(text("SELECT COUNT(*) FROM apps")).scalar_one()
            )
        if "app_dataset_references" in tables:
            report["active_application_reference_count"] = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM app_dataset_references "
                        "WHERE status='active' AND active_slot='active'"
                    )
                ).scalar_one()
            )
            if {"apps", "datasets"} <= tables:
                report["orphan_application_reference_ids"] = [
                    str(value)
                    for value in connection.execute(
                        text(
                            "SELECT r.id FROM app_dataset_references r "
                            "LEFT JOIN apps a ON a.tenant_id=r.tenant_id AND a.id=r.app_id "
                            "LEFT JOIN datasets d ON d.tenant_id=r.tenant_id AND d.id=r.dataset_id "
                            "WHERE a.id IS NULL OR d.id IS NULL ORDER BY r.id LIMIT 20"
                        )
                    ).scalars()
                ]
        if "tenant_approval_policies" in tables:
            report["dataset_workspace_transfer_approval_count"] = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM tenant_approval_policies "
                        "WHERE action_type='dataset_workspace_transfer'"
                    )
                ).scalar_one()
            )
    blockers: list[str] = []
    for code, field in (
        ("missing_primary_ownership", "missing_primary_dataset_ids"),
        ("ambiguous_primary_ownership", "ambiguous_primary_dataset_ids"),
        ("missing_registry_ownership", "missing_ownership_dataset_ids"),
        ("ownership_primary_mismatch", "ownership_primary_mismatch_dataset_ids"),
        ("orphan_application_reference", "orphan_application_reference_ids"),
    ):
        if report[field]:
            blockers.append(code)
    report["blockers"] = blockers
    return report


def _scoped_key(*values: Any) -> str:
    return ":".join(str(value) for value in values)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _safe_integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _database_boolean(value: Any) -> bool:
    return value is True or value == 1 or str(value).strip().casefold() in {"1", "true", "yes"}


def _empty_release_readiness() -> dict[str, Any]:
    return {
        "checked": False,
        "ready_dataset_count": 0,
        "blocked_dataset_count": 0,
        "unavailable_dataset_count": 0,
        "not_ready_dataset_count": 0,
        "ready_dataset_ids": [],
        "blocked_dataset_ids": [],
        "unavailable_dataset_ids": [],
        "missing_dataset_ids": [],
        "ambiguous_dataset_ids": [],
        "invalid_workspace_dataset_ids": [],
        "missing_document_ids": [],
        "invalid_document_ids": [],
        "drift_document_ids": [],
        "active_operation_ids": [],
        "active_run_ids": [],
        "missing_schema": {},
    }


def _empty_knowledge_base_releases_preflight_report() -> dict[str, Any]:
    return {
        "checked": False,
        "available": False,
        "schema_status": "not_available",
        "target_revision": STAGE19_TARGET_REVISION,
        "missing_tables": [],
        "missing_columns": {},
        "active_dataset_count": None,
        "active_dataset_ids": [],
        "tenant_summaries": [],
        "readiness": {
            "ownership": _empty_release_readiness(),
            "version": _empty_release_readiness(),
            "index": _empty_release_readiness(),
            "source_sync": _empty_release_readiness(),
            "projection": _empty_release_readiness(),
        },
        "readiness_schema_missing": {},
        "release_count": None,
        "release_entry_count": None,
        "release_event_count": None,
        "channel_count": None,
        "active_channel_count": None,
        "default_channel_count": None,
        "default_serving_channel_count": None,
        "custom_channel_count": None,
        "channel_binding_count": None,
        "active_channel_binding_count": None,
        "removed_channel_binding_count": None,
        "application_count": None,
        "application_reference_count": None,
        "active_application_reference_count": None,
        "release_mode_counts_by_tenant": {},
        "custom_channels": [],
        "default_channels": [],
        "missing_default_channels": [],
        "malformed_default_channel_ids": [],
        "duplicate_channel_code_ids": [],
        "malformed_release_ids": [],
        "release_entry_count_mismatch_ids": [],
        "serving_release_missing_dataset_ids": [],
        "release_not_ready_dataset_ids": [],
        "malformed_channel_binding_ids": [],
        "malformed_app_release_mode_count": None,
        "malformed_app_release_mode_ids": [],
        "malformed_app_release_mode_values": [],
        "malformed_app_release_pin_count": None,
        "malformed_app_release_pin_ids": [],
        "pinned_release_not_ready_ids": [],
        "blockers": [],
        "reasons": [],
    }


def _knowledge_base_releases_preflight_report(
    engine: Engine,
    tables: set[str],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    report = _empty_knowledge_base_releases_preflight_report()
    present_authority_tables = set(RELEASE_AUTHORITY_TABLES) & tables
    if revision not in {None, STAGE19_TARGET_REVISION} and not present_authority_tables:
        return report
    if revision is None and not present_authority_tables:
        return report

    required_tables = set(RELEASE_AUTHORITY_TABLES) | {"app_dataset_references", "datasets"}
    missing_tables = sorted(required_tables - tables)
    missing_columns: dict[str, list[str]] = {}
    columns_by_table: dict[str, set[str]] = {}
    for table_name, required_columns in RELEASE_SCHEMA_REQUIRED_COLUMNS.items():
        if table_name not in tables:
            continue
        actual_columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
        columns_by_table[table_name] = actual_columns
        missing = sorted(required_columns - actual_columns)
        if missing:
            missing_columns[table_name] = missing

    report.update(
        {
            "checked": True,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
        }
    )
    if missing_tables or missing_columns:
        report.update(
            {
                "available": False,
                "schema_status": "partial",
                "blockers": ["release_schema_incomplete"],
                "reasons": ["0029 release authority schema is incomplete"],
            }
        )
        return report

    report.update(
        {
            "available": True,
            "schema_status": (
                "current"
                if revision in {None, STAGE19_TARGET_REVISION}
                else "present_before_revision"
            ),
        }
    )

    readiness_missing: dict[str, list[str]] = {}
    for table_name, required_columns in RELEASE_READINESS_REQUIRED_COLUMNS.items():
        if table_name not in tables:
            readiness_missing[table_name] = sorted(required_columns)
            continue
        actual_columns = columns_by_table.get(table_name)
        if actual_columns is None:
            actual_columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
        missing = sorted(required_columns - actual_columns)
        if missing:
            readiness_missing[table_name] = missing

    readiness_table_map = {
        "ownership": {"tenant_workspaces", "dataset_workspace_ownerships"},
        "version": {"documents", "document_versions"},
        "index": {"documents", "index_operations"},
        "source_sync": {"data_sources", "source_sync_runs"},
        "projection": {"documents"},
    }

    with engine.connect() as connection:
        active_dataset_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, id, graph_enabled, serving_release_id "
                    "FROM datasets WHERE status='active' ORDER BY tenant_id, id"
                )
            ).mappings()
        )
        active_dataset_keys = [
            (str(row["tenant_id"]), str(row["id"])) for row in active_dataset_rows
        ]
        active_dataset_key_set = set(active_dataset_keys)
        report["active_dataset_count"] = len(active_dataset_keys)
        report["active_dataset_ids"] = [
            _scoped_key(tenant_id, dataset_id) for tenant_id, dataset_id in active_dataset_keys
        ]

        tenant_ids = {tenant_id for tenant_id, _dataset_id in active_dataset_keys}
        if "tenants" in tables:
            tenant_columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
            tenant_query = (
                "SELECT id" + (", status" if "status" in tenant_columns else "") + " FROM tenants"
            )
            tenant_rows = connection.execute(text(tenant_query)).all()
            for row in tenant_rows:
                if len(row) == 1 or str(row[1]).strip().casefold() == "active":
                    tenant_ids.add(str(row[0]))

        def new_tenant_summary(tenant_id: str) -> dict[str, Any]:
            return {
                "tenant_id": tenant_id,
                "active_dataset_count": 0,
                "release_count": 0,
                "channel_count": 0,
                "active_application_reference_count": 0,
            }

        tenant_summaries = {
            tenant_id: new_tenant_summary(tenant_id) for tenant_id in sorted(tenant_ids)
        }
        for tenant_id, _dataset_id in active_dataset_keys:
            tenant_summaries.setdefault(tenant_id, new_tenant_summary(tenant_id))[
                "active_dataset_count"
            ] += 1

        dataset_state: dict[tuple[str, str], dict[str, Any]] = {
            key: {
                "ownership": True,
                "version": True,
                "index": True,
                "source_sync": True,
                "projection": True,
                "active_document_ids": [],
                "active_source_ids": [],
            }
            for key in active_dataset_keys
        }

        def append_scoped(field: str, value: str) -> None:
            _append_unique(report[field], value)

        def grouped_count(
            table_name: str, predicate: str | None = None
        ) -> tuple[int, dict[str, int]]:
            statement = f"SELECT tenant_id, COUNT(*) AS item_count FROM {table_name}"
            if predicate:
                statement += f" WHERE {predicate}"
            statement += " GROUP BY tenant_id ORDER BY tenant_id"
            rows = connection.execute(text(statement)).mappings()
            by_tenant = {str(row["tenant_id"]): int(row["item_count"]) for row in rows}
            return sum(by_tenant.values()), by_tenant

        count_specs = (
            ("release_count", "dataset_release_manifests", None),
            ("release_entry_count", "dataset_release_entries", None),
            ("release_event_count", "dataset_release_events", None),
            ("channel_count", "tenant_release_channels", None),
            ("active_channel_count", "tenant_release_channels", "status='active'"),
            (
                "default_channel_count",
                "tenant_release_channels",
                "LOWER(TRIM(code)) IN ('development','testing','production')",
            ),
            (
                "default_serving_channel_count",
                "tenant_release_channels",
                "active_default_slot='default' AND status='active'",
            ),
            (
                "custom_channel_count",
                "tenant_release_channels",
                "LOWER(TRIM(code)) NOT IN ('development','testing','production')",
            ),
            ("channel_binding_count", "dataset_channel_releases", None),
            ("active_channel_binding_count", "dataset_channel_releases", "status='active'"),
            ("removed_channel_binding_count", "dataset_channel_releases", "status='removed'"),
            ("application_reference_count", "app_dataset_references", None),
            (
                "active_application_reference_count",
                "app_dataset_references",
                "status='active' AND active_slot='active'",
            ),
        )
        grouped_counts: dict[str, dict[str, int]] = {}
        for field, table_name, predicate in count_specs:
            total, by_tenant = grouped_count(table_name, predicate)
            report[field] = total
            grouped_counts[field] = by_tenant

        if "apps" in tables:
            application_total, application_by_tenant = grouped_count("apps")
            report["application_count"] = application_total
            for tenant_id in application_by_tenant:
                tenant_summaries.setdefault(tenant_id, new_tenant_summary(tenant_id))

        for tenant_counts in grouped_counts.values():
            for tenant_id in tenant_counts:
                tenant_summaries.setdefault(tenant_id, new_tenant_summary(tenant_id))
        for tenant_id, summary in tenant_summaries.items():
            summary["release_count"] = grouped_counts["release_count"].get(tenant_id, 0)
            summary["channel_count"] = grouped_counts["channel_count"].get(tenant_id, 0)
            summary["active_application_reference_count"] = grouped_counts[
                "active_application_reference_count"
            ].get(tenant_id, 0)
        report["tenant_summaries"] = [tenant_summaries[key] for key in sorted(tenant_summaries)]

        channel_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, id, code, normalized_code, status, is_default_serving, "
                    "active_default_slot, revision FROM tenant_release_channels "
                    "ORDER BY tenant_id, normalized_code, id"
                )
            ).mappings()
        )
        channel_by_scope_id = {(str(row["tenant_id"]), str(row["id"])): row for row in channel_rows}
        channel_codes: dict[str, set[str]] = {}
        normalized_code_rows: dict[tuple[str, str], list[Any]] = {}
        default_serving_rows: dict[str, list[Any]] = {}
        for row in channel_rows:
            tenant_id = str(row["tenant_id"])
            code = str(row["code"]).strip().casefold()
            if str(row["status"]).strip().casefold() == "active":
                channel_codes.setdefault(tenant_id, set()).add(code)
            normalized = str(row["normalized_code"]).strip().casefold()
            normalized_code_rows.setdefault((tenant_id, normalized), []).append(row)
            if (
                str(row["status"]).strip().casefold() == "active"
                and str(row["active_default_slot"] or "").strip().casefold() == "default"
            ):
                default_serving_rows.setdefault(tenant_id, []).append(row)
            if (
                str(row["status"]).strip().casefold() == "active"
                and code in DEFAULT_RELEASE_CHANNEL_CODES
            ):
                report["default_channels"].append({"tenant_id": tenant_id, "code": code})
            elif str(row["status"]).strip().casefold() == "active":
                report["custom_channels"].append({"tenant_id": tenant_id, "code": code})
            if (
                str(row["active_default_slot"] or "").strip().casefold() == "default"
                and not _database_boolean(row["is_default_serving"])
            ) or (
                _database_boolean(row["is_default_serving"])
                and str(row["active_default_slot"] or "").strip().casefold() != "default"
            ):
                append_scoped("malformed_default_channel_ids", _scoped_key(tenant_id, row["id"]))

        for (tenant_id, _normalized_code), rows in normalized_code_rows.items():
            if len(rows) > 1:
                for row in rows:
                    append_scoped("duplicate_channel_code_ids", _scoped_key(tenant_id, row["id"]))
        for tenant_id, rows in default_serving_rows.items():
            if len(rows) > 1:
                for row in rows:
                    append_scoped(
                        "malformed_default_channel_ids", _scoped_key(tenant_id, row["id"])
                    )

        for tenant_id in sorted(tenant_ids):
            missing_codes = sorted(
                set(DEFAULT_RELEASE_CHANNEL_CODES) - channel_codes.get(tenant_id, set())
            )
            if missing_codes:
                report["missing_default_channels"].append(
                    {"tenant_id": tenant_id, "missing_codes": missing_codes}
                )

        report["custom_channels"].sort(key=lambda item: (item["tenant_id"], item["code"]))
        report["default_channels"].sort(key=lambda item: (item["tenant_id"], item["code"]))

        manifest_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, id, readiness_state, entry_count, blocker_count, "
                    "release_number, profile_revision, ownership_revision, workspace_revision, "
                    "mutation_generation, serving_generation FROM dataset_release_manifests "
                    "ORDER BY tenant_id, dataset_id, release_number, id"
                )
            ).mappings()
        )
        manifests_by_scope_id = {
            (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"])): row
            for row in manifest_rows
        }
        valid_release_states = {"ready", "blocked", "unavailable"}
        for row in manifest_rows:
            scope_id = _scoped_key(row["tenant_id"], row["dataset_id"], row["id"])
            if str(row["readiness_state"]).strip().casefold() not in valid_release_states:
                append_scoped("malformed_release_ids", scope_id)

        entry_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, release_id FROM dataset_release_entries "
                    "ORDER BY tenant_id, dataset_id, release_id, id"
                )
            ).mappings()
        )
        entry_counts: dict[tuple[str, str, str], int] = {}
        for row in entry_rows:
            key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["release_id"]))
            entry_counts[key] = entry_counts.get(key, 0) + 1
        for key, manifest in manifests_by_scope_id.items():
            if _safe_integer(manifest["entry_count"], -1) != entry_counts.get(key, 0):
                append_scoped("release_entry_count_mismatch_ids", _scoped_key(*key))

        for row in active_dataset_rows:
            key = (str(row["tenant_id"]), str(row["id"]))
            serving_release_id = str(row["serving_release_id"] or "").strip()
            if not serving_release_id:
                continue
            manifest = manifests_by_scope_id.get((*key, serving_release_id))
            if manifest is None:
                append_scoped("serving_release_missing_dataset_ids", _scoped_key(*key))
            elif str(manifest["readiness_state"]).strip().casefold() != "ready":
                append_scoped("release_not_ready_dataset_ids", _scoped_key(*key))

        binding_rows = list(
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, id, channel_id, status, active_release_id "
                    "FROM dataset_channel_releases ORDER BY tenant_id, dataset_id, id"
                )
            ).mappings()
        )
        binding_invalid_ids: list[str] = []
        for row in binding_rows:
            if str(row["status"]).strip().casefold() != "active":
                continue
            tenant_id = str(row["tenant_id"])
            dataset_id = str(row["dataset_id"])
            binding_id = _scoped_key(tenant_id, dataset_id, row["id"])
            channel = channel_by_scope_id.get((tenant_id, str(row["channel_id"])))
            release = manifests_by_scope_id.get(
                (tenant_id, dataset_id, str(row["active_release_id"]))
            )
            invalid = channel is None or str(channel["status"]).strip().casefold() != "active"
            invalid = invalid or release is None
            if invalid:
                binding_invalid_ids.append(binding_id)
            elif str(release["readiness_state"]).strip().casefold() != "ready":
                append_scoped("release_not_ready_dataset_ids", _scoped_key(tenant_id, dataset_id))
        report["malformed_channel_binding_ids"] = sorted(set(binding_invalid_ids))

        if "app_dataset_references" in tables:
            reference_rows = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, id, app_id, dataset_id, status, active_slot, release_mode, "
                        "release_channel_id, pinned_release_id FROM app_dataset_references "
                        "ORDER BY tenant_id, id"
                    )
                ).mappings()
            )
            release_mode_counts: dict[str, dict[str, int]] = {}
            malformed_mode_values: set[str] = set()
            malformed_mode_ids: list[str] = []
            malformed_pin_ids: list[str] = []
            pinned_not_ready_ids: list[str] = []
            for row in reference_rows:
                if (
                    str(row["status"]).strip().casefold() != "active"
                    or str(row["active_slot"]).strip().casefold() != "active"
                ):
                    continue
                tenant_id = str(row["tenant_id"])
                mode = str(row["release_mode"] or "").strip().casefold()
                tenant_modes = release_mode_counts.setdefault(tenant_id, {})
                tenant_modes[mode] = tenant_modes.get(mode, 0) + 1
                reference_id = _scoped_key(tenant_id, row["id"])
                channel_id = str(row["release_channel_id"] or "").strip()
                pinned_release_id = str(row["pinned_release_id"] or "").strip()
                if mode not in {"follow_channel", "pinned"}:
                    malformed_mode_ids.append(reference_id)
                    malformed_mode_values.add(mode or "<missing>")
                    continue
                if mode == "follow_channel":
                    channel = (
                        channel_by_scope_id.get((tenant_id, channel_id)) if channel_id else None
                    )
                    if (
                        not channel_id
                        or pinned_release_id
                        or channel is None
                        or str(channel["status"]).strip().casefold() != "active"
                    ):
                        malformed_pin_ids.append(reference_id)
                    continue
                release = (
                    manifests_by_scope_id.get(
                        (tenant_id, str(row["dataset_id"]), pinned_release_id)
                    )
                    if pinned_release_id
                    else None
                )
                if channel_id or not pinned_release_id or release is None:
                    malformed_pin_ids.append(reference_id)
                elif str(release["readiness_state"]).strip().casefold() != "ready":
                    pinned_not_ready_ids.append(reference_id)

            report["release_mode_counts_by_tenant"] = {
                tenant_id: dict(sorted(counts.items()))
                for tenant_id, counts in sorted(release_mode_counts.items())
            }
            report["malformed_app_release_mode_ids"] = sorted(malformed_mode_ids)
            report["malformed_app_release_mode_values"] = sorted(malformed_mode_values)
            report["malformed_app_release_pin_ids"] = sorted(malformed_pin_ids)
            report["pinned_release_not_ready_ids"] = sorted(pinned_not_ready_ids)
            report["malformed_app_release_mode_count"] = len(malformed_mode_ids)
            report["malformed_app_release_pin_count"] = len(malformed_pin_ids)

        category_missing: dict[str, dict[str, list[str]]] = {}
        for category, required_category_tables in readiness_table_map.items():
            category_missing_tables = {
                table_name: readiness_missing[table_name]
                for table_name in sorted(required_category_tables)
                if table_name in readiness_missing
            }
            bucket = report["readiness"][category]
            if category_missing_tables:
                category_missing[category] = category_missing_tables
                bucket.update(
                    {
                        "checked": False,
                        "unavailable_dataset_count": len(active_dataset_keys),
                        "not_ready_dataset_count": len(active_dataset_keys),
                        "unavailable_dataset_ids": [
                            _scoped_key(*key) for key in active_dataset_keys
                        ],
                        "missing_schema": category_missing_tables,
                    }
                )
                for key in active_dataset_keys:
                    dataset_state[key][category] = None
            else:
                bucket["checked"] = True
        report["readiness_schema_missing"] = category_missing

        ownership_bucket = report["readiness"]["ownership"]
        if ownership_bucket["checked"]:
            ownership_rows = list(
                connection.execute(
                    text(
                        "SELECT o.tenant_id, o.dataset_id, o.workspace_id, o.revision, "
                        "w.id AS joined_workspace_id, w.status AS workspace_status, "
                        "w.revision AS workspace_revision FROM dataset_workspace_ownerships o "
                        "LEFT JOIN tenant_workspaces w ON w.tenant_id=o.tenant_id "
                        "AND w.id=o.workspace_id ORDER BY o.tenant_id, o.dataset_id, o.workspace_id"
                    )
                ).mappings()
            )
            ownership_by_dataset: dict[tuple[str, str], list[Any]] = {}
            for row in ownership_rows:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                ownership_by_dataset.setdefault(key, []).append(row)
            for key in active_dataset_keys:
                rows = ownership_by_dataset.get(key, [])
                scoped_dataset = _scoped_key(*key)
                if not rows:
                    dataset_state[key]["ownership"] = False
                    ownership_bucket["missing_dataset_ids"].append(scoped_dataset)
                elif len(rows) > 1:
                    dataset_state[key]["ownership"] = False
                    ownership_bucket["ambiguous_dataset_ids"].append(scoped_dataset)
                else:
                    row = rows[0]
                    if (
                        _safe_integer(row["revision"], 0) <= 0
                        or row["joined_workspace_id"] is None
                        or str(row["workspace_status"]).strip().casefold() != "active"
                        or _safe_integer(row["workspace_revision"], 0) <= 0
                    ):
                        dataset_state[key]["ownership"] = False
                        ownership_bucket["invalid_workspace_dataset_ids"].append(scoped_dataset)

        active_documents: list[Any] = []
        active_document_keys: set[tuple[str, str, str]] = set()
        version_bucket = report["readiness"]["version"]
        if version_bucket["checked"]:
            all_active_documents = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, dataset_id, id, current_version_id, "
                        "desired_index_revision, indexed_revision, graph_revision "
                        "FROM documents WHERE lifecycle_state='active' AND retrieval_enabled "
                        "ORDER BY tenant_id, dataset_id, id"
                    )
                ).mappings()
            )
            active_documents = [
                row
                for row in all_active_documents
                if (str(row["tenant_id"]), str(row["dataset_id"])) in active_dataset_key_set
            ]
            for row in active_documents:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                if key not in active_dataset_key_set:
                    continue
                document_id = str(row["id"])
                active_document_keys.add((*key, document_id))
                dataset_state[key]["active_document_ids"].append(document_id)
            version_rows = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, dataset_id, document_id, id, revision, source_hash "
                        "FROM document_versions ORDER BY tenant_id, dataset_id, document_id, id"
                    )
                ).mappings()
            )
            version_by_scope_id = {
                (
                    str(row["tenant_id"]),
                    str(row["dataset_id"]),
                    str(row["document_id"]),
                    str(row["id"]),
                ): row
                for row in version_rows
            }
            version_ready_documents = 0
            for row in active_documents:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                if key not in active_dataset_key_set:
                    continue
                document_id = str(row["id"])
                scoped_document = _scoped_key(*key, document_id)
                current_version_id = str(row["current_version_id"] or "").strip()
                version = version_by_scope_id.get((*key, document_id, current_version_id))
                if not current_version_id or version is None:
                    dataset_state[key]["version"] = False
                    version_bucket["missing_document_ids"].append(scoped_document)
                elif _safe_integer(version["revision"], 0) <= 0 or not _SHA256_RE.fullmatch(
                    str(version["source_hash"] or "").strip().casefold()
                ):
                    dataset_state[key]["version"] = False
                    version_bucket["invalid_document_ids"].append(scoped_document)
                else:
                    version_ready_documents += 1
            version_bucket["active_document_count"] = len(active_documents)
            version_bucket["version_ready_document_count"] = version_ready_documents
            version_bucket["missing_document_version_count"] = len(
                version_bucket["missing_document_ids"]
            )
            version_bucket["invalid_document_version_count"] = len(
                version_bucket["invalid_document_ids"]
            )

        index_bucket = report["readiness"]["index"]
        projection_bucket = report["readiness"]["projection"]
        if index_bucket["checked"] and active_documents:
            graph_required_by_dataset = {
                (str(row["tenant_id"]), str(row["id"])): _database_boolean(row["graph_enabled"])
                for row in active_dataset_rows
            }
            for row in active_documents:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                if key not in active_dataset_key_set:
                    continue
                document_id = str(row["id"])
                scoped_document = _scoped_key(*key, document_id)
                desired_revision = _safe_integer(row["desired_index_revision"], 0)
                indexed_revision = _safe_integer(row["indexed_revision"], 0)
                graph_required = graph_required_by_dataset.get(key, False)
                if desired_revision != indexed_revision:
                    dataset_state[key]["index"] = False
                    index_bucket["drift_document_ids"].append(scoped_document)
                if desired_revision != indexed_revision or (
                    graph_required and _safe_integer(row["graph_revision"], 0) != desired_revision
                ):
                    dataset_state[key]["projection"] = False
                    projection_bucket["drift_document_ids"].append(scoped_document)

            operation_rows = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, dataset_id, document_id, id, status FROM index_operations "
                        "ORDER BY tenant_id, dataset_id, document_id, id"
                    )
                ).mappings()
            )
            active_document_ids_by_dataset = {
                key: set(state["active_document_ids"]) for key, state in dataset_state.items()
            }
            for row in operation_rows:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                if (
                    key not in active_dataset_key_set
                    or str(row["document_id"]) not in active_document_ids_by_dataset[key]
                ):
                    continue
                if str(row["status"]).strip().casefold() in ACTIVE_INDEX_OPERATION_STATUSES:
                    dataset_state[key]["index"] = False
                    dataset_state[key]["projection"] = False
                    index_bucket["active_operation_ids"].append(
                        _scoped_key(*key, row["document_id"], row["id"])
                    )
            index_bucket["drift_document_ids"] = sorted(set(index_bucket["drift_document_ids"]))
            projection_bucket["drift_document_ids"] = sorted(
                set(projection_bucket["drift_document_ids"])
            )

        source_bucket = report["readiness"]["source_sync"]
        if source_bucket["checked"]:
            source_rows = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, dataset_id, id, status FROM data_sources "
                        "WHERE status='active' ORDER BY tenant_id, dataset_id, id"
                    )
                ).mappings()
            )
            active_source_keys: set[tuple[str, str, str]] = set()
            for row in source_rows:
                key = (str(row["tenant_id"]), str(row["dataset_id"]))
                if key in active_dataset_key_set:
                    source_key = (*key, str(row["id"]))
                    active_source_keys.add(source_key)
                    dataset_state[key]["active_source_ids"].append(str(row["id"]))
            run_rows = list(
                connection.execute(
                    text(
                        "SELECT tenant_id, dataset_id, source_id, id, status, execution_state "
                        "FROM source_sync_runs ORDER BY tenant_id, dataset_id, source_id, id"
                    )
                ).mappings()
            )
            source_bucket["active_source_count"] = len(active_source_keys)
            for row in run_rows:
                key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["source_id"]))
                if key not in active_source_keys:
                    continue
                if (
                    str(row["status"]).strip().casefold() in ACTIVE_SOURCE_SYNC_STATUSES
                    or str(row["execution_state"]).strip().casefold()
                    in ACTIVE_SOURCE_EXECUTION_STATES
                ):
                    dataset_key = key[:2]
                    dataset_state[dataset_key]["source_sync"] = False
                    source_bucket["active_run_ids"].append(_scoped_key(*key, row["id"]))

        for category, bucket in report["readiness"].items():
            states = [dataset_state[key][category] for key in active_dataset_keys]
            bucket["ready_dataset_ids"] = [
                _scoped_key(*key)
                for key in active_dataset_keys
                if dataset_state[key][category] is True
            ]
            bucket["blocked_dataset_ids"] = [
                _scoped_key(*key)
                for key in active_dataset_keys
                if dataset_state[key][category] is False
            ]
            bucket["unavailable_dataset_ids"] = [
                _scoped_key(*key)
                for key in active_dataset_keys
                if dataset_state[key][category] is None
            ]
            bucket["ready_dataset_count"] = sum(value is True for value in states)
            bucket["blocked_dataset_count"] = sum(value is False for value in states)
            bucket["unavailable_dataset_count"] = sum(value is None for value in states)
            bucket["not_ready_dataset_count"] = (
                len(active_dataset_keys) - bucket["ready_dataset_count"]
            )
            bucket["missing_dataset_ids"] = sorted(set(bucket["missing_dataset_ids"]))
            bucket["ambiguous_dataset_ids"] = sorted(set(bucket["ambiguous_dataset_ids"]))
            bucket["invalid_workspace_dataset_ids"] = sorted(
                set(bucket["invalid_workspace_dataset_ids"])
            )
            bucket["missing_document_ids"] = sorted(set(bucket["missing_document_ids"]))
            bucket["invalid_document_ids"] = sorted(set(bucket["invalid_document_ids"]))
            bucket["drift_document_ids"] = sorted(set(bucket["drift_document_ids"]))
            bucket["active_operation_ids"] = sorted(set(bucket["active_operation_ids"]))
            bucket["active_run_ids"] = sorted(set(bucket["active_run_ids"]))

        blockers: list[str] = []
        if report["missing_default_channels"]:
            blockers.append("missing_default_channels")
        if report["malformed_default_channel_ids"]:
            blockers.append("malformed_default_channel")
        if report["duplicate_channel_code_ids"]:
            blockers.append("duplicate_channel_code")
        if report["malformed_release_ids"]:
            blockers.append("malformed_release_manifest")
        if report["release_entry_count_mismatch_ids"]:
            blockers.append("release_entry_count_mismatch")
        if report["serving_release_missing_dataset_ids"]:
            blockers.append("serving_release_missing")
        if report["release_not_ready_dataset_ids"]:
            blockers.append("release_not_ready")
        if report["malformed_channel_binding_ids"]:
            blockers.append("malformed_channel_binding")
        if report["malformed_app_release_mode_ids"]:
            blockers.append("malformed_app_release_mode")
        if report["malformed_app_release_pin_ids"]:
            blockers.append("malformed_app_release_pin")
        if report["pinned_release_not_ready_ids"]:
            blockers.append("pinned_release_not_ready")

        ownership_bucket = report["readiness"]["ownership"]
        version_bucket = report["readiness"]["version"]
        index_bucket = report["readiness"]["index"]
        source_bucket = report["readiness"]["source_sync"]
        projection_bucket = report["readiness"]["projection"]
        if ownership_bucket["missing_dataset_ids"]:
            blockers.append("missing_ownership")
        if ownership_bucket["ambiguous_dataset_ids"]:
            blockers.append("ambiguous_ownership")
        if ownership_bucket["invalid_workspace_dataset_ids"]:
            blockers.append("invalid_ownership_workspace")
        if version_bucket["missing_document_ids"]:
            blockers.append("document_version_missing")
        if version_bucket["invalid_document_ids"]:
            blockers.append("document_version_invalid")
        if index_bucket["drift_document_ids"]:
            blockers.append("document_index_drift")
        if index_bucket["active_operation_ids"]:
            blockers.append("active_index_operation")
        if source_bucket["active_run_ids"]:
            blockers.append("active_source_sync")
        if projection_bucket["drift_document_ids"]:
            blockers.append("document_graph_drift")
        for category, bucket in report["readiness"].items():
            if not bucket["checked"]:
                blockers.append(f"{category}_readiness_unavailable")

        reason_text = {
            "missing_default_channels": "active Tenants are missing one or more default Release Channels",
            "malformed_default_channel": "Release Channel default-serving facts are malformed",
            "duplicate_channel_code": "duplicate tenant-scoped Release Channel codes were found",
            "malformed_release_manifest": "Release Manifest readiness states are malformed",
            "release_entry_count_mismatch": "Release Manifest entry counts do not match tenant-scoped entries",
            "serving_release_missing": "an active Dataset serving Release cannot be resolved in Tenant scope",
            "release_not_ready": "an active Dataset or channel binding points to a non-ready Release",
            "malformed_channel_binding": "active Dataset Channel bindings cannot be resolved in Tenant scope",
            "malformed_app_release_mode": "active Application references contain malformed Release modes",
            "malformed_app_release_pin": "active Application references contain malformed Release Channel or pinned Release facts",
            "pinned_release_not_ready": "active Application references pin a non-ready Release",
            "missing_ownership": "active Datasets are missing authoritative Workspace ownership",
            "ambiguous_ownership": "active Datasets have more than one authoritative Workspace ownership row",
            "invalid_ownership_workspace": "active Dataset ownership points to a missing or inactive Workspace",
            "document_version_missing": "active retrieval Documents are missing their current Document Version",
            "document_version_invalid": "active retrieval Documents have invalid current Document Version evidence",
            "document_index_drift": "active retrieval Documents have index revision drift",
            "active_index_operation": "active retrieval Documents still have pending index operations",
            "active_source_sync": "active Sources still have a running or pending sync",
            "document_graph_drift": "active Documents have projection or graph revision drift",
            "ownership_readiness_unavailable": "Workspace ownership readiness evidence is unavailable",
            "version_readiness_unavailable": "Document Version readiness evidence is unavailable",
            "index_readiness_unavailable": "index readiness evidence is unavailable",
            "source_sync_readiness_unavailable": "Source sync readiness evidence is unavailable",
            "projection_readiness_unavailable": "projection readiness evidence is unavailable",
        }
        report["blockers"] = list(dict.fromkeys(blockers))
        report["reasons"] = [reason_text.get(code, code) for code in report["blockers"]]

    return report


def _empty_release_quality_preflight_report() -> dict[str, Any]:
    return {
        "checked": False,
        "available": False,
        "schema_status": "not_available",
        "target_revision": STAGE20_TARGET_REVISION,
        "required_tables": list(RELEASE_QUALITY_TABLES),
        "missing_tables": [],
        "missing_columns": {},
        "dependency_missing_tables": [],
        "dependency_missing_columns": {},
        "policy_count": None,
        "active_policy_count": None,
        "baseline_count": None,
        "baseline_item_count": None,
        "certification_count": None,
        "certification_evidence_count": None,
        "waiver_count": None,
        "event_count": None,
        "tenant_summaries": [],
        "policy_scope_invalid_ids": [],
        "duplicate_active_policy_scope_ids": [],
        "baseline_orphan_ids": [],
        "baseline_item_orphan_ids": [],
        "baseline_item_count_mismatch_ids": [],
        "baseline_query_count_mismatch_ids": [],
        "certification_orphan_ids": [],
        "certification_evidence_orphan_ids": [],
        "certification_evidence_count_mismatch_ids": [],
        "waiver_orphan_ids": [],
        "event_tenant_integrity_ids": [],
        "event_sequence_duplicate_ids": [],
        "stale_certification_ids": [],
        "missing_certification_ids": [],
        "expired_waiver_ids": [],
        "revoked_waiver_ids": [],
        "invalid_envelope_ids": [],
        "approval": {
            "checked": False,
            "action_type": RELEASE_QUALITY_WAIVER_ACTION,
            "supported": False,
            "policy_count": None,
            "request_count": None,
        },
        "channel_coverage": {
            "checked": False,
            "required_channel_ids": [],
            "covered_channel_ids": [],
            "missing_policy_channel_ids": [],
            "missing_quality_authority_ids": [],
        },
        "blockers": [],
        "reasons": [],
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
    }


def _parse_readonly_json(value: Any) -> Any | None:
    if isinstance(value, (Mapping, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_quality_snapshot(value: Any, *, require_mapping: bool = True) -> bool:
    """Validate stored quality JSON without returning its contents."""

    parsed = _parse_readonly_json(value)
    if parsed is None or (require_mapping and not isinstance(parsed, Mapping)):
        return False
    forbidden = {
        "body",
        "content",
        "cookie",
        "authorization",
        "password",
        "secret",
        "ticket",
        "note",
        "query",
        "query_text",
        "raw_query",
        "raw_result",
        "token",
    }

    def visit(item: Any) -> bool:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or key.casefold() in forbidden:
                    return False
                if not visit(child):
                    return False
            return True
        if isinstance(item, list):
            return all(visit(child) for child in item)
        if type(item) is float and (item != item or item in {float("inf"), float("-inf")}):
            return False
        return item is None or type(item) in {bool, int, float, str}

    return visit(parsed)


def _parse_quality_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _quality_scope_key(row: Mapping[str, Any]) -> str | None:
    status = str(row.get("status") or "")
    scope_type = str(row.get("scope_type") or "")
    scope_value = str(row.get("scope_value") or "")
    channel_id = str(row.get("channel_id") or "")
    active_scope_key = str(row.get("active_scope_key") or "")
    if status != "active":
        return None
    if scope_type == "global" and scope_value == "*" and not channel_id:
        return "global:*" if active_scope_key == "global:*" else None
    if scope_type == "risk_tier" and scope_value in {"low", "medium", "high"}:
        expected = f"risk_tier:{scope_value}"
        return expected if not channel_id and active_scope_key == expected else None
    if scope_type == "channel" and channel_id and scope_value == channel_id:
        expected = f"channel:{channel_id}"
        return expected if active_scope_key == expected else None
    return None


def _quality_scoped_id(row: Mapping[str, Any], *fields: str) -> str:
    return _scoped_key(
        str(row.get("tenant_id") or ""), *(str(row.get(field) or "") for field in fields)
    )


def _quality_digest(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return normalized == normalized.casefold() and _SHA256_RE.fullmatch(normalized) is not None


def _quality_timestamp_authority(value: Any) -> str | None:
    parsed = _parse_quality_timestamp(value)
    if parsed is None:
        return None
    return parsed.replace(tzinfo=None).isoformat()


def _quality_check_supports_action(engine: Engine, table_name: str) -> bool:
    try:
        checks = inspect(engine).get_check_constraints(table_name)
    except Exception:
        return False
    action = RELEASE_QUALITY_WAIVER_ACTION.casefold()
    return any(
        "action_type" in str(item.get("sqltext") or "").casefold()
        and action in str(item.get("sqltext") or "").casefold()
        for item in checks
    )


def _grouped_quality_count(connection: Any, table_name: str) -> tuple[int, dict[str, int]]:
    rows = connection.execute(
        text(f"SELECT tenant_id, COUNT(*) AS item_count FROM {table_name} GROUP BY tenant_id")
    ).mappings()
    counts = {str(row["tenant_id"]): int(row["item_count"]) for row in rows}
    return sum(counts.values()), counts


def _quality_tenant_summary(tenant_id: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "policy_count": 0,
        "baseline_count": 0,
        "baseline_item_count": 0,
        "certification_count": 0,
        "certification_evidence_count": 0,
        "waiver_count": 0,
        "event_count": 0,
    }


def _quality_append_id(report: dict[str, Any], field: str, value: str) -> None:
    _append_unique(report[field], value)


def _quality_block(report: dict[str, Any], code: str) -> None:
    _append_unique(report["blockers"], code)


def _release_quality_preflight_report(
    engine: Engine,
    tables: set[str],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    """Inspect Stage20 quality authority using SELECT/metadata reads only."""

    report = _empty_release_quality_preflight_report()
    present_quality_tables = set(RELEASE_QUALITY_TABLES) & tables
    if not present_quality_tables and revision not in {None, STAGE20_TARGET_REVISION}:
        return report
    if revision is None and not present_quality_tables:
        return report

    report["checked"] = True
    report["missing_tables"] = sorted(set(RELEASE_QUALITY_TABLES) - tables)
    columns_by_table: dict[str, set[str]] = {}
    for table_name, required_columns in RELEASE_QUALITY_REQUIRED_COLUMNS.items():
        if table_name not in tables:
            continue
        columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
        columns_by_table[table_name] = columns
        missing = sorted(required_columns - columns)
        if missing:
            report["missing_columns"][table_name] = missing

    if report["missing_tables"] or report["missing_columns"]:
        report.update(
            {
                "schema_status": "partial",
                "blockers": ["release_quality_schema_incomplete"],
                "reasons": ["0030 Release Quality authority schema is incomplete"],
            }
        )
        return report

    report["available"] = True
    report["schema_status"] = (
        "current" if revision in {None, STAGE20_TARGET_REVISION} else "present_before_revision"
    )

    for table_name, required_columns in RELEASE_QUALITY_DEPENDENCY_REQUIRED_COLUMNS.items():
        if table_name not in tables:
            report["dependency_missing_tables"].append(table_name)
            continue
        actual_columns = columns_by_table.get(table_name)
        if actual_columns is None:
            actual_columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
            columns_by_table[table_name] = actual_columns
        missing = sorted(required_columns - actual_columns)
        if missing:
            report["dependency_missing_columns"][table_name] = missing
    if report["dependency_missing_tables"] or report["dependency_missing_columns"]:
        _quality_block(report, "release_quality_dependency_schema_incomplete")

    with engine.connect() as connection:
        now = datetime.now(timezone.utc)
        tenant_ids: set[str] = set()
        if "tenants" in tables:
            tenant_columns = {column["name"] for column in inspect(engine).get_columns("tenants")}
            if "id" in tenant_columns:
                query = (
                    "SELECT id"
                    + (", status" if "status" in tenant_columns else "")
                    + " FROM tenants"
                )
                for row in connection.execute(text(query)).all():
                    if len(row) == 1 or str(row[1]).strip().casefold() == "active":
                        tenant_ids.add(str(row[0]))

        grouped_counts: dict[str, dict[str, int]] = {}
        for field, table_name in (
            ("policy_count", "tenant_release_quality_gate_policies"),
            ("baseline_count", "dataset_quality_baselines"),
            ("baseline_item_count", "dataset_quality_baseline_items"),
            ("certification_count", "dataset_release_quality_certifications"),
            ("certification_evidence_count", "dataset_release_quality_certification_evidence"),
            ("waiver_count", "dataset_release_quality_waivers"),
            ("event_count", "dataset_release_quality_events"),
        ):
            total, by_tenant = _grouped_quality_count(connection, table_name)
            report[field] = total
            grouped_counts[field] = by_tenant
            tenant_ids.update(by_tenant)

        datasets: set[tuple[str, str]] = set()
        if "datasets" in tables and {
            "id",
            "tenant_id",
        } <= columns_by_table.get("datasets", set()):
            datasets = {
                (str(row["tenant_id"]), str(row["id"]))
                for row in connection.execute(text("SELECT tenant_id, id FROM datasets")).mappings()
            }
            tenant_ids.update(tenant_id for tenant_id, _dataset_id in datasets)

        channels: dict[tuple[str, str], Any] = {}
        if "tenant_release_channels" in tables and not report["dependency_missing_columns"].get(
            "tenant_release_channels"
        ):
            channel_rows = connection.execute(
                text(
                    "SELECT tenant_id, id, status, risk_tier, is_default_serving, active_default_slot "
                    "FROM tenant_release_channels ORDER BY tenant_id, id"
                )
            ).mappings()
            channels = {(str(row["tenant_id"]), str(row["id"])): row for row in channel_rows}
            tenant_ids.update(tenant_id for tenant_id, _channel_id in channels)

        manifests: dict[tuple[str, str, str], Any] = {}
        if "dataset_release_manifests" in tables and not report["dependency_missing_columns"].get(
            "dataset_release_manifests"
        ):
            manifest_rows = connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, id, manifest_digest, mutation_generation, "
                    "serving_generation FROM dataset_release_manifests ORDER BY tenant_id, dataset_id, id"
                )
            ).mappings()
            manifests = {
                (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"])): row
                for row in manifest_rows
            }
            tenant_ids.update(tenant_id for tenant_id, _dataset_id, _release_id in manifests)

        policy_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, scope_type, scope_value, channel_id, active_scope_key, "
                    "status, revision, policy_digest, max_certification_age_minutes "
                    "FROM tenant_release_quality_gate_policies ORDER BY tenant_id, id"
                )
            ).mappings()
        )
        active_policies: dict[tuple[str, str], list[Any]] = {}
        for row in policy_rows:
            tenant_id = str(row["tenant_id"])
            policy_id = _quality_scoped_id(row, "id")
            if str(row["status"] or "").strip().casefold() == "active":
                scope_key = _quality_scope_key(row)
                if scope_key is None:
                    _quality_append_id(report, "policy_scope_invalid_ids", policy_id)
                elif (
                    str(row["scope_type"] or "").strip().casefold() == "channel"
                    and (
                        tenant_id,
                        str(row["channel_id"] or ""),
                    )
                    not in channels
                ):
                    _quality_append_id(report, "policy_scope_invalid_ids", policy_id)
                else:
                    active_policies.setdefault((tenant_id, scope_key), []).append(row)
            if not _quality_digest(row["policy_digest"]):
                _quality_append_id(report, "invalid_envelope_ids", policy_id)
        report["active_policy_count"] = sum(
            1 for row in policy_rows if str(row["status"] or "").strip().casefold() == "active"
        )
        for rows in active_policies.values():
            if len(rows) > 1:
                for row in rows:
                    _quality_append_id(
                        report, "duplicate_active_policy_scope_ids", _quality_scoped_id(row, "id")
                    )

        baseline_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, parent_baseline_id, experiment_count, "
                    "query_count, baseline_digest FROM dataset_quality_baselines "
                    "ORDER BY tenant_id, dataset_id, id"
                )
            ).mappings()
        )
        baselines: dict[tuple[str, str, str], Any] = {}
        baseline_item_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, baseline_id, ordinal, experiment_id, "
                    "experiment_sequence, query_hash, experiment_serving_generation, strategy_digest, result_digest, "
                    "evidence_digest, judgment_digest FROM dataset_quality_baseline_items "
                    "ORDER BY tenant_id, dataset_id, baseline_id, ordinal, id"
                )
            ).mappings()
        )
        baseline_items_by_baseline: dict[tuple[str, str, str], list[Any]] = {}
        for row in baseline_rows:
            key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"]))
            baselines[key] = row
            baseline_id = _quality_scoped_id(row, "id")
            if (str(row["tenant_id"]), str(row["dataset_id"])) not in datasets:
                _quality_append_id(report, "baseline_orphan_ids", baseline_id)
            if not _quality_digest(row["baseline_digest"]):
                _quality_append_id(report, "invalid_envelope_ids", baseline_id)

        for row in baseline_rows:
            parent_id = str(row["parent_baseline_id"] or "").strip()
            if (
                parent_id
                and (
                    str(row["tenant_id"]),
                    str(row["dataset_id"]),
                    parent_id,
                )
                not in baselines
            ):
                _quality_append_id(report, "baseline_orphan_ids", _quality_scoped_id(row, "id"))

        for row in baseline_item_rows:
            key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["baseline_id"]))
            item_id = _quality_scoped_id(row, "dataset_id", "id")
            baseline = baselines.get(key)
            if baseline is None:
                _quality_append_id(report, "baseline_item_orphan_ids", item_id)
            for digest_field in (
                "query_hash",
                "strategy_digest",
                "result_digest",
                "evidence_digest",
                "judgment_digest",
            ):
                if not _quality_digest(row[digest_field]):
                    _quality_append_id(report, "invalid_envelope_ids", item_id)
            if baseline is not None:
                baseline_item_rows_for_key = baseline_items_by_baseline.setdefault(key, [])
                baseline_item_rows_for_key.append(row)

        if "retrieval_experiments" in tables:
            experiment_columns = {
                column["name"] for column in inspect(engine).get_columns("retrieval_experiments")
            }
            if {"id", "tenant_id", "dataset_id"} <= experiment_columns:
                experiment_keys = {
                    (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"]))
                    for row in connection.execute(
                        text("SELECT tenant_id, dataset_id, id FROM retrieval_experiments")
                    ).mappings()
                }
                for row in baseline_item_rows:
                    item_key = (
                        str(row["tenant_id"]),
                        str(row["dataset_id"]),
                        str(row["experiment_id"]),
                    )
                    if item_key not in experiment_keys:
                        _quality_append_id(
                            report,
                            "baseline_item_orphan_ids",
                            _quality_scoped_id(row, "dataset_id", "id"),
                        )

        for key, baseline in baselines.items():
            actual_items = baseline_items_by_baseline.get(key, [])
            if _safe_integer(baseline["experiment_count"], -1) != len(actual_items):
                _quality_append_id(
                    report,
                    "baseline_item_count_mismatch_ids",
                    _quality_scoped_id(baseline, "id"),
                )
            if _safe_integer(baseline["query_count"], -1) != len(
                {str(item["query_hash"]) for item in actual_items}
            ):
                _quality_append_id(
                    report,
                    "baseline_query_count_mismatch_ids",
                    _quality_scoped_id(baseline, "id"),
                )
        certification_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, release_id, baseline_id, policy_id, "
                    "policy_revision, release_manifest_digest, release_mutation_generation, "
                    "release_serving_generation, status, experiment_count, completed_experiment_count, "
                    "degraded_experiment_count, query_count, judged_result_count, total_result_count, "
                    "judgment_count, judgment_coverage_bps, multi_judged_results, unanimous_results, "
                    "conflicting_results, exact_agreement_bps, mean_score_milli, failed_rule_count, "
                    "policy_snapshot_json, summary_json, evidence_digest, certification_digest, valid_until "
                    "FROM dataset_release_quality_certifications ORDER BY tenant_id, dataset_id, id"
                )
            ).mappings()
        )
        certifications = {
            (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"])): row
            for row in certification_rows
        }
        evidence_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, certification_id, baseline_item_id, experiment_id, "
                    "ordinal, status, result_count, judged_result_count, judgment_count, "
                    "multi_judged_results, unanimous_results, conflicting_results, exact_agreement_bps, "
                    "mean_score_milli, experiment_digest, judgment_digest, safe_facts_json "
                    "FROM dataset_release_quality_certification_evidence "
                    "ORDER BY tenant_id, dataset_id, certification_id, ordinal, id"
                )
            ).mappings()
        )
        evidence_by_certification: dict[tuple[str, str, str], list[Any]] = {}
        for row in evidence_rows:
            cert_key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["certification_id"]))
            evidence_by_certification.setdefault(cert_key, []).append(row)
            evidence_id = _quality_scoped_id(row, "dataset_id", "id")
            item_key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["baseline_item_id"]))
            item_candidates = [
                item
                for key, item_rows in baseline_items_by_baseline.items()
                if key[:2] == item_key[:2]
                for item in item_rows
                if str(item["id"]) == str(row["baseline_item_id"])
            ]
            cert_key_exists = cert_key in certifications
            if not cert_key_exists or not item_candidates:
                _quality_append_id(report, "certification_evidence_orphan_ids", evidence_id)
            elif str(item_candidates[0]["experiment_id"]) != str(row["experiment_id"]):
                _quality_append_id(report, "certification_evidence_orphan_ids", evidence_id)
            if (
                not _quality_digest(row["experiment_digest"])
                or not _quality_digest(row["judgment_digest"])
                or not _safe_quality_snapshot(row["safe_facts_json"])
            ):
                _quality_append_id(report, "invalid_envelope_ids", evidence_id)

        revoked_waiver_ids: set[tuple[str, str, str]] = set()
        event_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, release_id, channel_id, certification_id, waiver_id, "
                    "event_type, event_sequence, state, previous_event_digest, event_digest, "
                    "approval_request_id, approval_execution_id, safe_snapshot_json "
                    "FROM dataset_release_quality_events ORDER BY tenant_id, dataset_id, release_id, "
                    "channel_id, event_sequence, id"
                )
            ).mappings()
        )
        event_sequences: dict[tuple[str, str, str, str, int], list[Any]] = {}
        allowed_event_types = {
            "certification_created",
            "gate_evaluated",
            "gate_passed",
            "gate_failed",
            "gate_blocked",
            "gate_unavailable",
            "gate_expired",
            "gate_revoked",
            "waiver_requested",
            "waiver_approved",
            "waiver_rejected",
            "waiver_applied",
            "waiver_expired",
            "waiver_revoked",
            "release_promoted_with_quality_gate",
            "release_rolled_back_with_quality_gate",
        }
        for row in event_rows:
            event_id = _quality_scoped_id(row, "dataset_id", "id")
            event_key = (
                str(row["tenant_id"]),
                str(row["dataset_id"]),
                str(row["release_id"]),
                str(row["channel_id"]),
                _safe_integer(row["event_sequence"], -1),
            )
            event_sequences.setdefault(event_key, []).append(row)
            release_key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["release_id"]))
            channel_key = (str(row["tenant_id"]), str(row["channel_id"]))
            cert_key = (
                str(row["tenant_id"]),
                str(row["dataset_id"]),
                str(row["certification_id"] or ""),
            )
            invalid_scope = release_key not in manifests or channel_key not in channels
            if row["certification_id"] is not None:
                event_cert = certifications.get(cert_key)
                invalid_scope = invalid_scope or event_cert is None
                if event_cert is not None:
                    invalid_scope = invalid_scope or str(event_cert["release_id"]) != str(
                        row["release_id"]
                    )
            if invalid_scope:
                _quality_append_id(report, "event_tenant_integrity_ids", event_id)
            if (
                _safe_integer(row["event_sequence"], 0) <= 0
                or str(row["event_type"] or "").strip().casefold() not in allowed_event_types
                or not _quality_digest(row["event_digest"])
                or (
                    row["previous_event_digest"] is not None
                    and not _quality_digest(row["previous_event_digest"])
                )
                or not _safe_quality_snapshot(row["safe_snapshot_json"])
            ):
                _quality_append_id(report, "invalid_envelope_ids", event_id)
        for rows in event_sequences.values():
            if len(rows) > 1:
                for row in rows:
                    _quality_append_id(
                        report,
                        "event_sequence_duplicate_ids",
                        _quality_scoped_id(row, "dataset_id", "id"),
                    )

        waiver_rows = list(
            connection.execute(
                text(
                    "SELECT id, tenant_id, dataset_id, release_id, channel_id, policy_id, policy_revision, "
                    "release_manifest_digest, approval_request_id, approval_execution_id, valid_from, "
                    "expires_at, waiver_digest FROM dataset_release_quality_waivers "
                    "ORDER BY tenant_id, dataset_id, id"
                )
            ).mappings()
        )
        waivers: dict[tuple[str, str, str], Any] = {}
        for row in waiver_rows:
            key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["id"]))
            waivers[key] = row
            waiver_id = _quality_scoped_id(row, "dataset_id", "id")
            release_key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["release_id"]))
            channel_key = (str(row["tenant_id"]), str(row["channel_id"]))
            policy_key = (str(row["tenant_id"]), str(row["policy_id"]))
            request_row = None
            approval_request_lookup_available = "tenant_approval_requests" in tables and {
                "tenant_id",
                "id",
                "action_type",
            } <= columns_by_table.get("tenant_approval_requests", set())
            if approval_request_lookup_available:
                request_row = (
                    connection.execute(
                        text(
                            "SELECT tenant_id, id, action_type FROM tenant_approval_requests "
                            "WHERE tenant_id=:tenant_id AND id=:request_id"
                        ),
                        {"tenant_id": row["tenant_id"], "request_id": row["approval_request_id"]},
                    )
                    .mappings()
                    .first()
                )
            invalid_scope = (
                release_key not in manifests
                or channel_key not in channels
                or policy_key
                not in {(str(item["tenant_id"]), str(item["id"])) for item in policy_rows}
                or request_row is None
                or str(request_row["action_type"] or "") != RELEASE_QUALITY_WAIVER_ACTION
            )
            manifest = manifests.get(release_key)
            policy = next(
                (
                    item
                    for item in policy_rows
                    if str(item["tenant_id"]) == str(row["tenant_id"])
                    and str(item["id"]) == str(row["policy_id"])
                ),
                None,
            )
            if (
                invalid_scope
                or manifest is None
                or str(row["release_manifest_digest"]) != str(manifest["manifest_digest"])
                or policy is None
                or _safe_integer(row["policy_revision"], -1)
                != _safe_integer(policy["revision"], -2)
            ):
                _quality_append_id(report, "waiver_orphan_ids", waiver_id)
            valid_from = _parse_quality_timestamp(row["valid_from"])
            expires_at = _parse_quality_timestamp(row["expires_at"])
            if expires_at is not None and expires_at <= now:
                _quality_append_id(report, "expired_waiver_ids", waiver_id)
            if (
                not _quality_digest(row["waiver_digest"])
                or valid_from is None
                or expires_at is None
                or expires_at <= valid_from
            ):
                _quality_append_id(report, "invalid_envelope_ids", waiver_id)
            if key in revoked_waiver_ids:
                _quality_append_id(report, "revoked_waiver_ids", waiver_id)

        waiver_keys = set(waivers)
        for row in event_rows:
            if row["waiver_id"] is not None:
                key = (str(row["tenant_id"]), str(row["dataset_id"]), str(row["waiver_id"]))
                if key not in waiver_keys:
                    _quality_append_id(
                        report,
                        "event_tenant_integrity_ids",
                        _quality_scoped_id(row, "dataset_id", "id"),
                    )
                elif str(waivers[key]["release_id"]) != str(row["release_id"]) or str(
                    waivers[key]["channel_id"]
                ) != str(row["channel_id"]):
                    _quality_append_id(
                        report,
                        "event_tenant_integrity_ids",
                        _quality_scoped_id(row, "dataset_id", "id"),
                    )
                elif str(row["event_type"] or "").strip().casefold() == "waiver_revoked":
                    revoked_waiver_ids.add(key)
        for key in revoked_waiver_ids:
            waiver = waivers.get(key)
            if waiver is not None:
                _quality_append_id(
                    report, "revoked_waiver_ids", _quality_scoped_id(waiver, "dataset_id", "id")
                )

        for cert_key, cert in certifications.items():
            certification_id = _quality_scoped_id(cert, "dataset_id", "id")
            release_key = (str(cert["tenant_id"]), str(cert["dataset_id"]), str(cert["release_id"]))
            baseline_key = (
                str(cert["tenant_id"]),
                str(cert["dataset_id"]),
                str(cert["baseline_id"]),
            )
            policy_key = (str(cert["tenant_id"]), str(cert["policy_id"]))
            release = manifests.get(release_key)
            baseline = baselines.get(baseline_key)
            policy = next(
                (
                    item
                    for item in policy_rows
                    if str(item["tenant_id"]) == str(cert["tenant_id"])
                    and str(item["id"]) == str(cert["policy_id"])
                ),
                None,
            )
            if (
                release is None
                or baseline is None
                or policy_key
                not in {(str(item["tenant_id"]), str(item["id"])) for item in policy_rows}
            ):
                _quality_append_id(report, "certification_orphan_ids", certification_id)
            if release is not None and (
                str(cert["release_manifest_digest"]) != str(release["manifest_digest"])
                or _safe_integer(cert["release_mutation_generation"], -1)
                != _safe_integer(release["mutation_generation"], -2)
                or _safe_integer(cert["release_serving_generation"], -1)
                != _safe_integer(release["serving_generation"], -2)
            ):
                _quality_append_id(report, "stale_certification_ids", certification_id)
            if policy is not None and _safe_integer(cert["policy_revision"], -1) != _safe_integer(
                policy["revision"], -2
            ):
                _quality_append_id(report, "stale_certification_ids", certification_id)
            evidence_for_cert = evidence_by_certification.get(cert_key, [])
            if len(evidence_for_cert) != _safe_integer(cert["experiment_count"], -1):
                _quality_append_id(
                    report, "certification_evidence_count_mismatch_ids", certification_id
                )
            cert_json_valid = _safe_quality_snapshot(
                cert["policy_snapshot_json"]
            ) and _safe_quality_snapshot(cert["summary_json"])
            summary = _parse_readonly_json(cert["summary_json"])
            policy_snapshot = _parse_readonly_json(cert["policy_snapshot_json"])
            summary_consistent = True
            if isinstance(summary, Mapping):
                if "verdict" in summary and str(summary["verdict"]) != str(cert["status"]):
                    summary_consistent = False
                for field in (
                    "experiment_count",
                    "completed_experiment_count",
                    "degraded_experiment_count",
                    "query_count",
                    "judged_result_count",
                    "total_result_count",
                    "judgment_count",
                    "multi_judged_results",
                    "unanimous_results",
                    "conflicting_results",
                    "failed_rule_count",
                ):
                    if field in summary and _safe_integer(summary[field], -1) != _safe_integer(
                        cert[field], -2
                    ):
                        summary_consistent = False
            if isinstance(policy_snapshot, Mapping):
                if "id" in policy_snapshot and str(policy_snapshot["id"]) != str(cert["policy_id"]):
                    summary_consistent = False
                if "revision" in policy_snapshot and _safe_integer(
                    policy_snapshot["revision"], -1
                ) != _safe_integer(cert["policy_revision"], -2):
                    summary_consistent = False
                if (
                    policy is not None
                    and "policy_digest" in policy_snapshot
                    and str(policy_snapshot["policy_digest"]) != str(policy["policy_digest"])
                ):
                    summary_consistent = False
            digest_consistent = True
            valid_until_authority = _quality_timestamp_authority(cert["valid_until"])
            if (
                release is not None
                and baseline is not None
                and policy is not None
                and isinstance(summary, Mapping)
                and valid_until_authority is not None
            ):
                try:
                    expected_certification_digest = canonical_quality_digest(
                        "certification",
                        {
                            "tenant_id": str(cert["tenant_id"]),
                            "dataset_id": str(cert["dataset_id"]),
                            "release_id": str(cert["release_id"]),
                            "baseline_id": str(cert["baseline_id"]),
                            "baseline_digest": str(baseline["baseline_digest"]),
                            "policy_id": str(cert["policy_id"]),
                            "policy_revision": _safe_integer(cert["policy_revision"], -1),
                            "policy_digest": str(policy["policy_digest"]),
                            "release_manifest_digest": str(cert["release_manifest_digest"]),
                            "release_mutation_generation": _safe_integer(
                                cert["release_mutation_generation"], -1
                            ),
                            "release_serving_generation": _safe_integer(
                                cert["release_serving_generation"], -1
                            ),
                            "status": str(cert["status"]),
                            "summary": summary,
                            "evidence_digest": str(cert["evidence_digest"]),
                            "valid_until": valid_until_authority,
                        },
                    )
                    digest_consistent = expected_certification_digest == str(
                        cert["certification_digest"]
                    )
                except Exception:
                    digest_consistent = False
            if (
                str(cert["status"] or "") not in {"passed", "failed"}
                or not _quality_digest(cert["release_manifest_digest"])
                or not _quality_digest(cert["evidence_digest"])
                or not _quality_digest(cert["certification_digest"])
                or _parse_quality_timestamp(cert["valid_until"]) is None
                or not cert_json_valid
                or not summary_consistent
                or not digest_consistent
            ):
                _quality_append_id(report, "invalid_envelope_ids", certification_id)

        approval = report["approval"]
        approval_tables = {"tenant_approval_policies", "tenant_approval_requests"}
        approval_schema_ready = approval_tables <= tables and all(
            not report["dependency_missing_columns"].get(table_name)
            for table_name in approval_tables
        )
        if approval_schema_ready:
            approval["checked"] = True
            approval["supported"] = all(
                _quality_check_supports_action(engine, table_name)
                for table_name in sorted(approval_tables)
            )
            approval["policy_count"] = int(
                connection.execute(
                    text("SELECT COUNT(*) FROM tenant_approval_policies WHERE action_type=:action"),
                    {"action": RELEASE_QUALITY_WAIVER_ACTION},
                ).scalar_one()
            )
            approval["request_count"] = int(
                connection.execute(
                    text("SELECT COUNT(*) FROM tenant_approval_requests WHERE action_type=:action"),
                    {"action": RELEASE_QUALITY_WAIVER_ACTION},
                ).scalar_one()
            )
            if not approval["supported"]:
                _quality_block(report, "approval_waiver_action_unsupported")

        coverage = report["channel_coverage"]
        if channels and not report["dependency_missing_columns"].get("tenant_release_channels"):
            coverage["checked"] = True
            for (tenant_id, channel_id), channel in sorted(channels.items()):
                if str(channel["status"] or "").strip().casefold() != "active":
                    continue
                risk_tier = str(channel["risk_tier"] or "").strip().casefold()
                if risk_tier != "high" and not _database_boolean(channel["is_default_serving"]):
                    continue
                scoped_channel = _scoped_key(tenant_id, channel_id)
                coverage["required_channel_ids"].append(scoped_channel)
                candidates: list[Any] = []
                for scope_key in (
                    f"channel:{channel_id}",
                    f"risk_tier:{risk_tier}",
                    "global:*",
                ):
                    candidates = active_policies.get((tenant_id, scope_key), [])
                    if candidates:
                        break
                candidate_id = (
                    _quality_scoped_id(candidates[0], "id") if len(candidates) == 1 else None
                )
                if len(candidates) == 1 and candidate_id not in set(report["invalid_envelope_ids"]):
                    coverage["covered_channel_ids"].append(scoped_channel)
                else:
                    coverage["missing_policy_channel_ids"].append(scoped_channel)
            coverage["required_channel_ids"] = sorted(set(coverage["required_channel_ids"]))
            coverage["covered_channel_ids"] = sorted(set(coverage["covered_channel_ids"]))
            coverage["missing_policy_channel_ids"] = sorted(
                set(coverage["missing_policy_channel_ids"])
            )
            if coverage["missing_policy_channel_ids"]:
                _quality_block(report, "high_or_default_policy_missing")

        if "dataset_channel_releases" in tables:
            binding_columns = {
                column["name"] for column in inspect(engine).get_columns("dataset_channel_releases")
            }
            required_binding_columns = {
                "id",
                "tenant_id",
                "dataset_id",
                "channel_id",
                "status",
                "active_release_id",
            }
            if required_binding_columns <= binding_columns:
                binding_rows = connection.execute(
                    text(
                        "SELECT id, tenant_id, dataset_id, channel_id, status, active_release_id "
                        "FROM dataset_channel_releases WHERE status='active' "
                        "ORDER BY tenant_id, dataset_id, id"
                    )
                ).mappings()
                stale_certification_set = set(report["stale_certification_ids"])
                invalid_envelope_set = set(report["invalid_envelope_ids"])
                expired_waiver_set = set(report["expired_waiver_ids"])
                revoked_waiver_set = set(report["revoked_waiver_ids"])
                orphan_waiver_set = set(report["waiver_orphan_ids"])
                for binding in binding_rows:
                    channel = channels.get((str(binding["tenant_id"]), str(binding["channel_id"])))
                    if channel is None or str(channel["status"] or "").casefold() != "active":
                        continue
                    risk_tier = str(channel["risk_tier"] or "").strip().casefold()
                    if risk_tier != "high" and not _database_boolean(channel["is_default_serving"]):
                        continue
                    tenant_id = str(binding["tenant_id"])
                    dataset_id = str(binding["dataset_id"])
                    release_id = str(binding["active_release_id"] or "")
                    required_policy_rows: list[Any] = []
                    for scope_key in (
                        f"channel:{binding['channel_id']}",
                        f"risk_tier:{risk_tier}",
                        "global:*",
                    ):
                        required_policy_rows = active_policies.get((tenant_id, scope_key), [])
                        if required_policy_rows:
                            break
                    authority_available = False
                    if len(required_policy_rows) == 1:
                        policy = required_policy_rows[0]
                        policy_id = str(policy["id"])
                        policy_revision = _safe_integer(policy["revision"], -1)
                        for cert in certification_rows:
                            cert_id = _quality_scoped_id(cert, "dataset_id", "id")
                            if (
                                str(cert["tenant_id"]) == tenant_id
                                and str(cert["dataset_id"]) == dataset_id
                                and str(cert["release_id"]) == release_id
                                and str(cert["policy_id"]) == policy_id
                                and _safe_integer(cert["policy_revision"], -1) == policy_revision
                                and str(cert["status"]) == "passed"
                                and _parse_quality_timestamp(cert["valid_until"]) is not None
                                and _parse_quality_timestamp(cert["valid_until"]) > now
                                and cert_id not in stale_certification_set
                                and cert_id not in invalid_envelope_set
                            ):
                                authority_available = True
                                break
                        if not authority_available:
                            for waiver in waiver_rows:
                                waiver_id = _quality_scoped_id(waiver, "dataset_id", "id")
                                waiver_key = (
                                    str(waiver["tenant_id"]),
                                    str(waiver["dataset_id"]),
                                    str(waiver["id"]),
                                )
                                expires_at = _parse_quality_timestamp(waiver["expires_at"])
                                if (
                                    waiver_key in waivers
                                    and str(waiver["tenant_id"]) == tenant_id
                                    and str(waiver["dataset_id"]) == dataset_id
                                    and str(waiver["release_id"]) == release_id
                                    and str(waiver["channel_id"]) == str(binding["channel_id"])
                                    and str(waiver["policy_id"]) == policy_id
                                    and _safe_integer(waiver["policy_revision"], -1)
                                    == policy_revision
                                    and expires_at is not None
                                    and expires_at > now
                                    and waiver_id not in expired_waiver_set
                                    and waiver_id not in revoked_waiver_set
                                    and waiver_id not in orphan_waiver_set
                                    and waiver_id not in invalid_envelope_set
                                ):
                                    authority_available = True
                                    break
                    if not authority_available:
                        binding_scope = _quality_scoped_id(binding, "dataset_id", "id")
                        _quality_append_id(
                            report,
                            "missing_certification_ids",
                            binding_scope,
                        )
                        coverage["missing_quality_authority_ids"].append(binding_scope)
                if report["missing_certification_ids"]:
                    _quality_block(report, "quality_authority_missing")

        report["tenant_summaries"] = []
        summary_by_tenant = {
            tenant_id: _quality_tenant_summary(tenant_id) for tenant_id in sorted(tenant_ids)
        }
        for field in (
            "policy_count",
            "baseline_count",
            "baseline_item_count",
            "certification_count",
            "certification_evidence_count",
            "waiver_count",
            "event_count",
        ):
            for tenant_id, count in grouped_counts[field].items():
                summary_by_tenant.setdefault(tenant_id, _quality_tenant_summary(tenant_id))[
                    field
                ] = count
        report["tenant_summaries"] = [summary_by_tenant[key] for key in sorted(summary_by_tenant)]

    if report["policy_scope_invalid_ids"]:
        _quality_block(report, "invalid_policy_scope")
    if report["duplicate_active_policy_scope_ids"]:
        _quality_block(report, "duplicate_active_policy_scope")
    if (
        report["baseline_orphan_ids"]
        or report["baseline_item_orphan_ids"]
        or report["baseline_item_count_mismatch_ids"]
        or report["baseline_query_count_mismatch_ids"]
    ):
        _quality_block(report, "baseline_tenant_integrity")
    if (
        report["certification_orphan_ids"]
        or report["certification_evidence_orphan_ids"]
        or report["certification_evidence_count_mismatch_ids"]
    ):
        _quality_block(report, "certification_tenant_integrity")
    if report["waiver_orphan_ids"]:
        _quality_block(report, "waiver_tenant_integrity")
    if report["event_tenant_integrity_ids"] or report["event_sequence_duplicate_ids"]:
        _quality_block(report, "event_tenant_integrity")
    if report["stale_certification_ids"]:
        _quality_block(report, "certification_stale")
    if report["expired_waiver_ids"]:
        _quality_block(report, "waiver_expired")
    if report["revoked_waiver_ids"]:
        _quality_block(report, "waiver_revoked")
    if report["invalid_envelope_ids"]:
        _quality_block(report, "invalid_quality_envelope")

    reason_text = {
        "release_quality_schema_incomplete": "0030 Release Quality tables or columns are incomplete",
        "release_quality_dependency_schema_incomplete": "Release Quality dependencies are incomplete",
        "approval_waiver_action_unsupported": "Approval schema does not support the Release Quality Waiver action",
        "high_or_default_policy_missing": "high-risk or default-serving Channels have no authoritative quality policy",
        "quality_authority_missing": "high-risk or default-serving bindings have no current Certification or Waiver",
        "invalid_policy_scope": "active Release Quality Policy scope is not canonical",
        "duplicate_active_policy_scope": "duplicate active Release Quality Policy scopes were found",
        "baseline_tenant_integrity": "Quality Baseline or Baseline Item Tenant scope is invalid",
        "certification_tenant_integrity": "Quality Certification or Evidence Tenant scope is invalid",
        "waiver_tenant_integrity": "Quality Waiver Tenant or Approval scope is invalid",
        "event_tenant_integrity": "Quality Event Tenant scope or sequence is invalid",
        "certification_stale": "Release Quality Certifications are stale against Release or Policy authority",
        "waiver_expired": "Release Quality Waivers are expired",
        "waiver_revoked": "Release Quality Waivers are revoked",
        "invalid_quality_envelope": "Release Quality authority envelope or safe snapshot is invalid",
    }
    report["blockers"] = list(dict.fromkeys(report["blockers"]))
    report["reasons"] = [reason_text.get(code, code) for code in report["blockers"]]
    for field in (
        "policy_scope_invalid_ids",
        "duplicate_active_policy_scope_ids",
        "baseline_orphan_ids",
        "baseline_item_orphan_ids",
        "baseline_item_count_mismatch_ids",
        "baseline_query_count_mismatch_ids",
        "certification_orphan_ids",
        "certification_evidence_orphan_ids",
        "certification_evidence_count_mismatch_ids",
        "waiver_orphan_ids",
        "event_tenant_integrity_ids",
        "event_sequence_duplicate_ids",
        "stale_certification_ids",
        "missing_certification_ids",
        "expired_waiver_ids",
        "revoked_waiver_ids",
        "invalid_envelope_ids",
    ):
        report[field] = sorted(set(report[field]))
    return report


def _operations_identity_bucket() -> dict[str, Any]:
    return {
        "checked": False,
        "invalid_ids": [],
        "duplicate_ids": [],
        "duplicate_keys": [],
    }


def _operations_scope_bucket() -> dict[str, Any]:
    return {
        "checked": False,
        "invalid_ids": [],
        "orphan_ids": [],
        "cross_tenant_ids": [],
    }


def _empty_release_quality_operations_preflight_report() -> dict[str, Any]:
    identities = {
        "slo_policy": _operations_identity_bucket(),
        "schedule": _operations_identity_bucket(),
        "alert": _operations_identity_bucket(),
        "job": _operations_identity_bucket(),
    }
    duplicates = {
        "active_slo_policies": identities["slo_policy"]["duplicate_ids"],
        "active_schedules": identities["schedule"]["duplicate_ids"],
        "active_alerts": identities["alert"]["duplicate_ids"],
        "non_terminal_jobs": identities["job"]["duplicate_ids"],
        "cycle_keys": [],
    }
    dataset_scope = {
        "checked": False,
        "schedule": _operations_scope_bucket(),
        "scan_run": _operations_scope_bucket(),
    }
    table_counts = {table: None for table in RELEASE_QUALITY_OPERATIONS_TABLES}
    return {
        "checked": False,
        "available": False,
        "schema_status": "not_available",
        "target_revision": STAGE21_TARGET_REVISION,
        "required_tables": list(RELEASE_QUALITY_OPERATIONS_TABLES),
        "missing_tables": [],
        "missing_columns": {},
        "dependency_missing_tables": [],
        "dependency_missing_columns": {},
        "table_counts": table_counts,
        "counts": table_counts,
        "status_counts": {},
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "dataset_scope": dataset_scope,
        "tenant_composite_scope": dataset_scope,
        "identities": identities,
        "duplicates": duplicates,
        "cycle_key": {
            "checked": False,
            "invalid_ids": [],
            "duplicate_ids": duplicates["cycle_keys"],
            "duplicate_keys": [],
        },
        "lease_terminal_consistency": {
            "checked": False,
            "scan_run_ids": [],
            "recertification_job_ids": [],
        },
        "observation_immutable_guards": {
            "checked": False,
            "supported": False,
            "required": list(RELEASE_QUALITY_OPERATIONS_GUARD_NAMES),
            "present": [],
            "missing": [],
            "target_table": "dataset_release_quality_observations",
        },
        "orphan_ids": {
            "schedules": [],
            "scan_runs": [],
            "observations": [],
            "alerts": [],
            "jobs": [],
        },
        "cross_tenant_ids": {
            "schedules": [],
            "scan_runs": [],
            "observations": [],
            "alerts": [],
            "jobs": [],
        },
        "authority_consistency": {
            "observation_digest_invalid_ids": [],
            "observation_envelope_invalid_ids": [],
            "alert_source_digest_invalid_ids": [],
            "job_digest_invalid_ids": [],
        },
        "blockers": [],
        "reasons": [],
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
    }


def _operations_block(report: dict[str, Any], code: str, reason: str) -> None:
    _append_unique(report["blockers"], code)
    _append_unique(report["reasons"], reason)


def _operations_finish_report(report: dict[str, Any]) -> dict[str, Any]:
    report["safe_to_upgrade"] = not report["blockers"]
    return report


def _operations_value(row: Mapping[str, Any], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _operations_present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _operations_row_id(row: Mapping[str, Any], *fields: str) -> str:
    values = [_operations_value(row, "tenant_id") or ""]
    values.extend(_operations_value(row, field) or "" for field in fields)
    return _scoped_key(*values)


def _operations_rows(connection: Any, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _operations_parent_snapshot(
    rows: Mapping[str, list[dict[str, Any]]],
    table: str,
    fields: tuple[str, ...],
) -> tuple[set[tuple[str | None, ...]], set[str]]:
    scoped: set[tuple[str | None, ...]] = set()
    unscoped: set[str] = set()
    for row in rows.get(table, []):
        values = tuple(_operations_value(row, field) for field in fields)
        scoped.add(values)
        if values and values[-1] is not None:
            unscoped.add(values[-1])
    return scoped, unscoped


def _operations_guard_names(engine: Engine) -> tuple[set[str], bool]:
    dialect = str(engine.dialect.name).casefold()
    with engine.connect() as connection:
        if dialect == "sqlite":
            names = connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:table_name"
                ),
                {"table_name": "dataset_release_quality_observations"},
            ).scalars()
            return {str(name) for name in names}, True
        if dialect == "postgresql":
            names = connection.execute(
                text(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE event_object_table=:table_name"
                ),
                {"table_name": "dataset_release_quality_observations"},
            ).scalars()
            return {str(name) for name in names}, True
        if dialect in {"mysql", "mariadb"}:
            names = connection.execute(
                text(
                    "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA=DATABASE() AND EVENT_OBJECT_TABLE=:table_name"
                ),
                {"table_name": "dataset_release_quality_observations"},
            ).scalars()
            return {str(name) for name in names}, True
    return set(), False


def _operations_duplicate_ids(
    rows: Sequence[Mapping[str, Any]],
    key_factory: Callable[[Mapping[str, Any]], str | None],
    id_factory: Callable[[Mapping[str, Any]], str],
) -> tuple[list[str], list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        key = key_factory(row)
        if key is None:
            continue
        grouped.setdefault(key, []).append(id_factory(row))
    duplicate_ids: list[str] = []
    duplicate_keys: list[str] = []
    for key, identifiers in grouped.items():
        if len(identifiers) > 1:
            duplicate_keys.append(key)
            for identifier in identifiers:
                _append_unique(duplicate_ids, identifier)
    return duplicate_ids, duplicate_keys


def _operations_canonical_slo_key(row: Mapping[str, Any]) -> str | None:
    if (_operations_value(row, "status") or "").casefold() != "active":
        return None
    scope_type = (_operations_value(row, "scope_type") or "").casefold()
    scope_value = _operations_value(row, "scope_value")
    channel_id = _operations_value(row, "channel_id")
    if scope_type == "global" and scope_value == "*" and channel_id is None:
        return "global:*"
    if (
        scope_type == "risk_tier"
        and scope_value in {"low", "medium", "high"}
        and channel_id is None
    ):
        return f"risk_tier:{scope_value}"
    if scope_type == "channel" and channel_id is not None and scope_value == channel_id:
        return f"channel:{scope_value}"
    return None


def _operations_canonical_schedule_key(row: Mapping[str, Any]) -> str | None:
    if (_operations_value(row, "status") or "").casefold() != "active":
        return None
    policy_id = _operations_value(row, "slo_policy_id")
    active_slot = _operations_value(row, "active_policy_slot")
    if policy_id is None or active_slot != policy_id:
        return None
    tenant_id = _operations_value(row, "tenant_id")
    dataset_id = _operations_value(row, "dataset_id")
    return (
        None
        if tenant_id is None or dataset_id is None
        else _scoped_key(tenant_id, dataset_id, active_slot)
    )


def _operations_canonical_alert_key(row: Mapping[str, Any]) -> str | None:
    status = (_operations_value(row, "status") or "").casefold()
    active_key = _operations_value(row, "active_alert_key")
    if status == "resolved":
        return None if active_key is None else "__invalid_resolved__"
    if status not in {"open", "acknowledged", "suppressed"}:
        return "__invalid_status__"
    expected = _scoped_key(
        _operations_value(row, "dataset_id") or "",
        _operations_value(row, "release_id") or "",
        _operations_value(row, "channel_id") or "",
        _operations_value(row, "release_role") or "",
        _operations_value(row, "alert_type") or "",
    )
    if active_key != expected or not all(
        _operations_present(_operations_value(row, field))
        for field in ("dataset_id", "release_id", "channel_id", "release_role", "alert_type")
    ):
        return "__invalid_identity__"
    return _scoped_key(_operations_value(row, "tenant_id") or "", active_key)


def _operations_canonical_job_key(row: Mapping[str, Any]) -> str | None:
    status = (_operations_value(row, "status") or "").casefold()
    active_key = _operations_value(row, "active_job_key")
    if status in RELEASE_QUALITY_OPERATIONS_TERMINAL_JOB_STATUSES:
        return None if active_key is None else "__invalid_terminal__"
    if status not in RELEASE_QUALITY_OPERATIONS_NON_TERMINAL_JOB_STATUSES:
        return "__invalid_status__"
    expected = _scoped_key(
        _operations_value(row, "dataset_id") or "",
        _operations_value(row, "release_id") or "",
        _operations_value(row, "channel_id") or "",
        _operations_value(row, "release_role") or "",
        _operations_value(row, "policy_id") or "",
    )
    if active_key != expected or not all(
        _operations_present(_operations_value(row, field))
        for field in ("dataset_id", "release_id", "channel_id", "release_role", "policy_id")
    ):
        return "__invalid_identity__"
    return _scoped_key(_operations_value(row, "tenant_id") or "", active_key)


def _operations_prepare_report(
    engine: Engine,
    tables: set[str],
    *,
    revision: str | None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]] | None]:
    report = _empty_release_quality_operations_preflight_report()
    present_tables = set(RELEASE_QUALITY_OPERATIONS_TABLES) & tables
    if not present_tables and revision not in {None, STAGE21_TARGET_REVISION}:
        return report, None
    if revision is None and not present_tables:
        return report, None

    report["checked"] = True
    report["missing_tables"] = sorted(set(RELEASE_QUALITY_OPERATIONS_TABLES) - tables)
    inspector = inspect(engine)
    for table, required_columns in RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        actual_columns = {str(item["name"]) for item in inspector.get_columns(table)}
        missing = sorted(required_columns - actual_columns)
        if missing:
            report["missing_columns"][table] = missing

    if report["missing_tables"] or report["missing_columns"]:
        report["schema_status"] = "partial"
        _operations_block(
            report,
            "release_quality_operations_schema_incomplete",
            "0031 Release Quality Operations tables or columns are incomplete",
        )
        return _operations_finish_report(report), None

    report["available"] = True
    report["schema_status"] = (
        "current" if revision in {None, STAGE21_TARGET_REVISION} else "present_before_revision"
    )
    if revision not in {None, STAGE21_TARGET_REVISION}:
        _operations_block(
            report,
            "release_quality_operations_revision_mismatch",
            "0031 Release Quality Operations tables exist before the catalog revision",
        )

    for table, required_columns in RELEASE_QUALITY_OPERATIONS_DEPENDENCY_REQUIRED_COLUMNS.items():
        if table not in tables:
            report["dependency_missing_tables"].append(table)
            continue
        actual_columns = {str(item["name"]) for item in inspector.get_columns(table)}
        missing = sorted(required_columns - actual_columns)
        if missing:
            report["dependency_missing_columns"][table] = missing
    if report["dependency_missing_tables"] or report["dependency_missing_columns"]:
        _operations_block(
            report,
            "release_quality_operations_dependency_schema_incomplete",
            "0031 Release Quality Operations parent scope schema is incomplete",
        )

    if revision == STAGE21_TARGET_REVISION:
        try:
            capability_state, capability_issues = (
                catalog_schema.inspect_enterprise_release_quality_operations_capability(engine)
            )
            report["schema_capability_state"] = capability_state
            report["schema_capability_issues"] = list(capability_issues)
            if capability_state != "ready":
                _operations_block(
                    report,
                    "release_quality_operations_schema_capability",
                    "0031 Release Quality Operations schema capability is unavailable",
                )
        except Exception as exc:
            report["schema_capability_state"] = "unavailable"
            report["schema_capability_issues"] = [
                f"schema inspection failed: {exc.__class__.__name__}"
            ]
            _operations_block(
                report,
                "release_quality_operations_schema_capability",
                "0031 Release Quality Operations schema inspection failed",
            )

    try:
        with engine.connect() as connection:
            readable_tables = tuple(
                dict.fromkeys(
                    RELEASE_QUALITY_OPERATIONS_TABLES
                    + tuple(RELEASE_QUALITY_OPERATIONS_DEPENDENCY_REQUIRED_COLUMNS)
                )
            )
            rows = {
                table: _operations_rows(connection, table)
                for table in readable_tables
                if table in tables
            }
            counts = {
                table: len(rows.get(table, [])) for table in RELEASE_QUALITY_OPERATIONS_TABLES
            }
            report["table_counts"] = counts
            report["counts"] = counts
            for table, table_rows in rows.items():
                status_counts: dict[str, int] = {}
                for row in table_rows:
                    status = _operations_value(row, "status")
                    if status is not None:
                        status_counts[status] = status_counts.get(status, 0) + 1
                if status_counts:
                    report["status_counts"][table] = dict(sorted(status_counts.items()))

        guard_names, guard_supported = _operations_guard_names(engine)
        guard_report = report["observation_immutable_guards"]
        guard_report["checked"] = True
        guard_report["supported"] = guard_supported
        guard_report["present"] = sorted(set(RELEASE_QUALITY_OPERATIONS_GUARD_NAMES) & guard_names)
        guard_report["missing"] = sorted(set(RELEASE_QUALITY_OPERATIONS_GUARD_NAMES) - guard_names)
        if guard_report["missing"] or not guard_supported:
            _operations_block(
                report,
                "quality_observation_immutable_guards_missing",
                "Release quality Observation immutable guards are missing or unverifiable",
            )
    except Exception as exc:
        _operations_block(
            report,
            "release_quality_operations_data_unavailable",
            f"Release quality Operations data inspection failed: {exc.__class__.__name__}",
        )
        return _operations_finish_report(report), None
    return report, rows


def _operations_check_reference(
    report: dict[str, Any],
    rows: Mapping[str, list[dict[str, Any]]],
    parent_scopes: Mapping[str, set[tuple[str | None, ...]]],
    parent_ids: Mapping[str, set[str]],
    child_kind: str,
    row: Mapping[str, Any],
    parent_table: str,
    values: tuple[str | None, ...],
    *,
    optional: bool = False,
) -> None:
    if optional and not any(_operations_present(value) for value in values):
        return
    identifier = _operations_row_id(row, "id")
    orphan_ids = report["orphan_ids"][child_kind]
    cross_tenant_ids = report["cross_tenant_ids"][child_kind]
    if not all(_operations_present(value) for value in values):
        _append_unique(orphan_ids, identifier)
        return
    if values in parent_scopes.get(parent_table, set()):
        return
    _append_unique(orphan_ids, identifier)
    if values[-1] in parent_ids.get(parent_table, set()):
        _append_unique(cross_tenant_ids, identifier)


def _operations_parent_indexes(
    rows: Mapping[str, list[dict[str, Any]]],
) -> tuple[dict[str, set[tuple[str | None, ...]]], dict[str, set[str]]]:
    parent_specs = {
        "tenants": ("id",),
        "datasets": ("tenant_id", "id"),
        "tenant_release_channels": ("tenant_id", "id"),
        "dataset_release_manifests": ("tenant_id", "dataset_id", "id"),
        "tenant_release_quality_gate_policies": ("tenant_id", "id"),
        "tenant_release_quality_slo_policies": ("tenant_id", "id"),
        "dataset_quality_baselines": ("tenant_id", "dataset_id", "id"),
        "dataset_release_quality_certifications": ("tenant_id", "dataset_id", "id"),
        "dataset_release_quality_waivers": ("tenant_id", "dataset_id", "id"),
        "tenant_release_quality_scan_schedules": ("tenant_id", "dataset_id", "id"),
        "tenant_release_quality_scan_runs": ("tenant_id", "dataset_id", "id"),
        "dataset_release_quality_observations": ("tenant_id", "dataset_id", "id"),
    }
    parent_scopes: dict[str, set[tuple[str | None, ...]]] = {}
    parent_ids: dict[str, set[str]] = {}
    for table, fields in parent_specs.items():
        scoped, unscoped = _operations_parent_snapshot(rows, table, fields)
        parent_scopes[table] = scoped
        parent_ids[table] = unscoped
    return parent_scopes, parent_ids


def _operations_validate_row_facts(
    report: dict[str, Any],
    rows: Mapping[str, list[dict[str, Any]]],
) -> None:
    for bucket in (report["orphan_ids"], report["cross_tenant_ids"]):
        bucket.setdefault("slo_policies", [])

    parent_scopes, parent_ids = _operations_parent_indexes(rows)
    policy_rows = rows["tenant_release_quality_slo_policies"]
    schedule_rows = rows["tenant_release_quality_scan_schedules"]
    run_rows = rows["tenant_release_quality_scan_runs"]
    observation_rows = rows["dataset_release_quality_observations"]
    alert_rows = rows["dataset_release_quality_alerts"]
    job_rows = rows["dataset_release_recertification_jobs"]

    for row in policy_rows:
        identifier = _operations_row_id(row, "id")
        _operations_check_reference(
            report,
            rows,
            parent_scopes,
            parent_ids,
            "slo_policies",
            row,
            "tenants",
            (_operations_value(row, "tenant_id"),),
        )
        if _operations_value(row, "channel_id") is not None:
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "slo_policies",
                row,
                "tenant_release_channels",
                (_operations_value(row, "tenant_id"), _operations_value(row, "channel_id")),
            )
        if (_operations_value(row, "status") or "").casefold() == "active":
            report["identities"]["slo_policy"]["checked"] = True
            canonical_key = _operations_canonical_slo_key(row)
            if canonical_key is None or _operations_value(row, "active_scope_key") != canonical_key:
                _append_unique(report["identities"]["slo_policy"]["invalid_ids"], identifier)

    for row in schedule_rows:
        identifier = _operations_row_id(row, "id")
        report["dataset_scope"]["schedule"]["checked"] = True
        if not all(
            _operations_present(_operations_value(row, field))
            for field in ("tenant_id", "dataset_id", "slo_policy_id")
        ):
            _append_unique(report["dataset_scope"]["schedule"]["invalid_ids"], identifier)
        for parent_table, values in (
            ("tenants", (_operations_value(row, "tenant_id"),)),
            (
                "datasets",
                (_operations_value(row, "tenant_id"), _operations_value(row, "dataset_id")),
            ),
            (
                "tenant_release_quality_slo_policies",
                (_operations_value(row, "tenant_id"), _operations_value(row, "slo_policy_id")),
            ),
        ):
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "schedules",
                row,
                parent_table,
                values,
            )
        if (_operations_value(row, "status") or "").casefold() == "active":
            report["identities"]["schedule"]["checked"] = True
            if _operations_canonical_schedule_key(row) is None:
                _append_unique(report["identities"]["schedule"]["invalid_ids"], identifier)

    for row in run_rows:
        identifier = _operations_row_id(row, "id")
        report["dataset_scope"]["scan_run"]["checked"] = True
        if not all(
            _operations_present(_operations_value(row, field))
            for field in ("tenant_id", "dataset_id", "schedule_id", "slo_policy_id")
        ):
            _append_unique(report["dataset_scope"]["scan_run"]["invalid_ids"], identifier)
        for parent_table, values in (
            ("tenants", (_operations_value(row, "tenant_id"),)),
            (
                "datasets",
                (_operations_value(row, "tenant_id"), _operations_value(row, "dataset_id")),
            ),
            (
                "tenant_release_quality_scan_schedules",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "schedule_id"),
                ),
            ),
            (
                "tenant_release_quality_slo_policies",
                (_operations_value(row, "tenant_id"), _operations_value(row, "slo_policy_id")),
            ),
        ):
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "scan_runs",
                row,
                parent_table,
                values,
            )

    for row in observation_rows:
        identifier = _operations_row_id(row, "id")
        for parent_table, values, optional in (
            ("tenants", (_operations_value(row, "tenant_id"),), False),
            (
                "datasets",
                (_operations_value(row, "tenant_id"), _operations_value(row, "dataset_id")),
                False,
            ),
            (
                "tenant_release_quality_scan_runs",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "scan_run_id"),
                ),
                False,
            ),
            (
                "dataset_release_manifests",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "release_id"),
                ),
                False,
            ),
            (
                "tenant_release_channels",
                (_operations_value(row, "tenant_id"), _operations_value(row, "channel_id")),
                False,
            ),
            (
                "tenant_release_quality_slo_policies",
                (_operations_value(row, "tenant_id"), _operations_value(row, "slo_policy_id")),
                False,
            ),
            (
                "dataset_release_quality_certifications",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "certification_id"),
                ),
                True,
            ),
            (
                "dataset_release_quality_waivers",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "waiver_id"),
                ),
                True,
            ),
        ):
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "observations",
                row,
                parent_table,
                values,
                optional=optional,
            )
        if not _quality_digest(row.get("observation_digest")):
            _append_unique(
                report["authority_consistency"]["observation_digest_invalid_ids"], identifier
            )
        certification_fields = (
            "certification_id",
            "certification_digest",
            "certification_valid_until",
            "minutes_to_certification_expiry",
        )
        certification_presence = [
            _operations_present(_operations_value(row, field)) for field in certification_fields
        ]
        if any(certification_presence) and not all(certification_presence):
            _append_unique(
                report["authority_consistency"]["observation_envelope_invalid_ids"], identifier
            )
        waiver_fields = (
            "waiver_id",
            "waiver_digest",
            "waiver_expires_at",
            "minutes_to_waiver_expiry",
        )
        waiver_presence = [
            _operations_present(_operations_value(row, field)) for field in waiver_fields
        ]
        if any(waiver_presence) and not all(waiver_presence):
            _append_unique(
                report["authority_consistency"]["observation_envelope_invalid_ids"], identifier
            )

    for row in alert_rows:
        identifier = _operations_row_id(row, "id")
        for parent_table, values in (
            ("tenants", (_operations_value(row, "tenant_id"),)),
            (
                "datasets",
                (_operations_value(row, "tenant_id"), _operations_value(row, "dataset_id")),
            ),
            (
                "dataset_release_manifests",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "release_id"),
                ),
            ),
            (
                "tenant_release_channels",
                (_operations_value(row, "tenant_id"), _operations_value(row, "channel_id")),
            ),
        ):
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "alerts",
                row,
                parent_table,
                values,
            )
        source_observation_id = _operations_value(row, "source_observation_id")
        if source_observation_id is not None:
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "alerts",
                row,
                "dataset_release_quality_observations",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    source_observation_id,
                ),
            )
        if not _quality_digest(row.get("source_observation_digest")):
            _append_unique(
                report["authority_consistency"]["alert_source_digest_invalid_ids"], identifier
            )
        report["identities"]["alert"]["checked"] = True
        if _operations_canonical_alert_key(row) in {
            "__invalid_resolved__",
            "__invalid_status__",
            "__invalid_identity__",
        }:
            _append_unique(report["identities"]["alert"]["invalid_ids"], identifier)

    for row in job_rows:
        identifier = _operations_row_id(row, "id")
        for parent_table, values, optional in (
            ("tenants", (_operations_value(row, "tenant_id"),), False),
            (
                "datasets",
                (_operations_value(row, "tenant_id"), _operations_value(row, "dataset_id")),
                False,
            ),
            (
                "dataset_release_manifests",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "release_id"),
                ),
                False,
            ),
            (
                "tenant_release_channels",
                (_operations_value(row, "tenant_id"), _operations_value(row, "channel_id")),
                False,
            ),
            (
                "dataset_quality_baselines",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "baseline_id"),
                ),
                False,
            ),
            (
                "tenant_release_quality_gate_policies",
                (_operations_value(row, "tenant_id"), _operations_value(row, "policy_id")),
                False,
            ),
            (
                "tenant_release_quality_slo_policies",
                (_operations_value(row, "tenant_id"), _operations_value(row, "slo_policy_id")),
                False,
            ),
            (
                "dataset_release_quality_certifications",
                (
                    _operations_value(row, "tenant_id"),
                    _operations_value(row, "dataset_id"),
                    _operations_value(row, "result_certification_id"),
                ),
                True,
            ),
        ):
            _operations_check_reference(
                report,
                rows,
                parent_scopes,
                parent_ids,
                "jobs",
                row,
                parent_table,
                values,
                optional=optional,
            )
        cycle_key = _operations_value(row, "cycle_key")
        if (
            cycle_key is None
            or cycle_key != cycle_key.casefold()
            or _SHA256_RE.fullmatch(cycle_key) is None
        ):
            _append_unique(report["cycle_key"]["invalid_ids"], identifier)
        report["identities"]["job"]["checked"] = True
        if _operations_canonical_job_key(row) in {
            "__invalid_terminal__",
            "__invalid_status__",
            "__invalid_identity__",
        }:
            _append_unique(report["identities"]["job"]["invalid_ids"], identifier)
        for field in (
            "expected_manifest_digest",
            "expected_evidence_digest",
            "idempotency_key_digest",
            "request_hash",
        ):
            value = _operations_value(row, field)
            if value is None or value != value.casefold() or _SHA256_RE.fullmatch(value) is None:
                _append_unique(
                    report["authority_consistency"]["job_digest_invalid_ids"], identifier
                )
                break


def _operations_validate_leases_and_duplicates(
    report: dict[str, Any],
    rows: Mapping[str, list[dict[str, Any]]],
) -> None:
    run_rows = rows["tenant_release_quality_scan_runs"]
    job_rows = rows["dataset_release_recertification_jobs"]
    lease_report = report["lease_terminal_consistency"]
    lease_report["checked"] = True

    def scan_run_is_consistent(row: Mapping[str, Any]) -> bool:
        status = (_operations_value(row, "status") or "").casefold()
        has_owner = _operations_present(_operations_value(row, "claim_owner"))
        has_lease = _operations_present(_operations_value(row, "claim_lease_until"))
        has_heartbeat = _operations_present(_operations_value(row, "heartbeat_at"))
        has_started = _operations_present(_operations_value(row, "started_at"))
        has_finished = _operations_present(_operations_value(row, "finished_at"))
        if status == "pending":
            return not any((has_owner, has_lease, has_heartbeat, has_finished))
        if status == "claimed":
            return has_owner and has_lease and not has_heartbeat and not has_finished
        if status == "running":
            return has_owner and has_lease and has_heartbeat and has_started and not has_finished
        if status in RELEASE_QUALITY_OPERATIONS_TERMINAL_RUN_STATUSES:
            return has_finished and not any((has_owner, has_lease, has_heartbeat))
        return False

    for row in run_rows:
        if not scan_run_is_consistent(row):
            _append_unique(lease_report["scan_run_ids"], _operations_row_id(row, "id"))

    def job_is_consistent(row: Mapping[str, Any]) -> bool:
        status = (_operations_value(row, "status") or "").casefold()
        has_owner = _operations_present(_operations_value(row, "claim_owner"))
        has_lease = _operations_present(_operations_value(row, "claim_lease_until"))
        has_heartbeat = _operations_present(_operations_value(row, "heartbeat_at"))
        has_completed = _operations_present(_operations_value(row, "completed_at"))
        has_result = _operations_present(_operations_value(row, "result_certification_id"))
        has_cancelled = _operations_present(_operations_value(row, "cancelled_at"))
        has_cancelled_by = _operations_present(_operations_value(row, "cancelled_by"))
        if status == "claimed":
            return has_owner and has_lease and not any((has_completed, has_result, has_cancelled))
        if status == "completed":
            return (
                has_completed
                and has_result
                and not any((has_owner, has_lease, has_heartbeat, has_cancelled, has_cancelled_by))
            )
        if status == "cancelled":
            return (
                has_cancelled
                and has_cancelled_by
                and not any((has_owner, has_lease, has_heartbeat, has_completed, has_result))
            )
        if status in {"pending", "awaiting_evidence", "ready_to_certify", "failed"}:
            return not any(
                (
                    has_owner,
                    has_lease,
                    has_heartbeat,
                    has_completed,
                    has_result,
                    has_cancelled,
                    has_cancelled_by,
                )
            )
        return False

    for row in job_rows:
        if not job_is_consistent(row):
            _append_unique(lease_report["recertification_job_ids"], _operations_row_id(row, "id"))

    identity_specs = (
        (
            "slo_policy",
            rows["tenant_release_quality_slo_policies"],
            lambda row: (
                _scoped_key(
                    _operations_value(row, "tenant_id") or "",
                    _operations_canonical_slo_key(row) or "",
                )
                if _operations_canonical_slo_key(row) is not None
                else None
            ),
            lambda row: _operations_row_id(row, "id"),
        ),
        (
            "schedule",
            rows["tenant_release_quality_scan_schedules"],
            _operations_canonical_schedule_key,
            lambda row: _operations_row_id(row, "id"),
        ),
        (
            "alert",
            rows["dataset_release_quality_alerts"],
            _operations_canonical_alert_key,
            lambda row: _scoped_key(
                _operations_value(row, "tenant_id") or "",
                _operations_value(row, "active_alert_key") or "",
            ),
        ),
        (
            "job",
            job_rows,
            _operations_canonical_job_key,
            lambda row: _scoped_key(
                _operations_value(row, "tenant_id") or "",
                _operations_value(row, "active_job_key") or "",
            ),
        ),
    )
    for name, source_rows, key_factory, id_factory in identity_specs:
        duplicate_ids, duplicate_keys = _operations_duplicate_ids(
            source_rows, key_factory, id_factory
        )
        for identifier in duplicate_ids:
            _append_unique(report["identities"][name]["duplicate_ids"], identifier)
        for key in duplicate_keys:
            _append_unique(report["identities"][name]["duplicate_keys"], key)

    cycle_ids, cycle_keys = _operations_duplicate_ids(
        job_rows,
        lambda row: (
            _scoped_key(
                _operations_value(row, "tenant_id") or "",
                _operations_value(row, "cycle_key") or "",
            )
            if _operations_present(_operations_value(row, "cycle_key"))
            else None
        ),
        lambda row: _operations_row_id(row, "id"),
    )
    for identifier in cycle_ids:
        _append_unique(report["cycle_key"]["duplicate_ids"], identifier)
    for key in cycle_keys:
        _append_unique(report["cycle_key"]["duplicate_keys"], key)
    report["cycle_key"]["checked"] = True


def _operations_add_blockers(report: dict[str, Any]) -> None:
    identities = report["identities"]
    if any(identities[name]["invalid_ids"] for name in identities):
        _operations_block(
            report,
            "quality_operations_canonical_identity_invalid",
            "canonical active SLO/Schedule/Alert/Job identity blockers were found",
        )
    if any(identities[name]["duplicate_ids"] for name in identities):
        _operations_block(
            report,
            "quality_operations_duplicate_active_identity",
            "duplicate active SLO/Schedule/Alert/Job identities were found",
        )
    if report["cycle_key"]["invalid_ids"]:
        _operations_block(
            report,
            "quality_operations_cycle_key_invalid",
            "invalid recertification cycle_key facts were found",
        )
    if report["cycle_key"]["duplicate_ids"]:
        _operations_block(
            report,
            "quality_operations_duplicate_cycle_key",
            "duplicate recertification cycle_key facts were found",
        )
    lease_report = report["lease_terminal_consistency"]
    if lease_report["scan_run_ids"] or lease_report["recertification_job_ids"]:
        _operations_block(
            report,
            "quality_operations_lease_terminal_inconsistent",
            "Scan Run or Recertification Job lease/terminal consistency blockers were found",
        )
    if any(report["orphan_ids"].values()):
        _operations_block(
            report,
            "quality_operations_orphan_authority",
            "orphan Release Quality Operations authority was found",
        )
    if any(report["cross_tenant_ids"].values()):
        _operations_block(
            report,
            "quality_operations_cross_tenant_authority",
            "cross-Tenant Release Quality Operations authority was found",
        )
    if any(report["authority_consistency"].values()):
        _operations_block(
            report,
            "quality_operations_authority_consistency",
            "Release Quality Operations authority digest or envelope blockers were found",
        )


def _release_quality_operations_preflight_report(
    engine: Engine,
    tables: set[str],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    """Inspect Stage21 authority with SELECT/metadata reads only."""

    report, rows = _operations_prepare_report(engine, tables, revision=revision)
    if rows is None:
        return report
    _operations_validate_row_facts(report, rows)
    _operations_validate_leases_and_duplicates(report, rows)
    _operations_add_blockers(report)
    return _operations_finish_report(report)


def _empty_notification_center_preflight_report() -> dict[str, Any]:
    return {
        "checked": False,
        "available": False,
        "target_revision": STAGE22_TARGET_REVISION,
        "required_tables": list(NOTIFICATION_CENTER_TABLES),
        "missing_tables": [],
        "table_counts": {table: None for table in NOTIFICATION_CENTER_TABLES},
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "blockers": [],
        "reasons": [],
        "safe": True,
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
    }


def _notification_center_preflight_report(
    engine: Engine,
    tables: set[str],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    report = _empty_notification_center_preflight_report()
    report["checked"] = True
    report["available"] = bool(set(NOTIFICATION_CENTER_TABLES) & tables)
    report["missing_tables"] = sorted(set(NOTIFICATION_CENTER_TABLES) - tables)
    if report["missing_tables"]:
        if report["available"] or revision == STAGE22_TARGET_REVISION:
            report["blockers"].append("notification_center_schema_incomplete")
            report["reasons"].append("Notification Center required tables are missing")
        report["safe"] = not report["blockers"]
        report["safe_to_upgrade"] = report["safe"]
        return report
    with engine.connect() as connection:
        report["table_counts"] = {
            table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
            for table in NOTIFICATION_CENTER_TABLES
        }
    state, issues = catalog_schema.inspect_enterprise_notification_center_capability(engine)
    report["schema_capability_state"] = state
    report["schema_capability_issues"] = list(issues)
    if state == "unavailable":
        for issue in issues or ("Notification Center authority is unavailable",):
            code = str(issue)
            if code not in report["blockers"]:
                report["blockers"].append(code)
                report["reasons"].append(code)
    report["safe"] = not report["blockers"]
    report["safe_to_upgrade"] = report["safe"]
    return report


def _empty_content_recovery_preflight_report() -> dict[str, Any]:
    return {
        "checked": False,
        "available": False,
        "target_revision": STAGE23_TARGET_REVISION,
        "required_tables": list(CONTENT_RECOVERY_TABLES),
        "missing_tables": [],
        "table_counts": {table: None for table in CONTENT_RECOVERY_TABLES},
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "blockers": [],
        "reasons": [],
        "safe": True,
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
    }


def _content_recovery_preflight_report(
    engine: Engine, tables: set[str], *, revision: str | None = None
) -> dict[str, Any]:
    report = _empty_content_recovery_preflight_report()
    report["checked"] = True
    report["available"] = bool(set(CONTENT_RECOVERY_TABLES) & tables)
    report["missing_tables"] = sorted(set(CONTENT_RECOVERY_TABLES) - tables)
    if report["missing_tables"]:
        if report["available"] or revision == STAGE23_TARGET_REVISION:
            report["blockers"].append("content_recovery_schema_incomplete")
            report["reasons"].append("Content Recovery required tables are missing")
        report["safe"] = not report["blockers"]
        report["safe_to_upgrade"] = report["safe"]
        return report
    with engine.connect() as connection:
        report["table_counts"] = {
            table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
            for table in CONTENT_RECOVERY_TABLES
        }
    state, issues = catalog_schema.inspect_enterprise_content_recovery_capability(engine)
    report["schema_capability_state"] = state
    report["schema_capability_issues"] = list(issues)
    if state == "unavailable":
        for issue in issues or ("Content Recovery authority is unavailable",):
            code = str(issue)
            if code not in report["blockers"]:
                report["blockers"].append(code)
                report["reasons"].append(code)
    report["safe"] = not report["blockers"]
    report["safe_to_upgrade"] = report["safe"]
    return report


def _empty_task_operations_preflight_report() -> dict[str, Any]:
    return {
        "checked": False,
        "available": False,
        "target_revision": STAGE24_TARGET_REVISION,
        "required_tables": list(TASK_OPERATIONS_TABLES),
        "missing_tables": [],
        "table_counts": {table: None for table in TASK_OPERATIONS_TABLES},
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "blockers": [],
        "reasons": [],
        "safe": True,
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
        "reconcile_performed": False,
        "source_actions_dispatched": [],
    }


def _task_operations_preflight_report(
    engine: Engine, tables: set[str], *, revision: str | None = None
) -> dict[str, Any]:
    """Inspect the Stage24 projection authority without reconciling or dispatching actions."""

    report = _empty_task_operations_preflight_report()
    report["checked"] = True
    report["available"] = bool(set(TASK_OPERATIONS_TABLES) & tables)
    report["missing_tables"] = sorted(set(TASK_OPERATIONS_TABLES) - tables)
    if report["missing_tables"]:
        if report["available"] or revision == STAGE24_TARGET_REVISION:
            report["blockers"].append("task_operations_schema_incomplete")
            report["reasons"].append("Task Operations required tables are missing")
        report["safe"] = not report["blockers"]
        report["safe_to_upgrade"] = report["safe"]
        return report
    with engine.connect() as connection:
        report["table_counts"] = {
            table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
            for table in TASK_OPERATIONS_TABLES
        }
    inspector = getattr(catalog_schema, "inspect_enterprise_task_operations_capability", None)
    if inspector is None:
        state, issues = "unavailable", ("task_operations_capability_missing",)
    else:
        state, issues = inspector(engine)
    report["schema_capability_state"] = state
    report["schema_capability_issues"] = list(issues)
    if state == "unavailable":
        for issue in issues or ("Task Operations authority is unavailable",):
            code = str(issue)
            if code not in report["blockers"]:
                report["blockers"].append(code)
                report["reasons"].append(code)
    report["safe"] = not report["blockers"]
    report["safe_to_upgrade"] = report["safe"]
    return report


def _empty_automation_workflows_preflight_report() -> dict[str, Any]:
    table_counts = {table: None for table in AUTOMATION_WORKFLOWS_TABLES}
    return {
        "checked": False,
        "available": False,
        "schema_status": "not_available",
        "target_revision": STAGE25_TARGET_REVISION,
        "required_tables": list(AUTOMATION_WORKFLOWS_TABLES),
        "missing_tables": [],
        "missing_columns": {},
        "table_counts": table_counts,
        "counts": table_counts,
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "blockers": [],
        "reasons": [],
        "safe": True,
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
        "source_observation_performed": False,
        "cursor_advanced": False,
        "action_dispatches": [],
    }


def _automation_workflows_preflight_report(
    engine: Engine, tables: set[str], *, revision: str | None = None
) -> dict[str, Any]:
    """Inspect the Stage25 Automation authority without observing or dispatching anything."""

    report = _empty_automation_workflows_preflight_report()
    present_tables = set(AUTOMATION_WORKFLOWS_TABLES) & tables
    if not present_tables and revision not in {None, STAGE25_TARGET_REVISION}:
        return report
    if revision is None and not present_tables:
        return report

    report["checked"] = True
    report["available"] = bool(present_tables)
    report["missing_tables"] = sorted(set(AUTOMATION_WORKFLOWS_TABLES) - tables)
    inspector = inspect(engine)
    for table in sorted(present_tables):
        actual_columns = {str(item["name"]) for item in inspector.get_columns(table)}
        missing = sorted(
            set(catalog_schema.ENTERPRISE_AUTOMATION_WORKFLOWS_REQUIRED_COLUMNS[table])
            - actual_columns
        )
        if missing:
            report["missing_columns"][table] = missing

    if report["missing_tables"] or report["missing_columns"]:
        report["schema_status"] = "partial"
        report["blockers"] = ["automation_workflows_schema_incomplete"]
        report["reasons"] = ["0035 Automation Workflows tables or columns are incomplete"]
        report["safe"] = False
        report["safe_to_upgrade"] = False
        return report

    report["available"] = True
    report["schema_status"] = (
        "current" if revision in {None, STAGE25_TARGET_REVISION} else "present_before_revision"
    )
    if revision not in {None, STAGE25_TARGET_REVISION}:
        report["blockers"].append("automation_workflows_revision_mismatch")
        report["reasons"].append(
            "0035 Automation Workflows tables exist before the catalog revision"
        )

    try:
        capability_state, capability_issues = (
            catalog_schema.inspect_enterprise_automation_workflows_capability(engine)
        )
        report["schema_capability_state"] = capability_state
        report["schema_capability_issues"] = list(capability_issues)
        if capability_state != "ready":
            report["blockers"].append("automation_workflows_schema_capability")
            report["reasons"].append("0035 Automation Workflows schema capability is unavailable")
    except Exception as exc:
        report["schema_capability_state"] = "unavailable"
        report["schema_capability_issues"] = [f"schema inspection failed: {exc.__class__.__name__}"]
        report["blockers"].append("automation_workflows_schema_capability")
        report["reasons"].append("0035 Automation Workflows schema inspection failed")

    with engine.connect() as connection:
        counts = {
            table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
            for table in AUTOMATION_WORKFLOWS_TABLES
        }
    report["table_counts"] = counts
    report["counts"] = counts
    report["safe"] = not report["blockers"]
    report["safe_to_upgrade"] = report["safe"]
    return report



def _empty_knowledge_serving_preflight_report() -> dict[str, Any]:
    counts = {table: None for table in KNOWLEDGE_SERVING_RELIABILITY_TABLES}
    return {
        "checked": False,
        "available": False,
        "schema_status": "not_available",
        "target_revision": STAGE26_TARGET_REVISION,
        "required_tables": list(KNOWLEDGE_SERVING_RELIABILITY_TABLES),
        "missing_tables": [],
        "missing_columns": {},
        "counts": counts,
        "table_counts": counts,
        "schema_capability_state": "not_checked",
        "schema_capability_issues": [],
        "blockers": [],
        "reasons": [],
        "safe": True,
        "safe_to_upgrade": True,
        "read_only": True,
        "mutations_performed": False,
        "snapshot_observation_performed": False,
        "source_state_advanced": False,
    }


def _knowledge_serving_preflight_report(
    engine: Engine, tables: set[str], *, revision: str | None = None
) -> dict[str, Any]:
    """Inspect Stage26 without recording a snapshot or advancing any source."""

    report = _empty_knowledge_serving_preflight_report()
    present = set(KNOWLEDGE_SERVING_RELIABILITY_TABLES) & set(tables)
    if not present and revision not in {None, STAGE26_TARGET_REVISION}:
        return report
    if revision is None and not present:
        return report
    report["checked"] = True
    report["available"] = bool(present)
    report["missing_tables"] = sorted(set(KNOWLEDGE_SERVING_RELIABILITY_TABLES) - set(tables))
    if report["missing_tables"]:
        report["schema_status"] = "partial"
        report["blockers"] = ["knowledge_serving_reliability_schema_incomplete"]
        report["reasons"] = ["0036 Knowledge Serving required tables are missing"]
        report["safe"] = False
        report["safe_to_upgrade"] = False
        return report
    if revision not in {None, STAGE26_TARGET_REVISION}:
        report["schema_status"] = "revision_mismatch"
        report["blockers"] = ["knowledge_serving_reliability_revision_mismatch"]
        report["reasons"] = ["0036 Knowledge Serving tables exist before the catalog revision"]
        report["safe"] = False
        report["safe_to_upgrade"] = False
        return report

    with engine.connect() as connection:
        counts = {
            table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
            for table in KNOWLEDGE_SERVING_RELIABILITY_TABLES
        }
    report["counts"] = counts
    report["table_counts"] = counts
    inspector = getattr(catalog_schema, "inspect_enterprise_knowledge_serving_reliability_capability", None)
    if inspector is None:
        state, issues = "unavailable", ("knowledge_serving_reliability_capability_missing",)
    else:
        state, issues = inspector(engine)
    report["schema_capability_state"] = state
    report["schema_capability_issues"] = list(issues)
    if state != "ready":
        report["schema_status"] = "unavailable"
        report["blockers"] = ["knowledge_serving_reliability_schema_capability"]
        report["reasons"] = ["0036 Knowledge Serving schema capability is unavailable"]
    else:
        report["schema_status"] = "current"
    report["safe"] = not report["blockers"]
    report["safe_to_upgrade"] = report["safe"]
    return report

def run_preflight(
    database_url: str,
    *,
    engine: Engine | None = None,
    target_label: str | None = None,
    backup_dir: str | Path | None = None,
    min_free_bytes: int = 0,
    expected_current: str | None = None,
    require_mysql: bool = True,
    head_revision: str = HEAD_REVISION,
) -> dict[str, Any]:
    """Run catalog, Stage19 and Stage20 safety checks using read-only queries."""

    url_report = inspect_url_safety(
        database_url,
        target_label=target_label,
        require_mysql=require_mysql,
        require_target_label=True,
    )
    disk = (
        check_disk_target(backup_dir, min_free_bytes=min_free_bytes)
        if backup_dir is not None
        else {"checked": False, "safe": False, "reasons": ["backup directory was not supplied"]}
    )
    schema: dict[str, Any] = {
        "status": "not_checked",
        "revision": None,
        "head_revision": head_revision,
        "missing_tables": [],
        "missing_baseline_tables": [],
    }
    roles: dict[str, Any] = {
        "checked": False,
        "known": sorted(KNOWN_ROLES),
        "counts": {},
        "unknown": [],
    }
    owners: dict[str, Any] = {"checked": False, "active_tenants": 0, "ownerless_active_tenants": 0}
    invitations: dict[str, Any] = {
        "checked": False,
        "duplicate_pending": [],
        "duplicate_token_hashes": [],
    }
    registry: dict[str, Any] = {"checked": False, "blockers": []}
    knowledge_base_releases = _empty_knowledge_base_releases_preflight_report()
    release_quality = _empty_release_quality_preflight_report()
    release_quality_operations = _empty_release_quality_operations_preflight_report()
    notification_center = _empty_notification_center_preflight_report()
    content_recovery = _empty_content_recovery_preflight_report()
    task_operations = _empty_task_operations_preflight_report()
    automation_workflows = _empty_automation_workflows_preflight_report()
    knowledge_serving_reliability = _empty_knowledge_serving_preflight_report()
    database_error: str | None = None
    active_engine = engine
    owned = False
    try:
        if active_engine is None:
            if not url_report.safe:
                database_error = "database connection skipped because URL safety failed"
            else:
                active_engine = _create_read_only_preflight_engine(database_url)
                owned = True
        if active_engine is not None and database_error is None and not _preflight_engine_read_only_proven(active_engine):
            database_error = "physical read-only access could not be proven"
        if active_engine is not None and database_error is None:
            tables = set(inspect(active_engine).get_table_names())
            revision = _read_revision(active_engine)
            status, missing_head, missing_baseline = _schema_status(tables, revision, head_revision)
            schema.update(
                {
                    "status": status,
                    "revision": revision,
                    "missing_tables": missing_head,
                    "missing_baseline_tables": missing_baseline,
                }
            )
            roles, owners = _role_owner_report(active_engine, tables)
            invitations = _invitation_preflight_report(active_engine, tables)
            registry = _knowledge_base_registry_preflight_report(active_engine, tables)
            knowledge_base_releases = _knowledge_base_releases_preflight_report(
                active_engine, tables, revision=revision
            )
            release_quality = _release_quality_preflight_report(
                active_engine, tables, revision=revision
            )
            release_quality_operations = _release_quality_operations_preflight_report(
                active_engine, tables, revision=revision
            )
            notification_center = _notification_center_preflight_report(
                active_engine, tables, revision=revision
            )
            content_recovery = _content_recovery_preflight_report(
                active_engine, tables, revision=revision
            )
            task_operations = _task_operations_preflight_report(
                active_engine, tables, revision=revision
            )
            automation_workflows = _automation_workflows_preflight_report(
                active_engine, tables, revision=revision
            )
            knowledge_serving_reliability = _knowledge_serving_preflight_report(
                active_engine, tables, revision=revision
            )
            quality_capability_state, quality_capability_issues = (
                catalog_schema.inspect_enterprise_release_quality_certification_capability(
                    active_engine
                )
            )
            release_quality["schema_capability_state"] = quality_capability_state
            release_quality["schema_capability_issues"] = list(quality_capability_issues)
            if quality_capability_state == "ready":
                from sqlalchemy.orm import Session

                from core.enterprise_release_quality_service import _certification_is_current
                from models.orm import DatasetReleaseQualityCertification

                with Session(active_engine) as quality_session:
                    for certification in quality_session.scalars(
                        select(DatasetReleaseQualityCertification).order_by(
                            DatasetReleaseQualityCertification.tenant_id,
                            DatasetReleaseQualityCertification.dataset_id,
                            DatasetReleaseQualityCertification.id,
                        )
                    ):
                        if not _certification_is_current(
                            quality_session, certification, lock_evidence=False
                        ):
                            scoped_id = (
                                f"{certification.tenant_id}:{certification.dataset_id}:"
                                f"{certification.id}"
                            )
                            stale = release_quality.setdefault("stale_certification_ids", [])
                            if scoped_id not in stale:
                                stale.append(scoped_id)
                            blockers = release_quality.setdefault("blockers", [])
                            if "certification_stale" not in blockers:
                                blockers.append("certification_stale")
            if quality_capability_state == "unavailable":
                blockers = release_quality.setdefault("blockers", [])
                if "release_quality_schema_capability" not in blockers:
                    blockers.append("release_quality_schema_capability")
    except Exception:
        database_error = "database inspection failed"
    finally:
        if owned and active_engine is not None:
            active_engine.dispose()

    reasons = list(url_report.reasons)
    if database_error:
        reasons.append(database_error)
    if expected_current and schema["revision"] != expected_current:
        reasons.append("current revision does not match expected-current")
    if schema["status"] != "behind":
        reasons.append("catalog is not at a known pre-head revision")
    if schema["missing_baseline_tables"]:
        reasons.append("baseline catalog tables are missing")
    if roles["unknown"]:
        reasons.append("unknown tenant member roles were found")
    if owners["checked"] and owners["ownerless_active_tenants"]:
        reasons.append("an active tenant has no owner")
    if invitations["duplicate_pending"]:
        reasons.append("duplicate pending tenant invitations were found")
    if invitations["duplicate_token_hashes"]:
        reasons.append("duplicate tenant invitation token hashes were found")
    if registry.get("blockers"):
        reasons.append("knowledge base registry preflight blockers were found")
    if knowledge_base_releases.get("blockers"):
        reasons.append("knowledge base releases preflight blockers were found")
    if release_quality.get("blockers"):
        reasons.append("release quality preflight blockers were found")
    if release_quality_operations.get("blockers"):
        reasons.append("release quality operations preflight blockers were found")
    if notification_center.get("blockers"):
        reasons.append("notification center preflight blockers were found")
    if content_recovery.get("blockers"):
        reasons.append("content recovery preflight blockers were found")
    if task_operations.get("blockers"):
        reasons.append("task operations preflight blockers were found")
    if automation_workflows.get("blockers"):
        reasons.append("automation workflows preflight blockers were found")
    if knowledge_serving_reliability.get("blockers"):
        reasons.append("knowledge serving reliability preflight blockers were found")
    if not disk.get("safe", False):
        reasons.extend(disk.get("reasons", []))
    return {
        "url": url_report.to_dict(),
        "schema": schema,
        "revision": schema["revision"],
        "head_revision": head_revision,
        "roles": roles,
        "owners": owners,
        "invitations": invitations,
        "registry": registry,
        "knowledge_base_releases": knowledge_base_releases,
        "release_quality": release_quality,
        "release_quality_operations": release_quality_operations,
        "notification_center": notification_center,
        "content_recovery": content_recovery,
        "task_operations": task_operations,
        "automation_workflows": automation_workflows,
        "knowledge_serving_reliability": knowledge_serving_reliability,
        "disk": disk,
        "database_error": database_error,
        "safe_to_upgrade": not reasons,
        "reasons": reasons,
        "read_only": True,
        "mutations_performed": False,
        "automatic_actions": [],
    }


def _powershell_quote(value: str) -> str:
    _single_line(value, "shell argument")
    return "'" + value.replace("'", "''") + "'"


def _backup_url(raw_url: str) -> URL:
    report = inspect_url_safety(
        raw_url,
        require_mysql=True,
        require_target_label=False,
    )
    if not report.safe:
        raise SafetyGateError("unsafe MySQL URL: " + "; ".join(report.reasons))
    return make_url(raw_url)


def build_backup_command(
    database_url: str,
    output_path: str | Path,
    *,
    shell: str = "powershell",
    defaults_extra_file: str | Path | None = None,
) -> str:
    """Render, but never execute, a password-free mysqldump command."""

    if shell not in {"powershell", "posix"}:
        raise SafetyGateError("shell must be powershell or posix")
    url = _backup_url(database_url)
    output = _single_line(str(Path(output_path).expanduser()), "backup output")
    tokens = [
        "mysqldump",
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        "--hex-blob",
        "--set-gtid-purged=OFF",
    ]
    if defaults_extra_file is not None:
        defaults_file = _single_line(
            str(Path(defaults_extra_file).expanduser()), "defaults-extra-file"
        )
        tokens.insert(1, f"--defaults-extra-file={defaults_file}")
    if url.host:
        tokens.extend(["--host", url.host])
    if url.port:
        tokens.extend(["--port", str(url.port)])
    if url.username:
        tokens.extend(["--user", url.username])
    tokens.append(url.database or "")
    if shell == "powershell":
        rendered = " ".join(_powershell_quote(token) for token in tokens)
        rendered += " | Out-File -FilePath " + _powershell_quote(output)
    else:
        rendered = " ".join(shlex.quote(token) for token in tokens)
        rendered += " > " + shlex.quote(output)
    return (
        "# Set MYSQL_PWD only in the process environment before running; "
        "never put the password in this command or a log.\n" + rendered
    )


def _validate_sha256(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _SHA256_RE.fullmatch(normalized):
        raise SafetyGateError("sha256 must be exactly 64 hexadecimal characters")
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(path: str | Path, expected_sha256: str) -> dict[str, Any]:
    """Verify a plain-text dump artifact without opening a database."""

    expected = _validate_sha256(expected_sha256)
    backup = Path(path).expanduser()
    base = {"path": str(backup), "expected_sha256": expected}
    if not backup.exists():
        return {
            **base,
            "verified": False,
            "exists": False,
            "is_file": False,
            "size_bytes": 0,
            "sha256": None,
            "sha256_match": False,
            "dump_markers": False,
            "reasons": ["backup file does not exist"],
        }
    if not backup.is_file():
        return {
            **base,
            "verified": False,
            "exists": True,
            "is_file": False,
            "size_bytes": 0,
            "sha256": None,
            "sha256_match": False,
            "dump_markers": False,
            "reasons": ["backup path is not a regular file"],
        }
    size = backup.stat().st_size
    reasons: list[str] = []
    if size == 0:
        reasons.append("backup file is empty")
    sample_size = 128 * 1024
    with backup.open("rb") as stream:
        head = stream.read(sample_size)
        if size > sample_size:
            stream.seek(max(0, size - sample_size))
        tail = stream.read(sample_size)
    sample = head + tail
    has_header = b"-- MySQL dump" in sample
    has_body = any(marker in sample for marker in (b"CREATE TABLE", b"INSERT INTO", b"SET NAMES"))
    has_footer = b"-- Dump completed" in sample
    markers = has_header and (has_body or has_footer)
    if not markers:
        reasons.append("basic mysqldump markers are missing")
    actual = _sha256_file(backup)
    matches = actual == expected
    if not matches:
        reasons.append("sha256 does not match the supplied digest")
    return {
        **base,
        "verified": not reasons,
        "exists": True,
        "is_file": True,
        "size_bytes": size,
        "sha256": actual,
        "sha256_match": matches,
        "dump_markers": markers,
        "reasons": reasons,
    }


def _default_alembic_runner(config: Any, target_revision: str) -> None:
    command.upgrade(config, target_revision)


def run_upgrade(
    database_url: str,
    *,
    execute: bool = False,
    backup_path: str | Path | None = None,
    backup_sha256: str | None = None,
    expected_current: str | None = None,
    target_label: str | None = None,
    engine: Engine | None = None,
    preflight_provider: Callable[[], Mapping[str, Any]] | None = None,
    alembic_runner: Callable[[Any, str], None] | None = None,
    require_mysql: bool = True,
) -> dict[str, Any]:
    """Dry-run by default; execute only after all explicit gates pass."""

    if not execute:
        return {
            "mode": "dry-run",
            "executed": False,
            "safety_gates": {
                "execute_required": True,
                "backup_sha256_required": True,
                "expected_current_required": True,
                "second_preflight_required": True,
            },
            "plan": build_migration_plan(
                start_revision=expected_current or "0007_chunk_rev",
                target_revision="head",
            ),
        }

    missing: list[str] = []
    if not backup_sha256:
        missing.append("--backup-sha256 is required with --execute")
    if not expected_current:
        missing.append("--expected-current is required with --execute")
    if backup_path is None:
        missing.append("--backup is required with --execute")
    if missing:
        raise SafetyGateError("; ".join(missing))

    url_report = inspect_url_safety(
        database_url,
        target_label=target_label,
        require_mysql=require_mysql,
        require_target_label=True,
    )
    if not url_report.safe:
        raise SafetyGateError("unsafe upgrade target: " + "; ".join(url_report.reasons))
    backup = verify_backup(backup_path, backup_sha256 or "")
    if not backup["verified"]:
        raise SafetyGateError("backup verification failed")
    if preflight_provider is None:
        preflight = run_preflight(
            database_url,
            engine=engine,
            target_label=target_label,
            backup_dir=Path(backup_path).expanduser().parent,
            min_free_bytes=0,
            expected_current=expected_current,
            require_mysql=require_mysql,
        )
    else:
        preflight = dict(preflight_provider())
    if not preflight.get("safe_to_upgrade", False):
        raise SafetyGateError("preflight did not approve the upgrade")

    active_engine = engine
    owned = False
    try:
        if active_engine is None:
            active_engine = create_engine(database_url, pool_pre_ping=True)
            owned = True
        before = _read_revision(active_engine)
        if before != expected_current:
            raise SafetyGateError("current revision changed before upgrade")
        runner = alembic_runner or _default_alembic_runner
        runner(catalog_schema._alembic_config(database_url), "head")
        after = _read_revision(active_engine)
    except SafetyGateError:
        raise
    except Exception as exc:
        raise SafetyGateError(
            f"Alembic upgrade failed ({type(exc).__name__}); no automatic rollback was attempted"
        ) from exc
    finally:
        if owned and active_engine is not None:
            active_engine.dispose()
    if after != HEAD_REVISION:
        raise SafetyGateError(
            "post-upgrade revision verification failed; no automatic rollback was attempted"
        )
    return {
        "mode": "execute",
        "executed": True,
        "before_revision": before,
        "after_revision": after,
        "head_revision": HEAD_REVISION,
        "backup": backup,
        "preflight": preflight,
        "automatic_rollback": False,
    }


def run_rollback(
    database_url: str,
    *,
    current_revision: str,
    target_revision: str,
    backup_path: str | Path | None = None,
    backup_sha256: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Generate manual downgrade/recovery steps and never execute them."""

    if execute:
        raise SafetyGateError(
            "automatic rollback execution is prohibited; only manual operator steps are generated"
        )
    target = inspect_url_safety(
        database_url,
        require_mysql=True,
        require_target_label=False,
    )
    backup = None
    if backup_path is not None and backup_sha256:
        backup = verify_backup(backup_path, backup_sha256)
    plan = build_migration_plan(start_revision=target_revision, target_revision=current_revision)
    steps = [
        {"order": 1, "operator_action": "Manual: 人工停止写入、应用、worker 和定时任务"},
        {"order": 2, "operator_action": "Manual: 人工核对备份文件、SHA-256 和恢复演练记录"},
        {
            "order": 3,
            "operator_action": f"Manual: DBA 按审批单执行 Alembic downgrade {target_revision}；本工具不会执行",
        },
        {"order": 4, "operator_action": "Manual: 只读核验 revision、表结构、应用启动和抽样数据"},
        {"order": 5, "operator_action": "Manual: 若降级不可接受，DBA 按审批单人工恢复备份"},
    ]
    return {
        "executed": False,
        "automatic_restore": False,
        "automatic_downgrade": False,
        "target": target.to_dict(),
        "from_revision": current_revision,
        "to_revision": target_revision,
        "migration_plan": plan,
        "steps": steps,
        "backup_verification": backup,
    }


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _add_url(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url",
        default=os.getenv("RAG4C_CATALOG_URL", ""),
        help="SQLAlchemy MySQL URL；也可通过 RAG4C_CATALOG_URL 提供",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    preflight = sub.add_parser("preflight", help="只读检查 URL、schema、角色、owner 和磁盘")
    _add_url(preflight)
    preflight.add_argument("--target-label", default=os.getenv("RAG4C_CATALOG_TARGET_LABEL"))
    preflight.add_argument("--backup-dir", required=True)
    preflight.add_argument("--min-free-bytes", type=int, default=0)
    preflight.add_argument("--expected-current")

    backup = sub.add_parser("backup-command", help="只打印安全引用的 mysqldump 命令")
    _add_url(backup)
    backup.add_argument("--output", required=True)
    backup.add_argument("--shell", choices=("powershell", "posix"), default="powershell")
    backup.add_argument("--defaults-extra-file")

    verify = sub.add_parser("verify-backup", help="只读核验备份文件")
    verify.add_argument("--file", required=True)
    verify.add_argument("--sha256", required=True)

    plan = sub.add_parser("plan", help="列出逐迁移计划和人工回滚点")
    plan.add_argument("--from", dest="from_revision", default="0007_chunk_rev")
    plan.add_argument("--to", dest="to_revision", default="head")
    plan.add_argument("--maintenance-window-minutes", type=int)

    upgrade = sub.add_parser("upgrade", help="默认 dry-run；execute 才允许调用 Alembic")
    _add_url(upgrade)
    upgrade.add_argument("--execute", action="store_true")
    upgrade.add_argument("--backup", dest="backup_path")
    upgrade.add_argument("--backup-file", dest="backup_path", help=argparse.SUPPRESS)
    upgrade.add_argument("--backup-sha256")
    upgrade.add_argument("--expected-current")
    upgrade.add_argument("--target-label", default=os.getenv("RAG4C_CATALOG_TARGET_LABEL"))

    rollback = sub.add_parser("rollback", help="只生成人工回滚/恢复步骤")
    _add_url(rollback)
    rollback.add_argument("--current", dest="current_revision", required=True)
    rollback.add_argument("--to", dest="target_revision", required=True)
    rollback.add_argument("--backup", dest="backup_path")
    rollback.add_argument("--backup-sha256")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.subcommand == "preflight":
            report = run_preflight(
                args.url,
                target_label=args.target_label,
                backup_dir=args.backup_dir,
                min_free_bytes=args.min_free_bytes,
                expected_current=args.expected_current,
            )
            _print_json(report)
            return 0 if report["safe_to_upgrade"] else 2
        if args.subcommand == "backup-command":
            print(
                build_backup_command(
                    args.url,
                    args.output,
                    shell=args.shell,
                    defaults_extra_file=args.defaults_extra_file,
                )
            )
            return 0
        if args.subcommand == "verify-backup":
            report = verify_backup(args.file, args.sha256)
            _print_json(report)
            return 0 if report["verified"] else 1
        if args.subcommand == "plan":
            _print_json(
                build_migration_plan(
                    start_revision=args.from_revision,
                    target_revision=args.to_revision,
                    maintenance_window_minutes=args.maintenance_window_minutes,
                )
            )
            return 0
        if args.subcommand == "upgrade":
            _print_json(
                run_upgrade(
                    args.url,
                    execute=args.execute,
                    backup_path=args.backup_path,
                    backup_sha256=args.backup_sha256,
                    expected_current=args.expected_current,
                    target_label=args.target_label,
                )
            )
            return 0
        if args.subcommand == "rollback":
            _print_json(
                run_rollback(
                    args.url,
                    current_revision=args.current_revision,
                    target_revision=args.target_revision,
                    backup_path=args.backup_path,
                    backup_sha256=args.backup_sha256,
                )
            )
            return 0
    except SafetyGateError as exc:
        _print_json({"status": "blocked", "error": str(exc)})
        return 2
    except Exception:
        _print_json(
            {
                "status": "failed",
                "error": "operation failed; inspect operator records without exposing credentials",
            }
        )
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
