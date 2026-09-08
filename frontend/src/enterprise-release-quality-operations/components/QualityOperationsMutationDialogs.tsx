import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Alert, Button, Dialog, Form, Tag, Textarea } from "tdesign-react";

import type { OperationsReleaseRole, RecertificationTrigger } from "../model/operationsModel";

export type QualityOperationsMutationMode =
  "acknowledge" | "resolve" | "suppress" | "queue" | "cancel";

export interface QualityOperationsQueueAuthoritySummary {
  releaseId: string;
  channelId: string;
  releaseRole: OperationsReleaseRole;
  baselineId: string;
  policyId: string;
  expectedPolicyRevision: number;
  sloPolicyId: string;
  expectedSloPolicyRevision: number;
  expectedManifestDigest: string;
  expectedEvidenceDigest: string | null;
  expectedChannelRevision: number;
  trigger: RecertificationTrigger;
}

export interface QualityOperationsAlertMutationPayload {
  expectedRevision: number;
  reason: string;
  comment?: string;
}

export interface QualityOperationsSuppressMutationPayload extends QualityOperationsAlertMutationPayload {
  suppressedUntil: string;
}

export interface QualityOperationsCancelMutationPayload {
  expectedStatus: string;
  reason: string;
  comment?: string;
}

export interface QualityOperationsQueueMutationPayload extends QualityOperationsQueueAuthoritySummary {
  reason: string;
  comment?: string;
}

export type QualityOperationsMutationPayload =
  | QualityOperationsAlertMutationPayload
  | QualityOperationsSuppressMutationPayload
  | QualityOperationsCancelMutationPayload
  | QualityOperationsQueueMutationPayload;

export type QualityOperationsMutationSubmitPayload =
  | ({ mode: "acknowledge" } & QualityOperationsAlertMutationPayload)
  | ({ mode: "resolve" } & QualityOperationsAlertMutationPayload)
  | ({ mode: "suppress" } & QualityOperationsSuppressMutationPayload)
  | ({ mode: "queue" } & QualityOperationsQueueMutationPayload)
  | ({ mode: "cancel" } & QualityOperationsCancelMutationPayload);

type MutationCallback<T> = (payload: T) => void | Promise<unknown>;

export interface QualityOperationsMutationDialogsProps {
  visible: boolean;
  mode: QualityOperationsMutationMode;
  resourceLabel?: string | null;
  revision?: number | null;
  status?: string | null;
  readOnly?: boolean;
  loading?: boolean;
  error?: string | null;
  queueAuthority?: QualityOperationsQueueAuthoritySummary | null;
  onClose: () => void;
  onAcknowledge?: MutationCallback<QualityOperationsAlertMutationPayload>;
  onAcknowledgeAlert?: MutationCallback<QualityOperationsAlertMutationPayload>;
  onResolve?: MutationCallback<QualityOperationsAlertMutationPayload>;
  onResolveAlert?: MutationCallback<QualityOperationsAlertMutationPayload>;
  onSuppress?: MutationCallback<QualityOperationsSuppressMutationPayload>;
  onSuppressAlert?: MutationCallback<QualityOperationsSuppressMutationPayload>;
  onQueue?: MutationCallback<QualityOperationsQueueMutationPayload>;
  onQueueRecertification?: MutationCallback<QualityOperationsQueueMutationPayload>;
  onCancel?: MutationCallback<QualityOperationsCancelMutationPayload>;
  onCancelRecertification?: MutationCallback<QualityOperationsCancelMutationPayload>;
  onSubmit?: MutationCallback<QualityOperationsMutationSubmitPayload>;
}

interface MutationModeConfig {
  title: string;
  confirmLabel: string;
  description: string;
  requiresRevision: boolean;
  requiresStatus: boolean;
  requiresSuppressedUntil: boolean;
  showsQueueAuthority: boolean;
}

