# Stage 20 Database/Security Sidecar — Release-linked Quality Certification, Publish Gate, and Waiver

**Date:** 2026-08-29  
**Status:** Proposed design sidecar; no implementation in this task  
**Target revision:** `0030_enterprise_release_quality_certification`  
**Down revision:** `0029_enterprise_knowledge_base_releases`  
**Scope:** Database, security, approval, migration, preflight, and API contracts only

## 1. Scope and non-actions

This document proposes the Stage 20 database/security contract for a Release-linked quality certification, publish gate, and approval-aware waiver. It is deliberately a sidecar to the existing Release authority rather than a redesign of the retrieval evaluator.

This task performed no code changes, no migration, no data backfill, no Release capture, no promotion, no rollback, no Application pin, no Approval execution, and no external service call. The only permitted output file for this task is:

```text
docs/research/2026-08-29-stage20-quality-certification-db-security.md
```

The following paths were read for contract discovery and remain protected from modification:

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

Stage 20 must not create a second Retrieval Experiment runner. It should add a read-only, release-aware certification service around the existing immutable experiment snapshots and the existing reviewer-judgment repository.

## 2. Repository evidence and findings

### 2.1 Stage 19 already provides the deployment identity that quality needs

The current `0029_enterprise_knowledge_base_releases` contract has separate tenant-scoped Release Channels, immutable Release Manifests and Entries, append-only Release Events, Dataset Channel bindings, and explicit Application `follow_channel` / `pinned` binding. The relevant ORM authority is in `models/orm.py:2099-2555`; the migration is `catalog_migrations/versions/0029_enterprise_knowledge_base_releases.py`; and the mutation service is `core/enterprise_knowledge_base_release_mutations.py:830-1544`.

A quality result must therefore be keyed by at least:

```text
tenant_id + dataset_id + release_id + manifest_digest + channel_id
```

A Dataset or a current `serving_generation` alone is not a reproducible deployment target.

### 2.2 RetrievalExperiment is an immutable evidence snapshot, but it is not Release-linked

`RetrievalExperiment` is an append-only/immutable row with a tenant/dataset scope, query hash, strategy snapshot, result snapshot, evidence lineage, latency, status, creator, timestamp, run ID, and a monotonic sequence. The ORM is `models/orm.py:3999-4065`; the original migration and database immutable triggers are `catalog_migrations/versions/0012_retrieval_experiments.py:80-224`.

The runner captures authority-checked result references and source revisions (`core/retrieval_experiment_runner.py:318-440`), strategy/result/evidence revisions and `dataset_serving_generation` (`:443-460`, `:503-519`), then writes variants atomically behind a Dataset serving-generation fence (`:536-590`). It does **not** store `release_id`, `manifest_digest`, `channel_id`, or a policy revision. A later certification must revalidate the experiment snapshots against the Release Manifest and persist its own immutable binding digest. It must not infer Release identity from matching generations.

### 2.3 RetrievalJudgment is reviewer-owned and revisioned, but the current row is mutable

`RetrievalJudgment` stores tenant/dataset/experiment scope, result rank, document/chunk references, relevance label, optional score, note, revision, reviewer, and timestamp (`models/orm.py:4067-4160`). The repository enforces reviewer uniqueness, experiment/result-rank consistency, tenant-scoped references, and optimistic revision fencing (`core/retrieval_experiments.py:809-1032`).

The agreement endpoint computes its result at read time from current judgment rows (`core/retrieval_experiments.py:1033-1078`, exposed by `server/retrieval_experiments_api.py:755-779`). There is no immutable agreement row and no immutable judgment history table. Therefore a certification must copy the labels, scores, reviewer reference, revision, and a canonical judgment digest at capture time. A later judgment edit must not rewrite the certification; the next gate recheck must detect the revision/digest mismatch. Raw notes must not be copied into certification evidence.

### 2.4 The current Retrieval Experiment API has no quality or mutation-idempotency contract

The protected API defines strict request models for experiment creation, bounded runner execution, judgment creation, judgment patching, listing, detail, and agreement (`server/retrieval_experiments_api.py:121-325`, `:537-779`). It uses tenant-bound bearer authorization and read/write Knowledge permissions, but experiment and judgment mutation routes do not accept the Stage 19 `Idempotency-Key` header.

Stage 20 must not silently change those protected routes in this sidecar. New quality mutations must use the existing generic tenant idempotency ledger (`core/enterprise_tenant_idempotency.py:54-208`) and must never rely on an experiment mutation being idempotent.

### 2.5 Stage 19 promotion already has revision fences and approval facts

