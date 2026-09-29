// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DocumentCatalogItem,
  DocumentCatalogSummaryResponse,
  DocumentItem,
  DocumentPageResponse,
} from "../types/rag";
import KnowledgeTaxonomyPage from "./KnowledgeTaxonomyPage";
const modernApi = vi.hoisted(() => ({
  fetchDocumentPage: vi.fn(),
  fetchDocumentSummary: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    fetchDocumentPage: modernApi.fetchDocumentPage,
    fetchDocumentSummary: modernApi.fetchDocumentSummary,
  };
});

const workspace = vi.hoisted(() => ({
  documents: [] as DocumentItem[],
  status: "ready" as "loading" | "ready" | "empty" | "error" | "demo",
  error: null as Error | null,
  loading: false,
  usingMock: false,
  summary: null as DocumentCatalogSummaryResponse["summary"] | null,
  facets: null as DocumentCatalogSummaryResponse["facets"] | null,
  recent: [] as DocumentCatalogItem[],
  tagFacetsComplete: null as boolean | null,
  tagFacetsScanLimit: null as number | null,
  tagFacetsScanned: null as number | null,
  tagFacetsTruncated: false,
  summaryGeneratedAt: null as string | null,
  reload: vi.fn(async () => undefined),
}));

vi.mock("../knowledge/useKnowledgeDocuments", () => ({
  useKnowledgeDocuments: () => workspace,
}));

const DOCUMENTS: DocumentItem[] = [
  {
    id: "doc-handbook",
    name: "员工手册.md",
    status: "completed",
    progress: 1,
    chunk_count: 12,
    doc_type: "markdown",
    error_message: "",
    logical_folder_path: "制度/人力",
    tags: ["制度", "员工"],
    updated_at: "2026-08-25T08:00:00Z",
  },
  {
    id: "doc-expense",
    name: "报销规范.pdf",
    status: "error",
    progress: 0.72,
    chunk_count: 4,
    doc_type: "pdf",
    error_message: "OCR 失败",
    logical_folder_path: "制度/财务",
    tags: ["制度", "财务"],
    updated_at: "2026-08-24T07:00:00Z",
  },
  {
    id: "doc-release",
    name: "产品发布清单.docx",
    status: "indexing",
    progress: 0.8,
    chunk_count: 8,
    doc_type: "docx",
    error_message: "",
    logical_folder_path: "产品",
    tags: ["发布"],
    updated_at: "2026-08-23T06:00:00Z",
  },
];

function makeDocuments(count: number): DocumentItem[] {
  return Array.from({ length: count }, (_, index) => {
    const ordinal = index + 1;
    return {
      id: `doc-${ordinal}`,
      name: `规模文档-${String(ordinal).padStart(2, "0")}.md`,
      status: "completed",
      progress: 1,
      chunk_count: ordinal,
      doc_type: "markdown",
      error_message: "",
      logical_folder_path: ordinal <= 25 ? "A/B/C" : "A/D",
      tags: [ordinal <= 25 ? "重点" : "常规"],
      updated_at: new Date(Date.UTC(2026, 7, 25, 0, ordinal)).toISOString(),
    };
  });
}


function enableModernScope() {
  localStorage.setItem("rag4c.knowledge_actor_token", "actor-token");
  localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-1");
  localStorage.setItem("rag4c.knowledge_dataset_id", "dataset-1");
}

function makeCatalogItem(
  overrides: Partial<DocumentCatalogItem> = {},
): DocumentCatalogItem {
  return {
    id: "server-document-1",
    tenant_id: "tenant-1",
    dataset_id: "dataset-1",
    name: "服务端员工手册.pdf",
    status: "completed",
    status_detail: "",
    progress: 1,
    chunk_count: 128,
    doc_type: "pdf",
    error_message: "",
    parser_meta: { engine: "vision" },
    updated_at: "2026-08-26T08:00:00Z",
    logical_folder_path: "制度/人力",
    tags: ["服务端标签"],
    source_uri: null,
    source_type: "local",
    source_id: null,
    external_id: null,
    mutation_generation: 1,
    lifecycle_state: "active",
    retrieval_enabled: true,
    active_delete_operation_id: null,
    ...overrides,
  };
}

