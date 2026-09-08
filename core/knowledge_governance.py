"""Authoritative, audited repository for knowledge governance facts."""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.orm import Dataset, Document, DocumentTag, KnowledgeAuditEvent, KnowledgeFolder, KnowledgeTag
from core.secret_fields import is_sensitive_field

_FOLDER_STATUSES = frozenset({"active", "archived"})
_WHITESPACE = re.compile(r"\s+")
_AUDIT_EXACT_SECRET_FIELDS = frozenset({"cookie", "token"})
_AUDIT_REDACTED = "[REDACTED]"
_AUDIT_URI_SECRET_PARAMS = frozenset({
    "accesstoken", "apikey", "credential", "key", "password", "passwd", "pwd",
    "refreshtoken", "secret", "signature", "token", "xamzsecuritytoken",
    "xamzsignature",
})
_AUDIT_URI_SECRET_SUFFIXES = (
    "accesskey", "accesstoken", "apikey", "bearer", "cookie", "credential",
    "encryptionkey", "passphrase", "passwd", "password", "privatekey", "pwd",
    "refreshtoken", "secret", "sessionkey", "signature", "signingkey", "token",
)
SYSTEM_BACKFILL_ACTOR = "system:governance-backfill"

class GovernanceError(RuntimeError):
    pass

class GovernanceNotFound(GovernanceError):
    pass

class GovernanceConflict(GovernanceError):
    pass

@dataclass(frozen=True)
class AuditContext:
    actor_id: str
    request_id: str
    request_ip: str = ""

    def __post_init__(self) -> None:
        if not str(self.actor_id or "").strip():
            raise ValueError("audit actor_id is required")
        if not str(self.request_id or "").strip():
            raise ValueError("audit request_id is required")

    @classmethod
    def system(cls, actor_id: str, request_id: str) -> "AuditContext":
        return cls(actor_id=actor_id, request_id=request_id)

@dataclass(frozen=True)
class FolderProjection:
    id: str
    tenant_id: str
    dataset_id: str
    parent_id: str | None
    name: str
    normalized_name: str
    path: str
    path_hash: str
    description: str
    sort_order: int
    status: str
    created_by: str
    depth: int
    child_count: int

@dataclass(frozen=True)
class TagProjection:
    id: str
    tenant_id: str
    dataset_id: str
    name: str
    normalized_name: str
    color: str
    description: str
    created_by: str
    usage_count: int

@dataclass(frozen=True)
class GovernanceBackfillResult:
    folders_created: int = 0
    tags_created: int = 0
    documents_assigned: int = 0
    tag_links_created: int = 0

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def path_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def _display_name(value: str, *, field: str = "name") -> str:
    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(value or ""))).strip()
    if not display:
        raise ValueError(f"{field} must not be empty")
    if "/" in display:
        raise ValueError(f"{field} must not contain '/'")
    if len(display) > 128:
        raise ValueError(f"{field} must be at most 128 characters")
    return display

def _normalized_name(display: str) -> str:
    normalized = display.casefold()
    if len(normalized) > 256:
        raise ValueError("normalized name must be at most 256 characters")
    return normalized

def _clean(value: str | None, maximum: int | None = None) -> str:
    result = unicodedata.normalize("NFKC", str(value or "")).strip()
    if maximum is not None and len(result) > maximum:
        raise ValueError(f"value must be at most {maximum} characters")
    return result


def _normalize_audit_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_sensitive_uri_parameter(name: str) -> bool:
    normalized = _normalize_audit_key(name)
    return (normalized in _AUDIT_URI_SECRET_PARAMS or
            normalized.endswith(_AUDIT_URI_SECRET_SUFFIXES))


def _sanitize_uri_pairs(value: str) -> str:
    try:
        pairs = parse_qsl(value, keep_blank_values=True, max_num_fields=256)
    except ValueError:
        return ""
    return urlencode([(name, _AUDIT_REDACTED if _is_sensitive_uri_parameter(name)
                       else item_value) for name, item_value in pairs])


def _sanitize_uri_value(value: str) -> str:
    if "://" not in value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or (not parsed.netloc and parsed.scheme.casefold() != "file"):
        return value
    host = parsed.netloc.rsplit("@", 1)[-1]
    query = _sanitize_uri_pairs(parsed.query) if parsed.query else ""
    if parsed.fragment and "=" in parsed.fragment:
        fragment = _sanitize_uri_pairs(parsed.fragment)
    else:
        fragment = ""
    return urlunsplit((parsed.scheme, host, parsed.path, query, fragment))