const MODE_CONFIG: Record<QualityOperationsMutationMode, MutationModeConfig> = {
  acknowledge: {
    title: "确认质量告警",
    confirmLabel: "确认告警",
    description: "确认代表运营人员接手处理，不会改变当前 Quality Gate 事实。",
    requiresRevision: true,
    requiresStatus: false,
    requiresSuppressedUntil: false,
    showsQueueAuthority: false,
  },
  resolve: {
    title: "解决质量告警",
    confirmLabel: "解决告警",
    description: "解决代表当前告警周期已完成处置；历史 Observation 仍保持不可变。",
    requiresRevision: true,
    requiresStatus: false,
    requiresSuppressedUntil: false,
    showsQueueAuthority: false,
  },
  suppress: {
    title: "抑制质量告警",
    confirmLabel: "抑制告警",
    description: "抑制只改变告警生命周期，不会绕过 Quality Gate 或修改 Release。",
    requiresRevision: true,
    requiresStatus: false,
    requiresSuppressedUntil: true,
    showsQueueAuthority: false,
  },
  queue: {
    title: "排队再认证",
    confirmLabel: "排队再认证",
    description: "操作只会创建持久再认证任务，不会立即认证、发布或修改 Judgment。",
    requiresRevision: false,
    requiresStatus: false,
    requiresSuppressedUntil: false,
    showsQueueAuthority: true,
  },
  cancel: {
    title: "取消再认证任务",
    confirmLabel: "取消再认证",
    description: "取消只结束当前非终态任务，不会删除已产生的运营审计事实。",
    requiresRevision: false,
    requiresStatus: true,
    requiresSuppressedUntil: false,
    showsQueueAuthority: false,
  },
};

const PROTECTED_KEY_PATTERN =
  /(?:token|password|credential|ticket|query|secret|authorization|api[_-]?key|idempotency[_-]?key)/i;
const PROTECTED_VALUE_PATTERN =
  /(?:\b(?:token|password|credential|ticket|query|secret|authorization)\b\s*[:=]?|api[_-]?key\s*[:=]?|idempotency[_-]?key\s*[:=]?|bearer\s+|opaque-secret|opaque-ticket|raw-ticket|secret:\/\/|sk_(?:live|test)|(?:mysql|mariadb|postgres(?:ql)?|redis(?:s)?):\/\/)/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/i;
const RELEASE_ROLES = new Set<OperationsReleaseRole>(["active", "pinned"]);
const RECERTIFICATION_TRIGGERS = new Set<RecertificationTrigger>([
  "manual",
  "certification_warning",
  "certification_expired",
  "stale_evidence",
  "alert_escalation",
]);

const MODE_VALIDATION_MESSAGES = {
  reasonRequired: "请填写操作原因。",
  reasonUnsafe: "操作原因包含受保护信息，已拒绝。",
  commentUnsafe: "备注包含受保护信息，已拒绝。",
  suppressedUntilRequired: "请填写抑制截止时间。",
  suppressedUntilInvalid: "抑制截止时间必须是未来 ISO 时间。",
  authorityUnavailable: "服务端 authority 摘要不可用，暂不能排队再认证。",
  revisionUnavailable: "服务端 revision 不可用，暂不能提交。",
  statusUnavailable: "服务端 status 不可用，暂不能提交。",
  callbackUnavailable: "当前操作未配置可用的提交回调。",
  operationFailed: "操作未提交，请由上层重新加载后重试。",
} as const;

function containsProtectedContent(value: unknown, keyHint = "", depth = 0): boolean {
  if (depth > 6) return true;
  if (PROTECTED_KEY_PATTERN.test(keyHint)) return true;
  if (typeof value === "string") return PROTECTED_VALUE_PATTERN.test(value);
  if (Array.isArray(value))
    return value.some((item) => containsProtectedContent(item, keyHint, depth + 1));
  if (typeof value !== "object" || value === null) return false;
  return Object.entries(value).some(([key, item]) =>
    containsProtectedContent(item, key, depth + 1),
  );
}

function unsafeText(value: string): boolean {
  return value.length > 512 || /[\r\n\0]/.test(value) || PROTECTED_VALUE_PATTERN.test(value);
}

function safeText(value: unknown, maximum = 512): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || /[\r\n\0]/.test(normalized)) return null;
  if (PROTECTED_VALUE_PATTERN.test(normalized)) return null;
  return normalized;
}

function displayText(value: unknown, fallback: string, maximum = 256): string {
  return safeText(value, maximum) ?? fallback;
}

function exactRevision(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

function safeStatus(value: string | null | undefined): string | null {
  return safeText(value, 64);
}

function futureIsoTimestamp(value: string): boolean {
  const normalized = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(normalized)) {
    return false;
  }
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) && timestamp > Date.now();
}

