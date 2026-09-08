import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  Drawer,
  Form,
  Input,
  InputNumber,
  PrimaryTable,
  Select,
  Tabs,
  Tag,
  Textarea,
  Timeline,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  CheckCircleIcon,
  CloseCircleIcon,
  EditIcon,
  FileIcon,
  RefreshIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import MetricStrip from "../../ui/enterprise/MetricStrip";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  approvalActionLabel,
  approvalApproverLabel,
  approvalChangeFactEntries,
  approvalDecisionLabel,
  approvalStatusLabel,
  canApproveApprovalRequest,
  canCancelApprovalRequest,
  type ApprovalActionType,
  type ApprovalPolicy,
  type ApprovalPolicyApprover,
  type ApprovalRequest,
  type ApprovalRequestDetail,
} from "../enterpriseApprovalModel";
import type { ApprovalPolicyInput, CreateApprovalRequestInput } from "../api/enterpriseApprovalApi";
import { approvalRequestIdFromLocation, clearApprovalRequestNavigation } from "../approvalRoute";
import {
  useEnterpriseApprovalCenter,
  type ApprovalFilters,
  type ApprovalExecutionTarget,
  type ApprovalMutationError,
  type ApprovalResourceError,
  type EnterpriseApprovalWorkspace,
} from "../hooks/useEnterpriseApproval";
import "../enterprise-approval.css";

interface Props {
  scope: EnterpriseScope;
  context: EnterpriseContext;
}

type CenterTab = "requests" | "policies";
type DetailTab = "facts" | "process";

const ACTION_OPTIONS = [
  { label: "全部审批类型", value: "all" },
  { label: "升级目录数据库", value: "catalog_upgrade" },
  { label: "初始化成员体系", value: "membership_bootstrap" },
  { label: "停用知识库 ACL", value: "dataset_acl_disable" },
  { label: "调整成员角色", value: "member_role_change" },
  { label: "变更 Workspace 授权模式", value: "workspace_authorization_mode_change" },
  { label: "转移 Knowledge Base Workspace", value: "dataset_workspace_transfer" },
  { label: "发布 Knowledge Base Release", value: "knowledge_base_release_publish" },
  { label: "回滚 Knowledge Base Release", value: "knowledge_base_release_rollback" },
  { label: "停用身份提供商", value: "identity_provider_disable" },
  { label: "执行审计保留策略", value: "audit_retention_execute" },
];
const STATUS_OPTIONS = [
  { label: "全部状态", value: "all" },
  { label: "待审批", value: "pending" },
  { label: "已批准", value: "approved" },
  { label: "已拒绝", value: "rejected" },
  { label: "已取消", value: "cancelled" },
  { label: "已过期", value: "expired" },
  { label: "已执行", value: "executed" },
  { label: "执行失败", value: "execution_failed" },
];
const MY_APPROVAL_OPTIONS = [
  { label: "全部申请", value: "all" },
  { label: "待我审批", value: "pending" },
  { label: "我提交的申请", value: "mine" },
];
const APPROVER_OPTIONS = [
  { label: "租户所有者", value: "role:owner" },
  { label: "租户管理员", value: "role:admin" },
  { label: "账号：当前审批人", value: "account:current" },
];

function dateTimeLabel(value?: string | null): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function numberLabel(value: number | null): string {
  return value === null ? "未返回" : new Intl.NumberFormat("zh-CN").format(value);
}

function statusTheme(status: string): "success" | "warning" | "danger" | "primary" | "default" {
  if (["approved", "executed", "active"].includes(status)) return "success";
  if (status === "pending") return "primary";
  if (["rejected", "execution_failed"].includes(status)) return "danger";
  if (["expired", "disabled"].includes(status)) return "warning";
  return "default";
}

function tabLabel(label: string, active: boolean) {
  return (
    <span role="tab" aria-selected={active} tabIndex={active ? 0 : -1}>
      {label}
    </span>
  );
}

function ErrorMessage({ error }: { error: ApprovalMutationError | null }) {
  return error ? (
    <div className="approval-mutation-error" role="alert">
      <Alert theme="error" title={error.title} message={error.message} />
    </div>
  ) : null;
}

function ReadError({ error, onRetry }: { error: ApprovalResourceError; onRetry: () => void }) {
  return (
    <PageState
      status="error"
      title={error.title}
      description={error.description}
      extra={
        error.canRetry ? (
          <Button onClick={onRetry} icon={<RefreshIcon />}>
            重新读取
          </Button>
        ) : undefined
      }
    />
  );
}

function EvidenceStrip({ evidence }: { evidence: EnterpriseApprovalWorkspace["evidence"] }) {
  const revisionLabel = evidence.catalog_revision?.split("_", 1)[0] ?? "未返回";
  const adapterLabel = evidence.execution_adapter_status === "connected" ? "已连接" : "未连接";
  return (
    <MetricStrip
      ariaLabel="审批权威证据"
      columns={5}
      metrics={[
        {
          id: "pending",
          label: "待处理申请",
          value: numberLabel(evidence.pending_count),
          unit: evidence.pending_count === null ? undefined : "条",
          tone: "primary",
          icon: <TimeIcon />,
          hint: "由服务端审批事实返回，未返回时不以 0 代替。",
        },
        {
          id: "mine",
          label: "待我审批",
          value: numberLabel(evidence.my_pending_count),
          unit: evidence.my_pending_count === null ? undefined : "条",
          tone: "warning",
          icon: <SecuredIcon />,
          hint: "仅表示服务端明确返回当前身份的待审批数量。",
        },
        {
          id: "rules",
          label: "启用规则",
          value: numberLabel(evidence.active_policy_count),
          unit: evidence.active_policy_count === null ? undefined : "条",
          tone: "success",
          icon: <CheckCircleIcon />,
        },
        {
          id: "revision",
          label: "目录修订",
          value: revisionLabel,
          tone: "neutral",
          icon: <FileIcon />,
          hint: evidence.catalog_revision
            ? `完整目录修订：${evidence.catalog_revision}`
            : "服务端未返回目录修订。",
        },
        {
          id: "adapter",
          label: "下游执行边界",
          value: adapterLabel,
          tone: evidence.execution_adapter_status === "connected" ? "success" : "warning",
          icon: <SecuredIcon />,
          hint: `执行适配器状态：${evidence.execution_adapter_status}`,
        },
      ]}
    />
  );
}

