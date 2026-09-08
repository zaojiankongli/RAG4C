# RAG4C Operational Clarity — Full-Product Stabilization and UI Design

Date: 2026-08-31
Status: approved by standing user authorization
Evidence: `.planning/2026-08-30-full-ui-playwright-audit/findings.md`
Playwright artifacts: `output/playwright/full-ui-audit-20260830/`

## Product intent

RAG4C already contains a broad enterprise KnowledgeOps surface, but the current experience exposes implementation seams, mixes several visual systems, and contains contract defects that make healthy capabilities appear unavailable. This design turns the existing product into one coherent operations console without replacing its domain model, routes, APIs, audit boundaries, or safety controls.

The product promise is:

```text
KNOW -> ASK -> VERIFY -> OPERATE -> IMPROVE
```

The interface should help a knowledge operator answer four questions quickly:

1. What knowledge is available and trustworthy?
2. What is running, slow, failed, or blocked?
3. What action should I take next?
4. What evidence proves the action and result?

## Selected approach

### Operational Clarity — stabilize first, then unify

Use a staged implementation:

1. repair P0/P1 runtime and API-contract defects;
2. harden the shared UI compatibility facade;
3. compact and unify the application shell;
4. improve high-frequency Knowledge workflows;
5. bring enterprise/governance pages into the same visual and interaction system;
6. verify the entire product with the existing Playwright matrix and real isolated write flows.

Why selected:

- It preserves the substantial enterprise authority work already present.
- It fixes false-unavailable states before styling them.
- It provides immediate value on Query, Documents, Overview, Visualize and Monitor.
- It avoids a risky route or domain rewrite in a worktree containing active cumulative development.

### Rejected — bug fixes only

Repairing only crashes and 5xx responses would leave the inconsistent shell, oversized mobile header, dead-end empty states and raw engineering copy untouched.

### Rejected — full information-architecture rewrite

Replacing navigation, route ownership and enterprise page boundaries would invalidate existing tests and handoff contracts while delaying functional recovery.

## Evidence baseline

The approved design is grounded in actual Playwright execution, not static preference:

- 19 routes at 1440×900 and 390×844;
- critical pages at 1366×768 and 375×812;
- light/dark shell interaction;
- mobile navigation;
- Ctrl+K global search;
- document import through the two-step UI;
- parsing, chunking, embedding and Milvus insertion;
- streaming Query through terminal verification and 1/1 citation validation;
- automation rule preview and draft creation;
- recycle and restore authority flows;
- API, console, network, keyboard and reduced-motion evidence.

The test document produced the verified answer `AURORA-20260830` from one cited chunk.

## Global constraints

- Preserve all existing route URLs and safe handoff query contracts.
- Preserve Tenant, Workspace, Dataset and actor authorization boundaries.
- Preserve read-engine versus mutation-engine separation; repair callers that choose the wrong engine.
- No raw query, answer, credential, prompt, document body or unsafe metadata is introduced by this work.
- Keep the existing blue/green trust palette. Do not introduce gradients as a general design device.
- Use Chinese product language for primary UI. English authority names, revision IDs and route codes move to secondary detail.
- Keep Knowledge Lifeline as the product’s one recognizable signature pattern.
- Mobile components must be purpose-built summaries or drawers, not scaled-down desktop tables/graphs.
- No broad formatting, reset, cleanup or rewrite of unrelated cumulative changes.
- All new behavior follows TDD and must be protected by observable consumer-level tests.

## Functional stabilization requirements

### 1. Current-head Workspace compatibility

`core.enterprise_workspace_control` must accept every known current or later-in-chain revision through `HEAD_REVISION`, rather than a hand-maintained set ending at 0030.

Requirements:

- current head `0036_enterprise_knowledge_serving_reliability` is accepted;
- known post-registry revisions retain registry-managed primary-binding protection;
- unknown/future revisions still fail closed;
- Stage 26–30 compatibility behavior remains unchanged.

