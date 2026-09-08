import { useEffect, useState, type RefObject } from "react";
import { Checkbox, Dialog, Tag } from "tdesign-react";
import { CloseCircleIcon, SecuredIcon } from "tdesign-icons-react";

import type { LegalHold } from "./contentRecoveryTypes";

export interface ReleaseLegalHoldDialogProps {
  visible: boolean;
  hold: LegalHold | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (hold: LegalHold) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export default function ReleaseLegalHoldDialog({
  visible,
  hold,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  returnFocusRef,
}: ReleaseLegalHoldDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (!visible) setConfirmed(false);
  }, [visible, hold?.id]);
  const disabled = !hold || hold.status !== "active" || !confirmed || readOnly || saving;
  return (
    <Dialog
      visible={visible}
      header="释放法律保留"
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: "释放法律保留", theme: "danger", disabled }}
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
        if (hold && !disabled) void onSubmit(hold);
      }}
      {...({ role: "dialog", "aria-label": "释放法律保留" } as Record<string, unknown>)}
    >
      <div className="content-recovery__dialog-body" data-testid="recovery-release-hold-dialog">
        {hold ? (
          <>
            <div className="content-recovery__dialog-hero">
              <span className="content-recovery__dialog-icon is-danger" aria-hidden="true">
                <CloseCircleIcon />
              </span>
              <div>
                <span className="content-recovery__eyebrow">RELEASE WITH AUDIT EVENT</span>
                <h3>{hold.safe_reason}</h3>
                <p>
                  释放后，条目仍受保留期与审批规则保护；只有有效期满足且无其他保全时才能申请清除。
                </p>
              </div>
            </div>
            <dl className="content-recovery__dialog-facts">
              <div>
                <dt>原因代码</dt>
                <dd>{hold.reason_code}</dd>
              </div>
              <div>
                <dt>保留版本</dt>
                <dd>
                  <code>revision {hold.revision}</code>
                </dd>
              </div>
              <div>
                <dt>创建账号</dt>
                <dd>{hold.held_by}</dd>
              </div>
            </dl>
            <div className="content-recovery__dialog-boundary" role="note">
              <SecuredIcon aria-hidden="true" />
              释放动作会追加不可变审计事件，不会直接提交清除审批。
            </div>
            {readOnly ? <Tag theme="warning">只读模式下不允许释放法律保留</Tag> : null}
            {hold.status !== "active" ? <Tag theme="default">该法律保留已释放</Tag> : null}
            <Checkbox
              checked={confirmed}
              disabled={readOnly || saving || hold.status !== "active"}
              onChange={(checked) => setConfirmed(Boolean(checked))}
            >
              我确认释放此法律保留
            </Checkbox>
          </>
        ) : (
          <Tag theme="warning">未返回可验证的法律保留</Tag>
        )}
      </div>
    </Dialog>
  );
}
