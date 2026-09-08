# RAG4C Approval-Gated Tenant Member Role Change Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 15  
**Schema revision:** remains `0025_enterprise_approval_control`

## Objective

Connect `member_role_change` as the second real approval consumer. An active approval policy covering a tenant member prevents direct role mutation. The requester submits the current member revision and requested role as a sanitized approval snapshot; after the threshold is reached, an owner/admin consumes the one-time ticket and the existing member mutation executes with a second revision/owner-safety check.

## Policy scope

- Action: `member_role_change`
- Resource type: `tenant_member`
- Resource ID: target account ID
- Supported scopes: exact `tenant_member:{account_id}`, account ID, tenant-member wildcard and global forms already supported by the approval authority.

When a matching active policy exists:

```text
PATCH /api/enterprise/members/{account_id}/role
→ 409 member_role_approval_required
```

The response contains only sanitized policy ID/name/revision/threshold/expiry evidence.

## Transactional enforcement

The public route performs an early policy check for good UX. The authoritative member mutation repeats the policy check inside the same transaction after locking the Tenant row and target membership. Policy create/update/disable uses the same Tenant row lock, preventing a concurrent policy activation from racing a direct role change.

The internal approval consumer passes a validated `approval_execution_id`; only this path may bypass the direct policy gate.

## Approval snapshot

```json
{
  "target_account_id": "account-42",
  "expected_member_revision": 7,
  "current_role": "member",
  "requested_role": "admin",
  "current_status": "active"
}
```

The snapshot is sanitized, hashed and stored by the Stage 13 authority. The role mutation still validates the live member revision, active status, actor authority and last-owner protection at execution time.

## Consumer

Add `member_role_change` to the action adapter registry. The consumer validates:

- resource type `tenant_member`;
- resource ID matches snapshot target account;
- consumer actor is owner/admin;
- expected revision is positive;
- current/requested roles are supported and differ;
- target status is active.

It invokes `change_tenant_member_role(...)` with the approval consumer actor and stable request evidence. The existing mutation writes the member audit in its own transaction; the approval authority finalizes `executed` or `execution_failed` through the Stage 13 two-phase execution boundary.

## Frontend

### Member mutation dialog

- Load active `member_role_change` policies.
- No matching policy: preserve direct role mutation.
- Matching policy: show rule, threshold, expiry and revision; submit an approval request instead of calling the role endpoint.
- Display request ID/status and a link to `/enterprise/approvals`.
- Do not update the member row until execution succeeds.

Suspend/resume are not included in Stage 15 and preserve their existing flow.

### Approval execution dialog

Extend the transient one-time execution surface to connected `member_role_change` requests. Keep the raw ticket only in memory and consume it with complete action/resource/revision scope. On execution success refresh the approval facts; the Enterprise Member Directory will refresh on the next authoritative load.

## Security

- Only owner/admin can consume a member-role execution ticket.
- Tenant admin cannot mutate an owner under existing directory rules.
- Last active owner protection remains authoritative at execution time.
- Direct-route and transactional policy checks use matching scope semantics.
- Network retry keeps the same ticket and idempotency key.
- No real production role change or migration is performed during tests.

## Testing

- no-policy direct role change remains;
- matching exact/wildcard/global policy blocks direct route;
- stale early route check is caught transactionally;
- approval request snapshot is exact and role row remains unchanged;
- final ticket executes role change with approval + member audit facts;
- stale member revision produces `execution_failed` without role change;
- non-manager cannot claim ticket;
- last-owner protection survives approval;
- frontend direct/approval modes and one-time execution for member role;
- desktop/dark/375/280 Playwright, zero console errors.

## Protected paths

Do not modify retrieval-quality or retrieval-experiment protected files.
