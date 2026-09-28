# Notification materializer strategy registry (2026-09-23)

## Summary

Notification source materialization and reconciliation discovery now dispatch through a source-kind policy registry backed by the existing `core.providers.ProviderRegistry`. The two existing public entry points remain compatibility adapters with unchanged signatures:

- `materialize_quality_alert_notifications(...)`
- `materialize_pending_approval_notifications(...)`

Their existing transaction and authority logic remains in private implementation functions. The new strategy layer is deliberately separate from `core/notification_source_kinds.py` (projection shapes), `core/notification_receipt_kinds.py` (receipt handoff), and notification route adapters.

## Design and extension points

`core/notification_materializers.py` defines:

- `NotificationMaterializationRequest`: source identity, expected revision/digest, tenant, engine, timestamp, and source-specific scope. The mapping scope is copied and exposed read-only so the frozen request cannot be mutated through a nested dict.
- `NotificationSourceDiscoveryContext`: one reconciliation engine/session and tenant/timestamp scope.
- `NotificationMaterializerPolicy`: stable reconciliation order plus synchronous `materialize` and `discover` strategies.
- A typed `ProviderRegistry` with `register_notification_materializer`, `unregister_notification_materializer`, and `notification_materializer_snapshot` helpers. The mutable registry and one-time built-in installer are private; built-in keys are reserved before initialization, all public registration checks share the install lock, and direct dispatch validates built-in callback identity fail-closed.

The built-in `quality_alert` policy has order 10 and the `approval_pending_for_me` policy order 20. Built-ins are required, frozen against replacement/removal, and checked when the reconciliation snapshot is pinned. `reconcile_notification_sources` discovers all work using one session, releases that session, then materializes in policy/discovery order. Final response notification-key sorting is unchanged.

For an application-owned source kind, register a policy with synchronous callbacks. Direct dispatch uses `materialize_notification_source(request)`. To include it in reconciliation, its discovery callback must return requests scoped to the supplied engine, tenant, timestamp, and matching source kind. Registration is runtime behavior only: it does **not** expand notification DB constraints, source/category/route vocabularies, authorization, OpenAPI, or frontend contracts. An end-to-end new persisted source kind still requires a separately reviewed schema/contract change.

Example shape:

```python
register_notification_materializer(
    "custom_notice",
    NotificationMaterializerPolicy(
        order=30,
        materialize=materialize_custom_notice,
        discover=discover_custom_notices,
    ),
)
```

The policy callbacks must be synchronous. Discovery must return a sequence of `NotificationMaterializationRequest`; materialization must return a mapping compatible with the existing notification result shape.

## Invariants preserved

- Existing row locks, tenant/dataset/approver authority checks, revision/digest fences, safe payload construction, atomic bundle persistence, idempotency, and error classification remain in the original built-in implementation bodies.
- Quality alerts remain reconciled before pending approvals; each built-in query keeps its existing deterministic row ordering.
- Discovery still occurs in one SQL session; materialization remains after that session closes and uses each implementation's existing transaction boundaries.
- Public built-in materializer signatures and returned dict shape remain compatible.
- No database migration, OpenAPI/frontend change, receipt/handoff change, route change, or authorization change was introduced by this slice.

## Verification

- Focused notification regression: `pytest -q tests/test_enterprise_notification_materializer.py tests/test_enterprise_notification_receipts.py tests/test_notification_receipt_kinds.py tests/test_notification_route_adapters.py tests/test_notification_source_kinds.py tests/test_enterprise_notification_center_core.py` — **65 passed**.
- `ruff check` on the materializer/route-adapter implementation and tests — passed.
- `git diff --check` — passed.
- Reverse validation temporarily disabled registry lookup in `materialize_notification_source`; the registered real-quality-flow integration test failed as expected. The source file was restored and byte-for-byte equality verified.
- Materializer-only regression on the final snapshot: `pytest -q tests/test_enterprise_notification_materializer.py` — **21 passed**. This rerun includes the final private-installer API boundary.
- Independent subagent review initially found three P2 issues: mutable public registry could replace built-ins on direct dispatch, reserved built-in keys had an initialization race, and dynamic reconciliation discovery lacked an integration assertion. Fixed by privatizing the registry, reserving keys under the shared install lock, validating built-in policy identity on direct dispatch, and exercising a registered discovery callback through reconciliation. Follow-up review identified the built-in installer as another overly public entry point; renamed it private and asserted the public symbol is absent. Final follow-up review **PASS**, no remaining finding.

## Follow-up / handoff

Review the policy/registry before adding source kinds. Keep DB vocabulary and route/receipt policy changes explicit and separately tested; do not treat a runtime materializer registration as permission to persist a new kind. Do not add generic factories, autodiscovery, or a new registry framework—this design intentionally reuses `ProviderRegistry`.
