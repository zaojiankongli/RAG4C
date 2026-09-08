// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type {
  DatasetAccessSummary,
  EnterpriseContext,
  EnterpriseScope,
} from "../../enterprise-admin/model";
import * as api from "../api/enterpriseAccessApi";
import type { DatasetAccessGrantPage } from "../enterpriseAccessModel";
import EnterpriseAccessGraph from "./EnterpriseAccessGraph";

vi.mock("../api/enterpriseAccessApi");

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
  actor: { id: "account-1", name: "林澈", email: "lin@example.com", role: "admin" },
  member_count: 1,
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

const accessSummary: DatasetAccessSummary = {
  dataset_id: "dataset-1",
  owner_id: "account-1",
  visibility: "private",
  enforcement_mode: "dataset_acl",
  actor_role: "admin",
  dataset_role: "manager",
  matched_grants: ["grant-1"],
  effective_permissions: ["knowledge.manage", "knowledge.read"],
  dataset_acl_supported: true,
  group_grants_supported: true,
  organization_inheritance_supported: true,
  warnings: [],
};

function emptyPage<T>(): { items: T[]; count: number; next_before_id: null } {
  return { items: [], count: 0, next_before_id: null };
}

function installGraphMocks(
  page: DatasetAccessGrantPage = { items: [grant], count: 1, next_before_id: null },
) {
  vi.mocked(api.fetchOrganizationUnits).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseGroups).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseInvitations).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchEnterpriseGroupMembers).mockResolvedValue(emptyPage());
  vi.mocked(api.fetchDatasetAccessSummary).mockResolvedValue(accessSummary);
  vi.mocked(api.fetchDatasetAccessGrants).mockResolvedValue(page);
  vi.mocked(api.createDatasetAccessGrant).mockResolvedValue({ ...grant, id: "grant-new" });
  vi.mocked(api.updateDatasetAccessGrantRole).mockResolvedValue({ ...grant, role: "manager" });
  vi.mocked(api.revokeDatasetAccessGrant).mockResolvedValue({ ...grant, status: "revoked" });
  vi.mocked(api.resumeDatasetAccessGrant).mockResolvedValue(grant);
}

