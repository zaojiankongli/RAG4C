# Frontend RetrievalQualityCenter facade adoption design (2026-09-26)

## Context

`RetrievalQualityCenter` is the remaining retrieval-quality host that imports
`Button` and `Tag` directly from `tdesign-react`. Its child surfaces already
use the shared renderer-policy facade, so the host currently bypasses the
same native/TDesign boundary for the dataset badge and mobile view tabs.

## Decision

Migrate only `RetrievalQualityCenter`:

- import `Button` and `Tag` from `../ui`;
- map the historical selected mobile-tab `variant="base"` / `theme="primary"`
  pair to facade `type="primary"`;
- keep the unselected default tab on the facade default button;
- preserve the dataset tag's `light-outline` variant, tab ids/ARIA links,
  scope and connection fail-closed states, and all child host contracts;
- add source, native, and TDesign regressions for the migrated controls.

## Compatibility and failure boundary

- missing scope, offline, and connection-pending states remain truthful and
  must not call retrieval APIs;
- authenticated desktop/mobile composition, tab labels, role/state/linking,
  dataset badge, and child callbacks remain unchanged;
- no route, API, backend, storage, authorization, or persisted-state change.

## Verification

1. Add failing source/native/TDesign tests for the facade boundary and mobile
   selected/unselected button semantics.
2. Implement the smallest host import/prop migration.
3. Run focused RetrievalQualityCenter/UI tests, TypeScript, focused ESLint,
   and `git diff --check`.
4. Request an independent sub-agent review and fix valid compatibility or
   accessibility findings.
5. Write the handoff/progress entry, then rerun the full frontend suite,
   build, lint, and `git diff --check`.
