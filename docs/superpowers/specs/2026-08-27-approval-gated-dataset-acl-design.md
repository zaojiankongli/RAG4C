# RAG4C Approval-Gated Dataset ACL Disable Design

**Date:** 2026-08-27  
**Timezone:** Asia/Shanghai  
**Stage:** 14  
**Schema revision:** remains `0025_enterprise_approval_control`

## Objective

Connect the first real high-risk consumer to the Stage 13 approval authority. When an active `dataset_acl_disable` approval policy covers a knowledge base, direct ACL disable must fail closed and the operator must submit an approval request. A final approval issues a one-time ticket that can execute the existing Dataset ACL disable mutation exactly once.

## Policy enforcement

- Resolve the active policy for `dataset_acl_disable` and scope `knowledge_base:{dataset_id}` or wildcard/global scope.
- If no active policy matches, the existing owner/admin direct-disable flow remains unchanged.
- If a policy matches, the direct disable HTTP endpoint returns `409 dataset_acl_approval_required` with sanitized policy ID, policy revision, required approval count and expiry.
- The internal approval execution adapter calls the core ACL mutation directly; it does not call the public HTTP endpoint and therefore cannot be trapped by the direct-route policy gate.

## Approval request

The frontend submits:

```json
{
  "policy_id": "...",
  "resource_type": "knowledge_base",
  "resource_id": "dataset-id",
  "snapshot": {
    "dataset_id": "dataset-id",
    "expected_acl_revision": 7,
    "current_acl_mode": "dataset_acl"
  },
  "reason": "..."
}
```

The snapshot is sanitized and hashed by the Stage 13 authority. No execution ticket is stored in browser persistence.

## Consumer registry

Add an action-keyed execution adapter registry to the approval router:

- `dataset_acl_disable` → Dataset ACL consumer;
- unsupported actions → `execution_adapter_not_connected` before claim;
- list/detail responses expose connected action evidence without implying all actions are connected.

The approval adapter payload includes:

- tenant, approval request and stable execution IDs;
- action/resource scope;
- ticket consumer actor ID and role;
- requester ID;
- sanitized request snapshot and reason;
- request IP/header evidence.

## Dataset ACL adapter

The consumer validates:

- action is exactly `dataset_acl_disable`;
- resource type is `knowledge_base`;
- actor role is owner/admin;
- snapshot dataset ID matches resource ID;
- `expected_acl_revision` is a positive integer;
- current Dataset still belongs to the tenant and is in `dataset_acl` mode.

It invokes `disable_dataset_acl(...)` with a deterministic idempotency key derived from the approval execution ID. The ACL mutation keeps its own revision fence, authorization recheck, audit and idempotency ledger.

The approval authority then finalizes the request as `executed` or `execution_failed`. Adapter success followed by final audit failure leaves the request in `executing` and never automatically repeats the ACL mutation.

## API changes

- Approval router accepts an action adapter registry.
- Approval list/detail responses expose `connected_actions` and action-specific adapter state.
- Approval decision delivery preserves a one-time ticket in the response only when the threshold is reached.
- Direct ACL disable may return `dataset_acl_approval_required`.

No new database migration is required.

## Frontend

### Dataset ACL dialog

- Fetch active approval policies for `dataset_acl_disable`.
- If no policy: keep current `确认停用 ACL` direct mutation.
- If policy matched: show policy name, threshold, expiry and `提交审批申请`.
- On submission, show the authoritative request ID/status and a button to open `/enterprise/approvals`.
- Do not change ACL mode until the approved ticket is actually consumed.

### Approval Center

- Preserve final-approval ticket only in component memory.
- Show a one-time execution authorization dialog for a connected Dataset ACL action.
- Require explicit `执行已批准变更` confirmation.
- Consume ticket with complete action/resource/revision scope.
- On success show executed evidence and refresh request facts.
- Never write the ticket to localStorage, sessionStorage, DOM data attributes or logs.

## Security

- Direct API bypass is blocked only when a matching active policy exists.
- Requester cannot self-approve under the Stage 13 rules.
- Ticket scope, digest, revision and single consumption remain enforced.
- Dataset ACL revision is checked again at execution time.
- Unsupported actions remain fail-closed.
- No real production database migration or ACL mutation is performed during development tests.

## Testing

- active policy blocks direct endpoint;
- no policy preserves existing direct behavior;
- request snapshot and policy scope are exact;
- final approval returns ticket once;
- connected adapter executes ACL disable and both audits exist;
- stale ACL revision produces `execution_failed` without ACL change;
- ticket replay does not repeat ACL mutation;
- frontend direct/approval modes, transient ticket and no browser persistence;
- desktop/dark/375/280 Playwright with zero console errors.

## Protected paths

Do not modify retrieval-quality or retrieval-experiment protected files.