### 2. Retrieval experiment engine ownership

Read routes may use the read-only enterprise engine. Mutation routes must construct `RetrievalExperimentRepository` from a writable catalog engine.

Requirements:

- POST experiment run and judgment mutations never use `app.state.knowledge_auth_engine` when it is read-only;
- GET list/detail/agreement remains read-only;
- app wiring exposes explicit read and mutation providers or explicit state attributes;
- the runner persists the Dataset fence and experiment rows atomically;
- safe 503 projection remains for genuine unavailable retrieval dependencies.

### 3. Eval renderer safety

The shared `Radio.Group` compatibility wrapper must honor `canRenderTDesign()`.

Fallback behavior:

- render an accessible fieldset/radiogroup using native radio inputs;
- support `value`, `onChange`, `options`, and child `Radio.Button` declarations;
- emit the Ant-compatible event shape expected by Eval;
- keep `TRadio.Group` only when direct TDesign rendering is enabled.

Eval must render with empty history, historical report and mobile viewport without blocking the browser renderer.

### 4. Notification default filter

The notification subscription list must treat an omitted HTTP status as `active`.

Requirements:

- GET without `status` returns an empty or populated page, not 422;
- explicit `active` and `archived` remain supported;
- invalid explicit values still fail closed;
- frontend empty state distinguishes “no subscriptions” from “request failed”.

### 5. Content Recovery capability correctness

Capability inspection must normalize SQL boolean representations and parse stored JSON before canonical validation.

Requirements:

- SQLite `0/1` and reflected boolean values are treated equivalently;
- MySQL/MariaDB boolean aliases remain valid;
- `safe_snapshot_json` is decoded to an object before `canonical_recovery_event()`;
- malformed JSON, non-object JSON and digest mismatches remain unavailable;
- valid recycled and restored event chains remain ready.

### 6. Documents recycle contract

`RecycleDocumentInput` must include `datasetId`, serialize it as `dataset_id`, and Documents must pass the active Dataset ID.

Requirements:

- single recycle succeeds from Documents;
- bulk recycle supplies the same Dataset scope;
- missing Dataset ID fails in the frontend before fetch;
- no fallback to permanent delete occurs.

### 7. Recovery mutation envelope parity

The frontend recovery projector and backend response model must share one exact envelope.

The canonical response includes:

```text
state / operation / resource_id / approval_request_id / route / revision / message / retryable
```

Requirements:

- `route` may be null or the validated approval handoff;
- applied recycle/restore responses project successfully;
- unknown extra fields still fail closed;
- post-mutation reload failures are reported separately from mutation failure.

### 8. Automation draft visibility

Saving a draft must leave the rule visible and actionable.

Requirements:

- default rule load includes draft, active and paused records or exposes an explicit status filter defaulting to all non-archived rules;
- summary metrics remain status-specific;
- newly created draft is selected or highlighted after refresh;
- empty-state copy is not shown when drafts exist.

### 9. CORS development origins

Loopback development supports both canonical host spellings:

```text
http://localhost:1420
http://127.0.0.1:1420
```

Tauri origins remain unchanged. Remote origins remain denied.

### 10. Shared facade warning cleanup

Fallback components must remove framework-only props before reaching DOM:

- Select maps `mode="multiple"` to native `multiple` and maintains array values;
- Space consumes `wrap`, `size`, `direction`, `align` and maps them to classes/styles;
- Progress consumes `showInfo`, `strokeColor`, `format`, `size` and renders equivalent fallback output;
- no React unknown-prop or scalar-select warnings remain in Query, Config or streaming states.

## Application shell design

### Desktop

Keep the left navigation, but simplify the top shell into three zones:

```text
[Tenant + environment] [Workspace + global search] [Identity + notifications]
```

Rules:

