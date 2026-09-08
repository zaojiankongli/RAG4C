from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.enterprise_knowledge_base_releases import (
    ReleaseManifestConflict,
    ReleaseManifestInvalid,
    ReleaseManifestNotFound,
    _canonical_json,
    _canonical_value,
)
from models.orm import (
    DatasetReleaseEntry,
    DatasetReleaseManifest,
    RetrievalExperiment,
    RetrievalJudgment,
)

QUALITY_SCHEMA_VERSION = 1
_BPS_MAX = 10_000
_SCORE_MILLI_MAX = 3_000


@dataclass(frozen=True)
class QualityEvidenceSummary:
    experiment_count: int
    completed_experiment_count: int
    degraded_experiment_count: int
    query_count: int
    total_result_count: int
    judged_result_count: int
    judgment_count: int
    multi_judged_results: int
    unanimous_results: int
    conflicting_results: int
    judgment_coverage_bps: int | None
    exact_agreement_bps: int | None
    mean_score_milli: int | None


@dataclass(frozen=True)
class QualityPolicyEvaluation:
    verdict: str
    failed_rules: tuple[Mapping[str, Any], ...]
    rule_results: tuple[Mapping[str, Any], ...]
    summary: QualityEvidenceSummary
    evidence_digest: str
    policy_digest: str


