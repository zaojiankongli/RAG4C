// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "collapse",
  },
}));

import { Collapse } from "./index";

afterEach(cleanup);

describe("ui/Collapse TDesign adapter", () => {
  it("uses the normalized items contract and preserves the TDesign change context", () => {
    const onChange = vi.fn();
    const { container, rerender } = render(
      <Collapse
        accordion
        value={[]}
        items={[
          { key: 7, label: "数值 Trace", children: <p>数值内容</p> },
          { key: "7", label: "重复 Trace", children: <p>重复内容</p> },
        ]}
        onChange={onChange}
      />,
    );

    expect(container.querySelector(".t-collapse")).not.toBeNull();
    expect(container.querySelectorAll(".t-collapse-panel")).toHaveLength(1);
    const trigger = screen.getByRole("button", { name: "数值 Trace" });
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(panelId!)?.getAttribute("role")).toBe("region");
    expect(document.getElementById(panelId!)?.hasAttribute("hidden")).toBe(true);
    expect(screen.queryByText("重复 Trace")).toBeNull();

    fireEvent.keyDown(trigger, { key: "Enter" });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(
      ["7"],
      expect.objectContaining({ e: expect.anything() }),
    );

    rerender(
      <Collapse
        accordion
        value={["7"]}
        items={[{ key: 7, label: "数值 Trace", children: <p>数值内容</p> }]}
        onChange={onChange}
      />,
    );
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(panelId!)?.hasAttribute("hidden")).toBe(false);
  });

  it("supports uncontrolled default values and Space-key activation", () => {
    const onChange = vi.fn();
    render(
      <Collapse
        accordion
        defaultValue={[7]}
        items={[{ key: 7, label: "数值 Trace", children: <p>数值内容</p> }]}
        onChange={onChange}
      />,
    );

    const trigger = screen.getByRole("button", { name: "数值 Trace" });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(trigger, { key: " " });

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(onChange).toHaveBeenCalledWith(
      [],
      expect.objectContaining({ e: expect.anything() }),
    );
  });
});
