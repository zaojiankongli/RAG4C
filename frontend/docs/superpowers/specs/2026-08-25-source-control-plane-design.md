# Source Control Plane Frontend Design

**Date:** 2026-08-25
**Status:** Approved

## Goal

Replace the projection-only Knowledge Sources page with a frontend-only Source Control Plane backed exclusively by the authenticated Source API. The page manages local-directory and GitHub sources, durable manual synchronization, execution history, retry, and projection-fence visibility without demo facts or backend changes.

## Architecture

`KnowledgeSourcesPage` is the composition root and receives an `active` signal from the kept-alive application router. Domain code lives under `src/sources-control`: strict API contracts, safe projections, durable idempotency storage, cancellable hooks, focused TDesign surfaces, and responsive styles. The subsystem imports only the shared `request` primitive and existing workspace/token readers.

## Authority and scope

Requests require a non-empty actor token, tenant, and dataset. Missing scope fails closed and sends no request. Offline or API failures never fall back to document projections or mocks. Backend read/write/manage authorization remains authoritative.

## Source operations

The list renders type, status, mutation generation, last sync/error/counts, and connector-specific safe summaries. Exact credential references are never rendered; fixed secret/vault badges are used. Local paths are reduced to a safe leaf label. URI query strings are omitted from frontend summaries.

Create and edit use strict local-directory/GitHub forms. Patch, enable, and disable carry `expected_generation`. A 409 refreshes the authoritative source and requires operator review. Backend-sanitized allowlist/config rejection messages are shown for `knowledge_source_invalid`; arbitrary error bodies are not rendered.

## Synchronization semantics

Sync Now persists a case-preserving idempotency key before the request. Its identity binds tenant, dataset, source ID, source mutation generation, and canonical `force_full`/`dry_run` payload. Lost responses retain and replay the same key. A received 202 is always “已排队”, never completed, and reports replay truth from the backend.

The page explains at-least-once execution and source/dataset generation projection fences. Scheduled synchronization is shown only as an honest capability gap because no schedule schema or endpoint exists.

## Runs

Run history uses status/trigger filters and backend keyset cursors with ten rows per page. A local cursor stack provides previous navigation without exposing cursor values. Detail and item views keep execution state separate from run status, show attempts and source/dataset generations, and omit unnecessary internal identifiers. Failed current-generation runs expose exactly one in-flight manage retry and display backend replay status.

The current API does not expose `execution_next_attempt_at`; the UI explicitly says the next attempt/backoff is not provided rather than deriving it.

## Polling

Polling runs only while the kept-alive Sources route is active, the document is visible, and an observed run is pending/executing/failed at the execution layer. Requests and timers are cancelled on route deactivation, visibility loss, scope changes, filter changes, and unmount. Bounded backoff is 2, 3, 5, 8, 13, then 20 seconds; successful refresh resets it and visibility recovery refreshes immediately.

## UI and accessibility

Use existing TDesign-native controls and theme tokens. The signature operational device is an aligned generation-fence rail (`G<n>`). Desktop uses source/run tables and mobile uses source cards plus keyboard-focusable horizontal table owners. Dialogs/drawers restore focus, confirmations are keyboard operable, reduced motion is respected, and light/dark layouts support 375px and 1440px.

## Verification

Use strict TDD for models, API, idempotency, hooks, components, route activity, and CSS. Run focused and full Vitest with a 10-second test timeout, build, lint, browser checks at 375/1440 in light/dark, and axe. Stage and commit only `frontend/**` in one final commit.