function RequestFilters({
  filters,
  onChange,
  onApply,
}: {
  filters: ApprovalFilters;
  onChange: (next: ApprovalFilters) => void;
  onApply: () => void;
}) {
  return (
    <div className="approval-filters" aria-label="审批申请筛选条件">
      <label>
        <span>工作空间</span>
        <Input
          aria-label="审批申请工作空间"
          value={filters.workspaceId}
          placeholder="知识库 ID"
          onChange={(value) => onChange({ ...filters, workspaceId: String(value) })}
        />
      </label>
      <label>
        <span>状态</span>
        <Select
          aria-label="审批申请状态"
          value={filters.status}
          options={STATUS_OPTIONS}
          onChange={(value) =>
            onChange({ ...filters, status: String(value) as ApprovalFilters["status"] })
          }
        />
      </label>
      <label>
        <span>审批类型</span>
        <Select
          aria-label="审批申请类型"
          value={filters.actionType}
          options={ACTION_OPTIONS}
          onChange={(value) =>
            onChange({ ...filters, actionType: String(value) as ApprovalFilters["actionType"] })
          }
        />
      </label>
      <label>
        <span>我的审批</span>
        <Select
          aria-label="我的审批状态"
          value={filters.myApproval}
          options={MY_APPROVAL_OPTIONS}
          onChange={(value) =>
            onChange({ ...filters, myApproval: String(value) as ApprovalFilters["myApproval"] })
          }
        />
      </label>
      <label className="approval-filter-keyword">
        <span>关键词</span>
        <Input
          aria-label="审批申请关键词"
          value={filters.query}
          placeholder="申请 ID、资源或申请人"
          onChange={(value) => onChange({ ...filters, query: String(value) })}
          onEnter={onApply}
        />
      </label>
      <Button theme="primary" variant="outline" onClick={onApply} icon={<RefreshIcon />}>
        查询
      </Button>
    </div>
  );
}

function RequestProgress({ request }: { request: ApprovalRequest }) {
  return (
    <span
      className="approval-progress"
      aria-label={
        "已收到 " + request.received_approvals + " / " + request.required_approvals + " 个审批"
      }
    >
      {request.received_approvals} / {request.required_approvals}
    </span>
  );
}

function matchesLocalRequestFilters(request: ApprovalRequest, filters: ApprovalFilters): boolean {
  const workspace = filters.workspaceId.trim().toLocaleLowerCase("zh-CN");
  const query = filters.query.trim().toLocaleLowerCase("zh-CN");
  const values = [
    request.id,
    request.resource_id,
    request.resource_name,
    request.requester.id,
    request.requester.name,
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.toLocaleLowerCase("zh-CN"));
  return (
    (!workspace || values.some((value) => value.includes(workspace))) &&
    (!query || values.some((value) => value.includes(query)))
  );
}

function RequestTable({
  requests,
  onOpen,
}: {
  requests: ApprovalRequest[];
  onOpen: (request: ApprovalRequest) => void;
}) {
  const columns: PrimaryTableCol<ApprovalRequest>[] = [
    {
      colKey: "request",
      title: "申请",
      width: 245,
      cell: ({ row }) => (
        <div className="approval-request-identity">
          <strong>{row.resource_name || row.resource_id}</strong>
          <span>
            {row.id} · {row.requester.name}
          </span>
        </div>
      ),
    },
    {
      colKey: "action",
      title: "审批类型",
      width: 160,
      cell: ({ row }) => approvalActionLabel(row.action_type),
    },
    {
      colKey: "status",
      title: "状态",
      width: 100,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {approvalStatusLabel(row.status)}
        </Tag>
      ),
    },
    {
      colKey: "approvals",
      title: "审批进度",
      width: 110,
      cell: ({ row }) => <RequestProgress request={row} />,
    },
    {
      colKey: "expires",
      title: "截止时间",
      width: 170,
      cell: ({ row }) => <time dateTime={row.expires_at}>{dateTimeLabel(row.expires_at)}</time>,
    },
    {
      colKey: "operation",
      title: "操作",
      width: 110,
      cell: ({ row }) => (
        <Button variant="text" onClick={() => onOpen(row)} aria-label={`查看申请 ${row.id}`}>
          查看详情
        </Button>
      ),
    },
  ];
  return (
    <div
      className="approval-request-desktop enterprise-approval-table-viewport"
      data-testid="approval-request-desktop-table"
      tabIndex={0}
      aria-label="审批申请表格，可横向滚动"
    >
      <PrimaryTable
        rowKey="id"
        data={requests}
        columns={columns}
        size="small"
        tableLayout="fixed"
        bordered={false}
        hover
      />
    </div>
  );
}

