# RAG4C Enterprise Audit Compliance Center Design

**Date:** 2026-08-26  
**Timezone:** Asia/Shanghai  
**Stage:** 11  
**Target revision:** `0023_enterprise_audit_compliance`

## Objective

Turn tenant audit history into an enterprise compliance control plane with retention policy, legal holds, tamper-evident exports, explicit retention previews, and audited deletion execution.

## Database contract

### `tenant_audit_retention_policies`

- one row per tenant;
- audit retention days and export retention days;
- status `active | paused`;
- revision > 0;
- last preview/execution evidence;
- creator/updater/executor tenant-member FKs;
- policy bounds: 30–3650 audit days and 1–365 export days.

### `tenant_audit_legal_holds`

- status `active | released`;
- name, reason, optional sequence/time bounds;
- revision and created/released evidence;
- active-name uniqueness per tenant through nullable `active_name_key`;
- legal hold prevents matching audit rows from retention deletion.

### `tenant_audit_export_jobs`

- format `ndjson | csv`;
- status `queued | running | completed | failed | expired`;
- allowlisted filters only;
- requested sequence/time range;
- relative object key, SHA-256, byte size, row count;
- revision and request/completion/expiry evidence;
- no absolute path, database URL, bearer token, raw request headers, or arbitrary filter SQL;
- tenant-leading indexes and tenant-scoped requester FK.

All control-plane mutations reuse `tenant_control_mutation_requests`.

## Export storage

- Controlled root: `data/audit-exports` by default, injectable in tests.
- Object key is generated server-side and must resolve inside the root.
- Write to a temporary file, fsync, calculate SHA-256, then atomic replace.
- NDJSON and CSV fields are allowlisted.
- Before/after snapshots are serialized as bounded JSON strings.
- Spreadsheet-formula prefixes in CSV are escaped.
- Download verifies current file size/hash before streaming.
- Expired/corrupt/missing files fail closed.

## Retention

- Preview is read-only and returns candidate count, protected count, cutoff and sequence bounds.
- Execute requires owner role, expected policy revision, exact preview fingerprint, business reason, confirmation string, and Idempotency-Key.
- Retention never deletes rows covered by an active legal hold.
- Retention writes a summary audit event after deletion in the same transaction.
- No automatic scheduler is enabled in Stage 11; operators invoke preview/execute explicitly.
- Production execute remains prohibited in this development stage.

## Authorization

- Owner: policy update, legal hold create/release, retention execute, exports.
- Admin: retention preview and exports; cannot execute deletion or release legal holds.
- Editor/member: read capability evidence only.
- Roles are re-read in the write transaction.

## API

- `GET/PUT /api/enterprise/compliance/retention-policy`
- `POST /api/enterprise/compliance/retention/preview`
- `POST /api/enterprise/compliance/retention/execute`
- `GET/POST /api/enterprise/compliance/legal-holds`
- `POST /api/enterprise/compliance/legal-holds/{id}/release`
- `GET/POST /api/enterprise/compliance/audit-exports`
- `GET /api/enterprise/compliance/audit-exports/{id}`
- `GET /api/enterprise/compliance/audit-exports/{id}/download`

All mutations require `Idempotency-Key: 1..128`.

## Frontend

Add a TDesign Compliance Center to Enterprise Management:

- policy evidence and revision;
- retention preview facts and protected rows;
- dangerous execute confirmation Dialog;
- legal hold table/cards and release action;
- audit export wizard, job table, integrity hash and download action;
- explicit `manual_execution_only` evidence;
- desktop/dark/375/280 cards and drawers;
- never display absolute file paths or internal storage roots.

## Production boundary

No real production deletion, external object storage, scheduled cleanup, real backup/restore, or external publishing is performed. Tests use temporary SQLite and temporary export directories.
