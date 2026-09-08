# KnowledgeOps production rollout preflight — 2026-08-25

## Scope

This report records read-only production observations and disposable-environment verification for the `feature/knowledgeops-authority` branch. No production migration, backfill apply, consistency repair, deletion repair, or authority-mode activation was performed.

## Application verification

- Backend: `1273 passed, 6 skipped` via `uv run pytest -q`.
- Frontend: `402 passed` via `npm test -- --testTimeout=10000`.
- Frontend TypeScript/Vite production build: passed.
- Frontend ESLint: passed.
- Git worktree: clean at the verification boundary.
- Quarantined and integration stashes were retained unchanged.

## Disposable MySQL drill

A temporary MySQL 8.0 instance was initialized under the workspace, bound only to `127.0.0.1`, and removed after verification.

Verified chain:

```text
0001_base
→ 0013_source_control
→ downgrade to 0006_source_id
→ re-upgrade to 0013_source_control
```

Result:

```text
status = passed
revision = 0013_source_control
head_revision = 0013_source_control
```

The drill also verified MySQL-specific `TEXT` backfill, quoted `trigger` checks, binary idempotency-key collation, `DATETIME(6)` lease fields, immutable retrieval triggers, and Catalog head-manifest inspection.

## Production read-only observations

Observed on 2026-08-25 without writes:

```text
schema revision       = 0007_chunk_rev
application head      = 0013_source_control
schema status         = behind
schema mode           = verify
chunk authority mode  = shadow
ingest ledger mode    = shadow
document scope        = default/default
document count        = 97
```

A content-free rollback/preflight snapshot was exported to:

```text
backups/knowledgeops-production-preflight-20260825-214622.json
```

The snapshot contains schema metadata, table names, columns, unique constraints, and row counts. It does not contain document or chunk content.

## Dry-run behavior

The report-only Backfill and Reconcile commands were invoked without `--apply` or `--repair`.

Both stopped before scanning because the production Catalog is behind application head:

```text
catalog schema 0007_chunk_rev is behind application head 0013_source_control
```

This is the required fail-closed behavior. Therefore no authoritative drift number is claimed yet; a drift report cannot be trusted until the production schema reaches the verified application head.

## Rollout blockers

Before any production activation:

1. Schedule a maintenance window and capture an independently verified database backup.
2. Review the `0007 → 0013` migration plan and expected lock/DDL duration against the production MySQL version.
3. Upgrade the production Catalog to `0013_source_control`; do not change authority modes in the migration step.
4. Run Catalog schema verification and confirm the full head manifest, retrieval immutability triggers, Source execution constraints, and tenant/dataset foreign keys.
5. Run chunk-authority backfill in dry-run mode with retained pseudonymized reports.
6. Run report-only Catalog/Milvus reconciliation. Keep repair disabled until a confirmable projection-generation authority exists.
7. Keep `chunk_authority_mode=shadow` and `ingest_ledger_mode=shadow` until drift is zero and operational evidence is complete.
8. Validate durable deletion, source dispatch, QA review, expiry, retrieval serving fences, dead letters, and rollback procedures in the maintenance environment.
9. Only after drift is zero and rollback gates pass may a separate reviewed change switch authority mode to Active.

## Explicitly prohibited in this preflight

- Production Alembic upgrade or downgrade
- `backfill_chunk_authority --apply`
- `reconcile_chunk_authority --repair`
- Consistency repair
- Direct Milvus or graph purge/reset
- `chunk_authority_mode=active`
- Applying or dropping the quarantined deletion-fence stash
