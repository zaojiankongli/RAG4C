// @vitest-environment jsdom

import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "tag",
  },
}));

import { Tag } from "./index";

afterEach(cleanup);

describe("ui/Tag TDesign adapter", () => {
  it("maps theme, variant, and size to the dependency renderer", () => {
    const { container } = render(
      <Tag theme="success" variant="light-outline" size="small">
        状态
      </Tag>,
    );

    const tag = container.querySelector(".t-tag");
    expect(tag).not.toBeNull();
    expect(within(tag as HTMLElement).getByText("状态")).toBeTruthy();
    expect(tag?.className).toContain("t-tag--success");
    expect(tag?.className).toContain("t-size-s");
    expect(tag?.getAttribute("theme")).toBeNull();
    expect(tag?.getAttribute("variant")).toBeNull();
    expect(tag?.getAttribute("size")).toBeNull();
  });
});
