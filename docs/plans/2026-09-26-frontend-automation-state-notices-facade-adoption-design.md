# Frontend Automation state notices facade adoption design

## Context

The Automation Workflows flow still imports TDesign state-notice controls
directly in its shared UI helpers, center-level authority notices, attention
board, and table loading/activity surfaces. The shared UI facade already owns
`Alert`, `Empty`, and `Spin`, while the flow's remaining buttons, tables,
timeline, and dialog controls are separate migrations.

## Decision

- Migrate `automationUi.tsx` to shared `Alert`, `Empty`, `Spin`, and `Tag`.
- Migrate `AutomationCenter` and `AutomationAttentionBoard` to shared
  `Alert`.
- Migrate only the state-notice controls in `AutomationTables`: `Alert` and
  `Loading` become shared `Alert` and `Spin`; unrelated TDesign table/button/
  timeline controls remain out of scope.
- Preserve loading, unavailable, error, partial, empty, capability, mutation,
  summary, and activity copy plus authority fail-closed behavior.
- Remove outer duplicate `role="alert"` wrappers where the shared `Alert`
  already owns the single accessible alert role.
- Do not change controller/API/model/backend contracts or mutation behavior.

## Verification plan

1. Add source-boundary and state-notice behavior tests before implementation.
2. Implement the isolated facade migration.
3. Run focused tests, the full Automation Workflows suite, TypeScript,
   focused ESLint, build, and `git diff --check`.
4. Request an independent sub-agent review, fix P0–P2 findings, rerun the
   affected checks, and write the handoff/progress entry.

## Scope boundary

This slice does not migrate Automation table/button/dialog/drawer/timeline
controls that are not state notices, and does not change state ownership,
data loading, authority policy, or backend behavior.
