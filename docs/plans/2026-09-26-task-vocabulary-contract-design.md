# Task vocabulary contract design (2026-09-26)

## Context

The task-operations path already has a source-kind registry, but the same
category and status vocabulary is still repeated in several layers:

- `core/task_source_kinds.py`;
- `core/enterprise_task_operations.py`;
- `core/enterprise_task_operations_service.py`;
- `core/catalog_schema.py`;
- `server/enterprise_task_operations_api.py`.

The frontend intentionally has a separate display vocabulary (`content` and
`source`, plus `completed` for the backend `succeeded` fact). The current
mapping is compatible but is spread across model/API parsing code.

This slice must reduce backend drift without widening the database or public
contract accidentally.

## Decision

Add one backend-only `core/task_vocabulary.py` contract that owns:

- canonical persisted task categories;
- legacy input aliases and their canonical projection;
- the explicit HTTP compatibility category set, kept separate from backend
  aliases and canonical persisted values;
- canonical normalized task statuses;
- accepted status aliases;
- the explicit fact-status-to-display-status projection for
  `succeeded → completed`.

Existing registries and validators consume this contract instead of copying
canonical sets or alias maps. The database CHECK values remain canonical and
closed. Alias acceptance remains an input compatibility concern only; aliases
are never written as persisted category/status values.

The frontend display vocabulary remains a separate boundary in this slice.
Its parity is documented and will be migrated in a follow-up frontend task
model contract slice, rather than importing Python-owned semantics into the
TypeScript build.

## Non-goals

- no new task category, source kind, or persisted status;
- no database migration or OpenAPI vocabulary expansion;
- no change to reconciliation `started`/`running` compatibility;
- no change to the frontend's `content`/`source` display labels;
- no HTTP app-mount claim (the environment still lacks optional `pymysql`).

## Verification plan

1. Add contract and parity tests first; observe the missing-module failure.
2. Implement the contract with immutable tuples/mappings and strict
   canonicalization.
3. Wire the backend registry, authority, service, schema, and API boundary to
   the contract while keeping DB values canonical and request aliases bounded.
4. Run task vocabulary/core/service/API/schema regressions and Ruff.
5. Request an independent sub-agent review, fix findings, and rerun the
   focused suite.
6. Write the handoff and update the cumulative progress ledger.
