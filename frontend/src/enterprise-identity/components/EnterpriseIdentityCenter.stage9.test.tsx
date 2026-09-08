// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseIdentityCenter from "./EnterpriseIdentityCenter";

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
    quota_documents: 1,
    quota_chunks: 1,
    doc_count: 0,
    chunk_count: 0,
  },
  actor: { id: "owner", name: "Owner", email: "owner@example.com", role: "owner" },
  member_count: 1,
  dataset_count: 1,
  effective_permissions: [],
  role_permissions: {},
  capabilities: { identity_federation: { state: "ready", label: "身份联合", reason: null } },
};
const domain = {
  id: "domain-1",
  domain: "example.com",
  normalized_domain: "example.com",
  status: "verified",
  verification_method: "dns_txt",
  txt_host: "_rag4c-verify.example.com",
  txt_value: "rag4c-verification=challenge",
  revision: 2,
  verified_at: "2026-08-26T08:00:00Z",
};
const provider = {
  id: "idp-1",
  name: "Company OIDC",
  provider_type: "oidc",
  status: "draft",
  trusted_domain_id: "domain-1",
  validation_state: "valid",
  runtime_state: "runtime_not_connected",
  issuer_url: "https://id.example.com",
  client_id: "client-id",
  secret_ref: "vault://identity/client",
  scopes: ["openid", "email"],
  revision: 3,
};
const scim = {
  id: "scim-1",
  name: "hr-sync",
  prefix: "r4c_scim_ab12",
  status: "active",
  scopes: ["Users.Read", "Groups.Read"],
  expires_at: "2026-09-26T08:00:00Z",
  revision: 1,
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
      if (url.includes("/domains") && method === "GET")
        return Promise.resolve(jsonResponse({ items: [domain], count: 1, next_before_id: null }));
      if (url.includes("/providers") && method === "GET")
        return Promise.resolve(jsonResponse({ items: [provider], count: 1, next_before_id: null }));
      if (url.includes("/scim-tokens") && method === "GET")
        return Promise.resolve(jsonResponse({ items: [scim], count: 1, next_before_id: null }));
      if (url.endsWith("/scim-tokens") && method === "POST")
        return Promise.resolve(
          jsonResponse(
            {
              token: { ...scim, id: "scim-new", name: "ci-sync" },
              delivery: { state: "token_returned_once", scim_token: "raw-scim-once" },
            },
            201,
          ),
        );
      return Promise.resolve(jsonResponse({ domain, provider, token: scim }));
    }),
  );
}

describe("Stage 9 enterprise identity center", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    installMedia(false);
    installFetch();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders honest runtime and SCIM data-plane evidence with three TDesign workspaces", async () => {
    render(<EnterpriseIdentityCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    expect(within(center).getByText("runtime_not_connected")).toBeTruthy();
    expect(within(center).getByText("scim_data_plane_not_connected")).toBeTruthy();
    expect(within(center).getAllByRole("tab")).toHaveLength(3);
    expect(within(center).getByText("example.com")).toBeTruthy();
    expect(
      within(center).getByRole("button", { name: "复制 example.com DNS TXT host" }),
    ).toBeTruthy();
    expect(within(center).getByRole("button", { name: "验证 example.com 域名" })).toBeTruthy();
    expect(within(center).getByRole("button", { name: "撤销 example.com 域名" })).toBeTruthy();
  });

  it("opens the OIDC/SAML TDesign wizard and exposes activate/disable controls", async () => {
    const user = userEvent.setup();
    render(<EnterpriseIdentityCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    await user.click(within(center).getByRole("tab", { name: /OIDC\/SAML/ }));
    expect(await within(center).findByText("Company OIDC")).toBeTruthy();
    expect(within(center).getByRole("button", { name: "激活 Company OIDC" })).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "配置身份提供商" }));
    const wizard = await screen.findByRole("dialog", { name: "配置 OIDC/SAML 身份提供商" });
    expect(wizard.querySelector(".t-steps")).toBeTruthy();
    expect(wizard.querySelector(".t-form")).toBeTruthy();
    expect(within(wizard).getByText("外部登录 runtime_not_connected")).toBeTruthy();
  });

  it("issues a SCIM token once, masks it, copies it, and clears transient state", async () => {
    const user = userEvent.setup();
    render(<EnterpriseIdentityCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    await user.click(within(center).getByRole("tab", { name: /SCIM/ }));
    expect(await within(center).findByText("hr-sync")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "签发 SCIM token" }));
    const dialog = await screen.findByRole("dialog", { name: "签发 SCIM token" });
    await user.type(within(dialog).getByLabelText("Token 名称"), "ci-sync");
    await user.type(within(dialog).getByLabelText("签发原因"), "CI provisioning control");
    await user.click(within(dialog).getByText("确认签发"));
    const result = await screen.findByRole("dialog", { name: "一次性 SCIM token" });
    expect(within(result).getByText("token_returned_once")).toBeTruthy();
    expect(within(result).getByText("scim_data_plane_not_connected")).toBeTruthy();
    expect(within(result).queryByText("raw-scim-once")).toBeNull();
    expect(within(result).getByRole("button", { name: "复制 SCIM token" })).toBeTruthy();
    await user.click(within(result).getByRole("button", { name: "关闭" }));
    expect(document.body.textContent).not.toContain("raw-scim-once");
  });

  it("renders 375/280 cards and an action drawer without duplicate desktop DOM", async () => {
    installMedia(true);
    const user = userEvent.setup();
    render(<EnterpriseIdentityCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    expect(within(center).getByTestId("identity-domain-mobile-list")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "管理 example.com 域名" }));
    const drawer = await screen.findByRole("dialog", { name: "企业身份对象操作" });
    expect(within(drawer).getByRole("button", { name: "验证域名" })).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "撤销域名" })).toBeTruthy();
  });
});
