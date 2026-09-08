// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationSubscriptionPanel from "./NotificationSubscriptionPanel";
import { makeHook } from "./notificationCenterTestUtils";

afterEach(cleanup);

describe("NotificationSubscriptionPanel", () => {
  it("renders one responsive subscription surface and updates through an explicit TDesign dialog", async () => {
    const user = userEvent.setup();
    const hook = makeHook();

    render(<NotificationSubscriptionPanel controller={hook} mobile={false} readOnly={false} />);
    expect(screen.getByText("站内通知订阅")).toBeTruthy();
    expect(screen.getByTestId("notification-subscription-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("notification-subscription-mobile-cards")).toBeNull();

    await user.click(screen.getByRole("button", { name: "调整质量订阅" }));
    expect(screen.getByRole("dialog", { name: "调整站内通知订阅" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "订阅" }));
    await user.click(screen.getByRole("button", { name: "保存订阅设置" }));

    expect(hook.mutation.updateSubscription).toHaveBeenCalledWith(
      "subscription-quality-001",
      expect.objectContaining({
        expectedRevision: 6,
        preference: "subscribed",
        minimumSeverity: "warning",
        mutedUntil: null,
        reason: "通知中心显式调整订阅",
      }),
      undefined,
    );
  });

  it("uses mobile cards only and keeps unavailable/read-only states explicit", () => {
    const hook = makeHook({
      subscriptions: {
        status: "unavailable",
        items: [],
        nextCursor: null,
        invalidItemCount: 0,
        error: new Error("secret token should not render"),
        load: vi.fn().mockResolvedValue(false),
      },
    });

    render(<NotificationSubscriptionPanel controller={hook} mobile readOnly />);
    expect(screen.getByTestId("notification-subscription-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("notification-subscription-desktop-table")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("订阅权威暂不可用");
    expect(screen.queryByText(/secret token/i)).toBeNull();
  });
});
