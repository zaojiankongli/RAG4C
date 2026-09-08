# Enterprise Knowledge Base Quality Certification and Publish Gate Design

**Date:** 2026-08-29  
**Status:** Approved through the user's standing “按推荐方案做 / 继续执行” direction  
**Stage:** 20  
**Target revision:** `0030_enterprise_release_quality_certification`  
**Down revision:** `0029_enterprise_knowledge_base_releases`

## 1. Decision summary

Stage 20 adds a Release-linked quality certification control plane without modifying or duplicating the protected Retrieval Quality runner.

The authority model has four parts:

1. **Quality Gate Policy** — revision-fenced Tenant policy resolved by Channel/risk scope.
2. **Quality Baseline** — immutable reusable selection of existing Retrieval Experiments.
3. **Certification** — immutable verdict and evidence snapshot for one Release, Baseline and Policy revision.
4. **Waiver** — expiring, approval-backed exception scoped to one Release and Channel.

High-risk and default-serving Channel promotion must have either a current passing Certification or a current approved Waiver. Missing or unverifiable authority fails closed.

## 2. Protected Retrieval Quality boundary

The following paths are read-only evidence sources and remain protected:

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

Stage 20 reads `retrieval_experiments` and `retrieval_judgments` directly through a new certification service. It does not modify experiments, judgments, runner behavior or existing API contracts.

## 3. Database authority

Revision `0030_enterprise_release_quality_certification` creates seven tables.

### 3.1 `tenant_release_quality_gate_policies`

Tenant-scoped, revision-fenced policy authority.

Key fields:

```text
id, tenant_id, name
scope_type = global | risk_tier | channel
scope_value
active_scope_key
status = active | disabled
revision
min_experiment_count
min_judged_result_count
min_judgment_coverage_bps
min_exact_agreement_bps
min_mean_score_milli
max_conflicting_results
require_all_experiments_completed
require_no_degraded_results
max_certification_age_minutes
created/updated/disabled evidence
```

Resolution order:

```text
exact Channel -> risk tier -> global
```

Only one active policy exists for one Tenant/scope key. High-risk/default-serving promotion with no resolvable active policy fails closed.

### 3.2 `dataset_quality_baselines`

Immutable baseline revision. A new revision creates a new row; old baselines are never edited.

```text
id, tenant_id, dataset_id
name, normalized_name
baseline_revision
parent_baseline_id
experiment_count, query_count
baseline_digest
created_at/by, reason, request_id
```

### 3.3 `dataset_quality_baseline_items`

Immutable ordered experiment links.

```text
id, tenant_id, dataset_id, baseline_id
ordinal
experiment_id, experiment_sequence
query_hash
experiment_serving_generation
strategy_digest, result_digest, evidence_digest, judgment_digest
created_at
```

A baseline item references an existing Retrieval Experiment in the same Tenant/Dataset. It snapshots the current judgment digest but never changes the judgment row.

### 3.4 `dataset_release_quality_certifications`

Immutable certification verdict.

```text
id, tenant_id, dataset_id, release_id
baseline_id, policy_id, policy_revision
release_manifest_digest
release_mutation_generation
release_serving_generation
status = passed | failed
experiment_count, completed_experiment_count
query_count, judged_result_count, total_result_count
judgment_coverage_bps
multi_judged_results, unanimous_results, conflicting_results
exact_agreement_bps
mean_score_milli
failed_rule_count
policy_snapshot_json
summary_json
evidence_digest, certification_digest
created_at/by, reason, request_id
valid_until
```

The row is immutable. A newer certification supersedes it by event/query precedence; it does not update it.

### 3.5 `dataset_release_quality_certification_evidence`

Immutable evidence rows linking Certification to baseline experiments.

```text
id, tenant_id, dataset_id, certification_id
baseline_item_id, experiment_id
ordinal
status
result_count, judged_result_count
agreement facts
experiment_digest, judgment_digest
safe_facts_json
created_at
```

### 3.6 `dataset_release_quality_waivers` and lifecycle events

`dataset_release_quality_waivers` is an immutable approved execution fact:

```text
id, tenant_id, dataset_id, release_id, channel_id
policy_id, policy_revision
release_manifest_digest
approval_request_id, approval_execution_id
reason
valid_from, expires_at
created_at/by, request_id
waiver_digest
```

`dataset_release_quality_events` is append-only:

```text
certification_created (state = passed | failed) |
gate_evaluated | gate_passed | gate_failed | gate_blocked | gate_unavailable |
waiver_requested | waiver_approved | waiver_rejected | waiver_applied |
waiver_expired | waiver_revoked |
release_promoted_with_quality_gate | release_rolled_back_with_quality_gate
```

Revocation never rewrites the immutable Certification/Waiver row.

