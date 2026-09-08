# RAG4C SCIM v2 Provisioning Data Plane Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Stage:** 10  
**Target revision:** `0022_scim_provisioning_data_plane`

## Objective

Expose a real SCIM 2.0 Users/Groups provisioning data plane authenticated by Stage 9 SCIM tokens and backed by RAG4C tenant membership, groups, audit, and revision authority.

OIDC/SAML login runtime remains out of scope. This stage changes only SCIM from `data_plane_not_connected` to `data_plane_ready` when 0022 is complete.

## Database contract

### `tenant_scim_user_links`

- tenant-scoped Account/TenantMember link;
- globally opaque SCIM resource `id`;
- externalId and userName unique per tenant;
- account_id unique per tenant;
- SCIM version/revision > 0;
- last-provisioned timestamp and source token ID;
- created/updated timestamps;
- FKs to tenant, tenant member, and SCIM token.

### `tenant_scim_group_links`

- tenant-scoped TenantGroup link;
- externalId and displayName unique per tenant;
- group_id unique per tenant;
- SCIM version/revision > 0;
- last-provisioned timestamp and source token ID;
- FKs to tenant, tenant group, and SCIM token.

### Existing table additions

`tenant_scim_tokens`:

- `last_used_at`
- `last_used_ip_hash`
- `use_count > 0` when last-used evidence exists

Do not persist raw bearer tokens or raw request IPs.

## Authentication

- `Authorization: Bearer <SCIM token>` only.
- Hash token with the same Stage 9 domain-separated digest and constant-time compare.
- Token must be active, unexpired, and contain the required scope.
- Tenant is derived only from the token row; `X-RAG4C-Tenant` cannot override it.
- Update last-used evidence and use count inside the provisioning transaction.
- Never put the bearer token in logs, audit snapshots, replay payloads, errors, or cursor state.

## SCIM protocol surface

Base path:

```text
/scim/v2
```

### Discovery

- `GET /ServiceProviderConfig`
- `GET /ResourceTypes`
- `GET /Schemas`

### Users

- `GET /Users`
- `POST /Users`
- `GET /Users/{id}`
- `PATCH /Users/{id}`
- `DELETE /Users/{id}`

User mapping:

- `userName` → Account email and link userName;
- `displayName` → Account name;
- `active` → TenantMember active/suspended;
- one SCIM user belongs to one tenant membership;
- POST creates Account only when email is not already used; existing Account may be attached only when the email matches exactly and it has no tenant membership;
- DELETE is deactivation/suspension, not global Account deletion.

### Groups

- `GET /Groups`
- `POST /Groups`
- `GET /Groups/{id}`
- `PATCH /Groups/{id}`
- `DELETE /Groups/{id}`

Group mapping:

- displayName → TenantGroup name;
- members → TenantGroupMember active relationships;
- referenced user IDs must belong to the same tenant;
- DELETE archives the tenant group and removes active group membership projections; it does not delete Accounts or TenantMembers.

## Concurrency and protocol correctness

- Every SCIM resource has a weak ETag derived from revision: `W/"<revision>"`.
- PATCH/DELETE require `If-Match`; stale versions return 412.
- POST duplicate externalId/userName/displayName returns SCIM 409.
- List supports bounded `startIndex`, `count`, and exact allowlisted filters:
  - `userName eq "..."`
  - `externalId eq "..."`
  - `displayName eq "..."`
- Unsupported filters return SCIM 400, never fall back to arbitrary SQL.
- Response schemas follow SCIM error/list/resource envelopes and never expose internal tenant IDs, token IDs, hashes, or audit data.

## Authorization scopes

- `users:read`, `users:write`
- `groups:read`, `groups:write`

Write scope does not imply read scope unless explicitly present. ServiceProviderConfig remains available to any active SCIM token.

## Audit

- `scim.user.created`
- `scim.user.updated`
- `scim.user.deactivated`
- `scim.group.created`
- `scim.group.updated`
- `scim.group.archived`

Audit includes resource ID, external ID, revision, changed allowlisted fields, token prefix, request ID and time. It excludes bearer tokens, token hash, raw IP, and full request bodies.

## Frontend

The enterprise identity center shows:

- `scim_data_plane_ready` only when readiness reports 0022;
- base endpoint `/scim/v2`;
- supported resources Users/Groups;
- supported scopes;
- token last-used/use-count evidence;
- copyable endpoint and ServiceProviderConfig URL;
- explicit `sso_runtime_not_connected` remains unchanged;
- desktop table, dark mode and 375/280 cards/drawer.

The frontend is a control/observability surface; it does not manually provision SCIM users or groups.

## Production boundary

No real external IdP calls, OIDC/SAML login, production migration, SCIM calls from a real provider, or external publishing are performed in this stage. Tests use temporary SQLite and synthetic SCIM tokens.
