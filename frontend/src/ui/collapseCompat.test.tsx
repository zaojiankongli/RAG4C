// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Collapse } from "./index";

afterEach(cleanup);

const items = [
  { key: "trace", label: "检索过程", children: <p>阶段详情</p> },
  { key: "audit", label: "审计信息", children: <p>审计详情</p> },
];

describe("ui/Collapse compat layer", () => {
  it("renders linked native disclosure controls and toggles an item", () => {
    render(<Collapse items={items} aria-label="执行详情" data-testid="collapse" />);

    const root = screen.getByTestId("collapse");
    const trigger = within(root).getByRole("button", { name: "检索过程" });
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    expect(document.getElementById(panelId!)).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(panelId!)?.hasAttribute("hidden")).toBe(true);

    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("阶段详情")).toBeTruthy();
    expect(document.getElementById(panelId!)?.hasAttribute("hidden")).toBe(false);
    expect(document.getElementById(panelId!)?.getAttribute("role")).toBe("region");
    expect(document.getElementById(panelId!)?.getAttribute("aria-labelledby")).toBe(
      trigger.id,
    );
  });

  it("supports controlled values and emits collection-shaped changes", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <Collapse items={items} value={["audit"]} onChange={onChange} />,
    );

    expect(screen.getByRole("button", { name: "审计信息" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "检索过程" }));
    expect(onChange).toHaveBeenLastCalledWith(["audit", "trace"]);

    rerender(<Collapse items={items} value={["trace"]} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "检索过程" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    const auditTrigger = screen.getByRole("button", { name: "审计信息" });
    const auditPanel = document.getElementById(auditTrigger.getAttribute("aria-controls")!);
    expect(auditPanel?.hasAttribute("hidden")).toBe(true);
  });

  it("supports accordion mode and disabled items without leaking framework props", () => {
    const onChange = vi.fn();
    render(
      <Collapse
        accordion
        ghost
        size="small"
        items={[{ ...items[0] }, { ...items[1], disabled: true }]}
        onChange={onChange}
        aria-label="审计折叠面板"
      />,
    );

    const trace = screen.getByRole("button", { name: "检索过程" });
    const audit = screen.getByRole("button", { name: "审计信息" });
    expect(trace.closest(".rag-collapse")?.classList.contains("is-ghost")).toBe(true);
    expect(trace.closest(".rag-collapse")?.classList.contains("is-small")).toBe(true);
    expect((audit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(trace);
    fireEvent.click(trace);
    expect(onChange).toHaveBeenNthCalledWith(1, ["trace"]);
    expect(onChange).toHaveBeenNthCalledWith(2, []);
    expect(trace.getAttribute("accordion")).toBeNull();
    expect(trace.getAttribute("ghost")).toBeNull();
    expect(trace.getAttribute("size")).toBeNull();
  });

  it("honors a collapse-level disabled state", () => {
    const onChange = vi.fn();
    render(<Collapse disabled items={items} onChange={onChange} />);

    for (const trigger of screen.getAllByRole("button")) {
      expect((trigger as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(trigger);
    }
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps uncontrolled default values and rejects unknown item keys safely", () => {
    render(
      <Collapse
        defaultValue={["audit"]}
        items={[...items, { key: "__proto__", label: "异常", children: <p>不应展示</p> }]}
      />,
    );

    expect(screen.getByRole("button", { name: "审计信息" }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.queryByText("不应展示")).toBeNull();
  });

  it("fails closed for null items and duplicate normalized keys", () => {
    render(
      <Collapse
        items={
          [
            items[0],
            { ...items[1], key: "trace", children: <p>重复内容</p> },
          ]
        }
        data-testid="duplicate-collapse"
      />,
    );

    const root = screen.getByTestId("duplicate-collapse");
    expect(within(root).getAllByRole("button")).toHaveLength(1);
    expect(within(root).queryByText("重复内容")).toBeNull();
  });
});
