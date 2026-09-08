// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  QualityOperationsMutationDialogsProps,
  QualityOperationsQueueAuthoritySummary,
} from "./QualityOperationsMutationDialogs";
import QualityOperationsMutationDialogs from "./QualityOperationsMutationDialogs";

const queueAuthority: QualityOperationsQueueAuthoritySummary = {
  releaseId: "release-42",
  channelId: "channel-production",
  releaseRole: "active",
  baselineId: "baseline-42",
  policyId: "policy-42",
  expectedPolicyRevision: 8,
  sloPolicyId: "slo-policy-42",
  expectedSloPolicyRevision: 11,
  expectedManifestDigest: "a".repeat(64),
  expectedEvidenceDigest: "b".repeat(64),
  expectedChannelRevision: 19,
  trigger: "certification_warning",
};

function renderDialog(
  overrides: Partial<QualityOperationsMutationDialogsProps> = {},
): ReturnType<typeof render> {
  const props: QualityOperationsMutationDialogsProps = {
    visible: true,
    mode: "acknowledge",
    resourceLabel: "Production / release-42",
    revision: 7,
    status: "open",
    readOnly: false,
    loading: false,
    error: null,
    queueAuthority: null,
    onClose: vi.fn(),
    onAcknowledge: vi.fn(),
    onResolve: vi.fn(),
    onSuppress: vi.fn(),
    onQueue: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  return render(<QualityOperationsMutationDialogs {...props} />);
}

afterEach(cleanup);

describe("QualityOperationsMutationDialogs", () => {
  it("renders one controlled TDesign dialog and submits a safe acknowledgement payload", async () => {
    const user = userEvent.setup();
    const onAcknowledge = vi.fn();
    renderDialog({ onAcknowledge });

    const dialog = screen.getByRole("dialog", { name: "确认质量告警" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect((within(dialog).getByLabelText("revision") as HTMLInputElement).value).toBe("7");
    expect(within(dialog).getByLabelText("revision").hasAttribute("readonly")).toBe(true);
    expect((within(dialog).getByLabelText("status") as HTMLInputElement).value).toBe("open");
    expect(within(dialog).getByLabelText("status").hasAttribute("readonly")).toBe(true);

    await user.type(within(dialog).getByLabelText("reason"), "证据接手并进入运营跟踪");
    await user.type(within(dialog).getByLabelText("comment"), "仅记录安全运营事实");
    await user.click(within(dialog).getByRole("button", { name: "确认告警" }));

    expect(onAcknowledge).toHaveBeenCalledTimes(1);
    expect(onAcknowledge).toHaveBeenCalledWith({
      expectedRevision: 7,
      reason: "证据接手并进入运营跟踪",
      comment: "仅记录安全运营事实",
    });
  });

  it("routes resolve and cancel to their mode-specific callbacks without mutating authority facts", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = renderDialog({ mode: "resolve", onResolve });

    let dialog = screen.getByRole("dialog", { name: "解决质量告警" });
    await user.type(within(dialog).getByLabelText("reason"), "事实已恢复并完成复核");
    await user.click(within(dialog).getByRole("button", { name: "解决告警" }));
    expect(onResolve).toHaveBeenCalledWith({ expectedRevision: 7, reason: "事实已恢复并完成复核" });

    rerender(
      <QualityOperationsMutationDialogs
        visible
        mode="cancel"
        resourceLabel="Production / release-42"
        revision={7}
        status="pending"
        readOnly={false}
        loading={false}
        error={null}
        queueAuthority={null}
        onClose={vi.fn()}
        onAcknowledge={vi.fn()}
        onResolve={vi.fn()}
        onSuppress={vi.fn()}
        onQueue={vi.fn()}
        onCancel={onCancel}
      />,
    );
    dialog = screen.getByRole("dialog", { name: "取消再认证任务" });
    expect((within(dialog).getByLabelText("revision") as HTMLInputElement).value).toBe("7");
    expect((within(dialog).getByLabelText("status") as HTMLInputElement).value).toBe("pending");
    await user.type(within(dialog).getByLabelText("reason"), "任务已由其他流程接管");
    await user.click(within(dialog).getByRole("button", { name: "取消再认证" }));
    expect(onCancel).toHaveBeenCalledWith({
      expectedStatus: "pending",
      reason: "任务已由其他流程接管",
    });
  });

  it("requires a future ISO timestamp before suppressing an alert", async () => {
    const user = userEvent.setup();
    const onSuppress = vi.fn();
    renderDialog({ mode: "suppress", onSuppress });
    const dialog = screen.getByRole("dialog", { name: "抑制质量告警" });
    // 时间戳动态生成：硬编码的"未来"日期会随墙钟推移变成过去（时间炸弹）。
    // 过去用固定远古时刻，未来用 now+1h，格式对齐校验的 ISO 毫秒 Z 形态。
    const pastTimestamp = "2020-01-01T00:00:00.000Z";
    const futureTimestamp = new Date(Date.now() + 3_600_000)
      .toISOString()
      .replace(/\.\d{3}Z$/, ".000Z");

    await user.type(within(dialog).getByLabelText("reason"), "安排窗口内处理");
    await user.type(within(dialog).getByLabelText("suppressedUntil"), pastTimestamp);
    await user.click(within(dialog).getByRole("button", { name: "抑制告警" }));
    expect(onSuppress).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("未来 ISO 时间");

    await user.clear(within(dialog).getByLabelText("suppressedUntil"));
    await user.type(within(dialog).getByLabelText("suppressedUntil"), futureTimestamp);
    await user.click(within(dialog).getByRole("button", { name: "抑制告警" }));
    expect(onSuppress).toHaveBeenCalledWith({
      expectedRevision: 7,
      reason: "安排窗口内处理",
      suppressedUntil: futureTimestamp,
    });
  });

  it("renders server authority facts for queue mode and forwards them without browser digest calculation", async () => {
    const user = userEvent.setup();
    const onQueue = vi.fn();
    renderDialog({ mode: "queue", queueAuthority, onQueue });
    const dialog = screen.getByRole("dialog", { name: "排队再认证" });

    expect(within(dialog).getByText("release-42")).toBeTruthy();
    expect(within(dialog).getByText("channel-production")).toBeTruthy();
    expect(within(dialog).getByText("baseline-42")).toBeTruthy();
    expect(within(dialog).getByText("服务端 authority 摘要")).toBeTruthy();
    expect(within(dialog).getByText(`sha256 · ${"a".repeat(64)}`)).toBeTruthy();

    await user.type(within(dialog).getByLabelText("reason"), "证书即将到期，排队再认证");
    await user.click(within(dialog).getByRole("button", { name: "排队再认证" }));
    expect(onQueue).toHaveBeenCalledWith({
      ...queueAuthority,
      reason: "证书即将到期，排队再认证",
    });
  });

  it("does not submit or enable mutation controls in read-only or loading states", async () => {
    const user = userEvent.setup();
    const onAcknowledge = vi.fn();
    const { rerender } = renderDialog({ readOnly: true, onAcknowledge });
    let dialog = screen.getByRole("dialog", { name: "确认质量告警" });
    expect((within(dialog).getByLabelText("reason") as HTMLTextAreaElement).disabled).toBe(true);
    expect(
      (within(dialog).getByRole("button", { name: "确认告警" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    rerender(
      <QualityOperationsMutationDialogs
        visible
        mode="acknowledge"
        resourceLabel="Production / release-42"
        revision={7}
        status="open"
        readOnly={false}
        loading
        error={null}
        queueAuthority={null}
        onClose={vi.fn()}
        onAcknowledge={onAcknowledge}
      />,
    );
    dialog = screen.getByRole("dialog", { name: "确认质量告警" });
    expect((within(dialog).getByLabelText("reason") as HTMLTextAreaElement).disabled).toBe(true);
    expect(
      (within(dialog).getByRole("button", { name: "确认告警" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    await user.click(within(dialog).getByRole("button", { name: "确认告警" }));
    expect(onAcknowledge).not.toHaveBeenCalled();
  });

  it("rejects protected text before it reaches a callback or safe projection", async () => {
    const user = userEvent.setup();
    const onAcknowledge = vi.fn();
    renderDialog({ onAcknowledge });
    const dialog = screen.getByRole("dialog", { name: "确认质量告警" });

    fireEvent.change(within(dialog).getByLabelText("reason"), {
      target: { value: "ticket=opaque-secret-ticket" },
    });
    expect((within(dialog).getByLabelText("reason") as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("受保护信息");
    expect(screen.queryByText("opaque-secret-ticket")).toBeNull();

    await user.type(within(dialog).getByLabelText("reason"), "已完成安全复核");
    fireEvent.change(within(dialog).getByLabelText("comment"), {
      target: { value: "query=select * from secrets" },
    });
    expect((within(dialog).getByLabelText("comment") as HTMLTextAreaElement).value).toBe("");
    await user.click(within(dialog).getByRole("button", { name: "确认告警" }));
    expect(onAcknowledge).not.toHaveBeenCalled();
  });

  it("keeps close handling single-shot while TDesign owns Escape and focus management", () => {
    const onClose = vi.fn();
    renderDialog({ onClose });
    const dialog = screen.getByRole("dialog", { name: "确认质量告警" });
    const closeButton = within(dialog).getByRole("button", { name: "取消" });

    fireEvent.click(closeButton);
    fireEvent.click(closeButton);
    fireEvent.keyDown(dialog, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
