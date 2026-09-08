// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Button, Card, Layout, Menu, Progress, Table, Tag, Tooltip } from "./index";

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
});
