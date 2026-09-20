// @vitest-environment jsdom

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Tabs } from "./index";

afterEach(cleanup);

const items = [
  { key: "overview", label: "概览", children: <p>概览内容</p> },
  { key: "impact", label: "影响", children: <p>影响内容</p> },
  { key: "audit", label: "审计", children: <p>审计内容</p> },
];

/**
 * 兼容层的 Tabs 兜底分支原先已经给了 `role="tablist"` + `role="tab"` + `aria-selected`，
 * 但**没有 aria-controls、也没有 role="tabpanel"**——内容只是被 `items.find(...)?.children`
 * 直接摊在 div 里。按 WAI-ARIA 这是"关联不到面板的 tab"，读屏拿不到面板归属。
 *
 * 这里钉的是结构不变量，而不是样式：
 *  - 每个 tab 的 aria-controls 必须指向**真实存在**且 role=tabpanel 的元素；
 *  - 只渲染一份面板，所以所有 tab 指向同一个它（各造 id 会退化成 dangling-aria-controls）；
 *  - roving tabindex：一组 tablist 只能有一个 Tab 停留点。
 */
describe("ui/Tabs compat layer", () => {
  it("links every tab to the one rendered tabpanel", () => {
    const { container, getByText } = render(
      <Tabs items={items} activeKey="overview" onChange={() => {}} />,
    );
    expect(getByText("概览内容")).toBeTruthy();

    const tabs = container.querySelectorAll('[role="tab"]');
    expect(tabs.length).toBe(3);
    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel).toBeTruthy();

    tabs.forEach((tab) => {
      const controls = tab.getAttribute("aria-controls");
      expect(controls, "tab 缺 aria-controls").toBeTruthy();
      const target = document.getElementById(controls!);
      expect(target, `aria-controls 指向不存在的元素：${controls}`).toBe(panel);
    });

    const labelledBy = panel!.getAttribute("aria-labelledby");
    expect(document.getElementById(labelledBy!)?.getAttribute("aria-selected")).toBe("true");
  });

  it("keeps exactly one tab in the sequential focus order", () => {
    const { container } = render(<Tabs items={items} activeKey="impact" onChange={() => {}} />);
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(tabs.map((tab) => tab.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
  });

  it("moves the active tab with arrow, Home and End keys", () => {
    const onChange = vi.fn();
    const { container } = render(<Tabs items={items} activeKey="audit" onChange={onChange} />);
    const [first, , last] = [...container.querySelectorAll('[role="tab"]')];

    fireEvent.keyDown(last!, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("overview"); // 末端向右要回卷
    fireEvent.keyDown(first!, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("audit"); // 首端向左要绕到末
    fireEvent.keyDown(first!, { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("audit");
    fireEvent.keyDown(last!, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("overview");
    expect(first!.getAttribute("data-key")).toBe("overview");
  });

  it("honours item.disabled on the fallback branch too", () => {
    const onChange = vi.fn();
    const { container } = render(
      <Tabs
        items={[{ ...items[0] }, { ...items[1], disabled: true }, { ...items[2] }]}
        activeKey="overview"
        onChange={onChange}
      />,
    );
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(tabs[1]!.hasAttribute("disabled")).toBe(true);
    expect(tabs[0]!.hasAttribute("disabled")).toBe(false);
  });

  it("forwards aria-label onto the tablist", () => {
    const { container } = render(
      <Tabs items={items} activeKey="overview" aria-label="通知中心视图" onChange={() => {}} />,
    );
    const tablist = container.querySelector('[role="tablist"]')!;
    expect(tablist.getAttribute("aria-label")).toBe("通知中心视图");
  });

  /**
   * 要迁过来的两个模块用的是 TDesign Tabs，而它的 TabPanel 是
   * **首次激活才挂载、之后留在 DOM 里靠 display:none 隐藏**
   * （tdesign-react/esm/tabs/TabPanel.js:34-43）。兼容层默认"只渲染当前 children"，
   * 照字面迁移会让切走的面板卸载 → 切回来重新发请求、丢滚动与筛选态。
   * keepAlive 就是为了不让这次迁移偷偷改掉行为。
   */
  it("keeps visited panels mounted when keepAlive is on", () => {
    const { rerender, queryByText } = render(
      <Tabs items={items} activeKey="overview" keepAlive onChange={() => {}} />,
    );
    // 没激活过的面板不挂载（与 TDesign 的 lazy 一致）
    expect(queryByText("影响内容")).toBeNull();

    rerender(<Tabs items={items} activeKey="impact" keepAlive onChange={() => {}} />);
    expect(queryByText("影响内容")).toBeTruthy();

    rerender(<Tabs items={items} activeKey="overview" keepAlive onChange={() => {}} />);
    const visited = queryByText("影响内容");
    expect(visited, "看过的面板不该在切走时被卸载").toBeTruthy();
    expect(visited!.closest("[hidden]"), "切走的面板要留在 DOM 里但标记 hidden").toBeTruthy();
  });

  it("unmounts inactive panels when keepAlive is off, preserving current callers", () => {
    const { rerender, queryByText } = render(
      <Tabs items={items} activeKey="impact" onChange={() => {}} />,
    );
    expect(queryByText("影响内容")).toBeTruthy();
    rerender(<Tabs items={items} activeKey="overview" onChange={() => {}} />);
    expect(queryByText("影响内容")).toBeNull();
  });

  /**
   * 13 个企业模块写的是 TDesign 的子元素 API，只有 1 处用 items 数组。
   * 兼容层如果只认 items，"迁到兼容层"就意味着逐页搬上百行 JSX；
   * 认两种写法之后，迁移一个页面只剩换一行 import。
   */
  it("accepts TDesign-style Tabs.TabPanel children with the same ARIA structure", () => {
    const { container, queryByText } = render(
      <Tabs activeKey="impact" aria-label="自动化工作面" onChange={() => {}}>
        <Tabs.TabPanel value="overview" label="概览">
          <p>概览内容</p>
        </Tabs.TabPanel>
        <Tabs.TabPanel value="impact" label="影响">
          <p>影响内容</p>
        </Tabs.TabPanel>
      </Tabs>,
    );

    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(tabs.map((tab) => tab.textContent)).toEqual(["概览", "影响"]);
    // 没开 keepAlive 时只挂载当前面板
    expect(queryByText("概览内容")).toBeNull();
    expect(queryByText("影响内容")).toBeTruthy();

    const panel = container.querySelector('[role="tabpanel"]')!;
    tabs.forEach((tab) => {
      const target = document.getElementById(tab.getAttribute("aria-controls")!);
      expect(target, "children 写法下 aria-controls 也要能解析到面板").toBe(panel);
    });
    expect(container.querySelector('[role="tablist"]')?.getAttribute("aria-label")).toBe(
      "自动化工作面",
    );
  });

  it("still exposes exactly one tabpanel under keepAlive", () => {
    const { container, rerender } = render(
      <Tabs items={items} activeKey="overview" keepAlive onChange={() => {}} />,
    );
    rerender(<Tabs items={items} activeKey="audit" keepAlive onChange={() => {}} />);
    // 堆叠的非当前面板是普通 div，不能再各自声明 tabpanel——那会让 aria-controls 指向
    // 尚未挂载的面板，把 no-aria-controls 换成 dangling-aria-controls。
    expect(container.querySelectorAll('[role="tabpanel"]').length).toBe(1);
    const tabs = container.querySelectorAll('[role="tab"]');
    tabs.forEach((tab) => {
      const target = document.getElementById(tab.getAttribute("aria-controls")!);
      expect(target?.getAttribute("role")).toBe("tabpanel");
    });
  });
});
