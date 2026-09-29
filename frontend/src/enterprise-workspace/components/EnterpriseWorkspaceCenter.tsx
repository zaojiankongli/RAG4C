import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  Drawer,
  Form,
  Input,
  PrimaryTable,
  Select,
  Tabs,
  Tag,
  Textarea,
  type InputProps,
  type InputRef,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  FolderOffIcon,
  EditIcon,
  FolderIcon,
  RefreshIcon,
  SecuredIcon,
  UserAddIcon,
  UsergroupIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import MetricStrip from "../../ui/enterprise/MetricStrip";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  type EnterpriseWorkspace,
  type WorkspaceDatasetBinding,
  type WorkspaceEnvironment,
  type WorkspaceMember,
  type WorkspaceRole,
} from "../enterpriseWorkspaceModel";
import { useEnterpriseWorkspaceCenter } from "../hooks/useEnterpriseWorkspace";
import {
  enterpriseWorkspaceIdFromLocation,
  enterpriseWorkspaceNavigationUrl,
} from "../workspaceRoute";
import { commitNavigationIntent } from "../../run/navigationAdapter";
import {
  firstWorkspaceValidationField,
  validateWorkspaceDialog,
  type WorkspaceDialogKind,
  type WorkspaceValidationErrors,
  type WorkspaceValidationField,
} from "../enterpriseWorkspaceValidation";
import WorkspacePermissionsRolloutCenter from "./WorkspacePermissionsRolloutCenter";
import "../enterprise-workspace.css";

interface Props {
  scope: EnterpriseScope;
  context: EnterpriseContext;
}

type AccessibleDialogProps = React.ComponentProps<typeof Dialog> &
  React.HTMLAttributes<HTMLDivElement>;
const AccessibleDialog = Dialog as unknown as React.ComponentType<AccessibleDialogProps>;

type DetailTab = "overview" | "members" | "datasets" | "permissions";
type DialogKind = WorkspaceDialogKind;

interface DialogState {
  kind: DialogKind;
  member?: WorkspaceMember;
  dataset?: WorkspaceDatasetBinding;
}

const ENVIRONMENT_LABELS: Record<string, string> = {
  development: "开发",
  testing: "测试",
  production: "生产",
};
const ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  viewer: "查看者",
};

const ENVIRONMENT_OPTIONS = [
  { label: "开发", value: "development" },
  { label: "测试", value: "testing" },
  { label: "生产", value: "production" },
];
const WORKSPACE_ROLE_OPTIONS = [
  { label: "所有者", value: "owner" },
  { label: "管理员", value: "admin" },
  { label: "编辑者", value: "editor" },
  { label: "查看者", value: "viewer" },
];
const BINDING_KIND_OPTIONS = [
  { label: "主绑定", value: "primary" },
  { label: "共享绑定", value: "shared" },
];

function environmentLabel(environment: string): string {
  return ENVIRONMENT_LABELS[environment] ?? environment;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function dateLabel(value: string | null): string {
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

function countLabel(value: number | null): string {
  return value === null ? "未返回" : new Intl.NumberFormat("zh-CN").format(value);
}

function workspaceTheme(status: string): "success" | "warning" | "default" {
  return status === "active" ? "success" : status === "archived" ? "warning" : "default";
}

function statusLabel(status: string): string {
  return status === "active" ? "运行中" : status === "archived" ? "已归档" : status;
}

function canManageTenant(context: EnterpriseContext): boolean {
  return ["owner", "admin"].includes(context.actor.role);
}

function canManageMembers(context: EnterpriseContext, workspace: EnterpriseWorkspace): boolean {
  return context.actor.role === "owner" || ["owner", "admin"].includes(workspace.actor_role ?? "");
}

function syncWorkspaceRoute(workspaceId: string | null, replace: boolean): void {
  if (typeof window === "undefined") return;
  const intent = enterpriseWorkspaceNavigationUrl(window.location, workspaceId);
  commitNavigationIntent(intent, { historyAction: replace ? "replace" : "push" });
}

function WorkspaceEvidenceStrip({
  count,
  activeCount,
  defaultId,
  primaryBindings,
  authorizationState,
}: {
  count: number | null;
  activeCount: number | null;
  defaultId: string | null;
  primaryBindings: number | null;
  authorizationState: string;
}) {
  return (
    <MetricStrip
      ariaLabel="Workspace 权威证据"
      columns={5}
      metrics={[
        { id: "total", label: "Workspace 总数", value: countLabel(count), unit: "个" },
        { id: "active", label: "有效 Workspace", value: countLabel(activeCount), unit: "个" },
        {
          id: "default",
          label: "默认 Workspace",
          value: defaultId ?? "未返回",
          hint: "每个租户最多一个有效默认 Workspace",
        },
        {
          id: "primary",
          label: "主知识库绑定",
          value: countLabel(primaryBindings),
          unit: "条",
        },
        {
          id: "authorization",
          label: "授权执行状态",
          value:
            authorizationState === "workspace_authorization_not_enforced"
              ? "以详情 policy 为准"
              : authorizationState,
          tone: "warning",
          hint:
            authorizationState === "workspace_authorization_not_enforced"
              ? "详情权限页返回 policy mode 与 impact evidence"
              : authorizationState,
        },
      ]}
    />
  );
}

function ContinuationControl({
  label,
  retryLabel,
  loading,
  error,
  onLoad,
}: {
  label: string;
  retryLabel: string;
  loading: boolean;
  error: { description: string } | null;
  onLoad: () => void;
}) {
  return (
    <div className="enterprise-workspace-continuation">
      {error ? <Alert theme="error" title={`无法${label}`} message={error.description} /> : null}
      <Button
        tag="button"
        variant="outline"
        loading={loading}
        disabled={loading}
        onClick={onLoad}
        aria-label={error ? retryLabel : label}
      >
        {error ? retryLabel : label}
      </Button>
    </div>
  );
}

function WorkspaceCard({
  workspace,
  onOpen,
}: {
  workspace: EnterpriseWorkspace;
  onOpen: (workspace: EnterpriseWorkspace) => void;
}) {
  return (
    <li className="enterprise-workspace-card">
      <header>
        <div>
          <strong>{workspace.name}</strong>
          <small>{workspace.code}</small>
        </div>
        <Tag theme={workspaceTheme(workspace.status)} variant="light-outline">
          {statusLabel(workspace.status)}
        </Tag>
      </header>
      <dl>
        <div>
          <dt>环境</dt>
          <dd>{environmentLabel(workspace.environment)}</dd>
        </div>
        <div>
          <dt>成员</dt>
          <dd>{countLabel(workspace.member_count)}</dd>
        </div>
        <div>
          <dt>知识库</dt>
          <dd>{countLabel(workspace.dataset_count)}</dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>{workspace.revision}</dd>
        </div>
      </dl>
      <Button
        tag="button"
        variant="outline"
        size="small"
        onClick={() => onOpen(workspace)}
        aria-label={`查看 Workspace ${workspace.name}`}
      >
        查看详情
      </Button>
    </li>
  );
}

const DETAIL_TAB_LABELS: Record<DetailTab, string> = {
  overview: "概览",
  members: "成员",
  datasets: "知识库",
  permissions: "权限",
};

function detailTabId(value: DetailTab): string {
  return `enterprise-workspace-tab-${value}`;
}

function detailPanelId(value: DetailTab): string {
  return `enterprise-workspace-panel-${value}`;
}

function tabLabel(value: DetailTab, active: boolean, onActivate: (value: DetailTab) => void) {
  const label = DETAIL_TAB_LABELS[value];
  return (
    <span
      id={detailTabId(value)}
      role="tab"
      aria-selected={active}
      aria-controls={detailPanelId(value)}
      tabIndex={active ? 0 : -1}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onActivate(value);
      }}
    >
      {label}
    </span>
  );
}

