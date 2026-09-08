# Stage26 Enterprise Knowledge Serving Database Security

Date: 2026-08-30  
Revision: `0036_enterprise_knowledge_serving_reliability`  
Down revision: `0035_enterprise_automation_workflows`

## Security objective

Create a Tenant-scoped observation authority that can prove the current Knowledge Serving state without becoming a second Source, Document, Index, Release, Quality, Task or Retrieval authority.

Architecture:

```text
existing domain authority
  -> explicit read adapter
  -> immutable Serving Snapshot
  -> five immutable Stage Facts
  -> bounded internal Evidence Links
  -> immutable Event evidence
```

No Stage26 table contains document text, chunk text, answer body, query body, URL, webhook, credential, token, prompt, arbitrary SQL or raw adapter payload.

## Exactly six tables

```text
tenant_knowledge_serving_profiles
tenant_knowledge_serving_policy_revisions
tenant_knowledge_serving_snapshots
tenant_knowledge_serving_stage_facts
tenant_knowledge_serving_evidence_links
tenant_knowledge_serving_events
```

## Tenant ownership

Every table has `UNIQUE(tenant_id, id)`. Every resource relationship uses a Tenant-leading composite FK.

Required examples:

```text
(tenant_id, profile_id)
  -> tenant_knowledge_serving_profiles(tenant_id, id)

(tenant_id, snapshot_id)
  -> tenant_knowledge_serving_snapshots(tenant_id, id)

(tenant_id, stage_fact_id)
  -> tenant_knowledge_serving_stage_facts(tenant_id, id)

(tenant_id, dataset_id)
  -> datasets(tenant_id, id)

(tenant_id, workspace_id)
  -> tenant_workspaces(tenant_id, id)
```

Profile `current_policy_revision_id` and `current_snapshot_id` are also Tenant-leading composite FKs. Readiness verifies that each pointer belongs to the same Profile.

## Mutability

Mutable control row:

- Profile metadata and current pointers, with revision/CAS.

Immutable rows:

- Policy Revision;
- Snapshot;
- Stage Fact;
- Evidence Link;
- Event.

Database guards reject UPDATE and DELETE for all five immutable tables. Service code cannot be the only immutability boundary.

## Exact allow-lists

Database CHECK and readiness exact parsing cover:

- profile status;
- snapshot state;
- stage code;
- stage state;
- evidence kind;
- route code;
- event type.

Approved stage order is exactly:

```text
1 source
2 parse
3 chunk
4 index
5 serve
```

Readiness rejects missing, duplicate, reordered or additional stages.

## Canonical digests

Domain-separated canonical SHA-256 digests:

```text
knowledge-serving-policy
knowledge-serving-stage
knowledge-serving-evidence
knowledge-serving-snapshot
knowledge-serving-event
```

The same pure-core function is used by writers, readers/tests and readiness. No serializer-specific digest implementation is allowed.

A Snapshot digest includes:

- Tenant/Profile/Policy identity;
- observation key and as-of time;
- derived overall state;
- canonical counters/generations;
- ordered Stage Fact digests;
- ordered Evidence Link digests.

## Event chain

Event stream identity is bounded to Profile or Snapshot internal identities. Sequence starts at 1. First event has no predecessor; all later events must reference the exact preceding digest in the same Tenant and stream.

Insert validation and UPDATE/DELETE guards are required for SQLite, MySQL/MariaDB and PostgreSQL. Unknown dialect fails closed.

## Evidence links

Evidence is an internal reference, not a generic polymorphic URL.

Allowed kinds:

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

Allowed route codes are internal and finite. `safe_label` is bounded and scanned for URI, SQL, bearer/JWT and credential-like text. Resource revision/digest fences are paired.

Evidence Link cannot own or rewrite the referenced source authority. Missing referenced facts become `unavailable`/`missing` observation evidence, not cascading deletion.

## Policy revision

Policy values are bounded integers/booleans only. No expression engine exists.

```text
max_source_staleness_seconds
max_parse_lag_seconds
max_index_lag_seconds
max_failed_document_count
max_pending_index_count
require_current_release
require_passing_certification
```

Policy Revision is immutable. Activation updates the Profile pointer with expected Profile revision and expected current policy digest.

## Snapshot integrity

Snapshot state is derived from Stage Facts. Caller-supplied overall state is rejected when it disagrees.

Required invariants:

- exactly five Stage Facts;
- ready/blocked stage counters equal the facts;
- observed serving generation cannot be negative;
- expected/observed revision and digest pairs are structurally valid;
- snapshot profile/policy/stage/evidence ownership is Tenant-consistent;
- current snapshot pointer belongs to the same Profile;
- replay uses a stable observation key/digest and does not duplicate facts/events.

## Adapter boundary

Stage adapter registry is a fixed code registry. A caller cannot submit a table name, ORM name, SQL fragment, URL or function name.

Adapters may read only the existing Source, ingest, document/chunk, index, release, certification and task authorities. They return canonical safe facts. Stage26 never mutates those rows.

The internal snapshot writer is not mounted as public HTTP in v1.

## API boundary

Authenticated reads and Profile/Policy metadata mutations only. No public:

```text
observe
record snapshot
reconcile
source sync
parse
index
publish
certify
query execute
```

Mutation requests require Tenant actor, permission, Idempotency-Key, expected revision/digest and safe reason. Strict Pydantic models use `extra=forbid`.

## Preflight / readiness

Readiness proves schema and data integrity. Preflight opens file SQLite in `mode=ro`, remains SELECT-only and reports six-table counts. It never records an observation.

Parent capabilities are checked only for malformed state. Stage26 must not require data to exist in every parent table in order to represent an honest empty/unavailable state.

## Cross-dialect migration

Supported:

```text
sqlite
mysql
mariadb
postgresql
```

SQLite online migration is tested. MySQL/PostgreSQL DDL is generated offline and inspected. Unsupported dialect raises before partial DDL.

## Downgrade

Any nonempty Stage26 table blocks downgrade. Empty downgrade removes foreign keys/guards in safe order, then the six tables. It never deletes serving evidence to make downgrade pass.

## Acceptance safety

All tests use temporary databases. Browser acceptance uses loopback `route.fulfill`. The run must prove:

```text
production migration = 0
source observation = 0
source/index/release/quality mutation = 0
query execution = 0
external network = 0
source changed during acceptance = false
```
