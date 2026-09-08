// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationCenter from "./NotificationCenter";
import { makeDetail, makeHook } from "./notificationCenterTestUtils";

afterEach(cleanup);

describe("NotificationCenter", () => {
  it("uses a named region instead of introducing a nested main landmark", () => {
    render(<NotificationCenter controller={makeHook()} capabilityReady mobile={true} />);

    expect(screen.queryByRole("main")).toBeNull();
    expect(screen.getByRole("region", { name: "消息中心" })).toBeTruthy();
  });

  it("renders the enterprise inbox shell and Signal Routing Rail without unsafe navigation", async () => {
    const user = userEvent.setup();
    const hook = makeHook({
      detail: { status: "ready", value: makeDetail(), error: null, load: vi.fn() },
    });
    const onHandoff = vi.fn();

    render(
      <NotificationCenter
        controller={hook}
        capabilityReady
        onHandoff={onHandoff}
        mobile={false}
        readOnly={false}
      />,
    );

    expect(screen.getByRole("heading", { name: "消息中心" })).toBeTruthy();
    expect(screen.getByLabelText("Signal Routing Rail")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Inbox" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Subscriptions" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Activity" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /12.*未读/ })).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "Subscriptions" }));
    expect(screen.getByText("站内通知订阅")).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByText("通知活动链")).toBeTruthy();
  });

  it("opens a detail drawer from an inbox item but leaves receipt state unchanged", async () => {
    const user = userEvent.setup();
    const hook = makeHook({
      detail: {
        status: "ready",
        value: makeDetail(),
        error: null,
        load: vi.fn().mockResolvedValue(true),
      },
    });

    render(<NotificationCenter controller={hook} capabilityReady mobile={true} />);
    await user.click(screen.getByRole("button", { name: "查看通知 notification-001" }));

    expect(screen.getByRole("dialog", { name: "通知详情" })).toBeTruthy();
    expect(hook.mutation.markRead).not.toHaveBeenCalled();
  });
});
