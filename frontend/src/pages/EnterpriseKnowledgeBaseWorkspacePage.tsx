import { Card, Tag } from "tdesign-react";
import { useKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import KnowledgeBaseResourceShell from "../enterprise-knowledge-base-shell/KnowledgeBaseResourceShell";
import { KnowledgeBaseDrawerCoordinatorProvider } from "../enterprise-knowledge-base-shell/KnowledgeBaseDrawerCoordinator";
import type { KnowledgeBaseResourceSection } from "../enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";
import type { ReleaseApi } from "../enterprise-knowledge-base-release/hooks/useKnowledgeBaseReleases";
import type { ReleaseQualityApi } from "../enterprise-release-quality/api/qualityApi";
import type { OperationsApi } from "../enterprise-release-quality-operations/hooks/useReleaseQualityOperations";
import QualityOperationsWorkspace from "../enterprise-release-quality-operations/components/QualityOperationsWorkspace";

export interface EnterpriseKnowledgeBaseWorkspacePageProps {
  active: boolean;
  section: KnowledgeBaseResourceSection;
  tenantId: string;
  datasetId: string;
  actorToken: string;
  onSectionChange?: (section: KnowledgeBaseResourceSection, trigger: HTMLElement) => boolean | void;
  onDirtyChange?: (dirty: boolean) => void;
  readOnly?: boolean;
  releaseApi?: ReleaseApi;
  qualityApi?: ReleaseQualityApi;
  operationsApi?: OperationsApi;
}

function WorkspaceOverview() {
  const workspace = useKnowledgeWorkspace();
  const authorityReady = workspace.workspaceScopeStatus === "verified";
  const summary = workspace.summary;
  return (
    <div className="knowledge-base-resource-overview page-shell">
      <div className="page-shell-inner">
        <section
          className="knowledge-base-resource-overview__intro"
          aria-labelledby="resource-overview-title"
        >
          <div>
            <span className="knowledge-base-resource-overview__eyebrow">RESOURCE OVERVIEW</span>
            <h2 id="resource-overview-title">一个知识库，统一承载知识责任边界</h2>
            <p>
              当前页面复用既有 Documents、Taxonomy、Sources 与 Governance
              权威；资源壳只负责上下文、导航和可验证状态，不复制业务表单。
            </p>
          </div>
          <Tag theme={authorityReady ? "success" : "warning"} variant="light-outline">
            {authorityReady ? "工作区作用域已验证" : "等待工作区作用域验证"}
          </Tag>
        </section>
        <section
          className="knowledge-base-resource-overview__facts"
          aria-label="Knowledge Base 工作区摘要"
        >
          <Card title="当前工作区" size="small">
            <dl>
              <div>
                <dt>Dataset</dt>
                <dd>{workspace.datasetId || "未返回"}</dd>
              </div>
              <div>
                <dt>Workspace</dt>
                <dd>{workspace.workspaceId || "未返回"}</dd>
              </div>
              <div>
                <dt>目录状态</dt>
                <dd>{workspace.status === "ready" ? "已读取" : workspace.status}</dd>
              </div>
              <div>
                <dt>文档总量</dt>
                <dd>{summary?.total ?? "未返回"}</dd>
              </div>
            </dl>
          </Card>
          <Card title="后续工作面" size="small">
            <ul>
              <li>Release Manifest 将在 Releases 资源中提供可复现发布事实。</li>
              <li>所有未返回的权威事实均保持为“未返回”，不以演示数据补齐。</li>
              <li>需要变更 ownership 或 Application reference 时返回 Registry 权威入口。</li>
            </ul>
          </Card>
        </section>
      </div>
    </div>
  );
}

function ReleasesSurface({
  active,
  tenantId,
  datasetId,
  actorToken,
  readOnly,
  releaseApi,
  qualityApi,
  operationsApi,
}: {
  active: boolean;
  tenantId: string;
  datasetId: string;
  actorToken: string;
  readOnly: boolean;
  releaseApi?: ReleaseApi;
  qualityApi?: ReleaseQualityApi;
  operationsApi?: OperationsApi;
}) {
  const workspace = useKnowledgeWorkspace();
  return (
    <QualityOperationsWorkspace
      active={active}
      scope={{ tenantId, datasetId, actorToken }}
      datasetName={datasetId}
      workspaceName={workspace.workspaceId || "Workspace 未返回"}
      readOnly={readOnly}
      scopeVerified={workspace.workspaceScopeStatus === "verified"}
      releaseApi={releaseApi}
      qualityApi={qualityApi}
      operationsApi={operationsApi}
    />
  );
}

function WorkspaceContent({
  section,
  active,
  tenantId,
  datasetId,
  actorToken,
  readOnly,
  releaseApi,
  qualityApi,
  operationsApi,
}: Pick<
  EnterpriseKnowledgeBaseWorkspacePageProps,
  | "section"
  | "active"
  | "tenantId"
  | "datasetId"
  | "actorToken"
  | "readOnly"
  | "releaseApi"
  | "qualityApi"
  | "operationsApi"
>) {
  return section === "releases" ? (
    <ReleasesSurface
      active={active}
      tenantId={tenantId}
      datasetId={datasetId}
      actorToken={actorToken}
      readOnly={readOnly ?? false}
      releaseApi={releaseApi}
      qualityApi={qualityApi}
      operationsApi={operationsApi}
    />
  ) : (
    <WorkspaceOverview />
  );
}

export default function EnterpriseKnowledgeBaseWorkspacePage({
  active,
  section,
  tenantId,
  datasetId,
  actorToken,
  onSectionChange,
  readOnly = false,
  releaseApi,
  qualityApi,
  operationsApi,
}: EnterpriseKnowledgeBaseWorkspacePageProps) {
  if (!active) return null;
  return (
    <KnowledgeBaseDrawerCoordinatorProvider>
      <KnowledgeBaseResourceShell
        active
        section={section}
        tenantId={tenantId}
        datasetId={datasetId}
        actorToken={actorToken}
        onSectionChange={onSectionChange}
      >
        <WorkspaceContent
          section={section}
          active={active}
          tenantId={tenantId}
          datasetId={datasetId}
          actorToken={actorToken}
          readOnly={readOnly}
          releaseApi={releaseApi}
          qualityApi={qualityApi}
          operationsApi={operationsApi}
        />
      </KnowledgeBaseResourceShell>
    </KnowledgeBaseDrawerCoordinatorProvider>
  );
}
