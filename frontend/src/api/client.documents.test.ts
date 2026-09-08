import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DocumentCatalogSummaryResponse,
  DocumentPageResponse,
} from "../types/rag";
import {
  ApiError,
  fetchDocumentPage,
  fetchDocumentSummary,
  requestDocumentBatchDelete,
  requestDocumentDelete,
  shouldRetainDocumentDeleteIdempotencyKey,
} from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const documentPageResponse = {
  items: [
    {
      id: "doc-1",
      tenant_id: "tenant-a",
      dataset_id: "dataset / A",
      name: "员工手册.pdf",
      status: "completed",
      status_detail: "",
      progress: 1,
      chunk_count: 128,
      doc_type: "pdf",
      error_message: "",
      logical_folder_path: "制度/人力",
      tags: ["员工", "制度"],
      source_uri: null,
      source_type: "local",
      source_id: null,
      external_id: null,
      mutation_generation: 2,
      lifecycle_state: "active",
      retrieval_enabled: true,
      active_delete_operation_id: null,
      parser_meta: { engine: "vision" },
      updated_at: "2026-08-26T10:00:00",
    },
  ],
  total: 98,
  offset: 20,
  limit: 20,
  next_cursor: "opaque-next-cursor",
} satisfies DocumentPageResponse;

const documentSummaryResponse = {
  dataset_id: "dataset / A",
  summary: {
    total: 98,
    completed: 90,
    processing: 3,
    failed: 5,
    chunks: 12420,
    parser_observed: 92,
    parser_coverage: 94,
  },
  facets: {
    statuses: {
      all: 98,
      waiting: 1,
      parsing: 1,
      splitting: 0,
      indexing: 1,
      processing: 3,
      completed: 90,
      error: 5,
    },
    types: [{ value: "pdf", count: 48 }],
    engines: [{ value: "vision", count: 35 }],
    folders: [{ path: "制度/人力", documents: 20, chunks: 2400 }],
    tags: [{ name: "员工", documents: 18, chunks: 2100 }],
  },
  recent: documentPageResponse.items,
  generated_at: "2026-08-26T10:00:00Z",
  tag_facets_complete: true,
  tag_facets_scan_limit: 1000,
  tag_facets_scanned: 98,
  tag_facets_truncated: false,
} satisfies DocumentCatalogSummaryResponse;

describe("document chunk authority client", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fails closed before fetch when single delete generation is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      requestDocumentDelete(
        "dataset-a",
        "doc-1",
        { expected_generation: undefined as unknown as number },
        { tenantId: "tenant-a", idempotencyKey: "delete-key" },
      ),
    ).rejects.toThrow("expected_generation");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed before fetch when any batch delete generation is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      requestDocumentBatchDelete(
        "dataset-a",
        { items: [{ document_id: "doc-1", expected_generation: undefined as unknown as number }] },
        { tenantId: "tenant-a", idempotencyKey: "batch-key" },
      ),
    ).rejects.toThrow("expected_generation");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("retains idempotency keys only for uncertain transport and server failures", () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(
        shouldRetainDocumentDeleteIdempotencyKey(new ApiError("deterministic", "http", status)),
      ).toBe(false);
    }
    for (const status of [500, 502, 503]) {
      expect(
        shouldRetainDocumentDeleteIdempotencyKey(new ApiError("server failure", "http", status)),
      ).toBe(true);
    }
    expect(shouldRetainDocumentDeleteIdempotencyKey(new ApiError("offline", "network"))).toBe(true);
    expect(shouldRetainDocumentDeleteIdempotencyKey(new ApiError("timeout", "timeout"))).toBe(true);
    expect(shouldRetainDocumentDeleteIdempotencyKey(new ApiError("aborted", "aborted"))).toBe(true);
  });

  it("requests the authenticated document page with only non-default filters", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(documentPageResponse),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchDocumentPage(
        "dataset / A",
        {
          offset: 20,
          limit: 20,
          q: "员工 手册",
          status: "processing",
          doc_type: "pdf",
          engine: "vision",
          folder: "制度/人力",
          folder_mode: "subtree",
          tag: "员工&制度",
          lifecycle_state: "active",
          sort: "name_asc",
        },
        { tenantId: "tenant-a", actorToken: "actor-token" },
      ),
    ).resolves.toEqual(documentPageResponse);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      "http://localhost:8000/api/knowledge-bases/dataset%20%2F%20A/documents" +
        "?offset=20&limit=20&q=%E5%91%98%E5%B7%A5+%E6%89%8B%E5%86%8C" +
        "&status=processing&doc_type=pdf&engine=vision" +
        "&folder=%E5%88%B6%E5%BA%A6%2F%E4%BA%BA%E5%8A%9B&folder_mode=subtree" +
        "&tag=%E5%91%98%E5%B7%A5%26%E5%88%B6%E5%BA%A6" +
        "&lifecycle_state=active&sort=name_asc",
    );
    expect(init).toEqual(
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer actor-token",
          "X-RAG4C-Tenant": "tenant-a",
        }),
      }),
    );
  });

  it("omits blank and default document page filters from the URL", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ ...documentPageResponse, offset: 0 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchDocumentPage(
      "dataset-a",
      {
        offset: 0,
        limit: 20,
        q: "   ",
        status: "all",
        doc_type: "all",
        engine: "all",
        folder: "all",
        folder_mode: "exact",
        tag: "all",
        lifecycle_state: "all",
        sort: "updated_at_desc",
      },
      { tenantId: "tenant-a", actorToken: "actor-token" },
    );

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/knowledge-bases/dataset-a/documents?offset=0&limit=20",
    );
  });

  it("requests the authenticated catalog summary without list filters", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(documentSummaryResponse),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchDocumentSummary("dataset / A", {
        tenantId: "tenant-a",
        actorToken: "actor-token",
      }),
    ).resolves.toEqual(documentSummaryResponse);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      "http://localhost:8000/api/knowledge-bases/dataset%20%2F%20A/documents/summary",
    );
    expect(init).toEqual(
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer actor-token",
          "X-RAG4C-Tenant": "tenant-a",
        }),
      }),
    );
  });

  it("cancels document catalog reads through the provided AbortSignal", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const requestPromise = fetchDocumentSummary("dataset-a", {
      tenantId: "tenant-a",
      actorToken: "actor-token",
      signal: controller.signal,
    });
    controller.abort();

    await expect(requestPromise).rejects.toMatchObject({ kind: "aborted" });
    expect(fetchMock.mock.calls[0][1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("fails closed before fetch when the document page has no actor token", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchDocumentPage(
        "dataset-a",
        { offset: 0, limit: 20 },
        { tenantId: "tenant-a" },
      ),
    ).rejects.toThrow("actorToken");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed before fetch when the document summary has no actor token", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchDocumentSummary("dataset-a", { tenantId: "tenant-a" }),
    ).rejects.toThrow("actorToken");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends an opaque keyset cursor alongside the page size", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(documentPageResponse),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchDocumentPage(
      "dataset-a",
      { offset: 0, limit: 20, cursor: "opaque-next-cursor" },
      { tenantId: "tenant-a", actorToken: "actor-token" },
    );

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "http://localhost:8000/api/knowledge-bases/dataset-a/documents" +
        "?offset=0&limit=20&cursor=opaque-next-cursor",
    );
  });

});
