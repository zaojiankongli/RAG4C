# Enterprise Knowledge Operations & Feedback Center — Stage 27 Design

Date: 2026-08-30
Stage: 27
Revision: `0037_enterprise_knowledge_operations_feedback`
Down revision: `0036_enterprise_knowledge_serving_reliability`
Status: self-approved recommended design

## Product intent

Stage 27 adds an enterprise operations authority for how a governed Knowledge Base is actually used after it becomes serveable. It combines Tencent-style application operations with RAG4C's Knowledge Lifeline without persisting raw questions, raw answers, prompts, credentials, user identities, or document bodies.

Signature:

```text
ASK -> RETRIEVE -> ANSWER -> FEEDBACK -> IMPROVE
```

This is not another chat page, retrieval experiment runner, or metrics dashboard. It is the durable Tenant/Dataset-scoped operating record for:

- conversation/session metadata;
- query and answer outcome facts;
- explicit/implicit feedback;
- low-quality review workflow;
- bounded improvement candidates;
- auditable human decisions.

## Selected approach

### Recommended — durable facts plus human review workflow

Persist privacy-safe immutable query/feedback facts, then manage mutable review cases and improvement candidates with revision fences and immutable event evidence.

Why selected:

- raw JSONL query history is not Tenant-safe or relationally governed;
- Prometheus counters cannot explain one low-quality answer;
- retrieval experiments are temporary comparison facts, not production operations;
- automatic edits to QA/document/index authority would violate existing ownership boundaries.

### Rejected — analytics only

A chart-only page would look polished but would not provide a trustworthy database or review lifecycle.

### Rejected — store full conversations

Raw question/answer persistence creates avoidable PII, secret, prompt and content-retention risk.

## Authority boundaries

```text
/api/query and /api/query/stream
  -> safe Stage27 observation adapter
  -> immutable session/query facts
  -> optional feedback facts
  -> explicit review case
  -> bounded improvement candidate
  -> safe handoff to Documents / QA / Sources / Retrieval Quality / Tasks
```

Stage27 never:

- records raw query or raw answer bodies;
- stores prompts, tokens, credentials, headers, ACL values or arbitrary metadata;
- changes QA knowledge, Document/Chunk Head, Source cursor or Index state;
- runs retrieval experiments automatically;
- dispatches Tasks/Automations/Approvals;
- turns feedback into production knowledge without a separate governed action.

## Database — exactly seven new tables

### 1. `tenant_knowledge_operations_profiles`

One active operations profile per Tenant/Dataset.

Key fields:

```text
id / tenant_id / workspace_id / dataset_id
status                       draft | active | paused | archived
retention_days               bounded
sampling_basis_points        0..10000
safe_preview_enabled
review_sla_minutes
revision
active_profile_key
created/updated/archived facts
```

Identity:

```text
UNIQUE(tenant_id,id)
UNIQUE(tenant_id,dataset_id)
UNIQUE(tenant_id,active_profile_key)
```

### 2. `tenant_knowledge_conversation_sessions`

Immutable/minimally mutable session envelope; no user ID or transcript.

```text
id / tenant_id / profile_id / dataset_id
session_key_digest
channel_code                 web | api | wecom | dingtalk | custom
actor_subject_digest         optional HMAC digest
query_count / feedback_count
started_at / last_observed_at / expires_at
session_digest
```

### 3. `tenant_knowledge_query_facts`

Immutable production query outcome fact.

```text
id / tenant_id / profile_id / session_id / dataset_id
request_id_digest / query_digest / answer_digest
safe_query_preview           optional, redacted, <=160 UTF-8 bytes
route_code                   rag | cache | fallback | abstain | changed
outcome_code                 answered | abstained | cancelled | failed | knowledge_changed
retrieval_count / citation_count
retrieval_ms / generation_ms / total_ms
cached / retry_used / serving_generation
trace_digest / fact_digest
observed_at
```

No raw query, answer, citation text, document body, prompt or trace JSON.

### 4. `tenant_knowledge_feedback_facts`

Immutable feedback observation.

```text
id / tenant_id / profile_id / session_id / query_fact_id
feedback_kind                helpful | unhelpful | correction | unsafe | incomplete
source_code                  explicit | operator | policy | implicit
reason_code                  allow-listed only
safe_comment_preview         optional, redacted
feedback_digest
actor_id                     optional enterprise operator only
observed_at
```

### 5. `tenant_knowledge_review_cases`

Mutable revision-fenced human review workflow.

```text
id / tenant_id / profile_id / dataset_id / query_fact_id
case_key
priority                     low | medium | high | critical
issue_type                   no_recall | weak_recall | citation_gap | wrong_answer |
                             outdated_knowledge | unsafe_answer | refused | latency
status                       open | triaged | investigating | resolved | dismissed
assignee_id / due_at
revision
resolution_code / safe_resolution_summary
created/updated/closed facts
```

