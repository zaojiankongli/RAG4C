# RAG4C Enterprise Approval Center Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 13  
**Target revision:** `0025_enterprise_approval_control`

## Objective

Add an auditable multi-person approval authority for high-risk KnowledgeOps changes. The product surface follows a restrained enterprise-console pattern: approval list, approval policies, request detail, decision timeline, and explicit execution boundary. The stage does not pretend that approved requests automatically execute every downstream mutation.

## Scope

Approval action types initially include:

- `catalog_upgrade`
- `membership_bootstrap`
- `dataset_acl_disable`
- `member_role_change`
- `identity_provider_disable`
- `audit_retention_execute`

The approval lifecycle is real and persistent. Downstream execution remains an explicit adapter boundary. Approved requests issue a one-time execution authorization ticket; Stage 13 stores only its digest and exposes `execution_adapter_not_connected` until a consumer integrates it.

## Database authority

### `tenant_approval_policies`

- tenant-scoped policy ID and human-readable name;
- action type and optional resource scope;
- status `active | disabled`;
- required approval count `1..5`;
- request expiry `15..10080` minutes;
- revision, lifecycle timestamps and actor evidence;
- one active policy per tenant/action/scope key.

### `tenant_approval_policy_approvers`

- tenant/policy scope;
- approver kind `account | role | group`;
- approver reference;
- active status and revision;
- unique policy/kind/reference;
- tenant-safe account/group references where applicable.

### `tenant_approval_requests`

- tenant/policy/requester scope;
- action type, resource type and resource ID;
- canonical sanitized request snapshot and SHA-256 payload hash;
- requester reason;
- status `pending | approved | rejected | cancelled | expired | executed | execution_failed`;
- required/received approval counters;
- one-time execution ticket hash only;
- rejection/cancellation/execution evidence;
- revision and bounded expiry;
- idempotency identity scoped to tenant, actor and exact mutation path.

### `tenant_approval_decisions`

- tenant/request/approver scope;
- decision `approved | rejected`;
- bounded comment and decision timestamp;
- unique request/approver decision;
- immutable decision facts.

## Lifecycle

```text
pending --approve threshold--> approved --consume ticket--> executed
   |              |                         \--> execution_failed
   |              \-- any reject --> rejected
   \-- requester cancel --> cancelled
   \-- expiry --> expired
```

Rules:

- requester cannot approve their own request;
- only active eligible approvers may decide;
- one approver has one immutable decision;
- any rejection is terminal;
- approval is terminal only when the policy threshold is met;
- requester may cancel only a pending request;
- revision fencing applies to approve, reject, cancel, policy update and ticket consumption;
- audit failure rolls back the lifecycle mutation;
- exact idempotent replay returns the same response; mismatched payload returns conflict.

## Execution authorization

When a request becomes approved, the service issues an opaque ticket once and stores only a domain-separated digest. Consumers call a core `consume_approval_ticket(...)` gate with tenant, action, resource and revision. Stage 13 provides the gate and lifecycle endpoint but does not automatically invoke catalog migration, retention deletion, ACL disable or identity-provider mutation.

The UI must label this honestly as `execution_adapter_not_connected` where no consumer is wired.

## API

Read:

- `GET /api/enterprise/approvals/policies`
- `GET /api/enterprise/approvals/requests`
- `GET /api/enterprise/approvals/requests/{request_id}`

Mutations:

- `POST /api/enterprise/approvals/policies`
- `PATCH /api/enterprise/approvals/policies/{policy_id}`
- `POST /api/enterprise/approvals/policies/{policy_id}/disable`
- `POST /api/enterprise/approvals/requests`
- `POST /api/enterprise/approvals/requests/{request_id}/approve`
- `POST /api/enterprise/approvals/requests/{request_id}/reject`
- `POST /api/enterprise/approvals/requests/{request_id}/cancel`
- `POST /api/enterprise/approvals/requests/{request_id}/consume-ticket`

List endpoints use bounded keyset pagination and exact tenant predicates.

## Frontend

Add a TDesign approval center inside Enterprise Administration:

- tabs: `审批申请` and `审批规则`;
- dense desktop table with workspace/status/action/my-approval filters;
- mobile priority cards with detail drawer;
- request detail drawer with `申请详情` and `审批流程` tabs;
- timeline showing requester, approvers, decisions and timestamps;
- approve confirmation;
- reject dialog with required comment up to 500 characters;
- cancel confirmation for the requester;
- policy creation/edit dialog with threshold, expiry and eligible approvers;
- persistent evidence strip for pending count, my pending decisions, active rules, catalog revision and execution-adapter state.

Use TDesign React and TDesign Icons. Uiverse/Morphicons remain fallback-only. No fabricated live counts or approvals.

## Visual reference translation

- quiet white/slate canvas and compact blue active state;
- rules and requests are separate tabs;
- filters sit above a dense table, not inside dashboard cards;
- request detail prioritizes approve/reject actions and a change-fact table;
- process history is a timeline with actor, status and time;
- rejection requires a bounded reason; approve uses a concise confirmation.

RAG4C's signature difference is the governance evidence strip and explicit execution boundary.

## Security and compliance

- no approval self-decision;
- tenant-safe account/group predicates on every read and write;
- request snapshots reject secrets, tokens, credentials and raw invitation links;
- decision facts are immutable;
- execution tickets are returned once and stored only as digests;
- audit and business mutation share one transaction;
- missing `0025` fails closed;
- no real production database upgrade, retention deletion or external publication is performed.

## Testing

- migration constraints and downgrade;
- policy uniqueness and approver scope;
- request idempotency and snapshot redaction;
- no self approval, threshold approval, any-reject terminal behavior;
- cancellation, expiry and revision fencing;
- audit rollback;
- one-time ticket exact scope consumption;
- TDesign desktop/dark/375/280 behavior;
- zero console errors and zero unknown requests in Playwright.

## Protected paths

Do not modify:

- `frontend/src/retrieval-quality/**`
- `core/retrieval_experiment_runner.py`
- `server/retrieval_experiments_api.py`
- related retrieval experiment tests.
