// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UseParseInterventionResult } from "./hooks/useParseIntervention";

const hook = vi.hoisted(() => ({ useParseIntervention: vi.fn() }));
vi.mock("./hooks/useParseIntervention", () => hook);

import ParseInterventionWorkspace from "./ParseInterventionWorkspace";

const baseChunk = {
  chunk_id: "chunk-1",
  doc_id: "doc-1",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  document_revision: 7,
  text: "Current policy body",
  text_hash: "hash",
  content_revision: 4,
  desired_index_revision: 5,
  indexed_revision: 4,
  index_status: "pending",
  projection_pending: true,
  enabled: true,
  chunk_role: "flat",
  seq: 0,
  page: 1,
  heading: "Policy",
  context: "Employee handbook policy context",
  char_count: 19,
  metadata: { heading: "Policy", page: 1, api_key: "never-render" },
};

function controller(overrides: Partial<UseParseInterventionResult> = {}): UseParseInterventionResult {
  return {
    status: "ready",
    document: {
      id: "doc-1",
      name: "Handbook.pdf",
      tenant_id: "tenant-a",
      dataset_id: "dataset-a",
      status: "completed",
      status_detail: "",
      progress: 1,
      chunk_count: 2,
      doc_type: "pdf",
      error_message: "",
      file_path: "C:/secret/credentials/Handbook.pdf",
      file_hash: "hash",
      updated_at: null,
      mutation_generation: 7,
      source_uri: "https://user:secret@example.com/private?token=abc",
      source_type: "github",
      parser_meta: {
        engine: "vision",
        pdf_type: "scanned",
        chunking_mode: "parent_child",
        chunking_reason: "文本 480 字或存在 8 个版面块，超出简单文档条件（阈值 4000）→ parent_child",
        chunking_reason_code: "complex_or_structured",
        chunking_decision: {
          doc_type: "pdf",
          text_chars: 480,
          layout_blocks: 8,
          simple_max_chars: 4000,
          configured_mode: "auto",
        },
        page_count: 2,
        layout_blocks: 8,
        text_chars: 480,
        total_ms: 24,
        stage_ms: { parse: 12, split: 4, embed: 8 },
      },
    },
    chunks: [
      baseChunk,
      { ...baseChunk, chunk_id: "chunk-2", seq: 1, page: 2, heading: "Benefits", text: "Benefits body", content_revision: 2, desired_index_revision: 2, indexed_revision: 2, index_status: "ready", projection_pending: false },
    ],
    authorityMode: "active",
    total: 2,
    knownParents: new Set<string>(),
    missingParents: new Set<string>(),
    hasMore: false,
    loadingMore: false,
    loadMore: vi.fn(async () => true),
    orphanDraft: null,
    canRebaseOrphan: false,
    discardOrphanDraft: vi.fn(),
    rebaseOrphanDraft: vi.fn(),
    selected: baseChunk,
    selectedId: "chunk-1",
    query: "",
    filters: { lifecycle: "all", projection: "all", relation: "all", warningsOnly: false },
    draft: "Current policy body",
    reason: "",
    expectedRevision: 4,
    dirty: false,
    error: "",
    conflict: null,
    receipt: null,
    mutating: false,
    setQuery: vi.fn(),
    setFilters: vi.fn(),
    setDraft: vi.fn(),
    setReason: vi.fn(),
    selectChunk: vi.fn(),
    resetDraft: vi.fn(),
    reload: vi.fn(async () => true),
    submit: vi.fn(async () => true),
    removeSelected: vi.fn(async () => true),
    adoptServer: vi.fn(),
    continueDraft: vi.fn(),
    dismissReceipt: vi.fn(),
    selectedProjection: { state: "pending", label: "投影待处理", detail: "目标 Revision 5，已索引 4" },
    ...overrides,
  } as UseParseInterventionResult;
}

