import { describe, expect, it } from "vitest";

import {
  UI_RENDERER_REGISTRY,
  createUiRendererAdapter,
  uiRendererComponentNames,
  uiRendererAdapter,
  type UiRendererRegistry,
} from "./rendererPolicy";

describe("ui renderer policy registry", () => {
  it("declares one native fallback policy for every conditional facade component", () => {
    expect(Object.keys(UI_RENDERER_REGISTRY).sort()).toEqual(
      [...uiRendererComponentNames].sort(),
    );
    expect(Object.values(UI_RENDERER_REGISTRY).every((spec) => spec.renderer === "native")).toBe(
      true,
    );
  });

  it("keeps renderer selection behind a keyed adapter", () => {
    const customRegistry: UiRendererRegistry = {
      ...UI_RENDERER_REGISTRY,
      button: { component: "button", renderer: "tdesign" },
    };
    const adapter = createUiRendererAdapter(customRegistry);

    expect(adapter.resolve("button")).toBe("tdesign");
    expect(adapter.useTDesign("button")).toBe(true);
    expect(uiRendererAdapter.useTDesign("button")).toBe(false);
  });

  it("pins the policy snapshot when a caller mutates its source object", () => {
    const mutableRegistry = {
      ...UI_RENDERER_REGISTRY,
      button: { component: "button", renderer: "tdesign" },
    } as UiRendererRegistry;
    const adapter = createUiRendererAdapter(mutableRegistry);

    (mutableRegistry as { button: { component: string; renderer: string } }).button = {
      component: "button",
      renderer: "native",
    };

    expect(adapter.useTDesign("button")).toBe(true);
  });

  it("keeps the exported registry and its entries immutable", () => {
    expect(Object.isFrozen(uiRendererComponentNames)).toBe(true);
    expect(Object.isFrozen(UI_RENDERER_REGISTRY)).toBe(true);
    expect(Object.isFrozen(UI_RENDERER_REGISTRY.button)).toBe(true);
  });

  it.each(["missing", "toString", "__proto__"])(
    "fails closed for an unknown or prototype key: %s",
    (component) => {
      expect(uiRendererAdapter.resolve(component)).toBe("native");
      expect(uiRendererAdapter.useTDesign(component)).toBe(false);
    },
  );

  it("fails closed when a registry entry is malformed at runtime", () => {
    const malformed = {
      ...UI_RENDERER_REGISTRY,
      button: { component: "card", renderer: "tdesign" },
    } as unknown as UiRendererRegistry;
    const adapter = createUiRendererAdapter(malformed);

    expect(adapter.resolve("button")).toBe("native");
    expect(adapter.useTDesign("button")).toBe(false);
  });

  it("fails closed for malformed registry containers and throwing entries", () => {
    const throwingRegistry = Object.create(null) as UiRendererRegistry;
    Object.defineProperty(throwingRegistry, "button", {
      get() {
        throw new Error("registry read failed");
      },
    });

    expect(createUiRendererAdapter(null as unknown as UiRendererRegistry).resolve("button")).toBe(
      "native",
    );
    expect(createUiRendererAdapter(throwingRegistry).resolve("button")).toBe("native");

    const throwingEntryRegistry = Object.create(null) as UiRendererRegistry;
    Object.defineProperty(throwingEntryRegistry, "button", {
      value: new Proxy(
        { component: "button", renderer: "tdesign" },
        {
          ownKeys() {
            throw new Error("entry enumeration failed");
          },
        },
      ),
    });
    expect(createUiRendererAdapter(throwingEntryRegistry).resolve("button")).toBe("native");
  });
});
