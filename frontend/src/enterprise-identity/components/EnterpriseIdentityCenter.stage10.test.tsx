// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import { SCIM_DATA_PLANE_REVISION } from "../enterpriseIdentityModel";
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
const readiness = {
  expected_head: SCIM_DATA_PLANE_REVISION,
  current_revision: SCIM_DATA_PLANE_REVISION,
  status: "ready",
  missing_capability_groups: [],
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
};
const provider = {
  id: "idp-1",
  name: "Company OIDC",
  provider_type: "oidc",
  status: "active",
  trusted_domain_id: "domain-1",
  validation_state: "valid",
  runtime_state: "runtime_not_connected",
  revision: 3,
};
const scim = {
  id: "scim-1",
  name: "hr-sync",
  prefix: "r4c_scim_ab12",
  status: "active",
  scopes: ["users:read", "groups:read"],
  expires_at: "2026-09-26T08:00:00Z",
  last_used_at: "2026-08-26T09:30:00Z",
  use_count: 17,
  revision: 4,
};
function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
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
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/domains"))
        return Promise.resolve(jsonResponse({ items: [domain], count: 1, next_before_id: null }));
      if (url.includes("/providers"))
        return Promise.resolve(jsonResponse({ items: [provider], count: 1, next_before_id: null }));
      return Promise.resolve(jsonResponse({ items: [scim], count: 1, next_before_id: null }));
    }),
  );
}

describe("Stage 10 SCIM data-plane identity center", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://rag.enterprise.example");
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

  it("shows ready endpoint/resources/scopes while SSO runtime remains not connected", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    render(<EnterpriseIdentityCenter scope={scope} context={context} readiness={readiness} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });

    expect(within(center).getByText("scim_data_plane_ready")).toBeTruthy();
    expect(within(center).getByText("sso_runtime_not_connected")).toBeTruthy();
    expect(within(center).getByDisplayValue("/scim/v2")).toBeTruthy();
    expect(within(center).getByText("Users")).toBeTruthy();
    expect(within(center).getByText("Groups")).toBeTruthy();
    expect(within(center).getByText("users:read")).toBeTruthy();
    expect(within(center).getByText("groups:write")).toBeTruthy();

    await user.click(
      within(center).getByRole("button", { name: "复制 ServiceProviderConfig URL" }),
    );
    expect(writeText).toHaveBeenCalledWith(
      "https://rag.enterprise.example/scim/v2/ServiceProviderConfig",
    );
  });

  it("shows token last-used/use-count evidence in desktop and mobile drawer surfaces", async () => {
    const user = userEvent.setup();
    render(<EnterpriseIdentityCenter scope={scope} context={context} readiness={readiness} />);
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    await user.click(within(center).getByRole("tab", { name: /SCIM/ }));
    expect(await within(center).findByText("17 次")).toBeTruthy();
    expect(within(center).getByText(/2026\/08\/26/)).toBeTruthy();

    cleanup();
    installMedia(true);
    render(<EnterpriseIdentityCenter scope={scope} context={context} readiness={readiness} />);
    const mobileCenter = await screen.findByRole("region", { name: "企业身份联合中心" });
    await user.click(within(mobileCenter).getByRole("tab", { name: /SCIM/ }));
    await user.click(within(mobileCenter).getByRole("button", { name: "管理 hr-sync SCIM token" }));
    const drawer = await screen.findByRole("dialog", { name: "企业身份对象操作" });
    expect(within(drawer).getByText("17 次")).toBeTruthy();
    expect(within(drawer).getByText("users:read, groups:read")).toBeTruthy();
  });
});
