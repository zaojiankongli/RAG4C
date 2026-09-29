// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DocumentCatalogItem,
  DocumentCatalogSummaryResponse,
  DocumentItem,
} from "../types/rag";
import KnowledgeOverviewPage from "./KnowledgeOverviewPage";

const workspace = vi.hoisted(() => ({
  documents: [] as DocumentItem[],
  status: "ready" as "loading" | "ready" | "empty" | "error" | "demo",
  error: null as string | null,
  loading: false,
  usingMock: false,
  dataMode: "legacy-local" as "legacy-local" | "summary-api" | "demo",
  summary: null as DocumentCatalogSummaryResponse["summary"] | null,
  facets: null as DocumentCatalogSummaryResponse["facets"] | null,
  recent: [] as DocumentCatalogItem[],
  tagAuthority: "unknown" as "governed" | "legacy_projection" | "unknown",
  tagFacetsComplete: null as boolean | null,
  tagFacetsTruncated: false,
  reload: vi.fn(),
}));

const MODERN_RECENT_DOCUMENT: DocumentCatalogItem = {
  id: "modern-recent",
  name: "现代摘要资产.pdf",
  status: "completed",
  status_detail: "",
  progress: 1,
  chunk_count: 128,
  doc_type: "pdf",
  error_message: "",
  tenant_id: "tenant-1",
  dataset_id: "dataset-1",
  parser_meta: { parse_ms: 360, chunk_count: 128 },
  updated_at: "2026-08-26T08:00:00Z",
  logical_folder_path: "制度/人力",
  tags: ["制度", "员工"],
  source_uri: null,
  source_type: "upload",
  source_id: "hr-policy",
  external_id: null,
  mutation_generation: 2,
  lifecycle_state: "active",
  retrieval_enabled: true,
  active_delete_operation_id: null,
};

const MODERN_OVERVIEW_SUMMARY: DocumentCatalogSummaryResponse & {
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
    chunking_reasons: [{ value: "complex_or_structured", count: 88 }],
    folders: [{ path: "制度/人力", documents: 40, chunks: 6400 }],
    tags: [{ name: "制度", documents: 32, chunks: 5200 }],
  },
  recent: [MODERN_RECENT_DOCUMENT],
  generated_at: "2026-08-26T08:05:00Z",
  tag_authority: "legacy_projection",
  tag_facets_complete: false,
  tag_facets_scan_limit: 1000,
  tag_facets_scanned: 1000,
  tag_facets_truncated: true,
};

vi.mock("../knowledge/useKnowledgeDocuments", () => ({
  useKnowledgeDocuments: () => workspace,
}));

function renderOverview() {
  return render(<KnowledgeOverviewPage />);
}

afterEach(cleanup);

