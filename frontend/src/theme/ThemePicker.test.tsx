// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ThemePicker from "./ThemePicker";
import { useThemeMode } from "./useThemeMode";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
  document.querySelector('meta[name="theme-color"]')?.remove();
});

function ThemeHarness() {
  const { mode, setMode } = useThemeMode();
  return <ThemePicker mode={mode} onChange={setMode} />;
}

describe("independent appearance choices", () => {
  it("selects Yanami directly from light, persists it, and restores it on remount", async () => {
    localStorage.setItem("rag4c.theme_mode", "light");
    const user = userEvent.setup();
    const { unmount } = render(<ThemeHarness />);
    expect(screen.getAllByRole("button")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: "使用八奈见主题" }));
    expect(document.documentElement.dataset.theme).toBe("anime");
    expect(localStorage.getItem("rag4c.theme_mode")).toBe("anime");
    expect(
      screen.getByRole("button", { name: "使用八奈见主题" }).getAttribute("aria-pressed"),
    ).toBe("true");
    unmount();
    render(<ThemeHarness />);
    expect(
      screen.getByRole("button", { name: "使用八奈见主题" }).getAttribute("aria-pressed"),
    ).toBe("true");
    await user.click(screen.getByRole("button", { name: "使用暗色主题" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    await user.click(screen.getByRole("button", { name: "使用亮色主题" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("exposes the real next theme in the collapsed rail instead of a binary label", async () => {
    const onChange = vi.fn();
    render(<ThemePicker collapsed mode="dark" onChange={onChange} />);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "当前暗色主题，切换到八奈见主题" }));
    expect(onChange).toHaveBeenCalledWith("anime");
  });

  it("supports selecting the third theme with keyboard alone", async () => {
    const onChange = vi.fn();
    render(<ThemePicker mode="light" onChange={onChange} />);
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "使用八奈见主题" }));
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("anime");
  });
});
