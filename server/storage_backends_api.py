"""Tenant-scoped storage backend registry API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field

from core import catalog
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from core.storage_backends import (
    StorageBackendConflict,
    StorageBackendInvalid,
    StorageBackendNotFound,
    StorageBackendRepository,
    test_storage_config,
)
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

router = APIRouter(prefix="/api/storage-backends", tags=["storage-backends"])

ReadActor = Annotated[KnowledgeActor, Depends(require_knowledge_permission(KNOWLEDGE_READ))]
ManageActor = Annotated[
    KnowledgeActor, Depends(require_knowledge_permission(KNOWLEDGE_MANAGE))
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StorageConfigModel(StrictModel):
    root_path: str | None = None
    endpoint: str | None = None
    bucket: str | None = None
    bucket_name: str | None = None
    region: str | None = None
    path_prefix: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    use_ssl: bool | None = None
    force_path_style: bool | None = None


class StorageCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=32)
    config: StorageConfigModel = Field(default_factory=StorageConfigModel)


class StorageUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")
    config: StorageConfigModel | None = None


class StorageTestRequest(StrictModel):
    provider: str = Field(min_length=1, max_length=32)
    config: StorageConfigModel = Field(default_factory=StorageConfigModel)


class DatasetBindRequest(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    storage_backend_id: str | None = Field(default=None, max_length=64)


def _repo() -> StorageBackendRepository:
    return StorageBackendRepository(catalog.get_engine())


def _cfg(body: StorageConfigModel | None) -> dict[str, Any]:
    if body is None:
        return {}
    data = body.model_dump(exclude_none=True)
    return data


def _raise(exc: Exception) -> None:
    if isinstance(exc, StorageBackendNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "storage_backend_not_found", "message": str(exc)},
        ) from exc
    if isinstance(exc, StorageBackendConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "storage_backend_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, StorageBackendInvalid):
        raise HTTPException(
            status_code=422,
            detail={"code": "storage_backend_invalid", "message": str(exc)},
        ) from exc
    raise HTTPException(
        status_code=500,
        detail={
            "code": "storage_backend_error",
            "message": "storage backend operation failed",
        },
    ) from exc


@router.get("/types")
def list_types(actor: ReadActor) -> dict[str, Any]:
    return _repo().list_types()


@router.get("")
def list_backends(actor: ReadActor) -> dict[str, Any]:
    try:
        return _repo().list_backends(actor.tenant_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/{backend_id}")
def get_backend(backend_id: str, actor: ReadActor) -> dict[str, Any]:
    try:
        return _repo().get_backend(actor.tenant_id, backend_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("", status_code=201)
def create_backend(body: StorageCreate, actor: ManageActor) -> dict[str, Any]:
    try:
        return _repo().create_backend(
            actor.tenant_id,
            name=body.name,
            provider=body.provider,
            config=_cfg(body.config),
            actor_id=getattr(actor, "account_id", None) or getattr(actor, "actor_id", "") or "",
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.patch("/{backend_id}")
def update_backend(
    backend_id: str, body: StorageUpdate, actor: ManageActor
) -> dict[str, Any]:
    try:
        return _repo().update_backend(
            actor.tenant_id,
            backend_id,
            name=body.name,
            status=body.status,
            config=_cfg(body.config) if body.config is not None else None,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.delete("/{backend_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_backend(backend_id: str, actor: ManageActor) -> Response:
    try:
        _repo().delete_backend(actor.tenant_id, backend_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post("/test")
def test_unsaved(body: StorageTestRequest, actor: ManageActor) -> dict[str, Any]:
    return test_storage_config(body.provider, _cfg(body.config))


@router.post("/{backend_id}/test")
def test_saved(backend_id: str, actor: ManageActor) -> dict[str, Any]:
    try:
        return _repo().test_saved(actor.tenant_id, backend_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/{backend_id}/default")
def set_default(backend_id: str, actor: ManageActor) -> dict[str, Any]:
    try:
        return _repo().set_default(actor.tenant_id, backend_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.put("/bind-dataset")
def bind_dataset(body: DatasetBindRequest, actor: ManageActor) -> dict[str, Any]:
    try:
        return _repo().bind_dataset(
            actor.tenant_id,
            dataset_id=body.dataset_id,
            storage_backend_id=body.storage_backend_id,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


__all__ = ["router"]
