// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "input-number",
  },
}));

import { InputNumber } from "./index";

afterEach(cleanup);

describe("ui/InputNumber TDesign adapter", () => {
  it("puts validation and accessible props on the real inner input", () => {
    const { container } = render(
      <InputNumber
        id="top-k"
        aria-label="策略 1 Top K"
        aria-invalid
        value={8}
        onChange={vi.fn()}
      />,
    );

    const input = container.querySelector(".t-input__inner") as HTMLInputElement;
    expect(input.id).toBe("top-k");
    expect(input.getAttribute("aria-label")).toBe("策略 1 Top K");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.closest(".t-input__wrap")?.getAttribute("aria-label")).toBeNull();
  });
});
