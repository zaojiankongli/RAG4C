// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ContentRecoveryCenter from "./ContentRecoveryCenter";
import PurgeApprovalDialog from "./PurgeApprovalDialog";
import ReleaseLegalHoldDialog from "./ReleaseLegalHoldDialog";
import RestoreDocumentDialog from "./RestoreDocumentDialog";
import RetentionPolicyPanel from "./RetentionPolicyPanel";
import type {
  ContentRecoveryController,
  LegalHold,
  RecoveryEntry,
  RecoveryEntryDetail,
  RecoverySummary,
  RetentionPolicy,
} from "./contentRecoveryTypes";

const entry: RecoveryEntry = {
  id: "recycle-001",
  tenant_id: "tenant-001",
  dataset_id: "dataset-001",
  dataset_label: "产品知识库",
  document_id: "document-001",
  document_label: "API 接入指南.md",
  status: "recycled",
  revision: 7,
  recycle_generation: 1,
  original_lifecycle_state: "active",
  original_retrieval_enabled: true,
  current_retrieval_enabled: false,
  retention_days_snapshot: 30,
  recycled_at: "2026-08-28T08:00:00Z",
  purge_eligible_at: "2026-08-27T08:00:00Z",
  restored_at: null,
  purge_requested_at: null,
  active_hold_count: 0,
  safe_snapshot: { document_type: "markdown", source_kind: "manual_upload" },
};

const heldEntry: RecoveryEntry = {
  ...entry,
  id: "recycle-002",
  document_id: "document-002",
  document_label: "客户合同留存说明.pdf",
  active_hold_count: 1,
};

const summary: RecoverySummary = {
  state: "ready",
  tenant_id: "tenant-001",
  as_of: "2026-08-29T08:00:00Z",
  recycled_count: 24,
  expiring_count: 3,
  held_count: 5,
  purge_pending_count: 2,
  reason_code: null,
};

const policy: RetentionPolicy = {
  id: "policy-001",
  tenant_id: "tenant-001",
  status: "active",
  retention_days: 30,
  auto_purge_enabled: false,
  purge_requires_approval: true,
  revision: 4,
  updated_at: "2026-08-29T08:00:00Z",
  updated_by: "account-001",
};

const hold: LegalHold = {
  id: "hold-001",
  tenant_id: "tenant-001",
  recycle_entry_id: heldEntry.id,
  document_id: heldEntry.document_id,
  status: "active",
  revision: 2,
  reason_code: "legal_review",
  safe_reason: "客户合同争议保全",
  held_at: "2026-08-28T09:00:00Z",
  held_by: "account-002",
  released_at: null,
};

const detail: RecoveryEntryDetail = {
  ...entry,
  events: [
    {
      id: "event-001",
      sequence: 1,
      event_type: "recycled",
      event_digest: "a".repeat(64),
      previous_event_digest: null,
      actor_id: "account-001",
      request_id: "request-001",
      occurred_at: "2026-08-28T08:00:00Z",
      safe_snapshot: { status: "recycled" },
    },
  ],
  holds: [],
  purge_requests: [],
};

