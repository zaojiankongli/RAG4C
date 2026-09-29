export const uiRendererComponentNames = Object.freeze([
  "alert",
  "empty",
  "checkbox",
  "button",
  "card",
  "tooltip",
  "popover",
  "popconfirm",
  "input",
  "input-search",
  "input-text-area",
  "select",
  "input-number",
  "switch",
  "spin",
  "skeleton",
  "progress",
  "tag",
  "text",
  "title",
  "paragraph",
  "space",
  "col",
  "row",
  "radio-button",
  "radio-group",
  "segmented",
  "statistic",
  "collapse",
  "tabs",
  "drawer",
  "dialog",
  "modal",
] as const);

export type UiRendererComponent = (typeof uiRendererComponentNames)[number];
export type UiRenderer = "native" | "tdesign";

export type UiRendererSpec<Component extends UiRendererComponent> = Readonly<{
  component: Component;
  renderer: UiRenderer;
}>;

export type UiRendererRegistry = {
  readonly [Component in UiRendererComponent]: UiRendererSpec<Component>;
};

// The native branch is the safe default while the facade is being migrated.
// Opting a component into TDesign is an explicit, typed registry change.
const defaultUiRendererRegistry: UiRendererRegistry = {
  alert: { component: "alert", renderer: "native" },
  empty: { component: "empty", renderer: "native" },
  checkbox: { component: "checkbox", renderer: "native" },
  button: { component: "button", renderer: "native" },
  card: { component: "card", renderer: "native" },
  tooltip: { component: "tooltip", renderer: "native" },
  popover: { component: "popover", renderer: "native" },
  popconfirm: { component: "popconfirm", renderer: "native" },
  input: { component: "input", renderer: "native" },
  "input-search": { component: "input-search", renderer: "native" },
  "input-text-area": { component: "input-text-area", renderer: "native" },
  select: { component: "select", renderer: "native" },
  "input-number": { component: "input-number", renderer: "native" },
  switch: { component: "switch", renderer: "native" },
  spin: { component: "spin", renderer: "native" },
  skeleton: { component: "skeleton", renderer: "native" },
  progress: { component: "progress", renderer: "native" },
  tag: { component: "tag", renderer: "native" },
  text: { component: "text", renderer: "native" },
  title: { component: "title", renderer: "native" },
  paragraph: { component: "paragraph", renderer: "native" },
  space: { component: "space", renderer: "native" },
  col: { component: "col", renderer: "native" },
  row: { component: "row", renderer: "native" },
  "radio-button": { component: "radio-button", renderer: "native" },
  "radio-group": { component: "radio-group", renderer: "native" },
  segmented: { component: "segmented", renderer: "native" },
  statistic: { component: "statistic", renderer: "native" },
  collapse: { component: "collapse", renderer: "native" },
  tabs: { component: "tabs", renderer: "native" },
  drawer: { component: "drawer", renderer: "native" },
  dialog: { component: "dialog", renderer: "native" },
  modal: { component: "modal", renderer: "native" },
};

for (const component of uiRendererComponentNames) {
  Object.freeze(defaultUiRendererRegistry[component]);
}

export const UI_RENDERER_REGISTRY: UiRendererRegistry = Object.freeze(defaultUiRendererRegistry);

export interface UiRendererAdapter {
  resolve(component: string): UiRenderer;
  useTDesign(component: string): boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object";

const snapshotEntry = (registry: unknown, component: UiRendererComponent): unknown => {
  try {
    if (!isRecord(registry)) return undefined;
    const spec = registry[component];
    return isRecord(spec) ? Object.freeze({ ...spec }) : undefined;
  } catch {
    return undefined;
  }
};

export function createUiRendererAdapter(
  registry: UiRendererRegistry = UI_RENDERER_REGISTRY,
): UiRendererAdapter {
  const snapshot = Object.freeze(
    Object.fromEntries(
      uiRendererComponentNames.map((component) => [
        component,
        snapshotEntry(registry, component),
      ]),
    ),
  ) as UiRendererRegistry;

  const resolve = (component: string): UiRenderer => {
    if (!Object.prototype.hasOwnProperty.call(snapshot, component)) return "native";

    const spec = snapshot[component as UiRendererComponent];
    // Keep the boundary fail-closed even if an untyped caller mutates or
    // fabricates a registry object at runtime.
    if (!isRecord(spec) || spec.component !== component) return "native";
    return spec.renderer === "tdesign" ? "tdesign" : "native";
  };

  return Object.freeze({
    resolve,
    useTDesign: (component: string) => resolve(component) === "tdesign",
  });
}

export const uiRendererAdapter = createUiRendererAdapter();
