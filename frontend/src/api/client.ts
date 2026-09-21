/**
 * 桥服务 HTTP 客户端（领域 API 层）。
 *
 * transport 层（地址/超时/取消/错误/SSE 流式解析）已抽取到 ./transport.ts，
 * 本文件仅保留按域组织的业务 API，并重新导出 transport 的核心符号，保证既有
 * `from "../api/client"` 调用路径不变。
 *
 * 业务 API 分组：health / query / metrics / graph / eval / config / documents /
 * knowledge-auth / consistency。
 */
export {
  DEFAULT_BASE,
  getBaseUrl,
  setBaseUrl,
  ApiError,
  request,
  streamAnswer,
  type ApiErrorKind,
  type RequestOptions,
  type QueryPayload,
  type StreamHandlers,
} from "./transport";
import { ApiError, request, type QueryPayload } from "./transport";

import type {
  ConfigSnapshot,
  ConfigUpdateResponse,
  DatasetList,
  DocumentDeleteBatchStatusResponse,
  DocumentDeleteOperationResponse,
  DocumentDetail,
  DocumentList,
  DocumentManagementSettings,
  DocumentMetricsResponse,
  DocumentCatalogSummaryResponse,
  DocumentPageQuery,
  DocumentPageResponse,
  BatchDocumentSettingsResponse,
  EvalReport,
  EvalRunResponse,
  FolderIngestResponse,
  GraphSubgraph,
  HealthInfo,
  IngestResponse,
  MetricStat,
  MetricsSnapshot,
  QueryResponse,
} from "../types/rag";
import type {
  ConsistencySummaryResponse,
  DeadLetterListResponse,
  DeadLetterRequeueResponse,
} from "../consistency/consistencyModel";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";

/** 探测桥服务健康状态；失败抛错（调用方决定是否回退 mock） */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthInfo> {
  return request<HealthInfo>("/api/health", { method: "GET", timeoutMs: 8_000, signal });
}

/** 提问。失败抛错（调用方决定是否回退 mock）；可传 signal 取消（停止生成） */
export async function fetchAnswer(
  payload: QueryPayload,
  signal?: AbortSignal,
): Promise<QueryResponse> {
  return request<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: 250_000,
    signal,
  });
}

/** 指标快照 + 最近查询 */
export async function fetchMetrics(signal?: AbortSignal): Promise<MetricsSnapshot> {
  return request<MetricsSnapshot>("/api/metrics", { method: "GET", timeoutMs: 10_000, signal });
}

/** 持久化的指标历史（data/metrics-history.jsonl 尾部快照，60s 粒度） */
export interface MetricsHistoryItem {
  ts: string;
  metrics: Record<string, MetricStat>;
}

export async function fetchMetricsHistory(
  limit = 120,
  signal?: AbortSignal,
): Promise<{ items: MetricsHistoryItem[]; count: number }> {
  return request<{ items: MetricsHistoryItem[]; count: number }>(
    "/api/metrics/history?limit=" + limit,
    { method: "GET", timeoutMs: 10_000, signal },
  );
}

/** 图谱检索（实体 + 关系） */
export async function graphSearch(
  query: string,
  topK = 8,
  signal?: AbortSignal,
): Promise<GraphSubgraph> {
  return request<GraphSubgraph>("/api/graph/search", {
    method: "POST",
    body: JSON.stringify({
      query,
      entity_top_k: topK,
      relation_top_k: topK,
    }),
    timeoutMs: 20_000,
    signal,
  });
}

/** 按实体/关系 ID 取子图 */
export async function graphSubgraph(
  params: {
    entity_ids?: string[];
    relation_ids?: string[];
    degree?: number;
  },
  signal?: AbortSignal,
): Promise<GraphSubgraph> {
  const qs = new URLSearchParams();
  if (params.entity_ids?.length) qs.set("entity_ids", params.entity_ids.join(","));
  if (params.relation_ids?.length) qs.set("relation_ids", params.relation_ids.join(","));
  qs.set("degree", String(params.degree ?? 1));
  return request<GraphSubgraph>("/api/graph/subgraph?" + qs.toString(), {
    method: "GET",
    timeoutMs: 20_000,
    signal,
  });
}