function RequestCards({
  requests,
  onOpen,
}: {
  requests: ApprovalRequest[];
  onOpen: (request: ApprovalRequest) => void;
}) {
  return (
    <div className="approval-request-mobile" data-testid="approval-request-mobile-list">
      {requests.map((request) => (
        <article key={request.id} className="approval-request-card">
          <header>
            <div>
              <span className="approval-eyebrow">{request.id}</span>
              <h3>{request.resource_name || request.resource_id}</h3>
            </div>
            <Tag theme={statusTheme(request.status)} variant="light-outline">
              {approvalStatusLabel(request.status)}
            </Tag>
          </header>
          <dl>
            <div>
              <dt>审批类型</dt>
              <dd>{approvalActionLabel(request.action_type)}</dd>
            </div>
            <div>
              <dt>申请人</dt>
              <dd>{request.requester.name}</dd>
            </div>
            <div>
              <dt>审批进度</dt>
              <dd>
                <RequestProgress request={request} />
              </dd>
            </div>
            <div>
              <dt>截止时间</dt>
              <dd>{dateTimeLabel(request.expires_at)}</dd>
            </div>
          </dl>
          <Button
            block
            variant="outline"
            onClick={() => onOpen(request)}
            aria-label={`查看申请 ${request.id}`}
          >
            查看详情
          </Button>
        </article>
      ))}
    </div>
  );
}
function SnapshotFacts({ snapshot }: { snapshot: Record<string, unknown> }) {
  const entries = Object.entries(snapshot);
  if (!entries.length)
    return <div className="approval-empty-facts">服务端未返回可展示的变更事实。</div>;
  return (
    <dl className="approval-fact-grid">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>
            {typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ApprovalDetailDrawer({
  detail,
  loading,
  error,
  actorId,
  mutation,
  mobile,
  onClose,
  onRetry,
  onApprove,
  onReject,
  onCancel,
}: {
  detail: ApprovalRequestDetail | null;
  loading: boolean;
  error: ApprovalResourceError | null;
  actorId: string;
  mutation: EnterpriseApprovalWorkspace["mutation"];
  mobile: boolean;
  onClose: () => void;
  onRetry: () => void;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
  onCancel: (request: ApprovalRequest) => void;
}) {
  const [tab, setTab] = useState<DetailTab>("facts");
  useEffect(() => setTab("facts"), [detail?.id]);
  const canApprove = detail ? canApproveApprovalRequest(detail, actorId) : false;
  const decisionDisabled = !canApprove || mutation.saving;
  const canApproveHint = canApprove ? undefined : "服务端未返回当前身份的有效审批资格";
  const canCancel = detail ? canCancelApprovalRequest(detail, actorId) : false;
  return (
    <Drawer
      visible={Boolean(detail) || loading || Boolean(error)}
      header={detail ? `申请详情 ${detail.id}` : "申请详情"}
      size={mobile ? "100%" : "650px"}
      placement={mobile ? "bottom" : "right"}
      destroyOnClose
      className="approval-detail-drawer"
      onClose={onClose}
    >
      {loading ? <PageState compact status="loading" title="正在读取申请详情" /> : null}
      {error ? <ReadError error={error} onRetry={onRetry} /> : null}
      {detail ? (
        <section
          role="dialog"
          aria-modal="true"
          aria-label={`申请详情 ${detail?.id ?? ""}`}
          className="approval-detail-content"
        >
          <div className="approval-detail-actions">
            <Tag theme={statusTheme(detail.status)} variant="light-outline">
              {approvalStatusLabel(detail.status)}
            </Tag>
            <div>
              <Button
                tag="button"
                theme="primary"
                icon={<CheckCircleIcon />}
                aria-disabled={decisionDisabled}
                className={decisionDisabled ? "approval-action-is-disabled" : undefined}
                title={canApproveHint}
                onClick={() => {
                  if (!decisionDisabled) onApprove(detail);
                }}
              >
                同意申请
              </Button>
              <Button
                tag="button"
                theme="danger"
                variant="outline"
                icon={<CloseCircleIcon />}
                aria-disabled={decisionDisabled}
                className={decisionDisabled ? "approval-action-is-disabled" : undefined}
                title={canApproveHint}
                onClick={() => {
                  if (!decisionDisabled) onReject(detail);
                }}
              >
                拒绝申请
              </Button>
              {canCancel ? (
                <Button variant="text" disabled={mutation.saving} onClick={() => onCancel(detail)}>
                  取消申请
                </Button>
              ) : null}
            </div>
          </div>
          <ErrorMessage error={mutation.error} />
          {mutation.success ? <Alert theme="success" title={mutation.success} /> : null}
          <Tabs value={tab} onChange={(value) => setTab(String(value) as DetailTab)}>
            <Tabs.TabPanel value="facts" label={tabLabel("申请详情", tab === "facts")}>
              <section className="approval-detail-section" aria-label="申请变更事实">
                <dl className="approval-detail-meta">
                  <div>
                    <dt>申请 ID</dt>
                    <dd>{detail.id}</dd>
                  </div>
                  <div>
                    <dt>申请人</dt>
                    <dd>
                      {detail.requester.name} · {detail.requester.email || "邮箱未返回"}
                    </dd>
                  </div>
                  <div>
                    <dt>审批类型</dt>
                    <dd>{approvalActionLabel(detail.action_type)}</dd>
                  </div>
                  <div>
                    <dt>资源</dt>
                    <dd>
                      {detail.resource_type} / {detail.resource_id}
                    </dd>
                  </div>
                  <div>
                    <dt>申请原因</dt>
                    <dd>{detail.reason}</dd>
                  </div>
                  <div>
                    <dt>revision</dt>
                    <dd>{detail.revision}</dd>
                  </div>
                </dl>
                <h3>变更事实</h3>
                <SnapshotFacts snapshot={detail.snapshot} />
                <Alert
                  theme={detail.execution_adapter_status === "connected" ? "success" : "warning"}
                  title={detail.execution_adapter_status}
                  message="批准只授予一次性执行授权；当前页面不会自动执行下游变更。"
                />
              </section>
            </Tabs.TabPanel>
            <Tabs.TabPanel value="process" label={tabLabel("审批流程", tab === "process")}>
              <section className="approval-detail-section" aria-label="审批流程时间线">
                {detail.process.length ? (
                  <Timeline className="approval-process-timeline" mode="same" theme="dot">
                    {detail.process.map((event, index) => (
                      <Timeline.Item
                        key={event.id}
                        label={
                          <time dateTime={event.occurred_at}>
                            {dateTimeLabel(event.occurred_at)}
                          </time>
                        }
                        dotColor={index === detail.process.length - 1 ? "primary" : "default"}
                      >
                        <article className="approval-process-event">
                          <strong>{event.actor.name}</strong>
                          <Tag theme={statusTheme(event.status)} variant="light-outline">
                            {approvalStatusLabel(event.status)}
                          </Tag>
                          {event.comment ? <p>{event.comment}</p> : null}
                        </article>
                      </Timeline.Item>
                    ))}
                  </Timeline>
                ) : (
                  <PageState
                    compact
                    status="empty"
                    title="暂无审批流程事件"
                    description="服务端未返回流程时间线，不使用前端默认审批人补齐。"
                  />
                )}
                {detail.decisions.length ? (
                  <div className="approval-decisions">
                    <h3>审批决定</h3>
                    {detail.decisions.map((decision) => (
                      <article
                        key={decision.id || `${decision.approver.id}-${decision.decided_at}`}
                      >
                        <strong>{decision.approver.name}</strong>
                        <Tag theme={statusTheme(decision.decision)} variant="light-outline">
                          {approvalDecisionLabel(decision.decision)}
                        </Tag>
                        <time dateTime={decision.decided_at}>
                          {dateTimeLabel(decision.decided_at)}
                        </time>
                        {decision.comment ? <p>{decision.comment}</p> : null}
                      </article>
                    ))}
                  </div>
                ) : null}
              </section>
            </Tabs.TabPanel>
          </Tabs>
        </section>
      ) : null}
    </Drawer>
  );
}

function ApprovalExecutionDialog({
  target,
  visible,
  ticketAvailable,
  saving,
  error,
  success,
  onClose,
  onConfirm,
}: {
  target: ApprovalExecutionTarget | null;
  visible: boolean;
  ticketAvailable: boolean;
  saving: boolean;
  error: ApprovalMutationError | null;
  success: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!visible || !target) return null;
  const terminal = Boolean(error || success);
  const changeFacts = approvalChangeFactEntries(target.change_facts);
  return (
    <Dialog
      visible
      header="一次性执行授权"
      width={560}
      destroyOnClose
      cancelBtn={terminal ? null : { content: "返回", disabled: saving }}
      confirmBtn={
        terminal
          ? { content: "关闭" }
          : { content: "执行已批准变更", theme: "primary", disabled: !ticketAvailable }
      }
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={terminal ? onClose : onConfirm}
      {...({
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "一次性执行授权",
      } as Record<string, unknown>)}
    >
      <div className="approval-execution-dialog" data-testid="approval-execution-dialog">
        <div className="approval-execution-ticket-banner">
          <div>
            <span className="approval-eyebrow">ONE-TIME EXECUTION AUTHORITY</span>
            <strong>ticket 仅显示/使用一次</strong>
          </div>
          <Tag theme="success" variant="light-outline">
            执行适配器已连接
          </Tag>
        </div>
        <Alert
          theme="warning"
          title="请在受控窗口内执行"
          message="该授权只保存在当前页面内存中。关闭页面或完成消费后立即清除，服务端会拒绝重复消费。"
        />
        <dl className="approval-execution-scope">
          <div>
            <dt>审批类型</dt>
            <dd>{approvalActionLabel(target.action_type)}</dd>
          </div>
          <div>
            <dt>资源</dt>
            <dd>
              {target.resource_type} / {target.resource_id}
            </dd>
          </div>
          <div>
            <dt>执行 revision</dt>
            <dd>{target.revision}</dd>
          </div>
          <div>
            <dt>授权状态</dt>
            <dd>{ticketAvailable ? "等待一次性消费" : saving ? "消费处理中" : "已清除"}</dd>
          </div>
        </dl>
        <div className="approval-execution-facts" aria-label="变更事实">
          <div className="approval-execution-facts-heading">
            <span className="approval-eyebrow">SANITIZED CHANGE FACTS</span>
            <strong>变更事实</strong>
          </div>
          {changeFacts.length ? (
            <dl className="approval-execution-facts-grid">
              {changeFacts.map((fact) => (
                <div key={fact.key}>
                  <dt>{fact.label}</dt>
                  <dd>{fact.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="approval-execution-facts-empty">服务端未返回可展示的变更事实。</p>
          )}
        </div>
        {error ? (
          <div className="approval-execution-error" role="alert">
            <Alert theme="error" title={error.title} message={error.message} />
          </div>
        ) : null}
        {success ? <Alert theme="success" title="变更已执行" message={success} /> : null}
      </div>
    </Dialog>
  );
}

function ApproveDialog({
  request,
  visible,
  saving,
  onClose,
  onConfirm,
}: {
  request: ApprovalRequest | null;
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!visible || !request) return null;
  return (
    <Dialog
      visible
      header="确认同意申请"
      width={500}
      confirmBtn={{ content: "确认同意", theme: "primary" }}
      confirmLoading={saving}
      cancelBtn={{ content: "返回" }}
      onClose={onClose}
      onConfirm={onConfirm}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "确认同意申请" } as Record<
        string,
        unknown
      >)}
    >
      <div className="approval-confirm-copy">
        <strong>{request.resource_name || request.resource_id}</strong>
        <p>
          将记录当前身份的不可变审批决定。达到规则阈值后只生成一次性执行授权，
          <b>不会自动执行下游变更</b>。
        </p>
        <dl>
          <div>
            <dt>当前进度</dt>
            <dd>
              {request.received_approvals} / {request.required_approvals}
            </dd>
          </div>
          <div>
            <dt>revision</dt>
            <dd>{request.revision}</dd>
          </div>
        </dl>
      </div>
    </Dialog>
  );
}

function RejectDialog({
  request,
  visible,
  saving,
  onClose,
  onConfirm,
}: {
  request: ApprovalRequest | null;
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onConfirm: (comment: string) => void;
}) {
  const [comment, setComment] = useState("");
  useEffect(() => {
    if (visible) setComment("");
  }, [visible, request?.id]);
  if (!visible || !request) return null;
  const valid = Boolean(comment.trim()) && comment.length <= 500;
  return (
    <Dialog
      visible
      header={`拒绝申请 ${request.id}`}
      width={560}
      footer={
        <div className="approval-dialog-footer">
          <Button tag="button" variant="text" disabled={saving} onClick={onClose}>
            返回
          </Button>
          <Button
            tag="button"
            theme="danger"
            disabled={!valid}
            loading={saving}
            onClick={() => onConfirm(comment)}
          >
            提交拒绝
          </Button>
        </div>
      }
      onClose={onClose}
      {...({
        role: "dialog",
        "aria-modal": "true",
        "aria-label": `拒绝申请 ${request.id}`,
      } as Record<string, unknown>)}
    >
      <Form labelAlign="top">
        <Form.FormItem>
          <label className="approval-field">
            <span>拒绝原因</span>
            <Textarea
              aria-label="拒绝原因"
              value={comment}
              maxLength={500}
              onChange={(value) => setComment(String(value))}
              placeholder="说明范围、风险或需要补充的事实"
            />
            <small className="approval-reject-counter">{comment.length} / 500</small>
          </label>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}

function CancelDialog({
  request,
  visible,
  saving,
  onClose,
  onConfirm,
}: {
  request: ApprovalRequest | null;
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  if (!visible || !request) return null;
  return (
    <Dialog
      visible
      header="取消审批申请"
      width={480}
      confirmBtn={{ content: "确认取消", theme: "danger" }}
      confirmLoading={saving}
      cancelBtn={{ content: "返回" }}
      onClose={onClose}
      onConfirm={onConfirm}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "取消审批申请" } as Record<
        string,
        unknown
      >)}
    >
      <p>取消后申请将进入终态，当前申请人只能取消自己的待审批申请。</p>
    </Dialog>
  );
}
function PolicyDialog({
  policy,
  visible,
  saving,
  onClose,
  onSubmit,
}: {
  policy: ApprovalPolicy | null;
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onSubmit: (input: ApprovalPolicyInput) => void;
}) {
  const [name, setName] = useState("");
  const [actionType, setActionType] = useState<ApprovalActionType>("dataset_acl_disable");
  const [resourceScope, setResourceScope] = useState("");
  const [required, setRequired] = useState(1);
  const [expiry, setExpiry] = useState(1440);
  const [approvers, setApprovers] = useState<string[]>(["role:owner"]);
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (!visible) return;
    setName(policy?.name ?? "");
    setActionType(policy?.action_type ?? "dataset_acl_disable");
    setResourceScope(policy?.resource_scope ?? "");
    setRequired(policy?.required_approvals ?? 1);
    setExpiry(policy?.request_expiry_minutes ?? 1440);
    setApprovers(policy?.approvers.map((item) => `${item.kind}:${item.ref}`) ?? ["role:owner"]);
    setReason("");
  }, [policy, visible]);
  if (!visible) return null;
  const valid = Boolean(name.trim() && reason.trim() && approvers.length);
  const submit = () => {
    if (!valid) return;
    onSubmit({
      name: name.trim(),
      action_type: actionType,
      resource_scope: resourceScope.trim() || null,
      required_approvals: required,
      request_expiry_minutes: expiry,
      approvers: approvers.map((value) => {
        const [kind, reference] = value.split(":", 2);
        return { kind: kind as ApprovalPolicyApprover["kind"], ref: reference };
      }),
      reason: reason.trim(),
    });
  };
  return (
    <Dialog
      visible
      header={policy ? "编辑审批规则" : "新建审批规则"}
      width={640}
      confirmBtn={{ content: policy ? "保存规则" : "创建规则", theme: "primary", disabled: !valid }}
      confirmLoading={saving}
      cancelBtn={{ content: "取消", disabled: saving }}
      onClose={onClose}
      onConfirm={submit}
      {...({
        role: "dialog",
        "aria-modal": "true",
        "aria-label": policy ? "编辑审批规则" : "新建审批规则",
      } as Record<string, unknown>)}
    >
      <Form className="approval-policy-form" labelAlign="top">
        <Form.FormItem>
          <label className="approval-field">
            <span>规则名称</span>
            <Input
              aria-label="审批规则名称"
              value={name}
              onChange={(value) => setName(String(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>审批类型</span>
            <Select
              aria-label="审批规则类型"
              value={actionType}
              options={ACTION_OPTIONS.filter((item) => item.value !== "all")}
              onChange={(value) => setActionType(String(value) as ApprovalActionType)}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>资源范围</span>
            <Input
              aria-label="审批规则资源范围"
              value={resourceScope}
              placeholder="留空表示租户范围"
              onChange={(value) => setResourceScope(String(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>所需同意数</span>
            <InputNumber
              aria-label="所需同意数"
              min={1}
              max={5}
              value={required}
              onChange={(value) => setRequired(Number(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>申请有效期（分钟）</span>
            <InputNumber
              aria-label="申请有效期"
              min={15}
              max={10080}
              value={expiry}
              onChange={(value) => setExpiry(Number(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>有效审批人</span>
            <Select
              aria-label="有效审批人"
              multiple
              value={approvers}
              options={APPROVER_OPTIONS}
              onChange={(value) =>
                setApprovers(Array.isArray(value) ? value.map(String) : [String(value)])
              }
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>变更原因</span>
            <Textarea
              aria-label="审批规则变更原因"
              value={reason}
              onChange={(value) => setReason(String(value))}
            />
          </label>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}

function RequestDialog({
  policies,
  visible,
  saving,
  onClose,
  onSubmit,
}: {
  policies: ApprovalPolicy[];
  visible: boolean;
  saving: boolean;
  onClose: () => void;
  onSubmit: (input: CreateApprovalRequestInput) => void;
}) {
  const [policyId, setPolicyId] = useState("");
  const [resourceType, setResourceType] = useState("knowledge_base");
  const [resourceId, setResourceId] = useState("");
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (visible) {
      setPolicyId("");
      setResourceType("knowledge_base");
      setResourceId("");
      setReason("");
    }
  }, [visible]);
  if (!visible) return null;
  const activePolicies = policies.filter((policy) => policy.status === "active");
  const valid = Boolean(policyId && resourceId.trim() && reason.trim());
  return (
    <Dialog
      visible
      header="新建审批申请"
      width={560}
      confirmBtn={{ content: "提交申请", theme: "primary", disabled: !valid }}
      confirmLoading={saving}
      cancelBtn={{ content: "取消" }}
      onClose={onClose}
      onConfirm={() =>
        valid &&
        onSubmit({
          policy_id: policyId,
          resource_type: resourceType.trim(),
          resource_id: resourceId.trim(),
          reason: reason.trim(),
          snapshot: { resource_type: resourceType.trim(), resource_id: resourceId.trim() },
        })
      }
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "新建审批申请" } as Record<
        string,
        unknown
      >)}
    >
      {!activePolicies.length ? (
        <Alert
          theme="warning"
          title="暂无可用审批规则"
          message="服务端未返回 active policy；不能在前端猜测 action type 或审批人。"
        />
      ) : null}
      <Form labelAlign="top">
        <Form.FormItem>
          <label className="approval-field">
            <span>审批规则</span>
            <Select
              aria-label="新建申请审批规则"
              value={policyId}
              options={activePolicies.map((policy) => ({
                label: policy.name + " · " + approvalActionLabel(policy.action_type),
                value: policy.id,
              }))}
              onChange={(value) => setPolicyId(String(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>资源类型</span>
            <Input
              aria-label="新建申请资源类型"
              value={resourceType}
              onChange={(value) => setResourceType(String(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>资源 ID</span>
            <Input
              aria-label="新建申请资源 ID"
              value={resourceId}
              onChange={(value) => setResourceId(String(value))}
            />
          </label>
        </Form.FormItem>
        <Form.FormItem>
          <label className="approval-field">
            <span>申请原因</span>
            <Textarea
              aria-label="新建申请原因"
              value={reason}
              onChange={(value) => setReason(String(value))}
            />
          </label>
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}

function PolicyTable({
  policies,
  onEdit,
  onDisable,
}: {
  policies: ApprovalPolicy[];
  onEdit: (policy: ApprovalPolicy) => void;
  onDisable: (policy: ApprovalPolicy) => void;
}) {
  const columns: PrimaryTableCol<ApprovalPolicy>[] = [
    {
      colKey: "name",
      title: "规则名称",
      width: 220,
      cell: ({ row }) => (
        <div className="approval-policy-identity">
          <strong>{row.name}</strong>
          <span>
            {row.id} · revision {row.revision}
          </span>
        </div>
      ),
    },
    {
      colKey: "action",
      title: "审批类型",
      width: 170,
      cell: ({ row }) => approvalActionLabel(row.action_type),
    },
    {
      colKey: "scope",
      title: "资源范围",
      width: 150,
      cell: ({ row }) => row.resource_scope || "租户范围",
    },
    {
      colKey: "threshold",
      title: "阈值",
      width: 100,
      cell: ({ row }) => `${row.required_approvals} 人`,
    },
    {
      colKey: "approvers",
      title: "有效审批人",
      width: 250,
      cell: ({ row }) => (
        <div className="approval-approver-list">
          {row.approvers.length ? (
            row.approvers.map((approver) => (
              <Tag key={approver.kind + ":" + approver.ref} variant="light-outline">
                {approvalApproverLabel(approver)}
              </Tag>
            ))
          ) : (
            <span>服务端未返回审批人</span>
          )}
        </div>
      ),
    },
    {
      colKey: "status",
      title: "状态",
      width: 90,
      cell: ({ row }) => (
        <Tag theme={statusTheme(row.status)} variant="light-outline">
          {approvalStatusLabel(row.status)}
        </Tag>
      ),
    },
    {
      colKey: "operation",
      title: "操作",
      width: 160,
      cell: ({ row }) => (
        <div className="approval-row-actions">
          <Button variant="text" icon={<EditIcon />} onClick={() => onEdit(row)}>
            编辑
          </Button>
          <Button variant="text" disabled={row.status !== "active"} onClick={() => onDisable(row)}>
            停用
          </Button>
        </div>
      ),
    },
  ];
  return (
    <div
      className="approval-request-desktop enterprise-approval-table-viewport"
      data-testid="approval-policy-desktop-table"
      tabIndex={0}
      aria-label="审批规则表格，可横向滚动"
    >
      <PrimaryTable
        rowKey="id"
        data={policies}
        columns={columns}
        size="small"
        tableLayout="fixed"
        bordered={false}
        hover
      />
    </div>
  );
}

function PolicyCards({
  policies,
  onEdit,
}: {
  policies: ApprovalPolicy[];
  onEdit: (policy: ApprovalPolicy) => void;
}) {
  return (
    <div className="approval-request-mobile" data-testid="approval-policy-mobile-list">
      {policies.map((policy) => (
        <article key={policy.id} className="approval-request-card">
          <header>
            <div>
              <span className="approval-eyebrow">{policy.id}</span>
              <h3>{policy.name}</h3>
            </div>
            <Tag theme={statusTheme(policy.status)} variant="light-outline">
              {approvalStatusLabel(policy.status)}
            </Tag>
          </header>
          <dl>
            <div>
              <dt>审批类型</dt>
              <dd>{approvalActionLabel(policy.action_type)}</dd>
            </div>
            <div>
              <dt>阈值</dt>
              <dd>{policy.required_approvals} 人</dd>
            </div>
            <div>
              <dt>有效期</dt>
              <dd>{policy.request_expiry_minutes} 分钟</dd>
            </div>
            <div>
              <dt>审批人</dt>
              <dd>
                {policy.approvers.map(approvalApproverLabel).join("、") || "服务端未返回审批人"}
              </dd>
            </div>
          </dl>
          <Button block variant="outline" onClick={() => onEdit(policy)}>
            编辑规则
          </Button>
        </article>
      ))}
    </div>
  );
}
function RequestsSurface({
  workspace,
  onOpen,
  onCreate,
  mobile,
}: {
  workspace: EnterpriseApprovalWorkspace;
  onOpen: (request: ApprovalRequest) => void;
  onCreate: () => void;
  mobile: boolean;
}) {
  const [draft, setDraft] = useState(workspace.filters);
  useEffect(() => setDraft(workspace.filters), [workspace.filters]);
  const apply = () => {
    workspace.setFilters(draft);
    void workspace.reload(draft);
  };
  const displayedRequests = workspace.requests.items.filter((request) =>
    matchesLocalRequestFilters(request, draft),
  );
  if (workspace.status === "loading" && !workspace.requests.items.length)
    return (
      <PageState
        status="loading"
        title="正在读取审批申请"
        description="申请与规则事实将并行加载。"
      />
    );
  if (workspace.error && workspace.status !== "ready")
    return <ReadError error={workspace.error} onRetry={() => void workspace.reload()} />;
  return (
    <>
      <RequestFilters filters={draft} onChange={setDraft} onApply={apply} />
      <div className="approval-list-heading">
        <div>
          <span className="approval-eyebrow">REQUEST REGISTER</span>
          <h2>审批申请</h2>
          <p>按服务端返回的申请、审批进度与 revision 做决定，不把批准视为已执行。</p>
        </div>
        <Button theme="primary" icon={<AddIcon />} onClick={onCreate}>
          新建审批申请
        </Button>
      </div>
      {displayedRequests.length ? (
        mobile ? (
          <RequestCards requests={displayedRequests} onOpen={onOpen} />
        ) : (
          <RequestTable requests={displayedRequests} onOpen={onOpen} />
        )
      ) : (
        <PageState
          compact
          status="empty"
          title="暂无审批申请"
          description={
            workspace.requests.items.length
              ? "当前已加载结果没有匹配项；服务端未提供关键词/工作空间过滤参数。"
              : "服务端已返回真实空列表；没有使用演示申请填充页面。"
          }
        />
      )}
    </>
  );
}

function PoliciesSurface({
  workspace,
  onCreate,
  onEdit,
  onDisable,
  mobile,
}: {
  workspace: EnterpriseApprovalWorkspace;
  onCreate: () => void;
  onEdit: (policy: ApprovalPolicy) => void;
  onDisable: (policy: ApprovalPolicy) => void;
  mobile: boolean;
}) {
  if (workspace.status === "loading" && !workspace.policies.items.length)
    return <PageState status="loading" title="正在读取审批规则" />;
  if (workspace.error && workspace.status !== "ready")
    return <ReadError error={workspace.error} onRetry={() => void workspace.reload()} />;
  return (
    <>
      <div className="approval-list-heading">
        <div>
          <span className="approval-eyebrow">POLICY REGISTER</span>
          <h2>审批规则</h2>
          <p>规则决定谁可以审批、需要几人同意，以及申请的有效期。</p>
        </div>
        <Button theme="primary" icon={<AddIcon />} onClick={onCreate} aria-label="新建审批规则">
          新建审批规则
        </Button>
      </div>
      {workspace.policies.items.length ? (
        mobile ? (
          <PolicyCards policies={workspace.policies.items} onEdit={onEdit} />
        ) : (
          <PolicyTable policies={workspace.policies.items} onEdit={onEdit} onDisable={onDisable} />
        )
      ) : (
        <PageState
          compact
          status="empty"
          title="暂无审批规则"
          description="服务端已返回真实空列表；审批规则不会由前端默认生成。"
        />
      )}
    </>
  );
}

export default function EnterpriseApprovalCenter({ scope, context }: Props) {
  const workspace = useEnterpriseApprovalCenter(scope, {
    actorId: context.actor.id,
    actorRole: context.actor.role,
  });
  const { openRequest, status } = workspace;
  const detailRequestIdRef = useRef<string | null>(null);
  const openApprovalRequest = useCallback(
    (requestId: string) => {
      detailRequestIdRef.current = requestId;
      return openRequest(requestId);
    },
    [openRequest],
  );
  const [tab, setTab] = useState<CenterTab>("requests");
  const [mobile, setMobile] = useState(
    () =>
      typeof window !== "undefined" && window.matchMedia?.("(max-width: 600px)").matches === true,
  );
  const [requestDialog, setRequestDialog] = useState(false);
  const [policyDialog, setPolicyDialog] = useState<{
    visible: boolean;
    policy: ApprovalPolicy | null;
  }>({ visible: false, policy: null });
  const [approveTarget, setApproveTarget] = useState<ApprovalRequest | null>(null);
  const [rejectTarget, setRejectTarget] = useState<ApprovalRequest | null>(null);
  const [cancelTarget, setCancelTarget] = useState<ApprovalRequest | null>(null);
  const deepLinkedRequestId =
    typeof window === "undefined" ? null : approvalRequestIdFromLocation(window.location);
  const handledDeepLinkRef = useRef<string | null>(null);
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width: 600px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  useEffect(() => {
    if (!deepLinkedRequestId) {
      handledDeepLinkRef.current = null;
      return;
    }
    if (status !== "ready" || handledDeepLinkRef.current === deepLinkedRequestId) return;
    handledDeepLinkRef.current = deepLinkedRequestId;
    void openApprovalRequest(deepLinkedRequestId);
  }, [deepLinkedRequestId, openApprovalRequest, status]);
  const submitPolicy = async (input: ApprovalPolicyInput) => {
    const current = policyDialog.policy;
    const result = current
      ? await workspace.updatePolicy(current.id, {
          revision: current.revision,
          name: input.name,
          resource_scope: input.resource_scope,
          required_approvals: input.required_approvals,
          request_expiry_minutes: input.request_expiry_minutes,
          approvers: input.approvers,
          reason: input.reason,
        })
      : await workspace.createPolicy(input);
    if (result) setPolicyDialog({ visible: false, policy: null });
  };
  const submitRequest = async (input: CreateApprovalRequestInput) => {
    const result = await workspace.createRequest(input);
    if (result) setRequestDialog(false);
  };
  const closeRequest = () => {
    clearApprovalRequestNavigation();
    detailRequestIdRef.current = null;
    workspace.closeRequest();
  };
  return (
    <section
      className="enterprise-approval-center enterprise-admin-surface"
      role="region"
      aria-label="企业审批中心"
    >
      <header className="approval-center-header">
        <div className="approval-center-mark" aria-hidden="true">
          <SecuredIcon />
        </div>
        <div>
          <span className="approval-eyebrow">GOVERNANCE / APPROVAL AUTHORITY</span>
          <h2>企业审批中心</h2>
          <p>管理高风险 KnowledgeOps 变更的申请、审批规则与执行授权边界。</p>
        </div>
        <Button variant="outline" icon={<RefreshIcon />} onClick={() => void workspace.reload()}>
          刷新事实
        </Button>
      </header>
      <EvidenceStrip evidence={workspace.evidence} />
      <Alert
        className="approval-boundary-alert"
        theme="warning"
        title="execution_adapter_not_connected"
        message="审批生命周期已持久化；批准后的下游目录升级、ACL、审计保留或身份变更仍需独立执行适配器消费一次性授权。"
      />
      <Tabs value={tab} onChange={(value) => setTab(String(value) as CenterTab)}>
        <Tabs.TabPanel value="requests" label={tabLabel("审批申请", tab === "requests")}>
          <RequestsSurface
            workspace={workspace}
            mobile={mobile}
            onOpen={(request) => void openApprovalRequest(request.id)}
            onCreate={() => setRequestDialog(true)}
          />
        </Tabs.TabPanel>
        <Tabs.TabPanel value="policies" label={tabLabel("审批规则", tab === "policies")}>
          <PoliciesSurface
            workspace={workspace}
            mobile={mobile}
            onCreate={() => setPolicyDialog({ visible: true, policy: null })}
            onEdit={(policy) => setPolicyDialog({ visible: true, policy })}
            onDisable={(policy) => {
              if (window.confirm(`确认停用规则 ${policy.name}？`))
                void workspace.disablePolicy(policy.id, {
                  revision: policy.revision,
                  reason: "企业管理员停用审批规则",
                });
            }}
          />
        </Tabs.TabPanel>
      </Tabs>
      <ApprovalDetailDrawer
        detail={workspace.selectedRequest}
        loading={workspace.selectedRequestLoading}
        error={workspace.detailError}
        actorId={context.actor.id}
        mutation={workspace.mutation}
        mobile={mobile}
        onClose={closeRequest}
        onRetry={() => {
          const requestId = detailRequestIdRef.current ?? deepLinkedRequestId;
          if (!requestId) return;
          handledDeepLinkRef.current = null;
          void openApprovalRequest(requestId);
        }}
        onApprove={setApproveTarget}
        onReject={setRejectTarget}
        onCancel={setCancelTarget}
      />
      <ApproveDialog
        request={approveTarget}
        visible={Boolean(approveTarget)}
        saving={workspace.mutation.saving}
        onClose={() => setApproveTarget(null)}
        onConfirm={() => {
          if (approveTarget)
            void workspace.approve(approveTarget.id, approveTarget.revision).then((result) => {
              setApproveTarget(null);
              if (result) closeRequest();
            });
        }}
      />
      <ApprovalExecutionDialog
        target={workspace.executionTarget}
        visible={
          Boolean(workspace.executionTarget) &&
          (workspace.executionTicketAvailable ||
            workspace.execution.saving ||
            Boolean(workspace.execution.error) ||
            Boolean(workspace.execution.success))
        }
        ticketAvailable={workspace.executionTicketAvailable}
        saving={workspace.execution.saving}
        error={workspace.execution.error}
        success={workspace.execution.success}
        onClose={workspace.clearExecutionTicket}
        onConfirm={() => void workspace.consumeExecutionTicket()}
      />
      <RejectDialog
        request={rejectTarget}
        visible={Boolean(rejectTarget)}
        saving={workspace.mutation.saving}
        onClose={() => setRejectTarget(null)}
        onConfirm={(comment) => {
          if (rejectTarget)
            void workspace
              .reject(rejectTarget.id, rejectTarget.revision, comment)
              .then(() => setRejectTarget(null));
        }}
      />
      <CancelDialog
        request={cancelTarget}
        visible={Boolean(cancelTarget)}
        saving={workspace.mutation.saving}
        onClose={() => setCancelTarget(null)}
        onConfirm={() => {
          if (cancelTarget)
            void workspace
              .cancel(cancelTarget.id, cancelTarget.revision, "申请人主动取消审批申请")
              .then(() => setCancelTarget(null));
        }}
      />
      <PolicyDialog
        policy={policyDialog.policy}
        visible={policyDialog.visible}
        saving={workspace.mutation.saving}
        onClose={() => setPolicyDialog({ visible: false, policy: null })}
        onSubmit={(input) => void submitPolicy(input)}
      />
      <RequestDialog
        policies={workspace.policies.items}
        visible={requestDialog}
        saving={workspace.mutation.saving}
        onClose={() => setRequestDialog(false)}
        onSubmit={(input) => void submitRequest(input)}
      />
    </section>
  );
}
