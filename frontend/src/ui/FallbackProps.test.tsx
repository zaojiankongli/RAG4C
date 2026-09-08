// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Progress, Select, Space } from "./index";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("shared fallback DOM props", () => {
  it("maps multiple Select to native semantics and returns every selected value", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onChange = vi.fn();

    render(
      <Select
        mode="multiple"
        aria-label="检索资料库"
        defaultValue={["dataset-a"]}
        options={[
          { label: "资料库 A", value: "dataset-a" },
          { label: "资料库 B", value: "dataset-b" },
        ]}
        onChange={onChange}
      />,
    );

    const select = screen.getByRole("listbox", { name: "检索资料库" }) as HTMLSelectElement;
    expect(select.multiple).toBe(true);
    expect(select.getAttribute("mode")).toBeNull();
    expect(Array.from(select.selectedOptions, (option) => option.value)).toEqual(["dataset-a"]);

    select.options[1]!.selected = true;
    fireEvent.change(select);
    expect(onChange).toHaveBeenLastCalledWith(["dataset-a", "dataset-b"]);
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("consumes Space and Progress framework props without leaking them to the DOM", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <>
        <Space data-testid="space" wrap size="small" align="center">
          <span>第一项</span>
          <span>第二项</span>
        </Space>
        <Progress
          aria-label="检索进度"
          percent={42}
          showInfo={false}
          strokeColor="rgb(1, 2, 3)"
          trailColor="rgb(4, 5, 6)"
        />
      </>,
    );

    const space = screen.getByTestId("space");
    expect(space.getAttribute("wrap")).toBeNull();
    expect(space.getAttribute("align")).toBeNull();
    expect(space.style.flexWrap).toBe("wrap");

    const progress = screen.getByRole("progressbar", { name: "检索进度" });
    expect(progress.getAttribute("showinfo")).toBeNull();
    expect(progress.getAttribute("strokecolor")).toBeNull();
    expect(progress.getAttribute("trailcolor")).toBeNull();
    expect(progress.querySelector("span")?.style.backgroundColor).toBe("rgb(1, 2, 3)");
    expect(consoleError).not.toHaveBeenCalled();
  });
});
