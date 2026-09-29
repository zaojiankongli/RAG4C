# Frontend JudgmentEditor facade adoption design (2026-09-26)

## Context

`JudgmentEditor` still imports Alert, Button, InputNumber, Tag, and Textarea
directly from `tdesign-react`; only its Radio control already uses the shared
facade. The component is a contained retrieval-quality editor and can migrate
without changing the judgment mutation contract.

## Decision

Migrate the component to `../../ui`:

- use `Input.TextArea` for the note field;
- keep the existing Radio group, score conversion, conflict Alert, owner
  revision Tag, save Button, and read-only judgment facts;
- normalize the TextArea facade so `maxLength` reaches both native and TDesign
  renderers;
- extract the text value from the facade's event-shaped native/TDesign
  `onChange` payload without changing `JudgmentDraft`.

## Compatibility and failure boundary

- Clearing score continues to map to `null`, not `NaN`.
- Existing accessible labels, conflict copy, revision text, and callback
  payloads remain unchanged.
- Framework-only TextArea props are consumed by the shared facade.
- No route, API, backend, storage, or persisted-state changes.

## Verification

1. Add a failing JudgmentEditor behavior/source test.
2. Implement the facade migration and TextArea maxLength mapping.
3. Run focused JudgmentEditor/ExperimentDetailDrawer/UI tests, TypeScript, and
   focused ESLint.
4. Request an independent sub-agent review and fix valid findings.
5. Write the handoff/progress entry, then rerun the full frontend suite,
   build, and lint.
