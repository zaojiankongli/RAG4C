# Projection dead-letter requeue policy registry handoff (2026-09-24)

## Change

- Replaced static supported-pair sets in
  `core/projection_target_contract.py` with an exact
  `target_store:operation` `ProviderRegistry`.
- Preserved built-in Milvus, Graph, and catalog-finalizer requeue pairs as
  reserved, immutable policies with their existing semantic families.
- Added a synchronous target validator for custom pairs. It receives a frozen,
  copied validation context and may only reject after common host validation;
  it cannot replace scope, generation, chunk, source, or delete-parent checks.
- Changed both `knowledge_consistency_api` dispatch and projection-runtime
  activation preflight to query the same registry live. Missing/unknown pairs
  remain fail-closed.
- Preserved linked-replay idempotency ordering and replay construction from the
  canonical original operation. No response, database, migration, OpenAPI, or
  frontend contract changed.
- Added API regressions for custom live dispatch, exact replay-field copying,
  unregistered target rejection, dispatch-bypass rejection, validator rejection,
  and canonical-generation fence ordering. Added Graph delete-child coverage.

## Verification

- Projection requeue policy + knowledge consistency API + document delete +
  target producer/registry regression after review remediation: **74 passed**.
- Ruff check across changed Python modules/tests — **passed**.
- Ruff format check for the new policy module and its focused tests — **passed**.
- `py_compile` for the policy module and API — **passed**.
- `git diff --check` — **passed**.
- Independent code review found a P2: the raw registry was exported and could
  bypass the registration guard. The registry is now module-private; lookup
  revalidates factory results and built-in families, and preflight validates
  resolved policies rather than only counting keys. A permanent regression
  attempts raw built-in replacement and malformed custom registration.
- Follow-up independent review — **PASS**, the P2 is closed with no remaining
  blocking findings. The reviewer independently reran 10 policy/preflight
  tests and 3 API/idempotency regressions.

The API tests used a temporary SQLite URL through
`RAG4C_CATALOG_DB_URL` so importing the bridge did not require a MySQL driver.

## Boundary

This closes the dead-letter requeue policy seam only. The consistency summary's
projection audit still reads Milvus through `reconcile_chunk_authority` and
remains best-effort/non-confirmable. A custom-target report reader needs a
separate snapshot and authority design; this change does not claim axis #3 is
fully complete.

Design record: `docs/plans/2026-09-24-projection-requeue-policy-registry-design.md`.
