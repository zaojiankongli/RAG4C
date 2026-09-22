import { request } from "../../api/client";
import { getBaseUrl } from "../../api/client";
import type { DocumentChunkList, DocumentChunkUpdateResponse, DocumentChunkDeleteResponse, DocumentChunkItem, DocumentChunkRevisionList } from "../../types/rag";
import type { ParseScope } from "../model/parseInterventionModel";

export interface ParsePageOptions { offset: number; limit: number; query?: string; includeDisabled?: boolean; }
function headers(scope: ParseScope) { return { Authorization: `Bearer ${scope.actorToken}`, "X-RAG4C-Tenant": scope.tenantId }; }
function base(scope: ParseScope) { return `/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}/documents/${encodeURIComponent(scope.docId)}/chunks`; }
function chunkUrl(scope: ParseScope, chunkId: string) { return `${base(scope)}/${encodeURIComponent(chunkId)}`; }
export function fetchParseChunkPage(scope: ParseScope, options: ParsePageOptions, signal: AbortSignal): Promise<DocumentChunkList> {
  const params = new URLSearchParams({ offset: String(options.offset), limit: String(options.limit) });
  if (options.query?.trim()) params.set("query", options.query.trim());
  if (options.includeDisabled) params.set("include_disabled", "true");
  return request<DocumentChunkList>(`${base(scope)}?${params}`, { method: "GET", headers: headers(scope), signal, timeoutMs: 20_000 });
}
export function fetchParseChunkDetail(scope: ParseScope, chunkId: string, signal: AbortSignal): Promise<DocumentChunkItem> {
  return request<DocumentChunkItem>(chunkUrl(scope, chunkId), { method: "GET", headers: headers(scope), signal });
}
export function patchParseChunk(scope: ParseScope, chunkId: string, text: string, expectedRevision: number, reason: string, signal: AbortSignal): Promise<DocumentChunkUpdateResponse> {
  return request<DocumentChunkUpdateResponse>(chunkUrl(scope, chunkId), { method: "PATCH", headers: headers(scope), body: JSON.stringify({ text, expected_revision: expectedRevision, reason }), signal, timeoutMs: 120_000 });
}
/** 启停切片：`enabled:false` 写入只读墓碑，`enabled:true` 从墓碑恢复；两者都走 ChunkHead CAS。 */
export function setParseChunkEnabled(scope: ParseScope, chunkId: string, enabled: boolean, expectedRevision: number, reason: string, signal: AbortSignal): Promise<DocumentChunkUpdateResponse> {
  return request<DocumentChunkUpdateResponse>(chunkUrl(scope, chunkId), { method: "PATCH", headers: headers(scope), body: JSON.stringify({ enabled, expected_revision: expectedRevision, reason }), signal, timeoutMs: 120_000 });
}
export function fetchParseChunkRevisions(scope: ParseScope, chunkId: string, signal: AbortSignal): Promise<DocumentChunkRevisionList> {
  return request<DocumentChunkRevisionList>(`${chunkUrl(scope, chunkId)}/revisions`, { method: "GET", headers: headers(scope), signal, timeoutMs: 20_000 });
}
export function revertParseChunk(scope: ParseScope, chunkId: string, targetRevision: number, expectedRevision: number, reason: string, signal: AbortSignal): Promise<DocumentChunkUpdateResponse> {
  return request<DocumentChunkUpdateResponse>(`${chunkUrl(scope, chunkId)}/revert`, { method: "POST", headers: headers(scope), body: JSON.stringify({ target_revision: targetRevision, expected_revision: expectedRevision, reason }), signal, timeoutMs: 120_000 });
}
export function tombstoneParseChunk(scope: ParseScope, chunkId: string, expectedRevision: number, signal: AbortSignal): Promise<DocumentChunkDeleteResponse> {
  return request<DocumentChunkDeleteResponse>(`${chunkUrl(scope, chunkId)}?expected_revision=${expectedRevision}`, { method: "DELETE", headers: headers(scope), signal, timeoutMs: 120_000 });
}

/** 原文字节。类型与 inline/下载由后端的可查看来源表决定，前端不自己猜 content type。 */
export interface DocumentSourceBlob { blob: Blob; mediaType: string; disposition: string; etag: string | null; }
export class SourcePreviewRefusedError extends Error {
  code: string;
  status: number;
  constructor(message: string, code: string, status: number) { super(message); this.name = "SourcePreviewRefusedError"; this.code = code; this.status = status; }
}
export async function fetchDocumentSource(scope: ParseScope, disposition: "inline" | "attachment", signal: AbortSignal): Promise<DocumentSourceBlob> {
  const url = `${getBaseUrl()}/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}/documents/${encodeURIComponent(scope.docId)}/source-preview?disposition=${disposition}`;
  const response = await fetch(url, { method: "GET", headers: headers(scope), signal });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as { detail?: { code?: string; message?: string } } | null;
    throw new SourcePreviewRefusedError(
      detail?.detail?.message || "原文查看被拒绝",
      detail?.detail?.code || "source_preview_unavailable",
      response.status,
    );
  }
  return {
    blob: await response.blob(),
    mediaType: response.headers.get("Content-Type") ?? "",
    disposition: response.headers.get("Content-Disposition") ?? "",
    etag: response.headers.get("ETag"),
  };
}