/** 运行评测（dry-run 或真实管线；真实管线耗时长，超时放宽） */
export async function runEval(
  payload: {
    dataset_spec: string;
    pipeline: string;
    out?: string;
  },
  signal?: AbortSignal,
): Promise<EvalRunResponse> {
  return request<EvalRunResponse>("/api/eval/run", {
    method: "POST",
    body: JSON.stringify(payload),
    timeoutMs: 600_000,
    signal,
  });
}

/** 可用评测数据集 */
export async function fetchEvalDatasets(signal?: AbortSignal): Promise<DatasetList> {
  return request<DatasetList>("/api/eval/datasets", { method: "GET", timeoutMs: 10_000, signal });
}

/** 最近一次评测报告 */
export async function fetchEvalResults(
  signal?: AbortSignal,
): Promise<{ report: EvalReport | null }> {
  return request<{ report: EvalReport | null }>("/api/eval/results", {
    method: "GET",
    timeoutMs: 10_000,
    signal,
  });
}

/** 完整配置清单（配置中心） */
export async function fetchConfig(signal?: AbortSignal): Promise<ConfigSnapshot> {
  return request<ConfigSnapshot>("/api/config", { method: "GET", timeoutMs: 3_000, signal });
}
/** 保存配置到 .env（path: [{"path","value"}, ...]） */
export async function updateConfig(
  updates: { path: string; value: unknown }[],
  signal?: AbortSignal,
): Promise<ConfigUpdateResponse> {
  return request<ConfigUpdateResponse>("/api/config/update", {
    method: "POST",
    body: JSON.stringify({ updates }),
    timeoutMs: 30_000,
    signal,
  });
}

/* ===================== 文档管理（server/documents.py） ===================== */

/** 知识库下的文档列表（含状态机状态与进度） */
export async function fetchDocuments(
  datasetId = "default",
  signal?: AbortSignal,
): Promise<DocumentList> {
  return request<DocumentList>("/api/documents?dataset_id=" + encodeURIComponent(datasetId), {
    method: "GET",
    timeoutMs: 10_000,
    signal,
  });
}

/** 单个文档详情（含分段进度汇总，轮询进度用） */
export async function fetchDocument(docId: string, signal?: AbortSignal): Promise<DocumentDetail> {
  return request<DocumentDetail>("/api/documents/" + encodeURIComponent(docId), {
    method: "GET",
    timeoutMs: 10_000,
    signal,
  });
}

/** 登记文档并后台入库（立即返回 document_id，进度需轮询 fetchDocument） */
export async function ingestDocument(
  payload: {
    file_path: string;
    dataset_id?: string;
    tenant_id?: string;
    name?: string;
    doc_type?: string;
  },
  signal?: AbortSignal,
  auth?: { tenantId?: string; actorToken?: string },
): Promise<IngestResponse> {
  const headers: Record<string, string> = {};
  const token = (auth?.actorToken ?? readKnowledgeActorToken() ?? "").trim();
  const tenant = auth?.tenantId?.trim();
  if (token || tenant) {
    Object.assign(headers, knowledgeAuthHeaders(tenant || payload.tenant_id || "default", token || undefined));
  }
  return request<IngestResponse>("/api/documents/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
    headers,
    timeoutMs: 30_000,
    signal,
  });
}

/** 递归扫描服务器文件夹，并用一个后台批次逐个入库 */
export async function ingestFolder(
  payload: {
    folder_path: string;
    dataset_id?: string;
    tenant_id?: string;
  },
  signal?: AbortSignal,
  auth?: { tenantId?: string; actorToken?: string },
): Promise<FolderIngestResponse> {
  const headers: Record<string, string> = {};
  const token = (auth?.actorToken ?? readKnowledgeActorToken() ?? "").trim();
  const tenant = auth?.tenantId?.trim();
  if (token || tenant) {
    Object.assign(headers, knowledgeAuthHeaders(tenant || payload.tenant_id || "default", token || undefined));
  }
  return request<FolderIngestResponse>("/api/documents/ingest-folder", {
    method: "POST",
    body: JSON.stringify(payload),
    headers,
    timeoutMs: 60_000,
    signal,
  });
}

