import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Dialog,
  Form,
  Input,
  Select,
  Steps,
  Textarea,
} from "tdesign-react";
import type {
  EnterpriseIdentityProvider,
  EnterpriseVerifiedDomain,
  IdentityProviderInput,
  IdentityProviderType,
  ProviderMutationResult,
} from "../enterpriseIdentityModel";
import type { IdentityMutationError } from "../hooks/useEnterpriseIdentityMutations";

interface Props {
  visible: boolean;
  domains: EnterpriseVerifiedDomain[];
  provider?: EnterpriseIdentityProvider | null;
  saving: boolean;
  error: IdentityMutationError | null;
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (input: IdentityProviderInput) => Promise<ProviderMutationResult | null>;
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="identity-form-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export default function IdentityProviderWizard({
  visible,
  domains,
  provider = null,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: Props) {
  const [step, setStep] = useState(0);
  const [type, setType] = useState<IdentityProviderType>("oidc");
  const [name, setName] = useState("");
  const [domainId, setDomainId] = useState("");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [entityId, setEntityId] = useState("");
  const [ssoUrl, setSsoUrl] = useState("");
  const [metadataUrl, setMetadataUrl] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  useEffect(() => {
    if (!visible) return;
    setStep(0);
    setType(provider?.provider_type ?? "oidc");
    setName(provider?.name ?? "");
    setDomainId(
      provider?.trusted_domain_id ??
        domains.find((item) => item.status === "verified")?.id ??
        domains[0]?.id ??
        "",
    );
    setIssuer(provider?.issuer_url ?? "");
    setClientId(provider?.client_id ?? "");
    setSecretRef(provider?.secret_ref ?? "");
    setEntityId(provider?.entity_id ?? "");
    setSsoUrl(provider?.sso_url ?? "");
    setMetadataUrl(provider?.metadata_url ?? "");
    setFingerprint(provider?.certificate_fingerprint ?? "");
    setReason("");
    setValidation("");
  }, [domains, provider, visible]);
  if (!visible) return null;
  const submit = async () => {
    if (!name.trim() || !domainId || !reason.trim()) {
      setValidation("名称、可信域名和变更原因不能为空。");
      return;
    }
    if (type === "oidc" && (!issuer.trim() || !clientId.trim() || !secretRef.trim())) {
      setValidation("OIDC issuer、client ID 和 secret_ref 不能为空。");
      return;
    }
    if (type === "saml" && (!entityId.trim() || !ssoUrl.trim() || !fingerprint.trim())) {
      setValidation("SAML entity ID、SSO URL 和证书指纹不能为空。");
      return;
    }
    const result = await onSubmit({
      name: name.trim(),
      provider_type: type,
      trusted_domain_id: domainId,
      ...(type === "oidc"
        ? {
            issuer_url: issuer.trim(),
            client_id: clientId.trim(),
            secret_ref: secretRef.trim(),
            scopes: ["openid", "email"],
          }
        : {
            entity_id: entityId.trim(),
            sso_url: ssoUrl.trim(),
            ...(metadataUrl.trim() ? { metadata_url: metadataUrl.trim() } : {}),
            certificate_fingerprint: fingerprint.trim(),
          }),
      reason: reason.trim(),
      ...(provider?.revision ? { revision: provider.revision } : {}),
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="配置 OIDC/SAML 身份提供商"
      width={700}
      destroyOnClose
      footer={false}
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      onClose={onClose}
      {...({
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "配置 OIDC/SAML 身份提供商",
      } as Record<string, unknown>)}
    >
      {error ? (
        <div className="identity-mutation-error" role="alert">
          <Alert theme="error" title="身份提供商操作未提交" message={error.message} />
          {error.retryAvailable ? (
            <Button variant="text" onClick={() => void onRetry()}>
              使用同一请求重试
            </Button>
          ) : null}
          {error.needsRefresh ? (
            <Button variant="text" onClick={() => void onRefresh()}>
              刷新身份提供商
            </Button>
          ) : null}
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐配置" message={validation} /> : null}
      <Alert
        theme="warning"
        title="外部登录 runtime_not_connected"
        message="Stage 9 仅验证配置合同与可信域名，不连接 OIDC/SAML callback 或登录 runtime。"
      />
      <Steps current={step} className="identity-provider-steps">
        <Steps.StepItem title="协议与域名" />
        <Steps.StepItem title="协议配置" />
        <Steps.StepItem title="审阅控制面" />
      </Steps>
      <Form className="identity-provider-form" labelAlign="top">
        {step === 0 ? (
          <>
            <Form.FormItem>
              <Field label="配置名称">
                <Input
                  value={name}
                  placeholder="Company Identity"
                  onChange={(value) => setName(String(value))}
                />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="协议类型">
                <Select
                  value={type}
                  options={[
                    { label: "OIDC", value: "oidc" },
                    { label: "SAML", value: "saml" },
                  ]}
                  onChange={(value) => setType(String(value) as IdentityProviderType)}
                />
              </Field>
            </Form.FormItem>
            <Form.FormItem className="identity-form-wide">
              <Field label="可信域名">
                <Select
                  value={domainId}
                  options={domains.map((item) => ({
                    label: `${item.domain} · ${item.status}`,
                    value: item.id,
                  }))}
                  onChange={(value) => setDomainId(String(value))}
                />
              </Field>
            </Form.FormItem>
          </>
        ) : null}
        {step === 1 && type === "oidc" ? (
          <>
            <Form.FormItem className="identity-form-wide">
              <Field label="OIDC issuer URL">
                <Input
                  value={issuer}
                  placeholder="https://id.example.com"
                  onChange={(value) => setIssuer(String(value))}
                />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Client ID">
                <Input value={clientId} onChange={(value) => setClientId(String(value))} />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Secret reference">
                <Input
                  value={secretRef}
                  placeholder="vault://identity/client"
                  onChange={(value) => setSecretRef(String(value))}
                />
              </Field>
            </Form.FormItem>
          </>
        ) : null}
        {step === 1 && type === "saml" ? (
          <>
            <Form.FormItem>
              <Field label="SAML entity ID">
                <Input value={entityId} onChange={(value) => setEntityId(String(value))} />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="SSO URL">
                <Input
                  value={ssoUrl}
                  placeholder="https://id.example.com/saml"
                  onChange={(value) => setSsoUrl(String(value))}
                />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Metadata URL">
                <Input value={metadataUrl} onChange={(value) => setMetadataUrl(String(value))} />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Certificate fingerprint">
                <Input value={fingerprint} onChange={(value) => setFingerprint(String(value))} />
              </Field>
            </Form.FormItem>
          </>
        ) : null}
        {step === 2 ? (
          <>
            <Descriptions
              column={2}
              bordered
              items={[
                { label: "协议", content: type.toUpperCase() },
                { label: "可信域名", content: domainId || "未选择" },
                { label: "运行面", content: "runtime_not_connected" },
                { label: "validation", content: "提交后由服务端验证" },
              ]}
            />
            <Form.FormItem className="identity-form-wide">
              <Field label="变更原因">
                <Textarea
                  value={reason}
                  autosize={{ minRows: 3, maxRows: 5 }}
                  onChange={(value) => setReason(String(value))}
                />
              </Field>
            </Form.FormItem>
          </>
        ) : null}
      </Form>
      <div className="identity-wizard-actions">
        <Button
          variant="outline"
          disabled={step === 0 || saving}
          onClick={() => setStep((value) => value - 1)}
        >
          上一步
        </Button>
        {step < 2 ? (
          <Button theme="primary" onClick={() => setStep((value) => value + 1)}>
            下一步
          </Button>
        ) : (
          <Button theme="primary" loading={saving} onClick={() => void submit()}>
            保存配置草稿
          </Button>
        )}
      </div>
    </Dialog>
  );
}