function validateQueueAuthority(
  value: QualityOperationsQueueAuthoritySummary | null | undefined,
): QualityOperationsQueueAuthoritySummary | null {
  if (!value || containsProtectedContent(value)) return null;
  const stringFields = [
    value.releaseId,
    value.channelId,
    value.baselineId,
    value.policyId,
    value.sloPolicyId,
  ];
  if (stringFields.some((item) => safeText(item, 128) === null)) return null;
  if (!RELEASE_ROLES.has(value.releaseRole)) return null;
  if (!RECERTIFICATION_TRIGGERS.has(value.trigger)) return null;
  if (exactRevision(value.expectedPolicyRevision) === null) return null;
  if (exactRevision(value.expectedSloPolicyRevision) === null) return null;
  if (exactRevision(value.expectedChannelRevision) === null) return null;
  if (!SHA256_PATTERN.test(value.expectedManifestDigest)) return null;
  if (value.expectedEvidenceDigest !== null && !SHA256_PATTERN.test(value.expectedEvidenceDigest)) {
    return null;
  }
  return {
    ...value,
    releaseId: safeText(value.releaseId, 128)!,
    channelId: safeText(value.channelId, 128)!,
    baselineId: safeText(value.baselineId, 128)!,
    policyId: safeText(value.policyId, 128)!,
    sloPolicyId: safeText(value.sloPolicyId, 128)!,
  };
}

function digestLabel(value: string | null): string {
  return value === null ? "未返回" : `sha256 · ${value}`;
}

function queueAuthorityRows(authority: QualityOperationsQueueAuthoritySummary) {
  return [
    ["Release", authority.releaseId],
    ["Channel", authority.channelId],
    ["Release role", authority.releaseRole],
    ["Baseline", authority.baselineId],
    ["Policy", `${authority.policyId} · revision ${authority.expectedPolicyRevision}`],
    ["SLO Policy", `${authority.sloPolicyId} · revision ${authority.expectedSloPolicyRevision}`],
    ["Manifest digest", digestLabel(authority.expectedManifestDigest)],
    ["Evidence digest", digestLabel(authority.expectedEvidenceDigest)],
    ["Channel revision", String(authority.expectedChannelRevision)],
    ["Trigger", authority.trigger],
  ] as const;
}

function QueueAuthoritySummary({
  authority,
}: {
  authority: QualityOperationsQueueAuthoritySummary | null;
}) {
  if (!authority) {
    return (
      <section aria-label="服务端 authority 摘要" role="status">
        <Tag theme="warning" variant="light-outline">
          authority unavailable
        </Tag>
        <p>{MODE_VALIDATION_MESSAGES.authorityUnavailable}</p>
      </section>
    );
  }

  return (
    <section aria-label="服务端 authority 摘要" role="status">
      <Tag theme="primary" variant="light-outline">
        服务端 authority 摘要
      </Tag>
      <dl>
        {queueAuthorityRows(authority).map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              <code>{value}</code>
            </dd>
          </div>
        ))}
      </dl>
      <p>以上 revision 与 digest 由服务端提供；浏览器不会重新计算或改写 authority。</p>
    </section>
  );
}

