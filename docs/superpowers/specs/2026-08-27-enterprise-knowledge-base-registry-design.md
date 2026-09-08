# RAG4C Enterprise Knowledge Base Registry Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 18  
**Target revision:** `0028_enterprise_knowledge_base_registry`

## Objective

Make Knowledge Base / Dataset a first-class enterprise resource with a provable owning Workspace, durable Application references, dependency impact evidence, and a formal TDesign resource center. Stage 18 closes the scope gap between the global Workspace selector and the Dataset actually used by KnowledgeOps pages without weakening Dataset ACL or Stage 17 Workspace authorization.

The chosen vertical slice is:

```text
Tenant
  -> owning Workspace
    -> Knowledge Base registry
      -> Application references
      -> Workspace associations
      -> archive readiness / dependency evidence
      -> deep links to Documents, Taxonomy, Sources and Governance
```

Stage 18 does not add physical Dataset deletion, a Release Manifest, or a second task execution authority.

## External design calibration

Fresh public Tencent documentation evidence is stored in:

```text
output/playwright/tencent-knowledge-base-stage18-2026-08-27/
```

The reusable patterns are resource-first information architecture, dense enterprise tables, explicit Workspace isolation, dependency warnings, and resource detail surfaces. RAG4C must not copy Tencent trademarks, screenshots, proprietary assets, DOM, CSS classes, or product wording.

## Chosen data model

### `dataset_workspace_ownerships`

One authoritative ownership row per Dataset:

- `id` — deterministic, opaque identifier;
- `tenant_id`, `dataset_id`, `workspace_id` — tenant-safe scope;
- `revision` — positive optimistic-concurrency fence;
- `created_at`, `created_by`, `updated_at`, `updated_by`;
- `last_transfer_at` — nullable until the first transfer.

Constraints:

- unique `(tenant_id, dataset_id)`;
- unique `(tenant_id, id)`;
- composite FK `(tenant_id, dataset_id) -> datasets(tenant_id, id)`;
- composite FK `(tenant_id, workspace_id) -> tenant_workspaces(tenant_id, id)`;
- actor FKs remain tenant-safe where the current catalog contract supports them;
- `revision > 0`;
- indexes by `(tenant_id, workspace_id, dataset_id)` and `(tenant_id, updated_at, dataset_id)`.

Ownership is not permission. Stage 17 Workspace authorization remains the only Workspace-derived Dataset permission source.

### `app_dataset_references`

Durable Application -> Knowledge Base references:

- `id`;
- `tenant_id`, `app_id`, `dataset_id`;
- `reference_kind = 'knowledge'` in version 1;
- `status = active | removed`;
- `active_slot = 'active' | NULL`;
- `revision`;
- `created_at`, `created_by`, `updated_at`, `updated_by`;
- `removed_at`, `removed_by`;
- `request_id`.

Constraints:

- add unique `(tenant_id, id)` to `apps` for tenant-safe references;
- unique active `(tenant_id, app_id, dataset_id, active_slot)`;
- composite FK to `apps(tenant_id, id)`;
- composite FK to `datasets(tenant_id, id)`;
- exact reference kind/status/revision/lifecycle evidence checks;
- indexes by Dataset, App, status, updated time and id.

An Application reference is a dependency fact, never an authorization grant.

## 0028 backfill

- Create ownership only from a provable active `primary` Workspace-Dataset binding.
- Existing active `shared` bindings remain collaboration/authorization associations and are never treated as ownership.
- Do not fabricate App references from Workflow JSON.
- Missing or ambiguous ownership is reported by Catalog Schema and Enterprise Readiness; modern Registry mutation paths fail closed.
- Preserve existing Dataset, Workspace binding and Stage 17 authorization rows unchanged.
- Extend approval action checks with `dataset_workspace_transfer` without rewriting existing approval facts.
- Downgrade is fail-closed while 0028 ownership transfers, Application references or transfer approval facts remain.

## Knowledge Base registry projection

The authenticated registry list returns server-owned facts only:

- Dataset ID, name, description, status, visibility and `profile_revision`;
- owning Workspace ID/name/status and ownership revision;
- active shared association count;
- active Application reference count;
- document/chunk counts from Dataset authority;
- source count from the relational Source authority;
- updated time;
- archive readiness and blocker counts;
- catalog revision and capability state.

Unknown or unavailable counts are `null`, never fabricated as zero.

Filters:

- `workspace_id`;
- Dataset lifecycle status;
- bounded keyword;
- opaque keyset cursor;
- limit up to 100.

## Core mutations

### Application reference create/remove

- Tenant owner/admin or an actor with the exact Dataset manage permission;
- exact Tenant predicate on App and Dataset;
- Idempotency-Key and canonical request hash;
- active reference uniqueness;
- expected revision for removal;
- audit/business/idempotency in one transaction;
- identical retry replays the original response;
- same key with a different payload returns conflict.

### Workspace ownership transfer

- Tenant owner/admin only;
- target Workspace must be active and in the same Tenant;
- expected Dataset profile revision and ownership revision;
- lock order: Tenant -> Dataset -> ownership -> source/target bindings;
- update `dataset_workspace_ownerships` as source of truth;
- update the active primary Workspace binding compatibility projection in the same transaction;
- retain former Workspace association as `shared` when safe rather than deleting evidence;
- audit before/after ownership and binding facts;
- active matching approval policy returns `dataset_workspace_transfer_approval_required`;
- Approval consumer rechecks Dataset, ownership, Workspace, action, resource, snapshot hash and revisions.

