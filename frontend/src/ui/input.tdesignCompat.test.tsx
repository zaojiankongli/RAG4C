// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "input",
  },
}));

import { Input } from "./index";

afterEach(cleanup);

describe("ui/Input TDesign adapter", () => {
  it("maps maxLength to TDesign maxlength enforcement", () => {
    const onChange = vi.fn();
    const { container } = render(
      <Input aria-label="策略名称" maxLength={3} value="abc" onChange={onChange} />,
    );

    const input = container.querySelector(".t-input__inner") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "abcd" } });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ target: expect.objectContaining({ value: "abc" }) }),
    );
  });

  it("keeps ARIA labels on the actual inner input", () => {
    render(<Input id="strategy-name" aria-label="策略名称" value="" onChange={vi.fn()} />);

    const input = screen.getByRole("textbox", { name: "策略名称" });
    expect(input.id).toBe("strategy-name");
    expect(input.closest(".t-input__wrap")?.getAttribute("aria-label")).toBeNull();
  });
});
