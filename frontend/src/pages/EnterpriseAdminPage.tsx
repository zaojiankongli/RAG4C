import "../enterprise-admin/enterprise-admin.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  Drawer,
  PrimaryTable,
  Tag,
  Textarea,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  BrowseIcon,
  CheckIcon,
  ControlPlatformIcon,
  RefreshIcon,
  SecuredIcon,
  UsergroupIcon,
} from "tdesign-icons-react";
import PageState from "../components/PageState";
import PageTopbar from "../components/PageTopbar";
import { ApiError, request } from "../api/client";
import { AuthorityBanner } from "../ui/enterprise";
import { CapabilityState, EnterpriseResourceErrorState } from "../enterprise-admin/components";
import MemberRoleApprovalDialog from "../enterprise-admin/components/MemberRoleApprovalDialog";
import { EnterpriseAccessGraph } from "../enterprise-access";
import { EnterpriseIdentityCenter } from "../enterprise-identity";
import OidcCallbackSurface from "../enterprise-identity/components/OidcCallbackSurface";
import { oidcCallbackFromLocation } from "../enterprise-identity/oidcRuntimeRoute";
import { EnterpriseComplianceCenter } from "../enterprise-compliance";
import {
  EnterpriseApprovalCenter,
  enterpriseApprovalRouteFromLocation,
} from "../enterprise-approval";
import {
  EnterpriseWorkspaceCenter,
  enterpriseWorkspaceRouteFromLocation,
} from "../enterprise-workspace";
import TenantInvitationAcceptSurface from "../enterprise-access/components/TenantInvitationAcceptSurface";
import {
  clearInvitationAcceptLocation,
  invitationAcceptTokenFromLocation,
} from "../enterprise-access/invitationRoute";
import { fetchDatasetAccessSummary } from "../enterprise-access/api/enterpriseAccessApi";
import type { PersistentDatasetAccessSummary } from "../enterprise-access/enterpriseAccessModel";
import {
  restoreEnterpriseMember,
  suspendEnterpriseMember,
  updateEnterpriseMemberRole,
} from "../enterprise-admin/api/enterpriseAdminApi";
import { useEnterpriseAdminWorkspace } from "../enterprise-admin/hooks";
import { useMemberRoleApprovalGate } from "../enterprise-admin/hooks/useMemberRoleApprovalGate";
import { navigateToApprovalCenter } from "../enterprise-admin/memberRoleApprovalNavigation";
import type {
  EnterpriseCapability,
  EnterpriseContext,
  EnterpriseMember,
  EnterpriseResourceError,
  EnterpriseScope,
} from "../enterprise-admin/model";
import {
  KNOWLEDGE_DATASET_STORAGE_KEY,
  readKnowledgeActorToken,
  resolveKnowledgeWorkspaceScope,
} from "../knowledge/workspaceScope";

const ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  member: "成员",
};

const CAPABILITY_FALLBACKS: Record<string, EnterpriseCapability> = {
  organization_units: {
    state: "unavailable",
    label: "组织架构",
    reason: "尚未接入企业组织目录",
  },
  user_groups: {
    state: "unavailable",
    label: "用户组",
    reason: "缺少用户组存储、组成员关系和批量授权能力",
  },
  dataset_acl: {
    state: "unavailable",
    label: "知识库 ACL",
    reason: "当前仍按租户固定角色执行授权",
  },
  sso: {
    state: "unavailable",
    label: "企业 SSO",
    reason: "尚未配置 OIDC、SAML 或企业身份提供商",
  },
};

const IDENTITY_MISSING_CAPABILITIES: EnterpriseCapability[] = [
  {
    state: "limited",
    label: "企业成员目录",
    reason: "连接身份后读取真实成员关系，不展示模拟成员",
  },
  {
    state: "limited",
    label: "固定角色权限",
    reason: "服务端已定义角色策略，连接身份后展示当前有效权限",
  },
  {
    state: "unavailable",
    label: "组织架构",
    reason: "尚未接入企业组织目录和部门关系",
  },
  {
    state: "unavailable",
    label: "用户组与知识库 ACL",
    reason: "用户组、直接授权和权限继承仍待企业目录迁移",
  },
];

type PageMember = EnterpriseMember & {
  status?: string | null;
  revision?: number | null;
};

interface EnterpriseReadinessReport {
  expected_head?: string | null;
  current_revision?: string | null;
  status?: string;
  missing_capability_groups?: string[];
  mutations_safe?: boolean;
}

interface MutationGate {
  allowed: boolean;
  reason: string;
}

type MemberMutationAction = "role" | "suspend" | "restore";

interface MemberMutationTarget {
  member: PageMember;
  action: MemberMutationAction;
}

interface MemberMutationPayload {
  expected_revision: number;
  reason: string;
  role?: string;
  status?: string;
}

interface AuditEvent {
  sequence: number;
  id: string;
  tenant_id: string;
  dataset_id?: string | null;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  before_snapshot?: unknown;
  after_snapshot?: unknown;
  request_id?: string | null;
  request_ip?: string | null;
  occurred_at: string;
}

interface AuditListResponse {
  items: AuditEvent[];
  count: number;
  next_before_sequence: number | null;
}

