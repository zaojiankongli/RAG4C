// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type {
  QABatchResult,
  QAImportResult,
  QAKnowledge,
} from "../model/governanceModel";
import QAGovernancePanel from "./QAGovernancePanel";

beforeAll(() => {
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});

afterEach(cleanup);

const pending: QAKnowledge = {
  id: "qa-pending", tenant_id: "tenant-a", dataset_id: "dataset-a", revision: 3,
  question: "How is access approved?", answer: "By the owner.", origin: "manual",
  review_status: "pending", lifecycle_state: "active", retrieval_enabled: false,
  effective_from: null, expires_at: null, source_document_id: "doc-a", source_uri: "", metadata: {},
  created_by: "editor-a", reviewed_by: null, reviewed_at: null,
  created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-25T00:00:00Z",
  alternatives: [{ id: "alt-1", qa_id: "qa-pending", question: "Who approves access?", created_by: "editor-a", created_at: "2026-08-25T00:00:00Z" }],
  negative_questions: [{ id: "neg-1", qa_id: "qa-pending", question: "How to bypass approval?", created_by: "editor-a", created_at: "2026-08-25T00:00:00Z" }],
  content_hash: "hash-1",
  import_batch_id: null,
};
const expired: QAKnowledge = {
  ...pending, id: "qa-expired", revision: 8, question: "Old policy?", origin: "automatic",
  review_status: "approved", lifecycle_state: "expired", retrieval_enabled: false, alternatives: [],
  negative_questions: [], import_batch_id: "qa-import-old",
};

function props(overrides: Record<string, unknown> = {}) {
  return {
    status: "ready" as const,
    items: [pending, expired],
    pageItems: [pending, expired],
    page: 1,
    pageCount: 1,
    pageSize: 10,
    filters: {},
    error: null,
    mutatingId: null,
    setPage: vi.fn(),
    setFilters: vi.fn(),
    refresh: vi.fn(async () => true),
    create: vi.fn(async () => true),
    update: vi.fn(async () => true),
    review: vi.fn(async () => true),
    expire: vi.fn(async () => true),
    restore: vi.fn(async () => true),
    addAlternative: vi.fn(async () => true),
    deleteAlternative: vi.fn(async () => true),
    addNegative: vi.fn(async () => true),
    deleteNegative: vi.fn(async () => true),
    importItems: vi.fn(async () => null as QAImportResult | null),
    batchReview: vi.fn(async () => null as QABatchResult | null),
    batchExpire: vi.fn(async () => null as QABatchResult | null),
    batchRestore: vi.fn(async () => null as QABatchResult | null),
    exportQA: vi.fn(async () => null as string | null),
    ...overrides,
  };
}

