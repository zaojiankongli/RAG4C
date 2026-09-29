// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Statistic } from "./index";

afterEach(cleanup);

describe("ui/Statistic compat layer", () => {
  it("renders the native metric structure with value formatting and styles", () => {
    render(
      <Statistic
        title={<span>贴题率</span>}
        value={42.5}
        precision={1}
        prefix={<span aria-hidden="true">↑</span>}
        suffix="%"
        valueStyle={{ color: "rgb(1, 2, 3)" }}
        aria-label="贴题率指标"
        data-testid="statistic"
      />,
    );

    const root = screen.getByTestId("statistic");
    expect(root.className).toContain("rag-statistic");
    expect(within(root).getByText("贴题率")).toBeTruthy();
    expect(within(root).getByText("↑")).toBeTruthy();
    expect(within(root).getByText("42.5")).toBeTruthy();
    expect(within(root).getByText("%")).toBeTruthy();
    expect(root.querySelector(".rag-statistic-content")?.getAttribute("style")).toContain(
      "color: rgb(1, 2, 3)",
    );
  });

  it("supports formatter and does not leak TDesign-only props to the DOM", () => {
    render(
      <Statistic
        title="完成率"
        value={0.875}
        precision={3}
        formatter={(value: number) => `${Math.round(value * 100)}%`}
        aria-label="完成率指标"
        data-testid="formatted-statistic"
      />,
    );

    const root = screen.getByTestId("formatted-statistic");
    expect(within(root).getByText("88%")).toBeTruthy();
    expect(root.getAttribute("precision")).toBeNull();
    expect(root.getAttribute("formatter")).toBeNull();
    expect(root.querySelector(".rag-statistic-content")).not.toBeNull();
  });
});
