// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { applyThemeMode } from "./tokens";

describe("TDesign theme bridge", () => {
  it("synchronizes the RAG4C and TDesign theme contracts", () => {
    applyThemeMode("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");

    applyThemeMode("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
