// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseAccessApi";
import * as approvalApi from "../../enterprise-approval/api/enterpriseApprovalApi";
import type { DatasetAccessGrantPage } from "../enterpriseAccessModel";
import EnterpriseAccessGraph from "./EnterpriseAccessGraph";

vi.mock("../api/enterpriseAccessApi", async () => {
  const actual = await vi.importActual<typeof import("../api/enterpriseAccessApi")>(
    "../api/enterpriseAccessApi",
  );
  return {
    ...actual,
    fetchOrganizationUnits: vi.fn(),
    fetchEnterpriseGroups: vi.fn(),
    fetchEnterpriseGroupMembers: vi.fn(),
    fetchEnterpriseInvitations: vi.fn(),
    fetchDatasetAccessSummary: vi.fn(),
    fetchDatasetAccessGrants: vi.fn(),
    createDatasetAccessGrant: vi.fn(),
    updateDatasetAccessGrantRole: vi.fn(),
    revokeDatasetAccessGrant: vi.fn(),
    resumeDatasetAccessGrant: vi.fn(),
    disableDatasetAcl: vi.fn(),
  };
});

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

const stage7Api = api as typeof api & {
  disableDatasetAcl: ReturnType<typeof vi.fn>;
};

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const grant: DatasetAccessGrantPage["items"][number] = {
  id: "grant-1",
  dataset_id: "dataset-1",
  subject_type: "group",
  subject_id: "group-1",
  subject_name: "研发协作组",
  role: "editor",
  status: "active",
  revision: 4,
};

const baseContext: EnterpriseContext = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 100,
    quota_chunks: 1000,
    doc_count: 10,
    chunk_count: 20,
  },
  actor: { id: "account-admin", name: "管理员", email: "admin@example.com", role: "admin" },
  member_count: 2,
  dataset_count: 1,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: { admin: ["knowledge.read", "knowledge.manage"] },
  capabilities: {
    organization_units: { state: "ready", label: "组织架构", reason: null },
    user_groups: { state: "ready", label: "用户组", reason: null },
    dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
    invitations: { state: "ready", label: "成员邀请", reason: null },
  },
};

const persistentSummary = {
  dataset_id: "dataset-1",
  owner_id: "account-owner",
  visibility: "private",
  // Deliberately stale/legacy-shaped enforcement text: acl_mode is the durable source of truth.
  enforcement_mode: "tenant_role_fallback",
  actor_role: "admin",
  dataset_role: "manager",
  matched_grants: [],
  effective_permissions: ["knowledge.manage", "knowledge.read"],
  dataset_acl_supported: true,
  group_grants_supported: true,
  organization_inheritance_supported: true,
  warnings: [],
  acl_mode: "dataset_acl",
  acl_revision: 7,
  acl_enabled_at: "2026-08-26T08:00:00Z",
  acl_enabled_by: "account-owner",
};

function emptyPage<T>(): { items: T[]; count: number; next_before_id: null } {
  return { items: [], count: 0, next_before_id: null };
}

function installMocks() {
  vi.mocked(approvalApi.fetchApprovalPolicies).mockResolvedValue({
    items: [],
    count: 0,
    next_cursor: null,
    evidence: {
      pending_count: null,
      my_pending_count: null,
      active_policy_count: null,
      catalog_revision: null,
      execution_adapter_status: "execution_adapter_not_connected",
    },
  });
  vi.mocked(api.fetchOrganizationUnits).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseGroups).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseInvitations).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseGroupMembers).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchDatasetAccessSummary).mockResolvedValue(persistentSummary as never);
  vi.mocked(api.fetchDatasetAccessGrants).mockResolvedValue({
    items: [grant],
    count: 1,
    next_before_id: null,
  });
  vi.mocked(api.createDatasetAccessGrant).mockResolvedValue(grant);
  vi.mocked(api.updateDatasetAccessGrantRole).mockResolvedValue(grant);
  vi.mocked(api.revokeDatasetAccessGrant).mockResolvedValue({ ...grant, status: "revoked" });
  vi.mocked(api.resumeDatasetAccessGrant).mockResolvedValue(grant);
  stage7Api.disableDatasetAcl.mockResolvedValue(persistentSummary);
}

