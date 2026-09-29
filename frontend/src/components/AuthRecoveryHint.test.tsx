// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthRecoveryHint, { isAuthError } from "./AuthRecoveryHint";

beforeEach(() => {
  localStorage.removeItem("rag4c.knowledge_actor_token");
  window.history.replaceState({}, "", "/query");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AuthRecoveryHint", () => {
  it("shows recovery copy when actor token is missing", () => {
    localStorage.removeItem("rag4c.knowledge_actor_token");
    render(<AuthRecoveryHint />);
    expect(screen.getByLabelText("鉴权恢复提示")).toBeTruthy();
    expect(screen.getByLabelText("打开系统设置")).toBeTruthy();
    expect(screen.getByText("缺少 KnowledgeOps 身份凭据")).toBeTruthy();
    expect(screen.getByText(/未配置有效的 Actor Bearer/)).toBeTruthy();
  });

  it("adapts copy when a token exists but server still rejects", () => {
    localStorage.setItem("rag4c.knowledge_actor_token", "tok");
    render(<AuthRecoveryHint onRetry={() => {}} />);
    expect(screen.getByText(/过期|租户不匹配|权限不足/)).toBeTruthy();
    expect(screen.getByLabelText("重新连接并重试")).toBeTruthy();
    localStorage.removeItem("rag4c.knowledge_actor_token");
  });

  it("keeps caller-supplied description even when a token exists", () => {
    localStorage.setItem("rag4c.knowledge_actor_token", "tok");
    render(
      <AuthRecoveryHint
        description="若后端已恢复，请确认工作区身份后刷新；若服务未启动，请先启动桥服务。"
        title="治理权威不可用"
      />,
    );
    expect(screen.getByText("治理权威不可用")).toBeTruthy();
    expect(screen.getByText(/服务未启动/)).toBeTruthy();
    localStorage.removeItem("rag4c.knowledge_actor_token");
  });

  it("opens Config through history navigation when the app uses direct routes", () => {
    const onPopState = vi.fn();
    window.addEventListener("popstate", onPopState);
    render(<AuthRecoveryHint />);

    fireEvent.click(screen.getByRole("button", { name: "打开系统设置" }));

    expect(window.location.pathname).toBe("/config");
    expect(onPopState).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", onPopState);
  });

  it("opens Config through the hash route when the app uses hash deployment", () => {
    window.history.replaceState({}, "", "/#/query");
    const dispatchEvent = vi.spyOn(window, "dispatchEvent");
    render(<AuthRecoveryHint />);

    fireEvent.click(screen.getByRole("button", { name: "打开系统设置" }));

    expect(window.location.hash).toBe("#/config");
    expect(dispatchEvent.mock.calls.filter(([event]) => event.type === "popstate")).toHaveLength(0);
  });
});

describe("isAuthError", () => {
  it("detects status and message auth failures", () => {
    expect(isAuthError({ status: 401 })).toBe(true);
    expect(isAuthError({ status: 403 })).toBe(true);
    expect(isAuthError({ message: "需要有效的 KnowledgeOps Actor Bearer 凭据" })).toBe(true);
    expect(isAuthError({ status: 500, message: "boom" })).toBe(false);
    expect(isAuthError(null)).toBe(false);
  });
});
