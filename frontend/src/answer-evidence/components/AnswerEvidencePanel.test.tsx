// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import type { AnswerFact } from "../model/answerEvidenceModel";
import AnswerEvidencePanel from "./AnswerEvidencePanel";
import * as api from "../api/answerEvidenceApi";
import { parseChunkWorkbenchLocation } from "../../run/appRoute";

vi.mock("../api/answerEvidenceApi", () => ({
  loadAnswerEvidence: vi.fn(),
  resolveAnswerEvidenceDatasetId: vi.fn(() => "ds-1"),
}));

const answeredFact: AnswerFact = {
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
      source_kind: "document",
    },
    {
      id: "ref-2",
      seq: 1,
      chunk_id: "chunk-2",
      chunk_revision_id: null,
      document_id: null,
      citation_status: "stale",
      source_kind: "document",
    },
  ],
};

const qaFact: AnswerFact = {
  ...answeredFact,
  id: "fact-qa",
  run_id: "run-qa",
  citation_count: 1,
  evidence_count: 2,
  evidence_refs: [
    {
      id: "ref-qa",
      seq: 0,
      chunk_id: "qa::qa-faq-1",
      chunk_revision_id: null,
      document_id: "doc-src-9",
      citation_status: "ok",
      source_kind: "qa",
      qa_id: "qa-faq-1",
      qa_revision: 3,
    },
    {
      id: "ref-doc",
      seq: 1,
      chunk_id: "chunk-1",
      chunk_revision_id: "chunk-1-r3",
      document_id: "doc-1",
      citation_status: "ok",
      source_kind: "document",
    },
  ],
};

const abstainedFact: AnswerFact = {
  ...answeredFact,
  id: "fact-2",
  run_id: "run-2",
  outcome_code: "abstained",
  route_code: "abstain",
  citation_count: 0,
  evidence_count: 0,
  safe_query_preview: null,
  evidence_refs: [],
};

beforeEach(() => {
  vi.mocked(api.loadAnswerEvidence).mockReset();
  vi.mocked(api.resolveAnswerEvidenceDatasetId).mockReset();
  vi.mocked(api.resolveAnswerEvidenceDatasetId).mockImplementation(() => "ds-1");
});