/** 增量重索引（文件哈希变化检测 + chunk 级差量重建，后台执行） */
export async function reindexDocument(
  docId: string,
  force = false,
  signal?: AbortSignal,
  auth?: { tenantId?: string; actorToken?: string },
): Promise<IngestResponse> {
  const headers: Record<string, string> = {};
  const token = (auth?.actorToken ?? readKnowledgeActorToken() ?? "").trim();
  const tenant = auth?.tenantId?.trim();
  if (token || tenant) {
    Object.assign(
      headers,
      knowledgeAuthHeaders(tenant || "default", token || undefined),
    );
  }
  return request<IngestResponse>("/api/documents/" + encodeURIComponent(docId) + "/reindex", {
    method: "POST",
    body: JSON.stringify({ force }),
    headers,
    timeoutMs: 30_000,
    signal,
  });
}

/** Replace one document's logical category and tags. */
export async function updateDocumentSettings(
  docId: string,
  payload: { logical_folder_path: string; tags: string[] },
  signal?: AbortSignal,
): Promise<DocumentManagementSettings> {
  return request<DocumentManagementSettings>(
    "/api/documents/" + encodeURIComponent(docId) + "/settings",
    { method: "PATCH", body: JSON.stringify(payload), timeoutMs: 15_000, signal },
  );
}

/** Move selected documents and merge/remove tags. */
export async function batchUpdateDocumentSettings(
  payload: { document_ids: string[]; logical_folder_path?: string; add_tags?: string[]; remove_tags?: string[] },
  signal?: AbortSignal,
): Promise<BatchDocumentSettingsResponse> {
  return request<BatchDocumentSettingsResponse>("/api/documents/batch-settings", {
    method: "POST", body: JSON.stringify(payload), timeoutMs: 30_000, signal,
  });
}

export interface KnowledgeAuthRequestOptions {
  actorToken?: string;
  tenantId?: string;
  signal?: AbortSignal;
}

export interface KnowledgeDeleteRequestOptions extends KnowledgeAuthRequestOptions {
  idempotencyKey?: string;
}

function knowledgeActorToken(explicit?: string): string {
  return readKnowledgeActorToken(explicit);
}

function requireKnowledgeActorToken(value?: string): string {
  const token = knowledgeActorToken(value).trim();
  if (!token) throw new Error("actorToken is required for authenticated document catalog requests");
  return token;
}

export function shouldRetainDocumentDeleteIdempotencyKey(error: unknown): boolean {
  return !(
    error instanceof ApiError &&
    error.kind === "http" &&
    typeof error.status === "number" &&
    error.status >= 400 &&
    error.status < 500
  );
}

