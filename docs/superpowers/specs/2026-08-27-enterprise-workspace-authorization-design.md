# RAG4C Enterprise Workspace Authorization Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 17  
**Target revision:** `0027_enterprise_workspace_authorization`

## Objective

Connect the explicit Workspace authority introduced in Stage 16 to real Dataset permission evaluation without replacing or weakening the existing Tenant Role and Dataset ACL authorities. Workspace authorization is an additive permission source with an auditable rollout lifecycle:

```text
disabled -> shadow -> enforced
```

Shadow computes evidence but never grants. Enforced contributes Workspace permissions to the final Dataset decision. High-risk mode changes reuse the enterprise approval authority and one-time execution tickets.

## Chosen authorization model

The final permission set is additive:

```text
existing Dataset decision permissions
UNION
permissions contributed by enforced Workspace policies
```

Existing authorities remain unchanged:

- active Tenant owner/admin break-glass;
- Dataset owner manager bypass;
- Tenant Role fallback when `datasets.acl_mode = tenant_role`;
- Account, Group and Organization Unit grants when `datasets.acl_mode = dataset_acl`.

Workspace authorization never subtracts an existing permission in Stage 17. A future restrictive Workspace boundary requires a separate design and migration.

## Permission model version 1

| Workspace role | Dataset projection | Permissions |
| --- | --- | --- |
| owner | manager | read, write, delete, manage, audit |
| admin | manager | read, write, delete, manage, audit |
| editor | editor | read, write, delete |
| viewer | viewer | read |

The mapping is fixed in code and identified by `permission_model_version = 1`. Stage 17 does not expose tenant-custom role matrices.

## Database authority

### `tenant_workspace_authorization_policies`

One policy row per Workspace:

- `id` — deterministic or generated policy ID;
- `tenant_id`, `workspace_id` — tenant-safe scope;
- `mode` — `disabled | shadow | enforced`;
- `permission_model_version` — positive integer, initially `1`;
- `revision` — positive optimistic-concurrency fence;
- `created_at`, `created_by`, `updated_at`, `updated_by`;
- `enforced_at`, `enforced_by`;
- `disabled_at`, `disabled_by`.

Constraints:

- unique `(tenant_id, workspace_id)`;
- composite FK to `tenant_workspaces(tenant_id, id)`;
- tenant FK;
- exact mode and evidence lifecycle checks;
- `permission_model_version > 0` and `revision > 0`;
- index `(tenant_id, mode, updated_at, id)`;
- index `(tenant_id, workspace_id, mode, id)`.

Evidence semantics:

```text
shadow   => enforced_* NULL and disabled_* NULL
enforced => enforced_* NOT NULL and disabled_* NULL
disabled => disabled_* NOT NULL and enforced_* NULL
```

### Backfill

- active Workspace -> `shadow`;
- archived Workspace -> `disabled` with migration actor evidence;
- deterministic and tenant-safe;
- no fabricated Account, TenantMember, WorkspaceMember or Dataset Binding;
- existing Workspace role facts remain unchanged.

New Workspaces created under a 0027 catalog receive a `shadow` policy in the same transaction as Workspace creation.

### Approval action extension

Add to the existing approval action checks:

```text
workspace_authorization_mode_change
```

Resource type:

```text
tenant_workspace
```

0027 alters the named checks on both approval policies and approval requests without rewriting existing approval facts. Downgrade is fail-closed while Stage 17 action rows remain.

## Workspace authorization evaluation

A Workspace contribution is eligible only when all facts are authoritative and active:

- Tenant is active;
- TenantMember is active;
- Workspace is active;
- WorkspaceMember is active;
- Workspace authorization policy exists and is structurally valid;
- Workspace Dataset Binding is active;
- Dataset belongs to the same Tenant;
- policy mode is `shadow` or `enforced`.

Removed members, removed bindings, archived Workspaces, suspended TenantMembers and cross-tenant rows never contribute.

When multiple Workspaces bind the same Dataset, candidate permissions are the union of the actor's highest effective Workspace roles across all eligible Workspaces.

### Disabled

- no Workspace permission contribution;
- no would-grant projection;
- existing Dataset decision remains authoritative.

### Shadow

- compute Workspace candidates and role evidence;
- expose `would_grant_permissions`;
- do not change `effective_permissions`;
- do not persist per-request shadow decisions.

### Enforced

- compute Workspace candidates;
- union them into `effective_permissions`;
- expose the exact Workspace policies, revisions, roles and bindings that contributed.

## Dataset decision contract

Keep the existing `enforcement_mode` meaning:

```text
tenant_role_fallback | dataset_acl
```

Add a separate projection:

```json
{
  "workspace_authorization": {
    "state": "workspace_authorization_shadow",
    "mode": "shadow",
    "permission_model_version": 1,
    "workspace_ids": [],
    "workspace_roles": [],
    "policy_revisions": [],
    "candidate_permissions": [],
    "would_grant_permissions": [],
    "granted_permissions": [],
    "warnings": []
  }
}
```

Allowed states:

```text
workspace_authorization_not_available
workspace_authorization_disabled
workspace_authorization_shadow
workspace_authorization_enforced
```

`DatasetAccessDecision.allows()` continues to read the final `effective_permissions` set.

## Schema and failure behavior