const VALIDATION_FIELD_NAMES: Record<WorkspaceValidationField, string> = {
  code: "workspace_code",
  name: "workspace_name",
  description: "workspace_description",
  accountId: "workspace_member_account_id",
  datasetId: "workspace_dataset_id",
  reason: "workspace_reason",
};

function workspaceValidationErrorId(name: string): string {
  return `${name}-error`;
}

function syncWorkspaceValidationAttributes(
  element: HTMLElement | null | undefined,
  name: string,
  error?: string,
): void {
  if (!element) return;
  if (error) {
    element.setAttribute("aria-invalid", "true");
    element.setAttribute("aria-describedby", workspaceValidationErrorId(name));
  } else {
    element.removeAttribute("aria-invalid");
    element.removeAttribute("aria-describedby");
  }
}

function focusWorkspaceValidationField(field: WorkspaceValidationField): void {
  document
    .querySelector<HTMLElement>(
      `.enterprise-workspace-operation-dialog [name="${VALIDATION_FIELD_NAMES[field]}"]`,
    )
    ?.focus();
}

function WorkspaceInput({
  ariaLabel,
  name,
  value,
  onChange,
  maxlength,
  spellCheck = true,
  autocomplete = "off",
  error,
}: {
  ariaLabel: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  maxlength?: number;
  spellCheck?: boolean;
  autocomplete?: string;
  error?: string;
}) {
  const ref = useRef<InputRef>(null);
  useEffect(() => {
    const element = ref.current?.inputElement;
    element?.setAttribute("aria-label", ariaLabel);
    element?.setAttribute("name", name);
    element?.setAttribute("autocomplete", autocomplete);
    element?.setAttribute("spellcheck", String(spellCheck));
    if (maxlength !== undefined) element?.setAttribute("maxlength", String(maxlength));
    syncWorkspaceValidationAttributes(element, name, error);
  }, [ariaLabel, autocomplete, error, maxlength, name, spellCheck]);
  return (
    <Input
      ref={ref}
      name={name}
      value={value}
      maxlength={maxlength}
      autocomplete={autocomplete}
      status={error ? "error" : "default"}
      onChange={(next) => onChange(String(next))}
    />
  );
}

function WorkspaceSelect({
  ariaLabel,
  name,
  value,
  options,
  onChange,
  error,
}: {
  ariaLabel: string;
  name: string;
  value: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
  error?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const input = hostRef.current?.querySelector("input");
    input?.setAttribute("role", "combobox");
    input?.setAttribute("aria-label", ariaLabel);
    input?.setAttribute("aria-haspopup", "listbox");
    input?.setAttribute("name", name);
    const wrapper = hostRef.current?.querySelector(".t-input__wrap");
    wrapper?.removeAttribute("role");
    wrapper?.removeAttribute("aria-label");
    syncWorkspaceValidationAttributes(input, name, error);
  }, [ariaLabel, error, name, value]);
  return (
    <div ref={hostRef} className="enterprise-workspace-select-host">
      <Select
        value={value}
        options={options}
        status={error ? "error" : "default"}
        inputProps={
          {
            name,
            autocomplete: "off",
            role: "combobox",
            "aria-label": ariaLabel,
          } as InputProps
        }
        onChange={(next) => onChange(String(next))}
      />
    </div>
  );
}

