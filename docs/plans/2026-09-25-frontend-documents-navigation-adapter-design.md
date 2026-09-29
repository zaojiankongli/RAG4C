# Frontend Documents navigation adapter design (2026-09-25)

## Context

`DocumentsPage.tsx` owns several distinct route side effects for filters and
the parse-intervention workspace:

- replace document filters without emitting a route event;
- push parse-workspace entries with a `rag4cParseWorkspace` history marker;
- repair a denied dirty-workspace browser navigation with a marked push;
- replace the parse route when closing without a marked history entry;
- operator handoffs that push and emit the existing route event.

These are valid behavior differences, but the browser calls are duplicated in
the page and are hard to extend safely.

## Design

1. Extend the shared navigation adapter with an explicit
   `dispatchPopStateAfterHistory` option, defaulting to `true`.
2. Use the adapter's history strategy for Documents route writes. The
   `historyState` option preserves the parse-origin marker and current state.
3. Use `dispatchPopStateAfterHistory: false` for the existing filter/parse
   writes that intentionally update local state directly without emitting a
   route event.
4. Keep `window.history.back()` for closing a marked parse entry; it is a
   browser back operation, not a URL-write protocol.
5. Add a source guard and preserve the existing Documents workspace regression
   suite.

## Compatibility

- Filter URL shapes, parse deep-link URLs, dataset query parameters, and
  dirty-draft confirmation remain unchanged.
- Parse-origin history state remains merged with the existing history state.
- Filter and parse close writes remain silent; operator handoffs still emit
  one synthetic route event, including hash compatibility.
- No backend, OpenAPI, database, or document state behavior changes.

## Verification

- Adapter tests for silent history commits and normal history/hash commits.
- Documents workspace regression and source guard.
- Focused ESLint, TypeScript/build, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **3 files / 48 tests passed**.
- Adapter contract covers silent history commits, parse state markers, and
  one operator-handoff `popstate` in both history and hash modes.
- Documents workspace behavior remains green, including dirty cancellation,
  hash parse-origin back navigation, direct/hash close replace semantics, and
  filter synchronization.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent sub-agent review initially identified missing runtime
  operator-handoff event coverage; that coverage was added and final review:
  **PASS**, no findings.
