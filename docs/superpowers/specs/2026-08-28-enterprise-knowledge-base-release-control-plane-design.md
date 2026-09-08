# Enterprise Knowledge Base Release Control Plane Design

**Date:** 2026-08-28  
**Status:** Approved by user continuation on 2026-08-28  
**Stage:** 19  
**Target revision:** `0029_enterprise_knowledge_base_releases`  
**Reference:** `docs/research/2026-08-28-tencent-knowledge-base-stage19-reference.md`

## 1. Decision summary

Stage 19 builds a first-class **Knowledge Base Release Control Plane**.

It combines two parts that must share one authority model:

1. a persistent Knowledge Base resource shell that unifies the existing Documents, Taxonomy, Sources and Governance workspaces without duplicating their data requests or mutation forms;
2. immutable Release Manifests, extensible Release Channels, Application release binding and auditable promotion/rollback.

The Registry remains the enterprise resource list and quick-inspection surface. The resource shell becomes the sustained workspace for one Knowledge Base. A Release is a reproducible snapshot of the exact knowledge facts an Application or serving channel is allowed to consume.

## 2. Why Release comes before quality certification

`Dataset.profile_revision`, `mutation_generation` and `serving_generation` are concurrency/runtime facts, not reproducible deployment objects. Current Retrieval Experiments and reviewer judgments are attached to a mutable Dataset/serving generation. If quality certification were implemented first, a later content or policy change could make the certification target ambiguous.

A Release Manifest supplies the stable identity required by later capabilities:

```text
immutable release
  -> release-linked quality baseline and certification
  -> publish gate and waiver
  -> retention-aware retirement and destruction
```

Therefore Stage 19 establishes Release authority; Stage 20 can certify that Release without creating a second experiment runner.

## 3. Alternatives considered

### Alternative A — UI-only Knowledge Base shell

Fast visual improvement, but no database reproducibility, promotion safety or rollback. The shell remains part of the chosen design but is not sufficient alone.

### Alternative B — Mutable settings center

RAG4C already stores Dataset policy/profile values and exposes them through Governance. A second editor would duplicate controls while continuing to mutate live configured state. Rejected.

### Alternative C — Release Manifest + Release Channel + resource shell

Creates a stable enterprise deployment object, reuses existing immutable facts, supports custom lanes and produces a coherent UI. Recommended and approved.

## 4. Domain boundaries

### 4.1 Workspace environment is not a Release Channel

`TenantWorkspace.environment` is an organizational/workspace classification currently constrained to development/testing/production. It does not prove where a Knowledge Base Release is deployed and must never be inferred as a Release binding.

Release Channels are a separate Tenant authority. A Tenant receives default channels named `development`, `testing` and `production`, but may add channels such as `uat`, `staging-cn`, `staging-eu` or `regulated-production`.

### 4.2 Application has no implicit environment

The current `App` authority has identity and kind but no deployment environment. Stage 19 therefore does not invent an App environment. Every active App → Dataset reference explicitly chooses one of:

```text
follow_channel(channel_id)
pinned(release_id)
```

### 4.3 Manifest content immutability vs lifecycle

Manifest content, entries and digest are immutable. Publication, supersession, rollback and retirement are represented by append-only Release events and mutable channel-binding rows; they do not rewrite manifest content.

## 5. Release Manifest snapshot

A Manifest captures, inside one consistent database snapshot:

- Dataset profile revision and canonical policy digest;
- Dataset mutation/serving generations;
- owning Workspace ID, Workspace revision and ownership revision;
- every effective retrieval-enabled Document's current immutable Document Version ID and source hash;
- Document content, desired-index, indexed and graph revisions;
- every effective approved QA row's ID, revision and canonical content digest;
- every active Source's configuration fingerprint, mutation generation and canonical redacted cursor/result digest;
- required Projection target/current revision evidence;
- manifest entry count, canonical serialization version and SHA-256 digest;
- creator, reason, request ID and capture timestamp.

Manifest capture uses deterministic lock/read order:

```text
Tenant
-> Dataset
-> ownership + owning Workspace
-> Dataset profile/policies
-> Documents + current Document Versions
-> effective QA
-> Sources + active runs
-> Projection operations
```

