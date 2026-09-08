# Tencent ADP / Tencent Cloud enterprise visual reference for RAG4C

**Observed:** 2026-08-26 (Asia/Shanghai)  
**Scope:** Tencent Cloud Intelligent Agent Development Platform public documentation and enterprise/knowledge-base information architecture.  
**Local evidence:** `output/playwright/tencent-adp-reference-2026-08-26/knowledge-base-doc-full.png`, five official console screenshots (`tencent-console-1.png` through `tencent-console-5.png`), and the extracted page text/HTML in the same directory.

## Official reference pages

- Knowledge-base overview and console screenshots: `https://cloud.tencent.com/document/product/1759/123551`
- Knowledge-base settings: `https://cloud.tencent.com/document/product/1759/123553`
- Knowledge-base settings overview: `https://cloud.tencent.com/document/product/1759/123554`
- Enterprise, workspace, and permission overview: `https://cloud.tencent.com/document/product/1759/122569`
- Enterprise management: `https://cloud.tencent.com/document/product/1759/122570`

These references are design calibration only. RAG4C must not copy Tencent trademarks, proprietary assets, or page markup.

## What the reference establishes

Tencent presents knowledge management as an enterprise console, not as a marketing dashboard:

- a restrained global header and a persistent product navigation rail;
- breadcrumb-first orientation and explicit page titles;
- dense information architecture with knowledge base, document, Q&A, database, settings, recall testing, enterprise, workspace, permissions, and approval surfaces;
- neutral white/slate canvases, thin borders, minimal shadow, compact controls, and blue reserved for selected state and primary action;
- operational language that names resources, scope, state, and consequences directly;
- enterprise permissions separated into organization-level and workspace/resource-level governance.
- a two-level knowledge-base workflow: a sparse list page for resource selection, then a dense detail workspace with document/Q&A/database tabs, category tree, filters, table operations, and a settings action;
- destructive actions are restrained text actions and become unavailable when dependency relationships exist, with the blocking consequence explained in context;
- edit operations use a centered, high-contrast modal over a quiet console canvas instead of turning the whole page into a form.

## RAG4C visual direction

### Subject and audience

RAG4C is a governed knowledge operations console for enterprise platform owners, knowledge administrators, data stewards, and retrieval-quality engineers. Each page has one primary job: establish the state of a governed resource and expose the next safe action.

### Token system

- `cloud-blue`: `#0052D9` — primary action, selected navigation, trusted active state.
- `cloud-blue-hover`: `#366EF4` — hover/focus emphasis.
- `ink`: `#1D2129` — primary text and data.
- `slate`: `#4E5969` — secondary labels and operational descriptions.
- `canvas`: `#F3F6F8` — application background.
- `success`: `#00A870`, `warning`: `#ED7B2F`, `danger`: `#D54941` — semantic evidence only.

Typography remains native and official: PingFang SC / Microsoft YaHei for Chinese UI, Inter-compatible system sans for Latin text, and IBM Plex Mono / system monospace only for revision IDs, request IDs, and database evidence.

### Layout

```text
┌──────────────────── global enterprise header ─────────────────────┐
├──────── product navigation ───────┬────────────────────────────────┤
│ tenant / workspace context        │ breadcrumb + page title        │
│ governed modules                  │ evidence strip                 │
│                                   │ filters / primary action       │
│                                   │ dense resource table or graph  │
│                                   │ contextual drawer / dialog     │
└───────────────────────────────────┴────────────────────────────────┘
```

Use compact 4–8 px radii, one-pixel borders, 8 px spacing increments, and shadows only for floating layers. Avoid oversized hero metrics, decorative gradients, glassmorphism, and floating cards that obscure hierarchy.

## RAG4C signature element

The product-specific visual signature is the **governance evidence strip**: a compact, persistent row that shows authoritative facts such as catalog revision, ACL mode and revision, readiness state, tenant scope, audit coverage, and last verified time. It makes RAG4C look official because the interface continuously proves what it claims instead of inventing activity metrics.

## Component policy

1. Prefer TDesign React and TDesign Icons for navigation, tables, dialogs, forms, alerts, tags, drawers, pagination, and loading/empty/error states.
2. Use Uiverse only for an isolated interaction TDesign cannot express without custom work; restyle it to the token system.
3. Use Morphicons only when no TDesign icon communicates the resource accurately.
4. Never mix multiple visual systems inside one workflow.
5. Every dangerous action requires an explicit consequence, current revision or scope evidence, a business reason when applicable, and a server refresh after success.

## Information architecture mapping

- Tencent knowledge-base management → RAG4C Knowledge overview, Documents, Knowledge taxonomy.
- Tencent knowledge-base settings and recall test → RAG4C Retrieval quality and dataset profile/evidence.
- Tencent enterprise/workspace permissions → RAG4C Enterprise admin, organization graph, groups, invitations, and dataset ACL.
- Tencent operational/approval language → RAG4C readiness, audit, revision fencing, and fail-closed database upgrade surfaces.

## Responsive rule

Desktop preserves the dense two-column console. At tablet and mobile widths, navigation becomes an explicitly closable drawer; evidence strips wrap by meaning, tables switch to priority columns plus a detail drawer, and dangerous dialogs remain fully visible without horizontal scrolling. Mobile screenshots used for approval must be captured with navigation closed unless the navigation itself is under review.
