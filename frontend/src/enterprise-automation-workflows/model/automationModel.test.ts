import { describe, expect, it } from "vitest";
import {
  projectAutomationActionRequest,
  projectAutomationEventPage,
  projectAutomationRule,
  projectAutomationRuleRevision,
  projectAutomationRun,
  projectAutomationSummary,
  projectAutomationTriggerRoute,
} from "./automationModel";

const scope = { tenantId: "tenant-a", accountId: "owner-a" };
const digest = "a".repeat(64);
const time = "2026-08-30T00:00:00.000000Z";

function rule(overrides: Record<string, unknown> = {}) {
  return {
    id: "rule-a",
    tenant_id: "tenant-a",
    name: "失败任务通知",
    status: "active",
    revision: 2,
    current_revision_id: "revision-a",
    workspace_id: "workspace-a",
    dataset_id: "dataset-a",
    priority: 100,
    created_at: time,
    created_by: "owner-a",
    updated_at: time,
    updated_by: "owner-a",
    archived_at: null,
    ...overrides,
  };
}
function revision(overrides: Record<string, unknown> = {}) {
  return {
    id: "revision-a",
    tenant_id: "tenant-a",
    rule_id: "rule-a",
    revision: 2,
    trigger_code: "task_failed",
    condition_code: "attempt_exhausted",
    condition_params_json: { minimum_attempts: 3 },
    action_plan_json: [
      {
        step_index: 0,
        action_code: "notify_operator",
        params: { category: "task_operations", severity: "error", title: "任务失败" },
      },
    ],
    definition_digest: digest,
    created_at: time,
    created_by: "owner-a",
    ...overrides,
  };
}
function run(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-a",
    tenant_id: "tenant-a",
    rule_id: "rule-a",
    rule_revision_id: "revision-a",
    trigger_event_id: "event-a",
    trigger_event_digest: digest,
    status: "requested",
    condition_matched: true,
    action_count: 1,
    requested_count: 1,
    rejected_count: 0,
    started_at: time,
    completed_at: time,
    safe_error_code: null,
    safe_error: null,
    ...overrides,
  };
}
function actionRequest(overrides: Record<string, unknown> = {}) {
  return {
    id: "request-a",
    tenant_id: "tenant-a",
    run_id: "run-a",
    rule_id: "rule-a",
    step_index: 0,
    action_code: "notify_operator",
    status: "requested",
    target_kind: null,
    target_id: null,
    target_revision: null,
    target_digest: null,
    safe_params_json: { category: "task_operations", severity: "error", title: "任务失败" },
    safe_reason: "通知管理员",
    approval_request_id: null,
    notification_id: null,
    task_id: null,
    requested_at: time,
    dispatched_at: null,
    applied_at: null,
    rejected_at: null,
    expires_at: time,
    ...overrides,
  };
}
function event(overrides: Record<string, unknown> = {}) {
  return {
    id: "event-1",
    tenant_id: "tenant-a",
    rule_id: "rule-a",
    run_id: "run-a",
    stream_key: "run:run-a",
    sequence: 1,
    event_type: "run_started",
    previous_event_digest: null,
    event_digest: digest,
    actor_id: "system:automation",
    request_id: "request-1",
    safe_snapshot_json: {},
    occurred_at: time,
    ...overrides,
  };
}

