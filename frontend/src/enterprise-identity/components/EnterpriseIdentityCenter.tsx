import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Dialog,
  Drawer,
  Form,
  Input,
  InputNumber,
  PrimaryTable,
  Tabs,
  Tag,
  Textarea,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  CertificateIcon,
  CopyIcon,
  DeleteIcon,
  KeyIcon,
  LoginIcon,
  MoreIcon,
  RefreshIcon,
  SettingIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  useEnterpriseIdentityCenter,
  type IdentityResource,
} from "../hooks/useEnterpriseIdentityCenter";
import { useEnterpriseIdentityMutations } from "../hooks/useEnterpriseIdentityMutations";
import { useOidcRuntimeStart } from "../hooks/useOidcRuntime";
import { buildOidcCallbackUrl } from "../oidcRuntimeRoute";
import type {
  DomainMutationResult,
  EnterpriseIdentityProvider,
  EnterpriseScimReadinessReport,
  EnterpriseScimToken,
  EnterpriseVerifiedDomain,
  IdentityRevisionInput,
} from "../enterpriseIdentityModel";
import IdentityProviderWizard from "./IdentityProviderWizard";
import ScimDataPlaneEvidence from "./ScimDataPlaneEvidence";
import { ScimTokenDeliveryDialog, ScimTokenIssueDialog } from "./ScimTokenDialogs";
import "../enterprise-identity.css";

type TabKey = "domains" | "providers" | "scim";
type ReasonAction =
  | { kind: "verify-domain" | "revoke-domain"; domain: EnterpriseVerifiedDomain }
  | { kind: "activate-provider" | "disable-provider"; provider: EnterpriseIdentityProvider }
  | { kind: "revoke-scim"; token: EnterpriseScimToken };