- tenant and Workspace names use one-line truncation with full tooltip/title;
- status uses one consistent vocabulary: 已连接 / 降级 / 离线;
- Workspace label is Chinese-first, with IDs in secondary text;
- global search remains Ctrl/⌘K accessible;
- skip link is the first tabbable element in document order;
- collapsed navigation preserves accessible names and tooltips.

### Mobile

The shell target height before page content is 88–104px, not ~180px.

Layout:

```text
[menu] [tenant + status] [identity]
[Workspace selector or search trigger]
```

Rules:

- search opens as an overlay/sheet instead of permanently consuming a row;
- Workspace selector truncates to one line;
- page title and primary action remain visible without scrolling;
- sticky page actions must not cover shell controls;
- navigation drawer opens from the exact `打开导航` control and preserves current-page selection.

## Shared page patterns

### Page header

Every main page uses one header contract:

- icon;
- Chinese title;
- one-sentence user outcome;
- status/evidence as secondary detail;
- no more than two primary actions.

Authority codes, revision IDs and English eyebrow labels are hidden behind “权威详情” or presented as small technical metadata.

### Metric strip

Metric strips display only actionable or non-zero metrics by default. A zero-only system uses one compact empty summary rather than four to six equal cards.

### Empty state

Every empty state contains:

1. what is empty;
2. why it matters;
3. the next available action;
4. an explanation when no action is implemented or permitted.

Sources must not tell the user to create a source without exposing a create/import action or a clear handoff.

### Error state

Errors separate:

- configuration missing;
- authorization denied;
- capability migration unavailable;
- service/network failure;
- data genuinely empty.

Each state provides one recovery action and safe technical detail when useful.

## High-frequency page design

### Query

- Reduce empty desktop vertical space and bring the composer closer to the conversation.
- On mobile, collapse the welcome explanation after the first message.
- The composer uses a compact sticky bar that never obscures navigation or the latest answer.
- Streaming separates “answer text available” from “citation verification complete”.
- Terminal state removes Stop immediately and exposes source count, verification status and elapsed time.
- Citation evidence remains expandable and keyboard accessible.

### Documents

- Remove repeated shell hierarchy where the global shell, Knowledge Base header and Documents navigation compete.
- Preserve Knowledge Base resource tabs but compact the authority header.
- Desktop keeps the data table inside an explicit horizontal scroll region.
- Mobile uses document cards with status, parser profile, chunk count and action menu; it does not render the 1066px table.
- Import wizard retains the two-step plan but clearly labels the path as server-local and explains desktop file selection limitations.
- Recycle feedback distinguishes request rejection, applied mutation and refresh failure.

### Knowledge Overview

- Show a compact empty-state operating summary when the Dataset has no documents.
- Reveal the full Knowledge Lifeline after the first real asset exists.
- Replace duplicated zero cards with one asset summary and one primary next action.

### Visualize

- Desktop keeps the graph, timeline, events and knowledge views.
- The run list receives enough width for status and duration without truncating IDs beyond recognition.
- Mobile defaults to a node/status list and timeline; topology becomes an optional zoomable detail.
- React Flow attribution policy is respected or the attribution is restored.

### Monitor

- Desktop attention cards remain grouped by running/stuck/slow/error/cancelled.
- Mobile uses a compact segmented selector and renders only the selected group.
- Zero metrics collapse into one healthy-state summary.
- Deep links to Visualize remain stable.

## Enterprise and governance page design

### Tasks, Automations and Notifications

Keep their strong audit-centric structure, but standardize:

- Chinese section labels as primary;
- one shared attention-strip style;
- one shared evidence/lifecycle rail;
- drafts, paused states and empty calls to action remain visible;
- gradients are removed from large headers in favor of flat semantic surfaces.

### Enterprise management

- Keep read-only organizational, group, ACL and invitation tabs.
- Mobile tables become summary cards or explicitly labelled scroll regions.
- Capability limitations are concise and action-oriented.

