// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as api from "../api/enterpriseAdminApi";
import type {
  EnterpriseContext,
  EnterpriseMember,
  EnterpriseMemberListResponse,
  EnterpriseScope,
} from "../model/enterpriseAdminModel";
import { useEnterpriseAdminWorkspace } from "./useEnterpriseAdminWorkspace";

vi.mock("../api/enterpriseAdminApi", () => ({
  fetchDatasetAccessSummary: vi.fn(),
  fetchEnterpriseAuditEvents: vi.fn(),
  fetchEnterpriseContext: vi.fn(),
  fetchEnterpriseMembers: vi.fn(),
  restoreEnterpriseMember: vi.fn(),
  suspendEnterpriseMember: vi.fn(),
  updateEnterpriseMemberRole: vi.fn(),
}));

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
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
  member_count: 2,
  dataset_count: 1,
  effective_permissions: ["knowledge.read", "knowledge.audit"],
  role_permissions: { admin: ["knowledge.read", "knowledge.audit"] },
  capabilities: {
    member_mutations: { state: "ready", label: "成员变更", reason: null },
    tenant_audit: { state: "ready", label: "企业管理日志", reason: null },
  },
};

const member: EnterpriseMember = {
  membership_id: 9,
  account_id: "account-1",
  name: "林澈",
  email: "lin@example.com",
  role: "admin",
  joined_at: "2026-08-20T08:00:00Z",
  status: "active",
  revision: 1,
};

const memberPage: EnterpriseMemberListResponse = {
  items: [member],
  count: 1,
  next_before_id: null,
};

function installReadMocks() {
  vi.mocked(api.fetchEnterpriseContext).mockResolvedValue(context);
  vi.mocked(api.fetchEnterpriseMembers).mockResolvedValue(memberPage);
  vi.mocked(api.fetchDatasetAccessSummary).mockResolvedValue({
    dataset_id: "dataset-1",
    owner_id: "account-owner",
    visibility: "private",
    enforcement_mode: "tenant_role",
    actor_role: "admin",
    effective_permissions: ["knowledge.read", "knowledge.manage"],
    dataset_acl_supported: false,
    group_grants_supported: false,
    organization_inheritance_supported: false,
    warnings: [],
  });
  vi.mocked(api.fetchEnterpriseAuditEvents).mockResolvedValue({
    items: [
      { sequence: 4, action: "member.updated" },
      { sequence: 3, action: "member.suspended" },
    ],
    next_before_sequence: 3,
  });
}

