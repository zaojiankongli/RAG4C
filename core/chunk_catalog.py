"""Authoritative chunk heads, immutable revisions and projection fencing."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, String, cast, func, or_, select, update
from sqlalchemy.orm import Session

from models.orm import ChunkHead, ChunkRevision


class ChunkRevisionConflict(RuntimeError):
    pass


class ChunkHeadNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ChunkHeadPage:
    items: list[ChunkHead]
    total: int
    known_parent_ids: tuple[str, ...]
    missing_parent_ids: tuple[str, ...]
    child_counts: dict[str, int]


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _context_header(metadata: dict[str, Any], explicit: str | None) -> str:
    if explicit is not None:
        return explicit.strip()
    for key in ("context_header", "context"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class ChunkCatalog:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    def upsert_head(
        self,
        *,
        chunk_id: str,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        parent_chunk_id: str | None,
        chunk_index: int,
        chunk_role: str,
        document_revision: int,
        source_content: str,
        content: str,
        content_hash: str = "",
        content_revision: int | None = None,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
        context_header: str | None = None,
        session: Session | None = None,
    ) -> ChunkHead:
        if chunk_role not in {"parent", "child", "flat"}:
            raise ValueError("chunk_role must be parent, child or flat")

        def apply(target: Session) -> ChunkHead:
            head = target.get(ChunkHead, chunk_id)
            is_new = head is None
            if head is None:
                head = ChunkHead(id=chunk_id)
                target.add(head)
            head.tenant_id = tenant_id
            head.dataset_id = dataset_id
            head.document_id = document_id
            head.parent_chunk_id = parent_chunk_id
            head.chunk_index = int(chunk_index)
            head.chunk_role = chunk_role
            head.document_revision = max(0, int(document_revision))
            if is_new and content_revision is not None:
                head.content_revision = max(0, int(content_revision))
            elif head.content_revision is None:
                head.content_revision = 0
            head.source_content = source_content
            head.content = content
            head.content_hash = content_hash or _hash(content)
            head.enabled = bool(enabled)
            head.desired_index_revision = head.content_revision
            head.index_status = (
                "pending" if chunk_role != "parent" and enabled else "not_indexed"
            )
            head.chunk_metadata = metadata or {}
            head.context_header = _context_header(head.chunk_metadata, context_header)
            target.flush()
            return head

        if session is not None:
            return apply(session)
        with Session(self.engine, expire_on_commit=False) as owned:
            head = apply(owned)
            owned.commit()
            return head

    def get_head(
        self, chunk_id: str, *, session: Session | None = None, for_update: bool = False
    ) -> ChunkHead:
        def load(target: Session) -> ChunkHead:
            statement = select(ChunkHead).where(ChunkHead.id == chunk_id)
            if for_update:
                statement = statement.with_for_update()
            head = target.scalar(statement)
            if head is None:
                raise ChunkRevisionConflict("chunk does not exist")
            return head

        if session is not None:
            return load(session)
        with Session(self.engine, expire_on_commit=False) as owned:
            return load(owned)

    def list_document_heads_page(
        self, tenant_id: str, dataset_id: str, document_id: str, *, document_revision: int,
        offset: int, limit: int, query: str = "", include_disabled: bool = False,
    ) -> ChunkHeadPage:
        conditions = [ChunkHead.tenant_id == tenant_id, ChunkHead.dataset_id == dataset_id,
            ChunkHead.document_id == document_id, ChunkHead.document_revision == int(document_revision),
            ChunkHead.chunk_role != "parent"]
        if not include_disabled:
            conditions.append(ChunkHead.enabled.is_(True))
        keyword = query.strip().casefold()
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(or_(func.lower(ChunkHead.id).like(pattern), func.lower(ChunkHead.content).like(pattern),
                func.lower(ChunkHead.context_header).like(pattern),
                func.lower(cast(ChunkHead.chunk_metadata["heading"], String)).like(pattern),
                func.lower(cast(ChunkHead.chunk_metadata["title"], String)).like(pattern),
                func.lower(cast(ChunkHead.chunk_metadata["section"], String)).like(pattern)))
        with Session(self.engine, expire_on_commit=False) as session:
            total = int(session.scalar(select(func.count()).select_from(ChunkHead).where(*conditions)) or 0)
            items = list(session.scalars(select(ChunkHead).where(*conditions).order_by(ChunkHead.chunk_index, ChunkHead.id).offset(int(offset)).limit(int(limit))))
            parent_refs = {str(item.parent_chunk_id) for item in items if item.parent_chunk_id}
            known = set(session.scalars(select(ChunkHead.id).where(ChunkHead.tenant_id == tenant_id,
                ChunkHead.dataset_id == dataset_id, ChunkHead.document_id == document_id,
                ChunkHead.document_revision == int(document_revision), ChunkHead.id.in_(parent_refs)))) if parent_refs else set()
            row_ids = [item.id for item in items]
            child_counts = {str(parent): int(count) for parent, count in session.execute(
                select(ChunkHead.parent_chunk_id, func.count(ChunkHead.id)).where(ChunkHead.tenant_id == tenant_id,
                    ChunkHead.dataset_id == dataset_id, ChunkHead.document_id == document_id,
                    ChunkHead.document_revision == int(document_revision), ChunkHead.parent_chunk_id.in_(row_ids)).group_by(ChunkHead.parent_chunk_id)
            )} if row_ids else {}
            return ChunkHeadPage(items, total, tuple(sorted(known)), tuple(sorted(parent_refs-known)), child_counts)

    def get_head_scoped(self, tenant_id: str, dataset_id: str, document_id: str, chunk_id: str, *, document_revision: int) -> ChunkHead:
        with Session(self.engine, expire_on_commit=False) as session:
            head = session.scalar(select(ChunkHead).where(ChunkHead.id == chunk_id, ChunkHead.tenant_id == tenant_id,
                ChunkHead.dataset_id == dataset_id, ChunkHead.document_id == document_id,
                ChunkHead.document_revision == int(document_revision)))
            if head is None:
                raise ChunkHeadNotFound("chunk does not exist in dataset scope")
            return head

    def relation_facts(
        self,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        *,
        document_revision: int,
        chunk_id: str,
        parent_chunk_id: str | None,
    ) -> tuple[str, int]:
        with Session(self.engine) as session:
            relation = "none"
            if parent_chunk_id:
                parent_exists = session.scalar(select(func.count()).select_from(ChunkHead).where(
                    ChunkHead.id == parent_chunk_id,
                    ChunkHead.tenant_id == tenant_id,
                    ChunkHead.dataset_id == dataset_id,
                    ChunkHead.document_id == document_id,
                    ChunkHead.document_revision == int(document_revision),
                ))
                relation = "known" if int(parent_exists or 0) > 0 else "missing"
            child_count = int(session.scalar(select(func.count()).select_from(ChunkHead).where(
                ChunkHead.tenant_id == tenant_id,
                ChunkHead.dataset_id == dataset_id,
                ChunkHead.document_id == document_id,
                ChunkHead.document_revision == int(document_revision),
                ChunkHead.parent_chunk_id == chunk_id,
            )) or 0)
            return relation, child_count

    def list_revisions(self, chunk_id: str) -> list[ChunkRevision]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(ChunkRevision)
                    .where(ChunkRevision.chunk_id == chunk_id)
                    .order_by(ChunkRevision.revision)
                )
            )

    def count_document_heads(
        self,
        document_id: str,
        *,
        document_revision: int,
        enabled_only: bool = True,
        session: Session | None = None,
    ) -> int:
        """Count document/quota chunks, including parent heads by historical semantics."""
        def count(target: Session) -> int:
            conditions = [
                ChunkHead.document_id == document_id,
                ChunkHead.document_revision == document_revision,
            ]
            if enabled_only:
                conditions.append(ChunkHead.enabled.is_(True))
            return int(
                target.scalar(select(func.count(ChunkHead.id)).where(*conditions)) or 0
            )

        if session is not None:
            return count(session)
        with Session(self.engine) as owned:
            return count(owned)

    def list_document_heads(
        self,
        document_id: str,
        *,
        document_revision: int,
        include_disabled: bool = False,
    ) -> list[ChunkHead]:
        conditions = [
            ChunkHead.document_id == document_id,
            ChunkHead.document_revision == document_revision,
            ChunkHead.chunk_role != "parent",
        ]
        if not include_disabled:
            conditions.append(ChunkHead.enabled.is_(True))
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(ChunkHead)
                    .where(*conditions)
                    .order_by(ChunkHead.chunk_index, ChunkHead.id)
                )
            )

    def count_retrievable_heads(
        self,
        document_id: str,
        *,
        document_revision: int,
        session: Session | None = None,
    ) -> int:
        def count(target: Session) -> int:
            return int(
                target.scalar(
                    select(func.count(ChunkHead.id)).where(
                        ChunkHead.document_id == document_id,
                        ChunkHead.document_revision == document_revision,
                        ChunkHead.enabled.is_(True),
                        ChunkHead.chunk_role != "parent",
                    )
                )
                or 0
            )

        if session is not None:
            return count(session)
        with Session(self.engine) as owned:
            return count(owned)

    def edit_chunk(
        self,
        chunk_id: str,
        *,
        expected_revision: int,
        content: str,
        editor_id: str,
        edit_source: str = "user",
        enabled: bool | None = None,
        session: Session | None = None,
    ) -> ChunkHead:
        def apply(target: Session) -> ChunkHead:
            head = self.get_head(chunk_id, session=target, for_update=True)
            if head.content_revision != expected_revision:
                raise ChunkRevisionConflict("chunk revision conflict")
            target.add(
                ChunkRevision(
                    id=self._new_id("chunk-revision"),
                    chunk_id=head.id,
                    tenant_id=head.tenant_id,
                    dataset_id=head.dataset_id,
                    document_id=head.document_id,
                    revision=head.content_revision,
                    content=head.content,
                    content_hash=head.content_hash,
                    enabled=head.enabled,
                    editor_id=head.editor_id,
                    edit_source=head.edit_source,
                )
            )
            head.content = content
            head.content_hash = _hash(content)
            head.content_revision = expected_revision + 1
            head.desired_index_revision = head.content_revision
            head.index_status = "pending"
            head.editor_id = editor_id
            head.edit_source = edit_source
            if enabled is not None:
                head.enabled = bool(enabled)
            target.flush()
            return head

        if session is not None:
            return apply(session)
        with Session(self.engine, expire_on_commit=False) as owned:
            head = apply(owned)
            owned.commit()
            return head

    def tombstone_chunk(
        self,
        chunk_id: str,
        *,
        expected_revision: int,
        editor_id: str,
        session: Session | None = None,
    ) -> ChunkHead:
        """Disable a head; repeating the current tombstone is idempotent."""
        if session is not None:
            head = self.get_head(chunk_id, session=session, for_update=True)
            if head.content_revision != expected_revision:
                raise ChunkRevisionConflict("chunk revision conflict")
            if not head.enabled:
                return head
            return self.edit_chunk(
                chunk_id,
                expected_revision=expected_revision,
                content=head.content,
                editor_id=editor_id,
                edit_source="delete",
                enabled=False,
                session=session,
            )
        with Session(self.engine, expire_on_commit=False) as owned:
            head = self.tombstone_chunk(
                chunk_id,
                expected_revision=expected_revision,
                editor_id=editor_id,
                session=owned,
            )
            owned.commit()
            return head

    def revert_chunk(
        self,
        chunk_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        editor_id: str,
    ) -> ChunkHead:
        with Session(self.engine, expire_on_commit=False) as session:
            revision = session.scalar(
                select(ChunkRevision).where(
                    ChunkRevision.chunk_id == chunk_id,
                    ChunkRevision.revision == target_revision,
                )
            )
            if revision is None:
                raise ChunkRevisionConflict("target revision does not exist")
            content = revision.content
            enabled = revision.enabled
        return self.edit_chunk(
            chunk_id,
            expected_revision=expected_revision,
            content=content,
            editor_id=editor_id,
            edit_source="revert",
            enabled=enabled,
        )

    def list_projection_candidates(
        self, document_id: str, *, document_revision: int
    ) -> list[ChunkHead]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(ChunkHead)
                    .where(
                        ChunkHead.document_id == document_id,
                        ChunkHead.document_revision == document_revision,
                        ChunkHead.enabled.is_(True),
                        ChunkHead.chunk_role != "parent",
                    )
                    .order_by(ChunkHead.chunk_index, ChunkHead.id)
                )
            )

    def update_context_header(
        self, chunk_id: str, *, input_revision: int, context_header: str
    ) -> bool:
        with Session(self.engine) as session:
            result = session.execute(
                update(ChunkHead)
                .where(
                    ChunkHead.id == chunk_id,
                    ChunkHead.content_revision == input_revision,
                )
                .values(context_header=context_header.strip())
            )
            session.commit()
            return result.rowcount == 1

    def mark_indexed(self, chunk_id: str, *, expected_revision: int) -> bool:
        with Session(self.engine) as session:
            result = session.execute(
                update(ChunkHead)
                .where(
                    ChunkHead.id == chunk_id,
                    ChunkHead.desired_index_revision == expected_revision,
                )
                .values(indexed_revision=expected_revision, index_status="ready")
            )
            session.commit()
            return result.rowcount == 1
