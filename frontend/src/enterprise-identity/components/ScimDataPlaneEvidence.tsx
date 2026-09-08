import { useMemo, useState } from "react";
import { Alert, Button, Input, Tag } from "tdesign-react";
import { CopyIcon } from "tdesign-icons-react";
import { buildScimDataPlaneUrls } from "../api/enterpriseIdentityApi";
import type { ScimDataPlaneEvidence as ScimDataPlaneEvidenceModel } from "../enterpriseIdentityModel";

interface Props {
  evidence: ScimDataPlaneEvidenceModel;
}

type CopyTarget = "endpoint" | "service-provider-config" | null;

export default function ScimDataPlaneEvidence({ evidence }: Props) {
  const urls = useMemo(() => buildScimDataPlaneUrls(), []);
  const [copied, setCopied] = useState<CopyTarget>(null);

  if (!evidence.ready) return null;

  const copy = async (target: Exclude<CopyTarget, null>, value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(target);
  };

  return (
    <section className="identity-scim-data-plane" aria-label="SCIM v2 provisioning 数据面">
      <div className="identity-scim-data-plane__heading">
        <div>
          <span>PROVISIONING DATA PLANE</span>
          <h3>SCIM v2 运行证据</h3>
          <p>
            0022 数据库权威已就绪；本页只展示 endpoint 与 token 使用证据，不手工创建 Users/Groups。
          </p>
        </div>
        <Tag theme="success" variant="light-outline">
          {evidence.revision}
        </Tag>
      </div>

      <div className="identity-scim-endpoints">
        <label className="identity-scim-endpoint">
          <span>Base endpoint</span>
          <Input value={evidence.base_path} readOnly aria-label="SCIM v2 endpoint" />
          <Button
            variant="outline"
            icon={<CopyIcon />}
            aria-label="复制 SCIM endpoint"
            onClick={() => void copy("endpoint", urls.baseEndpoint)}
          >
            复制 endpoint
          </Button>
        </label>
        <label className="identity-scim-endpoint">
          <span>ServiceProviderConfig</span>
          <Input
            value={urls.serviceProviderConfigUrl}
            readOnly
            aria-label="ServiceProviderConfig URL"
          />
          <Button
            variant="outline"
            icon={<CopyIcon />}
            aria-label="复制 ServiceProviderConfig URL"
            onClick={() => void copy("service-provider-config", urls.serviceProviderConfigUrl)}
          >
            复制配置 URL
          </Button>
        </label>
      </div>

      <div className="identity-scim-contract-grid">
        <div>
          <span>Resources</span>
          <div className="identity-scim-tags">
            {evidence.resources.map((resource) => (
              <Tag key={resource} theme="primary" variant="light-outline">
                {resource}
              </Tag>
            ))}
          </div>
        </div>
        <div>
          <span>Scopes</span>
          <div className="identity-scim-tags">
            {evidence.scopes.map((scope) => (
              <Tag key={scope} variant="light-outline">
                {scope}
              </Tag>
            ))}
          </div>
        </div>
      </div>

      <Alert
        theme="info"
        title="SCIM provider 调用边界"
        message="外部 IdP 使用 bearer token 调用 /scim/v2；租户来自 token 权威，控制台不会代替 provider 发起 Users/Groups provisioning。"
      />
      {copied ? (
        <p className="identity-scim-copy-status" role="status" aria-live="polite">
          {copied === "endpoint" ? "SCIM endpoint 已复制" : "ServiceProviderConfig URL 已复制"}
        </p>
      ) : null}
    </section>
  );
}
