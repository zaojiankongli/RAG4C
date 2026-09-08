// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchEnterpriseWorkspaces } from "../api/enterpriseWorkspaceApi";
import {
  ENTERPRISE_WORKSPACE_STORAGE_KEY,
  useEnterpriseWorkspaceSelector,
} from "./useEnterpriseWorkspace";

vi.mock("../api/enterpriseWorkspaceApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/enterpriseWorkspaceApi")>();
  return { ...actual, fetchEnterpriseWorkspaces: vi.fn() };
});

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };
const workspace = (id: string, name: string) => ({
  id,
  tenant_id: "tenant-a",
  code: id,
  name,
  description: "",
  status: "active" as const,
  environment: "production" as const,
  is_default: id === "workspace-default",
  revision: 1,
  member_count: null,
  dataset_count: null,
  primary_dataset_count: null,
  actor_role: null,
  created_at: null,
  updated_at: null,
  archived_at: null,
});

function page(
  ids: string[],
  defaultId: string | null = ids[0] ?? null,
  nextCursor: string | null = null,
) {
  return {
    items: ids.map((id) => workspace(id, id === "workspace-b" ? "业务域 B" : "默认域")),
    count: null,
    next_cursor: nextCursor,
    evidence: {
      workspace_count: null,
      active_count: null,
      default_workspace_id: defaultId,
      primary_dataset_binding_count: null,
      authorization_state: "workspace_authorization_not_enforced" as const,
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(fetchEnterpriseWorkspaces).mockReset();
});

afterEach(() => vi.restoreAllMocks());

describe("useEnterpriseWorkspaceSelector persistence", () => {
  it("restores only a saved ID that still exists in the active server list", async () => {
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-b");
    vi.mocked(fetchEnterpriseWorkspaces).mockResolvedValue(
      page(["workspace-default", "workspace-b"]),
    );

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.value).toBe("workspace-b");
    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-b");
  });

  it("removes an invalid saved ID and falls back server default then first on refresh", async () => {
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-missing");
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce(page(["workspace-default", "workspace-b"], "workspace-default"))
      .mockResolvedValueOnce(page(["workspace-b"], null));

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.value).toBe("workspace-default");
    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBeNull();

    await act(() => result.current.reload());
    expect(result.current.value).toBe("workspace-b");
  });

  it("persists selection as the independent ID key without caching labels or permissions", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    vi.mocked(fetchEnterpriseWorkspaces).mockResolvedValue(
      page(["workspace-default", "workspace-b"]),
    );
    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.setValue("workspace-b"));

    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-b");
    expect(setItem).toHaveBeenCalledWith(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-b");
    expect(setItem).not.toHaveBeenCalledWith(
      expect.stringMatching(/name|role|permission/i),
      expect.anything(),
    );
  });

  it("survives localStorage read, write, and remove exceptions without fabricating options", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage denied");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage denied");
    });
    vi.mocked(fetchEnterpriseWorkspaces).mockResolvedValue(page(["workspace-default"]));

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.value).toBe("workspace-default");
    expect(result.current.workspaces.map((item) => item.id)).toEqual(["workspace-default"]);

    expect(() => act(() => result.current.setValue("workspace-default"))).not.toThrow();
  });

  it("walks every active keyset page before restoring a saved Workspace ID", async () => {
    const firstPageIds = Array.from({ length: 200 }, (_, index) => `workspace-${index}`);
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-saved");
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce(page(firstPageIds, "workspace-0", "cursor-2"))
      .mockResolvedValueOnce(page(["workspace-saved"], "workspace-0", null));

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(fetchEnterpriseWorkspaces).toHaveBeenNthCalledWith(
      1,
      scope,
      { status: "active", limit: 200 },
      expect.any(Object),
    );
    expect(fetchEnterpriseWorkspaces).toHaveBeenNthCalledWith(
      2,
      scope,
      { status: "active", limit: 200, cursor: "cursor-2" },
      expect.any(Object),
    );
    expect(result.current.workspaces).toHaveLength(201);
    expect(result.current.value).toBe("workspace-saved");
    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-saved");
  });

  it("keeps the saved ID when a later active Workspace page fails", async () => {
    const removeItem = vi.spyOn(Storage.prototype, "removeItem");
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-saved");
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce(page(["workspace-first"], "workspace-first", "cursor-2"))
      .mockRejectedValueOnce(new Error("second page failed"));

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(result.current.workspaces.map((item) => item.id)).toEqual(["workspace-first"]);
    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-saved");
    expect(removeItem).not.toHaveBeenCalledWith(ENTERPRISE_WORKSPACE_STORAGE_KEY);
  });

  it("fails closed on a repeated cursor without deleting the saved ID", async () => {
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-saved");
    vi.mocked(fetchEnterpriseWorkspaces)
      .mockResolvedValueOnce(page(["workspace-first"], "workspace-first", "cursor-loop"))
      .mockResolvedValueOnce(page(["workspace-second"], "workspace-first", "cursor-loop"));

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(fetchEnterpriseWorkspaces).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-saved");
  });

  it("fails closed when active Workspace responses exceed the 20000 item safety limit", async () => {
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, "workspace-saved");
    vi.mocked(fetchEnterpriseWorkspaces).mockResolvedValue(
      page(
        Array.from({ length: 20001 }, (_, index) => `workspace-${index}`),
        "workspace-0",
        null,
      ),
    );

    const { result } = renderHook(() => useEnterpriseWorkspaceSelector(scope));
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)).toBe("workspace-saved");
  });
});
