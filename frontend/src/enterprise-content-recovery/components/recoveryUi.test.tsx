// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RecoveryStateNotice } from "./recoveryUi";

afterEach(cleanup);

describe("RecoveryStateNotice", () => {
  it("keeps loading, unavailable, error, partial, and empty authority copy explicit", () => {
    const { rerender } = render(
      <RecoveryStateNotice status="loading" resourceLabel="回收条目" />,
    );
    expect(screen.getByText("正在读取回收条目权威…")).toBeTruthy();

    rerender(<RecoveryStateNotice status="unavailable" resourceLabel="回收条目" />);
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");

    rerender(<RecoveryStateNotice status="error" resourceLabel="回收条目" />);
    expect(screen.getByRole("alert").textContent).toContain("读取失败");

    rerender(
      <RecoveryStateNotice status="partial" resourceLabel="回收条目" invalidItemCount={2} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("2 条记录");

    rerender(
      <RecoveryStateNotice
        status="empty"
        resourceLabel="回收条目"
        emptyTitle="回收站为空"
        emptyDescription="暂无可验证回收条目。"
      />,
    );
    expect(screen.getByText("回收站为空")).toBeTruthy();
    expect(screen.getByText("暂无可验证回收条目。")).toBeTruthy();
  });
});