describe("EnterpriseAccessGraph ACL mutations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installGraphMocks();
  });

  afterEach(() => cleanup());

  async function openAcl() {
    const user = userEvent.setup();
    render(
      <EnterpriseAccessGraph scope={scope} context={baseContext} accessSummary={accessSummary} />,
    );
    const graph = await screen.findByRole("region", { name: "企业访问图谱" });
    await user.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    expect(await within(graph).findByText("研发协作组")).toBeTruthy();
    return { user, graph };
  }

  it("opens the TDesign create dialog, submits the complete grant form, and refreshes the ACL facts", async () => {
    const { user, graph } = await openAcl();

    await user.click(within(graph).getByRole("button", { name: "新增授权" }));
    const dialog = await screen.findByRole("dialog", { name: "新增知识库授权" });
    await user.selectOptions(within(dialog).getByLabelText("主体类型"), "account");
    await user.type(within(dialog).getByLabelText("主体 ID"), "account-2");
    await user.selectOptions(within(dialog).getByLabelText("知识库角色"), "viewer");
    await user.type(within(dialog).getByLabelText("变更原因"), "为审计同事开放只读访问");
    await user.click(within(dialog).getByRole("button", { name: "创建授权" }));

    await waitFor(() => expect(api.createDatasetAccessGrant).toHaveBeenCalledTimes(1));
    expect(api.createDatasetAccessGrant).toHaveBeenCalledWith(
      scope,
      {
        subject_type: "account",
        subject_id: "account-2",
        role: "viewer",
        reason: "为审计同事开放只读访问",
      },
      expect.anything(),
    );
    expect(await within(graph).findByText("授权已创建")).toBeTruthy();
  });

  it("renders role, revoke, and resume operations in the ACL action column", async () => {
    vi.mocked(api.fetchDatasetAccessGrants).mockReset();
    vi.mocked(api.fetchDatasetAccessGrants)
      .mockResolvedValueOnce({ items: [grant], count: 1, next_before_id: null })
      .mockResolvedValueOnce({
        items: [{ ...grant, status: "revoked" }],
        count: 1,
        next_before_id: null,
      })
      .mockResolvedValueOnce({ items: [grant], count: 1, next_before_id: null });
    const { user, graph } = await openAcl();

    expect(within(graph).getByRole("button", { name: "变更研发协作组角色" })).toBeTruthy();
    expect(within(graph).getByRole("button", { name: "撤销研发协作组授权" })).toBeTruthy();
    await user.click(within(graph).getByRole("button", { name: "变更研发协作组角色" }));
    expect(await screen.findByRole("dialog", { name: "变更知识库授权角色" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));

    await user.click(within(graph).getByRole("button", { name: "撤销研发协作组授权" }));
    const revokeDialog = await screen.findByRole("dialog", { name: "撤销知识库授权" });
    await user.type(within(revokeDialog).getByLabelText("变更原因"), "权限范围收敛");
    await user.click(within(revokeDialog).getByRole("button", { name: "撤销授权" }));
    await waitFor(() => expect(api.revokeDatasetAccessGrant).toHaveBeenCalledTimes(1));

    vi.mocked(api.fetchDatasetAccessGrants).mockResolvedValue({
      items: [{ ...grant, status: "revoked" }],
      count: 1,
      next_before_id: null,
    });
    await user.click(within(graph).getByRole("button", { name: "恢复研发协作组授权" }));
    const resumeDialog = await screen.findByRole("dialog", { name: "恢复知识库授权" });
    await user.type(within(resumeDialog).getByLabelText("变更原因"), "重新开放项目访问");
    await user.click(within(resumeDialog).getByRole("button", { name: "恢复授权" }));
    await waitFor(() => expect(api.resumeDatasetAccessGrant).toHaveBeenCalledTimes(1));
  });

  it("disables every ACL mutation control unless the exact context permission and dataset gate are satisfied", async () => {
    const { graph } = await openAcl();
    const user = userEvent.setup();
    const buttons = within(graph).getAllByRole("button");
    const grantMutationButtons = buttons.filter(
      (button) => !button.getAttribute("aria-label")?.includes("停用 ACL"),
    );
    expect(grantMutationButtons.some((button) => (button as HTMLButtonElement).disabled)).toBe(
      false,
    );
    expect(
      (within(graph).getByRole("button", { name: "停用 ACL" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    cleanup();
    const restrictedContext = {
      ...baseContext,
      effective_permissions: ["knowledge.read"],
    };
    render(
      <EnterpriseAccessGraph
        scope={scope}
        context={restrictedContext}
        accessSummary={accessSummary}
      />,
    );
    const restrictedGraph = await screen.findByRole("region", { name: "企业访问图谱" });
    await user.click(within(restrictedGraph).getByRole("tab", { name: /知识库 ACL/ }));
    expect(await within(restrictedGraph).findByText("研发协作组")).toBeTruthy();
    const createButton = within(restrictedGraph).getByRole("button", { name: "新增授权" });
    expect((createButton as HTMLButtonElement).disabled).toBe(true);
    expect(createButton.getAttribute("title")).toContain("knowledge.manage");
    expect(
      (
        within(restrictedGraph).getByRole("button", {
          name: "变更研发协作组角色",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("keeps a 409 conflict actionable and surfaces migration-required 503 without guessing permission", async () => {
    const { user, graph } = await openAcl();
    vi.mocked(api.revokeDatasetAccessGrant).mockRejectedValueOnce(
      new ApiError("conflict", "http", 409, {
        detail: { code: "dataset_access_grant_revision_conflict" },
      }),
    );
    await user.click(within(graph).getByRole("button", { name: "撤销研发协作组授权" }));
    const dialog = await screen.findByRole("dialog", { name: "撤销知识库授权" });
    await user.type(within(dialog).getByLabelText("变更原因"), "撤销授权");
    await user.click(within(dialog).getByRole("button", { name: "撤销授权" }));
    expect(await within(dialog).findByRole("button", { name: "刷新授权列表" })).toBeTruthy();
    expect(within(graph).getByText("研发协作组")).toBeTruthy();

    vi.mocked(api.createDatasetAccessGrant).mockRejectedValueOnce(
      new ApiError("migration", "http", 503, {
        detail: { code: "enterprise_access_graph_migration_required" },
      }),
    );
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(within(graph).getByRole("button", { name: "新增授权" }));
    const createDialog = await screen.findByRole("dialog", { name: "新增知识库授权" });
    await user.type(within(createDialog).getByLabelText("主体 ID"), "account-9");
    await user.type(within(createDialog).getByLabelText("变更原因"), "迁移后授权");
    await user.click(within(createDialog).getByRole("button", { name: "创建授权" }));
    expect((await within(createDialog).findByRole("alert")).textContent).toContain(
      "数据库版本尚未就绪",
    );
  });
});
