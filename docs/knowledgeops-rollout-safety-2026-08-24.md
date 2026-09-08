# KnowledgeOps rollout scan safety contract

**Created:** August 24, 2026

**Revised:** August 25, 2026
**Applies to:** `backfill_chunk_authority.py` and `reconcile_chunk_authority.py`

## Stable batch snapshot validation

Cursor version 5 binds every paginated report-only rollout scan to a signed dataset snapshot.
The snapshot covers the number of documents in the tenant/dataset scope and a SHA-256
fingerprint over every document's:

- `id`;
- `created_at`;
- `updated_at`;
- `content_revision`;
- `desired_index_revision`;
- `status`.

Every batch now constructs its complete read-only plan inside one database-level snapshot:

```text
open stable snapshot transaction
→ fingerprint/count
→ keyset query
→ ChunkHead reads and external Milvus comparison
→ complete read-only plan
→ close snapshot transaction
→ fresh postflight fingerprint/count
```

The snapshot policy is dialect-specific:

- SQLite uses `BEGIN IMMEDIATE`, which prevents a concurrent writer from changing and restoring a
  document between fingerprinting and keyset selection;
- MySQL/MariaDB and PostgreSQL explicitly use `REPEATABLE READ`;
- other SQLAlchemy dialects use one explicit transaction and remain unsupported for a production
  rollout until their snapshot semantics are verified.

The external Milvus reads intentionally run while the database snapshot is open. Rollout is a
maintenance workflow, so snapshot correctness takes precedence over minimizing transaction
duration. The fresh postflight remains a supplementary detector for persistent changes after the
snapshot closes; preflight/postflight equality is no longer used as proof of immutability.

Any persistent insert, delete, ordering-key change, status change, revision change, or
dataset-generation change fails closed with:

```text
rollout scan snapshot assumptions were violated
```

Backfill `--apply` first builds an in-memory write plan, performs the postflight validation, and
only then writes ChunkHead rows. Each write also locks and revalidates the source Document facts.
No ChunkHead mutation occurs before the batch snapshot has passed its postflight check.

The operator must discard a failed cursor and restart from the first batch. Cursor version 4
values are rejected rather than upgraded implicitly.

## Repair is a non-paginated two-phase operation

Reconcile `--repair` does not accept `--cursor`. It always executes one complete scope scan:

```text
full stable snapshot scan
→ build complete repair plan in memory
→ postflight fingerprint/count validation
→ open one repair transaction
→ lock every planned Document in deterministic ID order
→ CAS content_revision / desired_index_revision and snapshot facts
→ create or bind Attempts and IndexOperations
→ commit all plans together
```

A stale Document causes the complete repair transaction to roll back and raises
`repair plan became stale before enqueue`. The command does not return a report claiming any
operation was enqueued. Attempt creation, `Document.current_attempt_id`, and every repair
IndexOperation therefore share one commit boundary. This also prevents the repair tool from
invalidating its own next cursor; report-only reconcile remains resumable and paginated.

## Strict resume cursor envelope

Resume cursors are unpadded canonical Base64URL values containing canonical JSON. Decoding fails
closed when the token contains:

- invalid Base64URL characters or padding;
- a non-canonical Base64URL representation;
- non-canonical JSON;
- missing or additional fields;
- an unsupported version or a non-integer version such as `5.0`;
- booleans supplied where integer counts are required;
- non-canonical timestamps, non-string IDs, unpaired keyset time/ID fields, or non-hex hashes;
- an invalid HMAC signature;
- a cursor from the other rollout command or another report secret.

The exact signed fields are the snapshot generation, snapshot count/fingerprint, upper keyset
bound, and last processed key.

## Retained report privacy

The unkeyed operational `manifest_hash` remains available only inside the in-memory report and
durable repair payload, where it provides stable deduplication across report-secret rotation.
Retained JSON never emits that raw hash. It emits only:

```text
manifest_ref = HMAC(report_secret, domain, manifest_hash)
```

This prevents retained reports from acting as an offline dictionary oracle for predictable
document or chunk identifiers while preserving internal repair deduplication. Tenant, dataset,
document, and chunk identifiers remain HMAC pseudonyms. The existing
`--emit-sensitive-resume-cursor` flag is the only explicit resume-cursor output channel.

## Batch size and rollout cost

The default report-only batch size is now **1,000** instead of 100. Each retained summary includes:

- estimated batch count;
- total preflight/postflight fingerprint scan count;
- estimated document rows scanned;
- a warning when pagination requires more than one batch.

For example, 50,000 documents with `--batch-size 1000` require approximately 100 full
fingerprint scans, or 5,000,000 document-row reads. Operators should increase the batch size when
memory and Milvus capacity permit, or run the rollout in a maintenance window. Repair mode always
uses one complete scan and therefore does not incur paginated O(N² / batch-size) validation.

## Rollout report key

`RAG4C_ROLLOUT_REPORT_SECRET` must be an unpadded, canonical Base64URL key that decodes
to at least 32 bytes. Runtime validation cannot prove entropy, so production keys must be
generated by a CSPRNG and stored in the deployment secrets manager.

Generate a suitable key with:

```powershell
uv run python scripts/rollout_security.py generate
```

The validator rejects malformed encodings and obvious human-authored patterns, including
low-diversity values, repeated blocks, sequential strings, fixed hexadecimal patterns, and known
example/change-me markers.

## Operational prohibition during finalization

The August 25 finalization verification is limited to isolated temporary SQLite databases and
static checks. It must not connect to the production database, execute `--apply`, or execute the
production `--repair` command. Production rollout remains gated by an approved release review.
