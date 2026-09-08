// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import KnowledgeServingCenter from "./KnowledgeServingCenter";
import ServingPolicyDialog from "./ServingPolicyDialog";
import type { KnowledgeServingController } from "./types";
import type { ServingStageFact, ServingSummary } from "../model/servingModel";

const digest = "a".repeat(64);
const now = "2026-08-30T00:00:00Z";
const stages: ServingStageFact[] = (["source", "parse", "chunk", "index", "serve"] as const).map(
  (stage_code, index) => ({
    id: `${stage_code}-fact`,
    tenant_id: "tenant-a",
    profile_id: "profile-a",
    snapshot_id: "snapshot-a",
    stage_code,
    sequence: index + 1,
    state: "ready",
    item_count: 10,
    ready_count: 10,
    warning_count: 0,
    pending_count: 0,
    error_count: 0,
    lag_seconds: 0,
    expected_revision: 4,
    observed_revision: 4,
    expected_digest: digest,
    observed_digest: digest,
    safe_error_code: null,
    safe_error: null,
    stage_digest: digest,
    observed_at: now,
  }),
);
const summary: ServingSummary = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  profile_id: "profile-a",
  profile_name: "产品知识库服务",
  profile_status: "active",
  state: "ready",
  as_of: now,
  snapshot_id: "snapshot-a",
  snapshot_digest: digest,
  policy_revision: 4,
  serving_generation: 18,
  source_count: 12,
  ready_source_count: 12,
  stale_source_count: 0,
  active_document_count: 240,
  failed_document_count: 0,
  pending_index_count: 0,
  stage_count: 5,
  ready_stage_count: 5,
  blocked_stage_count: 0,
  current_release_id: "release-18",
  current_certification_id: "cert-18",
  reason_code: null,
  stage_facts: stages,
};
const profile = {
  id: "profile-a",
  tenant_id: "tenant-a",
  workspace_id: "workspace-a",
  dataset_id: "dataset-a",
  name: "产品知识库服务",
  normalized_name: String("产品知识库服务").toLocaleLowerCase(),
  status: "active" as const,
  active_profile_key: "dataset-a",
  revision: 4,
  current_policy_revision_id: "policy-a",
  current_snapshot_id: "snapshot-a",
  created_at: now,
  created_by: "account-a",
  updated_at: now,
  updated_by: "account-a",
  archived_at: null,
  archived_by: null,
};
const policy = {
  id: "policy-a",
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  revision: 4,
  max_source_staleness_seconds: 3600,
  max_parse_lag_seconds: 7200,
  max_index_lag_seconds: 7200,
  max_failed_document_count: 2,
  max_pending_index_count: 3,
  require_current_release: true,
  require_passing_certification: false,
  policy_digest: digest,
  created_at: now,
  created_by: "account-a",
};
const snapshot = {
  id: "snapshot-a",
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  policy_revision_id: "policy-a",
  observation_key: "obs-a",
  state: "ready" as const,
  source_count: 12,
  ready_source_count: 12,
  stale_source_count: 0,
  active_document_count: 240,
  failed_document_count: 0,
  pending_index_count: 0,
  expected_serving_generation: 18,
  observed_serving_generation: 18,
  current_release_id: "release-18",
  current_certification_id: "cert-18",
  stage_count: 5,
  ready_stage_count: 5,
  blocked_stage_count: 0,
  snapshot_digest: digest,
  as_of: now,
  created_at: now,
  created_by: "account-a",
};

