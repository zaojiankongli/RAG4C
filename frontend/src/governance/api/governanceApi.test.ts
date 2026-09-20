import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import type {
  DatasetProfile,
  DocumentVersion,
  QAAlternativeCreated,
  QABatchResult,
  QAImportResult,
  QAKnowledge,
  QAListResponse,
  QANegativeQuestionCreated,
  GovernanceScope,
} from "../model/governanceModel";
import {
  addQAAlternative,
  addQANegative,
  archiveDataset,
  batchExpireQA,
  batchRestoreQA,
  batchReviewQA,
  createDocumentVersion,
  createQA,
  deleteQAAlternative,
  deleteQANegative,
  disableDataset,
  expireQA,
  exportQA,
  fetchDatasetProfile,
  fetchDocumentVersions,
  fetchQAList,
  importQA,
  patchDatasetProfile,
  patchQA,
  restoreDataset,
  restoreQA,
  reviewQA,
} from "./governanceApi";

vi.mock("../../api/client", () => ({
  request: vi.fn(),
  getBaseUrl: vi.fn(() => "http://localhost:8010"),
  ApiError: class ApiError extends Error {},
}));

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
      origin: "import",
      limit: 500,
    });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa?review_status=pending&lifecycle_state=expired&origin=import&limit=500",
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

  it("uses exact import, batch, and export contracts", async () => {
    const importResult: QAImportResult = {
      batch_id: "qa-import-1",
      created: [{ id: "qa-1", revision: 1, question: "Q", content_hash: "h" }],
      skipped_duplicate: [{ index: 0, reason: "duplicate_existing", content_hash: "h2" }],
      failed: [{ index: 1, reason: "bad row" }],
      counts: { created: 1, skipped_duplicate: 1, failed: 1 },
    };
    requestMock.mockResolvedValue(importResult);
    await importQA(scope, {
      origin: "import",
      items: [
        {
          question: "Q",
          answer: "A",
          alternatives: ["Alt?"],
          negative_questions: ["Neg?"],
          source_uri: "",
          metadata: {},
        },
      ],
    });
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa/import", {
      method: "POST",
      headers,
      body: JSON.stringify({
        origin: "import",
        items: [
          {
            question: "Q",
            answer: "A",
            alternatives: ["Alt?"],
            negative_questions: ["Neg?"],
            source_uri: "",
            metadata: {},
          },
        ],
      }),
      signal: undefined,
    });

    const batchResult: QABatchResult = {
      action: "review",
      succeeded: [{ qa_id: "qa-1", revision: 2, review_status: "approved", lifecycle_state: "active" }],
      failed: [{ index: 0, qa_id: "qa-2", reason: "revision conflict", code: "ContentConflict" }],
      counts: { succeeded: 1, failed: 1 },
    };
    requestMock.mockResolvedValue(batchResult);
    await batchReviewQA(scope, [{ qa_id: "qa-1", expected_revision: 1, decision: "approved" }]);
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa/batch/review", {
      method: "POST",
      headers,
      body: JSON.stringify({ items: [{ qa_id: "qa-1", expected_revision: 1, decision: "approved" }] }),
      signal: undefined,
    });

    await batchExpireQA(scope, [{ qa_id: "qa-1", expected_revision: 2 }]);
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa/batch/expire", {
      method: "POST",
      headers,
      body: JSON.stringify({ items: [{ qa_id: "qa-1", expected_revision: 2 }] }),
      signal: undefined,
    });

    await batchRestoreQA(scope, [{ qa_id: "qa-2", expected_revision: 8 }]);
    expect(requestMock).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/qa/batch/restore", {
      method: "POST",
      headers,
      body: JSON.stringify({ items: [{ qa_id: "qa-2", expected_revision: 8 }] }),
      signal: undefined,
    });

    requestMock.mockResolvedValue({ items: [], count: 0 } as QAListResponse);
    const exported = await exportQA(scope, { review_status: "approved", origin: "import", limit: 50 }, "json");
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/export?review_status=approved&origin=import&limit=50&format=json",
      { method: "GET", headers, signal: undefined },
    );
    expect(JSON.parse(exported)).toEqual({ items: [], count: 0 });
  });

  it("uses body CAS for adding alternatives and negatives, and strict query CAS for deletion", async () => {
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

    requestMock.mockResolvedValue({
      id: "neg-1",
      qa_id: "qa/1",
      question: "How to bypass?",
      created_by: "editor",
      created_at: "2026-09-20T00:00:00Z",
      qa_revision: 3,
    } as QANegativeQuestionCreated);
    await addQANegative(scope, "qa/1", { expected_revision: 3, question: "How to bypass?" });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/negative-questions",
      {
        method: "POST",
        headers,
        body: JSON.stringify({ expected_revision: 3, question: "How to bypass?" }),
        signal: undefined,
      },
    );

    requestMock.mockResolvedValue(undefined);
    await deleteQANegative(scope, "qa/1", "neg/1", 4);
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/qa/qa%2F1/negative-questions/neg%2F1?expected_revision=4",
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
