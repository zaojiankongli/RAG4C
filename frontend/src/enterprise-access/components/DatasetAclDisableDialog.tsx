import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Dialog, InputNumber, Tag, Textarea } from "tdesign-react";
import { ArrowRightIcon, CheckCircleIcon, LockOnIcon } from "tdesign-icons-react";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { approvalStatusLabel } from "../../enterprise-approval/enterpriseApprovalModel";
import type { PersistentDatasetAccessSummary } from "../enterpriseAccessModel";
import type {
  DatasetAclApprovalRequiredHint,
  DatasetAclApprovalPolicyFacts,
} from "../datasetAclApprovalModel";
import { useDatasetAclApprovalGate } from "../hooks/useDatasetAclApprovalGate";
import type { DatasetAccessGrantMutationError } from "../hooks/useDatasetAccessGrantMutations";

export interface DatasetAclDisableDialogProps {
  visible: boolean;
  scope: EnterpriseScope;
  aclRevision: number | null | undefined;
  currentAclMode: string | null | undefined;
  saving: boolean;
  error: DatasetAccessGrantMutationError | null;
  approvalRequiredHint?: DatasetAclApprovalRequiredHint | null;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onRetry?: () => Promise<unknown>;
  onSubmit: (payload: {
    expected_acl_revision: number;
    reason: string;
  }) => Promise<PersistentDatasetAccessSummary | null>;
}

function validRevision(value: string): number | null {
  const revision = Number(value.trim());
  return Number.isInteger(revision) && revision > 0 ? revision : null;
}

function policyName(policy: DatasetAclApprovalPolicyFacts): string {
  return policy.name || "服务端未返回规则名称";
}

function requiredApprovalsLabel(policy: DatasetAclApprovalPolicyFacts): string {
  return policy.required_approvals === null
    ? "服务端未返回"
    : `${policy.required_approvals} 人审批`;
}

function expiryLabel(policy: DatasetAclApprovalPolicyFacts): string {
  return policy.request_expiry_minutes === null
    ? "服务端未返回"
    : `提交后 ${policy.request_expiry_minutes} 分钟内有效`;
}

