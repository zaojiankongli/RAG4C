// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps, ReactNode } from "react";
import type {
  DocumentCatalogItem,
  DocumentCatalogSummaryResponse,
  DocumentChunkItem,
  DocumentDetail,
  DocumentItem,
} from "../types/rag";
import { KnowledgeWorkspaceProvider } from "../knowledge/KnowledgeWorkspaceContext";
import KnowledgeOverviewPage from "./KnowledgeOverviewPage";
import DocumentsPage from "./DocumentsPage";

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    kind: string;
    status?: number;

    constructor(message: string, kindOrStatus: string | number = "http", status?: number) {
      super(message);
      this.kind = typeof kindOrStatus === "string" ? kindOrStatus : "http";
      this.status = typeof kindOrStatus === "number" ? kindOrStatus : status;
    }
  }

  class MockSourcePreviewRefusedError extends Error {
    code: string;
    status: number;

    constructor(message: string, code: string, status: number) {
      super(message);
      this.name = "SourcePreviewRefusedError";
      this.code = code;
      this.status = status;
    }
  }

  return {
    ApiError: MockApiError,
    SourcePreviewRefusedError: MockSourcePreviewRefusedError,
    fetchDocuments: vi.fn(),
    fetchDocumentPage: vi.fn(),
    fetchDocumentSummary: vi.fn(),
    fetchDocument: vi.fn(),
    fetchDocumentChunks: vi.fn(),
    updateDocumentChunk: vi.fn(),
    deleteDocumentChunk: vi.fn(),
    fetchParseChunkPage: vi.fn(),
    fetchParseChunkDetail: vi.fn(),
    patchParseChunk: vi.fn(),
    tombstoneParseChunk: vi.fn(),
    setParseChunkEnabled: vi.fn(),
    fetchParseChunkRevisions: vi.fn(),
    revertParseChunk: vi.fn(),
    fetchDocumentSource: vi.fn(),
    ingestDocument: vi.fn(),
    ingestFolder: vi.fn(),
    reindexDocument: vi.fn(),
    createDocumentDeleteIdempotencyKey: vi.fn(),
    shouldRetainDocumentDeleteIdempotencyKey: vi.fn(),
    requestDocumentDelete: vi.fn(),
    requestDocumentBatchDelete: vi.fn(),
    fetchDocumentDeleteOperation: vi.fn(),
    fetchDocumentDeleteBatch: vi.fn(),
    updateDocumentSettings: vi.fn(),
    batchUpdateDocumentSettings: vi.fn(),
  };
});

const connection = vi.hoisted(() => ({
  online: true as boolean | null,
}));

const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock("../api/client", () => api);

const workspaceScopeApi = vi.hoisted(() => ({
  fetchWorkspaceDatasets: vi.fn(),
  fetchEnterpriseKnowledgeBaseDetail: vi.fn(),
}));

vi.mock("../enterprise-workspace/api/enterpriseWorkspaceApi", () => ({
  fetchWorkspaceDatasets: workspaceScopeApi.fetchWorkspaceDatasets,
}));
vi.mock("../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi", () => ({
  fetchEnterpriseKnowledgeBaseDetail: workspaceScopeApi.fetchEnterpriseKnowledgeBaseDetail,
}));
vi.mock("../parse-intervention/api/parseInterventionApi", () => ({
  fetchParseChunkPage: api.fetchParseChunkPage,
  fetchParseChunkDetail: api.fetchParseChunkDetail,
  patchParseChunk: api.patchParseChunk,
  tombstoneParseChunk: api.tombstoneParseChunk,
  setParseChunkEnabled: api.setParseChunkEnabled,
  fetchParseChunkRevisions: api.fetchParseChunkRevisions,
  revertParseChunk: api.revertParseChunk,
  fetchDocumentSource: api.fetchDocumentSource,
  SourcePreviewRefusedError: api.SourcePreviewRefusedError,
}));
vi.mock("../context/ConnectionContext", () => ({
  ConnectionProvider: ({ children }: { children: ReactNode }) => children,
  useConnection: () => ({
    online: connection.online,
    checking: connection.online === null,
    health: null,
    refresh: vi.fn(),
  }),
}));
vi.mock("../ui/feedback", () => ({ message: feedback }));

const DOCUMENT: DocumentItem = {
  id: "doc-1",
  name: "员工手册.md",
  status: "completed",
  progress: 1,
  chunk_count: 12,
  doc_type: "markdown",
  error_message: "",
  logical_folder_path: "人力资源",
  tags: ["制度"],
  tenant_id: "tenant-1",
  dataset_id: "default",
  mutation_generation: 0,
  lifecycle_state: "active",
  retrieval_enabled: true,
  active_delete_operation_id: null,
};

const SECOND_DOCUMENT: DocumentItem = {
  ...DOCUMENT,
  id: "doc-2",
  name: "薪酬制度.pdf",
  chunk_count: 5,
  doc_type: "pdf",
  logical_folder_path: "制度/薪酬",
};

const PROCESSING_DOCUMENT: DocumentItem = {
  ...DOCUMENT,
  id: "doc-busy",
  name: "正在解析的合同.pdf",
  status: "parsing",
  progress: 0.42,
  chunk_count: 0,
  doc_type: "pdf",
  status_detail: "视觉解析 4/10 页",
};

