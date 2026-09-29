// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Button, Card, Empty, Layout, Menu, Progress, Table, Tag, Tooltip } from "./index";

afterEach(cleanup);

describe("TDesign compatibility contracts", () => {
  it("keeps button icons and labels visible", () => {
    render(
      <Button icon={<span data-testid="button-icon">↻</span>}>
        刷新
      </Button>,
    );

    const button = screen.getByRole("button", { name: "↻刷新" });
    expect(within(button).getByTestId("button-icon")).toBeTruthy();
  });

  it("renders an app-owned empty state without leaking TDesign-only props", () => {
    render(
      <Empty
        type="empty"
        title="暂无对账事项"
        description="运行状态一致，暂不需要人工介入。"
        data-testid="empty-state"
        id="native-empty-id"
        role="status"
        tabIndex={0}
        aria-label="暂无对账事项"
      />,
    );

    const empty = screen.getByTestId("empty-state");
    expect(empty.classList.contains("rag-empty")).toBe(true);
    expect(within(empty).getByText("暂无对账事项")).toBeTruthy();
    expect(within(empty).getByText("运行状态一致，暂不需要人工介入。")).toBeTruthy();
    expect(empty.getAttribute("type")).toBeNull();
    expect(empty.id).toBe("native-empty-id");
    expect(empty.getAttribute("role")).toBe("status");
    expect(empty.getAttribute("tabindex")).toBe("0");
    expect(empty.getAttribute("aria-label")).toBe("暂无对账事项");
  });

  it("preserves card title, extra actions, ARIA attributes, and root style", () => {
    render(
      <Card
        title="知识健康度"
        extra={<button type="button">查看详情</button>}
        role="region"
        aria-labelledby="knowledge-health-heading"
        style={{ minHeight: 180 }}
      >
        <h2 id="knowledge-health-heading">健康指标</h2>
      </Card>,
    );

    const card = screen.getByRole("region");
    expect(card.getAttribute("aria-labelledby")).toBe("knowledge-health-heading");
    expect(card.style.minHeight).toBe("180px");
    expect(within(card).getByText("知识健康度")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "查看详情" })).toBeTruthy();
  });

  it("maps the historical card header prop without leaking it to native DOM", () => {
    render(
      <Card
        title="不应覆盖"
        header={<h2>历史标题</h2>}
        bordered={false}
        data-testid="header-card"
      >
        内容
      </Card>,
    );

    const card = screen.getByTestId("header-card");
    expect(within(card).getByText("历史标题")).toBeTruthy();
    expect(within(card).queryByText("不应覆盖")).toBeNull();
    expect(card.getAttribute("header")).toBeNull();
    expect(card.classList.contains("is-borderless")).toBe(true);
    expect(card.getAttribute("bordered")).toBeNull();
  });

  it("falls back to title when the historical header slot is falsy", () => {
    render(
      <Card title="标题回退" header="" data-testid="falsy-header-card">
        内容
      </Card>,
    );

    expect(within(screen.getByTestId("falsy-header-card")).getByText("标题回退")).toBeTruthy();
  });

  it("shows tooltip content for pointer hover and keyboard focus", () => {
    render(
      <Tooltip title="指标说明" trigger={["hover", "focus"]}>
        <button type="button">知识指标</button>
      </Tooltip>,
    );

    const trigger = screen.getByRole("button", { name: "知识指标" });
    fireEvent.mouseEnter(trigger);
    expect(screen.getByRole("tooltip").textContent).toBe("指标说明");

    fireEvent.mouseLeave(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.focus(trigger);
    expect(screen.getByRole("tooltip").textContent).toBe("指标说明");
    fireEvent.blur(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it.each([
    ["success", "is-success"],
    ["error", "is-danger"],
    ["warning", "is-warning"],
    ["processing", "is-primary"],
  ])("keeps the %s tag state visually distinguishable", (color, stateClass) => {
    render(<Tag color={color}>状态</Tag>);

    expect(screen.getByText("状态").classList.contains(stateClass)).toBe(true);
  });

  it("normalizes TDesign Tag metadata for native rendering without leaking props", () => {
    render(
      <Tag theme="success" variant="light-outline" size="small" data-testid="native-tag">
        兼容标签
      </Tag>,
    );

    const tag = screen.getByTestId("native-tag");
    expect(tag.classList.contains("is-success")).toBe(true);
    expect(tag.classList.contains("is-light-outline")).toBe(true);
    expect(tag.classList.contains("is-small")).toBe(true);
    expect(tag.getAttribute("theme")).toBeNull();
    expect(tag.getAttribute("variant")).toBeNull();
    expect(tag.getAttribute("size")).toBeNull();
  });

  it("keeps grouped navigation valid as a native list", () => {
    render(
      <Menu
        selectedKeys={["overview"]}
        items={[
          {
            type: "group",
            label: "知识库",
            children: [
              { key: "overview", label: "知识概览" },
              { key: "documents", label: "文档管理" },
            ],
          },
        ]}
      />,
    );

    const list = screen.getByRole("list");
    expect(screen.getByText("知识库").getAttribute("role")).not.toBe("presentation");
    expect([...list.children].every((child) => child.tagName === "LI")).toBe(true);
    for (const item of screen.getAllByRole("listitem")) {
      expect(["UL", "OL"]).toContain(item.parentElement?.tagName);
    }
  });

  it("collapses the TDesign sider to its configured mobile width", () => {
    render(
      <Layout.Sider width={232} collapsedWidth={0} collapsed>
        导航
      </Layout.Sider>,
    );

    const sider = screen.getByText("导航").closest("aside");
    expect(sider).not.toBeNull();
    expect(sider?.style.width).toBe("0px");
    expect(sider?.style.minWidth).toBe("0px");
    expect(sider?.style.maxWidth).toBe("0px");
    expect(sider?.style.flex).toBe("0 0 0px");
  });

  it("exposes native progressbar semantics and current value", () => {
    render(<Progress percent={64} status="active" aria-label="解析进度" />);

    const progress = screen.getByRole("progressbar", { name: "解析进度" });
    expect(progress.getAttribute("aria-valuemin")).toBe("0");
    expect(progress.getAttribute("aria-valuemax")).toBe("100");
    expect(progress.getAttribute("aria-valuenow")).toBe("64");
  });

  it("resolves nested table dataIndex paths for values and render callbacks", () => {
    render(
      <Table
        rowKey="id"
        pagination={false}
        dataSource={[{ id: "parse", stat: { count: 7, p95: 128 } }]}
        columns={[
          { title: "次数", dataIndex: ["stat", "count"] },
          {
            title: "P95",
            dataIndex: ["stat", "p95"],
            render: (value: number) => `${value} ms`,
          },
        ]}
      />,
    );

    expect(screen.getByRole("cell", { name: "7" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "128 ms" })).toBeTruthy();
  });

  it("keeps table sizing, alignment, and overflow focus at the shared boundary", () => {
    render(
      <Table
        size="small"
        verticalAlign="top"
        scroll={{ x: 900 }}
        scrollContainerProps={{ tabIndex: 0, "aria-label": "可横向滚动的表格" }}
        pagination={false}
        dataSource={[{ id: "task", label: "任务" }]}
        rowKey="id"
        columns={[{ title: "任务", dataIndex: "label" }]}
      />,
    );

    const scroll = screen.getByLabelText("可横向滚动的表格");
    expect(scroll.classList.contains("rag-table-scroll")).toBe(true);
    expect(scroll.getAttribute("tabindex")).toBe("0");
    const table = within(scroll).getByRole("table");
    expect(table.classList.contains("is-small")).toBe(true);
    expect(table.classList.contains("is-align-top")).toBe(true);
    expect((table as HTMLTableElement).style.minWidth).toBe("900px");
  });
});
