// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationCenterProps } from "./enterprise-notification-center/components/NotificationCenter";
import type { NotificationDrawerProps } from "./enterprise-notification-center/components/NotificationDrawer";

const enterpriseIdentityApi = vi.hoisted(() => ({ fetchEnterpriseContext: vi.fn() }));
const notificationHook = vi.hoisted(() => ({ useEnterpriseNotifications: vi.fn() }));

vi.mock("./enterprise-admin/api/enterpriseAdminApi", () => enterpriseIdentityApi);
vi.mock(
  "./enterprise-notification-center/hooks/useEnterpriseNotifications",
  () => notificationHook,
);
vi.mock("./enterprise-notification-center/components/NotificationDrawer", () => ({
  default: ({ visible, onClose, onHandoff }: NotificationDrawerProps) =>
    visible ? (
      <section aria-label="全局通知抽屉">
        <button
          onClick={() =>
            onHandoff?.({
              notificationId: "notification-quality-001",
              sourceKind: "quality_alert",
              route: {
                code: "knowledge_quality_operations",
                path: "/enterprise/knowledge-base",
                query: { dataset: "dataset-a", section: "releases", alert: "alert-a" },
                href: "/must-not-be-trusted",
              },
            })
          }
        >
          质量通知安全交接
        </button>
        <button onClick={onClose}>关闭通知抽屉</button>
      </section>
    ) : null,
}));
vi.mock("./enterprise-notification-center/components/NotificationCenter", () => ({
  default: ({ onHandoff }: NotificationCenterProps) => (
    <section aria-label="消息中心页面">
      <button
        onClick={() =>
          onHandoff?.({
            notificationId: "notification-approval-001",
            sourceKind: "approval_pending_for_me",
            route: {
              code: "enterprise_approval",
              path: "/enterprise/approvals",
              query: { request: "request-a" },
              href: "/must-not-be-trusted",
            },
          })
        }
      >
        审批通知安全交接
      </button>
    </section>
  ),
}));
vi.mock("./enterprise-notification-center/components/NotificationDetailDrawer", () => ({
  default: () => null,
}));
vi.mock("./enterprise-content-recovery/ContentRecoveryPage", () => ({
  default: ({
    capabilityReady,
    onApprovalHandoff,
  }: {
    capabilityReady: boolean;
    onApprovalHandoff?: (id: string) => void;
  }) => (
    <section aria-label="企业内容恢复页面">
      <span>{capabilityReady ? "恢复权威可用" : "恢复权威不可用"}</span>
      <button onClick={() => onApprovalHandoff?.("approval-stage23")}>恢复清除审批交接</button>
    </section>
  ),
}));
vi.mock("./context/ConnectionContext", () => ({
  useConnection: () => ({
    online: true,
    checking: false,
    health: { status: "ok" },
    refresh: vi.fn(),
  }),
}));
vi.mock("./enterprise-workspace", () => ({
  useEnterpriseWorkspaceSelector: () => ({
    value: "workspace-a",
    status: "ready",
    workspaces: [],
    setValue: vi.fn(),
  }),
}));
vi.mock("./components/ModeBanner", () => ({ default: () => null }));
vi.mock("./components/ErrorBoundary", () => ({
  default: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("./pages/QueryPage", () => ({ default: () => <section aria-label="问答页面" /> }));
vi.mock("./pages/KnowledgeOverviewPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeGovernancePage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/DocumentsPage", () => ({ default: () => <section /> }));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConsistencyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EnterpriseAdminPage", () => ({
  default: () => <section aria-label="企业管理页面" />,
}));
vi.mock("./pages/EnterpriseKnowledgeBasePage", () => ({ default: () => <section /> }));
vi.mock("./pages/EnterpriseKnowledgeBaseWorkspacePage", () => ({
  default: () => <section aria-label="知识库工作区页面" />,
}));

import App from "./App";

