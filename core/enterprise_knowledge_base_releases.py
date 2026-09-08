"""Enterprise Knowledge Base Release manifest authority.

The pure canonicalization helpers in this module deliberately precede the
persistence service. They define the byte-stable, secret-safe contract that
0029 database rows and approval snapshots must share.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import re
import uuid
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_knowledge_base_release_capability
from core.enterprise_access_control import evaluate_dataset_permissions
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from models.orm import (
    Account,
    AppDatasetReference,
    DataSourceRecord,
    Dataset,
    DatasetChannelRelease,
    DatasetReleaseEntry,
    DatasetReleaseEvent,
    DatasetReleaseManifest,
    DatasetWorkspaceOwnership,
    Document,
    DocumentVersion,
    QAKnowledge,
    SourceSyncRun,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantReleaseChannel,
    TenantWorkspace,
)

_RELEASE_SCHEMA_VERSION = 1
_ALLOWED_RESOURCE_TYPES = frozenset(
    {
        "dataset_profile",
        "document_version",
        "qa_revision",
        "source_generation",
        "projection_revision",
    }
)
_ALLOWED_BLOCKER_SEVERITIES = frozenset({"blocked", "unavailable"})
_SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "private_key",
    "credential",
    "cookie",
)
_BODY_KEYS = frozenset(
    {
        "body",
        "document_body",
        "qa_body",
        "question",
        "answer",
        "source_content",
        "content",
        "raw_content",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|client[_-]?secret|(?:raw[_-]?|invite[_-]?|session[_-]?)?token)\s*[:=]\s*\S+|"
    r"bearer\s+\S+|secret://\S+|sk_(?:live|test)[-_]\S+|ghp_\S+|xox[baprs]-\S+"
    r")"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\+[a-z0-9_]+)?|"
    r"mongodb(?:\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\/\/\S+"
)
_JWT_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


class ReleaseManifestError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ReleaseManifestInvalid(ReleaseManifestError, ValueError):
    def __init__(self, message: str = "Knowledge Base Release 请求无效") -> None:
        super().__init__("knowledge_base_release_invalid", message, 422)


class ReleaseManifestConflict(ReleaseManifestError):
    def __init__(self, message: str = "Knowledge Base Release 请求冲突") -> None:
        super().__init__("knowledge_base_release_conflict", message, 409)


class ReleaseManifestUnavailable(ReleaseManifestError):
    def __init__(self, message: str = "Knowledge Base Release 服务暂不可用") -> None:
        super().__init__("knowledge_base_release_unavailable", message, 503)


class ReleaseManifestForbidden(ReleaseManifestError):
    def __init__(self, message: str = "当前身份无权访问 Knowledge Base Release") -> None:
        super().__init__("knowledge_base_release_forbidden", message, 403)


class ReleaseManifestNotFound(ReleaseManifestError):
    def __init__(self, message: str = "Knowledge Base Release 不存在") -> None:
        super().__init__("knowledge_base_release_not_found", message, 404)


@dataclass(frozen=True)
class ReleaseSnapshot:
    manifest: Mapping[str, Any]
    entries: tuple[Mapping[str, Any], ...]
    blockers: tuple[Mapping[str, Any], ...]
    readiness_state: str
    readiness_fingerprint: str
    manifest_digest: str


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


def _unsafe_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_MARKERS)


def _reference_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized.endswith("_ref") or normalized.endswith("_reference")


def _body_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in _BODY_KEYS or normalized.endswith("_body")


def _safe_string(value: str, *, path: str) -> str:
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ReleaseManifestInvalid(f"{path} contains unsupported control characters")
    if (
        _SECRET_VALUE_RE.search(value)
        or _DATABASE_URL_RE.search(value)
        or _JWT_LIKE_RE.search(value)
    ):
        raise ReleaseManifestInvalid(f"{path} contains credential-like text")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.username is not None or parsed.password is not None:
            raise ReleaseManifestInvalid(f"{path} contains credential-bearing URL")
        for query_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if _unsafe_key(query_key):
                raise ReleaseManifestInvalid(f"{path} contains secret-bearing URL query")
    return value


def _canonical_value(value: Any, *, path: str, depth: int = 0) -> Any:
    if depth > 20:
        raise ReleaseManifestInvalid(f"{path} exceeds the canonical JSON depth limit")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ReleaseManifestInvalid(f"{path} contains a non-finite number")
        return value
    if isinstance(value, str):
        return _safe_string(value, path=path)
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key in sorted(value):
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ReleaseManifestInvalid(f"{path} contains an invalid object key")
            key = raw_key.strip()
            nested_path = f"{path}.{key}"
            if _body_key(key):
                raise ReleaseManifestInvalid(f"{nested_path} contains a body value")
            nested = value[raw_key]
            if _unsafe_key(key):
                if _reference_key(key) and isinstance(nested, str) and nested.strip():
                    normalized[key] = {
                        "reference_digest": sha256(nested.strip().encode("utf-8")).hexdigest()
                    }
                    continue
                if (
                    _reference_key(key)
                    and isinstance(nested, Mapping)
                    and set(nested) == {"reference_digest"}
                    and isinstance(nested.get("reference_digest"), str)
                    and _SHA256_RE.fullmatch(str(nested["reference_digest"]))
                ):
                    normalized[key] = {"reference_digest": str(nested["reference_digest"])}
                    continue
                raise ReleaseManifestInvalid(f"{nested_path} contains secret evidence")
            normalized[key] = _canonical_value(nested, path=nested_path, depth=depth + 1)
        return normalized
    raise ReleaseManifestInvalid(f"{path} contains unsupported value type {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required_text(value: Any, *, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseManifestInvalid(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ReleaseManifestInvalid(f"{field} must be at most {maximum} characters")
    return _safe_string(normalized, path=field)


def canonicalize_release_entries(
    entries: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    normalized_entries: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise ReleaseManifestInvalid("release entry must be an object")
        resource_type = _required_text(raw.get("resource_type"), field="resource_type", maximum=32)
        if resource_type not in _ALLOWED_RESOURCE_TYPES:
            raise ReleaseManifestInvalid(f"resource_type {resource_type!r} is unsupported")
        resource_id = _required_text(raw.get("resource_id"), field="resource_id", maximum=512)
        raw_revision = raw.get("resource_revision")
        if type(raw_revision) is int:
            if raw_revision < 0:
                raise ReleaseManifestInvalid("resource_revision must be non-negative")
            resource_revision = str(raw_revision)
        else:
            resource_revision = _required_text(raw_revision, field="resource_revision", maximum=128)
        identity = (resource_type, resource_id, resource_revision)
        if identity in identities:
            raise ReleaseManifestInvalid("release entries contain a duplicate resource identity")
        identities.add(identity)
        digest = raw.get("content_digest")
        if digest is not None:
            digest = _required_text(digest, field="content_digest", maximum=64)
            if not _SHA256_RE.fullmatch(digest):
                raise ReleaseManifestInvalid("content_digest must be lowercase SHA-256")
        facts = raw.get("facts", {})
        if not isinstance(facts, Mapping):
            raise ReleaseManifestInvalid("facts must be an object")
        normalized_entries.append(
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "resource_revision": resource_revision,
                "content_digest": digest,
                "facts": _canonical_value(facts, path=f"entry[{resource_id}].facts"),
            }
        )
    normalized_entries.sort(
        key=lambda item: (
            item["resource_type"],
            item["resource_id"],
            item["resource_revision"],
            item["content_digest"] or "",
            _canonical_json(item["facts"]),
        )
    )
    return tuple(
        {"ordinal": index, **entry} for index, entry in enumerate(normalized_entries, start=1)
    )


def canonical_release_digest(
    manifest: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> str:
    if not isinstance(manifest, Mapping):
        raise ReleaseManifestInvalid("manifest must be an object")
    normalized_manifest = _canonical_value(manifest, path="manifest")
    normalized_entries = canonicalize_release_entries(entries)
    payload = {
        "schema_version": _RELEASE_SCHEMA_VERSION,
        "manifest": normalized_manifest,
        "entries": list(normalized_entries),
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_blockers(
    blockers: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in blockers:
        if not isinstance(raw, Mapping):
            raise ReleaseManifestInvalid("release blocker must be an object")
        code = _required_text(raw.get("code"), field="blocker.code", maximum=128)
        resource_id = _required_text(
            raw.get("resource_id", "knowledge-base"),
            field="blocker.resource_id",
            maximum=512,
        )
        severity = _required_text(raw.get("severity"), field="blocker.severity", maximum=16)
        if severity not in _ALLOWED_BLOCKER_SEVERITIES:
            raise ReleaseManifestInvalid("blocker severity must be blocked or unavailable")
        identity = (severity, code, resource_id)
        if identity in seen:
            raise ReleaseManifestInvalid("release blockers contain a duplicate identity")
        seen.add(identity)
        safe_facts = raw.get("facts", {})
        if not isinstance(safe_facts, Mapping):
            raise ReleaseManifestInvalid("blocker facts must be an object")
        normalized.append(
            {
                "severity": severity,
                "code": code,
                "resource_id": resource_id,
                "facts": _canonical_value(safe_facts, path=f"blocker[{code}:{resource_id}].facts"),
            }
        )
    normalized.sort(key=lambda item: (item["severity"], item["code"], item["resource_id"]))
    return tuple(normalized)


def _digest_value(value: Any, *, path: str) -> str:
    normalized = _canonical_value(value, path=path)
    return sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


def _digest_sanitized_runtime_value(value: Any, *, path: str) -> str:
    sanitized = sanitize_audit_snapshot(value)
    try:
        serialized = _canonical_json(sanitized)
    except (TypeError, ValueError) as exc:
        raise ReleaseManifestInvalid(f"{path} contains unsupported runtime evidence") from exc
    return sha256(serialized.encode("utf-8")).hexdigest()


def _blocker(
    code: str,
    resource_id: str,
    *,
    severity: str = "blocked",
    facts: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    return {
        "code": code,
        "resource_id": resource_id,
        "severity": severity,
        "facts": dict(facts or {}),
    }


def collect_release_snapshot(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_number: int,
    now: datetime | None = None,
) -> ReleaseSnapshot:
    """Collect a deterministic, body-free candidate snapshot from current authorities.

    Persistence and idempotency are intentionally handled by
    ``capture_release_candidate``. This collector is the shared consistency
    boundary used by preview/readiness and by the write transaction.
    """

    tenant = _required_text(tenant_id, field="tenant_id", maximum=64)
    dataset_key = _required_text(dataset_id, field="dataset_id", maximum=64)
    if type(release_number) is not int or release_number < 1:
        raise ReleaseManifestInvalid("release_number must be an exact positive integer")
    moment = now or datetime.utcnow()
    dataset = session.scalar(
        select(Dataset).where(Dataset.tenant_id == tenant, Dataset.id == dataset_key)
    )
    if dataset is None:
        raise ReleaseManifestUnavailable("Knowledge Base does not exist in Tenant scope")

    blockers: list[Mapping[str, Any]] = []
    if dataset.status != "active":
        blockers.append(
            _blocker(
                "dataset_not_active",
                dataset.id,
                facts={"status": str(dataset.status)},
            )
        )

    ownership = session.scalar(
        select(DatasetWorkspaceOwnership).where(
            DatasetWorkspaceOwnership.tenant_id == tenant,
            DatasetWorkspaceOwnership.dataset_id == dataset_key,
        )
    )
    workspace = None
    if ownership is None:
        blockers.append(_blocker("ownership_missing", dataset.id, severity="unavailable"))
    else:
        workspace = session.scalar(
            select(TenantWorkspace).where(
                TenantWorkspace.tenant_id == tenant,
                TenantWorkspace.id == ownership.workspace_id,
            )
        )
        if workspace is None:
            blockers.append(
                _blocker(
                    "owning_workspace_missing",
                    str(ownership.workspace_id),
                    severity="unavailable",
                )
            )
        elif workspace.status != "active":
            blockers.append(
                _blocker(
                    "owning_workspace_inactive",
                    workspace.id,
                    facts={"status": str(workspace.status)},
                )
            )

    profile_facts = {
        "visibility": dataset.visibility,
        "default_language": dataset.default_language,
        "graph_enabled": bool(dataset.graph_enabled),
        "qa_enabled": bool(dataset.qa_enabled),
        "profile": dataset.profile_json or {},
        "parser_policy": dataset.parser_policy or {},
        "chunk_policy": dataset.chunk_policy or {},
        "retrieval_policy": dataset.retrieval_policy or {},
        "retention_policy": dataset.retention_policy or {},
        "metadata_policy": dataset.metadata_policy or {},
    }
    safe_profile_facts = _canonical_value(profile_facts, path="dataset_profile")
    entries: list[Mapping[str, Any]] = [
        {
            "resource_type": "dataset_profile",
            "resource_id": dataset.id,
            "resource_revision": int(dataset.profile_revision),
            "content_digest": sha256(
                _canonical_json(safe_profile_facts).encode("utf-8")
            ).hexdigest(),
            "facts": safe_profile_facts,
        }
    ]

    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.tenant_id == tenant,
                Document.dataset_id == dataset_key,
                Document.lifecycle_state == "active",
                Document.retrieval_enabled.is_(True),
                or_(Document.effective_from.is_(None), Document.effective_from <= moment),
                or_(Document.expires_at.is_(None), Document.expires_at > moment),
            )
            .order_by(Document.id)
            .with_for_update()
        )
    )
    for document in documents:
        version = None
        if document.current_version_id:
            version = session.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.tenant_id == tenant,
                    DocumentVersion.dataset_id == dataset_key,
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.id == document.current_version_id,
                )
                .with_for_update()
            )
        if version is None:
            blockers.append(
                _blocker(
                    "document_version_missing",
                    document.id,
                    severity="unavailable",
                )
            )
        if document.active_delete_operation_id:
            blockers.append(_blocker("document_delete_active", document.id))
        if int(document.indexed_revision or 0) != int(document.desired_index_revision or 0):
            blockers.append(
                _blocker(
                    "document_index_drift",
                    document.id,
                    facts={
                        "desired_index_revision": int(document.desired_index_revision or 0),
                        "indexed_revision": int(document.indexed_revision or 0),
                    },
                )
            )
        if bool(dataset.graph_enabled) and int(document.graph_revision or 0) != int(
            document.desired_index_revision or 0
        ):
            blockers.append(
                _blocker(
                    "document_graph_drift",
                    document.id,
                    facts={
                        "desired_index_revision": int(document.desired_index_revision or 0),
                        "graph_revision": int(document.graph_revision or 0),
                    },
                )
            )
        source_hash = str(version.source_hash).strip().casefold() if version is not None else ""
        if version is not None and not _SHA256_RE.fullmatch(source_hash):
            blockers.append(
                _blocker(
                    "document_version_digest_invalid",
                    document.id,
                    severity="unavailable",
                )
            )
            source_hash = ""
        entries.append(
            {
                "resource_type": "document_version",
                "resource_id": document.id,
                "resource_revision": int(version.revision) if version is not None else 0,
                "content_digest": source_hash or None,
                "facts": {
                    "version_id": version.id if version is not None else None,
                    "content_revision": int(document.content_revision or 0),
                    "desired_index_revision": int(document.desired_index_revision or 0),
                    "indexed_revision": int(document.indexed_revision or 0),
                    "graph_revision": int(document.graph_revision or 0),
                    "mutation_generation": int(document.mutation_generation or 0),
                },
            }
        )
        projection_facts = {
            "document_generation": int(document.mutation_generation or 0),
            "desired_index_revision": int(document.desired_index_revision or 0),
            "indexed_revision": int(document.indexed_revision or 0),
            "graph_revision": int(document.graph_revision or 0),
            "graph_required": bool(dataset.graph_enabled),
        }
        entries.append(
            {
                "resource_type": "projection_revision",
                "resource_id": document.id,
                "resource_revision": int(document.desired_index_revision or 0),
                "content_digest": _digest_value(
                    projection_facts, path=f"projection[{document.id}]"
                ),
                "facts": projection_facts,
            }
        )

    qa_rows = list(
        session.scalars(
            select(QAKnowledge)
            .where(
                QAKnowledge.tenant_id == tenant,
                QAKnowledge.dataset_id == dataset_key,
                QAKnowledge.review_status == "approved",
                QAKnowledge.lifecycle_state == "active",
                QAKnowledge.retrieval_enabled.is_(True),
                or_(QAKnowledge.effective_from.is_(None), QAKnowledge.effective_from <= moment),
                or_(QAKnowledge.expires_at.is_(None), QAKnowledge.expires_at > moment),
            )
            .order_by(QAKnowledge.id)
            .with_for_update()
        )
    )
    for qa in qa_rows:
        qa_digest = sha256(
            _canonical_json(
                {
                    "question": str(qa.question),
                    "answer": str(qa.answer),
                    "revision": int(qa.revision),
                }
            ).encode("utf-8")
        ).hexdigest()
        entries.append(
            {
                "resource_type": "qa_revision",
                "resource_id": qa.id,
                "resource_revision": int(qa.revision),
                "content_digest": qa_digest,
                "facts": {
                    "review_status": qa.review_status,
                    "origin": qa.origin,
                    "retrieval_enabled": bool(qa.retrieval_enabled),
                    "source_document_id": qa.source_document_id,
                },
            }
        )

    sources = list(
        session.scalars(
            select(DataSourceRecord)
            .where(
                DataSourceRecord.tenant_id == tenant,
                DataSourceRecord.dataset_id == dataset_key,
                DataSourceRecord.status == "active",
            )
            .order_by(DataSourceRecord.id)
            .with_for_update()
        )
    )
    for source in sources:
        active_run = session.scalar(
            select(SourceSyncRun.id)
            .where(
                SourceSyncRun.tenant_id == tenant,
                SourceSyncRun.dataset_id == dataset_key,
                SourceSyncRun.source_id == source.id,
                or_(
                    SourceSyncRun.status.in_(("pending", "running", "retrying")),
                    SourceSyncRun.execution_state.in_(("pending", "executing")),
                ),
            )
            .limit(1)
        )
        if active_run is not None:
            blockers.append(_blocker("source_sync_active", source.id))
        safe_source_state = sanitize_audit_snapshot(
            {
                "cursor": source.last_cursor or {},
                "result": source.last_result or {},
            }
        )
        state_digest = _digest_sanitized_runtime_value(
            safe_source_state, path=f"source[{source.id}].state"
        )
        config_fingerprint = str(source.config_fingerprint or "").strip().casefold()
        if not _SHA256_RE.fullmatch(config_fingerprint):
            config_fingerprint = _digest_sanitized_runtime_value(
                source.effective_config or {}, path=f"source[{source.id}].config"
            )
        entries.append(
            {
                "resource_type": "source_generation",
                "resource_id": source.id,
                "resource_revision": int(source.mutation_generation or 0),
                "content_digest": config_fingerprint,
                "facts": {
                    "source_type": source.source_type,
                    "state_digest": state_digest,
                    "last_sync_at": source.last_sync_at.isoformat()
                    if source.last_sync_at
                    else None,
                },
            }
        )

    manifest = {
        "tenant_id": tenant,
        "dataset_id": dataset.id,
        "release_number": release_number,
        "profile_revision": int(dataset.profile_revision),
        "ownership_revision": int(ownership.revision) if ownership is not None else 0,
        "workspace_id": workspace.id if workspace is not None else None,
        "workspace_revision": int(workspace.revision) if workspace is not None else 0,
        "dataset_mutation_generation": int(dataset.mutation_generation or 0),
        "dataset_serving_generation": int(dataset.serving_generation or 0),
        "captured_at": moment.isoformat(),
    }
    return build_release_snapshot(manifest=manifest, entries=entries, blockers=blockers)


_CHANNEL_CURSOR_KIND = "release_channel"
_RELEASE_CURSOR_KIND = "knowledge_base_release"


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip()
    if not normalized and not allow_empty:
        raise ReleaseManifestInvalid(f"{field} is required")
    if len(normalized) > maximum:
        raise ReleaseManifestInvalid(f"{field} must be at most {maximum} characters")
    return normalized


def _safe_release_reason(value: Any) -> str:
    return _safe_string(_clean(value, "reason", 512), path="reason")


def _exact_integer(value: Any, field: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ReleaseManifestInvalid(f"{field} must be an exact integer >= {minimum}")
    return value


def _ensure_release_capability(connection: Any) -> None:
    state, issues = inspect_enterprise_knowledge_base_release_capability(connection)
    if state != "ready":
        raise ReleaseManifestUnavailable("; ".join(issues) or state)


def _actor(session: Session, tenant_id: str, actor_id: str) -> tuple[TenantMember, Account]:
    tenant = session.get(Tenant, tenant_id)
    membership = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.account_id == actor_id,
            TenantMember.status == "active",
        )
    )
    account = session.get(Account, actor_id)
    if tenant is None or tenant.status != "active" or membership is None or account is None:
        raise ReleaseManifestForbidden("Actor is not active in Tenant scope")
    return membership, account


def _require_manage(
    engine: Any, session: Session, membership: TenantMember, dataset_id: str
) -> None:
    if str(membership.role) in {"owner", "admin"}:
        return
    decision = evaluate_dataset_permissions(
        engine,
        str(membership.tenant_id),
        str(membership.account_id),
        str(membership.role),
        dataset_id,
        session=session,
        lock_for_update=True,
    )
    if KNOWLEDGE_MANAGE not in decision.effective_permissions:
        raise ReleaseManifestForbidden("Actor lacks knowledge.manage")


def _require_read(engine: Any, session: Session, membership: TenantMember, dataset_id: str) -> None:
    if str(membership.role) in {"owner", "admin"}:
        return
    decision = evaluate_dataset_permissions(
        engine,
        str(membership.tenant_id),
        str(membership.account_id),
        str(membership.role),
        dataset_id,
        session=session,
        lock_for_update=False,
    )
    if KNOWLEDGE_READ not in decision.effective_permissions:
        raise ReleaseManifestForbidden("Actor lacks knowledge.read")


def _encode_cursor(kind: str, payload: Mapping[str, Any]) -> str:
    raw = _canonical_json({"kind": kind, **dict(payload)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None, kind: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw.decode())
    except Exception as exc:
        raise ReleaseManifestInvalid("cursor is invalid") from exc
    if not isinstance(decoded, Mapping) or decoded.get("kind") != kind:
        raise ReleaseManifestInvalid("cursor kind is invalid")
    return decoded


def _manifest_payload(row: DatasetReleaseManifest) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "release_number": int(row.release_number),
        "status": "candidate",
        "revision": 1,
        "profile_revision": int(row.profile_revision),
        "ownership_revision": int(row.ownership_revision),
        "workspace_id": row.workspace_id,
        "workspace_revision": int(row.workspace_revision),
        "mutation_generation": int(row.mutation_generation),
        "serving_generation": int(row.serving_generation),
        "schema_version": int(row.schema_version),
        "policy_digest": row.policy_digest,
        "manifest_digest": row.manifest_digest,
        "readiness_digest": row.readiness_digest,
        "readiness_fingerprint": row.readiness_digest,
        "readiness_state": row.readiness_state,
        "entry_count": int(row.entry_count),
        "blocker_count": int(row.blocker_count),
        "readiness_blockers": sanitize_audit_snapshot(row.readiness_blockers_json or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
        "reason": row.reason,
    }


def _entry_payload(row: DatasetReleaseEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "resource_revision": int(row.resource_revision),
        "content_digest": row.content_digest,
        "facts": _canonical_value(row.safe_facts_json or {}, path=f"release_entry[{row.id}].facts"),
    }


def _channel_payload(row: TenantReleaseChannel) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "code": row.code,
        "name": row.name,
        "status": row.status,
        "risk_tier": row.risk_tier,
        "promotion_order": int(row.promotion_order),
        "is_default_serving": bool(row.is_default_serving),
        "revision": int(row.revision),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "summary": None,
    }


def _current_dataset_policy_digest(dataset: Dataset) -> str:
    profile_facts = {
        "visibility": dataset.visibility,
        "default_language": dataset.default_language,
        "graph_enabled": bool(dataset.graph_enabled),
        "qa_enabled": bool(dataset.qa_enabled),
        "profile": dataset.profile_json or {},
        "parser_policy": dataset.parser_policy or {},
        "chunk_policy": dataset.chunk_policy or {},
        "retrieval_policy": dataset.retrieval_policy or {},
        "retention_policy": dataset.retention_policy or {},
        "metadata_policy": dataset.metadata_policy or {},
    }
    safe = _canonical_value(profile_facts, path="dataset_profile")
    return sha256(_canonical_json(safe).encode("utf-8")).hexdigest()


def _release_lifecycle(
    session: Session,
    row: DatasetReleaseManifest,
    *,
    channel_id: str | None = None,
) -> tuple[str, int]:
    all_events = list(
        session.scalars(
            select(DatasetReleaseEvent)
            .where(
                DatasetReleaseEvent.tenant_id == row.tenant_id,
                DatasetReleaseEvent.dataset_id == row.dataset_id,
                DatasetReleaseEvent.release_id == row.id,
            )
            .order_by(DatasetReleaseEvent.occurred_at, DatasetReleaseEvent.id)
        )
    )
    events = (
        all_events
        if channel_id is None
        else [
            event
            for event in all_events
            if event.channel_id == channel_id or event.event_type == "retired"
        ]
    )
    if any(event.event_type == "retired" for event in all_events):
        status = "retired"
    else:
        binding_statement = select(DatasetChannelRelease.id).where(
            DatasetChannelRelease.tenant_id == row.tenant_id,
            DatasetChannelRelease.dataset_id == row.dataset_id,
            DatasetChannelRelease.active_release_id == row.id,
            DatasetChannelRelease.status == "active",
        )
        if channel_id is not None:
            binding_statement = binding_statement.where(
                DatasetChannelRelease.channel_id == channel_id
            )
        if session.scalar(binding_statement) is not None:
            status = "published"
        elif any(event.event_type == "superseded" for event in events):
            status = "superseded"
        elif any(event.event_type in {"promoted", "rolled_back"} for event in events):
            status = "published"
        else:
            status = "candidate"
    revision = len(all_events) if channel_id is None else 1 + len(events)
    return status, max(1, revision)


def _manifest_read_payload(
    session: Session,
    row: DatasetReleaseManifest,
    *,
    channel_id: str | None = None,
) -> dict[str, Any]:
    status, revision = _release_lifecycle(session, row, channel_id=channel_id)
    return {**_manifest_payload(row), "status": status, "revision": revision}


def _release_state_payload(
    session: Session,
    row: DatasetReleaseManifest | None,
    *,
    channel_id: str | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = _manifest_read_payload(session, row, channel_id=channel_id)
    return {
        "release_id": row.id,
        "release_number": int(row.release_number),
        "status": payload["status"],
        "revision": payload["revision"],
        "manifest_digest": row.manifest_digest,
    }


def _channel_summary(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    channel: TenantReleaseChannel,
) -> dict[str, Any]:
    dataset = session.scalar(
        select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
    )
    if dataset is None:
        raise ReleaseManifestNotFound("Knowledge Base does not exist")
    ownership = session.scalar(
        select(DatasetWorkspaceOwnership).where(
            DatasetWorkspaceOwnership.tenant_id == tenant_id,
            DatasetWorkspaceOwnership.dataset_id == dataset_id,
        )
    )
    workspace = (
        session.scalar(
            select(TenantWorkspace).where(
                TenantWorkspace.tenant_id == tenant_id,
                TenantWorkspace.id == ownership.workspace_id,
            )
        )
        if ownership is not None
        else None
    )
    candidate = session.scalar(
        select(DatasetReleaseManifest)
        .where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
        )
        .order_by(
            DatasetReleaseManifest.release_number.desc(),
            DatasetReleaseManifest.id.desc(),
        )
        .limit(1)
    )
    binding = session.scalar(
        select(DatasetChannelRelease).where(
            DatasetChannelRelease.tenant_id == tenant_id,
            DatasetChannelRelease.dataset_id == dataset_id,
            DatasetChannelRelease.channel_id == channel.id,
            DatasetChannelRelease.status == "active",
        )
    )
    effective = (
        session.scalar(
            select(DatasetReleaseManifest).where(
                DatasetReleaseManifest.tenant_id == tenant_id,
                DatasetReleaseManifest.dataset_id == dataset_id,
                DatasetReleaseManifest.id == binding.active_release_id,
            )
        )
        if binding is not None and binding.active_release_id is not None
        else None
    )
    serving = (
        session.scalar(
            select(DatasetReleaseManifest).where(
                DatasetReleaseManifest.tenant_id == tenant_id,
                DatasetReleaseManifest.dataset_id == dataset_id,
                DatasetReleaseManifest.id == dataset.serving_release_id,
            )
        )
        if dataset.serving_release_id is not None
        else None
    )
    configured_digest = _current_dataset_policy_digest(dataset)
    if candidate is None or effective is None:
        comparison = "unavailable"
    elif (
        candidate.id == effective.id
        and candidate.policy_digest == configured_digest
        and int(candidate.profile_revision) == int(dataset.profile_revision)
        and int(candidate.mutation_generation) == int(dataset.mutation_generation)
    ):
        comparison = "aligned"
    else:
        comparison = "drifted"
    readiness = (
        {
            "state": candidate.readiness_state,
            "blocker_count": int(candidate.blocker_count),
            "blockers": sanitize_audit_snapshot(candidate.readiness_blockers_json or []),
            "fingerprint": candidate.readiness_digest,
            "reason": None,
        }
        if candidate is not None
        else {
            "state": "unavailable",
            "blocker_count": 0,
            "blockers": [],
            "fingerprint": None,
            "reason": "Release candidate 尚未生成",
        }
    )
    return {
        "channel_id": channel.id,
        "configured": {
            "profile_revision": int(dataset.profile_revision),
            "mutation_generation": int(dataset.mutation_generation),
            "ownership_revision": int(ownership.revision) if ownership is not None else None,
            "workspace_id": workspace.id if workspace is not None else None,
            "workspace_revision": int(workspace.revision) if workspace is not None else None,
            "policy_digest": configured_digest,
        },
        "candidate": _release_state_payload(session, candidate),
        "effective": _release_state_payload(session, effective, channel_id=channel.id),
        "serving": _release_state_payload(session, serving),
        "serving_generation": int(dataset.serving_generation),
        "comparison_state": comparison,
        "readiness": readiness,
        "revision": int(channel.revision),
    }


def _audit_release(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    account: Account,
    release_id: str,
    payload: Mapping[str, Any],
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=str(account.name)[:128],
            actor_email_snapshot=str(account.email)[:256],
            action="knowledge_base.release.candidate_created",
            resource_type="knowledge_base_release",
            resource_id=release_id,
            before_snapshot=None,
            after_snapshot=sanitize_audit_snapshot(dict(payload)),
            request_id=_clean(request_id, "request_id", 128, allow_empty=True),
            request_ip=_clean(request_ip, "request_ip", 64, allow_empty=True),
            occurred_at=now,
        )
    )


def _release_revisions(
    session: Session, tenant_id: str, dataset_id: str
) -> tuple[Dataset, DatasetWorkspaceOwnership, TenantWorkspace]:
    dataset = session.scalar(
        select(Dataset)
        .where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
        .with_for_update()
    )
    ownership = session.scalar(
        select(DatasetWorkspaceOwnership)
        .where(
            DatasetWorkspaceOwnership.tenant_id == tenant_id,
            DatasetWorkspaceOwnership.dataset_id == dataset_id,
        )
        .with_for_update()
    )
    workspace = (
        session.scalar(
            select(TenantWorkspace)
            .where(
                TenantWorkspace.tenant_id == tenant_id,
                TenantWorkspace.id == ownership.workspace_id,
            )
            .with_for_update()
        )
        if ownership is not None
        else None
    )
    if dataset is None:
        raise ReleaseManifestNotFound("Knowledge Base does not exist")
    if ownership is None or workspace is None:
        raise ReleaseManifestUnavailable("Ownership authority is unavailable")
    return dataset, ownership, workspace


def capture_release_candidate(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    expected_profile_revision: int,
    expected_ownership_revision: int,
    expected_workspace_revision: int,
    expected_mutation_generation: int,
    expected_serving_generation: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor_key = _clean(actor_id, "actor_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    expected = {
        "profile": _exact_integer(expected_profile_revision, "expected_profile_revision", 1),
        "ownership": _exact_integer(expected_ownership_revision, "expected_ownership_revision", 1),
        "workspace": _exact_integer(expected_workspace_revision, "expected_workspace_revision", 1),
        "mutation": _exact_integer(expected_mutation_generation, "expected_mutation_generation", 0),
        "serving": _exact_integer(expected_serving_generation, "expected_serving_generation", 0),
    }
    clean_reason = _safe_release_reason(reason)
    request_hash = tenant_request_hash(
        operation="capture_release_candidate",
        path_identity={"dataset_id": dataset_key},
        body={**expected, "reason": clean_reason},
    )
    key_digest = tenant_idempotency_key_digest(tenant, actor_key, idempotency_key)
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor_key, key_digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        _ensure_release_capability(session.connection())
        membership, account = _actor(session, tenant, actor_key)
        _require_manage(engine, session, membership, dataset_key)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor_key,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation="capture_release_candidate",
                resource_type="knowledge_base_release",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise ReleaseManifestConflict("idempotency key conflict") from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise ReleaseManifestConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise ReleaseManifestInvalid(str(exc)) from exc
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)

        dataset, ownership, workspace = _release_revisions(session, tenant, dataset_key)
        actual = {
            "profile": int(dataset.profile_revision),
            "ownership": int(ownership.revision),
            "workspace": int(workspace.revision),
            "mutation": int(dataset.mutation_generation or 0),
            "serving": int(dataset.serving_generation or 0),
        }
        labels = {
            "profile": "profile revision",
            "ownership": "ownership revision",
            "workspace": "workspace revision",
            "mutation": "mutation generation",
            "serving": "serving generation",
        }
        for key, value in expected.items():
            if actual[key] != value:
                raise ReleaseManifestConflict(f"{labels[key]} changed")

        release_number = (
            int(
                session.scalar(
                    select(func.max(DatasetReleaseManifest.release_number)).where(
                        DatasetReleaseManifest.tenant_id == tenant,
                        DatasetReleaseManifest.dataset_id == dataset_key,
                    )
                )
                or 0
            )
            + 1
        )
        snapshot = collect_release_snapshot(
            session,
            tenant_id=tenant,
            dataset_id=dataset_key,
            release_number=release_number,
        )
        if snapshot.readiness_state == "unavailable":
            raise ReleaseManifestUnavailable("release snapshot authority is unavailable")
        release_id = f"release-{uuid.uuid4().hex}"
        now = datetime.utcnow()
        profile_entry = next(
            item for item in snapshot.entries if item["resource_type"] == "dataset_profile"
        )
        manifest = DatasetReleaseManifest(
            id=release_id,
            tenant_id=tenant,
            dataset_id=dataset_key,
            release_number=release_number,
            profile_revision=actual["profile"],
            ownership_revision=actual["ownership"],
            workspace_id=workspace.id,
            workspace_revision=actual["workspace"],
            mutation_generation=actual["mutation"],
            serving_generation=actual["serving"],
            schema_version=_RELEASE_SCHEMA_VERSION,
            policy_digest=str(profile_entry["content_digest"]),
            manifest_digest=snapshot.manifest_digest,
            readiness_digest=snapshot.readiness_fingerprint,
            readiness_state=snapshot.readiness_state,
            entry_count=len(snapshot.entries),
            blocker_count=len(snapshot.blockers),
            readiness_blockers_json=[dict(item) for item in snapshot.blockers],
            created_at=now,
            created_by=actor_key,
            reason=clean_reason,
            request_id=_clean(request_id, "request_id", 128, allow_empty=True),
        )
        session.add(manifest)
        session.flush()
        for entry in snapshot.entries:
            session.add(
                DatasetReleaseEntry(
                    id=f"release-entry-{uuid.uuid4().hex}",
                    tenant_id=tenant,
                    dataset_id=dataset_key,
                    release_id=release_id,
                    ordinal=int(entry["ordinal"]),
                    resource_type=str(entry["resource_type"]),
                    resource_id=str(entry["resource_id"])[:128],
                    resource_revision=int(entry["resource_revision"]),
                    content_digest=entry.get("content_digest"),
                    safe_facts_json=dict(entry.get("facts") or {}),
                    created_at=now,
                )
            )
        session.add(
            DatasetReleaseEvent(
                id=f"release-event-{uuid.uuid4().hex}",
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=release_id,
                channel_id=None,
                event_type="candidate_created",
                actor_id=actor_key,
                reason=clean_reason,
                request_id=_clean(request_id, "request_id", 128, allow_empty=True),
                occurred_at=now,
            )
        )
        payload = {
            "state": "applied",
            "operation": "capture",
            "resource_id": release_id,
            "approval_request_id": None,
            "revision": 1,
            "message": "候选 Release 已生成",
            "retryable": False,
            "release": _manifest_payload(manifest),
            "readiness": {
                "state": snapshot.readiness_state,
                "blocker_count": len(snapshot.blockers),
                "blockers": list(snapshot.blockers),
                "fingerprint": snapshot.readiness_fingerprint,
                "reason": None,
            },
        }
        _audit_release(
            session,
            tenant_id=tenant,
            actor_id=actor_key,
            account=account,
            release_id=release_id,
            payload=payload,
            request_id=request_id,
            request_ip=request_ip,
            now=now,
        )
        try:
            replay = complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=201,
                resource_id=release_id,
            )
        except TenantMutationIdempotencyValidationError as exc:
            raise ReleaseManifestInvalid(str(exc)) from exc
        return ServiceResult(dict(replay), 201)


def list_release_channels(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant, actor_key = _clean(tenant_id, "tenant_id", 64), _clean(actor_id, "actor_id", 64)
    page_limit = _exact_integer(limit, "limit", 1)
    if page_limit > 200:
        raise ReleaseManifestInvalid("limit must be at most 200")
    decoded = _decode_cursor(cursor, _CHANNEL_CURSOR_KIND)
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        _actor(session, tenant, actor_key)
        statement = select(TenantReleaseChannel).where(TenantReleaseChannel.tenant_id == tenant)
        if decoded:
            order = _exact_integer(decoded.get("order"), "cursor.order", 0)
            item_id = _clean(decoded.get("id"), "cursor.id", 128)
            statement = statement.where(
                or_(
                    TenantReleaseChannel.promotion_order > order,
                    and_(
                        TenantReleaseChannel.promotion_order == order,
                        TenantReleaseChannel.id > item_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantReleaseChannel.promotion_order, TenantReleaseChannel.id
                ).limit(page_limit + 1)
            )
        )
        total = int(
            session.scalar(
                select(func.count())
                .select_from(TenantReleaseChannel)
                .where(TenantReleaseChannel.tenant_id == tenant)
            )
            or 0
        )
        more, items = len(rows) > page_limit, rows[:page_limit]
        next_cursor = (
            _encode_cursor(
                _CHANNEL_CURSOR_KIND,
                {"order": int(items[-1].promotion_order), "id": items[-1].id},
            )
            if more and items
            else None
        )
        return ServiceResult(
            {
                "items": [_channel_payload(row) for row in items],
                "count": total,
                "next_cursor": next_cursor,
                "summaries": {},
            }
        )


def list_releases(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    channel_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor_key = _clean(actor_id, "actor_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    channel_key = _clean(channel_id, "channel_id", 128) if channel_id is not None else None
    page_limit = _exact_integer(limit, "limit", 1)
    if page_limit > 200:
        raise ReleaseManifestInvalid("limit must be at most 200")
    decoded = _decode_cursor(cursor, _RELEASE_CURSOR_KIND)
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        membership, _account = _actor(session, tenant, actor_key)
        _require_read(engine, session, membership, dataset_key)
        statement = select(DatasetReleaseManifest).where(
            DatasetReleaseManifest.tenant_id == tenant,
            DatasetReleaseManifest.dataset_id == dataset_key,
        )
        count_statement = (
            select(func.count())
            .select_from(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant,
                DatasetReleaseManifest.dataset_id == dataset_key,
            )
        )
        if channel_key is not None:
            channel = session.scalar(
                select(TenantReleaseChannel).where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.id == channel_key,
                )
            )
            if channel is None:
                raise ReleaseManifestNotFound("Release Channel does not exist")
            channel_release_ids = (
                select(DatasetReleaseEvent.release_id)
                .where(
                    DatasetReleaseEvent.tenant_id == tenant,
                    DatasetReleaseEvent.dataset_id == dataset_key,
                    DatasetReleaseEvent.channel_id == channel_key,
                )
                .distinct()
            )
            statement = statement.where(DatasetReleaseManifest.id.in_(channel_release_ids))
            count_statement = count_statement.where(
                DatasetReleaseManifest.id.in_(channel_release_ids)
            )
        if decoded:
            number = _exact_integer(decoded.get("number"), "cursor.number", 1)
            item_id = _clean(decoded.get("id"), "cursor.id", 64)
            statement = statement.where(
                or_(
                    DatasetReleaseManifest.release_number < number,
                    and_(
                        DatasetReleaseManifest.release_number == number,
                        DatasetReleaseManifest.id < item_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetReleaseManifest.release_number.desc(),
                    DatasetReleaseManifest.id.desc(),
                ).limit(page_limit + 1)
            )
        )
        total = int(session.scalar(count_statement) or 0)
        more, items = len(rows) > page_limit, rows[:page_limit]
        next_cursor = (
            _encode_cursor(
                _RELEASE_CURSOR_KIND,
                {"number": int(items[-1].release_number), "id": items[-1].id},
            )
            if more and items
            else None
        )
        return ServiceResult(
            {
                "items": [
                    _manifest_read_payload(session, row, channel_id=channel_key) for row in items
                ],
                "count": total,
                "next_cursor": next_cursor,
                "summary": (
                    _channel_summary(
                        session,
                        tenant_id=tenant,
                        dataset_id=dataset_key,
                        channel=channel,
                    )
                    if channel_key is not None
                    else None
                ),
            }
        )


def _release_row(
    session: Session, tenant_id: str, dataset_id: str, release_id: str
) -> DatasetReleaseManifest:
    row = session.scalar(
        select(DatasetReleaseManifest).where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
            DatasetReleaseManifest.id == release_id,
        )
    )
    if row is None:
        raise ReleaseManifestNotFound()
    return row


def get_release(
    engine: Any, *, tenant_id: str, actor_id: str, dataset_id: str, release_id: str
) -> ServiceResult:
    tenant, actor_key = _clean(tenant_id, "tenant_id", 64), _clean(actor_id, "actor_id", 64)
    dataset_key, release_key = (
        _clean(dataset_id, "dataset_id", 64),
        _clean(release_id, "release_id", 64),
    )
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        membership, _account = _actor(session, tenant, actor_key)
        _require_read(engine, session, membership, dataset_key)
        row = _release_row(session, tenant, dataset_key, release_key)
        entries = list(
            session.scalars(
                select(DatasetReleaseEntry)
                .where(
                    DatasetReleaseEntry.tenant_id == tenant,
                    DatasetReleaseEntry.dataset_id == dataset_key,
                    DatasetReleaseEntry.release_id == release_key,
                )
                .order_by(DatasetReleaseEntry.ordinal)
            )
        )
        return ServiceResult(
            {
                "manifest": _manifest_read_payload(session, row),
                "entries": {
                    "items": [_entry_payload(item) for item in entries[:100]],
                    "count": len(entries),
                    "next_cursor": None,
                },
            }
        )


def get_release_readiness(
    engine: Any, *, tenant_id: str, actor_id: str, dataset_id: str, release_id: str
) -> ServiceResult:
    manifest = get_release(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        release_id=release_id,
    ).body["manifest"]
    return ServiceResult(
        {
            "state": manifest["readiness_state"],
            "blocker_count": manifest["blocker_count"],
            "blockers": sanitize_audit_snapshot(list(manifest.get("readiness_blockers") or [])),
            "fingerprint": manifest["readiness_digest"],
            "reason": None,
        }
    )


def list_channel_bindings(
    engine: Any, *, tenant_id: str, actor_id: str, dataset_id: str
) -> ServiceResult:
    tenant, actor_key = _clean(tenant_id, "tenant_id", 64), _clean(actor_id, "actor_id", 64)
    dataset_key = _clean(dataset_id, "dataset_id", 64)
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        membership, _account = _actor(session, tenant, actor_key)
        _require_read(engine, session, membership, dataset_key)
        rows = list(
            session.scalars(
                select(DatasetChannelRelease)
                .where(
                    DatasetChannelRelease.tenant_id == tenant,
                    DatasetChannelRelease.dataset_id == dataset_key,
                    DatasetChannelRelease.status == "active",
                )
                .order_by(DatasetChannelRelease.channel_id)
            )
        )
        return ServiceResult(
            {
                "items": [
                    {
                        "id": row.id,
                        "channel_id": row.channel_id,
                        "active_release_id": row.active_release_id,
                        "previous_release_id": row.previous_release_id,
                        "status": row.status,
                        "revision": int(row.revision),
                    }
                    for row in rows
                ],
                "count": len(rows),
                "next_cursor": None,
            }
        )


def get_release_impact(
    engine: Any, *, tenant_id: str, actor_id: str, dataset_id: str, release_id: str
) -> ServiceResult:
    tenant, actor_key = _clean(tenant_id, "tenant_id", 64), _clean(actor_id, "actor_id", 64)
    dataset_key, release_key = (
        _clean(dataset_id, "dataset_id", 64),
        _clean(release_id, "release_id", 64),
    )
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        membership, _account = _actor(session, tenant, actor_key)
        _require_read(engine, session, membership, dataset_key)
        _release_row(session, tenant, dataset_key, release_key)
        refs = list(
            session.scalars(
                select(AppDatasetReference).where(
                    AppDatasetReference.tenant_id == tenant,
                    AppDatasetReference.dataset_id == dataset_key,
                    AppDatasetReference.status == "active",
                )
            )
        )
        bindings = list(
            session.scalars(
                select(DatasetChannelRelease).where(
                    DatasetChannelRelease.tenant_id == tenant,
                    DatasetChannelRelease.dataset_id == dataset_key,
                    DatasetChannelRelease.status == "active",
                )
            )
        )
        items = [
            {
                "type": "application",
                "id": row.app_id,
                "label": row.app_id,
                "action": "review",
                "reason": row.release_mode,
            }
            for row in refs
        ] + [
            {
                "type": "channel",
                "id": row.channel_id,
                "label": row.channel_id,
                "action": "review",
                "reason": "active binding",
            }
            for row in bindings
        ]
        return ServiceResult(
            {"state": "ready", "items": items, "count": len(items), "next_cursor": None}
        )


def get_release_audit(
    engine: Any, *, tenant_id: str, actor_id: str, dataset_id: str, release_id: str
) -> ServiceResult:
    tenant, actor_key = _clean(tenant_id, "tenant_id", 64), _clean(actor_id, "actor_id", 64)
    dataset_key, release_key = (
        _clean(dataset_id, "dataset_id", 64),
        _clean(release_id, "release_id", 64),
    )
    with Session(engine) as session:
        _ensure_release_capability(session.connection())
        membership, _account = _actor(session, tenant, actor_key)
        _require_read(engine, session, membership, dataset_key)
        _release_row(session, tenant, dataset_key, release_key)
        rows = list(
            session.scalars(
                select(DatasetReleaseEvent)
                .where(
                    DatasetReleaseEvent.tenant_id == tenant,
                    DatasetReleaseEvent.dataset_id == dataset_key,
                    DatasetReleaseEvent.release_id == release_key,
                )
                .order_by(DatasetReleaseEvent.occurred_at.desc())
            )
        )
        return ServiceResult(
            {
                "items": [
                    {
                        "id": row.id,
                        "event": row.event_type,
                        "actor": row.actor_id,
                        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
                        "reason": row.reason,
                        "request_id": row.request_id,
                    }
                    for row in rows
                ],
                "count": len(rows),
                "next_cursor": None,
            }
        )


def build_release_snapshot(
    *,
    manifest: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    blockers: Sequence[Mapping[str, Any]],
) -> ReleaseSnapshot:
    normalized_manifest = _canonical_value(manifest, path="manifest")
    normalized_entries = canonicalize_release_entries(entries)
    normalized_blockers = _canonical_blockers(blockers)
    readiness_state = (
        "unavailable"
        if any(item["severity"] == "unavailable" for item in normalized_blockers)
        else "blocked"
        if normalized_blockers
        else "ready"
    )
    readiness_payload = {
        "schema_version": _RELEASE_SCHEMA_VERSION,
        "state": readiness_state,
        "blockers": list(normalized_blockers),
    }
    return ReleaseSnapshot(
        manifest=normalized_manifest,
        entries=normalized_entries,
        blockers=normalized_blockers,
        readiness_state=readiness_state,
        readiness_fingerprint=sha256(
            _canonical_json(readiness_payload).encode("utf-8")
        ).hexdigest(),
        manifest_digest=canonical_release_digest(normalized_manifest, normalized_entries),
    )


from core.enterprise_knowledge_base_release_mutations import (  # noqa: E402
    create_release_channel,
    promote_release,
    rollback_channel_release,
    update_app_release_binding,
    update_release_channel,
)


__all__ = [
    "ReleaseManifestConflict",
    "ReleaseManifestError",
    "ReleaseManifestForbidden",
    "ReleaseManifestInvalid",
    "ReleaseManifestNotFound",
    "ReleaseManifestUnavailable",
    "ReleaseSnapshot",
    "ServiceResult",
    "build_release_snapshot",
    "capture_release_candidate",
    "create_release_channel",
    "canonical_release_digest",
    "canonicalize_release_entries",
    "collect_release_snapshot",
    "get_release",
    "get_release_audit",
    "get_release_impact",
    "get_release_readiness",
    "list_channel_bindings",
    "list_release_channels",
    "list_releases",
    "promote_release",
    "rollback_channel_release",
    "update_app_release_binding",
    "update_release_channel",
]
