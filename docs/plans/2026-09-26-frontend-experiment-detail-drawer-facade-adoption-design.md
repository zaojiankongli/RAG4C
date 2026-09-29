# Frontend ExperimentDetailDrawer facade adoption design (2026-09-26)

## Context

The retrieval-quality detail drawer is one of the last direct TDesign
consumers. It already uses the shared `Drawer` facade, but still imports
`Alert`, `Button`, and `Tag` directly from `tdesign-react`. This leaves the
detail path outside the same renderer-policy boundary used by the list,
composer, results, and judgment surfaces.

## Decision

Migrate only `ExperimentDetailDrawer`:

- import `Alert`, `Button`, and `Tag` from `../../ui`;
- replace the historical outline button prop with the facade default button;
- preserve `Tag variant="light-outline"` and `Alert theme/title/message`;
- keep focus restoration, immutable facts, agreement, judgment editors,
  trace disclosure, and Eval capability copy unchanged;
- add native/TDesign component regressions and a source boundary guard.

## Compatibility and failure boundary

- close still invokes the caller callback and restores focus to the opener;
- error and conflict alerts remain semantic alerts with the same copy;
- owner/revision and experiment-id tags retain their status-independent
  appearance;
- no route, API, backend, storage, authorization, or persisted-state change;
- the already shared `Drawer`, `AgreementPanel`, `JudgmentEditor`, and
  `SafeTracePanel` contracts are not widened by this slice.

## Verification

1. Add failing behavior/source tests for the facade imports, native controls,
   alert/tag content, and focus restoration.
2. Implement the smallest consumer migration.
3. Run focused drawer/UI tests, TypeScript, focused ESLint, and diff-check.
4. Request an independent sub-agent review and fix valid findings, including
   native/TDesign semantics.
5. Write the handoff/progress entry, then rerun the full frontend suite, build,
   lint, and `git diff --check`.
