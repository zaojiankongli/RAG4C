# Parse Intervention Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a dedicated, scope-safe three-pane Parse Intervention Workspace that edits/tombstones authoritative chunks with CAS conflict recovery and honest durable projection status.

**Architecture:** Keep `DocumentsPage` as the route and return-context owner while a focused `parse-intervention` subsystem owns projections, controller state, and UI panes. Reuse the existing document/chunk mutation authority, add only optional operator-safe ChunkHead fields to the existing list endpoint, and fence every asynchronous result by tenant/dataset/document scope generation.

**Tech Stack:** React 18, TypeScript 5.6, Vite 6, Vitest 3, Testing Library, TDesign React 1.18, FastAPI, SQLAlchemy, pytest, Playwright, axe-core.

**Spec:** `frontend/docs/superpowers/specs/2026-08-25-parse-intervention-workspace-design.md`

## Global Constraints

- Use native TDesign controls and the existing RAG4C Knowledge Lifeline tokens; do not copy Tencent branding, icons, colors, dimensions, or component styling.
- Optimize the three-pane layout for 1440px and use one-pane-at-a-time tabs at 375px.
- Preserve Documents list/filter/selection/page state when entering and returning from the workspace.
- Missing or mismatched tenant/dataset/document scope fails closed; no stale load or mutation result may update state.
- A successful ChunkHead mutation is not projection completion; show queued/pending durable operations and Consistency/Monitor operator guidance.
- Never render actor tokens, credential URIs, raw connector configuration, `file_path`, or arbitrary unallowlisted metadata.
- Do not fabricate raw source preview, chunk revision history, or persisted edit reasons.
- Every production behavior is introduced by a failing test first.
- Backend and frontend changes must be committed separately.
- Focused and full Vitest use a 10,000ms test timeout.

## File map

### Frontend files to create

- `frontend/src/parse-intervention/model/parseInterventionModel.ts` — pure safe projections, outline, warnings, token estimates, lifecycle and diff helpers.
- `frontend/src/parse-intervention/model/parseInterventionModel.test.ts` — model red/green tests.
- `frontend/src/parse-intervention/api/parseInterventionApi.ts` — exact document/chunk API wrapper calls.
- `frontend/src/parse-intervention/api/parseInterventionApi.test.ts` — query and mutation argument tests.
- `frontend/src/parse-intervention/hooks/useParseIntervention.ts` — scope-fenced load/mutation controller.
- `frontend/src/parse-intervention/hooks/useParseIntervention.test.tsx` — stale scope, CAS conflict, receipt and deletion tests.
- `frontend/src/parse-intervention/components/ParsedContextPane.tsx` — safe document facts, waterfall, outline and capability gap.
- `frontend/src/parse-intervention/components/ChunkListPane.tsx` — contained windowed list, filters and keyboard selection.
- `frontend/src/parse-intervention/components/ChunkEditorPane.tsx` — editor, reason, safe metadata, diff, conflict and delete controls.
- `frontend/src/parse-intervention/components/ProjectionReceipt.tsx` — authoritative acceptance and projection guidance.
- `frontend/src/parse-intervention/ParseInterventionWorkspace.tsx` — header, lifeline, responsive pane composition and dirty guard callbacks.
- `frontend/src/parse-intervention/ParseInterventionWorkspace.test.tsx` — desktop/mobile landmarks, copy, focus and dirty UI tests.
- `frontend/src/parse-intervention/parse-intervention.css` — dark/light, containment, mobile tabs, focus and reduced-motion styles.
- `frontend/src/parse-intervention/parseIntervention.css.test.ts` — static containment/mobile/reduced-motion assertions.
- `frontend/src/documents/documentParseRoute.ts` — nested direct/hash route parsing and navigation intents.
- `frontend/src/documents/documentParseRoute.test.ts` — route and return-context tests.

### Frontend files to modify

