# Enterprise Knowledge Serving & Reliability Center v1.0 Design

Date: 2026-08-30  
Status: Self-approved under standing user authorization  
Stage: 26  
Revision: `0036_enterprise_knowledge_serving_reliability`  
Down revision: `0035_enterprise_automation_workflows`

## Problem

RAG4C already has authoritative Sources, ingest attempts, Documents, Chunk revisions, Index Operations, Knowledge Base Registry, Releases, Quality certification, Tasks and Automation. Operators can inspect each domain separately, but there is no Tenant-scoped authority that answers one operational question:

```text
Can this Knowledge Base reliably serve the intended knowledge right now,
and which exact stage or evidence blocks it?
```

The existing Overview is a resource summary, Monitor is run-centric, Task Center is a cross-domain task projection, Release Center is release-centric, and Retrieval Quality is an experiment/judgment authority. None is the current serving-reliability control plane. Stage26 must not copy those authorities or mutate their source facts.

## Decision

Build an Enterprise Knowledge Serving & Reliability Center using a verified observation architecture:

```text
Source / Parse / Chunk / Index / Release / Quality authorities
    -> explicit read adapters
    -> immutable serving snapshot
    -> five immutable stage facts
    -> bounded internal evidence links
    -> immutable reliability event evidence
    -> Serving Center read model and safe handoff
```

Signature UI:

```text
SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE
```

The center is a Knowledge Base Resource Shell section, not a new top-level product silo.

## Authority boundary

### Source domains remain authoritative

- `data_sources` and Source Sync own source connection and sync state;
- `document_ingest_attempts` owns parse attempt facts;
- Documents and Chunk Heads/Revisions own content and chunk lifecycle;
- Index Operations own projection attempts;
- Knowledge Base Release owns Release/Channel facts;
- Release Quality owns certification and quality gates;
- Task Operations owns task projections;
- Retrieval Quality owns experiments and judgments.

Stage26 only records a canonical observation of those facts. It does not update, retry, cancel, publish, certify, sync, index or execute a query.

### Forbidden surfaces

No arbitrary:

```text
SQL / expression / query language
URL / webhook / external route
raw source payload / document body / answer body
prompt / token / credential / ticket
reflection dispatch / dynamic import / function name
```

No public generic observe, ingest, execute or dispatch endpoint.

## Initial stages

Exactly five ordered stages:

```text
source
parse
chunk
index
serve
```

Stage states:

```text
ready
lagging
blocked
missing
unavailable
```

Overall snapshot states:

```text
ready
degraded
blocked
unavailable
```

The overall state is derived from the five canonical stage facts. It is not accepted as an unverified caller assertion.

## Database — exactly six new tables

### `tenant_knowledge_serving_profiles`

One operator-owned reliability profile per Tenant/Dataset lifecycle.

Key columns:

```text
id / tenant_id
workspace_id / dataset_id
name / normalized_name
status                       draft | active | paused | archived
active_profile_key
revision
current_policy_revision_id
current_snapshot_id
created_at/by / updated_at/by / archived_at/by
```

Canonical identities:

```text
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, dataset_id)
UNIQUE(tenant_id, active_profile_key)
```

Active key is the Dataset identity. Current policy and snapshot pointers use Tenant/Profile-owned triple FKs; the Dataset uniqueness prevents parallel profiles for the same Tenant/Dataset.

### `tenant_knowledge_serving_policy_revisions`

Immutable bounded evaluation policy.

```text
id / tenant_id / profile_id / revision
max_source_staleness_seconds
max_parse_lag_seconds
max_index_lag_seconds
max_failed_document_count
max_pending_index_count
require_current_release
require_passing_certification
policy_digest
created_at / created_by
```

`UNIQUE(tenant_id, profile_id, id)` and `UNIQUE(tenant_id, profile_id, revision)`. UPDATE/DELETE guards are mandatory.

### `tenant_knowledge_serving_snapshots`

Immutable observation of one profile against one policy revision.

```text
id / tenant_id / profile_id / policy_revision_id
observation_key / state
source_count / ready_source_count / stale_source_count
active_document_count / failed_document_count / pending_index_count
expected_serving_generation / observed_serving_generation
current_release_id / current_certification_id
stage_count / ready_stage_count / blocked_stage_count
snapshot_digest
as_of / created_at / created_by
```

`UNIQUE(tenant_id, profile_id, id)` supports profile-owned pointers. The policy FK is `(tenant_id, profile_id, policy_revision_id)`. `observation_key` and `snapshot_digest` are replay fences. A snapshot is append-only.

### `tenant_knowledge_serving_stage_facts`

Exactly five immutable facts per snapshot.

```text
id / tenant_id / profile_id / snapshot_id
stage_code / sequence / state
item_count / ready_count / warning_count / pending_count / error_count / lag_seconds
expected_revision / observed_revision
expected_digest / observed_digest
safe_error_code / safe_error
stage_digest / observed_at
```

Identity:

```text
UNIQUE(tenant_id, snapshot_id, stage_code)
UNIQUE(tenant_id, snapshot_id, sequence)
```

Sequence must be 1..5 and correspond exactly to source/parse/chunk/index/serve.

### `tenant_knowledge_serving_evidence_links`

Immutable bounded references from a Stage Fact to an existing internal authority.

```text
id / tenant_id / profile_id / snapshot_id / stage_fact_id
evidence_kind
resource_id / resource_revision / resource_digest
route_code / safe_label
evidence_digest / created_at
```

Initial evidence kinds:

```text
source
source_sync_run
document
ingest_attempt
chunk_head
index_operation
release
certification
task
```

Initial route codes are internal and allow-listed only. No URL column exists.

