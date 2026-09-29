// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import SafeTracePanel from "./SafeTracePanel";

afterEach(cleanup);

describe("SafeTracePanel", () => {
  it("does not render an empty trace disclosure", () => {
    const { container } = render(<SafeTracePanel traces={[]} />);

    expect(container.firstChild).toBeNull();
  });

  it("uses the shared Collapse facade and keeps traces closed until opened", () => {
    const { container } = render(
      <SafeTracePanel traces={["route selected", "fallback avoided"]} />,
    );

    const root = container.querySelector(".rag-collapse");
    expect(root).not.toBeNull();
    expect(root?.classList.contains("is-borderless")).toBe(true);

    const trigger = screen.getByRole("button", { name: "安全执行 Trace（2）" });
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    const panel = document.getElementById(panelId!);
    expect(panel?.hasAttribute("hidden")).toBe(true);
    expect(panel).not.toBeNull();
    expect(within(panel as HTMLElement).getByText("route selected")).toBeTruthy();
    expect(within(panel as HTMLElement).getByText("fallback avoided")).toBeTruthy();
    expect(panel?.querySelector(".rq-traces")).not.toBeNull();

    fireEvent.click(trigger);

    expect(panel?.hasAttribute("hidden")).toBe(false);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });
});
