import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useConnection } from "./context/ConnectionContext";
import { getServiceStatus } from "./components/serviceStatusModel";
import type { ThemeMode } from "./theme/tokens";
import { OnboardingTour } from "./onboarding/OnboardingTour";
import AppLayout from "./AppLayout";
import AppRoutes, { type AppRouteContext } from "./AppRoutes";
import {
  navigationIntent,
  parsePageLocation,
  type PageKey,
} from "./run/appRoute";
import { commitNavigationIntent } from "./run/navigationAdapter";
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
import type { WorkspaceScopeBarProps } from "./ui/enterprise/WorkspaceScopeBar";
import { fetchEnterpriseContext } from "./enterprise-admin/api/enterpriseAdminApi";
import type { EnterpriseContext } from "./enterprise-admin/model";
import { resolveEnterpriseCapabilityStates } from "./enterprise-admin/capabilityPolicy";
import { useEnterpriseWorkspaceSelector } from "./enterprise-workspace";
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

const NotificationDrawer = lazy(
  () => import("./enterprise-notification-center/components/NotificationDrawer"),
);
const NotificationDetailDrawer = lazy(
  () => import("./enterprise-notification-center/components/NotificationDetailDrawer"),
);

const ENTERPRISE_ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  member: "成员",
};

interface Props {
  themeMode: ThemeMode;
  onToggleTheme: () => void;
  onSetTheme?: (mode: ThemeMode) => void;
}

