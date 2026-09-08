// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const legacyApi = vi.hoisted(() => ({
  ApiError: class MockApiError extends Error {},
  batchUpdateDocumentSettings: vi.fn(),
  createDocumentDeleteIdempotencyKey: vi.fn(() => "legacy-key"),
  fetchDocumentDeleteBatch: vi.fn(),
  fetchDocumentPage: vi.fn(),
  fetchDocumentDeleteOperation: vi.fn(),
  ingestDocument: vi.fn(),
  ingestFolder: vi.fn(),
  reindexDocument: vi.fn(),
  requestDocumentBatchDelete: vi.fn(),
  requestDocumentDelete: vi.fn(),
  shouldRetainDocumentDeleteIdempotencyKey: vi.fn(() => false),
  updateDocumentSettings: vi.fn(),
}));
const recoveryApi = vi.hoisted(() => ({
  createRecoveryIdempotencyKey: vi.fn(() => "recovery-key-stage23"),
  recycleDocument: vi.fn(),
}));
const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
const workspaceState = vi.hoisted(() => ({
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  documents: [
    {
      id: "doc-1",
      name: "员工手册.md",
      status: "completed",
      progress: 1,
      chunk_count: 12,
      doc_type: "markdown",
      error_message: "",
      tenant_id: "tenant-1",
      dataset_id: "dataset-1",
      mutation_generation: 4,
      lifecycle_state: "active",
      retrieval_enabled: true,
      active_delete_operation_id: null,
    },
  ],
  status: "ready",
  error: null,
  loading: false,
  usingMock: false,
  summary: null,
  facets: null,
  recent: [],
  tagAuthority: null,
  tagFacetsComplete: null,
  tagFacetsScanLimit: null,
  tagFacetsScanned: null,
  tagFacetsTruncated: false,
  summaryGeneratedAt: null,
  refresh: vi.fn(),
  invalidate: vi.fn(),
}));

vi.mock("../api/client", () => legacyApi);
vi.mock("../enterprise-content-recovery/api/recoveryApi", () => recoveryApi);
vi.mock("../context/ConnectionContext", () => ({ useConnection: () => ({ online: true }) }));
vi.mock("../ui/feedback", () => ({ message: feedback }));
vi.mock("../knowledge/useKnowledgeDocuments", () => ({
  useKnowledgeDocuments: () => workspaceState,
}));
vi.mock("../parse-intervention/ParseInterventionWorkspace", () => ({ default: () => null }));

import DocumentsPage from "./DocumentsPage";

beforeEach(() => {
  localStorage.setItem("rag4c.knowledge_actor_token", "signed-test-token");
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
  recoveryApi.recycleDocument.mockReset();
  recoveryApi.createRecoveryIdempotencyKey.mockReturnValue("recovery-key-stage23");
  legacyApi.requestDocumentDelete.mockReset();
  feedback.success.mockReset();
});

afterEach(cleanup);

describe("DocumentsPage enterprise recovery boundary", () => {
  it("uses recycle authority and never calls durable delete", async () => {
    recoveryApi.recycleDocument.mockResolvedValue({ state: "applied" });
    render(
      <DocumentsPage
        preferModernCatalog={false}
        contentRecoveryCapabilityReady
        contentRecoveryReadOnly={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "移入回收站 员工手册.md" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认移入" }));

    await waitFor(() => expect(recoveryApi.recycleDocument).toHaveBeenCalledTimes(1));
    expect(recoveryApi.recycleDocument).toHaveBeenCalledWith(
      { tenantId: "tenant-1", actorToken: "signed-test-token" },
      "doc-1",
      {
        datasetId: "dataset-1",
        expectedMutationGeneration: 4,
        reason: "Documents workspace explicit recycle",
      },
      { idempotencyKey: "recovery-key-stage23" },
    );
    expect(legacyApi.requestDocumentDelete).not.toHaveBeenCalled();
  });

  it("disables deletion instead of falling back when recovery is unavailable", () => {
    render(<DocumentsPage preferModernCatalog={false} contentRecoveryCapabilityReady={false} />);
    const button = screen.getByRole("button", { name: "删除 员工手册.md" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(recoveryApi.recycleDocument).not.toHaveBeenCalled();
    expect(legacyApi.requestDocumentDelete).not.toHaveBeenCalled();
  });
});
