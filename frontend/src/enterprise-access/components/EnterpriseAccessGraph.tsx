import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Input,
  PrimaryTable,
  Select,
  Tabs,
  Tag,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  CheckCircleIcon,
  DeleteIcon,
  EditIcon,
  FolderOpenIcon,
  InfoCircleIcon,
  MailIcon,
  RefreshIcon,
  SearchIcon,
  ShareIcon,
  TreeCatalogIcon,
  UserIcon,
  UsergroupIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  useEnterpriseAccessGraph,
  type EnterpriseAccessCollection,
} from "../hooks/useEnterpriseAccessGraph";
import DatasetAccessGrantMutationDialog from "./DatasetAccessGrantMutationDialog";
import DatasetAclDisableDialog from "./DatasetAclDisableDialog";
import EnterpriseInvitationWorkspace from "./EnterpriseInvitationWorkspace";
import { fetchDatasetAccessSummary } from "../api/enterpriseAccessApi";
import {
  datasetAccessGrantMutationGate,
  datasetAclDisableGate,
  useDatasetAccessGrantMutations,
} from "../hooks/useDatasetAccessGrantMutations";
import type {
  DatasetAccessGrantDialogPayload,
  DatasetAccessGrantDialogMode,
} from "./DatasetAccessGrantMutationDialog";
import type {
  DatasetAccessGrant,
  EnterpriseGroup,
  EnterpriseGroupMember,
  OrganizationUnit,
  PersistentDatasetAccessSummary,
} from "../enterpriseAccessModel";
import "../enterprise-access.css";

type TabKey = "organization" | "groups" | "acl" | "invitations";

interface EnterpriseAccessGraphProps {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  accessSummary?: PersistentDatasetAccessSummary | null;
  onAccessSummaryRefresh?: () => Promise<void>;
}

interface ResourceStateProps<T> {
  resource: EnterpriseAccessCollection<T>;
  resourceLabel: string;
  emptyTitle: string;
  emptyDescription: string;
  idleTitle?: string;
  children: ReactNode;
}

const STATUS_OPTIONS = [
  { label: "全部状态", value: "all" },
  { label: "启用", value: "active" },
  { label: "已暂停", value: "suspended" },
  { label: "待处理", value: "pending" },
  { label: "已过期", value: "expired" },
];

const SUBJECT_TYPE_OPTIONS = [
  { label: "全部主体", value: "all" },
  { label: "账号", value: "account" },
  { label: "用户组", value: "group" },
  { label: "组织单元", value: "organization_unit" },
];

function numberLabel(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function dateTimeLabel(value: string): string {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function normalizedQuery(value: string): string {
  return value.trim().toLocaleLowerCase("zh-CN");
}

function containsQuery(query: string, values: Array<string | null | undefined>): boolean {
  const normalized = normalizedQuery(query);
  if (!normalized) return true;
  return values.some((value) => value?.toLocaleLowerCase("zh-CN").includes(normalized));
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return "未返回";
  const labels: Record<string, string> = {
    active: "启用",
    suspended: "已暂停",
    pending: "待处理",
    expired: "已过期",
    revoked: "已撤销",
    accepted: "已接受",
  };
  return labels[status] ?? status;
}

function roleLabel(role: string | null | undefined): string {
  if (!role) return "未返回";
  const labels: Record<string, string> = {
    owner: "所有者",
    admin: "管理员",
    editor: "编辑者",
    member: "成员",
    viewer: "只读成员",
    manager: "知识库管理员",
  };
  return labels[role] ?? role;
}

function subjectTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    account: "账号",
    group: "用户组",
    organization_unit: "组织单元",
  };
  return labels[type] ?? type;
}

function enforcementLabel(
  summary: PersistentDatasetAccessSummary | null | undefined,
  hasDataset: boolean,
): string {
  if (!hasDataset) return "未选择知识库";
  if (!summary) return "未返回服务端摘要";
  if (summary.acl_mode === "dataset_acl") return "持久 ACL 模式";
  if (summary.acl_mode === "tenant_role") return "租户角色模式";
  if (summary.enforcement_mode === "dataset_acl") return "知识库 ACL 已生效";
  if (
    summary.enforcement_mode === "tenant_role" ||
    summary.enforcement_mode === "tenant_role_fallback"
  ) {
    return "租户角色回退";
  }
  return `服务端模式：${summary.enforcement_mode}`;
}

function enforcementTheme(
  summary: PersistentDatasetAccessSummary | null | undefined,
  hasDataset: boolean,
): "success" | "warning" | "default" {
  if (!hasDataset || !summary) return "default";
  return (summary.acl_mode ?? summary.enforcement_mode) === "dataset_acl" ? "success" : "warning";
}

