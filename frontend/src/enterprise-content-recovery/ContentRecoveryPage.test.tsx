// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

const recoveryHook = vi.hoisted(() => ({ useEnterpriseContentRecovery: vi.fn() }));
vi.mock("./hooks/useEnterpriseContentRecovery", () => recoveryHook);
vi.mock("./components", () => ({
  ContentRecoveryCenter: ({ controller, onApprovalHandoff }: any) => (
    <section aria-label="恢复中心适配器">
      <span>{controller.entries.items[0]?.document_label}</span>
      <span>{controller.summary.value?.recycled_count}</span>
      <button onClick={() => controller.mutation.restore?.(controller.entries.items[0])}>
        恢复
      </button>
      <button onClick={() => onApprovalHandoff?.("approval-stage23")}>审批交接</button>
    </section>
  ),
}));

import EnterpriseContentRecoveryPage from "./ContentRecoveryPage";

const restore = vi.fn().mockResolvedValue({ state: "applied", operation: "restore" });
const reload = vi.fn().mockResolvedValue(true);

recoveryHook.useEnterpriseContentRecovery.mockReturnValue({
  active: true,
  load: { status: "ready", error: null, reload },
  summary: {
    status: "ready",
    value: {
      tenant_id: "tenant-a",
      state: "ready",
      recycled_count: 2,
      expiring_count: 1,
      held_count: 1,
      pending_purge_count: 0,
      as_of: "2026-08-29T12:00:00Z",
      reason_code: null,
    },
    error: null,
  },
  entries: {
    status: "ready",
    items: [
      {
        id: "entry-a",
        tenant_id: "tenant-a",
        dataset_id: "dataset-a",
        document_id: "document-a",
        recycle_generation: 1,
        active_recycle_key: "dataset-a:document-a",
        status: "recycled",
        revision: 1,
        document_mutation_generation: 4,
        original_lifecycle_state: "active",
        original_retrieval_enabled: true,
        retention_days_snapshot: 30,
        recycled_at: "2026-08-29T12:00:00Z",
        recycled_by: "owner-a",
        purge_eligible_at: "2026-09-28T12:00:00Z",
        restored_at: null,
        restored_by: null,
        purge_requested_at: null,
        purged_at: null,
        purged_by: null,
        safe_snapshot: {},
        snapshot_digest: "a".repeat(64),
        created_at: "2026-08-29T12:00:00Z",
        updated_at: "2026-08-29T12:00:00Z",
      },
    ],
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
  },
  detail: { status: "idle", value: null, error: null, load: vi.fn() },
  holds: {
    status: "idle",
    items: [],
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
    entryId: null,
    load: vi.fn(),
  },
  purgeRequests: {
    status: "idle",
    items: [],
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
    entryId: null,
    load: vi.fn(),
  },
  policy: { status: "idle", value: null, error: null, load: vi.fn() },
  retentionPolicy: { status: "idle", value: null, error: null, load: vi.fn() },
  mutation: {
    status: "idle",
    outcome: null,
    error: null,
    recycle: vi.fn(),
    bulkRecycle: vi.fn(),
    restore,
    applyHold: vi.fn(),
    releaseHold: vi.fn(),
    requestPurge: vi.fn(),
    cancelPurge: vi.fn(),
    updatePolicy: vi.fn(),
    retry: vi.fn(),
  },
});

describe("EnterpriseContentRecoveryPage adapter", () => {
  it("maps authoritative hook facts into the TDesign controller and serializes restore", async () => {
    render(
      <EnterpriseContentRecoveryPage
        tenantId="tenant-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
        onApprovalHandoff={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "恢复中心适配器" })).toBeTruthy();
    expect(screen.getByText("document-a")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() =>
      expect(restore).toHaveBeenCalledWith(
        "entry-a",
        { expectedRevision: 1, reason: "Recovery Center explicit restore" },
        undefined,
      ),
    );
  });

  it("passes safe approval request IDs to App handoff", () => {
    const onApprovalHandoff = vi.fn();
    render(
      <EnterpriseContentRecoveryPage
        tenantId="tenant-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
        onApprovalHandoff={onApprovalHandoff}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "审批交接" }));
    expect(onApprovalHandoff).toHaveBeenCalledWith("approval-stage23");
  });
});
