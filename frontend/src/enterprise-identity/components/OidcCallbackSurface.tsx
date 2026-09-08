import { Alert, Button, Tag } from "tdesign-react";
import { CheckCircleIcon, RefreshIcon, SecuredIcon } from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { OidcCallbackInput } from "../enterpriseIdentityModel";
import { useOidcCallback } from "../hooks/useOidcRuntime";

export default function OidcCallbackSurface({
  credentials,
  onContinue = () => window.location.reload(),
}: {
  credentials: OidcCallbackInput;
  onContinue?: () => void;
}) {
  const { status, result, error } = useOidcCallback(credentials);
  if (status === "loading")
    return (
      <section
        className="oidc-callback-surface enterprise-admin-surface"
        role="region"
        aria-label="OIDC 登录回调结果"
      >
        <PageState
          compact
          status="loading"
          title="正在验证 OIDC 登录回调"
          description="code/state 已提交并从浏览器地址中清除。"
        />
      </section>
    );
  if (status === "error" || !result)
    return (
      <section
        className="oidc-callback-surface enterprise-admin-surface"
        role="region"
        aria-label="OIDC 登录回调结果"
      >
        <header className="oidc-callback-surface__header">
          <SecuredIcon aria-hidden="true" />
          <div>
            <span>OIDC CALLBACK / SANITIZED RESULT</span>
            <h2>OIDC 登录未完成</h2>
            <p>URL code/state 已清除；页面只展示稳定错误，不显示 OAuth token。</p>
          </div>
          <Tag theme="danger" variant="light-outline">
            callback_failed
          </Tag>
        </header>
        <Alert
          theme="error"
          title={error?.title || "OIDC 登录未完成"}
          message={error?.message || "请重新启动登录。"}
        />
        <Button theme="primary" icon={<RefreshIcon />} onClick={onContinue}>
          重新进入企业身份中心
        </Button>
      </section>
    );
  return (
    <section
      className="oidc-callback-surface enterprise-admin-surface"
      role="region"
      aria-label="OIDC 登录回调结果"
    >
      <header className="oidc-callback-surface__header">
        <CheckCircleIcon aria-hidden="true" />
        <div>
          <span>OIDC CALLBACK / AUTHENTICATED</span>
          <h2>OIDC 登录成功</h2>
          <p>服务端已验证 issuer、audience、nonce、邮箱与现有 active membership。</p>
        </div>
        <Tag theme="success" variant="light-outline">
          knowledge_actor_token_stored
        </Tag>
      </header>
      <Alert
        theme="success"
        title="KnowledgeActor 身份已更新"
        message="前端仅写入既有 KnowledgeActor token 存储键；ID/access/refresh/session token 均未暴露。"
      />
      <dl className="oidc-callback-evidence">
        <div>
          <dt>签名账户</dt>
          <dd>{result.actor_name}</dd>
        </div>
        <div>
          <dt>账户邮箱</dt>
          <dd>{result.actor_email}</dd>
        </div>
        <div>
          <dt>企业租户</dt>
          <dd>{result.tenant_name}</dd>
        </div>
        <div>
          <dt>OIDC provider</dt>
          <dd>{result.provider_name}</dd>
        </div>
        <div>
          <dt>SSO session</dt>
          <dd>
            {result.session_status} · revision {result.session_revision}
          </dd>
        </div>
        <div>
          <dt>到期时间</dt>
          <dd>{result.expires_at}</dd>
        </div>
      </dl>
      <div className="oidc-callback-surface__actions">
        <Button theme="primary" onClick={onContinue}>
          进入企业身份中心
        </Button>
      </div>
    </section>
  );
}