The current Release mutation service locks tenant/dataset/channel authority, uses the generic tenant mutation ledger, validates Release Manifest authority, and accepts an opaque `ApprovalExecutionFact` for high-risk/default-serving publish and rollback (`core/enterprise_knowledge_base_release_mutations.py:116-207`, `:629-739`, `:830-1377`). The approval fact already carries Release identity, Manifest digest, Channel revision, profile/ownership/Workspace revisions, mutation generation, and serving generation (`core/enterprise_approval_control.py:152-330`, `:2476-2554`).

Stage 20 should extend that fact with a quality-gate binding rather than passing an untrusted quality JSON object from the HTTP caller. A quality waiver must never be accepted merely because a caller supplies a `waiver_id`.

### 2.6 `Dataset.serving_generation` is a fence, not the Release identity

Stage 19 increments Dataset `serving_generation` when a Release reaches a default-serving Channel. That generation is useful for a concurrent mutation fence, but it is not the immutable content identity of a Release. A candidate captured at generation `N` may be promoted while the current Dataset fence becomes `N+1`.

The Stage 20 certification must record both:

```text
manifest_serving_generation          = generation captured into the immutable Manifest
promotion_expected_serving_generation = current fence supplied to a later promotion
```

A gate must not be invalidated solely because the dynamic Dataset serving-generation counter changed after a successful promotion. It must be invalidated when the Manifest digest, Release entries, policy binding, certification evidence, or current judgment revisions no longer match.

### 2.7 Audit and approval are separate authorities and must remain separate

`KnowledgeAuditEvent` is the Dataset-scoped audit stream used by the retrieval repository; `TenantAuditEvent` is the tenant-level enterprise audit stream. Both are append-only audit facts with sanitized snapshots (`models/orm.py:3882-3998`). Approval requests and decisions are separate tenant-scoped authorities with immutable decision facts and hashed tickets (`models/orm.py:1330-1710`, `core/enterprise_approval_control.py:3014-3544`).

A quality event must be written in the same transaction as the corresponding quality projection mutation and the appropriate audit facts. The generic approval request remains the approval authority; quality tables store only the approval request ID, execution ID, snapshot hash, and safe scope facts—not a raw ticket.

## 3. Stage 20 decisions

1. **A certification is an immutable evaluation snapshot of one exact Release, Channel policy binding, and evidence set.**
2. **A publish gate is a current, revisioned projection backed by append-only quality events.** The projection is optimized for promotion checks; the event stream is the audit authority.
3. **Metrics are recomputed server-side.** The client submits evidence selectors and fences, never observed metric values or a pass/fail verdict.
4. **Hard authority blockers cannot be waived.** Cross-tenant references, missing Manifest entries, unreadable/unknown authority, invalid digests, stale evidence, and security/ACL integrity failures remain blocked or unavailable.
5. **A waiver is one-time, finite, scope-bound, and approval-aware.** It cannot be copied to another Release, Channel, policy version, or gate.
6. **A Release promotion consumes a current quality gate in the same authority/fence decision.** A historical `passed` certification is not sufficient.
7. **Every new table is tenant-leading and every public read/write is tenant-scoped.** No global ID lookup is considered safe.
8. **No historical certification is backfilled automatically.** Existing Releases become `not_evaluated`/`unavailable` for quality purposes until an explicit certification action is performed.

## 4. Revision and bounded domain

```text
revision:     0030_enterprise_release_quality_certification
down_revision: 0029_enterprise_knowledge_base_releases
```

The Stage 20 quality domain has four distinct states:

```text
Certification result:
  passed | failed | blocked | unavailable

Publish gate:
  passed | blocked | waiver_required | waived | expired | revoked | unavailable

Policy binding:
  active | removed

Quality event:
  append-only lifecycle fact; never updated or deleted
```

`failed` means the evaluator produced trustworthy observations and one or more policy thresholds did not pass. `blocked` means the evaluation is trustworthy but cannot pass because required evidence/sample coverage is missing or a non-waivable invariant failed. `unavailable` means the authority itself cannot be proven. Neither `blocked` nor `unavailable` may be converted into `passed` by a UI flag or client-supplied override.

## 5. Proposed 0030 schema

The following tables are the recommended relational core. All IDs are opaque strings, all timestamps use the existing `DATETIME(6)`/UTC convention, and every table has a tenant foreign key. JSON columns are bounded, canonical, and safe-projected; they are not a substitute for fields that participate in an invariant.

### 5.1 `tenant_quality_policy_versions` — immutable policy snapshots

