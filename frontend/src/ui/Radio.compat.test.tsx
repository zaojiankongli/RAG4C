// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Radio } from "./index";

afterEach(cleanup);

describe("Radio compatibility facade", () => {
  it("renders native accessible radios and emits the event-shaped value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <Radio.Group value="none" onChange={onChange} aria-label="选择检查方式">
        <Radio.Button value="none">快速演练</Radio.Button>
        <Radio.Button value="rag:answer_query">真实资料评测</Radio.Button>
      </Radio.Group>,
    );

    const dryRun = screen.getByRole("radio", { name: "快速演练" });
    const realRun = screen.getByRole("radio", { name: "真实资料评测" });
    expect((dryRun as HTMLInputElement).checked).toBe(true);
    expect((realRun as HTMLInputElement).checked).toBe(false);

    await user.click(realRun);

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]?.[0]?.target?.value).toBe("rag:answer_query");
  });

  it("supports the options contract without rendering TDesign-only markup", () => {
    render(
      <Radio.Group
        value="relevant"
        options={[
          { label: "相关", value: "relevant" },
          { label: "不相关", value: "irrelevant" },
        ]}
        aria-label="相关性"
      />,
    );

    expect((screen.getByRole("radio", { name: "相关" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: "不相关" }) as HTMLInputElement).checked).toBe(false);
  });
});