describe("QAGovernancePanel", () => {
  it("shows review and lifecycle truth separately in a keyboard-owned table", () => {
    render(<QAGovernancePanel {...props()} />);
    expect(screen.getByRole("group", { name: "QA 筛选" })).toBeTruthy();
    const scrollOwner = screen.getByRole("region", { name: "QA 治理表格，可横向滚动" });
    expect(scrollOwner.getAttribute("tabindex")).toBe("0");
    expect(screen.getByText("待审核")).toBeTruthy();
    expect(screen.getByText("已通过")).toBeTruthy();
    expect(screen.getByText("有效")).toBeTruthy();
    expect(screen.getByText("已过期")).toBeTruthy();
    expect(screen.getByText("每页 10 条")).toBeTruthy();
    expect(screen.getByText(/反例问 1/)).toBeTruthy();
    const expiredRow = screen.getByRole("row", { name: /Old policy/ });
    expect(within(expiredRow).getByRole("button", { name: "恢复 QA" }).tagName).toBe("BUTTON");
  });

  it("creates a manual QA with exact material fields", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    await user.click(screen.getByRole("button", { name: "新建 QA" }));
    fireEvent.change(screen.getByRole("textbox", { name: "问题" }), { target: { value: "New question" } });
    fireEvent.change(screen.getByRole("textbox", { name: "答案" }), { target: { value: "New answer" } });
    await user.click(screen.getByRole("button", { name: "保存 QA" }));

    await waitFor(() => expect(callbacks.create).toHaveBeenCalledWith({
      question: "New question",
      answer: "New answer",
      origin: "manual",
      source_document_id: null,
      source_uri: "",
      metadata: {},
      effective_from: null,
      expires_at: null,
    }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "新建 QA" })));
  });

  it("uses row revision for approval and confirmation-gated expiry", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    const pendingRow = screen.getByRole("row", { name: /How is access approved/ });

    await user.click(within(pendingRow).getByRole("button", { name: "通过 QA" }));
    expect(callbacks.review).toHaveBeenCalledWith("qa-pending", { expected_revision: 3, decision: "approved" });

    await user.click(within(pendingRow).getByRole("button", { name: "过期 QA" }));
    expect(callbacks.expire).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "确认过期" }));
    expect(callbacks.expire).toHaveBeenCalledWith("qa-pending", { expected_revision: 3 });
  });

  it("adds and confirmation-deletes alternatives with the current QA revision", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    const pendingRow = screen.getByRole("row", { name: /How is access approved/ });
    await user.click(within(pendingRow).getByRole("button", { name: "管理替代表述" }));

    fireEvent.change(screen.getByRole("textbox", { name: "新增替代问题" }), { target: { value: "Alternative wording" } });
    await user.click(screen.getByRole("button", { name: "添加替代问题" }));
    expect(callbacks.addAlternative).toHaveBeenCalledWith("qa-pending", { expected_revision: 3, question: "Alternative wording" });

    await user.click(screen.getByRole("button", { name: "删除替代问题 Who approves access?" }));
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    expect(callbacks.deleteAlternative).toHaveBeenCalledWith("qa-pending", "alt-1", 3);
  });

  it("manages negative questions in the same alternatives drawer", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    const pendingRow = screen.getByRole("row", { name: /How is access approved/ });
    await user.click(within(pendingRow).getByRole("button", { name: "管理替代表述" }));

    expect(screen.getByRole("region", { name: "反例问管理" })).toBeTruthy();
    expect(screen.getByText("How to bypass approval?")).toBeTruthy();

    fireEvent.change(screen.getByRole("textbox", { name: "新增反例问" }), { target: { value: "Skip review?" } });
    await user.click(screen.getByRole("button", { name: "添加反例问" }));
    expect(callbacks.addNegative).toHaveBeenCalledWith("qa-pending", { expected_revision: 3, question: "Skip review?" });

    await user.click(screen.getByRole("button", { name: "删除反例问 How to bypass approval?" }));
    await user.click(await screen.findByRole("button", { name: "确认删除" }));
    expect(callbacks.deleteNegative).toHaveBeenCalledWith("qa-pending", "neg-1", 3);
  });

  it("runs batch review/expire/restore against selected rows using revision CAS", async () => {
    const callbacks = props({
      batchReview: vi.fn(async () => ({
        action: "review",
        succeeded: [{ qa_id: "qa-pending", revision: 4, review_status: "approved", lifecycle_state: "active" }],
        failed: [],
        counts: { succeeded: 1, failed: 0 },
      }) as QABatchResult),
      batchExpire: vi.fn(async () => ({
        action: "expire",
        succeeded: [{ qa_id: "qa-pending", revision: 5, review_status: "approved", lifecycle_state: "expired" }],
        failed: [],
        counts: { succeeded: 1, failed: 0 },
      }) as QABatchResult),
      batchRestore: vi.fn(async () => ({
        action: "restore",
        succeeded: [{ qa_id: "qa-expired", revision: 9, review_status: "approved", lifecycle_state: "active" }],
        failed: [],
        counts: { succeeded: 1, failed: 0 },
      }) as QABatchResult),
    });
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);

    await user.click(screen.getByRole("checkbox", { name: "选择 QA How is access approved?" }));
    expect(screen.getByText("已选 1 条")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "批量通过 QA" }));
    expect(callbacks.batchReview).toHaveBeenCalledWith([
      { qa_id: "qa-pending", expected_revision: 3, decision: "approved" },
    ]);

    await user.click(screen.getByRole("checkbox", { name: "选择 QA How is access approved?" }));
    await user.click(screen.getByRole("button", { name: "批量过期 QA" }));
    expect(callbacks.batchExpire).toHaveBeenCalledWith([
      { qa_id: "qa-pending", expected_revision: 3 },
    ]);

    await user.click(screen.getByRole("checkbox", { name: "选择 QA Old policy?" }));
    await user.click(screen.getByRole("button", { name: "批量恢复 QA" }));
    expect(callbacks.batchRestore).toHaveBeenCalledWith([
      { qa_id: "qa-expired", expected_revision: 8 },
    ]);
  });

  it("shows batch partial-failure alert with failed list", async () => {
    const callbacks = props({
      batchReview: vi.fn(async () => ({
        action: "review",
        succeeded: [{ qa_id: "qa-pending", revision: 4, review_status: "approved", lifecycle_state: "active" }],
        failed: [{ index: 0, qa_id: "qa-pending", reason: "revision conflict", code: "ContentConflict" }],
        counts: { succeeded: 1, failed: 1 },
      }) as QABatchResult),
    });
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    await user.click(screen.getByRole("checkbox", { name: "选择 QA How is access approved?" }));
    await user.click(screen.getByRole("button", { name: "批量通过 QA" }));
    expect(await screen.findByText(/批量操作部分失败/)).toBeTruthy();
    expect(screen.getByText(/qa-pending：revision conflict/)).toBeTruthy();
  });

  it("opens import dialog, parses pipe-separated CSV, and shows batch counts + failed reasons", async () => {
    const importResult: QAImportResult = {
      batch_id: "qa-import-42",
      created: [{ id: "qa-new", revision: 1, question: "Q1", content_hash: "h1" }],
      skipped_duplicate: [{ index: 1, reason: "duplicate_existing", content_hash: "h2" }],
      failed: [{ index: 2, question: "Q3", reason: "invalid answer" }],
      counts: { created: 1, skipped_duplicate: 1, failed: 1 },
    };
    const callbacks = props({
      importItems: vi.fn(async () => importResult),
    });
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);

    await user.click(screen.getByRole("button", { name: "导入 QA" }));
    fireEvent.change(screen.getByRole("textbox", { name: "QA 导入内容" }), {
      target: {
        value: "question,answer,alternatives,negative_questions\nQ1,A1,Alt1|Alt2,Neg1\nQ2,A2,,",
      },
    });
    await user.click(screen.getByRole("button", { name: "确认导入 QA" }));

    await waitFor(() => expect(callbacks.importItems).toHaveBeenCalledWith({
      origin: "import",
      items: [
        {
          question: "Q1",
          answer: "A1",
          alternatives: ["Alt1", "Alt2"],
          negative_questions: ["Neg1"],
          source_uri: "",
          metadata: {},
        },
        {
          question: "Q2",
          answer: "A2",
          alternatives: [],
          negative_questions: [],
          source_uri: "",
          metadata: {},
        },
      ],
    }));
  });

  it("rejects invalid import rows before calling the API", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    await user.click(screen.getByRole("button", { name: "导入 QA" }));
    fireEvent.change(screen.getByRole("textbox", { name: "QA 导入内容" }), {
      target: { value: "question,answer\nOnlyOneColumn," },
    });
    await user.click(screen.getByRole("button", { name: "确认导入 QA" }));
    expect(callbacks.importItems).not.toHaveBeenCalled();
    expect(await screen.findByText("导入内容无效")).toBeTruthy();
  });

  it("shows empty-state CTA for import and create when items are empty", () => {
    render(<QAGovernancePanel {...props({ items: [], pageItems: [] })} />);
    expect(screen.getAllByRole("button", { name: "导入 QA" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "新建 QA" }).length).toBeGreaterThan(0);
  });

  it("exports current-filter QA as JSON/CSV via exportQA", async () => {
    const callbacks = props({
      exportQA: vi.fn(async () => '{"items":[],"count":0}'),
    });
    const user = userEvent.setup();
    render(<QAGovernancePanel {...callbacks} />);
    await user.click(screen.getByRole("button", { name: "导出 QA JSON" }));
    expect(callbacks.exportQA).toHaveBeenCalledWith("json");
    await user.click(screen.getByRole("button", { name: "导出 QA CSV" }));
    expect(callbacks.exportQA).toHaveBeenCalledWith("csv");
  });

  it("hides mutations for terminal lifecycle rows and warns when results may be truncated", () => {
    const terminal = { ...pending, id: "qa-deleting", lifecycle_state: "deleting" as const };
    render(<QAGovernancePanel {...props()} items={[terminal]} pageItems={[terminal]} truncated />);
    const row = screen.getByRole("row", { name: /How is access approved/ });
    expect(within(row).queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText("结果可能被截断")).toBeTruthy();
  });

  it("disables all QA mutations for archived read-only datasets", () => {
    render(<QAGovernancePanel {...props()} readOnly />);
    expect(screen.queryByRole("button", { name: "新建 QA" })).toBeNull();
    const row = screen.getByRole("row", { name: /How is access approved/ });
    expect(within(row).queryAllByRole("button")).toHaveLength(0);
  });


  it("associates invalid QA dates with the active dialog fields", async () => {
    const user=userEvent.setup(); render(<QAGovernancePanel {...props()} />); await user.click(screen.getByRole("button",{name:"新建 QA"}));
    fireEvent.change(screen.getByRole("textbox",{name:"问题"}),{target:{value:"Q"}}); fireEvent.change(screen.getByRole("textbox",{name:"答案"}),{target:{value:"A"}});
    fireEvent.change(screen.getByRole("textbox",{name:/生效时间/}),{target:{value:"bad"}}); await user.click(screen.getByRole("button",{name:"保存 QA"}));
    expect(screen.getByRole("textbox",{name:/生效时间/}).getAttribute("aria-invalid")).toBe("true");
  });


  it("disables new QA and all alternative mutations while another mutation is busy", async () => {
    const user=userEvent.setup(); render(<QAGovernancePanel {...props()} mutatingId="qa-pending" />);
    expect(screen.getByRole("button",{name:"新建 QA"}).hasAttribute("disabled")).toBe(true);
    cleanup(); render(<QAGovernancePanel {...props()} />); const row=screen.getByRole("row",{name:/How is access approved/}); await user.click(within(row).getByRole("button",{name:"管理替代表述"}));
    cleanup(); render(<QAGovernancePanel {...props()} mutatingId="qa-pending" />); const busyRow=screen.getByRole("row",{name:/How is access approved/}); expect(within(busyRow).getByRole("button",{name:"管理替代表述"}).hasAttribute("disabled")).toBe(true);
  });


  it("associates empty QA and invalid metadata errors to their controls", async () => {
    const user=userEvent.setup(); render(<QAGovernancePanel {...props()} />); await user.click(screen.getByRole("button",{name:"新建 QA"})); await user.click(screen.getByRole("button",{name:"保存 QA"}));
    expect(screen.getByRole("textbox",{name:"问题"}).getAttribute("aria-invalid")).toBe("true"); expect(screen.getByRole("textbox",{name:"答案"}).getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(screen.getByRole("textbox",{name:"问题"}),{target:{value:"Q"}}); fireEvent.change(screen.getByRole("textbox",{name:"答案"}),{target:{value:"A"}}); fireEvent.change(screen.getByRole("textbox",{name:/替换元数据/}),{target:{value:"[]"}}); await user.click(screen.getByRole("button",{name:"保存 QA"}));
    expect(screen.getByRole("textbox",{name:/替换元数据/}).getAttribute("aria-invalid")).toBe("true");
  });

});
