// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReleaseQualityApi } from "../../enterprise-release-quality/api/qualityApi";
import type {
  QualityBaseline,
  QualityCertification,
  QualityGate,
  QualityPolicy,
} from "../../enterprise-release-quality/model/qualityModel";
import type { ReleaseApi } from "../hooks/useKnowledgeBaseReleases";
import type {
  ReleaseAuditPage,
  ReleaseChannelPage,
  ReleaseDetail,
  ReleaseHistoryPage,
  ReleaseImpact,
  ReleaseMutationOutcome,
  ReleaseReadiness,
} from "../model/releaseModel";
import KnowledgeBaseReleaseCenter from "./KnowledgeBaseReleaseCenter";
import ReleaseMutationDialog from "./ReleaseMutationDialog";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

const release42 = {
  id: "release-42",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_number: 42,
  status: "candidate" as const,
  profile_revision: 12,
  ownership_revision: 7,
  workspace_revision: 9,
  mutation_generation: 18,
  serving_generation: 3,
  schema_version: 1,
  manifest_digest: "a".repeat(64),
  readiness_state: "ready" as const,
  readiness_fingerprint: "b".repeat(64),
  entry_count: 2,
  revision: 1,
  created_at: "2026-08-28T01:00:00Z",
  created_by: "account-1",
  reason: "客服政策更新",
};

const release41 = {
  ...release42,
  id: "release-41",
  release_number: 41,
  status: "published" as const,
  revision: 2,
  created_at: "2026-08-27T01:00:00Z",
};

const channels: ReleaseChannelPage = {
  items: [
    {
      id: "channel-uat",
      tenant_id: "tenant-a",
      code: "uat",
      name: "UAT 验证",
      status: "active",
      risk_tier: "medium",
      promotion_order: 10,
      is_default_serving: false,
      revision: 2,
      created_at: "2026-08-20T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
      summary: {
        channel_id: "channel-uat",
        configured: {
          profile_revision: 12,
          mutation_generation: 18,
          ownership_revision: 7,
          workspace_id: "workspace-a",
          workspace_revision: 9,
          policy_digest: "c".repeat(64),
        },
        candidate: {
          release_id: "release-42",
          release_number: 42,
          status: "candidate",
          revision: 1,
          manifest_digest: "a".repeat(64),
        },
        effective: {
          release_id: "release-41",
          release_number: 41,
          status: "published",
          revision: 2,
          manifest_digest: "d".repeat(64),
        },
        serving: {
          release_id: "release-41",
          release_number: 41,
          status: "published",
          revision: 2,
          manifest_digest: "d".repeat(64),
        },
        serving_generation: 3,
        comparison_state: "drifted",
        readiness: {
          state: "blocked",
          blocker_count: 1,
          blockers: [
            {
              code: "candidate_not_promoted",
              label: "候选尚未晋级",
              severity: "blocked",
              count: 1,
              reason: "等待验证完成",
            },
          ],
          fingerprint: "e".repeat(64),
          reason: "候选与生效 Release 不一致",
        },
        revision: 2,
      },
    },
  ],
  count: 1,
  next_cursor: null,
  summaries: {},
};

const history: ReleaseHistoryPage = {
  items: [release42, release41],
  count: 2,
  next_cursor: "history-next",
  summary: channels.items[0]!.summary,
};

const detail: ReleaseDetail = {
  manifest: release42,
  entries: {
    items: [
      {
        id: "entry-document-1",
        resource_type: "document_version",
        resource_id: "document-1",
        resource_revision: 4,
        content_digest: "f".repeat(64),
        facts: { title: "客服服务政策", source: "handbook" },
      },
    ],
    count: 1,
    next_cursor: null,
  },
};

const readiness: ReleaseReadiness = {
  state: "ready",
  blocker_count: 0,
  blockers: [],
  fingerprint: "b".repeat(64),
  reason: null,
};

const impact: ReleaseImpact = {
  state: "ready",
  count: 1,
  next_cursor: null,
  items: [
    {
      type: "application",
      id: "app-1",
      label: "客服助手",
      action: "review",
      reason: "将读取新候选版本",
    },
  ],
};