### Knowledge Base Registry and Workspace

- Registry hero uses product language: ownership, references, health and revision.
- Raw migration IDs move to technical detail.
- Workspace Overview becomes a structured summary grid rather than a plain definition list.
- Resource tabs share the same responsive tab/selector pattern as Documents.

### Governance, Consistency and Recovery

- Keep strict authority semantics.
- Show configuration requirements with direct Settings handoff.
- Distinguish successful mutation from failed refresh.
- Recovery actions retain explicit confirmation and revision fencing.
- Consistency report explains best-effort versus confirmable in plain language before technical flags.

### Config

- Add search result count and visible section breadcrumbs.
- Keep save explicit and sticky without hiding the current environment warning.
- Group settings into lifecycle stages with collapsible advanced controls.
- Backend URL remains browser-local and never writes to `.env`.

## Data fetching and performance

- Global health, enterprise context, Workspace list and Workspace Dataset bindings are deduplicated per app session.
- Independent bootstrap requests run in parallel.
- Route changes reuse resolved global context; page-specific requests remain abortable.
- Capability/schema inspection uses revision/schema-version-aware caching where safe.
- Page keep-alive is bounded: lightweight state may persist, but heavy Eval/ECharts/ReactFlow pages must unmount or suspend when inactive.
- Full-page reload remains correct without depending on client-side mocks.

## Accessibility

- Skip link is the first tabbable element and moves focus to `#main-content`.
- All icon-only controls have stable accessible names.
- Drawer/dialog close returns focus to the opener.
- Tables expose scroll-region labels.
- Tabs use tablist/tab/tabpanel semantics and remain keyboard operable.
- Reduced motion disables nonessential transitions and graph animation.
- Mobile primary actions meet minimum touch target sizes.
- No hidden desktop controls are reachable in the mobile tab order.

## Testing and acceptance criteria

### Backend

Focused tests prove:

- current-head Workspace compatibility;
- read/write retrieval engine selection;
- notification default filter;
- recovery boolean and JSON canonical normalization;
- recycle Dataset scope;
- exact recovery mutation envelope.

### Frontend

Focused tests prove:

- Eval renders without a TDesign loop;
- Select multiple, Space and Progress fallback contracts;
- draft automation visibility;
- skip-link focus order;
- compact mobile shell;
- Documents mobile card mode and recycle request payload;
- Query terminal streaming state;
- recovery success versus refresh failure messaging.

### Playwright

Final evidence must include:

- 19 routes at 1440×900 and 390×844;
- critical pages at 1366×768 and 375×812;
- light and dark shell;
- mobile drawer;
- Ctrl+K search;
- document import;
- terminal Query with verified citation;
- retrieval comparison persisted successfully;
- Eval page responsive and dry-run executable;
- automation draft visible after creation;
- recycle and restore both successful in UI;
- zero page errors, zero unexpected console warnings, zero unexplained 4xx/5xx;
- no document-level horizontal overflow.

## Rollout order

### Release 1 — Operational stability

Deliver all functional stabilization requirements and shared facade warning cleanup. Existing visual structure may remain while every affected workflow becomes truthful and operable.

### Release 2 — Shell and high-frequency workflows

Deliver the compact shell, shared page patterns, Query, Documents, Overview, Visualize and Monitor improvements.

### Release 3 — Enterprise and governance consistency

Deliver Tasks, Automations, Notifications, Enterprise, Registry, Workspace, Governance, Consistency, Recovery and Config improvements.

Each release is independently testable and must leave the application runnable.

## Self-review

- No route or authority boundary is replaced.
- Every Playwright P0/P1 finding maps to an explicit requirement.
- Desktop and mobile behavior are specified separately where shrinking would be unusable.
- Error, empty, loading, partial, read-only and success states remain explicit.
- The design does not require a new UI framework or a database revision.
- No placeholder or unresolved product decision remains.
