# RAG4C Enterprise Workspace Control Plane Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 16  
**Target revision:** `0026_enterprise_workspace_control`

## Objective

Introduce an explicit Workspace authority between Tenant and Knowledge Base. Today the product calls the Tenant a workspace and stores only tenant/dataset scope. Stage 16 adds real workspace lifecycle, workspace membership, knowledge-base bindings and workspace-level role evidence while keeping dataset authorization honest until enforcement is integrated.

## Database authority

### `tenant_workspaces`

- tenant-scoped workspace ID, code, normalized name and description;
- status `active | archived`;
- environment `development | testing | production`;
- default slot allowing one active default workspace per tenant;
- revision and lifecycle actor/timestamp evidence;
- unique tenant/code, tenant/name and tenant/id.

### `tenant_workspace_members`

- tenant/workspace/account scope;
- role `owner | admin | editor | viewer`;
- status `active | removed`;
- revision and lifecycle evidence;
- one active relation per workspace/account;
- tenant-safe FK to TenantMember and Workspace.

### `tenant_workspace_datasets`

- tenant/workspace/dataset scope;
- binding kind `primary | shared`;
- status `active | removed`;
- revision and lifecycle evidence;
- one active primary workspace per Dataset;
- tenant-safe FK to Dataset and Workspace.

## Migration and backfill

- Create one active default workspace for every existing Tenant using deterministic ID `workspace-default-{tenant_id}`.
- Bind every existing Dataset to its tenant default workspace as `primary`.
- Copy existing active TenantMembers into the default workspace:
  - tenant owner → workspace owner;
  - tenant admin → workspace admin;
  - tenant editor → workspace editor;
  - tenant member → workspace viewer.
- Do not invent accounts, members or owners.
- Missing tenant/dataset/member references fail closed.

## Lifecycle

Workspace:

```text
active --archive--> archived
```

- archived workspace cannot receive new members or datasets;
- default workspace cannot be archived while it owns active primary Dataset bindings;
- revision fencing applies to update/archive/member/binding mutations;
- last active workspace owner protection mirrors tenant owner protection.

## API

Read:

- `GET /api/enterprise/workspaces`
- `GET /api/enterprise/workspaces/{workspace_id}`
- `GET /api/enterprise/workspaces/{workspace_id}/members`
- `GET /api/enterprise/workspaces/{workspace_id}/datasets`

Mutations:

- `POST /api/enterprise/workspaces`
- `PATCH /api/enterprise/workspaces/{workspace_id}`
- `POST /api/enterprise/workspaces/{workspace_id}/archive`
- `POST /api/enterprise/workspaces/{workspace_id}/members`
- `PATCH /api/enterprise/workspaces/{workspace_id}/members/{account_id}`
- `POST /api/enterprise/workspaces/{workspace_id}/members/{account_id}/remove`
- `POST /api/enterprise/workspaces/{workspace_id}/datasets`
- `POST /api/enterprise/workspaces/{workspace_id}/datasets/{dataset_id}/remove`

All lists use bounded keyset pagination. Mutations use the tenant idempotency ledger, revision fencing and tenant audit transaction rollback.

## Authorization

- Tenant owner/admin may create/archive workspaces and manage bindings.
- Workspace owner/admin may manage workspace members.
- Tenant owner retains break-glass authority.
- Stage 16 records workspace roles but does not silently replace existing Dataset ACL enforcement.
- APIs and UI explicitly report `workspace_authorization_not_enforced` until the authorization engine consumes workspace membership.

## Frontend

Add `/enterprise/workspaces` with TDesign:

- governance evidence strip: workspace count, active count, default workspace, primary dataset bindings, authorization state;
- dense desktop workspace table and mobile priority cards;
- create/edit/archive dialogs;
- detail Drawer tabs: overview, members, knowledge bases, permissions;
- member role/status actions with revision evidence;
- Dataset binding actions and primary/shared labels;
- explicit empty/degraded/error states;
- workspace selector integrated into the shared header without fabricating workspace data.

Visual direction remains restrained: white/slate surfaces, compact filters, blue selected state, small radii, minimal shadow and no card-wall dashboard.

## Security

- exact Tenant predicate on every query;
- tenant-safe composite FKs;
- no cross-tenant workspace/member/dataset enumeration;
- last-owner and default-workspace protections;
- audit/business/idempotency same transaction;
- missing 0026 fail-closed;
- no workspace role is treated as effective Dataset permission until enforcement is connected.

## Testing

- migration/backfill/downgrade and multi-tenant isolation;
- active default uniqueness and active primary Dataset uniqueness;
- lifecycle/revision/idempotency/audit rollback;
- last workspace owner and archived workspace protections;
- real API against migration schema;
- frontend direct/hash route, table/cards/drawer/dialogs;
- desktop light/dark and 375/280 Playwright, zero console errors.

## Protected paths

Do not modify retrieval-quality or retrieval-experiment protected files.