interface SurfaceError {
  title: string;
  description: string;
  canRetry: boolean;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function numberLabel(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function dateLabel(value: string): string {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(parsed);
}

function readExplicitDatasetId(): string | undefined {
  if (typeof localStorage === "undefined") return undefined;
  try {
    return localStorage.getItem(KNOWLEDGE_DATASET_STORAGE_KEY)?.trim() || undefined;
  } catch {
    return undefined;
  }
}

function scopeFromStorage(): EnterpriseScope {
  const actorToken = readKnowledgeActorToken();
  const workspace = resolveKnowledgeWorkspaceScope({ actorToken });
  return {
    tenantId: workspace.tenantId,
    datasetId: readExplicitDatasetId(),
    actorToken,
  };
}

const MEMBER_ROLE_OPTIONS = [
  { label: "所有者", value: "owner" },
  { label: "管理员", value: "admin" },
  { label: "编辑者", value: "editor" },
  { label: "成员", value: "member" },
];

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  return {
    "X-RAG4C-Tenant": scope.tenantId,
    Authorization: `Bearer ${scope.actorToken}`,
  };
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return "未返回";
  if (status === "active") return "启用";
  if (status === "suspended" || status === "paused") return "已暂停";
  return status;
}

function isSuspended(status: string | null | undefined): boolean {
  return status === "suspended" || status === "paused";
}

function hasManagePermission(context: EnterpriseContext): boolean {
  const actor = context.actor as EnterpriseContext["actor"] & {
    permissions?: unknown;
    enterprise_permissions?: unknown;
  };
  const permissionValues = [
    ...context.effective_permissions,
    ...(Array.isArray(actor.permissions) ? actor.permissions.map(String) : []),
    ...(Array.isArray(actor.enterprise_permissions)
      ? actor.enterprise_permissions.map(String)
      : []),
  ];
  return permissionValues.some((permission) =>
    ["knowledge.manage", "enterprise.manage", "tenant.manage", "enterprise_admin"].includes(
      permission,
    ),
  );
}

function mutationGate(
  context: EnterpriseContext,
  readiness: EnterpriseReadinessReport | null,
  member: PageMember,
): MutationGate {
  if (context.capabilities?.member_mutations?.state !== "ready") {
    return { allowed: false, reason: "成员变更能力尚未接入" };
  }
  if (!hasManagePermission(context)) {
    return { allowed: false, reason: "当前身份没有管理权限" };
  }
  if (readiness?.status !== "ready" || readiness.mutations_safe !== true) {
    return { allowed: false, reason: "数据库尚未就绪，仅支持只读" };
  }
  if (typeof member.revision !== "number") {
    return { allowed: false, reason: "成员版本号未返回，仅支持只读" };
  }
  return { allowed: true, reason: "" };
}

function useEnterpriseReadiness(
  scope: EnterpriseScope,
  enabled: boolean,
): EnterpriseReadinessReport | null {
  const [readiness, setReadiness] = useState<EnterpriseReadinessReport | null>(null);

  useEffect(() => {
    let active = true;
    if (!enabled || !scope.actorToken.trim()) {
      setReadiness(null);
      return () => {
        active = false;
      };
    }

    setReadiness(null);
    void request<EnterpriseReadinessReport>("/api/enterprise/readiness", {
      method: "GET",
      headers: authenticatedHeaders(scope),
    })
      .then((report) => {
        if (active) setReadiness(report);
      })
      .catch(() => {
        if (active) setReadiness(null);
      });

    return () => {
      active = false;
    };
  }, [enabled, scope]);

  return readiness;
}

function auditError(error: unknown): SurfaceError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return {
        title: "管理审计身份已失效",
        description: "重新连接企业身份后再读取管理审计",
        canRetry: false,
      };
    }
    if (error.status === 403) {
      return {
        title: "没有读取管理审计的权限",
        description: "当前身份未获得企业管理审计权限",
        canRetry: false,
      };
    }
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout") {
      return {
        title: "管理审计接口尚未挂载/企业服务暂不可用",
        description: "服务没有返回可安全展示的企业管理事件",
        canRetry: true,
      };
    }
  }
  return {
    title: "管理审计读取失败",
    description: "服务没有返回可安全展示的企业管理事件",
    canRetry: true,
  };
}

function memberMutationError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return "成员信息已发生变化，请刷新后重试";
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout") {
      return "成员接口尚未挂载/企业服务暂不可用";
    }
    if (error.status === 401) return "身份已失效，无法提交成员变更";
    if (error.status === 403) return "当前身份没有管理权限";
  }
  return "成员变更提交失败，请稍后重试";
}

function mergeAuditEvents(current: AuditEvent[], next: AuditEvent[]): AuditEvent[] {
  const seen = new Set<string>();
  const merged: AuditEvent[] = [];
  for (const event of [...current, ...next]) {
    const key = event.id || String(event.sequence);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(event);
  }
  return merged;
}

function useEnterpriseAudit(scope: EnterpriseScope, enabled: boolean) {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextBeforeSequence, setNextBeforeSequence] = useState<number | null>(null);
  const [error, setError] = useState<SurfaceError | null>(null);
  const initialAuditLoadedRef = useRef(false);

  const load = useCallback(
    async (beforeSequence?: number) => {
      if (!enabled || !scope.actorToken.trim()) return;
      const query = new URLSearchParams({ limit: "50" });
      if (beforeSequence !== undefined) query.set("before_sequence", String(beforeSequence));
      if (beforeSequence === undefined) setLoading(true);
      else setLoadingMore(true);
      setError(null);
      try {
        const response = await request<AuditListResponse>(
          `/api/enterprise/audit-events?${query.toString()}`,
          {
            method: "GET",
            headers: authenticatedHeaders(scope),
          },
        );
        setItems((current) =>
          beforeSequence === undefined ? response.items : mergeAuditEvents(current, response.items),
        );
        setNextBeforeSequence(response.next_before_sequence ?? null);
      } catch (reason) {
        setError(auditError(reason));
        if (beforeSequence === undefined) {
          setItems([]);
          setNextBeforeSequence(null);
        }
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [enabled, scope],
  );

  useEffect(() => {
    if (!enabled) {
      initialAuditLoadedRef.current = false;
      setItems([]);
      setNextBeforeSequence(null);
      setError(null);
      setLoading(false);
      setLoadingMore(false);
      return;
    }
    if (initialAuditLoadedRef.current) return;
    initialAuditLoadedRef.current = true;
    void load();
  }, [enabled, load]);

  return {
    items,
    loading,
    loadingMore,
    nextBeforeSequence,
    error,
    reload: () => load(),
    loadMore: () => (nextBeforeSequence === null ? Promise.resolve() : load(nextBeforeSequence)),
  };
}

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 720px)").matches,
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 720px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  return mobile;
}

interface SurfaceHeaderProps {
  eyebrow: string;
  title: string;
  description: string;
  meta?: string;
}

function SurfaceHeader({ eyebrow, title, description, meta }: SurfaceHeaderProps) {
  return (
    <header className="enterprise-admin-surface__header">
      <div>
        <span className="enterprise-admin-surface__eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {meta ? <span className="enterprise-admin-surface__meta">{meta}</span> : null}
    </header>
  );
}

