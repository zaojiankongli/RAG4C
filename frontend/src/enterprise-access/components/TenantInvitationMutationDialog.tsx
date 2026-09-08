import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import {
  Alert,
  Button,
  DatePicker,
  Dialog,
  Form,
  Input,
  InputNumber,
  Select,
  Textarea,
} from "tdesign-react";
import type {
  CreateEnterpriseInvitationInput,
  EnterpriseInvitation,
  EnterpriseInvitationMutationResult,
  EnterpriseInvitationRole,
  ResendEnterpriseInvitationInput,
  RevokeEnterpriseInvitationInput,
} from "../enterpriseAccessModel";
import type { EnterpriseInvitationMutationError } from "../hooks/useEnterpriseInvitationMutations";

export type TenantInvitationDialogMode = "create" | "resend" | "revoke";
export type TenantInvitationDialogPayload =
  | { mode: "create"; input: CreateEnterpriseInvitationInput }
  | { mode: "resend"; input: ResendEnterpriseInvitationInput }
  | { mode: "revoke"; input: RevokeEnterpriseInvitationInput };

export interface TenantInvitationMutationDialogProps {
  visible: boolean;
  mode: TenantInvitationDialogMode;
  invitation: EnterpriseInvitation | null;
  actorRole: string;
  saving: boolean;
  error: EnterpriseInvitationMutationError | null;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onRetry?: () => Promise<EnterpriseInvitationMutationResult | null>;
  onSubmit: (
    payload: TenantInvitationDialogPayload,
  ) => Promise<EnterpriseInvitationMutationResult | null>;
}

const ROLE_OPTIONS: Array<{ label: string; value: EnterpriseInvitationRole }> = [
  { label: "所有者", value: "owner" },
  { label: "管理员", value: "admin" },
  { label: "编辑者", value: "editor" },
  { label: "成员", value: "member" },
];

function titleFor(mode: TenantInvitationDialogMode): string {
  if (mode === "create") return "发起成员邀请";
  if (mode === "resend") return "重新生成邀请链接";
  return "撤销成员邀请";
}

function confirmFor(mode: TenantInvitationDialogMode): string {
  if (mode === "create") return "确认邀请";
  if (mode === "resend") return "重新生成链接";
  return "撤销邀请";
}

function expiryDays(value: string): number | null {
  const selected = dayjs(value).startOf("day");
  if (!selected.isValid()) return null;
  const days = selected.diff(dayjs().startOf("day"), "day");
  return days >= 1 && days <= 90 ? days : null;
}