- `frontend/src/types/rag.ts` — optional operator-safe chunk head fields.
- `frontend/src/api/client.ts` — `includeDisabled` query support only.
- `frontend/src/api/client.documents.test.ts` — exact list request assertion.
- `frontend/src/documents/KnowledgeDocumentTable.tsx` — row-local `解析干预` action callback; remove expanded workspace responsibility.
- `frontend/src/documents/KnowledgeDocumentTable.test.tsx` — row action behavior.
- `frontend/src/pages/DocumentsPage.tsx` — nested workspace route, return context and shared snapshot invalidation.
- `frontend/src/pages/DocumentsPage.workspace.test.tsx` — full-screen route, stale/offline and return-state integration.
- `frontend/src/App.tsx` — application-navigation dirty guard registry.
- `frontend/src/App.initialRoute.test.tsx` — nested Documents route mounting.
- `frontend/src/styles.css` — import/host rules only if required; feature selectors remain in feature CSS.

### Backend files to modify

- `server/documents.py` — optional `include_disabled` query and additional safe ChunkHead fields.
- `tests/test_chunk_authority_api.py` — default exclusion, explicit tombstone inclusion, exact lifecycle projection.

## Task 1: Safe workspace model and exact frontend contracts

**Files:**
- Create: `frontend/src/parse-intervention/model/parseInterventionModel.test.ts`
- Create: `frontend/src/parse-intervention/model/parseInterventionModel.ts`
- Create: `frontend/src/parse-intervention/api/parseInterventionApi.test.ts`
- Create: `frontend/src/parse-intervention/api/parseInterventionApi.ts`
- Modify: `frontend/src/types/rag.ts:569-620`
- Modify: `frontend/src/api/client.ts:437-491`
- Modify: `frontend/src/api/client.documents.test.ts`

**Interfaces:**
- Produces `ParseScope`, `ChunkLifecycle`, `ProjectionLifecycle`, `ChunkWarning`, `OutlineNode`, `SafeMetadataFact`, `DiffLine`, and `ProjectionReceiptView` types.
- Produces `requireParseScope(input): ParseScope | null`, `sanitizeSourceFact(value): string`, `safeMetadataFacts(metadata): SafeMetadataFact[]`, `estimateTokens(text): number`, `projectChunkLifecycle(chunk): ChunkLifecycle`, `projectProjectionLifecycle(chunk): ProjectionLifecycle`, `chunkWarnings(chunk): ChunkWarning[]`, `buildChunkOutline(chunks): OutlineNode[]`, and `buildDiff(before, after): DiffLine[]`.
- Produces API functions `loadParseWorkspace(docId, signal)`, `saveParseChunk(docId, chunkId, text, expectedRevision, signal)`, and `tombstoneParseChunk(docId, chunkId, expectedRevision, signal)`.

- [ ] **Step 1: Write failing model tests**

Cover these exact behaviors:

```ts
expect(requireParseScope({ tenantId: "", datasetId: "default", docId: "doc" })).toBeNull();
expect(sanitizeSourceFact("https://user:secret@example.com/a?token=abc#x")).toBe("https://example.com/a");
expect(safeMetadataFacts({ heading: "Policy", page: 2, api_key: "secret", nested: { token: "x" } }))
  .toEqual([{ label: "标题", key: "heading", value: "Policy" }, { label: "页码", key: "page", value: "2" }]);
expect(estimateTokens("中文知识库 ABC 123")).toBeGreaterThan(0);
expect(projectChunkLifecycle({ enabled: false } as DocumentChunkItem)).toBe("tombstone");
expect(projectProjectionLifecycle({ content_revision: 5, desired_index_revision: 5, indexed_revision: 4, index_status: "pending" } as DocumentChunkItem).state).toBe("pending");
expect(buildChunkOutline(chunks)[0]).toMatchObject({ page: "1", headings: ["Introduction"] });
expect(buildDiff("old line", "new line")).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "remove" }), expect.objectContaining({ kind: "add" })]));
```

