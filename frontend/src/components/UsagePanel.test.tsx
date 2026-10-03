// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import UsagePanel from "./UsagePanel";
import type { QueryUsage } from "../types/rag";

afterEach(cleanup);

function usage(over: Partial<QueryUsage> = {}): QueryUsage {
  return {
    calls: 3,
    cached_calls: 1,
    failures: 0,
    prompt_tokens: 1000,
    completion_tokens: 500,
    total_tokens: 1500,
    saved_prompt_tokens: 0,
    saved_completion_tokens: 0,
    saved_total_tokens: 0,
    cost: 0.0123,
    cost_priced: true,
    unpriced_total_tokens: 0,
    by_slot: [],
    ...over,
  };
}

describe("UsagePanel", () => {
  it("展示调用数、token 与成本", () => {
    render(<UsagePanel usage={usage()} />);
    expect(screen.getByTestId("usage-panel")).toBeTruthy();
    expect(screen.getByText("调用")).toBeTruthy();
    expect(screen.getByText("3 次")).toBeTruthy();
    expect(screen.getByText("1,500")).toBeTruthy();
    expect(screen.getByText("0.0123")).toBeTruthy();
  });

  it("未定价时不把 cost=0 渲染成免费", () => {
    // 缺省价格表时后端 cost 恒为 0，但那意思是"算不出钱"不是"不花钱"
    render(
      <UsagePanel
        usage={usage({ cost: 0, cost_priced: false, unpriced_total_tokens: 1500 })}
      />,
    );
    expect(screen.getByText("未配置价格表")).toBeTruthy();
    // 且如实报出未定价的 token 量
    expect(screen.getByText(/1,500 token 未定价/)).toBeTruthy();
    // 关键：页面上不应出现 "0.0000" 这种会被读成免费的金额
    expect(screen.queryByText("0.0000")).toBeNull();
  });

  it("台账没开时不渲染任何内容", () => {
    const { container } = render(<UsagePanel usage={undefined} />);
    expect(container.innerHTML).toBe("");
  });

  it("calls=0 时不展示（避免展示一排 0 误导）", () => {
    const { container } = render(<UsagePanel usage={usage({ calls: 0 })} />);
    expect(container.innerHTML).toBe("");
  });

  it("按槽位展开明细，含缓存与失败", () => {
    render(
      <UsagePanel
        usage={usage({
          by_slot: [
            {
              model: "qwen2.5",
              slot: "triplet",
              calls: 2,
              cached_calls: 1,
              failures: 1,
              prompt_tokens: 800,
              completion_tokens: 200,
              total_tokens: 1000,
              saved_prompt_tokens: 0,
              saved_completion_tokens: 0,
              saved_total_tokens: 0,
              cost: 0.001,
              unpriced_total_tokens: 0,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("triplet")).toBeTruthy();
    expect(screen.getByText("qwen2.5")).toBeTruthy();
    expect(screen.getByText(/缓存 1/)).toBeTruthy();
    expect(screen.getByText(/失败 1/)).toBeTruthy();
  });

  it("缓存有节省时给出提示", () => {
    render(<UsagePanel usage={usage({ saved_total_tokens: 800 })} />);
    expect(screen.getByText("缓存节省")).toBeTruthy();
    expect(screen.getByText("800")).toBeTruthy();
  });

  it("失败调用数显式标红提示", () => {
    render(<UsagePanel usage={usage({ failures: 2 })} />);
    expect(screen.getByText("失败调用")).toBeTruthy();
    expect(screen.getByText("2 次")).toBeTruthy();
  });
});
