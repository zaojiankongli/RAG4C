// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ConsistencySummaryResponse,
  DeadLetterListResponse,
} from "../consistency/consistencyModel";
import ConsistencyPage from "./ConsistencyPage";

const api = vi.hoisted(() => ({
  fetchConsistencySummary: vi.fn(),
  fetchConsistencyDeadLetters: vi.fn(),
  requeueConsistencyDeadLetter: vi.fn(),
}));

const connection = vi.hoisted(() => ({
  online: true as boolean | null,
  checking: false,
  refresh: vi.fn(async () => undefined),
}));

const workspace = vi.hoisted(() => ({
  datasetId: "dataset-a",
  tenantId: "tenant-a",
  scope: { datasetId: "dataset-a", tenantId: "tenant-a" },
}));

vi.mock("../api/client", () => api);
vi.mock("../context/ConnectionContext", () => ({ useConnection: () => connection }));
vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({
  useKnowledgeWorkspace: () => workspace,
}));

const summary: ConsistencySummaryResponse = {
  mode: "report-only",
  best_effort: true,
  counts: {
    documents_scanned: 6,
    authoritative_heads: 8,
    projection_chunks: 13,
    missing_chunks: 3,
    stale_chunks: 2,
    orphaned_chunks: 4,
    blocked_documents: 1,
  },
  drift_categories: {
    missing: 3,
    stale: 2,
    orphaned: 4,
    blocked: 1,
    stale_reasons: { revision_mismatch: 2 },
  },
  manifest_ref: "ref-manifest-full-value",
  complete: false,
  confirmable: false,
  snapshot_guarantee: "catalog_only",
  has_drift: true,
  qa_authority: {
    total: 5,
    effective_retrieval: 2,
    pending_review: 1,
    rejected: 1,
    expired: 1,
    retrieval_disabled: 0,
    note: "QA 权威来自 MySQL Catalog",
  },
};

