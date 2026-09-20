/* eslint-disable @typescript-eslint/no-explicit-any -- compatibility callback types during TDesign migration */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Layout, Menu, Tooltip } from "./ui/index";
import type { MenuProps } from "./ui/index";
import {
  ApartmentOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  DeleteOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  LineChartOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  MoonOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TagsOutlined,
  TeamOutlined,
  SunOutlined,
  InfoCircleOutlined,
} from "./ui/icons";
import ModeBanner from "./components/ModeBanner";
import PageState from "./components/PageState";
import ErrorBoundary from "./components/ErrorBoundary";
import QueryPage from "./pages/QueryPage";
import { useConnection } from "./context/ConnectionContext";
import type { ThemeMode } from "./theme/tokens";
import { OnboardingTour } from "./onboarding/OnboardingTour";
import {
  PAGE_KEYS,
  mainNavigationKey,
  navigationIntent,
  parsePageLocation,
  type PageKey,
} from "./run/appRoute";
import {
  safeAutomationHandoff,
  safeKnowledgeServingHandoff,
  safeTaskHandoff,
} from "./run/appHandoffs";
import {
  readKnowledgeActorToken,
  readKnowledgeDatasetIdFromLocation,
  resolveKnowledgeWorkspaceScope,
} from "./knowledge/workspaceScope";
import { useOptionalKnowledgeWorkspace } from "./knowledge/KnowledgeWorkspaceContext";
import { WorkspaceScopeBar } from "./ui/enterprise";
import { fetchEnterpriseContext } from "./enterprise-admin/api/enterpriseAdminApi";
import type { EnterpriseContext } from "./enterprise-admin/model";
import { useEnterpriseWorkspaceSelector } from "./enterprise-workspace";
import KnowledgeBaseResourceShell from "./enterprise-knowledge-base-shell/KnowledgeBaseResourceShell";
import KnowledgeServingPage from "./enterprise-knowledge-base-shell/KnowledgeServingPageLoader";
import { useEnterpriseNotifications } from "./enterprise-notification-center/hooks/useEnterpriseNotifications";
import NotificationBell from "./enterprise-notification-center/components/NotificationBell";
import type { NotificationHandoffContext } from "./enterprise-notification-center/components/notificationCenterShared";
import type { TaskRoute } from "./enterprise-task-operations/model/taskModel";
import type { AutomationRoute } from "./enterprise-automation-workflows/model/automationModel";
import type {
  NotificationDetail,
  NotificationInboxItem,
} from "./enterprise-notification-center/model/notificationModel";
import { notificationHandoffTarget } from "./enterprise-notification-center/notificationNavigation";
import {
  knowledgeBaseResourceDatasetIdFromLocation,
  knowledgeBaseResourceNavigationUrl,
  knowledgeBaseResourcePageKey,
  knowledgeBaseResourceSectionFromLocation,
  type KnowledgeBaseResourceSection,
} from "./enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";

const KnowledgeOverviewPage = lazy(() => import("./pages/KnowledgeOverviewPage"));
const DocumentsPage = lazy(() => import("./pages/DocumentsPage"));
const KnowledgeTaxonomyPage = lazy(() => import("./pages/KnowledgeTaxonomyPage"));
const KnowledgeGovernancePage = lazy(() => import("./pages/KnowledgeGovernancePage"));
const KnowledgeSourcesPage = lazy(() => import("./pages/KnowledgeSourcesPage"));
const RetrievalLabPage = lazy(() => import("./pages/RetrievalLabPage"));
const VisualizePage = lazy(() => import("./pages/VisualizePage"));
const MonitorPage = lazy(() => import("./pages/MonitorPage"));
const ConsistencyPage = lazy(() => import("./pages/ConsistencyPage"));
const EvalPage = lazy(() => import("./pages/EvalPage"));
const ConfigPage = lazy(() => import("./pages/ConfigPage"));
const EnterpriseAdminPage = lazy(() => import("./pages/EnterpriseAdminPage"));
const EnterpriseKnowledgeBasePage = lazy(() => import("./pages/EnterpriseKnowledgeBasePage"));
const EnterpriseKnowledgeBaseWorkspacePage = lazy(
  () => import("./pages/EnterpriseKnowledgeBaseWorkspacePage"),
);
const NotificationCenter = lazy(
  () => import("./enterprise-notification-center/components/NotificationCenter"),
);
const NotificationDrawer = lazy(
  () => import("./enterprise-notification-center/components/NotificationDrawer"),
);
const NotificationDetailDrawer = lazy(
  () => import("./enterprise-notification-center/components/NotificationDetailDrawer"),
);
const EnterpriseContentRecoveryPage = lazy(
  () => import("./enterprise-content-recovery/ContentRecoveryPage"),
);
const TaskOperationsPage = lazy(() => import("./enterprise-task-operations/TaskOperationsPage"));
const AutomationPage = lazy(() => import("./enterprise-automation-workflows/AutomationPage"));

