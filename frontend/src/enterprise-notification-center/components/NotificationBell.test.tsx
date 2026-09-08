// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NotificationSummaryState } from "../hooks/useEnterpriseNotifications";
import NotificationBell from "./NotificationBell";

const readySummary: NotificationSummaryState = {
  status: "ready",
  value: {
    state: "ready",
    tenant_id: "tenant-001",
    account_id: "account-001",
    unread_count: 124,
    unread_state: "count",
    as_of: "2026-08-29T02:40:00.000000Z",
    reason_code: null,
  },
  error: null,
};

afterEach(cleanup);

describe("NotificationBell", () => {
  it("shows an exact accessible unread count while capping only the visual badge at 99+", () => {
    const onOpen = vi.fn();
    render(<NotificationBell summary={readySummary} capabilityReady onOpen={onOpen} />);

    const button = screen.getByRole("button", { name: /124.*未读/ });
    expect(button).toBeTruthy();
    expect(screen.getByTestId("notification-bell-badge").textContent).toContain("99+");
    expect(screen.getByTestId("notification-bell-accurate-count").textContent).toContain("124");
  });

  it("does not expose a badge before unread authority is ready", () => {
    render(
      <NotificationBell
        capabilityReady
        summary={{ ...readySummary, status: "unavailable", value: null }}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("notification-bell-badge")).toBeNull();
  });
});
