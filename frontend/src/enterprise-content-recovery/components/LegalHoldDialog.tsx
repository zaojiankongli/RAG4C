import { useEffect, useState, type RefObject } from "react";
import { Dialog, Select, Tag, Textarea } from "tdesign-react";
import { SecuredIcon } from "tdesign-icons-react";

import type { LegalHoldInput, RecoveryEntry } from "./contentRecoveryTypes";

export interface LegalHoldDialogProps {
  visible: boolean;
  entry: RecoveryEntry | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (entry: RecoveryEntry, input: LegalHoldInput) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

const REASON_OPTIONS = [
  { label: "法律审查", value: "legal_review" },
  { label: "监管调查", value: "regulatory_investigation" },
  { label: "诉讼保全", value: "litigation_hold" },
  { label: "内部合规审查", value: "compliance_review" },
];

export default function LegalHoldDialog({
  visible,
  entry,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  returnFocusRef,
}: LegalHoldDialogProps) {
  const [reasonCode, setReasonCode] = useState("legal_review");
  const [safeReason, setSafeReason] = useState("");
  useEffect(() => {
    if (!visible) {
      setReasonCode("legal_review");
      setSafeReason("");
    }
  }, [visible, entry?.id]);
  const disabled =
    !entry || !safeReason.trim() || readOnly || saving || entry.status !== "recycled";
  return (
    <Dialog
      visible={visible}
      header="添加法律保留"
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: "添加法律保留", theme: "primary", disabled }}
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
        if (entry && !disabled)
          void onSubmit(entry, { reason_code: reasonCode, safe_reason: safeReason.trim() });
      }}
      {...({ role: "dialog", "aria-label": "添加法律保留" } as Record<string, unknown>)}
    >
      <div className="content-recovery__dialog-body" data-testid="recovery-hold-dialog">
        {entry ? (
          <>
            <div className="content-recovery__dialog-hero">
              <span className="content-recovery__dialog-icon is-secured" aria-hidden="true">
                <SecuredIcon />
              </span>
              <div>
                <span className="content-recovery__eyebrow">LEGAL HOLD REGISTER</span>
                <h3>{entry.document_label}</h3>
                <p>法律保留会阻断永久清除申请，但不会改变文档的恢复路径。</p>
              </div>
            </div>
            <label className="content-recovery__field">
              <span>保留原因</span>
              <Select
                aria-label="保留原因"
                value={reasonCode}
                options={REASON_OPTIONS}
                disabled={readOnly || saving}
                onChange={(value) => setReasonCode(String(value))}
              />
            </label>
            <label className="content-recovery__field">
              <span>保全说明</span>
              <Textarea
                aria-label="保全说明"
                value={safeReason}
                disabled={readOnly || saving}
                placeholder="只填写可安全展示的业务说明，不要包含正文、凭据或外部链接"
                autosize={{ minRows: 3, maxRows: 6 }}
                onChange={(value) => setSafeReason(String(value))}
              />
            </label>
            {readOnly ? <Tag theme="warning">只读模式下不允许添加法律保留</Tag> : null}
          </>
        ) : (
          <Tag theme="warning">未返回可验证的回收条目</Tag>
        )}
      </div>
    </Dialog>
  );
}