function useIdentityMobile(): boolean {
  const query = "(max-width: 600px)";
  const [mobile, setMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(query).matches === true,
  );
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia(query);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return mobile;
}
function statusTheme(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "verified" || status === "active" || status === "valid") return "success";
  if (status === "pending" || status === "draft" || status === "unchecked") return "warning";
  if (status === "revoked" || status === "disabled" || status === "expired" || status === "invalid")
    return "danger";
  return "default";
}
function identityTabLabel(label: string, value: TabKey, active: TabKey) {
  return (
    <span role="tab" aria-selected={active === value} tabIndex={active === value ? 0 : -1}>
      {label}
    </span>
  );
}
function dateLabel(value?: string): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  return Number.isNaN(parsed)
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(parsed);
}
function ResourceBoundary<T>({
  resource,
  label,
  empty,
  children,
}: {
  resource: IdentityResource<T>;
  label: string;
  empty: string;
  children: ReactNode;
}) {
  if (resource.status === "loading" || resource.status === "idle")
    return (
      <PageState
        status="loading"
        compact
        title={`正在读取${label}`}
        description="从身份联合控制面读取权威事实"
      />
    );
  if (resource.status !== "ready" && resource.error)
    return (
      <PageState
        status="error"
        compact
        title={resource.error.title}
        description={resource.error.description}
        extra={
          resource.error.canRetry ? (
            <Button variant="outline" onClick={() => void resource.reload()}>
              重新读取
            </Button>
          ) : undefined
        }
      />
    );
  if (!resource.items.length)
    return <PageState status="empty" compact title={empty} description="真实接口已返回空列表" />;
  return <>{children}</>;
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="identity-form-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function DomainCreateDialog({
  visible,
  saving,
  error,
  onClose,
  onSubmit,
  onRetry,
}: {
  visible: boolean;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseIdentityMutations>["error"];
  onClose: () => void;
  onSubmit: (value: { domain: string; reason: string }) => Promise<DomainMutationResult | null>;
  onRetry: () => Promise<unknown>;
}) {
  const [domain, setDomain] = useState("");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  useEffect(() => {
    if (visible) {
      setDomain("");
      setReason("");
      setValidation("");
    }
  }, [visible]);
  if (!visible) return null;
  const submit = async () => {
    if (!domain.trim() || !reason.trim()) {
      setValidation("域名和业务原因不能为空。");
      return;
    }
    const result = await onSubmit({ domain: domain.trim(), reason: reason.trim() });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="声明可信域名"
      width={540}
      destroyOnClose
      confirmBtn={{ content: "创建 DNS 挑战", theme: "primary" }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "声明可信域名" } as Record<
        string,
        unknown
      >)}
    >
      {error ? (
        <div role="alert" className="identity-mutation-error">
          <Alert theme="error" title="域名挑战未创建" message={error.message} />
          {error.retryAvailable ? (
            <Button variant="text" onClick={() => void onRetry()}>
              使用同一请求重试
            </Button>
          ) : null}
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐域名信息" message={validation} /> : null}
      <Alert
        theme="info"
        title="DNS 只读验证"
        message="系统只生成 TXT challenge 并在操作员点击验证时执行有界 TXT 查询，不会写入外部 DNS。"
      />
      <Form labelAlign="top" className="identity-domain-form">
        <Form.FormItem>
          <Field label="企业域名">
            <Input
              value={domain}
              placeholder="example.com"
              onChange={(value) => setDomain(String(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="声明原因">
            <Textarea
              value={reason}
              autosize={{ minRows: 3, maxRows: 5 }}
              onChange={(value) => setReason(String(value))}
            />
          </Field>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}
function ReasonDialog({
  action,
  saving,
  error,
  onClose,
  onSubmit,
  onRetry,
}: {
  action: ReasonAction | null;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseIdentityMutations>["error"];
  onClose: () => void;
  onSubmit: (payload: IdentityRevisionInput) => Promise<unknown>;
  onRetry: () => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    setReason("");
  }, [action]);
  if (!action) return null;
  const title =
    action.kind === "verify-domain"
      ? "验证可信域名"
      : action.kind === "revoke-domain"
        ? "撤销可信域名"
        : action.kind === "activate-provider"
          ? "激活身份提供商"
          : action.kind === "disable-provider"
            ? "停用身份提供商"
            : "撤销 SCIM token";
  const revision =
    "domain" in action
      ? action.domain.revision
      : "provider" in action
        ? action.provider.revision
        : action.token.revision;
  const danger = action.kind.includes("revoke") || action.kind === "disable-provider";
  const submit = async () => {
    if (!reason.trim()) return;
    const result = await onSubmit({ revision, reason: reason.trim() });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header={title}
      width={520}
      destroyOnClose
      confirmBtn={{ content: title, theme: danger ? "danger" : "primary" }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": title } as Record<
        string,
        unknown
      >)}
    >
      {error ? (
        <div role="alert" className="identity-mutation-error">
          <Alert theme="error" title="操作未提交" message={error.message} />
          {error.retryAvailable ? (
            <Button variant="text" onClick={() => void onRetry()}>
              使用同一请求重试
            </Button>
          ) : null}
        </div>
      ) : null}
      <Form labelAlign="top">
        <Form.FormItem label="revision">
          <InputNumber value={revision} readOnly theme="normal" />
        </Form.FormItem>
        <Form.FormItem>
          <Field label="操作原因">
            <Textarea
              value={reason}
              autosize={{ minRows: 3, maxRows: 5 }}
              onChange={(value) => setReason(String(value))}
            />
          </Field>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}

export default function EnterpriseIdentityCenter({
  scope,
  context,
  readiness,
  onOidcNavigate,
}: {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  readiness?: EnterpriseScimReadinessReport | null;
  onOidcNavigate?: (url: string) => void;
}) {
  const mobile = useIdentityMobile();
  const [tab, setTab] = useState<TabKey>("domains");
  const [domainCreate, setDomainCreate] = useState(false);
  const [wizard, setWizard] = useState(false);
  const [scimIssue, setScimIssue] = useState(false);
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [drawerObject, setDrawerObject] = useState<{
    type: "domain" | "provider" | "scim";
    item: EnterpriseVerifiedDomain | EnterpriseIdentityProvider | EnterpriseScimToken;
  } | null>(null);
  const workspace = useEnterpriseIdentityCenter(scope, context, readiness);
  const mutation = useEnterpriseIdentityMutations({
    scope,
    context,
    domains: workspace.domains,
    providers: workspace.providers,
    scimTokens: workspace.scimTokens,
  });
  const oidcStart = useOidcRuntimeStart({
    scope,
    ...(onOidcNavigate ? { navigate: onOidcNavigate } : {}),
  });
  const startOidcLogin = (provider: EnterpriseIdentityProvider) =>
    oidcStart.start(
      { id: provider.id, name: provider.name },
      buildOidcCallbackUrl(window.location),
    );
  const owner = context.actor.role === "owner";
  const manager = owner || context.actor.role === "admin";
  const copy = async (value: string) => navigator.clipboard.writeText(value);
  if (!workspace.capability) return null;
  if (!workspace.readable)
    return (
      <section
        className="enterprise-identity-center enterprise-admin-surface"
        role="region"
        aria-label="企业身份联合中心"
      >
        <Alert
          theme="warning"
          title="企业身份联合控制面尚未接入"
          message={
            workspace.capability.reason || "服务端未声明 identity_federation capability ready"
          }
        />
      </section>
    );

  const domainColumns: PrimaryTableCol<EnterpriseVerifiedDomain>[] = [
    {
      colKey: "domain",
      title: "可信域名",
      width: 220,
      cell: ({ row }) => (
        <div className="identity-entity">
          <strong>{row.domain}</strong>
          <span>{row.id}</span>
        </div>
      ),
    },
    {
      colKey: "status",
      title: "状态",
      width: 110,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {row.status}
        </Tag>
      ),
    },
    {
      colKey: "txt",
      title: "DNS TXT",
      width: 330,
      cell: ({ row }) => (
        <div className="identity-dns-evidence">
          <span>{row.txt_host}</span>
          <strong>{row.txt_value}</strong>
        </div>
      ),
    },
    {
      colKey: "revision",
      title: "revision",
      width: 100,
      cell: ({ row }) => `revision ${row.revision}`,
    },
    {
      colKey: "operation",
      title: "操作",
      width: 260,
      fixed: "right",
      cell: ({ row }) => (
        <div className="identity-row-actions">
          <Button
            variant="text"
            aria-label={`复制 ${row.domain} DNS TXT host`}
            icon={<CopyIcon />}
            onClick={() => void copy(row.txt_host)}
          >
            复制 host
          </Button>
          <Button
            variant="text"
            theme="primary"
            aria-label={`验证 ${row.domain} 域名`}
            icon={<RefreshIcon />}
            disabled={!manager || row.status === "revoked"}
            onClick={() => setReasonAction({ kind: "verify-domain", domain: row })}
          >
            验证
          </Button>
          <Button
            variant="text"
            theme="danger"
            aria-label={`撤销 ${row.domain} 域名`}
            icon={<DeleteIcon />}
            disabled={!manager || row.status === "revoked"}
            onClick={() => setReasonAction({ kind: "revoke-domain", domain: row })}
          >
            撤销
          </Button>
        </div>
      ),
    },
  ];
  const providerColumns: PrimaryTableCol<EnterpriseIdentityProvider>[] = [
    {
      colKey: "name",
      title: "身份提供商",
      width: 220,
      cell: ({ row }) => (
        <div className="identity-entity">
          <strong>{row.name}</strong>
          <span>
            {row.provider_type.toUpperCase()} · {row.id}
          </span>
        </div>
      ),
    },
    {
      colKey: "status",
      title: "状态",
      width: 110,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {row.status}
        </Tag>
      ),
    },
    {
      colKey: "validation",
      title: "validation",
      width: 130,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.validation_state)} variant="light-outline">
          {row.validation_state}
        </Tag>
      ),
    },
    {
      colKey: "runtime",
      title: "运行面",
      width: 180,
      cell: ({ row }) => row.runtime_state,
    },
    {
      colKey: "revision",
      title: "revision",
      width: 100,
      cell: ({ row }) => `revision ${row.revision}`,
    },
    {
      colKey: "operation",
      title: "操作",
      width: 220,
      fixed: "right",
      cell: ({ row }) => (
        <div className="identity-row-actions">
          {workspace.oidcRuntime.ready && row.id === workspace.oidcRuntime.provider_id ? (
            <Button
              variant="text"
              theme="primary"
              icon={<LoginIcon />}
              aria-label={`Start Login ${row.name}`}
              loading={oidcStart.saving}
              onClick={() => void startOidcLogin(row)}
            >
              Start Login
            </Button>
          ) : null}
          {row.status !== "active" ? (
            <Button
              variant="text"
              theme="primary"
              aria-label={`激活 ${row.name}`}
              disabled={!owner || row.validation_state !== "valid"}
              onClick={() => setReasonAction({ kind: "activate-provider", provider: row })}
            >
              激活
            </Button>
          ) : (
            <Button
              variant="text"
              theme="danger"
              aria-label={`停用 ${row.name}`}
              disabled={!owner}
              onClick={() => setReasonAction({ kind: "disable-provider", provider: row })}
            >
              停用
            </Button>
          )}
        </div>
      ),
    },
  ];
  const scimColumns: PrimaryTableCol<EnterpriseScimToken>[] = [
    {
      colKey: "name",
      title: "Token",
      width: 200,
      cell: ({ row }) => (
        <div className="identity-entity">
          <strong>{row.name}</strong>
          <span>{row.prefix}</span>
        </div>
      ),
    },
    { colKey: "scopes", title: "Scopes", width: 240, cell: ({ row }) => row.scopes.join(", ") },
    {
      colKey: "status",
      title: "状态",
      width: 110,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {row.status}
        </Tag>
      ),
    },
    {
      colKey: "expires",
      title: "到期时间",
      width: 170,
      cell: ({ row }) => dateLabel(row.expires_at),
    },
    {
      colKey: "usage",
      title: "调用证据",
      width: 220,
      cell: ({ row }) => (
        <div className="identity-scim-token-evidence">
          <strong>{row.use_count ?? 0} 次</strong>
          <span>{row.last_used_at ? dateLabel(row.last_used_at) : "从未使用"}</span>
        </div>
      ),
    },
    {
      colKey: "revision",
      title: "revision",
      width: 100,
      cell: ({ row }) => `revision ${row.revision}`,
    },
    {
      colKey: "operation",
      title: "操作",
      width: 130,
      fixed: "right",
      cell: ({ row }) => (
        <Button
          variant="text"
          theme="danger"
          disabled={!manager || row.status !== "active"}
          onClick={() => setReasonAction({ kind: "revoke-scim", token: row })}
        >
          撤销
        </Button>
      ),
    },
  ];

  return (
    <section
      className="enterprise-identity-center enterprise-admin-surface"
      role="region"
      aria-label="企业身份联合中心"
    >
      <header className="enterprise-identity-header">
        <CertificateIcon aria-hidden="true" />
        <div>
          <span>IDENTITY FEDERATION / CONTROL PLANE</span>
          <h2>企业身份联合中心</h2>
          <p>可信域名、OIDC/SAML 配置与 SCIM token 的可审计控制面。</p>
        </div>
        <Tag theme="primary" variant="light-outline">
          control_plane_ready
        </Tag>
      </header>
      <div className="identity-runtime-evidence">
        <Alert
          theme={workspace.oidcRuntime.ready ? "success" : "warning"}
          title={workspace.oidcRuntime.ready ? "oidc_runtime_ready" : "runtime_not_connected"}
          message={
            workspace.oidcRuntime.ready ? (
              `Authorization Code + PKCE 已连接 active provider ${workspace.oidcRuntime.provider_name}。`
            ) : (
              <span>
                SSO <code>sso_runtime_not_connected</code>；OIDC runtime 尚未满足 0024 + active
                provider。
              </span>
            )
          }
        />
        <Alert
          theme="info"
          title="saml_runtime_not_connected"
          message="Stage 12 不提供 SAML ACS；SAML 配置仍仅是控制面事实。"
        />
        <Alert
          theme={workspace.scimDataPlane.ready ? "success" : "info"}
          title={workspace.scimDataPlane.state}
          message={
            workspace.scimDataPlane.ready
              ? "0022 SCIM provisioning authority 已就绪；支持 Users/Groups 与严格 scope。"
              : "SCIM token 控制面可用，但 0022 Users/Groups provisioning data plane 尚未就绪。"
          }
        />
      </div>
      {workspace.oidcRuntime.ready ? (
        <section className="identity-oidc-runtime" aria-label="OIDC SSO runtime">
          <div>
            <span>OIDC AUTHORIZATION CODE + PKCE</span>
            <h3>{workspace.oidcRuntime.provider_name}</h3>
            <p>callback 只接受一次性 code/state；成功后仅写入既有 KnowledgeActor token 存储键。</p>
          </div>
          {!mobile ? (
            <Button
              className="identity-oidc-start"
              theme="primary"
              icon={<LoginIcon />}
              loading={oidcStart.saving}
              aria-label={`Start Login ${workspace.oidcRuntime.provider_name}`}
              onClick={() => {
                const provider = workspace.providers.items.find(
                  (item) => item.id === workspace.oidcRuntime.provider_id,
                );
                if (provider) void startOidcLogin(provider);
              }}
            >
              Start Login
            </Button>
          ) : null}
        </section>
      ) : null}
      {oidcStart.error ? (
        <Alert
          className="identity-success"
          theme="error"
          title={oidcStart.error.title}
          message={oidcStart.error.message}
        />
      ) : null}
      <ScimDataPlaneEvidence evidence={workspace.scimDataPlane} />
      {mutation.success ? (
        <Alert
          className="identity-success"
          theme="success"
          title={mutation.success}
          message="已更新本地事实并请求刷新权威列表。"
        />
      ) : null}
      <Tabs value={tab} onChange={(value) => setTab(String(value) as TabKey)}>
        <Tabs.TabPanel
          value="domains"
          label={identityTabLabel("可信域名", "domains", tab)}
          destroyOnHide
        >
          <div className="identity-toolbar">
            <Button
              theme="primary"
              icon={<AddIcon />}
              disabled={!manager}
              onClick={() => {
                mutation.clear();
                setDomainCreate(true);
              }}
            >
              声明域名
            </Button>
          </div>
          <ResourceBoundary resource={workspace.domains} label="可信域名" empty="暂无可信域名">
            {mobile ? (
              <div className="identity-mobile-list" data-testid="identity-domain-mobile-list">
                {workspace.domains.items.map((item) => (
                  <article className="identity-domain-card identity-lifecycle-card" key={item.id}>
                    <header>
                      <div>
                        <strong>{item.domain}</strong>
                        <span>{item.id}</span>
                      </div>
                      <Tag theme={statusTheme(item.status)} variant="light-outline">
                        {item.status}
                      </Tag>
                    </header>
                    <dl>
                      <div>
                        <dt>verification</dt>
                        <dd>{item.verification_method}</dd>
                      </div>
                      <div>
                        <dt>revision</dt>
                        <dd>{item.revision}</dd>
                      </div>
                      <div>
                        <dt>TXT host</dt>
                        <dd>{item.txt_host}</dd>
                      </div>
                      <div>
                        <dt>TXT value</dt>
                        <dd>{item.txt_value}</dd>
                      </div>
                    </dl>
                    <Button
                      variant="outline"
                      icon={<MoreIcon />}
                      aria-label={`管理 ${item.domain} 域名`}
                      aria-haspopup="dialog"
                      onClick={() => setDrawerObject({ type: "domain", item })}
                    >
                      管理域名
                    </Button>
                  </article>
                ))}
              </div>
            ) : (
              <div
                className="identity-desktop identity-table-viewport"
                tabIndex={0}
                aria-label="可信域名表格，可横向滚动"
              >
                <PrimaryTable<EnterpriseVerifiedDomain>
                  rowKey="id"
                  data={workspace.domains.items}
                  columns={domainColumns}
                  tableLayout="fixed"
                  bordered={false}
                  hover
                  size="small"
                />
              </div>
            )}
          </ResourceBoundary>
        </Tabs.TabPanel>
        <Tabs.TabPanel
          value="providers"
          label={identityTabLabel("OIDC/SAML", "providers", tab)}
          destroyOnHide
        >
          <div className="identity-toolbar">
            <Button
              theme="primary"
              icon={<SettingIcon />}
              disabled={!manager || !workspace.domains.items.length}
              onClick={() => {
                mutation.clear();
                setWizard(true);
              }}
            >
              配置身份提供商
            </Button>
          </div>
          <ResourceBoundary
            resource={workspace.providers}
            label="身份提供商"
            empty="暂无身份提供商配置"
          >
            {mobile ? (
              <div className="identity-mobile-list">
                {workspace.providers.items.map((item) => (
                  <article className="identity-lifecycle-card" key={item.id}>
                    <header>
                      <strong>{item.name}</strong>
                      <Tag theme={statusTheme(item.status)} variant="light-outline">
                        {item.status}
                      </Tag>
                    </header>
                    <p>
                      {item.provider_type.toUpperCase()} · {item.validation_state} ·{" "}
                      {item.runtime_state}
                    </p>
                    {workspace.oidcRuntime.ready &&
                    item.id === workspace.oidcRuntime.provider_id ? (
                      <Button
                        className="identity-oidc-start"
                        theme="primary"
                        icon={<LoginIcon />}
                        aria-label={`Start Login ${item.name}`}
                        loading={oidcStart.saving}
                        onClick={() => void startOidcLogin(item)}
                      >
                        Start Login
                      </Button>
                    ) : null}
                    <Button
                      variant="outline"
                      onClick={() => setDrawerObject({ type: "provider", item })}
                    >
                      管理提供商
                    </Button>
                  </article>
                ))}
              </div>
            ) : (
              <div
                className="identity-desktop identity-table-viewport"
                tabIndex={0}
                aria-label="身份提供商表格，可横向滚动"
              >
                <PrimaryTable<EnterpriseIdentityProvider>
                  rowKey="id"
                  data={workspace.providers.items}
                  columns={providerColumns}
                  tableLayout="fixed"
                  bordered={false}
                  hover
                  size="small"
                />
              </div>
            )}
          </ResourceBoundary>
        </Tabs.TabPanel>
        <Tabs.TabPanel
          value="scim"
          label={identityTabLabel("SCIM tokens", "scim", tab)}
          destroyOnHide
        >
          <div className="identity-toolbar">
            <Button
              theme="primary"
              icon={<KeyIcon />}
              disabled={!manager}
              onClick={() => {
                mutation.clear();
                setScimIssue(true);
              }}
            >
              签发 SCIM token
            </Button>
          </div>
          <ResourceBoundary
            resource={workspace.scimTokens}
            label="SCIM token"
            empty="暂无 SCIM token"
          >
            {mobile ? (
              <div className="identity-mobile-list">
                {workspace.scimTokens.items.map((item) => (
                  <article className="identity-lifecycle-card" key={item.id}>
                    <header>
                      <strong>{item.name}</strong>
                      <Tag theme={statusTheme(item.status)} variant="light-outline">
                        {item.status}
                      </Tag>
                    </header>
                    <dl className="identity-scim-token-evidence identity-scim-token-evidence--card">
                      <div>
                        <dt>Prefix / revision</dt>
                        <dd>
                          {item.prefix} · revision {item.revision}
                        </dd>
                      </div>
                      <div>
                        <dt>Scopes</dt>
                        <dd>{item.scopes.join(", ") || "未返回"}</dd>
                      </div>
                      <div>
                        <dt>Last used / use count</dt>
                        <dd>
                          {item.last_used_at ? dateLabel(item.last_used_at) : "从未使用"} ·{" "}
                          {item.use_count ?? 0} 次
                        </dd>
                      </div>
                    </dl>
                    <Button
                      variant="outline"
                      aria-label={`管理 ${item.name} SCIM token`}
                      aria-haspopup="dialog"
                      onClick={() => setDrawerObject({ type: "scim", item })}
                    >
                      管理 token
                    </Button>
                  </article>
                ))}
              </div>
            ) : (
              <div
                className="identity-desktop identity-table-viewport"
                tabIndex={0}
                aria-label="SCIM token 表格，可横向滚动"
              >
                <PrimaryTable<EnterpriseScimToken>
                  rowKey="id"
                  data={workspace.scimTokens.items}
                  columns={scimColumns}
                  tableLayout="fixed"
                  bordered={false}
                  hover
                  size="small"
                />
              </div>
            )}
          </ResourceBoundary>
        </Tabs.TabPanel>
      </Tabs>
      <DomainCreateDialog
        visible={domainCreate}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setDomainCreate(false)}
        onRetry={mutation.retry}
        onSubmit={mutation.createDomain}
      />
      <IdentityProviderWizard
        visible={wizard}
        domains={workspace.domains.items}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setWizard(false)}
        onRetry={mutation.retry}
        onRefresh={workspace.providers.reload}
        onSubmit={mutation.createProvider}
      />
      <ScimTokenIssueDialog
        visible={scimIssue}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setScimIssue(false)}
        onRetry={mutation.retry}
        onSubmit={mutation.issueScim}
        dataPlaneReady={workspace.scimDataPlane.ready}
      />
      <ScimTokenDeliveryDialog
        delivery={mutation.scimDelivery}
        onClose={mutation.clearScimDelivery}
        dataPlaneReady={workspace.scimDataPlane.ready}
      />
      <ReasonDialog
        action={reasonAction}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setReasonAction(null)}
        onRetry={mutation.retry}
        onSubmit={(payload) => {
          if (!reasonAction) return Promise.resolve(null);
          switch (reasonAction.kind) {
            case "verify-domain":
              return mutation.verifyDomain(reasonAction.domain, payload);
            case "revoke-domain":
              return mutation.revokeDomain(reasonAction.domain, payload);
            case "activate-provider":
              return mutation.activate(reasonAction.provider, payload);
            case "disable-provider":
              return mutation.disable(reasonAction.provider, payload);
            case "revoke-scim":
              return mutation.revokeScim(reasonAction.token, payload);
          }
        }}
      />
      <Drawer
        visible={Boolean(drawerObject)}
        header="企业身份对象操作"
        size={mobile ? "100%" : "420px"}
        placement={mobile ? "bottom" : "right"}
        footer={null}
        destroyOnClose
        className="identity-action-drawer"
        onClose={() => setDrawerObject(null)}
      >
        {drawerObject ? (
          <section
            className="identity-action-drawer__content"
            role="dialog"
            aria-modal="true"
            aria-label="企业身份对象操作"
          >
            {drawerObject.type === "domain" ? (
              <>
                <strong>{(drawerObject.item as EnterpriseVerifiedDomain).domain}</strong>
                <Button
                  theme="primary"
                  variant="outline"
                  onClick={() => {
                    setReasonAction({
                      kind: "verify-domain",
                      domain: drawerObject.item as EnterpriseVerifiedDomain,
                    });
                    setDrawerObject(null);
                  }}
                >
                  验证域名
                </Button>
                <Button
                  theme="danger"
                  variant="outline"
                  onClick={() => {
                    setReasonAction({
                      kind: "revoke-domain",
                      domain: drawerObject.item as EnterpriseVerifiedDomain,
                    });
                    setDrawerObject(null);
                  }}
                >
                  撤销域名
                </Button>
              </>
            ) : drawerObject.type === "provider" ? (
              <>
                <strong>{(drawerObject.item as EnterpriseIdentityProvider).name}</strong>
                <Button
                  theme="primary"
                  variant="outline"
                  disabled={!owner}
                  onClick={() => {
                    setReasonAction({
                      kind: "activate-provider",
                      provider: drawerObject.item as EnterpriseIdentityProvider,
                    });
                    setDrawerObject(null);
                  }}
                >
                  激活提供商
                </Button>
              </>
            ) : (
              <>
                <strong>{(drawerObject.item as EnterpriseScimToken).name}</strong>
                <dl className="identity-scim-token-evidence identity-scim-token-evidence--drawer">
                  <div>
                    <dt>Prefix</dt>
                    <dd>{(drawerObject.item as EnterpriseScimToken).prefix}</dd>
                  </div>
                  <div>
                    <dt>Scopes</dt>
                    <dd>{(drawerObject.item as EnterpriseScimToken).scopes.join(", ")}</dd>
                  </div>
                  <div>
                    <dt>Last used</dt>
                    <dd>
                      {(drawerObject.item as EnterpriseScimToken).last_used_at
                        ? dateLabel((drawerObject.item as EnterpriseScimToken).last_used_at)
                        : "从未使用"}
                    </dd>
                  </div>
                  <div>
                    <dt>Use count</dt>
                    <dd>{(drawerObject.item as EnterpriseScimToken).use_count ?? 0} 次</dd>
                  </div>
                </dl>
                <Button
                  theme="danger"
                  variant="outline"
                  onClick={() => {
                    setReasonAction({
                      kind: "revoke-scim",
                      token: drawerObject.item as EnterpriseScimToken,
                    });
                    setDrawerObject(null);
                  }}
                >
                  撤销 token
                </Button>
              </>
            )}
          </section>
        ) : null}
      </Drawer>
    </section>
  );
}
