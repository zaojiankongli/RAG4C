# Knowledge Governance Center Frontend Design

**Date:** 2026-08-25
**Status:** Approved

## Goal

Add a frontend-only Knowledge Governance Center for the authenticated tenant and dataset scope. It governs the dataset profile and QA lifecycle and inspects immutable document versions without changing backend contracts or substituting demo facts.

## Architecture

The `governance` route is a lazy page under the knowledge-base navigation and remains distinct from taxonomy. `KnowledgeGovernancePage` composes focused native-TDesign components from `src/governance`; typed models and API functions mirror the existing FastAPI/OpenAPI contracts, while hooks own request fencing, refetches, conflict states, and fail-closed scope behavior.

## Scope authority

The page consumes `KnowledgeWorkspaceContext` for tenant and dataset identifiers and `workspaceScope` for the authenticated actor token. Governance requests require a non-empty tenant ID, dataset ID, and actor token. Missing authenticated scope prevents all requests and renders a closed-scope state. Offline and API failures never use demo data.

## API contracts

- `GET/PATCH /api/knowledge-bases/{dataset_id}`
- `POST /api/knowledge-bases/{dataset_id}/archive|restore|disable`
- `GET/POST /api/knowledge-bases/{dataset_id}/qa`
- `PATCH /api/knowledge-bases/{dataset_id}/qa/{qa_id}`
- `POST /api/knowledge-bases/{dataset_id}/qa/{qa_id}/review|expire|restore`
- `POST /api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives`
- `DELETE /api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives/{alternative_id}?expected_revision=N`
- `GET/POST /api/knowledge-bases/{dataset_id}/documents/{document_id}/versions`

Dataset and QA mutations carry their exact revision CAS fields. Version creation carries `expected_current_revision` and optional `expected_current_version_id`. A 409 never produces a success state and always exposes a refresh action.

## UI

A top authority banner identifies the authoritative Catalog scope. The profile section shows revision, status, visibility, owner, language, capabilities, usage, timestamps, and safe policy summaries. Secret-like policy values are omitted; credential references appear only as badges. Profile editing and dangerous lifecycle actions use native TDesign drawers/dialogs and confirmations.

The QA section provides review/lifecycle/origin filters, a horizontally owned native table, ten rows per client page, create/edit/review/expire/restore actions, and an alternatives panel. Review and lifecycle statuses remain separate so pending, approved, rejected, active, and expired facts are never conflated.

The version inspector requires an explicit document ID, shows an immutable revision timeline, and provides an advanced create-version form containing the exact backend fields.

## Error and data behavior

All visible errors are projected from `ApiError` kind/status and safe structured codes. Raw response bodies, arbitrary backend messages, secrets, credential-bearing URLs, and stack traces are not rendered. Successful mutations refetch the relevant fact set. Requests are aborted or ignored when scope/generation changes.

## Accessibility and responsive behavior

Every control has an accessible name, focus returns after modal surfaces close, and confirmations are keyboard operable. The QA table has a focusable horizontal-scroll owner at 375px. Drawers/dialogs use narrow-screen sizing. Existing light/dark tokens and AA-safe semantic colors are reused.

## Verification

Use strict TDD for route, API/model, hooks, and page/component behavior. Run focused and full Vitest, build, lint, 375px/1440px light/dark browser checks, and axe checks. Commit only paths under `frontend/`.