const audit: ReleaseAuditPage = {
  items: [
    {
      id: "event-1",
      event: "candidate_created",
      actor: "account-1",
      occurred_at: "2026-08-28T01:00:00Z",
      reason: "客服政策更新",
      request_id: "request-1",
    },
  ],
  count: 1,
  next_cursor: null,
};

const applied: ReleaseMutationOutcome = {
  state: "applied",
  operation: "capture",
  resource_id: "release-43",
  approval_request_id: null,
  revision: 1,
  message: "候选 Release 已生成",
  retryable: false,
};

function makeApi(overrides: Partial<ReleaseApi> = {}): ReleaseApi {
  return {
    fetchChannels: vi.fn().mockResolvedValue(channels),
    fetchHistory: vi.fn().mockResolvedValue(history),
    fetchDetail: vi.fn().mockResolvedValue(detail),
    fetchReadiness: vi.fn().mockResolvedValue(readiness),
    fetchImpact: vi.fn().mockResolvedValue(impact),
    fetchAudit: vi.fn().mockResolvedValue(audit),
    capture: vi.fn().mockResolvedValue(applied),
    promote: vi
      .fn()
      .mockResolvedValue({ ...applied, operation: "promote", resource_id: "release-42" }),
    rollback: vi
      .fn()
      .mockResolvedValue({ ...applied, operation: "rollback", resource_id: "release-41" }),
    ...overrides,
  };
}

const qualityPolicy: QualityPolicy = {
  id: "quality-policy-uat",
  tenant_id: "tenant-a",
  name: "UAT 质量门禁",
  scope_type: "channel",
  scope_value: "channel-uat",
  channel_id: "channel-uat",
  status: "active",
  revision: 1,
  min_experiment_count: 1,
  min_judged_result_count: 2,
  min_judgment_coverage_bps: 10000,
  min_exact_agreement_bps: 10000,
  min_mean_score_milli: 2000,
  max_conflicting_results: 0,
  require_all_experiments_completed: true,
  require_no_degraded_results: true,
  max_certification_age_minutes: 1440,
  policy_digest: "1".repeat(64),
  created_at: "2026-08-28T00:00:00Z",
  created_by: "owner-a",
  updated_at: "2026-08-28T00:00:00Z",
  updated_by: "owner-a",
};

const qualityBaseline: QualityBaseline = {
  id: "quality-baseline-uat",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  name: "UAT 核心问答",
  baseline_revision: 1,
  parent_baseline_id: null,
  experiment_count: 1,
  query_count: 1,
  baseline_digest: "2".repeat(64),
  created_at: "2026-08-28T00:00:00Z",
  created_by: "owner-a",
  reason: "冻结评测",
  request_id: null,
  items: [],
};

const qualityCertification: QualityCertification = {
  id: "quality-certification-uat",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  baseline_id: qualityBaseline.id,
  policy_id: qualityPolicy.id,
  policy_revision: 1,
  release_manifest_digest: "a".repeat(64),
  release_mutation_generation: 18,
  release_serving_generation: 3,
  status: "passed",
  experiment_count: 1,
  completed_experiment_count: 1,
  degraded_experiment_count: 0,
  query_count: 1,
  judged_result_count: 2,
  total_result_count: 2,
  judgment_count: 3,
  judgment_coverage_bps: 10000,
  multi_judged_results: 1,
  unanimous_results: 1,
  conflicting_results: 0,
  exact_agreement_bps: 10000,
  mean_score_milli: 2000,
  failed_rule_count: 0,
  evidence_digest: "3".repeat(64),
  certification_digest: "4".repeat(64),
  valid_until: "2026-08-29T00:00:00Z",
  created_at: "2026-08-28T01:00:00Z",
  created_by: "owner-a",
  reason: "UAT 认证",
};

const qualityGate: QualityGate = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  channel_id: "channel-uat",
  release_manifest_digest: "a".repeat(64),
  channel_revision: 2,
  risk_tier: "medium",
  is_default_serving: false,
  state: "passed",
  reason: "certification_passed",
  policy: qualityPolicy,
  certification: qualityCertification,
  waiver: null,
};

