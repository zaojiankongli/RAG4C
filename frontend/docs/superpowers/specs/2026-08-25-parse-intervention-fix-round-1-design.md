# Parse Intervention Fix Round 1 Design

**Date:** 2026-08-25

## Objective

Close the Parse Intervention Workspace review findings without weakening RAG4C authority boundaries. The correction replaces unauthenticated legacy chunk access with actor-bound dataset-scoped APIs, moves chunk paging/search to SQL authority, removes unsafe response metadata, makes mutation responses mode-exact, and hardens frontend pagination, conflicts, navigation, URI handling, tabs, and listbox focus.

`off` mode is intentionally fail-closed. The intervention workspace requires SQL ChunkHead authority and returns structured `409 knowledge_chunk_authority_unavailable` until the deployment migrates to `shadow` or `active`.

## Authenticated API surface

Create `server/knowledge_chunks_api.py` with router prefix:

```text
/api/knowledge-bases/{dataset_id}/documents/{doc_id}/chunks
```

Routes:

```http
GET    /chunks
GET    /chunks/{chunk_id}
PATCH  /chunks/{chunk_id}
DELETE /chunks/{chunk_id}?expected_revision=N
```

Permissions:

- list/detail: `KNOWLEDGE_READ`
- patch: `KNOWLEDGE_WRITE`
- delete: `KNOWLEDGE_DELETE`, matching the existing stricter deletion policy

The router uses the standard actor dependency plus OpenAPI `KnowledgeBearerAuth`. Remote and loopback mutations require a signed actor token. Every handler verifies the Document and ChunkHead tenant/dataset/document tuple against the actor tenant and path dataset. Missing, cross-tenant, cross-dataset, and cross-document resources share one structured 404 concealment response.

Error contracts:

- 401: existing Knowledge Bearer contract
- 403: existing permission/dataset contract
- 404: `knowledge_resource_not_found`
- 409: `knowledge_chunk_conflict` or `knowledge_chunk_authority_unavailable`
- 422: `knowledge_chunk_invalid`
- 503: auth/catalog unavailability where applicable

The old `/api/documents/{doc_id}/chunks...` GET/PATCH/DELETE routes become structured `410 knowledge_chunk_legacy_route_gone`. All in-repo frontend consumers migrate before that change lands.

## SQL page and detail contracts

`ChunkCatalog` gains scoped SQL methods rather than materializing all content:

```python
list_document_heads_page(
    tenant_id,
    dataset_id,
    document_id,
    document_revision,
    *,
    offset,
    limit,
    query,
    include_disabled,
) -> ChunkHeadPage

get_head_scoped(
    tenant_id,
    dataset_id,
    document_id,
    chunk_id,
    *,
    document_revision,
) -> ChunkHead
```

The page method performs SQL filtering, ordering, count, offset, and limit. Query length is bounded to 256 and page size to 200. Search covers chunk ID, current content, context header, and allowlisted heading/title/section JSON fields. Ordering is `chunk_index, id`.

The result includes:

```json
{
  "authority_mode": "active",
  "items": [],
  "total": 417,
  "offset": 0,
  "limit": 100,
  "known_parent_ids": ["parent-1"],
  "missing_parent_ids": ["parent-removed"]
}
```

Parent relation facts are computed against the complete scoped document revision, independent of the visible page. Parent absence from the page is therefore `unknown` unless the backend explicitly reports it in `missing_parent_ids`.

## Safe chunk projection

List, detail, PATCH, and DELETE share one exact projection builder. Raw `chunk_metadata` is never returned. Allowed response facts are explicit:

- chunk/document IDs and revisions
- enabled/tombstone state
- role and parent ID
- child count
- sequence, page, heading
- content/context and hashes required by the editor
- character count and deterministic token estimate
- desired/indexed revision and index status
- projection pending derived from revision/status facts
- created/updated timestamps
- sanitized source reference and safe parse facts such as language/MIME type

URI sanitization applies to every URI-like value. Hierarchical URIs are parsed, then userinfo, query, and fragment are removed. File references expose only a safe basename. Unknown or opaque schemes return `protected_reference` rather than the original value.

## Authority-mode semantics

### Off

All scoped list/detail/mutations return:

```json
{
  "code": "knowledge_chunk_authority_unavailable",
  "message": "解析干预工作区需要 SQL ChunkHead 权威；请先迁移到 shadow 或 active 模式"
}
```

No projection-dependent editing remains.

### Shadow

- Reads are SQL ChunkHead pages/details.
- PATCH/DELETE record immutable authority revisions and update ChunkHead.
- Existing synchronous legacy Milvus/graph shadow writes still run.
- Response copy identifies authority recorded plus shadow/legacy projection semantics.
- `projection_pending` is not inferred from operation IDs.

