// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AutomationStateNotice } from "./automationUi";

afterEach(cleanup);

describe("AutomationStateNotice", () => {
  it("keeps loading, unavailable, error, partial, and empty authority copy explicit", () => {
    const { rerender } = render(
      <AutomationStateNotice status="loading" resourceLabel="自动化规则" />,
    );
    expect(screen.getByText("正在读取自动化规则…")).toBeTruthy();

    rerender(<AutomationStateNotice status="unavailable" resourceLabel="自动化规则" />);
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");

    rerender(<AutomationStateNotice status="error" resourceLabel="自动化规则" />);
    expect(screen.getByRole("alert").textContent).toContain("读取失败");

    rerender(
      <AutomationStateNotice status="partial" resourceLabel="自动化规则" invalidItemCount={2} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("2 条记录");

    rerender(
      <AutomationStateNotice
        status="empty"
        resourceLabel="自动化规则"
        emptyTitle="暂无自动化规则"
        emptyDescription="暂无可验证规则。"
      />,
    );
    expect(screen.getByText("暂无自动化规则")).toBeTruthy();
    expect(screen.getByText("暂无可验证规则。")).toBeTruthy();
  });
});
