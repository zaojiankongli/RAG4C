// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Progress, Select, Space, Typography } from "./index";

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

  it("renders Typography.Text code as <code> and never leaks framework props to the DOM", () => {
    // 真机复现的缺陷：EvalPage 用 <Text code>，fallback 把 code={true} 透传到 <span>，
    // React 在每个页面每个视口报 "Received `true` for a non-boolean attribute `code`"
    // （见 frontend/output/shots/r5-before 的 shoot 报告）。这里钉死两件事：
    // 1) 不再透传布尔属性；2) code 语义译为真 <code>（等宽 + 读屏器可识别）。
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { Text } = Typography;

    render(
      <>
        <Text code>eval-case-1</Text>
        <Text delete>已作废</Text>
      </>,
    );

    const codeNode = screen.getByText("eval-case-1");
    expect(codeNode.tagName).toBe("CODE");
    expect(codeNode.getAttribute("code")).toBeNull();

    const struck = screen.getByText("已作废");
    expect(struck.tagName).toBe("SPAN");
    expect(struck.getAttribute("delete")).toBeNull();
    expect(struck.style.textDecoration).toBe("line-through");

    expect(consoleError).not.toHaveBeenCalled();
  });
});
