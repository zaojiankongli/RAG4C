import { Alert, Tag } from "tdesign-react";
import { SecuredIcon } from "tdesign-icons-react";
import type { DatasetProfile, GovernanceScope } from "../model/governanceModel";

interface Props {
  scope: GovernanceScope;
  profile: DatasetProfile | null;
}

export default function GovernanceAuthorityBanner({ scope, profile }: Props) {
  return (
    <Alert
      className="governance-authority-banner"
      theme="info"
      title="Catalog 权威治理范围"
      message={
        <div className="governance-authority-content">
          <SecuredIcon aria-hidden="true" />
          <span>所有资料、QA 生命周期和文档版本均来自当前租户与知识库的权威目录。</span>
          <Tag variant="light-outline">租户 {scope.tenantId}</Tag>
          <Tag variant="light-outline">知识库 {scope.datasetId}</Tag>
          {profile ? <Tag theme="primary">Revision {profile.profile_revision}</Tag> : null}
        </div>
      }
    />
  );
}
