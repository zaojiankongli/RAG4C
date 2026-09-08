import { request } from "../../api/client";
import type {
  ItemFilters, RunAccepted, RunFilters, RunItemListResponse, RunListResponse,
  SourceCreate, SourceListResponse, SourcePatch, SourceRecord, SourceScope, SourceStatus,
  SourceRun, SourceSyncRequest,
} from "../model/sourceModels";

export interface SourceRequestOptions { signal?: AbortSignal; }
function headers(scope: SourceScope): Record<string, string> {
  return { "X-RAG4C-Tenant": scope.tenantId, Authorization: `Bearer ${scope.actorToken}` };
}
function root(scope: SourceScope): string { return `/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}/sources`; }
function sourcePath(scope: SourceScope, sourceId: string): string { return `${root(scope)}/${encodeURIComponent(sourceId)}`; }
function get<T>(scope: SourceScope, path: string, options: SourceRequestOptions): Promise<T> {
  return request<T>(path, { method: "GET", headers: headers(scope), signal: options.signal });
}
function json<T>(scope: SourceScope, path: string, method: "POST" | "PATCH", body: unknown, options: SourceRequestOptions, extraHeaders: Record<string, string> = {}): Promise<T> {
  return request<T>(path, { method, headers: { ...headers(scope), ...extraHeaders }, body: JSON.stringify(body), signal: options.signal });
}
function query(path: string, values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  const encoded = params.toString();
  return encoded ? `${path}?${encoded}` : path;
}

export function fetchSources(scope: SourceScope, status?: SourceStatus, options: SourceRequestOptions = {}): Promise<SourceListResponse> {
  return get(scope, query(root(scope), { status }), options);
}
export function fetchSource(scope: SourceScope, sourceId: string, options: SourceRequestOptions = {}): Promise<SourceRecord> {
  return get(scope, sourcePath(scope, sourceId), options);
}
export function createSource(scope: SourceScope, payload: SourceCreate, options: SourceRequestOptions = {}): Promise<SourceRecord> {
  return json(scope, root(scope), "POST", payload, options);
}
export function patchSource(scope: SourceScope, sourceId: string, payload: SourcePatch, options: SourceRequestOptions = {}): Promise<SourceRecord> {
  return json(scope, sourcePath(scope, sourceId), "PATCH", payload, options);
}
function setEnabled(scope: SourceScope, sourceId: string, action: "enable" | "disable", generation: number, options: SourceRequestOptions): Promise<SourceRecord> {
  return json(scope, `${sourcePath(scope, sourceId)}/${action}`, "POST", { expected_generation: generation }, options);
}
export function enableSource(scope: SourceScope, sourceId: string, generation: number, options: SourceRequestOptions = {}): Promise<SourceRecord> {
  return setEnabled(scope, sourceId, "enable", generation, options);
}
export function disableSource(scope: SourceScope, sourceId: string, generation: number, options: SourceRequestOptions = {}): Promise<SourceRecord> {
  return setEnabled(scope, sourceId, "disable", generation, options);
}
export function syncSourceNow(scope: SourceScope, sourceId: string, payload: SourceSyncRequest, idempotencyKey: string, options: SourceRequestOptions = {}): Promise<RunAccepted> {
  return json(scope, `${sourcePath(scope, sourceId)}/sync-now`, "POST", payload, options, { "Idempotency-Key": idempotencyKey });
}
export function fetchRuns(scope: SourceScope, sourceId: string, filters: RunFilters = {}, options: SourceRequestOptions = {}): Promise<RunListResponse> {
  return get(scope, query(`${sourcePath(scope, sourceId)}/runs`, { status: filters.status, trigger: filters.trigger, cursor: filters.cursor, limit: filters.limit ?? 10 }), options);
}
export function fetchRun(scope: SourceScope, sourceId: string, runId: string, options: SourceRequestOptions = {}): Promise<SourceRun> {
  return get(scope, `${sourcePath(scope, sourceId)}/runs/${encodeURIComponent(runId)}`, options);
}
export function fetchRunItems(scope: SourceScope, sourceId: string, runId: string, filters: ItemFilters = {}, options: SourceRequestOptions = {}): Promise<RunItemListResponse> {
  return get(scope, query(`${sourcePath(scope, sourceId)}/runs/${encodeURIComponent(runId)}/items`, { result: filters.result, action: filters.action, cursor: filters.cursor, limit: filters.limit ?? 10 }), options);
}
export function retryRun(scope: SourceScope, sourceId: string, runId: string, options: SourceRequestOptions = {}): Promise<RunAccepted> {
  return json(scope, `${sourcePath(scope, sourceId)}/runs/${encodeURIComponent(runId)}/retry`, "POST", {}, options);
}
