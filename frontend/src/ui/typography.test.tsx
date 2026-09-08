// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Typography } from "./index";

describe("Typography compatibility", () => {
  it("maps strong to font weight without leaking a non-boolean DOM attribute", () => {
    render(<Typography.Text strong>Enterprise evidence</Typography.Text>);
    const text = screen.getByText("Enterprise evidence");
    expect(text.getAttribute("strong")).toBeNull();
    expect((text as HTMLElement).style.fontWeight).not.toBe("");
  });
});
