// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "alert",
  },
}));

import { Alert } from "./index";

afterEach(cleanup);

describe("ui/Alert TDesign adapter", () => {
  it("preserves theme, title/message mapping, and alert semantics", () => {
    render(
      <Alert
        theme="warning"
        title="判断已被更新"
        message="请复核后保存"
        aria-label="判断冲突"
      />,
    );

    const alert = screen.getByRole("alert", { name: "判断冲突" });
    expect(alert.className).toContain("t-alert--warning");
    expect(alert.textContent).toContain("判断已被更新");
    expect(alert.textContent).toContain("请复核后保存");
  });
});
