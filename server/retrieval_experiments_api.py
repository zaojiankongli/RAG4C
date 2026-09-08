"""Authenticated, tenant-scoped Retrieval Experiments API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, NoReturn
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.security import HTTPBearer
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core import catalog
from core.knowledge_permissions import KNOWLEDGE_READ, KNOWLEDGE_WRITE
from core.retrieval_experiment_runner import (
    RetrievalExperimentRunner,
    RetrievalExperimentVariant,
)
from core.retrieval_experiments import (
    JudgmentAgreementSummary,
    RetrievalExperimentConflict,
    RetrievalExperimentDatasetInactive,
    RetrievalExperimentNotFound,
    RetrievalExperimentRepository,
    RetrievalExperimentUnavailable,
)
from models.orm import Dataset, RetrievalExperiment, RetrievalJudgment
from server.knowledge_auth import KnowledgeActor, require_knowledge_permission

_OPENAPI_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="KnowledgeBearerAuth",
    description="Actor-bound signed KnowledgeOps bearer token.",
)


def _document_tenant_header(
    tenant_id: Annotated[
        str | None,
        Header(
            alias="X-RAG4C-Tenant",
            min_length=1,
            max_length=64,
            description="Signed tenant assertion; required for remote requests.",
        ),
    ] = None,
) -> None:
    del tenant_id


router = APIRouter(
    prefix="/api/knowledge-bases/{dataset_id}/retrieval-experiments",
    tags=["knowledge-retrieval-experiments"],
    dependencies=[Depends(_OPENAPI_BEARER), Depends(_document_tenant_header)],
)

_SAFE_ID = r"^[^\x00-\x1f\x7f]+$"
_SHA256 = r"^[0-9a-fA-F]{64}$"
DatasetId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
ExperimentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]
JudgmentId = Annotated[str, Path(min_length=1, max_length=64, pattern=_SAFE_ID)]

# Deliberately authorize tenant permission first and let the repository enforce
# dataset scope. That keeps foreign-tenant and foreign-dataset identifiers at 404
# instead of revealing their existence through the authorization layer.
_READ = require_knowledge_permission(KNOWLEDGE_READ)
_WRITE = require_knowledge_permission(KNOWLEDGE_WRITE)
ReadActor = Annotated[KnowledgeActor, Depends(_READ)]
WriteActor = Annotated[KnowledgeActor, Depends(_WRITE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExperimentStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class RouteTarget(StrEnum):
    AUTO = "auto"
    HYBRID = "hybrid"
    VECTOR_GRAPH_RAG = "vector_graph_rag"
    FULL = "full"


class SourceDiversity(StrEnum):
    OFF = "off"
    GROUP_ONLY = "group_only"
    GROUP_MMR = "group_mmr"


class RelevanceLabel(StrEnum):
    RELEVANT = "relevant"
    PARTIAL = "partial"
    IRRELEVANT = "irrelevant"


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorEnvelope(StrictModel):
    error: ErrorDetail


class ExperimentCreate(StrictModel):
    query: str = Field(min_length=1, max_length=20_000)
    strategy_snapshot: dict[str, JsonValue]
    result_snapshot: dict[str, JsonValue]
    evidence_lineage: dict[str, JsonValue]
    latency_ms: int = Field(ge=0)
    status: ExperimentStatus = Field(strict=False)
    run_id: str | None = Field(default=None, min_length=1, max_length=64)


_ACL_SAFE = re.compile(r"^[^\"'\\\x00-\x1f\x7f]+$")


class ExperimentRunVariant(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    route_target: RouteTarget = Field(strict=False)
    top_k: int = Field(ge=1, le=50)
    hybrid_search_on: bool
    rerank_on: bool
    graph_retrieval_on: bool
    sentence_window_on: bool
    source_diversity: SourceDiversity = Field(strict=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("variant name is required")
        return name


class ExperimentRunCreate(StrictModel):
    query: str = Field(min_length=1, max_length=20_000)
    acl: list[str] = Field(default_factory=list, max_length=100)
    variants: list[ExperimentRunVariant] = Field(min_length=1, max_length=4)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query is required")
        return query

    @field_validator("acl")
    @classmethod
    def validate_acl(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 256 or _ACL_SAFE.fullmatch(item) is None:
                raise ValueError("acl entries must be safe strings")
            normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_unique_names(self) -> ExperimentRunCreate:
        names = [variant.name.casefold() for variant in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        return self


class JudgmentCreate(StrictModel):
    result_rank: int = Field(ge=1)
    relevance_label: RelevanceLabel = Field(strict=False)
    document_id: str | None = Field(default=None, min_length=1, max_length=64)
    chunk_id: str | None = Field(default=None, min_length=1, max_length=512)
    score: int | None = Field(default=None, ge=0, le=3)
    note: str = Field(default="", max_length=20_000)


def _judgment_patch_schema(schema: dict[str, Any]) -> None:
    schema["anyOf"] = [
        {"required": ["relevance_label"]},
        {"required": ["score"]},
        {"required": ["note"]},
    ]
    properties = schema.get("properties", {})
    for name in ("relevance_label", "note", "score"):
        property_schema = properties.get(name)
        if isinstance(property_schema, dict):
            property_schema.pop("default", None)
    for name in ("relevance_label", "note"):
        property_schema = properties.get(name)
        if not isinstance(property_schema, dict):
            continue
        variants = property_schema.get("anyOf")
        if isinstance(variants, list):
            non_null = [item for item in variants if item.get("type") != "null"]
            if len(non_null) == 1:
                title = property_schema.get("title")
                property_schema.clear()
                property_schema.update(non_null[0])
                if title is not None:
                    property_schema["title"] = title


class JudgmentPatch(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_judgment_patch_schema)

    expected_revision: int = Field(ge=1)
    relevance_label: RelevanceLabel | None = Field(default=None, strict=False)
    score: int | None = Field(default=None, ge=0, le=3)
    note: str | None = Field(default=None, max_length=20_000)

    @field_validator("relevance_label")
    @classmethod
    def validate_relevance_label(cls, value: RelevanceLabel | None) -> RelevanceLabel | None:
        if value is None:
            raise PydanticCustomError(
                "judgment_patch_relevance_null",
                "relevance_label cannot be null",
            )
        return value

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is None:
            raise PydanticCustomError(
                "judgment_patch_note_null",
                "note cannot be null",
            )
        return value

    @model_validator(mode="after")
    def validate_mutation(self) -> JudgmentPatch:
        changed = self.model_fields_set & {"relevance_label", "score", "note"}
        if not changed:
            raise PydanticCustomError(
                "judgment_patch_mutation_required",
                "one of relevance_label, score, or note is required",
            )
        return self


class JudgmentResponse(StrictModel):
    id: str
    tenant_id: str
    dataset_id: str
    experiment_id: str
    result_rank: int
    document_id: str | None
    chunk_id: str | None
    relevance_label: RelevanceLabel
    score: int | None
    note: str
    revision: int
    created_by: str
    created_at: datetime


class ExperimentResponse(StrictModel):
    sequence: int
    id: str
    tenant_id: str
    dataset_id: str
    query: str
    query_hash: str
    strategy_snapshot: dict[str, JsonValue]
    result_snapshot: dict[str, JsonValue]
    evidence_lineage: dict[str, JsonValue]
    latency_ms: int
    status: ExperimentStatus
    created_by: str
    created_at: datetime
    run_id: str | None


class ExperimentDetailResponse(ExperimentResponse):
    judgments: list[JudgmentResponse]


class ExperimentRunVariantResponse(ExperimentResponse):
    name: str
    route: RouteTarget
    result_count: int
    reranked: bool
    degraded: bool


class ExperimentRunResponse(StrictModel):
    run_id: str
    dataset_serving_generation: int
    items: list[ExperimentRunVariantResponse]


class ExperimentListResponse(StrictModel):
    items: list[ExperimentResponse]
    next_before_sequence: int | None


class AgreementResponse(StrictModel):
    experiment_id: str
    judged_results: int
    judgment_count: int
    multi_judged_results: int
    unanimous_results: int
    conflicting_results: int
    exact_agreement_rate: float | None
    label_counts: dict[RelevanceLabel, int]
    mean_score: float | None


def _error_responses(*codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        400: "The required tenant assertion header is missing or invalid.",
        401: "Missing or invalid actor bearer token.",
        403: "Actor lacks the required KnowledgeOps permission.",
        404: "The resource does not exist in the signed tenant and dataset scope.",
        409: "The immutable identity or judgment revision conflicts with current authority.",
        422: "Request validation failed.",
        503: "Retrieval experiment authority is unavailable.",
    }
    return {code: {"model": ErrorEnvelope, "description": descriptions[code]} for code in codes}


def _repository(
    request: Request, *, writable: bool = False
) -> RetrievalExperimentRepository:
    if writable:
        engine = getattr(request.app.state, "retrieval_experiment_mutation_engine", None)
        if engine is None:
            provider = getattr(
                request.app.state, "retrieval_experiment_mutation_engine_provider", None
            )
            engine = provider() if callable(provider) else None
    else:
        engine = getattr(request.app.state, "knowledge_auth_engine", None)
    return RetrievalExperimentRepository(engine if engine is not None else catalog.get_engine())


def _raise_retrieval(exc: Exception) -> NoReturn:
    if isinstance(exc, RetrievalExperimentNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "retrieval_experiment_not_found", "message": "资源不存在"},
        ) from exc
    if isinstance(exc, RetrievalExperimentDatasetInactive):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "knowledge_dataset_inactive",
                "message": "知识库当前状态不允许该操作",
            },
        ) from exc
    if isinstance(exc, RetrievalExperimentConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "retrieval_experiment_conflict", "message": "检索实验状态冲突"},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "retrieval_experiment_invalid", "message": "检索实验请求无效"},
        ) from exc
    if isinstance(exc, (RetrievalExperimentUnavailable, SQLAlchemyError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "retrieval_experiment_unavailable",
                "message": "检索实验服务暂不可用",
            },
        ) from exc
    raise exc


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _experiment_payload(row: RetrievalExperiment) -> dict[str, Any]:
    return {
        "sequence": row.sequence,
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "query": row.query,
        "query_hash": row.query_hash,
        "strategy_snapshot": row.strategy_snapshot,
        "result_snapshot": row.result_snapshot,
        "evidence_lineage": row.evidence_lineage,
        "latency_ms": row.latency_ms,
        "status": ExperimentStatus(row.status),
        "created_by": row.created_by,
        "created_at": _utc_datetime(row.created_at),
        "run_id": row.run_id,
    }


def _run_variant_payload(item: Any) -> dict[str, Any]:
    return {
        **_experiment_payload(item.experiment),
        "name": item.name,
        "route": RouteTarget(item.route),
        "result_count": item.result_count,
        "reranked": item.reranked,
        "degraded": item.degraded,
    }


def _base_retrieval(request: Request) -> Any:
    retrieval = getattr(
        request.app.state, "retrieval_experiment_retrieval", None
    )
    if retrieval is None:
        raise RetrievalExperimentUnavailable(
            "dedicated experiment retrieval pipeline is unavailable"
        )
    required = (
        "embedder",
        "milvus",
        "reranker",
        "rewriter",
        "router",
        "settings",
        "graph_retriever",
        "hyde",
        "subqueries",
        "stepback",
        "sentence_window",
        "reranker_cb",
        "serving_guard",
    )
    if any(not hasattr(retrieval, name) for name in required):
        raise RetrievalExperimentUnavailable(
            "dedicated experiment retrieval pipeline is unavailable"
        )
    return retrieval


def _judgment_payload(row: RetrievalJudgment) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "experiment_id": row.experiment_id,
        "result_rank": row.result_rank,
        "document_id": row.document_id,
        "chunk_id": row.chunk_id,
        "relevance_label": RelevanceLabel(row.relevance_label),
        "score": row.score,
        "note": row.note,
        "revision": row.revision,
        "created_by": row.created_by,
        "created_at": _utc_datetime(row.created_at),
    }


def _agreement_payload(summary: JudgmentAgreementSummary) -> dict[str, Any]:
    return {
        "experiment_id": summary.experiment_id,
        "judged_results": summary.judged_results,
        "judgment_count": summary.judgment_count,
        "multi_judged_results": summary.multi_judged_results,
        "unanimous_results": summary.unanimous_results,
        "conflicting_results": summary.conflicting_results,
        "exact_agreement_rate": summary.exact_agreement_rate,
        "label_counts": {
            RelevanceLabel(label): count for label, count in summary.label_counts.items()
        },
        "mean_score": summary.mean_score,
    }


def _require_dataset_state(
    repository: RetrievalExperimentRepository,
    tenant_id: str,
    dataset_id: str,
    *,
    writable: bool,
) -> None:
    with Session(repository.engine) as session:
        dataset_status = session.scalar(
            select(Dataset.status).where(
                Dataset.tenant_id == tenant_id,
                Dataset.id == dataset_id,
            )
        )
    if dataset_status is None:
        raise RetrievalExperimentNotFound("dataset does not exist in tenant scope")
    allowed_states = {"active"} if writable else {"active", "archived"}
    if str(dataset_status).strip().casefold() not in allowed_states:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "knowledge_dataset_inactive",
                "message": "知识库当前状态不允许该操作",
            },
        )


def _judgments(
    repository: RetrievalExperimentRepository,
    tenant_id: str,
    dataset_id: str,
    experiment_id: str,
) -> list[RetrievalJudgment]:
    with Session(repository.engine, expire_on_commit=False) as session:
        return list(
            session.scalars(
                select(RetrievalJudgment)
                .where(
                    RetrievalJudgment.tenant_id == tenant_id,
                    RetrievalJudgment.dataset_id == dataset_id,
                    RetrievalJudgment.experiment_id == experiment_id,
                )
                .order_by(
                    RetrievalJudgment.result_rank,
                    RetrievalJudgment.created_at,
                    RetrievalJudgment.id,
                )
            )
        )


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentDetailResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def create_experiment(
    dataset_id: DatasetId,
    body: ExperimentCreate,
    actor: WriteActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request, writable=True)
    try:
        row = repository.create_experiment(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            query=body.query,
            strategy_snapshot=body.strategy_snapshot,
            result_snapshot=body.result_snapshot,
            evidence_lineage=body.evidence_lineage,
            latency_ms=body.latency_ms,
            status=body.status.value,
            run_id=body.run_id,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_retrieval(exc)
    return {**_experiment_payload(row), "judgments": []}


@router.post(
    "/run",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentRunResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def run_experiments(
    dataset_id: DatasetId,
    body: ExperimentRunCreate,
    actor: WriteActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request, writable=True)
    try:
        runner = RetrievalExperimentRunner(repository, _base_retrieval(request))
        outcome = runner.run(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            query=body.query,
            acl=body.acl,
            variants=[
                RetrievalExperimentVariant(
                    name=variant.name,
                    route_target=variant.route_target.value,
                    top_k=variant.top_k,
                    hybrid_search_on=variant.hybrid_search_on,
                    rerank_on=variant.rerank_on,
                    graph_retrieval_on=variant.graph_retrieval_on,
                    sentence_window_on=variant.sentence_window_on,
                    source_diversity=variant.source_diversity.value,
                )
                for variant in body.variants
            ],
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_retrieval(exc)
    return {
        "run_id": outcome.run_id,
        "dataset_serving_generation": outcome.dataset_serving_generation,
        "items": [_run_variant_payload(item) for item in outcome.items],
    }


@router.get(
    "",
    response_model=ExperimentListResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def list_experiments(
    dataset_id: DatasetId,
    actor: ReadActor,
    request: Request,
    experiment_status: Annotated[ExperimentStatus | None, Query(alias="status")] = None,
    query_hash: Annotated[str | None, Query(pattern=_SHA256)] = None,
    run_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    created_by: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    before_sequence: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    normalized_from = _naive_utc(created_from)
    normalized_to = _naive_utc(created_to)
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_from > normalized_to
    ):
        _raise_retrieval(ValueError("created_from must not be after created_to"))
    repository = _repository(request)
    try:
        _require_dataset_state(repository, actor.tenant_id, dataset_id, writable=False)
        rows = repository.list_experiments(
            actor.tenant_id,
            dataset_id,
            status=None if experiment_status is None else experiment_status.value,
            query_hash=query_hash,
            run_id=run_id,
            created_by=created_by,
            created_from=normalized_from,
            created_to=normalized_to,
            before_sequence=before_sequence,
            limit=limit + 1,
        )
    except Exception as exc:
        _raise_retrieval(exc)
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "items": [_experiment_payload(row) for row in page],
        "next_before_sequence": page[-1].sequence if has_more and page else None,
    }


@router.get(
    "/{experiment_id}",
    response_model=ExperimentDetailResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def get_experiment(
    dataset_id: DatasetId,
    experiment_id: ExperimentId,
    actor: ReadActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request)
    try:
        _require_dataset_state(repository, actor.tenant_id, dataset_id, writable=False)
        row = repository.get_experiment(actor.tenant_id, dataset_id, experiment_id)
        judgments = _judgments(repository, actor.tenant_id, dataset_id, experiment_id)
    except Exception as exc:
        _raise_retrieval(exc)
    return {
        **_experiment_payload(row),
        "judgments": [_judgment_payload(judgment) for judgment in judgments],
    }


@router.post(
    "/{experiment_id}/judgments",
    status_code=status.HTTP_201_CREATED,
    response_model=JudgmentResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def add_judgment(
    dataset_id: DatasetId,
    experiment_id: ExperimentId,
    body: JudgmentCreate,
    actor: WriteActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request, writable=True)
    try:
        row = repository.add_judgment(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
            result_rank=body.result_rank,
            document_id=body.document_id,
            chunk_id=body.chunk_id,
            relevance_label=body.relevance_label.value,
            score=body.score,
            note=body.note,
            audit=actor.to_audit_context(),
        )
    except Exception as exc:
        _raise_retrieval(exc)
    return _judgment_payload(row)


@router.patch(
    "/{experiment_id}/judgments/{judgment_id}",
    response_model=JudgmentResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def update_judgment(
    dataset_id: DatasetId,
    experiment_id: ExperimentId,
    judgment_id: JudgmentId,
    body: JudgmentPatch,
    actor: WriteActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request, writable=True)
    try:
        values: dict[str, Any] = {}
        if "relevance_label" in body.model_fields_set:
            values["relevance_label"] = body.relevance_label.value
        if "score" in body.model_fields_set:
            values["score"] = body.score
        if "note" in body.model_fields_set:
            values["note"] = body.note
        row = repository.update_judgment_partial(
            tenant_id=actor.tenant_id,
            dataset_id=dataset_id,
            experiment_id=experiment_id,
            judgment_id=judgment_id,
            expected_revision=body.expected_revision,
            audit=actor.to_audit_context(),
            **values,
        )
    except Exception as exc:
        _raise_retrieval(exc)
    return _judgment_payload(row)


@router.get(
    "/{experiment_id}/agreement",
    response_model=AgreementResponse,
    responses=_error_responses(400, 401, 403, 404, 409, 422, 503),
)
def get_agreement(
    dataset_id: DatasetId,
    experiment_id: ExperimentId,
    actor: ReadActor,
    request: Request,
) -> dict[str, Any]:
    repository = _repository(request)
    try:
        _require_dataset_state(repository, actor.tenant_id, dataset_id, writable=False)
        summary = repository.summarize_agreement(
            actor.tenant_id,
            dataset_id,
            experiment_id,
        )
    except Exception as exc:
        _raise_retrieval(exc)
    return _agreement_payload(summary)


__all__ = ["router"]
