import type { ReactNode } from "react";

export type YanamiIconName =
  "agent" | "knowledge" | "documents" | "integration" | "organization" | "settings" | "search";

/** Decorative SVG art only replaces the icon; the navigation keeps its accessible label. */
export default function ThemedNavIcon({
  name,
  children,
}: {
  name: YanamiIconName;
  children: ReactNode;
}) {
  return (
    <span className="themed-nav-icon" aria-hidden="true">
      <span className="themed-nav-icon-default">{children}</span>
      <span className={`themed-nav-icon-yanami yanami-icon-${name}`} />
    </span>
  );
}
