// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import WorkspacePermissionsRolloutCenter from "./WorkspacePermissionsRolloutCenter";

const VALID_FINGERPRINT = "d49cb11be241a82e4744fcafae0319b219c61c18cba69f00f33d85d5a2a04fff";
const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };
const context = {
  tenant: {
    id: "tenant-a",
    name: "RAG4C 企业",
    plan: "enterprise",
    status: "active",
    quota_documents: 1000,
    quota_chunks: 10000,
    doc_count: 20,
    chunk_count: 100,
  },
  actor: { id: "owner-a", name: "张三", email: "owner@example.com", role: "owner" },
  member_count: 4,
  dataset_count: 2,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: {},
  capabilities: {},
};
const workspace = {
  id: "workspace-prod",
  tenant_id: "tenant-a",
  code: "prod",
  name: "生产知识域",
  description: "生产 Workspace",
  status: "active",
  environment: "production",
  is_default: true,
  revision: 7,
  member_count: 4,
  dataset_count: 2,
  primary_dataset_count: 1,
  actor_role: "owner",
  created_at: "2026-08-27T08:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
  archived_at: null,
};

function policy(mode: "disabled" | "shadow" | "enforced", withApproval = false) {
  return {
    policy: {
      id: "workspace-auth-workspace-prod",
      tenant_id: "tenant-a",
      workspace_id: "workspace-prod",
      mode,
      permission_model_version: 1,
      revision: 3,
      created_at: "2026-08-27T08:00:00Z",
      created_by: "owner-a",
      updated_at: "2026-08-27T09:00:00Z",
      updated_by: "owner-a",
      enforced_at: mode === "enforced" ? "2026-08-27T09:00:00Z" : null,
      enforced_by: mode === "enforced" ? "owner-a" : null,
      disabled_at: mode === "disabled" ? "2026-08-27T09:00:00Z" : null,
      disabled_by: mode === "disabled" ? "owner-a" : null,
    },
    evidence: {
      active_member_count: 4,
      active_dataset_binding_count: 2,
      catalog_revision: "0027_enterprise_workspace_authorization",
      permission_matrix_fingerprint: VALID_FINGERPRINT,
      matching_approval_policy: withApproval
        ? {
            state: "active",
            id: "approval-policy-workspace",
            name: "Workspace 强制授权",
            required_approvals: 2,
            execution_adapter_status: "connected",
          }
        : null,
      latest_audit_event: {
        sequence: 91,
        action: "workspace.authorization.mode.changed",
        resource_type: "tenant_workspace_authorization_policy",
        resource_id: "workspace-auth-workspace-prod",
        actor_id: "owner-a",
        actor_name: "张三",
        occurred_at: "2026-08-27T09:00:00Z",
      },
    },
  };
}

const impact = {
  impact: {
    dataset_id: "dataset-a",
    state: "workspace_authorization_shadow",
    tenant_role: "owner",
    dataset_acl_role: "manager",
    matched_grants: [
      {
        id: "grant-a",
        subject_type: "account",
        subject_id: "owner-a",
        subject_name: "张三",
        role: "manager",
      },
    ],
    workspace_roles: [
      {
        workspace_id: "workspace-prod",
        workspace_name: "生产知识域",
        role: "owner",
        binding_kind: "primary",
        policy_mode: "shadow",
        policy_revision: 3,
      },
    ],
    contributing_workspaces: [],
    current_effective_permissions: ["knowledge.read", "knowledge.manage"],
    candidate_permissions: [
      "knowledge.read",
      "knowledge.write",
      "knowledge.delete",
      "knowledge.manage",
      "knowledge.audit",
    ],
    would_grant_permissions: ["knowledge.write", "knowledge.delete", "knowledge.audit"],
    granted_permissions: [],
    warnings: ["Shadow 模式不会改变 effective_permissions"],
  },
};