This table makes policy thresholds reproducible. A mutable “current policy” row is not enough because a later policy edit must not change the meaning of a previous certification.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
policy_key                  VARCHAR(128)      NOT NULL
policy_revision              INTEGER           NOT NULL
evaluation_contract_version  INTEGER           NOT NULL
waiver_mode                 VARCHAR(32)       NOT NULL
max_waiver_minutes          INTEGER           NOT NULL
threshold_count              INTEGER           NOT NULL
thresholds_digest            CHAR(64)          NOT NULL
policy_metadata_json         JSON              NOT NULL
created_at                   DATETIME(6)       NOT NULL
created_by                   VARCHAR(64)       NOT NULL
reason                       VARCHAR(512)      NOT NULL
```

Required constraints and indexes:

- unique `(tenant_id, id)`;
- unique `(tenant_id, policy_key, policy_revision)`;
- `policy_revision > 0`, `evaluation_contract_version > 0`;
- `waiver_mode IN ('never','approval_required','low_risk_approval')`;
- `max_waiver_minutes BETWEEN 15 AND 10080`;
- `threshold_count > 0` and `thresholds_digest` is a lowercase SHA-256 digest;
- `(created_by, tenant_id)` references the tenant member table;
- indexes `(tenant_id, policy_key, policy_revision)` and `(tenant_id, created_at, id)`;
- immutable UPDATE and DELETE guards, with a capability check that validates trigger semantics rather than only trigger names.

`policy_metadata_json` may contain display name, evaluation description, and version metadata. It must reject credentials, tokens, raw URLs with embedded credentials, document bodies, and arbitrary unbounded text.

### 5.2 `tenant_quality_policy_thresholds` — typed threshold facts

Thresholds are normalized so the database and preflight can reason about their identity. The policy version’s `thresholds_digest` is calculated from the canonical ordered child rows.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
policy_version_id            VARCHAR(64)       NOT NULL
metric_key                  VARCHAR(64)       NOT NULL
metric_kind                 VARCHAR(16)       NOT NULL
comparator                  VARCHAR(8)        NOT NULL
target_value                DECIMAL(20,8)     NOT NULL
minimum_sample_size         INTEGER           NOT NULL
required                    BOOLEAN           NOT NULL
waiver_allowed              BOOLEAN           NOT NULL
created_at                  DATETIME(6)       NOT NULL
```

Required constraints include unique `(tenant_id, id)`, unique `(tenant_id, policy_version_id, metric_key)`, a composite FK to the immutable policy version, `metric_kind IN ('rate','score','count','duration')`, `comparator IN ('gte','gt','lte','lt','eq')`, and `minimum_sample_size >= 0`. `metric_key` is an allowlisted Stage 20 metric, never an executable expression. Threshold rows are immutable and indexed by `(tenant_id, policy_version_id, metric_key)`.

### 5.3 `dataset_quality_policy_bindings` — current Channel policy projection

A policy version is immutable; this row selects which version applies to one Dataset and one Release Channel.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
policy_version_id            VARCHAR(64)       NOT NULL
status                      VARCHAR(16)       NOT NULL
active_slot                 VARCHAR(16)       NULL
revision                    INTEGER           NOT NULL
reason                      VARCHAR(512)      NOT NULL
created_at                  DATETIME(6)       NOT NULL
created_by                  VARCHAR(64)       NOT NULL
updated_at                  DATETIME(6)       NOT NULL
updated_by                  VARCHAR(64)       NOT NULL
removed_at                  DATETIME(6)       NULL
removed_by                  VARCHAR(64)       NULL
```

Required constraints and indexes:

- unique `(tenant_id, id)` and `(tenant_id, dataset_id, channel_id)`;
- unique `(tenant_id, dataset_id, channel_id, active_slot)` for the active slot;
- `status IN ('active','removed')`;
- active rows require `active_slot='active'` and no removal evidence; removed rows require removal evidence;
- `revision > 0`;
- composite FKs to Dataset, Release Channel, and policy version;
- index `(tenant_id, dataset_id, channel_id, status, revision)`;
- mutation uses the existing tenant idempotency ledger, manager permission, and expected revision.

There is no implicit “global default quality policy.” A missing active binding makes a gate `unavailable` and blocks promotion. This is intentional: a default policy must be explicitly selected and auditable.

### 5.4 `dataset_release_quality_certifications` — immutable certification envelope

This row is the signed-like envelope for one evaluation. It contains copies of every authority digest needed to prove what was evaluated.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
certification_number        INTEGER           NOT NULL
release_number              INTEGER           NOT NULL
manifest_digest             CHAR(64)          NOT NULL
policy_version_id           VARCHAR(64)       NOT NULL
policy_digest               CHAR(64)          NOT NULL
policy_binding_revision     INTEGER           NOT NULL
evaluation_contract_version INTEGER           NOT NULL
manifest_serving_generation BIGINT            NOT NULL
manifest_mutation_generation BIGINT           NOT NULL
profile_revision             INTEGER           NOT NULL
ownership_revision           INTEGER           NOT NULL
workspace_revision           INTEGER           NOT NULL
result_state                 VARCHAR(16)       NOT NULL
certification_digest         CHAR(64)          NOT NULL
metrics_digest               CHAR(64)          NOT NULL
evidence_digest              CHAR(64)          NOT NULL
metric_count                 INTEGER           NOT NULL
evidence_count              INTEGER           NOT NULL
source_experiment_count      INTEGER           NOT NULL
source_judgment_count        INTEGER           NOT NULL
valid_until                  DATETIME(6)       NULL
created_at                   DATETIME(6)       NOT NULL
created_by                   VARCHAR(64)       NOT NULL
reason                       VARCHAR(512)      NOT NULL
request_id                   VARCHAR(128)      NOT NULL
```