export default function App({ themeMode, onToggleTheme, onSetTheme }: Props) {
  const { online, checking, health, refresh } = useConnection();
  const knowledgeWorkspace = useOptionalKnowledgeWorkspace();
  const workspaceSyncRef = useRef<string>("");
  const [page, setPage] = useState<PageKey>(() => parsePageLocation(window.location) ?? "query");
  const [collapsed, setCollapsed] = useState(false);
  // 手动重开新手引导（侧边栏底部按钮触发）
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileNavToggleRef = useRef<HTMLButtonElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const appContentRef = useRef<HTMLDivElement>(null);
  const openMobileNavigation = useCallback(() => setMobileNavOpen(true), []);
  const closeMobileNavigation = useCallback(
    (focusTarget: "toggle" | "main" | "none" = "toggle") => {
      setMobileNavOpen(false);
      window.setTimeout(() => {
        if (focusTarget === "main" && isMobile) document.getElementById("main-content")?.focus();
        else if (focusTarget === "toggle") mobileNavToggleRef.current?.focus();
      }, 0);
    },
    [isMobile],
  );
  const [acl, setAcl] = useState<string[]>([]);
  const [mountedPages, setMountedPages] = useState<PageKey[]>(() => [page]);
  const [documentsWorkspaceDirty, setDocumentsWorkspaceDirty] = useState(false);
  const [workbenchDirty, setWorkbenchDirty] = useState(false);
  const [routeRevision, setRouteRevision] = useState(0);
  const [enterpriseIdentity, setEnterpriseIdentity] = useState<EnterpriseContext | null>(null);
  const [notificationDrawerOpen, setNotificationDrawerOpen] = useState(false);
  const [notificationDetailOpen, setNotificationDetailOpen] = useState(false);
  const onboardingReturnFocusRef = useRef<HTMLElement | null>(null);
  const notificationReturnFocusRef = useRef<HTMLElement | null>(null);
  const notificationDetailReturnFocusRef = useRef<HTMLElement | null>(null);
  const [enterpriseIdentityStatus, setEnterpriseIdentityStatus] = useState<
    "none" | "loading" | "ready" | "unavailable"
  >("none");

  useLayoutEffect(() => {
    const content = appContentRef.current;
    const contentIsInert = isMobile && mobileNavOpen;
    if (contentIsInert) mobileNavRef.current?.focus();
    if (content) {
      content.toggleAttribute("inert", contentIsInert);
      content.inert = contentIsInert;
    }
  }, [isMobile, mobileNavOpen]);

  useEffect(() => {
    if (!isMobile || !mobileNavOpen || onboardingOpen) return;
    const handleMobileNavigationKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeMobileNavigation();
        return;
      }
      if (event.key !== "Tab") return;
      const rail = document.querySelector<HTMLElement>("aside.app-sider.is-mobile");
      if (!rail) return;
      const focusable = Array.from(
        rail.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("inert") && element.getAttribute("aria-hidden") !== "true");
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        event.preventDefault();
        mobileNavRef.current?.focus();
        return;
      }
      const active = document.activeElement;
      const currentIndex = focusable.indexOf(active as HTMLElement);
      if (currentIndex < 0 || (event.shiftKey && currentIndex === 0)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && currentIndex === focusable.length - 1) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleMobileNavigationKeydown);
    return () => window.removeEventListener("keydown", handleMobileNavigationKeydown);
  }, [closeMobileNavigation, isMobile, mobileNavOpen, onboardingOpen]);

  useEffect(() => {
    setMountedPages((prev) => (prev.includes(page) ? prev : [...prev, page]));
  }, [page]);

  useEffect(() => {
    const onHashChange = () => {
      const key = parsePageLocation(window.location);
      if (key) setPage(key);
      if (mobileNavOpen) closeMobileNavigation(onboardingOpen ? "none" : "main");
      setRouteRevision((current) => current + 1);
    };
    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("popstate", onHashChange);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
      window.removeEventListener("popstate", onHashChange);
    };
  }, [closeMobileNavigation, mobileNavOpen, onboardingOpen]);

  const navigate = useCallback(
    (key: PageKey) => {
      const dirtyWorkspace =
        (page === "documents" && documentsWorkspaceDirty) ||
        (page === "parse-intervention" && workbenchDirty);
      if (
        dirtyWorkspace &&
        key !== page &&
        !window.confirm("当前切片修改尚未提交。离开解析干预工作区将丢弃草稿，是否继续？")
      )
        return;
      if (dirtyWorkspace && key !== page) {
        setDocumentsWorkspaceDirty(false);
        setWorkbenchDirty(false);
      }
      setPage(key);
      closeMobileNavigation("main");
      setNotificationDrawerOpen(false);
      setNotificationDetailOpen(false);
      if (parsePageLocation(window.location) !== key) {
        const intent = navigationIntent(window.location, key);
        commitNavigationIntent(intent);
      }
    },
    [closeMobileNavigation, documentsWorkspaceDirty, page, workbenchDirty],
  );

  const handleOnboardingNavigate = useCallback(
    (target: string) => {
      onboardingReturnFocusRef.current = document.getElementById("main-content");
      navigate(target as PageKey);
    },
    [navigate],
  );

  const handleGlobalSearch = useCallback((value: string) => {
    const query = value.trim();
    if (!query) return;
    const intent = navigationIntent(window.location, "documents");
    const url = `${intent.url}?q=${encodeURIComponent(query)}`;
    setPage("documents");
    closeMobileNavigation("main");
    commitNavigationIntent({ ...intent, url });
  }, [closeMobileNavigation]);

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
  const enterpriseCapabilityStates = resolveEnterpriseCapabilityStates({
    actorToken,
    tenantId: workspaceScope.tenantId,
    identity: enterpriseIdentity,
    online,
    healthStatus: health?.status,
  });
  const notificationsCapabilityReady = enterpriseCapabilityStates.notifications.ready;
  const notificationsReadOnly = enterpriseCapabilityStates.notifications.readOnly;
  const recoveryCapabilityReady = enterpriseCapabilityStates.contentRecovery.ready;
  const recoveryReadOnly = enterpriseCapabilityStates.contentRecovery.readOnly;
  const taskOperationsCapabilityReady = enterpriseCapabilityStates.taskOperations.ready;
  const taskOperationsReadOnly = enterpriseCapabilityStates.taskOperations.readOnly;
  const automationCapabilityReady = enterpriseCapabilityStates.automationWorkflows.ready;
  const automationReadOnly = enterpriseCapabilityStates.automationWorkflows.readOnly;
  const knowledgeServingCapabilityReady = enterpriseCapabilityStates.knowledgeServing.ready;
  const knowledgeServingReadOnly = enterpriseCapabilityStates.knowledgeServing.readOnly;
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
    closeMobileNavigation("main");
    const mode = navigationIntent(window.location, target.page).mode;
    commitNavigationIntent({ mode, url: target.url });
  }, [closeMobileNavigation]);

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
    closeMobileNavigation("main");
    const url = `/enterprise/approvals?request=${encodeURIComponent(normalized)}`;
    const mode = navigationIntent(window.location, "enterprise").mode;
    commitNavigationIntent({ mode, url });
  }, [closeMobileNavigation]);
  const handleTaskHandoff = useCallback((route: TaskRoute) => {
    const target = safeTaskHandoff(route);
    if (!target) return;
    setPage(target.page);
    closeMobileNavigation("main");
    const mode = navigationIntent(window.location, target.page).mode;
    commitNavigationIntent({ mode, url: target.url });
  }, [closeMobileNavigation]);
  const handleAutomationHandoff = useCallback((route: AutomationRoute) => {
    const target = safeAutomationHandoff(route);
    if (!target) return;
    setPage(target.page);
    closeMobileNavigation("main");
    const mode = navigationIntent(window.location, target.page).mode;
    commitNavigationIntent({ mode, url: target.url });
  }, [closeMobileNavigation]);

  const handleOpenOnboarding = useCallback(() => {
    onboardingReturnFocusRef.current = isMobile
      ? mobileNavToggleRef.current
      : document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (isMobile && mobileNavOpen) closeMobileNavigation("none");
    setOnboardingOpen(true);
  }, [closeMobileNavigation, isMobile, mobileNavOpen]);
  const handleCloseOnboarding = useCallback(() => {
    setOnboardingOpen(false);
    const returnFocus = onboardingReturnFocusRef.current;
    window.setTimeout(() => {
      const returnFocusIsUsable =
        returnFocus &&
        document.contains(returnFocus) &&
        !returnFocus.closest("[inert], [aria-hidden=\"true\"]");
      if (returnFocusIsUsable) returnFocus.focus();
      else if (isMobile) mobileNavToggleRef.current?.focus();
      else document.getElementById("main-content")?.focus();
      onboardingReturnFocusRef.current = null;
    }, 0);
  }, [isMobile]);

  const siderCollapsed = isMobile ? !mobileNavOpen : collapsed;
  const serviceStatus = getServiceStatus({ online, checking, health });
  const connLabel = serviceStatus.label;
  const connDotClass = serviceStatus.state === "checking" ? "processing" : serviceStatus.tone === "success" ? "online" : "warning";
  const connTip = serviceStatus.description;
  const handleSiderBreakpoint = useCallback(
    (broken: boolean) => {
      const focusWasInSider =
        document.activeElement instanceof HTMLElement &&
        document.activeElement.closest("aside.app-sider") !== null;
      const focusWasOnMobileToggle = document.activeElement === mobileNavToggleRef.current;
      setIsMobile(broken);
      if (broken && focusWasInSider) closeMobileNavigation();
      else {
        setMobileNavOpen(false);
        if (!broken && focusWasOnMobileToggle) {
          window.setTimeout(() => mobileNavRef.current?.focus(), 0);
        }
      }
    },
    [closeMobileNavigation],
  );
  const handleSiderCollapse = useCallback(
    (next: boolean) => {
      if (isMobile) {
        if (next) closeMobileNavigation();
        else openMobileNavigation();
      } else {
        setCollapsed(next);
      }
    },
    [closeMobileNavigation, isMobile, openMobileNavigation],
  );
  const handleToggleSider = useCallback(() => {
    if (isMobile) closeMobileNavigation();
    else setCollapsed((value) => !value);
  }, [closeMobileNavigation, isMobile]);

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
      closeMobileNavigation("main");
      commitNavigationIntent(intent);
      return true;
    },
    [closeMobileNavigation, documentsWorkspaceDirty, page, resourceDatasetId],
  );
  const handleKnowledgeServingHandoff = useCallback(
    (route: unknown) => {
      const target = safeKnowledgeServingHandoff(route, resourceDatasetId, window.location);
      if (!target) return;
      setPage(target.page);
      closeMobileNavigation("main");
      commitNavigationIntent({ mode: target.mode, url: target.url });
    },
    [closeMobileNavigation, resourceDatasetId],
  );

  const connectionStatus = {
    tooltip: connTip,
    label: connLabel,
    dotClass: connDotClass,
    checking,
    onRefresh: () => void refresh(),
  };
  const workspaceScopeBar: WorkspaceScopeBarProps = {
    organizationLabel,
    knowledgeBaseLabel,
    environmentLabel: online === true ? "本地环境" : "演示环境",
    healthLabel: serviceStatus.label,
    compactHealthLabel: serviceStatus.label,
    healthTone: serviceStatus.tone === "pending" ? "default" : serviceStatus.tone,
    actorLabel,
    actorRole,
    workspaceValue: enterpriseWorkspaceSelector.value,
    workspaceOptions: enterpriseWorkspaceOptions,
    workspaceStatus: enterpriseWorkspaceSelector.status,
    onWorkspaceChange: handleWorkspaceChange,
    onSearch: handleGlobalSearch,
    notificationControl: (
      <NotificationBell
        summary={notificationsController.summary}
        capabilityReady={notificationsCapabilityReady}
        onOpen={handleOpenNotifications}
      />
    ),
  };

  const routeContext: AppRouteContext = {
    scope: {
      tenantId: workspaceScope.tenantId,
      datasetId: activeDatasetId,
      actorToken: actorToken ?? "",
    },
    identity: enterpriseIdentity,
    identityStatus: enterpriseIdentityStatus,
    tenantLabel: organizationLabel,
    workspaceOptions: enterpriseWorkspaceOptions,
    activeDatasetId,
    resourceDatasetId,
    resourceSection: workspaceResourceSection,
    mobile: isMobile,
    acl,
    onAclChange: setAcl,
    contentRecovery: { ready: recoveryCapabilityReady, readOnly: recoveryReadOnly },
    taskOperations: {
      ready: taskOperationsCapabilityReady,
      readOnly: taskOperationsReadOnly,
    },
    automationWorkflows: { ready: automationCapabilityReady, readOnly: automationReadOnly },
    knowledgeServing: {
      ready: knowledgeServingCapabilityReady,
      readOnly: knowledgeServingReadOnly,
    },
    notifications: {
      controller: notificationsController,
      capabilityReady: notificationsCapabilityReady,
      readOnly: notificationsReadOnly,
      onHandoff: handleNotificationHandoff,
    },
    onResourceSectionChange: handleResourceSectionChange,
    onDocumentsDirtyChange: setDocumentsWorkspaceDirty,
    onWorkbenchDirtyChange: setWorkbenchDirty,
    onRecoveryApprovalHandoff: handleRecoveryApprovalHandoff,
    onTaskHandoff: handleTaskHandoff,
    onAutomationHandoff: handleAutomationHandoff,
    onKnowledgeServingHandoff: handleKnowledgeServingHandoff,
  };

  return (
    <>
      <OnboardingTour
        onNavigate={handleOnboardingNavigate}
        defaultOpen={onboardingOpen}
        onClose={handleCloseOnboarding}
      />
      <AppLayout
        page={page}
        isMobile={isMobile}
        mobileNavOpen={mobileNavOpen}
        siderCollapsed={siderCollapsed}
        themeMode={themeMode}
        mobileNavToggleRef={mobileNavToggleRef}
        mobileNavRef={mobileNavRef}
        appContentRef={appContentRef}
        connection={connectionStatus}
        workspaceScopeBar={workspaceScopeBar}
        onNavigate={navigate}
        onBreakpoint={handleSiderBreakpoint}
        onCollapse={handleSiderCollapse}
        onToggleSider={handleToggleSider}
        onToggleTheme={onToggleTheme}
        onSetTheme={onSetTheme}
        onOpenOnboarding={handleOpenOnboarding}
        onOpenMobileNavigation={openMobileNavigation}
        onCloseMobileNavigation={closeMobileNavigation}
        overlays={
          <>
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
          </>
        }
      >
        <AppRoutes
          activePage={page}
          mountedPages={mountedPages}
          context={routeContext}
        />
      </AppLayout>
    </>
  );
}
