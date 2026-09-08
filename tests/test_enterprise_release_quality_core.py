from __future__ import annotations

import subprocess
import sys

import pytest

from core.enterprise_knowledge_base_releases import ReleaseManifestInvalid
from core.enterprise_release_quality import (
    canonical_quality_digest,
    canonicalize_experiment_evidence,
    evaluate_quality_policy,
    summarize_quality_evidence,
)


def item(
    experiment_id: str,
    *,
    sequence: int,
    status: str = "completed",
    degraded: bool = False,
    judgments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "experiment_sequence": sequence,
        "query_hash": ("a" if sequence == 1 else "b") * 64,
        "status": status,
        "dataset_serving_generation": 3,
        "degraded": degraded,
        "result_count": 2,
        "result_ranks": [1, 2],
        "strategy_digest": "c" * 64,
        "result_digest": "d" * 64,
        "lineage_digest": "e" * 64,
        "judgments": judgments
        if judgments is not None
        else [
            {
                "result_rank": 1,
                "relevance_label": "relevant",
                "score": 3,
                "created_by": "judge-a",
                "revision": 1,
                "note": "must not persist",
            },
            {
                "result_rank": 1,
                "relevance_label": "relevant",
                "score": 2,
                "created_by": "judge-b",
                "revision": 2,
            },
            {
                "result_rank": 2,
                "relevance_label": "partial",
                "score": 1,
                "created_by": "judge-a",
                "revision": 1,
            },
        ],
    }


def policy(**overrides: object) -> dict[str, object]:
    return {
        "min_experiment_count": 1,
        "min_judged_result_count": 2,
        "min_judgment_coverage_bps": 10_000,
        "min_exact_agreement_bps": 10_000,
        "min_mean_score_milli": 2_000,
        "max_conflicting_results": 0,
        "require_all_experiments_completed": True,
        "require_no_degraded_results": True,
        **overrides,
    }


def test_quality_digest_and_evidence_order_are_deterministic_and_secret_safe() -> None:
    first = canonicalize_experiment_evidence([item("exp-b", sequence=2), item("exp-a", sequence=1)])
    second = canonicalize_experiment_evidence(
        [item("exp-a", sequence=1), item("exp-b", sequence=2)]
    )
    assert first == second
    assert [row["experiment_id"] for row in first] == ["exp-a", "exp-b"]
    rendered = repr(first)
    assert "must not persist" not in rendered
    assert "judgment_digest" in rendered
    assert canonical_quality_digest("baseline", first) == canonical_quality_digest(
        "baseline", second
    )


def test_quality_summary_uses_integer_bps_and_milli_score() -> None:
    summary = summarize_quality_evidence([item("exp-a", sequence=1)])
    assert summary.experiment_count == 1
    assert summary.completed_experiment_count == 1
    assert summary.query_count == 1
    assert summary.total_result_count == 2
    assert summary.judged_result_count == 2
    assert summary.judgment_count == 3
    assert summary.multi_judged_results == 1
    assert summary.unanimous_results == 1
    assert summary.conflicting_results == 0
    assert summary.judgment_coverage_bps == 10_000
    assert summary.exact_agreement_bps == 10_000
    assert summary.mean_score_milli == 2_000


def test_policy_evaluation_returns_explicit_pass_and_failed_rules() -> None:
    passed = evaluate_quality_policy(policy(), [item("exp-a", sequence=1)])
    assert passed.verdict == "passed"
    assert passed.failed_rules == ()
    assert len(passed.evidence_digest) == 64
    assert len(passed.policy_digest) == 64

    failed = evaluate_quality_policy(
        policy(min_mean_score_milli=2_500, require_no_degraded_results=True),
        [item("exp-a", sequence=1, degraded=True)],
    )
    assert failed.verdict == "failed"
    assert {rule["code"] for rule in failed.failed_rules} == {
        "mean_score_milli",
        "no_degraded_results",
    }


def test_required_unavailable_denominators_fail_closed() -> None:
    empty = item("exp-a", sequence=1, judgments=[])
    empty["result_count"] = 0
    empty["result_ranks"] = []
    result = evaluate_quality_policy(
        policy(
            min_judged_result_count=0,
            min_judgment_coverage_bps=0,
            min_exact_agreement_bps=0,
            min_mean_score_milli=0,
        ),
        [empty],
    )
    assert result.verdict == "failed"
    assert {rule["code"] for rule in result.failed_rules} == {
        "judgment_coverage_bps",
        "exact_agreement_bps",
        "mean_score_milli",
    }


def test_malformed_or_secret_evidence_is_rejected() -> None:
    bad = item("exp-a", sequence=1)
    bad["query_hash"] = "not-a-digest"
    with pytest.raises(ReleaseManifestInvalid):
        canonicalize_experiment_evidence([bad])
    with pytest.raises(ReleaseManifestInvalid, match="credential-like"):
        canonical_quality_digest("policy", {"notes": "token=topsecret"})


def test_quality_modules_import_directly_in_a_clean_interpreter() -> None:
    for module in (
        "core.enterprise_release_quality_service",
        "core.enterprise_release_quality",
        "core.enterprise_release_quality_waivers",
    ):
        completed = subprocess.run(
            [sys.executable, "-B", "-c", f"import {module}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_quality_evidence_rejects_coerced_boolean_and_malformed_judgment_collection() -> None:
    coerced = item("exp-a", sequence=1)
    coerced["degraded"] = 1
    with pytest.raises(ReleaseManifestInvalid, match="degraded"):
        canonicalize_experiment_evidence([coerced])

    malformed = item("exp-a", sequence=1)
    malformed["judgments"] = None
    with pytest.raises(ReleaseManifestInvalid, match="judgments"):
        canonicalize_experiment_evidence([malformed])

    malformed_policy = policy()
    malformed_policy["require_no_degraded_results"] = 1
    with pytest.raises(ReleaseManifestInvalid, match="exact boolean"):
        evaluate_quality_policy(malformed_policy, [item("exp-a", sequence=1)])
