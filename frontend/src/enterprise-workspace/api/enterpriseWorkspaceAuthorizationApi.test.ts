// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchWorkspaceAuthorizationImpact,
  fetchWorkspaceAuthorizationPolicy,
  updateWorkspaceAuthorizationMode,
} from "./enterpriseWorkspaceAuthorizationApi";

const scope = { tenantId: "tenant-a", datasetId: "dataset/a", actorToken: "actor-token" };
const policy = {
  id: "workspace-auth-workspace-prod",
  tenant_id: "tenant-a",
  workspace_id: "workspace/prod",
  mode: "shadow",
  permission_model_version: 1,
  revision: 3,
  created_at: null,
  created_by: null,
  updated_at: null,
  updated_by: null,
  enforced_at: null,
  enforced_by: null,
  disabled_at: null,
  disabled_by: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Stage17 workspace authorization API", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("uses exact policy and selected-dataset impact read contracts", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/impact")) {
        return jsonResponse({
          impact: {
            dataset_id: "dataset/a",
            state: "workspace_authorization_shadow",
            tenant_role: "owner",
            dataset_acl_role: null,
            matched_grants: [],
            workspace_roles: [],
            contributing_workspaces: [],
            current_effective_permissions: ["knowledge.read"],
            candidate_permissions: ["knowledge.read"],
            would_grant_permissions: [],
            granted_permissions: [],
            warnings: [],
          },
        });
      }
      return jsonResponse({ policy, evidence: {} });
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchWorkspaceAuthorizationPolicy(scope, "workspace/prod");
    await fetchWorkspaceAuthorizationImpact(scope, "workspace/prod");

    const [policyUrl, policyInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(policyUrl).toBe(
      "https://enterprise.test/api/enterprise/workspaces/workspace%2Fprod/authorization",
    );
    expect(policyInit.method).toBe("GET");

    const [impactUrl, impactInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(impactUrl).toBe(
      "https://enterprise.test/api/enterprise/workspaces/workspace%2Fprod/authorization/impact?dataset_id=dataset%2Fa",
    );
    expect(impactInit.method).toBe("GET");
    for (const [, init] of fetchMock.mock.calls as unknown as Array<[string, RequestInit]>) {
      expect(init.headers).toMatchObject({
        "X-RAG4C-Tenant": "tenant-a",
        Authorization: "Bearer actor-token",
      });
    }
  });

  it("sends revision-fenced mode changes with stable idempotency and no extra fields", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ policy: { ...policy, mode: "enforced", revision: 4 }, evidence: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await updateWorkspaceAuthorizationMode(
      scope,
      "workspace/prod",
      { expected_revision: 3, target_mode: "enforced", reason: "完成 Shadow 观察后启用" },
      { idempotencyKey: "workspace-authorization-key-1" },
    );

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      "https://enterprise.test/api/enterprise/workspaces/workspace%2Fprod/authorization",
    );
    expect(init.method).toBe("PATCH");
    expect(init.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant-a",
      Authorization: "Bearer actor-token",
      "Idempotency-Key": "workspace-authorization-key-1",
    });
    expect(JSON.parse(String(init.body))).toEqual({
      expected_revision: 3,
      target_mode: "enforced",
      reason: "完成 Shadow 观察后启用",
    });
  });

  it("fails closed before fetch for missing selected dataset, invalid revision or blank reason", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchWorkspaceAuthorizationImpact({ ...scope, datasetId: "" }, "workspace-prod"),
    ).rejects.toThrow("datasetId is required");
    await expect(
      updateWorkspaceAuthorizationMode(scope, "workspace-prod", {
        expected_revision: 0,
        target_mode: "shadow",
        reason: "有效原因",
      }),
    ).rejects.toThrow("expected_revision must be a positive integer");
    await expect(
      updateWorkspaceAuthorizationMode(scope, "workspace-prod", {
        expected_revision: 3,
        target_mode: "shadow",
        reason: "   ",
      }),
    ).rejects.toThrow("reason is required");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
