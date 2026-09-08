# RAG4C Dataset ACL Mutation Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai

## Objective

Turn the authoritative Dataset ACL read model into an audit-complete enterprise management workflow without weakening the Dataset authorization policy introduced in stage 5.

## Endpoints

- `POST /api/knowledge-bases/{dataset_id}/access-grants`
- `PATCH /api/knowledge-bases/{dataset_id}/access-grants/{grant_id}`
- `POST /api/knowledge-bases/{dataset_id}/access-grants/{grant_id}/revoke`
- `POST /api/knowledge-bases/{dataset_id}/access-grants/{grant_id}/resume`

Create accepts subject type, subject ID, role, and reason. Existing-resource changes require `expected_revision` and reason.

## Security ordering

1. Verify signed Actor and active tenant membership.
2. Verify Dataset scope and lifecycle.
3. Perform the dependency-level Dataset permission check.
4. Enter a writable transaction.
5. Re-evaluate Dataset permissions inside that transaction and require `knowledge.manage`.
6. Lock the Dataset and relevant grant rows where supported.
7. Validate the grant subject in the same tenant.
8. Apply revision-fenced state transition.
9. Append one tenant audit event in the same transaction.
10. Commit once.

Dependency authorization is an early rejection only; it is not sufficient for mutation safety.

## Subject validation

- account: active TenantMember in the current tenant.
- group: active TenantGroup in the current tenant.
- organization_unit: active TenantOrganizationUnit in the current tenant.

A polymorphic subject ID is never trusted without its tenant predicate and type-specific existence check.

## State and concurrency

- New grants start at revision 1 and active.
- Role updates increment revision.
- Revoke changes active to revoked and increments revision.
- Resume changes revoked to active, optionally updates role, and increments revision.
- Stale revisions return HTTP 409.
- Invalid state transitions return HTTP 409.
- Duplicate dataset/subject grants return HTTP 409 rather than creating parallel rows.

## Audit

Actions:

- `dataset_access_grant.created`
- `dataset_access_grant.role_updated`
- `dataset_access_grant.revoked`
- `dataset_access_grant.resumed`

Audit snapshots include grant IDs, subject identity, role, status, and revision, but no bearer credentials, invitation token hash, headers, or database URL.

## Frontend

The ACL table is the editable relationship directory; the enforcement evidence remains the only source of effective permissions. Dialogs use TDesign controls, require a reason, display revision, preserve rows on conflicts, and refresh both access summary and grants after successful mutations.

## Remaining boundary

This stage does not add group membership, organization membership, or invitation mutations. It also does not migrate the real MySQL catalog.
