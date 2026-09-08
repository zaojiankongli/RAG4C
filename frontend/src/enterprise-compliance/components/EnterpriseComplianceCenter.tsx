import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  Drawer,
  Form,
  Input,
  InputNumber,
  PrimaryTable,
  Select,
  Steps,
  Tabs,
  Tag,
  Textarea,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  CloudDownloadIcon,
  DeleteIcon,
  FileExportIcon,
  LockOnIcon,
  MoreIcon,
  RefreshIcon,
  SettingIcon,
  SecuredIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import { downloadAuditExport } from "../api/enterpriseComplianceApi";
import type {
  AuditExportFormat,
  AuditExportInput,
  AuditExportJob,
  AuditLegalHold,
  AuditRetentionPolicy,
  LegalHoldInput,
  RetentionExecuteInput,
  RetentionPolicyInput,
  RetentionPreview,
  RevisionReasonInput,
} from "../enterpriseComplianceModel";
import { useEnterpriseComplianceCenter } from "../hooks/useEnterpriseComplianceCenter";
import { useEnterpriseComplianceMutations } from "../hooks/useEnterpriseComplianceMutations";
import "../enterprise-compliance.css";

type TabKey = "retention" | "holds" | "exports";
type DrawerObject =
  { type: "hold"; item: AuditLegalHold } | { type: "export"; item: AuditExportJob };
