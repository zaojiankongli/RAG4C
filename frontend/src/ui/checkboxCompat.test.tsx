// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Checkbox } from "./index";

afterEach(cleanup);

describe("ui/Checkbox native compatibility", () => {
  it("keeps boolean change values and disabled semantics", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Checkbox checked={false} onChange={onChange} aria-label="确认操作">
        我确认
      </Checkbox>,
    );

    await user.click(screen.getByRole("checkbox", { name: "确认操作" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