async function openAcl(summary = persistentSummary, context = baseContext) {
  const user = userEvent.setup();
  render(
    <EnterpriseAccessGraph scope={scope} context={context} accessSummary={summary as never} />,
  );
  const graph = await screen.findByRole("region", { name: "企业访问图谱" });
  await user.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
  expect(await within(graph).findByText("研发协作组")).toBeTruthy();
  return { user, graph };
}

describe("stage 7 persistent ACL evidence and disable control", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installMocks();
  });

  afterEach(() => cleanup());

  it("renders durable dataset_acl evidence and never shows tenant-role fallback after the last grant is gone", async () => {
    const { graph } = await openAcl();
    const evidence = within(graph).getByRole("group", { name: "知识库 ACL 生效证据" });

    expect(within(evidence).getByText("持久 ACL 模式")).toBeTruthy();
    expect(within(evidence).getByText("acl_revision")).toBeTruthy();
    expect(within(evidence).getByText("account-owner")).toBeTruthy();
    expect(within(evidence).getByText(/最后一条授权撤销后仍保持 ACL/)).toBeTruthy();
    expect(within(evidence).queryByText("租户角色回退")).toBeNull();
  });

  it("opens a TDesign danger dialog, requires revision/reason/second confirmation, and refreshes after disable", async () => {
    const { user, graph } = await openAcl();
    const disableButton = within(graph).getByRole("button", { name: "停用 ACL" });
    expect((disableButton as HTMLButtonElement).disabled).toBe(false);

    await user.click(disableButton);
    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    const revisionControl = within(dialog).getByLabelText("ACL revision");
    expect(revisionControl.closest(".t-input-number")).toBeTruthy();
    expect(revisionControl.querySelector("input")?.value).toBe("7");
    const confirmContainer = () =>
      within(dialog).getByText("确认停用 ACL").closest("button, [role=button], div") as HTMLElement;
    expect(confirmContainer().className).toContain("t-is-disabled");

    await user.type(within(dialog).getByLabelText("停用原因"), "完成治理切换");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await waitFor(() => expect(confirmContainer().className).not.toContain("t-is-disabled"));

    await user.click(within(dialog).getByText("确认停用 ACL"));
    await waitFor(() => expect(stage7Api.disableDatasetAcl).toHaveBeenCalledTimes(1));
    expect(stage7Api.disableDatasetAcl).toHaveBeenCalledWith(
      scope,
      { expected_acl_revision: 7, reason: "完成治理切换" },
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
    await waitFor(() => expect(api.fetchDatasetAccessSummary).toHaveBeenCalledTimes(1));
    expect(await within(graph).findByText("ACL 已停用")).toBeTruthy();
  });

  it("uses TDesign InputNumber and Checkbox for the governed disable form", async () => {
    const { user, graph } = await openAcl();
    await user.click(within(graph).getByRole("button", { name: "停用 ACL" }));
    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });

    const revisionInput = within(dialog).getByLabelText("ACL revision");
    expect(revisionInput.closest(".t-input-number")).toBeTruthy();
    const confirmation = within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式");
    expect(confirmation.closest(".t-checkbox")).toBeTruthy();
  });

  it("closes the disable dialog when the same idempotent request succeeds on retry", async () => {
    stage7Api.disableDatasetAcl
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockResolvedValueOnce(persistentSummary);
    const { user, graph } = await openAcl();

    await user.click(within(graph).getByRole("button", { name: "停用 ACL" }));
    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await user.type(within(dialog).getByLabelText("停用原因"), "网络恢复后重试");
    await user.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await user.click(within(dialog).getByText("确认停用 ACL"));

    const retryButton = await within(dialog).findByRole("button", { name: "使用同一请求重试" });
    await user.click(retryButton);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "停用知识库 ACL" })).toBeNull(),
    );
  });

  it("keeps the disable action unavailable for a dataset manager who is not a tenant owner or admin", async () => {
    const managerSummary = { ...persistentSummary, actor_role: "manager" };
    const { graph } = await openAcl(managerSummary as never);
    const disableButton = within(graph).getByRole("button", { name: "停用 ACL" });

    expect((disableButton as HTMLButtonElement).disabled).toBe(true);
    expect(disableButton.getAttribute("title")).toContain("tenant owner");
    expect(stage7Api.disableDatasetAcl).not.toHaveBeenCalled();
  });
});