function makeController(
  overrides: Partial<ContentRecoveryController> = {},
): ContentRecoveryController {
  const controller: ContentRecoveryController = {
    active: true,
    summary: { status: "ready", value: summary, error: null },
    entries: {
      status: "ready",
      items: [entry],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    detail: { status: "idle", value: null, error: null },
    retentionPolicy: { status: "ready", value: policy, error: null },
    mutation: {
      status: "idle",
      error: null,
      restore: vi.fn().mockResolvedValue({ state: "applied" }),
      applyHold: vi.fn().mockResolvedValue({ state: "applied" }),
      releaseHold: vi.fn().mockResolvedValue({ state: "applied" }),
      requestPurge: vi
        .fn()
        .mockResolvedValue({ state: "applied", approval_request_id: "approval-001" }),
      updatePolicy: vi.fn().mockResolvedValue({ state: "applied" }),
    },
    ...overrides,
  };
  return controller;
}

afterEach(cleanup);

describe("Stage 23 ContentRecoveryCenter", () => {
  it("renders the authoritative header, metrics and RECYCLE-to-PURGE lifecycle rail", () => {
    render(<ContentRecoveryCenter controller={makeController()} tenantLabel="RAG4C 企业" />);

    expect(screen.getByRole("heading", { name: "内容恢复中心" })).toBeTruthy();
    expect(screen.getByText("RAG4C 企业")).toBeTruthy();
    expect(screen.getByRole("region", { name: "回收治理核心指标" })).toBeTruthy();
    const rail = screen.getByRole("list", { name: "内容恢复生命周期" });
    expect(
      within(rail)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("RECYCLE"),
      expect.stringContaining("RETAIN"),
      expect.stringContaining("HOLD / ELIGIBLE"),
      expect.stringContaining("RESTORE / PURGE REQUEST"),
    ]);
  });

  it("renders exactly one entry surface for the requested viewport", () => {
    const { rerender } = render(
      <ContentRecoveryCenter controller={makeController()} mobile={false} />,
    );
    expect(screen.getByTestId("recovery-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("recovery-mobile-cards")).toBeNull();

    rerender(<ContentRecoveryCenter controller={makeController()} mobile />);
    expect(screen.getByTestId("recovery-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("recovery-desktop-table")).toBeNull();
  });

  it("opens detail with the focused entry and returns focus after Escape", async () => {
    const user = userEvent.setup();
    const controller = makeController({
      detail: { status: "ready", value: detail, error: null },
    });
    render(<ContentRecoveryCenter controller={controller} />);

    const openButton = screen.getByRole("button", { name: "查看 API 接入指南.md 详情" });
    openButton.focus();
    await user.click(openButton);
    expect(await screen.findByRole("dialog", { name: "回收条目详情" })).toBeTruthy();
    expect(screen.getByText("IMMUTABLE RECOVERY EVENT CHAIN")).toBeTruthy();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "回收条目详情" })).toBeNull());
    expect(document.activeElement).toBe(openButton);
  });

  it("keeps restore, hold and purge operations actor-driven through controller callbacks", async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<ContentRecoveryCenter controller={controller} />);

    await user.click(screen.getByRole("button", { name: "恢复 API 接入指南.md" }));
    const restoreDialog = await screen.findByRole("dialog", { name: "恢复文档" });
    await user.click(within(restoreDialog).getByRole("checkbox", { name: "我确认恢复此文档" }));
    await user.click(within(restoreDialog).getByRole("button", { name: "确认恢复" }));
    expect(controller.mutation.restore).toHaveBeenCalledWith(entry);

    await user.click(screen.getByRole("button", { name: "为 API 接入指南.md 添加法律保留" }));
    const holdDialog = await screen.findByRole("dialog", { name: "添加法律保留" });
    await user.type(within(holdDialog).getByLabelText("保全说明"), "合同审查期间暂缓清除");
    await user.click(within(holdDialog).getByRole("button", { name: "添加法律保留" }));
    expect(controller.mutation.applyHold).toHaveBeenCalledWith(
      entry,
      expect.objectContaining({
        safe_reason: "合同审查期间暂缓清除",
      }),
    );

    await user.click(screen.getByRole("button", { name: "申请清除 API 接入指南.md" }));
    const purgeDialog = await screen.findByRole("dialog", { name: "申请永久清除审批" });
    expect(within(purgeDialog).getByText("仅创建审批申请，不会批准或执行清除")).toBeTruthy();
    await user.click(
      within(purgeDialog).getByRole("checkbox", { name: "我确认清除申请已满足保留期" }),
    );
    await user.click(within(purgeDialog).getByRole("button", { name: "提交清除审批申请" }));
    expect(controller.mutation.requestPurge).toHaveBeenCalledWith(entry);
  });

  it("shows legal hold release as a separate safe action", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ReleaseLegalHoldDialog visible hold={hold} onClose={vi.fn()} onSubmit={onSubmit} />);

    const dialog = screen.getByRole("dialog", { name: "释放法律保留" });
    await user.click(within(dialog).getByRole("checkbox", { name: "我确认释放此法律保留" }));
    await user.click(within(dialog).getByRole("button", { name: "释放法律保留" }));
    expect(onSubmit).toHaveBeenCalledWith(hold);
  });

  it("blocks purge approval while retention or legal hold prerequisites are not satisfied", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <PurgeApprovalDialog
        visible
        entry={heldEntry}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        purgeEligible={false}
        activeHoldCount={1}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "申请永久清除审批" });
    expect(within(dialog).getByText("保留期尚未满足")).toBeTruthy();
    expect(within(dialog).getByText("存在 1 个有效法律保留")).toBeTruthy();
    const submit = within(dialog).getByText("提交清除审批申请").closest(".t-button") as HTMLElement;
    expect(submit).not.toBeNull();
    expect(submit.hasAttribute("disabled") || submit.className.includes("t-is-disabled")).toBe(
      true,
    );
    await user.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps retention policy controls explicit and immutable in read-only mode", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <RetentionPolicyPanel
        visible
        policy={policy}
        readOnly
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    const panel = screen.getByRole("dialog", { name: "内容保留策略" });
    expect(within(panel).getByText("只读模式下不允许修改策略")).toBeTruthy();
    const save = within(panel).getByText("保存保留策略").closest(".t-button") as HTMLElement;
    expect(save).not.toBeNull();
    expect(save.hasAttribute("disabled") || save.className.includes("t-is-disabled")).toBe(true);
    await user.click(save);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not invent zeroes for unavailable, partial or empty authority", () => {
    const { rerender } = render(
      <ContentRecoveryCenter
        controller={makeController({
          summary: { status: "unavailable", value: null, error: "unavailable" },
          entries: {
            status: "unavailable",
            items: [],
            invalidItemCount: 0,
            nextCursor: null,
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");
    expect(screen.queryByText("0")).toBeNull();

    rerender(
      <ContentRecoveryCenter
        controller={makeController({
          summary: { status: "partial", value: summary, error: null },
          entries: {
            status: "partial",
            items: [],
            invalidItemCount: 2,
            nextCursor: null,
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("部分回收条目无法读取");

    rerender(
      <ContentRecoveryCenter
        controller={makeController({
          entries: {
            status: "ready",
            items: [],
            invalidItemCount: 0,
            nextCursor: null,
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByText("回收站为空")).toBeTruthy();
  });
});

describe("Stage 23 standalone recovery dialogs", () => {
  it("requires an explicit restore confirmation", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<RestoreDocumentDialog visible entry={entry} onClose={vi.fn()} onSubmit={onSubmit} />);
    const dialog = screen.getByRole("dialog", { name: "恢复文档" });
    const submit = within(dialog).getByText("确认恢复").closest(".t-button") as HTMLElement;
    expect(submit).not.toBeNull();
    expect(submit.hasAttribute("disabled") || submit.className.includes("t-is-disabled")).toBe(
      true,
    );
    await user.click(within(dialog).getByRole("checkbox", { name: "我确认恢复此文档" }));
    await user.click(within(dialog).getByRole("button", { name: "确认恢复" }));
    expect(onSubmit).toHaveBeenCalledWith(entry);
  });

  it("renders an unavailable detail without exposing raw unsafe facts", () => {
    const unsafeEntry = { ...entry, safe_snapshot: { credential: "should-not-render" } };
    render(
      <ContentRecoveryCenter
        controller={makeController({
          detail: {
            status: "unavailable",
            value: { ...detail, safe_snapshot: unsafeEntry.safe_snapshot },
            error: null,
          },
        })}
      />,
    );
    expect(screen.queryByText("should-not-render")).toBeNull();
  });
});
