// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Spin } from "./index";

afterEach(cleanup);

describe("ui/Spin native compatibility", () => {
  it("exposes busy status and a visible indicator for loading content", () => {
    render(<Spin tip="正在读取任务事件链…" />);

    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(screen.getByText("正在读取任务事件链…")).toBeTruthy();
    expect(status.querySelector(".rag-spin-indicator")).not.toBeNull();
  });
});
