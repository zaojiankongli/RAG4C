// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useThemeMode } from "./useThemeMode";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
  document.querySelector('meta[name="theme-color"]')?.remove();
});

describe("useThemeMode storage degradation", () => {
  it("keeps the app mountable and theme state usable when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota blocked");
    });

    const { result } = renderHook(() => useThemeMode());
    expect(result.current.mode).toBe("light");

    act(() => result.current.setMode("anime"));

    expect(result.current.mode).toBe("anime");
    expect(document.documentElement.dataset.theme).toBe("anime");
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe(
      "#f2f7fc",
    );
  });
});
