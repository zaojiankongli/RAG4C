import { describe, expect, it } from "vitest";
import {
  projectWorkspaceDatasetPage,
  projectWorkspaceDetail,
  projectWorkspacePage,
  type EnterpriseWorkspace,
} from "./enterpriseWorkspaceModel";

const rawWorkspace = {
  id: "workspace-prod",
  tenant_id: "tenant-a",
  code: "prod",
  name: "生产知识域",
  description: "面向正式业务的企业知识工作区",
  status: "active",
  environment: "production",
  is_default: true,
  revision: 7,
  member_count: 12,
  dataset_count: 4,
  primary_dataset_count: 3,
  updated_at: "2026-08-27T08:00:00Z",
};

describe("enterprise workspace model", () => {
  it("projects bounded workspace pages without fabricating invalid rows", () => {
    const page = projectWorkspacePage({
      items: [rawWorkspace, { name: "missing id" }, null],
      count: 1,
      next_cursor: "workspace-prod",
      evidence: {
        active_count: 1,
        default_workspace_id: "workspace-prod",
        primary_dataset_binding_count: 3,
        authorization_state: "workspace_authorization_not_enforced",
      },
    });

    expect(page.items).toHaveLength(1);
    expect(page.items[0]).toMatchObject<Partial<EnterpriseWorkspace>>({
      id: "workspace-prod",
      name: "生产知识域",
      is_default: true,
      revision: 7,
    });
    expect(page.next_cursor).toBe("workspace-prod");
    expect(page.evidence).toEqual({
      workspace_count: 1,
      active_count: 1,
      default_workspace_id: "workspace-prod",
      primary_dataset_binding_count: 3,
      authorization_state: "workspace_authorization_not_enforced",
    });
  });

  it("keeps authoritative totals unknown when list responses omit count", () => {
    const workspacePage = projectWorkspacePage({
      items: [rawWorkspace],
      next_cursor: "workspace-next",
      evidence: {
        authorization_state: "workspace_authorization_not_enforced",
      },
    });
    const detail = projectWorkspaceDetail({
      workspace: rawWorkspace,
      members: {
        items: [
          {
            account_id: "owner-a",
            role: "owner",
            status: "active",
            revision: 1,
          },
        ],
        next_cursor: "member-next",
      },
      datasets: {
        items: [
          {
            dataset_id: "dataset-a",
            binding_kind: "primary",
            status: "active",
            revision: 1,
          },
        ],
        next_cursor: "dataset-next",
      },
    });

    expect(workspacePage.count).toBeNull();
    expect(workspacePage.evidence.workspace_count).toBeNull();
    expect(detail.members.count).toBeNull();
    expect(detail.datasets.count).toBeNull();
  });

  it("rejects missing or malformed Dataset pagination authority", () => {
    expect(() => projectWorkspaceDatasetPage({ items: [] })).toThrow(/next_cursor/);
    expect(() => projectWorkspaceDatasetPage({ items: [], next_cursor: { opaque: true } })).toThrow(
      /next_cursor/,
    );
    expect(projectWorkspaceDatasetPage({ items: [], next_cursor: null }).next_cursor).toBeNull();
  });

  it("keeps missing counts explicit and never upgrades workspace roles into permissions", () => {
    const detail = projectWorkspaceDetail({
      workspace: { ...rawWorkspace, member_count: undefined, dataset_count: undefined },
      members: {
        items: [
          {
            account_id: "owner-a",
            name: "张三",
            email: "owner@example.com",
            role: "owner",
            status: "active",
            revision: 2,
          },
        ],
        next_cursor: null,
      },
      datasets: {
        items: [
          {
            dataset_id: "dataset-a",
            name: "产品知识库",
            binding_kind: "primary",
            status: "active",
            revision: 3,
          },
        ],
        next_cursor: null,
      },
      authorization_state: "workspace_authorization_not_enforced",
    });

    expect(detail.workspace.member_count).toBeNull();
    expect(detail.workspace.dataset_count).toBeNull();
    expect(detail.members.items[0]?.role).toBe("owner");
    expect(detail.datasets.items[0]?.binding_kind).toBe("primary");
    expect(detail.authorization_state).toBe("workspace_authorization_not_enforced");
    expect(detail).not.toHaveProperty("effective_permissions");
  });
});