function useComplianceMobile() {
  const query = "(max-width: 600px)";
  const [mobile, setMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(query).matches === true,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia(query);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return mobile;
}
function tabLabel(label: string, value: TabKey, active: TabKey) {
  return (
    <span role="tab" aria-selected={value === active} tabIndex={value === active ? 0 : -1}>
      {label}
    </span>
  );
}
function dateLabel(value?: string) {
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
function statusTheme(status: string): "success" | "warning" | "danger" | "default" {
  if (["active", "completed"].includes(status)) return "success";
  if (["queued", "running", "paused"].includes(status)) return "warning";
  if (["released", "failed", "expired"].includes(status)) return "danger";
  return "default";
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="compliance-form-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function ErrorAlert({
  error,
  onRetry,
  onRefresh,
}: {
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
}) {
  if (!error) return null;
  return (
    <div className="compliance-mutation-error" role="alert">
      <Alert theme="error" title="合规操作未提交" message={error.message} />
      {error.retryAvailable ? (
        <Button variant="text" onClick={() => void onRetry()}>
          使用同一请求重试
        </Button>
      ) : null}
      {error.needsRefresh ? (
        <Button variant="text" onClick={() => void onRefresh()}>
          刷新合规事实
        </Button>
      ) : null}
    </div>
  );
}

function PolicyDialog({
  policy,
  visible,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: {
  policy: AuditRetentionPolicy | null;
  visible: boolean;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (input: RetentionPolicyInput) => Promise<AuditRetentionPolicy | null>;
}) {
  const [auditDays, setAuditDays] = useState(365);
  const [exportDays, setExportDays] = useState(30);
  const [status, setStatus] = useState("active");
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (visible && policy) {
      setAuditDays(policy.audit_retention_days);
      setExportDays(policy.export_retention_days);
      setStatus(policy.status);
      setReason("");
    }
  }, [policy, visible]);
  if (!visible || !policy) return null;
  const submit = async () => {
    if (!reason.trim()) return;
    const result = await onSubmit({
      audit_retention_days: auditDays,
      export_retention_days: exportDays,
      status,
      revision: policy.revision,
      reason: reason.trim(),
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="更新审计保留策略"
      width={600}
      destroyOnClose
      confirmBtn={{ content: "保存策略", theme: "primary" }}
      confirmLoading={saving}
      cancelBtn={{ content: "取消", disabled: saving }}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "更新审计保留策略" } as Record<
        string,
        unknown
      >)}
    >
      <ErrorAlert error={error} onRetry={onRetry} onRefresh={onRefresh} />
      <Form className="compliance-form-grid" labelAlign="top">
        <Form.FormItem>
          <Field label="审计保留天数">
            <InputNumber
              min={30}
              max={3650}
              value={auditDays}
              onChange={(value) => setAuditDays(Number(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="导出保留天数">
            <InputNumber
              min={1}
              max={365}
              value={exportDays}
              onChange={(value) => setExportDays(Number(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="策略状态">
            <Select
              value={status}
              options={[
                { label: "active", value: "active" },
                { label: "paused", value: "paused" },
              ]}
              onChange={(value) => setStatus(String(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="revision">
            <InputNumber value={policy.revision} readOnly />
          </Field>
        </Form.FormItem>
        <Form.FormItem className="compliance-form-wide">
          <Field label="变更原因">
            <Textarea value={reason} onChange={(value) => setReason(String(value))} />
          </Field>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}
function ExecuteDialog({
  preview,
  visible,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: {
  preview: RetentionPreview | null;
  visible: boolean;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (input: RetentionExecuteInput) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (visible) {
      setReason("");
      setConfirmation("");
      setConfirmed(false);
    }
  }, [visible]);
  if (!visible || !preview) return null;
  const valid = reason.trim() && confirmation === "EXECUTE RETENTION" && confirmed;
  const submit = async () => {
    if (!valid) return;
    const result = await onSubmit({
      policy_revision: preview.policy_revision,
      preview_fingerprint: preview.preview_fingerprint,
      reason: reason.trim(),
      confirmation,
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="执行审计保留删除"
      width={620}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{ content: "执行不可逆删除", theme: "danger", disabled: !valid }}
      confirmLoading={saving}
      cancelBtn={{ content: "取消", disabled: saving }}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "执行审计保留删除" } as Record<
        string,
        unknown
      >)}
    >
      <Alert
        theme="error"
        title="manual_execution_only"
        message="Stage 11 不启用自动调度；只有 tenant owner 可基于当前 preview fingerprint 手工执行。开发环境禁止真实生产删除。"
      />
      <ErrorAlert error={error} onRetry={onRetry} onRefresh={onRefresh} />
      <Form labelAlign="top">
        <Form.FormItem>
          <Field label="policy revision">
            <InputNumber
              value={preview.policy_revision}
              min={1}
              step={1}
              decimalPlaces={0}
              theme="normal"
              readOnly
              inputProps={{ "aria-label": "policy revision", inputMode: "numeric" } as never}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="preview fingerprint">
            <Input value={preview.preview_fingerprint} readOnly />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="业务原因">
            <Textarea value={reason} onChange={(value) => setReason(String(value))} />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="确认字符串">
            <Input
              value={confirmation}
              placeholder="EXECUTE RETENTION"
              onChange={(value) => setConfirmation(String(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Checkbox checked={confirmed} onChange={(value) => setConfirmed(Boolean(value))}>
            我已核对 {preview.deletable_count} 条可删除记录及 {preview.protected_count} 条受保护记录
          </Checkbox>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}
function HoldCreateDialog({
  visible,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: {
  visible: boolean;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (input: LegalHoldInput) => Promise<unknown>;
}) {
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [start, setStart] = useState<number | undefined>();
  const [end, setEnd] = useState<number | undefined>();
  useEffect(() => {
    if (visible) {
      setName("");
      setReason("");
      setStart(undefined);
      setEnd(undefined);
    }
  }, [visible]);
  if (!visible) return null;
  const submit = async () => {
    if (!name.trim() || !reason.trim()) return;
    const result = await onSubmit({
      name: name.trim(),
      reason: reason.trim(),
      ...(start !== undefined ? { sequence_start: start } : {}),
      ...(end !== undefined ? { sequence_end: end } : {}),
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="创建 legal hold"
      width={600}
      destroyOnClose
      confirmBtn={{ content: "创建 legal hold", theme: "primary" }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "创建 legal hold" } as Record<
        string,
        unknown
      >)}
    >
      <ErrorAlert error={error} onRetry={onRetry} onRefresh={onRefresh} />
      <Alert
        theme="info"
        title="保全优先"
        message="匹配 active legal hold 的审计行不会被 retention execute 删除。"
      />
      <Form className="compliance-form-grid" labelAlign="top">
        <Form.FormItem>
          <Field label="Hold 名称">
            <Input value={name} onChange={(value) => setName(String(value))} />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="原因">
            <Input value={reason} onChange={(value) => setReason(String(value))} />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="起始 sequence">
            <InputNumber
              min={0}
              value={start}
              onChange={(value) => setStart(value === undefined ? undefined : Number(value))}
            />
          </Field>
        </Form.FormItem>
        <Form.FormItem>
          <Field label="结束 sequence">
            <InputNumber
              min={0}
              value={end}
              onChange={(value) => setEnd(value === undefined ? undefined : Number(value))}
            />
          </Field>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}
function ReleaseDialog({
  hold,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: {
  hold: AuditLegalHold | null;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (hold: AuditLegalHold, input: RevisionReasonInput) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => setReason(""), [hold]);
  if (!hold) return null;
  const submit = async () => {
    if (!reason.trim()) return;
    const result = await onSubmit(hold, { revision: hold.revision, reason: reason.trim() });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="释放 legal hold"
      width={540}
      destroyOnClose
      confirmBtn={{ content: "释放 legal hold", theme: "danger" }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "释放 legal hold" } as Record<
        string,
        unknown
      >)}
    >
      <ErrorAlert error={error} onRetry={onRetry} onRefresh={onRefresh} />
      <Alert
        theme="warning"
        title={hold.name}
        message={`释放后 revision ${hold.revision} 对应范围不再受本 hold 保护。`}
      />
      <Field label="释放原因">
        <Textarea value={reason} onChange={(value) => setReason(String(value))} />
      </Field>
    </Dialog>
  );
}
function ExportWizard({
  visible,
  saving,
  error,
  onClose,
  onRetry,
  onRefresh,
  onSubmit,
}: {
  visible: boolean;
  saving: boolean;
  error: ReturnType<typeof useEnterpriseComplianceMutations>["error"];
  onClose: () => void;
  onRetry: () => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onSubmit: (input: AuditExportInput) => Promise<unknown>;
}) {
  const [step, setStep] = useState(0);
  const [format, setFormat] = useState<AuditExportFormat>("ndjson");
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [resource, setResource] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (visible) {
      setStep(0);
      setFormat("ndjson");
      setAction("");
      setActor("");
      setResource("");
      setReason("");
    }
  }, [visible]);
  if (!visible) return null;
  const submit = async () => {
    if (!reason.trim()) return;
    const result = await onSubmit({
      format,
      filters: {
        ...(action.trim() ? { action: action.trim() } : {}),
        ...(actor.trim() ? { actor: actor.trim() } : {}),
        ...(resource.trim() ? { resource: resource.trim() } : {}),
      },
      reason: reason.trim(),
    });
    if (result) onClose();
  };
  return (
    <Dialog
      visible
      header="创建审计导出"
      width={720}
      destroyOnClose
      footer={false}
      onClose={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "创建审计导出" } as Record<
        string,
        unknown
      >)}
    >
      <ErrorAlert error={error} onRetry={onRetry} onRefresh={onRefresh} />
      <Alert
        theme="info"
        title="仅允许审计字段白名单过滤"
        message="支持 actor/action/resource/request/time；不接受任意 SQL、绝对路径或存储根目录。"
      />
      <Steps current={step}>
        <Steps.StepItem title="格式" />
        <Steps.StepItem title="过滤" />
        <Steps.StepItem title="审阅" />
      </Steps>
      <Form className="compliance-export-form" labelAlign="top">
        {step === 0 ? (
          <Form.FormItem>
            <Field label="导出格式">
              <Select
                value={format}
                options={[
                  { label: "NDJSON", value: "ndjson" },
                  { label: "CSV", value: "csv" },
                ]}
                onChange={(value) => setFormat(String(value) as AuditExportFormat)}
              />
            </Field>
          </Form.FormItem>
        ) : null}
        {step === 1 ? (
          <>
            <Form.FormItem>
              <Field label="Action">
                <Input value={action} onChange={(value) => setAction(String(value))} />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Actor">
                <Input value={actor} onChange={(value) => setActor(String(value))} />
              </Field>
            </Form.FormItem>
            <Form.FormItem>
              <Field label="Resource">
                <Input value={resource} onChange={(value) => setResource(String(value))} />
              </Field>
            </Form.FormItem>
          </>
        ) : null}
        {step === 2 ? (
          <Form.FormItem>
            <Field label="导出原因">
              <Textarea value={reason} onChange={(value) => setReason(String(value))} />
            </Field>
          </Form.FormItem>
        ) : null}
      </Form>
      <div className="compliance-wizard-actions">
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
            创建导出作业
          </Button>
        )}
      </div>
    </Dialog>
  );
}

export default function EnterpriseComplianceCenter({
  scope,
  context,
}: {
  scope: EnterpriseScope;
  context: EnterpriseContext;
}) {
  const mobile = useComplianceMobile();
  const [tab, setTab] = useState<TabKey>("retention");
  const [policyDialog, setPolicyDialog] = useState(false);
  const [executeDialog, setExecuteDialog] = useState(false);
  const [holdCreate, setHoldCreate] = useState(false);
  const [releaseHold, setReleaseHold] = useState<AuditLegalHold | null>(null);
  const [exportWizard, setExportWizard] = useState(false);
  const [drawerObject, setDrawerObject] = useState<DrawerObject | null>(null);
  const workspace = useEnterpriseComplianceCenter(scope, context);
  const mutation = useEnterpriseComplianceMutations({ scope, context, reload: workspace.reload });
  const owner = context.actor.role === "owner";
  const manager = owner || context.actor.role === "admin";
  if (!workspace.capability) return null;
  if (!workspace.readable)
    return (
      <section
        className="enterprise-compliance-center enterprise-admin-surface"
        role="region"
        aria-label="企业审计合规中心"
      >
        <Alert
          theme="warning"
          title="审计合规控制面尚未接入"
          message={workspace.capability.reason || "服务端未声明 audit_compliance capability ready"}
        />
      </section>
    );
  if ((workspace.status === "loading" || workspace.status === "idle") && !workspace.policy)
    return (
      <section
        className="enterprise-compliance-center enterprise-admin-surface"
        role="region"
        aria-label="企业审计合规中心"
      >
        <PageState
          compact
          status="loading"
          title="正在读取企业审计合规事实"
          description="读取 policy、legal holds 与 export jobs"
        />
      </section>
    );
  if (workspace.error && !workspace.policy)
    return (
      <section
        className="enterprise-compliance-center enterprise-admin-surface"
        role="region"
        aria-label="企业审计合规中心"
      >
        <PageState
          compact
          status="error"
          title={workspace.error.title}
          description={workspace.error.description}
          extra={
            workspace.error.canRetry ? (
              <Button onClick={() => void workspace.reload()}>重新读取</Button>
            ) : undefined
          }
        />
      </section>
    );
  const policy = workspace.policy;
  const holdColumns: PrimaryTableCol<AuditLegalHold>[] = [
    {
      colKey: "name",
      title: "Legal hold",
      width: 220,
      cell: ({ row }) => (
        <div className="compliance-entity">
          <strong>{row.name}</strong>
          <span>{row.id}</span>
        </div>
      ),
    },
    {
      colKey: "scope",
      title: "保全范围",
      width: 180,
      cell: ({ row }) => `sequence ${row.sequence_start ?? "*"}–${row.sequence_end ?? "*"}`,
    },
    {
      colKey: "status",
      title: "状态",
      width: 100,
      cell: ({ row }) => <Tag theme={statusTheme(row.status)}>{row.status}</Tag>,
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
          aria-label={`释放 ${row.name} legal hold`}
          disabled={!owner || row.status !== "active"}
          onClick={() => setReleaseHold(row)}
        >
          释放
        </Button>
      ),
    },
  ];
  const exportColumns: PrimaryTableCol<AuditExportJob>[] = [
    {
      colKey: "job",
      title: "导出作业",
      width: 190,
      cell: ({ row }) => (
        <div className="compliance-entity">
          <strong>{row.id}</strong>
          <span>{row.format.toUpperCase()}</span>
        </div>
      ),
    },
    {
      colKey: "status",
      title: "状态",
      width: 100,
      cell: ({ row }) => <Tag theme={statusTheme(row.status)}>{row.status}</Tag>,
    },
    { colKey: "rows", title: "规模", width: 120, cell: ({ row }) => `${row.row_count ?? 0} rows` },
    {
      colKey: "hash",
      title: "SHA-256",
      width: 330,
      cell: ({ row }) => <code className="compliance-hash">{row.sha256 || "尚未生成"}</code>,
    },
    { colKey: "expires", title: "到期", width: 170, cell: ({ row }) => dateLabel(row.expires_at) },
    {
      colKey: "operation",
      title: "操作",
      width: 150,
      fixed: "right",
      cell: ({ row }) => (
        <Button
          variant="text"
          icon={<CloudDownloadIcon />}
          aria-label={`下载 ${row.id} 审计导出`}
          disabled={row.status !== "completed" || !row.sha256}
          onClick={() => void handleDownload(row)}
        >
          下载
        </Button>
      ),
    },
  ];
  const handleDownload = async (job: AuditExportJob) => {
    const result = await downloadAuditExport(scope, job.id);
    const url = URL.createObjectURL(result.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section
      className="enterprise-compliance-center enterprise-admin-surface"
      role="region"
      aria-label="企业审计合规中心"
    >
      <header className="compliance-header">
        <SecuredIcon aria-hidden="true" />
        <div>
          <span>AUDIT COMPLIANCE / CONTROL PLANE</span>
          <h2>企业审计合规中心</h2>
          <p>保留策略、法律保全和可校验审计导出的企业控制面。</p>
        </div>
        <Tag theme="primary" variant="light-outline">
          0023_control_plane
        </Tag>
      </header>
      <div className="compliance-evidence">
        <Alert
          theme="warning"
          title="manual_execution_only"
          message="无自动 retention scheduler；preview 为只读，execute 仅由 tenant owner 显式确认。"
        />
        <Alert
          theme="info"
          title="tamper_evident_exports"
          message="completed export 显示 SHA-256、行数和字节数；下载前由服务端重新校验。"
        />
      </div>
      {mutation.success ? (
        <Alert
          className="compliance-success"
          theme="success"
          title={mutation.success}
          message="已刷新服务端权威事实。"
        />
      ) : null}
      <Tabs value={tab} onChange={(value) => setTab(String(value) as TabKey)}>
        <Tabs.TabPanel
          value="retention"
          label={tabLabel("保留策略", "retention", tab)}
          destroyOnHide
        >
          <div className="compliance-toolbar">
            <Button
              variant="outline"
              icon={<SettingIcon />}
              disabled={!owner || !policy}
              onClick={() => {
                mutation.clear();
                setPolicyDialog(true);
              }}
            >
              更新策略
            </Button>
            <Button
              theme="primary"
              icon={<RefreshIcon />}
              disabled={!manager || !policy}
              onClick={() => void workspace.runPreview()}
            >
              预览保留影响
            </Button>
          </div>
          {policy ? (
            <div className="compliance-policy-grid">
              <article className="compliance-policy-card">
                <header>
                  <div>
                    <span>RETENTION POLICY</span>
                    <h3>{policy.status}</h3>
                  </div>
                  <Tag theme={statusTheme(policy.status)}>revision {policy.revision}</Tag>
                </header>
                <dl>
                  <div>
                    <dt>Audit retention</dt>
                    <dd>{policy.audit_retention_days} 天</dd>
                  </div>
                  <div>
                    <dt>Export retention</dt>
                    <dd>{policy.export_retention_days} 天</dd>
                  </div>
                  <div>
                    <dt>Last preview</dt>
                    <dd>{dateLabel(policy.last_preview_at)}</dd>
                  </div>
                  <div>
                    <dt>Last execute</dt>
                    <dd>{dateLabel(policy.last_executed_at)}</dd>
                  </div>
                </dl>
              </article>
              {workspace.preview ? (
                <article className="compliance-preview-card">
                  <header>
                    <div>
                      <span>RETENTION PREVIEW</span>
                      <h3>{workspace.preview.deletable_count} 条可删除</h3>
                    </div>
                    <Tag theme="warning">revision {workspace.preview.policy_revision}</Tag>
                  </header>
                  <div className="compliance-preview-facts">
                    <strong>{workspace.preview.candidate_count} 条候选</strong>
                    <strong>{workspace.preview.protected_count} 条受 legal hold 保护</strong>
                    <span>cutoff {dateLabel(workspace.preview.cutoff_at)}</span>
                    <code>{workspace.preview.preview_fingerprint}</code>
                  </div>
                  <Button
                    theme="danger"
                    icon={<DeleteIcon />}
                    disabled={!owner}
                    onClick={() => {
                      mutation.clear();
                      setExecuteDialog(true);
                    }}
                  >
                    执行审计保留删除
                  </Button>
                </article>
              ) : (
                <PageState
                  compact
                  status="empty"
                  title="尚未生成 retention preview"
                  description="预览只读，不删除任何审计记录。"
                />
              )}
            </div>
          ) : null}
        </Tabs.TabPanel>
        <Tabs.TabPanel value="holds" label={tabLabel("Legal holds", "holds", tab)} destroyOnHide>
          <div className="compliance-toolbar">
            <Button
              theme="primary"
              icon={<AddIcon />}
              disabled={!owner}
              onClick={() => {
                mutation.clear();
                setHoldCreate(true);
              }}
            >
              创建 legal hold
            </Button>
          </div>
          {workspace.holds.length ? (
            mobile ? (
              <div
                className="compliance-mobile-list"
                data-testid="compliance-legal-hold-mobile-list"
              >
                {workspace.holds.map((item) => (
                  <article className="compliance-lifecycle-card" key={item.id}>
                    <header>
                      <strong>{item.name}</strong>
                      <Tag theme={statusTheme(item.status)}>{item.status}</Tag>
                    </header>
                    <p>
                      sequence {item.sequence_start ?? "*"}–{item.sequence_end ?? "*"}
                    </p>
                    <p>revision {item.revision}</p>
                    <Button
                      variant="outline"
                      icon={<MoreIcon />}
                      aria-label={`管理 ${item.name} legal hold`}
                      aria-haspopup="dialog"
                      onClick={() => setDrawerObject({ type: "hold", item })}
                    >
                      管理 legal hold
                    </Button>
                  </article>
                ))}
              </div>
            ) : (
              <div
                className="compliance-desktop compliance-table-viewport"
                tabIndex={0}
                aria-label="Legal hold 表格，可横向滚动"
              >
                <PrimaryTable<AuditLegalHold>
                  rowKey="id"
                  data={workspace.holds}
                  columns={holdColumns}
                  tableLayout="fixed"
                  size="small"
                />
              </div>
            )
          ) : (
            <PageState
              compact
              status="empty"
              title="暂无 legal hold"
              description="服务端已返回真实空列表。"
            />
          )}
        </Tabs.TabPanel>
        <Tabs.TabPanel value="exports" label={tabLabel("审计导出", "exports", tab)} destroyOnHide>
          <div className="compliance-toolbar">
            <Button
              theme="primary"
              icon={<FileExportIcon />}
              disabled={!manager}
              onClick={() => {
                mutation.clear();
                setExportWizard(true);
              }}
            >
              创建审计导出
            </Button>
          </div>
          {workspace.exports.length ? (
            mobile ? (
              <div className="compliance-mobile-list">
                {workspace.exports.map((item) => (
                  <article className="compliance-lifecycle-card" key={item.id}>
                    <header>
                      <strong>{item.id}</strong>
                      <Tag theme={statusTheme(item.status)}>{item.status}</Tag>
                    </header>
                    <p>
                      {item.format.toUpperCase()} · {item.row_count ?? 0} rows
                    </p>
                    <code className="compliance-hash">{item.sha256 || "尚未生成"}</code>
                    <Button
                      variant="outline"
                      aria-label={`管理 ${item.id} 审计导出`}
                      onClick={() => setDrawerObject({ type: "export", item })}
                    >
                      查看导出作业
                    </Button>
                  </article>
                ))}
              </div>
            ) : (
              <div
                className="compliance-desktop compliance-table-viewport"
                tabIndex={0}
                aria-label="审计导出作业表格，可横向滚动"
              >
                <PrimaryTable<AuditExportJob>
                  rowKey="id"
                  data={workspace.exports}
                  columns={exportColumns}
                  tableLayout="fixed"
                  size="small"
                />
              </div>
            )
          ) : (
            <PageState
              compact
              status="empty"
              title="暂无审计导出作业"
              description="服务端已返回真实空列表。"
            />
          )}
        </Tabs.TabPanel>
      </Tabs>
      <PolicyDialog
        policy={policy}
        visible={policyDialog}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setPolicyDialog(false)}
        onRetry={mutation.retry}
        onRefresh={workspace.reload}
        onSubmit={mutation.updatePolicy}
      />
      <ExecuteDialog
        preview={workspace.preview}
        visible={executeDialog}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setExecuteDialog(false)}
        onRetry={mutation.retry}
        onRefresh={workspace.reload}
        onSubmit={mutation.execute}
      />
      <HoldCreateDialog
        visible={holdCreate}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setHoldCreate(false)}
        onRetry={mutation.retry}
        onRefresh={workspace.reload}
        onSubmit={mutation.createHold}
      />
      <ReleaseDialog
        hold={releaseHold}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setReleaseHold(null)}
        onRetry={mutation.retry}
        onRefresh={workspace.reload}
        onSubmit={mutation.releaseHold}
      />
      <ExportWizard
        visible={exportWizard}
        saving={mutation.saving}
        error={mutation.error}
        onClose={() => setExportWizard(false)}
        onRetry={mutation.retry}
        onRefresh={workspace.reload}
        onSubmit={mutation.createExport}
      />
      <Drawer
        visible={Boolean(drawerObject)}
        header="合规对象操作"
        size={mobile ? "100%" : "430px"}
        placement={mobile ? "bottom" : "right"}
        footer={null}
        destroyOnClose
        className="compliance-action-drawer"
        onClose={() => setDrawerObject(null)}
      >
        {drawerObject ? (
          <section
            className="compliance-action-drawer__content"
            role="dialog"
            aria-modal="true"
            aria-label="合规对象操作"
          >
            {drawerObject.type === "hold" ? (
              <>
                <strong>{drawerObject.item.name}</strong>
                <p>revision {drawerObject.item.revision}</p>
                <p>
                  sequence {drawerObject.item.sequence_start ?? "*"}–
                  {drawerObject.item.sequence_end ?? "*"}
                </p>
                <Button
                  theme="danger"
                  variant="outline"
                  icon={<LockOnIcon />}
                  disabled={!owner || drawerObject.item.status !== "active"}
                  onClick={() => {
                    setReleaseHold(drawerObject.item);
                    setDrawerObject(null);
                  }}
                >
                  释放 legal hold
                </Button>
              </>
            ) : (
              <>
                <strong>{drawerObject.item.id}</strong>
                <p>
                  {drawerObject.item.row_count ?? 0} rows · {drawerObject.item.byte_size ?? 0} bytes
                </p>
                <code className="compliance-hash">{drawerObject.item.sha256 || "尚未生成"}</code>
                <Button
                  theme="primary"
                  variant="outline"
                  icon={<CloudDownloadIcon />}
                  disabled={drawerObject.item.status !== "completed" || !drawerObject.item.sha256}
                  onClick={() => void handleDownload(drawerObject.item)}
                >
                  下载导出
                </Button>
              </>
            )}
          </section>
        ) : null}
      </Drawer>
    </section>
  );
}
