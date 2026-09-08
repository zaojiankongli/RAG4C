// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import RuleBuilderDialog, { createActionParams, createConditionParams } from "./RuleBuilderDialog";

describe("Stage25 RuleBuilderDialog strict schemas", () => {
  afterEach(cleanup);

  it("creates exact condition params and removes incompatible keys on condition changes", () => {
    expect(createConditionParams("always", { minimum_attempts: 9 })).toEqual({});
    expect(createConditionParams("status_is", { minimum_attempts: 9 })).toEqual({
      status: "failed",
    });
    expect(createConditionParams("status_is", { status: "blocked" })).toEqual({
      status: "blocked",
    });
    expect(createConditionParams("action_required", { minimum_attempts: 9 })).toEqual({
      value: true,
    });
    expect(createConditionParams("severity_at_least", { status: "failed" })).toEqual({
      severity: "error",
    });
    expect(createConditionParams("attempt_exhausted", { status: "failed" })).toEqual({
      minimum_attempts: 3,
    });
    expect(createConditionParams("source_is_stale", { status: "failed" })).toEqual({
      value: true,
    });
  });

  it("creates exact action params, including the complete notify_operator default", () => {
    expect(createActionParams("notify_operator", {})).toEqual({
      category: "task_operations",
      severity: "error",
      title: "自动化规则通知",
    });
    expect(createActionParams("request_approval", { severity: "critical" })).toEqual({
      action_type: "review",
      resource_type: "knowledge_base",
      reason_code: "automation_request",
    });
    expect(createActionParams("open_task_attention", { severity: "critical" })).toEqual({
      task_id: "task-id",
      reason_code: "automation_attention",
    });
    expect(createActionParams("pause_rule", { severity: "critical" })).toEqual({
      reason_code: "automation_pause",
    });
  });

  it("uses the strict API validator before showing preview", async () => {
    const user = userEvent.setup();
    render(<RuleBuilderDialog visible onClose={() => undefined} onSubmit={() => undefined} />);

    await user.click(screen.getByRole("button", { name: "查看预览" }));
    expect(screen.getByTestId("automation-preview-panel")).toBeTruthy();
  });

  it("does not preview an incompatible revision definition", async () => {
    const user = userEvent.setup();
    render(
      <RuleBuilderDialog
        visible
        revision={
          {
            tenant_id: "tenant-a",
            rule_id: "rule-a",
            id: "revision-a",
            revision: 1,
            trigger_code: "task_failed",
            condition_code: "always",
            condition_params: { minimum_attempts: 3 },
            action_plan: [
              {
                step_index: 0,
                action_code: "notify_operator",
                params: { category: "task_operations", severity: "error" },
              },
            ],
            definition_digest: "a".repeat(64),
            created_at: "2026-08-29T00:00:00.000Z",
            created_by: "owner-a",
          } as never
        }
        onClose={() => undefined}
        onSubmit={() => undefined}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看预览" }));
    expect(screen.queryByTestId("automation-preview-panel")).toBeNull();
    expect(screen.getByTestId("automation-rule-builder-validation-error").textContent).toMatch(
      /condition|action|title|参数/i,
    );
  });
});
