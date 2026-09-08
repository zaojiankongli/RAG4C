// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentMetricsResponse } from "../types/rag";
import DocumentIngestMonitor from "./DocumentIngestMonitor";

const api = vi.hoisted(() => ({ fetchDocumentMetrics: vi.fn() }));
vi.mock("../api/client", () => ({ fetchDocumentMetrics: api.fetchDocumentMetrics }));

afterEach(cleanup);
beforeEach(() => api.fetchDocumentMetrics.mockReset());

const stat = (overrides: Partial<DocumentMetricsResponse["latency_ms"]["parse"]> = {}) => ({
  count: 4,
  sum: 1000,
  mean: 250,
  min: 100,
  max: 500,
  p50: 220,
  p95: 480,
  p99: 500,
  ...overrides,
});

function metrics(overrides: Partial<DocumentMetricsResponse> = {}): DocumentMetricsResponse {
  return {
    summary: { total: 8, completed: 5, failed: 1, processing: 2, chunks: 96 },
    latency_ms: {
      parse: stat({ p95: 480 }),
      total: stat({ p95: 1850 }),
      stages: { parse: stat({ p95: 480 }), split: stat({ p95: 260 }), index: stat({ p95: 890 }) },
    },
    engine_distribution: [
      { name: "MinerU", value: 5 },
      { name: "文本直取", value: 3 },
    ],
    type_distribution: [
      { name: "pdf", value: 6 },
      { name: "markdown", value: 2 },
    ],
    slow_documents: [
      { document_id: "doc-1", name: "年度制度汇编.pdf", total_ms: 3850, status: "completed" },
    ],
    recent_failures: [
      {
        document_id: "doc-2",
        name: "扫描件.pdf",
        message: "OCR 页面识别失败",
        updated_at: "2026-08-24T08:00:00Z",
      },
    ],
    legacy_metadata_count: 0,
    ...overrides,
  };
}

describe("DocumentIngestMonitor", () => {
  it("presents P95, the stage waterfall, distributions, slow documents and failures", async () => {
    api.fetchDocumentMetrics.mockResolvedValue(metrics());
    render(<DocumentIngestMonitor />);

    const region = await screen.findByRole("region", { name: "文档入库监控" });
    expect(region.querySelectorAll(".t-card").length).toBeGreaterThan(3);
    expect(within(region).getByText("解析 P95")).toBeTruthy();
    expect(within(region).getAllByText("480ms").length).toBeGreaterThanOrEqual(2);
    expect(within(region).getByText("入库总耗时 P95")).toBeTruthy();
    expect(within(region).getByText("1.85s")).toBeTruthy();
    expect(within(region).getByRole("list", { name: "解析阶段耗时瀑布" })).toBeTruthy();
    expect(within(region).getByText("MinerU")).toBeTruthy();
    expect(within(region).getByText("年度制度汇编.pdf")).toBeTruthy();
    expect(within(region).getByText("扫描件.pdf")).toBeTruthy();
    expect(within(region).getByText("OCR 页面识别失败")).toBeTruthy();
  });

  it("separates total-duration coverage from completely missing legacy metadata", async () => {
    api.fetchDocumentMetrics.mockResolvedValue(
      metrics({
        summary: { total: 97, completed: 97, failed: 0, processing: 0, chunks: 1354 },
        latency_ms: {
          parse: stat({ count: 95, p95: 1.33 }),
          total: stat({ count: 0, p95: 0 }),
          stages: {},
        },
        slow_documents: [],
        legacy_metadata_count: 2,
      }),
    );
    render(<DocumentIngestMonitor />);

    const region = await screen.findByRole("region", { name: "文档入库监控" });
    expect(within(region).getByText("暂无可比数据")).toBeTruthy();
    expect(within(region).getByText(/97 篇缺少完整总耗时/)).toBeTruthy();
    expect(within(region).getByText(/2 篇完全缺少解析元数据/)).toBeTruthy();
    expect(region.textContent).not.toContain("2 篇旧文档缺少总耗时");
    expect(region.textContent).not.toContain("0秒");
  });

  it("offers a real retry action when document metrics fail", async () => {
    api.fetchDocumentMetrics
      .mockRejectedValueOnce(new Error("metrics offline"))
      .mockResolvedValueOnce(metrics());
    render(<DocumentIngestMonitor />);

    const retry = await screen.findByRole("button", { name: "重试文档监控" });
    expect(retry.closest(".t-alert")).not.toBeNull();
    retry.click();
    await waitFor(() => expect(screen.getByText("解析 P95")).toBeTruthy());
    expect(api.fetchDocumentMetrics).toHaveBeenCalledTimes(2);
  });
});