function supportLabel(value: boolean): string {
  return value ? "支持" : "不支持";
}

function statusTheme(
  status: string | null | undefined,
): "success" | "warning" | "danger" | "default" {
  if (status === "active" || status === "accepted") return "success";
  if (status === "pending") return "warning";
  if (status === "expired" || status === "revoked" || status === "suspended") return "danger";
  return "default";
}

function isReadableCapabilityState(state: string): boolean {
  return state === "ready" || state === "limited";
}

function StatusTag({ status }: { status: string | null | undefined }) {
  return (
    <Tag theme={statusTheme(status)} variant="light-outline" size="small">
      {statusLabel(status)}
    </Tag>
  );
}

function ResourceState<T>({
  resource,
  resourceLabel,
  emptyTitle,
  emptyDescription,
  idleTitle,
  children,
}: ResourceStateProps<T>) {
  if (!isReadableCapabilityState(resource.capabilityState)) {
    return (
      <div className="enterprise-access-unavailable" role="status">
        <InfoCircleIcon aria-hidden="true" />
        <div>
          <strong>{resourceLabel}尚未接入</strong>
          <span>{resource.capabilityReason || "服务端未声明该能力可用"}</span>
        </div>
        <Tag variant="light-outline" size="small">
          尚未接入
        </Tag>
      </div>
    );
  }
  if (resource.status === "idle") {
    return (
      <PageState
        status="empty"
        compact
        title={idleTitle || `${resourceLabel}尚未开始读取`}
        description={resource.capabilityReason || "满足读取条件后加载真实访问事实"}
      />
    );
  }
  if (resource.status === "loading") {
    return (
      <PageState
        status="loading"
        compact
        title={`正在读取${resourceLabel}`}
        description="从企业目录读取当前租户下的真实访问事实"
      />
    );
  }
  if (resource.status !== "ready" && resource.error) {
    return (
      <PageState
        status="error"
        compact
        title={resource.error.title}
        description={resource.error.description}
        extra={
          resource.error.canRetry ? (
            <Button
              variant="outline"
              size="small"
              icon={<RefreshIcon />}
              onClick={() => void resource.reload()}
            >
              重新读取
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (resource.status === "ready" && resource.items.length === 0) {
    return <PageState status="empty" compact title={emptyTitle} description={emptyDescription} />;
  }
  return <>{children}</>;
}

function FactMetric<T>({
  resource,
  label,
  ariaLabel,
  icon,
}: {
  resource: EnterpriseAccessCollection<T>;
  label: string;
  ariaLabel: string;
  icon: ReactNode;
}) {
  const value =
    resource.status === "ready" && resource.count !== null ? numberLabel(resource.count) : "—";
  const detail =
    resource.status === "loading"
      ? "读取中"
      : resource.status === "ready"
        ? "服务端总量"
        : !isReadableCapabilityState(resource.capabilityState)
          ? "能力未接入"
          : resource.status === "idle"
            ? "等待读取"
            : resource.status === "migration-required"
              ? "等待迁移"
              : "读取受限";
  return (
    <div className="enterprise-access-fact" role="group" aria-label={ariaLabel}>
      <span className="enterprise-access-fact__icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function GraphPrelude({ workspace }: { workspace: ReturnType<typeof useEnterpriseAccessGraph> }) {
  return (
    <>
      <Alert
        className="enterprise-access-authority"
        theme="info"
        icon={<InfoCircleIcon />}
        title="访问事实按当前租户范围读取"
        message="组织、用户组、邀请和知识库授权均来自只读企业接口；未接入能力不发请求，也不展示模拟关系。"
      />
      <div className="enterprise-access-facts" aria-label="企业访问关系摘要">
        <FactMetric
          resource={workspace.organizationUnits}
          label="组织节点"
          ariaLabel="组织节点事实"
          icon={<TreeCatalogIcon />}
        />
        <FactMetric
          resource={workspace.groups}
          label="用户组"
          ariaLabel="用户组事实"
          icon={<UsergroupIcon />}
        />
        <FactMetric
          resource={workspace.accessGrants}
          label="ACL 授权"
          ariaLabel="ACL 授权事实"
          icon={<FolderOpenIcon />}
        />
        <FactMetric
          resource={workspace.invitations}
          label="邀请记录"
          ariaLabel="邀请事实"
          icon={<MailIcon />}
        />
      </div>
      <div className="enterprise-access-relation-summary" role="note">
        <ShareIcon aria-hidden="true" />
        <strong>关系摘要</strong>
        <span>组织单元 → 用户组 / 账号 → 知识库角色</span>
        <small>当前工作面只展示服务端确认的直接关系，不推断未返回的继承链。</small>
      </div>
    </>
  );
}

function FilterToolbar({
  searchLabel,
  placeholder,
  query,
  onQueryChange,
  status,
  onStatusChange,
  extra,
}: {
  searchLabel: string;
  placeholder: string;
  query: string;
  onQueryChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  extra?: ReactNode;
}) {
  return (
    <div className="enterprise-access-toolbar">
      <label className="enterprise-access-search">
        <span className="sr-only">{searchLabel}</span>
        <Input
          type="search"
          value={query}
          placeholder={placeholder}
          prefixIcon={<SearchIcon />}
          clearable
          onChange={(value) => onQueryChange(String(value))}
        />
      </label>
      <Select
        className="enterprise-access-filter-select"
        value={status}
        options={STATUS_OPTIONS}
        aria-label="筛选状态"
        onChange={(value) => onStatusChange(String(value))}
      />
      {extra}
      <span className="enterprise-access-toolbar__note">仅筛选当前已加载结果</span>
    </div>
  );
}

function LoadMoreFooter<T>({
  resource,
  label,
}: {
  resource: EnterpriseAccessCollection<T>;
  label: string;
}) {
  if (resource.nextBeforeId === null) return null;
  return (
    <div className="enterprise-access-load-more">
      {resource.error ? (
        <Alert theme="error" title="继续读取失败" message={resource.error.description} />
      ) : null}
      <div>
        <span>
          已展示 {numberLabel(resource.items.length)} /{" "}
          {numberLabel(resource.count ?? resource.items.length)}
        </span>
        <Button
          variant="outline"
          size="small"
          aria-busy={resource.loadingMore}
          aria-disabled={resource.loadingMore ? "true" : undefined}
          aria-label={resource.loadingMore ? `正在加载更多${label}` : `加载更多${label}`}
          onClick={() => {
            if (!resource.loadingMore) void resource.loadMore();
          }}
        >
          {resource.loadingMore ? "正在加载" : "加载更多"}
        </Button>
      </div>
    </div>
  );
}

function organizationDepth(item: OrganizationUnit, items: OrganizationUnit[]): number {
  const byId = new Map(items.map((candidate) => [candidate.id, candidate]));
  let depth = 0;
  let parentId = item.parent_id;
  const visited = new Set<string>();
  while (parentId && depth < 4 && !visited.has(parentId)) {
    visited.add(parentId);
    depth += 1;
    parentId = byId.get(parentId)?.parent_id ?? null;
  }
  return depth;
}

function OrganizationWorkspace({
  workspace,
}: {
  workspace: ReturnType<typeof useEnterpriseAccessGraph>;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const items = useMemo(
    () =>
      workspace.organizationUnits.items.filter(
        (item) =>
          (status === "all" || item.status === status) &&
          containsQuery(query, [item.name, item.code, item.id, item.parent_id]),
      ),
    [query, status, workspace.organizationUnits.items],
  );
  const columns = useMemo<PrimaryTableCol<OrganizationUnit>[]>(
    () => [
      {
        colKey: "name",
        title: "组织单元",
        width: 260,
        cell: ({ row }) => {
          const depth = organizationDepth(row, workspace.organizationUnits.items);
          return (
            <div className={`enterprise-access-entity depth-${depth}`}>
              <span className="enterprise-access-entity__node" aria-hidden="true" />
              <div>
                <strong>{row.name}</strong>
                <span>{row.id}</span>
              </div>
            </div>
          );
        },
      },
      { colKey: "code", title: "编码", width: 130 },
      {
        colKey: "status",
        title: "状态",
        width: 110,
        cell: ({ row }) => <StatusTag status={row.status} />,
      },
      {
        colKey: "member_count",
        title: "成员",
        width: 100,
        align: "right",
        cell: ({ row }) => numberLabel(row.member_count),
      },
      {
        colKey: "child_count",
        title: "子单元",
        width: 100,
        align: "right",
        cell: ({ row }) => numberLabel(row.child_count),
      },
      {
        colKey: "parent_id",
        title: "上级关系",
        width: 180,
        cell: ({ row }) => row.parent_id || "根节点",
      },
    ],
    [workspace.organizationUnits.items],
  );

  return (
    <div className="enterprise-access-panel" aria-label="组织架构访问事实">
      <FilterToolbar
        searchLabel="筛选已加载的组织单元"
        placeholder="搜索组织名称、编码或 ID"
        query={query}
        onQueryChange={setQuery}
        status={status}
        onStatusChange={setStatus}
      />
      <ResourceState
        resource={workspace.organizationUnits}
        resourceLabel="组织架构"
        emptyTitle="暂无组织单元"
        emptyDescription="真实接口已返回空列表"
      >
        {items.length ? (
          <div
            className="enterprise-access-table-viewport"
            data-testid="enterprise-access-organization-table"
            tabIndex={0}
            aria-label="组织架构表格，可横向滚动"
          >
            <PrimaryTable
              rowKey="id"
              data={items}
              columns={columns}
              tableLayout="fixed"
              bordered={false}
              hover
              size="small"
            />
          </div>
        ) : (
          <div className="enterprise-access-filter-empty" role="status">
            没有匹配当前已加载结果
          </div>
        )}
        <LoadMoreFooter resource={workspace.organizationUnits} label="组织单元" />
      </ResourceState>
    </div>
  );
}

function GroupWorkspace({ workspace }: { workspace: ReturnType<typeof useEnterpriseAccessGraph> }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const groups = useMemo(
    () =>
      workspace.groups.items.filter(
        (group) =>
          (status === "all" || group.status === status) &&
          containsQuery(query, [group.name, group.description, group.id]),
      ),
    [query, status, workspace.groups.items],
  );
  const groupColumns = useMemo<PrimaryTableCol<EnterpriseGroup>[]>(
    () => [
      {
        colKey: "name",
        title: "用户组",
        width: 230,
        cell: ({ row }) => (
          <div className="enterprise-access-entity">
            <UsergroupIcon aria-hidden="true" />
            <div>
              <strong>{row.name}</strong>
              <span>{row.description || row.id}</span>
            </div>
          </div>
        ),
      },
      {
        colKey: "status",
        title: "状态",
        width: 100,
        cell: ({ row }) => <StatusTag status={row.status} />,
      },
      {
        colKey: "member_count",
        title: "成员",
        width: 90,
        align: "right",
        cell: ({ row }) => numberLabel(row.member_count),
      },
      {
        colKey: "operation",
        title: "成员关系",
        width: 120,
        fixed: "right",
        cell: ({ row }) => (
          <Button
            variant="text"
            theme="primary"
            size="small"
            aria-label={`查看${row.name}成员`}
            onClick={() => workspace.selectGroup(row.id)}
          >
            查看成员
          </Button>
        ),
      },
    ],
    [workspace],
  );
  const memberColumns = useMemo<PrimaryTableCol<EnterpriseGroupMember>[]>(
    () => [
      {
        colKey: "name",
        title: "成员",
        width: 210,
        cell: ({ row }) => (
          <div className="enterprise-access-entity">
            <UserIcon aria-hidden="true" />
            <div>
              <strong>{row.name || row.account_id || "未返回名称"}</strong>
              <span>{row.email || row.account_id || row.id}</span>
            </div>
          </div>
        ),
      },
      { colKey: "role", title: "角色", width: 110, cell: ({ row }) => roleLabel(row.role) },
      {
        colKey: "status",
        title: "状态",
        width: 100,
        cell: ({ row }) => <StatusTag status={row.status} />,
      },
    ],
    [],
  );

  return (
    <div className="enterprise-access-panel" aria-label="用户组访问事实">
      <FilterToolbar
        searchLabel="筛选已加载的用户组"
        placeholder="搜索用户组名称、描述或 ID"
        query={query}
        onQueryChange={setQuery}
        status={status}
        onStatusChange={setStatus}
      />
      <ResourceState
        resource={workspace.groups}
        resourceLabel="用户组"
        emptyTitle="暂无用户组"
        emptyDescription="真实接口已返回空列表"
      >
        <div className="enterprise-access-group-layout">
          <div>
            {groups.length ? (
              <div
                className="enterprise-access-table-viewport"
                tabIndex={0}
                aria-label="用户组表格，可横向滚动"
              >
                <PrimaryTable
                  rowKey="id"
                  data={groups}
                  columns={groupColumns}
                  tableLayout="fixed"
                  bordered={false}
                  hover
                  size="small"
                />
              </div>
            ) : (
              <div className="enterprise-access-filter-empty" role="status">
                没有匹配当前已加载结果
              </div>
            )}
            <LoadMoreFooter resource={workspace.groups} label="用户组" />
          </div>
          <div className="enterprise-access-relation-pane">
            <div className="enterprise-access-relation-pane__header">
              <div>
                <span>组成员关系</span>
                <strong>{workspace.selectedGroup?.name || "尚未选择用户组"}</strong>
              </div>
              {workspace.selectedGroup ? (
                <Tag variant="light-outline" size="small">
                  {numberLabel(workspace.selectedGroup.member_count)} 位成员
                </Tag>
              ) : null}
            </div>
            <ResourceState
              resource={workspace.groupMembers}
              resourceLabel="用户组成员"
              idleTitle="选择用户组后读取成员关系"
              emptyTitle="该用户组暂无成员"
              emptyDescription="真实接口已返回空列表"
            >
              <div
                className="enterprise-access-table-viewport"
                tabIndex={0}
                aria-label="用户组成员表格，可横向滚动"
              >
                <PrimaryTable
                  rowKey="id"
                  data={workspace.groupMembers.items}
                  columns={memberColumns}
                  tableLayout="fixed"
                  bordered={false}
                  hover
                  size="small"
                />
              </div>
              <LoadMoreFooter resource={workspace.groupMembers} label="用户组成员" />
            </ResourceState>
          </div>
        </div>
      </ResourceState>
    </div>
  );
}

function AclEnforcementEvidence({
  accessSummary,
  hasDataset,
}: {
  accessSummary?: PersistentDatasetAccessSummary | null;
  hasDataset: boolean;
}) {
  const enforcement = enforcementLabel(accessSummary, hasDataset);
  const hasSummary = hasDataset && Boolean(accessSummary);
  const permissions = accessSummary?.effective_permissions ?? [];
  const supportFacts = accessSummary
    ? [
        { key: "dataset_acl_supported", value: accessSummary.dataset_acl_supported },
        { key: "group_grants_supported", value: accessSummary.group_grants_supported },
        {
          key: "organization_inheritance_supported",
          value: accessSummary.organization_inheritance_supported,
        },
      ]
    : [];

  return (
    <section
      className="enterprise-access-acl-evidence"
      role="group"
      aria-label="知识库 ACL 生效证据"
    >
      <div className="enterprise-access-acl-evidence__header">
        <div className="enterprise-access-acl-evidence__enforcement">
          <span>服务端 enforcement</span>
          <strong>{enforcement}</strong>
          <small>
            {hasSummary
              ? `enforcement_mode：${accessSummary?.enforcement_mode}`
              : "权限状态仅由服务端访问摘要确认"}
          </small>
        </div>
        <Tag
          theme={enforcementTheme(accessSummary, hasDataset)}
          variant="light-outline"
          size="small"
        >
          {hasSummary ? "服务端摘要" : hasDataset ? "等待摘要" : "待选择"}
        </Tag>
      </div>

      {accessSummary && hasDataset ? (
        <>
          <div className="enterprise-access-acl-evidence__facts" aria-label="ACL 服务端事实">
            <div className="enterprise-access-acl-evidence__fact">
              <span>actor_role</span>
              <strong>{roleLabel(accessSummary.actor_role)}</strong>
              <small>{accessSummary.actor_role}</small>
            </div>
            <div className="enterprise-access-acl-evidence__fact">
              <span>dataset_id</span>
              <strong>{accessSummary.dataset_id}</strong>
              <small>{accessSummary.visibility}</small>
            </div>
            {supportFacts.map((fact) => (
              <div className="enterprise-access-acl-evidence__fact" key={fact.key}>
                <span>{fact.key}</span>
                <Tag
                  theme={fact.value ? "success" : "default"}
                  variant="light-outline"
                  size="small"
                >
                  {supportLabel(fact.value)}
                </Tag>
              </div>
            ))}
          </div>
          {accessSummary.acl_mode ? (
            <>
              <div
                className="enterprise-access-acl-evidence__persistence"
                aria-label="持久 ACL 模式事实"
              >
                <div>
                  <span>acl_mode</span>
                  <strong>{accessSummary.acl_mode}</strong>
                </div>
                <div>
                  <span>acl_revision</span>
                  <strong>{accessSummary.acl_revision ?? "未返回"}</strong>
                </div>
                <div>
                  <span>acl_enabled_at</span>
                  <strong>
                    {accessSummary.acl_enabled_at
                      ? dateTimeLabel(accessSummary.acl_enabled_at)
                      : "未返回"}
                  </strong>
                </div>
                <div>
                  <span>acl_enabled_by</span>
                  <strong>{accessSummary.acl_enabled_by || "未返回"}</strong>
                </div>
              </div>
              {accessSummary.acl_mode === "dataset_acl" ? (
                <Alert
                  className="enterprise-access-acl-evidence__persistence-note"
                  theme="success"
                  title="持久 ACL 模式已启用"
                  message="最后一条授权撤销后仍保持 ACL，不会回退到租户角色。"
                />
              ) : null}
            </>
          ) : null}
          <div className="enterprise-access-acl-evidence__permissions">
            <span>effective_permissions</span>
            <div className="enterprise-access-acl-evidence__permission-list">
              {permissions.length ? (
                permissions.map((permission, index) => (
                  <Tag
                    key={`${permission}-${index}`}
                    theme="primary"
                    variant="light-outline"
                    size="small"
                  >
                    {permission}
                  </Tag>
                ))
              ) : (
                <span>未返回权限</span>
              )}
            </div>
          </div>
          {accessSummary.warnings.length ? (
            <Alert
              className="enterprise-access-acl-evidence__warnings"
              theme="warning"
              title="服务端 warnings"
              message={accessSummary.warnings.join("；")}
            />
          ) : (
            <div className="enterprise-access-acl-evidence__no-warnings" role="status">
              服务端未返回 warnings
            </div>
          )}
        </>
      ) : (
        <div className="enterprise-access-acl-evidence__empty" role="status">
          <strong>{hasDataset ? "尚未返回访问摘要" : "当前没有选择知识库"}</strong>
          <span>
            {hasDataset ? "页面不会根据 grant 列表推断实际权限" : "选择知识库后读取服务端访问摘要"}
          </span>
        </div>
      )}
      <p className="enterprise-access-acl-evidence__note">
        实际权限以服务端访问摘要为准；下方授权列表仅展示目录关系，不用于推断实际权限。
      </p>
    </section>
  );
}

function AclWorkspace({
  workspace,
  accessSummary,
  hasDataset,
  scope,
  context,
  onAccessSummaryRefresh,
}: {
  workspace: ReturnType<typeof useEnterpriseAccessGraph>;
  accessSummary?: PersistentDatasetAccessSummary | null;
  hasDataset: boolean;
  scope: EnterpriseScope;
  context: EnterpriseContext;
  onAccessSummaryRefresh?: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [subjectType, setSubjectType] = useState("all");
  const [dialogState, setDialogState] = useState<{
    mode: DatasetAccessGrantDialogMode;
    grant: DatasetAccessGrant | null;
  } | null>(null);
  const gate = datasetAccessGrantMutationGate(context, scope);
  const disableGate = datasetAclDisableGate(context, scope, accessSummary);
  const dialogGrant = dialogState?.grant ?? null;
  const [disableDialogVisible, setDisableDialogVisible] = useState(false);
  const {
    saving,
    error,
    success,
    create,
    updateRole,
    revoke,
    resume,
    disable,
    retry,
    refreshFacts,
    clear,
  } = useDatasetAccessGrantMutations({
    scope,
    context,
    accessGrants: workspace.accessGrants,
    accessSummary,
    onAccessSummaryRefresh,
  });
  const grants = useMemo(
    () =>
      workspace.accessGrants.items.filter(
        (grant) =>
          (status === "all" || grant.status === status) &&
          (subjectType === "all" || grant.subject_type === subjectType) &&
          containsQuery(query, [grant.subject_name, grant.subject_id, grant.role, grant.id]),
      ),
    [query, status, subjectType, workspace.accessGrants.items],
  );
  const openDialog = useCallback(
    (mode: DatasetAccessGrantDialogMode, grant: DatasetAccessGrant | null = null) => {
      clear();
      setDialogState({ mode, grant });
    },
    [clear],
  );
  const closeDialog = useCallback(() => setDialogState(null), []);
  const openDisableDialog = useCallback(() => {
    clear();
    setDisableDialogVisible(true);
  }, [clear]);
  const closeDisableDialog = useCallback(() => setDisableDialogVisible(false), []);
  const refreshAfterConflict = useCallback(async () => {
    await refreshFacts();
    clear();
  }, [clear, refreshFacts]);
  const submitDialog = useCallback(
    async (payload: DatasetAccessGrantDialogPayload) => {
      if (payload.mode === "create") return create(payload.input);
      if (payload.mode === "role") {
        if (!dialogGrant) return null;
        return updateRole(dialogGrant, payload.input);
      }
      if (!dialogGrant) return null;
      return payload.mode === "revoke"
        ? revoke(dialogGrant, payload.input)
        : resume(dialogGrant, payload.input);
    },
    [dialogGrant, create, revoke, resume, updateRole],
  );
  const submitDisable = useCallback(
    (payload: { expected_acl_revision: number; reason: string }) => disable(payload),
    [disable],
  );
  const columns = useMemo<PrimaryTableCol<DatasetAccessGrant>[]>(
    () => [
      {
        colKey: "subject_name",
        title: "授权主体",
        width: 260,
        cell: ({ row }) => (
          <div className="enterprise-access-entity">
            <ShareIcon aria-hidden="true" />
            <div>
              <strong>{row.subject_name}</strong>
              <span>{row.subject_id}</span>
            </div>
          </div>
        ),
      },
      {
        colKey: "subject_type",
        title: "主体类型",
        width: 120,
        cell: ({ row }) => subjectTypeLabel(row.subject_type),
      },
      { colKey: "role", title: "知识库角色", width: 130, cell: ({ row }) => roleLabel(row.role) },
      {
        colKey: "status",
        title: "状态",
        width: 100,
        cell: ({ row }) => <StatusTag status={row.status} />,
      },
      {
        colKey: "revision",
        title: "版本",
        width: 110,
        cell: ({ row }) => `revision ${row.revision}`,
      },
      { colKey: "id", title: "授权 ID", width: 180 },
      {
        colKey: "operation",
        title: "操作",
        width: 220,
        fixed: "right",
        cell: ({ row }) => {
          const disabledTitle = gate.allowed ? undefined : gate.reason;
          if (row.status === "active") {
            return (
              <div className="enterprise-access-acl-actions">
                <Button
                  tag="button"
                  variant="text"
                  theme="primary"
                  size="small"
                  icon={<EditIcon />}
                  aria-label={`变更${row.subject_name}角色`}
                  title={disabledTitle}
                  disabled={!gate.allowed}
                  onClick={() => openDialog("role", row)}
                >
                  变更角色
                </Button>
                <Button
                  tag="button"
                  variant="text"
                  theme="danger"
                  size="small"
                  icon={<DeleteIcon />}
                  aria-label={`撤销${row.subject_name}授权`}
                  title={disabledTitle}
                  disabled={!gate.allowed}
                  onClick={() => openDialog("revoke", row)}
                >
                  撤销
                </Button>
              </div>
            );
          }
          if (row.status === "revoked") {
            return (
              <Button
                tag="button"
                variant="text"
                theme="primary"
                size="small"
                icon={<CheckCircleIcon />}
                aria-label={`恢复${row.subject_name}授权`}
                title={disabledTitle}
                disabled={!gate.allowed}
                onClick={() => openDialog("resume", row)}
              >
                恢复
              </Button>
            );
          }
          return <span className="enterprise-access-acl-actions__muted">不可操作</span>;
        },
      },
    ],
    [gate.allowed, gate.reason, openDialog],
  );

  return (
    <div className="enterprise-access-panel" aria-label="知识库 ACL 访问事实">
      <AclEnforcementEvidence accessSummary={accessSummary} hasDataset={hasDataset} />
      {success ? (
        <Alert
          className="enterprise-access-mutation-success"
          theme="success"
          title={success}
          message="已局部更新当前授权关系，并请求刷新服务端访问摘要与授权列表。"
        />
      ) : null}
      <FilterToolbar
        searchLabel="筛选已加载的知识库 ACL"
        placeholder="搜索主体名称、ID 或角色"
        query={query}
        onQueryChange={setQuery}
        status={status}
        onStatusChange={setStatus}
        extra={
          <div className="enterprise-access-acl-toolbar-actions">
            <Select
              className="enterprise-access-filter-select"
              value={subjectType}
              options={SUBJECT_TYPE_OPTIONS}
              aria-label="筛选授权主体类型"
              onChange={(value) => setSubjectType(String(value))}
            />
            <Button
              tag="button"
              theme="primary"
              size="small"
              icon={<AddIcon />}
              aria-label="新增授权"
              title={gate.allowed ? undefined : gate.reason}
              disabled={!gate.allowed || saving}
              onClick={() => openDialog("create")}
            >
              新增授权
            </Button>
            <Button
              tag="button"
              theme="danger"
              variant="outline"
              size="small"
              aria-label="停用 ACL"
              title={disableGate.allowed ? undefined : disableGate.reason}
              disabled={!disableGate.allowed || saving}
              onClick={openDisableDialog}
            >
              停用 ACL
            </Button>
          </div>
        }
      />
      <ResourceState
        resource={workspace.accessGrants}
        resourceLabel="知识库 ACL"
        idleTitle="未选择知识库"
        emptyTitle="暂无知识库授权"
        emptyDescription="真实接口已返回空列表"
      >
        {grants.length ? (
          <div
            className="enterprise-access-table-viewport"
            tabIndex={0}
            aria-label="知识库 ACL 表格，可横向滚动"
          >
            <PrimaryTable
              rowKey="id"
              data={grants}
              columns={columns}
              tableLayout="fixed"
              bordered={false}
              hover
              size="small"
            />
          </div>
        ) : (
          <div className="enterprise-access-filter-empty" role="status">
            没有匹配当前已加载结果
          </div>
        )}
        <LoadMoreFooter resource={workspace.accessGrants} label="知识库授权" />
      </ResourceState>
      <DatasetAccessGrantMutationDialog
        visible={Boolean(dialogState)}
        mode={dialogState?.mode ?? "create"}
        grant={dialogState?.grant ?? null}
        saving={saving}
        error={error}
        onClose={closeDialog}
        onRefresh={refreshAfterConflict}
        onRetry={retry}
        onSubmit={submitDialog}
      />
      <DatasetAclDisableDialog
        visible={disableDialogVisible}
        scope={scope}
        aclRevision={accessSummary?.acl_revision}
        currentAclMode={accessSummary?.acl_mode}
        saving={saving}
        error={error}
        approvalRequiredHint={
          error?.code === "dataset_acl_approval_required" ? error.approvalRequired : null
        }
        onClose={closeDisableDialog}
        onRefresh={refreshAfterConflict}
        onRetry={retry}
        onSubmit={submitDisable}
      />
    </div>
  );
}

function tabLabel(label: string, value: TabKey, activeTab: TabKey) {
  return (
    <span role="tab" aria-selected={activeTab === value} tabIndex={activeTab === value ? 0 : -1}>
      {label}
    </span>
  );
}
export default function EnterpriseAccessGraph({
  scope,
  context,
  accessSummary,
  onAccessSummaryRefresh,
}: EnterpriseAccessGraphProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("organization");
  const [liveAccessSummary, setLiveAccessSummary] = useState<PersistentDatasetAccessSummary | null>(
    accessSummary ?? null,
  );
  const workspace = useEnterpriseAccessGraph(scope, context.capabilities);
  const refreshAccessSummary = useCallback(async () => {
    if (!scope.datasetId?.trim()) return;
    if (onAccessSummaryRefresh) {
      await onAccessSummaryRefresh();
      return;
    }
    const next = await fetchDatasetAccessSummary(scope);
    setLiveAccessSummary(next);
  }, [onAccessSummaryRefresh, scope]);
  const prelude = <GraphPrelude workspace={workspace} />;

  useEffect(() => {
    setLiveAccessSummary(accessSummary ?? null);
  }, [accessSummary]);

  return (
    <section
      className="enterprise-access-graph enterprise-admin-surface"
      role="region"
      aria-label="企业访问图谱"
    >
      <header className="enterprise-access-graph__header">
        <div className="enterprise-access-graph__title-mark" aria-hidden="true">
          <ShareIcon />
        </div>
        <div>
          <span className="enterprise-access-graph__eyebrow">ACCESS GRAPH / GOVERNED</span>
          <h2>企业访问图谱</h2>
          <p>以组织、用户组、邀请与知识库授权为主线，核对当前租户下的真实访问关系。</p>
        </div>
        <Tag
          variant="light-outline"
          theme={datasetAccessGrantMutationGate(context, scope).allowed ? "success" : "primary"}
          size="small"
        >
          {datasetAccessGrantMutationGate(context, scope).allowed ? "ACL 管理" : "只读事实"}
        </Tag>
      </header>
      <Tabs
        className="enterprise-access-tabs"
        value={activeTab}
        theme="normal"
        onChange={(value) => setActiveTab(String(value) as TabKey)}
      >
        <Tabs.TabPanel
          value="organization"
          label={tabLabel("组织架构", "organization", activeTab)}
          destroyOnHide
        >
          {prelude}
          <OrganizationWorkspace workspace={workspace} />
        </Tabs.TabPanel>
        <Tabs.TabPanel value="groups" label={tabLabel("用户组", "groups", activeTab)} destroyOnHide>
          {prelude}
          <GroupWorkspace workspace={workspace} />
        </Tabs.TabPanel>
        <Tabs.TabPanel value="acl" label={tabLabel("知识库 ACL", "acl", activeTab)} destroyOnHide>
          {prelude}
          <AclWorkspace
            workspace={workspace}
            accessSummary={liveAccessSummary}
            hasDataset={Boolean(scope.datasetId?.trim())}
            scope={scope}
            context={context}
            onAccessSummaryRefresh={refreshAccessSummary}
          />
        </Tabs.TabPanel>
        <Tabs.TabPanel
          value="invitations"
          label={tabLabel("成员邀请", "invitations", activeTab)}
          destroyOnHide
        >
          {prelude}
          <EnterpriseInvitationWorkspace
            scope={scope}
            context={context}
            invitations={workspace.invitations}
          />
        </Tabs.TabPanel>
      </Tabs>
    </section>
  );
}
