import { useEffect, useState } from "react";
import { Alert, Button, Dialog, Tag, Textarea } from "tdesign-react";
import { ArrowRightIcon, CheckCircleIcon, SecuredIcon } from "tdesign-icons-react";
import type { ApprovalRequestStatus } from "../../enterprise-approval/enterpriseApprovalModel";
import type {
  MemberRoleApprovalGateMode,
  MemberRoleApprovalMember,
} from "../hooks/useMemberRoleApprovalGate";
import type {
  MemberRoleApprovalPolicyFacts,
  MemberRoleApprovalRequest,
} from "../memberRoleApprovalModel";

const ROLE_OPTIONS = [
  { label: "所有者", value: "owner" },
  { label: "管理员", value: "admin" },
  { label: "编辑者", value: "editor" },
  { label: "成员", value: "member" },
];

const ROLE_LABELS: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  member: "成员",
};

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function approvalStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待审批",
    approved: "已批准",
    rejected: "已拒绝",
    cancelled: "已取消",
    expired: "已过期",
    executing: "执行中",
    executed: "已执行",
    execution_failed: "执行失败",
  };
  return labels[status] ?? status;
}

function expiresAtLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

export interface MemberRoleApprovalDialogProps {
  member: MemberRoleApprovalMember & { name: string; email: string };
  visible: boolean;
  saving: boolean;
  error: string | null;
  mode: MemberRoleApprovalGateMode;
  policy: MemberRoleApprovalPolicyFacts | null;
  request: MemberRoleApprovalRequest | null;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (payload: { role: string; reason: string }) => Promise<boolean>;
  onNavigateToApprovalCenter: () => void;
}