function approvalRequest() {
  return {
    id: "request-workspace-17",
    policy_id: "approval-policy-workspace",
    action_type: "workspace_authorization_mode_change",
    resource_type: "tenant_workspace",
    resource_id: "workspace-prod",
    requester: { id: "owner-a", name: "张三" },
    reason: "Shadow 观察通过",
    status: "pending",
    required_approvals: 2,
    received_approvals: 0,
    expires_at: "2026-08-28T09:00:00Z",
    created_at: "2026-08-27T09:00:00Z",
    revision: 1,
    execution_adapter_status: "connected",
    snapshot: {},
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installApi(currentMode: "disabled" | "shadow" | "enforced", withApproval = false) {
  let currentPolicy = policy(currentMode, withApproval);
  const calls: Array<{ url: string; init: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes("/authorization/impact")) return jsonResponse(impact);
      if (url.includes("/api/enterprise/approvals/requests") && init.method === "POST") {
        return jsonResponse({
          request: approvalRequest(),
          execution: { ticket: "opaque-ticket-never-render" },
        });
      }
      if (url.endsWith("/authorization") && init.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        currentPolicy = {
          ...currentPolicy,
          policy: {
            ...currentPolicy.policy,
            mode: body.target_mode,
            revision: currentPolicy.policy.revision + 1,
          },
        };
        return jsonResponse(currentPolicy);
      }
      if (url.endsWith("/authorization")) return jsonResponse(currentPolicy);
      throw new Error(`unknown request: ${url}`);
    }),
  );
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("rag4c.base_url", "https://enterprise.test");
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("Stage17 Workspace Permissions Rollout Center", () => {
  it("renders authoritative rollout evidence, role matrix and actor impact without card-wall metrics", async () => {
    installApi("shadow", true);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    expect(await screen.findByText("Shadow 观察")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Workspace 授权策略证据" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Workspace 角色权限矩阵" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Workspace 授权影响预览" })).toBeTruthy();
    expect(screen.getByText("Dataset projection role")).toBeTruthy();
    expect(screen.getByRole("list", { name: "移动端 Workspace 角色权限矩阵" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "移动端 Workspace 授权影响预览" })).toBeTruthy();
    expect(screen.getByText(VALID_FINGERPRINT)).toBeTruthy();
    expect(screen.getByText("0027_enterprise_workspace_authorization")).toBeTruthy();
    expect(screen.getByText("workspace-prod")).toBeTruthy();
    expect(screen.getByText("Tenant role")).toBeTruthy();
    expect(screen.getByText("Dataset ACL role")).toBeTruthy();
    expect(screen.getByText("Matched grants")).toBeTruthy();
    expect(screen.getByText("Workspace roles")).toBeTruthy();
    expect(screen.getByText("Contributing workspaces")).toBeTruthy();
    expect(screen.getByText(/binding kind: primary/)).toBeTruthy();
    expect(screen.getByText(/policy revision: 3/)).toBeTruthy();
    expect(screen.getByText("Shadow 模式不会改变 effective_permissions")).toBeTruthy();
    expect(screen.queryByText(/示例策略|模拟权限/)).toBeNull();
  });

  it("validates and executes a direct disabled-to-shadow revision-fenced mutation", async () => {
    const calls = installApi("disabled", false);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("已停用");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const dialog = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    expect(within(dialog).getByText("Policy Revision")).toBeTruthy();
    expect(within(dialog).getByText("Workspace revision")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "切换到 Shadow" }));
    expect(within(dialog).getByText("请输入变更原因")).toBeTruthy();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "先启用 Shadow 观察" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "切换到 Shadow" }));

    await waitFor(() => expect(screen.getByText("Workspace 授权模式已更新")).toBeTruthy());
    const mutation = calls.find((call) => call.init.method === "PATCH");
    expect(mutation).toBeTruthy();
    expect(JSON.parse(String(mutation?.init.body))).toEqual({
      expected_revision: 3,
      target_mode: "shadow",
      reason: "先启用 Shadow 观察",
    });
  });

  it("reuses one direct mutation idempotency key after a transient response failure", async () => {
    let patchAttempts = 0;
    const patchKeys: string[] = [];
    const current = policy("disabled", false);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(impact);
        if (url.endsWith("/authorization") && init.method === "PATCH") {
          patchAttempts += 1;
          patchKeys.push(String((init.headers as Record<string, string>)["Idempotency-Key"]));
          if (patchAttempts === 1) throw new TypeError("Failed to fetch");
          return jsonResponse({
            ...current,
            policy: { ...current.policy, mode: "shadow", revision: 4 },
          });
        }
        if (url.endsWith("/authorization")) return jsonResponse(current);
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("已停用");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "稳定重试 Shadow 变更" },
    });
    const submit = within(panel).getByRole("button", { name: "切换到 Shadow" });
    fireEvent.click(submit);
    await waitFor(() => expect(patchAttempts).toBe(1));
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(patchAttempts).toBe(2));

    expect(patchKeys[0]).toBeTruthy();
    expect(patchKeys[1]).toBe(patchKeys[0]);
  });

  it("scrolls the opened mode panel into the Drawer viewport before focusing it", async () => {
    installApi("shadow", true);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );
    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));

    await waitFor(() =>
      expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({
        block: "start",
        inline: "nearest",
      }),
    );
  });

  it("submits approval for shadow-to-enforced, deep-links the request, and never exposes tickets", async () => {
    const calls = installApi("shadow", true);
    window.history.replaceState(null, "", "/enterprise/workspaces?workspace=workspace-prod");
    const consoleLog = vi.spyOn(console, "log").mockImplementation(() => undefined);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const dialog = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(dialog).getByRole("radio", { name: "强制执行" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "Shadow 观察通过" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "提交审批申请" }));

    expect(await screen.findByText("审批申请已提交")).toBeTruthy();
    expect(screen.getByText(/request-workspace-17/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("opaque-ticket-never-render");
    expect(JSON.stringify({ ...localStorage })).not.toContain("opaque-ticket-never-render");
    expect(consoleLog).not.toHaveBeenCalled();
    expect(calls.some((call) => call.init.method === "PATCH")).toBe(false);

    const approvalCall = calls.find(
      (call) =>
        call.url.includes("/api/enterprise/approvals/requests") && call.init.method === "POST",
    );
    const body = JSON.parse(String(approvalCall?.init.body));
    expect(body).toMatchObject({
      policy_id: "approval-policy-workspace",
      resource_type: "tenant_workspace",
      resource_id: "workspace-prod",
      reason: "Shadow 观察通过",
      snapshot: {
        workspace_id: "workspace-prod",
        workspace_revision: 7,
        policy_revision: 3,
        from_mode: "shadow",
        target_mode: "enforced",
        permission_model_version: 1,
        permission_matrix_fingerprint: VALID_FINGERPRINT,
        reason: "Shadow 观察通过",
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "前往审批中心" }));
    expect(window.location.pathname + window.location.search).toBe(
      "/enterprise/approvals?request=request-workspace-17",
    );
    consoleLog.mockRestore();
  });

  it("uses the existing hash approval route helper for request deep links", async () => {
    installApi("shadow", true);
    window.history.replaceState(null, "", "/");
    window.location.hash = "/enterprise/workspaces";
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(panel).getByRole("radio", { name: "强制执行" }));
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "Hash 路由审批申请" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "提交审批申请" }));
    await screen.findByText("审批申请已提交");

    fireEvent.click(screen.getByRole("button", { name: "前往审批中心" }));
    expect(window.location.pathname).toBe("/");
    expect(window.location.hash).toBe("#/enterprise/approvals?request=request-workspace-17");
  });

  it("maps a server revision conflict instead of replacing it with a generic failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(impact);
        if (url.endsWith("/authorization") && init.method === "PATCH") {
          return jsonResponse(
            {
              detail: {
                code: "workspace_authorization_policy_revision_conflict",
                message: "Policy revision 已变化",
              },
            },
            409,
          );
        }
        if (url.endsWith("/authorization")) return jsonResponse(policy("shadow", false));
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(panel).getByRole("radio", { name: "已停用" }));
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "申请停用授权" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "停用授权" }));

    expect(
      await within(panel).findByText("Policy revision 已变化，请刷新 Workspace 授权事实后重试。"),
    ).toBeTruthy();
  });

  it("focuses the non-modal panel, isolates Escape, restores the trigger, and associates reason errors", async () => {
    installApi("shadow", false);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    const trigger = screen.getByRole("button", { name: "变更 Workspace 授权模式" });
    fireEvent.click(trigger);
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    const title = within(panel).getByRole("heading", { name: "变更 Workspace 授权模式" });

    await waitFor(() => expect(document.activeElement).toBe(title));
    expect(panel.getAttribute("aria-modal")).toBe("false");

    fireEvent.keyDown(panel, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "变更 Workspace 授权模式" })).toBeNull(),
    );
    expect(document.activeElement).toBe(trigger);

    fireEvent.click(trigger);
    const reopened = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    const submit = within(reopened).getByRole("button", { name: "切换到 Shadow" });
    fireEvent.click(submit);

    const reason = within(reopened).getByRole("textbox", { name: "变更原因" });
    expect(reason.getAttribute("aria-invalid")).toBe("true");
    const describedBy = reason.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain("请输入变更原因");
    expect(document.activeElement).toBe(reason);
  });

  it("disables high-risk submission when authority evidence is missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(impact);
        if (url.endsWith("/authorization") && init.method !== "PATCH") {
          const current = policy("shadow", true);
          return jsonResponse({
            ...current,
            evidence: { ...current.evidence, permission_matrix_fingerprint: null },
          });
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(panel).getByRole("radio", { name: "强制执行" }));
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "需要启用强制授权" },
    });

    const submit = within(panel).getByRole("button", { name: "提交审批申请" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    expect(
      within(panel).getByText("缺少 permission matrix fingerprint，无法提交高风险变更。"),
    ).toBeTruthy();
  });

  it("reuses the approval idempotency key and blocks a second successful submission", async () => {
    const calls = installApi("shadow", true);
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(panel).getByRole("radio", { name: "强制执行" }));
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "一次性申请强制执行" },
    });
    const submit = within(panel).getByRole("button", { name: "提交审批申请" });
    fireEvent.click(submit);
    await screen.findByText("审批申请已提交");

    const firstApprovalCall = calls.find(
      (call) =>
        call.url.includes("/api/enterprise/approvals/requests") && call.init.method === "POST",
    );
    expect(firstApprovalCall?.init.headers).toMatchObject({
      "Idempotency-Key": expect.any(String),
    });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.click(submit);
    expect(
      calls.filter(
        (call) =>
          call.url.includes("/api/enterprise/approvals/requests") && call.init.method === "POST",
      ),
    ).toHaveLength(1);
  });

  it("reuses the same approval idempotency key after a transient approval error", async () => {
    let approvalAttempts = 0;
    const keys: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(impact);
        if (url.endsWith("/authorization")) return jsonResponse(policy("shadow", true));
        if (url.includes("/api/enterprise/approvals/requests") && init.method === "POST") {
          const headers = init.headers as Record<string, string>;
          keys.push(headers["Idempotency-Key"]);
          approvalAttempts += 1;
          if (approvalAttempts === 1) {
            return jsonResponse(
              {
                detail: {
                  code: "workspace_authorization_unavailable",
                  message: "审批服务暂时不可用",
                },
              },
              503,
            );
          }
          return jsonResponse({
            request: approvalRequest(),
            execution: { ticket: "never-render" },
          });
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("Shadow 观察");
    fireEvent.click(screen.getByRole("button", { name: "变更 Workspace 授权模式" }));
    const panel = screen.getByRole("dialog", { name: "变更 Workspace 授权模式" });
    fireEvent.click(within(panel).getByRole("radio", { name: "强制执行" }));
    fireEvent.change(within(panel).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "服务恢复后重试审批" },
    });
    const submit = within(panel).getByRole("button", { name: "提交审批申请" });
    fireEvent.click(submit);
    await within(panel).findByText("Workspace 授权服务暂不可用，请稍后重试。");
    fireEvent.click(submit);
    await screen.findByText("审批申请已提交");

    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[0]).toBe(keys[1]);
  });

  it("shows disabled current permissions without presenting workspace candidates as effective", async () => {
    const disabledImpact = {
      impact: {
        ...impact.impact,
        state: "workspace_authorization_disabled",
        candidate_permissions: ["knowledge.write"],
        would_grant_permissions: ["knowledge.write"],
        granted_permissions: [],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(disabledImpact);
        if (url.endsWith("/authorization")) return jsonResponse(policy("disabled", false));
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("已停用");
    expect(screen.getAllByText("Workspace candidate：未启用").length).toBeGreaterThan(0);
    expect(screen.getAllByText("权限增量：未启用").length).toBeGreaterThan(0);
  });

  it("shows enforced permissions from actual contributing workspaces and granted delta", async () => {
    const enforcedImpact = {
      impact: {
        ...impact.impact,
        state: "workspace_authorization_enforced",
        candidate_permissions: ["knowledge.read", "knowledge.write"],
        would_grant_permissions: ["knowledge.write"],
        granted_permissions: ["knowledge.write"],
        contributing_workspaces: [
          {
            workspace_id: "workspace-prod",
            workspace_name: "生产知识域",
            role: "owner",
            binding_kind: "primary",
            policy_mode: "enforced",
            policy_revision: 3,
            permissions: ["knowledge.read", "knowledge.write"],
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/authorization/impact")) return jsonResponse(enforcedImpact);
        if (url.endsWith("/authorization")) return jsonResponse(policy("enforced", false));
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    render(
      <WorkspacePermissionsRolloutCenter
        scope={scope}
        context={context}
        workspace={workspace}
        legacyAuthorizationState="workspace_authorization_not_enforced"
      />,
    );

    await screen.findByText("强制执行");
    expect(screen.getAllByText(/权限增量（granted）：knowledge.write/).length).toBeGreaterThan(0);
    expect(screen.getByText("permissions: knowledge.read / knowledge.write")).toBeTruthy();
  });
});
