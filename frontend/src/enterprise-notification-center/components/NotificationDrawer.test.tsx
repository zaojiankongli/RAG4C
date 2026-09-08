// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationDrawer from "./NotificationDrawer";
import { makeHook } from "./notificationCenterTestUtils";

const handoff = vi.fn();

afterEach(() => {
  cleanup();
  handoff.mockReset();
});

describe("NotificationDrawer", () => {
  it("renders one desktop table, switches the four inbox scopes, and hands off without marking read", async () => {
    const user = userEvent.setup();
    const hook = makeHook();
    const onOpenDetail = vi.fn();

    render(
      <NotificationDrawer
        controller={hook}
        visible
        onClose={vi.fn()}
        onOpenDetail={onOpenDetail}
        onHandoff={handoff}
        mobile={false}
      />,
    );

    expect(screen.getByTestId("notification-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("notification-mobile-cards")).toBeNull();
    expect(screen.getByRole("tab", { name: "未读" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "全部" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "质量" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "审批" })).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "审批" }));
    expect(screen.getByText("approval_pending_for_me")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "查看通知 notification-approval-001" }));
    expect(onOpenDetail).toHaveBeenCalledTimes(1);
    expect(hook.mutation.markRead).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("button", { name: "打开通知来源 notification-approval-001" }),
    );
    expect(handoff).toHaveBeenCalledWith(
      expect.objectContaining({
        notificationId: "notification-approval-001",
        route: expect.objectContaining({ code: "enterprise_approval" }),
      }),
    );
  });

  it("uses mobile cards only and sends one server-computed bulk-read mutation", async () => {
    const user = userEvent.setup();
    const hook = makeHook();

    render(
      <NotificationDrawer controller={hook} visible onClose={vi.fn()} onHandoff={handoff} mobile />,
    );

    expect(screen.getByTestId("notification-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("notification-desktop-table")).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "选择当前页全部通知" }));
    await user.click(screen.getByRole("button", { name: "批量标记已读" }));

    expect(hook.mutation.bulkRead).toHaveBeenCalledWith({
      items: [{ notificationId: "notification-001", expectedRevision: 3 }],
      reason: "通知中心显式标记为已读",
    });
  });

  it("renders partial, unavailable, and read-only states without enabling mutation controls", () => {
    const hook = makeHook({
      unread: {
        status: "partial",
        items: [],
        nextCursor: null,
        invalidItemCount: 2,
        error: new Error("unsafe backend detail"),
      },
    });
    render(
      <NotificationDrawer
        controller={hook}
        visible
        readOnly
        onClose={vi.fn()}
        onHandoff={handoff}
        mobile={true}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("部分通知无法读取");
    expect(screen.getByText("只读模式")).toBeTruthy();
    const bulkButton = screen.getByLabelText("批量标记已读");
    expect(bulkButton.getAttribute("disabled")).toBe("");
    expect(bulkButton.classList.contains("t-is-disabled")).toBe(true);
    expect(screen.queryByText("unsafe backend detail")).toBeNull();
  });
});
