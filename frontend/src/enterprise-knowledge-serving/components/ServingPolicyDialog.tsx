import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Checkbox, Dialog, InputNumber, Tag, Textarea } from "tdesign-react";
import { ChartLineIcon, LockOnIcon, ViewModuleIcon } from "tdesign-icons-react";
import type { ServingPolicyRevision, ServingPreview, ServingProfile } from "../model/servingModel";
import type { ServingPolicyInput } from "../api/servingApi";
import {
  dateLabel,
  safeErrorMessage,
  stateTheme,
  OVERALL_STATE_LABELS,
  StageTag,
} from "./servingUi";

export interface ServingPolicyDialogProps {
  visible: boolean;
  profile: ServingProfile | null;
  policy: ServingPolicyRevision | null;
  readOnly: boolean;
  saving: boolean;
  preview: ServingPreview | null;
  error: Error | string | null;
  onClose: () => void;
  onPreview: (input: {
    expectedRevision: number;
    expectedPolicyDigest: string;
    policy: ServingPolicyInput;
    reason: string;
  }) => Promise<ServingPreview | null>;
  onSubmit: (input: {
    expectedRevision: number;
    expectedPolicyDigest: string;
    policy: ServingPolicyInput;
    reason: string;
  }) => Promise<unknown>;
}
const FALLBACK_DIGEST = "0".repeat(64);
function inputNumber(value: number | string | undefined, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : fallback;
}
function policyInput(policy: ServingPolicyRevision | null): ServingPolicyInput {
  return {
    maxSourceStalenessSeconds: policy?.max_source_staleness_seconds ?? 3600,
    maxParseLagSeconds: policy?.max_parse_lag_seconds ?? 7200,
    maxIndexLagSeconds: policy?.max_index_lag_seconds ?? 7200,
    maxFailedDocumentCount: policy?.max_failed_document_count ?? 2,
    maxPendingIndexCount: policy?.max_pending_index_count ?? 3,
    requireCurrentRelease: policy?.require_current_release ?? true,
    requirePassingCertification: policy?.require_passing_certification ?? false,
  };
}
function PreviewResult({ preview }: { preview: ServingPreview }) {
  return (
    <section className="knowledge-serving__preview-result" aria-label="策略预览结果">
      <div className="knowledge-serving__preview-result-header">
        <div>
          <span className="knowledge-serving__eyebrow">ZERO-WRITE PREVIEW</span>
          <strong>策略模拟结果</strong>
        </div>
        <Tag theme={stateTheme(preview.state)} variant="light-outline">
          {OVERALL_STATE_LABELS[preview.state]}
        </Tag>
      </div>
      <div className="knowledge-serving__preview-stages">
        {preview.stage_facts.map((stage) => (
          <div key={stage.stage_code}>
            <span>{stage.stage_code.toUpperCase()}</span>
            <StageTag state={stage.state} />
          </div>
        ))}
      </div>
      {preview.blockers.length ? (
        <Alert
          theme="warning"
          title="存在阻断证据"
          message={preview.blockers.map((item) => item.safe_message).join("；")}
        />
      ) : (
        <div className="knowledge-serving__preview-clear">
          <ChartLineIcon aria-hidden="true" />
          没有发现新的策略阻断
        </div>
      )}
    </section>
  );
}
export default function ServingPolicyDialog({
  visible,
  profile,
  policy,
  readOnly,
  saving,
  preview,
  error,
  onClose,
  onPreview,
  onSubmit,
}: ServingPolicyDialogProps) {
  const [draft, setDraft] = useState<ServingPolicyInput>(() => policyInput(policy));
  const [reason, setReason] = useState("调整知识服务可靠性阈值");
  const [validation, setValidation] = useState<string | null>(null);
  useEffect(() => {
    if (visible) {
      setDraft(policyInput(policy));
      setReason("调整知识服务可靠性阈值");
      setValidation(null);
    }
  }, [policy, visible]);
  const fence = useMemo(
    () => ({
      expectedRevision: profile?.revision ?? 1,
      expectedPolicyDigest: policy?.policy_digest ?? FALLBACK_DIGEST,
      policy: draft,
      reason: reason.trim(),
    }),
    [draft, policy?.policy_digest, profile?.revision, reason],
  );
  const updateNumber =
    (field: keyof ServingPolicyInput, fallback: number) => (value: number | string | undefined) =>
      setDraft((current) => ({ ...current, [field]: inputNumber(value, fallback) }));
  const runPreview = async () => {
    if (readOnly || saving) return;
    if (!fence.reason) {
      setValidation("预览原因不能为空。");
      return;
    }
    setValidation(null);
    await onPreview(fence);
  };
  const save = async () => {
    if (readOnly || saving) return;
    if (!fence.reason) {
      setValidation("变更原因不能为空。");
      return;
    }
    setValidation(null);
    await onSubmit(fence);
  };
  if (!visible) return null;
  return (
    <Dialog
      visible
      header="服务策略"
      width={620}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{
        tag: "button",
        content: "保存策略",
        disabled: readOnly || saving || !fence.reason,
      }}
      cancelBtn={{ tag: "button", content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void save()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": "服务策略" } as Record<
        string,
        unknown
      >)}
    >
      <div className="knowledge-serving__policy-dialog">
        <div className="knowledge-serving__policy-dialog-intro">
          <div className="knowledge-serving__policy-dialog-icon">
            <LockOnIcon />
          </div>
          <div>
            <span className="knowledge-serving__eyebrow">POLICY REVISION / FENCED</span>
            <strong>{profile?.name ?? "Knowledge Serving"}</strong>
            <p>策略只评估可靠性证据，不会写入来源、索引或查询状态。</p>
          </div>
          <Tag theme="primary" variant="light-outline">
            R{profile?.revision ?? "未返回"}
          </Tag>
        </div>
        {error ? (
          <Alert
            theme="error"
            title="策略操作未提交"
            message={safeErrorMessage(error) ?? "策略权威不可用。"}
          />
        ) : null}
        {validation ? <Alert theme="warning" title="请补齐策略信息" message={validation} /> : null}
        <div className="knowledge-serving__policy-grid">
          <label>
            <span>Source freshness / 秒</span>
            <InputNumber
              value={draft.maxSourceStalenessSeconds}
              min={0}
              max={31536000}
              onChange={updateNumber("maxSourceStalenessSeconds", 3600)}
            />
          </label>
          <label>
            <span>Parse lag / 秒</span>
            <InputNumber
              value={draft.maxParseLagSeconds}
              min={0}
              max={31536000}
              onChange={updateNumber("maxParseLagSeconds", 7200)}
            />
          </label>
          <label>
            <span>Index lag / 秒</span>
            <InputNumber
              value={draft.maxIndexLagSeconds}
              min={0}
              max={31536000}
              onChange={updateNumber("maxIndexLagSeconds", 7200)}
            />
          </label>
          <label>
            <span>Failed documents</span>
            <InputNumber
              value={draft.maxFailedDocumentCount}
              min={0}
              max={1000000}
              onChange={updateNumber("maxFailedDocumentCount", 2)}
            />
          </label>
          <label>
            <span>Pending index</span>
            <InputNumber
              value={draft.maxPendingIndexCount}
              min={0}
              max={1000000}
              onChange={updateNumber("maxPendingIndexCount", 3)}
            />
          </label>
        </div>
        <div className="knowledge-serving__policy-checks">
          <Checkbox
            checked={draft.requireCurrentRelease}
            onChange={(checked) =>
              setDraft((current) => ({ ...current, requireCurrentRelease: Boolean(checked) }))
            }
          >
            要求当前 Release
          </Checkbox>
          <Checkbox
            checked={draft.requirePassingCertification}
            onChange={(checked) =>
              setDraft((current) => ({ ...current, requirePassingCertification: Boolean(checked) }))
            }
          >
            要求质量认证通过
          </Checkbox>
        </div>
        <label className="knowledge-serving__policy-reason">
          <span>变更原因</span>
          <Textarea
            aria-label="变更原因"
            value={reason}
            disabled={saving || readOnly}
            autosize={{ minRows: 2, maxRows: 4 }}
            onChange={(value) => setReason(String(value))}
          />
        </label>
        <div className="knowledge-serving__zero-write-callout">
          <ViewModuleIcon aria-hidden="true" />
          <div>
            <strong>预览不会写入快照或推进来源状态</strong>
            <span>Preview 只返回计算后的五阶段结果，不创建 Snapshot、Event 或任何业务任务。</span>
          </div>
          <Button
            variant="outline"
            theme="primary"
            icon={<ChartLineIcon />}
            disabled={readOnly || saving}
            onClick={() => void runPreview()}
          >
            零写入预览
          </Button>
        </div>
        {preview ? <PreviewResult preview={preview} /> : null}
        <div className="knowledge-serving__policy-footnote">
          Current policy ·{" "}
          {policy?.policy_digest ? policy.policy_digest.slice(0, 12) + "…" : "未返回"} ·{" "}
          {dateLabel(policy?.created_at)}
        </div>
      </div>
    </Dialog>
  );
}