const ENTERPRISE_ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  member: "成员",
};

const { Sider } = Layout;
const PRIMARY_NAV_ID = "primary-navigation";

const MENU_ITEMS: MenuProps["items"] = [
  {
    type: "group",
    label: "智能问答",
    children: [
      { key: "query", icon: <MessageOutlined />, label: "知识问答" },
      { key: "visualize", icon: <ApartmentOutlined />, label: "回答过程" },
      { key: "retrieval-lab", icon: <FileSearchOutlined />, label: "检索调试" },
    ],
  },
  {
    type: "group",
    label: "知识库",
    children: [
      { key: "overview", icon: <DatabaseOutlined />, label: "知识概览" },
      { key: "documents", icon: <FolderOpenOutlined />, label: "文档管理" },
      { key: "taxonomy", icon: <TagsOutlined />, label: "知识组织" },
      { key: "sources", icon: <CloudServerOutlined />, label: "数据来源" },
      { key: "knowledge-bases", icon: <ApartmentOutlined />, label: "知识库注册表" },
      { key: "recycle-bin", icon: <DeleteOutlined />, label: "回收站" },
    ],
  },
  {
    type: "group",
    label: "质量与运维",
    children: [
      { key: "eval", icon: <SafetyCertificateOutlined />, label: "质量评测" },
      { key: "monitor", icon: <LineChartOutlined />, label: "运行监控" },
      { key: "consistency", icon: <BranchesOutlined />, label: "一致性控制台" },
      { key: "governance", icon: <DeploymentUnitOutlined />, label: "内容治理" },
      { key: "tasks", icon: <CloudServerOutlined />, label: "任务中心" },
      { key: "automations", icon: <BranchesOutlined />, label: "自动化中心" },
    ],
  },
  {
    type: "group",
    label: "系统",
    children: [
      { key: "enterprise", icon: <TeamOutlined />, label: "组织与权限" },
      { key: "config", icon: <SettingOutlined />, label: "系统设置" },
    ],
  },
];

interface Props {
  themeMode: ThemeMode;
  onToggleTheme: () => void;
}