Approval action:

```text
dataset_workspace_transfer
```

Approval resource:

```text
knowledge_base
```

### Archive readiness

Dataset archive is blocked while active Application references exist. Approval cannot bypass this hard dependency blocker. The dependency API returns the exact active references and associations that explain the blocker.

Stage 18 does not implement physical Dataset deletion.

## HTTP API

```text
GET  /api/enterprise/knowledge-bases
GET  /api/enterprise/knowledge-bases/{dataset_id}
GET  /api/enterprise/knowledge-bases/{dataset_id}/dependencies

GET    /api/enterprise/apps/{app_id}/knowledge-bases
POST   /api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}
DELETE /api/enterprise/apps/{app_id}/knowledge-bases/{dataset_id}

POST /api/enterprise/knowledge-bases/{dataset_id}/workspace-transfer
```

All routes use strict Pydantic contracts, signed actor identity, Tenant scope and safe error codes. Raw actor tokens, secrets, approval tickets and opaque idempotency values never appear in response bodies or audit snapshots.

## Frontend: Enterprise Knowledge Base Center

Route compatibility:

```text
/enterprise/knowledge-bases
#/enterprise/knowledge-bases
```

Stateful deep links:

```text
?dataset=<dataset_id>
?dataset=<dataset_id>&tab=applications
```

### Page hierarchy

```text
Page title / primary create action only when the backend contract is ready
Authority strip
Filter toolbar
Dense Knowledge Base table / mobile resource cards
Knowledge Base detail Drawer
```

Desktop table columns:

- Knowledge Base name/ID;
- lifecycle status;
- owning Workspace;
- active Application references;
- document count;
- source count;
- profile/ownership revision;
- updated time;
- restrained row actions.

Detail tabs:

- Overview;
- Workspace;
- Application references;
- Dependencies;
- Operations.

Documents, Taxonomy, Sources and Governance remain separate work surfaces reached through Dataset-scoped deep links. They are not duplicated inside the Drawer.

### Signature element: Dependency Rail

A compact evidence rail shows:

```text
Owning Workspace -> Shared associations -> Application references -> Archive readiness
```

The rail uses TDesign Tag, Alert, descriptions and compact table primitives. It is evidence, not a decorative metric wall.

### Responsive behavior

Desktop:

- dense `PrimaryTable`;
- 680–720px detail Drawer;
- one primary action;
- filters remain inline.

375px:

- resource cards instead of compressed table;
- filters in a Drawer;
- full-width detail Drawer;
- one action per row.

280px:

- name, status, owning Workspace, reference count, revision and view action only;
- all IDs and states wrap;
- buttons are full width;
- dialog/drawer content uses viewport-bounded internal scrolling;
- zero horizontal overflow.

Use TDesign React and TDesign Icons first. Uiverse/Morphicons are allowed only where TDesign has no suitable primitive. Do not introduce a second visual system.

## Global Workspace/Dataset scope

The selected Workspace and selected Dataset must become one verified scope chain:

```text
WorkspaceScopeBar selection
  -> server-returned Workspace ownership/bindings
    -> KnowledgeWorkspaceContext Dataset selection
      -> Dataset-scoped KnowledgeOps routes
```

Rules:

- prefer the owning/primary Dataset for the selected Workspace;
- if none exists, show `no_primary_knowledge_base` and do not invent `default`;
- localStorage stores only IDs revalidated against the latest server response;
- invalid saved IDs are cleared;
- the Workspace label in the shell and the Dataset used by pages must describe the same server-owned scope.

## Security invariants

- no cross-Tenant ownership or Application reference enumeration;
- shared association is never ownership;
- ownership and Application reference are never Dataset permissions;
- dependency query failures fail closed;
- active Application references hard-block archive;
- approval cannot bypass dependency blockers;
- transfer and reference mutations are revision-fenced, idempotent and audited;
- exact Catalog Schema manifest is required at 0028;
- no secrets, raw ticket, raw Authorization header or raw external path in DOM, logs, audit or fixtures;
- protected Retrieval Quality paths remain unchanged.

## Operational rollout

- Production migration is manual.
- Preflight reports missing/ambiguous primary ownership, active shared associations, orphan Apps/Datasets/Workspaces, active references and approval action facts.
- Shared associations are reported but not converted to ownership.
- Backfill counts are verified before enabling Registry mutations.
- Production must use schema verification, not `create_all` as migration.
- Rollback is manual and blocked while 0028 business facts remain.

## Testing

- migration structure, backfill, exact checks, indexes and composite FKs;
- SQLite upgrade/downgrade/re-upgrade and offline MySQL DDL;
- missing ownership and malformed schema fail closed;
- list/detail/dependency keyset pagination and Tenant isolation;
- Application reference create/remove, replay, conflict and audit rollback;
- ownership transfer, binding projection, revision conflict and approval gate;
- archive blocker cannot be bypassed;
- strict HTTP models and real 0028 SQLite router integration;
- direct/hash deep links;
- desktop/light/dark/375/280;
- table/cards/Drawer/Dialog accessibility and focus return;
- zero console/page/unknown/unexpected request errors;
- zero horizontal overflow;
- no raw approval ticket or secret in DOM/storage/log/result JSON.

## Production prohibition

No real production migration, ownership transfer, Application reference mutation, Dataset archive/delete, approval execution, restore, source sync, document ingestion, external publication or Retrieval Quality modification is performed automatically.
