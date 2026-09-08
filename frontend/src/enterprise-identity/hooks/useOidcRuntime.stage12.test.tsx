// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY } from "../../knowledge/workspaceScope";
import * as api from "../api/enterpriseIdentityApi";
import * as route from "../oidcRuntimeRoute";
import { useOidcCallback, useOidcRuntimeStart } from "./useOidcRuntime";

vi.mock("../api/enterpriseIdentityApi");
vi.mock("../oidcRuntimeRoute", async () => {
  const actual = await vi.importActual<typeof import("../oidcRuntimeRoute")>("../oidcRuntimeRoute");
  return { ...actual, clearOidcCallbackLocation: vi.fn() };
});

describe("Stage 12 OIDC runtime hooks", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });
  afterEach(() => localStorage.clear());

  it("starts login and delegates only the validated authorization URL", async () => {
    vi.mocked(api.startOidcLogin).mockResolvedValue({
      authorization_url: "https://id.example.com/authorize",
      provider_id: "idp-1",
      expires_at: "2026-08-26T08:05:00Z",
    });
    const navigate = vi.fn();
    const { result } = renderHook(() =>
      useOidcRuntimeStart({
        scope: { tenantId: "tenant-1", datasetId: "dataset-1", actorToken: "actor-token" },
        navigate,
      }),
    );
    await act(async () => {
      await result.current.start(
        { id: "idp-1", name: "Company OIDC" },
        "https://rag.example/enterprise/sso/oidc/callback",
      );
    });
    expect(navigate).toHaveBeenCalledWith("https://id.example.com/authorize");
  });

  it("completes a delayed callback exactly once under React StrictMode", async () => {
    vi.mocked(api.completeOidcCallback).mockImplementation(
      (_credentials, options) =>
        new Promise((resolve, reject) => {
          options?.signal?.addEventListener("abort", () => reject(new Error("aborted")), {
            once: true,
          });
          setTimeout(
            () =>
              resolve({
                knowledge_actor_token: "strict-actor-token",
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
              }),
            20,
          );
        }),
    );
    const credentials = {
      code: "strict-code",
      state: "strict-state",
    };
    const { result } = renderHook(() => useOidcCallback(credentials), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    });
    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(api.completeOidcCallback).toHaveBeenCalledTimes(1);
  });

  it.each([
    [409, "oidc_state_consumed", "OIDC 登录事务已使用", "state 已消费"],
    [410, "oidc_state_expired", "OIDC 登录事务已过期", "登录窗口已过期"],
    [409, "oidc_state_expired", "OIDC 登录事务已过期", "登录窗口已过期"],
  ] as const)(
    "maps OIDC callback %s %s to a sanitized operator message",
    async (status, code, expectedTitle, expectedMessage) => {
      vi.mocked(api.completeOidcCallback).mockRejectedValueOnce(
        new ApiError("callback failed", "http", status, {
          detail: { code, message: "backend detail must not render" },
        }),
      );
      const { result } = renderHook(
        () => useOidcCallback({ code: "one-time-code", state: "one-time-state" }),
        {
          wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
        },
      );
      await waitFor(() => expect(result.current.status).toBe("error"));
      expect(result.current.error).toMatchObject({
        title: expectedTitle,
        status,
      });
      expect(result.current.error?.message).toContain(expectedMessage);
      expect(result.current.error?.message).not.toContain("backend detail must not render");
      expect(api.completeOidcCallback).toHaveBeenCalledTimes(1);
    },
  );

  it("submits transient code/state, immediately clears the URL, and stores only KnowledgeActor token", async () => {
    vi.mocked(api.completeOidcCallback).mockResolvedValue({
      knowledge_actor_token: "knowledge-actor-once",
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
    });
    const credentials = {
      code: "secret-code",
      state: "secret-state",
    };
    const { result } = renderHook(() => useOidcCallback(credentials), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    });
    expect(route.clearOidcCallbackLocation).toHaveBeenCalledTimes(1);
    expect(vi.mocked(route.clearOidcCallbackLocation).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.completeOidcCallback).mock.invocationCallOrder[0],
    );
    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(localStorage.getItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY)).toBe("knowledge-actor-once");
    expect(localStorage.getItem("id_token")).toBeNull();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(JSON.stringify(result.current.result)).not.toContain("knowledge-actor-once");
    expect(JSON.stringify(result.current.result)).not.toContain("secret-code");
    expect(JSON.stringify(result.current.result)).not.toContain("secret-state");
  });
});
