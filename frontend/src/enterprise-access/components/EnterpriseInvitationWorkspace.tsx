import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Input,
  PrimaryTable,
  Select,
  Tag,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  AddIcon,
  DeleteIcon,
  MailIcon,
  MoreIcon,
  RefreshIcon,
  SendIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import type { EnterpriseAccessCollection } from "../hooks/useEnterpriseAccessGraph";
import {
  invitationMutationGate,
  useEnterpriseInvitationMutations,
} from "../hooks/useEnterpriseInvitationMutations";
import type { EnterpriseInvitation, InvitationActor } from "../enterpriseAccessModel";
import InvitationDeliveryDialog from "./InvitationDeliveryDialog";
import TenantInvitationMutationDialog, {
  type TenantInvitationDialogMode,
  type TenantInvitationDialogPayload,
} from "./TenantInvitationMutationDialog";

const STATUS_OPTIONS = [
  { label: "全部状态", value: "all" },
  { label: "待接受", value: "pending" },
  { label: "已接受", value: "accepted" },
  { label: "已撤销", value: "revoked" },
  { label: "已过期", value: "expired" },
];

function roleLabel(role: string): string {
  return { owner: "所有者", admin: "管理员", editor: "编辑者", member: "成员" }[role] ?? role;
}

function statusLabel(status: string): string {
  return (
    { pending: "待接受", accepted: "已接受", revoked: "已撤销", expired: "已过期" }[status] ??
    status
  );
}

function statusTheme(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "accepted") return "success";
  if (status === "pending") return "warning";
  if (status === "revoked" || status === "expired") return "danger";
  return "default";
}

