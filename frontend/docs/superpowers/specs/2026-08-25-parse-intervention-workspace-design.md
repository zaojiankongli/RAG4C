# Parse Intervention Workspace Design

**Date:** 2026-08-25

## Objective

Replace the cramped expanded-row chunk inspector with a dedicated, document-scoped Parse Intervention Workspace. The workspace must let operators inspect parsed context, navigate authoritative chunk heads, edit or tombstone a selected chunk with CAS protection, and understand that a successful ChunkHead mutation does not prove Milvus or graph convergence.

The implementation is frontend-first. One narrowly additive backend contract extension is included because the current chunk list excludes disabled heads and omits document-revision and projection-lifecycle facts required by the workspace. Raw source preview, chunk revision history, and persisted edit reasons remain explicit capability gaps.

## Routing and return context

The workspace is a nested Documents route rather than a new product-area page:

```text
/documents/parse?doc=<document-id>
#/documents/parse?doc=<document-id>
```

The existing route parser continues to select the `documents` page key. `DocumentsPage` listens for its nested route, renders the workspace at full content size, and preserves its kept-alive filters, category, selection, and pagination state. A row-local `解析干预` action pushes the nested route into browser history. Closing uses browser history when the workspace was opened from Documents and falls back to `/documents` for a direct deep link.

A dirty edit session guards browser back/forward, application navigation, workspace close, document changes, and window unload. Confirmed navigation resets the draft and aborts in-flight work. Cancelled navigation keeps focus in the editor session.

## Frontend architecture

The feature lives under `frontend/src/parse-intervention`:

```text
model/
api/
hooks/
components/
ParseInterventionWorkspace.tsx
parse-intervention.css
```

- Model code owns safe fact projection, metadata allowlisting, source sanitization, outline inference, warning generation, token estimation, projection lifecycle, diff preview, and dirty-session rules.
- API code wraps the existing document/chunk endpoints with the exact additive list fields.
- A controller hook owns parallel loading, selection, filtering, scope validation, AbortControllers, scope-generation fencing, CAS mutations, conflict recovery, and projection receipts.
- Focused components render the workspace header, parsed-context pane, contained chunk list, editor, diff, conflict recovery, and projection receipt.
- `DocumentsPage` remains the owner of list state, opening/closing the workspace, and invalidating the shared document snapshot after accepted mutations.

## Scope and concurrency safety

Every load and mutation captures the current tenant, dataset, document ID, and scope generation. A response may update state only while all captured values remain current. Scope or document changes abort all outstanding requests and clear document, chunk, draft, conflict, and receipt state.

The route may open only for a document present in the current authoritative Documents snapshot. The returned document detail must match the current tenant and dataset before any chunk facts render. Missing or mismatched scope fails closed and sends no mutation. Offline/demo documents never fall back to fabricated chunk detail.

No actor token, credential-bearing URI, raw connector configuration, file-system credential path, or arbitrary metadata value is rendered.

## Workspace composition

### Header

The header contains return navigation, document identity, authority mode, a compact Knowledge Lifeline, operator links to Consistency and Monitor, and one top-right `提交修改` action while the selected edit session is dirty. Secondary actions do not compete with the commit action.

The Knowledge Lifeline is the feature's signature visual element:

```text
source facts → document revision → ChunkHead revision → projection state
```

It is informational, not decorative, and uses text plus color so lifecycle does not depend on color alone.

### Parsed context pane

The left pane displays only available authoritative facts:

- document name and safe abbreviated ID;
- tenant and dataset labels;
- document mutation/content generation where present;
- source type and sanitized source identity;
- parser engine, PDF classification, chunking mode, page count, layout blocks, text characters, confidence, and total duration;
- stage waterfall with millisecond values and timing-coverage status;
- page/section outline inferred from chunk page, heading, sequence, and parent relationships.

The pane never invents a raw document preview. It displays an explicit source-preview capability gap when no endpoint exists.

### Chunk list pane

The center pane is a contained virtualized list with deferred search and filters for lifecycle, warning, projection state, page/section, and relation. Each row displays sequence/heading, enabled or tombstoned state, content revision, document revision, deterministic character/token estimate, parent/child relation, validation warnings, and projection lifecycle.

Arrow Up/Down, Home/End, and Enter support keyboard selection. Filtering and conflict reloads retain a valid selection where possible and move focus predictably when the selected row disappears.

### Selected chunk editor

The right pane presents current authoritative content, contextual header, safe allowlisted metadata, immutable identifiers and CAS facts, edit reason, diff preview, projection-impact copy, reset/copy controls, and delete.

HTTP 409 does not discard the draft. The workspace reloads the authoritative list and exposes server content beside the local draft with actions to adopt the server version or continue the draft against the new revision.

