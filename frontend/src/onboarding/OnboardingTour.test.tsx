// @vitest-environment jsdom
/**
 * OnboardingTour 新手引导测试。
 *
 * 覆盖：defaultOpen 打开 / 步骤流转 / 关闭持久化 rag4c.onboarding.v1 / 跳转导航回调。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dismissOnboarding, OnboardingTour } from "./OnboardingTour";

const NAV = vi.fn();

function renderTour(defaultOpen = true) {
  return render(<OnboardingTour onNavigate={NAV} defaultOpen={defaultOpen} />);
}

function nextButton() {
  return screen.getAllByRole("button").find((b) => b.textContent === "下一步");
}

afterEach(() => cleanup());

beforeEach(() => {
  localStorage.clear();
  NAV.mockClear();
});

describe("OnboardingTour", () => {
  it("defaultOpen 时显示弹窗与第一步", () => {
    renderTour();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("欢迎来到 RAG4C")).toBeTruthy();
  });

  it("点击下一步流转到第二步", () => {
    renderTour();
    fireEvent.click(nextButton()!);
    expect(screen.getByText("提问试试")).toBeTruthy();
  });

  it("最后一步点击「开始使用」关闭并持久化 rag4c.onboarding.v1", () => {
    renderTour();
    for (let i = 0; i < 3; i += 1) fireEvent.click(nextButton()!);
    fireEvent.click(screen.getByText("开始使用"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(localStorage.getItem("rag4c.onboarding.v1")).toBe("1");
  });

  it("跳转按钮触发 onNavigate 并标记已引导", () => {
    renderTour();
    fireEvent.click(nextButton()!);
    fireEvent.click(screen.getByText("去提问"));
    expect(NAV).toHaveBeenCalledWith("query");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(localStorage.getItem("rag4c.onboarding.v1")).toBe("1");
  });

  it("跳过按钮关闭不强制导航，并写入 rag4c.onboarding.v1", () => {
    renderTour();
    const skip = screen.getAllByRole("button").find((b) => b.textContent === "跳过");
    fireEvent.click(skip!);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(NAV).not.toHaveBeenCalled();
    expect(localStorage.getItem("rag4c.onboarding.v1")).toBe("1");
  });

  it("dismissOnboarding 持久化已引导标记（含兼容旧键）", () => {
    dismissOnboarding();
    expect(localStorage.getItem("rag4c.onboarding.v1")).toBe("1");
    expect(localStorage.getItem("rag4c.onboarding_done")).toBe("1");
  });

  it("primary next button uses theme on-primary token for contrast", () => {
    renderTour();
    const next = nextButton()!;
    const style = next.getAttribute("style") ?? "";
    expect(style).toContain("--color-on-primary");
  });
});
