// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseCapabilities, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseAccessApi";
import type {
  EnterpriseGroupPage,
  EnterpriseInvitationPage,
  OrganizationUnitPage,
  DatasetAccessGrantPage,
} from "../enterpriseAccessModel";
import { useEnterpriseAccessGraph } from "./useEnterpriseAccessGraph";

vi.mock("../api/enterpriseAccessApi");

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const readyCapabilities: EnterpriseCapabilities = {
  organization_units: { state: "ready", label: "组织架构", reason: null },
  user_groups: { state: "ready", label: "用户组", reason: null },
  dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
  invitations: { state: "ready", label: "成员邀请", reason: null },
};

const emptyOrganizationPage: OrganizationUnitPage = {
  items: [],
  count: 0,
  next_before_id: null,
};
const emptyGroupPage: EnterpriseGroupPage = { items: [], count: 0, next_before_id: null };
const emptyInvitationPage: EnterpriseInvitationPage = {
  items: [],
  count: 0,
  next_before_id: null,
};
const emptyGrantPage: DatasetAccessGrantPage = { items: [], count: 0, next_before_id: null };

describe("useEnterpriseAccessGraph", () => {
  beforeEach(() => {
    vi.mocked(api.fetchOrganizationUnits).mockResolvedValue(emptyOrganizationPage);
    vi.mocked(api.fetchEnterpriseGroups).mockResolvedValue(emptyGroupPage);
    vi.mocked(api.fetchEnterpriseInvitations).mockResolvedValue(emptyInvitationPage);
    vi.mocked(api.fetchDatasetAccessGrants).mockResolvedValue(emptyGrantPage);
    vi.mocked(api.fetchEnterpriseGroupMembers).mockResolvedValue({
      items: [],
      count: 0,
      next_before_id: null,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not issue requests for capabilities the server marks unavailable", async () => {
    const capabilities: EnterpriseCapabilities = {
      organization_units: {
        state: "unavailable",
        label: "组织架构",
        reason: "尚未接入企业组织目录",
      },
      user_groups: { state: "unavailable", label: "用户组", reason: "尚未接入企业用户组" },
      dataset_acl: { state: "unavailable", label: "知识库 ACL", reason: "迁移尚未完成" },
      invitations: { state: "unavailable", label: "成员邀请", reason: "邀请流程尚未接入" },
    };

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, capabilities));

    await waitFor(() => expect(result.current.organizationUnits.status).toBe("unavailable"));
    expect(result.current.organizationUnits.capabilityReason).toBe("尚未接入企业组织目录");
    expect(api.fetchOrganizationUnits).not.toHaveBeenCalled();
    expect(api.fetchEnterpriseGroups).not.toHaveBeenCalled();
    expect(api.fetchDatasetAccessGrants).not.toHaveBeenCalled();
    expect(api.fetchEnterpriseInvitations).not.toHaveBeenCalled();
  });

  it("loads authoritative read APIs when capabilities are limited to read-only", async () => {
    const limitedCapabilities: EnterpriseCapabilities = {
      organization_units: { state: "limited", label: "组织架构", reason: "权威只读" },
      user_groups: { state: "limited", label: "用户组", reason: "权威只读" },
      dataset_acl: { state: "limited", label: "知识库 ACL", reason: "权威只读" },
      invitations: { state: "limited", label: "成员邀请", reason: "权威只读" },
    };

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, limitedCapabilities));

    await waitFor(() => expect(result.current.organizationUnits.status).toBe("ready"));
    expect(result.current.organizationUnits.capabilityState).toBe("limited");
    expect(result.current.organizationUnits.capabilityReason).toBe("权威只读");
    expect(api.fetchOrganizationUnits).toHaveBeenCalledTimes(1);
    expect(api.fetchEnterpriseGroups).toHaveBeenCalledTimes(1);
    expect(api.fetchDatasetAccessGrants).toHaveBeenCalledTimes(1);
    expect(api.fetchEnterpriseInvitations).toHaveBeenCalledTimes(1);
  });

  it("loads independent ready resources in parallel and merges organization keyset pages", async () => {
    const first: OrganizationUnitPage = {
      items: [
        {
          id: "ou-9",
          parent_id: null,
          name: "集团总部",
          code: "HQ",
          status: "active",
          member_count: 12,
          child_count: 1,
        },
      ],
      count: 2,
      next_before_id: "ou-8",
    };
    const second: OrganizationUnitPage = {
      items: [
        first.items[0],
        {
          id: "ou-7",
          parent_id: "ou-9",
          name: "平台研发部",
          code: "RD",
          status: "active",
          member_count: 8,
          child_count: 0,
        },
      ],
      count: 2,
      next_before_id: null,
    };
    vi.mocked(api.fetchOrganizationUnits)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, readyCapabilities));

    await waitFor(() => expect(result.current.organizationUnits.items).toHaveLength(1));
    expect(api.fetchEnterpriseGroups).toHaveBeenCalledTimes(1);
    expect(api.fetchEnterpriseInvitations).toHaveBeenCalledTimes(1);
    expect(api.fetchDatasetAccessGrants).toHaveBeenCalledTimes(1);

    await act(async () => result.current.organizationUnits.loadMore());

    expect(result.current.organizationUnits.items.map((item) => item.id)).toEqual(["ou-9", "ou-7"]);
    expect(api.fetchOrganizationUnits).toHaveBeenNthCalledWith(
      2,
      scope,
      { beforeId: "ou-8", limit: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("loads group members only after a real group is selected", async () => {
    vi.mocked(api.fetchEnterpriseGroups).mockResolvedValueOnce({
      items: [
        {
          id: "group-rd",
          name: "研发协作组",
          description: "跨团队研发协作",
          status: "active",
          member_count: 2,
        },
      ],
      count: 1,
      next_before_id: null,
    });
    vi.mocked(api.fetchEnterpriseGroupMembers).mockResolvedValueOnce({
      items: [
        {
          id: "gm-1",
          account_id: "account-2",
          name: "周宁",
          email: "zhou@example.com",
          role: "editor",
          status: "active",
        },
      ],
      count: 1,
      next_before_id: null,
    });

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, readyCapabilities));
    await waitFor(() => expect(result.current.groups.items).toHaveLength(1));
    expect(api.fetchEnterpriseGroupMembers).not.toHaveBeenCalled();

    act(() => result.current.selectGroup("group-rd"));

    await waitFor(() => expect(result.current.groupMembers.items).toHaveLength(1));
    expect(api.fetchEnterpriseGroupMembers).toHaveBeenCalledWith(
      scope,
      "group-rd",
      { limit: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("projects migration-required separately from a generic 503", async () => {
    vi.mocked(api.fetchDatasetAccessGrants).mockRejectedValueOnce(
      new ApiError("migration required", "http", 503, {
        detail: { code: "enterprise_access_graph_migration_required" },
      }),
    );

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, readyCapabilities));

    await waitFor(() => expect(result.current.accessGrants.status).toBe("migration-required"));
    expect(result.current.accessGrants.error?.title).toBe("访问图谱数据库版本尚未就绪");
    expect(result.current.accessGrants.items).toEqual([]);
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [503, "unavailable"],
  ] as const)("projects HTTP %s into the distinct %s resource state", async (status, expected) => {
    vi.mocked(api.fetchEnterpriseInvitations).mockRejectedValueOnce(
      new ApiError("unavailable", "http", status),
    );

    const { result } = renderHook(() => useEnterpriseAccessGraph(scope, readyCapabilities));

    await waitFor(() => expect(result.current.invitations.status).toBe(expected));
    expect(result.current.invitations.items).toEqual([]);
  });
});