export function createDocumentDeleteIdempotencyKey(prefix: string): string {
  const randomId = globalThis.crypto?.randomUUID?.();
  return randomId
    ? prefix + "-" + randomId
    : prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function requireKnowledgeTenantId(value?: string): string {
  const tenantId = value?.trim();
  if (!tenantId) throw new Error("tenantId is required for authenticated KnowledgeOps requests");
  return tenantId;
}

export function knowledgeAuthHeaders(tenantId: string, actorToken?: string): Record<string, string> {
  const token = knowledgeActorToken(actorToken);
  return {
    "X-RAG4C-Tenant": tenantId,
    ...(token ? { Authorization: "Bearer " + token } : {}),
  };
}

function appendNonDefaultDocumentFilter(
  params: URLSearchParams,
  name: string,
  value: string | undefined,
  defaultValue: string,
): void {
  const normalized = value?.trim();
  if (normalized && normalized !== defaultValue) params.set(name, normalized);
}

/** Read one authenticated, server-paginated document catalog page. */
export async function fetchDocumentPage(
  datasetId: string,
  query: DocumentPageQuery,
  options: KnowledgeAuthRequestOptions = {},
): Promise<DocumentPageResponse> {
  const tenantId = requireKnowledgeTenantId(options.tenantId);
  const actorToken = requireKnowledgeActorToken(options.actorToken);
  const params = new URLSearchParams({
    offset: String(query.offset),
    limit: String(query.limit),
  });
  appendNonDefaultDocumentFilter(params, "q", query.q, "");
  appendNonDefaultDocumentFilter(params, "status", query.status, "all");
  appendNonDefaultDocumentFilter(params, "doc_type", query.doc_type, "all");
  appendNonDefaultDocumentFilter(params, "engine", query.engine, "all");
  appendNonDefaultDocumentFilter(params, "folder", query.folder, "all");
  appendNonDefaultDocumentFilter(params, "folder_mode", query.folder_mode, "exact");
  appendNonDefaultDocumentFilter(params, "tag", query.tag, "all");
  appendNonDefaultDocumentFilter(params, "lifecycle_state", query.lifecycle_state, "all");
  appendNonDefaultDocumentFilter(params, "sort", query.sort, "updated_at_desc");
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());

  return request<DocumentPageResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) + "/documents?" + params.toString(),
    {
      method: "GET",
      headers: knowledgeAuthHeaders(tenantId, actorToken),
      timeoutMs: 10_000,
      signal: options.signal,
    },
  );
}

/** Read authenticated, catalog-wide document metrics, facets and recent assets. */
export async function fetchDocumentSummary(
  datasetId: string,
  options: KnowledgeAuthRequestOptions = {},
): Promise<DocumentCatalogSummaryResponse> {
  const tenantId = requireKnowledgeTenantId(options.tenantId);
  const actorToken = requireKnowledgeActorToken(options.actorToken);
  return request<DocumentCatalogSummaryResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) + "/documents/summary",
    {
      method: "GET",
      headers: knowledgeAuthHeaders(tenantId, actorToken),
      timeoutMs: 10_000,
      signal: options.signal,
    },
  );
}

function knowledgeDeleteHeaders(
  tenantId: string,
  idempotencyKey: string,
  actorToken?: string,
): Record<string, string> {
  return {
    ...knowledgeAuthHeaders(tenantId, actorToken),
    "Idempotency-Key": idempotencyKey,
  };
}

function requireDocumentGeneration(value: number, documentId: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error("expected_generation is required for document " + documentId);
  }
  return value;
}

/** Submit one authenticated, durable delete request. */
export async function requestDocumentDelete(
  datasetId: string,
  docId: string,
  payload: { expected_generation: number; reason?: string },
  options: KnowledgeDeleteRequestOptions = {},
): Promise<DocumentDeleteOperationResponse> {
  const tenantId = options.tenantId?.trim() || "default";
  const expectedGeneration = requireDocumentGeneration(payload.expected_generation, docId);
  const idempotencyKey = options.idempotencyKey ?? createDocumentDeleteIdempotencyKey("delete-document");
  return request<DocumentDeleteOperationResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) +
      "/documents/" + encodeURIComponent(docId) + "/delete",
    {
      method: "POST",
      headers: knowledgeDeleteHeaders(tenantId, idempotencyKey, options.actorToken),
      body: JSON.stringify({ ...payload, expected_generation: expectedGeneration }),
      timeoutMs: 30_000,
      signal: options.signal,
    },
  );
}

/** Submit up to 100 authenticated, durable delete items. */
export async function requestDocumentBatchDelete(
  datasetId: string,
  payload: {
    items: { document_id: string; expected_generation: number }[];
    reason?: string;
  },
  options: KnowledgeDeleteRequestOptions = {},
): Promise<DocumentDeleteBatchStatusResponse> {
  const tenantId = options.tenantId?.trim() || "default";
  const items = payload.items.map((item) => ({
    ...item,
    expected_generation: requireDocumentGeneration(item.expected_generation, item.document_id),
  }));
  const idempotencyKey = options.idempotencyKey ?? createDocumentDeleteIdempotencyKey("delete-batch");
  return request<DocumentDeleteBatchStatusResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) + "/documents/batch-delete",
    {
      method: "POST",
      headers: knowledgeDeleteHeaders(tenantId, idempotencyKey, options.actorToken),
      body: JSON.stringify({ ...payload, items }),
      timeoutMs: 30_000,
      signal: options.signal,
    },
  );
}