SQLite uses the existing writer lock/transaction pattern; MySQL uses deterministic `SELECT ... FOR UPDATE` where required. Revisions are rechecked before insert. A changed fact aborts capture rather than producing a mixed snapshot.

No document body, QA answer body, secret value or credential-bearing cursor is stored in Release entries. Entries store IDs, revisions and canonical digests; safe bounded facts are allowed.

## 6. Database authority

### 6.1 `tenant_release_channels`

Tenant-scoped, extensible release lanes:

- `id VARCHAR(128)`;
- `tenant_id VARCHAR(64)`;
- `code VARCHAR(64)` and normalized unique code;
- `name VARCHAR(128)`;
- `status active | archived`;
- `risk_tier low | medium | high`;
- `promotion_order INT >= 0`;
- `is_default_serving BOOLEAN` plus active-default slot uniqueness;
- `revision INT > 0`;
- lifecycle actor/time evidence.

Default channels are development/testing/production. Custom channels are supported. High-risk or default-serving channels use approval by default.

### 6.2 `dataset_release_manifests`

Immutable release envelope:

- Tenant/Dataset scoped ID;
- positive release number;
- profile, ownership, Workspace and generation snapshots;
- schema version;
- manifest/readiness digests;
- readiness state `ready | blocked | unavailable`;
- entry/blocker counts;
- creator/reason/request/timestamp.

Unique constraints:

- `(tenant_id, dataset_id, id)`;
- `(tenant_id, dataset_id, release_number)`;
- `(tenant_id, dataset_id, manifest_digest)`.

Manifest content columns cannot be updated or deleted after creation.

### 6.3 `dataset_release_entries`

Immutable entries with resource type:

```text
dataset_profile
document_version
qa_revision
source_generation
projection_revision
```

Each entry records Release/Tenant/Dataset scope, ordinal, resource identity, revision, optional digest and bounded safe facts. Composite uniqueness prevents duplicate resource identities inside one Release.

### 6.4 `dataset_release_events`

Append-only lifecycle evidence:

```text
candidate_created
promoted
superseded
rolled_back
retired
```

Each event stores Release, optional channel, actor, reason, request ID, occurred-at time and previous/current binding revisions. Events are not updated or deleted.

### 6.5 `dataset_channel_releases`

Current channel binding authority:

- Tenant/Dataset/Channel scope;
- active Release ID;
- previous Release ID;
- status and active-slot uniqueness;
- positive revision;
- activation actor/time/request/reason.

One active binding exists per Tenant + Dataset + Channel.

### 6.6 Compatibility projections

Add to `datasets`:

- `serving_release_id` nullable composite FK;
- `release_revision INT > 0`.

The channel marked `is_default_serving` is projected into these fields. Promotion/rollback of that channel advances `serving_generation`.

Extend `app_dataset_references` with:

- `release_mode follow_channel | pinned`;
- `release_channel_id` nullable;
- `pinned_release_id` nullable;
- exact XOR check and composite Tenant/Dataset FKs.

## 7. Readiness and blockers

Manifest capture and promotion are fail-closed. Required blockers include:

- Dataset not active;
- missing/inactive owning Workspace;
- profile/ownership/Workspace revision drift;
- effective Document without current immutable Version;
- `indexed_revision != desired_index_revision`;
- active durable-delete operation;
- active Source sync or Source generation drift;
- pending/failed/dead-letter required Projection;
- effective approved QA changed during capture;
- unsupported/secret-bearing policy value;
- incomplete Catalog capability.

Unknown evidence is `unavailable`, never ready.

Embedding/vector-model changes are blocked while active Documents, chunks or database references exist unless a separately approved full rebuild path is present.

## 8. Lifecycle, promotion and approval

```text
capture immutable candidate
-> validate readiness
-> promote along Tenant-defined channels
-> supersede previous binding
-> rollback binding when required
-> retire only when no channel or App pin remains
```

Rules:

- capture and mutation requests use canonical idempotency hashes;
- promotion requires Manifest ready, channel active and exact revisions;
- channel risk tier and approval policy determine direct vs approval-required outcome;
- default-serving/high-risk promotion and rollback are approval-required by default;
- approval snapshot contains manifest digest, channel revision, Dataset profile revision, ownership revision and serving generation;
- execution is one-time and rechecks every revision;
- rollback changes bindings/projections only, never Manifest content;
- raw approval tickets never enter result payloads.

Approval actions:

```text
knowledge_base_release_publish
knowledge_base_release_rollback
```

Resource type remains `knowledge_base`.

## 9. API surface

```text
GET  /api/enterprise/release-channels
POST /api/enterprise/release-channels
PATCH /api/enterprise/release-channels/{channel_id}

GET  /api/enterprise/knowledge-bases/{dataset_id}/releases
POST /api/enterprise/knowledge-bases/{dataset_id}/releases
GET  /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}
GET  /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/readiness
GET  /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/impact
POST /api/enterprise/knowledge-bases/{dataset_id}/releases/{release_id}/promote
POST /api/enterprise/knowledge-bases/{dataset_id}/channels/{channel_id}/rollback
GET  /api/enterprise/knowledge-bases/{dataset_id}/release-channels
```

All lists use opaque keyset cursors. Mutations require authenticated actor, Tenant scope, exact revisions, Idempotency-Key, bounded reason, audit and atomic rollback.

## 10. Frontend composition contract

### 10.1 Header ownership

- `WorkspaceScopeBar` remains the single global Tenant/Workspace/actor header.
- `KnowledgeBaseResourceShell` owns the single page-level Knowledge Base heading and compact Dataset context row.
- Embedded Documents, Taxonomy, Sources, Governance and Releases content must not emit another `PageTopbar`.
- Existing pages keep standalone mode for focused tests/legacy entry, but accept an `embedded`/content-only mode.
- At most one sticky header is allowed. The resource context is non-sticky unless it replaces, rather than stacks with, another sticky layer.

Desktop context shows Knowledge Base, lifecycle, owning Workspace, active channel/Release and concise revision/readiness. Mobile context is exactly two rows:

1. Knowledge Base name + lifecycle status;
2. selected channel + active Release or unavailable.

IDs, digests, Workspace and revisions live in a “View authority facts” Drawer.

### 10.2 Canonical route contract

Existing scoped routes remain canonical:

```text
/documents?dataset={id}
/taxonomy?dataset={id}
/sources?dataset={id}
/governance?dataset={id}
```

New hidden resource-workspace route:

```text
/enterprise/knowledge-base?dataset={id}&section=overview
/enterprise/knowledge-base?dataset={id}&section=releases
```

Hash mode mirrors the same path/query after `#`.

Rules:

- `knowledge-base-workspace` is a hidden `PageKey`, not a duplicate main-sider item;
- main sider continues to represent product domains;
- resource navigation represents sections inside the selected Knowledge Base;
- Overview/Releases keep the main sider's Knowledge Base Registry domain active;
- Documents/Taxonomy/Sources/Governance keep their existing main-sider item active;
- browser back/forward and refresh restore Dataset and section;
- Dataset change resets invalid section state;
- resource switching reuses the existing Documents dirty-state leave confirmation;
- cancel returns focus to the resource selector/trigger.

Desktop resource navigation uses TDesign `Tabs`. At 375/280 it uses one visibly labelled TDesign `Select`; a second navigation Drawer is prohibited.

### 10.3 Registry Drawer boundary

Registry Drawer retains only Registry authority:

- identity and revisions;
- owning Workspace and ownership transfer;
- Application reference summary/mutations;
- dependency/archive-blocker summary;
- primary action “Open Knowledge Base workspace”.

It does not add Releases, Manifest entries, promotion, rollback, full impact or audit. The persistent workspace shows Registry facts read-only and deep-links back to the Registry action when mutation is required.

“Open Knowledge Base workspace” closes the Drawer, updates the canonical route and moves focus to the workspace heading.

## 11. Release Center hierarchy

The main Releases section has three layers only.

### Layer 1 — Channel summary

Shows selected channel, active Release, serving generation, drift/readiness and one primary action (`Capture candidate` or `Promote`).