export default function TenantInvitationMutationDialog({
  visible,
  mode,
  invitation,
  actorRole,
  saving,
  error,
  onClose,
  onRefresh,
  onRetry,
  onSubmit,
}: TenantInvitationMutationDialogProps) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<EnterpriseInvitationRole>("member");
  const [expiryDate, setExpiryDate] = useState("");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  const roles = useMemo(
    () =>
      actorRole === "owner" ? ROLE_OPTIONS : ROLE_OPTIONS.filter((item) => item.value !== "owner"),
    [actorRole],
  );

  useEffect(() => {
    if (!visible) return;
    setEmail("");
    setRole(
      invitation?.role && roles.some((item) => item.value === invitation.role)
        ? (invitation.role as EnterpriseInvitationRole)
        : "member",
    );
    setExpiryDate(dayjs().add(7, "day").format("YYYY-MM-DD"));
    setReason("");
    setValidation("");
  }, [invitation?.id, invitation?.revision, invitation?.role, mode, roles, visible]);

  if (!visible) return null;

  const retryMutation = async () => {
    if (!onRetry) return;
    const result = await onRetry();
    if (result) onClose();
  };

  const submit = async () => {
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      setValidation("邀请原因不能为空。");
      return;
    }
    if (mode === "create") {
      const normalizedEmail = email.trim().toLocaleLowerCase("en-US");
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
        setValidation("请输入有效的受邀邮箱。");
        return;
      }
      const days = expiryDays(expiryDate);
      if (!days) {
        setValidation("邀请到期日期必须在未来 1 到 90 天内。");
        return;
      }
      const result = await onSubmit({
        mode,
        input: { email: normalizedEmail, role, expires_in_days: days, reason: normalizedReason },
      });
      if (result) onClose();
      return;
    }
    if (!invitation || !Number.isInteger(invitation.revision) || (invitation.revision ?? 0) < 1) {
      setValidation("服务端未返回有效 invitation revision，无法安全提交。");
      return;
    }
    if (mode === "resend") {
      const days = expiryDays(expiryDate);
      if (!days) {
        setValidation("邀请到期日期必须在未来 1 到 90 天内。");
        return;
      }
      const result = await onSubmit({
        mode,
        input: {
          revision: invitation.revision as number,
          expires_in_days: days,
          reason: normalizedReason,
        },
      });
      if (result) onClose();
      return;
    }
    const result = await onSubmit({
      mode,
      input: { revision: invitation.revision as number, reason: normalizedReason },
    });
    if (result) onClose();
  };

  return (
    <Dialog
      visible={visible}
      header={titleFor(mode)}
      width={560}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{ content: confirmFor(mode), theme: mode === "revoke" ? "danger" : "primary" }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": titleFor(mode) } as Record<
        string,
        unknown
      >)}
    >
      {error ? (
        <div className="enterprise-access-mutation-error" role="alert">
          <Alert theme="error" title="邀请操作未提交" message={error.message} />
          {error.retryAvailable && onRetry ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              disabled={saving}
              onClick={() => void retryMutation()}
            >
              使用同一请求重试
            </Button>
          ) : null}
          {error.needsRefresh ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              disabled={saving}
              onClick={() => void onRefresh()}
            >
              刷新邀请列表
            </Button>
          ) : null}
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐邀请信息" message={validation} /> : null}
      <Alert
        className="enterprise-invitation-manual-alert"
        theme="info"
        title="邮件通道未接入"
        message="提交后服务端只返回一次性安全链接，当前需要管理员手动复制并通过受控渠道交付。"
      />
      {invitation ? (
        <div className="enterprise-access-mutation-dialog__intro">
          <strong>{invitation.email}</strong>
          <span>
            {invitation.role} · {invitation.status} · revision {invitation.revision ?? "未返回"}
          </span>
        </div>
      ) : null}
      <Form className="enterprise-invitation-form" labelAlign="top">
        {mode === "create" ? (
          <>
            <Form.FormItem>
              <label className="enterprise-invitation-form-field">
                <span>受邀邮箱</span>
                <Input
                  value={email}
                  placeholder="member@example.com"
                  disabled={saving}
                  onChange={(value) => {
                    setEmail(String(value));
                    setValidation("");
                  }}
                />
              </label>
            </Form.FormItem>
            <Form.FormItem label="预设租户角色">
              <Select
                value={role}
                aria-label="预设租户角色"
                options={roles}
                disabled={saving}
                onChange={(value) => {
                  setRole(String(value) as EnterpriseInvitationRole);
                  setValidation("");
                }}
              />
            </Form.FormItem>
          </>
        ) : null}
        {mode !== "revoke" ? (
          <Form.FormItem label="邀请到期日期">
            <DatePicker
              value={expiryDate}
              aria-label="邀请到期日期"
              format="YYYY-MM-DD"
              disableDate={{ before: dayjs().add(1, "day").format("YYYY-MM-DD") }}
              disabled={saving}
              onChange={(value) => {
                setExpiryDate(String(value));
                setValidation("");
              }}
            />
          </Form.FormItem>
        ) : null}
        {mode !== "create" ? (
          <Form.FormItem label="邀请 revision">
            <InputNumber
              value={invitation?.revision ?? ""}
              readOnly
              theme="normal"
              aria-label="邀请 revision"
            />
          </Form.FormItem>
        ) : null}
        <Form.FormItem label="邀请原因" className="enterprise-invitation-form__wide">
          <Textarea
            value={reason}
            aria-label="邀请原因"
            placeholder="说明本次成员邀请的业务原因"
            disabled={saving}
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </Form.FormItem>
      </Form>
    </Dialog>
  );
}
