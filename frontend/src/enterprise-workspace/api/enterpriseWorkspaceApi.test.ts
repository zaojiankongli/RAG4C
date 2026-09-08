// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  addWorkspaceMember,
  archiveWorkspace,
  bindWorkspaceDataset,
  createEnterpriseWorkspace,
  fetchEnterpriseWorkspaceDetail,
  fetchEnterpriseWorkspaces,
  fetchWorkspaceDatasets,
  fetchWorkspaceMembers,
  removeWorkspaceDataset,
  updateEnterpriseWorkspace,
} from "./enterpriseWorkspaceApi";

const scope = { tenantId: "tenant-a", actorToken: "actor-token", datasetId: "dataset-a" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("enterprise workspace API", () => {
  it("loads a bounded server workspace page with enterprise identity headers", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchEnterpriseWorkspaces(scope, {
      status: "active",
      cursor: "workspace-z",
      limit: 40,
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/enterprise/workspaces?status=active&cursor=workspace-z&limit=40");
    expect(init.method).toBe("GET");
    expect(init.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant-a",
      Authorization: "Bearer actor-token",
    });
  });

  it("uses exact workspace detail, member and dataset endpoints", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        urls.push(String(input));
        return Promise.resolve(
          jsonResponse({
            workspace: {
              id: "workspace/prod",
              tenant_id: "tenant-a",
              code: "prod",
              name: "生产域",
              status: "active",
              environment: "production",
              revision: 1,
            },
          }),
        );
      }),
    );

    await fetchEnterpriseWorkspaceDetail(scope, "workspace/prod");
    expect(urls[0]).toContain("/api/enterprise/workspaces/workspace%2Fprod");
  });

  it("accepts deterministic default Workspace IDs up to the 128-character database contract", async () => {
    const tenantId = "t".repeat(64);
    const defaultWorkspaceId = `workspace-default-${tenantId}`;
    const defaultScope = { ...scope, tenantId };
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/members")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/datasets")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        return Promise.resolve(
          jsonResponse({
            workspace: {
              id: defaultWorkspaceId,
              tenant_id: tenantId,
              code: "default",
              name: "默认 Workspace",
              status: "active",
              environment: "production",
              revision: 1,
            },
          }),
        );
      }),
    );

    await fetchEnterpriseWorkspaceDetail(defaultScope, defaultWorkspaceId);
    await fetchWorkspaceMembers(defaultScope, defaultWorkspaceId);
    await fetchWorkspaceDatasets(defaultScope, defaultWorkspaceId);
    await addWorkspaceMember(
      defaultScope,
      defaultWorkspaceId,
      {
        account_id: "member-a",
        role: "viewer",
        reason: "默认 Workspace 回填成员",
      },
      { idempotencyKey: "default-workspace-member" },
    );
    await bindWorkspaceDataset(
      defaultScope,
      defaultWorkspaceId,
      {
        dataset_id: "dataset-a",
        binding_kind: "primary",
        reason: "默认 Workspace 回填主绑定",
      },
      { idempotencyKey: "default-workspace-dataset" },
    );

    expect(defaultWorkspaceId).toHaveLength(82);
    expect(calls).toHaveLength(5);
    for (const url of calls) {
      expect(decodeURIComponent(new URL(url).pathname)).toContain(
        `/api/enterprise/workspaces/${defaultWorkspaceId}`,
      );
    }
  });

  it("enforces the backend 512-character Workspace description contract", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const tooLong = "x".repeat(513);

    await expect(
      createEnterpriseWorkspace(scope, {
        code: "prod",
        name: "生产域",
        description: tooLong,
        environment: "production",
        reason: "创建 Workspace",
      }),
    ).rejects.toThrow("description must be at most 512 characters");

    await expect(
      updateEnterpriseWorkspace(scope, "workspace-prod", {
        revision: 1,
        description: tooLong,
        reason: "更新 Workspace",
      }),
    ).rejects.toThrow("description must be at most 512 characters");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requests only active member and dataset bindings in management views", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        calls.push(String(input));
        return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
      }),
    );

    await fetchWorkspaceMembers(scope, "workspace-prod", {
      status: "active",
      cursor: "member-next",
      limit: 100,
    });
    await fetchWorkspaceDatasets(scope, "workspace-prod", {
      status: "active",
      cursor: "dataset-next",
      limit: 100,
    });

    expect(new URL(calls[0] ?? "").search).toBe("?status=active&cursor=member-next&limit=100");
    expect(new URL(calls[1] ?? "").search).toBe("?status=active&cursor=dataset-next&limit=100");
  });

  it("sends revision-fenced mutations with exact replay identity", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init: init ?? {} });
        return Promise.resolve(
          jsonResponse({
            workspace: {
              id: "workspace-prod",
              tenant_id: "tenant-a",
              code: "prod",
              name: "生产域",
              status: "active",
              environment: "production",
              revision: 2,
            },
          }),
        );
      }),
    );

    const options = { idempotencyKey: "workspace-replay-key" };
    await createEnterpriseWorkspace(
      scope,
      {
        code: "prod",
        name: "生产域",
        description: "正式工作区",
        environment: "production",
        reason: "建立生产治理边界",
      },
      options,
    );
    await updateEnterpriseWorkspace(
      scope,
      "workspace-prod",
      { revision: 1, name: "生产知识域", reason: "名称规范化" },
      options,
    );
    await archiveWorkspace(scope, "workspace-prod", { revision: 2, reason: "停止使用" }, options);
    await addWorkspaceMember(
      scope,
      "workspace-prod",
      { account_id: "editor-a", role: "editor", reason: "项目协作" },
      options,
    );
    await bindWorkspaceDataset(
      scope,
      "workspace-prod",
      {
        dataset_id: "dataset-a",
        binding_kind: "primary",
        reason: "绑定主知识库",
      },
      options,
    );
    await removeWorkspaceDataset(
      scope,
      "workspace-prod",
      "dataset-a",
      { revision: 1, reason: "解除绑定" },
      options,
    );

    expect(calls.map((call) => [call.init.method, new URL(call.url).pathname])).toEqual([
      ["POST", "/api/enterprise/workspaces"],
      ["PATCH", "/api/enterprise/workspaces/workspace-prod"],
      ["POST", "/api/enterprise/workspaces/workspace-prod/archive"],
      ["POST", "/api/enterprise/workspaces/workspace-prod/members"],
      ["POST", "/api/enterprise/workspaces/workspace-prod/datasets"],
      ["POST", "/api/enterprise/workspaces/workspace-prod/datasets/dataset-a/remove"],
    ]);
    for (const call of calls) {
      expect(call.init.headers).toMatchObject({ "Idempotency-Key": "workspace-replay-key" });
    }
    expect(JSON.parse(String(calls[3]?.init.body))).toEqual({
      account_id: "editor-a",
      role: "editor",
      reason: "项目协作",
    });
    expect(JSON.parse(String(calls[4]?.init.body))).toEqual({
      dataset_id: "dataset-a",
      binding_kind: "primary",
      reason: "绑定主知识库",
    });
  });
});