function EnterpriseSummary({ context }: { context: EnterpriseContext }) {
  const { tenant, actor } = context;
  return (
    <section
      className="enterprise-admin-surface enterprise-admin-summary"
      role="region"
      aria-label="企业摘要"
    >
      <SurfaceHeader
        eyebrow="Enterprise context"
        title="企业摘要"
        description="当前租户、身份与容量事实来自企业上下文接口。"
        meta="权威只读"
      />
      <div className="enterprise-admin-summary__identity">
        <div className="enterprise-admin-summary__monogram" aria-hidden="true">
          {tenant.name.trim().slice(0, 1) || "企"}
        </div>
        <div>
          <strong>{tenant.name}</strong>
          <span>{tenant.id}</span>
        </div>
        <Tag theme={tenant.status === "active" ? "success" : "warning"} variant="light-outline">
          {tenant.status === "active" ? "企业空间启用" : tenant.status}
        </Tag>
      </div>
      <dl className="enterprise-admin-fact-grid">
        <div>
          <dt>企业套餐</dt>
          <dd>{tenant.plan || "未标注"}</dd>
        </div>
        <div>
          <dt>当前身份</dt>
          <dd>{actor.name}</dd>
          <small>{actor.email}</small>
        </div>
        <div>
          <dt>租户角色</dt>
          <dd>{roleLabel(actor.role)}</dd>
        </div>
        <div>
          <dt>成员总数</dt>
          <dd>{numberLabel(context.member_count)}</dd>
          <small>企业上下文总量</small>
        </div>
        <div>
          <dt>知识库总数</dt>
          <dd>{numberLabel(context.dataset_count)}</dd>
          <small>企业上下文总量</small>
        </div>
        <div>
          <dt>知识文档</dt>
          <dd>{numberLabel(tenant.doc_count)}</dd>
          <small>配额 {numberLabel(tenant.quota_documents)}</small>
        </div>
        <div>
          <dt>目录片段</dt>
          <dd>{numberLabel(tenant.chunk_count)}</dd>
          <small>配额 {numberLabel(tenant.quota_chunks)}</small>
        </div>
        <div>
          <dt>有效权限</dt>
          <dd>{context.effective_permissions.length}</dd>
          <small>由服务端角色策略计算</small>
        </div>
      </dl>
    </section>
  );
}

function MemberMutationDialog({
  target,
  visible,
  saving,
  error,
  onClose,
  onSubmit,
}: {
  target: MemberMutationTarget | null;
  visible: boolean;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (payload: MemberMutationPayload) => Promise<boolean>;
}) {
  const [role, setRole] = useState("member");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");

  useEffect(() => {
    if (!target || !visible) return;
    setRole(target.member.role);
    setReason("");
    setValidation("");
  }, [target, visible]);

  if (!target) return null;
  const title =
    target.action === "role"
      ? "修改成员角色"
      : target.action === "suspend"
        ? "暂停成员"
        : "恢复成员";
  const revision = target.member.revision;

  const submit = async () => {
    if (typeof revision !== "number") {
      setValidation("成员版本号未返回，无法安全提交变更。");
      return;
    }
    if (target.action === "role" && !role.trim()) {
      setValidation("请选择成员角色。");
      return;
    }
    if (!reason.trim()) {
      setValidation("变更原因不能为空。");
      return;
    }
    const payload: MemberMutationPayload = {
      expected_revision: revision,
      reason: reason.trim(),
    };
    if (target.action === "role") payload.role = role.trim();
    if (target.action === "suspend") payload.status = "suspended";
    if (target.action === "restore") payload.status = "active";
    const succeeded = await onSubmit(payload);
    if (succeeded) onClose();
  };

  return (
    <Dialog
      visible={visible}
      header={title}
      width={520}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{ content: "提交成员变更", theme: "primary" }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": title } as Record<
        string,
        unknown
      >)}
    >
      {error ? <Alert theme="error" title="成员变更未提交" message={error} /> : null}
      {validation ? <Alert theme="warning" title="请补齐变更信息" message={validation} /> : null}
      <div className="enterprise-admin-form-grid">
        <div>
          <strong>{target.member.name}</strong>
          <span>{target.member.email}</span>
        </div>
        <label>
          <span>新角色</span>
          <select
            className="enterprise-admin-native-select"
            aria-label="新角色"
            value={role}
            disabled={saving}
            onChange={(event) => {
              setRole(event.currentTarget.value);
              setValidation("");
            }}
          >
            {MEMBER_ROLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>成员版本</span>
          <input
            className="enterprise-admin-revision-input"
            aria-label="expected_revision"
            value={String(revision ?? "")}
            readOnly
            disabled={saving}
          />
        </label>
        <label>
          <span>变更原因</span>
          <Textarea
            aria-label="变更原因"
            value={reason}
            disabled={saving}
            placeholder="说明本次成员变更的业务原因"
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </label>
      </div>
    </Dialog>
  );
}

function MemberDetailDrawer({
  member,
  onClose,
}: {
  member: PageMember | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      visible={Boolean(member)}
      header="成员详情"
      size="420px"
      footer={null}
      destroyOnClose
      onClose={onClose}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "成员详情" } as Record<
        string,
        unknown
      >)}
    >
      {member ? (
        <dl className="enterprise-admin-fact-grid">
          <div>
            <dt>成员</dt>
            <dd>{member.name}</dd>
          </div>
          <div>
            <dt>账号 ID</dt>
            <dd>{member.account_id}</dd>
          </div>
          <div>
            <dt>邮箱</dt>
            <dd>{member.email}</dd>
          </div>
          <div>
            <dt>租户角色</dt>
            <dd>{roleLabel(member.role)}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>{statusLabel(member.status)}</dd>
          </div>
          <div>
            <dt>revision</dt>
            <dd>{member.revision ?? "未提供"}</dd>
          </div>
        </dl>
      ) : null}
    </Drawer>
  );
}

