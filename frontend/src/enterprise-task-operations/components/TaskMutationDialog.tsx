import { useEffect, useState, type RefObject } from "react";
import { Checkbox, Dialog, Tag } from "tdesign-react";
import { CloseIcon, RefreshIcon, SecuredIcon } from "tdesign-icons-react";

import type { TaskOperation } from "./taskOperationsTypes";

export interface TaskMutationDialogProps {
  kind: "retry" | "cancel";
  visible: boolean;
  task: TaskOperation | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (task: TaskOperation) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export default function TaskMutationDialog({
  kind,
  visible,
  task,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  returnFocusRef,
}: TaskMutationDialogProps) {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (!visible) setConfirmed(false);
  }, [visible, task?.id]);

  const retry = kind === "retry";
  const title = retry ? "重试任务" : "取消任务";
  const confirmLabel = retry ? "确认重试" : "确认取消";
  const checkLabel = retry ? "我确认重新执行此任务" : "我确认取消此任务";
  const actionAllowed = retry ? task?.retryable : task?.cancellable;
  const disabled = !task || !confirmed || readOnly || saving || !actionAllowed;
  const handleClose = () => {
    onClose();
    const target = returnFocusRef?.current;
    if (target && target.isConnected) window.setTimeout(() => target.focus(), 0);
  };

  return (
    <Dialog
      visible={visible}
      header={title}
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: confirmLabel, theme: retry ? "primary" : "danger", disabled }}
      confirmLoading={saving}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      attach="body"
      onClose={handleClose}
      onCancel={handleClose}
      onConfirm={() => {
        if (task && !disabled) void onSubmit(task);
      }}
      {...({ role: "dialog", "aria-label": title } as Record<string, unknown>)}
    >
      <div className="task-operations__mutation-body" data-testid={`task-${kind}-dialog`}>
        {task ? (
          <>
            <div className="task-operations__mutation-hero">
              <span
                className={`task-operations__mutation-icon task-operations__mutation-icon--${retry ? "retry" : "cancel"}`}
                aria-hidden="true"
              >
                {retry ? <RefreshIcon /> : <CloseIcon />}
              </span>
              <div>
                <span className="task-operations__eyebrow">
                  {retry ? "REVISION FENCED RETRY" : "CONTROLLED CANCELLATION"}
                </span>
                <h3>{task.task_label}</h3>
                <p>
                  {retry
                    ? "系统会基于当前 revision 创建新的执行尝试，不会修改来源内容。"
                    : "取消只停止尚未完成的执行，不会删除任务事件或结果。"}
                </p>
              </div>
            </div>
            <div className="task-operations__mutation-boundary" role="note">
              <SecuredIcon aria-hidden="true" />
              <span>
                服务端会重新校验 Tenant、队列状态与 mutation generation；页面不会绕过任务权威。
              </span>
            </div>
            {readOnly ? <Tag theme="warning">只读模式下不允许执行此操作</Tag> : null}
            {!actionAllowed ? <Tag theme="warning">当前任务状态不允许此操作</Tag> : null}
            <Checkbox
              checked={confirmed}
              disabled={readOnly || saving || !actionAllowed}
              onChange={(checked) => setConfirmed(Boolean(checked))}
            >
              {checkLabel}
            </Checkbox>
          </>
        ) : (
          <Tag theme="warning">未返回可验证的任务记录</Tag>
        )}
      </div>
    </Dialog>
  );
}

export function TaskRetryDialog(props: Omit<TaskMutationDialogProps, "kind">) {
  return <TaskMutationDialog {...props} kind="retry" />;
}

export function TaskCancelDialog(props: Omit<TaskMutationDialogProps, "kind">) {
  return <TaskMutationDialog {...props} kind="cancel" />;
}