- [ ] **Step 2: Run model tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/parse-intervention/model/parseInterventionModel.test.ts
```

Expected: FAIL because the module and functions do not exist.

- [ ] **Step 3: Implement minimal pure model code**

Use an explicit metadata allowlist such as `heading`, `title`, `section`, `page`, `page_number`, `language`, `mime_type`, `source`, and `seq`. URI sanitization must strip userinfo, query, and fragment; invalid values become a bounded plain-text basename/label. Token estimation is deterministic and labeled as an estimate, not tokenizer truth.

- [ ] **Step 4: Run model tests and verify GREEN**

Run the command from Step 2. Expected: all model tests PASS.

- [ ] **Step 5: Write failing API/type tests**

Assert that:

```ts
await fetchDocumentChunks("doc/a", { offset: 0, limit: 200, query: "policy", includeDisabled: true });
expect(fetch).toHaveBeenCalledWith(
  expect.stringContaining("/api/documents/doc%2Fa/chunks?offset=0&limit=200&query=policy&include_disabled=true"),
  expect.objectContaining({ method: "GET" }),
);
```

Also assert that `loadParseWorkspace` starts document and chunk requests without a serial dependency and requests `includeDisabled: true`.

- [ ] **Step 6: Run API tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/api/client.documents.test.ts src/parse-intervention/api/parseInterventionApi.test.ts
```

Expected: FAIL because `includeDisabled` and the wrapper module do not exist.

- [ ] **Step 7: Implement exact frontend contracts**

Add optional fields to `DocumentChunkItem`:

```ts
tenant_id?: string;
dataset_id?: string;
document_revision?: number;
enabled?: boolean;
chunk_role?: "flat" | "child" | "parent" | string;
desired_index_revision?: number;
index_status?: string;
```

Extend `fetchDocumentChunks` options with `includeDisabled?: boolean` and append `include_disabled=true` only when requested. Implement wrappers with `Promise.all` for document and chunk loading.

- [ ] **Step 8: Run focused frontend tests and verify GREEN**

Run both commands from Steps 2 and 6. Expected: PASS.

- [ ] **Step 9: Commit frontend model/API slice**

```powershell
git add -- frontend/src/types/rag.ts frontend/src/api/client.ts frontend/src/api/client.documents.test.ts frontend/src/parse-intervention/model frontend/src/parse-intervention/api
git commit -m "feat(frontend): model parse intervention facts"
```

## Task 2: Scope-fenced controller, conflict recovery, and projection receipts

**Files:**
- Create: `frontend/src/parse-intervention/hooks/useParseIntervention.test.tsx`
- Create: `frontend/src/parse-intervention/hooks/useParseIntervention.ts`

**Interfaces:**
- Consumes `ParseScope`, model projections, and parse API wrappers from Task 1.
- Produces `UseParseInterventionResult` with `status`, `document`, `chunks`, `authorityMode`, `selected`, `selectedId`, `query`, `filters`, `draft`, `reason`, `dirty`, `conflict`, `receipt`, `mutating`, `selectChunk`, `setQuery`, `setFilters`, `setDraft`, `setReason`, `resetDraft`, `reload`, `submit`, `removeSelected`, `adoptServer`, and `continueDraft`.

- [ ] **Step 1: Write failing controller load/scope tests**

Use `renderHook` with deferred promises. Assert:

```ts
const first = renderHook(({ scope }) => useParseIntervention(scope, true), { initialProps: { scope: scopeA } });
first.rerender({ scope: scopeB });
resolveScopeA();
expect(first.result.current.document).toBeNull();
expect(first.result.current.chunks).toEqual([]);
```

Also assert missing scope and offline mode call no API, and a returned document with a mismatched tenant/dataset enters `scope-error` without exposing chunks.