function dateTimeLabel(value: string | undefined): string {
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

function actorLabel(actor: InvitationActor): string {
  if (typeof actor === "string") return actor;
  return actor?.name || actor?.email || actor?.id || "未返回";
}

function useInvitationMobile(): boolean {
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

export interface EnterpriseInvitationWorkspaceProps {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  invitations: EnterpriseAccessCollection<EnterpriseInvitation>;
}

export default function EnterpriseInvitationWorkspace({
  scope,
  context,
  invitations,
}: EnterpriseInvitationWorkspaceProps) {
  const mobile = useInvitationMobile();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [dialog, setDialog] = useState<{
    mode: TenantInvitationDialogMode;
    invitation: EnterpriseInvitation | null;
  } | null>(null);
  const [drawerInvitation, setDrawerInvitation] = useState<EnterpriseInvitation | null>(null);
  const mutation = useEnterpriseInvitationMutations({ scope, context, invitations });
  const createGate = invitationMutationGate(context, scope);

  const items = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("en-US");
    return invitations.items.filter((invitation) => {
      if (status !== "all" && invitation.status !== status) return false;
      if (!normalized) return true;
      return [
        invitation.email,
        invitation.id,
        invitation.role,
        actorLabel(invitation.invited_by),
      ].some((value) => value.toLocaleLowerCase("en-US").includes(normalized));
    });
  }, [invitations.items, query, status]);

  const openDialog = useCallback(
    (mode: TenantInvitationDialogMode, invitation: EnterpriseInvitation | null = null) => {
      mutation.clear();
      setDrawerInvitation(null);
      setDialog({ mode, invitation });
    },
    [mutation],
  );

  const closeDialog = useCallback(() => setDialog(null), []);
  const refreshAfterConflict = useCallback(async () => {
    await invitations.reload();
    mutation.clear();
  }, [invitations, mutation]);
  const submit = useCallback(
    (payload: TenantInvitationDialogPayload) => {
      if (payload.mode === "create") return mutation.create(payload.input);
      if (!dialog?.invitation) return Promise.resolve(null);
      return payload.mode === "resend"
        ? mutation.resend(dialog.invitation, payload.input)
        : mutation.revoke(dialog.invitation, payload.input);
    },
    [dialog, mutation],
  );

  const actionAllowed = useCallback(
    (invitation: EnterpriseInvitation) =>
      invitation.status === "pending" &&
      invitationMutationGate(context, scope, invitation.role).allowed,
    [context, scope],
  );

  const columns = useMemo<PrimaryTableCol<EnterpriseInvitation>[]>(
    () => [
      {
        colKey: "email",
        title: "受邀成员",
        width: 250,
        cell: ({ row }) => (
          <div className="enterprise-access-entity">
            <MailIcon aria-hidden="true" />
            <div>
              <strong>{row.email}</strong>
              <span>{row.id}</span>
            </div>
          </div>
        ),
      },
      { colKey: "role", title: "预设角色", width: 105, cell: ({ row }) => roleLabel(row.role) },
      {
        colKey: "status",
        title: "状态",
        width: 105,
        cell: ({ row }) => (
          <Tag theme={statusTheme(row.status)} variant="light-outline" size="small">
            {statusLabel(row.status)}
          </Tag>
        ),
      },
      {
        colKey: "expires_at",
        title: "到期时间",
        width: 175,
        cell: ({ row }) => dateTimeLabel(row.expires_at),
      },
      {
        colKey: "revision",
        title: "revision",
        width: 105,
        cell: ({ row }) => `revision ${row.revision ?? "—"}`,
      },
      {
        colKey: "send_count",
        title: "链接轮换",
        width: 165,
        cell: ({ row }) => (
          <div className="enterprise-invitation-send-evidence">
            <strong>发送 {row.send_count ?? "—"} 次</strong>
            <span>{dateTimeLabel(row.last_sent_at)}</span>
          </div>
        ),
      },
      {
        colKey: "invited_by",
        title: "邀请人",
        width: 135,
        cell: ({ row }) => actorLabel(row.invited_by),
      },
      {
        colKey: "operation",
        title: "操作",
        width: 210,
        fixed: "right",
        cell: ({ row }) => {
          if (row.status !== "pending")
            return <span className="enterprise-access-acl-actions__muted">不可操作</span>;
          const gate = invitationMutationGate(context, scope, row.role);
          return (
            <div className="enterprise-invitation-row-actions">
              <Button
                variant="text"
                theme="primary"
                size="small"
                icon={<RefreshIcon />}
                aria-label={`重新生成${row.email}安全链接`}
                disabled={!gate.allowed}
                title={gate.allowed ? undefined : gate.reason}
                onClick={() => openDialog("resend", row)}
              >
                重新生成
              </Button>
              <Button
                variant="text"
                theme="danger"
                size="small"
                icon={<DeleteIcon />}
                aria-label={`撤销${row.email}邀请`}
                disabled={!gate.allowed}
                title={gate.allowed ? undefined : gate.reason}
                onClick={() => openDialog("revoke", row)}
              >
                撤销
              </Button>
            </div>
          );
        },
      },
    ],
    [context, openDialog, scope],
  );

  const content = (() => {
    if (invitations.status === "loading")
      return (
        <PageState
          status="loading"
          compact
          title="正在读取成员邀请"
          description="从企业目录读取真实邀请生命周期事实"
        />
      );
    if (invitations.status !== "ready" && invitations.error)
      return (
        <PageState
          status="error"
          compact
          title={invitations.error.title}
          description={invitations.error.description}
          extra={
            invitations.error.canRetry ? (
              <Button variant="outline" onClick={() => void invitations.reload()}>
                重新读取
              </Button>
            ) : undefined
          }
        />
      );
    if (!items.length)
      return (
        <PageState status="empty" compact title="暂无成员邀请" description="真实接口已返回空列表" />
      );
    if (mobile)
      return (
        <div
          className="enterprise-invitation-mobile"
          data-testid="enterprise-invitation-mobile-list"
        >
          {items.map((invitation) => (
            <article className="enterprise-invitation-card" key={invitation.id}>
              <header>
                <div>
                  <strong>{invitation.email}</strong>
                  <span>{invitation.id}</span>
                </div>
                <Tag theme={statusTheme(invitation.status)} variant="light-outline" size="small">
                  {statusLabel(invitation.status)}
                </Tag>
              </header>
              <dl>
                <div>
                  <dt>预设角色</dt>
                  <dd>{roleLabel(invitation.role)}</dd>
                </div>
                <div>
                  <dt>到期时间</dt>
                  <dd>{dateTimeLabel(invitation.expires_at)}</dd>
                </div>
                <div>
                  <dt>revision</dt>
                  <dd>{invitation.revision ?? "—"}</dd>
                </div>
                <div>
                  <dt>链接轮换</dt>
                  <dd>发送 {invitation.send_count ?? "—"} 次</dd>
                </div>
              </dl>
              <Button
                variant="outline"
                size="small"
                icon={<MoreIcon />}
                aria-label={`管理${invitation.email}邀请`}
                aria-haspopup="dialog"
                aria-expanded={drawerInvitation?.id === invitation.id}
                onClick={() => setDrawerInvitation(invitation)}
              >
                管理邀请
              </Button>
            </article>
          ))}
        </div>
      );
    return (
      <div
        className="enterprise-invitation-desktop enterprise-access-table-viewport"
        tabIndex={0}
        aria-label="成员邀请表格，可横向滚动"
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
    );
  })();

  return (
    <div
      className="enterprise-access-panel enterprise-invitation-workspace"
      aria-label="成员邀请访问事实"
    >
      <Alert
        className="enterprise-invitation-authority"
        theme="info"
        title="manual_link_required / 邮件通道未接入"
        message="邀请创建或重新生成链接后，系统只返回一次性安全链接；当前不会声称邮件已发送。"
      />
      {mutation.success ? (
        <Alert
          className="enterprise-access-mutation-success"
          theme="success"
          title={mutation.success}
          message="已更新邀请事实并请求刷新邀请列表。"
        />
      ) : null}
      <div className="enterprise-access-toolbar enterprise-invitation-toolbar">
        <label className="enterprise-access-search-label">
          <span className="enterprise-access-filter-label">筛选已加载的成员邀请</span>
          <Input
            value={query}
            placeholder="搜索邮箱、角色、邀请人或 ID"
            clearable
            onChange={(value) => setQuery(String(value))}
          />
        </label>
        <Select
          value={status}
          options={STATUS_OPTIONS}
          aria-label="筛选邀请状态"
          onChange={(value) => setStatus(String(value))}
        />
        <Button
          theme="primary"
          icon={<AddIcon />}
          disabled={!createGate.allowed}
          title={createGate.allowed ? undefined : createGate.reason}
          onClick={() => openDialog("create")}
        >
          发起邀请
        </Button>
        <span className="enterprise-access-toolbar__note">仅筛选当前已加载结果</span>
      </div>
      {content}
      {invitations.nextBeforeId !== null ? (
        <div className="enterprise-access-load-more">
          <Button
            variant="outline"
            size="small"
            loading={invitations.loadingMore}
            onClick={() => void invitations.loadMore()}
          >
            加载更多邀请
          </Button>
        </div>
      ) : null}
      <TenantInvitationMutationDialog
        visible={Boolean(dialog)}
        mode={dialog?.mode ?? "create"}
        invitation={dialog?.invitation ?? null}
        actorRole={context.actor.role}
        saving={mutation.saving}
        error={mutation.error}
        onClose={closeDialog}
        onRefresh={refreshAfterConflict}
        onRetry={mutation.retry}
        onSubmit={submit}
      />
      <InvitationDeliveryDialog delivery={mutation.delivery} onClose={mutation.clearDelivery} />
      <Drawer
        visible={Boolean(drawerInvitation)}
        header="成员邀请操作"
        size={mobile ? "100%" : "420px"}
        placement={mobile ? "bottom" : "right"}
        footer={null}
        destroyOnClose
        className="enterprise-invitation-action-drawer"
        onClose={() => setDrawerInvitation(null)}
        {...({ role: "dialog", "aria-modal": "true", "aria-label": "成员邀请操作" } as Record<
          string,
          unknown
        >)}
      >
        {drawerInvitation ? (
          <section
            className="enterprise-invitation-action-drawer__content"
            role="dialog"
            aria-modal="true"
            aria-label="成员邀请操作"
          >
            <div>
              <span>邀请对象</span>
              <strong>{drawerInvitation.email}</strong>
              <small>{drawerInvitation.id}</small>
            </div>
            {drawerInvitation.status === "pending" ? (
              <>
                <Button
                  theme="primary"
                  variant="outline"
                  icon={<SendIcon />}
                  disabled={!actionAllowed(drawerInvitation)}
                  onClick={() => openDialog("resend", drawerInvitation)}
                >
                  重新生成链接
                </Button>
                <Button
                  theme="danger"
                  variant="outline"
                  icon={<DeleteIcon />}
                  disabled={!actionAllowed(drawerInvitation)}
                  onClick={() => openDialog("revoke", drawerInvitation)}
                >
                  撤销邀请
                </Button>
              </>
            ) : (
              <Alert
                theme="info"
                title="当前邀请不可操作"
                message={`状态：${statusLabel(drawerInvitation.status)}`}
              />
            )}
          </section>
        ) : null}
      </Drawer>
    </div>
  );
}
