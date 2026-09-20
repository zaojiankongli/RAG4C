import { knowledgeAuthHeaders, request } from "../../api/client";
import { resolveKnowledgeWorkspaceScope } from "../../knowledge/workspaceScope";
import {
  normalizeAnswerFact,
  normalizeAnswerFactOrList,
  type AnswerFact,
  type AnswerFactListResponse,
} from "../model/answerEvidenceModel";

export interface AnswerEvidenceRequestOptions {
  signal?: AbortSignal;
  tenantId?: string;
  datasetId?: string;
  actorToken?: string;
}

export interface AnswerFactListQuery {
  outcome?: string;
  run_id?: string;
  limit?: number;
}

/**
 * Knowledge actor auth — same contract as storageBackendsApi:
 * resolveKnowledgeWorkspaceScope + knowledgeAuthHeaders (Bearer + X-RAG4C-Tenant).
 */
function authHeaders(options: AnswerEvidenceRequestOptions = {}): Record<string, string> {
  const scope = resolveKnowledgeWorkspaceScope({
    tenantId: options.tenantId,
    datasetId: options.datasetId,
    actorToken: options.actorToken,
  });
  return knowledgeAuthHeaders(scope.tenantId, options.actorToken);
}

/** Resolve datasetId for answer-facts paths (localStorage / token scope / default). */
export function resolveAnswerEvidenceDatasetId(options: AnswerEvidenceRequestOptions = {}): string {
  return resolveKnowledgeWorkspaceScope({
    tenantId: options.tenantId,
    datasetId: options.datasetId,
    actorToken: options.actorToken,
  }).datasetId;
}

export function answerEvidenceBase(datasetId: string): string {
  return `/api/knowledge-bases/${encodeURIComponent(datasetId)}/answer-facts`;
}

function buildListQuery(query: AnswerFactListQuery = {}): string {
  const params = new URLSearchParams();
  const outcome = query.outcome?.trim();
  if (outcome) params.set("outcome", outcome);
  const runId = query.run_id?.trim();
  if (runId) params.set("run_id", runId);
  if (typeof query.limit === "number" && Number.isFinite(query.limit) && query.limit > 0) {
    params.set("limit", String(Math.floor(query.limit)));
  }
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** GET /api/knowledge-bases/{datasetId}/answer-facts?outcome=&run_id=&limit= */
export async function fetchAnswerFacts(
  datasetId: string,
  query: AnswerFactListQuery = {},
  options: AnswerEvidenceRequestOptions = {},
): Promise<AnswerFactListResponse> {
  const path = `${answerEvidenceBase(datasetId)}${buildListQuery(query)}`;
  const payload = await request<unknown>(path, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
  return normalizeAnswerFactOrList(payload);
}

/** GET /api/knowledge-bases/{datasetId}/answer-facts/{factId} */
export async function fetchAnswerFact(
  datasetId: string,
  factId: string,
  options: AnswerEvidenceRequestOptions = {},
): Promise<AnswerFact> {
  const path = `${answerEvidenceBase(datasetId)}/${encodeURIComponent(factId)}`;
  const payload = await request<unknown>(path, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
  const fact = normalizeAnswerFact(payload);
  if (!fact) {
    throw new Error("answer-fact response did not include a valid fact id");
  }
  return fact;
}

/** GET /api/knowledge-bases/{datasetId}/answer-facts/by-run/{runId} */
export async function fetchAnswerFactsByRun(
  datasetId: string,
  runId: string,
  options: AnswerEvidenceRequestOptions = {},
): Promise<AnswerFactListResponse> {
  const path = `${answerEvidenceBase(datasetId)}/by-run/${encodeURIComponent(runId)}`;
  const payload = await request<unknown>(path, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
  return normalizeAnswerFactOrList(payload);
}

/**
 * Panel loader: prefer by-run when a run is selected; otherwise list recent facts.
 * 404 on by-run is treated as empty (run has no catalog fact yet).
 */
export async function loadAnswerEvidence(
  datasetId: string,
  params: { runId?: string | null; limit?: number } = {},
  options: AnswerEvidenceRequestOptions = {},
): Promise<AnswerFactListResponse> {
  const runId = params.runId?.trim();
  if (runId) {
    try {
      return await fetchAnswerFactsByRun(datasetId, runId, options);
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status === 404) return { items: [], count: 0 };
      throw error;
    }
  }
  return fetchAnswerFacts(datasetId, { limit: params.limit ?? 10 }, options);
}