/** Poll one durable delete operation. */
export async function fetchDocumentDeleteOperation(
  datasetId: string,
  operationId: string,
  options: Omit<KnowledgeDeleteRequestOptions, "idempotencyKey"> = {},
): Promise<DocumentDeleteOperationResponse> {
  const tenantId = options.tenantId?.trim() || "default";
  return request<DocumentDeleteOperationResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) +
      "/document-delete-operations/" + encodeURIComponent(operationId),
    {
      headers: knowledgeAuthHeaders(tenantId, options.actorToken),
      signal: options.signal,
    },
  );
}

/** Poll one durable batch delete. */
export async function fetchDocumentDeleteBatch(
  datasetId: string,
  batchId: string,
  options: Omit<KnowledgeDeleteRequestOptions, "idempotencyKey"> = {},
): Promise<DocumentDeleteBatchStatusResponse> {
  const tenantId = options.tenantId?.trim() || "default";
  return request<DocumentDeleteBatchStatusResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) +
      "/document-delete-batches/" + encodeURIComponent(batchId),
    {
      headers: knowledgeAuthHeaders(tenantId, options.actorToken),
      signal: options.signal,
    },
  );
}

/** Persistent document-ingestion aggregate metrics. */
export async function fetchDocumentMetrics(datasetId = "default", signal?: AbortSignal): Promise<DocumentMetricsResponse> {
  return request<DocumentMetricsResponse>("/api/documents/metrics?dataset_id=" + encodeURIComponent(datasetId), {
    method: "GET", timeoutMs: 10_000, signal,
  });
}

/** Read the report-only, catalog-scoped consistency summary. */
export async function fetchConsistencySummary(
  datasetId: string,
  options: KnowledgeAuthRequestOptions = {},
): Promise<ConsistencySummaryResponse> {
  const tenantId = requireKnowledgeTenantId(options.tenantId);
  return request<ConsistencySummaryResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) + "/consistency/summary",
    {
      method: "GET",
      headers: knowledgeAuthHeaders(tenantId, options.actorToken),
      timeoutMs: 30_000,
      signal: options.signal,
    },
  );
}

/** List tenant- and dataset-scoped consistency dead letters. */
export async function fetchConsistencyDeadLetters(
  datasetId: string,
  options: KnowledgeAuthRequestOptions = {},
): Promise<DeadLetterListResponse> {
  const tenantId = requireKnowledgeTenantId(options.tenantId);
  return request<DeadLetterListResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) + "/consistency/dead-letters",
    {
      method: "GET",
      headers: knowledgeAuthHeaders(tenantId, options.actorToken),
      signal: options.signal,
    },
  );
}

/** Requeue one scoped dead letter; no repair-plan or repair authority is exposed here. */
export async function requeueConsistencyDeadLetter(
  datasetId: string,
  deadLetterRef: string,
  options: KnowledgeAuthRequestOptions & { operatorNote?: string } = {},
): Promise<DeadLetterRequeueResponse> {
  const tenantId = requireKnowledgeTenantId(options.tenantId);
  return request<DeadLetterRequeueResponse>(
    "/api/knowledge-bases/" + encodeURIComponent(datasetId) +
      "/consistency/dead-letters/" + encodeURIComponent(deadLetterRef) + "/requeue",
    {
      method: "POST",
      headers: knowledgeAuthHeaders(tenantId, options.actorToken),
      body: JSON.stringify({ operator_note: options.operatorNote ?? "" }),
      timeoutMs: 30_000,
      signal: options.signal,
    },
  );
}
