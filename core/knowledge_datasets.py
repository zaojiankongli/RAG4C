"""Authoritative, revisioned and audited knowledge-base dataset profiles."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import unicodedata
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext
from models.orm import Dataset, KnowledgeAuditEvent, TenantMember

_VISIBILITIES = frozenset({"private", "tenant", "public"})
_MUTABLE_STATUSES = frozenset({"active", "archived"})
_SENSITIVE_POLICY_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "refresh_token",
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "client_secret",
        "private_key",
        "bearer",
        "cookie",
        "session_id",
        "session_key",
        "access_key",
        "signing_key",
        "encryption_key",
        "credential",
    }
)
_CREDENTIAL_REF_KEY = "credential_ref"
_CREDENTIAL_REF_SCHEMES = frozenset({"secret", "vault"})
_CYRILLIC_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ѕ": "s",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "b",
        "һ": "h",
        "ԁ": "d",
        "ԛ": "q",
    }
)
_UNSET = object()


class DatasetProfileError(RuntimeError):
    """Base error for knowledge dataset profile authority."""


class DatasetProfileNotFound(DatasetProfileError):
    """The requested dataset does not exist in the tenant scope."""


class DatasetProfileConflict(DatasetProfileError):
    """The requested profile mutation conflicts with current authority."""


class DatasetArchiveBlocked(DatasetProfileConflict):
    """An active Application reference blocks Dataset archive."""


@dataclass(frozen=True)
class DatasetProfile:
    id: str
    tenant_id: str
    name: str
    description: str
    status: str
    profile_revision: int
    owner_id: str | None
    visibility: str
    profile_json: dict[str, Any]
    parser_policy: dict[str, Any]
    chunk_policy: dict[str, Any]
    retrieval_policy: dict[str, Any]
    retention_policy: dict[str, Any]
    metadata_policy: dict[str, Any]
    default_language: str
    graph_enabled: bool
    qa_enabled: bool
    archived_at: datetime | None
    archived_by: str | None
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    cleaned = unicodedata.normalize("NFKC", str(value or "")).strip()
    if required and not cleaned:
        raise ValueError(f"{field} is required")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return cleaned


def _normalized_policy_key_text(value: str) -> str:
    compatibility = unicodedata.normalize("NFKC", value)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", compatibility)
    decomposed = unicodedata.normalize("NFKD", camel_separated)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.category(character).startswith("M")
    )
    folded = without_marks.casefold()
    normalized: list[str] = []
    separator_pending = False
    for character in folded:
        if character.isalnum():
            if separator_pending and normalized:
                normalized.append("_")
            normalized.append(character)
            separator_pending = False
        else:
            separator_pending = True
    return "".join(normalized).strip("_")


def _normalize_policy_key(value: Any, *, path: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{path} JSON object keys must be strings")
    normalized = _normalized_policy_key_text(value)
    if not normalized:
        raise ValueError(f"{path} JSON object key must not be empty")
    if normalized in _SENSITIVE_POLICY_KEYS:
        raise ValueError(f"{path} contains secret-like key: {value}")
    if any(ord(character) > 127 for character in value):
        skeleton = _normalized_policy_key_text(value.casefold().translate(_CYRILLIC_CONFUSABLES))
        if skeleton in _SENSITIVE_POLICY_KEYS:
            raise ValueError(f"{path} contains secret-like non-ASCII key: {value}")
    return normalized


def _normalize_credential_ref(value: Any, *, path: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{path} credential_ref must be a URI string")
    reference = value.strip()
    parsed = urlsplit(reference)
    if parsed.scheme.casefold() not in _CREDENTIAL_REF_SCHEMES:
        raise ValueError(f"{path} credential_ref must use secret:// or vault://")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError(f"{path} credential_ref must not contain userinfo")
    if not parsed.netloc and not parsed.path.strip("/"):
        raise ValueError(f"{path} credential_ref must identify a secret")
    return reference


def _normalize_json_tree(
    value: Any,
    *,
    path: str,
    ancestors: frozenset[int] = frozenset(),
) -> Any:
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite JSON number")
        return value
    if value_type is list:
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{path} contains a cyclic JSON list")
        nested_ancestors = ancestors | {identity}
        return [
            _normalize_json_tree(
                nested,
                path=f"{path}[{index}]",
                ancestors=nested_ancestors,
            )
            for index, nested in enumerate(value)
        ]
    if value_type is dict:
        identity = id(value)
        if identity in ancestors:
            raise ValueError(f"{path} contains a cyclic JSON object")
        nested_ancestors = ancestors | {identity}
        normalized_object: dict[str, Any] = {}
        for raw_key, nested in value.items():
            normalized_key = _normalize_policy_key(raw_key, path=path)
            if normalized_key in normalized_object:
                raise ValueError(f"{path} contains duplicate normalized key: {normalized_key}")
            nested_path = f"{path}.{normalized_key}"
            if normalized_key == _CREDENTIAL_REF_KEY:
                normalized_object[normalized_key] = _normalize_credential_ref(
                    nested,
                    path=nested_path,
                )
            else:
                normalized_object[normalized_key] = _normalize_json_tree(
                    nested,
                    path=nested_path,
                    ancestors=nested_ancestors,
                )
        return normalized_object
    raise ValueError(f"{path} contains unsupported JSON value type: {value_type.__name__}")


def _policy_object(value: Any, *, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{field} must be a JSON object")
    normalized = _normalize_json_tree(value, path=field)
    assert isinstance(normalized, dict)
    return normalized


def _positive_revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("expected_revision must be an exact positive integer")
    return value


def _snapshot(dataset: Dataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "tenant_id": dataset.tenant_id,
        "name": dataset.name,
        "description": dataset.description,
        "status": dataset.status,
        "profile_revision": dataset.profile_revision,
        "owner_id": dataset.owner_id,
        "visibility": dataset.visibility,
        "profile_json": deepcopy(dataset.profile_json or {}),
        "parser_policy": deepcopy(dataset.parser_policy or {}),
        "chunk_policy": deepcopy(dataset.chunk_policy or {}),
        "retrieval_policy": deepcopy(dataset.retrieval_policy or {}),
        "retention_policy": deepcopy(dataset.retention_policy or {}),
        "metadata_policy": deepcopy(dataset.metadata_policy or {}),
        "default_language": dataset.default_language,
        "graph_enabled": bool(dataset.graph_enabled),
        "qa_enabled": bool(dataset.qa_enabled),
        "archived_at": dataset.archived_at.isoformat() if dataset.archived_at else None,
        "archived_by": dataset.archived_by,
        "updated_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
    }


def _projection(dataset: Dataset) -> DatasetProfile:
    return DatasetProfile(
        id=dataset.id,
        tenant_id=dataset.tenant_id,
        name=dataset.name,
        description=dataset.description,
        status=dataset.status,
        profile_revision=dataset.profile_revision,
        owner_id=dataset.owner_id,
        visibility=dataset.visibility,
        profile_json=deepcopy(dataset.profile_json or {}),
        parser_policy=deepcopy(dataset.parser_policy or {}),
        chunk_policy=deepcopy(dataset.chunk_policy or {}),
        retrieval_policy=deepcopy(dataset.retrieval_policy or {}),
        retention_policy=deepcopy(dataset.retention_policy or {}),
        metadata_policy=deepcopy(dataset.metadata_policy or {}),
        default_language=dataset.default_language,
        graph_enabled=bool(dataset.graph_enabled),
        qa_enabled=bool(dataset.qa_enabled),
        archived_at=dataset.archived_at,
        archived_by=dataset.archived_by,
        doc_count=dataset.doc_count,
        chunk_count=dataset.chunk_count,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


class KnowledgeDatasetRepository:
    """Tenant-scoped authority for knowledge-base product profiles."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _require_audit(audit: AuditContext) -> AuditContext:
        if not isinstance(audit, AuditContext):
            raise ValueError("a valid AuditContext is required")
        return audit

    @staticmethod
    def _dataset(
        session: Session,
        tenant_id: str,
        dataset_id: str,
    ) -> Dataset:
        dataset = session.scalar(
            select(Dataset).where(
                Dataset.id == dataset_id,
                Dataset.tenant_id == tenant_id,
            )
        )
        if dataset is None:
            raise DatasetProfileNotFound("dataset does not exist in tenant scope")
        return dataset

    @staticmethod
    def _assert_owner_membership(
        session: Session,
        *,
        tenant_id: str,
        owner_id: str | None,
    ) -> None:
        if owner_id is None:
            return
        exists = session.scalar(
            select(TenantMember.id).where(
                TenantMember.account_id == owner_id,
                TenantMember.tenant_id == tenant_id,
            )
        )
        if exists is None:
            raise DatasetProfileConflict("owner must be a member of the same tenant")

    def _event(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        audit: AuditContext,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        session.add(
            KnowledgeAuditEvent(
                id=self._new_id("audit"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                actor_id=_clean_text(audit.actor_id, field="actor_id", maximum=64, required=True),
                action=action,
                resource_type="dataset",
                resource_id=dataset_id,
                before_snapshot=before,
                after_snapshot=after,
                request_id=_clean_text(
                    audit.request_id,
                    field="request_id",
                    maximum=128,
                    required=True,
                ),
                request_ip=_clean_text(audit.request_ip, field="request_ip", maximum=64),
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _commit(session: Session, message: str) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise DatasetProfileConflict(message) from exc

    def get_profile(self, tenant_id: str, dataset_id: str) -> DatasetProfile:
        with Session(self.engine, expire_on_commit=False) as session:
            return _projection(self._dataset(session, tenant_id, dataset_id))

    def list_profiles(self, tenant_id: str) -> list[DatasetProfile]:
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.scalars(
                select(Dataset).where(Dataset.tenant_id == tenant_id).order_by(Dataset.id)
            )
            return [_projection(row) for row in rows]

    def update_profile(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
        name: str | object = _UNSET,
        description: str | object = _UNSET,
        owner_id: str | None | object = _UNSET,
        visibility: str | object = _UNSET,
        profile_json: dict[str, Any] | object = _UNSET,
        parser_policy: dict[str, Any] | object = _UNSET,
        chunk_policy: dict[str, Any] | object = _UNSET,
        retrieval_policy: dict[str, Any] | object = _UNSET,
        retention_policy: dict[str, Any] | object = _UNSET,
        metadata_policy: dict[str, Any] | object = _UNSET,
        default_language: str | object = _UNSET,
        graph_enabled: bool | object = _UNSET,
        qa_enabled: bool | object = _UNSET,
    ) -> DatasetProfile:
        audit = self._require_audit(audit)
        revision = _positive_revision(expected_revision)
        values: dict[str, Any] = {}
        if name is not _UNSET:
            values["name"] = _clean_text(name, field="name", maximum=128, required=True)
        if description is not _UNSET:
            values["description"] = _clean_text(
                description,
                field="description",
                maximum=512,
            )
        if owner_id is not _UNSET:
            values["owner_id"] = (
                None
                if owner_id is None
                else _clean_text(owner_id, field="owner_id", maximum=64, required=True)
            )
        if visibility is not _UNSET:
            normalized_visibility = _clean_text(
                visibility,
                field="visibility",
                maximum=16,
                required=True,
            ).casefold()
            if normalized_visibility not in _VISIBILITIES:
                raise ValueError("visibility must be private, tenant, or public")
            values["visibility"] = normalized_visibility
        for field, value in (
            ("profile_json", profile_json),
            ("parser_policy", parser_policy),
            ("chunk_policy", chunk_policy),
            ("retrieval_policy", retrieval_policy),
            ("retention_policy", retention_policy),
            ("metadata_policy", metadata_policy),
        ):
            if value is not _UNSET:
                values[field] = _policy_object(value, field=field)
        if default_language is not _UNSET:
            values["default_language"] = _clean_text(
                default_language,
                field="default_language",
                maximum=32,
                required=True,
            )
        if graph_enabled is not _UNSET:
            if not isinstance(graph_enabled, bool):
                raise ValueError("graph_enabled must be a boolean")
            values["graph_enabled"] = graph_enabled
        if qa_enabled is not _UNSET:
            if not isinstance(qa_enabled, bool):
                raise ValueError("qa_enabled must be a boolean")
            values["qa_enabled"] = qa_enabled
        if not values:
            raise ValueError("profile update must include at least one field")

        with Session(self.engine, expire_on_commit=False) as session:
            current = self._dataset(session, tenant_id, dataset_id)
            if current.status not in _MUTABLE_STATUSES:
                raise DatasetProfileConflict("disabled dataset profile cannot be updated")
            if "owner_id" in values:
                self._assert_owner_membership(
                    session,
                    tenant_id=tenant_id,
                    owner_id=values["owner_id"],
                )
            before = _snapshot(current)
            now = _utc_now()
            result = session.execute(
                update(Dataset)
                .where(
                    Dataset.id == dataset_id,
                    Dataset.tenant_id == tenant_id,
                    Dataset.profile_revision == revision,
                    Dataset.status.in_(_MUTABLE_STATUSES),
                )
                .values(
                    **values,
                    profile_revision=Dataset.profile_revision + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise DatasetProfileConflict("dataset profile revision conflict")
            session.expire_all()
            updated = self._dataset(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action="dataset.profile.update",
                before=before,
                after=_snapshot(updated),
                occurred_at=now,
            )
            self._commit(session, "dataset profile update failed")
            return _projection(updated)

    def _transition(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
        action: str,
        allowed_from: frozenset[str],
        target: str,
    ) -> DatasetProfile:
        audit = self._require_audit(audit)
        revision = _positive_revision(expected_revision)
        with Session(self.engine, expire_on_commit=False) as session:
            current = self._dataset(session, tenant_id, dataset_id)
            if target == "archived":
                from core.enterprise_knowledge_base_registry import assert_dataset_archive_allowed

                assert_dataset_archive_allowed(session, tenant_id, dataset_id)
            if current.status not in allowed_from:
                expected = " or ".join(sorted(allowed_from))
                raise DatasetProfileConflict(
                    f"dataset must be {expected} before {action.removeprefix('dataset.')}"
                )
            before = _snapshot(current)
            now = _utc_now()
            values: dict[str, Any] = {
                "status": target,
                "profile_revision": Dataset.profile_revision + 1,
                "updated_at": now,
            }
            if target == "archived":
                values.update(
                    archived_at=now,
                    archived_by=_clean_text(
                        audit.actor_id,
                        field="actor_id",
                        maximum=64,
                        required=True,
                    ),
                )
            elif target == "active":
                values.update(archived_at=None, archived_by=None)
            result = session.execute(
                update(Dataset)
                .where(
                    Dataset.id == dataset_id,
                    Dataset.tenant_id == tenant_id,
                    Dataset.profile_revision == revision,
                    Dataset.status.in_(allowed_from),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise DatasetProfileConflict("dataset profile revision conflict")
            session.expire_all()
            updated = self._dataset(session, tenant_id, dataset_id)
            self._event(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                audit=audit,
                action=action,
                before=before,
                after=_snapshot(updated),
                occurred_at=now,
            )
            self._commit(session, f"{action} failed")
            return _projection(updated)

    def archive(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> DatasetProfile:
        return self._transition(
            tenant_id,
            dataset_id,
            expected_revision=expected_revision,
            audit=audit,
            action="dataset.archive",
            allowed_from=frozenset({"active"}),
            target="archived",
        )

    def restore(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> DatasetProfile:
        return self._transition(
            tenant_id,
            dataset_id,
            expected_revision=expected_revision,
            audit=audit,
            action="dataset.restore",
            allowed_from=frozenset({"archived"}),
            target="active",
        )

    def disable(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> DatasetProfile:
        return self._transition(
            tenant_id,
            dataset_id,
            expected_revision=expected_revision,
            audit=audit,
            action="dataset.disable",
            allowed_from=frozenset({"active", "archived"}),
            target="disabled",
        )


__all__ = [
    "DatasetProfile",
    "DatasetArchiveBlocked",
    "DatasetProfileConflict",
    "DatasetProfileError",
    "DatasetProfileNotFound",
    "KnowledgeDatasetRepository",
]
