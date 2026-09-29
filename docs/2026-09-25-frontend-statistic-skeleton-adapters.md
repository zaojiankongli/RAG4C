# Frontend Statistic/Skeleton native Adapters

## Outcome

The UI compatibility facade now brings `Statistic` and `Skeleton` under the
same typed renderer policy as the other conditional components:

- `D:\program_project\python_project\RAG4C\frontend\src\ui\rendererPolicy.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The registry now declares **29** conditional components. All entries still
select the `native` renderer, so the production path remains deterministic
while Statistic and Skeleton no longer bypass the extension boundary.

## Implemented contracts

### Statistic

The native Adapter preserves the Eval metric contract:

- title, prefix, value, suffix;
- numeric `precision`;
- custom `formatter`;
- `valueStyle`;
- `aria-*`, `data-*`, `id`, `role`, `tabIndex`, and root style.

When a future registry decision selects TDesign, the compatibility props are
translated explicitly:

- `precision → decimalPlaces`;
- `formatter → format`;
- `valueStyle.color → color`;
- remaining `valueStyle` fields are merged into the TDesign root style.

### Skeleton

The native Adapter renders app-owned placeholder markup with:

- optional title line;
- configurable paragraph rows;
- `paragraph={false}` support;
- `active={false}` static rendering;
- `aria-busy="true"` and safe DOM metadata;
- reduced-motion support.

The TDesign branch retains the historical forced `animation="gradient"`
behavior. It does not accidentally pass the compatibility-only `active` prop
into TDesign.

## Review

The independent sub-agent review initially found three issues:

1. missing Statistic TDesign prop translation;
2. `active={false}` did not disable native shimmer;
3. `paragraph={false}` incorrectly produced default rows.

All three were fixed with permanent tests and source guards. Final follow-up
review returned **PASS**, with no actionable P0/P1/P2/P3 findings.

## Verification

- post-fix Statistic/Skeleton contract checks: **4 files / 10 tests passed**
- final full frontend suite: **310/310 files passed**
- full-suite manifest:
  `8dfa98fa93ebf86be26a20105d3934ae46ec94bb6c2b0434b94d8be66c5a6535`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- focused ESLint: passed
- `npx tsc --noEmit`: passed
- `git diff --check`: passed
- independent follow-up review: **PASS**; reviewer reran **6 files / 22 tests**

## Scope boundary

This slice does not enable TDesign renderer entries by default and does not
change routes, APIs, backend contracts, CSS theme tokens outside the new
native surfaces, or domain state. `Collapse`, `Menu`, `Layout`,
`ConfigProvider`, and `Radio` remain separate follow-up boundaries because
their child APIs and shell semantics require dedicated adapters.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-statistic-skeleton-adapters-design.md`