- No 0027 tables at all: preserve the Stage 16 Dataset decision and report `not_available` during rolling deployment.
- Partial 0027 schema: fail closed with `DatasetAccessControlUnavailable` / HTTP 503.
- A bound active Workspace missing its policy under a complete 0027 schema: fail closed.
- An enforced policy with malformed role, revision, binding or membership evidence: fail closed.
- MySQL/MariaDB schema checks are not permanently cached.
- Critical checks use canonical exact SQL fingerprints, not fragment-only matching.

## Policy lifecycle API

Read:

- `GET /api/enterprise/workspaces/{workspace_id}/authorization`
- `GET /api/enterprise/workspaces/{workspace_id}/authorization/impact`

Mutation:

- `PATCH /api/enterprise/workspaces/{workspace_id}/authorization`

Mutation body:

```json
{
  "expected_revision": 1,
  "target_mode": "shadow",
  "reason": "Enable safe evaluation"
}
```

Authorization:

- Tenant owner/admin may manage policy mode;
- Workspace owner/admin cannot change rollout mode without Tenant authority;
- revision fence, tenant row lock, Workspace row lock, idempotency and audit share one transaction;
- archived Workspace cannot enter shadow or enforced;
- no-op mode changes are conflicts, not fake successes.

## Approval-gated mode changes

Action:

```text
workspace_authorization_mode_change
```

Approval snapshot:

```text
workspace_id
workspace_revision
policy_revision
from_mode
target_mode
permission_model_version
permission_matrix_fingerprint
reason
```

Scope matching supports exact Workspace, tenant Workspace wildcard and global policy scopes using the existing approval scope grammar.

If an active matching approval policy exists, direct high-risk mutation returns:

```text
409 workspace_authorization_approval_required
```

The route performs an early policy check and the Core repeats it inside the locked transaction.

Approval consumer rules:

- current consumer actor must be Tenant owner/admin;
- ticket action/resource/scope must match exactly;
- Workspace and policy revisions are re-checked;
- Workspace must still be active for shadow/enforced;
- stable Approval Execution ID becomes the policy mutation idempotency identity;
- non-managers cannot claim or burn a ticket;
- stale revision becomes `execution_failed`;
- network retry preserves the same ticket and idempotency identity;
- successful execution records both Approval and Workspace Authorization audit facts.

## Frontend: Permissions Rollout Center

Upgrade the Workspace detail Permissions tab using TDesign.

### Authority strip

- authorization mode;
- policy revision;
- permission model version;
- active Workspace member count;
- active Dataset binding count;
- matching approval policy state;
- catalog revision.

### Role matrix

Dense TDesign table showing the version 1 Workspace-to-Dataset permission projection.

### Impact preview

For the current actor and selected Dataset:

- Tenant role;
- Dataset ACL role and matched grants;
- Workspace roles and contributing Workspaces;
- current effective permissions;
- shadow candidate permissions;
- granted delta in enforced mode;
- warnings and unavailable evidence.

### Mode change UX

- Disabled -> Shadow: direct Tenant owner/admin mutation;
- Shadow -> Enforced: submit approval when a matching rule exists, otherwise direct Tenant owner/admin mutation;
- Enforced -> Shadow/Disabled: explicit revocation consequence and approval support;
- current revision, scope and reason are always visible;
- no raw execution ticket enters DOM, storage or logs;
- Approval Center direct/hash navigation remains supported.

### Responsive behavior

- desktop: authority strip, role matrix and impact table;
- 375px: priority cards and full-width mode dialog;
- 280px: one action per row, Drawer/Dialog width bounded by viewport;
- light/dark themes;
- zero horizontal overflow;
- all long state tokens wrap.

## Security invariants

- exact Tenant predicate on every Workspace authorization query;
- active-only membership and binding facts;
- no cross-tenant enumeration;
- immediate revocation because every request re-evaluates current facts;
- no permission decision cache that omits policy/member/binding revision;
- no shadow permission enters `effective_permissions`;
- enforced policy query failures never fall back to ordinary ACL;
- approval ticket replay cannot repeat the policy mutation;
- audit/business/idempotency changes roll back together;
- schema drift is fail-closed;
- Workspace role is never treated as Tenant role.

## Operational rollout

- production migration remains manual;
- no automatic rollback;
- preflight counts Workspaces, policy backfill rows and existing approval action rows;
- verify shadow rows for every active Workspace and disabled rows for every archived Workspace;
- observe Shadow evidence before any Enforced change;
- Enforced changes are performed per Workspace with revision evidence;
- rollback from Enforced to Shadow is a policy mutation, not a database downgrade.

## Testing

- migration structure, backfill, constraints, cross-tenant FK and downgrade;
- approval action check upgrade/downgrade;
- policy lifecycle, revision, idempotency and audit rollback;
- shadow never grants;
- enforced union grants;
- owner/admin and Dataset owner behavior unchanged;
- multiple Workspace union;
- removed/archived/suspended facts do not grant;
- partial schema and missing policy fail closed;
- approval-required early and in-transaction checks;
- ticket consume, stale revision and retry;
- strict API contracts and real 0027 SQLite router integration;
- TDesign direct/hash, table/cards/dialogs;
- Playwright desktop light/dark and 375/280, zero console/page/unknown errors.

## Production prohibition

No real production migration, policy backfill, authorization enforcement, approval execution or external publication is performed automatically.
