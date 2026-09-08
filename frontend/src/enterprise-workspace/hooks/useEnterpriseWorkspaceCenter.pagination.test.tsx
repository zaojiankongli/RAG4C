// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import {
  fetchEnterpriseWorkspaceDetail,
  fetchEnterpriseWorkspaces,
  fetchWorkspaceDatasets,
  fetchWorkspaceMembers,
} from "../api/enterpriseWorkspaceApi";
import type {
  EnterpriseWorkspace,
  WorkspaceDatasetBinding,
  WorkspaceMember,
} from "../enterpriseWorkspaceModel";
import { projectWorkspaceError, useEnterpriseWorkspaceCenter } from "./useEnterpriseWorkspace";

vi.mock("../api/enterpriseWorkspaceApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/enterpriseWorkspaceApi")>();
  return {
    ...actual,
    fetchEnterpriseWorkspaces: vi.fn(),
    fetchEnterpriseWorkspaceDetail: vi.fn(),
    fetchWorkspaceMembers: vi.fn(),
    fetchWorkspaceDatasets: vi.fn(),
  };
});

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };
const workspace = (id: string): EnterpriseWorkspace => ({
  id,
  tenant_id: "tenant-a",
  code: id,
  name: id,
  description: "",
  status: "active",
  environment: "production",
  is_default: id === "workspace-a",
  revision: 1,
  member_count: null,
  dataset_count: null,
  primary_dataset_count: null,
  actor_role: "owner",
  created_at: null,
  updated_at: null,
  archived_at: null,
});
const member = (id: string): WorkspaceMember => ({
  account_id: id,
  name: id,
  email: `${id}@example.com`,
  role: "owner",
  status: "active",
  revision: 1,
  created_at: null,
  updated_at: null,
});
const dataset = (id: string): WorkspaceDatasetBinding => ({
  dataset_id: id,
  name: id,
  binding_kind: "primary",
  status: "active",
  revision: 1,
  created_at: null,
  updated_at: null,
});
const evidence = {
  workspace_count: null,
  active_count: null,
  default_workspace_id: "workspace-a",
  primary_dataset_binding_count: null,
  authorization_state: "workspace_authorization_not_enforced" as const,
};

beforeEach(() => {
  vi.mocked(fetchEnterpriseWorkspaces).mockReset();
  vi.mocked(fetchEnterpriseWorkspaceDetail).mockReset();
  vi.mocked(fetchWorkspaceMembers).mockReset();
  vi.mocked(fetchWorkspaceDatasets).mockReset();
});

describe("useEnterpriseWorkspaceCenter keyset continuation", () => {
  it("merges Workspace pages by ID and preserves unknown totals", async () => {
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce({
        items: [workspace("workspace-a")],
        count: null,
        next_cursor: "w-1",
        evidence,
      })
      .mockResolvedValueOnce({
        items: [workspace("workspace-a"), workspace("workspace-b")],
        count: null,
        next_cursor: null,
        evidence,
      });

    const { result } = renderHook(() => useEnterpriseWorkspaceCenter(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.page?.count).toBeNull();
    expect(result.current.page?.next_cursor).toBe("w-1");

    await act(() => result.current.loadMoreWorkspaces());

    expect(result.current.page?.items.map((item) => item.id)).toEqual([
      "workspace-a",
      "workspace-b",
    ]);
    expect(result.current.page?.count).toBeNull();
    expect(result.current.page?.next_cursor).toBeNull();
    expect(result.current.workspaceMoreLoading).toBe(false);
    expect(result.current.workspaceMoreError).toBeNull();
  });

  it("loads member and dataset continuation independently with stable deduplication", async () => {
    vi.mocked(fetchEnterpriseWorkspaces).mockResolvedValue({
      items: [workspace("workspace-a")],
      count: 1,
      next_cursor: null,
      evidence: { ...evidence, workspace_count: 1 },
    });
    vi.mocked(fetchEnterpriseWorkspaceDetail).mockResolvedValue({
      workspace: workspace("workspace-a"),
      members: {
        items: [],
        count: null,
        next_cursor: null,
        authorization_state: evidence.authorization_state,
      },
      datasets: {
        items: [],
        count: null,
        next_cursor: null,
        authorization_state: evidence.authorization_state,
      },
      authorization_state: evidence.authorization_state,
    });
    vi.mocked(fetchWorkspaceMembers)
      .mockResolvedValueOnce({
        items: [member("owner-a")],
        count: null,
        next_cursor: "m-1",
        authorization_state: evidence.authorization_state,
      })
      .mockResolvedValueOnce({
        items: [member("owner-a"), member("admin-a")],
        count: null,
        next_cursor: null,
        authorization_state: evidence.authorization_state,
      });
    vi.mocked(fetchWorkspaceDatasets)
      .mockResolvedValueOnce({
        items: [dataset("dataset-a")],
        count: null,
        next_cursor: "d-1",
        authorization_state: evidence.authorization_state,
      })
      .mockResolvedValueOnce({
        items: [dataset("dataset-a"), dataset("dataset-b")],
        count: null,
        next_cursor: null,
        authorization_state: evidence.authorization_state,
      });

    const { result } = renderHook(() => useEnterpriseWorkspaceCenter(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(() => result.current.openWorkspace("workspace-a"));
    expect(result.current.selected?.members.next_cursor).toBe("m-1");
    expect(result.current.selected?.datasets.next_cursor).toBe("d-1");

    await act(async () => {
      await Promise.all([result.current.loadMoreMembers(), result.current.loadMoreDatasets()]);
    });

    expect(result.current.selected?.members.items.map((item) => item.account_id)).toEqual([
      "owner-a",
      "admin-a",
    ]);
    expect(result.current.selected?.datasets.items.map((item) => item.dataset_id)).toEqual([
      "dataset-a",
      "dataset-b",
    ]);
    expect(result.current.memberMoreLoading).toBe(false);
    expect(result.current.datasetMoreLoading).toBe(false);
  });

  it("keeps the loaded Workspace page when continuation fails", async () => {
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce({
        items: [workspace("workspace-a")],
        count: null,
        next_cursor: "w-1",
        evidence,
      })
      .mockRejectedValueOnce(new ApiError("network", "network"));

    const { result } = renderHook(() => useEnterpriseWorkspaceCenter(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(() => result.current.loadMoreWorkspaces());

    expect(result.current.page?.items.map((item) => item.id)).toEqual(["workspace-a"]);
    expect(result.current.page?.next_cursor).toBe("w-1");
    expect(result.current.workspaceMoreError?.title).toBe("无法连接 Workspace 服务");
  });
});

describe("workspace migration diagnostics", () => {
  it.each(["enterprise_workspace_migration_required", "workspace_migration_required"])(
    "maps %s to the authoritative 0026 migration guidance",
    (code) => {
      const error = projectWorkspaceError(
        new ApiError("migration", "http", 503, { detail: { code } }),
      );
      expect(error.description).toContain("0026_enterprise_workspace_control");
    },
  );
});
