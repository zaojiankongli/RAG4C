# Stage 20 Enterprise Release Quality Certification Implementation Plan

> Execute with `subagent-driven-development`, TDD and verification-before-completion. Protected Retrieval Quality paths remain read-only.

**Date:** 2026-08-29  
**Goal:** Add Release-linked Quality Baselines, immutable Certifications, Channel publish gates and approval-backed expiring Waivers.  
**Revision:** `0030_enterprise_release_quality_certification`  
**Down revision:** `0029_enterprise_knowledge_base_releases`

## Invariants

- No second Retrieval Experiment runner.
- No writes to protected Retrieval Quality paths.
- Certification facts are computed server-side from existing immutable Experiments and current reviewer Judgments.
- Baseline, Certification, evidence and events are immutable/append-only.
- High-risk/default-serving promotion requires both quality authority and existing publish approval authority.
- No raw query/result body/judgment note/ticket/secret/Idempotency-Key is persisted or rendered.
- No production migration, certification, waiver or promotion is executed automatically.

## Task 1 — Migration, ORM, Catalog and Readiness

**Files:**

- `catalog_migrations/versions/0030_enterprise_release_quality_certification.py`
- `models/orm.py`
- `core/catalog_schema.py`
- `server/enterprise_readiness_api.py`
- migration/ORM/catalog/readiness tests

**RED coverage:**

- seven tables and Tenant-leading composite FKs;
- integer threshold checks and exact lifecycle predicates;
- active policy scope uniqueness;
- immutable Baseline/Item/Certification/Evidence/Waiver guards;
- append-only events;
- new Approval action CHECK;
- offline MySQL/PostgreSQL DDL and online downgrade blockers;
- 0029 capabilities remain usable at 0030;
- `enterprise_release_quality_certification` readiness capability.

## Task 2 — Pure Quality Evidence and Policy Evaluation

**Files:**

- `core/enterprise_release_quality.py`
- `tests/test_enterprise_release_quality_core.py`
- `tests/test_enterprise_release_quality_evidence.py`

**Interfaces:**

```python
canonical_quality_digest(...)
collect_experiment_evidence(...)
evaluate_quality_policy(...)
```

**RED coverage:**

- deterministic baseline/evidence/certification digest;
- bps/milli arithmetic and unavailable denominator handling;
- Experiment Dataset/serving generation/Release lineage match;
- judgment digest and agreement calculation;
- failed/degraded/conflicting evidence;
- secret/body/note exclusion.

## Task 3 — Policy, Baseline and Certification Persistence/API

**Files:**

- `core/enterprise_release_quality.py`
- `server/enterprise_release_quality_api.py`
- `server/app.py`
- persistence/API tests

**Interfaces:**

```python
create_quality_gate_policy(...)
update_quality_gate_policy(...)
create_quality_baseline(...)
list_quality_baselines(...)
get_quality_baseline(...)
certify_release(...)
list_release_certifications(...)
get_release_quality_gate(...)
```

**RED coverage:**

- Tenant manager/read permission separation;
- immutable baseline from selected Experiment IDs;
- exact policy resolution order;
- Certification pass/fail persistence and replay;
- stale Release/Policy/Baseline/Judgment refusal;
- strict API schemas, cursors and sanitization.

## Task 4 — Promotion Gate and Approval-backed Waiver

**Files:**

- `core/enterprise_knowledge_base_release_mutations.py`
- `core/enterprise_approval_control.py`
- `server/enterprise_approval_consumers.py`
- gate/approval tests

**Interfaces:**

```python
resolve_release_quality_gate(...)
request_quality_waiver(...)
grant_quality_waiver(..., approval_execution_fact=...)
```

**RED coverage:**

- low-risk no-policy compatibility;
- policy-required direct block;
- high-risk/default missing-policy fail closed;
- passing current Certification allows quality gate;
- failed/stale/expired/revoked Certification blocks;
- exact Release/Channel valid Waiver allows quality gate;
- waiver Approval fact forged/replay/stale refusal;
- downstream receipt reconciliation;
- quality gate runs before publish approval and before binding writes.

## Task 5 — TDesign Certification Models/API/Hook

**Files:**

- `frontend/src/enterprise-release-quality/**`

**Interfaces:**

- strict Policy/Baseline/Certification/Gate/Waiver projectors;
- inactive/scope-safe hooks;
- current gate summary, lazy certification detail/history;
- mutation retry reuses one Idempotency-Key;
- malformed authority becomes unavailable, never empty.

## Task 6 — Certification Center UI Integration

**Files:**

- `frontend/src/enterprise-release-quality/components/**`
- existing Stage 19 Release Center/Detail Drawer integration files only
- no protected Retrieval Quality changes

**RED coverage:**

- desktop threshold comparison table and mobile cards;
- Certification tab in Release Detail Drawer;
- Channel gate strip and Release history Certification tag;
- baseline selection Dialog;
- certify and request-waiver flows;
- approval deep link;
- read-only, keyboard Tabs, one Drawer, focus return, 375/280, dark mode;
- safe null/unavailable and no raw secrets.

## Task 7 — Preflight and Controlled Playwright

**Files:**

- `scripts/enterprise_catalog_upgrade.py`
- `docs/operations/enterprise-catalog-upgrade.md`
- `output/playwright/enterprise-release-quality-stage20/**`
- Stage 20 visual acceptance report

**Matrix:**

```text
direct/hash × light/dark × 1440/375/280
```

**Flows:**

- no-policy block;
- failed Certification;
- passing Certification;
- approval-required Waiver;
- valid Waiver gate;
- Certification evidence and history;
- zero browser/network/overflow/leak errors;
- source-bound fresh artifact manifest.

## Task 8 — Final Regression and Reviews

- Stage 7–20 backend affected baseline in bounded batches;
- enterprise frontend batches plus production build;
- Ruff/format/py_compile/ESLint/Prettier/TypeScript;
- Alembic head, lock and diff checks;
- independent Luna-max Database/Security and TDesign/UI reviews;
- resolve all P0/P1/P2;
- keep long-term `/goal` active for Stage 21.