Required constraints:

- composite FK `(tenant_id, dataset_id, release_id)` to the immutable Release Manifest;
- composite FK `(tenant_id, channel_id)` to the Release Channel;
- composite FK `(tenant_id, policy_version_id)` to the policy version;
- unique `(tenant_id, id)`;
- unique `(tenant_id, dataset_id, certification_number)`;
- unique `(tenant_id, dataset_id, release_id, channel_id, policy_version_id, certification_digest)`;
- `result_state IN ('passed','failed','blocked','unavailable')`;
- all revisions/generations/counts are in valid ranges;
- all four digests are lowercase SHA-256 values;
- `valid_until` is null only when the policy explicitly permits non-expiring certification; recommended v1 policies always use a finite validity window;
- immutable UPDATE and DELETE guards;
- indexes `(tenant_id, dataset_id, release_id, channel_id, created_at, id)`, `(tenant_id, certification_digest)`, and `(tenant_id, result_state, created_at, id)`.

The `manifest_serving_generation` column is the captured Manifest fact. It is not compared directly with the post-promotion dynamic Dataset serving counter during ordinary gate reads.

### 5.5 `dataset_release_quality_metrics` — immutable threshold evaluations

Each policy threshold produces one typed metric row. The threshold snapshot is copied into the row so a policy-binding change cannot rewrite historical meaning.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
certification_id             VARCHAR(64)       NOT NULL
metric_key                  VARCHAR(64)       NOT NULL
metric_kind                 VARCHAR(16)       NOT NULL
comparator                 VARCHAR(8)        NOT NULL
target_value               DECIMAL(20,8)     NOT NULL
minimum_sample_size        INTEGER           NOT NULL
sample_count                INTEGER           NOT NULL
observed_value              DECIMAL(20,8)     NULL
evaluation_state            VARCHAR(16)       NOT NULL
metric_digest               CHAR(64)          NOT NULL
safe_facts_json             JSON              NULL
created_at                  DATETIME(6)       NOT NULL
```

`evaluation_state` is one of `passed`, `failed`, `insufficient`, or `unavailable`. A null observation is allowed only for `insufficient` or `unavailable`. Required checks include unique `(tenant_id, certification_id, metric_key)`, a composite certification FK, nonnegative sample counts, valid metric ranges, and immutable UPDATE/DELETE guards.

The gate uses these rows plus hard authority checks. It never accepts a client-provided `passed=true` field.

### 5.6 `dataset_release_quality_evidence` — immutable source evidence copies

This table is the bridge from existing RetrievalExperiment/RetrievalJudgment facts to a Release-linked certification. It intentionally stores references, revisions, and digests rather than duplicating document bodies or retrieval content.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
certification_id             VARCHAR(64)       NOT NULL
ordinal                     INTEGER           NOT NULL
evidence_kind               VARCHAR(24)       NOT NULL
source_experiment_id        VARCHAR(64)       NULL
source_experiment_sequence  BIGINT            NULL
source_judgment_id          VARCHAR(64)       NULL
source_judgment_revision    INTEGER           NULL
source_run_id               VARCHAR(64)       NULL
source_serving_generation   BIGINT            NULL
source_snapshot_digest      CHAR(64)          NOT NULL
safe_facts_json              JSON              NOT NULL
evidence_digest              CHAR(64)          NOT NULL
created_at                  DATETIME(6)       NOT NULL
```

Recommended evidence kinds are `experiment`, `judgment`, `agreement`, `run_summary`, and `hard_invariant`. Required rules:

- unique `(tenant_id, certification_id, ordinal)`;
- composite certification FK;
- composite source Experiment FK `(tenant_id, dataset_id, source_experiment_id)`;
- composite source Judgment FK `(tenant_id, dataset_id, source_judgment_id)`;
- `source_experiment_sequence` is stored as an additional anti-confusion check;
- a `judgment` row requires `source_judgment_id` and positive `source_judgment_revision`;
- an `experiment` row requires `source_experiment_id` and a source snapshot digest;
- `safe_facts_json` cannot contain query text, document/chunk body, raw source content, raw notes, credentials, bearer values, cookies, raw approval tickets, or raw `Idempotency-Key` values;
- immutable UPDATE and DELETE guards;
- indexes `(tenant_id, certification_id, ordinal)`, `(tenant_id, dataset_id, source_experiment_id)`, and `(tenant_id, dataset_id, source_judgment_id)`.

