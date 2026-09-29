// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "button",
  },
}));

import { Button } from "./index";

afterEach(cleanup);

describe("ui/Button TDesign adapter", () => {
  it("preserves facade danger/text mapping and disabled semantics", () => {
    render(
      <Button type="text" danger disabled aria-label="处理对账">
        处理
      </Button>,
    );

    const button = screen.getByLabelText("处理对账");
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.className).toContain("t-button");
    expect(button.className).toContain("t-button--variant-text");
  });
});
