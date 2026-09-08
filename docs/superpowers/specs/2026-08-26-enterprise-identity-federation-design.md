# RAG4C Enterprise Identity Federation Control Plane Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Stage:** 9  
**Target revision:** `0021_enterprise_identity_federation`

## Objective

Add a real, auditable enterprise identity control plane for trusted domains, OIDC/SAML provider configuration, and one-time SCIM token issuance, while explicitly keeping external login handshakes and SCIM provisioning data-plane endpoints unavailable until a later stage.

## Product truth

The UI and API may claim:

- domain challenge issued/pending/verified/revoked;
- IdP configuration draft/active/disabled;
- configuration validation evidence;
- SCIM token issued once/revoked/expired;
- exact database revision and audit evidence.

They must not claim:

- users can already sign in through SSO;
- SAML/OIDC callback/runtime is connected;
- SCIM Users/Groups provisioning endpoints are live;
- an email or external provider action happened when it did not.

## Database contract

### `tenant_verified_domains`

- globally unique `normalized_domain` so one domain cannot be claimed by two tenants;
- status: `pending | verified | revoked`;
- verification method: `dns_txt`;
- challenge token and TXT host/value (not credentials);
- `revision > 0`;
- checked/verified/revoked timestamps and actors;
- tenant-scoped creator/updater/revoker/verifier FKs;
- verified/revoked evidence checks.

### `tenant_identity_providers`

- provider type: `oidc | saml`;
- status: `draft | active | disabled`;
- one active primary provider per tenant through nullable `active_slot` and unique `(tenant_id, active_slot)`;
- OIDC fields: issuer URL, client ID, secret reference, scopes;
- SAML fields: entity ID, SSO URL, metadata URL, certificate fingerprint;
- trusted-domain FK;
- validation state: `unchecked | valid | invalid | unavailable`;
- last validation timestamp/error/metadata hash;
- revision, creator/updater/activator/deactivator evidence;
- conditional checks require the correct fields for each provider type;
- raw client secrets/private keys/certificates are never stored; only a vault/KMS `secret_ref` or fingerprint is accepted.

### `tenant_scim_tokens`

- token hash only, 64-character domain-separated SHA-256 digest;
- non-secret display prefix;
- status: `active | revoked | expired`;
- scopes allowlist, expiry, last-used timestamp;
- revision and lifecycle actor/timestamp evidence;
- raw token returned once and never persisted or listed;
- token names unique among active tokens per tenant using a nullable active-name key.

All identity mutations reuse `tenant_control_mutation_requests` with Account-level actor FK and tenant-scoped uniqueness.

## DNS verification

- Generate a random challenge.
- TXT host: `_rag4c-verify.<normalized_domain>`.
- TXT value: `rag4c-verification=<challenge>`.
- Verification uses a bounded resolver abstraction.
- Production adapter uses `dnspython` with timeout/lifetime limits and no arbitrary URL fetch.
- Tests inject an in-memory resolver; they do not use public DNS.
- Verification failure remains pending and records a sanitized reason.
- Verification success uses row lock, revision fencing, audit, and idempotency.

## IdP validation

Stage 9 validates configuration syntax and trusted-domain ownership only.

- OIDC issuer/redirect targets must be HTTPS and reject localhost, loopback, private, link-local, multicast, and user-info URLs.
- SAML URLs receive the same network-boundary validation.
- `secret_ref` is an opaque reference such as `vault://...` or `env://...`; raw secret-looking values are rejected.
- Activate requires a verified domain and `validation_state=valid`.
- `validation_state=valid` in Stage 9 means the configuration contract is internally valid, not that an external login handshake succeeded.
- Runtime login remains `not_connected` and is shown explicitly.

## SCIM token lifecycle

- Issue: owner/admin, name/scopes/expiry/reason, one-time raw token response.
- Replay never returns raw token; it returns `token_already_issued` evidence.
- Revoke: revision-fenced and audited.
- Token list returns only prefix, scopes, timestamps, status, and revision.
- No SCIM provisioning endpoint is exposed in Stage 9; capability reads `control_plane_ready / data_plane_not_connected`.

## Authorization

- Tenant owner: manage domains, IdPs, and SCIM tokens.
- Tenant admin: manage domains and IdP drafts, issue/revoke non-owner SCIM tokens, but cannot activate/disable the primary IdP without owner permission.
- Editor/member: read capability evidence only.
- All mutations re-read active membership and role inside the write transaction.

## Audit actions

- `tenant_domain.created`
- `tenant_domain.verification_checked`
- `tenant_domain.verified`
- `tenant_domain.revoked`
- `tenant_identity_provider.created`
- `tenant_identity_provider.updated`
- `tenant_identity_provider.activated`
- `tenant_identity_provider.disabled`
- `tenant_scim_token.issued`
- `tenant_scim_token.revoked`

Audit and replay responses never contain raw SCIM tokens, authorization headers, raw IdP secrets, or invitation tokens.

## API surface

- `GET/POST /api/enterprise/identity/domains`
- `POST /api/enterprise/identity/domains/{id}/verify`
- `POST /api/enterprise/identity/domains/{id}/revoke`
- `GET/POST /api/enterprise/identity/providers`
- `PATCH /api/enterprise/identity/providers/{id}`
- `POST /api/enterprise/identity/providers/{id}/activate`
- `POST /api/enterprise/identity/providers/{id}/disable`
- `GET/POST /api/enterprise/identity/scim-tokens`
- `POST /api/enterprise/identity/scim-tokens/{id}/revoke`

All mutations require `Idempotency-Key: 1..128`.

## Frontend

Add a TDesign enterprise identity center under Enterprise Management:

- verified-domain cards with copyable DNS TXT evidence and verify/revoke actions;
- OIDC/SAML configuration wizard using Form, Steps, Select, Input, Alert, Dialog, Drawer, Tag and Descriptions;
- SCIM token table/cards and one-time token dialog;
- explicit `runtime_not_connected` and `scim_data_plane_not_connected` evidence;
- desktop dense tables, dark mode, 375px/280px lifecycle cards and action drawers;
- no raw secret in localStorage, list models, logs, ARIA labels, audit or browser evidence JSON.

## Production boundary

Do not perform real DNS writes, IdP login, SAML/OIDC callbacks, SCIM provisioning, SMTP, production migration, backup/restore or external publishing in this stage. DNS verification may perform bounded read-only TXT lookup only when an operator invokes it.
