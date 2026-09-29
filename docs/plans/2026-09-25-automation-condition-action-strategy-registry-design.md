# Stage25 automation condition/action strategy registry design

## Context

`core/enterprise_automation_workflows.py` currently keeps condition parameter
schemas, condition evaluation, action parameter schemas, and action target
selection in separate `if`/`dict` branches. The HTTP model in
`server/enterprise_automation_workflows_api.py` repeats the condition/action
parameter vocabulary, so adding or correcting one existing implementation
requires synchronising multiple consumers.

The current database CHECK constraints and OpenAPI vocabulary are intentionally
closed in this slice. This change does **not** add a trigger, condition, or
action code. A future persistent code still requires its own contract/database
change. The registry is therefore an implementation seam for the existing
codes and for runtime-only strategies used by trusted internal integrations;
canonical rule persistence remains fail-closed against the frozen contract
sets.

## Decisions

1. Reuse `core.providers.ProviderRegistry`; do not introduce another registry
   kernel.
2. Add separate condition and action strategy registries. A condition strategy
   owns its parameter declarations and synchronous evaluator. An action
   strategy owns its parameter declarations and target-reference adapter.
3. Keep the public `AUTOMATION_*_CODES` sets closed and unchanged. Canonical
   rule revisions/action plans still validate against those sets before any
   database write.
4. Derive parameter names and kinds from the strategy declarations. The pure
   authority normalises values with its existing safety helpers; the HTTP
   boundary uses the same declarations for shape/type checks.
5. Registration is fail-closed: duplicate names, malformed parameter
   declarations, missing callback shape, async callbacks, and non-callable
   callbacks are rejected at registration. Built-in factories are reserved;
   raw registry replacement/removal is detected by the resolver.
6. Action target selection remains side-effect free. The service still creates
   durable action-request rows and never dispatches an external side effect.

## Preserved contracts

- Existing built-in code sets, error ordering, canonical digests, safe-field
  filtering, target selection, API/OpenAPI models, database constraints, and
  service transaction/idempotency behavior.
- Unknown or unregistered persistent condition/action codes remain rejected.
- No plugin discovery, entry points, or second registry implementation.

## Verification plan

- Add registry tests for built-in declaration parity, duplicate/invalid/async
  registration rejection, raw built-in tampering fail-closed behavior, and
  runtime-only condition/action strategies through the real pure target paths.
- Keep the existing Stage25 core/service/API/readiness suites unchanged and
  passing.
- Run Ruff, `.venv` `py_compile`, and `git diff --check`.
- Temporarily bypass one dispatch seam and confirm the dynamic-path test fails,
  then restore the source byte-for-byte.
- Request an independent sub-agent review, fix all actionable findings, and
  run a follow-up review.

## Implementation outcome（2026-09-25）

Implemented in `core/automation_rule_strategies.py`, `core/enterprise_automation_workflows.py`,
`core/enterprise_automation_workflows_service.py`, and
`server/enterprise_automation_workflows_api.py`. Added
`tests/test_automation_rule_strategy_registry.py`.

The final design keeps the API/DB code vocabulary frozen and treats new persistent codes as a separate
contract change. Runtime-only strategies are available only on pure/internal paths. Contract parity,
registration-time shape/async checks, canonical event input, API declaration validation, tamper detection,
and reverse dispatch validation are all covered by tests.

Final verification: registry/core focused **60 passed**; Stage25 combined **120 passed**; Ruff,
`.venv` `py_compile`, and `git diff --check` passed. Independent sub-agent follow-up review **PASS**.