- [ ] **Step 2: Run controller tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/parse-intervention/hooks/useParseIntervention.test.tsx
```

Expected: FAIL because the hook does not exist.

- [ ] **Step 3: Implement minimal fenced loading**

Use a scope key `${tenantId}\u0000${datasetId}\u0000${docId}`, a generation ref incremented on key change, one load AbortController, one mutation AbortController, and sequence checks. Clear all authority facts synchronously on key change before accepting any new result.

- [ ] **Step 4: Run load/scope tests and verify GREEN**

Run Step 2. Expected: load/scope tests PASS.

- [ ] **Step 5: Write failing CAS/conflict/receipt tests**

Assert:

```ts
expect(updateDocumentChunk).toHaveBeenCalledWith("doc-1", "chunk-1", "local", 4, expect.any(AbortSignal));
expect(result.current.conflict).toMatchObject({ localDraft: "local", serverRevision: 5 });
expect(result.current.dirty).toBe(true);
```

For accepted active-mode edits/deletes, assert the receipt says `authorityAccepted: true`, `projectionState: "pending"`, and keeps safe operation IDs. Assert no string in the receipt claims projection completion. Assert a scope switch during mutation suppresses both receipt and list changes.

- [ ] **Step 6: Run mutation tests and verify RED**

Run Step 2. Expected: FAIL on missing mutation behavior.

- [ ] **Step 7: Implement CAS mutation and recovery**

On submit, require nonblank changed text and nonblank reason. Capture the selected revision. On 409, reload with the same scope generation, preserve local draft/reason, and store the refreshed selected chunk as conflict server state. `continueDraft` rebases the local draft onto the refreshed `content_revision`; `adoptServer` resets draft/reason to server state.

On delete, call the existing expected-revision delete. Retain a tombstone row if the refreshed list returns it; otherwise remove the selected row. Build projection receipts from `authority_mode`, `projection_pending`, and `operation_ids` without inferring completion.

- [ ] **Step 8: Run controller tests and verify GREEN**

Run Step 2. Expected: all controller tests PASS.

- [ ] **Step 9: Commit frontend controller slice**

```powershell
git add -- frontend/src/parse-intervention/hooks
git commit -m "feat(frontend): fence parse intervention sessions"
```

## Task 3: Three-pane UI, contained chunk window, diff, and responsive tabs

**Files:**
- Create: `frontend/src/parse-intervention/components/ParsedContextPane.tsx`
- Create: `frontend/src/parse-intervention/components/ChunkListPane.tsx`
- Create: `frontend/src/parse-intervention/components/ChunkEditorPane.tsx`
- Create: `frontend/src/parse-intervention/components/ProjectionReceipt.tsx`
- Create: `frontend/src/parse-intervention/ParseInterventionWorkspace.test.tsx`
- Create: `frontend/src/parse-intervention/ParseInterventionWorkspace.tsx`
- Create: `frontend/src/parse-intervention/parseIntervention.css.test.ts`
- Create: `frontend/src/parse-intervention/parse-intervention.css`

**Interfaces:**
- Consumes `UseParseInterventionResult` from Task 2.
- Produces `ParseInterventionWorkspace({ scope, online, onReturn, onNavigateOperator, onChanged, onDirtyChange })`.

- [ ] **Step 1: Write failing desktop/mobile/layout tests**

Render with a controller fixture and assert:

```ts
expect(screen.getByRole("region", { name: "解析上下文" })).toBeTruthy();
expect(screen.getByRole("region", { name: "切片列表" })).toBeTruthy();
expect(screen.getByRole("region", { name: "切片编辑器" })).toBeTruthy();
expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
expect(screen.getByText("当前没有原始文档预览能力")).toBeTruthy();
expect(screen.getByText("当前 API 不持久化修改原因")).toBeTruthy();
```

Set `matchMedia('(max-width: 720px)')` to true and assert three tabs exist and only the selected pane is exposed.

- [ ] **Step 2: Run workspace tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/parse-intervention/ParseInterventionWorkspace.test.tsx
```

Expected: FAIL because the workspace and panes do not exist.

- [ ] **Step 3: Implement the header and parsed-context pane**

Use TDesign `Button`, `Tag`, `Alert`, `Tabs`, `Input`, `Select`, `Textarea`, `Popconfirm`, and `Loading`. Render the Knowledge Lifeline as an ordered status list with textual node labels. Do not render `file_path` or raw metadata JSON.

- [ ] **Step 4: Implement the contained chunk window**