const deadLetters: DeadLetterListResponse = {
  count: 1,
  items: [
    {
      dead_letter_ref: "ref-dead-letter-full-value-abcdefghijklmnopqrstuvwxyz",
      operation_ref: "ref-operation-full-value-abcdefghijklmnopqrstuvwxyz",
      document_ref: "ref-document-full-value-abcdefghijklmnopqrstuvwxyz",
      target_store: "milvus_chunks",
      operation: "delete_document",
      retry_count: 4,
      failed_at: "2026-08-24T08:09:10Z",
      requeued_operation_ref: null,
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function deadLetterList(count: number): DeadLetterListResponse {
  return {
    count,
    items: Array.from({ length: count }, (_, index) => ({
      dead_letter_ref: `ref-dead-${index + 1}`,
      operation_ref: `ref-operation-${index + 1}`,
      document_ref: `ref-document-${index + 1}`,
      target_store: `store-${index + 1}`,
      operation: `operation-${index + 1}`,
      retry_count: index,
      failed_at: `2026-08-24T08:${String(index).padStart(2, "0")}:10Z`,
      requeued_operation_ref: null,
    })),
  };
}

function installClipboard() {
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  return writeText;
}

beforeEach(() => {
  connection.online = true;
  workspace.datasetId = "dataset-a";
  workspace.tenantId = "tenant-a";
  workspace.scope = { datasetId: "dataset-a", tenantId: "tenant-a" };
  localStorage.setItem("rag4c.knowledge_actor_token", "test-actor-token");
  localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-a");
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  connection.checking = false;
  connection.refresh.mockReset();
  connection.refresh.mockResolvedValue(undefined);
  api.fetchConsistencySummary.mockReset();
  api.fetchConsistencyDeadLetters.mockReset();
  api.requeueConsistencyDeadLetter.mockReset();
  api.fetchConsistencySummary.mockResolvedValue(summary);
  api.fetchConsistencyDeadLetters.mockResolvedValue(deadLetters);
  api.requeueConsistencyDeadLetter.mockResolvedValue({
    status: "enqueued",
    dead_letter_ref: deadLetters.items[0].dead_letter_ref,
    operation_ref: "ref-requeued-operation",
  });
});

afterEach(cleanup);

describe("ConsistencyPage auth recovery", () => {
  it("shows recovery hint when actor token is missing and does not call APIs", async () => {
    localStorage.removeItem("rag4c.knowledge_actor_token");
    render(<ConsistencyPage />);
    expect(await screen.findByText("一致性报告身份校验失败")).toBeTruthy();
    expect(screen.getByLabelText("鉴权恢复提示")).toBeTruthy();
    expect(api.fetchConsistencySummary).not.toHaveBeenCalled();
    expect(api.fetchConsistencyDeadLetters).not.toHaveBeenCalled();
  });
});

describe("ConsistencyPage", () => {
  it("states catalog-only best-effort authority and renders lifeline plus drift categories", async () => {
    render(<ConsistencyPage />);

    expect(await screen.findByText("一致性控制台")).toBeTruthy();
    const authority = screen.getByRole("alert", { name: /最佳努力目录报告/ });
    expect(authority.textContent).toContain("best_effort=true");
    expect(authority.textContent).toContain("catalog-only");
    expect(authority.textContent).toContain("complete=false");
    expect(authority.textContent).toContain("confirmable=false");

    const lifeline = screen.getByRole("list", { name: "知识一致性生命线" });
    expect(within(lifeline).getByText("期望").parentElement?.textContent).toContain("8");
    expect(within(lifeline).getByText("投影").parentElement?.textContent).toContain("13");
    expect(within(lifeline).getByText("漂移").parentElement?.textContent).toContain("10");
    for (const [label, count] of [["缺失", "3"], ["过期", "2"], ["孤儿", "4"], ["阻塞", "1"]]) {
      expect(screen.getByText(label).parentElement?.textContent).toContain(count);
    }
    // QA 权威区块 + 治理深链
    const qaLifeline = screen.getByRole("list", { name: "QA 权威生命线" });
    expect(qaLifeline).toBeTruthy();
    const qaLink = screen.getByLabelText("打开 QA 治理");
    expect(qaLink.getAttribute("href")).toBe("#/governance");
    expect(screen.queryByRole("button", { name: /修复/ })).toBeNull();
  });

  it("keeps full refs available by title and copy while visually truncating table cells", async () => {
    const writeText = installClipboard();
    render(<ConsistencyPage />);

    const table = await screen.findByRole("region", { name: "死信记录，可横向滚动" });
    expect(within(table).getByText("delete_document")).toBeTruthy();
    expect(within(table).getByText("milvus_chunks")).toBeTruthy();
    expect(within(table).getByText("4")).toBeTruthy();
    expect(within(table).getByText(/2026/)).toBeTruthy();

    const fullRef = deadLetters.items[0].dead_letter_ref;
    const refText = within(table).getByTitle(fullRef);
    expect(refText.classList.contains("consistency-ref-text")).toBe(true);
    fireEvent.click(within(table).getByRole("button", { name: "复制死信引用" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(fullRef));
  });

  it("requeues a dead letter and refreshes real API data without exposing repair", async () => {
    api.fetchConsistencyDeadLetters
      .mockResolvedValueOnce(deadLetters)
      .mockResolvedValueOnce({
        ...deadLetters,
        items: [{ ...deadLetters.items[0], requeued_operation_ref: "ref-requeued-operation" }],
      });
    render(<ConsistencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /重放死信/ }));
    await waitFor(() =>
      expect(api.requeueConsistencyDeadLetter).toHaveBeenCalledWith(
        "dataset-a",
        deadLetters.items[0].dead_letter_ref,
        expect.objectContaining({ operatorNote: expect.any(String) }),
      ),
    );
    await waitFor(() => expect(api.fetchConsistencyDeadLetters).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("已入队")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /修复/ })).toBeNull();
  });

  it("does not fabricate data offline and retry refreshes connection before refetching", async () => {
    connection.online = false;
    const order: string[] = [];
    connection.refresh.mockImplementation(async () => {
      order.push("connection");
    });
    api.fetchConsistencySummary.mockImplementation(async () => {
      order.push("summary");
      return summary;
    });
    api.fetchConsistencyDeadLetters.mockImplementation(async () => {
      order.push("dead-letters");
      return { items: [], count: 0 };
    });

    render(<ConsistencyPage />);
    expect((await screen.findByRole("alert")).textContent).toContain("后端服务未连接");
    expect(screen.queryByText("13")).toBeNull();
    expect(api.fetchConsistencySummary).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "重新连接并重试" }));
    await waitFor(() => expect(api.fetchConsistencySummary).toHaveBeenCalledTimes(1));
    expect(order[0]).toBe("connection");
    expect(new Set(order.slice(1))).toEqual(new Set(["summary", "dead-letters"]));
  });

  it("labels an omitted best_effort field defensively instead of crashing", async () => {
    api.fetchConsistencySummary.mockResolvedValueOnce({
      ...summary,
      best_effort: undefined,
    });
    render(<ConsistencyPage />);
    const authority = await screen.findByRole("alert", { name: /最佳努力目录报告/ });
    expect(authority.textContent).toContain("best_effort=未返回");
  });

  it("shows loading, error, and empty dead-letter states without demo rows", async () => {
    let resolveSummary!: (value: ConsistencySummaryResponse) => void;
    api.fetchConsistencySummary.mockReturnValue(
      new Promise<ConsistencySummaryResponse>((resolve) => {
        resolveSummary = resolve;
      }),
    );
    api.fetchConsistencyDeadLetters.mockResolvedValue({ items: [], count: 0 });
    const view = render(<ConsistencyPage />);
    expect(screen.getByRole("status").textContent).toContain("正在读取一致性报告");

    resolveSummary(summary);
    expect(await screen.findByText("当前没有死信记录")).toBeTruthy();

    api.fetchConsistencySummary.mockRejectedValueOnce(new Error("gateway timeout"));
    api.fetchConsistencyDeadLetters.mockResolvedValueOnce({ items: [], count: 0 });
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("gateway timeout"));
    expect(screen.queryByText("delete_document")).toBeNull();

    view.unmount();
  });

  it("uses the non-default authenticated workspace tenant and dataset for every request", async () => {
    workspace.datasetId = "dataset-z";
    workspace.tenantId = "tenant-z";
    workspace.scope = { datasetId: "dataset-z", tenantId: "tenant-z" };

    render(<ConsistencyPage />);
    await screen.findByRole("alert", { name: /最佳努力目录报告/ });

    expect(api.fetchConsistencySummary).toHaveBeenCalledWith(
      "dataset-z",
      expect.objectContaining({ tenantId: "tenant-z" }),
    );
    expect(api.fetchConsistencyDeadLetters).toHaveBeenCalledWith(
      "dataset-z",
      expect.objectContaining({ tenantId: "tenant-z" }),
    );
  });

  it("runs retry as one connection refresh followed by one non-aborted refetch", async () => {
    connection.online = false;
    const health = deferred<void>();
    const summaryRequest = deferred<ConsistencySummaryResponse>();
    const lettersRequest = deferred<DeadLetterListResponse>();
    const order: string[] = [];
    connection.refresh.mockImplementation(async () => {
      order.push("connection:start");
      await health.promise;
      order.push("connection:end");
    });
    api.fetchConsistencySummary.mockImplementation(async () => {
      order.push("summary");
      return summaryRequest.promise;
    });
    api.fetchConsistencyDeadLetters.mockImplementation(async () => {
      order.push("dead-letters");
      return lettersRequest.promise;
    });

    render(<ConsistencyPage />);
    const retryButton = await screen.findByRole("button", { name: "重新连接并重试" });
    fireEvent.click(retryButton);
    fireEvent.click(retryButton);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "刷新" }).hasAttribute("disabled")).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(connection.refresh).toHaveBeenCalledTimes(1);
    expect(api.fetchConsistencySummary).not.toHaveBeenCalled();

    health.resolve();
    await waitFor(() => expect(api.fetchConsistencySummary).toHaveBeenCalledTimes(1));
    expect(api.fetchConsistencyDeadLetters).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["connection:start", "connection:end", "summary", "dead-letters"]);
    const summarySignal = api.fetchConsistencySummary.mock.calls[0][1].signal as AbortSignal;
    const lettersSignal = api.fetchConsistencyDeadLetters.mock.calls[0][1].signal as AbortSignal;
    expect(connection.refresh).toHaveBeenCalledTimes(1);
    expect(summarySignal.aborted).toBe(false);
    expect(lettersSignal.aborted).toBe(false);

    summaryRequest.resolve(summary);
    lettersRequest.resolve(deadLetters);
    expect(await screen.findByRole("alert", { name: /最佳努力目录报告/ })).toBeTruthy();
  });

  it("deduplicates the same pending ref while allowing different refs to progress", async () => {
    const letters = deadLetterList(2);
    api.fetchConsistencyDeadLetters.mockResolvedValueOnce(letters);
    const first = deferred<{ status: "enqueued"; dead_letter_ref: string; operation_ref: string }>();
    const second = deferred<{ status: "already_requeued"; dead_letter_ref: string; operation_ref: string }>();
    api.requeueConsistencyDeadLetter.mockImplementation(
      (_datasetId: string, ref: string) => (ref === "ref-dead-1" ? first.promise : second.promise),
    );

    render(<ConsistencyPage />);
    const buttons = await screen.findAllByRole("button", { name: /重放死信/ });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);

    expect(api.requeueConsistencyDeadLetter).toHaveBeenCalledTimes(2);
    expect(api.requeueConsistencyDeadLetter.mock.calls.map((call) => call[1])).toEqual([
      "ref-dead-1",
      "ref-dead-2",
    ]);
    await waitFor(() => {
      const pendingButtons = screen
        .getAllByText("重放中")
        .map((label) => label.closest("button"));
      expect(pendingButtons).toHaveLength(2);
      expect(pendingButtons.every((button) => button?.hasAttribute("disabled"))).toBe(true);
    });

    api.fetchConsistencyDeadLetters.mockRejectedValueOnce(new Error("list refresh failed"));
    first.resolve({ status: "enqueued", dead_letter_ref: "ref-dead-1", operation_ref: "op-new-1" });
    expect(await screen.findByText("已入队")).toBeTruthy();
    expect(await screen.findByText(/列表刷新失败/)).toBeTruthy();

    api.fetchConsistencyDeadLetters.mockResolvedValueOnce(letters);
    second.resolve({
      status: "already_requeued",
      dead_letter_ref: "ref-dead-2",
      operation_ref: "op-existing-2",
    });
    expect(await screen.findByText("此前已重放")).toBeTruthy();
    expect(screen.getByText("已入队")).toBeTruthy();
  });

  it("aborts pending requeues on unmount and never starts their follow-up refresh", async () => {
    const pending = deferred<{ status: "enqueued"; dead_letter_ref: string; operation_ref: string }>();
    api.requeueConsistencyDeadLetter.mockReturnValueOnce(pending.promise);
    const view = render(<ConsistencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /重放死信/ }));
    const signal = api.requeueConsistencyDeadLetter.mock.calls[0][2].signal as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      pending.resolve({
        status: "enqueued",
        dead_letter_ref: deadLetters.items[0].dead_letter_ref,
        operation_ref: "op-after-unmount",
      });
      await pending.promise;
    });
    expect(api.fetchConsistencyDeadLetters).toHaveBeenCalledTimes(1);
  });

  it("moves the actual horizontal scroll owner with ArrowLeft and ArrowRight", async () => {
    render(<ConsistencyPage />);
    const table = await screen.findByRole("region", { name: "死信记录，可横向滚动" });
    table.scrollLeft = 100;
    fireEvent.keyDown(table, { key: "ArrowRight" });
    expect(table.scrollLeft).toBe(196);
    fireEvent.keyDown(table, { key: "ArrowLeft" });
    expect(table.scrollLeft).toBe(100);
  });

  it("reports unsupported clipboard instead of announcing a false success", async () => {
    render(<ConsistencyPage />);
    const table = await screen.findByRole("region", { name: "死信记录，可横向滚动" });
    fireEvent.click(within(table).getByRole("button", { name: "复制死信引用" }));

    expect(await screen.findByText("复制失败")).toBeTruthy();
    expect(screen.getByText(/当前环境不支持剪贴板复制/)).toBeTruthy();
    expect(screen.queryByText("完整引用已复制")).toBeNull();
  });

  it("uses controlled native TDesign pagination with ten rows and clamps after refresh", async () => {
    api.fetchConsistencyDeadLetters
      .mockResolvedValueOnce(deadLetterList(11))
      .mockResolvedValueOnce(deadLetterList(3));
    render(<ConsistencyPage />);

    const table = await screen.findByRole("region", { name: "死信记录，可横向滚动" });
    expect(within(table).getAllByRole("row")).toHaveLength(11);
    const pagination = document.querySelector(".t-pagination");
    expect(pagination).not.toBeNull();
    fireEvent.click(within(pagination as HTMLElement).getByText("2"));
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
    expect(within(table).getByTitle("ref-dead-11")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.fetchConsistencyDeadLetters).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "死信记录，可横向滚动" })).getAllByRole("row"),
      ).toHaveLength(4),
    );
    expect(
      within(screen.getByRole("region", { name: "死信记录，可横向滚动" })).getByTitle(
        "ref-dead-1",
      ),
    ).toBeTruthy();
    expect(document.querySelector(".t-pagination")).toBeNull();
  });

});
