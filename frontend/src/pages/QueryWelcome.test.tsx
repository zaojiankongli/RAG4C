// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import QueryWelcome from "./QueryWelcome";

afterEach(cleanup);
describe("QueryWelcome", () => {
  it("starts with an invitation and two examples, with the explanation closed", () => {
    render(<QueryWelcome onPick={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "今天，想弄明白什么？" })).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(3);
    const disclosure = screen.getByText("回答会怎样使用资料？").closest("details")!;
    expect(disclosure.open).toBe(false);
    fireEvent.click(screen.getByText("回答会怎样使用资料？"));
    expect(disclosure.open).toBe(true);
    expect(screen.getByRole("list", { name: "回答依据路径" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("资料范围由页面顶部选择器控制")).toBeTruthy();
  });
  it("starts a query when a suggested question is selected", () => {
    const onPick = vi.fn();
    render(<QueryWelcome onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "公司的报销流程是什么？" }));
    expect(onPick).toHaveBeenCalledOnce();
    expect(onPick).toHaveBeenCalledWith("公司的报销流程是什么？");
  });
  it("offers the remaining examples on demand without sending a query", () => {
    const onPick = vi.fn();
    render(<QueryWelcome onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "换一组" }));
    expect(screen.queryByRole("button", { name: "公司的报销流程是什么？" })).toBeNull();
    expect(screen.getByRole("button", { name: "入职需要准备哪些材料？" })).toBeTruthy();
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "入职需要准备哪些材料？" }));
    expect(onPick).toHaveBeenCalledWith("入职需要准备哪些材料？");
    fireEvent.click(screen.getByRole("button", { name: "换一组" }));
    expect(screen.getByRole("button", { name: "公司的报销流程是什么？" })).toBeTruthy();
  });
});