interface WorkspaceTextareaRef {
  currentElement: HTMLDivElement;
  textareaElement: HTMLTextAreaElement;
}

function WorkspaceTextarea({
  ariaLabel,
  name,
  value,
  onChange,
  maxlength = 512,
  error,
}: {
  ariaLabel: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
  maxlength?: number;
  error?: string;
}) {
  const ref = useRef<WorkspaceTextareaRef>(null);
  useEffect(() => {
    const element = ref.current?.textareaElement;
    element?.setAttribute("aria-label", ariaLabel);
    element?.setAttribute("maxlength", String(maxlength));
    element?.setAttribute("name", name);
    syncWorkspaceValidationAttributes(element, name, error);
  }, [ariaLabel, error, maxlength, name]);
  return (
    <Textarea
      ref={ref}
      aria-label={ariaLabel}
      name={name}
      maxlength={maxlength}
      status={error ? "error" : "default"}
      count
      value={value}
      onChange={(next) => onChange(String(next))}
    />
  );
}

function FormField({
  label,
  children,
  hint,
  error,
  fieldName,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  error?: string;
  fieldName?: string;
}) {
  return (
    <Form.FormItem>
      <label className="enterprise-workspace-field">
        <span>{label}</span>
        {children}
        {error ? (
          <small
            id={fieldName ? workspaceValidationErrorId(fieldName) : undefined}
            className="enterprise-workspace-field__error"
            role="alert"
            aria-label={error}
          >
            {error}
          </small>
        ) : hint ? (
          <small>{hint}</small>
        ) : null}
      </label>
    </Form.FormItem>
  );
}

