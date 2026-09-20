import PageState from "../components/PageState";
import AuthRecoveryHint from "../components/AuthRecoveryHint";
import PageTopbar from "../components/PageTopbar";
import type { EnterpriseContext, EnterpriseScope } from "../enterprise-admin/model";
import { useOptionalKnowledgeWorkspace } from "../knowledge/KnowledgeWorkspaceContext";
import {
  readKnowledgeActorToken,
  resolveKnowledgeWorkspaceScope,
} from "../knowledge/workspaceScope";
import EnterpriseKnowledgeBaseCenter, {
  type KnowledgeBaseWorkspaceOption,
} from "../enterprise-knowledge-base/components/EnterpriseKnowledgeBaseCenter";
import { DataBaseIcon } from "tdesign-icons-react";

export interface EnterpriseKnowledgeBasePageProps {
  scope?: EnterpriseScope;
  context?: EnterpriseContext | null;
  workspaceOptions?: KnowledgeBaseWorkspaceOption[];
}

export default function EnterpriseKnowledgeBasePage({
  scope,
  context,
  workspaceOptions = [],
}: EnterpriseKnowledgeBasePageProps) {
  const knowledgeWorkspace = useOptionalKnowledgeWorkspace();
  const actorToken = readKnowledgeActorToken(scope?.actorToken);
  const fallbackScope = resolveKnowledgeWorkspaceScope({ actorToken });
  const resolvedScope: EnterpriseScope = scope ?? {
    tenantId: knowledgeWorkspace?.tenantId ?? fallbackScope.tenantId,
    datasetId: knowledgeWorkspace?.datasetId ?? fallbackScope.datasetId,
    actorToken,
  };
  const resolvedContext = context === undefined ? null : context;

  if (!resolvedScope.actorToken.trim()) {
    return (
      <PageState
        status="error"
        title="需要企业身份"
        description="连接签名企业身份后才能读取 Knowledge Base Registry 真账。"
        extra={<AuthRecoveryHint compact title="无法打开知识库注册表" />}
      />
    );
  }
  if (!resolvedContext) {
    return (
      <PageState
        status="loading"
        title="正在验证企业身份"
        description="Knowledge Base Registry 只接受已认证的企业上下文。"
      />
    );
  }

  return (
    <div className="enterprise-knowledge-base-page">
      <PageTopbar
        title="知识库注册表"
        icon={<DataBaseIcon />}
        subtitle="Workspace ownership · Application references · dependency evidence"
      />
      <EnterpriseKnowledgeBaseCenter
        scope={resolvedScope}
        context={resolvedContext}
        workspaceOptions={workspaceOptions}
      />
    </div>
  );
}
