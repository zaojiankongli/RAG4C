// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => component === "card",
  },
}));

import { Card } from "./index";

afterEach(cleanup);

describe("ui/Card TDesign adapter", () => {
  it("forwards header content and bordered state without duplicating the title", () => {
    const { container } = render(
      <Card
        title="fallback title"
        header={<h2 id="card-heading">header slot</h2>}
        bordered={false}
        role="region"
        aria-labelledby="card-heading"
        data-testid="tdesign-card"
      >
        body
      </Card>,
    );

    const root = screen.getByTestId("tdesign-card");
    expect(root.getAttribute("role")).toBe("region");
    expect(root.getAttribute("aria-labelledby")).toBe("card-heading");
    expect(root.querySelector(`#${root.getAttribute("aria-labelledby")}`)).not.toBeNull();
    expect(root.querySelector(".t-card")?.classList.contains("t-card--bordered")).toBe(false);
    expect(within(root).getByRole("heading", { name: "header slot" })).toBeTruthy();
    expect(within(root).queryByText("fallback title")).toBeNull();
    expect(container.querySelector(".t-card__body")?.textContent).toContain("body");
  });

  it("keeps title and extra actions when no header slot is supplied", () => {
    const { container } = render(
      <Card title="title only" extra={<button type="button">extra action</button>}>
        body
      </Card>,
    );

    expect(within(container).getByText("title only")).toBeTruthy();
    expect(within(container).getByRole("button", { name: "extra action" })).toBeTruthy();
  });
});