### Active

- Reads are SQL ChunkHead pages/details.
- PATCH/DELETE perform CAS authority mutations and enqueue durable projection operations.
- Responses use the exact post-mutation ChunkHead lifecycle projection.
- `projection_pending` comes from desired/indexed revision and index status.
- Operation IDs are receipts, not proof of pending or completion by themselves.

## Frontend authenticated API

The parse intervention API receives a complete scope:

```ts
interface ParseScope {
  tenantId: string;
  datasetId: string;
  actorToken: string;
  docId: string;
}
```

Every request sends:

```http
Authorization: Bearer <actor token>
X-RAG4C-Tenant: <tenant ID>
```

The workspace fails closed before fetch when any scope field is missing. `DocumentWorkspace` and its tests are removed because it is no longer an application consumer and would preserve the insecure legacy contract. Legacy client methods are removed after migration.

The parse workspace uses the authoritative Documents snapshot for document/parser facts and the scoped chunk endpoints for chunk truth. Direct links wait for the shared document snapshot and never call the unauthenticated document-detail route.

## Paging and server search

The controller stores `items`, `total`, `offset`, `pageSize`, `query`, `hasMore`, and loading-more state. Initial and reset loads request offset zero. `Load more` appends unique items in server order. Search is debounced/deferred but executed by the server, resets the page, and aborts the previous request. Loaded/total copy remains explicit.

Selection survives appended pages where possible. If filtering removes the selection, the first loaded visible chunk becomes selected unless an orphaned conflict draft is pinned.

## Dirty routing

Dirty state is document-scoped. Any route transition from document A to B is treated as destructive navigation, including programmatic history changes and hash changes.

Opening from Documents writes an origin marker for both direct and hash deployments. Closing an origin-marked workspace uses `history.back()`. A direct deep link has no origin marker and closes via `replace` to Documents, so browser Back cannot reopen it. Cancelled navigation restores the previous document URL and draft.

## Mode-specific editor and receipts

Editor controls and copy depend on authority mode:

- `off`: no editor; migration alert only
- `shadow`: authority revision plus synchronous shadow/legacy projection warning
- `active`: CAS authority plus durable asynchronous projection warning

Tombstones are read-only. They cannot be edited, deleted again, or used as a rebase target.

Receipts derive projection state only from mode plus exact lifecycle fields. Operation IDs are displayed as references but never used alone to infer pending work.

## Conflict and orphan draft recovery

Conflict state is independent of current selection and loaded page:

```ts
interface OrphanDraft {
  chunkId: string;
  draft: string;
  reason: string;
  baseRevision: number;
  server: ChunkDetail | null;
  resolution: "conflict" | "missing" | "tombstone";
}
```

After HTTP 409, the controller fetches scoped chunk detail by ID. If the chunk is enabled, the operator may copy, discard, adopt server, or rebase on the new revision. If missing or tombstoned, the draft remains pinned for copy/discard only. Search, paging, and selection changes do not discard it. Rebase is impossible for tombstones, preventing conflict loops.

## URI and relation warnings

Frontend URI sanitization mirrors the backend contract and treats unknown opaque schemes as protected references. Parent warnings have three states:

- known: backend confirms the parent exists
- missing: backend explicitly reports it missing
- unknown: parent is outside the loaded page and the backend did not report it missing

Only `missing` is a warning.

## Accessibility

Mobile pane tabs implement the complete ARIA tabs pattern:

- tablist label
- stable tab IDs
- `aria-controls`
- panel `aria-labelledby`
- selected tab `tabIndex=0`, others `-1`
- Left/Right/Home/End keyboard movement and activation

The chunk list uses one focus owner: the listbox. Options are not independently tabbable. `aria-activedescendant` references an always-mounted option; virtualization adjusts the mounted window around the active item before updating the attribute.

## Testing and commits

TDD order:

1. P0 auth, concealment, OpenAPI, and legacy 410 tests
2. SQL paging/search/count/parent/safe-projection tests
3. exact PATCH/DELETE/detail mode tests
4. frontend authenticated API and missing-scope tests
5. pagination/server-search tests
6. doc-to-doc and hash/direct history tests
7. mode copy and receipt tests
8. orphan conflict/tombstone tests
9. URI/relation tests
10. tabs/listbox accessibility tests

Backend and frontend commits stay separate. Final verification includes security/auth tests, focused and full backend suites, focused and full Vitest, lint, build, 1440/375 light/dark Playwright, reduced motion, horizontal containment, and axe.
