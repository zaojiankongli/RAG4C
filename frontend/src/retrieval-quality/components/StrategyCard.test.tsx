// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RetrievalVariantDraft } from "../model/contracts";
import StrategyCard from "./StrategyCard";

afterEach(cleanup);

const value: RetrievalVariantDraft = {
  clientId: "variant-a",
  name: "策略 A",
  route_target: "auto",
  top_k: 8,
  hybrid_search_on: true,
  rerank_on: false,
  graph_retrieval_on: false,
  sentence_window_on: false,
  source_diversity: "off",
};

describe("StrategyCard", () => {
  it("renders the native facade controls and emits normalized field updates", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <StrategyCard
        value={value}
        index={0}
        canRemove
        canDuplicate
        onChange={onChange}
        onDuplicate={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(document.querySelector(".rag-card")).not.toBeNull();
    expect(screen.getByText("自动路由")).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "策略 1 名称" }) as HTMLInputElement).value).toBe(
      "策略 A",
    );

    const name = screen.getByRole("textbox", { name: "策略 1 名称" });
    await user.clear(name);
    await user.type(name, "策略 A+");
    expect(onChange).toHaveBeenLastCalledWith({ ...value, name: "策略 A+" });

    const hybrid = screen.getByRole("checkbox", { name: "策略 1 混合检索" });
    expect((hybrid as HTMLInputElement).checked).toBe(true);
    await user.click(hybrid);
    expect(onChange).toHaveBeenLastCalledWith({ ...value, hybrid_search_on: false });
  });

  it("keeps duplicate and remove actions available through the facade", async () => {
    const user = userEvent.setup();
    const onDuplicate = vi.fn();
    const onRemove = vi.fn();
    render(
      <StrategyCard
        value={value}
        index={0}
        canRemove
        canDuplicate
        onChange={vi.fn()}
        onDuplicate={onDuplicate}
        onRemove={onRemove}
      />,
    );

    await user.click(screen.getByRole("button", { name: "复制策略 策略 A" }));
    await user.click(screen.getByRole("button", { name: "删除策略 策略 A" }));
    expect(onDuplicate).toHaveBeenCalledOnce();
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("keeps a cleared Top K value defined so validation can report it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <StrategyCard
        value={value}
        index={0}
        canRemove
        canDuplicate
        onChange={onChange}
        onDuplicate={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    await user.clear(screen.getByRole("spinbutton", { name: "策略 1 Top K" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, top_k: 0 });
  });

  it("marks a Top K validation error on the actual input", () => {
    render(
      <StrategyCard
        value={value}
        index={0}
        errors={{ top_k: "Top K 必须是 1 到 50 的整数" }}
        canRemove
        canDuplicate
        onChange={vi.fn()}
        onDuplicate={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("spinbutton", { name: "策略 1 Top K" }).getAttribute("aria-invalid"),
    ).toBe("true");
  });
});