export default function EnterpriseWorkspaceCenter({ scope, context }: Props) {
  const workspace = useEnterpriseWorkspaceCenter(scope);
  const [detailTab, setDetailTab] = useState<DetailTab>("overview");
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [environment, setEnvironment] = useState<WorkspaceEnvironment>("development");
  const [reason, setReason] = useState("");
  const [accountId, setAccountId] = useState("");
  const [memberRole, setMemberRole] = useState<WorkspaceRole>("viewer");
  const [datasetId, setDatasetId] = useState("");
  const [bindingKind, setBindingKind] = useState<"primary" | "shared">("shared");
  const [validationErrors, setValidationErrors] = useState<WorkspaceValidationErrors>({});
  const tableHostRef = useRef<HTMLDivElement>(null);
  const deepLinkOpenRef = useRef<string | null>(null);
  const [requestedWorkspaceId, setRequestedWorkspaceId] = useState<string | null>(null);

  const selected = workspace.selected;
  const openWorkspace = workspace.openWorkspace;
  const closeWorkspace = workspace.closeWorkspace;
  const tenantManage = canManageTenant(context);
  const memberManage = selected ? canManageMembers(context, selected.workspace) : false;

  const openWorkspaceDetail = useCallback(
    (workspaceId: string) => {
      deepLinkOpenRef.current = workspaceId;
      setDetailTab("overview");
      setRequestedWorkspaceId(workspaceId);
      syncWorkspaceRoute(workspaceId, false);
      void openWorkspace(workspaceId);
    },
    [openWorkspace],
  );

  const closeWorkspaceDetail = useCallback(() => {
    deepLinkOpenRef.current = null;
    setRequestedWorkspaceId(null);
    closeWorkspace();
    syncWorkspaceRoute(null, true);
  }, [closeWorkspace]);

  useEffect(() => {
    const table = tableHostRef.current?.querySelector("table");
    table?.setAttribute("aria-label", "企业 Workspace 列表");
  }, [workspace.page]);

  const syncDrawerAccessibility = useCallback(() => {
    const drawer = document.querySelector<HTMLElement>(".enterprise-workspace-detail-drawer");
    if (!drawer || (!selected && !workspace.selectedLoading && !workspace.detailError)) return;
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute(
      "aria-label",
      selected ? `Workspace 详情：${selected.workspace.name}` : "Workspace 详情",
    );
    const tabList = drawer.querySelector<HTMLElement>(".t-tabs__nav");
    tabList?.setAttribute("role", "tablist");
    tabList?.setAttribute("aria-label", "Workspace 详情导航");
  }, [selected, workspace.detailError, workspace.selectedLoading]);

  useEffect(() => {
    syncDrawerAccessibility();
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(syncDrawerAccessibility);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [detailTab, syncDrawerAccessibility]);

  const applyWorkspaceRoute = useCallback(() => {
    const workspaceId = enterpriseWorkspaceIdFromLocation(window.location);
    if (!workspaceId) {
      deepLinkOpenRef.current = null;
      setRequestedWorkspaceId(null);
      closeWorkspace();
      return;
    }
    if (deepLinkOpenRef.current === workspaceId) return;
    deepLinkOpenRef.current = workspaceId;
    setDetailTab("overview");
    setRequestedWorkspaceId(workspaceId);
    void openWorkspace(workspaceId);
  }, [closeWorkspace, openWorkspace]);

  useEffect(() => {
    if (workspace.status !== "ready" || !workspace.page) return;
    applyWorkspaceRoute();
    window.addEventListener("popstate", applyWorkspaceRoute);
    window.addEventListener("hashchange", applyWorkspaceRoute);
    return () => {
      window.removeEventListener("popstate", applyWorkspaceRoute);
      window.removeEventListener("hashchange", applyWorkspaceRoute);
    };
  }, [applyWorkspaceRoute, workspace.page, workspace.status]);

  const resetForm = () => {
    setCode("");
    setName("");
    setDescription("");
    setEnvironment("development");
    setReason("");
    setAccountId("");
    setMemberRole("viewer");
    setDatasetId("");
    setBindingKind("shared");
  };

  const openDialog = (state: DialogState) => {
    workspace.clearMutation();
    setValidationErrors({});
    resetForm();
    if (selected && state.kind === "edit") {
      setName(selected.workspace.name);
      setDescription(selected.workspace.description);
      setEnvironment(selected.workspace.environment);
    }
    if (state.member) setMemberRole(state.member.role);
    setDialog(state);
  };

  const closeDialog = () => {
    setDialog(null);
    setValidationErrors({});
    resetForm();
  };

  const submitDialog = async () => {
    if (!dialog) return;
    const errors = validateWorkspaceDialog(dialog.kind, {
      code,
      name,
      description,
      accountId,
      datasetId,
      reason,
    });
    setValidationErrors(errors);
    const firstError = firstWorkspaceValidationField(dialog.kind, errors);
    if (firstError) {
      focusWorkspaceValidationField(firstError);
      return;
    }
    let ok = false;
    if (dialog.kind === "create") {
      ok = await workspace.createWorkspace({ code, name, description, environment, reason });
    } else if (selected && dialog.kind === "edit") {
      ok = await workspace.updateWorkspace(selected.workspace.id, {
        revision: selected.workspace.revision,
        name,
        description,
        environment,
        reason,
      });
    } else if (selected && dialog.kind === "archive") {
      ok = await workspace.archiveWorkspace(selected.workspace.id, {
        revision: selected.workspace.revision,
        reason,
      });
    } else if (selected && dialog.kind === "add-member") {
      ok = await workspace.addMember(selected.workspace.id, {
        account_id: accountId,
        role: memberRole,
        reason,
      });
    } else if (selected && dialog.kind === "edit-member" && dialog.member) {
      ok = await workspace.updateMember(selected.workspace.id, dialog.member.account_id, {
        revision: dialog.member.revision,
        role: memberRole,
        reason,
      });
    } else if (selected && dialog.kind === "remove-member" && dialog.member) {
      ok = await workspace.removeMember(selected.workspace.id, dialog.member.account_id, {
        revision: dialog.member.revision,
        reason,
      });
    } else if (selected && dialog.kind === "bind-dataset") {
      ok = await workspace.bindDataset(selected.workspace.id, {
        dataset_id: datasetId,
        binding_kind: bindingKind,
        reason,
      });
    } else if (selected && dialog.kind === "remove-dataset" && dialog.dataset) {
      ok = await workspace.removeDataset(selected.workspace.id, dialog.dataset.dataset_id, {
        revision: dialog.dataset.revision,
        reason,
      });
    }
    if (ok) closeDialog();
  };

  const columns = useMemo<PrimaryTableCol<EnterpriseWorkspace>[]>(
    () => [
      {
        colKey: "name",
        title: "Workspace",
        width: 250,
        cell: ({ row }) => (
          <div className="enterprise-workspace-name-cell">
            <span className="enterprise-workspace-name-cell__icon" aria-hidden="true">
              <FolderIcon />
            </span>
            <span>
              <strong>{row.name}</strong>
              <small>{row.code}</small>
            </span>
            {row.is_default ? (
              <Tag theme="primary" variant="light-outline" size="small">
                默认
              </Tag>
            ) : null}
          </div>
        ),
      },
      {
        colKey: "environment",
        title: "环境",
        width: 100,
        cell: ({ row }) => environmentLabel(row.environment),
      },
      {
        colKey: "status",
        title: "状态",
        width: 110,
        cell: ({ row }) => (
          <Tag theme={workspaceTheme(row.status)} variant="light-outline">
            {statusLabel(row.status)}
          </Tag>
        ),
      },
      {
        colKey: "member_count",
        title: "成员",
        width: 90,
        cell: ({ row }) => countLabel(row.member_count),
      },
      {
        colKey: "dataset_count",
        title: "知识库",
        width: 90,
        cell: ({ row }) => countLabel(row.dataset_count),
      },
      { colKey: "revision", title: "Revision", width: 90 },
      {
        colKey: "updated_at",
        title: "更新时间",
        width: 170,
        cell: ({ row }) => dateLabel(row.updated_at),
      },
      {
        colKey: "actions",
        title: "操作",
        width: 110,
        fixed: "right",
        cell: ({ row }) => (
          <Button
            tag="button"
            variant="text"
            size="small"
            onClick={() => openWorkspaceDetail(row.id)}
            aria-label={`查看 Workspace ${row.name}`}
          >
            详情
          </Button>
        ),
      },
    ],
    [openWorkspaceDetail],
  );

  if (workspace.status === "identity-missing") {
    return (
      <PageState
        status="error"
        title="需要企业身份"
        description="连接签名企业身份后才能读取 Workspace 真账。"
      />
    );
  }
  if (workspace.status === "loading") {
    return (
      <PageState
        status="loading"
        title="正在读取 Workspace 控制面"
        description="Workspace、成员和知识库绑定不会使用模拟数据填充。"
      />
    );
  }
  if (workspace.status === "error" || !workspace.page) {
    return (
      <PageState
        status="error"
        title={workspace.error?.title ?? "Workspace 控制面不可用"}
        description={workspace.error?.description ?? "服务未返回 Workspace 真账。"}
        extra={
          workspace.error?.canRetry ? (
            <Button tag="button" icon={<RefreshIcon />} onClick={() => void workspace.reload()}>
              重新读取
            </Button>
          ) : undefined
        }
      />
    );
  }

  const page = workspace.page;
  const isEmpty = page.items.length === 0;
  const defaultWorkspaceLabel =
    page.items.find((item) => item.id === page.evidence.default_workspace_id)?.name ??
    page.evidence.default_workspace_id;

  const tenantManagerReason = tenantManage ? null : "仅租户所有者或管理员可管理 Workspace";

  return (
    <section className="enterprise-workspace-center" aria-label="Enterprise Workspace Center">
      <WorkspaceEvidenceStrip
        count={page.evidence.workspace_count}
        activeCount={page.evidence.active_count}
        defaultId={defaultWorkspaceLabel}
        primaryBindings={page.evidence.primary_dataset_binding_count}
        authorizationState={page.evidence.authorization_state}
      />

      <Alert
        className="enterprise-workspace-boundary"
        theme="warning"
        icon={<SecuredIcon />}
        title="Workspace Authorization rollout 已接入"
        message="Workspace 生命周期与授权策略使用同一租户权威事实；Dataset ACL 仍由独立授权引擎执行，具体效果以详情内权限页的 current / candidate / delta 证据为准。"
      />

      {workspace.mutation.success ? (
        <Alert
          theme="success"
          title={workspace.mutation.success}
          message="已重新读取服务端真账。"
        />
      ) : null}

      <div
        className="enterprise-workspace-table-toolbar"
        role="region"
        aria-label="Workspace 列表操作"
      >
        <div>
          <strong>Workspace 列表</strong>
          <span>{page.count === null ? "权威总数未返回" : `共 ${page.count} 个`}</span>
        </div>
        <div className="enterprise-workspace-table-toolbar__actions">
          <Button
            tag="button"
            variant="outline"
            icon={<RefreshIcon />}
            onClick={() => void workspace.reload()}
          >
            刷新事实
          </Button>
          <Button
            tag="button"
            theme="primary"
            icon={<AddIcon />}
            disabled={!tenantManage}
            title={tenantManagerReason ?? undefined}
            onClick={() => openDialog({ kind: "create" })}
          >
            创建 Workspace
          </Button>
        </div>
        {tenantManagerReason ? (
          <span className="enterprise-workspace-action-note" role="note">
            {tenantManagerReason}
          </span>
        ) : null}
      </div>

      {isEmpty ? (
        <div className="enterprise-workspace-empty">
          <FolderIcon aria-hidden="true" />
          <strong>当前租户没有可展示的 Workspace</strong>
          <p>未生成演示 Workspace。完成 0026 迁移或由租户管理员创建后再刷新。</p>
        </div>
      ) : (
        <>
          <div ref={tableHostRef} className="enterprise-workspace-table">
            <PrimaryTable
              aria-label="企业 Workspace 列表"
              rowKey="id"
              data={page.items}
              columns={columns}
              bordered
              hover
              tableLayout="fixed"
            />
          </div>
          <ul className="enterprise-workspace-cards" aria-label="移动端 Workspace 列表">
            {page.items.map((item) => (
              <WorkspaceCard
                key={item.id}
                workspace={item}
                onOpen={(row) => openWorkspaceDetail(row.id)}
              />
            ))}
          </ul>
          {page.next_cursor || workspace.workspaceMoreError ? (
            <ContinuationControl
              label="加载更多 Workspace"
              retryLabel="重试加载更多 Workspace"
              loading={workspace.workspaceMoreLoading}
              error={workspace.workspaceMoreError}
              onLoad={() => void workspace.loadMoreWorkspaces()}
            />
          ) : null}
        </>
      )}

      <Drawer
        visible={
          Boolean(requestedWorkspaceId) ||
          Boolean(selected) ||
          workspace.selectedLoading ||
          Boolean(workspace.detailError)
        }
        header={selected ? `Workspace 详情：${selected.workspace.name}` : "Workspace 详情"}
        size="min(720px, 100vw)"
        forceRender
        closeBtn={
          <Button
            tag="button"
            variant="text"
            aria-label="关闭"
            onClick={(event) => {
              event.stopPropagation();
              closeWorkspaceDetail();
            }}
          >
            关闭
          </Button>
        }
        onClose={closeWorkspaceDetail}
        destroyOnClose
        footer={false}
        className="enterprise-workspace-detail-drawer"
      >
        {workspace.selectedLoading ? (
          <PageState status="loading" title="正在读取 Workspace 详情" />
        ) : workspace.detailError ? (
          <PageState
            status="error"
            title={workspace.detailError.title}
            description={workspace.detailError.description}
          />
        ) : selected ? (
          <div className="enterprise-workspace-detail">
            <div className="enterprise-workspace-detail__toolbar">
              <div>
                <Tag theme={workspaceTheme(selected.workspace.status)} variant="light-outline">
                  {statusLabel(selected.workspace.status)}
                </Tag>
                <Tag variant="light-outline">Revision {selected.workspace.revision}</Tag>
              </div>
              <div>
                <Button
                  tag="button"
                  variant="outline"
                  icon={<EditIcon />}
                  disabled={!tenantManage}
                  title={tenantManagerReason ?? undefined}
                  aria-label="编辑 Workspace"
                  onClick={() => openDialog({ kind: "edit" })}
                >
                  编辑 Workspace
                </Button>
                <Button
                  tag="button"
                  theme="danger"
                  variant="outline"
                  icon={<FolderOffIcon />}
                  disabled={!tenantManage || selected.workspace.status !== "active"}
                  title={
                    tenantManagerReason ??
                    (selected.workspace.status === "active" ? undefined : "Workspace 已归档")
                  }
                  onClick={() => openDialog({ kind: "archive" })}
                >
                  归档 Workspace
                </Button>
                {tenantManagerReason ? (
                  <span className="enterprise-workspace-action-note" role="note">
                    {tenantManagerReason}
                  </span>
                ) : null}
              </div>
            </div>
            <Tabs value={detailTab} onChange={(value) => setDetailTab(value as DetailTab)}>
              <Tabs.TabPanel
                value="overview"
                label={tabLabel("overview", detailTab === "overview", setDetailTab)}
              >
                <div
                  id={detailPanelId("overview")}
                  role="tabpanel"
                  aria-labelledby={detailTabId("overview")}
                  className="enterprise-workspace-overview"
                >
                  <dl>
                    <div>
                      <dt>Workspace ID</dt>
                      <dd>{selected.workspace.id}</dd>
                    </div>
                    <div>
                      <dt>Code</dt>
                      <dd>{selected.workspace.code}</dd>
                    </div>
                    <div>
                      <dt>环境</dt>
                      <dd>{environmentLabel(selected.workspace.environment)}</dd>
                    </div>
                    <div>
                      <dt>默认 Workspace</dt>
                      <dd>{selected.workspace.is_default ? "是" : "否"}</dd>
                    </div>
                    <div>
                      <dt>成员数</dt>
                      <dd>{countLabel(selected.workspace.member_count)}</dd>
                    </div>
                    <div>
                      <dt>知识库数</dt>
                      <dd>{countLabel(selected.workspace.dataset_count)}</dd>
                    </div>
                  </dl>
                  <section>
                    <h3>描述</h3>
                    <p>{selected.workspace.description || "未填写描述"}</p>
                  </section>
                </div>
              </Tabs.TabPanel>
              <Tabs.TabPanel
                value="members"
                label={tabLabel("members", detailTab === "members", setDetailTab)}
              >
                <section
                  id={detailPanelId("members")}
                  role="tabpanel"
                  aria-labelledby={detailTabId("members")}
                  className="enterprise-workspace-detail-section"
                >
                  <header>
                    <div>
                      <h3>Workspace 成员</h3>
                      <p>角色事实记录在 Workspace 控制面，当前不等同于知识库有效权限。</p>
                    </div>
                    <Button
                      tag="button"
                      icon={<UserAddIcon />}
                      disabled={!memberManage || selected.workspace.status !== "active"}
                      onClick={() => openDialog({ kind: "add-member" })}
                    >
                      添加成员
                    </Button>
                  </header>
                  {selected.members.items.length === 0 ? (
                    <p className="enterprise-workspace-detail-empty">服务端未返回成员关系。</p>
                  ) : (
                    <ul className="enterprise-workspace-relation-list">
                      {selected.members.items.map((member) => (
                        <li key={member.account_id}>
                          <span
                            className="enterprise-workspace-relation-list__icon"
                            aria-hidden="true"
                          >
                            <UsergroupIcon />
                          </span>
                          <span>
                            <strong>{member.name}</strong>
                            <small>{member.email || member.account_id}</small>
                          </span>
                          <Tag variant="light-outline">{roleLabel(member.role)}</Tag>
                          <span>Revision {member.revision}</span>
                          <div>
                            <Button
                              tag="button"
                              variant="text"
                              size="small"
                              disabled={!memberManage}
                              onClick={() => openDialog({ kind: "edit-member", member })}
                            >
                              修改角色
                            </Button>
                            <Button
                              tag="button"
                              variant="text"
                              theme="danger"
                              size="small"
                              disabled={!memberManage}
                              onClick={() => openDialog({ kind: "remove-member", member })}
                            >
                              移除
                            </Button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                  {selected.members.next_cursor || workspace.memberMoreError ? (
                    <ContinuationControl
                      label="加载更多成员"
                      retryLabel="重试加载更多成员"
                      loading={workspace.memberMoreLoading}
                      error={workspace.memberMoreError}
                      onLoad={() => void workspace.loadMoreMembers()}
                    />
                  ) : null}
                </section>
              </Tabs.TabPanel>
              <Tabs.TabPanel
                value="datasets"
                label={tabLabel("datasets", detailTab === "datasets", setDetailTab)}
              >
                <section
                  id={detailPanelId("datasets")}
                  role="tabpanel"
                  aria-labelledby={detailTabId("datasets")}
                  className="enterprise-workspace-detail-section"
                >
                  <header>
                    <div>
                      <h3>知识库绑定</h3>
                      <p>主绑定具有唯一性；共享绑定只记录归属，不改变现有 Dataset ACL。</p>
                    </div>
                    <Button
                      tag="button"
                      icon={<AddIcon />}
                      disabled={!tenantManage || selected.workspace.status !== "active"}
                      onClick={() => openDialog({ kind: "bind-dataset" })}
                    >
                      绑定知识库
                    </Button>
                  </header>
                  {selected.datasets.items.length === 0 ? (
                    <p className="enterprise-workspace-detail-empty">服务端未返回知识库绑定。</p>
                  ) : (
                    <ul className="enterprise-workspace-relation-list">
                      {selected.datasets.items.map((dataset) => (
                        <li key={dataset.dataset_id}>
                          <span
                            className="enterprise-workspace-relation-list__icon"
                            aria-hidden="true"
                          >
                            <FolderIcon />
                          </span>
                          <span>
                            <strong>{dataset.name}</strong>
                            <small>{dataset.dataset_id}</small>
                          </span>
                          <Tag
                            theme={dataset.binding_kind === "primary" ? "primary" : "default"}
                            variant="light-outline"
                          >
                            {dataset.binding_kind === "primary" ? "主绑定" : "共享绑定"}
                          </Tag>
                          <span>Revision {dataset.revision}</span>
                          <Button
                            tag="button"
                            variant="text"
                            theme="danger"
                            size="small"
                            disabled={!tenantManage}
                            onClick={() => openDialog({ kind: "remove-dataset", dataset })}
                          >
                            解除绑定
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {selected.datasets.next_cursor || workspace.datasetMoreError ? (
                    <ContinuationControl
                      label="加载更多知识库"
                      retryLabel="重试加载更多知识库"
                      loading={workspace.datasetMoreLoading}
                      error={workspace.datasetMoreError}
                      onLoad={() => void workspace.loadMoreDatasets()}
                    />
                  ) : null}
                </section>
              </Tabs.TabPanel>
              <Tabs.TabPanel
                value="permissions"
                label={tabLabel("permissions", detailTab === "permissions", setDetailTab)}
              >
                <div
                  id={detailPanelId("permissions")}
                  role="tabpanel"
                  aria-labelledby={detailTabId("permissions")}
                  className="enterprise-workspace-permissions"
                >
                  <WorkspacePermissionsRolloutCenter
                    scope={scope}
                    context={context}
                    workspace={selected.workspace}
                    legacyAuthorizationState={selected.authorization_state}
                  />
                </div>
              </Tabs.TabPanel>
            </Tabs>
          </div>
        ) : null}
      </Drawer>

      <AccessibleDialog
        role="dialog"
        aria-modal="true"
        aria-label={dialogTitle(dialog)}
        visible={Boolean(dialog)}
        header={dialogTitle(dialog)}
        confirmBtn={dialogConfirmLabel(dialog)}
        cancelBtn="取消"
        onClose={closeDialog}
        onCancel={closeDialog}
        onConfirm={() => void submitDialog()}
        confirmLoading={workspace.mutation.saving}
        dialogClassName="enterprise-workspace-operation-dialog"
      >
        <div className="enterprise-workspace-dialog">
          <Form className="enterprise-workspace-form" labelAlign="top">
            {dialog?.kind === "create" ? (
              <>
                <FormField
                  label="Workspace Code"
                  error={validationErrors.code}
                  fieldName="workspace_code"
                >
                  <WorkspaceInput
                    ariaLabel="Workspace Code"
                    name="workspace_code"
                    value={code}
                    maxlength={64}
                    spellCheck={false}
                    error={validationErrors.code}
                    onChange={setCode}
                  />
                </FormField>
                <FormField
                  label="Workspace 名称"
                  error={validationErrors.name}
                  fieldName="workspace_name"
                >
                  <WorkspaceInput
                    ariaLabel="Workspace 名称"
                    name="workspace_name"
                    value={name}
                    maxlength={128}
                    error={validationErrors.name}
                    onChange={setName}
                  />
                </FormField>
                <WorkspaceFields
                  description={description}
                  setDescription={setDescription}
                  environment={environment}
                  setEnvironment={setEnvironment}
                  validationErrors={validationErrors}
                />
              </>
            ) : null}
            {dialog?.kind === "edit" ? (
              <>
                <FormField
                  label="Workspace 名称"
                  error={validationErrors.name}
                  fieldName="workspace_name"
                >
                  <WorkspaceInput
                    ariaLabel="Workspace 名称"
                    name="workspace_name"
                    value={name}
                    maxlength={128}
                    error={validationErrors.name}
                    onChange={setName}
                  />
                </FormField>
                <WorkspaceFields
                  description={description}
                  setDescription={setDescription}
                  environment={environment}
                  setEnvironment={setEnvironment}
                  validationErrors={validationErrors}
                />
              </>
            ) : null}
            {dialog?.kind === "add-member" ? (
              <>
                <FormField
                  label="成员 Account ID"
                  error={validationErrors.accountId}
                  fieldName="workspace_member_account_id"
                >
                  <WorkspaceInput
                    ariaLabel="成员 Account ID"
                    name="workspace_member_account_id"
                    value={accountId}
                    maxlength={64}
                    spellCheck={false}
                    error={validationErrors.accountId}
                    onChange={setAccountId}
                  />
                </FormField>
                <RoleField value={memberRole} onChange={setMemberRole} />
              </>
            ) : null}
            {dialog?.kind === "edit-member" ? (
              <RoleField value={memberRole} onChange={setMemberRole} />
            ) : null}
            {dialog?.kind === "bind-dataset" ? (
              <>
                <FormField
                  label="知识库 ID"
                  error={validationErrors.datasetId}
                  fieldName="workspace_dataset_id"
                >
                  <WorkspaceInput
                    ariaLabel="知识库 ID"
                    name="workspace_dataset_id"
                    value={datasetId}
                    maxlength={64}
                    spellCheck={false}
                    error={validationErrors.datasetId}
                    onChange={setDatasetId}
                  />
                </FormField>
                <FormField label="绑定类型">
                  <WorkspaceSelect
                    ariaLabel="绑定类型"
                    name="workspace_binding_kind"
                    value={bindingKind}
                    options={BINDING_KIND_OPTIONS}
                    onChange={(value) => setBindingKind(value as "primary" | "shared")}
                  />
                </FormField>
              </>
            ) : null}
            {dialog?.kind === "archive" ? (
              <Alert
                theme="warning"
                title="归档保护"
                message="默认 Workspace 拥有主知识库绑定时不能归档"
              />
            ) : null}
            {dialog?.kind === "remove-member" && dialog.member ? (
              <Alert
                theme="warning"
                title="最后所有者保护"
                message={`将移除 ${dialog.member.name}；服务端会阻止移除最后一名有效 Workspace 所有者。`}
              />
            ) : null}
            {dialog?.kind === "remove-dataset" && dialog.dataset ? (
              <Alert
                theme="warning"
                title="解除知识库绑定"
                message={`将解除 ${dialog.dataset.name} 的${dialog.dataset.binding_kind === "primary" ? "主" : "共享"}绑定。`}
              />
            ) : null}
            <FormField
              label="变更原因"
              error={validationErrors.reason}
              fieldName="workspace_reason"
            >
              <WorkspaceTextarea
                ariaLabel="变更原因"
                name="workspace_reason"
                value={reason}
                error={validationErrors.reason}
                onChange={setReason}
              />
            </FormField>
          </Form>
          {workspace.mutation.error ? (
            <div className="enterprise-workspace-dialog__error" role="alert">
              <Alert
                theme="error"
                title={workspace.mutation.error.title}
                message={workspace.mutation.error.description}
              />
              {workspace.mutation.error.canRetry ? (
                <Button
                  tag="button"
                  variant="outline"
                  onClick={() => void workspace.retryMutation()}
                >
                  使用相同操作标识重试
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </AccessibleDialog>
    </section>
  );
}

function WorkspaceFields({
  description,
  setDescription,
  environment,
  setEnvironment,
  validationErrors,
}: {
  description: string;
  setDescription: (value: string) => void;
  environment: WorkspaceEnvironment;
  setEnvironment: (value: WorkspaceEnvironment) => void;
  validationErrors: WorkspaceValidationErrors;
}) {
  return (
    <>
      <FormField
        label="描述"
        error={validationErrors.description}
        fieldName="workspace_description"
      >
        <WorkspaceTextarea
          ariaLabel="描述"
          name="workspace_description"
          value={description}
          error={validationErrors.description}
          onChange={setDescription}
        />
      </FormField>
      <FormField label="环境">
        <WorkspaceSelect
          ariaLabel="环境"
          name="workspace_environment"
          value={environment}
          options={ENVIRONMENT_OPTIONS}
          onChange={(value) => setEnvironment(value as WorkspaceEnvironment)}
        />
      </FormField>
    </>
  );
}

function RoleField({
  value,
  onChange,
}: {
  value: WorkspaceRole;
  onChange: (value: WorkspaceRole) => void;
}) {
  return (
    <FormField label="Workspace 角色">
      <WorkspaceSelect
        ariaLabel="Workspace 角色"
        name="workspace_role"
        value={value}
        options={WORKSPACE_ROLE_OPTIONS}
        onChange={(next) => onChange(next as WorkspaceRole)}
      />
    </FormField>
  );
}

function dialogTitle(dialog: DialogState | null): string {
  const titles: Record<DialogKind, string> = {
    create: "创建 Workspace",
    edit: "编辑 Workspace",
    archive: "归档 Workspace",
    "add-member": "添加 Workspace 成员",
    "edit-member": "修改 Workspace 成员角色",
    "remove-member": "移除 Workspace 成员",
    "bind-dataset": "绑定知识库",
    "remove-dataset": "解除知识库绑定",
  };
  return dialog ? titles[dialog.kind] : "Workspace 操作";
}

function dialogConfirmLabel(dialog: DialogState | null): string {
  if (!dialog) return "确认";
  if (dialog.kind === "create") return "创建 Workspace";
  if (dialog.kind === "edit") return "保存 Workspace";
  if (dialog.kind === "archive") return "确认归档";
  if (dialog.kind === "add-member") return "添加成员";
  if (dialog.kind === "edit-member") return "保存成员角色";
  if (dialog.kind === "remove-member") return "确认移除";
  if (dialog.kind === "bind-dataset") return "绑定知识库";
  return "解除绑定";
}
