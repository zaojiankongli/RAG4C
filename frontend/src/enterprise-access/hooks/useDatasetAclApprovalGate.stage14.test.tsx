// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import type {
  ApprovalPolicy,
  ApprovalRequest,
} from "../../enterprise-approval/enterpriseApprovalModel";
import * as approvalApi from "../../enterprise-approval/api/enterpriseApprovalApi";
import { useDatasetAclApprovalGate } from "./useDatasetAclApprovalGate";

vi.mock("../../enterprise-approval/api/enterpriseApprovalApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../enterprise-approval/api/enterpriseApprovalApi")
  >("../../enterprise-approval/api/enterpriseApprovalApi");
  return {
    ...actual,
    fetchApprovalPolicies: vi.fn(),
    createApprovalRequest: vi.fn(),
  };
});

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const policy: ApprovalPolicy = {
  id: "policy-1",
  name: "知识库 ACL 高风险变更",
  action_type: "dataset_acl_disable",
  resource_scope: "knowledge_base:dataset-1",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
  revision: 3,
};

const request: ApprovalRequest = {
  id: "request-1",
  policy_id: "policy-1",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-1",
  requester: { id: "account-1", name: "管理员" },
  reason: "切换租户治理模式",
  status: "pending",
  required_approvals: 2,
  received_approvals: 0,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 1,
  execution_adapter_status: "execution_adapter_not_connected",
  snapshot: {
    dataset_id: "dataset-1",
    expected_acl_revision: 7,
    current_acl_mode: "dataset_acl",
  },
};

function page(items: ApprovalPolicy[]) {
  return {
    items,
    count: items.length,
    next_cursor: null,
    evidence: {
      pending_count: null,
      my_pending_count: null,
      active_policy_count: null,
      catalog_revision: null,
      execution_adapter_status: "execution_adapter_not_connected" as const,
    },
  };
}

describe("Stage 14 useDatasetAclApprovalGate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(page([]));
  });

  afterEach(() => vi.restoreAllMocks());

  it("loads only active dataset ACL policies and enters approval mode for a matching policy", async () => {
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(
      page([{ ...policy, id: "wildcard", resource_scope: "knowledge_base:*" }, policy]),
    );

    const { result } = renderHook(() =>
      useDatasetAclApprovalGate({
        visible: true,
        scope,
        aclRevision: 7,
        currentAclMode: "dataset_acl",
      }),
    );

    await waitFor(() => expect(result.current.mode).toBe("approval"));
    expect(approvalApi.fetchApprovalPolicies).toHaveBeenCalledWith(
      scope,
      { status: "active", actionType: "dataset_acl_disable" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.current.policy).toMatchObject({
      id: "policy-1",
      name: "知识库 ACL 高风险变更",
      required_approvals: 2,
      request_expiry_minutes: 1440,
    });
  });

  it("keeps the existing direct mode when no active matching policy exists", async () => {
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(
      page([
        { ...policy, status: "disabled" },
        { ...policy, action_type: "member_role_change" },
        { ...policy, resource_scope: "knowledge_base:another-dataset" },
      ]),
    );

    const { result } = renderHook(() =>
      useDatasetAclApprovalGate({
        visible: true,
        scope,
        aclRevision: 7,
        currentAclMode: "dataset_acl",
      }),
    );

    await waitFor(() => expect(result.current.mode).toBe("direct"));
    expect(result.current.policy).toBeNull();
  });

  it("submits the exact ACL snapshot and keeps the result transient in memory", async () => {
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(page([policy]));
    vi.mocked(approvalApi.createApprovalRequest).mockResolvedValue(request);

    const { result } = renderHook(() =>
      useDatasetAclApprovalGate({
        visible: true,
        scope,
        aclRevision: 7,
        currentAclMode: "dataset_acl",
      }),
    );
    await waitFor(() => expect(result.current.mode).toBe("approval"));

    await act(async () => {
      await result.current.submitApproval({
        expected_acl_revision: 7,
        reason: "切换租户治理模式",
      });
    });

    expect(approvalApi.createApprovalRequest).toHaveBeenCalledWith(
      scope,
      {
        policy_id: "policy-1",
        resource_type: "knowledge_base",
        resource_id: "dataset-1",
        snapshot: {
          dataset_id: "dataset-1",
          expected_acl_revision: 7,
          current_acl_mode: "dataset_acl",
        },
        reason: "切换租户治理模式",
      },
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
    expect(result.current.request).toEqual(request);
    expect(result.current.mode).toBe("submitted");
  });

  it("fails closed when policy facts cannot be loaded", async () => {
    vi.mocked(approvalApi.fetchApprovalPolicies).mockRejectedValue(
      new Error("upstream unavailable"),
    );

    const { result } = renderHook(() =>
      useDatasetAclApprovalGate({
        visible: true,
        scope,
        aclRevision: 7,
        currentAclMode: "dataset_acl",
      }),
    );

    await waitFor(() => expect(result.current.mode).toBe("error"));
    expect(result.current.error).toContain("审批规则事实");
    expect(result.current.submitApproval).toBeTypeOf("function");
  });

  it("adopts a sanitized server approval-required hint without attempting direct ACL mutation", async () => {
    const { result } = renderHook(() =>
      useDatasetAclApprovalGate({
        visible: true,
        scope,
        aclRevision: 7,
        currentAclMode: "dataset_acl",
        serverApprovalRequired: {
          policy_id: "policy-server",
          policy_name: "服务端 ACL 规则",
          policy_revision: 8,
          required_approvals: 2,
          request_expiry_minutes: 60,
        },
      }),
    );

    await waitFor(() => expect(result.current.mode).toBe("approval"));
    expect(result.current.policy).toMatchObject({
      id: "policy-server",
      name: "服务端 ACL 规则",
      required_approvals: 2,
    });
  });
});