function controller(
  overrides: Partial<KnowledgeServingController> = {},
): KnowledgeServingController {
  return {
    active: true,
    readOnly: false,
    summary: { status: "ready", value: summary, error: null, reload: vi.fn() },
    profile: {
      status: "ready",
      value: { profile, current_policy: policy, current_snapshot: snapshot },
      error: null,
      reload: vi.fn(),
    },
    snapshots: {
      status: "ready",
      items: [snapshot],
      count: 1,
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: vi.fn(),
    },
    detail: { status: "idle", value: null, error: null, load: vi.fn() },
    activity: {
      status: "idle",
      items: [],
      count: null,
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      load: vi.fn(),
    },
    mutation: {
      status: "idle",
      outcome: null,
      error: null,
      createProfile: vi.fn(),
      createPolicyRevision: vi.fn(),
      activateProfile: vi.fn(),
      previewPolicy: vi.fn(),
    },
    ...overrides,
  };
}
afterEach(cleanup);

function clearedController(overrides: Partial<KnowledgeServingController> = {}) {
  return controller({
    active: true,
    readOnly: false,
    summary: { status: "idle", value: null, error: null, reload: vi.fn() },
    profile: { status: "idle", value: null, error: null, reload: vi.fn() },
    snapshots: {
      status: "idle",
      items: [],
      count: null,
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: vi.fn(),
    },
    detail: { status: "idle", value: null, error: null, load: vi.fn() },
    activity: {
      status: "idle",
      items: [],
      count: null,
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      load: vi.fn(),
    },
    ...overrides,
  });
}

