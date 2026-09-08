# RAG4C Dataset ACL Control and Idempotency Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai

## Objective

Persist Dataset authorization mode independently of current grant count and make every ACL mutation replay-safe under network retries.

## Persistent mode

Dataset owns:

- acl_mode: tenant_role or dataset_acl
- acl_revision
- acl_enabled_at
- acl_enabled_by

The first successful grant create enables dataset_acl. Revoking the final active grant leaves dataset_acl enabled, so unmatched actors remain denied. Returning to tenant_role requires an explicit, revision-fenced, audited disable operation restricted to active tenant owners or admins.

During the 0018 → 0019 upgrade, any Dataset that already has an active 0017 grant is backfilled to `dataset_acl` at revision 1. Datasets without an active grant remain `tenant_role`. This migration rule preserves pre-0019 ACL enforcement and prevents a silent authorization expansion.

## Idempotency

Every ACL mutation requires an Idempotency-Key. The server canonicalizes operation, path identity, and validated body into a SHA-256 request hash.

- same tenant + actor + key + same hash replays the completed response;
- same key with a different hash returns 409;
- the ledger row and business mutation share one transaction;
- credentials, headers, bearer tokens, cookies, and database URLs are never persisted;
- request tracing IDs remain separate from idempotency keys.

## Concurrency anchor

Mutation lock order is active actor membership, Dataset, idempotency row, target grant, subject rows, audit. Dataset locking serializes first-grant enablement and explicit disable operations.

## Disable ACL

POST /api/knowledge-bases/{dataset_id}/access-control/disable

Body contains expected_acl_revision and reason. It does not delete or revoke grant history. After disable, grants remain visible as governed records but do not participate in authorization until ACL is explicitly enabled again by a grant mutation.

## Frontend

The enforcement evidence displays persistent acl_mode and revision. Mutation requests generate stable keys per operator action. The dangerous disable flow requires a TDesign confirmation dialog, business reason, current revision, and server refresh.

## Production boundary

No production database migration or write is performed in this stage. Real rollout remains blocked by ownerless active tenants and the approved backup/restore process.
