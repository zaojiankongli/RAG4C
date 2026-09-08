"""Authenticated, tenant-scoped enterprise document catalog API."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from core import catalog
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

router = APIRouter(prefix="/api/knowledge-bases/{dataset_id}", tags=["document-catalog"])

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]

DocumentStatus = Literal[
    "all",
    "processing",
    "waiting",
    "parsing",
    "splitting",
    "indexing",
    "completed",
    "error",
    "deleting",
]
FolderMode = Literal["exact", "subtree"]
DocumentSort = Literal["updated_at_desc", "created_at_asc", "name_asc"]


def _catalog_query_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, catalog.DocumentCatalogCapabilityError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "document_catalog_capability_unavailable",
                "message": "当前文档目录能力无法在已部署数据库上安全执行",
            },
        )
    return HTTPException(
        status_code=422,
        detail={
            "code": "document_catalog_query_invalid",
            "message": "文档目录查询参数无效",
        },
    )



def _request_catalog_engine(request: Request):
    engine = getattr(request.app.state, "knowledge_auth_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "document_catalog_unavailable",
                "message": "文档目录只读数据库尚未配置",
            },
        )
    return engine
@router.get("/documents")
def list_document_catalog(
    request: Request,
    dataset_id: DatasetId,
    actor: ReadActor,
    offset: Annotated[int, Query(ge=0, le=catalog.DOCUMENT_CATALOG_MAX_OFFSET)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str, Query(alias="q", max_length=256)] = "",
    status: DocumentStatus = "all",
    doc_type: Annotated[str, Query(min_length=1, max_length=32)] = "all",
    engine: Annotated[str, Query(min_length=1, max_length=64)] = "all",
    folder: Annotated[str, Query(min_length=1, max_length=1024)] = "all",
    folder_mode: FolderMode = "exact",
    tag: Annotated[str, Query(min_length=1, max_length=32)] = "all",
    lifecycle_state: Annotated[str, Query(min_length=1, max_length=24)] = "all",
    sort: DocumentSort = "updated_at_desc",
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, object]:
    try:
        return catalog.list_documents_page(
        actor.tenant_id,
        dataset_id,
        offset=offset,
        limit=limit,
        query=query,
        status=status,
        doc_type=doc_type,
        engine=engine,
        folder=folder,
        folder_mode=folder_mode,
        tag=tag,
            lifecycle_state=lifecycle_state,
            sort=sort,
            cursor=cursor,
            engine_override=_request_catalog_engine(request),
        )
    except (ValueError, catalog.DocumentCatalogCapabilityError) as exc:
        raise _catalog_query_error(exc) from exc


@router.get("/documents/summary")
def document_catalog_summary(
    request: Request,
    dataset_id: DatasetId,
    actor: ReadActor,
    recent_limit: Annotated[int, Query(ge=1, le=100)] = 6,
) -> dict[str, object]:
    try:
        return catalog.summarize_documents(
            actor.tenant_id,
            dataset_id,
            recent_limit=recent_limit,
            engine_override=_request_catalog_engine(request),
        )
    except (ValueError, catalog.DocumentCatalogCapabilityError) as exc:
        raise _catalog_query_error(exc) from exc


__all__ = ["router"]