describe("Stage25 automation frontend model", () => {
  it("projects the exact minimal service wire DTO", () => {
    expect(projectAutomationRule(rule(), scope).status).toBe("active");
    const projectedRevision = projectAutomationRuleRevision(revision(), scope);
    expect(projectedRevision.id).toBe("revision-a");
    expect(projectedRevision.condition_params).toEqual({ minimum_attempts: 3 });
    expect(projectAutomationRun(run(), scope).status).toBe("requested");
    expect(projectAutomationActionRequest(actionRequest(), scope).safe_params).toEqual({
      category: "task_operations",
      severity: "error",
      title: "任务失败",
    });
  });

  it("rejects aliases, persistence internals and unknown response fields", () => {
    expect(() => projectAutomationRule({ ...rule(), archived_by: null }, scope)).toThrow(
      /unexpected|field/i,
    );
    expect(() =>
      projectAutomationRuleRevision(
        { ...revision(), condition_params: { minimum_attempts: 3 } },
        scope,
      ),
    ).toThrow(/unexpected|field/i);
    expect(() => projectAutomationRun({ ...run(), idempotency_digest: digest }, scope)).toThrow(
      /unexpected|field/i,
    );
    expect(() =>
      projectAutomationActionRequest({ ...actionRequest(), safe_params: {} }, scope),
    ).toThrow(/unexpected|field/i);
  });

  it("supports exact trigger, condition and action allowlists", () => {
    for (const trigger of [
      "task_failed",
      "task_source_stale",
      "source_sync_failed",
      "release_quality_alert_opened",
      "release_recertification_blocked",
      "approval_request_terminal",
    ])
      expect(
        projectAutomationRuleRevision(revision({ trigger_code: trigger }), scope).trigger_code,
      ).toBe(trigger);
    expect(() =>
      projectAutomationRuleRevision(revision({ trigger_code: "custom_webhook" }), scope),
    ).toThrow(/trigger/i);
    expect(() =>
      projectAutomationRuleRevision(
        revision({
          action_plan_json: [{ step_index: 0, action_code: "execute_sql", params: {} }],
        }),
        scope,
      ),
    ).toThrow(/action/i);
  });

  it("validates immutable event pages per stream and supports mid-page evidence", () => {
    const first = event();
    const second = event({
      id: "event-2",
      sequence: 2,
      event_type: "action_requested",
      previous_event_digest: digest,
      event_digest: "b".repeat(64),
    });
    const otherStream = event({
      id: "other-event-1",
      rule_id: "rule-b",
      stream_key: "run:run-b",
      event_digest: "c".repeat(64),
    });
    expect(
      projectAutomationEventPage(
        { items: [first, otherStream, second], next_cursor: null, invalid_item_count: 0 },
        scope,
      ).items,
    ).toHaveLength(3);
    expect(
      projectAutomationEventPage(
        {
          items: [event({ sequence: 7, previous_event_digest: digest })],
          next_cursor: "next",
          invalid_item_count: 0,
        },
        scope,
      ).items[0]?.sequence,
    ).toBe(7);
    expect(() =>
      projectAutomationEventPage(
        { items: [first, { ...second, sequence: 3 }], next_cursor: null, invalid_item_count: 0 },
        scope,
      ),
    ).toThrow(/chain|sequence/i);
    expect(() =>
      projectAutomationEventPage(
        {
          items: [event({ sequence: 2, previous_event_digest: null })],
          next_cursor: null,
          invalid_item_count: 0,
        },
        scope,
      ),
    ).toThrow(/previous|chain/i);
  });

  it("keeps strict ready and unavailable summary semantics", () => {
    expect(
      projectAutomationSummary(
        {
          tenant_id: "tenant-a",
          state: "ready",
          active_rule_count: 1,
          paused_rule_count: 0,
          failed_run_count: 0,
          pending_request_count: 2,
          as_of: time,
          reason_code: null,
        },
        scope,
      ).pending_request_count,
    ).toBe(2);
    expect(
      projectAutomationSummary(
        {
          tenant_id: "tenant-a",
          state: "unavailable",
          active_rule_count: null,
          paused_rule_count: null,
          failed_run_count: null,
          pending_request_count: null,
          as_of: null,
          reason_code: "authority_unavailable",
        },
        scope,
      ).active_rule_count,
    ).toBeNull();
    expect(() =>
      projectAutomationSummary(
        {
          tenant_id: "tenant-a",
          state: "ready",
          active_rule_count: null,
          paused_rule_count: 0,
          failed_run_count: 0,
          pending_request_count: 0,
          as_of: time,
          reason_code: null,
        },
        scope,
      ),
    ).toThrow(/summary|count/i);
  });

  it("rejects unsafe and non-exact condition/action params", () => {
    expect(() =>
      projectAutomationRuleRevision(
        revision({ condition_code: "always", condition_params_json: { minimum_attempts: 3 } }),
        scope,
      ),
    ).toThrow(/condition|unexpected|exact/i);
    expect(() =>
      projectAutomationRuleRevision(
        revision({
          action_plan_json: [
            {
              step_index: 0,
              action_code: "notify_operator",
              params: { category: "task_operations", severity: "error" },
            },
          ],
        }),
        scope,
      ),
    ).toThrow(/action|title|exact/i);
    expect(() =>
      projectAutomationRuleRevision(
        revision({
          action_plan_json: [
            {
              step_index: 0,
              action_code: "notify_operator",
              params: {
                category: "task_operations",
                severity: "error",
                title: "任务失败",
                webhookUrl: "https://example.com",
              },
            },
          ],
        }),
        scope,
      ),
    ).toThrow(/unsafe|field|param/i);
  });

  it("builds only allowlisted encoded internal handoff routes", () => {
    expect(
      projectAutomationTriggerRoute({
        trigger_code: "task_failed",
        safe_facts_json: { task_id: "task-a" },
      }),
    ).toEqual({
      code: "enterprise_tasks",
      path: "/enterprise/tasks",
      query: { task: "task-a" },
      href: "/enterprise/tasks?task=task-a",
    });
    expect(
      projectAutomationTriggerRoute({
        trigger_code: "source_sync_failed",
        safe_facts_json: { source_id: "source-a" },
      }).path,
    ).toBe("/sources");
    expect(() =>
      projectAutomationTriggerRoute({
        trigger_code: "custom_webhook",
        safe_facts_json: { url: "https://example.com" },
      }),
    ).toThrow(/trigger|route/i);
  });

  it("rejects sensitive key spellings, SQL-like text, Bearer, JWT, and oversized safe maps", () => {
    const unsafeKeys = [
      "apiKey",
      "api_key",
      "apikey",
      "authorizationHeader",
      "authorization_header",
      "authorizationheader",
      "header",
      "answer",
      "accessKey",
      "access_key",
      "accesskey",
    ];
    for (const unsafeKey of unsafeKeys) {
      expect(() =>
        projectAutomationEventPage(
          {
            items: [event({ safe_snapshot_json: { [unsafeKey]: "redacted" } })],
            next_cursor: null,
            invalid_item_count: 0,
          },
          scope,
        ),
      ).toThrow(/unsafe|field|forbidden/i);
    }

    const unsafeTexts = [
      "SELECT * FROM users",
      "INSERT INTO users VALUES (1)",
      "UPDATE users SET role = 'admin'",
      "DELETE FROM users",
      "DROP TABLE users",
      "ALTER TABLE users ADD COLUMN x",
      "CREATE TABLE users (id INT)",
      "GRANT SELECT ON users TO app",
      "REVOKE SELECT ON users FROM app",
      "EXEC xp_cmdshell 'whoami'",
      "UNION SELECT password FROM users",
      "SELECT",
      "INSERT",
      "UPDATE",
      "DELETE",
      "DROP",
      "ALTER",
      "CREATE",
      "GRANT",
      "REVOKE",
      "EXEC",
      "UNION",
      "Bearer abc123",
      "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
    ];
    for (const unsafeText of unsafeTexts) {
      expect(() =>
        projectAutomationEventPage(
          {
            items: [event({ safe_snapshot_json: { reason_code: unsafeText } })],
            next_cursor: null,
            invalid_item_count: 0,
          },
          scope,
        ),
      ).toThrow(/unsafe|invalid/i);
    }

    const oversized = Object.fromEntries(
      Array.from({ length: 32 }, (_, index) => [`fact_${index}`, "x".repeat(500)]),
    );
    expect(() =>
      projectAutomationEventPage(
        {
          items: [event({ safe_snapshot_json: oversized })],
          next_cursor: null,
          invalid_item_count: 0,
        },
        scope,
      ),
    ).toThrow(/safe|bytes|large/i);
  });

  it("keeps existing safe snapshot fields usable after strict scanning", () => {
    const projected = projectAutomationEventPage(
      {
        items: [
          event({
            safe_snapshot_json: {
              status: "started",
              revision: 1,
              reason_code: "automation_started",
            },
          }),
        ],
        next_cursor: null,
        invalid_item_count: 0,
      },
      scope,
    );
    expect(projected.items[0]?.safe_snapshot).toEqual({
      status: "started",
      revision: 1,
      reason_code: "automation_started",
    });
  });
});