const controller = {
  active: true,
  load: { status: "ready", error: null, reload: vi.fn() },
  summary: {
    status: "ready",
    value: {
      state: "ready",
      tenant_id: "tenant-a",
      account_id: "account-a",
      unread_count: 124,
      unread_state: "count",
      as_of: "2026-08-29T07:30:00Z",
      reason_code: null,
    },
    error: null,
  },
  unread: { status: "ready", items: [], nextCursor: null, invalidItemCount: 0, error: null },
  history: {
    status: "idle",
    items: [],
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
    load: vi.fn(),
  },
  subscriptions: {
    status: "idle",
    items: [],
    nextCursor: null,
    invalidItemCount: 0,
    error: null,
    load: vi.fn(),
  },
  detail: { status: "idle", value: null, error: null, load: vi.fn() },
  mutation: {
    status: "idle",
    outcome: null,
    error: null,
    markRead: vi.fn(),
    markUnread: vi.fn(),
    archive: vi.fn(),
    bulkRead: vi.fn(),
    updateSubscription: vi.fn(),
    retry: vi.fn(),
  },
};

function installDesktopMedia() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      media: "",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function authenticate(capabilityState: "ready" | "limited" = "ready") {
  localStorage.setItem("rag4c.knowledge_actor_token", "actor-token");
  localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-a");
  localStorage.setItem("rag4c.knowledge_dataset_id", "dataset-a");
  enterpriseIdentityApi.fetchEnterpriseContext.mockResolvedValue({
    tenant: {
      id: "tenant-a",
      name: "星海科技",
      plan: "enterprise",
      status: "active",
      quota_documents: 100,
      quota_chunks: 1000,
      doc_count: 8,
      chunk_count: 80,
    },
    actor: { id: "account-a", name: "林澈", email: "lin@example.com", role: "admin" },
    member_count: 4,
    dataset_count: 2,
    effective_permissions: ["knowledge.read"],
    role_permissions: { admin: ["knowledge.read"] },
    capabilities: {
      enterprise_notification_center: {
        state: capabilityState,
        label: "Enterprise Notification Center",
        reason: capabilityState === "ready" ? null : "schema unavailable",
      },
      enterprise_content_recovery: {
        state: capabilityState,
        label: "Enterprise Content Recovery",
        reason: capabilityState === "ready" ? null : "schema unavailable",
      },
    },
  });
}

beforeEach(() => {
  localStorage.clear();
  enterpriseIdentityApi.fetchEnterpriseContext.mockReset();
  notificationHook.useEnterpriseNotifications.mockReset();
  notificationHook.useEnterpriseNotifications.mockReturnValue(controller);
  window.history.replaceState(null, "", "/query");
  installDesktopMedia();
});

afterEach(cleanup);

describe("App Notification Center integration", () => {
  it("keeps the Bell hidden until both capability and exact summary authority are ready", async () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /通知中心/ })).toBeNull();
    expect(notificationHook.useEnterpriseNotifications).toHaveBeenCalledWith(
      expect.objectContaining({ actorToken: "" }),
      expect.objectContaining({ enabled: false }),
    );

    cleanup();
    authenticate("limited");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);
    await waitFor(() => expect(enterpriseIdentityApi.fetchEnterpriseContext).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /通知中心/ })).toBeNull();
  });

  it("shows the exact accessible count, caps only the visual badge, and opens the global Drawer", async () => {
    authenticate();
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    const bell = await screen.findByRole("button", { name: /124.*未读/ });
    expect(screen.getByTestId("notification-bell-badge").textContent).toContain("99+");
    fireEvent.click(bell);
    expect(await screen.findByRole("region", { name: "全局通知抽屉" })).toBeTruthy();
  });

  it("mounts the enterprise recycle-bin route and safely hands purge approval to Approval Center", async () => {
    authenticate();
    window.history.replaceState(null, "", "/enterprise/recycle-bin");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "企业内容恢复页面" })).toBeTruthy();
    expect(screen.getByText("恢复权威可用")).toBeTruthy();
    expect(screen.getByText("回收站")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "恢复清除审批交接" }));
    expect(window.location.pathname).toBe("/enterprise/approvals");
    expect(window.location.search).toBe("?request=approval-stage23");
  });

  it("mounts the internal route and rebuilds safe Quality and Approval handoff URLs", async () => {
    authenticate();
    window.history.replaceState(null, "", "/enterprise/notifications");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "消息中心页面" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "审批通知安全交接" }));
    expect(window.location.pathname).toBe("/enterprise/approvals");
    expect(window.location.search).toBe("?request=request-a");

    fireEvent.click(await screen.findByRole("button", { name: /124.*未读/ }));
    fireEvent.click(await screen.findByRole("button", { name: "质量通知安全交接" }));
    expect(window.location.pathname).toBe("/enterprise/knowledge-base");
    expect(window.location.search).toBe("?dataset=dataset-a&section=releases&alert=alert-a");
  });
});
