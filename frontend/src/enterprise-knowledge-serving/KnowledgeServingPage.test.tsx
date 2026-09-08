// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeServingPage from "./KnowledgeServingPage";
import type { ServingApi } from "./api/servingApi";

const digest = "a".repeat(64);
const now = "2026-08-30T00:00:00Z";
const stage = (stage_code: "source" | "parse" | "chunk" | "index" | "serve", sequence: number) => ({
  id: `${stage_code}-fact`,
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  snapshot_id: "snapshot-a",
  stage_code,
  sequence,
  state: "ready",
  item_count: 1,
  ready_count: 1,
  warning_count: 0,
  pending_count: 0,
  error_count: 0,
  lag_seconds: 0,
  expected_revision: 1,
  observed_revision: 1,
  expected_digest: digest,
  observed_digest: digest,
  safe_error_code: null,
  safe_error: null,
  stage_digest: digest,
  observed_at: now,
});
const summary = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  profile_id: "profile-a",
  profile_name: "知识服务",
  profile_status: "active",
  state: "ready",
  as_of: now,
  snapshot_id: "snapshot-a",
  snapshot_digest: digest,
  policy_revision: 1,
  serving_generation: 1,
  source_count: 1,
  ready_source_count: 1,
  stale_source_count: 0,
  active_document_count: 1,
  failed_document_count: 0,
  pending_index_count: 0,
  stage_count: 5,
  ready_stage_count: 5,
  blocked_stage_count: 0,
  current_release_id: null,
  current_certification_id: null,
  reason_code: null,
  stage_facts: [
    stage("source", 1),
    stage("parse", 2),
    stage("chunk", 3),
    stage("index", 4),
    stage("serve", 5),
  ],
};
const profile = {
  id: "profile-a",
  tenant_id: "tenant-a",
  workspace_id: "workspace-a",
  dataset_id: "dataset-a",
  name: "知识服务",
  normalized_name: String("知识服务").toLocaleLowerCase(),
  status: "active",
  active_profile_key: "dataset-a",
  revision: 1,
  current_policy_revision_id: null,
  current_snapshot_id: "snapshot-a",
  created_at: now,
  created_by: "account-a",
  updated_at: now,
  updated_by: "account-a",
  archived_at: null,
  archived_by: null,
};
const api: ServingApi = {
  fetchSummary: vi.fn().mockResolvedValue(summary),
  fetchProfile: vi
    .fn()
    .mockResolvedValue({ profile, current_policy: null, current_snapshot: null }),
  fetchSnapshots: vi
    .fn()
    .mockResolvedValue({ items: [], count: 0, next_cursor: null, invalid_item_count: 0 }),
  fetchSnapshot: vi
    .fn()
    .mockResolvedValue({ snapshot: null, stage_facts: [], evidence_links: [], events: [] }),
  fetchStageFacts: vi
    .fn()
    .mockResolvedValue({ items: [], count: 0, next_cursor: null, invalid_item_count: 0 }),
  fetchEvents: vi
    .fn()
    .mockResolvedValue({ items: [], count: 0, next_cursor: null, invalid_item_count: 0 }),
  createProfile: vi.fn(),
  createPolicyRevision: vi.fn(),
  activateProfile: vi.fn(),
  previewPolicy: vi.fn(),
};
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

describe("Stage26 KnowledgeServingPage", () => {
  it("fails closed when the capability is unavailable", () => {
    render(
      <KnowledgeServingPage
        active
        tenantId="tenant-a"
        datasetId="dataset-a"
        actorToken="actor-token"
        capabilityReady={false}
        tenantLabel="RAG4C 企业"
        readOnly={false}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("知识服务可靠性能力不可用");
  });

  it("clears authority reads when the kept-alive Page becomes inactive", async () => {
    const { rerender } = render(
      <KnowledgeServingPage
        active
        tenantId="tenant-a"
        accountId="account-a"
        datasetId="dataset-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="RAG4C 企业"
        readOnly={false}
        api={api}
      />,
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("region", { name: "知识服务可靠性中心" })).toBeTruthy();

    rerender(
      <KnowledgeServingPage
        active={false}
        tenantId="tenant-a"
        accountId="account-a"
        datasetId="dataset-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="RAG4C 企业"
        readOnly={false}
        api={api}
      />,
    );

    await waitFor(() => expect(screen.getByText("知识服务可靠性中心未启用")).toBeTruthy());
    expect(api.fetchSummary).toHaveBeenCalledTimes(1);
    expect(api.fetchProfile).toHaveBeenCalledTimes(1);
    expect(api.fetchSnapshots).toHaveBeenCalledTimes(1);
  });

  it("binds the real scope to the center and exposes only allowlisted handoff facts", async () => {
    const onHandoff = vi.fn();
    render(
      <KnowledgeServingPage
        active
        tenantId="tenant-a"
        accountId="account-a"
        datasetId="dataset-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="RAG4C 企业"
        readOnly={false}
        api={api}
        onHandoff={onHandoff}
      />,
    );
    await waitFor(() => expect(screen.getByText("知识服务可靠性中心")).toBeTruthy());
    expect(api.fetchSummary).toHaveBeenCalledWith(
      expect.objectContaining({ tenantId: "tenant-a", datasetId: "dataset-a" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.queryByText("raw_source_payload")).toBeNull();
  });
});
