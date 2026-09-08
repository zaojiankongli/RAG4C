// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AutomationActionRequest,
  AutomationEvent,
  AutomationRule,
  AutomationRuleRevision,
  AutomationRun,
  AutomationSummary,
} from "../model/automationModel";
import AutomationCenter from "./AutomationCenter";
import type { AutomationCenterController } from "./types";

const digestA = "a".repeat(64);
const digestB = "b".repeat(64);
const digestC = "c".repeat(64);
const time = "2026-08-30T08:00:00.000Z";

const rule: AutomationRule = {
  id: "rule-failed-task",
  tenant_id: "tenant-a",
  name: "失败任务通知",
  status: "active",
  revision: 3,
  current_revision_id: "revision-3",
  workspace_id: "workspace-a",
  dataset_id: "dataset-a",
  priority: 80,
  created_at: time,
  created_by: "account-a",
  updated_at: time,
  updated_by: "account-a",
  archived_at: null,
};

const revision: AutomationRuleRevision = {
  id: "revision-3",
  tenant_id: "tenant-a",
  rule_id: rule.id,
  revision: 3,
  trigger_code: "task_failed",
  condition_code: "attempt_exhausted",
  condition_params: { minimum_attempts: 3 },
  action_plan: [
    {
      step_index: 0,
      action_code: "notify_operator",
      params: { severity: "error", category: "task_operations" },
    },
    {
      step_index: 1,
      action_code: "open_task_attention",
      params: { queue: "priority-indexing" },
    },
  ],
  definition_digest: digestA,
  created_at: time,
  created_by: "account-a",
};

const run: AutomationRun = {
  id: "run-001",
  tenant_id: "tenant-a",
  rule_id: rule.id,
  rule_revision_id: revision.id,
  trigger_event_id: "source-event-001",
  trigger_event_digest: digestB,
  status: "requested",
  condition_matched: true,
  action_count: 2,
  requested_count: 2,
  rejected_count: 0,
  started_at: time,
  completed_at: null,
  safe_error_code: null,
  safe_error: null,
};

const request: AutomationActionRequest = {
  id: "request-001",
  tenant_id: "tenant-a",
  run_id: run.id,
  rule_id: rule.id,
  step_index: 0,
  action_code: "notify_operator",
  status: "requested",
  target_kind: "operator",
  target_id: "account-a",
  target_revision: 1,
  target_digest: digestC,
  safe_params: { severity: "error" },
  safe_reason: "任务达到最大尝试次数",
  approval_request_id: null,
  notification_id: null,
  task_id: "task-001",
  requested_at: time,
  dispatched_at: null,
  applied_at: null,
  rejected_at: null,
  expires_at: "2026-08-31T08:00:00.000Z",
};

const eventOne: AutomationEvent = {
  id: "automation-event-001",
  tenant_id: "tenant-a",
  rule_id: rule.id,
  run_id: run.id,
  stream_key: "run:run-001",
  sequence: 1,
  event_type: "run_started",
  previous_event_digest: null,
  event_digest: digestA,
  actor_id: "system:automation",
  request_id: "request-001",
  safe_snapshot: { revision: 3, trigger_code: "task_failed" },
  occurred_at: time,
};

const eventTwo: AutomationEvent = {
  ...eventOne,
  id: "automation-event-002",
  sequence: 2,
  event_type: "action_requested",
  previous_event_digest: digestA,
  event_digest: digestB,
  safe_snapshot: { action_code: "notify_operator", token: "do-not-render" },
  occurred_at: "2026-08-30T08:00:03.000Z",
};

const summary: AutomationSummary = {
  tenant_id: "tenant-a",
  state: "ready",
  active_rule_count: 4,
  paused_rule_count: 1,
  failed_run_count: 2,
  pending_request_count: 3,
  as_of: time,
  reason_code: null,
};

