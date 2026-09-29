// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: () => false,
  },
}));

import { Alert } from "./index";

afterEach(cleanup);

describe("ui/Alert native operation compatibility", () => {
  it("keeps retry operations visible in the native renderer", () => {
    render(
      <Alert
        theme="error"
        title="权威事实不可用"
        message="请稍后重试。"
        operation={<button type="button">重试</button>}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain("请稍后重试。");
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});