The current `retrieval_experiments` table already has a tenant/dataset/id unique key. Because the current `retrieval_judgments` ORM does not expose the same scoped unique key, the 0030 migration should add `uq_retrieval_judgments_scope_id` before adding the composite evidence FK. This is a schema compatibility change, not a modification to the protected retrieval runner/API files.

The capture service must re-read every selected judgment under a row lock, record its revision and canonical label/score digest, and use that copied fact for the certification. It must not trust the live agreement endpoint as an immutable source.

### 5.7 `dataset_release_quality_gates` — current publish-gate projection

This is a mutable read-optimized projection. Its history is in the quality event stream below.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
policy_version_id           VARCHAR(64)       NOT NULL
certification_id             VARCHAR(64)       NOT NULL
manifest_digest              CHAR(64)          NOT NULL
policy_digest                CHAR(64)          NOT NULL
certification_digest         CHAR(64)          NOT NULL
state                        VARCHAR(16)       NOT NULL
revision                     INTEGER           NOT NULL
decision_digest              CHAR(64)          NOT NULL
failed_metric_count          INTEGER           NOT NULL
hard_blocker_count           INTEGER           NOT NULL
active_waiver_id             VARCHAR(64)       NULL
waiver_expires_at            DATETIME(6)       NULL
last_evaluated_at            DATETIME(6)       NOT NULL
last_evaluated_by            VARCHAR(64)       NOT NULL
request_id                   VARCHAR(128)      NOT NULL
updated_at                   DATETIME(6)       NOT NULL
```

Required constraints and indexes:

- unique `(tenant_id, id)`;
- unique `(tenant_id, dataset_id, release_id, channel_id)`;
- composite FKs to Dataset, Release Manifest, Channel, policy version, and certification;
- `state IN ('passed','blocked','waiver_required','waived','expired','revoked','unavailable')`;
- `revision > 0`, nonnegative blocker counts, lowercase digest checks;
- index `(tenant_id, dataset_id, channel_id, state, revision, id)`;
- index `(tenant_id, dataset_id, release_id, channel_id, updated_at, id)`.

The optional `active_waiver_id` is validated transactionally against the immutable waiver row and latest event. Avoid a circular hard FK if the target database makes creation order fragile; capability/preflight must instead report any orphan or scope mismatch as unavailable.

### 5.8 `dataset_release_quality_waivers` — immutable waiver request/fact

A waiver identity and its requested scope are immutable. Its lifecycle is represented by quality events, not by rewriting this row.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
gate_id                     VARCHAR(64)       NOT NULL
certification_id             VARCHAR(64)       NOT NULL
manifest_digest              CHAR(64)          NOT NULL
policy_version_id            VARCHAR(64)       NOT NULL
policy_digest                CHAR(64)          NOT NULL
gate_digest                 CHAR(64)          NOT NULL
failed_metric_digest         CHAR(64)          NOT NULL
requested_expires_at         DATETIME(6)       NOT NULL
reason                       VARCHAR(512)      NOT NULL
compensating_controls_json   JSON              NOT NULL
approval_request_id          VARCHAR(64)       NULL
approval_snapshot_hash       CHAR(64)          NULL
created_at                   DATETIME(6)       NOT NULL
created_by                   VARCHAR(64)       NOT NULL
request_id                   VARCHAR(128)      NOT NULL
```

Required rules:

- composite scope FKs to Dataset, Release Manifest, Channel, gate, certification, and policy version;
- `requested_expires_at` must be later than creation and no later than the policy’s `max_waiver_minutes`;
- `approval_request_id` and `approval_snapshot_hash` are both null for a policy-allowed direct low-risk waiver, or both non-null for an approval-required waiver;
- no waiver can target an `unavailable` gate, a hard integrity blocker, a cross-tenant mismatch, a missing Manifest entry, or a security/ACL failure;
- immutable UPDATE and DELETE guards;
- unique `(tenant_id, id)` and index `(tenant_id, dataset_id, gate_id, created_at, id)`.

A waiver record does not mean “approved.” Approval is proven only by the corresponding generic approval request, an issued one-time `ApprovalExecutionFact`, and a `waiver_approved`/`waiver_applied` event written under the same quality authority fence.

### 5.9 `dataset_release_quality_events` — append-only quality event stream

This stream is the historical authority for certification, gate, and waiver lifecycle. It also provides a tamper-evident digest chain per `(tenant_id, dataset_id, release_id, channel_id)` stream.

