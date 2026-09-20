import { beforeEach, describe, expect, it, vi } from "vitest";
import { getBaseUrl, request } from "../../api/client";
import type {
  StorageBackend,
  StorageBackendListResponse,
  StorageBackendTestResult,
  StorageBackendTypesResponse,
} from "../model/storageBackendModel";
import {
  bindDatasetStorageBackend,
  createStorageBackend,
  deleteStorageBackend,
  fetchKnowledgeBaseOptions,
  fetchStorageBackend,
  fetchStorageBackendTypes,
  fetchStorageBackends,
  normalizeKnowledgeBaseOptions,
  patchStorageBackend,
  setDefaultStorageBackend,
  testStorageBackend,
  testStorageBackendConfig,
} from "./storageBackendsApi";

vi.mock("../../api/client", () => ({
  request: vi.fn(),
  getBaseUrl: vi.fn(() => "http://localhost:8010"),
  knowledgeAuthHeaders: vi.fn((tenantId: string, actorToken?: string) => ({
    "X-RAG4C-Tenant": tenantId,
    ...(actorToken ? { Authorization: `Bearer ${actorToken}` } : {}),
  })),
  ApiError: class ApiError extends Error {
    status?: number;
    kind?: string;
    constructor(message: string, kind?: string, status?: number) {
      super(message);
      this.kind = kind;
      this.status = status;
    }
  },
}));

vi.mock("../../knowledge/workspaceScope", () => ({
  resolveKnowledgeWorkspaceScope: vi.fn(() => ({
    tenantId: "tenant-a",
    datasetId: "default",
  })),
}));

const requestMock = vi.mocked(request);
const getBaseUrlMock = vi.mocked(getBaseUrl);

const AUTH_HEADERS = {
  "X-RAG4C-Tenant": "tenant-a",
  Authorization: "Bearer tok",
};

const item: StorageBackend = {
  id: "sb-1",
  name: "prod-minio",
  provider: "minio",
  status: "active",
  source: "user",
  is_default: true,
  config_masked: {
    endpoint: "http://minio:9000",
    bucket: "rag4c",
    access_key_id: "ak_***abcd",
    secret_access_key: "***",
  },
  created_at: "2026-09-20T00:00:00Z",
  updated_at: "2026-09-20T01:00:00Z",
};

beforeEach(() => {
  requestMock.mockReset();
  getBaseUrlMock.mockReturnValue("http://localhost:8010");
  const store = new Map<string, string>();
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
  };
});

