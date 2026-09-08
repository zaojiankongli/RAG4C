import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import type {
  DatasetProfile,
  DocumentVersion,
  QAAlternativeCreated,
  QAKnowledge,
  QAListResponse,
  GovernanceScope,
} from "../model/governanceModel";
import {
  addQAAlternative,
  archiveDataset,
  createDocumentVersion,
  createQA,
  deleteQAAlternative,
  disableDataset,
  expireQA,
  fetchDatasetProfile,
  fetchDocumentVersions,
  fetchQAList,
  patchDatasetProfile,
  patchQA,
  restoreDataset,
  restoreQA,
  reviewQA,
} from "./governanceApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope: GovernanceScope = {
  tenantId: "tenant a",
  datasetId: "dataset/a",
  actorToken: "signed-token",
};
const headers = {
  "X-RAG4C-Tenant": "tenant a",
  Authorization: "Bearer signed-token",
};

beforeEach(() => requestMock.mockReset());

describe("governance API", () => {
  it("uses the exact dataset profile paths, methods, auth headers, and revision bodies", async () => {
    requestMock.mockResolvedValue({} as DatasetProfile);

    await fetchDatasetProfile(scope);
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa", {
      method: "GET",
      headers,
      signal: undefined,
    });

    await patchDatasetProfile(scope, { expected_revision: 7, visibility: "tenant" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa", {
      method: "PATCH",
      headers,
      body: JSON.stringify({ expected_revision: 7, visibility: "tenant" }),
      signal: undefined,
    });

    for (const [operation, call] of [
      ["archive", archiveDataset],
      ["restore", restoreDataset],
      ["disable", disableDataset],
    ] as const) {
      await call(scope, { expected_revision: 8 });
      expect(requestMock).toHaveBeenLastCalledWith(
        `/api/knowledge-bases/dataset%2Fa/${operation}`,
        { method: "POST", headers, body: JSON.stringify({ expected_revision: 8 }), signal: undefined },
      );
    }
  });

  it("serializes only supported QA filters and exact CRUD/review/lifecycle paths", async () => {
    requestMock.mockResolvedValue({ items: [], count: 0 } as QAListResponse);
    await fetchQAList(scope, {
      review_status: "pending",
      lifecycle_state: "expired",
      origin: "automatic",
      limit: 500,
    });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa?review_status=pending&lifecycle_state=expired&origin=automatic&limit=500",
      { method: "GET", headers, signal: undefined },
    );

    requestMock.mockResolvedValue({} as QAKnowledge);
    await createQA(scope, { question: "Q", answer: "A", origin: "manual" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa", {
      method: "POST",
      headers,
      body: JSON.stringify({ question: "Q", answer: "A", origin: "manual" }),
      signal: undefined,
    });

    await patchQA(scope, "qa/1", { expected_revision: 3, answer: "A2" });
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa/qa%2F1", {
      method: "PATCH",
      headers,
      body: JSON.stringify({ expected_revision: 3, answer: "A2" }),
      signal: undefined,
    });

    await reviewQA(scope, "qa/1", { expected_revision: 4, decision: "approved" });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/review",
      {
        method: "POST",
        headers,
        body: JSON.stringify({ expected_revision: 4, decision: "approved" }),
        signal: undefined,
      },
    );

    for (const [operation, call] of [
      ["expire", expireQA],
      ["restore", restoreQA],
    ] as const) {
      await call(scope, "qa/1", { expected_revision: 5 });
      expect(requestMock).toHaveBeenLastCalledWith(
        `/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/${operation}`,
        { method: "POST", headers, body: JSON.stringify({ expected_revision: 5 }), signal: undefined },
      );
    }
  });

  it("uses body CAS for adding alternatives and strict query CAS for deletion", async () => {
    requestMock.mockResolvedValue({} as QAAlternativeCreated);
    await addQAAlternative(scope, "qa/1", { expected_revision: 9, question: "Alternative?" });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/alternatives",
      {
        method: "POST",
        headers,
        body: JSON.stringify({ expected_revision: 9, question: "Alternative?" }),
        signal: undefined,
      },
    );

    requestMock.mockResolvedValue(undefined);
    await deleteQAAlternative(scope, "qa/1", "alt/1", 10);
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/alternatives/alt%2F1?expected_revision=10",
      { method: "DELETE", headers, signal: undefined },
    );
  });

  it("uses exact immutable document-version list and create contracts", async () => {
    requestMock.mockResolvedValue({ items: [], count: 0 });
    await fetchDocumentVersions(scope, "doc/1", 100);
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/versions?limit=100",
      { method: "GET", headers, signal: undefined },
    );

    requestMock.mockResolvedValue({} as DocumentVersion);
    const payload = {
      expected_current_revision: 2,
      expected_current_version_id: "version-2",
      source_identity: "upload:guide.pdf",
      source_hash: "a".repeat(64),
      parser_policy_snapshot: { engine: "mineru" },
      parser_metadata: { pages: 4 },
      source_content_ref: "vault://documents/doc-1/v3",
      change_reason: "policy update",
      effective_from: "2026-08-25T10:00:00Z",
      expires_at: null,
      purge_after: null,
      retrieval_enabled: true,
    };
    await createDocumentVersion(scope, "doc/1", payload);
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/versions",
      { method: "POST", headers, body: JSON.stringify(payload), signal: undefined },
    );
  });
});