const MODERN_DOCUMENT: DocumentCatalogItem = {
  ...DOCUMENT,
  status: "completed",
  tenant_id: "tenant-1",
  dataset_id: "default",
  status_detail: "",
  parser_meta: {
    engine: "vision",
    chunking_mode: "parent_child",
    chunking_reason_code: "complex_or_structured",
    chunking_reason: "文本 480 字或存在 8 个版面块 → parent_child",
    total_ms: 120,
  },
  updated_at: "2026-08-26T10:00:00Z",
  logical_folder_path: "人力资源",
  tags: ["制度"],
  source_uri: null,
  source_type: "local",
  source_id: null,
  external_id: null,
  mutation_generation: 0,
  lifecycle_state: "active",
  retrieval_enabled: true,
  active_delete_operation_id: null,
};

const SECOND_MODERN_DOCUMENT: DocumentCatalogItem = {
  ...MODERN_DOCUMENT,
  id: "doc-2",
  name: "薪酬制度.pdf",
  doc_type: "pdf",
  logical_folder_path: "制度/薪酬",
  updated_at: "2026-08-26T09:00:00Z",
  chunk_count: 5,
};

function makeModernSummary(
  summary: Partial<DocumentCatalogSummaryResponse["summary"]> = {},
): DocumentCatalogSummaryResponse {
  return {
    dataset_id: "default",
    summary: {
      total: 1,
      completed: 1,
      processing: 0,
      failed: 0,
      chunks: 12,
      parser_observed: 1,
      parser_coverage: 100,
      ...summary,
    },
    facets: {
      statuses: {
        all: 1,
        waiting: 0,
        parsing: 0,
        splitting: 0,
        indexing: 0,
        processing: 0,
        completed: 1,
        error: 0,
      },
      types: [{ value: "markdown", count: 1 }],
      engines: [{ value: "vision", count: 1 }],
      chunking_reasons: [{ value: "complex_or_structured", count: 1 }],
      folders: [{ path: "人力资源", documents: 1, chunks: 12 }],
      tags: [{ name: "制度", documents: 1, chunks: 12 }],
    },
    recent: [MODERN_DOCUMENT],
    generated_at: "2026-08-26T10:00:00Z",
    tag_facets_complete: true,
    tag_facets_scan_limit: 1000,
    tag_facets_scanned: 1,
    tag_facets_truncated: false,
  };
}

function TestKnowledgeWorkspaceProvider({
  children,
  ...props
}: ComponentProps<typeof KnowledgeWorkspaceProvider>) {
  return (
    <KnowledgeWorkspaceProvider tenantId="tenant-1" datasetId="default" {...props}>
      {children}
    </KnowledgeWorkspaceProvider>
  );
}

function renderModernDocuments(props: Partial<ComponentProps<typeof DocumentsPage>> = {}) {
  localStorage.setItem("rag4c.knowledge_actor_token", "signed-test-token");
  return render(
    <TestKnowledgeWorkspaceProvider preferSummaryApi tenantId="tenant-1" datasetId="default">
      <DocumentsPage {...props} />
    </TestKnowledgeWorkspaceProvider>,
  );
}

const DOCUMENT_DETAIL: DocumentDetail = {
  ...DOCUMENT,
  tenant_id: "tenant-1",
  dataset_id: "default",
  status_detail: "",
  file_path: "/员工手册.md",
  file_hash: "hash",
  updated_at: null,
};

const CHUNK: DocumentChunkItem = {
  chunk_id: "chunk-1",
  doc_id: DOCUMENT.id,
  text: "old chunk body",
  text_hash: "hash",
  content_revision: 4,
  seq: 0,
  context: "",
  char_count: 14,
  metadata: {},
};