### Layer 2 — Release history

Desktop uses `PrimaryTable`; mobile uses compact cards. One row/card exposes at most one primary action; secondary actions use a menu.

### Layer 3 — Release detail Drawer

TDesign Tabs:

```text
Manifest | Readiness | Impact | Audit
```

Manifest entries use keyset pagination inside the Drawer. Impact and Audit are lazy-loaded. Steps appear only during capture/promote workflow, not permanently.

Configured/Candidate/Effective/Serving are comparison layers. Drifted/Unavailable are overlay Tag/Alert states, not peer lifecycle states.

Promotion/rollback uses one Dialog. A Drawer may be covered by a Dialog, but no second Drawer may open while another Drawer is active.

## 12. Existing component reuse contract

- `WorkspaceScopeBar`: global authority only;
- `PageState`: loading/error/empty/unavailable;
- TDesign `Tabs` and mobile `Select`: resource navigation;
- existing feedback/message system;
- existing Registry focus-return and viewport-bound patterns;
- existing desktop `PrimaryTable` / mobile-card / opaque-cursor pattern;
- existing theme tokens, typography, spacing and radii.

Stage 19 does not copy the Registry's hand-built Button tablist and does not create a new visual token system. Uiverse/Morphicons are used only if TDesign lacks a required interaction/icon.

## 13. Configured vs effective state

The UI distinguishes:

- **Configured:** current Dataset profile/policies;
- **Candidate:** captured immutable Manifest;
- **Effective:** Release bound to selected channel;
- **Serving:** Release bound to Tenant default-serving channel;
- **Drifted:** configured facts changed after capture;
- **Unavailable:** authority cannot be proven.

This authority comparison, rather than a generic settings form, is the RAG4C-specific enterprise signature.

## 14. Security and privacy

- Manifest entries contain no bodies or credentials.
- cursor/result facts are canonical redacted digests, not raw secret-bearing JSON;
- cross-Tenant Release/Channel/App pinning is blocked by composite FKs and service checks;
- manifest content and entries are immutable;
- lifecycle events are append-only;
- promotion/rollback is revision-fenced, idempotent, audited and approval-aware;
- published secrets and raw approval tickets are never serialized.

## 15. Readiness, migration and operations

Revision `0029_enterprise_knowledge_base_releases` extends ORM, Catalog Schema, Enterprise Readiness, manual upgrade preflight/runbook and downgrade blockers.

Migration seeds default release channels for existing Tenants deterministically, but no production migration/backfill is executed automatically by Codex. Preflight remains read-only and reports malformed source/version/projection facts, missing ownership, invalid App release mode and channel uniqueness problems.

## 16. Testing and visual acceptance

Required evidence:

- SQLite/MySQL migration, exact checks, composite FKs, indexes and downgrade blockers;
- custom Release Channels plus default-channel seed;
- strict separation from Workspace environment;
- immutable Manifest/entries and append-only events;
- deterministic digest and consistent snapshot drift refusal;
- effective Document/QA/Source/Projection completeness;
- direct low-risk promotion and approval-required high-risk/default-serving promotion;
- rollback/replay/retirement blockers;
- App follow-channel and pinned Release behavior;
- direct/hash route contract and dirty-state navigation guard;
- one header owner and no duplicated PageTopbar;
- desktop/light/dark/375/280;
- mobile labelled resource/channel Selects;
- Release table/cards, detail Drawer, lazy impact/audit, Dialog focus return;
- zero console/page/unknown/unexpected requests and horizontal overflow;
- no raw secret, Idempotency-Key or approval ticket in DOM/storage/log/result JSON.

## 17. Explicit non-goals

- second Retrieval Experiment runner;
- quality certification/waiver (Stage 20);
- physical Knowledge Base destruction (later retirement stage);
- real production migration, capture, promotion or rollback;
- external model-provider mutation;
- protected Retrieval Quality modification without separate approval.

## 18. Follow-on sequence

```text
Stage 19 — Release Manifest / custom channels / resource shell
Stage 20 — Release-linked quality certification and publish gates
Stage 21 — Retention-aware retirement, legal hold and destruction evidence
```
