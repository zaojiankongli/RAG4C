import { useEffect, useState, type RefObject } from "react";
import { Alert, Checkbox, Dialog, InputNumber, Select, Tag } from "tdesign-react";
import { SecuredIcon } from "tdesign-icons-react";

import type { RetentionPolicy, RetentionPolicyInput } from "./contentRecoveryTypes";

export interface RetentionPolicyPanelProps {
  visible: boolean;
  policy: RetentionPolicy | null;
  readOnly?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (input: RetentionPolicyInput) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

const STATUS_OPTIONS = [
  { label: "启用策略", value: "active" },
  { label: "暂停策略", value: "paused" },
];

export default function RetentionPolicyPanel({
  visible,
  policy,
  readOnly = false,
  saving = false,
  onClose,
  onSubmit,
  returnFocusRef,
}: RetentionPolicyPanelProps) {
  const [status, setStatus] = useState<RetentionPolicy["status"]>("active");
  const [retentionDays, setRetentionDays] = useState<number>(30);
  const [autoPurgeEnabled, setAutoPurgeEnabled] = useState(false);
  const [purgeRequiresApproval, setPurgeRequiresApproval] = useState(true);

  useEffect(() => {
    if (!policy) return;
    setStatus(policy.status);
    setRetentionDays(policy.retention_days);
    setAutoPurgeEnabled(policy.auto_purge_enabled);
    setPurgeRequiresApproval(policy.purge_requires_approval);
  }, [policy, visible]);

  const disabled = !policy || readOnly || saving || retentionDays < 1 || retentionDays > 3650;
  return (
    <Dialog
      visible={visible}
      header="内容保留策略"
      closeBtn
      cancelBtn="取消"
      confirmBtn={{ content: "保存保留策略", theme: "primary", disabled }}
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
        if (policy && !disabled) {
          void onSubmit({
            status,
            retention_days: retentionDays,
            auto_purge_enabled: autoPurgeEnabled,
            purge_requires_approval: purgeRequiresApproval,
            expected_revision: policy.revision,
          });
        }
      }}
      {...({ role: "dialog", "aria-label": "内容保留策略" } as Record<string, unknown>)}
    >
      <div className="content-recovery__policy-body" data-testid="recovery-retention-policy-panel">
        {policy ? (
          <>
            <div className="content-recovery__policy-intro">
              <div className="content-recovery__policy-icon" aria-hidden="true">
                <SecuredIcon />
              </div>
              <div>
                <span className="content-recovery__eyebrow">TENANT RETENTION POLICY</span>
                <h3>把不可逆动作留在治理边界之外</h3>
                <p>策略只定义保留与审批前置条件，不会自动清除任何文档。</p>
              </div>
            </div>
            {readOnly ? (
              <Alert
                theme="warning"
                icon={<SecuredIcon />}
                title="只读模式下不允许修改策略"
                message="当前页面只展示租户权威策略，保存按钮已禁用。"
              />
            ) : null}
            <div className="content-recovery__policy-grid">
              <label className="content-recovery__field">
                <span>策略状态</span>
                <Select
                  aria-label="策略状态"
                  value={status}
                  options={STATUS_OPTIONS}
                  disabled={readOnly || saving}
                  onChange={(value) => setStatus(String(value) as RetentionPolicy["status"])}
                />
              </label>
              <label className="content-recovery__field">
                <span>保留天数</span>
                <InputNumber
                  aria-label="保留天数"
                  value={retentionDays}
                  min={1}
                  max={3650}
                  disabled={readOnly || saving}
                  onChange={(value) => setRetentionDays(Number(value))}
                />
              </label>
            </div>
            <div className="content-recovery__policy-options">
              <Checkbox
                checked={autoPurgeEnabled}
                disabled={readOnly || saving}
                onChange={(checked) => setAutoPurgeEnabled(Boolean(checked))}
              >
                允许策略驱动的自动清除资格计算
              </Checkbox>
              <Checkbox
                checked={purgeRequiresApproval}
                disabled={readOnly || saving}
                onChange={(checked) => setPurgeRequiresApproval(Boolean(checked))}
              >
                永久清除必须经过审批
              </Checkbox>
            </div>
            <div className="content-recovery__policy-boundary" role="note">
              <Tag theme="warning">默认安全值</Tag>
              <span>自动清除默认关闭，永久清除默认必须审批；本阶段不会自动执行物理删除。</span>
            </div>
            <div className="content-recovery__policy-meta">
              <span>
                当前 revision <code>{policy.revision}</code>
              </span>
              <span>更新于 {policy.updated_at ?? "未返回"}</span>
              <span>更新账号 {policy.updated_by ?? "未返回"}</span>
            </div>
          </>
        ) : (
          <Tag theme="warning">保留策略暂不可用</Tag>
        )}
      </div>
    </Dialog>
  );
}