export default function App({ themeMode, onToggleTheme }: Props) {
  const { online, checking, health, refresh } = useConnection();
  const knowledgeWorkspace = useOptionalKnowledgeWorkspace();
  const workspaceSyncRef = useRef<string>("");
  const [page, setPage] = useState<PageKey>(() => parsePageLocation(window.location) ?? "query");
  const [collapsed, setCollapsed] = useState(false);
  // 手动重开新手引导（侧边栏底部按钮触发）
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [acl, setAcl] = useState<string[]>([]);
  const [mountedPages, setMountedPages] = useState<PageKey[]>(() => [page]);
  const [documentsWorkspaceDirty, setDocumentsWorkspaceDirty] = useState(false);
  const [routeRevision, setRouteRevision] = useState(0);
  const [enterpriseIdentity, setEnterpriseIdentity] = useState<EnterpriseContext | null>(null);
  const [notificationDrawerOpen, setNotificationDrawerOpen] = useState(false);
  const [notificationDetailOpen, setNotificationDetailOpen] = useState(false);
  const notificationReturnFocusRef = useRef<HTMLElement | null>(null);
  const notificationDetailReturnFocusRef = useRef<HTMLElement | null>(null);
  const [enterpriseIdentityStatus, setEnterpriseIdentityStatus] = useState<
    "none" | "loading" | "ready" | "unavailable"
  >("none");

  useEffect(() => {
    setMountedPages((prev) => (prev.includes(page) ? prev : [...prev, page]));
  }, [page]);

  useEffect(() => {
    const onHashChange = () => {
      const key = parsePageLocation(window.location);
      if (key) setPage(key);
      setRouteRevision((current) => current + 1);
    };
    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("popstate", onHashChange);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
      window.removeEventListener("popstate", onHashChange);
    };
  }, []);

  const navigate = useCallback(
    (key: PageKey) => {
      if (
        page === "documents" &&
        key !== "documents" &&
        documentsWorkspaceDirty &&
        !window.confirm("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？")
      )
        return;
      if (page === "documents" && key !== "documents") setDocumentsWorkspaceDirty(false);
      setPage(key);
      setMobileNavOpen(false);
      setNotificationDrawerOpen(false);
      setNotificationDetailOpen(false);
      if (parsePageLocation(window.location) !== key) {
        const intent = navigationIntent(window.location, key);
        if (intent.mode === "history") {
          window.history.pushState(window.history.state, "", intent.url);
          window.dispatchEvent(new PopStateEvent("popstate"));
        } else window.location.hash = intent.url;
      }
    },
    [documentsWorkspaceDirty, page],
  );

  const handleGlobalSearch = useCallback((value: string) => {
    const query = value.trim();
    if (!query) return;
    const intent = navigationIntent(window.location, "documents");
    const url = `${intent.url}?q=${encodeURIComponent(query)}`;
    setPage("documents");
    setMobileNavOpen(false);
    if (intent.mode === "history") {
      window.history.pushState(window.history.state, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } else {
      window.location.hash = url;
    }
  }, []);

  const actorToken = readKnowledgeActorToken();
  const workspaceScope = resolveKnowledgeWorkspaceScope({ actorToken });
  const verifiedDatasetId = knowledgeWorkspace
    ? knowledgeWorkspace.workspaceScopeStatus === "verified"
      ? knowledgeWorkspace.datasetId
      : ""
    : workspaceScope.datasetId;
  const enterpriseWorkspaceSelector = useEnterpriseWorkspaceSelector(
    {
      tenantId: workspaceScope.tenantId,
      datasetId: verifiedDatasetId,
      actorToken: actorToken ?? "",
    },
    Boolean(actorToken),
  );
  const enterpriseWorkspaceOptions = enterpriseWorkspaceSelector.workspaces.map((workspace) => ({
    value: workspace.id,
    label: workspace.name,
    revision: workspace.revision,
    environment:
      workspace.environment === "production"
        ? "生产"
        : workspace.environment === "testing"
          ? "测试"
          : workspace.environment === "development"
            ? "开发"
            : workspace.environment,
  }));

  const handleWorkspaceChange = useCallback(
    (workspaceId: string) => {
      const normalized = workspaceId.trim();
      if (!normalized) return;
      if (!knowledgeWorkspace) {
        enterpriseWorkspaceSelector.setValue(normalized);
        return;
      }
      void knowledgeWorkspace.selectWorkspace(normalized).then((result) => {
        if (result.status === "verified" || result.status === "no_primary_knowledge_base") {
          enterpriseWorkspaceSelector.setValue(normalized);
        }
      });
    },
    [enterpriseWorkspaceSelector, knowledgeWorkspace],
  );

  useEffect(() => {
    if (readKnowledgeDatasetIdFromLocation(window.location)) return;
    const selectedWorkspaceId = enterpriseWorkspaceSelector.value.trim();
    if (
      !knowledgeWorkspace ||
      enterpriseWorkspaceSelector.status !== "ready" ||
      !selectedWorkspaceId ||
      workspaceSyncRef.current === selectedWorkspaceId ||
      knowledgeWorkspace.workspaceId === selectedWorkspaceId
    ) {
      return;
    }
    workspaceSyncRef.current = selectedWorkspaceId;
    void knowledgeWorkspace.selectWorkspace(selectedWorkspaceId).then((result) => {
      if (result.status === "unavailable") workspaceSyncRef.current = "";
    });
  }, [enterpriseWorkspaceSelector.status, enterpriseWorkspaceSelector.value, knowledgeWorkspace]);

  useEffect(() => {
    if (!actorToken) {
      setEnterpriseIdentity(null);
      setEnterpriseIdentityStatus("none");
      return;
    }
    setEnterpriseIdentityStatus("loading");
    const controller = new AbortController();
    void fetchEnterpriseContext(
      {
        tenantId: workspaceScope.tenantId,
        datasetId: verifiedDatasetId,
        actorToken,
      },
      { signal: controller.signal },
    )
      .then((context) => {
        setEnterpriseIdentity(context);
        setEnterpriseIdentityStatus("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setEnterpriseIdentity(null);
          setEnterpriseIdentityStatus("unavailable");
        }
      });
    return () => controller.abort();
  }, [actorToken, verifiedDatasetId, workspaceScope.tenantId]);

  const organizationLabel =
    enterpriseIdentity?.tenant.name ||
    (workspaceScope.tenantId === "default" ? "RAG4C 工作区" : workspaceScope.tenantId);
  const activeDatasetId = verifiedDatasetId;
  const knowledgeBaseLabel = knowledgeWorkspace
    ? knowledgeWorkspace.workspaceScopeStatus === "no_primary_knowledge_base"
      ? "未选择主知识库"
      : knowledgeWorkspace.workspaceScopeStatus === "unavailable"
        ? "工作区范围不可用"
        : knowledgeWorkspace.workspaceScopeStatus !== "verified"
          ? "等待主知识库验证"
          : activeDatasetId || "未返回主知识库"
    : activeDatasetId === "default"
      ? "默认知识库"
      : activeDatasetId;
  const actorLabel =
    enterpriseIdentity?.actor.name ||
    (actorToken
      ? enterpriseIdentityStatus === "loading"
        ? "身份验证中"
        : "身份暂不可用"
      : "未连接身份");
  const actorRole = enterpriseIdentity
    ? ENTERPRISE_ROLE_LABELS[enterpriseIdentity.actor.role] || enterpriseIdentity.actor.role
    : actorToken
      ? enterpriseIdentityStatus === "loading"
        ? "企业身份"
        : "上下文不可用"
      : online === true
        ? "本地模式"
        : "演示模式";
  const notificationsCapabilityReady =
    Boolean(actorToken) &&
    enterpriseIdentity?.tenant.id === workspaceScope.tenantId &&
    enterpriseIdentity.capabilities.enterprise_notification_center?.state === "ready";
  const notificationsReadOnly =
    online !== true || health?.status === "degraded" || health?.status === "down";
  const recoveryCapabilityReady =
    Boolean(actorToken) &&
    enterpriseIdentity?.tenant.id === workspaceScope.tenantId &&
    enterpriseIdentity.capabilities.enterprise_content_recovery?.state === "ready";
  const recoveryReadOnly =
    online !== true || health?.status === "degraded" || health?.status === "down";
  const taskOperationsCapabilityReady =
    Boolean(actorToken) &&
    enterpriseIdentity?.tenant.id === workspaceScope.tenantId &&
    enterpriseIdentity.capabilities.enterprise_task_operations?.state === "ready";
  const taskOperationsReadOnly =
    online !== true || health?.status === "degraded" || health?.status === "down";
  const automationCapabilityReady =
    Boolean(actorToken) &&
    enterpriseIdentity?.tenant.id === workspaceScope.tenantId &&
    enterpriseIdentity.capabilities.enterprise_automation_workflows?.state === "ready";
  const automationReadOnly =
    online !== true || health?.status === "degraded" || health?.status === "down";
  const knowledgeServingCapabilityReady =
    Boolean(actorToken) &&
    enterpriseIdentity?.tenant.id === workspaceScope.tenantId &&
    enterpriseIdentity.capabilities.enterprise_knowledge_serving_reliability?.state === "ready";
  const knowledgeServingReadOnly =
    online !== true ||
    health?.status === "degraded" ||
    health?.status === "down" ||
    enterpriseIdentity?.effective_permissions.includes("knowledge.manage") !== true;
  const notificationsController = useEnterpriseNotifications(
    { tenantId: workspaceScope.tenantId, actorToken: actorToken ?? "" },
    { enabled: notificationsCapabilityReady, readOnly: notificationsReadOnly },
  );

  const handleOpenNotifications = useCallback(() => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      notificationReturnFocusRef.current = document.activeElement;
    }
    setNotificationDrawerOpen(true);
  }, []);

  const handleOpenNotificationDetail = useCallback(
    (item: NotificationInboxItem) => {
      if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
        notificationDetailReturnFocusRef.current = document.activeElement;
      }
      setNotificationDetailOpen(true);
      void notificationsController.detail.load(item.notification.id);
    },
    [notificationsController.detail],
  );

  const handleNotificationHandoff = useCallback((context: NotificationHandoffContext) => {
    const target = notificationHandoffTarget(context);
    if (!target) return;
    setNotificationDrawerOpen(false);
    setNotificationDetailOpen(false);
    setPage(target.page);
    setMobileNavOpen(false);
    const mode = navigationIntent(window.location, target.page).mode;
    if (mode === "history") {
      window.history.pushState(window.history.state, "", target.url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } else {
      window.location.hash = target.url;
    }
  }, []);

  const handleNotificationMarkRead = useCallback(
    (detail: NotificationDetail) => {
      if (notificationsReadOnly) return;
      void notificationsController.mutation.markRead(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: "通知中心显式标记为已读",
      });
    },
    [notificationsController.mutation, notificationsReadOnly],
  );

  const handleNotificationMarkUnread = useCallback(
    (detail: NotificationDetail) => {
      if (notificationsReadOnly) return;
      void notificationsController.mutation.markUnread(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: "通知中心显式标记为未读",
      });
    },
    [notificationsController.mutation, notificationsReadOnly],
  );

  const handleNotificationArchive = useCallback(
    (detail: NotificationDetail) => {
      if (notificationsReadOnly) return;
      void notificationsController.mutation.archive(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: "通知中心显式归档通知",
      });
    },
    [notificationsController.mutation, notificationsReadOnly],
  );
  const handleRecoveryApprovalHandoff = useCallback((approvalRequestId: string) => {
    const normalized = approvalRequestId.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(normalized)) return;
    setPage("enterprise");
    setMobileNavOpen(false);
    const url = `/enterprise/approvals?request=${encodeURIComponent(normalized)}`;
    const mode = navigationIntent(window.location, "enterprise").mode;
    if (mode === "history") {
      window.history.pushState(window.history.state, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } else {
      window.location.hash = url;
    }
  }, []);
  const handleTaskHandoff = useCallback((route: TaskRoute) => {
    const target = safeTaskHandoff(route);
    if (!target) return;
    setPage(target.page);
    setMobileNavOpen(false);
    const mode = navigationIntent(window.location, target.page).mode;
    if (mode === "history") {
      window.history.pushState(window.history.state, "", target.url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } else {
      window.location.hash = target.url;
    }
  }, []);
  const handleAutomationHandoff = useCallback((route: AutomationRoute) => {
    const target = safeAutomationHandoff(route);
    if (!target) return;
    setPage(target.page);
    setMobileNavOpen(false);
    const mode = navigationIntent(window.location, target.page).mode;
    if (mode === "history") {
      window.history.pushState(window.history.state, "", target.url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } else window.location.hash = target.url;
  }, []);

  const siderCollapsed = isMobile ? !mobileNavOpen : collapsed;
  const healthStatus = health?.status;
  const degraded = online === true && (healthStatus === "degraded" || healthStatus === "down");
  const connLabel =
    online === null
      ? "正在检查服务"
      : degraded
        ? "部分服务不可用"
        : online
          ? "服务已连接"
          : "演示模式";
  const connDotClass =
    online === null ? "processing" : degraded ? "warning" : online ? "online" : "warning";
  const connTip =
    online === null
      ? "正在探测后端服务"
      : degraded
        ? "基础问答可用，部分辅助能力暂不可用"
        : online
          ? "已连接后端服务，页面展示真实数据"
          : "后端服务未连接，页面展示演示数据";

  const resourceDatasetId = useMemo(() => {
    void routeRevision;
    return knowledgeBaseResourceDatasetIdFromLocation(window.location) ?? activeDatasetId;
  }, [activeDatasetId, routeRevision]);
  const workspaceResourceSection = useMemo(() => {
    void routeRevision;
    return knowledgeBaseResourceSectionFromLocation(window.location);
  }, [routeRevision]);
  const handleResourceSectionChange = useCallback(
    (section: KnowledgeBaseResourceSection, trigger: HTMLElement) => {
      if (
        page === "documents" &&
        section !== "documents" &&
        documentsWorkspaceDirty &&
        !window.confirm("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？")
      ) {
        window.setTimeout(() => trigger.focus(), 0);
        return false;
      }
      if (page === "documents" && section !== "documents") setDocumentsWorkspaceDirty(false);
      const nextPage = knowledgeBaseResourcePageKey(section);
      const intent = knowledgeBaseResourceNavigationUrl(window.location, {
        datasetId: resourceDatasetId,
        section,
      });
      setPage(nextPage);
      setMobileNavOpen(false);
      if (intent.mode === "history") {
        window.history.pushState(window.history.state, "", intent.url);
        window.dispatchEvent(new PopStateEvent("popstate"));
      } else {
        window.location.hash = intent.url;
      }
      return true;
    },
    [documentsWorkspaceDirty, page, resourceDatasetId],
  );
  const handleKnowledgeServingHandoff = useCallback(
    (route: unknown) => {
      const target = safeKnowledgeServingHandoff(route, resourceDatasetId, window.location);
      if (!target) return;
      setPage(target.page);
      setMobileNavOpen(false);
      if (target.mode === "history") {
        window.history.pushState(window.history.state, "", target.url);
        window.dispatchEvent(new PopStateEvent("popstate"));
      } else {
        window.location.hash = target.url;
      }
    },
    [resourceDatasetId],
  );

  const pageNodes: Record<PageKey, JSX.Element> = {
    overview: <KnowledgeOverviewPage />,
    query: <QueryPage acl={acl} onAclChange={setAcl} />,
    documents: (
      <KnowledgeBaseResourceShell
        active={page === "documents"}
        section="documents"
        onSectionChange={handleResourceSectionChange}
      >
        <DocumentsPage
          active={page === "documents"}
          embedded
          onDirtyChange={setDocumentsWorkspaceDirty}
          contentRecoveryCapabilityReady={recoveryCapabilityReady}
          contentRecoveryReadOnly={recoveryReadOnly}
        />
      </KnowledgeBaseResourceShell>
    ),
    "recycle-bin": (
      <EnterpriseContentRecoveryPage
        tenantId={workspaceScope.tenantId}
        actorToken={actorToken ?? ""}
        capabilityReady={recoveryCapabilityReady}
        tenantLabel={organizationLabel}
        readOnly={recoveryReadOnly}
        mobile={isMobile}
        onApprovalHandoff={handleRecoveryApprovalHandoff}
      />
    ),
    tasks: (
      <TaskOperationsPage
        tenantId={workspaceScope.tenantId}
        accountId={enterpriseIdentity?.actor.id}
        actorToken={actorToken ?? ""}
        capabilityReady={taskOperationsCapabilityReady}
        tenantLabel={organizationLabel}
        readOnly={taskOperationsReadOnly}
        mobile={isMobile}
        onTaskHandoff={handleTaskHandoff}
      />
    ),
    automations: (
      <AutomationPage
        tenantId={workspaceScope.tenantId}
        accountId={enterpriseIdentity?.actor.id}
        actorToken={actorToken ?? ""}
        capabilityReady={automationCapabilityReady}
        tenantLabel={organizationLabel}
        readOnly={automationReadOnly}
        mobile={isMobile}
        onAutomationHandoff={handleAutomationHandoff}
      />
    ),
    taxonomy: (
      <KnowledgeBaseResourceShell
        active={page === "taxonomy"}
        section="taxonomy"
        onSectionChange={handleResourceSectionChange}
      >
        <KnowledgeTaxonomyPage embedded />
      </KnowledgeBaseResourceShell>
    ),
    governance: (
      <KnowledgeBaseResourceShell
        active={page === "governance"}
        section="governance"
        onSectionChange={handleResourceSectionChange}
      >
        <KnowledgeGovernancePage embedded />
      </KnowledgeBaseResourceShell>
    ),
    sources: (
      <KnowledgeBaseResourceShell
        active={page === "sources"}
        section="sources"
        onSectionChange={handleResourceSectionChange}
      >
        <KnowledgeSourcesPage active={page === "sources"} embedded />
      </KnowledgeBaseResourceShell>
    ),
    "retrieval-lab": <RetrievalLabPage />,
    visualize: <VisualizePage />,
    eval: <EvalPage />,
    monitor: <MonitorPage active={page === "monitor"} />,
    consistency: <ConsistencyPage />,
    enterprise: <EnterpriseAdminPage />,
    notifications: notificationsCapabilityReady ? (
      <NotificationCenter
        controller={notificationsController}
        capabilityReady
        readOnly={notificationsReadOnly}
        mobile={isMobile}
        tenantLabel={organizationLabel}
        onHandoff={handleNotificationHandoff}
      />
    ) : (
      <div className="page-shell">
        <div className="page-shell-inner">
          <PageState
            status={enterpriseIdentityStatus === "loading" ? "loading" : "error"}
            title={
              enterpriseIdentityStatus === "loading"
                ? "正在读取通知中心权威"
                : "通知中心权威暂不可用"
            }
            description="仅在服务端能力与当前账号身份均可验证后开放通知权威。"
          />
        </div>
      </div>
    ),
    "knowledge-bases": (
      <EnterpriseKnowledgeBasePage
        scope={{
          tenantId: workspaceScope.tenantId,
          datasetId: activeDatasetId,
          actorToken: actorToken ?? "",
        }}
        context={enterpriseIdentity}
        workspaceOptions={enterpriseWorkspaceOptions}
      />
    ),
    "knowledge-base-workspace":
      workspaceResourceSection === "serving" ? (
        <KnowledgeBaseResourceShell
          active={page === "knowledge-base-workspace"}
          section="serving"
          tenantId={workspaceScope.tenantId}
          datasetId={resourceDatasetId}
          actorToken={actorToken ?? ""}
          onSectionChange={handleResourceSectionChange}
        >
          <KnowledgeServingPage
            active={page === "knowledge-base-workspace"}
            tenantId={workspaceScope.tenantId}
            accountId={enterpriseIdentity?.actor.id}
            actorToken={actorToken ?? ""}
            datasetId={resourceDatasetId}
            capabilityReady={knowledgeServingCapabilityReady}
            tenantLabel={organizationLabel}
            readOnly={knowledgeServingReadOnly}
            mobile={isMobile}
            onServingHandoff={handleKnowledgeServingHandoff}
          />
        </KnowledgeBaseResourceShell>
      ) : (
        <EnterpriseKnowledgeBaseWorkspacePage
          active={page === "knowledge-base-workspace"}
          section={workspaceResourceSection}
          tenantId={workspaceScope.tenantId}
          datasetId={resourceDatasetId}
          actorToken={actorToken ?? ""}
          onSectionChange={handleResourceSectionChange}
        />
      ),
    config: <ConfigPage />,
  };

  return (
    <>
      <OnboardingTour
        onNavigate={(p) => navigate(p as PageKey)}
        defaultOpen={onboardingOpen}
        onClose={() => setOnboardingOpen(false)}
      />
      <Layout className="app-layout">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主内容
      </a>
      <Sider
        width={232}
        theme="light"
        className={isMobile ? "app-sider is-mobile" : "app-sider"}
        collapsible
        breakpoint="md"
        collapsedWidth={isMobile ? 0 : 72}
        collapsed={siderCollapsed}
        onBreakpoint={(broken: any) => {
          setIsMobile(broken);
          if (broken) setMobileNavOpen(false);
        }}
        onCollapse={(next: any) => {
          if (isMobile) {
            setMobileNavOpen(!next);
          } else {
            setCollapsed(next);
          }
        }}
        trigger={null}
      >
        <div className="sider-brand">
          <div className="brand-logo" aria-hidden="true">
            <DeploymentUnitOutlined />
          </div>
          {!siderCollapsed && (
            <div className="brand-content">
              {/* 这里原本也是 <h1>，与 PageTopbar 的页面标题构成同页两个 h1，
                  读屏的标题大纲会出现两个并列的一级标题。品牌名不是页面主题，
                  降级为普通元素，h1 只留给 PageTopbar。 */}
              <div className="brand-title">RAG4C</div>
              <div className="brand-sub">企业知识库平台</div>
            </div>
          )}
        </div>

        <nav id={PRIMARY_NAV_ID} className="app-nav" aria-label="主导航" tabIndex={0}>
          <Menu
            mode="inline"
            selectedKeys={[mainNavigationKey(page)]}
            items={MENU_ITEMS}
            onClick={(e: any) => navigate(e.key as PageKey)}
          />
        </nav>

        <div className="sider-foot">
          {!siderCollapsed && (
            <Tooltip title={connTip}>
              <div className="conn-pill">
                <span className={"status-dot " + connDotClass} aria-hidden="true" />
                <span className="conn-text">{connLabel}</span>
                <Button
                  type="text"
                  size="small"
                  aria-label="重新检查服务连接"
                  icon={<ReloadOutlined />}
                  loading={checking}
                  onClick={() => void refresh()}
                />
              </div>
            </Tooltip>
          )}
          <div className={siderCollapsed ? "sider-actions is-collapsed" : "sider-actions"}>
            <Tooltip title={siderCollapsed ? "展开侧栏" : "收起侧栏"} placement="right">
              <Button
                type="text"
                size="small"
                aria-label={siderCollapsed ? "展开侧栏" : "收起侧栏"}
                icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => {
                  if (isMobile) {
                    setMobileNavOpen(false);
                  } else {
                    setCollapsed((value) => !value);
                  }
                }}
              />
            </Tooltip>
            <Tooltip
              title={themeMode === "light" ? "切换到深色主题" : "切换到浅色主题"}
              placement="right"
            >
              <Button
                type="text"
                size="small"
                aria-label={themeMode === "light" ? "切换到深色主题" : "切换到浅色主题"}
                icon={themeMode === "light" ? <MoonOutlined /> : <SunOutlined />}
                onClick={onToggleTheme}
              />
            </Tooltip>
            <Tooltip title={siderCollapsed ? "新手引导" : "重看新手引导"} placement="right">
              <Button
                type="text"
                size="small"
                aria-label="重看新手引导"
                icon={<InfoCircleOutlined />}
                onClick={() => setOnboardingOpen(true)}
              />
            </Tooltip>
          </div>
        </div>
      </Sider>

      <div className="app-content">
        {isMobile && (
          <Button
            className={mobileNavOpen ? "mobile-nav-toggle is-open" : "mobile-nav-toggle"}
            type="text"
            size="small"
            aria-label={mobileNavOpen ? "关闭导航" : "打开导航"}
            aria-controls={PRIMARY_NAV_ID}
            aria-expanded={mobileNavOpen}
            icon={mobileNavOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            onClick={() => setMobileNavOpen((open) => !open)}
          />
        )}
        <div className="enterprise-shell-header">
          <WorkspaceScopeBar
            organizationLabel={organizationLabel}
            knowledgeBaseLabel={knowledgeBaseLabel}
            environmentLabel={online === true ? "本地环境" : "演示环境"}
            healthLabel={
              online === null
                ? "连接检测中"
                : degraded
                  ? "能力降级"
                  : online
                    ? "本地连接"
                    : "离线演示"
            }
            compactHealthLabel={
              online === null ? "检测" : degraded ? "降级" : online ? "在线" : "离线"
            }
            healthTone={
              online === null ? "default" : degraded ? "warning" : online ? "success" : "warning"
            }
            actorLabel={actorLabel}
            actorRole={actorRole}
            workspaceValue={enterpriseWorkspaceSelector.value}
            workspaceOptions={enterpriseWorkspaceOptions}
            workspaceStatus={enterpriseWorkspaceSelector.status}
            onWorkspaceChange={handleWorkspaceChange}
            onSearch={handleGlobalSearch}
            onOpenNotifications={handleOpenNotifications}
            notificationControl={
              <NotificationBell
                summary={notificationsController.summary}
                capabilityReady={notificationsCapabilityReady}
                onOpen={handleOpenNotifications}
              />
            }
          />
        </div>
        <ModeBanner />
        <main id="main-content" className="page-slot" tabIndex={-1}>
          {PAGE_KEYS.filter((key: PageKey) => mountedPages.includes(key)).map((key: PageKey) => (
            <div key={key} className={page === key ? "page-slot" : "page-slot is-hidden"}>
              <ErrorBoundary>
                <Suspense fallback={<PageState status="loading" title="加载中…" />}>
                  {pageNodes[key]}
                </Suspense>
              </ErrorBoundary>
            </div>
          ))}
        </main>
      </div>
      {notificationDrawerOpen ? (
        <Suspense fallback={null}>
          <NotificationDrawer
            controller={notificationsController}
            visible
            mobile={isMobile}
            readOnly={notificationsReadOnly}
            onClose={() => setNotificationDrawerOpen(false)}
            onOpenDetail={handleOpenNotificationDetail}
            onHandoff={handleNotificationHandoff}
            returnFocusRef={notificationReturnFocusRef}
          />
        </Suspense>
      ) : null}
      {notificationDetailOpen ? (
        <Suspense fallback={null}>
          <NotificationDetailDrawer
            visible
            state={notificationsController.detail}
            readOnly={notificationsReadOnly}
            loading={notificationsController.mutation.status === "saving"}
            onClose={() => setNotificationDetailOpen(false)}
            onHandoff={handleNotificationHandoff}
            onMarkRead={handleNotificationMarkRead}
            onMarkUnread={handleNotificationMarkUnread}
            onArchive={handleNotificationArchive}
            returnFocusRef={notificationDetailReturnFocusRef}
          />
        </Suspense>
      ) : null}
    </Layout>
    </>
  );
}