```text
id                           VARCHAR(64)       PK
tenant_id                   VARCHAR(64)       NOT NULL
dataset_id                  VARCHAR(64)       NOT NULL
release_id                  VARCHAR(64)       NOT NULL
channel_id                  VARCHAR(128)      NOT NULL
certification_id             VARCHAR(64)       NULL
gate_id                     VARCHAR(64)       NULL
waiver_id                   VARCHAR(64)       NULL
event_type                 VARCHAR(32)       NOT NULL
event_sequence              BIGINT            NOT NULL
state                       VARCHAR(16)       NULL
previous_event_digest        CHAR(64)          NULL
event_digest                CHAR(64)          NOT NULL
approval_request_id          VARCHAR(64)       NULL
approval_execution_id       VARCHAR(128)      NULL
actor_id                    VARCHAR(64)       NOT NULL
reason                      VARCHAR(512)      NOT NULL
safe_snapshot_json           JSON              NOT NULL
request_id                   VARCHAR(128)      NOT NULL
occurred_at                  DATETIME(6)       NOT NULL
```

Allowed event types are:

```text
certification_created
gate_evaluated
gate_passed
gate_failed
gate_blocked
gate_unavailable
gate_expired
gate_revoked
waiver_requested
waiver_approved
waiver_rejected
waiver_applied
waiver_expired
waiver_revoked
release_promoted_with_quality_gate
release_rolled_back_with_quality_gate
```

Required constraints and indexes:

- unique `(tenant_id, id)`;
- unique `(tenant_id, dataset_id, release_id, channel_id, event_sequence)`;
- `event_sequence > 0`;
- lowercase SHA-256 checks for event digests;
- event-specific nullability checks, such as approval IDs required for approval events;
- composite FKs for every non-null quality ID;
- immutable UPDATE and DELETE guards;
- indexes `(tenant_id, dataset_id, release_id, channel_id, event_sequence)`, `(tenant_id, gate_id, occurred_at, id)`, and `(tenant_id, waiver_id, occurred_at, id)`.

The digest is calculated over the canonical event envelope, including the previous event digest, event type, scoped IDs, decision/gate/certification digests, safe snapshot, actor, request ID, and timestamp normalized to UTC. A database trigger must prevent UPDATE/DELETE; application code must serialize event append under the same gate lock.

## 6. Policy threshold contract v1

The policy allowlist is intentionally small and deterministic. A tenant may choose a subset, but every certification contract must include `sample_count`, `evidence_completeness`, and `judgment_coverage` as required quality metrics unless a future evaluation-contract version explicitly replaces them.

### 6.1 Metrics

- `sample_count`: integer count of eligible ranked results or evaluation samples.
- `judgment_coverage`: judged eligible results divided by eligible results, range `[0,1]`.
- `exact_agreement_rate`: the existing agreement summary’s unanimous multi-judged result ratio, range `[0,1]`.
- `conflict_rate`: conflicting multi-judged results divided by multi-judged results, range `[0,1]`.
- `relevant_rate`: `relevant` judgments divided by judged results, range `[0,1]`.
- `mean_score`: average non-null reviewer score, range `[0,3]`.
- `experiment_success_rate`: completed experiments divided by selected experiments, range `[0,1]`.
- `degraded_rate`: degraded result snapshots divided by completed result snapshots, range `[0,1]`.
- `p95_latency_ms`: deterministic percentile of captured experiment latency, integer `>=0`.
- `evidence_completeness`: eligible evidence units with valid Release entry and revision matches divided by required evidence units, range `[0,1]`.

### 6.2 Hard invariants versus waivable metrics

These are hard, non-waivable invariants:

- tenant/dataset/release/channel/policy scope matches at every join;
- Release Manifest and Entry digests match the referenced Release;
- Release readiness is provable and not `unavailable`;
- every selected experiment is inside the signed tenant/dataset scope;
- every selected experiment’s strategy/result/evidence generation is internally consistent and matches the Manifest’s captured generation;
- every evidence citation maps to an Entry revision/content digest in the Release;
- every selected judgment belongs to the selected experiment/result rank and its captured revision is valid;
- no selected source row is missing or inaccessible;
- no raw secret, credential, body, token, ticket, or idempotency key is present in quality facts;
- no stale policy binding, gate revision, or approval snapshot is used.

Threshold failures such as insufficient sample size, low agreement, high degraded rate, or low relevance may be waivable only when the policy threshold explicitly allows it and the requested compensating controls are recorded.


## 7. Certification capture algorithm

The quality certification mutation should be implemented as a single tenant-idempotent transaction, with a deterministic lock/read order:

```text
Tenant
-> Dataset
-> Release Manifest
-> Release Channel
-> active policy binding
-> policy version and threshold rows
-> selected RetrievalExperiments
-> selected RetrievalJudgments
-> current gate projection (if present)
```

The service performs the following steps:

