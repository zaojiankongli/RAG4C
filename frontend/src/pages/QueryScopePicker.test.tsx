// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import QueryScopePicker from "./QueryScopePicker";

afterEach(cleanup);
const options = [
  { value: "public", label: "公开资料" },
  { value: "hr", label: "人力资源" },
];
function Example() {
  const [value, setValue] = useState<string[]>([]);
  return (
    <>
      <QueryScopePicker value={value} onChange={setValue} options={options} />
      <button>外部操作</button>
    </>
  );
}
describe("query scope picker", () => {
  it("keeps the toolbar compact and updates multi-select scope without closing", () => {
    render(<Example />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "限定查询的资料范围：全部资料" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "公开资料" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "人力资源" }));
    expect(
      screen
        .getByRole("button", { name: "限定查询的资料范围：公开资料、人力资源" })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "使用全部资料" }));
    expect((screen.getByRole("checkbox", { name: "公开资料" }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(screen.getByRole("button", { name: "限定查询的资料范围：全部资料" })).toBeTruthy();
  });
  it("dismisses on Escape with restored trigger focus and on outside click", () => {
    render(<Example />);
    const trigger = screen.getByRole("button", { name: "限定查询的资料范围：全部资料" });
    fireEvent.click(trigger);
    const checkbox = screen.getByRole("checkbox", { name: "公开资料" });
    checkbox.focus();
    fireEvent.keyDown(checkbox, { key: "Escape" });
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole("button", { name: "外部操作" }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