describe("AnswerEvidencePanel", () => {
  it("loads by-run when a run is selected and shows 证据链 outcome/route/counts/ref status tags", async () => {
    vi.mocked(api.loadAnswerEvidence).mockResolvedValue({
      items: [answeredFact],
      count: 1,
    });

    render(<AnswerEvidencePanel runId="run-1" datasetId="ds-1" />);

    await waitFor(() => expect(screen.getByTestId("answer-evidence-panel")).toBeTruthy());
    expect(api.loadAnswerEvidence).toHaveBeenCalledWith(
      "ds-1",
      { runId: "run-1", limit: 10 },
      expect.objectContaining({ datasetId: "ds-1" }),
    );

    expect(screen.getByRole("region", { name: "答案证据链" })).toBeTruthy();
    expect(screen.getByLabelText("答案事实摘要")).toBeTruthy();
    expect(screen.getByText("已回答")).toBeTruthy();
    expect(screen.getByText("RAG")).toBeTruthy();
    expect(screen.getByLabelText("证据引用列表")).toBeTruthy();
    expect(screen.getByLabelText("证据引用 0 ok source_kind=document")).toBeTruthy();
    expect(screen.getByLabelText("证据引用 1 stale source_kind=document")).toBeTruthy();
    expect(screen.getByText("引用有效")).toBeTruthy();
    expect(screen.getByText("过期")).toBeTruthy();
    expect(document.body.textContent).toContain("如何配置对象存储");
    expect(screen.getByLabelText("打开文档 doc-1")).toBeTruthy();
    expect(screen.getByLabelText("QA 权威命中数")).toBeTruthy();

    const refresh = screen.getByRole("button", { name: "刷新答案证据链" });
    expect(refresh.getAttribute("class") || "").toContain("answer-evidence-control-min-h");
  });

  it("marks QA evidence with source tag, governance deep link, and QA hit count", async () => {
    vi.mocked(api.loadAnswerEvidence).mockResolvedValue({ items: [qaFact], count: 1 });
    render(<AnswerEvidencePanel runId="run-qa" datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByTestId("answer-evidence-panel")).toBeTruthy());

    expect(screen.getByLabelText("证据引用 0 ok source_kind=qa")).toBeTruthy();
    expect(screen.getByText("QA 权威")).toBeTruthy();
    expect(screen.getByLabelText("打开 QA 治理 qa-faq-1")).toBeTruthy();
    const qaLink = screen.getByLabelText("打开 QA 治理 qa-faq-1") as HTMLAnchorElement;
    expect(qaLink.getAttribute("href")).toContain("#/governance?qa=qa-faq-1");
    // document link still available for the non-QA ref
    expect(screen.getByLabelText("打开文档 doc-1")).toBeTruthy();
    // QA ref must not produce a documents deep-link for qa:: / source doc only when kind=qa
    expect(screen.queryByLabelText("打开文档 doc-src-9")).toBeNull();
    expect(screen.getByLabelText("QA 权威命中数").textContent).toBe("1");
  });

  it("links a document reference into the 解析干预 workbench so that chunk gets selected, not just navigated", async () => {
    vi.mocked(api.loadAnswerEvidence).mockResolvedValue({ items: [answeredFact], count: 1 });
    render(<AnswerEvidencePanel runId="run-1" datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByTestId("answer-evidence-panel")).toBeTruthy());

    const link = screen.getByLabelText("在解析干预中查看切片 chunk-1") as HTMLAnchorElement;
    expect(link.textContent).toContain("在解析干预中查看这个切片");
    expect(link.getAttribute("class") || "").toContain("answer-evidence-control-min-h");
    const href = link.getAttribute("href") ?? "";
    expect(href).toBe("#/parse-intervention?doc=doc-1&chunk=chunk-1&dataset=ds-1");
    // 深链必须能被工作区原样解析回同一个 chunk —— 只带 doc 的链接等于没接上回路
    expect(parseChunkWorkbenchLocation({ pathname: "/", search: "", hash: href })).toEqual({
      docId: "doc-1",
      chunkId: "chunk-1",
      datasetId: "ds-1",
    });
    // 没有 document 归属的证据不编造工作区链接
    expect(screen.queryByLabelText("在解析干预中查看切片 chunk-2")).toBeNull();
  });

  it("never offers a chunk workbench link for QA authority hits", async () => {
    vi.mocked(api.loadAnswerEvidence).mockResolvedValue({ items: [qaFact], count: 1 });
    render(<AnswerEvidencePanel runId="run-qa" datasetId="ds-1" />);
    await waitFor(() => expect(screen.getByTestId("answer-evidence-panel")).toBeTruthy());

    expect(screen.getAllByLabelText(/^在解析干预中查看切片/).map((a) => a.getAttribute("href"))).toEqual([
      "#/parse-intervention?doc=doc-1&chunk=chunk-1&dataset=ds-1",
    ]);
  });

  it("falls back to list mode on load without a run and can switch facts", async () => {
    const user = userEvent.setup();
    vi.mocked(api.loadAnswerEvidence)
      .mockResolvedValueOnce({ items: [answeredFact, abstainedFact], count: 2 })
      .mockResolvedValueOnce({ items: [answeredFact, abstainedFact], count: 2 });

    render(<AnswerEvidencePanel datasetId="ds-1" />);

    await waitFor(() =>
      expect(api.loadAnswerEvidence).toHaveBeenCalledWith(
        "ds-1",
        { runId: null, limit: 10 },
        expect.anything(),
      ),
    );
    expect(await screen.findByText("已回答")).toBeTruthy();

    const switcher = screen.getByRole("group", { name: "选择答案事实" });
    expect(switcher).toBeTruthy();
    await user.click(screen.getByRole("button", { name: `选择答案事实 ${abstainedFact.id}` }));
    expect(await screen.findByText("已弃权")).toBeTruthy();
    expect(screen.getByLabelText("证据引用为空")).toBeTruthy();
  });

  it("renders empty state when the selected run has no catalog fact", async () => {
    vi.mocked(api.loadAnswerEvidence).mockResolvedValue({ items: [], count: 0 });

    render(<AnswerEvidencePanel runId="run-empty" datasetId="ds-1" />);

    expect(await screen.findByLabelText("暂无答案证据")).toBeTruthy();
    expect(screen.queryByLabelText("证据引用列表")).toBeNull();
  });

  it("shows an error state with retry on API failure", async () => {
    const user = userEvent.setup();
    const failure = Object.assign(new Error("forbidden"), {
      kind: "http",
      status: 403,
    });
    // projectAnswerEvidenceError checks ApiError instances; simulate via status-bearing error path
    vi.mocked(api.loadAnswerEvidence)
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce({ items: [answeredFact], count: 1 });

    render(<AnswerEvidencePanel runId="run-1" datasetId="ds-1" />);

    // generic error → 不可用 or 没有读取权限 depending on ApiError identity
    const alert = await screen.findByRole("alert");
    expect(alert).toBeTruthy();

    const retry = screen.getByRole("button", { name: "重试加载答案证据链" });
    expect(retry.getAttribute("class") || "").toContain("answer-evidence-control-min-h");
    await user.click(retry);
    await waitFor(() => expect(screen.getByTestId("answer-evidence-panel")).toBeTruthy());
    expect(api.loadAnswerEvidence).toHaveBeenCalledTimes(2);
  });
});
