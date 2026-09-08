// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeWorkspaceProvider, useKnowledgeWorkspace } from "./KnowledgeWorkspaceContext";
import type { ReactNode } from "react";
import type {
  DocumentCatalogItem,
  DocumentCatalogSummaryResponse,
  DocumentItem,
} from "../types/rag";
import { useKnowledgeDocuments } from "./useKnowledgeDocuments";
import KnowledgeOverviewPage from "../pages/KnowledgeOverviewPage";
import KnowledgeTaxonomyPage from "../pages/KnowledgeTaxonomyPage";
import KnowledgeSourcesPage from "../pages/KnowledgeSourcesPage";
import { KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY } from "./workspaceScope";

const api = vi.hoisted(() => ({
  fetchDocuments: vi.fn(),
  fetchDocumentSummary: vi.fn(),
  fetchWorkspaceDatasets: vi.fn(),
  fetchEnterpriseKnowledgeBaseDetail: vi.fn(),
}));

const connection = vi.hoisted(() => ({
  online: true as boolean | null,
}));

vi.mock("../api/client", () => ({
  ApiError: class MockApiError extends Error {
    status?: number;
  },
  fetchDocuments: api.fetchDocuments,
  fetchDocumentSummary: api.fetchDocumentSummary,
}));
vi.mock("../enterprise-workspace/api/enterpriseWorkspaceApi", () => ({
  fetchWorkspaceDatasets: api.fetchWorkspaceDatasets,
}));
vi.mock("../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi", () => ({
  fetchEnterpriseKnowledgeBaseDetail: api.fetchEnterpriseKnowledgeBaseDetail,
}));

// App keeps QueryPage mounted even when the overview route is active. It is
// unrelated to this provider integration and emits a legacy select warning.
vi.mock("../pages/QueryPage", () => ({ default: () => null }));

vi.mock("../context/ConnectionContext", () => ({
  ConnectionProvider: ({ children }: { children: ReactNode }) => children,
  useConnection: () => ({
    online: connection.online,
    checking: connection.online === null,
    health: null,
    refresh: vi.fn(),
  }),
}));

const DOCUMENT: DocumentItem = {
  id: "doc-1",
  name: "员工手册.md",
  status: "completed",
  progress: 1,
  chunk_count: 12,
  doc_type: "markdown",
  error_message: "",
};

const MODERN_DOCUMENT: DocumentCatalogItem = {
  ...DOCUMENT,
  status: "completed",
  tenant_id: "tenant-1",
  dataset_id: "dataset-1",
  status_detail: "",
  parser_meta: { parse_ms: 420, chunk_count: 12 },
  updated_at: "2026-08-26T08:00:00Z",
  logical_folder_path: "制度/人力",
  tags: ["制度", "员工"],
  source_uri: null,
  source_type: "upload",
  source_id: "hr-policy",
  external_id: null,
  mutation_generation: 1,
  lifecycle_state: "active",
  retrieval_enabled: true,
  active_delete_operation_id: null,
};

const MODERN_SUMMARY: DocumentCatalogSummaryResponse & {
  tag_authority: "legacy_projection";
} = {
  dataset_id: "dataset-1",
  summary: {
    total: 128,
    completed: 120,
    processing: 5,
    failed: 3,
    chunks: 20480,
    parser_observed: 124,
    parser_coverage: 97,
  },
  facets: {
    statuses: {
      all: 128,
      waiting: 1,
      parsing: 1,
      splitting: 1,
      indexing: 2,
      processing: 5,
      completed: 120,
      error: 3,
    },
    types: [{ value: "pdf", count: 64 }],
    engines: [{ value: "vision", count: 88 }],
    folders: [{ path: "制度/人力", documents: 40, chunks: 6400 }],
    tags: [{ name: "制度", documents: 32, chunks: 5200 }],
  },
  recent: [MODERN_DOCUMENT],
  generated_at: "2026-08-26T08:05:00Z",
  tag_authority: "legacy_projection",
  tag_facets_complete: false,
  tag_facets_scan_limit: 1000,
  tag_facets_scanned: 1000,
  tag_facets_truncated: true,
};

