// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import { OIDC_RUNTIME_REVISION } from "../enterpriseIdentityModel";
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
  expected_head: OIDC_RUNTIME_REVISION,
  current_revision: OIDC_RUNTIME_REVISION,
  status: "ready",
  missing_capability_groups: [],
};
const provider = {
  id: "idp-1",
  name: "Company OIDC",
  provider_type: "oidc",
  status: "active",
  trusted_domain_id: "domain-1",
  validation_state: "valid",
  runtime_state: "oidc_runtime_ready",
  issuer_url: "https://id.example.com",
  client_id: "client",
  secret_ref: "vault://client",
  revision: 4,
};
function jsonResponse(body: unknown) {
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
        return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
      if (url.includes("/providers"))
        return Promise.resolve(jsonResponse({ items: [provider], count: 1, next_before_id: null }));
      if (url.includes("/scim-tokens"))
        return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
      return Promise.resolve(
        jsonResponse({
          authorization_url: "https://id.example.com/authorize",
          provider_id: "idp-1",
          expires_at: "2026-08-26T08:05:00Z",
        }),
      );
    }),
  );
}

describe("Stage 12 OIDC runtime identity center", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    installMedia(false);
    installFetch();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("shows oidc_runtime_ready, keeps SAML disconnected, and starts login without exposing tokens", async () => {
    const user = userEvent.setup();
    const navigate = vi.fn();
    render(
      <EnterpriseIdentityCenter
        scope={scope}
        context={context}
        readiness={readiness}
        onOidcNavigate={navigate}
      />,
    );
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    expect(within(center).getByText("oidc_runtime_ready")).toBeTruthy();
    expect(within(center).getByText("saml_runtime_not_connected")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "Start Login Company OIDC" }));
    expect(navigate).toHaveBeenCalledWith("https://id.example.com/authorize");
    expect(center.textContent).not.toContain("id_token");
    expect(center.textContent).not.toContain("access_token");
  });

  it("keeps the runtime action usable on 375/280 provider cards", async () => {
    installMedia(true);
    const navigate = vi.fn();
    const user = userEvent.setup();
    render(
      <EnterpriseIdentityCenter
        scope={scope}
        context={context}
        readiness={readiness}
        onOidcNavigate={navigate}
      />,
    );
    const center = await screen.findByRole("region", { name: "企业身份联合中心" });
    await user.click(within(center).getByRole("tab", { name: /OIDC\/SAML/ }));
    expect(within(center).getByRole("button", { name: "Start Login Company OIDC" })).toBeTruthy();
  });
});
