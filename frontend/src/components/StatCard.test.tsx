// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import StatCard from "./StatCard";

afterEach(cleanup);

describe("StatCard", () => {
  it("uses a native TDesign card while preserving the RAG4C metric structure", () => {
    render(
      <StatCard
        label="检索片段"
        value="1,280"
        unit="条"
        labelIcon={<span data-testid="label-icon">L</span>}
        icon={<span data-testid="value-icon">V</span>}
        tone="success"
      />,
    );

    const card = screen.getByText("检索片段").closest(".stat-card");
    expect(card).not.toBeNull();
    expect(card?.classList.contains("t-card")).toBe(true);
    expect(within(card as HTMLElement).getByText("1,280")).toBeTruthy();
    expect(within(card as HTMLElement).getByText("条").classList.contains("stat-unit")).toBe(true);
    expect(within(card as HTMLElement).getByTestId("label-icon").parentElement?.classList.contains("is-success")).toBe(true);
    expect(within(card as HTMLElement).getByTestId("value-icon").parentElement?.classList.contains("is-success")).toBe(true);
  });

  it("exposes metric hints to both hover and keyboard focus", async () => {
    render(<StatCard label="解析覆盖" value="86%" hint="已记录解析阶段元数据的文档占比" />);

    const trigger = screen.getByText("解析覆盖").closest("[tabindex='0']");
    expect(trigger).not.toBeNull();

    fireEvent.mouseEnter(trigger as HTMLElement);
    expect(screen.getByRole("tooltip").textContent).toContain("已记录解析阶段元数据");

    fireEvent.mouseLeave(trigger as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());

    fireEvent.focus(trigger as HTMLElement);
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toContain("已记录解析阶段元数据");
    expect(trigger?.getAttribute("aria-describedby")).toBe(tooltip.id);
    expect(tooltip.id).not.toBe("");
    fireEvent.blur(trigger as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  });
});
