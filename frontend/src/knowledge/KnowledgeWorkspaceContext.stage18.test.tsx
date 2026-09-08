// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { KnowledgeWorkspaceProvider, useKnowledgeWorkspace } from "./KnowledgeWorkspaceContext";

const api = vi.hoisted(() => ({
  fetchDocuments: vi.fn(),
  fetchDocumentSummary: vi.fn(),
  fetchWorkspaceDatasets: vi.fn(),
  fetchEnterpriseKnowledgeBaseDetail: vi.fn(),
}));

vi.mock("../api/client", () => ({
  fetchDocuments: api.fetchDocuments,
  fetchDocumentSummary: api.fetchDocumentSummary,
}));
vi.mock("../enterprise-workspace/api/enterpriseWorkspaceApi", () => ({
  fetchWorkspaceDatasets: api.fetchWorkspaceDatasets,
}));
vi.mock("../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi", () => ({
  fetchEnterpriseKnowledgeBaseDetail: api.fetchEnterpriseKnowledgeBaseDetail,
}));
vi.mock("../context/ConnectionContext", () => ({
  useConnection: () => ({ online: true }),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function Probe() {
  const value = useKnowledgeWorkspace();
  return (
    <div>
      <output data-testid="scope">
        {value.workspaceId ?? "none"}:{value.datasetId || "no-dataset"}:{value.workspaceScopeStatus}
      </output>
      <button onClick={() => void value.selectWorkspace("workspace-prod")}>select</button>
    </div>
  );
}

beforeEach(() => {
  localStorage.clear();
  api.fetchDocuments.mockReset().mockResolvedValue({ documents: [] });
  api.fetchDocumentSummary.mockResolvedValue({ summary: { total: 0 }, facets: [], recent: [] });
  api.fetchWorkspaceDatasets.mockReset();
  api.fetchEnterpriseKnowledgeBaseDetail.mockReset();
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("Stage18 verified Workspace -> Dataset scope", () => {
  it("does not request a stored or default Dataset while the Workspace scope is unverified", async () => {
    localStorage.setItem("rag4c.knowledge_actor_token", "actor-token");
    localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-a");
    localStorage.setItem("rag4c.knowledge_dataset_id", "dataset-stale");

    render(
      <KnowledgeWorkspaceProvider preferSummaryApi={false}>
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("scope").textContent).toContain("unverified"));
    expect(api.fetchDocuments).not.toHaveBeenCalled();
    expect(api.fetchDocumentSummary).not.toHaveBeenCalled();
  });

  it("does not trust an explicit Dataset until Registry ownership is verified", async () => {
    const detailRequest = deferred<{
      knowledge_base: {
        id: string;
        owning_workspace: { id: string; status: string };
      };
    }>();
    api.fetchEnterpriseKnowledgeBaseDetail.mockReturnValue(detailRequest.promise);

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        datasetId="dataset-explicit"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() => expect(api.fetchEnterpriseKnowledgeBaseDetail).toHaveBeenCalledTimes(1));
    expect(api.fetchDocuments).not.toHaveBeenCalled();
    detailRequest.resolve({
      knowledge_base: {
        id: "dataset-explicit",
        owning_workspace: { id: "workspace-explicit", status: "active" },
      },
    });
    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe(
        "workspace-explicit:dataset-explicit:verified",
      ),
    );
    expect(api.fetchDocuments).toHaveBeenCalledWith("dataset-explicit", expect.any(AbortSignal));
  });

  it("rejects a Registry response whose Dataset ID does not match the requested authority", async () => {
    api.fetchEnterpriseKnowledgeBaseDetail.mockResolvedValue({
      knowledge_base: {
        id: "dataset-other",
        owning_workspace: { id: "workspace-other", status: "active" },
      },
    });

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        datasetId="dataset-requested"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe("none:no-dataset:unavailable"),
    );
    expect(api.fetchDocuments).not.toHaveBeenCalled();
    expect(localStorage.getItem("rag4c.knowledge_dataset_id")).toBeNull();
  });

  it("rejects an ambiguous Workspace with multiple active primary knowledge bases", async () => {
    api.fetchWorkspaceDatasets.mockResolvedValue({
      items: [
        {
          dataset_id: "dataset-a",
          name: "知识库 A",
          binding_kind: "primary",
          status: "active",
          revision: 1,
        },
        {
          dataset_id: "dataset-b",
          name: "知识库 B",
          binding_kind: "primary",
          status: "active",
          revision: 1,
        },
      ],
      next_cursor: null,
    });

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "select" }));

    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe("none:no-dataset:unavailable"),
    );
    expect(api.fetchDocuments).not.toHaveBeenCalled();
    expect(localStorage.getItem("rag4c.knowledge_dataset_id")).toBeNull();
  });

  it("rejects a Workspace binding scan that exceeds the pagination safety bound", async () => {
    let page = 0;
    api.fetchWorkspaceDatasets.mockImplementation(async () => {
      page += 1;
      return { items: [], next_cursor: `cursor-${page}` };
    });

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "select" }));

    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe("none:no-dataset:unavailable"),
    );
    expect(api.fetchWorkspaceDatasets).toHaveBeenCalledTimes(100);
    expect(api.fetchDocuments).not.toHaveBeenCalled();
  });

  it("updates KnowledgeWorkspaceContext only after a server primary binding is returned", async () => {
    api.fetchWorkspaceDatasets.mockResolvedValue({
      items: [
        {
          dataset_id: "dataset-prod",
          name: "生产知识库",
          binding_kind: "primary",
          status: "active",
          revision: 8,
        },
      ],
      next_cursor: null,
    });

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "select" }));
    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe("workspace-prod:dataset-prod:verified"),
    );
    expect(localStorage.getItem("rag4c.knowledge_dataset_id")).toBe("dataset-prod");
    expect(api.fetchWorkspaceDatasets).toHaveBeenCalledWith(
      { tenantId: "tenant-a", actorToken: "actor-token" },
      "workspace-prod",
      { status: "active", limit: 100 },
      expect.any(Object),
    );
    expect(api.fetchDocuments).toHaveBeenCalledWith("dataset-prod", expect.any(AbortSignal));
  });

  it.each(["documents", "taxonomy", "sources", "governance"])(
    "updates the context for a %s Dataset query deep link",
    async (page) => {
      window.history.replaceState(null, "", `/${page}?dataset=dataset-deep-link`);
      api.fetchDocuments.mockResolvedValue({ documents: [{ id: "doc-deep-link" }] });
      api.fetchEnterpriseKnowledgeBaseDetail.mockResolvedValue({
        knowledge_base: {
          id: "dataset-deep-link",
          owning_workspace: { id: "workspace-deep-link", status: "active" },
        },
      });

      render(
        <KnowledgeWorkspaceProvider
          tenantId="tenant-a"
          actorToken="actor-token"
          preferSummaryApi={false}
        >
          <Probe />
        </KnowledgeWorkspaceProvider>,
      );

      await waitFor(() =>
        expect(screen.getByTestId("scope").textContent).toBe(
          "workspace-deep-link:dataset-deep-link:verified",
        ),
      );
      expect(api.fetchEnterpriseKnowledgeBaseDetail).toHaveBeenCalledWith(
        { tenantId: "tenant-a", datasetId: "dataset-deep-link", actorToken: "actor-token" },
        "dataset-deep-link",
        expect.any(Object),
      );
      expect(api.fetchDocuments).toHaveBeenCalledWith("dataset-deep-link", expect.any(AbortSignal));
    },
  );

  it("fails closed with no_primary_knowledge_base instead of inventing a default Dataset", async () => {
    localStorage.setItem("rag4c.knowledge_dataset_id", "dataset-stale");
    api.fetchWorkspaceDatasets.mockResolvedValue({ items: [], next_cursor: null });

    render(
      <KnowledgeWorkspaceProvider
        tenantId="tenant-a"
        actorToken="actor-token"
        preferSummaryApi={false}
      >
        <Probe />
      </KnowledgeWorkspaceProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "select" }));
    await waitFor(() =>
      expect(screen.getByTestId("scope").textContent).toBe(
        "workspace-prod:no-dataset:no_primary_knowledge_base",
      ),
    );
    expect(localStorage.getItem("rag4c.knowledge_dataset_id")).toBeNull();
    expect(api.fetchDocuments).not.toHaveBeenCalledWith("default", expect.anything());
  });
});