function SnapshotProbe({ label }: { label: string }) {
  const workspace = useKnowledgeWorkspace();
  return (
    <output data-testid={label}>
      {workspace.status}:{workspace.documents.map((document) => document.id).join(",")}
    </output>
  );
}

function DocumentsHookProbe() {
  const workspace = useKnowledgeDocuments();
  return (
    <output data-testid="documents-hook">
      {workspace.status}:{workspace.documents.map((document) => document.id).join(",")}
    </output>
  );
}

function WorkspaceMetadataProbe() {
  const workspace = useKnowledgeWorkspace();
  return (
    <>
      <output data-testid="data-mode">{workspace.dataMode}</output>
      <output data-testid="summary-total">{workspace.summary?.total ?? "none"}</output>
      <output data-testid="facet-folder-count">{workspace.facets?.folders.length ?? "none"}</output>
      <output data-testid="recent-ids">
        {(workspace.recent ?? []).map((document) => document.id).join(",")}
      </output>
      <output data-testid="tag-authority">{workspace.tagAuthority}</output>
      <output data-testid="tag-completeness">
        {workspace.tagFacetsComplete === null ? "none" : String(workspace.tagFacetsComplete)}
      </output>
    </>
  );
}

function InvalidateProbe() {
  const workspace = useKnowledgeWorkspace();
  return (
    <>
      <SnapshotProbe label="invalidated" />
      <button type="button" onClick={workspace.invalidate}>
        invalidate
      </button>
    </>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

afterEach(cleanup);

describe("KnowledgeWorkspaceProvider", () => {
  beforeEach(() => {
    api.fetchDocuments.mockReset();
    api.fetchDocumentSummary.mockReset();
    api.fetchWorkspaceDatasets.mockReset();
    api.fetchEnterpriseKnowledgeBaseDetail
      .mockReset()
      .mockImplementation((_scope: unknown, requestedDatasetId: string) =>
        Promise.resolve({
          knowledge_base: {
            id: requestedDatasetId,
            owning_workspace: { id: "workspace-authority", status: "active" },
          },
        }),
      );
    connection.online = true;
    localStorage.clear();
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

  it("shares one fetched document snapshot across every mounted consumer", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });

    render(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="overview" />
        <SnapshotProbe label="taxonomy" />
        <SnapshotProbe label="sources" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("overview").textContent).toBe("ready:doc-1");
    });
    expect(screen.getByTestId("taxonomy").textContent).toBe("ready:doc-1");
    expect(screen.getByTestId("sources").textContent).toBe("ready:doc-1");
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);
  });

  it("uses the authenticated summary API without downloading the full document list", async () => {
    const actorToken = "signed-summary-token";
    localStorage.setItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY, actorToken);
    api.fetchDocuments.mockResolvedValue({ documents: [] });
    api.fetchDocumentSummary.mockResolvedValue(MODERN_SUMMARY);

    render(
      <KnowledgeWorkspaceProvider tenantId="tenant-1" datasetId="dataset-1">
        <SnapshotProbe label="modern" />
        <WorkspaceMetadataProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("modern").textContent).toBe("ready:");
    });

    expect(api.fetchDocumentSummary).toHaveBeenCalledTimes(1);
    expect(api.fetchDocumentSummary).toHaveBeenCalledWith(
      "dataset-1",
      expect.objectContaining({
        tenantId: "tenant-1",
        actorToken,
        signal: expect.any(AbortSignal),
      }),
    );
    expect(api.fetchDocuments).not.toHaveBeenCalled();
    expect(screen.getByTestId("data-mode").textContent).toBe("summary-api");
    expect(screen.getByTestId("summary-total").textContent).toBe("128");
    expect(screen.getByTestId("facet-folder-count").textContent).toBe("1");
    expect(screen.getByTestId("recent-ids").textContent).toBe("doc-1");
    expect(screen.getByTestId("tag-authority").textContent).toBe("legacy_projection");
    expect(screen.getByTestId("tag-completeness").textContent).toBe("false");
  });

  it("marks tokenless local data as legacy-local while preserving the legacy fetch contract", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });

    render(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="legacy-mode" />
        <WorkspaceMetadataProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("legacy-mode").textContent).toBe("ready:doc-1");
    });

    expect(screen.getByTestId("data-mode").textContent).toBe("legacy-local");
    expect(screen.getByTestId("summary-total").textContent).toBe("none");
    expect(screen.getByTestId("recent-ids").textContent).toBe("");
    expect(screen.getByTestId("tag-authority").textContent).toBe("unknown");
    expect(screen.getByTestId("tag-completeness").textContent).toBe("none");
    expect(api.fetchDocumentSummary).not.toHaveBeenCalled();
  });

  it("keeps invalidate coalescing and generation fencing for summary requests", async () => {
    localStorage.setItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY, "signed-summary-token");
    api.fetchDocuments.mockResolvedValue({ documents: [] });
    const first = deferred<DocumentCatalogSummaryResponse>();
    const second = deferred<DocumentCatalogSummaryResponse>();
    api.fetchDocumentSummary.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(
      <KnowledgeWorkspaceProvider tenantId="tenant-1" datasetId="dataset-1">
        <WorkspaceMetadataProbe />
        <InvalidateProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocumentSummary).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "invalidate" }));
    first.resolve(MODERN_SUMMARY);

    await waitFor(() => expect(api.fetchDocumentSummary).toHaveBeenCalledTimes(2));
    expect(api.fetchDocumentSummary.mock.calls[0][0]).toBe("dataset-1");
    expect(api.fetchDocumentSummary.mock.calls[1][0]).toBe("dataset-1");

    second.resolve({
      ...MODERN_SUMMARY,
      recent: [{ ...MODERN_DOCUMENT, id: "doc-modern-2" }],
    });
    await waitFor(() => {
      expect(screen.getByTestId("recent-ids").textContent).toBe("doc-modern-2");
    });
  });

  it("shows a real connection error instead of silently substituting demo documents", async () => {
    connection.online = false;

    render(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="offline" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("offline").textContent).toBe("error:");
    });
    expect(api.fetchDocuments).not.toHaveBeenCalled();
  });

  it("uses demo documents only when demo mode is explicitly enabled", async () => {
    connection.online = false;

    render(
      <KnowledgeWorkspaceProvider demoEnabled>
        <SnapshotProbe label="demo" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("demo").textContent).toContain("demo:doc-hr-001");
    });
    expect(api.fetchDocuments).not.toHaveBeenCalled();
  });

  it("runs one follow-up refresh when invalidated during an in-flight request", async () => {
    const first = deferred<{ documents: DocumentItem[] }>();
    const second = deferred<{ documents: DocumentItem[] }>();
    api.fetchDocuments.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(
      <KnowledgeWorkspaceProvider>
        <InvalidateProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "invalidate" }));
    first.resolve({ documents: [DOCUMENT] });

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
    second.resolve({ documents: [{ ...DOCUMENT, id: "doc-2" }] });
    await waitFor(() => {
      expect(screen.getByTestId("invalidated").textContent).toBe("ready:doc-2");
    });
  });

  it("keeps the compatibility document hook on the provider snapshot without another request", async () => {
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });

    render(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="context-hook" />
        <DocumentsHookProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("documents-hook").textContent).toBe("ready:doc-1");
    });
    expect(screen.getByTestId("context-hook").textContent).toBe("ready:doc-1");
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);
  });

  it("shows the real fetch error and lets any kept-alive knowledge page retry the shared snapshot", async () => {
    api.fetchDocuments.mockRejectedValueOnce(new Error("catalog unavailable"));

    render(
      <KnowledgeWorkspaceProvider>
        <KnowledgeOverviewPage />
        <KnowledgeTaxonomyPage />
        <KnowledgeSourcesPage />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getAllByText("知识库数据加载失败")).toHaveLength(2);
    });
    expect(screen.getAllByText("catalog unavailable")).toHaveLength(2);
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);

    api.fetchDocuments.mockResolvedValueOnce({ documents: [DOCUMENT] });
    fireEvent.click(screen.getAllByRole("button", { name: "重试加载知识库数据" })[0]);

    await waitFor(() => {
      expect(screen.queryAllByText("知识库数据加载失败")).toHaveLength(0);
    });
    expect(api.fetchDocuments).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0);
  });

  it("installs the shared workspace once at the application provider boundary", async () => {
    window.history.replaceState(null, "", "/overview");
    api.fetchDocuments.mockResolvedValue({ documents: [DOCUMENT] });
    const { default: AppProviders } = await import("../AppProviders");

    render(<AppProviders />);

    await waitFor(() => {
      expect(screen.getAllByText("员工手册.md").length).toBeGreaterThan(0);
    });
    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);
  });

  it("aborts the old request and fetches a fresh snapshot when the dataset changes", async () => {
    const first = deferred<{ documents: DocumentItem[] }>();
    const second = deferred<{ documents: DocumentItem[] }>();
    api.fetchDocuments.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const view = render(
      <KnowledgeWorkspaceProvider datasetId="alpha">
        <SnapshotProbe label="dataset" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(1));
    const firstSignal = api.fetchDocuments.mock.calls[0][1] as AbortSignal;

    view.rerender(
      <KnowledgeWorkspaceProvider datasetId="beta">
        <SnapshotProbe label="dataset" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
    expect(firstSignal.aborted).toBe(true);
    expect(api.fetchDocuments.mock.calls[1][0]).toBe("beta");

    second.resolve({ documents: [{ ...DOCUMENT, id: "doc-beta" }] });
    await waitFor(() => {
      expect(screen.getByTestId("dataset").textContent).toBe("ready:doc-beta");
    });

    first.resolve({ documents: [{ ...DOCUMENT, id: "doc-alpha" }] });
    await Promise.resolve();
    expect(screen.getByTestId("dataset").textContent).toBe("ready:doc-beta");
  });

  it("aborts and fences the previous connection generation across offline and online transitions", async () => {
    const first = deferred<{ documents: DocumentItem[] }>();
    const second = deferred<{ documents: DocumentItem[] }>();
    api.fetchDocuments.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const view = render(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="connection-generation" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(1));
    const firstSignal = api.fetchDocuments.mock.calls[0][1] as AbortSignal;

    connection.online = false;
    view.rerender(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="connection-generation" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("connection-generation").textContent).toBe("error:");
    });
    expect(firstSignal.aborted).toBe(true);

    connection.online = true;
    view.rerender(
      <KnowledgeWorkspaceProvider>
        <SnapshotProbe label="connection-generation" />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(2));
    second.resolve({ documents: [{ ...DOCUMENT, id: "doc-online-2" }] });
    await waitFor(() => {
      expect(screen.getByTestId("connection-generation").textContent).toBe("ready:doc-online-2");
    });

    first.resolve({ documents: [{ ...DOCUMENT, id: "doc-online-1" }] });
    await Promise.resolve();
    expect(screen.getByTestId("connection-generation").textContent).toBe("ready:doc-online-2");
  });

  it("clears pending invalidation on unmount and never starts a follow-up request", async () => {
    const first = deferred<{ documents: DocumentItem[] }>();
    api.fetchDocuments
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({ documents: [{ ...DOCUMENT, id: "unexpected-follow-up" }] });

    const view = render(
      <KnowledgeWorkspaceProvider>
        <InvalidateProbe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchDocuments).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "invalidate" }));
    view.unmount();

    first.resolve({ documents: [DOCUMENT] });
    await first.promise;
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(api.fetchDocuments).toHaveBeenCalledTimes(1);
  });
});
