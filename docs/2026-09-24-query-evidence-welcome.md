# Query evidence-first welcome workspace handoff (2026-09-24)

## Change

- Extracted the Query page's first-use state into
  `frontend/src/pages/QueryWelcome.tsx`.
- Replaced the centered feature-card stack with an asymmetric evidence
  workbench: action-oriented introduction and the existing sample questions on
  the left; a semantic, ordered retrieval → source → answer trail on the
  right.
- Added responsive styling in `query-welcome.css`; the layout stacks on small
  screens, uses existing light/dark/anime theme tokens, preserves visible
  keyboard focus, and disables its small transitions under reduced motion.
- Kept all four sample questions and their existing `ask()` callback. Active
  conversation rendering, ACL selection, streaming, stop, citations, and API
  behavior are unchanged.
- Removed superseded shared `.welcome*` rules and anime overrides. No backend,
  route, schema, API, or persisted-state change.

## Verification

- `npm run test:single -- src/pages/QueryWelcome.test.tsx` — **3 passed**.
- `npm run build` — **passed**, TypeScript check and 7,123 Vite modules.
- Focused ESLint for `QueryPage.tsx`, `QueryWelcome.tsx`, and its test —
  **passed**.
- Prettier check for the new component, test, and stylesheet — **passed**.
  A broader check over legacy `QueryPage.tsx`, `styles.css`, and `anime.css`
  still reports existing whole-file formatting differences; those large files
  were not mass-formatted.
- `git diff --check` — **passed**.
- Fresh screenshots: `frontend/output/shots/query-evidence-final/` contains
  light/dark captures at 1440×900, 1366×768, and 375×812. The screenshot
  script completed without page errors.
- Independent sub-agent review — **code PASS**, no actionable behavior,
  accessibility, responsive, or theme findings. It identified that an earlier
  screenshot directory predated the final primary-color dot; the
  `query-evidence-final` captures were regenerated after that CSS change and
  are the valid evidence set.

## Design boundary

The three-step panel is static product guidance, not live telemetry. Keep it
that way unless a future API explicitly supplies per-query stage state. Do not
change active chat layout or claim external projection completion in this
component.

Design record: `docs/plans/2026-09-24-query-evidence-welcome-design.md`.