function makeQualityApi(overrides: Partial<ReleaseQualityApi> = {}): ReleaseQualityApi {
  return {
    fetchPolicies: vi.fn().mockResolvedValue({
      items: [qualityPolicy],
      next_cursor: null,
      invalid_item_count: 0,
    }),
    fetchBaselines: vi.fn().mockResolvedValue({
      items: [qualityBaseline],
      next_cursor: null,
      invalid_item_count: 0,
    }),
    fetchBaseline: vi.fn().mockResolvedValue(qualityBaseline),
    fetchCertifications: vi.fn().mockResolvedValue({
      items: [qualityCertification],
      next_cursor: null,
      invalid_item_count: 0,
    }),
    fetchGate: vi.fn().mockResolvedValue(qualityGate),
    createBaseline: vi.fn(),
    certify: vi.fn().mockResolvedValue({
      state: "applied",
      operation: "release_quality_certify",
      resource_id: qualityCertification.id,
      message: "已认证",
      retryable: false,
    }),
    updatePolicy: vi.fn(),
    requestWaiver: vi.fn(),
    ...overrides,
  };
}

function setViewport(mobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: mobile,
      media: "(max-width: 768px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

async function renderReady(
  props: Partial<React.ComponentProps<typeof KnowledgeBaseReleaseCenter>> = {},
) {
  const api = props.api ?? makeApi();
  render(
    <KnowledgeBaseReleaseCenter
      active
      scope={scope}
      api={api}
      datasetName="客服知识库"
      workspaceName="生产知识域"
      {...props}
    />,
  );
  await waitFor(() =>
    expect(api.fetchHistory).toHaveBeenCalledWith(scope, "channel-uat", {}, expect.anything()),
  );
  await screen.findByRole("button", { name: "查看 Release 42" });
  return api;
}

afterEach(cleanup);

beforeEach(() => {
  setViewport(false);
});

describe("Stage19 Release Center", () => {
  it("renders the channel control plane and a desktop PrimaryTable with truthful state layers", async () => {
    await renderReady();

    expect(screen.getByRole("region", { name: "Knowledge Base Release 控制中心" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Release 控制中心" })).toBeTruthy();
    expect(screen.getByText("UAT 验证")).toBeTruthy();
    expect(screen.getByText("配置已漂移")).toBeTruthy();
    expect(screen.getAllByText("候选").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已发布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可发布").length).toBeGreaterThan(0);
    expect(screen.getByTestId("release-history-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("release-history-mobile-cards")).toBeNull();
    expect(screen.getByRole("button", { name: "生成 Release 候选" })).toBeTruthy();
  });

  it("uses compact mobile release cards and keeps channel switching visibly labelled", async () => {
    setViewport(true);
    await renderReady();

    expect(screen.getByRole("combobox", { name: "发布 Channel" })).toBeTruthy();
    expect(screen.getByTestId("release-history-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("release-history-desktop-table")).toBeNull();
    expect(screen.getByRole("button", { name: "查看 Release 42" })).toBeTruthy();
  });

  it("opens one detail Drawer and lazy-loads Impact and Audit tabs", async () => {
    const api = await renderReady();
    const trigger = screen.getByRole("button", { name: "查看 Release 42" });
    fireEvent.click(trigger);

    const drawer = await screen.findByRole("dialog", { name: /Release 42 详情/ });
    expect(within(drawer).getByRole("tab", { name: "Manifest" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Readiness" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Impact" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Audit" })).toBeTruthy();
    expect(api.fetchImpact).not.toHaveBeenCalled();
    expect(api.fetchAudit).not.toHaveBeenCalled();

    fireEvent.click(within(drawer).getByRole("tab", { name: "Impact" }));
    await waitFor(() => expect(api.fetchImpact).toHaveBeenCalledWith(scope, "release-42"));
    expect(await within(drawer).findByText("客服助手")).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("tab", { name: "Audit" }));
    await waitFor(() => expect(api.fetchAudit).toHaveBeenCalledWith(scope, "release-42"));
    expect(await within(drawer).findByText("candidate_created")).toBeTruthy();
    expect(document.querySelectorAll(".t-drawer")).toHaveLength(1);
  });

  it("closes the detail Drawer with Escape and returns focus to the release trigger", async () => {
    await renderReady();
    const trigger = screen.getByRole("button", { name: "查看 Release 42" });
    fireEvent.click(trigger);
    await screen.findByRole("dialog", { name: /Release 42 详情/ });

    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /Release 42 详情/ })).toBeNull(),
    );
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("opens the capture Dialog, submits the observed revision fence, and handles the applied outcome", async () => {
    const api = await renderReady();
    fireEvent.click(screen.getByRole("button", { name: "生成 Release 候选" }));
    const dialog = await screen.findByRole("dialog", { name: "生成 Release 候选" });
    const reason = within(dialog).getByRole("textbox", { name: "候选原因" });
    fireEvent.change(reason, { target: { value: "完成客服政策校验" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "生成候选" }));

    await waitFor(() =>
      expect(api.capture).toHaveBeenCalledWith(
        scope,
        {
          expectedProfileRevision: 12,
          expectedOwnershipRevision: 7,
          expectedWorkspaceRevision: 9,
          expectedMutationGeneration: 18,
          expectedServingGeneration: 3,
          reason: "完成客服政策校验",
        },
        expect.objectContaining({ idempotencyKey: expect.any(String) }),
      ),
    );
    await waitFor(() =>
      expect(screen.getAllByText("候选 Release 已生成").length).toBeGreaterThan(0),
    );
  });

  it("offers promote and rollback Dialog surfaces from the same detail Drawer", async () => {
    const api = await renderReady();
    const trigger = screen.getByRole("button", { name: "查看 Release 42" });
    fireEvent.click(trigger);
    const drawer = await screen.findByRole("dialog", { name: /Release 42 详情/ });

    fireEvent.click(within(drawer).getByRole("button", { name: "发布到 UAT 验证" }));
    const promoteDialog = await screen.findByRole("dialog", { name: "发布 Release 到 Channel" });
    expect(within(promoteDialog).getByRole("textbox", { name: "变更原因" })).toBeTruthy();
    fireEvent.click(within(promoteDialog).getByRole("button", { name: "取消" }));

    fireEvent.click(within(drawer).getByRole("button", { name: "回滚到 Release 41" }));
    const rollbackDialog = await screen.findByRole("dialog", { name: "回滚 Channel" });
    const rollbackReason = within(rollbackDialog).getByRole("textbox", { name: "变更原因" });
    fireEvent.change(rollbackReason, { target: { value: "验证回滚路径" } });
    fireEvent.click(within(rollbackDialog).getByRole("button", { name: "确认回滚" }));
    await waitFor(() => expect(api.rollback).toHaveBeenCalled());
  });

  it("keeps all mutation controls disabled in read-only mode", async () => {
    await renderReady({ readOnly: true });

    expect(screen.getByText("只读事实")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成 Release 候选" })).toHaveProperty(
      "disabled",
      true,
    );
    const trigger = screen.getByRole("button", { name: "查看 Release 42" });
    fireEvent.click(trigger);
    const drawer = await screen.findByRole("dialog", { name: /Release 42 详情/ });
    expect(within(drawer).getByRole("button", { name: "发布到 UAT 验证" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(within(drawer).getByRole("button", { name: "回滚到 Release 41" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("supports arrow-key navigation across Release detail Tabs", async () => {
    await renderReady();
    fireEvent.click(screen.getByRole("button", { name: "查看 Release 42" }));
    const drawer = await screen.findByRole("dialog", { name: /Release 42 详情/ });
    const manifestTab = within(drawer).getByRole("tab", { name: "Manifest" });
    manifestTab.focus();
    fireEvent.keyDown(manifestTab, { key: "ArrowRight" });
    await waitFor(() =>
      expect(
        within(drawer).getByRole("tab", { name: "Readiness" }).getAttribute("aria-selected"),
      ).toBe("true"),
    );
  });

  it("redacts mutation errors and disables same-key retry after switching to read-only", async () => {
    const capture = vi.fn().mockRejectedValue(new Error("ticket=opaque-ticket-value"));
    const api = makeApi({ capture });
    const view = render(
      <KnowledgeBaseReleaseCenter
        active
        scope={scope}
        api={api}
        datasetName="客服知识库"
        workspaceName="生产知识域"
      />,
    );
    await screen.findByRole("button", { name: "查看 Release 42" });
    fireEvent.click(screen.getByRole("button", { name: "生成 Release 候选" }));
    const dialog = await screen.findByRole("dialog", { name: "生成 Release 候选" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "候选原因" }), {
      target: { value: "验证脱敏" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "生成候选" }));
    await within(dialog).findByText("服务端拒绝了本次 Release 操作。");
    expect(document.body.textContent).not.toContain("opaque-ticket-value");

    view.unmount();
    const retryMutation = vi.fn();
    render(
      <ReleaseMutationDialog
        visible
        kind="capture"
        channel={channels.items[0]!}
        release={release42}
        targetRelease={null}
        captureFence={{
          profileRevision: 12,
          ownershipRevision: 7,
          workspaceRevision: 9,
          mutationGeneration: 18,
          servingGeneration: 3,
        }}
        servingGeneration={3}
        readOnly
        saving={false}
        error={new Error("ticket=opaque-ticket-value")}
        onClose={vi.fn()}
        onRetry={retryMutation}
        onSubmit={vi.fn()}
      />,
    );
    const readOnlyDialog = screen.getByRole("dialog", { name: "生成 Release 候选" });
    expect(within(readOnlyDialog).queryByRole("button", { name: "使用同一请求重试" })).toBeNull();
    expect(retryMutation).not.toHaveBeenCalled();
  });

  it("shows unavailable instead of a truthful-empty state when every declared row is malformed", async () => {
    const api = makeApi({
      fetchHistory: vi.fn().mockResolvedValue({
        items: [],
        count: 1,
        next_cursor: null,
        summary: channels.items[0]!.summary,
        invalid_item_count: 1,
      }),
    });
    render(
      <KnowledgeBaseReleaseCenter
        active
        scope={scope}
        api={api}
        datasetName="客服知识库"
        workspaceName="生产知识域"
      />,
    );
    expect(await screen.findByText("发布权威无法验证")).toBeTruthy();
    expect(screen.queryByText("当前 Channel 没有 Release")).toBeNull();
  });

  it("allows the first candidate capture from current configured authority without history", async () => {
    const capture = vi.fn().mockResolvedValue(applied);
    const api = makeApi({
      capture,
      fetchHistory: vi.fn().mockResolvedValue({
        items: [],
        count: 0,
        next_cursor: null,
        summary: channels.items[0]!.summary,
        invalid_item_count: 0,
      }),
    });
    render(
      <KnowledgeBaseReleaseCenter
        active
        scope={scope}
        api={api}
        datasetName="客服知识库"
        workspaceName="生产知识域"
      />,
    );
    const button = await screen.findByRole("button", { name: "生成 Release 候选" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog", { name: "生成 Release 候选" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "候选原因" }), {
      target: { value: "首次候选" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "生成候选" }));
    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith(
        scope,
        {
          expectedProfileRevision: 12,
          expectedOwnershipRevision: 7,
          expectedWorkspaceRevision: 9,
          expectedMutationGeneration: 18,
          expectedServingGeneration: 3,
          reason: "首次候选",
        },
        expect.objectContaining({ idempotencyKey: expect.any(String) }),
      ),
    );
  });

  it("integrates the Quality gate strip, history tag and Certification drawer tab", async () => {
    const qualityApi = makeQualityApi();
    await renderReady({ qualityApi });
    expect(await screen.findByText("质量门禁已通过")).toBeTruthy();
    expect(screen.getByText("已认证")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看 Release 42" }));
    const drawer = await screen.findByRole("dialog", { name: /Release 42 详情/ });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Certification" }));
    expect(await within(drawer).findByRole("heading", { name: "Release 质量认证" })).toBeTruthy();
    expect(qualityApi.fetchGate).toHaveBeenCalledWith(
      scope,
      "release-42",
      "channel-uat",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
