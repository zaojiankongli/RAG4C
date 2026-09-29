# Frontend Visualize navigation adapter design (2026-09-25)

## Context

`VisualizePage` synchronizes tab, run, node, view, and follow state back into
the canonical URL with a direct `history.replaceState` call. The page already
listens to `popstate`/`hashchange`; only the browser commit side effect is
duplicated.

## Design

1. Keep `runViewUrl` as the canonical URL builder.
2. Commit the generated URL through the shared adapter as a silent history
   replace (`dispatchPopStateAfterHistory: false`), preserving the current
   behavior that React state is already authoritative and no route event is
   synthesized for each local control change.
3. Keep the existing pathname guard so a kept-alive hidden Visualize page
   cannot overwrite another route.
4. Add a source guard and retain the existing orchestration regression.

## Compatibility

- Run query parameters, tab/view/follow serialization, pathname guard, and
  `history.state` are unchanged.
- No extra `popstate` is emitted for local Visualize control changes.
- No backend, OpenAPI, database, or public contract changes.

## Verification

- Visualize orchestration and adapter/source guard tests.
- Focused ESLint, TypeScript/build, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **4 files / 24 tests passed**.
- Direct-route test verifies current `history.state` preservation.
- Hash-route tests verify silent replace URL serialization and `hashchange`
  state synchronization.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent review initially suggested the direct state/hash coverage;
  coverage was added and final review: **PASS**, no findings.
