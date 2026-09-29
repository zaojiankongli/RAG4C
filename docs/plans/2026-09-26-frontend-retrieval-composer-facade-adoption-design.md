# Frontend RetrievalComposer facade adoption design (2026-09-26)

## Context

`RetrievalComposer` is the next retrieval-quality boundary that still imports
`Alert`, `Button`, `Card`, `Input`, `Space`, and `Textarea` directly from
`tdesign-react`. The shared facade already has the matching adapters, but this
consumer uses several historical TDesign spellings:

- `Textarea` uses lowercase `maxlength`/`autosize` and value callbacks;
- `Button` uses `variant`/`theme`;
- `Input` and `Input.TextArea` use the shared event-shaped change payload;
- an empty `Space` is intentionally the flex spacer between composer actions.

## Decision

Migrate only `RetrievalComposer` to `../../ui`:

- use `Input.TextArea` for the query field;
- use `type="text"` for sample actions, the facade default for the outline
  add action, and `type="primary"` for the run action;
- extract query/ACL values from `{ target: { value } }`;
- keep `Alert`, `Card`, `Space`, strategy collection state, validation,
  duplicate/remove behavior, focus restoration, and run payload semantics;
- update the existing retrieval-quality action CSS to recognize the facade's
  native `rag-space` class as well as the historical TDesign `t-space` class.

## Compatibility and failure boundary

- native and TDesign text controls must preserve the query/ACL strings and the
  20,000-character, 3–8 row query behavior;
- invalid validation remains visible and blocks `onRun`;
- sample questions update state but never auto-run;
- duplicate/remove/add behavior and the `.rq-composer [aria-invalid]` focus
  path remain unchanged;
- no route, API, backend, storage, authorization, or persisted-state change.

## Verification

1. Add failing behavior/source/CSS tests for facade rendering, event
   normalization, button vocabulary, and the spacer class.
2. Implement the smallest consumer and CSS changes; do not add a new shared
   registry or component-specific adapter.
3. Run the focused RetrievalComposer/UI suite, TypeScript, and focused ESLint.
4. Request an independent sub-agent review and fix valid findings, including
   renderer-specific accessibility and keyboard regressions in validation
   focus paths.
5. Write the handoff/progress entry, then rerun the full frontend suite, build,
   lint, and `git diff --check`.
