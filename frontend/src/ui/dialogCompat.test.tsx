// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Checkbox, Dialog, Drawer } from "./index";

afterEach(cleanup);

describe("ui/Dialog native compatibility", () => {
  it("keeps modal naming, confirmation fences, and callback boundaries", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    render(
      <Dialog
        open
        title="重试任务"
        aria-label="重试任务"
        closeBtn
        cancelBtn="取消"
        confirmBtn={{ content: "确认重试", disabled: true }}
        onConfirm={onConfirm}
        onCancel={onCancel}
        attach="body"
      >
        <Checkbox aria-label="确认重试">我确认</Checkbox>
      </Dialog>,
    );

    const dialog = screen.getByRole("dialog", { name: "重试任务" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(screen.getByRole("button", { name: "确认重试" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("checkbox", { name: "确认重试" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("keeps the topmost nested dialog as the active focus trap", () => {
    render(
      <>
        <Drawer open title="父抽屉">
          <button type="button">父控件</button>
        </Drawer>
        <Dialog open title="子对话框">
          <button type="button">子控件</button>
        </Dialog>
      </>,
    );

    const child = screen.getByRole("button", { name: "子控件" });
    child.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(child);
  });
});