Delete uses explicit tombstone/durable-projection language. It is never described as synchronous multi-store deletion.

## Mutation and projection semantics

The active authority flow is:

```text
operator edit or delete
→ immutable prior ChunkRevision
→ ChunkHead CAS
→ durable Milvus/graph operations
→ Consistency/Monitor convergence
```

A successful PATCH or DELETE means the authoritative mutation was accepted. When the response returns `projection_pending` or operation IDs, the workspace shows a durable queued/pending receipt and operator links. It never reports projection completion merely because the request returned successfully.

Legacy `off` and transitional `shadow` authority modes are labeled honestly. If no durable operation receipt exists, the UI explains that durable projection verification is unavailable rather than manufacturing one.

The current API does not persist an edit reason. The frontend keeps the reason in the dirty session and accepted-action receipt, labels it as not persisted, and does not claim it is part of audit history.

## Narrow backend contract addition

Extend the existing endpoint without changing default behavior:

```http
GET /api/documents/{doc_id}/chunks?include_disabled=true
```

When ChunkHead authority is active, `include_disabled=true` passes through to `ChunkCatalog.list_document_heads`. Add these optional operator-safe fields to each item:

```json
{
  "tenant_id": "tenant-a",
  "dataset_id": "dataset-a",
  "document_revision": 7,
  "enabled": false,
  "chunk_role": "flat",
  "desired_index_revision": 5,
  "indexed_revision": 4,
  "index_status": "pending"
}
```

The endpoint default remains `include_disabled=false`, preserving existing consumers. Legacy projections populate only facts they authoritatively possess and do not invent disabled or index-status data.

Backend tests cover default exclusion, explicit disabled inclusion, and exact projection fields. This change is committed separately from the frontend implementation.

## Capability gaps

The implementation does not add or fabricate:

- raw document source preview;
- chunk revision history;
- persisted edit reason.

Future contracts should be tenant/dataset scoped and authenticated:

```http
GET /api/knowledge/datasets/{dataset_id}/documents/{doc_id}/source-preview
GET /api/knowledge/datasets/{dataset_id}/documents/{doc_id}/chunks/{chunk_id}/revisions
```

A future edit mutation may add a bounded `reason` field backed by an audited revision attribute. This must not be simulated in local-only metadata.

## Visual system

The workspace uses native TDesign controls and the existing RAG4C Knowledge Lifeline token system. It does not copy Tencent branding, icons, colors, spacing, or component styling.

Primary semantic colors are existing RAG4C values:

- Lifeline blue `#3164F4`
- Projection cyan `#07879A`
- Revision violet `#7656C9`
- Authority green `#166534`
- Pending amber `#6B2D0E`
- Conflict red `#B42318`

Body text uses the existing local system/PingFang/Segoe stack. IDs, revisions, and timing facts use the existing local monospace stack. No remote fonts or assets are introduced.

At 1440px the workspace uses three contained columns with restrained borders and no page-level horizontal overflow. At 375px it uses three pane tabs (`上下文`, `切片`, `编辑`) and renders one pane at a time instead of compressing the desktop grid. Focus rings remain visible, motion is limited, and `prefers-reduced-motion` removes nonessential transitions.

## Error handling

- Missing scope: fail-closed scope state and no request.
- Offline/demo mode: honest unavailable state and no fake chunks.
- Initial load failure: retryable error without preserving facts from another scope.
- Scope mismatch: clear data and show an authorization/scope failure.
- Abort or stale generation: silent discard.
- HTTP 409: preserve draft and enter conflict recovery.
- HTTP 404 after reload: clear selection and explain that the chunk no longer exists.
- Other mutation errors: preserve the dirty session and show a safe structured message.
- Projection receipt: persists for the workspace session until superseded or dismissed.

## Test strategy

TDD cycles cover:

1. model projections, source/metadata sanitization, outline inference, warnings, token estimates, projection lifecycle, and diff output;
2. API query/payload integration and additive chunk-list fields;
3. desktop pane landmarks and mobile tabs;
4. keyboard selection and focus restoration;
5. CAS conflict recovery with preserved draft and updated expected revision;
6. stale scope load and mutation suppression;
7. dirty navigation confirmation and unload protection;
8. authoritative mutation acceptance versus projection-pending copy;
9. source-preview, revision-history, and edit-reason capability labels;
10. backend default/exact `include_disabled` behavior.

Final verification runs focused and full Vitest with a 10-second test timeout, TypeScript/Vite build, ESLint, focused backend tests, Playwright at 375 and 1440 in light and dark themes, reduced-motion checks, horizontal-containment inspection, and axe scans. A final code review checks the approved requirements line by line before the backend and frontend changes are committed separately.