function MemberDirectory({
  members,
  totalCount,
  loading,
  loadingMore,
  error,
  loadMoreError,
  onRetry,
  onLoadMore,
  context,
  scope,
  readiness,
}: {
  members: import("../enterprise-admin/model").EnterpriseMemberListResponse | null;
  totalCount: number;
  loading: boolean;
  loadingMore: boolean;
  error: EnterpriseResourceError | null;
  loadMoreError: EnterpriseResourceError | null;
  onRetry: () => void;
  onLoadMore: () => Promise<void>;
  context: EnterpriseContext;
  scope: EnterpriseScope;
  readiness: EnterpriseReadinessReport | null;
}) {
  const [memberOverrides, setMemberOverrides] = useState<Record<string, PageMember>>({});
  const [mutationTarget, setMutationTarget] = useState<MemberMutationTarget | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutationSaving, setMutationSaving] = useState(false);
  const [mutationSuccess, setMutationSuccess] = useState(false);
  const [detailMember, setDetailMember] = useState<PageMember | null>(null);
  const items = useMemo<PageMember[]>(
    () =>
      (members?.items ?? []).map((member) =>
        memberOverrides[String(member.membership_id)]
          ? { ...member, ...memberOverrides[String(member.membership_id)] }
          : (member as PageMember),
      ),
    [memberOverrides, members],
  );
  const hasNextPage = members?.next_before_id != null;
  const displayedTotal = totalCount;
  const roleTarget = mutationTarget?.action === "role" ? mutationTarget : null;
  const roleApprovalGate = useMemberRoleApprovalGate({
    visible: roleTarget !== null,
    scope,
    member: roleTarget?.member ?? null,
  });

  const submitMutation = useCallback(
    async (payload: MemberMutationPayload): Promise<boolean> => {
      if (!mutationTarget) return false;
      const gate = mutationGate(context, readiness, mutationTarget.member);
      if (!gate.allowed) {
        setMutationError(gate.reason);
        return false;
      }

      if (mutationTarget.action === "role" && roleApprovalGate.mode === "approval") {
        setMutationSaving(true);
        setMutationError(null);
        try {
          const request = await roleApprovalGate.submitApproval(payload.role ?? "", payload.reason);
          return request !== null;
        } finally {
          setMutationSaving(false);
        }
      }

      if (mutationTarget.action === "role" && roleApprovalGate.mode !== "direct") {
        setMutationError(roleApprovalGate.error ?? "正在核对审批规则，暂不能直接修改角色");
        return false;
      }

      setMutationSaving(true);
      setMutationError(null);
      try {
        const response =
          mutationTarget.action === "role"
            ? await updateEnterpriseMemberRole(
                scope,
                mutationTarget.member.account_id,
                payload.role as "owner" | "admin" | "editor" | "member",
                payload.reason,
                payload.expected_revision,
              )
            : mutationTarget.action === "suspend"
              ? await suspendEnterpriseMember(
                  scope,
                  mutationTarget.member.account_id,
                  payload.reason,
                  payload.expected_revision,
                )
              : await restoreEnterpriseMember(
                  scope,
                  mutationTarget.member.account_id,
                  payload.reason,
                  payload.expected_revision,
                );
        const responseMember = response.member as Partial<PageMember>;
        const nextMember: PageMember = {
          ...mutationTarget.member,
          ...responseMember,
          role:
            typeof responseMember.role === "string"
              ? responseMember.role
              : (payload.role ?? mutationTarget.member.role),
          status:
            typeof responseMember.status === "string"
              ? responseMember.status
              : (payload.status ?? mutationTarget.member.status),
          revision:
            typeof responseMember.revision === "number"
              ? responseMember.revision
              : mutationTarget.member.revision === undefined ||
                  mutationTarget.member.revision === null
                ? mutationTarget.member.revision
                : mutationTarget.member.revision + 1,
        };
        setMemberOverrides((current) => ({
          ...current,
          [String(nextMember.membership_id)]: nextMember,
        }));
        setMutationTarget(null);
        setMutationSuccess(true);
        return true;
      } catch (reason) {
        if (mutationTarget.action === "role" && roleApprovalGate.applyApprovalRequired(reason)) {
          setMutationError(null);
          return false;
        }
        setMutationError(memberMutationError(reason));
        return false;
      } finally {
        setMutationSaving(false);
      }
    },
    [context, mutationTarget, readiness, roleApprovalGate, scope],
  );

  const columns = useMemo<PrimaryTableCol<PageMember>[]>(
    () => [
      {
        colKey: "member",
        title: "成员",
        width: 250,
        cell: ({ row }) => (
          <div className="enterprise-admin-member">
            <span className="enterprise-admin-member__avatar" aria-hidden="true">
              {row.name.trim().slice(0, 1) || "成"}
            </span>
            <span className="enterprise-admin-member__identity">
              <strong>{row.name}</strong>
              <small>{row.account_id}</small>
            </span>
            <Button
              variant="text"
              size="small"
              aria-label={`查看成员详情 ${row.name}`}
              className="enterprise-admin-member__detail"
              onClick={() => setDetailMember(row)}
            >
              详情
            </Button>
          </div>
        ),
      },
      { colKey: "email", title: "邮箱", width: 230 },
      {
        colKey: "role",
        title: "租户角色",
        width: 120,
        cell: ({ row }) => <Tag variant="light-outline">{roleLabel(row.role)}</Tag>,
      },
      {
        colKey: "status",
        title: "状态",
        width: 150,
        cell: ({ row }) => (
          <span className="enterprise-admin-status-cell">
            <Tag theme={isSuspended(row.status) ? "warning" : "success"} variant="light-outline">
              {statusLabel(row.status)}
            </Tag>
            <small>{row.status ?? "未返回"}</small>
          </span>
        ),
      },
      {
        colKey: "revision",
        title: "revision",
        width: 100,
        cell: ({ row }) => row.revision ?? "未提供",
      },
      {
        colKey: "joined_at",
        title: "加入时间",
        width: 150,
        cell: ({ row }) => dateLabel(row.joined_at),
      },
      {
        colKey: "actions",
        title: "操作",
        width: 300,
        cell: ({ row }) => {
          const gate = mutationGate(context, readiness, row);
          const disabledReason = gate.allowed ? undefined : gate.reason;
          const actionDisabled = !gate.allowed || mutationSaving;
          const stateDisabledReason = isSuspended(row.status) ? "成员当前已暂停" : "成员当前未暂停";
          const button = (action: MemberMutationAction, label: string, disabled = false) => (
            <Button
              tag="button"
              variant="text"
              size="small"
              disabled={actionDisabled || disabled}
              title={disabledReason ?? (disabled ? stateDisabledReason : undefined)}
              aria-label={`${label}${disabledReason ? `（${disabledReason}）` : disabled ? `（${stateDisabledReason}）` : ""}`}
              onClick={() => {
                if (!gate.allowed || disabled) return;
                setMutationError(null);
                setMutationSuccess(false);
                setMutationTarget({ member: row, action });
              }}
            >
              {label}
            </Button>
          );
          return (
            <div className="enterprise-admin-row-actions">
              {button("role", "修改角色")}
              {button("suspend", "暂停成员", isSuspended(row.status))}
              {button("restore", "恢复成员", !isSuspended(row.status))}
            </div>
          );
        },
      },
    ],
    [context, mutationSaving, readiness],
  );

  return (
    <section
      className="enterprise-admin-surface enterprise-admin-members"
      role="region"
      aria-label="成员目录"
    >
      <SurfaceHeader
        eyebrow="Member directory"
        title="成员目录"
        description="只展示受认证接口返回的真实租户成员，不生成示例账号。"
        meta={`${numberLabel(displayedTotal)} 位成员`}
      />
      {context.capabilities?.member_mutations?.state !== "ready" ? (
        <Alert
          theme="warning"
          title="成员变更能力尚未接入"
          message={context.capabilities?.member_mutations?.reason || "当前成员目录仅支持只读"}
        />
      ) : null}
      {mutationSuccess ? (
        <Alert
          theme="success"
          title="成员变更已提交"
          message="成员目录已保留当前结果；如需确认最新版本，可刷新事实。"
        />
      ) : null}
      {error ? <EnterpriseResourceErrorState error={error} onRetry={onRetry} /> : null}
      {!error && loading && !members ? (
        <div className="enterprise-admin-empty" role="status" aria-live="polite">
          <UsergroupIcon aria-hidden="true" />
          <strong>正在读取成员目录</strong>
          <span>正在从企业目录加载真实成员关系</span>
        </div>
      ) : null}
      {!error && !loading && members?.items.length === 0 ? (
        <div className="enterprise-admin-empty" role="status">
          <UsergroupIcon aria-hidden="true" />
          <strong>尚未添加企业成员</strong>
          <span>成员接口已成功返回空列表</span>
        </div>
      ) : null}
      {!error && members && items.length > 0 ? (
        <>
          <div
            className="enterprise-admin-table enterprise-admin-member-table"
            tabIndex={0}
            aria-label="企业成员表格，可横向滚动"
          >
            <PrimaryTable
              rowKey="membership_id"
              data={items}
              columns={columns}
              tableLayout="fixed"
              bordered={false}
              hover
            />
          </div>
          {hasNextPage ? (
            <div className="enterprise-admin-member-actions">
              {loadMoreError ? (
                <div className="enterprise-admin-member-actions__error" role="alert">
                  <strong>成员加载失败</strong>
                  <span>{loadMoreError.description}</span>
                  <Button
                    variant="outline"
                    size="small"
                    onClick={() => void onLoadMore()}
                    aria-label="重试加载成员"
                  >
                    重试加载成员
                  </Button>
                </div>
              ) : null}
              <div className="enterprise-admin-member-actions__footer">
                <span>
                  已展示 {numberLabel(items.length)} / {numberLabel(displayedTotal)} 位成员
                </span>
                <Button
                  variant="outline"
                  onClick={() => {
                    if (!loadingMore) void onLoadMore();
                  }}
                  aria-disabled={loadingMore ? "true" : undefined}
                  aria-busy={loadingMore}
                  aria-label={loadingMore ? "正在加载更多成员" : "加载更多成员"}
                >
                  {loadingMore ? "正在加载更多成员" : "加载更多成员"}
                </Button>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
      {roleTarget ? (
        <MemberRoleApprovalDialog
          member={roleTarget.member}
          visible
          saving={mutationSaving}
          error={mutationError ?? roleApprovalGate.error}
          mode={roleApprovalGate.mode}
          policy={roleApprovalGate.policy}
          request={roleApprovalGate.request}
          submitting={roleApprovalGate.submitting}
          onClose={() => {
            if (!mutationSaving && !roleApprovalGate.submitting) {
              setMutationTarget(null);
              setMutationError(null);
            }
          }}
          onSubmit={(payload) =>
            submitMutation({
              expected_revision:
                typeof roleTarget.member.revision === "number" ? roleTarget.member.revision : 0,
              reason: payload.reason,
              role: payload.role,
            })
          }
          onNavigateToApprovalCenter={navigateToApprovalCenter}
        />
      ) : null}
      <MemberMutationDialog
        target={mutationTarget?.action === "role" ? null : mutationTarget}
        visible={mutationTarget !== null && mutationTarget.action !== "role"}
        saving={mutationSaving}
        error={mutationError}
        onClose={() => {
          if (!mutationSaving) {
            setMutationTarget(null);
            setMutationError(null);
          }
        }}
        onSubmit={submitMutation}
      />
      <MemberDetailDrawer member={detailMember} onClose={() => setDetailMember(null)} />
    </section>
  );
}
function auditActionLabel(event: AuditEvent): string {
  return event.action || "未标注事件";
}

function auditResourceLabel(event: AuditEvent): string {
  return [event.resource_type, event.resource_id].filter(Boolean).join("/") || "未标注资源";
}

function AuditDetailDrawer({ event, onClose }: { event: AuditEvent | null; onClose: () => void }) {
  return (
    <Drawer
      visible={Boolean(event)}
      header="管理审计事件详情"
      size="560px"
      footer={null}
      destroyOnClose
      onClose={onClose}
    >
      {event ? (
        <section
          className="enterprise-admin-audit-detail"
          role="dialog"
          aria-modal="true"
          aria-label="管理审计事件详情"
        >
          <dl className="enterprise-admin-audit-detail__grid">
            <div>
              <dt>事件 ID</dt>
              <dd>{event.id}</dd>
            </div>
            <div>
              <dt>sequence</dt>
              <dd>{event.sequence}</dd>
            </div>
            <div>
              <dt>操作</dt>
              <dd>{auditActionLabel(event)}</dd>
            </div>
            <div>
              <dt>操作者</dt>
              <dd>{event.actor_id}</dd>
            </div>
            <div>
              <dt>资源</dt>
              <dd>{auditResourceLabel(event)}</dd>
            </div>
            <div>
              <dt>request_id</dt>
              <dd>{event.request_id || "未返回"}</dd>
            </div>
            <div>
              <dt>发生时间</dt>
              <dd>{dateLabel(event.occurred_at)}</dd>
            </div>
            <div className="enterprise-admin-audit-detail__snapshot">
              <dt>前后快照</dt>
              <dd>
                <pre>
                  {JSON.stringify(
                    { before: event.before_snapshot ?? null, after: event.after_snapshot ?? null },
                    null,
                    2,
                  )}
                </pre>
              </dd>
            </div>
          </dl>
        </section>
      ) : null}
    </Drawer>
  );
}

function AuditSurface({ context, scope }: { context: EnterpriseContext; scope: EnterpriseScope }) {
  const capability = context.capabilities?.tenant_audit;
  const enabled = capability?.state === "ready";
  const audit = useEnterpriseAudit(scope, enabled);
  const mobile = useIsMobile();
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const columns = useMemo<PrimaryTableCol<AuditEvent>[]>(
    () => [
      {
        colKey: "occurred_at",
        title: "发生时间",
        width: 140,
        cell: ({ row }) => dateLabel(row.occurred_at),
      },
      { colKey: "action", title: "操作", width: 220, cell: ({ row }) => auditActionLabel(row) },
      { colKey: "actor_id", title: "操作者", width: 150 },
      { colKey: "resource", title: "资源", width: 190, cell: ({ row }) => auditResourceLabel(row) },
      {
        colKey: "details",
        title: "详情",
        width: 100,
        cell: ({ row }) => (
          <Button
            variant="text"
            size="small"
            aria-label={`查看审计事件详情 ${row.id}`}
            onClick={() => setSelectedEvent(row)}
          >
            查看详情
          </Button>
        ),
      },
    ],
    [],
  );

  return (
    <section
      className="enterprise-admin-surface enterprise-admin-audit"
      role="region"
      aria-label="管理审计"
    >
      <SurfaceHeader
        eyebrow="Management audit"
        title="管理审计"
        description="只展示企业管理审计接口返回的真实事件，不用示例日志填充空白。"
        meta={enabled ? `${numberLabel(audit.items.length)} 条已加载` : "能力未接入"}
      />
      {!enabled ? (
        <div className="enterprise-admin-empty" role="status">
          <BrowseIcon aria-hidden="true" />
          <strong>管理审计接口尚未挂载</strong>
          <span>{capability?.reason || "企业管理审计能力尚未接入"}</span>
        </div>
      ) : null}
      {enabled && audit.error ? (
        <PageState
          status="error"
          compact
          title={audit.error.title}
          description={audit.error.description}
          extra={
            audit.error.canRetry ? (
              <Button variant="outline" icon={<RefreshIcon />} onClick={() => void audit.reload()}>
                重新读取审计
              </Button>
            ) : undefined
          }
        />
      ) : null}
      {enabled && !audit.error && audit.loading && !audit.items.length ? (
        <div className="enterprise-admin-empty" role="status" aria-live="polite">
          <BrowseIcon aria-hidden="true" />
          <strong>正在读取管理审计</strong>
          <span>正在从企业管理审计接口加载真实事件</span>
        </div>
      ) : null}
      {enabled && !audit.error && !audit.loading && audit.items.length === 0 ? (
        <div className="enterprise-admin-empty" role="status">
          <BrowseIcon aria-hidden="true" />
          <strong>暂无管理审计事件</strong>
          <span>真实接口已返回空列表</span>
        </div>
      ) : null}
      {enabled && !audit.error && audit.items.length > 0 && mobile ? (
        <div data-testid="enterprise-audit-mobile-list" className="enterprise-admin-member-actions">
          {audit.items.map((event) => (
            <article key={event.id}>
              <strong>{auditActionLabel(event)}</strong>
              <span>
                {event.actor_id} · {auditResourceLabel(event)}
              </span>
              <small>
                {dateLabel(event.occurred_at)} · #{event.sequence}
              </small>
              <Button
                variant="text"
                size="small"
                aria-label={`查看审计事件详情 ${event.id}`}
                onClick={() => setSelectedEvent(event)}
              >
                查看详情
              </Button>
            </article>
          ))}
        </div>
      ) : null}
      {enabled && !audit.error && audit.items.length > 0 && !mobile ? (
        <div
          data-testid="enterprise-audit-desktop-table"
          className="enterprise-admin-table"
          tabIndex={0}
          aria-label="管理审计事件表格，可横向滚动"
        >
          <PrimaryTable
            rowKey="id"
            data={audit.items}
            columns={columns}
            tableLayout="fixed"
            bordered={false}
            hover
          />
        </div>
      ) : null}
      {enabled && !audit.error && audit.nextBeforeSequence !== null ? (
        <div className="enterprise-admin-member-actions">
          <div className="enterprise-admin-member-actions__footer">
            <span>按 sequence 游标继续读取</span>
            <Button
              variant="outline"
              onClick={() => {
                if (!audit.loadingMore) void audit.loadMore();
              }}
              aria-busy={audit.loadingMore}
              aria-disabled={audit.loadingMore ? "true" : undefined}
              aria-label={audit.loadingMore ? "正在加载更多审计事件" : "加载更多审计事件"}
            >
              {audit.loadingMore ? "正在加载更多审计事件" : "加载更多审计事件"}
            </Button>
          </div>
        </div>
      ) : null}
      <AuditDetailDrawer event={selectedEvent} onClose={() => setSelectedEvent(null)} />
    </section>
  );
}
function PermissionModel({
  context,
  selectedDatasetId,
  access,
  error,
  onRetry,
}: {
  context: EnterpriseContext;
  selectedDatasetId?: string;
  access: ReturnType<typeof useEnterpriseAdminWorkspace>["access"];
  error: EnterpriseResourceError | null;
  onRetry: () => void;
}) {
  const roles = Object.keys(context.role_permissions);
  const permissions = Array.from(new Set(Object.values(context.role_permissions).flat())).sort();
  type PermissionRow = { key: string; permission: string } & Record<string, string | boolean>;
  const rows: PermissionRow[] = permissions.map((permission) => {
    const row: PermissionRow = { key: permission, permission };
    for (const role of roles)
      row[role] = context.role_permissions[role]?.includes(permission) ?? false;
    return row;
  });
  const columns: PrimaryTableCol<PermissionRow>[] = [
    { colKey: "permission", title: "服务端权限", width: 210 },
    ...roles.map<PrimaryTableCol<PermissionRow>>((role) => ({
      colKey: role,
      title: roleLabel(role),
      width: 96,
      align: "center",
      cell: ({ row }) =>
        row[role] === true ? (
          <span
            className="enterprise-admin-permission-check"
            aria-label={`${roleLabel(role)} 已拥有`}
          >
            <CheckIcon />
          </span>
        ) : (
          <span
            className="enterprise-admin-permission-empty"
            aria-label={`${roleLabel(role)} 未拥有`}
          >
            —
          </span>
        ),
    })),
  ];
  const hasSelectedDataset = Boolean(selectedDatasetId?.trim());

  return (
    <section
      className="enterprise-admin-surface enterprise-admin-permissions"
      role="region"
      aria-label="权限模型"
    >
      <SurfaceHeader
        eyebrow="Access evidence"
        title="权限模型"
        description="权限矩阵完全来自 enterprise context，不在浏览器维护第二套角色策略。"
        meta="租户固定角色"
      />
      {error ? <EnterpriseResourceErrorState error={error} onRetry={onRetry} /> : null}
      {!error && !hasSelectedDataset ? (
        <div className="enterprise-admin-empty is-compact" role="status">
          <SecuredIcon aria-hidden="true" />
          <strong>未选择知识库</strong>
          <span>选择知识库后读取当前访问策略</span>
        </div>
      ) : null}
      {!error && hasSelectedDataset && access ? (
        <>
          <dl className="enterprise-admin-access-strip">
            <div>
              <dt>执行模式</dt>
              <dd>
                {access.enforcement_mode === "tenant_role"
                  ? "租户固定角色"
                  : access.enforcement_mode}
              </dd>
            </div>
            <div>
              <dt>知识库可见性</dt>
              <dd>{access.visibility}</dd>
            </div>
            <div>
              <dt>知识库负责人</dt>
              <dd>{access.owner_id || "未设置"}</dd>
            </div>
            <div>
              <dt>当前角色</dt>
              <dd>{roleLabel(access.actor_role)}</dd>
            </div>
          </dl>
          {access.warnings.length ? (
            <Alert
              className="enterprise-admin-access-warning"
              theme="warning"
              title="访问控制边界"
              message={access.warnings.join("；")}
            />
          ) : null}
        </>
      ) : null}
      <div
        className="enterprise-admin-table enterprise-admin-permission-table"
        role="region"
        aria-label="服务端角色权限矩阵"
        tabIndex={0}
      >
        {permissions.length ? (
          <PrimaryTable
            rowKey="key"
            data={rows}
            columns={columns}
            tableLayout="fixed"
            bordered
            hover={false}
          />
        ) : (
          <div className="enterprise-admin-empty is-compact" role="status">
            <SecuredIcon aria-hidden="true" />
            <strong>服务端尚未返回角色权限矩阵</strong>
            <span>页面不会使用前端默认值补齐权限。</span>
          </div>
        )}
      </div>
    </section>
  );
}
function CapabilityWorkspace({ context }: { context: EnterpriseContext }) {
  const capabilities = { ...CAPABILITY_FALLBACKS, ...context.capabilities };
  const ordered = Object.entries(capabilities).sort(([left], [right]) => left.localeCompare(right));
  return (
    <section
      className="enterprise-admin-surface enterprise-admin-capabilities"
      role="region"
      aria-label="能力状态"
    >
      <SurfaceHeader
        eyebrow="Capability readiness"
        title="能力状态"
        description="区分已接入、能力受限与尚未接入，避免把缺失能力展示成 0 条数据。"
      />
      <div className="enterprise-admin-capability-list">
        {ordered.map(([key, capability]) => (
          <CapabilityState key={key} capability={capability} />
        ))}
      </div>
      <Alert
        className="enterprise-admin-audit-note"
        theme="info"
        icon={<BrowseIcon />}
        title="知识库治理审计已具备后端能力"
        message="本工作面暂不伪造事件；独立审计页接入后将读取正式审计接口"
      />
    </section>
  );
}

function PageFailure({
  status,
  reload,
}: {
  status: ReturnType<typeof useEnterpriseAdminWorkspace>["status"];
  reload: () => Promise<void>;
}) {
  if (status === "identity-missing") {
    return (
      <>
        <AuthorityBanner
          tone="warning"
          badgeLabel="需要身份"
          title="身份未连接"
          description="连接企业身份后才能读取成员、角色和权限事实"
        />
        <section
          className="enterprise-admin-surface enterprise-admin-capabilities"
          role="region"
          aria-label="企业能力接入范围"
        >
          <SurfaceHeader
            eyebrow="Enterprise capability map"
            title="企业能力接入范围"
            description="先展示可接入边界，不用虚构组织、成员或授权数量填充空白区域。"
            meta="未读取敏感数据"
          />
          <div className="enterprise-admin-capability-list">
            {IDENTITY_MISSING_CAPABILITIES.map((capability) => (
              <CapabilityState key={capability.label} capability={capability} />
            ))}
          </div>
          <Alert
            className="enterprise-admin-audit-note"
            theme="info"
            icon={<BrowseIcon />}
            title="身份接入后启用真实工作面"
            message="成员目录、服务端角色矩阵、知识库访问摘要和治理审计将按当前租户权限加载。"
          />
        </section>
      </>
    );
  }
  if (status === "loading") {
    return (
      <PageState
        status="loading"
        title="正在读取企业管理事实"
        description="成员、角色和知识库访问摘要将并行加载。"
      />
    );
  }
  const error =
    status === "unauthorized"
      ? { title: "身份已失效", description: "重新连接企业身份后再访问此工作面", canRetry: false }
      : status === "forbidden"
        ? {
            title: "没有查看企业管理数据的权限",
            description: "当前身份未获得企业目录读取权限",
            canRetry: false,
          }
        : status === "unavailable"
          ? {
              title: "企业服务暂不可用",
              description: "数据库或企业目录服务尚未就绪",
              canRetry: true,
            }
          : {
              title: "企业管理事实读取失败",
              description: "服务没有返回可安全展示的企业目录结果",
              canRetry: true,
            };
  return (
    <PageState
      status="error"
      title={error.title}
      description={error.description}
      extra={
        error.canRetry ? (
          <Button variant="outline" icon={<RefreshIcon />} onClick={() => void reload()}>
            重新读取
          </Button>
        ) : undefined
      }
    />
  );
}

function EnterpriseAdminWorkspaceCenterPage({ scope }: { scope: EnterpriseScope }) {
  const workspace = useEnterpriseAdminWorkspace(scope, { auditEnabled: false });
  return (
    <div className="enterprise-admin-page enterprise-workspace-page">
      <PageTopbar
        icon={<ControlPlatformIcon />}
        title="Enterprise Workspace Center"
        subtitle="Workspace 生命周期、成员关系与知识库绑定的企业控制面"
      />
      <div className="enterprise-admin-content">
        {workspace.status !== "ready" || !workspace.context ? (
          <PageFailure status={workspace.status} reload={workspace.reload} />
        ) : (
          <>
            <AuthorityBanner
              tone="authoritative"
              badgeLabel="Workspace 真账"
              title="Workspace 上下文已验证"
              description={`当前按 ${roleLabel(workspace.context.actor.role)} 身份读取 ${workspace.context.tenant.name} 的 Workspace 权威数据；Workspace Authorization rollout 已接入，Dataset ACL 仍按独立授权引擎执行，具体效果以权限页的 current / candidate / delta 证据为准。`}
            />
            <EnterpriseWorkspaceCenter scope={scope} context={workspace.context} />
          </>
        )}
      </div>
    </div>
  );
}

function EnterpriseAdminApprovalPage({ scope }: { scope: EnterpriseScope }) {
  const workspace = useEnterpriseAdminWorkspace(scope, { auditEnabled: false });
  return (
    <div className="enterprise-admin-page">
      <PageTopbar
        icon={<SecuredIcon />}
        title="企业审批中心"
        subtitle="高风险 KnowledgeOps 变更的审批申请、规则与执行授权边界"
      />
      <div className="enterprise-admin-content">
        {workspace.status !== "ready" || !workspace.context ? (
          <PageFailure status={workspace.status} reload={workspace.reload} />
        ) : (
          <>
            <AuthorityBanner
              tone="authoritative"
              badgeLabel="企业真账"
              title="审批上下文已验证"
              description={`当前按 ${roleLabel(workspace.context.actor.role)} 身份读取 ${workspace.context.tenant.name} 的审批事实；审批通过不会自动执行下游变更。`}
            />
            <EnterpriseApprovalCenter scope={scope} context={workspace.context} />
          </>
        )}
      </div>
    </div>
  );
}
function EnterpriseAdminWorkspacePage({ scope }: { scope: EnterpriseScope }) {
  const workspace = useEnterpriseAdminWorkspace(scope, { auditEnabled: false });
  const [accessOverride, setAccessOverride] = useState<PersistentDatasetAccessSummary | null>(null);
  const displayedAccess = accessOverride ?? workspace.access;
  const reloadAccess = useCallback(async () => {
    if (!scope.datasetId?.trim()) {
      setAccessOverride(null);
      return;
    }
    const next = await fetchDatasetAccessSummary(scope);
    setAccessOverride(next);
  }, [scope]);
  useEffect(() => {
    setAccessOverride(null);
  }, [scope]);
  const readiness = useEnterpriseReadiness(
    scope,
    workspace.status === "ready" && Boolean(workspace.context),
  );

  return (
    <div className="enterprise-admin-page">
      <PageTopbar
        icon={<ControlPlatformIcon />}
        title="企业管理"
        subtitle="组织、成员、角色与知识库访问控制的企业工作面"
        extra={
          workspace.status === "ready" ? (
            <Button
              variant="outline"
              icon={<RefreshIcon />}
              onClick={() => void workspace.reload()}
            >
              刷新事实
            </Button>
          ) : undefined
        }
      />
      <div className="enterprise-admin-content">
        {workspace.status !== "ready" || !workspace.context ? (
          <PageFailure status={workspace.status} reload={workspace.reload} />
        ) : (
          <>
            <AuthorityBanner
              tone="authoritative"
              badgeLabel="企业真账"
              title="企业上下文已验证"
              description={`当前按 ${roleLabel(workspace.context.actor.role)} 身份读取租户目录；所有写操作仍由服务端权限与数据库就绪度控制。`}
            />{" "}
            <EnterpriseAccessGraph
              scope={scope}
              context={workspace.context}
              accessSummary={displayedAccess}
              onAccessSummaryRefresh={reloadAccess}
            />
            {workspace.context.capabilities.identity_federation ? (
              <EnterpriseIdentityCenter
                scope={scope}
                context={workspace.context}
                readiness={readiness}
              />
            ) : null}
            {workspace.context.capabilities.audit_compliance ||
            workspace.context.capabilities.enterprise_audit_compliance ? (
              <EnterpriseComplianceCenter scope={scope} context={workspace.context} />
            ) : null}
            <div className="enterprise-admin-layout">
              <div className="enterprise-admin-layout__main">
                <EnterpriseSummary context={workspace.context} />
                <MemberDirectory
                  members={workspace.members}
                  totalCount={workspace.context.member_count}
                  loading={workspace.membersLoading}
                  loadingMore={workspace.membersLoadingMore}
                  error={workspace.membersError}
                  loadMoreError={workspace.membersLoadMoreError}
                  onRetry={() => void workspace.reload()}
                  onLoadMore={workspace.loadMoreMembers}
                  context={workspace.context}
                  scope={scope}
                  readiness={readiness}
                />
                <AuditSurface context={workspace.context} scope={scope} />
              </div>
              <div className="enterprise-admin-layout__side">
                <PermissionModel
                  context={workspace.context}
                  selectedDatasetId={scope.datasetId}
                  access={displayedAccess}
                  error={workspace.accessError}
                  onRetry={() => void workspace.reload()}
                />{" "}
                <CapabilityWorkspace context={workspace.context} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function EnterpriseAdminPage() {
  const scope = useMemo(() => scopeFromStorage(), []);
  const [routeTick, setRouteTick] = useState(0);
  useEffect(() => {
    const refreshRoute = () => setRouteTick((value) => value + 1);
    window.addEventListener("hashchange", refreshRoute);
    window.addEventListener("popstate", refreshRoute);
    return () => {
      window.removeEventListener("hashchange", refreshRoute);
      window.removeEventListener("popstate", refreshRoute);
    };
  }, []);
  void routeTick;
  const [acceptToken, setAcceptToken] = useState(() =>
    typeof window === "undefined" ? null : invitationAcceptTokenFromLocation(window.location),
  );
  const [oidcCallback] = useState(() =>
    typeof window === "undefined" ? null : oidcCallbackFromLocation(window.location),
  );

  if (oidcCallback) {
    return (
      <div className="enterprise-admin-page">
        <PageTopbar
          icon={<SecuredIcon />}
          title="OIDC 登录回调"
          subtitle="验证 Authorization Code + PKCE 并建立企业签名身份"
        />
        <div className="enterprise-admin-content">
          <OidcCallbackSurface credentials={oidcCallback} />
        </div>
      </div>
    );
  }

  if (enterpriseWorkspaceRouteFromLocation(window.location)) {
    return <EnterpriseAdminWorkspaceCenterPage scope={scope} />;
  }

  if (enterpriseApprovalRouteFromLocation(window.location)) {
    return <EnterpriseAdminApprovalPage scope={scope} />;
  }

  if (acceptToken) {
    return (
      <div className="enterprise-admin-page">
        <PageTopbar
          icon={<SecuredIcon />}
          title="接受企业邀请"
          subtitle="使用签名账户确认加入企业租户"
        />
        <div className="enterprise-admin-content">
          <TenantInvitationAcceptSurface
            scope={scope}
            token={acceptToken}
            onClearToken={() => {
              clearInvitationAcceptLocation();
              setAcceptToken(null);
            }}
          />
        </div>
      </div>
    );
  }

  return <EnterpriseAdminWorkspacePage scope={scope} />;
}
