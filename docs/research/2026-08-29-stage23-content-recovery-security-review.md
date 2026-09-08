# Stage 23 Content Recovery Security Review Status

Date: 2026-08-29
Status: Complete

## Review input

Independent read-only review reported 1 Critical, 10 Important and 5 Minor findings.

## Fixed and verified

- Dataset serving generation now increments on recycle and restore.
- Dataset ACL / Workspace authorization is enforced for recovery mutations.
- Restore rejects Document content/version-head drift after recycle.
- Explicit Approval Policy must be active and `document_purge`.
- Generic `document_purge` Approval creation is bound to an existing recycled Entry, exact revision, elapsed retention and zero active holds.
- Active Purge Request rows require an Approval Request reference at database level.
- Entry read projection validates stored canonical recycle snapshot and digest.
- Readiness validates the stored immutable recycle snapshot instead of rebuilding it from mutable lifecycle columns.
- Readiness and detail reads reject Recovery Entries without a first Recovery Event.
- Legal Hold frontend/server field contract uses `safe_reason` consistently.
- Recovery reasons reject Bearer, credential markers and URI schemes.
- Documents capability defaults fail closed; missing capability no longer falls back to durable delete.
- Recycle API can safely derive Dataset scope when the frontend omits dataset_id.
- Default `document_purge` Approval Policy lookup is reachable.

Focused evidence:

```text
Backend security/migration focused: 23 passed
Frontend Recovery/App/Documents focused: 50 passed
Ruff: passed
TypeScript: passed
Alembic head: 0033_enterprise_content_recovery
Stage23 standalone gate tests: 6 passed
```

## Final review result

```text
Critical: 0
Important: 0
Minor: 2
```

The two remaining Minor items are crash-window/operator-reconciliation concerns:

1. If the process is terminated during the compensated Approval/Purge cross-transaction window, an operator may need to reconcile terminal facts. Decision-time Recovery validation prevents stale Approval from authorizing purge.
2. If the process terminates after some Bulk children commit but before the parent ledger completes, the parent may remain `in_progress`; deterministic child keys prevent duplicate recycle and allow operator reconciliation.

## Final gates

```text
Backend Stage 23 integration: 229 passed
Frontend Stage 23 focused: 63 passed
TypeScript / Prettier / Vite build: passed
Playwright matrix: 12/12
Playwright scenarios: 15/15
Fresh PNG: 27/27
Console/Page/Unknown/Request/Leak/Overflow errors: 0
Standalone gate tests: 6 passed
Alembic head: 0033_enterprise_content_recovery
```

No production migration, recycle, restore, legal hold, Approval execution, purge or durable delete was performed.
