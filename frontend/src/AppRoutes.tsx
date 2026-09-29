import { lazy, type Dispatch, type SetStateAction } from "react";
import PageState from "./components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "./enterprise-admin/model";
import KnowledgeBaseResourceShell from "./enterprise-knowledge-base-shell/KnowledgeBaseResourceShell";
import KnowledgeServingPage from "./enterprise-knowledge-base-shell/KnowledgeServingPageLoader";
import type { KnowledgeBaseResourceSection } from "./enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";
import type { NotificationHandoffContext } from "./enterprise-notification-center/components/notificationCenterShared";
import type { EnterpriseNotificationsHook } from "./enterprise-notification-center/hooks/useEnterpriseNotifications";
import type { AutomationRoute } from "./enterprise-automation-workflows/model/automationModel";
import type { TaskRoute } from "./enterprise-task-operations/model/taskModel";
import MountedPageHost from "./run/MountedPageHost";
import type { PageKey } from "./run/appRoute";
import type { KnowledgeBaseWorkspaceOption } from "./enterprise-knowledge-base/components/EnterpriseKnowledgeBaseCenter";
import QueryPage from "./pages/QueryPage";

const KnowledgeOverviewPage = lazy(() => import("./pages/KnowledgeOverviewPage"));
const DocumentsPage = lazy(() => import("./pages/DocumentsPage"));
const ChunkWorkbenchPage = lazy(() => import("./pages/ChunkWorkbenchPage"));
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
const EnterpriseContentRecoveryPage = lazy(
  () => import("./enterprise-content-recovery/ContentRecoveryPage"),
);
const NotificationCenter = lazy(
  () => import("./enterprise-notification-center/components/NotificationCenter"),
);
const TaskOperationsPage = lazy(() => import("./enterprise-task-operations/TaskOperationsPage"));
const AutomationPage = lazy(() => import("./enterprise-automation-workflows/AutomationPage"));

interface CapabilityState {
  ready: boolean;
  readOnly: boolean;
}

export interface AppRouteContext {
  scope: EnterpriseScope;
  identity: EnterpriseContext | null;
  identityStatus: "none" | "loading" | "ready" | "unavailable";
  tenantLabel: string;
  workspaceOptions: KnowledgeBaseWorkspaceOption[];
  activeDatasetId: string;
  resourceDatasetId: string;
  resourceSection: KnowledgeBaseResourceSection;
  mobile: boolean;
  acl: string[];
  onAclChange: Dispatch<SetStateAction<string[]>>;
  contentRecovery: CapabilityState;
  taskOperations: CapabilityState;
  automationWorkflows: CapabilityState;
  knowledgeServing: CapabilityState;
  notifications: {
    controller: EnterpriseNotificationsHook;
    capabilityReady: boolean;
    readOnly: boolean;
    onHandoff: (context: NotificationHandoffContext) => void;
  };
  onResourceSectionChange: (
    section: KnowledgeBaseResourceSection,
    trigger: HTMLElement,
  ) => boolean;
  onDocumentsDirtyChange: (dirty: boolean) => void;
  onWorkbenchDirtyChange: (dirty: boolean) => void;
  onRecoveryApprovalHandoff: (approvalRequestId: string) => void;
  onTaskHandoff: (route: TaskRoute) => void;
  onAutomationHandoff: (route: AutomationRoute) => void;
  onKnowledgeServingHandoff: (route: unknown) => void;
}

interface Props {
  activePage: PageKey;
  mountedPages: readonly PageKey[];
  context: AppRouteContext;
}