def _exact_integer(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ReleaseManifestInvalid(f"{field} must be an exact integer >= {minimum}{suffix}")
    return value


def _exact_boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReleaseManifestInvalid(f"{field} must be an exact boolean")
    return value


def _strict_snapshot_value(value: Any, *, path: str, depth: int = 0) -> Any:
    if depth > 32:
        raise ReleaseManifestInvalid(f"{path} exceeds snapshot depth limit")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ReleaseManifestInvalid(f"{path} contains a non-finite number")
        return value
    if isinstance(value, list):
        return [
            _strict_snapshot_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str) or not key:
                raise ReleaseManifestInvalid(f"{path} contains an invalid key")
            result[key] = _strict_snapshot_value(value[key], path=f"{path}.{key}", depth=depth + 1)
        return result
    raise ReleaseManifestInvalid(f"{path} contains unsupported snapshot value")


def _snapshot_quality_digest(namespace: str, value: Any) -> str:
    normalized = _strict_snapshot_value(value, path=f"snapshot.{namespace}")
    digest = sha256()
    digest.update(b"rag4c:release-quality-snapshot:v1\x00")
    for payload in (
        namespace,
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    ):
        raw = payload.encode("utf-8")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def canonical_quality_digest(namespace: str, value: Any) -> str:
    label = str(namespace or "").strip()
    if not label or len(label) > 64:
        raise ReleaseManifestInvalid("quality digest namespace is invalid")
    normalized = _canonical_value(value, path=f"quality.{label}")
    digest = sha256()
    digest.update(b"rag4c:release-quality:v1\x00")
    for payload in (label, _canonical_json(normalized)):
        raw = payload.encode("utf-8")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _safe_judgment_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    rank = _exact_integer(row.get("result_rank"), "judgment.result_rank", 1)
    label = str(row.get("relevance_label") or "").strip().casefold()
    if label not in {"relevant", "partial", "irrelevant"}:
        raise ReleaseManifestInvalid("judgment relevance_label is invalid")
    score = row.get("score")
    if score is not None:
        score = _exact_integer(score, "judgment.score", 0, 3)
    created_by = str(row.get("created_by") or "").strip()
    if not created_by or len(created_by) > 64:
        raise ReleaseManifestInvalid("judgment created_by is invalid")
    revision = _exact_integer(row.get("revision"), "judgment.revision", 1)
    return {
        "result_rank": rank,
        "relevance_label": label,
        "score": score,
        "created_by": created_by,
        "revision": revision,
    }


def canonicalize_experiment_evidence(
    items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ReleaseManifestInvalid("experiment evidence item must be an object")
        experiment_id = str(raw.get("experiment_id") or "").strip()
        if not experiment_id or len(experiment_id) > 64 or experiment_id in seen:
            raise ReleaseManifestInvalid("experiment evidence id is missing or duplicated")
        seen.add(experiment_id)
        status = str(raw.get("status") or "").strip().casefold()
        if status not in {"completed", "failed"}:
            raise ReleaseManifestInvalid("experiment evidence status is invalid")
        query_hash = str(raw.get("query_hash") or "").strip().casefold()
        if len(query_hash) != 64 or any(
            character not in "0123456789abcdef" for character in query_hash
        ):
            raise ReleaseManifestInvalid("experiment evidence query_hash is invalid")
        generation = _exact_integer(
            raw.get("dataset_serving_generation"),
            "experiment.dataset_serving_generation",
            0,
        )
        result_count = _exact_integer(raw.get("result_count"), "experiment.result_count", 0)
        raw_judgments = raw.get("judgments", ())
        if not isinstance(raw_judgments, (list, tuple)):
            raise ReleaseManifestInvalid("experiment judgments must be an array")
        judgments = sorted(
            (_safe_judgment_projection(item) for item in raw_judgments),
            key=lambda item: (item["result_rank"], item["created_by"], item["revision"]),
        )
        raw_ranks = raw.get("result_ranks", ())
        if not isinstance(raw_ranks, (list, tuple)):
            raise ReleaseManifestInvalid("experiment result_ranks must be an array")
        result_ranks = {_exact_integer(item, "experiment.result_rank", 1) for item in raw_ranks}
        if result_ranks != set(range(1, result_count + 1)):
            raise ReleaseManifestInvalid("experiment result ranks must be contiguous and complete")
        if any(item["result_rank"] not in result_ranks for item in judgments):
            raise ReleaseManifestInvalid("judgment rank is outside experiment results")
        evidence = {
            "experiment_id": experiment_id,
            "experiment_sequence": _exact_integer(
                raw.get("experiment_sequence"), "experiment.sequence", 1
            ),
            "query_hash": query_hash,
            "status": status,
            "dataset_serving_generation": generation,
            "degraded": _exact_boolean(raw.get("degraded"), "experiment.degraded"),
            "result_count": result_count,
            "result_ranks": sorted(result_ranks),
            "strategy_digest": str(raw.get("strategy_digest") or "").strip().casefold(),
            "result_digest": str(raw.get("result_digest") or "").strip().casefold(),
            "lineage_digest": str(raw.get("lineage_digest") or "").strip().casefold(),
            "judgments": judgments,
        }
        for field in ("strategy_digest", "result_digest", "lineage_digest"):
            value = evidence[field]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ReleaseManifestInvalid(f"experiment evidence {field} is invalid")
        evidence["judgment_digest"] = canonical_quality_digest("judgments", judgments)
        normalized.append(evidence)
    return tuple(
        sorted(normalized, key=lambda item: (item["experiment_sequence"], item["experiment_id"]))
    )


def summarize_quality_evidence(
    items: Sequence[Mapping[str, Any]],
) -> QualityEvidenceSummary:
    evidence = canonicalize_experiment_evidence(items)
    total_results = sum(int(item["result_count"]) for item in evidence)
    judged_results = 0
    judgment_count = 0
    multi = 0
    unanimous = 0
    scores: list[int] = []
    for item in evidence:
        by_rank: dict[int, list[Mapping[str, Any]]] = {}
        for judgment in item["judgments"]:
            rank = int(judgment["result_rank"])
            by_rank.setdefault(rank, []).append(judgment)
            judgment_count += 1
            if judgment["score"] is not None:
                scores.append(int(judgment["score"]))
        judged_results += len(by_rank)
        for judgments in by_rank.values():
            if len(judgments) >= 2:
                multi += 1
                if len({str(value["relevance_label"]) for value in judgments}) == 1:
                    unanimous += 1
    coverage = None if total_results == 0 else (judged_results * _BPS_MAX) // total_results
    agreement = None if multi == 0 else (unanimous * _BPS_MAX) // multi
    mean_score = None if not scores else (sum(scores) * 1000) // len(scores)
    return QualityEvidenceSummary(
        experiment_count=len(evidence),
        completed_experiment_count=sum(1 for item in evidence if item["status"] == "completed"),
        degraded_experiment_count=sum(1 for item in evidence if item["degraded"]),
        query_count=len({str(item["query_hash"]) for item in evidence}),
        total_result_count=total_results,
        judged_result_count=judged_results,
        judgment_count=judgment_count,
        multi_judged_results=multi,
        unanimous_results=unanimous,
        conflicting_results=multi - unanimous,
        judgment_coverage_bps=coverage,
        exact_agreement_bps=agreement,
        mean_score_milli=mean_score,
    )


def _policy_projection(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "min_experiment_count": _exact_integer(
            policy.get("min_experiment_count"), "policy.min_experiment_count", 1
        ),
        "min_judged_result_count": _exact_integer(
            policy.get("min_judged_result_count"), "policy.min_judged_result_count", 0
        ),
        "min_judgment_coverage_bps": _exact_integer(
            policy.get("min_judgment_coverage_bps"),
            "policy.min_judgment_coverage_bps",
            0,
            _BPS_MAX,
        ),
        "min_exact_agreement_bps": _exact_integer(
            policy.get("min_exact_agreement_bps"),
            "policy.min_exact_agreement_bps",
            0,
            _BPS_MAX,
        ),
        "min_mean_score_milli": _exact_integer(
            policy.get("min_mean_score_milli"),
            "policy.min_mean_score_milli",
            0,
            _SCORE_MILLI_MAX,
        ),
        "max_conflicting_results": _exact_integer(
            policy.get("max_conflicting_results"), "policy.max_conflicting_results", 0
        ),
        "require_all_experiments_completed": _exact_boolean(
            policy.get("require_all_experiments_completed"),
            "policy.require_all_experiments_completed",
        ),
        "require_no_degraded_results": _exact_boolean(
            policy.get("require_no_degraded_results"),
            "policy.require_no_degraded_results",
        ),
    }


def evaluate_quality_policy(
    policy: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
) -> QualityPolicyEvaluation:
    projected_policy = _policy_projection(policy)
    evidence = canonicalize_experiment_evidence(items)
    summary = summarize_quality_evidence(evidence)
    rules: list[dict[str, Any]] = []

    def rule(code: str, observed: int | None, required: int | bool, passed: bool) -> None:
        rules.append({"code": code, "observed": observed, "required": required, "passed": passed})

    rule(
        "experiment_count",
        summary.experiment_count,
        projected_policy["min_experiment_count"],
        summary.experiment_count >= projected_policy["min_experiment_count"],
    )
    rule(
        "judged_result_count",
        summary.judged_result_count,
        projected_policy["min_judged_result_count"],
        summary.judged_result_count >= projected_policy["min_judged_result_count"],
    )
    rule(
        "judgment_coverage_bps",
        summary.judgment_coverage_bps,
        projected_policy["min_judgment_coverage_bps"],
        summary.judgment_coverage_bps is not None
        and summary.judgment_coverage_bps >= projected_policy["min_judgment_coverage_bps"],
    )
    rule(
        "exact_agreement_bps",
        summary.exact_agreement_bps,
        projected_policy["min_exact_agreement_bps"],
        summary.exact_agreement_bps is not None
        and summary.exact_agreement_bps >= projected_policy["min_exact_agreement_bps"],
    )
    rule(
        "mean_score_milli",
        summary.mean_score_milli,
        projected_policy["min_mean_score_milli"],
        summary.mean_score_milli is not None
        and summary.mean_score_milli >= projected_policy["min_mean_score_milli"],
    )
    rule(
        "conflicting_results",
        summary.conflicting_results,
        projected_policy["max_conflicting_results"],
        summary.conflicting_results <= projected_policy["max_conflicting_results"],
    )
    if projected_policy["require_all_experiments_completed"]:
        rule(
            "all_experiments_completed",
            summary.completed_experiment_count,
            True,
            summary.completed_experiment_count == summary.experiment_count,
        )
    if projected_policy["require_no_degraded_results"]:
        rule(
            "no_degraded_results",
            summary.degraded_experiment_count,
            True,
            summary.degraded_experiment_count == 0,
        )
    failed = tuple(item for item in rules if not item["passed"])
    return QualityPolicyEvaluation(
        verdict="passed" if not failed else "failed",
        failed_rules=failed,
        rule_results=tuple(rules),
        summary=summary,
        evidence_digest=canonical_quality_digest("evidence", evidence),
        policy_digest=canonical_quality_digest("policy", projected_policy),
    )


def collect_release_experiment_evidence(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    experiment_ids: Sequence[str],
    lock_for_update: bool = False,
) -> tuple[dict[str, Any], ...]:
    manifest = session.scalar(
        select(DatasetReleaseManifest).where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
            DatasetReleaseManifest.id == release_id,
        )
    )
    if manifest is None:
        raise ReleaseManifestNotFound("Release does not exist")
    document_entries = {
        row.resource_id: row
        for row in session.scalars(
            select(DatasetReleaseEntry).where(
                DatasetReleaseEntry.tenant_id == tenant_id,
                DatasetReleaseEntry.dataset_id == dataset_id,
                DatasetReleaseEntry.release_id == release_id,
                DatasetReleaseEntry.resource_type == "document_version",
            )
        )
    }
    normalized_ids = [str(value or "").strip() for value in experiment_ids]
    if not normalized_ids or any(not value for value in normalized_ids):
        raise ReleaseManifestInvalid("experiment_ids must be nonempty")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ReleaseManifestInvalid("experiment_ids must be unique")
    experiment_statement = (
        select(RetrievalExperiment)
        .where(
            RetrievalExperiment.tenant_id == tenant_id,
            RetrievalExperiment.dataset_id == dataset_id,
            RetrievalExperiment.id.in_(normalized_ids),
        )
        .order_by(RetrievalExperiment.sequence, RetrievalExperiment.id)
    )
    if lock_for_update:
        experiment_statement = experiment_statement.with_for_update()
    experiments = list(session.scalars(experiment_statement))
    if len(experiments) != len(normalized_ids):
        raise ReleaseManifestConflict("experiment scope is incomplete")
    evidence_items: list[dict[str, Any]] = []
    for experiment in experiments:
        strategy = _strict_snapshot_value(experiment.strategy_snapshot, path="experiment.strategy")
        result = _strict_snapshot_value(experiment.result_snapshot, path="experiment.result")
        lineage = _strict_snapshot_value(experiment.evidence_lineage, path="experiment.lineage")
        if not all(isinstance(value, Mapping) for value in (strategy, result, lineage)):
            raise ReleaseManifestConflict("experiment snapshot authority is malformed")
        generations = {
            _exact_integer(
                value.get("dataset_serving_generation"),
                "experiment.dataset_serving_generation",
                0,
            )
            for value in (strategy, result, lineage)
        }
        if generations != {int(manifest.serving_generation)}:
            raise ReleaseManifestConflict("experiment serving generation does not match Release")
        results = result.get("results")
        if not isinstance(results, list):
            raise ReleaseManifestConflict("experiment result authority is malformed")
        ranks: list[int] = []
        for raw_result in results:
            if not isinstance(raw_result, Mapping):
                raise ReleaseManifestConflict("experiment result authority is malformed")
            rank = _exact_integer(raw_result.get("rank"), "experiment.result.rank", 1)
            document_id = str(raw_result.get("document_id") or "").strip()
            entry = document_entries.get(document_id)
            if entry is None:
                raise ReleaseManifestConflict("experiment result is outside Release Manifest")
            facts = entry.safe_facts_json or {}
            if not isinstance(facts, Mapping):
                raise ReleaseManifestConflict("Release entry authority is malformed")
            try:
                document_revision = _exact_integer(
                    raw_result.get("document_revision"), "experiment.document_revision", 1
                )
                content_revision = _exact_integer(
                    facts.get("content_revision"), "release_entry.content_revision", 1
                )
            except ReleaseManifestInvalid as exc:
                raise ReleaseManifestConflict(
                    "experiment document revision does not match Release"
                ) from exc
            if document_revision != content_revision:
                raise ReleaseManifestConflict("experiment document revision does not match Release")
            ranks.append(rank)
        judgment_statement = (
            select(RetrievalJudgment)
            .where(
                RetrievalJudgment.tenant_id == tenant_id,
                RetrievalJudgment.dataset_id == dataset_id,
                RetrievalJudgment.experiment_id == experiment.id,
            )
            .order_by(
                RetrievalJudgment.result_rank,
                RetrievalJudgment.created_by,
                RetrievalJudgment.id,
            )
        )
        if lock_for_update:
            judgment_statement = judgment_statement.with_for_update()
        judgments = list(session.scalars(judgment_statement))
        evidence_items.append(
            {
                "experiment_id": experiment.id,
                "experiment_sequence": int(experiment.sequence),
                "query_hash": experiment.query_hash,
                "status": experiment.status,
                "dataset_serving_generation": int(manifest.serving_generation),
                "degraded": _exact_boolean(result.get("degraded"), "experiment.result.degraded"),
                "result_count": len(results),
                "result_ranks": ranks,
                "strategy_digest": _snapshot_quality_digest("experiment.strategy", strategy),
                "result_digest": _snapshot_quality_digest("experiment.result", result),
                "lineage_digest": _snapshot_quality_digest("experiment.lineage", lineage),
                "judgments": [
                    {
                        "result_rank": int(row.result_rank),
                        "relevance_label": row.relevance_label,
                        "score": row.score,
                        "created_by": row.created_by,
                        "revision": int(row.revision),
                    }
                    for row in judgments
                ],
            }
        )
    return canonicalize_experiment_evidence(evidence_items)


__all__ = [
    "QUALITY_SCHEMA_VERSION",
    "QualityEvidenceSummary",
    "QualityPolicyEvaluation",
    "canonical_quality_digest",
    "canonicalize_experiment_evidence",
    "collect_release_experiment_evidence",
    "evaluate_quality_policy",
    "summarize_quality_evidence",
]