### 6. `tenant_knowledge_review_events`

Immutable hash chain per Review Case.

```text
id / tenant_id / case_id / sequence
event_type                   case_created | triaged | assigned | status_changed |
                             candidate_linked | resolved | dismissed
previous_event_digest / event_digest
actor_id / request_id
safe_snapshot_json
occurred_at
```

### 7. `tenant_knowledge_improvement_candidates`

Governed suggestions only; never auto-applied.

```text
id / tenant_id / profile_id / dataset_id
candidate_key / candidate_type
candidate_type               qa_gap | document_gap | source_gap | retrieval_tuning |
                             citation_policy | refusal_policy
status                       proposed | accepted | rejected | converted | archived
query_cluster_digest
supporting_fact_count / negative_feedback_count
safe_title / safe_summary
linked_case_id / target_route_code / target_resource_id
revision
created/updated/decided facts
```

## Database invariants

- Every FK is Tenant-leading.
- Session, Query, Feedback, Review and Candidate ownership is Profile/Dataset consistent.
- Immutable Fact/Event tables have UPDATE/DELETE guards.
- Review/Applicant mutations are idempotent and revision fenced.
- All digests are lowercase SHA-256 with domain separation.
- Event chain starts at sequence 1 with `case_created` and is contiguous.
- Safe previews reject URI, SQL, prompt, token, secret, credential and control content.
- Retention is explicit; expiration disables UI visibility before a separate purge authority exists.
- Unknown database dialect fails closed.

## API surface

Authenticated read routes:

```text
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/summary
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/sessions
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/query-facts
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/reviews
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/reviews/{case_id}
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/candidates
GET /api/enterprise/knowledge-bases/{dataset_id}/operations/activity
```

Approved mutations:

```text
POST /operations/profile
POST /operations/feedback
POST /operations/reviews
POST /operations/reviews/{case_id}/triage
POST /operations/reviews/{case_id}/resolve
POST /operations/candidates
POST /operations/candidates/{candidate_id}/decision
```

Internal-only adapter:

```text
record_query_fact(...)
```

It is called after a completed query delivery and is not mounted as public HTTP.

## Frontend placement

```text
/enterprise/knowledge-base?dataset=<id>&section=operations
#/enterprise/knowledge-base?dataset=<id>&section=operations
```

Knowledge Base Resource Shell adds `Operations / 运营`.

Desktop composition:

```text
Operations authority header
Operational Evidence Strip
ASK -> RETRIEVE -> ANSWER -> FEEDBACK -> IMPROVE rail
Time / channel / outcome / feedback / issue / owner filters
Dense TDesign table: Query Fact / Outcome / Evidence / Feedback / Review / Time
Single Review Drawer
Improvement Candidate side panel
```

Mobile composition:

```text
Priority evidence cards
Time and channel summary
Filter Drawer
Conversation/query cards
Full-screen Review Drawer
One governed action per screen
```

## Visual system

Palette:

```text
Authority Blue     #0052D9
Operations Cyan    #00A4A6
Attention Amber    #ED7B2F
Critical Red       #D54941
Ink                #1F2329
Canvas              #F3F6FA
```

Typography uses the existing enterprise sans stack, with monospace only for digests, request IDs and revisions.

Signature element: the five-step Operational Learning Rail. It visualizes where negative evidence accumulates without becoming a decorative funnel.

TDesign React and TDesign Icons are mandatory for controls, tables, tags, tabs, drawers, dialogs, date ranges, pagination and accessibility. Uiverse/Morphicons may only supplement the empty improvement-candidate illustration.

## Context and concurrency

Frontend authority key:

```text
tenantId + accountId + datasetId + capability + readOnly + active + timeRange
```

Context changes abort requests and clear Session/Query/Review/Candidate details before new authority loads.

## Readiness

Capability:

```text
enterprise_knowledge_operations_feedback
```

Readiness proves:

- seven exact tables/columns/checks/indexes/FKs;
- immutable guards and event function bodies;
- one active Profile per Dataset;
- Session/Query/Feedback/Profile ownership;
- Review Case revision and status invariants;
- Event chain completeness;
- Candidate target route allow-list;
- digest recomputation for all immutable facts;
- no raw/protected columns;
- parent Serving, Registry, Task and Retrieval Quality capabilities are not malformed;
- unknown dialect fails closed.

## Acceptance

- migration/ORM/catalog/readiness tests;
- pure canonical and safe projection tests;
- service/API Tenant isolation, idempotency and response semantics;
- query delivery adapter proves no raw query/answer persistence;
- TDesign desktop/mobile component tests;
- direct/hash, light/dark, 1440/375/280 Playwright matrix;
- scenarios: summary, sessions, low-quality filter, review detail, feedback, candidate, empty, read-only, unavailable, Dataset switch, focus return;
- zero external network, production query, Source/Index/Release mutation, Task/Automation/Approval dispatch;
- independent security review Critical 0 / Important 0.