Use a scroll-owned `role="listbox"` region with fixed row height and calculated visible start/end indexes plus overscan. Keep spacer height inside the list owner. Rows use `role="option"`, `aria-selected`, and stable `id`. Handle ArrowUp, ArrowDown, Home, End, and Enter. Call `scrollIntoView({ block: "nearest" })` only for keyboard selection.

- [ ] **Step 5: Implement editor, diff, conflict, delete, and receipt**

The editor exposes current content, allowlisted metadata, immutable CAS facts, reason input, line diff, projection-impact alert, and secondary reset/copy/delete controls. The top-right workspace button is the only primary commit action. Conflict actions are explicit and preserve draft. Projection receipts link to Consistency and Monitor through callbacks, not raw URLs.

- [ ] **Step 6: Run workspace tests and verify GREEN**

Run Step 2. Expected: component tests PASS.

- [ ] **Step 7: Write failing CSS contract tests**

Read the CSS file as text and assert it includes:

```ts
expect(css).toMatch(/grid-template-columns:\s*minmax\(260px/);
expect(css).toMatch(/overflow-x:\s*hidden/);
expect(css).toMatch(/@media\s*\(max-width:\s*720px\)/);
expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
expect(css).toMatch(/:focus-visible/);
```

- [ ] **Step 8: Run CSS tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/parse-intervention/parseIntervention.css.test.ts
```

Expected: FAIL because the CSS file does not exist.

- [ ] **Step 9: Implement visual styles**

Use existing CSS variables for light/dark surfaces and semantic colors. Desktop grid uses three `minmax` columns and a bounded viewport height. At 720px and below, hide inactive pane panels and show tab navigation. All scroll owners receive `min-width: 0`, `overflow: auto`, and visible focus. Reduced motion sets transition/animation duration to near-zero for feature selectors.

- [ ] **Step 10: Run component and CSS tests and verify GREEN**

Run Steps 2 and 8. Expected: PASS.

- [ ] **Step 11: Commit frontend UI slice**

```powershell
git add -- frontend/src/parse-intervention/components frontend/src/parse-intervention/ParseInterventionWorkspace.tsx frontend/src/parse-intervention/ParseInterventionWorkspace.test.tsx frontend/src/parse-intervention/parse-intervention.css frontend/src/parse-intervention/parseIntervention.css.test.ts
git commit -m "feat(frontend): add parse intervention workspace"
```

## Task 4: Documents nested route, row entry, dirty navigation, and return context

**Files:**
- Create: `frontend/src/documents/documentParseRoute.test.ts`
- Create: `frontend/src/documents/documentParseRoute.ts`
- Modify: `frontend/src/documents/KnowledgeDocumentTable.tsx`
- Modify: `frontend/src/documents/KnowledgeDocumentTable.test.tsx`
- Modify: `frontend/src/pages/DocumentsPage.tsx`
- Modify: `frontend/src/pages/DocumentsPage.workspace.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.initialRoute.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Produces `parseDocumentWorkspaceLocation(location): { docId: string } | null`, `documentWorkspaceNavigationIntent(location, docId)`, and `documentsReturnIntent(location)`.
- Extends `KnowledgeDocumentTable` with `onOpenParse(document)` and a row-local `解析干预` button.
- Registers a single current dirty-navigation guard that `App.navigate` consults before changing top-level pages.

- [ ] **Step 1: Write failing nested-route tests**

Assert direct and hash shapes:

```ts
expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "?doc=doc%2F1", hash: "" })).toEqual({ docId: "doc/1" });
expect(parseDocumentWorkspaceLocation({ pathname: "/", search: "", hash: "#/documents/parse?doc=doc%2F1" })).toEqual({ docId: "doc/1" });
expect(documentWorkspaceNavigationIntent({ pathname: "/documents", search: "", hash: "" }, "doc/1")).toEqual({ mode: "history", url: "/documents/parse?doc=doc%2F1" });
```