describe("storageBackendsApi", () => {
  it("fetches types, list, and single backend on the contract paths", async () => {
    const types: StorageBackendTypesResponse = {
      providers: [
        {
          provider: "minio",
          label: "MinIO",
          fields: [
            { name: "endpoint", label: "Endpoint", required: true },
            { name: "secret_access_key", label: "Secret", required: true, secret: true },
          ],
        },
      ],
    };
    requestMock.mockResolvedValueOnce(types);
    await expect(fetchStorageBackendTypes({ actorToken: "tok" })).resolves.toEqual(types);
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/types", {
      method: "GET",
      headers: AUTH_HEADERS,
      signal: undefined,
    });

    const list: StorageBackendListResponse = {
      items: [item],
      default_storage_backend_id: "sb-1",
    };
    requestMock.mockResolvedValueOnce(list);
    await expect(fetchStorageBackends({ actorToken: "tok" })).resolves.toEqual(list);
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends", {
      method: "GET",
      headers: AUTH_HEADERS,
      signal: undefined,
    });

    requestMock.mockResolvedValueOnce(item);
    await expect(fetchStorageBackend("sb-1", { actorToken: "tok" })).resolves.toEqual(item);
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/sb-1", {
      method: "GET",
      headers: AUTH_HEADERS,
      signal: undefined,
    });
  });

  it("creates, patches, deletes, tests, sets default, and binds on exact paths", async () => {
    const jsonAuth = { "Content-Type": "application/json", ...AUTH_HEADERS };
    const createPayload = {
      name: "local-root",
      provider: "local",
      config: { root_path: "/data/rag4c" },
    };
    requestMock.mockResolvedValueOnce(item);
    await createStorageBackend(createPayload, { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends", {
      method: "POST",
      headers: jsonAuth,
      body: JSON.stringify(createPayload),
      signal: undefined,
    });

    const patchPayload = {
      name: "prod-minio-2",
      config: { secret_access_key: "" },
    };
    requestMock.mockResolvedValueOnce(item);
    await patchStorageBackend("sb-1", patchPayload, { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/sb-1", {
      method: "PATCH",
      headers: jsonAuth,
      body: JSON.stringify(patchPayload),
      signal: undefined,
    });

    requestMock.mockResolvedValueOnce(undefined);
    await deleteStorageBackend("sb-1", { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/sb-1", {
      method: "DELETE",
      headers: AUTH_HEADERS,
      signal: undefined,
    });

    const testResult: StorageBackendTestResult = {
      status: "validated_config_only",
      detail: "结构校验通过；未安装 SDK，跳过写探测",
    };
    requestMock.mockResolvedValueOnce(testResult);
    await testStorageBackendConfig(
      {
        provider: "minio",
        config: { endpoint: "http://minio:9000", bucket: "rag4c" },
      },
      { actorToken: "tok" },
    );
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/test", {
      method: "POST",
      headers: jsonAuth,
      body: JSON.stringify({
        provider: "minio",
        config: { endpoint: "http://minio:9000", bucket: "rag4c" },
      }),
      signal: undefined,
    });

    requestMock.mockResolvedValueOnce(testResult);
    await testStorageBackend("sb/1", { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/sb%2F1/test", {
      method: "POST",
      headers: jsonAuth,
      body: JSON.stringify({}),
      signal: undefined,
    });

    requestMock.mockResolvedValueOnce(item);
    await setDefaultStorageBackend("sb-1", { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/sb-1/default", {
      method: "POST",
      headers: jsonAuth,
      body: JSON.stringify({}),
      signal: undefined,
    });

    const bindPayload = { dataset_id: "dataset-a", storage_backend_id: "sb-1" };
    requestMock.mockResolvedValueOnce({ ok: true });
    await bindDatasetStorageBackend(bindPayload, { actorToken: "tok" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/storage-backends/bind-dataset", {
      method: "PUT",
      headers: jsonAuth,
      body: JSON.stringify(bindPayload),
      signal: undefined,
    });
  });

  it("normalizes knowledge-base option payloads and never invents empty ids", () => {
    expect(
      normalizeKnowledgeBaseOptions({
        items: [
          { id: "ds-1", name: "Support" },
          { dataset_id: "ds-2" },
          { name: "missing-id" },
          null,
        ],
      }),
    ).toEqual([
      { id: "ds-1", name: "Support" },
      { id: "ds-2", name: "ds-2" },
    ]);
    expect(normalizeKnowledgeBaseOptions(undefined)).toEqual([]);
    expect(normalizeKnowledgeBaseOptions({ items: "nope" })).toEqual([]);
  });

  it("fetches knowledge-base options via raw getBaseUrl fetch and degrades to []", async () => {
    const okJson = vi.fn(async () => ({ items: [{ id: "ds-1", name: "KB" }] }));
    const failJson = vi.fn(async () => ({ items: [] }));
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce({
        ok: true,
        json: okJson,
      } as unknown as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => "boom",
      } as unknown as Response);

    await expect(
      fetchKnowledgeBaseOptions({ actorToken: "tok" }),
    ).resolves.toEqual([{ id: "ds-1", name: "KB" }]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8010/api/knowledge-bases",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer tok",
          "X-RAG4C-Tenant": "tenant-a",
        }),
      }),
    );

    await expect(fetchKnowledgeBaseOptions()).resolves.toEqual([]);
    expect(okJson).toHaveBeenCalled();
    expect(failJson).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });
});
