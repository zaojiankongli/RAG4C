// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import AuthRecoveryHint, { isAuthError } from "./AuthRecoveryHint";

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
