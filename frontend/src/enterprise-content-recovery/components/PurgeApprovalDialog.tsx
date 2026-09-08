import { useEffect, useState, type RefObject } from "react";
import { Button, Checkbox, Dialog, Tag } from "tdesign-react";
import { DeleteTimeIcon, SecuredIcon, TimeIcon } from "tdesign-icons-react";

import type { RecoveryEntry } from "./contentRecoveryTypes";
import { formatRecoveryDate, purgeEligibility } from "./recoveryUi";

export interface PurgeApprovalDialogProps {
  visible: boolean;
  entry: RecoveryEntry | null;
  purgeEligible?: boolean;
  activeHoldCount?: number | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (entry: RecoveryEntry) => void | Promise<unknown>;
  onApprovalHandoff?: (approvalRequestId: string) => void;
  approvalRequestId?: string | null;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export default function PurgeApprovalDialog({
  visible,
  entry,
  purgeEligible,
  activeHoldCount,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  onApprovalHandoff,
  approvalRequestId = null,
  returnFocusRef,
}: PurgeApprovalDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (!visible) setConfirmed(false);
  }, [visible, entry?.id]);
  const holdCount = activeHoldCount ?? entry?.active_hold_count ?? null;
  const eligible = purgeEligible ?? (entry ? purgeEligibility(entry) : false);
  const disabled =
    !entry || !eligible || holdCount === null || holdCount > 0 || !confirmed || readOnly || saving;
  return (
    <Dialog
      visible={visible}
      header="申请永久清除审批"
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: "提交清除审批申请", theme: "danger", disabled }}
      confirmLoading={saving}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      attach="body"
      onClose={() => {
        onClose();
        const target = returnFocusRef?.current;
        if (target && target.isConnected) window.setTimeout(() => target.focus(), 0);
      }}
      onCancel={onClose}
      onConfirm={() => {
        if (entry && !disabled) void onSubmit(entry);
      }}
      {...({ role: "dialog", "aria-label": "申请永久清除审批" } as Record<string, unknown>)}
    >
      <div className="content-recovery__dialog-body" data-testid="recovery-purge-dialog">
        {entry ? (
          <>
            <div className="content-recovery__dialog-hero">
              <span className="content-recovery__dialog-icon is-danger" aria-hidden="true">
                <DeleteTimeIcon />
              </span>
              <div>
                <span className="content-recovery__eyebrow">APPROVAL-GATED PURGE REQUEST</span>
                <h3>{entry.document_label}</h3>
                <p>仅创建审批申请，不会批准或执行清除</p>
              </div>
            </div>
            <div className="content-recovery__purge-boundary" role="note">
              <SecuredIcon aria-hidden="true" />
              <strong>申请与执行分离</strong>
              <span>
                本中心只提交 document_purge 审批请求，Approval Center
                和后续执行器负责各自的权威动作。
              </span>
            </div>
            <dl className="content-recovery__dialog-facts">
              <div>
                <dt>保留期截止</dt>
                <dd>
                  <time dateTime={entry.purge_eligible_at ?? undefined}>
                    {formatRecoveryDate(entry.purge_eligible_at)}
                  </time>
                </dd>
              </div>
              <div>
                <dt>有效法律保留</dt>
                <dd>{holdCount === null ? "未返回" : `${holdCount} 个`}</dd>
              </div>
              <div>
                <dt>条目版本</dt>
                <dd>
                  <code>revision {entry.revision}</code>
                </dd>
              </div>
              <div>
                <dt>保留天数快照</dt>
                <dd>
                  {entry.retention_days_snapshot === null
                    ? "未返回"
                    : `${entry.retention_days_snapshot} 天`}
                </dd>
              </div>
            </dl>
            <div className="content-recovery__purge-blockers" aria-label="清除申请前置条件">
              <div className={eligible ? "is-satisfied" : "is-blocked"}>
                <TimeIcon aria-hidden="true" />
                <span>{eligible ? "保留期已满足" : "保留期尚未满足"}</span>
              </div>
              <div className={holdCount === 0 ? "is-satisfied" : "is-blocked"}>
                <SecuredIcon aria-hidden="true" />
                <span>
                  {holdCount === null
                    ? "法律保留状态未返回"
                    : holdCount > 0
                      ? `存在 ${holdCount} 个有效法律保留`
                      : "无有效法律保留"}
                </span>
              </div>
            </div>
            {approvalRequestId && onApprovalHandoff ? (
              <Button
                variant="text"
                size="small"
                onClick={() => onApprovalHandoff(approvalRequestId)}
              >
                前往审批中心查看申请
              </Button>
            ) : null}
            {readOnly ? <Tag theme="warning">只读模式下不允许提交清除审批</Tag> : null}
            <Checkbox
              checked={confirmed}
              disabled={disabled && !confirmed && (readOnly || saving)}
              onChange={(checked) => setConfirmed(Boolean(checked))}
            >
              我确认清除申请已满足保留期
            </Checkbox>
          </>
        ) : (
          <Tag theme="warning">未返回可验证的回收条目</Tag>
        )}
      </div>
    </Dialog>
  );
}