1. Validate actor, tenant, Dataset scope, request ID, safe reason, and `Idempotency-Key`.
2. Reserve the generic tenant mutation key using an operation such as `create_release_quality_certification`. A matching completed request replays the sanitized result; a different request hash returns conflict; a pending reservation returns in-progress.
3. Lock and validate the Release Manifest, exact lowercase `manifest_digest`, Channel, active policy binding, policy revision, and expected policy-binding revision supplied by the caller.
4. Revalidate the current Release authority using the existing Stage 19 snapshot logic. Do not treat a stored `readiness_state=ready` as proof that the current authority is still ready.
5. Read only selected experiments. For each experiment, require tenant/dataset scope, immutable identity, valid status, valid snapshot fences, valid evidence lineage, and a Release-entry match for every cited document/chunk revision and content digest.
6. Read selected judgments under lock. Require experiment/rank/document/chunk consistency and capture the exact current revision. Compute a canonical safe judgment snapshot and digest. Do not copy raw notes.
7. Recompute all metric values server-side from the selected immutable experiment snapshots and copied judgment facts. Never accept client metric values.
8. Create the immutable certification envelope, metric rows, evidence rows, and `certification_created` event atomically. The certification digest is calculated from the Release digest, policy digest, policy-binding revision, captured authority revisions, canonical metric rows, and ordered evidence digests.
9. Create or update the current gate projection and append `gate_evaluated` plus `gate_passed`, `gate_failed`, `gate_blocked`, or `gate_unavailable`. A gate projection update uses its own expected revision.
10. Write Dataset-scoped `KnowledgeAuditEvent` and tenant-level `TenantAuditEvent` records with safe IDs/digests/counts. Complete the idempotency ledger only after all rows are flushed and the audit facts are part of the same transaction.

A certification with no eligible data is `blocked` or `unavailable`; it is never a synthetic passing certificate. Existing Release rows remain untouched until this explicit action runs.

## 8. Publish-gate and Release/Channel integration

### 8.1 Promotion precondition

The existing Stage 19 promote operation should gain quality fence inputs in its request/approval snapshot:

```text
expected_quality_gate_revision
expected_quality_gate_digest
expected_certification_id
expected_certification_digest
```

Before changing a Channel binding, the promotion transaction must lock and revalidate:

- current Release Manifest ID and `manifest_digest`;
- current Channel ID, status, risk tier, and Channel revision;
- current Dataset/Workspace/ownership authority and promotion serving fence;
- active Dataset/Channel policy binding and policy-binding revision;
- current quality gate projection and its `decision_digest`;
- certification digest, policy digest, metric rows, evidence digest, and validity window;
- current mutable judgment revisions/digests against the copied certification evidence;
- waiver event state and expiry, if a waiver is involved.

The operation must reject stale or missing quality facts with a conflict/unavailable response. It must not “try promotion and let quality catch up later.”

### 8.2 Allowed promotion outcomes

- `passed` gate, no waiver: direct promotion is allowed for a low-risk non-default Channel, subject to existing Stage 19 permissions/fences.
- `passed` gate to a high-risk or default-serving Channel: existing `knowledge_base_release_publish` approval remains required.
- `waived` gate: approval is always required, even for a low-risk Channel. The waiver approval and any high-risk/default publish approval must be distinct approval requests and distinct execution facts.
- `failed`, `blocked`, `expired`, `revoked`, or `unavailable` gate: promotion is rejected. A client cannot set `force=true` to bypass it.

The quality event `release_promoted_with_quality_gate` is appended in the same transaction as the Stage 19 Channel binding event. If the implementation cannot share a transaction boundary, it must use a durable command/receipt/outbox and reconciliation state; a synchronous call that can leave “Release promoted but quality event failed” is not acceptable.

### 8.3 Rollback

A rollback target must have its own valid current gate for the target Channel and policy. A source Release’s certification or waiver must never be inherited by the rollback target. The target must be `passed` or have a separately approved, still-valid waiver. The rollback approval snapshot must carry the target certification/gate digests and the current Channel/serving fences.

### 8.4 Application binding and pinned-release bypass

`follow_channel` resolves the Channel’s current quality gate at serving/promotion time. A pinned Application reference must not bypass quality merely because Stage 19 already checks `readiness_state=ready`.

The recommended 0030 extension adds nullable `pinned_quality_gate_id` and `pinned_quality_gate_digest` to `app_dataset_references` and extends the XOR invariant:

```text
follow_channel:
  release_channel_id IS NOT NULL
  pinned_release_id IS NULL
  pinned_quality_gate_id IS NULL

pinned:
  release_channel_id IS NULL
  pinned_release_id IS NOT NULL
  pinned_quality_gate_id IS NOT NULL
  pinned_quality_gate_digest IS NOT NULL
```

