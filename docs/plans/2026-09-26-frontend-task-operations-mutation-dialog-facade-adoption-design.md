# Frontend Task Operations mutation dialog facade adoption design

## Context

`TaskMutationDialog` is the remaining destructive Task Operations consumer
with direct TDesign `Checkbox`, `Dialog`, and `Tag` imports. It is a
revision-fenced confirmation boundary: the page must not submit retry/cancel
operations until the user explicitly confirms, and read-only/saving states
must remain fail-closed.

The shared UI facade currently has `Modal` but no renderer-selectable
`Dialog`/`Checkbox` vocabulary matching this consumer. A direct `Modal`
substitution would lose TDesign-compatible close/confirm props and make the
mutation boundary harder to migrate safely.

## Decision

- Add renderer-policy `dialog` and `checkbox` components.
- Implement native/TDesign-compatible `Dialog` and `Checkbox` facades with
  body attachment, focus trapping, Escape/overlay guards, explicit modal
  semantics, confirm loading/disabled mapping, and boolean checkbox change
  values.
- Migrate `TaskMutationDialog` to shared `Dialog`, `Checkbox`, and `Tag`.
- Preserve confirmation reset behavior, retry/cancel copy, safe boundary
  note, read-only/saving/action-allowed disable fences, focus return, and
  controller callback ownership.

## Verification plan

1. Add source/behavior tests for the consumer and compatibility contracts
   before implementation and observe the expected red boundary.
2. Implement the renderer registry and native/TDesign facades.
3. Run mutation-dialog, Task Operations, shared UI, TypeScript, ESLint,
   build, and `git diff --check` verification.
4. Request independent sub-agent review, fix all findings, rerun checks,
   and write the handoff/progress entry.

## Scope boundary

This slice does not migrate `TaskOperationsCenter`, the direct `Alert`/
`Loading` state notices in `taskOperationsUi.tsx`, or unrelated enterprise
dialogs. The shared Dialog/Checkbox adapters are added only as the
compatibility boundary required by this consumer.
