import { request } from "../../api/client";
import type {
  Agreement, ExperimentDetail, ExperimentListResponse, HistoryFilters, Judgment,
  JudgmentCreate, JudgmentPatch, RetrievalScope, RunResponse, RunRetrievalRequest,
} from "../model/contracts";
import { normalizeHistoryQuery } from "../model/validation";

export interface RetrievalRequestOptions { signal?: AbortSignal; }

function headers(scope: RetrievalScope): Record<string, string> {
  return { "X-RAG4C-Tenant": scope.tenantId, Authorization: `Bearer ${scope.actorToken}` };
}
function base(scope: RetrievalScope): string {
  return `/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}/retrieval-experiments`;
}
function json<T>(scope: RetrievalScope, path: string, method: "POST" | "PATCH", body: unknown, options: RetrievalRequestOptions): Promise<T> {
  return request<T>(path, { method, headers: headers(scope), body: JSON.stringify(body), signal: options.signal });
}

export function runRetrievalComparison(scope: RetrievalScope, payload: RunRetrievalRequest, options: RetrievalRequestOptions = {}): Promise<RunResponse> {
  return request<RunResponse>(`${base(scope)}/run`, {
    method: "POST", headers: headers(scope), body: JSON.stringify(payload), signal: options.signal, timeoutMs: 120_000,
  });
}

export function fetchExperiments(scope: RetrievalScope, filters: HistoryFilters = {}, options: RetrievalRequestOptions = {}): Promise<ExperimentListResponse> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.queryHash) query.set("query_hash", filters.queryHash);
  if (filters.runId?.trim()) query.set("run_id", filters.runId.trim());
  if (filters.beforeSequence !== undefined) query.set("before_sequence", String(filters.beforeSequence));
  query.set("limit", "10");
  return request<ExperimentListResponse>(`${base(scope)}?${query.toString()}`, { method: "GET", headers: headers(scope), signal: options.signal });
}

export function fetchExperiment(scope: RetrievalScope, experimentId: string, options: RetrievalRequestOptions = {}): Promise<ExperimentDetail> {
  return request<ExperimentDetail>(`${base(scope)}/${encodeURIComponent(experimentId)}`, { method: "GET", headers: headers(scope), signal: options.signal });
}
export function createJudgment(scope: RetrievalScope, experimentId: string, payload: JudgmentCreate, options: RetrievalRequestOptions = {}): Promise<Judgment> {
  return json(scope, `${base(scope)}/${encodeURIComponent(experimentId)}/judgments`, "POST", payload, options);
}
export function patchJudgment(scope: RetrievalScope, experimentId: string, judgmentId: string, payload: JudgmentPatch, options: RetrievalRequestOptions = {}): Promise<Judgment> {
  return json(scope, `${base(scope)}/${encodeURIComponent(experimentId)}/judgments/${encodeURIComponent(judgmentId)}`, "PATCH", payload, options);
}
export function fetchAgreement(scope: RetrievalScope, experimentId: string, options: RetrievalRequestOptions = {}): Promise<Agreement> {
  return request<Agreement>(`${base(scope)}/${encodeURIComponent(experimentId)}/agreement`, { method: "GET", headers: headers(scope), signal: options.signal });
}

export async function hashNormalizedQuery(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(normalizeHistoryQuery(value));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
}
