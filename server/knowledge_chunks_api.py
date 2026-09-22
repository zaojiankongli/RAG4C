"""Authenticated dataset-scoped ChunkHead intervention APIs."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import catalog
from core.chunk_catalog import ChunkCatalog, ChunkHeadNotFound, ChunkRevisionConflict
from core.knowledge_permissions import KNOWLEDGE_DELETE, KNOWLEDGE_READ, KNOWLEDGE_WRITE
from models.orm import Document
from server import documents
from server.chunk_operations import (
    ChunkAuthorityIncomplete,
    delete_document_chunk_artifacts,
    revert_document_chunk_artifacts,
    update_document_chunk_artifacts,
)
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

_OPENAPI_BEARER = HTTPBearer(auto_error=False, scheme_name="KnowledgeBearerAuth", description="Actor-bound signed KnowledgeOps bearer token.")
router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/documents/{doc_id}/chunks",
    tags=["knowledge-chunks"],
    dependencies=[Depends(_OPENAPI_BEARER)],
)
_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
DocumentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ChunkId = Annotated[str, Path(min_length=1, max_length=512, pattern=_SAFE_ID)]
_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_WRITE = require_knowledge_permission(KNOWLEDGE_WRITE, resolve_path_dataset("dataset_id"))
_DELETE_PERMISSION = require_knowledge_permission(KNOWLEDGE_DELETE, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
WriteActor = Annotated[KnowledgeActor, Depends(_WRITE)]
DeleteActor = Annotated[KnowledgeActor, Depends(_DELETE_PERMISSION)]
_update = update_document_chunk_artifacts
_delete = delete_document_chunk_artifacts
_revert = revert_document_chunk_artifacts
_reserve = documents._reserve_chunk_mutation
_release = documents._release_document_operation
_TOKEN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]|[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ChunkPatch(StrictModel):
    """Edit a chunk's text and/or flip its enabled flag, under optimistic concurrency.

    ``reason`` is recorded on the chunk head for operator audit; it is not carried on
    historical revision rows (see ``docs/compose/spec/chunk-lifecycle-writers.md`` S2.4).
    """

    text: str | None = Field(default=None, min_length=1, max_length=200_000)
    enabled: bool | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=500)
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def _requires_a_change(self) -> "ChunkPatch":
        if self.text is None and self.enabled is None:
            raise ValueError("text 与 enabled 至少给出一个")
        return self


class ChunkRevertRequest(StrictModel):
    """Roll a chunk head back to one of its recorded revisions."""

    target_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    reason: str | None = Field(default=None, max_length=500)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "knowledge_resource_not_found", "message": "资源不存在"})


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "knowledge_chunk_conflict", "message": message})


def _authority_unavailable() -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "knowledge_chunk_authority_unavailable", "message": "解析干预工作区需要 SQL ChunkHead 权威；请先迁移到 shadow 或 active 模式"})


def _authority_mode(request: Request) -> str:
    explicit = getattr(request.app.state, "chunk_authority_mode", None)
    return str(explicit or documents._configured_chunk_authority_mode())


def _document(engine: Any, actor: KnowledgeActor, dataset_id: str, doc_id: str) -> Document:
    with Session(engine, expire_on_commit=False) as session:
        document = session.scalar(select(Document).where(
            Document.id == doc_id,
            Document.tenant_id == actor.tenant_id,
            Document.dataset_id == dataset_id,
        ))
        if document is None:
            raise _not_found()
        return document


def sanitize_reference(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    normalized = raw.replace("\\", "/")
    try:
        parsed = urlsplit(normalized)
        scheme = parsed.scheme.casefold()
        if scheme == "file":
            return PurePosixPath(parsed.path).name[:160] or "protected_reference"
        if scheme:
            if not parsed.netloc or not parsed.hostname:
                return "protected_reference"
            port_value = parsed.port
            port = f":{port_value}" if port_value is not None else ""
            return urlunsplit((scheme, parsed.hostname + port, parsed.path, "", ""))[:512]
        if "/" in normalized:
            return PurePosixPath(normalized).name[:160] or "protected_reference"
        return normalized[:160]
    except (TypeError, ValueError, OverflowError):
        return "protected_reference"


def _bounded_string(value: Any, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_length] if text else None


def _first_bounded_string(metadata: dict[str, Any], keys: tuple[str, ...], *, max_length: int) -> str | None:
    for key in keys:
        value = _bounded_string(metadata.get(key), max_length=max_length)
        if value is not None:
            return value
    return None


def _bounded_page(value: Any) -> str | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -(2**31) <= value <= 2**31 - 1 else None
    return _bounded_string(value, max_length=64)


def _sequence(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return int(fallback)
    if isinstance(value, int) and 0 <= value <= 2**31 - 1:
        return value
    return int(fallback)


def _token_estimate(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _projection(
    head: Any,
    *,
    child_count: int = 0,
    parent_relation: str = "none",
    include_source_content: bool = False,
) -> dict[str, Any]:
    metadata = dict(head.chunk_metadata or {})
    text = str(head.content or "")
    desired = int(head.desired_index_revision or 0)
    indexed = int(head.indexed_revision or 0)
    status = str(head.index_status or "")
    payload: dict[str, Any] = {
        "chunk_id": str(head.id), "doc_id": str(head.document_id),
        "tenant_id": str(head.tenant_id), "dataset_id": str(head.dataset_id),
        "text": text, "text_hash": str(head.content_hash or ""),
        "content_revision": int(head.content_revision or 0),
        "document_revision": int(head.document_revision or 0),
        "enabled": bool(head.enabled), "chunk_role": str(head.chunk_role or "flat"),
        "parent_chunk_id": head.parent_chunk_id, "parent_relation": parent_relation,
        "child_count": int(child_count), "seq": _sequence(metadata.get("seq"), head.chunk_index),
        "context": str(head.context_header or ""), "char_count": len(text),
        "token_estimate": _token_estimate(text), "desired_index_revision": desired,
        "indexed_revision": indexed, "index_status": status,
        "projection_pending": indexed < desired or status != "ready",
        "editor_id": str(head.editor_id or ""), "edit_source": str(head.edit_source or ""),
        "created_at": head.created_at.isoformat() if head.created_at else None,
        "updated_at": head.updated_at.isoformat() if head.updated_at else None,
    }
    reason = _bounded_string(metadata.get("edit_reason"), max_length=500)
    if reason is not None:
        payload["edit_reason"] = reason
        payload["edit_reason_at"] = _bounded_string(
            metadata.get("edit_reason_at"), max_length=64
        )
    if include_source_content:
        # Parser output for this chunk, kept separate from the (possibly hand-edited)
        # head content so the workbench can show what a human changed.  Single-chunk
        # reads only: doubling every list row with it would bloat the list response.
        payload["source_content"] = str(head.source_content or "")
    page = _bounded_page(metadata.get("page") if metadata.get("page") is not None else metadata.get("page_number"))
    heading = _first_bounded_string(metadata, ("heading", "title", "section"), max_length=512)
    language = _bounded_string(metadata.get("language"), max_length=64)
    mime_type = _bounded_string(metadata.get("mime_type"), max_length=128)
    source = _first_bounded_string(metadata, ("source", "source_uri"), max_length=2048)
    if page is not None:
        payload["page"] = page
    if heading is not None:
        payload["heading"] = heading
    if language is not None:
        payload["language"] = language
    if mime_type is not None:
        payload["mime_type"] = mime_type
    if source is not None:
        payload["source_reference"] = sanitize_reference(source)
    return payload


def _relation_projection(chunks: ChunkCatalog, actor: KnowledgeActor, dataset_id: str, doc_id: str, document_revision: int, head: Any) -> tuple[str, int]:
    return chunks.relation_facts(
        actor.tenant_id,
        dataset_id,
        doc_id,
        document_revision=document_revision,
        chunk_id=str(head.id),
        parent_chunk_id=head.parent_chunk_id,
    )


def _catalog_and_document(request: Request, actor: KnowledgeActor, dataset_id: str, doc_id: str):
    mode = _authority_mode(request)
    if mode == "off":
        raise _authority_unavailable()
    engine = catalog.get_engine()
    document = _document(engine, actor, dataset_id, doc_id)
    return mode, ChunkCatalog(engine), document


@router.get("")
def list_chunks(
    request: Request,
    dataset_id: DatasetId,
    doc_id: DocumentId,
    actor: ReadActor,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    query: Annotated[str, Query(max_length=256)] = "",
    include_disabled: bool = False,
) -> dict[str, Any]:
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    page = chunks.list_document_heads_page(actor.tenant_id, dataset_id, doc_id, document_revision=int(document.content_revision or 0), offset=offset, limit=limit, query=query, include_disabled=include_disabled)
    projected = []
    for item in page.items:
        relation, child_count = _relation_projection(chunks, actor, dataset_id, doc_id, int(document.content_revision or 0), item)
        projected.append(_projection(item, child_count=child_count, parent_relation=relation))
    return {
        "authority_mode": mode,
        "items": projected,
        "total": page.total,
        "offset": offset,
        "limit": limit,
        "known_parent_ids": list(page.known_parent_ids),
        "missing_parent_ids": list(page.missing_parent_ids),
    }


@router.get("/{chunk_id}")
def get_chunk(request: Request, dataset_id: DatasetId, doc_id: DocumentId, chunk_id: ChunkId, actor: ReadActor) -> dict[str, Any]:
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    try:
        head = chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
    except ChunkHeadNotFound as exc:
        raise _not_found() from exc
    relation, child_count = _relation_projection(chunks, actor, dataset_id, doc_id, int(document.content_revision or 0), head)
    payload = _projection(head, parent_relation=relation, child_count=child_count, include_source_content=True)
    payload["authority_mode"] = mode
    return payload


@router.get("/{chunk_id}/revisions")
def list_chunk_revisions(
    request: Request,
    dataset_id: DatasetId,
    doc_id: DocumentId,
    chunk_id: ChunkId,
    actor: ReadActor,
) -> dict[str, Any]:
    """Immutable content snapshots this head has superseded, newest revision last.

    The head is resolved through the tenant/dataset/document scope first, so an
    unscoped ``chunk_id`` cannot reach another tenant's revision content.
    """
    _mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    try:
        chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
    except ChunkHeadNotFound as exc:
        raise _not_found() from exc
    revisions = chunks.list_revisions(actor.tenant_id, dataset_id, doc_id, chunk_id)
    return {
        "chunk_id": chunk_id,
        "items": [
            {
                "revision": int(item.revision),
                "content": str(item.content or ""),
                "content_hash": str(item.content_hash or ""),
                "enabled": bool(item.enabled),
                "editor_id": str(item.editor_id or ""),
                "edit_source": str(item.edit_source or ""),
                "edited_at": item.edited_at.isoformat() if item.edited_at else None,
            }
            for item in revisions
        ],
    }


@router.post("/{chunk_id}/revert")
def revert_chunk(
    request: Request,
    dataset_id: DatasetId,
    doc_id: DocumentId,
    chunk_id: ChunkId,
    body: ChunkRevertRequest,
    actor: WriteActor,
) -> dict[str, Any]:
    """Roll a chunk back to ``target_revision`` as a new head revision (never rewinding)."""
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    reserved = False
    try:
        current = chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
        if not current.enabled:
            raise _conflict("墓碑切片为只读状态，请先启用后再回滚")
        _reserve(doc_id)
        reserved = True
        result = _revert(
            doc_id,
            chunk_id,
            target_revision=body.target_revision,
            expected_revision=body.expected_revision,
            reason=body.reason,
            authority_mode=mode,
            pipeline=documents._get_ingest_pipeline(),
        )
        return _mutated_payload(request, actor, dataset_id, doc_id, chunk_id, list(result.operation_ids))
    except HTTPException:
        raise
    except (ChunkRevisionConflict, ChunkAuthorityIncomplete) as exc:
        raise _conflict(str(exc)) from exc
    except (KeyError, ChunkHeadNotFound) as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "knowledge_chunk_invalid", "message": str(exc)}) from exc
    finally:
        if reserved:
            _release(doc_id)


def _mutated_payload(request: Request, actor: KnowledgeActor, dataset_id: str, doc_id: str, chunk_id: str, operation_ids: list[str]) -> dict[str, Any]:
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    try:
        head = chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
    except ChunkHeadNotFound as exc:
        raise _not_found() from exc
    relation, child_count = _relation_projection(chunks, actor, dataset_id, doc_id, int(document.content_revision or 0), head)
    payload = _projection(head, parent_relation=relation, child_count=child_count, include_source_content=True)
    payload.update({"authority_mode": mode, "operation_ids": operation_ids, "projection_semantics": "active_async_durable" if mode == "active" else "shadow_authority_plus_legacy_sync"})
    return payload


@router.patch("/{chunk_id}")
def patch_chunk(request: Request, dataset_id: DatasetId, doc_id: DocumentId, chunk_id: ChunkId, body: ChunkPatch, actor: WriteActor) -> dict[str, Any]:
    """Edit text, tombstone (``enabled: false``) or restore (``enabled: true``) one chunk."""
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    reserved = False
    try:
        current = chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
        if not current.enabled and body.enabled is not True:
            raise _conflict("墓碑切片为只读状态，请先启用")
        _reserve(doc_id)
        reserved = True
        result = _update(
            doc_id,
            chunk_id,
            body.text or "",
            expected_revision=body.expected_revision,
            authority_mode=mode,
            pipeline=documents._get_ingest_pipeline(),
            reason=body.reason,
            enabled=body.enabled,
        )
        return _mutated_payload(request, actor, dataset_id, doc_id, chunk_id, list(result.operation_ids))
    except HTTPException:
        raise
    except (ChunkRevisionConflict, ChunkAuthorityIncomplete) as exc:
        raise _conflict(str(exc)) from exc
    except (KeyError, ChunkHeadNotFound) as exc:
        raise _not_found() from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "knowledge_chunk_invalid", "message": str(exc)}) from exc
    finally:
        if reserved:
            _release(doc_id)


@router.delete("/{chunk_id}")
def delete_chunk(request: Request, dataset_id: DatasetId, doc_id: DocumentId, chunk_id: ChunkId, actor: DeleteActor, expected_revision: Annotated[int, Query(ge=0)]) -> dict[str, Any]:
    mode, chunks, document = _catalog_and_document(request, actor, dataset_id, doc_id)
    reserved = False
    try:
        current = chunks.get_head_scoped(actor.tenant_id, dataset_id, doc_id, chunk_id, document_revision=int(document.content_revision or 0))
        if not current.enabled:
            raise _conflict("墓碑切片已经是只读状态")
        _reserve(doc_id)
        reserved = True
        result = _delete(doc_id, chunk_id, expected_revision=expected_revision, authority_mode=mode, pipeline=documents._get_ingest_pipeline())
        operation_ids = list(result.get("operation_ids", [])) if isinstance(result, dict) else list(result.operation_ids)
        return _mutated_payload(request, actor, dataset_id, doc_id, chunk_id, operation_ids)
    except HTTPException:
        raise
    except (ChunkRevisionConflict, ChunkAuthorityIncomplete) as exc:
        raise _conflict(str(exc)) from exc
    except (KeyError, ChunkHeadNotFound) as exc:
        raise _not_found() from exc
    finally:
        if reserved:
            _release(doc_id)


__all__ = ["router", "sanitize_reference"]