function navigateToApprovalCenter(): void {
  window.history.pushState({}, "", "/enterprise/approvals");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export default function DatasetAclDisableDialog({
  visible,
  scope,
  aclRevision,
  currentAclMode,
  saving,
  error,
  approvalRequiredHint,
  onClose,
  onRefresh,
  onRetry,
  onSubmit,
}: DatasetAclDisableDialogProps) {
  const [revision, setRevision] = useState<string | number>("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [validation, setValidation] = useState("");
  const gate = useDatasetAclApprovalGate({
    visible,
    scope,
    aclRevision,
    currentAclMode,
    serverApprovalRequired:
      approvalRequiredHint ??
      (error?.code === "dataset_acl_approval_required" ? error.approvalRequired : null),
  });

  useEffect(() => {
    if (!visible) return;
    setRevision(
      typeof aclRevision === "number" && Number.isInteger(aclRevision) ? aclRevision : "",
    );
    setReason("");
    setConfirmed(false);
    setValidation("");
  }, [aclRevision, visible]);

  if (!visible) return null;

  const retryMutation = async () => {
    if (!onRetry) return;
    const result = await onRetry();
    if (result) onClose();
  };

  const submit = async () => {
    const expectedAclRevision = validRevision(String(revision));
    const trimmedReason = reason.trim();
    if (!expectedAclRevision) {
      setValidation("ACL revision 必须是大于 0 的整数。");
      return;
    }
    if (!trimmedReason) {
      setValidation("停用原因不能为空。");
      return;
    }
    if (!confirmed) {
      setValidation("请确认你理解停用后的权限影响。");
      return;
    }
    if (gate.approvalMode) {
      await gate.submitApproval({
        expected_acl_revision: expectedAclRevision,
        reason: trimmedReason,
      });
      return;
    }
    const result = await onSubmit({
      expected_acl_revision: expectedAclRevision,
      reason: trimmedReason,
    });
    if (result) onClose();
  };

  const approvalMode = gate.approvalMode;
  const submitted = gate.mode === "submitted" && gate.request !== null;
  const busy = saving || gate.submitting;
  const canSubmit = Boolean(
    validRevision(String(revision)) &&
    reason.trim() &&
    confirmed &&
    !busy &&
    !submitted &&
    (gate.mode === "direct" || gate.mode === "approval"),
  );
  const approvalError =
    error?.code === "dataset_acl_approval_required" ? null : error?.message || null;
  const displayedPolicy = gate.policy;
  const confirmContent = submitted
    ? "审批申请已提交"
    : gate.mode === "approval"
      ? "提交审批申请"
      : gate.mode === "loading" || gate.mode === "idle"
        ? "正在核对审批规则"
        : "确认停用 ACL";

  return (
    <Dialog
      visible={visible}
      header="停用知识库 ACL"
      width={560}
      destroyOnClose
      closeOnEscKeydown={!busy}
      closeOnOverlayClick={!busy}
      confirmBtn={{
        content: confirmContent,
        theme: approvalMode ? "primary" : "danger",
        disabled: !canSubmit,
      }}
      cancelBtn={{ content: "取消", disabled: busy }}
      confirmLoading={busy}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "停用知识库 ACL" } as Record<
        string,
        unknown
      >)}
    >
      {approvalError ? (
        <div className="enterprise-access-mutation-error" role="alert">
          <Alert theme="error" title="ACL 停用未提交" message={approvalError} />
          {error?.retryAvailable && onRetry ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              onClick={() => void retryMutation()}
              disabled={busy}
            >
              使用同一请求重试
            </Button>
          ) : null}
          {error?.needsRefresh ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              onClick={() => void onRefresh()}
              disabled={busy}
            >
              刷新访问事实
            </Button>
          ) : null}
        </div>
      ) : null}
      {gate.error ? (
        <div className="enterprise-access-acl-approval-error" role="alert">
          <Alert theme="warning" title="审批规则读取受限" message={gate.error} />
          <Button
            variant="text"
            theme="primary"
            size="small"
            onClick={() => void gate.refresh()}
            disabled={busy}
          >
            重新读取审批规则
          </Button>
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐停用信息" message={validation} /> : null}
      {gate.mode === "loading" || gate.mode === "idle" ? (
        <div className="enterprise-access-acl-approval-loading" role="status">
          <LockOnIcon aria-hidden="true" />
          <span>正在核对当前租户的 ACL 停用审批规则…</span>
        </div>
      ) : null}
      {approvalMode && displayedPolicy ? (
        <div
          className="enterprise-access-acl-approval-policy"
          role="status"
          aria-label="ACL 停用审批规则"
        >
          <div className="enterprise-access-acl-approval-policy__heading">
            <div>
              <span className="enterprise-access-acl-approval-policy__eyebrow">
                GOVERNANCE / APPROVAL REQUIRED
              </span>
              <strong>{policyName(displayedPolicy)}</strong>
              <small>{displayedPolicy.id}</small>
            </div>
            <Tag theme="warning" variant="light-outline" size="small">
              需要审批
            </Tag>
          </div>
          <div className="enterprise-access-acl-approval-policy__facts">
            <div>
              <span>审批阈值</span>
              <strong>{requiredApprovalsLabel(displayedPolicy)}</strong>
            </div>
            <div>
              <span>有效期</span>
              <strong>{expiryLabel(displayedPolicy)}</strong>
            </div>
            <div>
              <span>规则 revision</span>
              <strong>{displayedPolicy.revision ?? "服务端未返回"}</strong>
            </div>
          </div>
        </div>
      ) : null}
      {approvalMode && currentAclMode === "dataset_acl" ? (
        <div className="enterprise-access-acl-approval-boundary" role="note">
          <CheckCircleIcon aria-hidden="true" />
          <div>
            <strong>ACL 模式保持 dataset_acl</strong>
            <span>申请提交不会改变当前访问模式；审批通过后仍需在审批中心执行一次性授权。</span>
          </div>
        </div>
      ) : null}
      {submitted && gate.request ? (
        <div
          className="enterprise-access-acl-approval-request"
          role="status"
          aria-label="审批申请结果"
        >
          <div className="enterprise-access-acl-approval-request__heading">
            <div>
              <span>审批申请已提交</span>
              <strong>request_id</strong>
            </div>
            <Tag theme="success" variant="light-outline" size="small">
              {approvalStatusLabel(gate.request.status)}
            </Tag>
          </div>
          <div className="enterprise-access-acl-approval-request__facts">
            <code>{gate.request.id}</code>
            <span>当前状态：{approvalStatusLabel(gate.request.status)}</span>
          </div>
          <Button
            className="enterprise-access-acl-approval-request__link"
            theme="primary"
            variant="outline"
            size="small"
            icon={<ArrowRightIcon />}
            onClick={navigateToApprovalCenter}
          >
            前往审批中心
          </Button>
        </div>
      ) : null}
      <div className="enterprise-access-mutation-dialog__intro enterprise-access-acl-disable-dialog__intro">
        <strong>这是一个租户级治理边界操作。</strong>
        <span>
          停用后不会删除或撤销现有授权记录；知识库会回到租户角色模式，后续授权记录仍保留供审计。
        </span>
      </div>
      <div className="enterprise-access-mutation-form enterprise-access-acl-disable-dialog__form">
        <div className="enterprise-access-field">
          <span>ACL revision</span>
          <InputNumber
            className="enterprise-access-acl-disable-dialog__revision"
            value={revision}
            min={1}
            step={1}
            decimalPlaces={0}
            allowInputOverLimit={false}
            disabled={busy || submitted}
            inputProps={{ "aria-label": "ACL revision", inputMode: "numeric" } as never}
            onChange={(value) => {
              setRevision(value === null || value === undefined ? "" : value);
              setValidation("");
            }}
          />
        </div>
        <label className="enterprise-access-mutation-form__wide">
          <span>停用原因</span>
          <Textarea
            aria-label="停用原因"
            value={reason}
            disabled={busy || submitted}
            placeholder="说明为何要将当前知识库从持久 ACL 模式切回租户角色模式"
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </label>
        <Checkbox
          className="enterprise-access-acl-disable-dialog__confirm"
          checked={confirmed}
          disabled={busy || submitted}
          onChange={(checked) => {
            setConfirmed(checked);
            setValidation("");
          }}
        >
          我确认停用此知识库的持久 ACL 模式
        </Checkbox>
      </div>
    </Dialog>
  );
}