A pinned reference is accepted only when the referenced gate is current, `passed` or validly `waived`, not expired/revoked, and scoped to the same tenant, Dataset, Release, and policy. The runtime serving guard should recheck the gate digest before serving a pinned Release; a stale gate must fail closed rather than silently serving an unqualified pin.

## 9. Approval-aware waiver flow

### 9.1 New approval action

Extend the approval action allowlist with:

```text
knowledge_base_quality_waiver
```

A future policy-change approval may be added separately, but it is not needed to establish the Stage 20 waiver contract.

The approval request snapshot must contain only canonical safe facts:

```text
action_type = knowledge_base_quality_waiver
resource_type = knowledge_base_quality_gate
resource_id = gate_id
tenant_id, dataset_id, release_id, channel_id
manifest_digest
policy_version_id, policy_digest, policy_binding_revision
certification_id, certification_digest
quality_gate_revision, quality_gate_digest
failed_metric_digest
requested_expires_at
compensating_controls_digest
reason (safe, bounded, credential-rejected)
```

It must not contain a raw approval ticket, raw Idempotency-Key, document body, query text, source credential, or unbounded exception message.

### 9.2 Issuance and application

1. A manager with Dataset/quality manage permission submits a waiver request with an exact gate revision/digest and a finite expiry.
2. The service locks the gate, certification, policy binding, and Release authority, verifies that every failed item is policy-waivable, and writes the immutable waiver row plus `waiver_requested` event.
3. For `approval_required`, it creates a generic tenant approval request using the snapshot above. The request is manager-approved by an eligible approver who is not the requester.
4. The approval consumer issues the one-time opaque `ApprovalExecutionFact`. Stage 20 extends that fact with `quality_gate_id`, `quality_gate_revision`, `quality_gate_digest`, `certification_id`, `certification_digest`, `policy_version_id`, `policy_digest`, `waiver_id`, and `waiver_digest` (or an equivalent versioned quality namespace).
5. The quality mutation validates the fact against the locked approval request, its payload hash/snapshot hash, the exact waiver row, and the current gate revision. A forged mapping or caller-supplied dictionary is rejected.
6. The service appends `waiver_approved` and `waiver_applied`, moves the gate projection to `waived` with a new revision, and records the approval execution ID in the gate/event/audit facts.
7. Promotion consumes that exact gate/waiver identity. The waiver is single-use for the specified Release/Channel gate; a later Channel promotion, Release, or policy revision requires a new certification/waiver.

Any waiver expiry is checked against the database clock at gate read and promotion time. A background expiry worker is useful but not authoritative. The gate must become `expired`/blocked even if no worker has run.

### 9.3 Non-waivable conditions

The service rejects waiver creation for:

- `unavailable` authority or missing quality tables;
- cross-tenant or cross-Dataset scope mismatch;
- Manifest digest/Entry revision mismatch;
- forged/missing evidence lineage;
- invalid or missing source experiment/judgment rows;
- security, ACL, credential, or data-integrity blockers;
- an expired or already consumed waiver;
- a gate whose certification or policy binding is stale;
- a waiver that would outlive the policy maximum or has no compensating controls.

This keeps waiver as documented risk acceptance for a known metric deficit, not as a production bypass switch.

## 10. Tenant isolation and data safety

### 10.1 Relational isolation

Every new table begins indexes and composite keys with `tenant_id`. Required composite relationships include:

```text
(tenant_id, dataset_id) -> datasets
(tenant_id, channel_id) -> tenant_release_channels
(tenant_id, release_id) -> dataset_release_manifests within Dataset scope
(tenant_id, policy_version_id) -> tenant_quality_policy_versions
(tenant_id, certification_id) -> dataset_release_quality_certifications
(tenant_id, gate_id) -> dataset_release_quality_gates
(tenant_id, waiver_id) -> dataset_release_quality_waivers
(tenant_id, source_experiment_id, dataset_id) -> retrieval_experiments
(tenant_id, source_judgment_id, dataset_id) -> retrieval_judgments
```

The application must still include tenant and Dataset predicates on every SELECT/UPDATE. Composite FKs protect accidental cross-scope writes but are not a reason to omit service-level authorization.

### 10.2 Read authorization

Quality reads require the signed actor tenant and Dataset read permission. A missing/foreign Release, gate, policy, certification, evidence, or waiver must return the same not-found projection as the existing Knowledge API where existence disclosure is a concern. Admin/manage permission is required for policy binding, certification capture, gate evaluation, waiver request, and pin/publish mutations.

### 10.3 Safe projections

The canonicalizer must reject or redact credential-like values by value, not only by field name. It must reject password/key assignments, bearer/basic values, JWT-like tokens, database URLs with credentials, secret/vault URLs outside an explicit reference field, raw cookies, raw ticket values, and raw Idempotency-Key values. Quality audit snapshots contain IDs, digests, counts, revisions, status codes, and safe reasons only.
