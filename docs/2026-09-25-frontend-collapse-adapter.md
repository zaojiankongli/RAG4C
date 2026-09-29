# Frontend Collapse native Adapter

## Outcome

`Collapse` now participates in the shared UI renderer policy:

- `D:\program_project\python_project\RAG4C\frontend\src\ui\rendererPolicy.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The registry now declares **30** conditional components. All entries still
select `native`, so the current application uses a deterministic app-owned
renderer while the TDesign branch remains compatible and explicitly mapped.

## Contracts

The native Adapter supports the existing `items` API:

- controlled `value` and uncontrolled `defaultValue`;
- collection-shaped `onChange`;
- accordion mode;
- item-level and collapse-level disabled state;
- keyboard-focusable native buttons;
- linked `aria-expanded`, `aria-controls`, `aria-labelledby`, and `region`;
- `ghost`, `borderless`, and `size` modifier classes;
- safe `aria-*`/`data-*` root props.

Native and TDesign branches share one normalization boundary:

- numeric keys become strings;
- unknown, prototype, null, malformed, and duplicate keys are rejected;
- TDesign uses `expandMutex={accordion}`;
- TDesign receives normalized values and normalized `onChange` output;
- closed native panels remain mounted with `hidden`, matching TDesign's default
  non-destroying collapse behavior and keeping ARIA links stable.

The direct TDesign `SafeTracePanel` child-API consumer remains outside this
slice. It will need a separate `Collapse.Panel` child-syntax Adapter before it
can migrate safely.

## Review

The independent sub-agent review first found five parity issues:

1. TDesign `accordion` needed `expandMutex`;
2. TDesign needed the same fail-closed item normalization;
3. numeric keys needed one cross-renderer representation;
4. native closed-panel ARIA links needed stable mounted regions;
5. native needed collapse-level `disabled`.

All were fixed with permanent tests and source guards. Final follow-up review:
**PASS**, with no actionable P0/P1/P2/P3 findings.

## Verification

- Collapse/registry focused checks: **6 files / 24 tests passed**
- Retrieval trace consumer regression: **3 tests passed**
- Full frontend suite: **313/313 files passed**
- Full-suite manifest:
  `acfd11930af3a6130bd73995de9cd48c355fecffafc1b44c23e54fdc2b07803a`
- `npm run build`: passed, **7,127 modules transformed**
- Full ESLint: **0 errors / 98 existing warnings**
- `npx tsc --noEmit`: passed
- Focused ESLint: passed
- `git diff --check`: passed
- Independent follow-up review: **PASS**

## Scope boundary

This slice does not change routes, APIs, backend contracts, authority
semantics, or persisted state. It does not migrate the separate direct
TDesign `SafeTracePanel` child syntax or implement native `Menu`, `Layout`,
`ConfigProvider`, or `Radio` root namespaces.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-collapse-adapter-design.md`
