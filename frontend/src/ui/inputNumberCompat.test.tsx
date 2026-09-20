// @vitest-environment jsdom

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InputNumber } from "./index";

afterEach(cleanup);

/**
 * `InputNumber` 原先是 TDesign 的**直连别名**，是整个兼容层里唯一绕过 facade 的表单控件。
 * 绕过 facade 不是风格问题，而是让可访问名物理上不可能送达：
 *   TDesign 把 restProps 摊到外层 div，内层 `input.t-input__inner` 的属性表是写死闭合的
 *   （无 aria-*、无 id、无展开），`tdesign-react/esm/input/` 整个目录 grep "aria" 命中 0。
 * 所以调用方 `<InputNumber aria-label="bge-m3 优先级" />` 写了也白写——
 * 视觉门禁在 /config 量到的 81 个无可访问名控件全是 `input.t-input__inner`。
 *
 * 换成兜底分支后有一个新的回归风险：TDesign 的 onChange 签名是 `(value)`，
 * 原生 input 的是 `(event)`。两个调用方都按 `(value)` 写并靠 `v != null` 判空，
 * 所以下面第 2、3 条把签名钉死，而不是只测 aria。
 */
describe("ui/InputNumber compat layer", () => {
  it("puts aria-label on the real number input", () => {
    const { container } = render(
      <InputNumber value={3} onChange={() => {}} aria-label="bge-m3 优先级" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="number"]')!;
    expect(input.getAttribute("aria-label")).toBe("bge-m3 优先级");
  });

  it("calls onChange with a number, not with the DOM event", () => {
    const onChange = vi.fn();
    const { container } = render(
      <InputNumber value={3} min={1} max={9999} onChange={onChange} aria-label="阈值" />,
    );
    fireEvent.change(container.querySelector('input[type="number"]')!, { target: { value: "7" } });
    expect(onChange).toHaveBeenCalledWith(7);
  });

  it("yields undefined when cleared so callers' null-guards skip the write", () => {
    const onChange = vi.fn();
    const { container } = render(
      <InputNumber value={3} onChange={onChange} aria-label="阈值" />,
    );
    fireEvent.change(container.querySelector('input[type="number"]')!, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(undefined);
  });

  it("keeps min/max/step and disabled on the DOM", () => {
    const { container } = render(
      <InputNumber value={1} min={1} max={3} step={1} disabled onChange={() => {}} aria-label="质量分" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="number"]')!;
    expect(input.getAttribute("min")).toBe("1");
    expect(input.getAttribute("max")).toBe("3");
    expect(input.getAttribute("step")).toBe("1");
    expect(input.disabled).toBe(true);
  });

  it("does not leak size onto the native input, where it means character width", () => {
    const { container } = render(
      <InputNumber size="small" value={3} onChange={() => {}} aria-label="并发数" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="number"]')!;
    expect(input.getAttribute("size")).toBeNull();
    expect(input.className).toContain("rag-input");
  });
});
