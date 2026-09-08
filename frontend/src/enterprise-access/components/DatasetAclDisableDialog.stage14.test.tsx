// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import type {
  ApprovalPolicy,
  ApprovalRequest,
} from "../../enterprise-approval/enterpriseApprovalModel";
import * as approvalApi from "../../enterprise-approval/api/enterpriseApprovalApi";
import DatasetAclDisableDialog from "./DatasetAclDisableDialog";
import type { PersistentDatasetAccessSummary } from "../enterpriseAccessModel";

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

const summary = {
  acl_mode: "dataset_acl",
  acl_revision: 7,
} as PersistentDatasetAccessSummary;

function policyPage(items: ApprovalPolicy[]) {
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

function renderDialog(onSubmit = vi.fn().mockResolvedValue(summary)) {
  return render(
    <DatasetAclDisableDialog
      visible
      scope={scope}
      aclRevision={7}
      currentAclMode="dataset_acl"
      saving={false}
      error={null}
      onClose={vi.fn()}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onSubmit={onSubmit}
    />,
  );
}

describe("Stage 14 DatasetAclDisableDialog approval UX", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(policyPage([]));
  });

  afterEach(() => cleanup());

  it("keeps direct confirmation when no matching policy is returned", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(summary);
    renderDialog(onSubmit);

    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await waitFor(() => expect(within(dialog).getByText("确认停用 ACL")).toBeTruthy());
    await user.type(within(dialog).getByLabelText("停用原因"), "无审批规则时直接停用");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await user.click(within(dialog).getByText("确认停用 ACL"));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        expected_acl_revision: 7,
        reason: "无审批规则时直接停用",
      }),
    );
    expect(approvalApi.createApprovalRequest).not.toHaveBeenCalled();
  });

  it("shows authoritative policy facts and submits an approval request without changing ACL mode", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(summary);
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(policyPage([policy]));
    vi.mocked(approvalApi.createApprovalRequest).mockResolvedValue(request);
    renderDialog(onSubmit);

    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await waitFor(() => expect(within(dialog).getByText("知识库 ACL 高风险变更")).toBeTruthy());
    expect(within(dialog).getByText("2 人审批")).toBeTruthy();
    expect(within(dialog).getByText("提交后 1440 分钟内有效")).toBeTruthy();
    expect(within(dialog).getByText("ACL 模式保持 dataset_acl")).toBeTruthy();

    await user.type(within(dialog).getByLabelText("停用原因"), "切换租户治理模式");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await user.click(within(dialog).getByText("提交审批申请"));

    await waitFor(() =>
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
      ),
    );
    expect(onSubmit).not.toHaveBeenCalled();
    expect(await within(dialog).findByText("request-1")).toBeTruthy();
    expect(within(dialog).getByText("待审批")).toBeTruthy();
    expect(within(dialog).getByText("前往审批中心")).toBeTruthy();
  });

  it("converts a backend dataset_acl_approval_required 409 into approval mode without retrying direct disable", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(summary);
    render(
      <DatasetAclDisableDialog
        visible
        scope={scope}
        aclRevision={7}
        currentAclMode="dataset_acl"
        saving={false}
        error={
          {
            code: "dataset_acl_approval_required",
            message: "该停用操作需要审批，已切换到审批申请模式。",
            needsRefresh: false,
            migrationRequired: false,
            retryAvailable: false,
            approvalRequired: {
              policy_id: "policy-1",
              policy_name: "服务端 ACL 高风险规则",
              policy_revision: 4,
              required_approvals: 2,
              request_expiry_minutes: 60,
            },
            status: 409,
          } as never
        }
        onClose={vi.fn()}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
        onSubmit={onSubmit}
      />,
    );

    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await waitFor(() => expect(within(dialog).getByText("服务端 ACL 高风险规则")).toBeTruthy());
    await user.type(within(dialog).getByLabelText("停用原因"), "服务端要求审批");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await user.click(within(dialog).getByText("提交审批申请"));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("navigates to the approval center without persisting request or ticket data", async () => {
    const user = userEvent.setup();
    vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue(policyPage([policy]));
    vi.mocked(approvalApi.createApprovalRequest).mockResolvedValue(request);
    const pushState = vi.spyOn(window.history, "pushState");
    renderDialog();

    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await user.type(within(dialog).getByLabelText("停用原因"), "提交审批并前往中心");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await user.click(within(dialog).getByText("提交审批申请"));
    await user.click(await within(dialog).findByText("前往审批中心"));

    expect(pushState).toHaveBeenCalledWith({}, "", "/enterprise/approvals");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    pushState.mockRestore();
  });
});
