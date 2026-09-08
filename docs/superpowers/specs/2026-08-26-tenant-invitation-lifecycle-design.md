# RAG4C Enterprise Invitation Lifecycle Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Stage:** 8  
**Target revision:** `0020_tenant_invitation_lifecycle`

## Objective

Turn the existing read-only tenant invitation projection into a secure enterprise onboarding lifecycle without pretending that an email delivery provider or SSO/SCIM connector already exists.

The lifecycle covers:

- create invitation;
- generate/rotate a one-time invitation link;
- revoke a pending invitation;
- accept an invitation as an authenticated account;
- atomically create tenant membership and audit evidence;
- replay every mutation safely under network retries.

## Why invitations before SSO

The repository already has `tenant_invitations`, a read API, a capability flag, and a TDesign invitation tab. Completing this vertical slice gives the enterprise console a real member onboarding path immediately. SSO/SCIM remains a later integration because it requires an external identity-provider contract, secret storage, domain verification, and provisioning semantics that are not currently available.

## Database contract

### Existing `tenant_invitations` additions

Add:

- `pending_email_key VARCHAR(256) NULL`
- `last_sent_at DATETIME(6) NOT NULL`
- `send_count INTEGER NOT NULL DEFAULT 1`
- `revoked_at DATETIME(6) NULL`
- `revoked_by VARCHAR(64) NULL`
- `updated_by VARCHAR(64) NOT NULL`

Constraints:

- `send_count > 0`
- pending state requires `pending_email_key = normalized_email`
- accepted/revoked/expired states require `pending_email_key IS NULL`
- accepted state requires `accepted_at` and `accepted_by`
- revoked state requires `revoked_at` and `revoked_by`
- unique `(tenant_id, pending_email_key)` prevents two pending invitations for one tenant/email while allowing historical terminal rows
- unique `(tenant_id, token_hash)` prevents token-hash duplication
- tenant-scoped FKs for `revoked_by` and `updated_by`

Backfill:

- pending rows receive `pending_email_key = normalized_email`;
- terminal rows receive `NULL`;
- `last_sent_at = created_at`;
- `send_count = 1`;
- `updated_by = invited_by`;
- existing duplicate pending tenant/email rows fail closed before DDL/unique creation and require operator cleanup.

### Generic tenant mutation ledger

Create `tenant_control_mutation_requests` for invitation and future organization/group mutations:

- `id`
- `tenant_id`
- `actor_id`
- `idempotency_key` (domain-separated digest only)
- `request_hash`
- `operation`
- `resource_type`
- `resource_id`
- `status` (`pending/completed/failed`)
- `response_json`
- `http_status`
- `created_at`
- `completed_at`

Unique scope:

```text
(tenant_id, actor_id, idempotency_key)
```

The ledger never stores the raw invitation token, raw idempotency key, Authorization/Cookie headers, credentials, or the original request body. The Account-level actor FK intentionally supports signed accounts that have not yet become tenant members; tenant claims and management permissions remain service-enforced.

## Authorization

- Active tenant owner: may invite any role including owner.
- Active tenant admin: may invite admin/editor/member, but not owner.
- Editor/member: read-only; cannot mutate invitations.
- Accept endpoint requires a valid signed actor and exact normalized email match.
- Invitation acceptance uses a dedicated signed-account identity resolver that does not require pre-existing tenant membership; all ordinary knowledge and enterprise management endpoints continue to require active membership.
- A suspended tenant member cannot manage invitations; a cross-tenant signed actor cannot accept or mutate them. The current Account model has no lifecycle field, so this stage does not invent an account-status check.
- Existing active membership is a conflict; the invitation is not silently consumed.

## Token security

- Generate 32 random bytes and encode URL-safe without padding.
- Return the raw token only in the successful create/rotate response.
- Persist only a domain-separated SHA-256 digest.
- Never log, audit, or include the token in later list responses.
- Acceptance uses constant-time digest comparison.
- Token rotation invalidates the old token in the same transaction.
- Expired invitations fail closed and transition to `expired` under row lock.

## API contract

All mutations require `Idempotency-Key: 1..128`.

### Create

```text
POST /api/enterprise/invitations
```

Body:

```json
{
  "email": "member@example.com",
  "role": "member",
  "expires_in_days": 7,
  "reason": "加入知识运营团队"
}
```

Returns `201` with invitation facts and one-time delivery evidence:

```json
{
  "invitation": { "...": "authoritative facts" },
  "delivery": {
    "state": "manual_link_required",
    "invite_token": "returned once",
    "expires_at": "..."
  }
}
```

### Rotate link / resend intent

```text
POST /api/enterprise/invitations/{invitation_id}/resend
```

Requires `revision`, `expires_in_days`, and `reason`. Rotates the token, increments revision/send count, and returns a new one-time token. The response truthfully states that the email delivery connector is not configured.

### Revoke

```text
POST /api/enterprise/invitations/{invitation_id}/revoke
```

Requires revision and reason. Pending → revoked only.

### Accept

```text
POST /api/enterprise/invitations/accept
```

Body contains the raw invitation token. The authenticated actor email must match. Acceptance creates and flushes `TenantMember` first, then marks the invitation accepted and sets `accepted_by` so the existing tenant-scoped FK remains valid; clearing `pending_email_key` and writing audit happen in the same transaction.

## Audit actions

- `tenant_invitation.created`
- `tenant_invitation.link_rotated`
- `tenant_invitation.revoked`
- `tenant_invitation.accepted`

Audit snapshots contain invitation ID, normalized email, role, status, revision, expiry, send count, reason, and actor/resource metadata. They never contain raw tokens or raw idempotency keys.

## Frontend

Use TDesign React and TDesign Icons exclusively for this flow.

Invitation tab additions:

- `发起邀请` primary button;
- create Dialog with email, role, expiry, reason;
- one-time secure-link result panel with Copy button and explicit `邮件通道未接入` evidence;
- table operation column with `重新生成链接` and `撤销`;
- revision/status/expiry/send-count evidence;
- 409 refresh recovery, 503 migration-required, and same-key retry handling;
- route `/enterprise/invitations/accept?token=...` with signed-actor email evidence and explicit acceptance confirmation.

The link is held only in transient React state and is cleared when the result Dialog closes.

## Responsive and visual direction

- Desktop retains the dense Tencent/TDesign console table.
- At 375px and 280px, invitation rows become summary cards with an action drawer.
- One-time token is masked by default and copyable through a TDesign control.
- Dangerous revoke uses a TDesign danger Dialog.
- Reduced-motion and keyboard focus remain required.

## Production boundary

No SMTP provider, SSO/SCIM provider, production migration, external message delivery, or real database write is performed in this stage. The UI must say `manual_link_required` rather than claiming an email was sent.