describe("Stage26 KnowledgeServingCenter", () => {
  it("renders the authority header, Governance Evidence Strip, and five-stage rail", () => {
    render(
      <KnowledgeServingCenter
        controller={controller()}
        tenantLabel="RAG4C 企业"
        datasetName="产品知识库"
      />,
    );
    expect(screen.getByRole("heading", { name: "知识服务可靠性中心" })).toBeTruthy();
    expect(screen.getByText("RAG4C 企业")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Governance Evidence Strip" })).toBeTruthy();
    const rail = screen.getByRole("list", { name: "知识服务生命周期" });
    expect(
      within(rail)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("SOURCE"),
      expect.stringContaining("PARSE"),
      expect.stringContaining("CHUNK"),
      expect.stringContaining("INDEX"),
      expect.stringContaining("SERVE"),
    ]);
  });

  it("uses a dense PrimaryTable on desktop and priority cards on 375/280 mode", () => {
    const { rerender } = render(
      <KnowledgeServingCenter controller={controller()} mobile={false} />,
    );
    expect(screen.getByTestId("serving-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("serving-mobile-cards")).toBeNull();
    rerender(<KnowledgeServingCenter controller={controller()} mobile />);
    expect(screen.getByTestId("serving-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("serving-desktop-table")).toBeNull();
  });

  it.each([
    ["degraded", "服务状态：降级"],
    ["blocked", "服务状态：阻断"],
    ["unavailable", "服务状态：不可用"],
  ] as const)("makes %s an explicit operator state", (state, label) => {
    render(
      <KnowledgeServingCenter
        controller={controller({
          summary: { status: "ready", value: { ...summary, state }, error: null, reload: vi.fn() },
        })}
      />,
    );
    expect(screen.getByText(label)).toBeTruthy();
  });

  it("distinguishes partial and empty list states without inventing ready data", () => {
    const { rerender } = render(
      <KnowledgeServingCenter
        controller={controller({
          snapshots: {
            status: "partial",
            items: [],
            count: 3,
            nextCursor: null,
            invalidItemCount: 2,
            error: null,
            reload: vi.fn(),
          },
        })}
      />,
    );
    expect(screen.getByText("部分服务快照可用")).toBeTruthy();
    rerender(
      <KnowledgeServingCenter
        controller={controller({
          snapshots: {
            status: "empty",
            items: [],
            count: 0,
            nextCursor: null,
            invalidItemCount: 0,
            error: null,
            reload: vi.fn(),
          },
        })}
      />,
    );
    expect(screen.getByText("暂无服务快照")).toBeTruthy();
  });

  it("opens one detail Drawer with Overview/Evidence/Pipeline/Policy/History", async () => {
    const user = userEvent.setup();
    const load = vi.fn().mockResolvedValue(true);
    render(
      <KnowledgeServingCenter
        controller={controller({
          detail: {
            status: "ready",
            value: { snapshot, stage_facts: stages, evidence_links: [], events: [] },
            error: null,
            load,
          },
        })}
      />,
    );
    const button = screen.getByRole("button", { name: "查看服务快照 snapshot-a 详情" });
    await user.click(button);
    expect(load).toHaveBeenCalledWith("snapshot-a");
    expect(screen.getByRole("dialog", { name: "服务快照详情" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Evidence" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Pipeline" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Serving Policy" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "History" })).toBeTruthy();
  });

  it("closes a stale detail Drawer and never falls back to the selected Snapshot after an authority change", async () => {
    const user = userEvent.setup();
    const load = vi.fn().mockResolvedValue(true);
    const { rerender } = render(
      <KnowledgeServingCenter
        controller={controller({
          detail: { status: "loading", value: null, error: null, load },
        })}
        datasetId="dataset-a"
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看服务快照 snapshot-a 详情" }));
    expect(screen.getByRole("dialog", { name: "服务快照详情" })).toBeTruthy();

    rerender(<KnowledgeServingCenter controller={clearedController()} datasetId="dataset-b" />);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "服务快照详情" })).toBeNull());
    expect(screen.queryByText("snapshot-a")).toBeNull();
  });

  it("resets filters, policy preview, and the policy Dialog across capability/read-only authority changes", async () => {
    const user = userEvent.setup();
    const previewPolicy = vi.fn().mockResolvedValue({
      preview: true,
      state: "ready",
      policy_revision: 4,
      stage_facts: stages,
      blockers: [],
    });
    const initialController = controller({
      mutation: { ...controller().mutation, previewPolicy },
    });
    const { rerender } = render(
      <KnowledgeServingCenter controller={initialController} datasetId="dataset-a" />,
    );

    await user.type(screen.getByPlaceholderText("搜索 authority ID"), "snapshot-a");
    await user.click(screen.getByRole("button", { name: "调整服务策略" }));
    await user.click(screen.getByRole("button", { name: "零写入预览" }));
    await waitFor(() => expect(screen.getByText("策略模拟结果")).toBeTruthy());

    rerender(
      <KnowledgeServingCenter
        controller={clearedController({ active: false, readOnly: true })}
        datasetId="dataset-b"
      />,
    );
    rerender(
      <KnowledgeServingCenter
        controller={clearedController({ readOnly: true })}
        datasetId="dataset-b"
      />,
    );

    expect(screen.queryByRole("dialog", { name: "服务策略" })).toBeNull();
    expect(screen.queryByText("策略模拟结果")).toBeNull();
    expect(screen.getByPlaceholderText("搜索 authority ID")).toHaveProperty("value", "");
  });

  it("keeps policy controls disabled and labels the surface read-only", () => {
    render(<KnowledgeServingCenter controller={controller({ readOnly: true })} />);
    expect(screen.getByText("只读事实")).toBeTruthy();
    expect(screen.getByRole("button", { name: "调整服务策略" }).getAttribute("aria-disabled")).toBe(
      "true",
    );
  });
});

describe("Stage26 ServingPolicyDialog", () => {
  it("previews a policy without invoking a mutation and shows the zero-write contract", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn().mockResolvedValue({
      preview: true,
      state: "ready",
      policy_revision: 4,
      stage_facts: stages,
      blockers: [],
    });
    const onSubmit = vi.fn();
    render(
      <ServingPolicyDialog
        visible
        profile={profile}
        policy={policy}
        readOnly={false}
        saving={false}
        preview={null}
        error={null}
        onClose={vi.fn()}
        onPreview={onPreview}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("dialog", { name: "服务策略" })).toBeTruthy();
    expect(screen.getByText("预览不会写入快照或推进来源状态")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "零写入预览" }));
    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