function setMobile(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: "(max-width: 720px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  setMobile(false);
  hook.useParseIntervention.mockReturnValue(controller());
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("ParseInterventionWorkspace", () => {
  it("renders three desktop panes, the Knowledge Lifeline, and honest capability gaps", () => {
    const { container } = render(
      <ParseInterventionWorkspace
        scope={{ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-1" }}
        online
        onReturn={vi.fn()}
        onNavigateOperator={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "解析上下文" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "切片列表" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "切片编辑器" })).toBeTruthy();
    expect(screen.getByLabelText("Knowledge Lifeline")).toBeTruthy();
    expect(screen.getByText("切分策略")).toBeTruthy();
    expect(screen.getByText("按章节结构")).toBeTruthy();
    expect(screen.getByText("决策理由")).toBeTruthy();
    expect(screen.getByText(/文本 480 字或存在 8 个版面块/)).toBeTruthy();
    expect(screen.getByText("决策依据")).toBeTruthy();
    expect(screen.getByText(/类型 pdf · 480 字 · 8 版面块 · 阈值 4000/)).toBeTruthy();
    expect(screen.getByText("当前没有原始文档预览能力")).toBeTruthy();
    expect(screen.getByText("当前没有切片 Revision 历史接口")).toBeTruthy();
    expect(screen.getByText("当前 API 不持久化修改原因")).toBeTruthy();
    expect((screen.getByRole("button", { name: "提交修改" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("region", { name: "修改差异预览" }).tabIndex).toBe(0);
    expect(container.textContent).not.toContain("C:/secret");
    expect(container.textContent).not.toContain("never-render");
    expect(container.textContent).not.toContain("token=abc");
    expect(container.textContent).toContain("https://example.com/private");
  });

  it("uses honest one-pane tabs on a 375px viewport", () => {
    setMobile(true);
    render(
      <ParseInterventionWorkspace
        scope={{ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-1" }}
        online
        onReturn={vi.fn()}
        onNavigateOperator={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: "上下文" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "切片" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "编辑" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "解析上下文" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "切片编辑器" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "编辑" }));
    expect(screen.getByRole("region", { name: "切片编辑器" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "解析上下文" })).toBeNull();
    const editorTab = screen.getByRole("tab", { name: "编辑" });
    expect(editorTab.id).toBe("parse-tab-editor");
    expect(editorTab.getAttribute("aria-controls")).toBe("parse-panel-editor");
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe("parse-tab-editor");
    fireEvent.keyDown(editorTab.closest("[role=tablist]") as HTMLElement, { key: "Home" });
    expect(screen.getByRole("tab", { name: "上下文" }).getAttribute("tabindex")).toBe("0");
  });

  it("supports keyboard chunk selection and one top-right commit action", () => {
    const state = controller({ dirty: true, draft: "Changed body", reason: "修正 OCR" });
    hook.useParseIntervention.mockReturnValue(state);
    render(
      <ParseInterventionWorkspace
        scope={{ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-1" }}
        online
        onReturn={vi.fn()}
        onNavigateOperator={vi.fn()}
      />,
    );

    const list = screen.getByRole("listbox", { name: "文档切片" });
    const activeId = list.getAttribute("aria-activedescendant");
    expect(activeId).toBeTruthy();
    expect(document.getElementById(String(activeId))).not.toBeNull();
    expect(screen.getAllByRole("option").every((option) => option.tagName !== "BUTTON")).toBe(true);
    fireEvent.keyDown(list, { key: "ArrowDown" });
    expect(state.selectChunk).toHaveBeenCalledWith("chunk-2");
    expect(screen.getAllByRole("button", { name: "提交修改" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    expect(state.submit).toHaveBeenCalledTimes(1);
  });

  it("shows durable projection guidance and conflict recovery actions", () => {
    const navigate = vi.fn();
    const state = controller({
      orphanDraft: { chunkId: "chunk-1", originalContent: "Current policy body", draft: "mine", reason: "reason", baseRevision: 4, server: { ...baseChunk, text: "theirs", content_revision: 5 }, resolution: "conflict" },
      canRebaseOrphan: true,
      receipt: {
        kind: "edit",
        authorityAccepted: true,
        authorityMode: "active",
        projectionState: "pending",
        operationIds: ["op-1234567890-secret"],
        reason: "reason",
        message: "权威切片已提交；Milvus/图谱投影操作已进入持久队列，仍需确认收敛。",
      },
    });
    hook.useParseIntervention.mockReturnValue(state);
    render(
      <ParseInterventionWorkspace
        scope={{ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-1" }}
        online
        onReturn={vi.fn()}
        onNavigateOperator={navigate}
      />,
    );

    expect(screen.getByText(/投影操作已进入持久队列/)).toBeTruthy();
    expect(screen.queryByText("投影完成")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "基于新 Revision 继续修改" }));
    expect(state.rebaseOrphanDraft).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "前往一致性控制台" }));
    expect(navigate).toHaveBeenCalledWith("consistency");
  });
});