- [ ] **Step 2: Run route tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/documents/documentParseRoute.test.ts
```

Expected: FAIL because the route helpers do not exist.

- [ ] **Step 3: Implement route helpers and verify GREEN**

Implement direct/hash parsing without accepting other page keys. Run Step 2 and expect PASS.

- [ ] **Step 4: Write failing table/page integration tests**

Assert the completed/active row exposes `解析干预`, clicking pushes the nested URL, the full workspace replaces the table workbench, and returning reveals the same filter text and selected category. Assert mock/offline rows cannot open fabricated workspace data.

- [ ] **Step 5: Run table/page tests and verify RED**

Run:

```powershell
npm exec -- vitest run --testTimeout=10000 src/documents/KnowledgeDocumentTable.test.tsx src/pages/DocumentsPage.workspace.test.tsx src/App.initialRoute.test.tsx
```

Expected: FAIL on the missing row callback and nested workspace composition.

- [ ] **Step 6: Implement row entry and page composition**

Replace the expanded-row `DocumentWorkspace` entry with the row-local action. Keep the old component file temporarily only if another test imports it; remove its page usage. Resolve the target document from the current shared snapshot and construct `ParseScope` from its tenant/dataset and the current dataset. If no exact document exists, render a fail-closed workspace state.

- [ ] **Step 7: Write failing dirty-navigation tests**

Dirty the editor, attempt workspace return and top-level navigation, and assert `window.confirm` is called. With `false`, assert route and focus remain. With `true`, assert navigation occurs. Dispatch `beforeunload` and assert `preventDefault` plus `returnValue` behavior.

- [ ] **Step 8: Run dirty-navigation tests and verify RED**

Run the page/App tests from Step 5. Expected: FAIL because the guard is not connected.

- [ ] **Step 9: Implement dirty navigation and focus restoration**

Use a small guard registration interface owned by `App` or a dedicated context. `ParseInterventionWorkspace` reports dirty state and provides the confirmation copy. On cancelled return, focus the editor heading or textarea. On confirmed return, abort mutations/loads through unmount and restore focus to the originating row action when the list remounts.

- [ ] **Step 10: Run route, table, page and App tests and verify GREEN**

Run Steps 2 and 5. Expected: PASS.

- [ ] **Step 11: Commit frontend routing slice**

```powershell
git add -- frontend/src/documents/documentParseRoute.ts frontend/src/documents/documentParseRoute.test.ts frontend/src/documents/KnowledgeDocumentTable.tsx frontend/src/documents/KnowledgeDocumentTable.test.tsx frontend/src/pages/DocumentsPage.tsx frontend/src/pages/DocumentsPage.workspace.test.tsx frontend/src/App.tsx frontend/src/App.initialRoute.test.tsx frontend/src/styles.css
git commit -m "feat(frontend): route document parse intervention"
```

## Task 5: Narrow additive backend ChunkHead list projection

**Files:**
- Modify: `tests/test_chunk_authority_api.py`
- Modify: `server/documents.py:910-982`

**Interfaces:**
- Extends `GET /api/documents/{doc_id}/chunks` with `include_disabled: bool = False`.
- Adds optional active-authority item fields `tenant_id`, `dataset_id`, `document_revision`, `enabled`, `chunk_role`, `desired_index_revision`, and `index_status`.

- [ ] **Step 1: Write failing backend contract tests**

Seed one enabled and one disabled ChunkHead at the current document revision. Assert:

```py
current = documents.get_document_chunks("doc-1")
assert [item["chunk_id"] for item in current["items"]] == ["enabled"]

all_heads = documents.get_document_chunks("doc-1", include_disabled=True)
assert [item["chunk_id"] for item in all_heads["items"]] == ["enabled", "tombstone"]
assert all_heads["items"][1] == {
    **all_heads["items"][1],
    "tenant_id": "tenant-1",
    "dataset_id": "dataset-1",
    "document_revision": 7,
    "enabled": False,
    "chunk_role": "flat",
    "desired_index_revision": 5,
    "indexed_revision": 4,
    "index_status": "pending",
}
```

Also assert the legacy/off path does not invent these fields beyond existing authoritative projection data.

- [ ] **Step 2: Run focused pytest and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chunk_authority_api.py -q
```