/** Owns lazy page composition while App retains route, identity, and capability state. */
export default function AppRoutes({ activePage, mountedPages, context }: Props) {
  const {
    scope,
    identity,
    identityStatus,
    tenantLabel,
    workspaceOptions,
    activeDatasetId,
    resourceDatasetId,
    resourceSection,
    mobile,
    acl,
    onAclChange,
    contentRecovery,
    taskOperations,
    automationWorkflows,
    knowledgeServing,
    notifications,
    onResourceSectionChange,
    onDocumentsDirtyChange,
    onWorkbenchDirtyChange,
    onRecoveryApprovalHandoff,
    onTaskHandoff,
    onAutomationHandoff,
    onKnowledgeServingHandoff,
  } = context;

  const pageNodes: Record<PageKey, JSX.Element> = {
    overview: <KnowledgeOverviewPage />,
    query: <QueryPage acl={acl} onAclChange={onAclChange} />,
    documents: (
      <KnowledgeBaseResourceShell
        active={activePage === "documents"}
        section="documents"
        onSectionChange={onResourceSectionChange}
      >
        <DocumentsPage
          active={activePage === "documents"}
          embedded
          onDirtyChange={onDocumentsDirtyChange}
          contentRecoveryCapabilityReady={contentRecovery.ready}
          contentRecoveryReadOnly={contentRecovery.readOnly}
        />
      </KnowledgeBaseResourceShell>
    ),
    "recycle-bin": (
      <EnterpriseContentRecoveryPage
        tenantId={scope.tenantId}
        actorToken={scope.actorToken}
        capabilityReady={contentRecovery.ready}
        tenantLabel={tenantLabel}
        readOnly={contentRecovery.readOnly}
        mobile={mobile}
        onApprovalHandoff={onRecoveryApprovalHandoff}
      />
    ),
    tasks: (
      <TaskOperationsPage
        tenantId={scope.tenantId}
        accountId={identity?.actor.id}
        actorToken={scope.actorToken}
        capabilityReady={taskOperations.ready}
        tenantLabel={tenantLabel}
        readOnly={taskOperations.readOnly}
        mobile={mobile}
        onTaskHandoff={onTaskHandoff}
      />
    ),
    automations: (
      <AutomationPage
        tenantId={scope.tenantId}
        accountId={identity?.actor.id}
        actorToken={scope.actorToken}
        capabilityReady={automationWorkflows.ready}
        tenantLabel={tenantLabel}
        readOnly={automationWorkflows.readOnly}
        mobile={mobile}
        onAutomationHandoff={onAutomationHandoff}
      />
    ),
    taxonomy: (
      <KnowledgeBaseResourceShell
        active={activePage === "taxonomy"}
        section="taxonomy"
        onSectionChange={onResourceSectionChange}
      >
        <KnowledgeTaxonomyPage embedded />
      </KnowledgeBaseResourceShell>
    ),
    governance: (
      <KnowledgeBaseResourceShell
        active={activePage === "governance"}
        section="governance"
        onSectionChange={onResourceSectionChange}
      >
        <KnowledgeGovernancePage embedded />
      </KnowledgeBaseResourceShell>
    ),
    sources: (
      <KnowledgeBaseResourceShell
        active={activePage === "sources"}
        section="sources"
        onSectionChange={onResourceSectionChange}
      >
        <KnowledgeSourcesPage active={activePage === "sources"} embedded />
      </KnowledgeBaseResourceShell>
    ),
    "parse-intervention": <ChunkWorkbenchPage onDirtyChange={onWorkbenchDirtyChange} />,
    "retrieval-lab": <RetrievalLabPage />,
    visualize: <VisualizePage />,
    eval: <EvalPage />,
    monitor: <MonitorPage active={activePage === "monitor"} />,
    consistency: <ConsistencyPage />,
    enterprise: <EnterpriseAdminPage />,
    notifications: notifications.capabilityReady ? (
      <NotificationCenter
        controller={notifications.controller}
        capabilityReady
        readOnly={notifications.readOnly}
        mobile={mobile}
        tenantLabel={tenantLabel}
        onHandoff={notifications.onHandoff}
      />
    ) : (
      <div className="page-shell">
        <div className="page-shell-inner">
          <PageState
            status={identityStatus === "loading" ? "loading" : "error"}
            title={identityStatus === "loading" ? "正在读取通知中心权威" : "通知中心权威暂不可用"}
            description="仅在服务端能力与当前账号身份均可验证后开放通知权威。"
          />
        </div>
      </div>
    ),
    "knowledge-bases": (
      <EnterpriseKnowledgeBasePage
        scope={{
          tenantId: scope.tenantId,
          datasetId: activeDatasetId,
          actorToken: scope.actorToken,
        }}
        context={identity}
        workspaceOptions={workspaceOptions}
      />
    ),
    "knowledge-base-workspace":
      resourceSection === "serving" ? (
        <KnowledgeBaseResourceShell
          active={activePage === "knowledge-base-workspace"}
          section="serving"
          tenantId={scope.tenantId}
          datasetId={resourceDatasetId}
          actorToken={scope.actorToken}
          onSectionChange={onResourceSectionChange}
        >
          <KnowledgeServingPage
            active={activePage === "knowledge-base-workspace"}
            tenantId={scope.tenantId}
            accountId={identity?.actor.id}
            actorToken={scope.actorToken}
            datasetId={resourceDatasetId}
            capabilityReady={knowledgeServing.ready}
            tenantLabel={tenantLabel}
            readOnly={knowledgeServing.readOnly}
            mobile={mobile}
            onServingHandoff={onKnowledgeServingHandoff}
          />
        </KnowledgeBaseResourceShell>
      ) : (
        <EnterpriseKnowledgeBaseWorkspacePage
          active={activePage === "knowledge-base-workspace"}
          section={resourceSection}
          tenantId={scope.tenantId}
          datasetId={resourceDatasetId}
          actorToken={scope.actorToken}
          onSectionChange={onResourceSectionChange}
        />
      ),
    config: <ConfigPage />,
  };

  return (
    <MountedPageHost
      activePage={activePage}
      mountedPages={mountedPages}
      pageNodes={pageNodes}
    />
  );
}
