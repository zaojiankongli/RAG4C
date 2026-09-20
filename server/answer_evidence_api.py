"""Read-only answer facts and evidence refs API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from core import catalog
from core.answer_evidence_facts import AnswerEvidenceRepository
from core.knowledge_permissions import KNOWLEDGE_READ
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/answer-facts",
    tags=["answer-evidence"],
)

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
FactId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
RunId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ReadActor = Annotated[KnowledgeActor, Depends(require_knowledge_permission(KNOWLEDGE_READ))]


def _repo() -> AnswerEvidenceRepository:
    return AnswerEvidenceRepository(catalog.get_engine())


@router.get("")
def list_answer_facts(
    dataset_id: DatasetId,
    actor: ReadActor,
    outcome: Annotated[str | None, Query(max_length=24)] = None,
    run_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    try:
        return _repo().list_answer_facts(
            actor.tenant_id,
            dataset_id=dataset_id,
            outcome=outcome,
            run_id=run_id,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "code": "answer_evidence_error",
                "message": "answer fact query failed",
            },
        ) from exc


@router.get("/by-run/{run_id}")
def get_by_run(run_id: RunId, dataset_id: DatasetId, actor: ReadActor) -> dict[str, Any]:
    try:
        payload = _repo().get_by_run(actor.tenant_id, run_id, dataset_id=dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "code": "answer_evidence_error",
                "message": "answer fact query failed",
            },
        ) from exc
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "answer_evidence_not_found",
                "message": "no answer fact for run_id in dataset scope",
            },
        )
    return payload


@router.get("/{fact_id}")
def get_answer_fact(fact_id: FactId, dataset_id: DatasetId, actor: ReadActor) -> dict[str, Any]:
    try:
        payload = _repo().get_answer_fact(actor.tenant_id, fact_id, dataset_id=dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "code": "answer_evidence_error",
                "message": "answer fact query failed",
            },
        ) from exc
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "answer_evidence_not_found",
                "message": "answer fact does not exist in dataset scope",
            },
        )
    return payload


__all__ = ["router"]