export default function MemberRoleApprovalDialog({
  member,
  visible,
  saving,
  error,
  mode,
  policy,
  request,
  submitting,
  onClose,
  onSubmit,
  onNavigateToApprovalCenter,
}: MemberRoleApprovalDialogProps) {
  const [role, setRole] = useState(member.role);
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");

  useEffect(() => {
    if (!visible) return;
    setRole(member.role);
    setReason("");
    setValidation("");
  }, [member.account_id, member.role, visible]);

  const policyLoading = mode === "loading";
  const submitted = mode === "submitted" && request !== null;
  const approvalMode = mode === "approval";
  const formDisabled = saving || submitting || policyLoading || submitted || mode === "error";
  const confirmLabel = submitted ? "关闭" : approvalMode ? "提交审批申请" : "提交成员变更";
  const title = "修改成员角色";

  const submit = async () => {
    if (submitted) {
      onClose();
      return;
    }
    if (policyLoading || mode === "error") return;
    if (!role.trim()) {
      setValidation("请选择成员角色。");
      return;
    }
    if (approvalMode && role.trim() === member.role.trim()) {
      setValidation("请选择与当前角色不同的新角色。");
      return;
    }
    if (!reason.trim()) {
      setValidation("变更原因不能为空。");
      return;
    }
    const succeeded = await onSubmit({ role: role.trim(), reason: reason.trim() });
    if (succeeded && !approvalMode) onClose();
  };

  return (
    <Dialog
      visible={visible}
      header={title}
      width={560}
      destroyOnClose
      closeOnEscKeydown={!saving && !submitting}
      closeOnOverlayClick={!saving && !submitting}
      confirmBtn={{
        content: confirmLabel,
        theme: "primary",
        disabled: formDisabled,
      }}
      cancelBtn={{ content: "取消", disabled: saving || submitting }}
      confirmLoading={saving || submitting}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": title } as Record<
        string,
        unknown
      >)}
    >
      {error ? <Alert theme="error" title="成员角色变更未提交" message={error} /> : null}
      {validation ? <Alert theme="warning" title="请补齐变更信息" message={validation} /> : null}
      {policyLoading ? (
        <Alert
          theme="info"
          title="正在核对角色变更审批规则"
          message="规则事实返回前不会直接修改成员角色。"
        />
      ) : null}
      {approvalMode && policy ? (
        <section
          className="enterprise-admin-role-approval-policy"
          aria-label="成员角色变更审批规则"
          role="status"
        >
          <div className="enterprise-admin-role-approval-policy__heading">
            <div>
              <span className="enterprise-admin-role-approval-policy__eyebrow">
                GOVERNANCE / ROLE CHANGE
              </span>
              <strong>{policy.name ?? "成员角色变更审批规则"}</strong>
              <small>{policy.id}</small>
            </div>
            <Tag theme="warning" variant="light-outline" size="small">
              需要审批
            </Tag>
          </div>
          <div className="enterprise-admin-role-approval-policy__facts">
            <div>
              <span>审批阈值</span>
              <strong>
                {policy.required_approvals === null ? "未返回" : `${policy.required_approvals} 人`}
              </strong>
            </div>
            <div>
              <span>有效期</span>
              <strong>
                {policy.request_expiry_minutes === null
                  ? "未返回"
                  : `${policy.request_expiry_minutes} 分钟`}
              </strong>
              {expiresAtLabel(policy.expires_at) ? (
                <small>截止 {expiresAtLabel(policy.expires_at)}</small>
              ) : null}
            </div>
            <div>
              <span>规则 revision</span>
              <strong>revision {policy.revision ?? "未返回"}</strong>
            </div>
          </div>
          <div className="enterprise-admin-role-approval-policy__boundary" role="note">
            <SecuredIcon aria-hidden="true" />
            <div>
              <strong>成员角色保持不变</strong>
              <span>提交审批申请不会提前修改成员角色；审批通过后仍需执行一次性授权。</span>
            </div>
          </div>
        </section>
      ) : null}
      {submitted && request ? (
        <section
          className="enterprise-admin-role-approval-request"
          aria-label="成员角色审批申请结果"
          role="status"
        >
          <div className="enterprise-admin-role-approval-request__heading">
            <div>
              <span>审批申请已提交</span>
              <strong>request_id</strong>
            </div>
            <Tag theme="success" variant="light-outline" size="small">
              {approvalStatusLabel(request.status)}
            </Tag>
          </div>
          <div className="enterprise-admin-role-approval-request__facts">
            <code>{request.id}</code>
            <span>当前状态：{approvalStatusLabel(request.status)}</span>
          </div>
          <Button
            className="enterprise-admin-role-approval-request__link"
            theme="primary"
            variant="outline"
            size="small"
            icon={<ArrowRightIcon />}
            onClick={onNavigateToApprovalCenter}
          >
            前往审批中心
          </Button>
        </section>
      ) : null}
      <div className="enterprise-admin-form-grid enterprise-admin-role-approval-form">
        <div className="enterprise-admin-role-approval-member">
          <span className="enterprise-admin-role-approval-member__avatar" aria-hidden="true">
            {member.name.trim().slice(0, 1) || "成"}
          </span>
          <div>
            <strong>{member.name}</strong>
            <span>{member.email}</span>
          </div>
          <Tag variant="light-outline">当前 · {roleLabel(member.role)}</Tag>
        </div>
        <label>
          <span>新角色</span>
          <select
            className="enterprise-admin-native-select"
            aria-label="新角色"
            value={role}
            disabled={formDisabled}
            onChange={(event) => {
              setRole(event.currentTarget.value);
              setValidation("");
            }}
          >
            {ROLE_OPTIONS.map((option) => (
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
            value={String(member.revision ?? "")}
            readOnly
            disabled={formDisabled}
          />
        </label>
        <label>
          <span>变更原因</span>
          <Textarea
            aria-label="变更原因"
            value={reason}
            disabled={formDisabled}
            placeholder="说明本次成员角色调整的业务原因"
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </label>
      </div>
      {submitted ? (
        <div className="enterprise-admin-role-approval-result-note" role="note">
          <CheckCircleIcon aria-hidden="true" />
          <span>
            当前目录仍显示角色“{roleLabel(member.role)}”，待审批中心执行后再刷新权威成员事实。
          </span>
        </div>
      ) : null}
    </Dialog>
  );
}

export type { ApprovalRequestStatus };
