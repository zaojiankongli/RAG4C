import { ApiError, getBaseUrl, knowledgeAuthHeaders, request } from "../../api/client";
import { resolveKnowledgeWorkspaceScope } from "../../knowledge/workspaceScope";
import type {
  KnowledgeBaseOption,
  StorageBackend,
  StorageBackendBindPayload,
  StorageBackendCreatePayload,
  StorageBackendListResponse,
  StorageBackendPatchPayload,
  StorageBackendTestResult,
  StorageBackendTypesResponse,
} from "../model/storageBackendModel";

export interface StorageBackendsRequestOptions {
  signal?: AbortSignal;
  tenantId?: string;
  actorToken?: string;
}

const BASE = "/api/storage-backends";

function authHeaders(options: StorageBackendsRequestOptions = {}): Record<string, string> {
  const scope = resolveKnowledgeWorkspaceScope({
    tenantId: options.tenantId,
    actorToken: options.actorToken,
  });
  return knowledgeAuthHeaders(scope.tenantId, options.actorToken);
}

function jsonHeaders(options: StorageBackendsRequestOptions = {}): Record<string, string> {
  return {
    "Content-Type": "application/json",
    ...authHeaders(options),
  };
}

function jsonRequest<T>(
  path: string,
  method: "POST" | "PATCH" | "PUT",
  body: unknown,
  options: StorageBackendsRequestOptions = {},
): Promise<T> {
  return request<T>(path, {
    method,
    headers: jsonHeaders(options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

/** GET /api/storage-backends/types — provider 字段定义 */
export function fetchStorageBackendTypes(
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackendTypesResponse> {
  return request<StorageBackendTypesResponse>(`${BASE}/types`, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
}

/** GET /api/storage-backends — 列表 + 租户默认 id */
export function fetchStorageBackends(
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackendListResponse> {
  return request<StorageBackendListResponse>(BASE, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
}

/** GET /api/storage-backends/{id} */
export function fetchStorageBackend(
  id: string,
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackend> {
  return request<StorageBackend>(`${BASE}/${encodeURIComponent(id)}`, {
    method: "GET",
    headers: authHeaders(options),
    signal: options.signal,
  });
}

/** POST /api/storage-backends — 创建（201） */
export function createStorageBackend(
  payload: StorageBackendCreatePayload,
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackend> {
  return jsonRequest<StorageBackend>(BASE, "POST", payload, options);
}

/** PATCH /api/storage-backends/{id} — 空 secret 字段后端保持原值 */
export function patchStorageBackend(
  id: string,
  payload: StorageBackendPatchPayload,
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackend> {
  return jsonRequest<StorageBackend>(
    `${BASE}/${encodeURIComponent(id)}`,
    "PATCH",
    payload,
    options,
  );
}

/** DELETE /api/storage-backends/{id} — 204；默认或已绑定 → 409 */
export async function deleteStorageBackend(
  id: string,
  options: StorageBackendsRequestOptions = {},
): Promise<void> {
  await request<void>(`${BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(options),
    signal: options.signal,
  });
}

/** POST /api/storage-backends/test — 请求体试连，不落库 */
export function testStorageBackendConfig(
  payload: { provider: string; config: Record<string, unknown> },
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackendTestResult> {
  return jsonRequest<StorageBackendTestResult>(`${BASE}/test`, "POST", payload, options);
}

/** POST /api/storage-backends/{id}/test — 测已保存实例 */
export function testStorageBackend(
  id: string,
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackendTestResult> {
  return jsonRequest<StorageBackendTestResult>(
    `${BASE}/${encodeURIComponent(id)}/test`,
    "POST",
    {},
    options,
  );
}

/** POST /api/storage-backends/{id}/default — 设为租户默认 */
export function setDefaultStorageBackend(
  id: string,
  options: StorageBackendsRequestOptions = {},
): Promise<StorageBackend> {
  return jsonRequest<StorageBackend>(
    `${BASE}/${encodeURIComponent(id)}/default`,
    "POST",
    {},
    options,
  );
}

/** PUT /api/storage-backends/bind-dataset — 知识库绑定 */
export function bindDatasetStorageBackend(
  payload: StorageBackendBindPayload,
  options: StorageBackendsRequestOptions = {},
): Promise<unknown> {
  return jsonRequest<unknown>(`${BASE}/bind-dataset`, "PUT", payload, options);
}

/**
 * 可选：列出知识库供绑定选择器使用。
 * 后端 list_profiles 返回开放 JSON；这里做宽容解析。
 */
export async function fetchKnowledgeBaseOptions(
  options: StorageBackendsRequestOptions = {},
): Promise<KnowledgeBaseOption[]> {
  const path = "/api/knowledge-bases";
  try {
    const res = await fetch(`${getBaseUrl()}${path}`, {
      method: "GET",
      headers: { Accept: "application/json", ...authHeaders(options) },
      signal: options.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(text || `HTTP ${res.status}`, "http", res.status);
    }
    const payload = (await res.json()) as unknown;
    return normalizeKnowledgeBaseOptions(payload);
  } catch {
    return [];
  }
}

export function normalizeKnowledgeBaseOptions(payload: unknown): KnowledgeBaseOption[] {
  const rawItems = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object"
      ? ((payload as { items?: unknown }).items ??
        (payload as { datasets?: unknown }).datasets ??
        (payload as { data?: unknown }).data ??
        [])
      : [];
  if (!Array.isArray(rawItems)) return [];
  return rawItems
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      const id = String(record.id ?? record.dataset_id ?? "").trim();
      if (!id) return null;
      const name = String(record.name ?? record.title ?? id).trim();
      return { id, name };
    })
    .filter((item): item is KnowledgeBaseOption => item !== null);
}
