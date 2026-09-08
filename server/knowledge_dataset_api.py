"""Authenticated KnowledgeOps dataset profile APIs."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from core import catalog
from core.knowledge_datasets import (
    DatasetProfile,
    DatasetProfileConflict,
    DatasetProfileNotFound,
    KnowledgeDatasetRepository,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission, resolve_path_dataset

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-datasets"])

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]

_LIST_READ = require_knowledge_permission(KNOWLEDGE_READ)
_DATASET_READ = require_knowledge_permission(KNOWLEDGE_READ, resolve_path_dataset("dataset_id"))
_DATASET_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE, resolve_path_dataset("dataset_id"))
# Archived datasets must remain restorable. The shared auth state gate only permits
# active mutations, so restore checks tenant-level manage permission here and lets
# the repository enforce dataset scope plus the archived -> active CAS transition.
_RESTORE_MANAGE = require_knowledge_permission(KNOWLEDGE_MANAGE)
ListReadActor = Annotated[KnowledgeActor, Depends(_LIST_READ)]
DatasetReadActor = Annotated[KnowledgeActor, Depends(_DATASET_READ)]
DatasetManageActor = Annotated[KnowledgeActor, Depends(_DATASET_MANAGE)]
RestoreManageActor = Annotated[KnowledgeActor, Depends(_RESTORE_MANAGE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatasetPoliciesPatch(StrictModel):
    parser: dict[str, Any] | None = None
    chunk: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    retention: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class DatasetProfilePatch(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    owner_id: str | None = Field(default=None, min_length=1, max_length=64)
    visibility: Literal["private", "tenant", "public"] | None = None
    profile: dict[str, Any] | None = None
    policies: DatasetPoliciesPatch | None = None
    default_language: str | None = Field(default=None, min_length=1, max_length=32)
    graph_enabled: bool | None = Field(default=None, strict=True)
    qa_enabled: bool | None = Field(default=None, strict=True)


class DatasetRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


def _repository() -> KnowledgeDatasetRepository:
    return KnowledgeDatasetRepository(catalog.get_engine())


def _raise_dataset(exc: Exception) -> None:
    if isinstance(exc, DatasetProfileNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_resource_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, DatasetProfileConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_dataset_conflict", "message": str(exc)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_dataset_invalid", "message": str(exc)},
        ) from exc
    raise exc


def _profile_payload(profile: DatasetProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "tenant_id": profile.tenant_id,
        "name": profile.name,
        "description": profile.description,
        "status": profile.status,
        "profile_revision": profile.profile_revision,
        "owner_id": profile.owner_id,
        "visibility": profile.visibility,
        "profile": profile.profile_json,
        "policies": {
            "parser": profile.parser_policy,
            "chunk": profile.chunk_policy,
            "retrieval": profile.retrieval_policy,
            "retention": profile.retention_policy,
            "metadata": profile.metadata_policy,
        },
        "default_language": profile.default_language,
        "graph_enabled": profile.graph_enabled,
        "qa_enabled": profile.qa_enabled,
        "usage": {"documents": profile.doc_count, "chunks": profile.chunk_count},
        "timestamps": {
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            "archived_at": profile.archived_at,
            "archived_by": profile.archived_by,
        },
    }


@router.get("")
def list_profiles(actor: ListReadActor) -> dict[str, Any]:
    try:
        rows = [
            profile
            for profile in _repository().list_profiles(actor.tenant_id)
            if profile.status in {"active", "archived"}
        ]
    except Exception as exc:
        _raise_dataset(exc)
    return {"items": [_profile_payload(profile) for profile in rows], "count": len(rows)}


@router.get("/{dataset_id}")
def get_profile(dataset_id: DatasetId, actor: DatasetReadActor) -> dict[str, Any]:
    try:
        profile = _repository().get_profile(actor.tenant_id, dataset_id)
    except Exception as exc:
        _raise_dataset(exc)
    return _profile_payload(profile)


@router.patch("/{dataset_id}")
def update_profile(
    dataset_id: DatasetId, body: DatasetProfilePatch, actor: DatasetManageActor
) -> dict[str, Any]:
    nullable_only = {"owner_id"}
    explicit_nulls = {
        field
        for field in body.model_fields_set
        if field not in nullable_only and getattr(body, field) is None
    }
    if explicit_nulls:
        field = sorted(explicit_nulls)[0]
        raise HTTPException(
            status_code=422,
            detail={
                "code": "knowledge_dataset_invalid",
                "message": f"{field} 不得为 null",
            },
        )
    values = body.model_dump(exclude_unset=True)
    expected_revision = values.pop("expected_revision")
    policies = values.pop("policies", None)
    if "profile" in values:
        values["profile_json"] = values.pop("profile")
    if policies is not None:
        for source, target in {
            "parser": "parser_policy",
            "chunk": "chunk_policy",
            "retrieval": "retrieval_policy",
            "retention": "retention_policy",
            "metadata": "metadata_policy",
        }.items():
            if source in policies:
                values[target] = policies[source]
    if not values:
        raise HTTPException(
            status_code=422,
            detail={"code": "knowledge_dataset_invalid", "message": "至少提供一个修改字段"},
        )
    try:
        profile = _repository().update_profile(
            actor.tenant_id,
            dataset_id,
            expected_revision=expected_revision,
            audit=actor.to_audit_context(),
            **values,
        )
    except Exception as exc:
        _raise_dataset(exc)
    return _profile_payload(profile)


@router.post("/{dataset_id}/archive")
def archive_profile(
    dataset_id: DatasetId, body: DatasetRevisionRequest, actor: DatasetManageActor
) -> dict[str, Any]:
    try:
        profile = _repository().archive(
            actor.tenant_id,
            dataset_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_dataset(exc)
    return _profile_payload(profile)


@router.post("/{dataset_id}/restore")
def restore_profile(
    dataset_id: DatasetId, body: DatasetRevisionRequest, actor: RestoreManageActor
) -> dict[str, Any]:
    try:
        profile = _repository().restore(
            actor.tenant_id,
            dataset_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_dataset(exc)
    return _profile_payload(profile)


@router.post("/{dataset_id}/disable")
def disable_profile(
    dataset_id: DatasetId, body: DatasetRevisionRequest, actor: DatasetManageActor
) -> dict[str, Any]:
    try:
        profile = _repository().disable(
            actor.tenant_id,
            dataset_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_dataset(exc)
    return _profile_payload(profile)


__all__ = ["router"]