function WorkspacePages({ documents }: { documents: boolean }) {
  return (
    <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
      <KnowledgeOverviewPage />
      {documents ? <DocumentsPage preferModernCatalog={false} /> : null}
    </TestKnowledgeWorkspaceProvider>
  );
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("DocumentsPage shared knowledge workspace", () => {
  beforeEach(() => {
    connection.online = true;
    localStorage.setItem("rag4c.knowledge_actor_token", "signed-test-token");
    window.history.replaceState(null, "", "/documents");
    Object.values(api).forEach((mock) => {
      if (typeof mock === "function" && "mockReset" in mock) mock.mockReset();
    });
    Object.values(feedback).forEach((mock) => mock.mockReset());
    workspaceScopeApi.fetchWorkspaceDatasets.mockReset().mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    workspaceScopeApi.fetchEnterpriseKnowledgeBaseDetail
      .mockReset()
      .mockImplementation((_scope: unknown, requestedDatasetId: string) =>
        Promise.resolve({
          knowledge_base: {
            id: requestedDatasetId,
            owning_workspace: { id: "workspace-test", status: "active" },
          },
        }),
      );
    let deleteKeySequence = 0;
    api.createDocumentDeleteIdempotencyKey.mockImplementation(
      (prefix: string) => prefix + "-key-" + String(++deleteKeySequence),
    );
    api.shouldRetainDocumentDeleteIdempotencyKey.mockImplementation(
      (error: unknown) =>
        !(error instanceof api.ApiError && error.kind === "http" && (error.status ?? 0) < 500),
    );
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });
    api.fetchDocument.mockResolvedValue(DOCUMENT_DETAIL);
    api.fetchDocumentChunks.mockResolvedValue({
      items: [CHUNK],
      total: 1,
      offset: 0,
      limit: 200,
    });
    api.fetchParseChunkPage.mockResolvedValue({
      authority_mode: "active",
      items: [CHUNK],
      total: 1,
      offset: 0,
      limit: 100,
      known_parent_ids: [],
      missing_parent_ids: [],
    });
    api.fetchParseChunkDetail.mockResolvedValue(CHUNK);
    // 原文查看默认按"这台服务没开这个能力"拒绝：这些用例考的是切片编辑与导航，
    // 不该因为读不到原文而失败，也不该走进需要 URL.createObjectURL 的成功分支。
    api.fetchDocumentSource.mockRejectedValue(
      new api.SourcePreviewRefusedError(
        "这台服务没有开放原文查看",
        "source_preview_disabled",
        409,
      ),
    );
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: false,
        media: "",
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("uses the history URL q parameter as the initial document search", async () => {
    window.history.replaceState(
      null,
      "",
      "/documents?q=%E8%96%AA%E9%85%AC&status=completed&document=doc-2",
    );
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    const search = (await screen.findByPlaceholderText("搜索文件名")) as HTMLInputElement;
    expect(search.value).toBe("薪酬");
    expect(screen.queryByText(DOCUMENT.name)).toBeNull();
    expect((await screen.findAllByText(SECOND_DOCUMENT.name)).length).toBeGreaterThan(0);
    const params = new URLSearchParams(window.location.search);
    expect(params.get("status")).toBe("completed");
    expect(params.get("document")).toBe("doc-2");
  });

  it("synchronizes q after popstate navigation while the kept-alive page stays mounted", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    const search = (await screen.findByPlaceholderText("搜索文件名")) as HTMLInputElement;
    fireEvent.change(search, { target: { value: "local draft" } });

    window.history.pushState(
      null,
      "",
      "/documents?q=%E8%96%AA%E9%85%AC&status=completed&tag=%E5%88%B6%E5%BA%A6",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(search.value).toBe("薪酬"));
    expect(screen.queryByText(DOCUMENT.name)).toBeNull();
    expect(screen.getAllByText(SECOND_DOCUMENT.name).length).toBeGreaterThan(0);
    const params = new URLSearchParams(window.location.search);
    expect(params.get("status")).toBe("completed");
    expect(params.get("tag")).toBe("制度");
  });

  it("synchronizes q from hash navigation while the kept-alive page stays mounted", async () => {
    window.history.replaceState(null, "", "/#/documents?q=%E5%91%98%E5%B7%A5&status=completed");
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    const search = (await screen.findByPlaceholderText("搜索文件名")) as HTMLInputElement;
    expect(search.value).toBe("员工");

    window.location.hash =
      "/documents?q=%E8%96%AA%E9%85%AC&status=completed&tag=%E5%88%B6%E5%BA%A6";
    window.dispatchEvent(new Event("hashchange"));

    await waitFor(() => expect(search.value).toBe("薪酬"));
    const hashParams = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
    expect(hashParams.get("status")).toBe("completed");
    expect(hashParams.get("tag")).toBe("制度");
  });

  it("writes manual search changes to q without dropping deep-link parameters or looping", async () => {
    window.history.replaceState(
      null,
      "",
      "/documents?q=%E5%91%98%E5%B7%A5&status=completed&document=doc-1",
    );
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    const replaceState = vi.spyOn(window.history, "replaceState");
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    const search = (await screen.findByPlaceholderText("搜索文件名")) as HTMLInputElement;
    expect(search.value).toBe("员工");
    replaceState.mockClear();

    fireEvent.change(search, { target: { value: "薪酬" } });

    await waitFor(() => expect(new URLSearchParams(window.location.search).get("q")).toBe("薪酬"));
    let params = new URLSearchParams(window.location.search);
    expect(params.get("status")).toBe("completed");
    expect(params.get("document")).toBe("doc-1");
    expect(replaceState).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect(search.value).toBe("薪酬"));
    expect(replaceState).toHaveBeenCalledTimes(1);

    fireEvent.change(search, { target: { value: "" } });
    await waitFor(() => expect(new URLSearchParams(window.location.search).has("q")).toBe(false));
    params = new URLSearchParams(window.location.search);
    expect(params.get("status")).toBe("completed");
    expect(params.get("document")).toBe("doc-1");
    expect(replaceState).toHaveBeenCalledTimes(2);
  });
  it("uses the authenticated server catalog and sends URL filters to the backend", async () => {
    window.history.replaceState(
      null,
      "",
      "/documents?q=%E8%96%AA%E9%85%AC&status=completed&type=pdf&engine=vision&category=%E5%88%B6%E5%BA%A6%2F%E8%96%AA%E9%85%AC&tag=%E5%88%B6%E5%BA%A6&chunking_reason_code=table_doc_type",
    );
    api.fetchDocumentPage.mockResolvedValue({
      items: [SECOND_MODERN_DOCUMENT],
      total: 1,
      offset: 0,
      limit: 10,
      next_cursor: null,
    });
    api.fetchDocumentSummary.mockResolvedValue(
      makeModernSummary({ total: 2, completed: 2, chunks: 17 }),
    );

    renderModernDocuments();

    expect(await screen.findByText(SECOND_MODERN_DOCUMENT.name)).toBeTruthy();
    expect(api.fetchDocumentPage).toHaveBeenCalledWith(
      "default",
      expect.objectContaining({
        offset: 0,
        limit: 10,
        q: "薪酬",
        status: "completed",
        doc_type: "pdf",
        engine: "vision",
        folder: "制度/薪酬",
        folder_mode: "exact",
        tag: "制度",
        lifecycle_state: "all",
        chunking_reason_code: "table_doc_type",
        sort: "updated_at_desc",
      }),
      expect.objectContaining({
        tenantId: "tenant-1",
        actorToken: "signed-test-token",
        signal: expect.any(AbortSignal),
      }),
    );
    expect(api.fetchDocumentSummary).toHaveBeenCalledWith(
      "default",
      expect.objectContaining({
        tenantId: "tenant-1",
        actorToken: "signed-test-token",
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("filters by chunking reason from the facet list and keeps the choice in the URL", async () => {
    window.history.replaceState(null, "", "/documents");
    api.fetchDocumentPage.mockResolvedValue({
      items: [MODERN_DOCUMENT],
      total: 1,
      offset: 0,
      limit: 10,
      next_cursor: null,
    });
    api.fetchDocumentSummary.mockResolvedValue(makeModernSummary());

    renderModernDocuments();
    await screen.findByText(MODERN_DOCUMENT.name);
    // 侧栏显示的是中文判定名，不是裸的原因码：原因码是给 SQL 用的，不是给人读的。
    const facet = screen.getByRole("button", { name: /长文或含版面结构/ });
    api.fetchDocumentPage.mockClear();

    fireEvent.click(facet);

    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("chunking_reason_code")).toBe(
        "complex_or_structured",
      ),
    );
    expect(api.fetchDocumentPage).toHaveBeenLastCalledWith(
      "default",
      expect.objectContaining({ chunking_reason_code: "complex_or_structured" }),
      expect.objectContaining({ tenantId: "tenant-1" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /全部判定/ }));

    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).has("chunking_reason_code")).toBe(false),
    );
    expect(api.fetchDocumentPage).toHaveBeenLastCalledWith(
      "default",
      expect.objectContaining({ chunking_reason_code: "all" }),
      expect.objectContaining({ tenantId: "tenant-1" }),
    );
  });

  it("uses an opaque cursor stack for next and previous pages while preserving cross-page selection", async () => {
    const pageOne = {
      items: [MODERN_DOCUMENT],
      total: 11,
      offset: 0,
      limit: 10,
      next_cursor: "cursor-page-2",
    };
    const pageTwo = {
      items: [SECOND_MODERN_DOCUMENT],
      total: 11,
      offset: 0,
      limit: 10,
      next_cursor: null,
    };
    api.fetchDocumentPage.mockImplementation((_datasetId: string, query: { cursor?: string }) =>
      Promise.resolve(query.cursor ? pageTwo : pageOne),
    );
    api.fetchDocumentSummary.mockResolvedValue(makeModernSummary({ total: 11 }));

    renderModernDocuments();
    await screen.findByText(MODERN_DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await screen.findByText(SECOND_MODERN_DOCUMENT.name);
    expect(api.fetchDocumentPage).toHaveBeenLastCalledWith(
      "default",
      expect.objectContaining({ offset: 0, limit: 10, cursor: "cursor-page-2" }),
      expect.objectContaining({ tenantId: "tenant-1", actorToken: "signed-test-token" }),
    );

    fireEvent.click(screen.getByRole("checkbox", { name: `选择 ${SECOND_MODERN_DOCUMENT.name}` }));
    expect(screen.getByText("已选择 1 篇")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    await screen.findByText(MODERN_DOCUMENT.name);
    expect(screen.getByText("已选择 1 篇")).toBeTruthy();
  });

  it("polls the modern page and summary only while summary.processing is positive", async () => {
    vi.useFakeTimers();
    let pageCalls = 0;
    let summaryCalls = 0;
    api.fetchDocumentPage.mockImplementation(() => {
      pageCalls += 1;
      return Promise.resolve({
        items: [{ ...MODERN_DOCUMENT, status: pageCalls === 1 ? "parsing" : "completed" }],
        total: 1,
        offset: 0,
        limit: 10,
        next_cursor: null,
      });
    });
    api.fetchDocumentSummary.mockImplementation(() => {
      summaryCalls += 1;
      return Promise.resolve(
        makeModernSummary({
          processing: summaryCalls === 1 ? 1 : 0,
          completed: summaryCalls === 1 ? 0 : 1,
        }),
      );
    });

    renderModernDocuments();
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(pageCalls).toBe(1);
    expect(summaryCalls).toBe(1);

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(pageCalls).toBe(2);
    expect(summaryCalls).toBe(2);

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });
    expect(pageCalls).toBe(2);
    expect(summaryCalls).toBe(2);
  });

  it("moves back to the last valid page after a modern single delete removes its only row", async () => {
    const pageOne = {
      items: [MODERN_DOCUMENT],
      total: 10,
      offset: 0,
      limit: 10,
      next_cursor: "cursor-page-2",
    };
    const pageTwo = {
      items: [SECOND_MODERN_DOCUMENT],
      total: 11,
      offset: 0,
      limit: 10,
      next_cursor: null,
    };
    let pageTwoVisible = true;
    api.fetchDocumentPage.mockImplementation((_datasetId: string, query: { cursor?: string }) => {
      if (query.cursor && pageTwoVisible) return Promise.resolve(pageTwo);
      return Promise.resolve(pageOne);
    });
    api.fetchDocumentSummary.mockResolvedValue(makeModernSummary({ total: 11 }));
    api.requestDocumentDelete.mockResolvedValue({
      operation_id: "delete-op-modern",
      batch_operation_id: "delete-batch-modern",
      document_id: SECOND_MODERN_DOCUMENT.id,
      requested_document_id: SECOND_MODERN_DOCUMENT.id,
      status: "completed",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: false,
      stores: { required: 2, completed: 2, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });

    renderModernDocuments();
    await screen.findByText(MODERN_DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText(SECOND_MODERN_DOCUMENT.name);
    pageTwoVisible = false;

    fireEvent.click(screen.getByRole("button", { name: `删除 ${SECOND_MODERN_DOCUMENT.name}` }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(api.requestDocumentDelete).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(MODERN_DOCUMENT.name)).toBeTruthy());
    expect(screen.queryByText(SECOND_MODERN_DOCUMENT.name)).toBeNull();
    expect((screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("guards dirty document A to document B navigation and restores A when cancelled", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));
    await screen.findByRole("heading", { name: DOCUMENT.name });
    fireEvent.change(screen.getByLabelText("切片正文"), { target: { value: "dirty A" } });
    fireEvent.change(screen.getByPlaceholderText("例如：修正 OCR 错字或恢复遗漏条款"), {
      target: { value: "reason" },
    });
    window.history.pushState({}, "", "/documents/parse?doc=doc-2");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(window.location.search).toBe("?doc=doc-1"));
    expect((screen.getByLabelText("切片正文") as HTMLTextAreaElement).value).toBe("dirty A");
  });

  it("marks hash openings as Documents-originated so close uses browser back", async () => {
    const back = vi.spyOn(window.history, "back").mockImplementation(() => undefined);
    window.history.replaceState(null, "", "/#/documents");
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));
    expect(window.location.hash).toBe("#/documents/parse?doc=doc-1");
    expect(window.history.state?.rag4cParseWorkspace).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: "返回文档" }));
    expect(back).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole("heading", { name: "文档管理" })).toBeTruthy());
  });

  it("leaves /parse-intervention to the workbench page instead of taking it over in place", async () => {
    // App 用 keep-alive 保留文档页（隐藏 ≠ 卸载）：别名若也解析新路径，这里会长出第二个工作区
    window.history.replaceState(null, "", "/parse-intervention?doc=doc-1&chunk=chunk-1");
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByRole("heading", { name: "文档管理" });
    expect(screen.queryByRole("region", { name: "切片编辑器" })).toBeNull();
    expect(screen.queryByLabelText("Knowledge Lifeline")).toBeNull();
  });

  it("closes a direct hash deep link with replace semantics and does not add history", async () => {
    window.history.replaceState(null, "", "/#/documents/parse?doc=doc-1");
    const initialLength = window.history.length;
    const replace = vi.spyOn(window.history, "replaceState");
    const back = vi.spyOn(window.history, "back");
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByRole("heading", { name: DOCUMENT.name });
    fireEvent.click(screen.getByRole("button", { name: "返回文档" }));
    expect(back).not.toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith(null, "", "#/documents");
    expect(window.history.length).toBe(initialLength);
    expect(screen.getByRole("heading", { name: "文档管理" })).toBeTruthy();
  });

  it("uses the current workspace scope when the live document list omits tenant and dataset", async () => {
    window.history.replaceState(null, "", "/documents/parse?doc=doc-1");
    api.fetchDocuments.mockResolvedValue({
      documents: [{ ...DOCUMENT, tenant_id: undefined, dataset_id: undefined }],
    });
    api.fetchDocument.mockResolvedValue({
      ...DOCUMENT_DETAIL,
      tenant_id: "default",
      dataset_id: "default",
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    expect(await screen.findByRole("heading", { name: DOCUMENT.name })).toBeTruthy();
    expect(api.fetchParseChunkPage).toHaveBeenCalledWith(
      expect.objectContaining({ docId: DOCUMENT.id, actorToken: "signed-test-token" }),
      expect.objectContaining({ offset: 0, includeDisabled: true }),
      expect.any(AbortSignal),
    );
  });

  it("opens a dedicated parse route and preserves Documents search context on return", async () => {
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    const search = screen.getByPlaceholderText("搜索文件名") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "员工" } });

    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));

    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe("/documents/parse?doc=doc-1"),
    );
    expect(await screen.findByRole("heading", { name: DOCUMENT.name })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "文档管理" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "返回文档" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "文档管理" })).toBeTruthy());
    expect((screen.getByPlaceholderText("搜索文件名") as HTMLInputElement).value).toBe("员工");
  });

  it("confirms dirty workspace return and keeps focusable editor state when cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));
    await screen.findByRole("heading", { name: DOCUMENT.name });
    fireEvent.change(screen.getByLabelText("切片正文"), { target: { value: "local dirty" } });
    fireEvent.change(screen.getByPlaceholderText("例如：修正 OCR 错字或恢复遗漏条款"), {
      target: { value: "reason" },
    });

    fireEvent.click(screen.getByRole("button", { name: "返回文档" }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: DOCUMENT.name })).toBeTruthy();

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "返回文档" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "文档管理" })).toBeTruthy());
  });
  it("reuses the kept-alive snapshot when navigating from overview to documents", async () => {
    const view = render(<WorkspacePages documents={false} />);

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0));
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);

    view.rerender(<WorkspacePages documents />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "文档管理" })).toBeTruthy());
    expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(1);
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);
  });

  it("renders the production fetch error with retry instead of demo documents", async () => {
    api.fetchDocuments.mockRejectedValueOnce(new Error("catalog unavailable"));

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("知识库数据加载失败")).toBeTruthy();
    });
    expect(screen.getByText("catalog unavailable")).toBeTruthy();
    expect(screen.queryByText("员工手册-2026版.pdf")).toBeNull();
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);

    api.fetchDocuments.mockResolvedValueOnce({ documents: [DOCUMENT] });
    screen.getByRole("button", { name: "重试加载知识库数据" }).click();

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0));
    expect(api.fetchDocuments).toHaveBeenCalledTimes(2);
  });

  it("refreshes the shared snapshot after a successful reindex", async () => {
    api.reindexDocument.mockResolvedValue({ document_id: DOCUMENT.id, status: "queued" });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "重新索引" }));

    await waitFor(() => expect(api.reindexDocument).toHaveBeenCalledWith(DOCUMENT.id, false));
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
  });

  it("refreshes the shared snapshot after a successful document import", async () => {
    api.ingestDocument.mockResolvedValue({ document_id: "doc-new", status: "queued" });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "添加文件" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText("例如：D:/data/员工手册.pdf"), {
      target: { value: "D:/data/new.pdf" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步：解析设置" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "开始导入" }));

    await waitFor(() =>
      expect(api.ingestDocument).toHaveBeenCalledWith({
        file_path: "D:/data/new.pdf",
        dataset_id: "default",
      }),
    );
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
  });

  it("renders the document table with real TDesign primitives and millisecond parsing profile", async () => {
    api.fetchDocuments.mockResolvedValue({
      documents: [
        {
          ...DOCUMENT,
          parser_meta: {
            engine: "vision",
            chunking_mode: "parent_child",
            total_ms: 1.7,
            stage_ms: { parse: 0.4, split: 1.3 },
          },
        },
      ],
    });

    const { container } = render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await screen.findByText("员工手册.md");
    expect(container.querySelector(".t-table")).not.toBeNull();
    expect(container.querySelector(".t-input")).not.toBeNull();
    expect(screen.getAllByText("视觉解析（OCR）").length).toBeGreaterThan(0);
    expect(screen.getAllByText("按章节结构").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1\.7ms/).length).toBeGreaterThan(0);
  });

  it("does not allow queued or running documents to enter destructive selection", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, PROCESSING_DOCUMENT] });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    expect((await screen.findAllByText(PROCESSING_DOCUMENT.name)).length).toBeGreaterThan(0);
    const completed = screen.getByRole("checkbox", { name: `选择 ${DOCUMENT.name}` });
    const processing = screen.getByRole("checkbox", { name: `选择 ${PROCESSING_DOCUMENT.name}` });
    expect((completed as HTMLInputElement).disabled).toBe(false);
    expect((processing as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(processing);
    expect(screen.queryByText("已选择 1 篇")).toBeNull();
  });

  it("organizes document import into source selection and parsing setup stages", async () => {
    api.ingestDocument.mockResolvedValue({ document_id: "doc-new", status: "queued" });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    expect((await screen.findAllByText(DOCUMENT.name)).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "添加文件" }));
    expect(document.querySelector(".t-dialog")).not.toBeNull();
    expect(screen.getByText("1. 选择来源")).toBeTruthy();
    expect(screen.getByText("2. 设置解析与切分")).toBeTruthy();
    const disabledNext = screen.getByRole("button", { name: "下一步：解析设置" });
    expect(disabledNext.tagName).toBe("BUTTON");
    expect((disabledNext as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("例如：D:/data/员工手册.pdf"), {
      target: { value: "D:/data/new.pdf" },
    });
    fireEvent.click(screen.getByRole("button", { name: "下一步：解析设置" }));
    expect(screen.getByRole("heading", { name: "解析与切分计划" })).toBeTruthy();
    expect(screen.getByText("自动识别文本层与 OCR 需求")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));

    await waitFor(() =>
      expect(api.ingestDocument).toHaveBeenCalledWith({
        file_path: "D:/data/new.pdf",
        dataset_id: "default",
      }),
    );
  });

  it("refreshes the shared snapshot after successful document settings", async () => {
    api.updateDocumentSettings.mockResolvedValue({
      document_id: DOCUMENT.id,
      logical_folder_path: "制度/新版",
      tags: ["制度", "新版"],
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByPlaceholderText("例如：制度/人力；留空表示未分类"), {
      target: { value: "制度/新版" },
    });
    fireEvent.change(
      within(dialog).getByPlaceholderText("多个标签使用逗号分隔，例如：员工, 制度, 2026"),
      { target: { value: "制度, 新版" } },
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(api.updateDocumentSettings).toHaveBeenCalledWith(DOCUMENT.id, {
        logical_folder_path: "制度/新版",
        tags: ["制度", "新版"],
      }),
    );
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
  });

  it("updates the shared overview chunk count after deleting a chunk", async () => {
    api.fetchDocuments
      .mockReset()
      .mockResolvedValueOnce({ documents: [DOCUMENT] })
      .mockResolvedValueOnce({
        documents: [{ ...DOCUMENT, chunk_count: DOCUMENT.chunk_count - 1 }],
      });
    api.tombstoneParseChunk.mockResolvedValue({
      document_id: DOCUMENT.id,
      chunk_id: CHUNK.chunk_id,
      remaining_chunks: DOCUMENT.chunk_count - 1,
      removed_chunks: 0,
      removed_relations: 0,
      removed_entities: 0,
      content_revision: CHUNK.content_revision + 1,
      authority_mode: "active",
      projection_pending: true,
      operation_ids: ["op-delete-1"],
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <KnowledgeOverviewPage />
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(1));
    const overviewStat = screen.getByText("目录片段").closest(".stat-card");
    expect(overviewStat).not.toBeNull();
    expect(within(overviewStat as HTMLElement).getByText("12")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));
    await waitFor(() => expect(screen.getAllByText("old chunk body").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "删除切片" }));
    fireEvent.click(await screen.findByRole("button", { name: "写入墓碑" }));

    await waitFor(() =>
      expect(api.tombstoneParseChunk).toHaveBeenCalledWith(
        expect.objectContaining({ docId: DOCUMENT.id, actorToken: "signed-test-token" }),
        CHUNK.chunk_id,
        CHUNK.content_revision,
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
    await waitFor(() => {
      const updatedOverviewStat = screen.getByText("目录片段").closest(".stat-card");
      expect(updatedOverviewStat).not.toBeNull();
      expect(within(updatedOverviewStat as HTMLElement).getByText("11")).toBeTruthy();
    });
  });

  it("invalidates the shared document snapshot after editing a chunk", async () => {
    api.patchParseChunk.mockResolvedValue({
      ...CHUNK,
      text: "edited chunk body",
      text_hash: "edited-hash",
      content_revision: CHUNK.content_revision + 1,
      char_count: 17,
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <KnowledgeOverviewPage />
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(1));
    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${DOCUMENT.name}` }));
    await waitFor(() => expect(screen.getAllByText("old chunk body").length).toBeGreaterThan(0));
    fireEvent.change(screen.getByLabelText("切片正文"), { target: { value: "edited chunk body" } });
    fireEvent.change(screen.getByPlaceholderText("例如：修正 OCR 错字或恢复遗漏条款"), {
      target: { value: "修正 OCR" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    await waitFor(() =>
      expect(api.patchParseChunk).toHaveBeenCalledWith(
        expect.objectContaining({ docId: DOCUMENT.id, actorToken: "signed-test-token" }),
        CHUNK.chunk_id,
        "edited chunk body",
        CHUNK.content_revision,
        "修正 OCR",
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
  });

  it("submits generation-aware delete and only reports deletion after polling completed", async () => {
    api.requestDocumentDelete.mockResolvedValue({
      operation_id: "delete-op-1",
      batch_operation_id: "delete-batch-1",
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "queued",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: true,
      stores: { required: 2, completed: 0, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });
    api.fetchDocumentDeleteOperation.mockResolvedValue({
      operation_id: "delete-op-1",
      batch_operation_id: "delete-batch-1",
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "completed",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: false,
      stores: { required: 2, completed: 2, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(api.requestDocumentDelete).toHaveBeenCalledTimes(1));
    expect(api.requestDocumentDelete).toHaveBeenCalledWith(
      "default",
      DOCUMENT.id,
      { expected_generation: 0 },
      expect.objectContaining({ tenantId: "tenant-1", idempotencyKey: expect.any(String) }),
    );
    await waitFor(() =>
      expect(feedback.info).toHaveBeenCalledWith("删除请求已接受，正在清理向量与图谱…"),
    );
    await waitFor(() => expect(api.fetchDocumentDeleteOperation).toHaveBeenCalled());
    await waitFor(() => expect(feedback.success).toHaveBeenCalledWith("已删除 " + DOCUMENT.name));
  });

  it("fails closed when the document generation is missing", async () => {
    api.fetchDocuments.mockResolvedValue({
      documents: [{ ...DOCUMENT, mutation_generation: undefined }],
    });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
    await waitFor(() =>
      expect(feedback.error).toHaveBeenCalledWith("文档缺少删除 Generation，请刷新后重试"),
    );
    expect(api.requestDocumentDelete).not.toHaveBeenCalled();
  });

  it("reuses the same idempotency key when the delete response is lost", async () => {
    api.requestDocumentDelete.mockRejectedValue(new Error("network lost after commit"));
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
      fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
      await waitFor(() => expect(api.requestDocumentDelete).toHaveBeenCalledTimes(attempt + 1));
    }
    const firstOptions = api.requestDocumentDelete.mock.calls[0][3];
    const secondOptions = api.requestDocumentDelete.mock.calls[1][3];
    expect(firstOptions.idempotencyKey).toBeTruthy();
    expect(secondOptions.idempotencyKey).toBe(firstOptions.idempotencyKey);
  });

  it("shows delete lifecycle progress and failure after refresh", async () => {
    api.fetchDocuments.mockResolvedValue({
      documents: [
        {
          ...DOCUMENT,
          lifecycle_state: "delete_requested",
          active_delete_operation_id: "op-pending",
        },
        {
          ...SECOND_DOCUMENT,
          lifecycle_state: "delete_failed",
          active_delete_operation_id: "op-failed",
        },
      ],
    });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    expect((await screen.findAllByText("删除清理中")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("删除失败").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/请联系管理员/).length).toBeGreaterThan(0);
  });

  it("keeps queued batch items pending and counts only completed items after polling", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    const queuedItem = (id: string, operationId: string) => ({
      operation_id: operationId,
      batch_operation_id: "batch-1",
      document_id: id,
      requested_document_id: id,
      status: "queued",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: true,
      stores: { required: 2, completed: 0, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });
    api.requestDocumentBatchDelete.mockResolvedValue({
      batch_operation_id: "batch-1",
      status: "running",
      requested: 2,
      accepted: 2,
      completed: 0,
      failed: 0,
      rejected: 0,
      reason: "",
      items: [queuedItem(DOCUMENT.id, "op-1"), queuedItem(SECOND_DOCUMENT.id, "op-2")],
      timestamps: { created_at: null, updated_at: null, finished_at: null },
    });
    api.fetchDocumentDeleteBatch.mockResolvedValue({
      batch_operation_id: "batch-1",
      status: "partially_failed",
      requested: 2,
      accepted: 2,
      completed: 1,
      failed: 1,
      rejected: 0,
      reason: "",
      items: [
        {
          ...queuedItem(DOCUMENT.id, "op-1"),
          status: "completed",
          projection_pending: false,
          stores: { required: 2, completed: 2, failed: 0 },
        },
        {
          ...queuedItem(SECOND_DOCUMENT.id, "op-2"),
          status: "failed",
          code: "graph_unavailable",
          message: "graph unavailable",
          projection_pending: false,
          stores: { required: 2, completed: 1, failed: 1 },
        },
      ],
      timestamps: { created_at: null, updated_at: null, finished_at: null },
    });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(SECOND_DOCUMENT.name);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + SECOND_DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(api.requestDocumentBatchDelete).toHaveBeenCalledTimes(1));
    expect(feedback.success).not.toHaveBeenCalledWith("已删除 2 篇文档");
    await waitFor(() => expect(api.fetchDocumentDeleteBatch).toHaveBeenCalled());
    await waitFor(() => expect(feedback.success).toHaveBeenCalledWith("已删除 1 篇文档"));
    expect(screen.getAllByText(/graph unavailable/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/请联系管理员/).length).toBeGreaterThan(0);
  });

  it("continues persistent polling beyond three projecting responses until completed", async () => {
    api.requestDocumentDelete.mockResolvedValue({
      operation_id: "op-long",
      batch_operation_id: null,
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "queued",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: true,
      stores: { required: 2, completed: 0, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });
    const projecting = {
      operation_id: "op-long",
      batch_operation_id: null,
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "projecting",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: true,
      stores: { required: 2, completed: 0, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    };
    api.fetchDocumentDeleteOperation
      .mockResolvedValueOnce(projecting)
      .mockResolvedValueOnce(projecting)
      .mockResolvedValueOnce(projecting)
      .mockResolvedValueOnce(projecting)
      .mockResolvedValueOnce({
        ...projecting,
        status: "completed",
        projection_pending: false,
        stores: { required: 2, completed: 2, failed: 0 },
      });

    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await act(async () => Promise.resolve());

    for (const delay of [1000, 2000, 3000, 5000, 1000]) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(delay);
      });
    }
    expect(api.fetchDocumentDeleteOperation).toHaveBeenCalledTimes(5);
    expect(feedback.success).toHaveBeenCalledWith("已删除 " + DOCUMENT.name);
  });

  it("recovers polling from refreshed active_delete_operation_id", async () => {
    api.fetchDocuments.mockResolvedValue({
      documents: [
        { ...DOCUMENT, lifecycle_state: "deleting", active_delete_operation_id: "op-recovered" },
      ],
    });
    api.fetchDocumentDeleteOperation.mockResolvedValue({
      operation_id: "op-recovered",
      batch_operation_id: null,
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "completed",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: false,
      stores: { required: 2, completed: 2, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });
    vi.useFakeTimers();
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await act(async () => Promise.resolve());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(api.fetchDocumentDeleteOperation).toHaveBeenCalledWith(
      "default",
      "op-recovered",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(feedback.success).toHaveBeenCalledWith("已删除 " + DOCUMENT.name);
  });

  it("aborts persistent polling on unmount", async () => {
    api.fetchDocuments.mockResolvedValue({
      documents: [
        { ...DOCUMENT, lifecycle_state: "deleting", active_delete_operation_id: "op-abort" },
      ],
    });
    api.fetchDocumentDeleteOperation.mockResolvedValue({
      operation_id: "op-abort",
      batch_operation_id: null,
      document_id: DOCUMENT.id,
      requested_document_id: DOCUMENT.id,
      status: "projecting",
      code: null,
      message: null,
      expected_generation: 0,
      delete_generation: 1,
      retrieval_enabled: false,
      projection_pending: true,
      stores: { required: 2, completed: 0, failed: 0 },
      reason: "",
      timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
    });
    vi.useFakeTimers();
    const view = render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await act(async () => Promise.resolve());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(api.fetchDocumentDeleteOperation).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(api.fetchDocumentDeleteOperation).toHaveBeenCalledTimes(1);
  });

  it("clears a deterministic generation-conflict key and uses a new key for the new generation", async () => {
    api.fetchDocuments
      .mockResolvedValueOnce({ documents: [DOCUMENT] })
      .mockResolvedValue({ documents: [{ ...DOCUMENT, mutation_generation: 1 }] });
    api.requestDocumentDelete
      .mockRejectedValueOnce(new api.ApiError("generation conflict", "http", 409))
      .mockResolvedValueOnce({
        operation_id: "op-generation-1",
        batch_operation_id: null,
        document_id: DOCUMENT.id,
        requested_document_id: DOCUMENT.id,
        status: "completed",
        code: null,
        message: null,
        expected_generation: 1,
        delete_generation: 2,
        retrieval_enabled: false,
        projection_pending: false,
        stores: { required: 2, completed: 2, failed: 0 },
        reason: "",
        timestamps: { started_at: null, finalized_at: null, created_at: null, updated_at: null },
      });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(DOCUMENT.name);
    fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "删除 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.requestDocumentDelete).toHaveBeenCalledTimes(2));
    expect(api.requestDocumentDelete.mock.calls[0][3].idempotencyKey).not.toBe(
      api.requestDocumentDelete.mock.calls[1][3].idempotencyKey,
    );
    expect(api.requestDocumentDelete.mock.calls[1][2]).toEqual({ expected_generation: 1 });
  });

  it("binds batch idempotency identity to ordered document generations", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT, SECOND_DOCUMENT] });
    api.requestDocumentBatchDelete.mockRejectedValue(new Error("network lost"));
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    await screen.findByText(SECOND_DOCUMENT.name);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + SECOND_DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.requestDocumentBatchDelete).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 " + DOCUMENT.name }));
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.requestDocumentBatchDelete).toHaveBeenCalledTimes(2));
    expect(api.requestDocumentBatchDelete.mock.calls[0][2].idempotencyKey).not.toBe(
      api.requestDocumentBatchDelete.mock.calls[1][2].idempotencyKey,
    );
  });

  it("suppresses its standalone PageTopbar in embedded content-only mode", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });
    render(
      <TestKnowledgeWorkspaceProvider preferSummaryApi={false}>
        <DocumentsPage active embedded preferModernCatalog={false} />
      </TestKnowledgeWorkspaceProvider>,
    );
    expect(screen.getByText("文档导航")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "文档管理" })).toBeNull();
  });
});