function makeModernSummary(
  overrides: Partial<DocumentCatalogSummaryResponse> = {},
): DocumentCatalogSummaryResponse {
  return {
    dataset_id: "dataset-1",
    summary: {
      total: 23,
      completed: 20,
      processing: 2,
      failed: 1,
      chunks: 2048,
      parser_observed: 22,
      parser_coverage: 96,
    },
    facets: {
      statuses: {
        all: 23,
        waiting: 0,
        parsing: 0,
        splitting: 0,
        indexing: 2,
        processing: 2,
        completed: 20,
        error: 1,
      },
      types: [{ value: "pdf", count: 23 }],
      engines: [{ value: "vision", count: 22 }],
      chunking_reasons: [{ value: "complex_or_structured", count: 22 }],
      folders: [
        { path: "制度/人力", documents: 8, chunks: 1024 },
        { path: "产品", documents: 15, chunks: 1024 },
      ],
      tags: [{ name: "服务端标签", documents: 7, chunks: 700 }],
    },
    recent: [],
    generated_at: "2026-08-26T08:00:00Z",
    tag_facets_complete: true,
    tag_facets_scan_limit: 1000,
    tag_facets_scanned: 23,
    tag_facets_truncated: false,
    ...overrides,
  };
}

function makeModernPage(
  overrides: Partial<DocumentPageResponse> = {},
): DocumentPageResponse {
  return {
    items: [makeCatalogItem()],
    total: 23,
    offset: 0,
    limit: 20,
    next_cursor: null,
    ...overrides,
  };
}
beforeEach(() => {
  localStorage.clear();
  modernApi.fetchDocumentPage.mockReset();
  modernApi.fetchDocumentSummary.mockReset();
  window.history.replaceState({}, "", "/taxonomy");
  workspace.documents = DOCUMENTS;
  workspace.status = "ready";
  workspace.error = null;
  workspace.loading = false;
  workspace.usingMock = false;
  workspace.summary = null;
  workspace.facets = null;
  workspace.recent = [];
  workspace.tagFacetsComplete = null;
  workspace.tagFacetsScanLimit = null;
  workspace.tagFacetsScanned = null;
  workspace.tagFacetsTruncated = false;
  workspace.summaryGeneratedAt = null;
  workspace.reload.mockClear();
});

afterEach(cleanup);