def sanitize_audit_snapshot(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe audit snapshot with credentials removed defensively."""
    if (_normalize_audit_key(key) in _AUDIT_EXACT_SECRET_FIELDS or
            is_sensitive_field(key)):
        return _AUDIT_REDACTED
    if isinstance(value, dict):
        return {str(item_key): sanitize_audit_snapshot(item_value, key=str(item_key))
                for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_snapshot(item) for item in value]
    if isinstance(value, str):
        return _sanitize_uri_value(value)
    return value


@event.listens_for(KnowledgeAuditEvent, "before_insert")
def _sanitize_audit_event_before_insert(_mapper: Any, _connection: Any,
                                        target: KnowledgeAuditEvent) -> None:
    target.before_snapshot = sanitize_audit_snapshot(target.before_snapshot)
    target.after_snapshot = sanitize_audit_snapshot(target.after_snapshot)

def _folder_snapshot(folder: KnowledgeFolder) -> dict[str, Any]:
    return {"id": folder.id, "parent_id": folder.parent_id, "name": folder.name, "path": folder.path,
            "description": folder.description, "sort_order": folder.sort_order, "status": folder.status}

def _tag_snapshot(tag: KnowledgeTag) -> dict[str, Any]:
    return {"id": tag.id, "name": tag.name, "color": tag.color, "description": tag.description}

class KnowledgeGovernanceRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _dataset(session: Session, tenant_id: str, dataset_id: str) -> Dataset:
        row = session.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id))
        if row is None:
            raise GovernanceNotFound("dataset does not exist in tenant scope")
        return row

    @staticmethod
    def _folder(session: Session, tenant_id: str, dataset_id: str, folder_id: str, *, lock: bool = False) -> KnowledgeFolder:
        query = select(KnowledgeFolder).where(KnowledgeFolder.id == folder_id,
            KnowledgeFolder.tenant_id == tenant_id, KnowledgeFolder.dataset_id == dataset_id)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise GovernanceNotFound("folder does not exist in dataset scope")
        return row

    @staticmethod
    def _tag(session: Session, tenant_id: str, dataset_id: str, tag_id: str, *, lock: bool = False) -> KnowledgeTag:
        query = select(KnowledgeTag).where(KnowledgeTag.id == tag_id,
            KnowledgeTag.tenant_id == tenant_id, KnowledgeTag.dataset_id == dataset_id)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise GovernanceNotFound("tag does not exist in dataset scope")
        return row

    @staticmethod
    def _document(session: Session, tenant_id: str, dataset_id: str, document_id: str, *, lock: bool = False) -> Document:
        query = select(Document).where(Document.id == document_id,
            Document.tenant_id == tenant_id, Document.dataset_id == dataset_id)
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise GovernanceNotFound("document does not exist in dataset scope")
        return row

    @staticmethod
    def _sibling(session: Session, tenant_id: str, dataset_id: str, parent_key: str,
                 normalized_name: str, exclude_id: str | None = None) -> KnowledgeFolder | None:
        query = select(KnowledgeFolder).where(KnowledgeFolder.tenant_id == tenant_id,
            KnowledgeFolder.dataset_id == dataset_id, KnowledgeFolder.parent_key == parent_key,
            KnowledgeFolder.normalized_name == normalized_name)
        if exclude_id:
            query = query.where(KnowledgeFolder.id != exclude_id)
        return session.scalar(query.limit(1))

    def _event(self, session: Session, *, tenant_id: str, dataset_id: str, audit: AuditContext,
               action: str, resource_type: str, resource_id: str,
               before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
               occurred_at: datetime | None = None) -> KnowledgeAuditEvent:
        event = KnowledgeAuditEvent(id=self._new_id("audit"), tenant_id=tenant_id, dataset_id=dataset_id,
            actor_id=_clean(audit.actor_id, 64), action=_clean(action, 128),
            resource_type=_clean(resource_type, 64), resource_id=_clean(resource_id, 512),
            before_snapshot=sanitize_audit_snapshot(before),
            after_snapshot=sanitize_audit_snapshot(after),
            request_id=_clean(audit.request_id, 128),
            request_ip=_clean(audit.request_ip, 64), occurred_at=occurred_at or _utc_now())
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _commit(session: Session, message: str) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise GovernanceConflict(message) from exc

    def create_folder(self, tenant_id: str, dataset_id: str, name: str, *, parent_id: str | None = None,
                      description: str = "", sort_order: int = 0, status: str = "active",
                      audit: AuditContext) -> KnowledgeFolder:
        display, lifecycle = _display_name(name), _clean(status).casefold()
        if lifecycle not in _FOLDER_STATUSES:
            raise ValueError("folder status must be active or archived")
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            parent = None if parent_id is None else self._folder(session, tenant_id, dataset_id, parent_id)
            if parent is not None and lifecycle == "active" and parent.status != "active":
                raise GovernanceConflict("active folder cannot use an archived parent")
            parent_key = "" if parent is None else parent.id
            normalized = _normalized_name(display)
            if self._sibling(session, tenant_id, dataset_id, parent_key, normalized):
                raise GovernanceConflict("folder sibling name already exists")
            folder = KnowledgeFolder(id=self._new_id("folder"), tenant_id=tenant_id, dataset_id=dataset_id,
                parent_id=None if parent is None else parent.id, parent_key=parent_key, name=display,
                normalized_name=normalized, path=display if parent is None else f"{parent.path}/{display}",
                description=_clean(description), sort_order=int(sort_order), status=lifecycle,
                created_by=audit.actor_id)
            folder.path_hash = path_hash(folder.path)
            session.add(folder)
            session.flush()
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="folder.create", resource_type="knowledge_folder", resource_id=folder.id,
                after=_folder_snapshot(folder))
            self._commit(session, "folder sibling name already exists")
            return folder

    def get_folder(self, *, tenant_id: str, dataset_id: str, folder_id: str) -> KnowledgeFolder:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._folder(session, tenant_id, dataset_id, folder_id)

    @staticmethod
    def _descendants(session: Session, tenant_id: str, dataset_id: str, root_id: str) -> list[KnowledgeFolder]:
        result: list[KnowledgeFolder] = []
        frontier = [root_id]
        while frontier:
            level = list(session.scalars(select(KnowledgeFolder).where(
                KnowledgeFolder.tenant_id == tenant_id, KnowledgeFolder.dataset_id == dataset_id,
                KnowledgeFolder.parent_id.in_(frontier)).order_by(KnowledgeFolder.id)))
            result.extend(level)
            frontier = [item.id for item in level]
        return result

    def _rebuild_paths(self, session: Session, tenant_id: str, dataset_id: str,
                       root: KnowledgeFolder) -> list[KnowledgeFolder]:
        affected = [root]
        frontier = [root]
        while frontier:
            parents = {item.id: item for item in frontier}
            children = list(session.scalars(select(KnowledgeFolder).where(
                KnowledgeFolder.tenant_id == tenant_id, KnowledgeFolder.dataset_id == dataset_id,
                KnowledgeFolder.parent_id.in_(parents)).order_by(KnowledgeFolder.id)))
            for child in children:
                child.path = f"{parents[child.parent_id].path}/{child.name}"
                child.path_hash = path_hash(child.path)
            affected.extend(children)
            frontier = children
        return affected

    @staticmethod
    def _sync_folder_projection(session: Session, folders: list[KnowledgeFolder]) -> None:
        paths = {item.id: item.path for item in folders}
        if not paths:
            return
        for document in session.scalars(select(Document).where(Document.folder_id.in_(paths))):
            document.logical_folder_path = paths[document.folder_id]

    def update_folder(self, *, tenant_id: str, dataset_id: str, folder_id: str,
                      name: str | None = None, description: str | None = None,
                      sort_order: int | None = None, status: str | None = None,
                      audit: AuditContext) -> KnowledgeFolder:
        with Session(self.engine, expire_on_commit=False) as session:
            folder = self._folder(session, tenant_id, dataset_id, folder_id, lock=True)
            before = _folder_snapshot(folder)
            if name is not None:
                display, normalized = _display_name(name), _normalized_name(_display_name(name))
                if self._sibling(session, tenant_id, dataset_id, folder.parent_key, normalized, folder.id):
                    raise GovernanceConflict("folder sibling name already exists")
                folder.name, folder.normalized_name = display, normalized
                if folder.parent_id is None:
                    folder.path = display
                else:
                    folder.path = f"{self._folder(session, tenant_id, dataset_id, folder.parent_id).path}/{display}"
                folder.path_hash = path_hash(folder.path)
            if description is not None:
                folder.description = _clean(description)
            if sort_order is not None:
                folder.sort_order = int(sort_order)
            if status is not None:
                lifecycle = _clean(status).casefold()
                if lifecycle not in _FOLDER_STATUSES:
                    raise ValueError("folder status must be active or archived")
                if lifecycle == "archived":
                    raise ValueError("use archive_folder to archive a folder")
                if folder.parent_id is not None:
                    parent = self._folder(session, tenant_id, dataset_id, folder.parent_id)
                    if parent.status != "active":
                        raise GovernanceConflict("active folder cannot use an archived parent")
                folder.status = lifecycle
            affected = self._rebuild_paths(session, tenant_id, dataset_id, folder)
            self._sync_folder_projection(session, affected)
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="folder.update", resource_type="knowledge_folder", resource_id=folder.id,
                before=before, after=_folder_snapshot(folder))
            self._commit(session, "folder update conflicts with sibling")
            return folder

    def move_folder(self, *, tenant_id: str, dataset_id: str, folder_id: str,
                    new_parent_id: str | None, audit: AuditContext) -> KnowledgeFolder:
        with Session(self.engine, expire_on_commit=False) as session:
            folder = self._folder(session, tenant_id, dataset_id, folder_id, lock=True)
            before = _folder_snapshot(folder)
            parent = None if new_parent_id is None else self._folder(session, tenant_id, dataset_id, new_parent_id)
            if parent is not None and folder.status == "active" and parent.status != "active":
                raise GovernanceConflict("active folder cannot use an archived parent")
            descendants = {item.id for item in self._descendants(session, tenant_id, dataset_id, folder.id)}
            if parent is not None and (parent.id == folder.id or parent.id in descendants):
                raise GovernanceConflict("folder cannot move below its descendant")
            parent_key = "" if parent is None else parent.id
            if self._sibling(session, tenant_id, dataset_id, parent_key, folder.normalized_name, folder.id):
                raise GovernanceConflict("folder sibling name already exists")
            folder.parent_id, folder.parent_key = (None if parent is None else parent.id), parent_key
            folder.path = folder.name if parent is None else f"{parent.path}/{folder.name}"
            folder.path_hash = path_hash(folder.path)
            affected = self._rebuild_paths(session, tenant_id, dataset_id, folder)
            self._sync_folder_projection(session, affected)
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="folder.move", resource_type="knowledge_folder", resource_id=folder.id,
                before=before, after=_folder_snapshot(folder))
            self._commit(session, "folder move conflicts with sibling")
            return folder

    def archive_folder(self, *, tenant_id: str, dataset_id: str, folder_id: str,
                       audit: AuditContext) -> KnowledgeFolder:
        with Session(self.engine, expire_on_commit=False) as session:
            folder = self._folder(session, tenant_id, dataset_id, folder_id, lock=True)
            if folder.status == "archived":
                return folder
            active_child = session.scalar(select(KnowledgeFolder.id).where(
                KnowledgeFolder.tenant_id == tenant_id,
                KnowledgeFolder.dataset_id == dataset_id,
                KnowledgeFolder.parent_id == folder.id,
                KnowledgeFolder.status == "active").limit(1))
            if active_child is not None:
                raise GovernanceConflict("folder with active children cannot be archived")
            assigned_document = session.scalar(select(Document.id).where(
                Document.tenant_id == tenant_id, Document.dataset_id == dataset_id,
                Document.folder_id == folder.id).limit(1))
            if assigned_document is not None:
                raise GovernanceConflict("folder with documents cannot be archived")
            before = _folder_snapshot(folder)
            folder.status = "archived"
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="folder.archive", resource_type="knowledge_folder", resource_id=folder.id,
                before=before, after=_folder_snapshot(folder))
            self._commit(session, "folder archive failed")
            return folder

    def list_folders(self, tenant_id: str, dataset_id: str, *, include_archived: bool = False) -> list[FolderProjection]:
        with Session(self.engine) as session:
            self._dataset(session, tenant_id, dataset_id)
            query = select(KnowledgeFolder).where(KnowledgeFolder.tenant_id == tenant_id,
                KnowledgeFolder.dataset_id == dataset_id)
            if not include_archived:
                query = query.where(KnowledgeFolder.status == "active")
            rows = list(session.scalars(query))
        counts: dict[str, int] = {}
        for item in rows:
            if item.parent_id:
                counts[item.parent_id] = counts.get(item.parent_id, 0) + 1
        rows.sort(key=lambda item: (item.path.casefold(), item.path, item.id))
        return [FolderProjection(item.id, item.tenant_id, item.dataset_id, item.parent_id,
            item.name, item.normalized_name, item.path, item.path_hash, item.description,
            item.sort_order, item.status, item.created_by, item.path.count("/"), counts.get(item.id, 0))
            for item in rows]

    def assign_document_folder(self, tenant_id: str, dataset_id: str, document_id: str,
                               folder_id: str | None, *, audit: AuditContext) -> Document:
        with Session(self.engine, expire_on_commit=False) as session:
            document = self._document(session, tenant_id, dataset_id, document_id, lock=True)
            folder = None if folder_id is None else self._folder(session, tenant_id, dataset_id, folder_id)
            before = {"folder_id": document.folder_id, "logical_folder_path": document.logical_folder_path}
            document.folder_id = None if folder is None else folder.id
            document.logical_folder_path = None if folder is None else folder.path
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="document.folder.assign", resource_type="document", resource_id=document.id,
                before=before, after={"folder_id": document.folder_id,
                                     "logical_folder_path": document.logical_folder_path})
            self._commit(session, "document folder assignment failed")
            return document

    def delete_folder(self, tenant_id: str, dataset_id: str, folder_id: str, *, audit: AuditContext) -> None:
        with Session(self.engine) as session:
            folder = self._folder(session, tenant_id, dataset_id, folder_id, lock=True)
            if session.scalar(select(KnowledgeFolder.id).where(KnowledgeFolder.parent_id == folder.id).limit(1)):
                raise GovernanceConflict("folder with children cannot be deleted")
            if session.scalar(select(Document.id).where(Document.folder_id == folder.id).limit(1)):
                raise GovernanceConflict("folder with documents cannot be deleted")
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="folder.delete", resource_type="knowledge_folder", resource_id=folder.id,
                before=_folder_snapshot(folder))
            session.delete(folder)
            self._commit(session, "folder deletion failed")

    def create_tag(self, tenant_id: str, dataset_id: str, name: str, *, color: str = "",
                   description: str = "", audit: AuditContext) -> KnowledgeTag:
        display = _display_name(name, field="tag name")
        normalized = _normalized_name(display)
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            duplicate = session.scalar(select(KnowledgeTag.id).where(KnowledgeTag.tenant_id == tenant_id,
                KnowledgeTag.dataset_id == dataset_id, KnowledgeTag.normalized_name == normalized))
            if duplicate:
                raise GovernanceConflict("tag name already exists")
            tag = KnowledgeTag(id=self._new_id("tag"), tenant_id=tenant_id, dataset_id=dataset_id,
                name=display, normalized_name=normalized, color=_clean(color, 32),
                description=_clean(description), created_by=audit.actor_id)
            session.add(tag)
            session.flush()
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.create", resource_type="knowledge_tag", resource_id=tag.id,
                after=_tag_snapshot(tag))
            self._commit(session, "tag name already exists")
            return tag

    @staticmethod
    def _sync_tag_projection(session: Session, document_ids: set[str]) -> None:
        for document_id in sorted(document_ids):
            document = session.get(Document, document_id)
            if document is None:
                continue
            names = list(session.scalars(select(KnowledgeTag.name).join(
                DocumentTag, DocumentTag.tag_id == KnowledgeTag.id).where(
                DocumentTag.document_id == document_id).order_by(
                KnowledgeTag.normalized_name, KnowledgeTag.id)))
            metadata = dict(document.parser_meta or {})
            management = dict(metadata.get("management") or {})
            management["tags"] = names
            metadata["management"] = management
            document.parser_meta = metadata

    def update_tag(self, *, tenant_id: str, dataset_id: str, tag_id: str,
                   name: str | None = None, color: str | None = None,
                   description: str | None = None, audit: AuditContext) -> KnowledgeTag:
        with Session(self.engine, expire_on_commit=False) as session:
            tag = self._tag(session, tenant_id, dataset_id, tag_id, lock=True)
            before = _tag_snapshot(tag)
            if name is not None:
                display = _display_name(name, field="tag name")
                normalized = _normalized_name(display)
                duplicate = session.scalar(select(KnowledgeTag.id).where(
                    KnowledgeTag.tenant_id == tenant_id, KnowledgeTag.dataset_id == dataset_id,
                    KnowledgeTag.normalized_name == normalized, KnowledgeTag.id != tag.id))
                if duplicate:
                    raise GovernanceConflict("tag name already exists")
                tag.name, tag.normalized_name = display, normalized
            if color is not None:
                tag.color = _clean(color, 32)
            if description is not None:
                tag.description = _clean(description)
            documents = set(session.scalars(select(DocumentTag.document_id).where(DocumentTag.tag_id == tag.id)))
            self._sync_tag_projection(session, documents)
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.update", resource_type="knowledge_tag", resource_id=tag.id,
                before=before, after=_tag_snapshot(tag))
            self._commit(session, "tag name already exists")
            return tag

    def attach_tag(self, tenant_id: str, dataset_id: str, document_id: str, tag_id: str,
                   *, audit: AuditContext) -> DocumentTag:
        with Session(self.engine, expire_on_commit=False) as session:
            self._document(session, tenant_id, dataset_id, document_id)
            self._tag(session, tenant_id, dataset_id, tag_id)
            existing = session.scalar(select(DocumentTag).where(DocumentTag.tenant_id == tenant_id,
                DocumentTag.dataset_id == dataset_id, DocumentTag.document_id == document_id,
                DocumentTag.tag_id == tag_id))
            if existing is not None:
                return existing
            link = DocumentTag(id=self._new_id("document-tag"), tenant_id=tenant_id,
                dataset_id=dataset_id, document_id=document_id, tag_id=tag_id,
                created_by=audit.actor_id)
            session.add(link)
            session.flush()
            self._sync_tag_projection(session, {document_id})
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.attach", resource_type="document", resource_id=document_id,
                after={"tag_id": tag_id})
            self._commit(session, "document tag attachment failed")
            return link

    def detach_tag(self, tenant_id: str, dataset_id: str, document_id: str, tag_id: str,
                   *, audit: AuditContext) -> bool:
        with Session(self.engine) as session:
            self._document(session, tenant_id, dataset_id, document_id)
            self._tag(session, tenant_id, dataset_id, tag_id)
            link = session.scalar(select(DocumentTag).where(DocumentTag.tenant_id == tenant_id,
                DocumentTag.dataset_id == dataset_id, DocumentTag.document_id == document_id,
                DocumentTag.tag_id == tag_id))
            if link is None:
                return False
            session.delete(link)
            session.flush()
            self._sync_tag_projection(session, {document_id})
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.detach", resource_type="document", resource_id=document_id,
                before={"tag_id": tag_id})
            self._commit(session, "document tag detachment failed")
            return True

    def list_document_tags(self, tenant_id: str, dataset_id: str, document_id: str) -> list[str]:
        with Session(self.engine) as session:
            self._document(session, tenant_id, dataset_id, document_id)
            return list(session.scalars(select(DocumentTag.tag_id).join(
                KnowledgeTag, KnowledgeTag.id == DocumentTag.tag_id).where(
                DocumentTag.document_id == document_id).order_by(
                KnowledgeTag.normalized_name, KnowledgeTag.id)))

    @staticmethod
    def _tag_projection(session: Session, tag: KnowledgeTag) -> TagProjection:
        usage = int(session.scalar(select(func.count(DocumentTag.id)).where(
            DocumentTag.tag_id == tag.id)) or 0)
        return TagProjection(tag.id, tag.tenant_id, tag.dataset_id, tag.name,
            tag.normalized_name, tag.color, tag.description, tag.created_by, usage)

    def list_tags(self, tenant_id: str, dataset_id: str) -> list[TagProjection]:
        with Session(self.engine) as session:
            self._dataset(session, tenant_id, dataset_id)
            tags = list(session.scalars(select(KnowledgeTag).where(
                KnowledgeTag.tenant_id == tenant_id, KnowledgeTag.dataset_id == dataset_id
            ).order_by(KnowledgeTag.normalized_name, KnowledgeTag.id)))
            return [self._tag_projection(session, tag) for tag in tags]

    def merge_tags(self, *, tenant_id: str, dataset_id: str, source_tag_id: str,
                   target_tag_id: str, audit: AuditContext) -> TagProjection:
        if source_tag_id == target_tag_id:
            raise GovernanceConflict("source and target tags must differ")
        with Session(self.engine, expire_on_commit=False) as session:
            source = self._tag(session, tenant_id, dataset_id, source_tag_id, lock=True)
            target = self._tag(session, tenant_id, dataset_id, target_tag_id, lock=True)
            source_before = _tag_snapshot(source)
            target_documents = set(session.scalars(select(DocumentTag.document_id).where(
                DocumentTag.tag_id == target.id)))
            source_links = list(session.scalars(select(DocumentTag).where(DocumentTag.tag_id == source.id)))
            affected = set(target_documents)
            for link in source_links:
                affected.add(link.document_id)
                if link.document_id in target_documents:
                    session.delete(link)
                else:
                    link.tag_id = target.id
                    target_documents.add(link.document_id)
            session.flush()
            session.delete(source)
            session.flush()
            self._sync_tag_projection(session, affected)
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.merge", resource_type="knowledge_tag", resource_id=target.id,
                before={"source": source_before, "target": _tag_snapshot(target)},
                after={"target": _tag_snapshot(target)})
            self._commit(session, "tag merge failed")
            return self._tag_projection(session, target)

    def delete_tag(self, tenant_id: str, dataset_id: str, tag_id: str, *, audit: AuditContext) -> None:
        with Session(self.engine) as session:
            tag = self._tag(session, tenant_id, dataset_id, tag_id, lock=True)
            if session.scalar(select(DocumentTag.id).where(DocumentTag.tag_id == tag.id).limit(1)):
                raise GovernanceConflict("attached tag cannot be deleted")
            self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action="tag.delete", resource_type="knowledge_tag", resource_id=tag.id,
                before=_tag_snapshot(tag))
            session.delete(tag)
            self._commit(session, "tag deletion failed")

    def backfill_legacy_management(self, tenant_id: str, dataset_id: str) -> GovernanceBackfillResult:
        audit = AuditContext.system(SYSTEM_BACKFILL_ACTOR,
            f"system:governance-backfill:{uuid.uuid4().hex[:16]}")
        folders_created = tags_created = documents_assigned = tag_links_created = 0
        with Session(self.engine) as session:
            self._dataset(session, tenant_id, dataset_id)
            documents = list(session.scalars(select(Document).where(Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id).order_by(Document.id)))
            for document in documents:
                parent: KnowledgeFolder | None = None
                raw_path = str(document.logical_folder_path or "").strip().strip("/")
                if raw_path:
                    for component in raw_path.split("/"):
                        display = _display_name(component, field="folder name")
                        normalized = _normalized_name(display)
                        parent_key = "" if parent is None else parent.id
                        folder = self._sibling(session, tenant_id, dataset_id, parent_key, normalized)
                        if folder is None:
                            folder = KnowledgeFolder(id=self._new_id("folder"), tenant_id=tenant_id,
                                dataset_id=dataset_id, parent_id=None if parent is None else parent.id,
                                parent_key=parent_key, name=display, normalized_name=normalized,
                                path=display if parent is None else f"{parent.path}/{display}",
                                description="", sort_order=0, status="active", created_by=audit.actor_id)
                            folder.path_hash = path_hash(folder.path)
                            session.add(folder)
                            session.flush()
                            folders_created += 1
                        parent = folder
                    if document.folder_id != parent.id:
                        document.folder_id = parent.id
                        document.logical_folder_path = parent.path
                        documents_assigned += 1
                metadata = dict(document.parser_meta or {})
                raw_tags = (metadata.get("management") or {}).get("tags")
                seen: set[str] = set()
                if isinstance(raw_tags, list):
                    for raw in raw_tags:
                        try:
                            display = _display_name(str(raw), field="tag name")
                        except ValueError:
                            continue
                        normalized = _normalized_name(display)
                        if normalized in seen:
                            continue
                        seen.add(normalized)
                        tag = session.scalar(select(KnowledgeTag).where(
                            KnowledgeTag.tenant_id == tenant_id, KnowledgeTag.dataset_id == dataset_id,
                            KnowledgeTag.normalized_name == normalized))
                        if tag is None:
                            tag = KnowledgeTag(id=self._new_id("tag"), tenant_id=tenant_id,
                                dataset_id=dataset_id, name=display, normalized_name=normalized,
                                color="", description="", created_by=audit.actor_id)
                            session.add(tag)
                            session.flush()
                            tags_created += 1
                        linked = session.scalar(select(DocumentTag.id).where(
                            DocumentTag.document_id == document.id, DocumentTag.tag_id == tag.id))
                        if linked is None:
                            session.add(DocumentTag(id=self._new_id("document-tag"), tenant_id=tenant_id,
                                dataset_id=dataset_id, document_id=document.id, tag_id=tag.id,
                                created_by=audit.actor_id))
                            tag_links_created += 1
                session.flush()
                self._sync_tag_projection(session, {document.id})
            result = GovernanceBackfillResult(folders_created, tags_created,
                documents_assigned, tag_links_created)
            if result != GovernanceBackfillResult():
                self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                    action="governance.compatibility.backfill", resource_type="dataset",
                    resource_id=dataset_id, after={
                        "folders_created": folders_created, "tags_created": tags_created,
                        "documents_assigned": documents_assigned,
                        "tag_links_created": tag_links_created})
            self._commit(session, "governance compatibility backfill failed")
            return result

    def append_audit_event(self, *, tenant_id: str, dataset_id: str, audit: AuditContext,
                           action: str, resource_type: str, resource_id: str,
                           before_snapshot: dict[str, Any] | None = None,
                           after_snapshot: dict[str, Any] | None = None,
                           occurred_at: datetime | None = None) -> KnowledgeAuditEvent:
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            event = self._event(session, tenant_id=tenant_id, dataset_id=dataset_id, audit=audit,
                action=action, resource_type=resource_type, resource_id=resource_id,
                before=before_snapshot, after=after_snapshot, occurred_at=occurred_at)
            session.commit()
            return event

    def list_audit_events(self, tenant_id: str, dataset_id: str, *, actor_id: str | None = None,
                          action: str | None = None, resource_type: str | None = None,
                          resource_id: str | None = None, request_id: str | None = None,
                          occurred_from: datetime | None = None, occurred_to: datetime | None = None,
                          before_sequence: int | None = None,
                          limit: int = 100) -> list[KnowledgeAuditEvent]:
        bounded = max(1, min(int(limit), 500))
        with Session(self.engine, expire_on_commit=False) as session:
            self._dataset(session, tenant_id, dataset_id)
            query = select(KnowledgeAuditEvent).where(KnowledgeAuditEvent.tenant_id == tenant_id,
                KnowledgeAuditEvent.dataset_id == dataset_id)
            for column, value in ((KnowledgeAuditEvent.actor_id, actor_id),
                                  (KnowledgeAuditEvent.action, action),
                                  (KnowledgeAuditEvent.resource_type, resource_type),
                                  (KnowledgeAuditEvent.resource_id, resource_id),
                                  (KnowledgeAuditEvent.request_id, request_id)):
                if value is not None:
                    query = query.where(column == value)
            if occurred_from is not None:
                query = query.where(KnowledgeAuditEvent.occurred_at >= occurred_from)
            if occurred_to is not None:
                query = query.where(KnowledgeAuditEvent.occurred_at <= occurred_to)
            if before_sequence is not None:
                if int(before_sequence) < 1:
                    raise ValueError("before_sequence must be positive")
                query = query.where(KnowledgeAuditEvent.sequence < int(before_sequence))
            return list(session.scalars(query.order_by(
                KnowledgeAuditEvent.sequence.desc()).limit(bounded)))

__all__ = ["AuditContext", "FolderProjection", "GovernanceBackfillResult",
    "GovernanceConflict", "GovernanceError", "GovernanceNotFound",
    "KnowledgeGovernanceRepository", "SYSTEM_BACKFILL_ACTOR", "TagProjection", "path_hash",
    "sanitize_audit_snapshot"]
