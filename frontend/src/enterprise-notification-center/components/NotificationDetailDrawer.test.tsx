// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationDetailDrawer from "./NotificationDetailDrawer";
import { makeDetail, makeHook } from "./notificationCenterTestUtils";

afterEach(cleanup);

describe("NotificationDetailDrawer", () => {
  it("shows immutable event facts and explicit actions without auto-reading or rendering unsafe fields", async () => {
    const user = userEvent.setup();
    const detail = makeDetail();
    const hook = makeHook();
    const onHandoff = vi.fn();
    const onClose = vi.fn();

    render(
      <NotificationDetailDrawer
        visible
        state={{ status: "ready", value: detail, error: null, load: vi.fn() }}
        readOnly={false}
        onClose={onClose}
        onHandoff={onHandoff}
        onMarkRead={vi.fn()}
        onMarkUnread={vi.fn()}
        onArchive={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "通知详情" })).toBeTruthy();
    expect(screen.getByText("certification_expired")).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText("materialized")).toBeTruthy();
    expect(screen.getByText("marked_read")).toBeTruthy();
    expect(screen.getByText("notification-001")).toBeTruthy();
    expect(screen.queryByText(/password|token|ticket|query|credential/i)).toBeNull();
    expect(hook.mutation.markRead).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "打开通知来源" }));
    expect(onHandoff).toHaveBeenCalledWith(
      expect.objectContaining({
        route: expect.objectContaining({ code: "knowledge_quality_operations" }),
      }),
    );
  });

  it("keeps mutation actions disabled in read-only mode and returns focus through the close callback", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const returnFocus = document.createElement("button");
    returnFocus.setAttribute("aria-label", "notification entry");
    document.body.append(returnFocus);
    returnFocus.focus();

    render(
      <NotificationDetailDrawer
        visible
        state={{ status: "ready", value: makeDetail(), error: null, load: vi.fn() }}
        readOnly
        onClose={onClose}
        returnFocusRef={{ current: returnFocus }}
      />,
    );

    const markReadButton = screen.getByLabelText("标记已读");
    const archiveButton = screen.getByLabelText("归档通知");
    expect(markReadButton).toBeTruthy();
    expect(archiveButton).toBeTruthy();
    expect(markReadButton.getAttribute("disabled")).toBe("");
    expect(markReadButton.classList.contains("t-is-disabled")).toBe(true);
    expect(archiveButton.getAttribute("disabled")).toBe("");
    expect(archiveButton.classList.contains("t-is-disabled")).toBe(true);
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
