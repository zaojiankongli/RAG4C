import { request } from "../../api/client";
import type { DocumentChunkList, DocumentChunkUpdateResponse, DocumentChunkDeleteResponse, DocumentChunkItem } from "../../types/rag";
import type { ParseScope } from "../model/parseInterventionModel";

export interface ParsePageOptions { offset: number; limit: number; query?: string; includeDisabled?: boolean; }
function headers(scope: ParseScope) { return { Authorization: `Bearer ${scope.actorToken}`, "X-RAG4C-Tenant": scope.tenantId }; }
function base(scope: ParseScope) { return `/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}/documents/${encodeURIComponent(scope.docId)}/chunks`; }
export function fetchParseChunkPage(scope: ParseScope, options: ParsePageOptions, signal: AbortSignal): Promise<DocumentChunkList> {
  const params = new URLSearchParams({ offset: String(options.offset), limit: String(options.limit) });
  if (options.query?.trim()) params.set("query", options.query.trim());
  if (options.includeDisabled) params.set("include_disabled", "true");
  return request<DocumentChunkList>(`${base(scope)}?${params}`, { method: "GET", headers: headers(scope), signal, timeoutMs: 20_000 });
}
export function fetchParseChunkDetail(scope: ParseScope, chunkId: string, signal: AbortSignal): Promise<DocumentChunkItem> {
  return request<DocumentChunkItem>(`${base(scope)}/${encodeURIComponent(chunkId)}`, { method: "GET", headers: headers(scope), signal });
}
export function patchParseChunk(scope: ParseScope, chunkId: string, text: string, expectedRevision: number, signal: AbortSignal): Promise<DocumentChunkUpdateResponse> {
  return request<DocumentChunkUpdateResponse>(`${base(scope)}/${encodeURIComponent(chunkId)}`, { method: "PATCH", headers: headers(scope), body: JSON.stringify({ text, expected_revision: expectedRevision }), signal, timeoutMs: 120_000 });
}
export function tombstoneParseChunk(scope: ParseScope, chunkId: string, expectedRevision: number, signal: AbortSignal): Promise<DocumentChunkDeleteResponse> {
  return request<DocumentChunkDeleteResponse>(`${base(scope)}/${encodeURIComponent(chunkId)}?expected_revision=${expectedRevision}`, { method: "DELETE", headers: headers(scope), signal, timeoutMs: 120_000 });
}
