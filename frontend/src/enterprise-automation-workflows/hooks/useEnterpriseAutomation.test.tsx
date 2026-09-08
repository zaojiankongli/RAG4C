// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useEnterpriseAutomation } from "./useEnterpriseAutomation";
import type { AutomationApi } from "./useEnterpriseAutomation";
import type { AutomationApiScope } from "../api/automationApi";
import type { AutomationRuleBuilderInput } from "../components/types";

const scope: AutomationApiScope = {
  tenantId: "tenant-a",
  actorToken: "token-a",
  accountId: "owner-a",
};
const scopeB: AutomationApiScope = {
  tenantId: "tenant-b",
  actorToken: "token-b",
  accountId: "owner-b",
};
const time = "2026-08-30T00:00:00.000000Z";
const digest = "a".repeat(64);
const rule: any = {
  id: "rule-a",
  tenant_id: "tenant-a",
  name: "失败任务通知",
  status: "active",
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
const draftRule: any = {
  ...rule,
  id: "rule-draft",
  name: "新建草稿规则",
  status: "draft",
  current_revision_id: "revision-draft",
  updated_at: "2026-08-30T00:01:00.000000Z",
};
const archivedRule: any = {
  ...rule,
  id: "rule-archived",
  name: "已归档规则",
  status: "archived",
  archived_at: time,
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
const summary: any = {
  tenant_id: "tenant-a",
  state: "ready",
  active_rule_count: 1,
  paused_rule_count: 0,
  failed_run_count: 0,
  pending_request_count: 0,
  as_of: time,
  reason_code: null,
};
const outcome: any = {
  state: "applied",
  operation: "automation",
  resource_id: "rule-a",
  revision: 1,
  message: null,
  retryable: false,
};
const page = (items: any[]) => ({ items, next_cursor: null, invalid_item_count: 0 });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

const createRuleInput: AutomationRuleBuilderInput = {
  name: "上下文隔离测试规则",
  priority: 10,
  definition: {
    trigger_code: "task_failed",
    condition_code: "always",
    condition_params: {},
    action_plan: [{ step_index: 0, action_code: "notify_operator", params: {} }],
  },
  reason: "验证上下文隔离",
};

function makeApi(overrides: Partial<AutomationApi> = {}): AutomationApi {
  return {
    fetchSummary: vi.fn().mockResolvedValue(summary),
    fetchRules: vi.fn().mockResolvedValue(page([rule])),
    fetchRule: vi.fn().mockResolvedValue({ rule, current_revision: revision, recent_runs: [] }),
    fetchRevisions: vi.fn().mockResolvedValue(page([revision])),
    fetchRuns: vi.fn().mockResolvedValue(page([])),
    fetchRun: vi.fn(),
    fetchRequests: vi.fn().mockResolvedValue(page([])),
    fetchEvents: vi.fn().mockResolvedValue(page([])),
    createRule: vi.fn().mockResolvedValue(outcome),
    createRevision: vi.fn().mockResolvedValue(outcome),
    previewRule: vi.fn().mockResolvedValue(outcome),
    activateRule: vi.fn().mockResolvedValue(outcome),
    pauseRule: vi.fn().mockResolvedValue(outcome),
    ...overrides,
  };
}

describe("useEnterpriseAutomation", () => {
  it("is inactive-safe", async () => {
    const api = makeApi();
    const { result } = renderHook(() => useEnterpriseAutomation(scope, { enabled: false, api }));
    expect(result.current.active).toBe(false);
    expect(api.fetchSummary).not.toHaveBeenCalled();
    await act(async () => expect(result.current.mutation.activate(rule)).resolves.toBeNull());
    expect(api.activateRule).not.toHaveBeenCalled();
  });

  it("loads summary and rules in parallel with one AbortSignal", async () => {
    let resolveSummary!: (value: any) => void;
    let resolveRules!: (value: any) => void;
    const api = makeApi({
      fetchSummary: vi.fn().mockReturnValue(new Promise((r) => (resolveSummary = r))),
      fetchRules: vi.fn().mockReturnValue(new Promise((r) => (resolveRules = r))),
    });
    const { result } = renderHook(() => useEnterpriseAutomation(scope, { enabled: true, api }));
    await waitFor(() => expect(api.fetchRules).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.fetchRules).mock.calls[0]?.[1]).toEqual({});
    expect(vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
    expect(vi.mocked(api.fetchRules).mock.calls[0]?.[2]?.signal).toBe(
      vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal,
    );
    expect(result.current.detail.status).toBe("idle");
    await act(async () => {
      resolveSummary(summary);
      resolveRules(page([rule]));
    });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
  });

  it("keeps a newly created draft visible after the mutation reload", async () => {
    const api = makeApi({
      fetchRules: vi
        .fn()
        .mockResolvedValueOnce(page([]))
        .mockResolvedValueOnce(page([draftRule, archivedRule])),
      createRule: vi.fn().mockResolvedValue({ ...outcome, resource_id: draftRule.id }),
    });
    const { result } = renderHook(() => useEnterpriseAutomation(scope, { enabled: true, api }));

    await waitFor(() => expect(result.current.rules.status).toBe("empty"));
    await act(async () => {
      await result.current.mutation.createRule(createRuleInput);
    });

    await waitFor(() => expect(result.current.rules.items.map((item) => item.id)).toEqual([draftRule.id]));
    expect(vi.mocked(api.fetchRules).mock.calls.map((call) => call[1])).toEqual([{}, {}]);
  });

  it("passes an explicit status filter only when one is selected", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useEnterpriseAutomation(scope, { enabled: true, api, ruleQuery: { status: "active" } }),
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchRules).toHaveBeenCalledWith(
      scope,
      { status: "active" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("lazy-loads detail, revisions, runs, requests and activity", async () => {
    const api = makeApi();
    const { result } = renderHook(() => useEnterpriseAutomation(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    await act(async () => {
      await result.current.detail.load("rule-a");
      await result.current.runs.load();
      await result.current.requests.load();
      await result.current.activity.load();
    });
    expect(api.fetchRule).toHaveBeenCalled();
    expect(api.fetchRevisions).toHaveBeenCalled();
    expect(api.fetchEvents).toHaveBeenCalled();
    expect(api.fetchRuns).toHaveBeenCalled();
    expect(api.fetchRequests).toHaveBeenCalled();
    expect(result.current.detail.value?.rule.id).toBe("rule-a");
  });

  it("drops stale responses after context change", async () => {
    let resolveOld!: (value: any) => void;
    const api = makeApi({
      fetchSummary: vi
        .fn()
        .mockImplementation((current) =>
          current.tenantId === "tenant-a"
            ? new Promise((r) => (resolveOld = r))
            : Promise.resolve({ ...summary, tenant_id: "tenant-b" }),
        ),
      fetchRules: vi
        .fn()
        .mockImplementation((current) =>
          Promise.resolve(page([{ ...rule, tenant_id: current.tenantId }])),
        ),
    });
    const { result, rerender } = renderHook(
      ({ current }) => useEnterpriseAutomation(current, { enabled: true, api }),
      { initialProps: { current: scope } },
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalled());
    rerender({ current: scopeB });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    await act(async () => resolveOld(summary));
    expect(result.current.summary.value?.tenant_id).toBe("tenant-b");
  });

  it("passes AbortSignals to lazy collections and fences stale pages after context changes", async () => {
    const pendingRuns = deferred<any>();
    const pendingRequests = deferred<any>();
    const pendingActivity = deferred<any>();
    const api = makeApi({
      fetchSummary: vi
        .fn()
        .mockImplementation((current) =>
          Promise.resolve({ ...summary, tenant_id: current.tenantId }),
        ),
      fetchRules: vi
        .fn()
        .mockImplementation((current) =>
          Promise.resolve(page([{ ...rule, tenant_id: current.tenantId }])),
        ),
      fetchRuns: vi.fn().mockReturnValue(pendingRuns.promise),
      fetchRequests: vi.fn().mockReturnValue(pendingRequests.promise),
      fetchEvents: vi.fn().mockReturnValue(pendingActivity.promise),
    });
    const { result, rerender } = renderHook(
      ({ current }) => useEnterpriseAutomation(current, { enabled: true, api }),
      { initialProps: { current: scope } },
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    const lazyLoads: Array<Promise<boolean>> = [];
    act(() => {
      lazyLoads.push(result.current.runs.load());
      lazyLoads.push(result.current.requests.load());
      lazyLoads.push(result.current.activity.load());
    });
    await waitFor(() => {
      expect(api.fetchRuns).toHaveBeenCalledTimes(1);
      expect(api.fetchRequests).toHaveBeenCalledTimes(1);
      expect(api.fetchEvents).toHaveBeenCalledTimes(1);
    });

    const signals = [
      vi.mocked(api.fetchRuns).mock.calls[0]?.[2]?.signal,
      vi.mocked(api.fetchRequests).mock.calls[0]?.[2]?.signal,
      vi.mocked(api.fetchEvents).mock.calls[0]?.[2]?.signal,
    ];
    signals.forEach((signal) => expect(signal).toBeInstanceOf(AbortSignal));

    rerender({ current: scopeB });
    await waitFor(() => signals.forEach((signal) => expect(signal?.aborted).toBe(true)));

    expect(result.current.runs.items).toEqual([]);
    expect(result.current.requests.items).toEqual([]);
    expect(result.current.activity.items).toEqual([]);

    await act(async () => {
      pendingRuns.resolve(page([{ id: "stale-run" }]));
      pendingRequests.resolve(page([{ id: "stale-request" }]));
      pendingActivity.resolve(page([{ id: "stale-event" }]));
      await Promise.all(lazyLoads);
    });

    expect(result.current.runs.items).toEqual([]);
    expect(result.current.requests.items).toEqual([]);
    expect(result.current.activity.items).toEqual([]);
  });

  it("clears every context-owned layer before loading the next tenant", async () => {
    const pendingTenantBSummary = deferred<any>();
    const pendingTenantBRules = deferred<any>();
    const api = makeApi({
      fetchSummary: vi
        .fn()
        .mockImplementation((current) =>
          current.tenantId === "tenant-a"
            ? Promise.resolve(summary)
            : pendingTenantBSummary.promise,
        ),
      fetchRules: vi
        .fn()
        .mockImplementation((current) =>
          current.tenantId === "tenant-a"
            ? Promise.resolve(page([rule]))
            : pendingTenantBRules.promise,
        ),
    });
    const { result, rerender } = renderHook(
      ({ current }) => useEnterpriseAutomation(current, { enabled: true, api }),
      { initialProps: { current: scope } },
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    await act(async () => {
      await result.current.detail.load("rule-a");
      await Promise.all([
        result.current.runs.load(),
        result.current.requests.load(),
        result.current.activity.load(),
      ]);
    });
    expect(result.current.summary.value?.tenant_id).toBe("tenant-a");
    expect(result.current.rules.items).toHaveLength(1);
    expect(result.current.revisions.items).toHaveLength(1);
    expect(result.current.runs.status).toBe("empty");
    expect(result.current.requests.status).toBe("empty");
    expect(result.current.activity.status).toBe("empty");
    expect(result.current.detail.value).not.toBeNull();

    rerender({ current: scopeB });
    await waitFor(() => {
      expect(api.fetchSummary).toHaveBeenCalledTimes(2);
      expect(api.fetchRules).toHaveBeenCalledTimes(2);
    });

    expect(result.current.summary.value).toBeNull();
    expect(result.current.summary.error).toBeNull();
    expect(result.current.rules.items).toEqual([]);
    expect(result.current.rules.error).toBeNull();
    expect(result.current.revisions.items).toEqual([]);
    expect(result.current.revisions.status).toBe("idle");
    expect(result.current.runs.items).toEqual([]);
    expect(result.current.runs.status).toBe("idle");
    expect(result.current.requests.items).toEqual([]);
    expect(result.current.requests.status).toBe("idle");
    expect(result.current.activity.items).toEqual([]);
    expect(result.current.activity.status).toBe("idle");
    expect(result.current.detail.value).toBeNull();
    expect(result.current.detail.status).toBe("idle");
    expect(result.current.detail.error).toBeNull();
    expect(result.current.mutation.status).toBe("idle");
    expect(result.current.mutation.error).toBeNull();
    expect(result.current.mutation.outcome).toBeNull();
    expect(result.current.load.error).toBeNull();

    pendingTenantBSummary.resolve({ ...summary, tenant_id: "tenant-b" });
    pendingTenantBRules.resolve(page([{ ...rule, tenant_id: "tenant-b" }]));
  });

  it("does not execute queued old-context mutations or commit their results", async () => {
    const pendingMutation = deferred<any>();
    const api = makeApi({
      createRule: vi.fn().mockReturnValue(pendingMutation.promise),
    });
    const { result, rerender } = renderHook(
      ({ current }) => useEnterpriseAutomation(current, { enabled: true, api }),
      { initialProps: { current: scope } },
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    let firstMutation!: Promise<any>;
    let queuedMutation!: Promise<any>;
    await act(async () => {
      firstMutation = result.current.mutation.createRule(createRuleInput);
      queuedMutation = result.current.mutation.createRule({
        ...createRuleInput,
        name: "不应提交的排队规则",
      });
    });
    await waitFor(() => expect(api.createRule).toHaveBeenCalledTimes(1));
    const mutationSignal = vi.mocked(api.createRule).mock.calls[0]?.[2]?.signal;

    rerender({ current: scopeB });
    await waitFor(() => expect(mutationSignal?.aborted).toBe(true));

    await act(async () => {
      pendingMutation.resolve(outcome);
      await expect(firstMutation).resolves.toBeNull();
      await expect(queuedMutation).resolves.toBeNull();
    });

    expect(api.createRule).toHaveBeenCalledTimes(1);
    expect(result.current.mutation.status).not.toBe("success");
    expect(result.current.mutation.outcome).toBeNull();
  });

  it("serializes mutations, retains fences and blocks read-only", async () => {
    const api = makeApi();
    const { result } = renderHook(() => useEnterpriseAutomation(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    await act(async () => {
      await Promise.all([
        result.current.mutation.activate(rule),
        result.current.mutation.pause(rule),
      ]);
    });
    expect(api.activateRule).toHaveBeenCalledWith(
      scope,
      "rule-a",
      {
        revisionId: "revision-a",
        expectedRevision: 1,
        expectedDefinitionDigest: digest,
        reason: expect.any(String),
      },
      expect.objectContaining({ idempotencyKey: expect.stringMatching(/^rag4c-automation-/) }),
    );
    const readOnlyApi = makeApi();
    const readOnly = renderHook(() =>
      useEnterpriseAutomation(scope, { enabled: true, readOnly: true, api: readOnlyApi }),
    );
    await waitFor(() => expect(readOnly.result.current.load.status).toBe("ready"));
    await act(async () => expect(readOnly.result.current.mutation.pause(rule)).resolves.toBeNull());
    expect(readOnlyApi.pauseRule).not.toHaveBeenCalled();
  });
});
