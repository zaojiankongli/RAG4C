"""Authenticated KnowledgeOps folder and tag governance APIs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from core import catalog
from core.knowledge_governance import (
    GovernanceConflict,
    GovernanceNotFound,
    KnowledgeGovernanceRepository,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ, KNOWLEDGE_WRITE
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

router = APIRouter(prefix="/api/knowledge-bases/{dataset_id}", tags=["knowledge-governance"])

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
FolderId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
TagId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
DocumentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]

_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_WRITE = require_knowledge_permission(KNOWLEDGE_WRITE, resolve_path_dataset("dataset_id"))
_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id"))
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
WriteActor = Annotated[KnowledgeActor, Depends(_WRITE)]
ManageActor = Annotated[KnowledgeActor, Depends(_MANAGE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FolderCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, min_length=1, max_length=64)
    description: str = Field(default="", max_length=2048)
    sort_order: int = Field(default=0, ge=-1_000_000, le=1_000_000, strict=True)


class FolderUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    sort_order: int | None = Field(default=None, ge=-1_000_000, le=1_000_000, strict=True)


class FolderMove(StrictModel):
    new_parent_id: str | None = Field(default=None, min_length=1, max_length=64)


class TagCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    color: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2048)


class TagUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    color: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=2048)


class TagMerge(StrictModel):
    source_tag_id: str = Field(min_length=1, max_length=64)


def _repository() -> KnowledgeGovernanceRepository:
    return KnowledgeGovernanceRepository(catalog.get_engine())


def _raise_governance(exc: Exception) -> None:
    if isinstance(exc, GovernanceNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, GovernanceConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_governance_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_governance_invalid", "message": str(exc)},
        ) from exc
    raise exc


def _folder_payload(folder: Any) -> dict[str, Any]:
    return {
        "id": folder.id,
        "tenant_id": folder.tenant_id,
        "dataset_id": folder.dataset_id,
        "parent_id": folder.parent_id,
        "name": folder.name,
        "path": folder.path,
        "description": folder.description,
        "sort_order": folder.sort_order,
        "status": folder.status,
        "created_by": folder.created_by,
        "depth": getattr(folder, "depth", folder.path.count("/")),
        "child_count": getattr(folder, "child_count", 0),
    }


def _tag_payload(tag: Any) -> dict[str, Any]:
    return {
        "id": tag.id,
        "tenant_id": tag.tenant_id,
        "dataset_id": tag.dataset_id,
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        "created_by": tag.created_by,
        "usage_count": getattr(tag, "usage_count", 0),
    }


def _folder_projection(
    repository: KnowledgeGovernanceRepository, tenant_id: str, dataset_id: str, folder_id: str
) -> Any:
    for folder in repository.list_folders(tenant_id, dataset_id, include_archived=True):
        if folder.id == folder_id:
            return folder
    raise GovernanceNotFound("folder does not exist in dataset scope")


def _tag_projection(
    repository: KnowledgeGovernanceRepository, tenant_id: str, dataset_id: str, tag_id: str
) -> Any:
    for tag in repository.list_tags(tenant_id, dataset_id):
        if tag.id == tag_id:
            return tag
    raise GovernanceNotFound("tag does not exist in dataset scope")


@router.get("/folders")
def list_folders(
    dataset_id: DatasetId,
    actor: ReadActor,
    include_archived: bool = False,
) -> dict[str, Any]:
    try:
        rows = _repository().list_folders(
            actor.tenant_id, dataset_id, include_archived=include_archived
        )
    except Exception as exc:
        _raise_governance(exc)
    return {"items": [_folder_payload(item) for item in rows], "count": len(rows)}


@router.post("/folders", status_code=status.HTTP_201_CREATED)
def create_folder(dataset_id: DatasetId, body: FolderCreate, actor: WriteActor) -> dict[str, Any]:
    try:
        repository = _repository()
        folder = repository.create_folder(
            actor.tenant_id,
            dataset_id,
            body.name,
            parent_id=body.parent_id,
            description=body.description,
            sort_order=body.sort_order,
            audit=actor.to_audit_context(),
        )
        projection = _folder_projection(repository, actor.tenant_id, dataset_id, folder.id)
    except Exception as exc:
        _raise_governance(exc)
    return _folder_payload(projection)


@router.patch("/folders/{folder_id}")
def update_folder(
    dataset_id: DatasetId,
    folder_id: FolderId,
    body: FolderUpdate,
    actor: WriteActor,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_governance_invalid", "message": "至少提供一个修改字段"},
        )
    try:
        repository = _repository()
        repository.update_folder(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            folder_id=folder_id,
            **values,
            audit=actor.to_audit_context(),
        )
        projection = _folder_projection(repository, actor.tenant_id, dataset_id, folder_id)
    except Exception as exc:
        _raise_governance(exc)
    return _folder_payload(projection)


@router.post("/folders/{folder_id}/move")
def move_folder(
    dataset_id: DatasetId,
    folder_id: FolderId,
    body: FolderMove,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        repository = _repository()
        repository.move_folder(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            folder_id=folder_id,
            new_parent_id=body.new_parent_id,
            audit=actor.to_audit_context(),
        )
        projection = _folder_projection(repository, actor.tenant_id, dataset_id, folder_id)
    except Exception as exc:
        _raise_governance(exc)
    return _folder_payload(projection)


@router.post("/folders/{folder_id}/archive")
def archive_folder(
    dataset_id: DatasetId, folder_id: FolderId, actor: ManageActor
) -> dict[str, Any]:
    try:
        repository = _repository()
        repository.archive_folder(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            folder_id=folder_id,
            audit=actor.to_audit_context(),
        )
        projection = _folder_projection(repository, actor.tenant_id, dataset_id, folder_id)
    except Exception as exc:
        _raise_governance(exc)
    return _folder_payload(projection)


@router.get("/tags")
def list_tags(dataset_id: DatasetId, actor: ReadActor) -> dict[str, Any]:
    try:
        rows = _repository().list_tags(actor.tenant_id, dataset_id)
    except Exception as exc:
        _raise_governance(exc)
    return {"items": [_tag_payload(item) for item in rows], "count": len(rows)}


@router.post("/tags", status_code=status.HTTP_201_CREATED)
def create_tag(dataset_id: DatasetId, body: TagCreate, actor: WriteActor) -> dict[str, Any]:
    try:
        repository = _repository()
        tag = repository.create_tag(
            actor.tenant_id,
            dataset_id,
            body.name,
            color=body.color,
            description=body.description,
            audit=actor.to_audit_context(),
        )
        projection = _tag_projection(repository, actor.tenant_id, dataset_id, tag.id)
    except Exception as exc:
        _raise_governance(exc)
    return _tag_payload(projection)


@router.patch("/tags/{tag_id}")
def update_tag(
    dataset_id: DatasetId,
    tag_id: TagId,
    body: TagUpdate,
    actor: WriteActor,
) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_governance_invalid", "message": "至少提供一个修改字段"},
        )
    try:
        repository = _repository()
        repository.update_tag(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            tag_id=tag_id,
            **values,
            audit=actor.to_audit_context(),
        )
        projection = _tag_projection(repository, actor.tenant_id, dataset_id, tag_id)
    except Exception as exc:
        _raise_governance(exc)
    return _tag_payload(projection)


@router.post("/tags/{target_tag_id}/merge")
def merge_tags(
    dataset_id: DatasetId,
    target_tag_id: TagId,
    body: TagMerge,
    actor: ManageActor,
) -> dict[str, Any]:
    try:
        merged = _repository().merge_tags(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            source_tag_id=body.source_tag_id,
            target_tag_id=target_tag_id,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_governance(exc)
    return _tag_payload(merged)


@router.post("/documents/{document_id}/tags/{tag_id}", status_code=status.HTTP_201_CREATED)
def attach_tag(
    dataset_id: DatasetId,
    document_id: DocumentId,
    tag_id: TagId,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        link = _repository().attach_tag(
            actor.tenant_id,
            dataset_id,
            document_id,
            tag_id,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_governance(exc)
    return {
        "attached": True,
        "document_id": link.document_id,
        "tag_id": link.tag_id,
        "created_by": link.created_by,
    }


@router.delete("/documents/{document_id}/tags/{tag_id}")
def detach_tag(
    dataset_id: DatasetId,
    document_id: DocumentId,
    tag_id: TagId,
    actor: WriteActor,
) -> dict[str, Any]:
    try:
        detached = _repository().detach_tag(
            actor.tenant_id,
            dataset_id,
            document_id,
            tag_id,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_governance(exc)
    return {"detached": detached, "document_id": document_id, "tag_id": tag_id}


__all__ = ["router"]
