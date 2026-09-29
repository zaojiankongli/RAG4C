// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Drawer } from "./index";

afterEach(cleanup);

describe("ui/Drawer compat layer", () => {
  it("keeps a non-destroyed drawer mounted while closed and restores its geometry when reopened", async () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Drawer
        open
        title="运行详情"
        placement="left"
        width="min(760px, 96vw)"
        onClose={onClose}
        destroyOnClose={false}
        data-testid="drawer"
      >
        <input aria-label="详情内容" />
      </Drawer>,
    );

    const drawer = screen.getByRole("dialog", { name: "运行详情" });
    const input = screen.getByRole("textbox", { name: "详情内容" });
    expect(drawer.className).toContain("rag-drawer");
    expect(drawer.className).toContain("is-left");
    expect((drawer as HTMLElement).style.width).toBe("min(760px, 96vw)");

    rerender(
      <Drawer
        open={false}
        title="运行详情"
        placement="left"
        width="min(760px, 96vw)"
        onClose={onClose}
        destroyOnClose={false}
        data-testid="drawer"
      >
        <input aria-label="详情内容" />
      </Drawer>,
    );
    expect(screen.getByTestId("drawer").hasAttribute("hidden")).toBe(true);
    expect(screen.getByRole("textbox", { name: "详情内容", hidden: true })).toBe(input);

    rerender(
      <Drawer
        open
        title="运行详情"
        placement="left"
        width="min(760px, 96vw)"
        onClose={onClose}
        destroyOnClose={false}
        data-testid="drawer"
      >
        <input aria-label="详情内容" />
      </Drawer>,
    );
    expect(screen.getByRole("dialog", { name: "运行详情" })).toBe(drawer);
    expect(screen.getByRole("textbox", { name: "详情内容" })).toBe(input);

    const mask = screen.getByTestId("drawer").querySelector(".rag-drawer-mask");
    expect(mask).not.toBeNull();
    fireEvent.mouseDown(mask!);
    expect(onClose).toHaveBeenCalledTimes(1);

    const user = userEvent.setup();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("unmounts content after a destroyed drawer closes", async () => {
    const { rerender } = render(
      <Drawer open title="一次性详情" destroyOnClose>
        <span>只存在于打开状态</span>
      </Drawer>,
    );

    expect(screen.getByText("只存在于打开状态")).toBeTruthy();
    rerender(
      <Drawer open={false} title="一次性详情" destroyOnClose>
        <span>只存在于打开状态</span>
      </Drawer>,
    );
    await waitFor(() => expect(screen.queryByText("只存在于打开状态")).toBeNull());
  });

  it("accepts TDesign-compatible visible, header, and size aliases", () => {
    render(
      <Drawer visible placement="bottom" header="底部详情" size="72vh">
        <span>兼容别名内容</span>
      </Drawer>,
    );

    const drawer = screen.getByRole("dialog", { name: "底部详情" });
    expect(drawer.className).toContain("is-bottom");
    expect((drawer as HTMLElement).style.height).toBe("72vh");
    expect(screen.getByText("兼容别名内容")).toBeTruthy();
  });

  it("returns focus to the opener after an Escape close", async () => {
    function StatefulDrawer() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            打开抽屉
          </button>
          <Drawer open={open} title="焦点详情" onClose={() => setOpen(false)}>
            <p>焦点内容</p>
          </Drawer>
        </>
      );
    }

    const user = userEvent.setup();
    render(<StatefulDrawer />);
    const opener = screen.getByRole("button", { name: "打开抽屉" });
    await user.click(opener);
    expect(screen.getByRole("dialog", { name: "焦点详情" })).toBeTruthy();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(document.activeElement).toBe(opener));
  });

  it("keeps native modal focus inside the drawer", () => {
    render(
      <Drawer open title="键盘详情">
        <button type="button">第一个控件</button>
        <button type="button">最后一个控件</button>
      </Drawer>,
    );

    const first = screen.getByRole("button", { name: "第一个控件" });
    const last = screen.getByRole("button", { name: "最后一个控件" });
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it("does not steal focus from a nested modal opened above the drawer", () => {
    render(
      <>
        <Drawer open title="键盘详情">
          <button type="button">抽屉控件</button>
        </Drawer>
        <div role="dialog" aria-modal="true">
          <button type="button">确认弹窗控件</button>
        </div>
      </>,
    );

    const nested = screen.getByRole("button", { name: "确认弹窗控件" });
    nested.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(nested);
  });

  it("portals body-attached drawers to the document body", () => {
    render(
      <Drawer open title="Body 抽屉" attach="body" data-testid="body-drawer">
        <span>内容</span>
      </Drawer>,
    );

    expect(screen.getByTestId("body-drawer").parentElement).toBe(document.body);
  });

  it("preserves an explicit accessible name over the generated title relation", () => {
    render(
      <Drawer open title="内部标题" aria-label="自定义抽屉名称">
        <p>内容</p>
      </Drawer>,
    );

    const drawer = screen.getByRole("dialog", { name: "自定义抽屉名称" });
    expect(drawer.getAttribute("aria-label")).toBe("自定义抽屉名称");
    expect(drawer.getAttribute("aria-labelledby")).toBeNull();
  });
});
