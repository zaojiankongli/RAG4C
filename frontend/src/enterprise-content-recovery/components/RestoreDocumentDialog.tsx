import { useEffect, useState, type RefObject } from "react";
import { Checkbox, Dialog, Tag } from "tdesign-react";
import { ArrowRightIcon, SecuredIcon } from "tdesign-icons-react";

import type { RecoveryEntry } from "./contentRecoveryTypes";
import { formatRecoveryDate } from "./recoveryUi";

export interface RestoreDocumentDialogProps {
  visible: boolean;
  entry: RecoveryEntry | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (entry: RecoveryEntry) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export default function RestoreDocumentDialog({
  visible,
  entry,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  returnFocusRef,
}: RestoreDocumentDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (!visible) setConfirmed(false);
  }, [visible, entry?.id]);

  const disabled = !entry || !confirmed || readOnly || saving || entry.status !== "recycled";
  return (
    <Dialog
      visible={visible}
      header="恢复文档"
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: "确认恢复", theme: "primary", disabled }}
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
      {...({ role: "dialog", "aria-label": "恢复文档" } as Record<string, unknown>)}
    >
      <div className="content-recovery__dialog-body" data-testid="recovery-restore-dialog">
        {entry ? (
          <>
            <div className="content-recovery__dialog-hero">
              <span className="content-recovery__dialog-icon is-success" aria-hidden="true">
                <ArrowRightIcon />
              </span>
              <div>
                <span className="content-recovery__eyebrow">REVISION FENCED RESTORE</span>
                <h3>{entry.document_label}</h3>
                <p>恢复将重新启用该文档的原始生命周期状态与检索设置。</p>
              </div>
            </div>
            <dl className="content-recovery__dialog-facts">
              <div>
                <dt>原生命周期</dt>
                <dd>{entry.original_lifecycle_state === "active" ? "有效" : "已过期"}</dd>
              </div>
              <div>
                <dt>原检索状态</dt>
                <dd>{entry.original_retrieval_enabled ? "启用" : "关闭"}</dd>
              </div>
              <div>
                <dt>当前版本</dt>
                <dd>
                  <code>revision {entry.revision}</code>
                </dd>
              </div>
              <div>
                <dt>移入回收站</dt>
                <dd>{formatRecoveryDate(entry.recycled_at)}</dd>
              </div>
            </dl>
            <div className="content-recovery__dialog-boundary" role="note">
              <SecuredIcon aria-hidden="true" />
              <span>
                恢复不会批准、执行或触发永久清除；服务端会重新校验 Dataset、Workspace 和成员权限。
              </span>
            </div>
            {readOnly ? <Tag theme="warning">只读模式下不允许恢复</Tag> : null}
            {entry.status !== "recycled" ? (
              <Tag theme="warning">当前条目不处于可恢复状态</Tag>
            ) : null}
            <Checkbox
              checked={confirmed}
              disabled={readOnly || saving || entry.status !== "recycled"}
              onChange={(checked) => setConfirmed(Boolean(checked))}
            >
              我确认恢复此文档
            </Checkbox>
          </>
        ) : (
          <Tag theme="warning">未返回可验证的回收条目</Tag>
        )}
      </div>
    </Dialog>
  );
}
