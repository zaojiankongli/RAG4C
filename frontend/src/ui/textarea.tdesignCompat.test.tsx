// @vitest-environment jsdom

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "input-text-area",
  },
}));

import { Input } from "./index";

afterEach(cleanup);

describe("ui/TextArea TDesign adapter", () => {
  it("maps maxLength and autoSize to TDesign's lowercase props", () => {
    const onChange = vi.fn();
    const { container } = render(
      <Input.TextArea
        value="备注"
        maxLength={20}
        autoSize={{ minRows: 2, maxRows: 5 }}
        onChange={onChange}
      />,
    );

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.className).toContain("t-textarea__inner");
    fireEvent.change(textarea, { target: { value: "a".repeat(21) } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ target: expect.objectContaining({ value: "a".repeat(20) }) }),
    );
  });
});