describe("KnowledgeOverviewPage enterprise command center", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/overview");
    workspace.documents = [
      {
        id: "doc-1",
        name: "员工手册.md",
        status: "completed",
        progress: 1,
        chunk_count: 12,
        doc_type: "markdown",
        error_message: "",
        parser_meta: { parse_ms: 420, chunk_count: 12 },
        logical_folder_path: "制度/人力",
        tags: ["制度", "员工"],
        source_type: "upload",
        source_id: "hr-policy",
        updated_at: "2026-08-24T08:00:00Z",
      },
      {
        id: "doc-2",
        name: "报销规范.pdf",
        status: "error",
        progress: 0.72,
        chunk_count: 4,
        doc_type: "pdf",
        error_message: "OCR 失败",
        logical_folder_path: "制度/财务",
        tags: ["制度", "财务"],
        source_type: "upload",
        source_id: "finance-policy",
        updated_at: "2026-08-24T07:00:00Z",
      },
      {
        id: "doc-3",
        name: "产品知识.txt",
        status: "indexing",
        progress: 0.82,
        chunk_count: 8,
        doc_type: "text",
        error_message: "",
        parser_meta: { parse_ms: 230, chunk_count: 8 },
        logical_folder_path: "产品/知识",
        tags: ["产品"],
        source_type: "website",
        source_id: "product-site",
        updated_at: "2026-08-24T06:00:00Z",
      },
    ];
    workspace.status = "ready";
    workspace.error = null;
    workspace.loading = false;
    workspace.usingMock = false;
    workspace.dataMode = "legacy-local";
    workspace.summary = null;
    workspace.facets = null;
    workspace.recent = [];
    workspace.tagAuthority = "unknown";
    workspace.tagFacetsComplete = null;
    workspace.tagFacetsTruncated = false;
    workspace.reload.mockReset();
  });

  it("keeps a single page heading and presents the authoritative workspace state without a marketing hero", () => {
    const { container } = renderOverview();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "知识概览" })).toBeTruthy();
    expect(screen.getByText("权威目录").closest(".t-tag")).not.toBeNull();
    expect(screen.getByText("企业知识资产运营指挥台")).toBeTruthy();
    expect(container.querySelector(".knowledge-hero")).toBeNull();
  });

  it("clearly marks demo data when the workspace is not authoritative", () => {
    workspace.usingMock = true;
    workspace.status = "demo";

    renderOverview();

    expect(screen.getByText("演示数据").closest(".t-tag")).not.toBeNull();
    expect(screen.getByText("当前为演示投影，不代表企业数据库真账")).toBeTruthy();
  });

  it("renders a compact metric strip as directory projections rather than live retrieval metrics", () => {
    renderOverview();

    const metrics = screen.getByRole("region", { name: "知识资产指标" });
    const documentMetric = within(metrics).getByText("知识文档").closest(".stat-card") as HTMLElement;
    const chunkMetric = within(metrics).getByText("目录片段").closest(".stat-card") as HTMLElement;
    const categoryMetric = within(metrics).getByText("逻辑分类").closest(".stat-card") as HTMLElement;
    const tagMetric = within(metrics).getByText("文档标签").closest(".stat-card") as HTMLElement;
    expect(within(documentMetric).getByText("3")).toBeTruthy();
    expect(within(chunkMetric).getByText("24")).toBeTruthy();
    expect(within(categoryMetric).getByText("3")).toBeTruthy();
    expect(within(tagMetric).getByText("4")).toBeTruthy();
    expect(metrics.querySelectorAll(".t-card")).toHaveLength(4);
  });

  it("uses server aggregates and bounded recent assets in summary mode instead of the legacy document array", () => {
    workspace.dataMode = "summary-api";
    workspace.documents = [
      {
        id: "legacy-only",
        name: "不应参与摘要的旧列表.pdf",
        status: "completed",
        progress: 1,
        chunk_count: 1,
        doc_type: "pdf",
        error_message: "",
      },
    ];
    workspace.summary = MODERN_OVERVIEW_SUMMARY.summary;
    workspace.facets = MODERN_OVERVIEW_SUMMARY.facets;
    workspace.recent = MODERN_OVERVIEW_SUMMARY.recent;
    workspace.tagAuthority = MODERN_OVERVIEW_SUMMARY.tag_authority;
    workspace.tagFacetsComplete = MODERN_OVERVIEW_SUMMARY.tag_facets_complete;
    workspace.tagFacetsTruncated = MODERN_OVERVIEW_SUMMARY.tag_facets_truncated;

    renderOverview();

    const metrics = screen.getByRole("region", { name: "知识资产指标" });
    const documentMetric = within(metrics).getByText("知识文档").closest(".stat-card") as HTMLElement;
    const chunkMetric = within(metrics).getByText("目录片段").closest(".stat-card") as HTMLElement;
    const categoryMetric = within(metrics).getByText("逻辑分类").closest(".stat-card") as HTMLElement;
    const tagMetric = within(metrics).getByText("文档标签").closest(".stat-card") as HTMLElement;
    expect(within(documentMetric).getByText("128")).toBeTruthy();
    expect(within(chunkMetric).getByText("20,480")).toBeTruthy();
    expect(within(categoryMetric).getByText("1")).toBeTruthy();
    expect(within(tagMetric).getByText("1")).toBeTruthy();
    expect(screen.getByText("企业目录摘要 · 服务器聚合指标")).toBeTruthy();
    expect(screen.getByText("标签统计为有限扫描结果")).toBeTruthy();

    const recent = screen.getByRole("region", { name: "最近资产" });
    expect(within(recent).getByText("现代摘要资产.pdf")).toBeTruthy();
    expect(screen.queryByText("不应参与摘要的旧列表.pdf")).toBeNull();

    const lifeline = screen.getByRole("list", { name: "知识生命线" });
    const stages = within(lifeline).getAllByRole("listitem");
    expect(stages[0].classList.contains("is-pending")).toBe(true);
    expect(stages[1].classList.contains("is-error")).toBe(true);
    expect(stages[2].classList.contains("is-active")).toBe(true);
    expect(stages[3].classList.contains("is-active")).toBe(true);
    expect(stages.slice(4).every((stage) => stage.classList.contains("is-pending"))).toBe(true);
  });

  it("shows the complete seven-stage knowledge lifeline as an ordered operating process", () => {
    renderOverview();

    const lifeline = screen.getByRole("list", { name: "知识生命线" });
    expect(within(lifeline).getAllByRole("listitem").map((item) => item.textContent?.trim())).toEqual([
      "来源",
      "解析",
      "切片",
      "索引",
      "召回",
      "引用",
      "回答",
    ]);

    const stages = within(lifeline).getAllByRole("listitem");
    expect(stages[0].classList.contains("is-complete")).toBe(true);
    expect(stages[1].classList.contains("is-error")).toBe(true);
    expect(stages[2].classList.contains("is-warning")).toBe(true);
    expect(stages[3].classList.contains("is-active")).toBe(true);
    expect(stages.slice(4).every((stage) => stage.classList.contains("is-pending"))).toBe(true);
    expect(screen.getByText("仅依据文档状态、解析元数据与来源标识推断")).toBeTruthy();
    expect(screen.getByText("召回、引用和回答暂无运行指标")).toBeTruthy();
    expect(screen.queryByText("知识处理链路运行正常")).toBeNull();
  });

  it("keeps every lifeline stage pending for an empty knowledge base", () => {
    workspace.documents = [];
    workspace.status = "empty";

    renderOverview();

    const lifeline = screen.getByRole("list", { name: "知识生命线" });
    expect(within(lifeline).getAllByRole("listitem")).toHaveLength(7);
    expect(
      within(lifeline)
        .getAllByRole("listitem")
        .every((stage) => stage.classList.contains("is-pending")),
    ).toBe(true);
    expect(screen.getByText("暂无文档，生命线等待真实处理记录")).toBeTruthy();
  });

  it("translates waiting and splitting document states into enterprise Chinese labels", () => {
    workspace.documents = [
      {
        id: "doc-waiting",
        name: "等待入库.pdf",
        status: "waiting",
        progress: 0,
        chunk_count: 0,
        doc_type: "pdf",
        error_message: "",
        source_type: "upload",
        source_id: "waiting-source",
        updated_at: "2026-08-24T09:00:00Z",
      },
      {
        id: "doc-splitting",
        name: "正在切片.md",
        status: "splitting",
        progress: 0.4,
        chunk_count: 0,
        doc_type: "markdown",
        error_message: "",
        parser_meta: { parse_ms: 180 },
        source_type: "upload",
        source_id: "splitting-source",
        updated_at: "2026-08-24T08:30:00Z",
      },
    ];

    renderOverview();

    const recent = screen.getByRole("region", { name: "最近资产" });
    expect(within(recent).getByText("等待入库").closest(".t-tag")).not.toBeNull();
    expect(within(recent).getByText("切片中").closest(".t-tag")).not.toBeNull();

    const lifeline = screen.getByRole("list", { name: "知识生命线" });
    const stages = within(lifeline).getAllByRole("listitem");
    expect(stages[1].classList.contains("is-warning")).toBe(true);
    expect(stages[2].classList.contains("is-active")).toBe(true);
    expect(stages[3].classList.contains("is-pending")).toBe(true);
  });

  it("prioritizes actionable work and keeps recent assets visible", () => {
    renderOverview();

    const attention = screen.getByRole("region", { name: "待处理事项" });
    expect(within(attention).getByText("1 个解析失败")).toBeTruthy();
    expect(within(attention).getByText("1 个正在处理")).toBeTruthy();
    expect(within(attention).getByText("报销规范.pdf")).toBeTruthy();

    const recent = screen.getByRole("region", { name: "最近资产" });
    expect(within(recent).getByText("员工手册.md")).toBeTruthy();
    expect(within(recent).getByText("制度/人力 · 12 个片段")).toBeTruthy();
    expect(within(recent).getByText("已完成").closest(".t-tag")).not.toBeNull();
  });

  it("uses navigationIntent-style client navigation for every quick action", () => {
    renderOverview();
    let popstateCount = 0;
    const onPopstate = () => { popstateCount += 1; };
    window.addEventListener("popstate", onPopstate);

    const quickActions = screen.getByRole("region", { name: "快捷操作" });
    const importButton = within(quickActions).getByRole("button", { name: "导入文档" });
    expect(importButton.classList.contains("t-button")).toBe(true);

    fireEvent.click(importButton);

    expect(window.location.pathname).toBe("/documents");
    expect(popstateCount).toBe(1);
    window.removeEventListener("popstate", onPopstate);
  });

  it("preserves the legacy synthetic popstate contract in hash deployment mode", () => {
    window.history.replaceState({}, "", "/#/overview");
    renderOverview();
    const onPopstate = vi.fn();
    window.addEventListener("popstate", onPopstate);

    fireEvent.click(within(screen.getByRole("region", { name: "快捷操作" })).getByRole("button", { name: "导入文档" }));

    expect(window.location.hash).toBe("#/documents");
    expect(onPopstate).toHaveBeenCalled();
    window.removeEventListener("popstate", onPopstate);
  });

  it("keeps refresh as an explicit TDesign operation", () => {
    renderOverview();

    const refresh = screen.getByRole("button", { name: "刷新" });
    expect(refresh.classList.contains("t-button")).toBe(true);
    fireEvent.click(refresh);
    expect(workspace.reload).toHaveBeenCalledTimes(1);
  });
});
