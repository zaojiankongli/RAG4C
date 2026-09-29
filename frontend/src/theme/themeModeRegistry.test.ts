import { describe, expect, it } from "vitest";
import {
  getThemeModeSpec,
  isThemeMode,
  nextThemeMode,
  parseStoredThemeMode,
  themeModeNames,
} from "./themeModeRegistry";

describe("theme mode registry", () => {
  it("declares every supported mode with browser metadata", () => {
    expect(themeModeNames).toEqual(["light", "dark", "anime"]);
    expect(themeModeNames.map((mode) => getThemeModeSpec(mode).mode)).toEqual(themeModeNames);
    expect(themeModeNames.map((mode) => getThemeModeSpec(mode).browserChromeColor)).toEqual([
      "#ffffff",
      "#12161f",
      "#f2f7fc",
    ]);
    expect(themeModeNames.map((mode) => getThemeModeSpec(mode).colorScheme)).toEqual([
      "light",
      "dark",
      "light",
    ]);
  });

  it("keeps persistence parsing fail-closed", () => {
    expect(parseStoredThemeMode("light")).toBe("light");
    expect(parseStoredThemeMode("anime")).toBe("anime");
    expect(parseStoredThemeMode("unknown")).toBeUndefined();
    expect(parseStoredThemeMode(null)).toBeUndefined();
    expect(isThemeMode("dark")).toBe(true);
    expect(isThemeMode("DARK")).toBe(false);
    expect(isThemeMode("toString")).toBe(false);
    expect(isThemeMode("__proto__")).toBe(false);
    expect(isThemeMode(42)).toBe(false);
  });

  it("keeps the intentional light -> dark -> anime -> light cycle", () => {
    expect(themeModeNames.map(nextThemeMode)).toEqual(["dark", "anime", "light"]);
  });
});