describe("KnowledgeTaxonomyPage enterprise governance workspace", () => {
  it("renders directory navigation, tag filters, and document results as three labelled surfaces", () => {
    render(<KnowledgeTaxonomyPage />);

    expect(screen.getByRole("heading", { name: "目录治理" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "目录导航" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "标签筛选" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "文档结果" })).toBeTruthy();

    expect(screen.getByText("3 篇文档")).toBeTruthy();
    expect(screen.getByText("3 个目录")).toBeTruthy();
    expect(screen.getByText("4 个标签")).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "目录导航" })).getByRole("button", { name: "制度，2 篇文档" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "人力，1 篇文档" })).toBeTruthy();
  });

  it("combines directory, tag, and keyword filters against the existing document projection", async () => {
    const user = userEvent.setup();
    render(<KnowledgeTaxonomyPage />);

    await user.click(within(screen.getByRole("navigation", { name: "目录导航" })).getByRole("button", { name: "制度，2 篇文档" }));
    const results = screen.getByRole("region", { name: "文档结果" });
    expect(within(results).getByText("员工手册.md")).toBeTruthy();
    expect(within(results).getByText("报销规范.pdf")).toBeTruthy();
    expect(within(results).queryByText("产品发布清单.docx")).toBeNull();

    await user.click(within(screen.getByRole("navigation", { name: "目录导航" })).getByRole("button", { name: "财务，1 篇文档" }));
    expect(within(results).queryByText("员工手册.md")).toBeNull();
    expect(within(results).getByText("报销规范.pdf")).toBeTruthy();

    const search = screen.getByRole("searchbox", { name: "搜索文档" });
    await user.clear(search);
    await user.type(search, "员工");
    expect(within(results).queryByText("报销规范.pdf")).toBeNull();
    expect(within(results).getByText("没有符合当前筛选条件的文档")).toBeTruthy();
  });

  it("keeps a synthesized intermediate parent active and includes descendant documents", async () => {
    const user = userEvent.setup();
    workspace.documents = [makeDocuments(1)[0]];
    render(<KnowledgeTaxonomyPage />);

    const directory = screen.getByRole("navigation", { name: "目录导航" });
    const middleParent = within(directory).getByRole("button", { name: "B，1 篇文档" });
    await user.click(middleParent);

    expect(middleParent.getAttribute("aria-pressed")).toBe("true");
    const results = screen.getByRole("region", { name: "文档结果" });
    expect(within(results).getByText("规模文档-01.md")).toBeTruthy();
    expect(within(results).getByText("A/B", { exact: true })).toBeTruthy();
  });

  it("supports keyboard selection and exposes the active filter state", async () => {
    const user = userEvent.setup();
    render(<KnowledgeTaxonomyPage />);

    const employeeTag = screen.getByRole("button", { name: "员工，1 篇文档" });
    employeeTag.focus();
    await user.keyboard("{Enter}");

    expect(employeeTag.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("status").textContent).toContain("1 篇文档");
    const results = screen.getByRole("region", { name: "文档结果" });
    expect(within(results).getByText("员工手册.md")).toBeTruthy();
    expect(within(results).queryByText("报销规范.pdf")).toBeNull();
  });

  it("uses navigationIntent client routing for the manage documents action", async () => {
    const user = userEvent.setup();
    const onPopState = vi.fn();
    window.addEventListener("popstate", onPopState);

    render(<KnowledgeTaxonomyPage />);
    await user.click(screen.getByRole("button", { name: "管理文档" }));

    expect(window.location.pathname).toBe("/documents");
    expect(onPopState).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", onPopState);
  });

  it("preserves the legacy synthetic popstate contract in hash deployment mode", async () => {
    window.history.replaceState({}, "", "/#/taxonomy");
    const user = userEvent.setup();
    const onPopState = vi.fn();
    window.addEventListener("popstate", onPopState);

    render(<KnowledgeTaxonomyPage />);
    await user.click(screen.getByRole("button", { name: "管理文档" }));

    expect(window.location.hash).toBe("#/documents");
    expect(onPopState).toHaveBeenCalled();
    window.removeEventListener("popstate", onPopState);
  });

  it("keeps all governance surfaces visible and routes empty-state import without a document link", async () => {
    const user = userEvent.setup();
    const onPopState = vi.fn();
    window.addEventListener("popstate", onPopState);
    workspace.documents = [];
    workspace.status = "empty";

    render(<KnowledgeTaxonomyPage />);

    expect(screen.getByRole("navigation", { name: "目录导航" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "标签筛选" })).toBeTruthy();
    const results = screen.getByRole("region", { name: "文档结果" });
    expect(within(results).getByText("知识库尚无可治理文档")).toBeTruthy();
    expect(within(results).queryByRole("link", { name: "导入文档" })).toBeNull();

    await user.click(within(results).getByRole("button", { name: "导入文档" }));
    expect(window.location.pathname).toBe("/documents");
    expect(onPopState).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", onPopState);
  });

  it("renders twenty documents per page and resets to page one when filters change", async () => {
    const user = userEvent.setup();
    workspace.documents = makeDocuments(45);
    render(<KnowledgeTaxonomyPage />);

    const results = screen.getByRole("region", { name: "文档结果" });
    expect(results.querySelectorAll(".knowledge-taxonomy-document-item")).toHaveLength(20);
    expect(within(results).getByText("规模文档-45.md")).toBeTruthy();
    expect(within(results).queryByText("规模文档-25.md")).toBeNull();

    const pagination = within(results).getByRole("navigation", { name: "文档结果分页" });
    await user.click(within(pagination).getByRole("button", { name: "第 2 页" }));
    expect(within(results).getByText("规模文档-25.md")).toBeTruthy();
    expect(within(pagination).getByRole("button", { name: "第 2 页" }).getAttribute("aria-current")).toBe("page");

    await user.click(screen.getByRole("button", { name: "重点，25 篇文档" }));
    expect(within(pagination).getByRole("button", { name: "第 1 页" }).getAttribute("aria-current")).toBe("page");
    expect(results.querySelectorAll(".knowledge-taxonomy-document-item")).toHaveLength(20);
  });

  it("uses server summary facets and server-paginated results when an actor token exists", async () => {
    enableModernScope();
    const sharedSummary = makeModernSummary();
    workspace.summary = sharedSummary.summary;
    workspace.facets = sharedSummary.facets;
    workspace.recent = sharedSummary.recent;
    workspace.tagFacetsComplete = sharedSummary.tag_facets_complete;
    workspace.tagFacetsScanLimit = sharedSummary.tag_facets_scan_limit;
    workspace.tagFacetsScanned = sharedSummary.tag_facets_scanned;
    workspace.tagFacetsTruncated = sharedSummary.tag_facets_truncated;
    workspace.summaryGeneratedAt = sharedSummary.generated_at;
    modernApi.fetchDocumentPage.mockResolvedValue(makeModernPage());
    workspace.documents = [DOCUMENTS[0]];

    render(<KnowledgeTaxonomyPage />);

    expect(await screen.findByText("服务端员工手册.pdf")).toBeTruthy();
    expect(modernApi.fetchDocumentSummary).not.toHaveBeenCalled();
    expect(modernApi.fetchDocumentPage).toHaveBeenCalledWith(
      "dataset-1",
      expect.objectContaining({
        offset: 0,
        limit: 20,
        folder_mode: "subtree",
        sort: "updated_at_desc",
      }),
      expect.objectContaining({
        tenantId: "tenant-1",
        actorToken: "actor-token",
        signal: expect.any(AbortSignal),
      }),
    );
    expect(screen.getByText("23 篇文档")).toBeTruthy();
    expect(screen.getByRole("button", { name: "服务端标签，7 篇文档" })).toBeTruthy();
    expect(screen.queryByText("员工手册.md")).toBeNull();
  });

  it("sends q, tag and subtree folder filters to the server instead of filtering the page locally", async () => {
    const user = userEvent.setup();
    enableModernScope();
    modernApi.fetchDocumentSummary.mockResolvedValue(makeModernSummary());
    modernApi.fetchDocumentPage.mockImplementation(async (_datasetId, query) =>
      makeModernPage({
        items: [
          makeCatalogItem({
            id: `server-${query.q || query.tag || query.folder || "all"}`,
            name: "服务端筛选结果.md",
          }),
        ],
      }),
    );

    render(<KnowledgeTaxonomyPage />);
    await screen.findByText("服务端筛选结果.md");

    await user.click(screen.getByRole("button", { name: "服务端标签，7 篇文档" }));
    await user.type(screen.getByRole("searchbox", { name: "搜索文档" }), "员工");

    await vi.waitFor(() => {
      expect(
        modernApi.fetchDocumentPage.mock.calls.some(([, query]) =>
          query.q === "员工" &&
          query.tag === "服务端标签" &&
          query.folder_mode === "subtree" &&
          query.offset === 0 &&
          query.limit === 20,
        ),
      ).toBe(true);
    });
  });

  it("prefers opaque next cursors and uses a previous cursor stack", async () => {
    const user = userEvent.setup();
    enableModernScope();
    modernApi.fetchDocumentSummary.mockResolvedValue(makeModernSummary());
    modernApi.fetchDocumentPage.mockImplementation(async (_datasetId, query) =>
      query.cursor === "cursor-page-2"
        ? makeModernPage({
            items: [makeCatalogItem({ id: "server-page-3", name: "第三页文档.md" })],
            next_cursor: "cursor-page-3",
          })
        : query.cursor === "cursor-page-1"
          ? makeModernPage({
              items: [makeCatalogItem({ id: "server-page-2", name: "第二页文档.md" })],
              next_cursor: "cursor-page-2",
            })
          : makeModernPage({
              items: [makeCatalogItem({ id: "server-page-1", name: "第一页文档.md" })],
              next_cursor: "cursor-page-1",
            }),
    );

    render(<KnowledgeTaxonomyPage />);
    await screen.findByText("第一页文档.md");

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("第二页文档.md");
    expect(modernApi.fetchDocumentPage).toHaveBeenLastCalledWith(
      "dataset-1",
      expect.objectContaining({ cursor: "cursor-page-1", offset: 0, limit: 20 }),
      expect.anything(),
    );

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("第三页文档.md");
    expect(modernApi.fetchDocumentPage).toHaveBeenLastCalledWith(
      "dataset-1",
      expect.objectContaining({ cursor: "cursor-page-2", offset: 0, limit: 20 }),
      expect.anything(),
    );

    await user.click(screen.getByRole("button", { name: "上一页" }));
    await screen.findByText("第二页文档.md");
    expect(modernApi.fetchDocumentPage).toHaveBeenLastCalledWith(
      "dataset-1",
      expect.objectContaining({ cursor: "cursor-page-1", offset: 0, limit: 20 }),
      expect.anything(),
    );
  });

  it("warns when tag facets only cover a bounded scan", async () => {
    enableModernScope();
    modernApi.fetchDocumentSummary.mockResolvedValue(
      makeModernSummary({
        tag_facets_complete: false,
        tag_facets_scan_limit: 1000,
        tag_facets_scanned: 1000,
        tag_facets_truncated: true,
      }),
    );
    modernApi.fetchDocumentPage.mockResolvedValue(makeModernPage());

    render(<KnowledgeTaxonomyPage />);

    const warning = await screen.findByRole("note");
    expect(warning.textContent).toContain("仅统计前 1000 篇文档，标签结果可能不完整");

  });
  it.each([
    [401, "身份已失效"],
    [403, "没有权限查看目录治理数据"],
    [422, "当前筛选条件无法执行"],
    [503, "目录服务暂不可用"],
  ])("distinguishes server error status %s from empty data", async (statusCode, title) => {
    enableModernScope();
    const error = Object.assign(new Error(`HTTP ${statusCode}`), { status: statusCode });
    modernApi.fetchDocumentSummary.mockRejectedValue(error);
    modernApi.fetchDocumentPage.mockResolvedValue(makeModernPage());

    render(<KnowledgeTaxonomyPage />);

    expect(await screen.findByText(title)).toBeTruthy();
    expect(screen.queryByText("知识库尚无可治理文档")).toBeNull();
  });

  it("distinguishes an authenticated empty catalog from an empty filtered result", async () => {
    enableModernScope();
    const emptySummary = makeModernSummary({
      summary: { ...makeModernSummary().summary, total: 0 },
    });
    modernApi.fetchDocumentSummary.mockResolvedValue(emptySummary);
    modernApi.fetchDocumentPage.mockResolvedValue(
      makeModernPage({ items: [], total: 0, next_cursor: null }),
    );

    const { unmount } = render(<KnowledgeTaxonomyPage />);
    expect(await screen.findByText("知识库尚无可治理文档")).toBeTruthy();
    unmount();

    modernApi.fetchDocumentSummary.mockResolvedValue(makeModernSummary());
    modernApi.fetchDocumentPage.mockResolvedValue(
      makeModernPage({ items: [], total: 0, next_cursor: null }),
    );
    render(<KnowledgeTaxonomyPage />);
    expect(await screen.findByText("没有符合当前筛选条件的文档")).toBeTruthy();
  });

  it("does not call modern APIs without an actor token and keeps the local projection", () => {
    modernApi.fetchDocumentSummary.mockResolvedValue(makeModernSummary());
    modernApi.fetchDocumentPage.mockResolvedValue(makeModernPage());

    render(<KnowledgeTaxonomyPage />);

    expect(screen.getByText("员工手册.md")).toBeTruthy();
    expect(modernApi.fetchDocumentSummary).not.toHaveBeenCalled();
    expect(modernApi.fetchDocumentPage).not.toHaveBeenCalled();
  });

  it("suppresses its standalone PageTopbar in embedded content-only mode", () => {
    render(<KnowledgeTaxonomyPage embedded />);
    expect(screen.queryByRole("heading", { name: "目录治理" })).toBeNull();
  });

});
