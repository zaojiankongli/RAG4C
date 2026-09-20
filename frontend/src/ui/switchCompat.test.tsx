// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Switch } from "./index";

afterEach(cleanup);

/**
 * 兼容层的 Switch 有两条渲染分支，`canRenderTDesign()` 目前恒为 false，
 * 所以线上跑的是自绘 `<input type="checkbox">` 那一条。
 * 这条分支原先只接 `checked` / `onChange`，把其余 props 全丢了，
 * 于是调用方写了也白写：
 *   - `aria-label` 被吞 → 视觉门禁在 /config 量到的 270 个无可访问名控件里本处占 189 个，另 81 个是绕过 facade 的 InputNumber（见 inputNumberCompat.test.tsx）；
 *   - `disabled` 被吞 → EngineBoard 的 `disabled={!p.configured}` 失效，
 *     未配置的服务提供者开关其实可以点。
 * 这里把两条都钉住：断言的是**落到真实 DOM 上**的结果，不是组件的 props。
 */
describe("ui/Switch compat layer", () => {
  it("forwards aria-label to the real checkbox", () => {
    const { container } = render(
      <Switch checked={false} onChange={() => {}} aria-label="启用 bge-m3" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    expect(input.getAttribute("aria-label")).toBe("启用 bge-m3");
  });

  it("honours disabled instead of silently dropping it", () => {
    const onChange = vi.fn();
    const { container } = render(
      <Switch checked={false} disabled onChange={onChange} aria-label="启用 local 模型" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    // 只断言"属性真的落到 DOM 上"。点击是否被拦下是浏览器的原生 disabled 语义，
    // 不是这段代码的责任范围——而且在 jsdom 里根本测不出来：
    // 它对 disabled 元素照样派发事件，用它做断言会得到一个"通过但抛未捕获错误"的假信号。
    expect(input.disabled).toBe(true);
    expect(input.getAttribute("aria-label")).toBe("启用 local 模型");
  });

  it("does not leak non-DOM props like size onto the native input", () => {
    const { container } = render(
      <Switch checked size="small" onChange={() => {}} aria-label="启用 cli" />,
    );
    const input = container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    // size="small" 对 checkbox 没有意义，落到 DOM 上会变成无效属性 / React 警告
    expect(input.getAttribute("size")).toBeNull();
    expect(input.checked).toBe(true);
  });
});
