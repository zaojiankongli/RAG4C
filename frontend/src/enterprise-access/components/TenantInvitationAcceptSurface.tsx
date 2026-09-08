import { useState } from "react";
import { Alert, Button, Checkbox, Input, Tag } from "tdesign-react";
import { CheckCircleIcon, SecuredIcon } from "tdesign-icons-react";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { useEnterpriseInvitationMutations } from "../hooks/useEnterpriseInvitationMutations";

export interface TenantInvitationAcceptSurfaceProps {
  scope: EnterpriseScope;
  token: string;
  actorEmail?: string | null;
  actorId?: string | null;
  onAccepted?: () => Promise<void>;
  onClearToken: () => void;
}

export default function TenantInvitationAcceptSurface({
  scope,
  token,
  actorEmail,
  actorId,
  onAccepted,
  onClearToken,
}: TenantInvitationAcceptSurfaceProps) {
  const [confirmed, setConfirmed] = useState(false);
  const { saving, error, success, accept, retry } = useEnterpriseInvitationMutations({
    scope,
    context: null,
    invitations: null,
    onAccepted,
  });

  const submit = async () => {
    if (!confirmed) return;
    const result = await accept({ invite_token: token });
    if (result) onClearToken();
  };
  const retryAcceptance = async () => {
    const result = await retry();
    if (result) onClearToken();
  };

  return (
    <section
      className="enterprise-invitation-accept-surface enterprise-admin-surface"
      role="region"
      aria-label="接受企业邀请"
    >
      <header className="enterprise-invitation-accept-surface__header">
        <SecuredIcon aria-hidden="true" />
        <div>
          <span>INVITATION ACCEPTANCE / SIGNED ACTOR</span>
          <h2>接受企业邀请</h2>
          <p>仅在你明确确认后提交；服务端会验证签名账户邮箱与邀请邮箱完全一致。</p>
        </div>
        <Tag theme="primary" variant="light-outline">
          一次性 token
        </Tag>
      </header>
      <Alert
        theme={actorEmail ? "success" : "info"}
        title="签名身份邮箱校验"
        message={
          actorEmail
            ? `当前签名账户：${actorEmail}；服务端仍会重新读取权威账户邮箱并精确匹配。`
            : "前端不会从 Actor Token 伪造邮箱；提交后由专用接受接口读取签名账户并完成精确邮箱校验。"
        }
      />
      <dl className="enterprise-invitation-accept-evidence">
        <div>
          <dt>签名账号</dt>
          <dd>{actorId || "由服务端解析"}</dd>
        </div>
        <div>
          <dt>签名邮箱</dt>
          <dd>{actorEmail || "由服务端验证"}</dd>
        </div>
      </dl>
      <Input type="password" value={token} readOnly aria-label="邀请 token（已隐藏）" />
      {error ? (
        <div className="enterprise-access-mutation-error" role="alert">
          <Alert theme="error" title="邀请接受未完成" message={error.message} />
          {error.retryAvailable ? (
            <Button variant="text" theme="primary" onClick={() => void retryAcceptance()}>
              使用同一请求重试
            </Button>
          ) : null}
        </div>
      ) : null}
      {success ? (
        <Alert
          theme="success"
          title="邀请已接受"
          message="成员关系与审计证据已由服务端原子提交，正在进入企业工作面。"
          icon={<CheckCircleIcon />}
        />
      ) : null}
      <Checkbox checked={confirmed} disabled={saving} onChange={setConfirmed}>
        我确认使用当前签名账户接受此企业邀请
      </Checkbox>
      <div className="enterprise-invitation-accept-surface__actions">
        <Button variant="outline" disabled={saving} onClick={onClearToken}>
          暂不接受
        </Button>
        <Button
          theme="primary"
          loading={saving}
          aria-disabled={!confirmed || saving ? "true" : undefined}
          onClick={() => {
            if (confirmed && !saving) void submit();
          }}
        >
          确认接受邀请
        </Button>
      </div>
    </section>
  );
}
