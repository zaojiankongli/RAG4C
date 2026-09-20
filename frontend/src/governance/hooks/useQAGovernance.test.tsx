// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as api from "../api/governanceApi";
import type { GovernanceScope, QAKnowledge } from "../model/governanceModel";
import { useQAGovernance } from "./useQAGovernance";

vi.mock("../api/governanceApi");

const scope: GovernanceScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token" };
const qa = (index: number, revision = 1): QAKnowledge => ({
  id: `qa-${index}`, tenant_id: "tenant-a", dataset_id: "dataset-a", revision,
  question: `Question ${index}`, answer: `Answer ${index}`, origin: index % 2 ? "manual" : "automatic",
  review_status: "pending", lifecycle_state: "active", retrieval_enabled: false,
  effective_from: null, expires_at: null, source_document_id: null, source_uri: "", metadata: {},
  created_by: "editor-a", reviewed_by: null, reviewed_at: null,
  created_at: `2026-08-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
  updated_at: "2026-08-25T00:00:00Z", alternatives: [], negative_questions: [],
  content_hash: null, import_batch_id: null,
});

beforeEach(() => vi.resetAllMocks());

describe("useQAGovernance", () => {
  it("loads at most 500 filtered facts and exposes ten rows per client page", async () => {
    const items = Array.from({ length: 21 }, (_, index) => qa(index));
    vi.mocked(api.fetchQAList).mockResolvedValue({ items, count: 21 });
    const { result } = renderHook(() => useQAGovernance(scope, true));

    await waitFor(() => expect(result.current.items).toHaveLength(21));
    expect(api.fetchQAList).toHaveBeenCalledWith(scope, { limit: 500 }, expect.anything());
    expect(result.current.pageItems).toHaveLength(10);
    expect(result.current.pageCount).toBe(3);

    act(() => result.current.setPage(3));
    expect(result.current.pageItems).toHaveLength(1);
  });

  it("resets pagination, sends supported filters, and refetches after review", async () => {
    vi.mocked(api.fetchQAList).mockResolvedValue({ items: [qa(1)], count: 1 });
    vi.mocked(api.reviewQA).mockResolvedValue({ ...qa(1, 2), review_status: "approved", retrieval_enabled: true });
    const { result } = renderHook(() => useQAGovernance(scope, true));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    act(() => result.current.setPage(2));
    act(() => result.current.setFilters({ review_status: "pending", origin: "import" }));
    await waitFor(() => expect(api.fetchQAList).toHaveBeenLastCalledWith(
      scope,
      { review_status: "pending", origin: "import", limit: 500 },
      expect.anything(),
    ));
    expect(result.current.page).toBe(1);

    await act(async () => {
      await result.current.review("qa-1", { expected_revision: 1, decision: "approved" });
    });
    expect(api.reviewQA).toHaveBeenCalledWith(scope, "qa-1", { expected_revision: 1, decision: "approved" }, expect.anything());
    expect(api.fetchQAList).toHaveBeenCalledTimes(3);
  });

  it("does not report success when QA CAS conflicts", async () => {
    vi.mocked(api.fetchQAList).mockResolvedValueOnce({ items: [qa(1, 4)], count: 1 }).mockResolvedValueOnce({ items: [qa(1, 5)], count: 1 });
    vi.mocked(api.patchQA).mockRejectedValue(new ApiError("raw", "http", 409));
    const { result } = renderHook(() => useQAGovernance(scope, true));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    await act(async () => {
      await result.current.update("qa-1", { expected_revision: 3, answer: "stale" });
    });
    expect(result.current.error?.kind).toBe("conflict");
    expect(result.current.items[0].revision).toBe(5);
    expect(api.fetchQAList).toHaveBeenCalledTimes(2);
  });

  it("clamps the page after shrink and marks a 500-row response truncated", async () => {
    const many = Array.from({ length: 21 }, (_, index) => qa(index));
    vi.mocked(api.fetchQAList).mockResolvedValueOnce({ items: many, count: 500 }).mockResolvedValueOnce({ items: [qa(1)], count: 1 });
    const { result } = renderHook(() => useQAGovernance(scope, true));
    await waitFor(() => expect(result.current.truncated).toBe(true)); act(() => result.current.setPage(3));
    await act(async () => { await result.current.refresh(); }); await waitFor(() => expect(result.current.page).toBe(1));
  });

  it("clears A and ignores an A review completion after scope B loads", async () => {
    let resolveMutation!: (value: QAKnowledge) => void;
    const mutation = new Promise<QAKnowledge>((resolve) => { resolveMutation = resolve; });
    vi.mocked(api.fetchQAList).mockImplementation(async (current) => ({ items: [{ ...qa(1), id: current.datasetId }], count: 1 })); vi.mocked(api.reviewQA).mockReturnValue(mutation);
    const scopeB = { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b" };
    const { result, rerender } = renderHook(({ current }) => useQAGovernance(current, true), { initialProps: { current: scope } });
    await waitFor(() => expect(result.current.items[0]?.id).toBe("dataset-a")); act(() => { void result.current.review("dataset-a", { expected_revision: 1, decision: "approved" }); });
    rerender({ current: scopeB }); expect(result.current.items).toEqual([]); await waitFor(() => expect(result.current.items[0]?.id).toBe("dataset-b")); resolveMutation(qa(1, 99)); await mutation; await Promise.resolve(); expect(result.current.items[0]?.id).toBe("dataset-b");
  });

  it("wires import, batch review, negative CRUD, and export to governanceApi", async () => {
    vi.mocked(api.fetchQAList).mockResolvedValue({ items: [qa(1, 3)], count: 1 });
    vi.mocked(api.importQA).mockResolvedValue({
      batch_id: "qa-import-1",
      created: [{ id: "qa-new", revision: 1, question: "Q", content_hash: "h" }],
      skipped_duplicate: [],
      failed: [],
      counts: { created: 1, skipped_duplicate: 0, failed: 0 },
    });
    vi.mocked(api.batchReviewQA).mockResolvedValue({
      action: "review",
      succeeded: [{ qa_id: "qa-1", revision: 4, review_status: "approved", lifecycle_state: "active" }],
      failed: [],
      counts: { succeeded: 1, failed: 0 },
    });
    vi.mocked(api.batchExpireQA).mockResolvedValue({
      action: "expire",
      succeeded: [{ qa_id: "qa-1", revision: 5, review_status: "approved", lifecycle_state: "expired" }],
      failed: [],
      counts: { succeeded: 1, failed: 0 },
    });
    vi.mocked(api.batchRestoreQA).mockResolvedValue({
      action: "restore",
      succeeded: [{ qa_id: "qa-1", revision: 6, review_status: "approved", lifecycle_state: "active" }],
      failed: [],
      counts: { succeeded: 1, failed: 0 },
    });
    vi.mocked(api.exportQA).mockResolvedValue('{"items":[],"count":0}');
    vi.mocked(api.addQANegative).mockResolvedValue({
      id: "neg-1", qa_id: "qa-1", question: "Neg?", created_by: "a", created_at: "2026-09-20T00:00:00Z", qa_revision: 4,
    });
    vi.mocked(api.deleteQANegative).mockResolvedValue(undefined);

    const { result } = renderHook(() => useQAGovernance(scope, true));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    await act(async () => {
      await result.current.importItems({
        origin: "import",
        items: [{ question: "Q", answer: "A", alternatives: [], negative_questions: [], source_uri: "", metadata: {} }],
      });
    });
    expect(api.importQA).toHaveBeenCalledWith(
      scope,
      {
        origin: "import",
        items: [{ question: "Q", answer: "A", alternatives: [], negative_questions: [], source_uri: "", metadata: {} }],
      },
      expect.anything(),
    );

    await act(async () => {
      await result.current.batchReview([{ qa_id: "qa-1", expected_revision: 3, decision: "approved" }]);
    });
    expect(api.batchReviewQA).toHaveBeenCalledWith(
      scope,
      [{ qa_id: "qa-1", expected_revision: 3, decision: "approved" }],
      expect.anything(),
    );

    await act(async () => {
      await result.current.batchExpire([{ qa_id: "qa-1", expected_revision: 3 }]);
    });
    expect(api.batchExpireQA).toHaveBeenCalledWith(scope, [{ qa_id: "qa-1", expected_revision: 3 }], expect.anything());

    await act(async () => {
      await result.current.batchRestore([{ qa_id: "qa-1", expected_revision: 5 }]);
    });
    expect(api.batchRestoreQA).toHaveBeenCalledWith(scope, [{ qa_id: "qa-1", expected_revision: 5 }], expect.anything());

    await act(async () => {
      await result.current.addNegative("qa-1", { expected_revision: 3, question: "Neg?" });
    });
    expect(api.addQANegative).toHaveBeenCalledWith(scope, "qa-1", { expected_revision: 3, question: "Neg?" }, expect.anything());

    await act(async () => {
      await result.current.deleteNegative("qa-1", "neg-1", 4);
    });
    expect(api.deleteQANegative).toHaveBeenCalledWith(scope, "qa-1", "neg-1", 4, expect.anything());

    let exported: string | null = null;
    await act(async () => {
      exported = await result.current.exportQA("json");
    });
    expect(api.exportQA).toHaveBeenCalledWith(scope, { limit: 500 }, "json");
    expect(exported).toBe('{"items":[],"count":0}');
  });
});
