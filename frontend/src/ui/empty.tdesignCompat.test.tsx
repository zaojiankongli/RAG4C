// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "empty",
  },
}));

import { Empty } from "./index";

afterEach(cleanup);

describe("ui/Empty TDesign adapter", () => {
  it("maps the empty compatibility vocabulary to TDesign", () => {
    render(
      <Empty
        type="empty"
        title="TDesign 空状态"
        description="来自兼容层的描述"
        data-testid="tdesign-empty"
        id="tdesign-empty-id"
        role="status"
        tabIndex={0}
        aria-label="TDesign 空状态"
      />,
    );

    const root = screen.getByTestId("tdesign-empty");
    const empty = root.querySelector(".t-empty");
    expect(empty).not.toBeNull();
    expect(within(root).getByText("TDesign 空状态")).toBeTruthy();
    expect(within(root).getByText("来自兼容层的描述")).toBeTruthy();
    expect(root.id).toBe("tdesign-empty-id");
    expect(root.getAttribute("role")).toBe("status");
    expect(root.getAttribute("tabindex")).toBe("0");
    expect(root.getAttribute("aria-label")).toBe("TDesign 空状态");
  });
});