## 4. Certification evaluation

`certify_release(...)` runs inside one transaction and deterministic lock order:

```text
Tenant
-> Dataset
-> ownership + Workspace
-> Release Manifest + entries
-> selected policy
-> Baseline + items
-> Retrieval Experiments
-> Retrieval Judgments
-> existing Certification/Event facts
```

It verifies:

- Release is same Tenant/Dataset and non-retired;
- Release Manifest digest and entries remain immutable and valid;
- every baseline Experiment is same Tenant/Dataset;
- every Experiment has `dataset_serving_generation == Release.serving_generation`;
- experiment result/evidence document/chunk revision lineage is represented by the Release Manifest;
- baseline digests still match immutable Experiment snapshots;
- current judgments are snapshotted and digested;
- all policy metrics are computed from facts, not client values.

The result is `passed` or `failed`; both are persisted and auditable.

## 5. Metrics and deterministic arithmetic

To avoid cross-dialect floating-point authority drift, persistent thresholds and results use integers:

```text
coverage/agreement: basis points 0..10000
mean score: milli-score 0..3000
```

Rules:

```text
experiment_count >= minimum
completed_experiment_count == experiment_count when required
judged_result_count >= minimum
coverage_bps >= minimum
agreement_bps >= minimum when multi-review facts exist
mean_score_milli >= minimum
conflicting_results <= maximum
no degraded experiment when required
```

A metric with unavailable denominator remains unavailable and fails any required threshold.

## 6. Promotion gate

Stage 20 extends `promote_release` immediately before Channel binding writes.

For low-risk/non-default Channels:

- no policy: existing direct behavior remains;
- matching active policy: Certification is required.

For high-risk/default-serving Channels:

- matching active policy is mandatory;
- latest passing Certification must match Release digest, Policy revision and validity window;
- otherwise a valid non-revoked Waiver scoped to the exact Release/Channel is required.

A Certification or Waiver does not replace the existing publish Approval requirement. Both gates apply:

```text
quality gate -> publish approval gate -> atomic Channel promotion
```

## 7. Waiver approval

New Approval action:

```text
knowledge_base_release_quality_waiver
```

Snapshot binds:

- Release ID/number/digest;
- Channel ID/revision;
- Policy ID/revision/digest;
- current failed/missing Certification evidence digest;
- Dataset profile/mutation/serving generation;
- ownership/Workspace revisions;
- waiver expiry and reason.

The one-time consumer validates the opaque `ApprovalExecutionFact`, current scope and revisions, then writes the immutable Waiver. Forged Mapping, wrong actor/scope, replay and stale evidence fail closed.

## 8. API boundary

Strict routes:

```text
GET/POST/PATCH /api/enterprise/release-quality/policies
GET/POST /api/enterprise/knowledge-bases/{dataset_id}/quality-baselines
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-baselines/{baseline_id}
POST /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certifications
GET /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/certifications
GET /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-gate?channel_id=...
POST /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/quality-waivers
```

All mutations use strict bodies, exact integer/bool validation, Idempotency-Key, actor/Tenant/request evidence and sanitized results.

## 9. UI design

Certification remains inside the Knowledge Base resource workspace.

### Channel summary

Adds one compact gate strip:

```text
Quality Gate | Certification | Baseline | Valid until | Waiver
```

### Release history

Adds Certification status as one dense column/tag. Mobile cards add one compact gate line.

### Release Detail Drawer

Adds `Certification` between Readiness and Impact:

```text
Manifest | Readiness | Certification | Impact | Audit
```

Certification panel contains:

- policy and revision;
- threshold vs observed comparison table;
- baseline name/revision and experiment count;
- evidence status;
- Certification history;
- one primary action: Certify or Request waiver.

No nested Drawer. Baseline selection and waiver reason use Dialogs. Approval-required waiver deep-links to the existing Approval Center.

## 10. Security and privacy

- no query text, result body, judgment note or raw ticket enters Certification/Policy API unless explicitly projected safe;
- Experiment and Judgment content is represented by digests and bounded metrics;
- baseline/certification/evidence rows are immutable;
- events are append-only;
- Tenant-leading composite FKs prevent cross-Tenant evidence reuse;
- Certification creation is revision-fenced, idempotent and audited;
- promotion revalidates Certification/Waiver in the same transaction and lock scope.

## 11. Operations and non-goals

Preflight is read-only and reports malformed policies, orphan baselines/evidence, stale certifications, expired waivers and Release/Channel gate failures.

No automatic production migration, baseline creation, Certification, waiver, approval execution or promotion is performed.

Non-goals:

- second Retrieval Experiment runner;
- model/code judge execution;
- modifying reviewer judgments;
- automatic waiver;
- physical retirement/destruction.
