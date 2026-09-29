// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HealthInfo } from "../types/rag";
const connection = vi.hoisted(() => ({
  online: true as boolean | null,
  checking: false,
  health: null as HealthInfo | null,
  refresh: vi.fn(),
}));
vi.mock("../context/ConnectionContext", () => ({ useConnection: () => connection }));
import ServiceStatusNotice from "./ServiceStatusNotice";
const ready = { status: "ok", components: { llm: { status: "ok", detail: "" } } } as HealthInfo;
const incomplete = {
  ...ready,
  status: "degraded",
  components: { llm: { status: "unconfigured", detail: "missing" } },
} as HealthInfo;
afterEach(cleanup);
beforeEach(() => {
  connection.online = true;
  connection.checking = false;
  connection.health = ready;
  connection.refresh.mockReset();
});

describe("compact service status transitions", () => {
  it("updates the existing control in place without opening a banner or details", () => {
    const props = { onConfigure: vi.fn() };
    const { rerender } = render(<ServiceStatusNotice {...props} />);
    const trigger = screen.getByRole("button", { name: /服务健康状态/ });
    expect(trigger.textContent).toContain("已就绪");
    connection.checking = true;
    rerender(<ServiceStatusNotice {...props} />);
    expect(screen.getByRole("button", { name: /服务健康状态/ })).toBe(trigger);
    expect(trigger.getAttribute("data-state")).toBe("checking");
    connection.checking = false;
    connection.health = incomplete;
    rerender(<ServiceStatusNotice {...props} />);
    expect(trigger.textContent).toContain("待配置");
    connection.health = ready;
    rerender(<ServiceStatusNotice {...props} />);
    expect(trigger.getAttribute("data-state")).toBe("ready");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("服务状态：已就绪");
  });
  it("reveals actual missing components on demand and routes configuration through the callback", () => {
    connection.health = incomplete;
    const configure = vi.fn();
    render(<ServiceStatusNotice onConfigure={configure} />);
    expect(screen.queryByText("回答模型")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /服务健康状态/ }));
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
    expect(screen.getByText("回答模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "补全配置" }));
    expect(configure).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("refreshes state while the disclosure remains open and prevents duplicate checks", () => {
    const { rerender } = render(<ServiceStatusNotice onConfigure={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /服务健康状态/ }));
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    expect(connection.refresh).toHaveBeenCalledOnce();
    connection.checking = true;
    rerender(<ServiceStatusNotice onConfigure={vi.fn()} />);
    expect((screen.getByRole("button", { name: "检查中…" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    connection.checking = false;
    connection.online = false;
    rerender(<ServiceStatusNotice onConfigure={vi.fn()} />);
    expect(screen.getByRole("button", { name: /服务健康状态：离线演示/ })).toBeTruthy();
  });
  it("dismisses with Escape, outside pointer, and keyboard focus leaving the control", () => {
    render(
      <>
        <ServiceStatusNotice onConfigure={vi.fn()} />
        <button>外部</button>
      </>,
    );
    const trigger = screen.getByRole("button", { name: /服务健康状态/ });
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(document.activeElement).toBe(trigger);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole("button", { name: "外部" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(trigger);
    fireEvent.blur(screen.getByRole("dialog"), {
      relatedTarget: screen.getByRole("button", { name: "外部" }),
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("never exposes offline mock component health as a real configuration result", () => {
    connection.online = false;
    connection.health = incomplete;
    render(<ServiceStatusNotice onConfigure={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /离线演示/ }));
    expect(screen.queryByText("回答模型")).toBeNull();
    expect(screen.getByText(/演示内容/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "连接设置" })).toBeTruthy();
  });
});
