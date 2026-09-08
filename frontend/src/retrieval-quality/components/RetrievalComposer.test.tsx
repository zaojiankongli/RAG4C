// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComposerDraft } from "../model/contracts";
import RetrievalComposer from "./RetrievalComposer";

const initial: ComposerDraft = { query: "公司的报销流程是什么？", acl: [], variants: [{ clientId: "a", name: "策略 A", route_target: "auto", top_k: 8, hybrid_search_on: true, rerank_on: true, graph_retrieval_on: false, sentence_window_on: false, source_diversity: "off" }] };

function Harness({ onRun }: { onRun: ReturnType<typeof vi.fn> }) {
  const [value, setValue] = useState(initial);
  return <RetrievalComposer value={value} onChange={setValue} onRun={onRun} running={false} />;
}

afterEach(cleanup);

describe("RetrievalComposer", () => {
  it("duplicates strategy cards, blocks duplicate names, and labels pure retrieval", async () => {
    const user = userEvent.setup(); const onRun = vi.fn(); render(<Harness onRun={onRun} />);
    await user.click(screen.getByRole("button", { name: "复制策略 策略 A" }));
    expect(screen.getAllByRole("group", { name: /检索策略/ })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "运行纯检索对比（不生成答案）" }));
    expect(screen.getByText("策略名称不能重复")).toBeTruthy();
    expect(onRun).not.toHaveBeenCalled();
  });

  it("uses sample questions without auto-running", async () => {
    const user = userEvent.setup(); const onRun = vi.fn(); render(<Harness onRun={onRun} />);
    await user.click(screen.getByRole("button", { name: "样例：员工年假制度是怎么规定的？" }));
    expect((screen.getByLabelText("检索问题") as HTMLTextAreaElement).value).toBe("员工年假制度是怎么规定的？");
    expect(onRun).not.toHaveBeenCalled();
  });
});