describe("useEnterpriseAdminWorkspace member mutations and tenant audit", () => {
  beforeEach(() => {
    installReadMocks();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("uses the capability gate, exposes mutation loading, and updates only the changed member", async () => {
    let resolveMutation: ((value: unknown) => void) | undefined;
    vi.mocked(api.updateEnterpriseMemberRole).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveMutation = resolve;
      }) as never,
    );

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    let mutationPromise: Promise<boolean> | undefined;
    act(() => {
      mutationPromise = result.current.updateMemberRole(member.account_id, "editor", "职责调整", 1);
    });
    await waitFor(() => expect(result.current.memberMutationLoading).toBe(true));
    expect(api.updateEnterpriseMemberRole).toHaveBeenCalledWith(
      scope,
      member.account_id,
      "editor",
      "职责调整",
      1,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await act(async () => {
      resolveMutation?.({
        member: { ...member, role: "editor", revision: 2 },
        audit_metadata: { event_id: "evt-1", sequence: 5 },
      });
      await mutationPromise;
    });

    expect(result.current.memberMutationLoading).toBe(false);
    expect(result.current.members?.items).toHaveLength(1);
    expect(result.current.members?.items[0]).toMatchObject({
      membership_id: 9,
      role: "editor",
      revision: 2,
    });
    expect(result.current.memberMutationError).toBeNull();
  });

  it("projects a 409 as an explicit revision conflict, preserves backend detail, and keeps members", async () => {
    vi.mocked(api.suspendEnterpriseMember).mockRejectedValueOnce(
      new ApiError("成员 revision=4，当前 revision=5", "http", 409, {
        detail: "成员 revision=4，当前 revision=5",
      }),
    );

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.members?.items).toHaveLength(1));

    await act(async () => {
      await result.current.suspendMember(member.account_id, "安全事件调查", 4);
    });

    expect(result.current.members?.items).toEqual([member]);
    expect(result.current.memberMutationError?.state).toBe("conflict");
    expect(result.current.memberMutationError?.description).toContain(
      "成员信息已发生变化，请刷新后重试",
    );
    expect(result.current.memberMutationError?.detail).toBe("成员 revision=4，当前 revision=5");
    expect(result.current.memberMutationLoading).toBe(false);
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [422, "error"],
    [503, "unavailable"],
  ] as const)(
    "keeps members and projects mutation HTTP %s as %s",
    async (status, expectedState) => {
      vi.mocked(api.restoreEnterpriseMember).mockRejectedValueOnce(
        new ApiError(`backend detail ${status}`, "http", status, {
          detail: `backend detail ${status}`,
        }),
      );

      const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
      await waitFor(() => expect(result.current.members?.items).toHaveLength(1));

      await act(async () => {
        await result.current.restoreMember(member.account_id, "恢复访问", 1);
      });

      expect(result.current.members?.items).toEqual([member]);
      expect(result.current.memberMutationError?.state).toBe(expectedState);
      expect(result.current.memberMutationError?.detail).toBe(`backend detail ${status}`);
    },
  );

  it("does not call member mutation APIs when the page capability gate is not ready", async () => {
    vi.mocked(api.fetchEnterpriseContext).mockResolvedValueOnce({
      ...context,
      capabilities: {
        member_mutations: { state: "limited", label: "成员变更", reason: "只读" },
        tenant_audit: { state: "unavailable", label: "企业管理日志", reason: "未接入" },
      },
    });

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    await act(async () => {
      await result.current.updateMemberRole(member.account_id, "editor", "职责调整", 1);
    });

    expect(api.updateEnterpriseMemberRole).not.toHaveBeenCalled();
    expect(result.current.members?.items).toEqual([member]);
  });

  it("loads audit events only through a ready tenant-audit capability and merges the next keyset page by sequence", async () => {
    vi.mocked(api.fetchEnterpriseAuditEvents).mockResolvedValueOnce({
      items: [
        { sequence: 4, action: "member.updated" },
        { sequence: 3, action: "member.suspended" },
      ],
      next_before_sequence: 3,
    });
    vi.mocked(api.fetchEnterpriseAuditEvents).mockResolvedValueOnce({
      items: [
        { sequence: 3, action: "member.suspended" },
        { sequence: 2, action: "member.restored" },
      ],
      next_before_sequence: null,
    });

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.audit?.items).toHaveLength(2));

    expect(api.fetchEnterpriseAuditEvents).toHaveBeenCalledWith(
      scope,
      {},
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await act(async () => result.current.loadMoreAudit());

    expect(result.current.audit?.items.map((item) => item.sequence)).toEqual([4, 3, 2]);
    expect(result.current.audit?.next_before_sequence).toBeNull();
    expect(result.current.auditLoadingMore).toBe(false);
    expect(result.current.auditError).toBeNull();
    expect(api.fetchEnterpriseAuditEvents).toHaveBeenNthCalledWith(
      2,
      scope,
      { beforeSequence: 3, limit: 50 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("preserves loaded audit events when the next keyset page fails", async () => {
    vi.mocked(api.fetchEnterpriseAuditEvents)
      .mockResolvedValueOnce({
        items: [{ sequence: 4, action: "member.updated" }],
        next_before_sequence: 3,
      })
      .mockRejectedValueOnce(new ApiError("audit unavailable", "http", 503));

    const { result } = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(result.current.audit?.items).toHaveLength(1));

    await act(async () => result.current.loadMoreAudit());

    expect(result.current.audit?.items.map((item) => item.sequence)).toEqual([4]);
    expect(result.current.auditError?.state).toBe("unavailable");
    expect(result.current.auditLoadingMore).toBe(false);
  });

  it("does not generate or request audit events when tenant-audit is unavailable or the initial endpoint returns 503", async () => {
    vi.mocked(api.fetchEnterpriseContext).mockResolvedValueOnce({
      ...context,
      capabilities: {
        ...context.capabilities,
        tenant_audit: { state: "unavailable", label: "企业管理日志", reason: "未接入" },
      },
    });

    const unavailable = renderHook(() => useEnterpriseAdminWorkspace(scope));
    await waitFor(() => expect(unavailable.result.current.status).toBe("ready"));
    expect(api.fetchEnterpriseAuditEvents).not.toHaveBeenCalled();
    expect(unavailable.result.current.audit).toBeNull();
    unavailable.unmount();

    vi.clearAllMocks();
    installReadMocks();
    vi.mocked(api.fetchEnterpriseAuditEvents).mockRejectedValueOnce(
      new ApiError("route not mounted", "http", 503),
    );
    const serviceDown = renderHook(() =>
      useEnterpriseAdminWorkspace(scope, { auditEnabled: true }),
    );
    await waitFor(() => expect(serviceDown.result.current.auditError?.state).toBe("unavailable"));
    expect(serviceDown.result.current.audit?.items ?? []).toEqual([]);
    expect(serviceDown.result.current.auditLoading).toBe(false);
  });
});
