// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const hook = vi.hoisted(() => ({ useEnterpriseAutomation: vi.fn() }));
vi.mock("./hooks/useEnterpriseAutomation", () => hook);
vi.mock("./components", () => ({
  AutomationCenter: (props: Record<string, any>) => {
    const rule = props.controller.rules.items[0];
    return (
      <section aria-label="自动化页面适配器">
        <span>{rule?.name}</span>
        <span>{props.controller.summary.value?.active_rule_count}</span>
        <button onClick={() => props.controller.mutation.activateRule?.(rule)}>启用映射</button>
        <button onClick={() => props.onHandoff?.(rule, props.controller.revisions.items[0])}>
          触发来源交接
        </button>
      </section>
    );
  },
}));
import AutomationPage from "./AutomationPage";
const time = "2026-08-30T00:00:00.000000Z",
  digest = "a".repeat(64);
const rule: any = {
  id: "rule-a",
  tenant_id: "tenant-a",
  name: "失败任务通知",
  status: "paused",
  revision: 1,
  current_revision_id: "revision-a",
  workspace_id: null,
  dataset_id: null,
  priority: 100,
  created_at: time,
  created_by: "owner-a",
  updated_at: time,
  updated_by: "owner-a",
  archived_at: null,
};
const revision: any = {
  id: "revision-a",
  tenant_id: "tenant-a",
  rule_id: "rule-a",
  revision: 1,
  trigger_code: "task_failed",
  condition_code: "always",
  condition_params: {},
  action_plan: [{ step_index: 0, action_code: "notify_operator", params: {} }],
  definition_digest: digest,
  created_at: time,
  created_by: "owner-a",
};
const activate = vi.fn().mockResolvedValue({ state: "applied" });
function value() {
  const collection = (items: any[]) => ({
    status: "ready",
    items,
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
    load: vi.fn(),
    reload: vi.fn(),
  });
  return {
    active: true,
    readOnly: false,
    load: { status: "ready", error: null, reload: vi.fn() },
    summary: {
      status: "ready",
      value: {
        tenant_id: "tenant-a",
        state: "ready",
        active_rule_count: 1,
        paused_rule_count: 1,
        failed_run_count: 0,
        pending_request_count: 0,
        as_of: time,
        reason_code: null,
      },
      error: null,
    },
    rules: collection([rule]),
    revisions: collection([revision]),
    runs: collection([]),
    requests: collection([]),
    activity: collection([]),
    detail: { status: "idle", value: null, error: null, load: vi.fn() },
    mutation: {
      status: "idle",
      error: null,
      outcome: null,
      createRule: vi.fn(),
      createRevision: vi.fn(),
      activate,
      pause: vi.fn(),
    },
  };
}
beforeEach(() => {
  hook.useEnterpriseAutomation.mockReset();
  hook.useEnterpriseAutomation.mockReturnValue(value());
  activate.mockClear();
});
afterEach(cleanup);
describe("AutomationPage adapter", () => {
  it("maps verified hook authority into the TDesign controller", async () => {
    render(
      <AutomationPage
        tenantId="tenant-a"
        accountId="owner-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
      />,
    );
    expect(hook.useEnterpriseAutomation).toHaveBeenCalledWith(
      { tenantId: "tenant-a", accountId: "owner-a", actorToken: "actor-token" },
      { enabled: true, readOnly: false },
    );
    expect(screen.getByText("失败任务通知")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    const statusFilter = screen.getByLabelText("规则状态筛选") as HTMLSelectElement;
    expect(statusFilter.value).toBe("all");
    fireEvent.change(statusFilter, { target: { value: "active" } });
    expect(hook.useEnterpriseAutomation).toHaveBeenLastCalledWith(
      { tenantId: "tenant-a", accountId: "owner-a", actorToken: "actor-token" },
      { enabled: true, readOnly: false, ruleQuery: { status: "active" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "启用映射" }));
    await waitFor(() => expect(activate).toHaveBeenCalledWith(rule));
  });
  it("keeps draft rules visible in the default non-archived view", () => {
    const controllerValue = value();
    controllerValue.rules.items = [
      { ...rule, id: "rule-draft", name: "新建草稿规则", status: "draft" },
    ];
    hook.useEnterpriseAutomation.mockReturnValue(controllerValue);

    render(
      <AutomationPage
        tenantId="tenant-a"
        accountId="owner-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
      />,
    );

    expect(screen.getByText("新建草稿规则")).toBeTruthy();
    expect(hook.useEnterpriseAutomation).toHaveBeenCalledWith(
      { tenantId: "tenant-a", accountId: "owner-a", actorToken: "actor-token" },
      { enabled: true, readOnly: false },
    );
  });

  it("preserves an allowlisted task target id during handoff", () => {
    const onAutomationHandoff = vi.fn();
    const controllerValue = value();
    controllerValue.revisions.items = [
      {
        ...revision,
        action_plan: [
          {
            step_index: 0,
            action_code: "open_task_attention",
            params: { task_id: "task-a", reason_code: "automation_attention" },
          },
        ],
      },
    ];
    hook.useEnterpriseAutomation.mockReturnValue(controllerValue);
    render(
      <AutomationPage
        tenantId="tenant-a"
        accountId="owner-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
        onAutomationHandoff={onAutomationHandoff}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "触发来源交接" }));
    expect(onAutomationHandoff).toHaveBeenCalledWith({
      code: "enterprise_tasks",
      path: "/enterprise/tasks",
      query: { task: "task-a" },
      href: "/enterprise/tasks?task=task-a",
    });
  });

  it("hands off only the strict route projected from the current trigger", () => {
    const onAutomationHandoff = vi.fn();
    render(
      <AutomationPage
        tenantId="tenant-a"
        accountId="owner-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
        onAutomationHandoff={onAutomationHandoff}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "触发来源交接" }));
    expect(onAutomationHandoff).toHaveBeenCalledWith({
      code: "enterprise_tasks",
      path: "/enterprise/tasks",
      query: {},
      href: "/enterprise/tasks",
    });
  });
});
