// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as api from "../api/enterpriseAdminApi";
import type {
  DatasetAccessSummary,
  EnterpriseContext,
  EnterpriseMemberListResponse,
  EnterpriseScope,
} from "../model/enterpriseAdminModel";
import { useEnterpriseAdminWorkspace } from "./useEnterpriseAdminWorkspace";

vi.mock("../api/enterpriseAdminApi");

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const context: EnterpriseContext = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 10000,
    quota_chunks: 1000000,
    doc_count: 98,
    chunk_count: 12420,
  },
  actor: {
    id: "account-1",
    name: "林澈",
    email: "lin@example.com",
    role: "admin",
  },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read", "knowledge.audit"],
  role_permissions: {
    admin: ["knowledge.read", "knowledge.audit"],
    member: ["knowledge.read"],
  },
  capabilities: {},
};

const members: EnterpriseMemberListResponse = {
  items: [],
  count: 0,
  next_before_id: null,
};

const access: DatasetAccessSummary = {
  dataset_id: "dataset-1",
  owner_id: "account-1",
  visibility: "private",
  enforcement_mode: "tenant_role",
  actor_role: "admin",
  effective_permissions: ["knowledge.read", "knowledge.audit"],
  dataset_acl_supported: false,
  group_grants_supported: false,
  organization_inheritance_supported: false,
  warnings: ["可见性尚未参与细粒度授权"],
};

describe("useEnterpriseAdminWorkspace", () => {
  beforeEach(() => {
    vi.mocked(api.fetchEnterpriseContext).mockResolvedValue(context);
    vi.mocked(api.fetchEnterpriseMembers).mockResolvedValue(members);
    vi.mocked(api.fetchDatasetAccessSummary).mockResolvedValue(access);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not call enterprise APIs when the identity token is missing", () => {
    const { result } = renderHook(() => useEnterpriseAdminWorkspace({ ...scope, actorToken: "" }));

    expect(result.current.status).toBe("identity-missing");
    expect(api.fetchEnterpriseContext).not.toHaveBeenCalled();
    expect(api.fetchEnterpriseMembers).not.toHaveBeenCalled();
    expect(api.fetchDatasetAccessSummary).not.toHaveBeenCalled();
  });

  it("loads context, members and access summary in parallel and exposes real empty membership", async () => {
    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.context?.tenant.name).toBe("星海科技");
    expect(result.current.members?.count).toBe(0);
    expect(result.current.access?.enforcement_mode).toBe("tenant_role");
    expect(api.fetchEnterpriseContext).toHaveBeenCalledTimes(1);
    expect(api.fetchEnterpriseMembers).toHaveBeenCalledTimes(1);
    expect(api.fetchDatasetAccessSummary).toHaveBeenCalledTimes(1);
  });

  it("does not request an access summary when no knowledge base is selected", async () => {
    const { result } = renderHook(() =>
      useEnterpriseAdminWorkspace({ ...scope, datasetId: undefined }),
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.access).toBeNull();
    expect(result.current.accessError).toBeNull();
    expect(api.fetchEnterpriseContext).toHaveBeenCalledTimes(1);
    expect(api.fetchEnterpriseMembers).toHaveBeenCalledTimes(1);
    expect(api.fetchDatasetAccessSummary).not.toHaveBeenCalled();
  });

  it("loads the next member cursor and merges pages by membership id", async () => {
    const firstPage: EnterpriseMemberListResponse = {
      items: [
        {
          membership_id: 9,
          account_id: "account-1",
          name: "林澈",
          email: "lin@example.com",
          role: "admin",
          joined_at: "2026-08-20T08:00:00Z",
          status: "active",
          revision: 1,
        },
      ],
      count: 3,
      next_before_id: 8,
    };
    const secondPage: EnterpriseMemberListResponse = {
      items: [
        firstPage.items[0],
        {
          membership_id: 7,
          account_id: "account-2",
          name: "周宁",
          email: "zhou@example.com",
          role: "editor",
          joined_at: "2026-08-19T08:00:00Z",
          status: "active",
          revision: 1,
        },
      ],
      count: 3,
      next_before_id: null,
    };
    vi.mocked(api.fetchEnterpriseMembers)
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce(secondPage);

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.members?.items).toHaveLength(1));

    await act(async () => result.current.loadMoreMembers());

    await waitFor(() => expect(result.current.members?.items).toHaveLength(2));
    expect(result.current.members?.items.map((member) => member.membership_id)).toEqual([9, 7]);
    expect(result.current.members?.next_before_id).toBeNull();
    expect(api.fetchEnterpriseMembers).toHaveBeenNthCalledWith(
      2,
      scope,
      { beforeId: 8, limit: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("keeps the current member page and exposes a retryable continuation error", async () => {
    const firstPage: EnterpriseMemberListResponse = {
      items: [
        {
          membership_id: 9,
          account_id: "account-1",
          name: "林澈",
          email: "lin@example.com",
          role: "admin",
          joined_at: "2026-08-20T08:00:00Z",
          status: "active",
          revision: 1,
        },
      ],
      count: 2,
      next_before_id: 8,
    };
    vi.mocked(api.fetchEnterpriseMembers)
      .mockResolvedValueOnce(firstPage)
      .mockRejectedValueOnce(new ApiError("offline", "http", 503));

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.members?.items).toHaveLength(1));

    await act(async () => result.current.loadMoreMembers());

    expect(result.current.membersLoadingMore).toBe(false);
    expect(result.current.members?.items).toHaveLength(1);
    expect(result.current.membersLoadMoreError?.title).toBe("企业服务暂不可用");
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [503, "unavailable"],
  ] as const)("projects HTTP %s into the distinct %s page state", async (status, expected) => {
    vi.mocked(api.fetchEnterpriseContext).mockRejectedValueOnce(
      new ApiError("raw backend detail", "http", status),
    );

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));

    await waitFor(() => expect(result.current.status).toBe(expected));
    expect(result.current.context).toBeNull();
  });

  it("can retry after a transient service failure", async () => {
    vi.mocked(api.fetchEnterpriseContext)
      .mockRejectedValueOnce(new ApiError("offline", "http", 503))
      .mockResolvedValueOnce(context);

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.status).toBe("unavailable"));

    await act(async () => result.current.reload());

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(api.fetchEnterpriseContext).toHaveBeenCalledTimes(2);
  });
});