### `tenant_knowledge_serving_events`

Immutable hash chain per Profile/Snapshot.

```text
id / tenant_id / profile_id / snapshot_id
stream_key / sequence / event_type
previous_event_digest / event_digest
actor_id / request_id
safe_snapshot_json / occurred_at
```

Initial event types:

```text
profile_created
policy_revision_created
policy_activated
snapshot_recorded
stage_degraded
stage_blocked
service_recovered
```

UPDATE/DELETE guards and insert-time predecessor validation are mandatory.

## Canonical authority

Pure core functions own:

- profile identity normalization;
- policy revision projection and digest;
- stage fact projection and digest;
- evidence-link projection and digest;
- snapshot projection, derived state and digest;
- event digest and chain;
- safe internal route projection;
- exact safe mapping limits shared with Stage25 principles.

The service and readiness layer must call these functions rather than reproduce digest logic.

## Observation service

An internal explicit adapter registry provides bounded current facts:

```text
source
parse
chunk
index
serve
```

Adapters only read known ORM tables. They never import a table/class/function by caller-supplied name.

`record_knowledge_serving_snapshot()`:

1. validates active Tenant actor and profile;
2. captures the current policy revision;
3. runs the five adapters in stable order;
4. canonicalizes five Stage Facts;
5. derives overall state;
6. computes observation/snapshot digests;
7. inserts immutable Snapshot, Stage Facts, Evidence Links and Events;
8. updates only the Profile's current snapshot pointer with a revision fence;
9. is replay-safe and concurrency-safe.

No public HTTP route calls this operation in Stage26. Tests may invoke it only against temporary SQLite fixtures. Browser acceptance uses `route.fulfill` fixtures.

## API

Approved authenticated routes:

```text
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/summary
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/profile
POST /api/enterprise/knowledge-bases/{dataset_id}/serving/profile
POST /api/enterprise/knowledge-bases/{dataset_id}/serving/profile/revisions
POST /api/enterprise/knowledge-bases/{dataset_id}/serving/profile/activate
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots/{snapshot_id}
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/stage-facts
GET  /api/enterprise/knowledge-bases/{dataset_id}/serving/events
POST /api/enterprise/knowledge-bases/{dataset_id}/serving/preview
```

Profile/policy mutations require manager permission, `Idempotency-Key`, expected revision/digest and safe reason. Preview is zero-write and returns no raw source payload.

There is no public snapshot-record, reconcile, observe, retry, sync, index, publish or query-execute endpoint.

## Frontend

Knowledge Base Resource Shell section:

```text
serving
```

Route examples:

```text
/enterprise/knowledge-base?dataset=<id>&section=serving
#/enterprise/knowledge-base?dataset=<id>&section=serving
```

Composition:

```text
Serving authority header
Governance Evidence Strip
SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE rail
Status / source / revision / owner / keyword filters
Dense source/pipeline table on desktop
Priority cards on 375px / 280px
Single detail Drawer:
  Overview / Evidence / Pipeline / Serving Policy / History
Profile Policy Dialog + zero-write Preview panel
```

TDesign React and TDesign Icons are mandatory for core UI. ReactFlow/canvas is not required; the lifecycle must remain understandable in semantic DOM.

Safe handoffs are internal only:

- Sources;
- Documents;
- Task Center;
- Release Center;
- Quality certification;
- Retrieval Debug / Quality experiment pages without copying their data.

## Context and concurrency fence

Frontend requests must bind to:

```text
tenantId + accountId + datasetId + capability state + readOnly state
```

All summary/profile/snapshot/stage/event requests receive `AbortSignal`. Context changes immediately clear old authority state. Mutation and detail queues capture generation and cannot commit stale responses.

## Readiness / preflight

Capability:

```text
enterprise_knowledge_serving_reliability
```

Readiness proves:

- all six tables and exact constraints;
- Tenant-leading FKs and pointer ownership;
- immutable policy/snapshot/stage/evidence/event guards;
- exact stage/state/evidence/event allow-lists;
- policy, stage, evidence, snapshot and event digests;
- exactly five ordered Stage Facts per Snapshot;
- Snapshot counters agree with Stage Facts;
- current pointers belong to the same Tenant/Profile;
- Event hash chains are complete;
- parent Source, Registry, Release, Quality and Task capabilities are not malformed;
- unknown dialect fails closed.

Preflight is read-only and reports six-table counts and integrity blockers. It never records a snapshot or advances source state.

## Downgrade

Downgrade is blocked when any Stage26 authority table is nonempty. Empty downgrade removes guards, tables and the capability cleanly.

## Safety

Implementation and acceptance must not execute:

- production migration or backfill;
- snapshot observation against production data;
- Source sync, parse, chunk or index mutation;
- Release promote/rollback;
- quality certification or recertification;
- Task retry/cancel;
- Automation Run/Action dispatch;
- query execution, external request or webhook;
- purge/delete.

Playwright is loopback-only with controlled fixtures and source hashing.

## Acceptance

Required evidence:

- migration/ORM/catalog/readiness and SQLite upgrade/downgrade tests;
- pure core canonical tests;
- service/API Tenant isolation and strict response tests;
- frontend strict model/API/hook/component tests;
- cumulative catalog/readiness and core integration suites;
- full manifest-bound frontend runner and production build;
- Playwright direct/hash × light/dark × 1440/375/280 matrix;
- bounded scenarios for five stages, degraded/blocked/unavailable/empty/read-only, policy preview, detail evidence, safe handoff, Dataset context switch and keyboard focus;
- zero external requests, production actions, console errors, leaks and horizontal overflow;
- independent security review with zero Critical/Important.
