"""Stable public facade for Stage 20 Release quality authority."""

from __future__ import annotations

from typing import Any

from core.enterprise_release_quality_evidence import (
    QUALITY_SCHEMA_VERSION,
    QualityEvidenceSummary,
    QualityPolicyEvaluation,
    canonical_quality_digest,
    canonicalize_experiment_evidence,
    collect_release_experiment_evidence,
    evaluate_quality_policy,
    summarize_quality_evidence,
)
from core.enterprise_release_quality_service import (
    ReleaseQualityConflict,
    ReleaseQualityError,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    ReleaseQualityNotFound,
    ReleaseQualityUnavailable,
    certify_release,
    collect_baseline_experiment_evidence,
    create_quality_baseline,
    create_quality_gate_policy,
    get_quality_baseline,
    get_release_quality_gate,
    list_quality_baselines,
    list_quality_gate_policies,
    list_release_certifications,
    resolve_release_quality_gate,
    update_quality_gate_policy,
)

__all__ = [
    "QUALITY_SCHEMA_VERSION",
    "QualityEvidenceSummary",
    "QualityPolicyEvaluation",
    "ReleaseQualityConflict",
    "ReleaseQualityError",
    "ReleaseQualityForbidden",
    "ReleaseQualityInvalid",
    "ReleaseQualityNotFound",
    "ReleaseQualityUnavailable",
    "canonical_quality_digest",
    "canonicalize_experiment_evidence",
    "certify_release",
    "collect_baseline_experiment_evidence",
    "collect_release_experiment_evidence",
    "create_quality_baseline",
    "create_quality_gate_policy",
    "evaluate_quality_policy",
    "get_quality_baseline",
    "get_release_quality_gate",
    "list_quality_baselines",
    "list_quality_gate_policies",
    "list_release_certifications",
    "resolve_release_quality_gate",
    "summarize_quality_evidence",
    "update_quality_gate_policy",
]

_QUALITY_WAIVER_EXPORTS = frozenset(
    {
        "ACTION_KNOWLEDGE_BASE_RELEASE_QUALITY_WAIVER",
        "QUALITY_WAIVER_OPERATION",
        "RESOURCE_KNOWLEDGE_BASE",
        "grant_quality_waiver",
        "request_quality_waiver",
    }
)
__all__.extend(sorted(_QUALITY_WAIVER_EXPORTS))


def __getattr__(name: str) -> Any:
    if name in _QUALITY_WAIVER_EXPORTS:
        from core import enterprise_release_quality_waivers

        value = getattr(enterprise_release_quality_waivers, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