Expected: FAIL because `include_disabled` and the fields are missing.

- [ ] **Step 3: Implement the additive projection**

Add the safe fields in `_chunk_head_operator_projection`. Pass `include_disabled=include_disabled` only in active authority mode. Preserve the endpoint default and existing sort/filter/pagination behavior.

- [ ] **Step 4: Run focused pytest and verify GREEN**

Run Step 2. Expected: PASS.

- [ ] **Step 5: Commit backend slice separately**

```powershell
git add -- server/documents.py tests/test_chunk_authority_api.py
git commit -m "feat(api): expose chunk head lifecycle facts"
```

## Task 6: Focused/full verification, browser QA, axe, and review

**Files:**
- Modify only files required to fix verified failures; keep backend and frontend fixes in separate commits.
- Create screenshots under `output/playwright/parse-intervention/` only if repository practice retains QA evidence; do not commit generated screenshots unless existing policy requires it.

**Interfaces:**
- Consumes the complete workspace and additive backend contract.
- Produces verification evidence and final scoped commits.

- [ ] **Step 1: Run all focused frontend tests**

```powershell
npm exec -- vitest run --testTimeout=10000 src/parse-intervention src/documents/documentParseRoute.test.ts src/documents/KnowledgeDocumentTable.test.tsx src/pages/DocumentsPage.workspace.test.tsx src/App.initialRoute.test.tsx src/api/client.documents.test.ts
```

Expected: PASS with zero failures and no unhandled errors.

- [ ] **Step 2: Run full frontend Vitest**

```powershell
npm test -- --testTimeout=10000
```

Expected: PASS with zero failed files/tests.

- [ ] **Step 3: Run frontend lint**

```powershell
npm run lint
```

Expected: exit 0 with no ESLint errors.

- [ ] **Step 4: Run frontend production build**

```powershell
npm run build
```

Expected: TypeScript and Vite exit 0.

- [ ] **Step 5: Run focused backend tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chunk_authority_api.py tests/test_document_chunk_operations.py -q
```

Expected: PASS.

- [ ] **Step 6: Start or reuse the local backend/frontend for browser QA**

Use the repository's existing launch scripts or Vite/FastAPI commands. Do not expose credentials in command output or screenshots. Seed/use a real scoped document with active ChunkHead data; do not inject demo workspace facts.

- [ ] **Step 7: Capture and inspect four required viewport/theme states**

Capture:

```text
1440x1000 light
1440x1000 dark
375x812 light
375x812 dark
```

For each, inspect the rendered screenshot for horizontal containment, readable revisions/timings, one primary commit action, lifecycle text, mobile tabs, focus visibility, and no secret URI/path exposure.

- [ ] **Step 8: Verify keyboard and dirty-navigation behavior**

Use Tab, Shift+Tab, ArrowUp/Down, Home/End, Enter, Escape where supported, browser Back, and top-level menu navigation. Confirm selection remains visible, cancelled dirty navigation remains in the workspace, and confirmed navigation returns to preserved Documents context.

- [ ] **Step 9: Verify reduced motion and axe**

Emulate `prefers-reduced-motion: reduce`. Confirm no essential state depends on animation. Load axe-core and run against desktop and mobile light/dark workspace states. Record confirmed violations separately from third-party/manual incompletes and fix all feature-owned confirmed violations.

- [ ] **Step 10: Review requirements and diff**

Run:

```powershell
git status --short
git diff --check
git log --oneline -8
```

Review every spec section against code and tests. Confirm no raw preview/history fabrication, no false projection completion copy, no mixed backend/frontend commit, and no unrelated changes.

- [ ] **Step 11: Commit any verification fixes by scope**

Frontend-only fixes:

```powershell
git add -- frontend
git commit -m "fix(frontend): finalize parse intervention workspace"
```

Backend-only fixes:

```powershell
git add -- server/documents.py tests/test_chunk_authority_api.py tests/test_document_chunk_operations.py
git commit -m "fix(api): finalize chunk lifecycle projection"
```

Skip empty commits.
