// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import {
  activateAutomationRule,
  createAutomationIdempotencyKey,
  createAutomationRule,
  createAutomationRuleRevision,
  fetchAutomationActionRequests,
  fetchAutomationEvents,
  fetchAutomationRule,
  fetchAutomationRuleRevisions,
  fetchAutomationRules,
  fetchAutomationRun,
  fetchAutomationRuns,
  fetchAutomationSummary,
  pauseAutomationRule,
  previewAutomationRule,
  validateAutomationRuleDefinition,
  type AutomationApiScope,
} from "./automationApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope: AutomationApiScope = {
  tenantId: "tenant-a",
  actorToken: "actor-token",
  accountId: "owner-a",
};
const digest = "a".repeat(64);
const time = "2026-08-30T00:00:00.000000Z";
const rule = {
  id: "rule-a",
  tenant_id: "tenant-a",
  name: "失败任务通知",
  status: "active",
  revision: 2,
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
const revision = {
  id: "revision-a",
  tenant_id: "tenant-a",
  rule_id: "rule-a",
  revision: 2,
  trigger_code: "task_failed",
  condition_code: "always",
  condition_params_json: {},
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
};
const definition = {
  trigger_code: revision.trigger_code,
  condition_code: revision.condition_code,
  condition_params: revision.condition_params_json,
  action_plan: revision.action_plan_json.map(({ action_code, params }) => ({
    action_code,
    params,
  })),
};
const page = (items: unknown[]) => ({ items, next_cursor: null, invalid_item_count: 0 });
const outcome = {
  state: "applied",
  operation: "automation",
  resource_id: "rule-a",
  revision: 2,
  message: null,
  retryable: false,
};

describe("Stage25 automation API", () => {
  beforeEach(() => mocked.mockReset());

  it("sends scoped reads, bounded queries and one AbortSignal", async () => {
    mocked
      .mockResolvedValueOnce({
        tenant_id: "tenant-a",
        state: "ready",
        active_rule_count: 1,
        paused_rule_count: 0,
        failed_run_count: 0,
        pending_request_count: 0,
        as_of: time,
        reason_code: null,
      })
      .mockResolvedValueOnce(page([rule]))
      .mockResolvedValueOnce({ rule, current_revision: revision, recent_runs: [] })
      .mockResolvedValueOnce(page([revision]))
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce({
        run: {
          id: "run-a",
          tenant_id: "tenant-a",
          rule_id: "rule-a",
          rule_revision_id: "revision-a",
          trigger_event_id: "event-a",
          trigger_event_digest: digest,
          status: "completed",
          condition_matched: true,
          action_count: 0,
          requested_count: 0,
          rejected_count: 0,
          started_at: time,
          completed_at: time,
          safe_error_code: null,
          safe_error: null,
        },
        action_requests: [],
        events: [],
      })
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([]));
    const controller = new AbortController();
    await fetchAutomationSummary(scope, { signal: controller.signal });
    await fetchAutomationRules(
      scope,
      { status: "active", limit: 20 },
      { signal: controller.signal },
    );
    await fetchAutomationRule(scope, "rule-a", { signal: controller.signal });
    await fetchAutomationRuleRevisions(
      scope,
      "rule-a",
      { limit: 20 },
      { signal: controller.signal },
    );
    await fetchAutomationRuns(
      scope,
      { status: "failed", limit: 20 },
      { signal: controller.signal },
    );
    await fetchAutomationRun(scope, "run-a", { signal: controller.signal });
    await fetchAutomationActionRequests(
      scope,
      { status: "requested", limit: 20 },
      { signal: controller.signal },
    );
    await fetchAutomationEvents(
      scope,
      { ruleId: "rule-a", limit: 20 },
      { signal: controller.signal },
    );
    expect(mocked.mock.calls.map(([path]) => path)).toEqual([
      "/api/enterprise/automations/summary",
      "/api/enterprise/automations/rules?limit=20&status=active",
      "/api/enterprise/automations/rules/rule-a",
      "/api/enterprise/automations/rules/rule-a/revisions?limit=20",
      "/api/enterprise/automations/runs?limit=20&status=failed",
      "/api/enterprise/automations/runs/run-a",
      "/api/enterprise/automations/action-requests?limit=20&status=requested",
      "/api/enterprise/automations/events?limit=20&rule_id=rule-a",
    ]);
    expect(mocked.mock.calls.every(([, init]) => init?.signal === controller.signal)).toBe(true);
    expect(mocked.mock.calls[0]?.[1]?.headers).toEqual(
      expect.objectContaining({
        "X-RAG4C-Tenant": "tenant-a",
        Authorization: "Bearer actor-token",
      }),
    );
  });

  it("sends every mutation with Idempotency-Key and complete fences", async () => {
    mocked.mockResolvedValue(outcome);
    const options = { idempotencyKey: "automation-key" };
    await createAutomationRule(
      scope,
      { name: "失败任务通知", priority: 100, definition, reason: "create rule" },
      options,
    );
    await createAutomationRuleRevision(
      scope,
      "rule-a",
      {
        expectedRevision: 2,
        expectedDefinitionDigest: digest,
        definition,
        reason: "new revision",
      },
      options,
    );
    await previewAutomationRule(
      scope,
      "rule-a",
      {
        expectedRevision: 2,
        expectedDefinitionDigest: digest,
        triggerEvent: {
          tenant_id: "tenant-a",
          trigger_code: "task_failed",
          source_stream_id: "stream-a",
          source_event_id: "event-a",
          source_event_digest: digest,
          sequence: 1,
          status: "failed",
          severity: "error",
          action_required: true,
          attempt_number: 3,
          max_attempts: 3,
          source_current: true,
          occurred_at: time,
          safe_facts: { task_id: "task-a" },
        },
        reason: "preview",
      },
      options,
    );
    await activateAutomationRule(
      scope,
      "rule-a",
      {
        revisionId: "revision-a",
        expectedRevision: 2,
        expectedDefinitionDigest: digest,
        reason: "activate",
      },
      options,
    );
    await pauseAutomationRule(
      scope,
      "rule-a",
      { expectedRevision: 2, expectedDefinitionDigest: digest, reason: "pause" },
      options,
    );
    expect(mocked.mock.calls.map(([path]) => path)).toEqual([
      "/api/enterprise/automations/rules",
      "/api/enterprise/automations/rules/rule-a/revisions",
      "/api/enterprise/automations/rules/rule-a/preview",
      "/api/enterprise/automations/rules/rule-a/activate",
      "/api/enterprise/automations/rules/rule-a/pause",
    ]);
    for (const [, init] of mocked.mock.calls)
      expect(init?.headers).toEqual(
        expect.objectContaining({ "Idempotency-Key": "automation-key" }),
      );
    expect(JSON.parse(String(mocked.mock.calls[3]?.[1]?.body))).toEqual({
      revision_id: "revision-a",
      expected_revision: 2,
      expected_definition_digest: digest,
      reason: "activate",
    });
    for (const index of [0, 1]) {
      const body = JSON.parse(String(mocked.mock.calls[index]?.[1]?.body));
      expect(body.action_plan).toEqual([
        {
          action_code: "notify_operator",
          params: { category: "task_operations", severity: "error", title: "任务失败" },
        },
      ]);
      expect(body.action_plan[0]).not.toHaveProperty("step_index");
    }
  });

  it("rejects missing keys, revision zero and unsafe rule definitions before network", () => {
    expect(() =>
      activateAutomationRule(scope, "rule-a", {
        revisionId: "revision-a",
        expectedRevision: 2,
        expectedDefinitionDigest: digest,
        reason: "activate",
      }),
    ).toThrow(/idempotency/i);
    expect(() =>
      pauseAutomationRule(
        scope,
        "rule-a",
        { expectedRevision: 0, expectedDefinitionDigest: digest, reason: "pause" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/revision|integer/i);
    expect(() =>
      createAutomationRule(
        scope,
        {
          name: "unsafe",
          priority: 100,
          definition: {
            ...revision,
            action_plan: [
              {
                action_code: "notify_operator",
                params: { webhook: "https://example.com" },
              },
            ],
          },
          reason: "create",
        },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/unsafe|field|param/i);
    expect(mocked).not.toHaveBeenCalled();
  });

  it("rejects unknown response fields and bounds list inputs", async () => {
    mocked.mockResolvedValueOnce({ ...rule, unexpected: true });
    await expect(fetchAutomationRule(scope, "rule-a")).rejects.toThrow(/unexpected|field/i);
    expect(() => fetchAutomationRules(scope, { limit: 201 })).toThrow(/limit/i);
  });

  it("uses one canonical definition validator for mutation and preview inputs", () => {
    const definition = validateAutomationRuleDefinition({
      trigger_code: "task_failed",
      condition_code: "attempt_exhausted",
      condition_params: { minimum_attempts: 3 },
      action_plan: [
        {
          action_code: "notify_operator",
          params: { category: "task_operations", severity: "error", title: "任务失败" },
        },
      ],
    });
    expect(definition).toEqual({
      trigger_code: "task_failed",
      condition_code: "attempt_exhausted",
      condition_params: { minimum_attempts: 3 },
      action_plan: [
        {
          step_index: 0,
          action_code: "notify_operator",
          params: { category: "task_operations", severity: "error", title: "任务失败" },
        },
      ],
    });
    expect(() =>
      validateAutomationRuleDefinition({
        trigger_code: "task_failed",
        condition_code: "always",
        condition_params: { minimum_attempts: 3 },
        action_plan: [
          {
            action_code: "notify_operator",
            params: { category: "task_operations", severity: "error", title: "任务失败" },
          },
        ],
      }),
    ).toThrow(/condition|exact/i);
    expect(() =>
      validateAutomationRuleDefinition({
        trigger_code: "task_failed",
        condition_code: "always",
        condition_params: {},
        action_plan_json: [
          {
            action_code: "notify_operator",
            params: { category: "task_operations", severity: "error", title: "旧字段" },
          },
        ],
      }),
    ).toThrow(/canonical|unexpected|action_plan/i);
  });

  it("creates a nonempty automation idempotency key", () => {
    expect(createAutomationIdempotencyKey()).toMatch(/^rag4c-automation-/);
  });
});
