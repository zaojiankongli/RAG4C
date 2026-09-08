// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OidcCallbackSurface from "./OidcCallbackSurface";

vi.mock("../hooks/useOidcRuntime", () => ({
  useOidcCallback: vi.fn(() => ({
    status: "success",
    error: null,
    result: {
      status: "authenticated",
      actor_id: "account-1",
      actor_name: "林澈",
      actor_email: "lin@example.com",
      tenant_id: "tenant-1",
      tenant_name: "星海科技",
      provider_id: "idp-1",
      provider_name: "Company OIDC",
      session_id: "session-1",
      session_status: "active",
      session_revision: 1,
      expires_at: "2026-08-26T12:00:00Z",
    },
  })),
}));

describe("Stage 12 OIDC callback surface", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("renders sanitized success evidence without code/state or OAuth tokens", () => {
    render(
      <OidcCallbackSurface
        credentials={{
          code: "secret-code",
          state: "secret-state",
        }}
      />,
    );
    const surface = screen.getByRole("region", { name: "OIDC 登录回调结果" });
    expect(within(surface).getByText("OIDC 登录成功")).toBeTruthy();
    expect(within(surface).getByText("lin@example.com")).toBeTruthy();
    expect(within(surface).getByText("Company OIDC")).toBeTruthy();
    expect(surface.textContent).not.toContain("secret-code");
    expect(surface.textContent).not.toContain("secret-state");
    expect(surface.textContent).not.toContain("id_token");
    expect(surface.textContent).not.toContain("access_token");
  });
});
