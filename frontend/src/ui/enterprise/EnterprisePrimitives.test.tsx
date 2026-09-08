// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AuthorityBanner,
  LifecycleRail,
  MetricStrip,
  WorkspaceScopeBar,
  type LifecycleStage,
  type MetricStripItem,
} from "./index";

afterEach(cleanup);

describe("WorkspaceScopeBar", () => {
  it("exposes the enterprise scope, actor and health context", () => {
    render(
      <WorkspaceScopeBar
        organizationLabel="RAG4C 示例企业"
        knowledgeBaseLabel="产品知识库"
        environmentLabel="生产环境"
        healthLabel="服务正常"
        healthTone="success"
        actorLabel="饶策"
        actorRole="知识库管理员"
      />,
    );

    const bar = screen.getByRole("region", { name: "当前企业工作区" });
    expect(within(bar).getByText("RAG4C 示例企业")).toBeTruthy();
    expect(within(bar).getByText("产品知识库")).toBeTruthy();
    expect(within(bar).getByText("生产环境")).toBeTruthy();
    expect(within(bar).getByText("服务正常").closest(".t-tag")).not.toBeNull();
    expect(within(bar).getByText("饶策")).toBeTruthy();
    expect(within(bar).getByText("知识库管理员")).toBeTruthy();
  });

  it("submits the global search from the required labelled input", () => {
    const onSearch = vi.fn();
    render(
      <WorkspaceScopeBar
        organizationLabel="RAG4C 示例企业"
        knowledgeBaseLabel="产品知识库"
        environmentLabel="生产环境"
        healthLabel="服务正常"
        actorLabel="饶策"
        actorRole="管理员"
        searchPlaceholder="搜索知识、文档或问答"
        onSearch={onSearch}
      />,
    );

    const input = screen.getByRole("searchbox", { name: "全局知识搜索" });
    expect(input.getAttribute("placeholder")).toBe("搜索知识、文档或问答");
    fireEvent.change(input, { target: { value: "  权限继承  " } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onSearch).toHaveBeenCalledOnce();
    expect(onSearch).toHaveBeenCalledWith("权限继承");
  });

  it("provides explicit notification and audit actions when handlers are available", () => {
    const onOpenNotifications = vi.fn();
    const onOpenAudit = vi.fn();
    render(
      <WorkspaceScopeBar
        organizationLabel="RAG4C 示例企业"
        knowledgeBaseLabel="产品知识库"
        environmentLabel="生产环境"
        healthLabel="服务正常"
        actorLabel="饶策"
        actorRole="管理员"
        onOpenNotifications={onOpenNotifications}
        onOpenAudit={onOpenAudit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "打开通知中心" }));
    fireEvent.click(screen.getByRole("button", { name: "打开审计日志" }));
    expect(onOpenNotifications).toHaveBeenCalledOnce();
    expect(onOpenAudit).toHaveBeenCalledOnce();
  });
});

describe("AuthorityBanner", () => {
  it("keeps authoritative, projected and degraded states explicit", () => {
    const { rerender } = render(
      <AuthorityBanner
        title="目录真账"
        description="当前内容直接来自企业目录数据库。"
        tone="authoritative"
        badgeLabel="权威数据"
      />,
    );

    let banner = screen.getByRole("status", { name: "目录真账" });
    expect(banner.classList.contains("is-authoritative")).toBe(true);
    expect(within(banner).getByText("权威数据")).toBeTruthy();

    rerender(
      <AuthorityBanner
        title="索引投影"
        description="检索索引落后于目录版本。"
        tone="warning"
        badgeLabel="需要关注"
      />,
    );
    banner = screen.getByRole("status", { name: "索引投影" });
    expect(banner.classList.contains("is-warning")).toBe(true);
  });
});

describe("LifecycleRail", () => {
  const stages: LifecycleStage[] = [
    { id: "source", label: "来源", status: "complete", meta: "12 个连接器" },
    { id: "parse", label: "解析", status: "current", description: "正在处理 3 篇文档" },
    { id: "index", label: "索引", status: "warning", description: "2 个版本待同步" },
    { id: "answer", label: "回答", status: "pending" },
  ];

  it("derives the horizontal track count from the supplied lifecycle", () => {
    render(<LifecycleRail stages={stages.slice(0, 3)} ariaLabel="三阶段链路" />);

    const rail = screen.getByRole("list", { name: "三阶段链路" });
    expect(rail.style.getPropertyValue("--enterprise-lifecycle-count")).toBe("3");
  });

  it("renders an ordered lifecycle and marks the current stage for assistive technology", () => {
    render(<LifecycleRail stages={stages} ariaLabel="知识处理链路" />);

    const rail = screen.getByRole("list", { name: "知识处理链路" });
    const items = within(rail).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items.map((item) => item.textContent)).toEqual([
      "来源12 个连接器",
      "解析正在处理 3 篇文档",
      "索引2 个版本待同步",
      "回答",
    ]);
    expect(items[1].getAttribute("aria-current")).toBe("step");
    expect(items[2].classList.contains("is-warning")).toBe(true);
  });
});

describe("MetricStrip", () => {
  const metrics: MetricStripItem[] = [
    { id: "documents", label: "知识文档", value: "1,248", unit: "篇", delta: "较昨日 +12", tone: "primary" },
    { id: "coverage", label: "索引一致率", value: "98.6", unit: "%", tone: "success", hint: "目录版本与检索投影一致的文档占比" },
  ];

  it("renders compact enterprise metrics with values, units and semantic labels", () => {
    render(<MetricStrip metrics={metrics} ariaLabel="知识库核心指标" columns={4} />);

    const strip = screen.getByRole("region", { name: "知识库核心指标" });
    expect(strip.getAttribute("data-columns")).toBe("4");
    expect(within(strip).getByText("知识文档")).toBeTruthy();
    expect(within(strip).getByText("1,248")).toBeTruthy();
    expect(within(strip).getByText("篇")).toBeTruthy();
    expect(within(strip).getByText("索引一致率")).toBeTruthy();
    expect(within(strip).getByText("98.6")).toBeTruthy();
  });


  it("keeps every definition-list child within valid term and description semantics", () => {
    render(<MetricStrip metrics={metrics} />);

    const label = screen.getByText("知识文档");
    const list = label.closest("dl");
    expect(list).not.toBeNull();
    expect(Array.from(list?.children ?? []).map((element) => element.tagName)).toEqual([
      "DT",
      "DD",
      "DD",
    ]);
    expect(screen.getByText("较昨日 +12").tagName).toBe("DD");
  });

  it("makes metric explanations available to keyboard users", () => {
    render(<MetricStrip metrics={metrics} />);

    const trigger = screen.getByText("索引一致率").closest("[tabindex='0']");
    expect(trigger).not.toBeNull();
    fireEvent.focus(trigger as HTMLElement);
    expect(screen.getByRole("tooltip").textContent).toContain("目录版本与检索投影一致");
  });
});
