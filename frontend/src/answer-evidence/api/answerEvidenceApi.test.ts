import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import type { AnswerFact } from "../model/answerEvidenceModel";
import {
  answerEvidenceBase,
  fetchAnswerFact,
  fetchAnswerFacts,
  fetchAnswerFactsByRun,
  loadAnswerEvidence,
  resolveAnswerEvidenceDatasetId,
} from "./answerEvidenceApi";

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
  resolveKnowledgeWorkspaceScope: vi.fn((options?: { datasetId?: string }) => ({
    tenantId: "tenant-a",
    datasetId: options?.datasetId?.trim() || "ds-1",
  })),
}));

const requestMock = vi.mocked(request);

const AUTH_HEADERS = {
  "X-RAG4C-Tenant": "tenant-a",
  Authorization: "Bearer tok",
};

const fact: AnswerFact = {
  id: "fact-1",
  tenant_id: "tenant-a",
  dataset_id: "ds-1",
  run_id: "run-1",
  outcome_code: "answered",
  route_code: "rag",
  citation_count: 2,
  evidence_count: 2,
  safe_query_preview: "如何配置对象存储",
  observed_at: "2026-09-20T10:00:00Z",
  evidence_refs: [
    {
      id: "ref-1",
      seq: 0,
      chunk_id: "chunk-1",
      chunk_revision_id: "chunk-1-r3",
      document_id: "doc-1",
      citation_status: "ok",
    },
    {
      id: "ref-2",
      seq: 1,
      chunk_id: "chunk-2",
      chunk_revision_id: null,
      document_id: null,
      citation_status: "stale",
    },
  ],
};

/** Normalizer always materializes optional digest keys as null when absent. */
const normalizedFact = {
  ...fact,
  request_id_digest: null,
  query_digest: null,
  answer_digest: null,
  evidence_chain_digest: null,
  fact_digest: null,
  evidence_refs: fact.evidence_refs.map((ref) => ({ ...ref, evidence_digest: null })),
};

class TestApiError extends Error {
  status?: number;
  kind?: string;
  constructor(message: string, kind?: string, status?: number) {
    super(message);
    this.kind = kind;
    this.status = status;
  }
}

beforeEach(() => {
  requestMock.mockReset();
});

describe("answerEvidenceApi", () => {
  it("resolves dataset id and builds the contract base path", () => {
    expect(resolveAnswerEvidenceDatasetId({ datasetId: "ds-9" })).toBe("ds-9");
    expect(answerEvidenceBase("ds-1")).toBe("/api/knowledge-bases/ds-1/answer-facts");
  });

  it("lists answer facts with outcome/run_id/limit query and knowledge auth headers", async () => {
    requestMock.mockResolvedValueOnce({ items: [fact], count: 1 });

    const result = await fetchAnswerFacts(
      "ds-1",
      { outcome: "answered", run_id: "run-1", limit: 5 },
      { actorToken: "tok" },
    );

    expect(result).toEqual({ items: [normalizedFact], count: 1 });
    expect(requestMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/ds-1/answer-facts?outcome=answered&run_id=run-1&limit=5",
      {
        method: "GET",
        headers: AUTH_HEADERS,
        signal: undefined,
      },
    );
  });

  it("omits empty list filters from the query string", async () => {
    requestMock.mockResolvedValueOnce({ items: [], count: 0 });
    await fetchAnswerFacts("ds-1", { outcome: "  ", run_id: "", limit: 0 }, { actorToken: "tok" });
    expect(requestMock).toHaveBeenCalledWith("/api/knowledge-bases/ds-1/answer-facts", {
      method: "GET",
      headers: AUTH_HEADERS,
      signal: undefined,
    });
  });

  it("fetches a single answer fact by id", async () => {
    requestMock.mockResolvedValueOnce(fact);
    await expect(fetchAnswerFact("ds-1", "fact-1", { actorToken: "tok" })).resolves.toEqual(
      normalizedFact,
    );
    expect(requestMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/ds-1/answer-facts/fact-1",
      {
        method: "GET",
        headers: AUTH_HEADERS,
        signal: undefined,
      },
    );
  });

  it("fetches by-run on the contract path and normalizes single-fact payloads", async () => {
    requestMock.mockResolvedValueOnce(fact);
    await expect(fetchAnswerFactsByRun("ds-1", "run-1", { actorToken: "tok" })).resolves.toEqual({
      items: [normalizedFact],
      count: 1,
    });
    expect(requestMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/ds-1/answer-facts/by-run/run-1",
      {
        method: "GET",
        headers: AUTH_HEADERS,
        signal: undefined,
      },
    );
  });

  it("loadAnswerEvidence prefers by-run and maps 404 to empty", async () => {
    requestMock.mockRejectedValueOnce(new TestApiError("not found", "http", 404));
    await expect(
      loadAnswerEvidence("ds-1", { runId: "run-missing" }, { actorToken: "tok" }),
    ).resolves.toEqual({ items: [], count: 0 });
    expect(requestMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/ds-1/answer-facts/by-run/run-missing",
      expect.objectContaining({ method: "GET" }),
    );

    requestMock.mockResolvedValueOnce({ items: [fact], count: 1 });
    await expect(
      loadAnswerEvidence("ds-1", { runId: null, limit: 3 }, { actorToken: "tok" }),
    ).resolves.toEqual({ items: [normalizedFact], count: 1 });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/ds-1/answer-facts?limit=3",
      expect.objectContaining({ method: "GET", headers: AUTH_HEADERS }),
    );
  });

  it("propagates non-404 by-run failures", async () => {
    requestMock.mockRejectedValueOnce(new TestApiError("forbidden", "http", 403));
    await expect(
      loadAnswerEvidence("ds-1", { runId: "run-1" }, { actorToken: "tok" }),
    ).rejects.toMatchObject({ status: 403 });
  });
});
