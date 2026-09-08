from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_release_quality_gate_policies": "TenantReleaseQualityGatePolicy",
    "dataset_quality_baselines": "DatasetQualityBaseline",
    "dataset_quality_baseline_items": "DatasetQualityBaselineItem",
    "dataset_release_quality_certifications": "DatasetReleaseQualityCertification",
    "dataset_release_quality_certification_evidence": "DatasetReleaseQualityCertificationEvidence",
    "dataset_release_quality_waivers": "DatasetReleaseQualityWaiver",
    "dataset_release_quality_events": "DatasetReleaseQualityEvent",
}


def test_quality_orm_models_exist_with_constraints_and_tenant_leading_fks() -> None:
    for table_name, class_name in TABLES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, class_name
        table = orm.Base.metadata.tables[table_name]
        assert table is model.__table__
        assert any(isinstance(item, UniqueConstraint) for item in table.constraints)
        assert any(isinstance(item, CheckConstraint) for item in table.constraints)
        for fk in (item for item in table.constraints if isinstance(item, ForeignKeyConstraint)):
            assert tuple(fk.column_keys)[0] == "tenant_id"
        assert any(isinstance(item, Index) for item in table.indexes)


def test_quality_policy_and_certification_use_integer_metric_columns() -> None:
    policy = orm.Base.metadata.tables["tenant_release_quality_gate_policies"]
    certification = orm.Base.metadata.tables["dataset_release_quality_certifications"]
    for name in (
        "min_judgment_coverage_bps",
        "min_exact_agreement_bps",
        "min_mean_score_milli",
    ):
        assert name in policy.c
    for name in (
        "judgment_coverage_bps",
        "exact_agreement_bps",
        "mean_score_milli",
    ):
        assert name in certification.c


def test_quality_policy_requires_canonical_active_scope_key() -> None:
    policy = orm.Base.metadata.tables["tenant_release_quality_gate_policies"]
    canonical = str(
        policy.constraints
        and next(
            constraint.sqltext
            for constraint in policy.constraints
            if isinstance(constraint, CheckConstraint)
            and constraint.name == "ck_tenant_release_quality_gate_policies_active_scope_key"
        ).compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "active_scope_key = 'global:*'" in canonical
    assert "active_scope_key = ('risk_tier:' || scope_value)" in canonical
    assert "active_scope_key = ('channel:' || scope_value)" in canonical


def test_approval_orm_action_checks_include_quality_waiver() -> None:
    expected = "knowledge_base_release_quality_waiver"
    for table_name, constraint_name in (
        ("tenant_approval_policies", "ck_tenant_approval_policies_action_type"),
        ("tenant_approval_requests", "ck_tenant_approval_requests_action_type"),
    ):
        table = orm.Base.metadata.tables[table_name]
        checks = {
            str(constraint.name): str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert expected in checks[constraint_name]
