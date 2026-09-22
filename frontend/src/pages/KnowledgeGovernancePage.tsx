import "../governance/governance.css";
import { Button, Tag } from "tdesign-react";
import { ControlPlatformIcon, RefreshIcon } from "tdesign-icons-react";
import { useEffect, useMemo, useState } from "react";
import PageState from "../components/PageState";
import AuthRecoveryHint from "../components/AuthRecoveryHint";
import PageTopbar from "../components/PageTopbar";
import DocumentVersionInspector from "../governance/components/DocumentVersionInspector";
import GovernanceAuthorityBanner from "../governance/components/GovernanceAuthorityBanner";
import DatasetProfilePanel from "../governance/components/DatasetProfilePanel";
import QAGovernancePanel from "../governance/components/QAGovernancePanel";
import { useDatasetGovernance } from "../governance/hooks/useDatasetGovernance";
import { useDocumentVersions } from "../governance/hooks/useDocumentVersions";
import { useQAGovernance } from "../governance/hooks/useQAGovernance";
import { parseGovernanceQaDeepLink } from "../run/appRoute";
import {
  GovernanceScopeError,
  projectGovernanceError,
  requireGovernanceScope,
  type GovernanceScope,
} from "../governance/model/governanceModel";
import { useConnection } from "../context/ConnectionContext";
import { useKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import { readKnowledgeActorToken } from "../knowledge/workspaceScope";

function authenticatedScope(
  workspaceScope: { tenantId: string; datasetId: string },
  actorToken: string,
): { scope: GovernanceScope | null; error: ReturnType<typeof projectGovernanceError> | null } {
  try {
    return { scope: requireGovernanceScope(workspaceScope, actorToken), error: null };
  } catch (caught) {
    return {
      scope: null,
      error: projectGovernanceError(
        caught instanceof GovernanceScopeError ? caught : new GovernanceScopeError(),
      ),
    };
  }
}

export default function KnowledgeGovernancePage({ embedded = false }: { embedded?: boolean }) {
  const workspace = useKnowledgeWorkspace();
  const { online } = useConnection();
  const tenantId = workspace.scope.tenantId;
  const datasetId = workspace.scope.datasetId;
  const actorToken = readKnowledgeActorToken();
  // 证据/一致性面板点入治理面时带 `#/governance?qa=<id>`；这里读取并随 hash 变化更新，
  // 交给 QA hook 做定位（翻到目标所在页），不再展示裸列表。
  const [qaDeepLink, setQaDeepLink] = useState<string>(() =>
    typeof window === "undefined" ? "" : parseGovernanceQaDeepLink(window.location).qaId,
  );
  useEffect(() => {
    const onLocation = () => setQaDeepLink(parseGovernanceQaDeepLink(window.location).qaId);
    window.addEventListener("popstate", onLocation);
    window.addEventListener("hashchange", onLocation);
    return () => {
      window.removeEventListener("popstate", onLocation);
      window.removeEventListener("hashchange", onLocation);
    };
  }, []);
  const resolved = useMemo(
    () => authenticatedScope({ tenantId, datasetId }, actorToken),
    [tenantId, datasetId, actorToken],
  );
  const dataset = useDatasetGovernance(resolved.scope, online);
  const datasetStatus = dataset.profile?.status ?? null;
  const qa = useQAGovernance(resolved.scope, online, datasetStatus, qaDeepLink || null);
  const versions = useDocumentVersions(resolved.scope, online, datasetStatus);

  const topbar = embedded ? null : (
    <PageTopbar
      icon={<ControlPlatformIcon />}
      title="知识治理"
      subtitle="管理数据集权威资料、QA 审核生命周期和不可变文档版本"
      extra={
        resolved.scope ? <Tag variant="light-outline">{resolved.scope.datasetId}</Tag> : undefined
      }
    />
  );

  if (!resolved.scope) {
    return (
      <div className="page-slot governance-page">
        {topbar}
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title={resolved.error?.title}
              description={resolved.error?.description}
              extra={<AuthRecoveryHint compact title="无法进入知识治理" />}
            />
          </div>
        </div>
      </div>
    );
  }

  if (online === false) {
    return (
      <div className="page-slot governance-page">
        {topbar}
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title="知识治理服务未连接"
              description="当前无法读取数据集、QA 或文档版本的权威事实。恢复连接后再重试。"
              extra={
                <AuthRecoveryHint
                  compact
                  title="治理权威不可用"
                  description="若后端已恢复，请确认工作区身份后刷新；若服务未启动，请先启动桥服务。"
                  onRetry={() => window.location.reload()}
                  retryLabel="刷新页面"
                />
              }
            />
          </div>
        </div>
      </div>
    );
  }

  if (dataset.status === "loading" && !dataset.profile) {
    return (
      <div className="page-slot governance-page">
        {topbar}
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState status="loading" title="正在读取治理权威资料" />
          </div>
        </div>
      </div>
    );
  }

  if (!dataset.profile) {
    const error = dataset.error ?? projectGovernanceError(new Error("unavailable"));
    return (
      <div className="page-slot governance-page">
        {topbar}
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title={error.title}
              description={error.description}
              extra={
                error.canRetry ? (
                  <Button variant="outline" onClick={() => void dataset.refresh()}>
                    <RefreshIcon /> 刷新数据集资料
                  </Button>
                ) : undefined
              }
            />
          </div>
        </div>
      </div>
    );
  }

  if (dataset.profile.status === "disabled") {
    return (
      <div className="page-slot governance-page">
        {topbar}
        <div className="page-shell">
          <div className="page-shell-inner">
            <PageState
              status="error"
              title="数据集已停用"
              description="治理资料、QA 和文档版本已从当前工作区清除，所有操作均已关闭。"
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-slot governance-page">
      {topbar}
      <div className="page-shell">
        <div className="page-shell-inner governance-shell">
          <GovernanceAuthorityBanner scope={resolved.scope} profile={dataset.profile} />
          <DatasetProfilePanel
            profile={dataset.profile}
            error={dataset.error}
            mutating={dataset.mutating}
            onRefresh={dataset.refresh}
            onUpdate={dataset.update}
            onArchive={dataset.archive}
            onRestore={dataset.restore}
            onDisable={dataset.disable}
          />
          <QAGovernancePanel {...qa} />
          <DocumentVersionInspector
            status={versions.status}
            documentId={versions.documentId}
            versions={versions.versions}
            error={versions.error}
            creating={versions.creating}
            readOnly={versions.readOnly}
            truncated={versions.truncated}
            onInspect={versions.inspect}
            onRefresh={versions.refresh}
            onCreate={versions.create}
          />
        </div>
      </div>
    </div>
  );
}
