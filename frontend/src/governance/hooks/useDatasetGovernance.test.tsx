// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as api from "../api/governanceApi";
import type { DatasetProfile, GovernanceScope } from "../model/governanceModel";
import { useDatasetGovernance } from "./useDatasetGovernance";

vi.mock("../api/governanceApi");

const scope: GovernanceScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token" };
const profile = (revision = 1, status: DatasetProfile["status"] = "active"): DatasetProfile => ({
  id: "dataset-a", tenant_id: "tenant-a", name: "Support", description: "Trusted support",
  status, profile_revision: revision, owner_id: "owner-a", visibility: "private", profile: {},
  policies: { parser: {}, chunk: {}, retrieval: {}, retention: {}, metadata: {} },
  default_language: "zh-CN", graph_enabled: true, qa_enabled: true,
  usage: { documents: 4, chunks: 20 },
  timestamps: { created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-25T00:00:00Z", archived_at: null, archived_by: null },
});

beforeEach(() => vi.resetAllMocks());

describe("useDatasetGovernance", () => {
  it("fails closed without scope and remains truthful offline without issuing requests", () => {
    const { result, rerender } = renderHook(
      ({ currentScope, online }) => useDatasetGovernance(currentScope, online),
      { initialProps: { currentScope: null as GovernanceScope | null, online: true as boolean | null } },
    );
    expect(result.current.status).toBe("scope");
    expect(api.fetchDatasetProfile).not.toHaveBeenCalled();

    rerender({ currentScope: scope, online: false });
    expect(result.current.status).toBe("offline");
    expect(api.fetchDatasetProfile).not.toHaveBeenCalled();
  });

  it("loads authoritative profile and refetches it after a revision-fenced update", async () => {
    vi.mocked(api.fetchDatasetProfile)
      .mockResolvedValueOnce(profile(1))
      .mockResolvedValueOnce(profile(2));
    vi.mocked(api.patchDatasetProfile).mockResolvedValue(profile(2));
    const { result } = renderHook(() => useDatasetGovernance(scope, true));

    await waitFor(() => expect(result.current.profile?.profile_revision).toBe(1));
    await act(async () => {
      await result.current.update({ expected_revision: 1, visibility: "tenant" });
    });

    expect(api.patchDatasetProfile).toHaveBeenCalledWith(
      scope,
      { expected_revision: 1, visibility: "tenant" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.fetchDatasetProfile).toHaveBeenCalledTimes(2);
    expect(result.current.profile?.profile_revision).toBe(2);
    expect(result.current.error).toBeNull();
  });

  it("keeps the current fact and exposes refresh when a mutation revision is stale", async () => {
    vi.mocked(api.fetchDatasetProfile).mockResolvedValueOnce(profile(4)).mockResolvedValueOnce(profile(5));
    vi.mocked(api.patchDatasetProfile).mockRejectedValue(new ApiError("raw", "http", 409));
    const { result } = renderHook(() => useDatasetGovernance(scope, true));
    await waitFor(() => expect(result.current.profile?.profile_revision).toBe(4));

    await act(async () => {
      await result.current.update({ expected_revision: 3, owner_id: "owner-b" });
    });

    expect(result.current.profile?.profile_revision).toBe(5);
    expect(api.fetchDatasetProfile).toHaveBeenCalledTimes(2);
    expect(result.current.error?.kind).toBe("conflict");
    expect(result.current.error?.canRetry).toBe(true);
  });

  it("does not refetch when equivalent scope objects rerender repeatedly", async () => {
    vi.mocked(api.fetchDatasetProfile).mockResolvedValue(profile(1));
    const { result, rerender } = renderHook(({ tenant }) => useDatasetGovernance({ tenantId: tenant, datasetId: "dataset-a", actorToken: "token" }, true), { initialProps: { tenant: "tenant-a" } });
    await waitFor(() => expect(result.current.profile?.id).toBe("dataset-a"));
    rerender({ tenant: "tenant-a" }); rerender({ tenant: "tenant-a" }); rerender({ tenant: "tenant-a" });
    await Promise.resolve(); expect(api.fetchDatasetProfile).toHaveBeenCalledTimes(1);
  });

  it("clears A and ignores an A mutation that completes after scope B loads", async () => {
    let resolveMutation!: (value: DatasetProfile) => void;
    const mutation = new Promise<DatasetProfile>((resolve) => { resolveMutation = resolve; });
    vi.mocked(api.fetchDatasetProfile).mockImplementation(async (current) => ({ ...profile(1), id: current.datasetId, tenant_id: current.tenantId }));
    vi.mocked(api.patchDatasetProfile).mockReturnValue(mutation);
    const scopeB = { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b" };
    const { result, rerender } = renderHook(({ current }) => useDatasetGovernance(current, true), { initialProps: { current: scope } });
    await waitFor(() => expect(result.current.profile?.id).toBe("dataset-a")); act(() => { void result.current.update({ expected_revision: 1, visibility: "tenant" }); });
    rerender({ current: scopeB }); expect(result.current.profile).toBeNull(); await waitFor(() => expect(result.current.profile?.id).toBe("dataset-b"));
    resolveMutation({ ...profile(99), id: "dataset-a" }); await mutation; await Promise.resolve(); expect(result.current.profile?.id).toBe("dataset-b");
  });

});
