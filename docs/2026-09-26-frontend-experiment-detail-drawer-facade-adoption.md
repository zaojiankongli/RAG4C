# Frontend ExperimentDetailDrawer facade adoption

## Outcome

`ExperimentDetailDrawer` now routes `Alert`, `Button`, `Drawer`, and `Tag`
through the shared UI facade:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\ExperimentDetailDrawer.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The consumer keeps its existing retrieval-quality behavior:

- immutable experiment facts, generation lineage, agreement metrics, judgment
  conflict state, trace disclosure, focus restoration, and the honest Eval
  capability boundary are unchanged;
- the close action uses the facade default button instead of a direct
  TDesign outline prop;
- the Drawer explicitly disables the framework footer and built-in close
  control because the surface owns its single semantic close action.

## Facade hardening included in this slice

The native Drawer path now has parity for the contracts exercised by existing
consumers:

- `open`/`visible`, `title`/`header`, and `width`/`height`/`size` aliases;
- left/right/top/bottom placement, overlay dismissal, Escape dismissal, and
  scrollable body layout;
- `destroyOnClose={false}` kept-alive mounting versus destroyed content;
- dialog role/name, explicit accessible-name precedence, and modal
  `Tab`/`Shift+Tab` focus containment;
- focus capture on open and return focus on close for native and TDesign paths.

The TDesign portal path receives the missing dialog semantics after the portal
mounts without changing the existing TDesign DOM or footer/close defaults.

## Verification

- focused ExperimentDetailDrawer/Drawer/consumer suite: **8 files / 42 tests passed**
- focused TypeScript check: passed
- focused ESLint: **0 errors**
- `git diff --check`: passed
- independent sub-agent follow-up review: **PASS**, no actionable P0–P3 findings

Final repository-wide validation:

- full frontend suite: **335/335 files passed**, manifest
  `acb919a3c04558995f6007a3fe4b5505fd9efa24a3b2a42e79eb1264221d968f`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- final `git diff --check`: passed

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `RetrievalQualityCenter` remains the next retrieval-quality facade
adoption slice.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-experiment-detail-drawer-facade-adoption-design.md`