function makeController(
  overrides: Partial<AutomationCenterController> = {},
): AutomationCenterController {
  return {
    active: true,
    summary: { status: "ready", value: summary, error: null },
    rules: {
      status: "ready",
      items: [rule],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    revisions: {
      status: "ready",
      items: [revision],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    runs: {
      status: "ready",
      items: [run],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    requests: {
      status: "ready",
      items: [request],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    activity: {
      status: "ready",
      items: [eventOne, eventTwo],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    detail: {
      status: "ready",
      value: {
        rule,
        current_revision: revision,
        revisions: [revision],
        recent_runs: [run],
        events: [eventOne, eventTwo],
      },
      error: null,
    },
    mutation: {
      status: "idle",
      error: null,
      createRule: vi.fn().mockResolvedValue({ state: "applied" }),
      createRevision: vi.fn().mockResolvedValue({ state: "applied" }),
      activateRule: vi.fn().mockResolvedValue({ state: "applied" }),
      pauseRule: vi.fn().mockResolvedValue({ state: "applied" }),
    },
    ...overrides,
  };
}

function makeEmptyContextController(): AutomationCenterController {
  return makeController({
    summary: { status: "loading", value: null, error: null },
    rules: { status: "loading", items: [], invalidItemCount: 0, nextCursor: null, error: null },
    revisions: { status: "idle", items: [], invalidItemCount: 0, nextCursor: null, error: null },
    runs: { status: "idle", items: [], invalidItemCount: 0, nextCursor: null, error: null },
    requests: { status: "idle", items: [], invalidItemCount: 0, nextCursor: null, error: null },
    activity: { status: "idle", items: [], invalidItemCount: 0, nextCursor: null, error: null },
    detail: { status: "idle", value: null, error: null },
  });
}

afterEach(cleanup);

describe("Stage25 AutomationCenter", () => {
  it("renders the header, tenant authority badge, attention board, and automation rail", () => {
    render(<AutomationCenter controller={makeController()} tenantLabel="RAG4C 企业" />);

    expect(screen.getByRole("region", { name: "自动化中心" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "自动化中心" })).toBeTruthy();
    expect(screen.getByText("RAG4C 企业")).toBeTruthy();
    expect(screen.getByRole("status", { name: "租户自动化权威" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "自动化关注面板" })).toBeTruthy();
    expect(screen.getByText("失败运行")).toBeTruthy();

    const rail = screen.getByRole("list", { name: "自动化编排路径" });
    expect(
      within(rail)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("WHEN"),
      expect.stringContaining("IF"),
      expect.stringContaining("REQUEST"),
      expect.stringContaining("EVIDENCE"),
    ]);
  });

  it("renders rules as a desktop PrimaryTable and mobile cards without duplicating the surface", () => {
    const { rerender } = render(<AutomationCenter controller={makeController()} />);

    expect(screen.getByTestId("automation-rules-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("automation-rules-mobile-cards")).toBeNull();

    rerender(<AutomationCenter controller={makeController()} mobile />);
    expect(screen.getByTestId("automation-rules-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("automation-rules-desktop-table")).toBeNull();
  });

  it("shows the activated current revision separately from a newer unactivated head", async () => {
    const user = userEvent.setup();
    const headRevision: AutomationRuleRevision = {
      ...revision,
      id: "revision-4",
      revision: 4,
      trigger_code: "source_sync_failed",
      definition_digest: digestC,
    };
    const divergedRule: AutomationRule = {
      ...rule,
      revision: 4,
      current_revision_id: revision.id,
    };
    const controller = makeController({
      rules: {
        status: "ready",
        items: [divergedRule],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
      },
      revisions: {
        status: "ready",
        items: [revision, headRevision],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
      },
      detail: { status: "idle", value: null, error: null },
    });

    render(<AutomationCenter controller={controller} />);

    expect(screen.getByText("current revision 3")).toBeTruthy();
    expect(screen.getByText("head revision 4 · 待启用")).toBeTruthy();
    expect(screen.getByText("任务失败")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "查看规则 失败任务通知" }));
    const detail = await screen.findByRole("dialog", { name: "规则详情 rule-failed-task" });
    expect(within(detail).getAllByText("任务失败").length).toBeGreaterThan(0);
    await user.click(within(detail).getByRole("button", { name: "创建下一版" }));
    const builder = await screen.findByRole("dialog", {
      name: "创建自动化规则 revision · 失败任务通知",
    });
    expect(within(builder).getByDisplayValue("来源同步失败")).toBeTruthy();
  });

  it("switches between Rules, Runs, Requests, and Activity tabs", async () => {
    const user = userEvent.setup();
    render(<AutomationCenter controller={makeController()} />);

    await user.click(screen.getByRole("tab", { name: "运行记录" }));
    expect(screen.getByText("执行请求已生成")).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "请求" }));
    expect(screen.getByText("任务达到最大尝试次数")).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "活动" }));
    expect(screen.getByTestId("automation-event-timeline")).toBeTruthy();
    expect(screen.getByText("不可变事件链")).toBeTruthy();
  });

  it("loads each lazy authority collection when its tab becomes active", async () => {
    const user = userEvent.setup();
    const controller = makeController();
    controller.runs.reload = vi.fn().mockResolvedValue(true);
    controller.requests.reload = vi.fn().mockResolvedValue(true);
    controller.activity.reload = vi.fn().mockResolvedValue(true);
    render(<AutomationCenter controller={controller} />);

    await user.click(screen.getByRole("tab", { name: "运行记录" }));
    await waitFor(() => expect(controller.runs.reload).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: "请求" }));
    await waitFor(() => expect(controller.requests.reload).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("tab", { name: "活动" }));
    await waitFor(() => expect(controller.activity.reload).toHaveBeenCalledTimes(1));
  });

  it("opens an immutable rule detail drawer and returns focus after Escape", async () => {
    const user = userEvent.setup();
    render(<AutomationCenter controller={makeController()} />);

    const trigger = screen.getByRole("button", { name: "查看规则 失败任务通知" });
    trigger.focus();
    await user.click(trigger);

    const drawer = await screen.findByRole("dialog", { name: "规则详情 rule-failed-task" });
    expect(within(drawer).getByText("不可变 revision")).toBeTruthy();
    expect(within(drawer).getAllByText(digestA).length).toBeGreaterThan(0);
    expect(within(drawer).getByTestId("automation-revision-timeline")).toBeTruthy();
    expect(within(drawer).getByText("#2 · action_requested")).toBeTruthy();
    expect(within(drawer).queryByText("do-not-render")).toBeNull();

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "规则详情 rule-failed-task" })).toBeNull(),
    );
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("closes selected rule detail when tenant or account authority context is replaced", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <AutomationCenter controller={makeController()} tenantLabel="RAG4C 企业 A" />,
    );
    await user.click(screen.getByRole("button", { name: "查看规则 失败任务通知" }));
    expect(await screen.findByRole("dialog", { name: "规则详情 rule-failed-task" })).toBeTruthy();

    rerender(
      <AutomationCenter controller={makeEmptyContextController()} tenantLabel="RAG4C 企业 B" />,
    );

    expect(screen.queryByRole("dialog", { name: "规则详情 rule-failed-task" })).toBeNull();
  });

  it("closes selected rule detail when automation capability context changes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<AutomationCenter controller={makeController()} />);
    await user.click(screen.getByRole("button", { name: "查看规则 失败任务通知" }));
    expect(await screen.findByRole("dialog", { name: "规则详情 rule-failed-task" })).toBeTruthy();

    rerender(<AutomationCenter controller={makeController()} capabilityReady={false} />);

    expect(screen.queryByRole("dialog", { name: "规则详情 rule-failed-task" })).toBeNull();
  });

  it("opens the rule builder and exposes a local preview that never executes a real action", async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<AutomationCenter controller={controller} />);

    await user.click(screen.getByRole("button", { name: "新建自动化规则" }));
    const dialog = await screen.findByRole("dialog", { name: "新建自动化规则" });
    expect(within(dialog).getByLabelText("规则名称")).toBeTruthy();
    await user.type(within(dialog).getByLabelText("规则名称"), "来源同步失败提醒");
    await user.click(within(dialog).getByRole("button", { name: "查看预览" }));
    expect(within(dialog).getByTestId("automation-preview-panel")).toBeTruthy();
    expect(within(dialog).getByText("预览不会执行真实动作")).toBeTruthy();
    expect(within(dialog).getByText(/只生成受限 Action Request/)).toBeTruthy();
    expect(controller.mutation.createRule).not.toHaveBeenCalled();
  });

  it("selects the newly created draft after the refreshed rule list arrives", async () => {
    const user = userEvent.setup();
    const createRule = vi
      .fn()
      .mockResolvedValue({ state: "applied", resource_id: "rule-new-draft" });
    const controller = makeController({
      mutation: {
        ...makeController().mutation,
        createRule,
      },
    });
    const { rerender } = render(<AutomationCenter controller={controller} />);

    await user.click(screen.getByRole("button", { name: "新建自动化规则" }));
    const dialog = await screen.findByRole("dialog", { name: "新建自动化规则" });
    await user.type(within(dialog).getByLabelText("规则名称"), "新建草稿规则");
    await user.type(within(dialog).getByLabelText("变更原因"), "验证草稿保存后保持可见");
    await user.click(within(dialog).getByRole("button", { name: "保存为草稿" }));
    await waitFor(() => expect(createRule).toHaveBeenCalledTimes(1));

    const draftRule: AutomationRule = {
      ...rule,
      id: "rule-new-draft",
      name: "新建草稿规则",
      status: "draft",
      current_revision_id: null,
      revision: 1,
    };
    rerender(
      <AutomationCenter
        controller={makeController({
          rules: { ...controller.rules, items: [draftRule, rule] },
          mutation: controller.mutation,
        })}
      />,
    );

    expect(
      await screen.findByRole("dialog", { name: "规则详情 rule-new-draft" }),
    ).toBeTruthy();
  });

  it("keeps mutation controls read-only and does not open the builder", async () => {
    const user = userEvent.setup();
    render(<AutomationCenter controller={makeController()} readOnly />);

    const create = screen.getByRole("button", { name: "新建自动化规则" });
    expect(create.getAttribute("aria-disabled")).toBe("true");
    await user.click(create);
    expect(screen.queryByRole("dialog", { name: "新建自动化规则" })).toBeNull();
    expect(screen.getByText("只读模式")).toBeTruthy();
  });

  it("keeps partial, unavailable, error, and empty states explicit", () => {
    const { rerender } = render(
      <AutomationCenter
        controller={makeController({
          rules: {
            status: "partial",
            items: [],
            invalidItemCount: 2,
            nextCursor: null,
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("部分自动化规则无法读取");

    rerender(
      <AutomationCenter
        controller={makeController({
          rules: {
            status: "unavailable",
            items: [],
            invalidItemCount: 0,
            nextCursor: null,
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("自动化规则暂不可用");

    rerender(
      <AutomationCenter
        controller={makeController({
          rules: {
            status: "error",
            items: [],
            invalidItemCount: 0,
            nextCursor: null,
            error: "boom",
          },
        })}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("自动化规则读取失败");

    rerender(
      <AutomationCenter
        controller={makeController({
          rules: { status: "empty", items: [], invalidItemCount: 0, nextCursor: null, error: null },
        })}
      />,
    );
    expect(screen.getByText("暂无自动化规则")).toBeTruthy();
  });

  it("renders revision and event evidence as immutable facts, not edit controls", async () => {
    const user = userEvent.setup();
    render(<AutomationCenter controller={makeController()} />);
    await user.click(screen.getByRole("button", { name: "查看规则 失败任务通知" }));

    const drawer = await screen.findByRole("dialog", { name: "规则详情 rule-failed-task" });
    expect(within(drawer).getByText("revision 3")).toBeTruthy();
    expect(within(drawer).getByText("rule_created / revision_created 仅追加")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: "编辑当前版本" })).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "删除 revision" })).toBeNull();
  });

  it("shows a summary warning when authority data is partial or unavailable", () => {
    const { rerender } = render(
      <AutomationCenter
        controller={makeController({
          summary: {
            status: "ready",
            value: { ...summary, state: "partial", active_rule_count: null },
            error: null,
          },
        })}
      />,
    );
    expect(screen.getByText("自动化摘要部分可用")).toBeTruthy();
    expect(screen.getByText("未返回")).toBeTruthy();

    rerender(
      <AutomationCenter
        controller={makeController({
          summary: { status: "unavailable", value: null, error: "offline" },
        })}
      />,
    );
    expect(screen.getByText("自动化摘要暂不可用")).toBeTruthy();
  });
});
