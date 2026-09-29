// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Skeleton } from "./index";

afterEach(cleanup);

describe("ui/Skeleton compat layer", () => {
  it("renders the requested native placeholder lines and loading semantics", () => {
    render(
      <Skeleton
        active
        title={false}
        paragraph={{ rows: 2 }}
        aria-label="正在加载回答"
        data-testid="skeleton"
      />,
    );

    const root = screen.getByTestId("skeleton");
    expect(root.className).toContain("rag-skeleton");
    expect(root.className).toContain("is-active");
    expect(root.getAttribute("aria-busy")).toBe("true");
    expect(root.querySelectorAll(".rag-skeleton-line")).toHaveLength(2);
    expect(root.getAttribute("active")).toBeNull();
    expect(root.getAttribute("paragraph")).toBeNull();
  });

  it("keeps the default title and three paragraph rows", () => {
    render(<Skeleton data-testid="default-skeleton" />);

    const root = screen.getByTestId("default-skeleton");
    expect(root.querySelector(".rag-skeleton-title")).not.toBeNull();
    expect(root.querySelectorAll(".rag-skeleton-line")).toHaveLength(4);
  });

  it("supports an explicit paragraph=false contract without creating rows", () => {
    render(
      <Skeleton active={false} paragraph={false} data-testid="empty-skeleton" />,
    );

    const root = screen.getByTestId("empty-skeleton");
    expect(root.className).not.toContain("is-active");
    expect(root.querySelectorAll(".rag-skeleton-line")).toHaveLength(1);
    expect(root.querySelector(".rag-skeleton-title")).not.toBeNull();
  });
});
