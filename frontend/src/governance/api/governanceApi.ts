import { request } from "../../api/client";
import type {
  DatasetProfile,
  DatasetProfilePatch,
  DatasetRevisionRequest,
  DocumentVersion,
  DocumentVersionCreate,
  DocumentVersionListResponse,
  GovernanceScope,
  QAAlternativeCreate,
  QAAlternativeCreated,
  QACreate,
  QAKnowledge,
  QAListFilters,
  QAListResponse,
  QAReviewRequest,
  QAUpdate,
} from "../model/governanceModel";

export interface GovernanceRequestOptions {
  signal?: AbortSignal;
}

function headers(scope: GovernanceScope): Record<string, string> {
  return {
    "X-RAG4C-Tenant": scope.tenantId,
    Authorization: `Bearer ${scope.actorToken}`,
  };
}

function basePath(scope: GovernanceScope): string {
  return `/api/knowledge-bases/${encodeURIComponent(scope.datasetId)}`;
}

function jsonRequest<T>(
  scope: GovernanceScope,
  path: string,
  method: "POST" | "PATCH",
  body: unknown,
  options: GovernanceRequestOptions,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: headers(scope),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function fetchDatasetProfile(
  scope: GovernanceScope,
  options: GovernanceRequestOptions = {},
): Promise<DatasetProfile> {
  return request<DatasetProfile>(basePath(scope), {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  });
}

export function patchDatasetProfile(
  scope: GovernanceScope,
  payload: DatasetProfilePatch,
  options: GovernanceRequestOptions = {},
): Promise<DatasetProfile> {
  return jsonRequest(scope, basePath(scope), "PATCH", payload, options);
}

function datasetLifecycle(
  scope: GovernanceScope,
  action: "archive" | "restore" | "disable",
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions,
): Promise<DatasetProfile> {
  return jsonRequest(scope, `${basePath(scope)}/${action}`, "POST", payload, options);
}

export function archiveDataset(
  scope: GovernanceScope,
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions = {},
): Promise<DatasetProfile> {
  return datasetLifecycle(scope, "archive", payload, options);
}

export function restoreDataset(
  scope: GovernanceScope,
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions = {},
): Promise<DatasetProfile> {
  return datasetLifecycle(scope, "restore", payload, options);
}

export function disableDataset(
  scope: GovernanceScope,
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions = {},
): Promise<DatasetProfile> {
  return datasetLifecycle(scope, "disable", payload, options);
}

export function fetchQAList(
  scope: GovernanceScope,
  filters: QAListFilters = {},
  options: GovernanceRequestOptions = {},
): Promise<QAListResponse> {
  const query = new URLSearchParams();
  if (filters.review_status) query.set("review_status", filters.review_status);
  if (filters.lifecycle_state) query.set("lifecycle_state", filters.lifecycle_state);
  if (filters.origin) query.set("origin", filters.origin);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  const suffix = query.size ? `?${query.toString()}` : "";
  return request<QAListResponse>(`${basePath(scope)}/qa${suffix}`, {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  });
}

export function createQA(
  scope: GovernanceScope,
  payload: QACreate,
  options: GovernanceRequestOptions = {},
): Promise<QAKnowledge> {
  return jsonRequest(scope, `${basePath(scope)}/qa`, "POST", payload, options);
}

export function patchQA(
  scope: GovernanceScope,
  qaId: string,
  payload: QAUpdate,
  options: GovernanceRequestOptions = {},
): Promise<QAKnowledge> {
  return jsonRequest(
    scope,
    `${basePath(scope)}/qa/${encodeURIComponent(qaId)}`,
    "PATCH",
    payload,
    options,
  );
}

export function reviewQA(
  scope: GovernanceScope,
  qaId: string,
  payload: QAReviewRequest,
  options: GovernanceRequestOptions = {},
): Promise<QAKnowledge> {
  return jsonRequest(
    scope,
    `${basePath(scope)}/qa/${encodeURIComponent(qaId)}/review`,
    "POST",
    payload,
    options,
  );
}

function qaLifecycle(
  scope: GovernanceScope,
  qaId: string,
  action: "expire" | "restore",
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions,
): Promise<QAKnowledge> {
  return jsonRequest(
    scope,
    `${basePath(scope)}/qa/${encodeURIComponent(qaId)}/${action}`,
    "POST",
    payload,
    options,
  );
}

export function expireQA(
  scope: GovernanceScope,
  qaId: string,
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions = {},
): Promise<QAKnowledge> {
  return qaLifecycle(scope, qaId, "expire", payload, options);
}

export function restoreQA(
  scope: GovernanceScope,
  qaId: string,
  payload: DatasetRevisionRequest,
  options: GovernanceRequestOptions = {},
): Promise<QAKnowledge> {
  return qaLifecycle(scope, qaId, "restore", payload, options);
}

export function addQAAlternative(
  scope: GovernanceScope,
  qaId: string,
  payload: QAAlternativeCreate,
  options: GovernanceRequestOptions = {},
): Promise<QAAlternativeCreated> {
  return jsonRequest(
    scope,
    `${basePath(scope)}/qa/${encodeURIComponent(qaId)}/alternatives`,
    "POST",
    payload,
    options,
  );
}

export function deleteQAAlternative(
  scope: GovernanceScope,
  qaId: string,
  alternativeId: string,
  expectedRevision: number,
  options: GovernanceRequestOptions = {},
): Promise<void> {
  const path = `${basePath(scope)}/qa/${encodeURIComponent(qaId)}/alternatives/${encodeURIComponent(alternativeId)}?expected_revision=${expectedRevision}`;
  return request<void>(path, {
    method: "DELETE",
    headers: headers(scope),
    signal: options.signal,
  });
}

export function fetchDocumentVersions(
  scope: GovernanceScope,
  documentId: string,
  limit = 100,
  options: GovernanceRequestOptions = {},
): Promise<DocumentVersionListResponse> {
  return request<DocumentVersionListResponse>(
    `${basePath(scope)}/documents/${encodeURIComponent(documentId)}/versions?limit=${limit}`,
    { method: "GET", headers: headers(scope), signal: options.signal },
  );
}

export function createDocumentVersion(
  scope: GovernanceScope,
  documentId: string,
  payload: DocumentVersionCreate,
  options: GovernanceRequestOptions = {},
): Promise<DocumentVersion> {
  return jsonRequest(
    scope,
    `${basePath(scope)}/documents/${encodeURIComponent(documentId)}/versions`,
    "POST",
    payload,
    options,
  );
}