export default function QualityOperationsMutationDialogs({
  visible,
  mode,
  resourceLabel,
  revision = null,
  status = null,
  readOnly = false,
  loading = false,
  error = null,
  queueAuthority = null,
  onClose,
  onAcknowledge,
  onAcknowledgeAlert,
  onResolve,
  onResolveAlert,
  onSuppress,
  onSuppressAlert,
  onQueue,
  onQueueRecertification,
  onCancel,
  onCancelRecertification,
  onSubmit,
}: QualityOperationsMutationDialogsProps) {
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [suppressedUntil, setSuppressedUntil] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [protectedRejected, setProtectedRejected] = useState(false);
  const [callbackPending, setCallbackPending] = useState(false);
  const closeDispatchedRef = useRef(false);
  const config = MODE_CONFIG[mode];
  const busy = loading || callbackPending;
  const formDisabled = readOnly || busy;
  const authority = useMemo(() => validateQueueAuthority(queueAuthority), [queueAuthority]);
  const safeResourceLabel = displayText(resourceLabel, "未指定资源", 256);
  const safeStatusValue = safeStatus(status);
  const safeRevisionValue = exactRevision(revision);
  const operationContextReady =
    (!config.requiresRevision || safeRevisionValue !== null) &&
    (!config.requiresStatus || safeStatusValue !== null) &&
    (!config.showsQueueAuthority || authority !== null);
  const formKey = [
    mode,
    visible ? "visible" : "hidden",
    safeResourceLabel,
    safeRevisionValue ?? "unavailable",
    safeStatusValue ?? "unavailable",
  ].join(":");

  useLayoutEffect(() => {
    if (!visible) return;
    setReason("");
    setComment("");
    setSuppressedUntil("");
    setValidation(null);
    setSubmissionError(null);
    setProtectedRejected(false);
    setCallbackPending(false);
  }, [mode, resourceLabel, revision, status, visible]);

  useEffect(() => {
    closeDispatchedRef.current = false;
  }, [mode, visible]);

  const closeOnce = useCallback(() => {
    if (closeDispatchedRef.current || busy) return;
    closeDispatchedRef.current = true;
    onClose();
  }, [busy, onClose]);

  const rejectProtectedInput = useCallback((message: string) => {
    setProtectedRejected(true);
    setValidation(message);
    setSubmissionError(null);
  }, []);

  const acceptText = useCallback(
    (
      value: string,
      setter: (next: string) => void,
      unsafeMessage: string,
      element?: HTMLTextAreaElement,
    ) => {
      if (unsafeText(value)) {
        if (element) element.value = "";
        setter("");
        rejectProtectedInput(unsafeMessage);
        return;
      }
      setter(value);
      setValidation(null);
      setSubmissionError(null);
    },
    [rejectProtectedInput],
  );

  const handleProtectedTextCapture = useCallback(
    (event: FormEvent<HTMLDivElement>, setter: (next: string) => void, unsafeMessage: string) => {
      const target = event.target;
      if (!(target instanceof HTMLTextAreaElement) || !unsafeText(target.value)) return;
      event.stopPropagation();
      target.value = "";
      setter("");
      rejectProtectedInput(unsafeMessage);
    },
    [rejectProtectedInput],
  );

  const buildPayload = useCallback((): QualityOperationsMutationPayload | null => {
    const safeReason = safeText(reason);
    if (!safeReason) {
      setValidation(
        reason.trim()
          ? MODE_VALIDATION_MESSAGES.reasonUnsafe
          : MODE_VALIDATION_MESSAGES.reasonRequired,
      );
      return null;
    }

    const safeComment = comment.trim() ? safeText(comment) : null;
    if (comment.trim() && !safeComment) {
      setValidation(MODE_VALIDATION_MESSAGES.commentUnsafe);
      return null;
    }
    if (protectedRejected) {
      setValidation(MODE_VALIDATION_MESSAGES.reasonUnsafe);
      return null;
    }

    if (config.requiresRevision && safeRevisionValue === null) {
      setValidation(MODE_VALIDATION_MESSAGES.revisionUnavailable);
      return null;
    }
    if (config.requiresStatus && safeStatusValue === null) {
      setValidation(MODE_VALIDATION_MESSAGES.statusUnavailable);
      return null;
    }
    if (config.requiresSuppressedUntil && !suppressedUntil.trim()) {
      setValidation(MODE_VALIDATION_MESSAGES.suppressedUntilRequired);
      return null;
    }
    if (config.requiresSuppressedUntil && !futureIsoTimestamp(suppressedUntil)) {
      setValidation(MODE_VALIDATION_MESSAGES.suppressedUntilInvalid);
      return null;
    }
    if (config.showsQueueAuthority && !authority) {
      setValidation(MODE_VALIDATION_MESSAGES.authorityUnavailable);
      return null;
    }

    const commentField = safeComment ? { comment: safeComment } : {};
    switch (mode) {
      case "acknowledge":
      case "resolve":
        return {
          expectedRevision: safeRevisionValue!,
          reason: safeReason,
          ...commentField,
        };
      case "suppress":
        return {
          expectedRevision: safeRevisionValue!,
          reason: safeReason,
          suppressedUntil: suppressedUntil.trim(),
          ...commentField,
        };
      case "queue":
        return {
          ...authority!,
          reason: safeReason,
          ...commentField,
        };
      case "cancel":
        return {
          expectedStatus: safeStatusValue!,
          reason: safeReason,
          ...commentField,
        };
      default:
        return null;
    }
  }, [
    authority,
    comment,
    config,
    mode,
    protectedRejected,
    reason,
    safeRevisionValue,
    safeStatusValue,
    suppressedUntil,
  ]);

  const submit = useCallback(async () => {
    if (formDisabled || !operationContextReady || protectedRejected) {
      if (protectedRejected) {
        setValidation(MODE_VALIDATION_MESSAGES.reasonUnsafe);
      } else if (!operationContextReady && config.showsQueueAuthority) {
        setValidation(MODE_VALIDATION_MESSAGES.authorityUnavailable);
      }
      return;
    }
    const payload = buildPayload();
    if (!payload) return;

    const genericPayload = { mode, ...payload } as QualityOperationsMutationSubmitPayload;
    setSubmissionError(null);
    setValidation(null);
    setCallbackPending(true);
    try {
      switch (mode) {
        case "acknowledge": {
          const callback = onAcknowledge ?? onAcknowledgeAlert;
          if (callback) await callback(payload as QualityOperationsAlertMutationPayload);
          else if (onSubmit) await onSubmit(genericPayload);
          else setValidation(MODE_VALIDATION_MESSAGES.callbackUnavailable);
          break;
        }
        case "resolve": {
          const callback = onResolve ?? onResolveAlert;
          if (callback) await callback(payload as QualityOperationsAlertMutationPayload);
          else if (onSubmit) await onSubmit(genericPayload);
          else setValidation(MODE_VALIDATION_MESSAGES.callbackUnavailable);
          break;
        }
        case "suppress": {
          const callback = onSuppress ?? onSuppressAlert;
          if (callback) await callback(payload as QualityOperationsSuppressMutationPayload);
          else if (onSubmit) await onSubmit(genericPayload);
          else setValidation(MODE_VALIDATION_MESSAGES.callbackUnavailable);
          break;
        }
        case "queue": {
          const callback = onQueue ?? onQueueRecertification;
          if (callback) await callback(payload as QualityOperationsQueueMutationPayload);
          else if (onSubmit) await onSubmit(genericPayload);
          else setValidation(MODE_VALIDATION_MESSAGES.callbackUnavailable);
          break;
        }
        case "cancel": {
          const callback = onCancel ?? onCancelRecertification;
          if (callback) await callback(payload as QualityOperationsCancelMutationPayload);
          else if (onSubmit) await onSubmit(genericPayload);
          else setValidation(MODE_VALIDATION_MESSAGES.callbackUnavailable);
          break;
        }
      }
    } catch {
      setSubmissionError(MODE_VALIDATION_MESSAGES.operationFailed);
    } finally {
      setCallbackPending(false);
    }
  }, [
    buildPayload,
    config.showsQueueAuthority,
    formDisabled,
    mode,
    onAcknowledge,
    onAcknowledgeAlert,
    onCancel,
    onCancelRecertification,
    onQueue,
    onQueueRecertification,
    onResolve,
    onResolveAlert,
    onSubmit,
    onSuppress,
    onSuppressAlert,
    operationContextReady,
  ]);

  const safeError = displayText(error, "操作事实不可用，请重新加载后重试.", 512);
  const visibleError = error ? safeError : submissionError;
  const canSubmit = !formDisabled && operationContextReady && !protectedRejected;
  const confirmDisabled = !canSubmit;

  return (
    <Dialog
      visible={visible}
      header={config.title}
      width={620}
      mode="modal"
      placement="center"
      destroyOnClose
      preventScrollThrough
      closeOnEscKeydown={!busy}
      closeOnOverlayClick={!busy}
      closeBtn={!busy}
      confirmOnEnter={false}
      confirmBtn={null}
      cancelBtn={null}
      confirmLoading={busy}
      onClose={closeOnce}
      footer={
        <div role="group" aria-label="质量运营变更操作">
          <Button tag="button" variant="outline" disabled={busy} onClick={closeOnce}>
            取消
          </Button>
          <Button
            tag="button"
            theme="primary"
            loading={busy}
            disabled={confirmDisabled}
            onClick={() => void submit()}
          >
            {config.confirmLabel}
          </Button>
        </div>
      }
      {...({ role: "dialog", "aria-modal": "true", "aria-label": config.title } as Record<
        string,
        unknown
      >)}
    >
      <section aria-label="质量运营变更上下文" role="status">
        <Tag theme="primary" variant="light-outline">
          {mode.toUpperCase()} / QUALITY OPERATIONS
        </Tag>
        <p>{config.description}</p>
        <dl>
          <div>
            <dt>资源</dt>
            <dd>{safeResourceLabel}</dd>
          </div>
          <div>
            <dt>revision</dt>
            <dd>
              <input
                id="quality-operations-mutation-revision"
                aria-label="revision"
                className="quality-operations-mutation-readonly-fact"
                value={safeRevisionValue === null ? "未返回" : String(safeRevisionValue)}
                readOnly
                disabled={formDisabled}
                onChange={() => undefined}
              />
            </dd>
          </div>
          <div>
            <dt>status</dt>
            <dd>
              <input
                id="quality-operations-mutation-status"
                aria-label="status"
                className="quality-operations-mutation-readonly-fact"
                value={safeStatusValue ?? "未返回"}
                readOnly
                disabled={formDisabled}
                onChange={() => undefined}
              />
            </dd>
          </div>
        </dl>
      </section>

      {config.showsQueueAuthority ? <QueueAuthoritySummary authority={authority} /> : null}

      {visibleError ? (
        <div role="alert" aria-live="assertive">
          <Alert theme="error" title="操作未提交" message={visibleError} />
        </div>
      ) : null}
      {validation ? (
        <div role="alert" aria-live="assertive">
          <Alert theme="warning" title="请检查变更信息" message={validation} />
        </div>
      ) : null}
      {readOnly ? (
        <div role="status" aria-live="polite">
          <Alert
            theme="info"
            title="只读事实"
            message="当前权限或 scope 不允许提交质量运营变更。"
          />
        </div>
      ) : null}

      <Form key={formKey} layout="vertical" colon={false} aria-label="质量运营变更表单">
        <Form.FormItem label="操作原因 / reason" requiredMark>
          <div
            onChangeCapture={(event) =>
              handleProtectedTextCapture(event, setReason, MODE_VALIDATION_MESSAGES.reasonUnsafe)
            }
            onInputCapture={(event) =>
              handleProtectedTextCapture(event, setReason, MODE_VALIDATION_MESSAGES.reasonUnsafe)
            }
          >
            <Textarea
              id="quality-operations-mutation-reason"
              aria-label="reason"
              value={reason}
              disabled={formDisabled}
              placeholder="说明本次质量运营状态变更的业务原因"
              maxlength={512}
              autosize={{ minRows: 3, maxRows: 6 }}
              onChange={(value, context) =>
                acceptText(
                  String(value),
                  setReason,
                  MODE_VALIDATION_MESSAGES.reasonUnsafe,
                  context?.e?.currentTarget,
                )
              }
            />
          </div>
        </Form.FormItem>
        <Form.FormItem label="安全备注 / comment">
          <div
            onChangeCapture={(event) =>
              handleProtectedTextCapture(event, setComment, MODE_VALIDATION_MESSAGES.commentUnsafe)
            }
            onInputCapture={(event) =>
              handleProtectedTextCapture(event, setComment, MODE_VALIDATION_MESSAGES.commentUnsafe)
            }
          >
            <Textarea
              id="quality-operations-mutation-comment"
              aria-label="comment"
              value={comment}
              disabled={formDisabled}
              placeholder="可选；不要填写 ticket、credential、query 或其他受保护信息"
              maxlength={512}
              autosize={{ minRows: 2, maxRows: 4 }}
              onChange={(value, context) =>
                acceptText(
                  String(value),
                  setComment,
                  MODE_VALIDATION_MESSAGES.commentUnsafe,
                  context?.e?.currentTarget,
                )
              }
            />
          </div>
        </Form.FormItem>
        {config.requiresSuppressedUntil ? (
          <Form.FormItem label="抑制截止时间 / suppressedUntil" requiredMark>
            <div>
              <input
                id="quality-operations-mutation-suppressed-until"
                aria-label="suppressedUntil"
                className="quality-operations-mutation-text-input"
                type="text"
                value={suppressedUntil}
                disabled={formDisabled}
                placeholder="例如 2026-09-01T10:00:00.000Z"
                maxLength={64}
                onChange={(event) => {
                  const next = event.currentTarget.value;
                  if (PROTECTED_VALUE_PATTERN.test(next) || /[\r\n\0]/.test(next)) {
                    event.currentTarget.value = "";
                    setSuppressedUntil("");
                    rejectProtectedInput(MODE_VALIDATION_MESSAGES.reasonUnsafe);
                    return;
                  }
                  setSuppressedUntil(next);
                  setValidation(null);
                  setSubmissionError(null);
                }}
              />
            </div>
          </Form.FormItem>
        ) : null}
      </Form>
      <p>
        提交只会调用上层传入的安全 callback；组件不会访问 API、生成 Idempotency-Key 或计算 digest。
      </p>
    </Dialog>
  );
}
