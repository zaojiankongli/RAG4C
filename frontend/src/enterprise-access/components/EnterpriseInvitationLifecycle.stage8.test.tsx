// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseAccessGraph from "./EnterpriseAccessGraph";

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
    quota_documents: 100,
    quota_chunks: 1000,
    doc_count: 1,
    chunk_count: 2,
  },
  actor: { id: "account-admin", name: "管理员", email: "admin@example.com", role: "admin" },
  member_count: 2,
  dataset_count: 1,
  effective_permissions: ["knowledge.manage"],
  role_permissions: { admin: ["knowledge.manage"] },
  capabilities: {
    organization_units: { state: "ready", label: "组织架构", reason: null },
    user_groups: { state: "ready", label: "用户组", reason: null },
    dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
    invitations: { state: "ready", label: "成员邀请", reason: null },
  },
};
const invitation = {
  id: "invite-1",
  email: "member@example.com",
  role: "member",
  status: "pending",
  expires_at: "2026-09-02T08:00:00Z",
  invited_by: "account-admin",
  revision: 4,
  send_count: 2,
  last_sent_at: "2026-08-26T08:30:00Z",
  created_at: "2026-08-25T08:00:00Z",
};
const delivery = {
  state: "manual_link_required",
  invite_token: "one-time-secret",
  expires_at: invitation.expires_at,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      media: "(max-width: 600px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api/enterprise/invitations/invite-1/resend") && method === "POST")
        return Promise.resolve(
          jsonResponse({ invitation: { ...invitation, revision: 5, send_count: 3 }, delivery }),
        );
      if (url.includes("/api/enterprise/invitations/invite-1/revoke") && method === "POST")
        return Promise.resolve(
          jsonResponse({
            invitation: { ...invitation, status: "revoked", revision: 5 },
            delivery: null,
          }),
        );
      if (url.endsWith("/api/enterprise/invitations") && method === "POST")
        return Promise.resolve(
          jsonResponse({ invitation: { ...invitation, id: "invite-new" }, delivery }, 201),
        );
      if (url.includes("/api/enterprise/invitations") && method === "GET")
        return Promise.resolve(
          jsonResponse({ items: [invitation], count: 1, next_before_id: null }),
        );
      if (
        url.includes("/organization-units") ||
        url.includes("/api/enterprise/groups") ||
        url.includes("/access-grants")
      )
        return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    }),
  );
}

async function openInvitationTab() {
  const user = userEvent.setup();
  render(<EnterpriseAccessGraph scope={scope} context={context} />);
  const graph = await screen.findByRole("region", { name: "企业访问图谱" });
  await user.click(within(graph).getByRole("tab", { name: /成员邀请/ }));
  expect(await within(graph).findByText("member@example.com")).toBeTruthy();
  return { user, graph };
}

describe("Stage 8 enterprise invitation lifecycle", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    installMedia(false);
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders lifecycle evidence and desktop/mobile actions", async () => {
    const { graph } = await openInvitationTab();
    expect(within(graph).getByRole("button", { name: "发起邀请" })).toBeTruthy();
    expect(within(graph).getByText("revision 4")).toBeTruthy();
    expect(within(graph).getByText("发送 2 次")).toBeTruthy();
    expect(
      within(graph).getByRole("button", { name: "重新生成member@example.com安全链接" }),
    ).toBeTruthy();
    expect(within(graph).getByRole("button", { name: "撤销member@example.com邀请" })).toBeTruthy();
  });

  it("creates an invitation through TDesign form controls and clears the one-time token on close", async () => {
    const { user, graph } = await openInvitationTab();
    await user.click(within(graph).getByRole("button", { name: "发起邀请" }));
    const dialog = await screen.findByRole("dialog", { name: "发起成员邀请" });
    expect(dialog.querySelector(".t-form")).toBeTruthy();
    expect(dialog.querySelector(".t-input")).toBeTruthy();
    expect(dialog.querySelector(".t-select")).toBeTruthy();
    expect(dialog.querySelector(".t-date-picker")).toBeTruthy();

    await user.type(within(dialog).getByLabelText("受邀邮箱"), "new@example.com");
    await user.type(within(dialog).getByLabelText("邀请原因"), "加入知识运营团队");
    await user.click(within(dialog).getByText("确认邀请"));

    const result = await screen.findByRole("dialog", { name: "一次性邀请安全链接" });
    expect(within(result).getByText("manual_link_required")).toBeTruthy();
    expect(within(result).getByText("邮件通道未接入")).toBeTruthy();
    expect(within(result).getByRole("button", { name: "复制安全链接" })).toBeTruthy();
    expect(within(result).queryByText("one-time-secret")).toBeNull();
    await user.click(within(result).getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog", { name: "一次性邀请安全链接" })).toBeNull();
    expect(document.body.textContent).not.toContain("one-time-secret");
  });

  it("opens the mobile action drawer and exposes resend/revoke actions", async () => {
    installMedia(true);
    const { user, graph } = await openInvitationTab();
    expect(within(graph).getByTestId("enterprise-invitation-mobile-list")).toBeTruthy();
    await user.click(within(graph).getByRole("button", { name: "管理member@example.com邀请" }));
    const drawer = await screen.findByRole("dialog", { name: "成员邀请操作" });
    expect(within(drawer).getByRole("button", { name: "重新生成链接" })).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "撤销邀请" })).toBeTruthy();
  });

  it("shows the manual-link and refresh recovery message for mutation failures", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/enterprise/invitations") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ detail: { code: "tenant_invitation_revision_conflict" } }, 409),
        );
      if (url.includes("/api/enterprise/invitations") && (init?.method ?? "GET") === "GET")
        return Promise.resolve(
          jsonResponse({ items: [invitation], count: 1, next_before_id: null }),
        );
      return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
    });
    const { user, graph } = await openInvitationTab();
    await user.click(within(graph).getByRole("button", { name: "发起邀请" }));
    const dialog = await screen.findByRole("dialog", { name: "发起成员邀请" });
    await user.type(within(dialog).getByLabelText("受邀邮箱"), "new@example.com");
    await user.type(within(dialog).getByLabelText("邀请原因"), "冲突测试");
    await user.click(within(dialog).getByText("确认邀请"));
    expect(await within(dialog).findByText(/刷新邀请列表后重试/)).toBeTruthy();
  });
});
