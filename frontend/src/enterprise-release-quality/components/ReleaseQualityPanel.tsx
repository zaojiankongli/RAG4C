import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Button, Card, Dialog, Input, PrimaryTable, Select, Tag, Textarea } from "tdesign-react";
import { CheckCircleIcon, HistoryIcon, SecuredIcon } from "tdesign-icons-react";
import type { CertifyReleaseQualityInput, RequestQualityWaiverInput } from "../api/qualityApi";
import { navigateToApprovalRequest } from "../../enterprise-approval/approvalRoute";
import type {
  QualityBaseline,
  QualityCertification,
  QualityGate,
  QualityMutationOutcome,
  QualityPolicy,
} from "../model/qualityModel";
import QualityGateStrip from "./QualityGateStrip";
import "../release-quality.css";

export interface ReleaseQualityPanelProps {
  gateStatus: "idle" | "loading" | "ready" | "error";
  gate: QualityGate | null;
  policies: QualityPolicy[];
  baselines: QualityBaseline[];
  certifications: QualityCertification[];
  certificationStatus: "idle" | "loading" | "ready" | "error";
  certificationError?: Error | null;
  readOnly: boolean;
  saving: boolean;
  error: Error | null;
  mutationError?: Error | null;
  mutationOutcome?: QualityMutationOutcome | null;
  onLoadCertifications: () => void | Promise<void>;
  onCertify: (input: CertifyReleaseQualityInput) => void | Promise<unknown>;
  onRequestWaiver?: (input: RequestQualityWaiverInput) => void | Promise<unknown>;
}

function percent(value: number | null): string {
  return value == null ? "未返回" : `${(value / 100).toFixed(2)}%`;
}

function score(value: number | null): string {
  return value == null ? "未返回" : (value / 1000).toFixed(3);
}

function dateLabel(value: string | null): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  return Number.isNaN(parsed)
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(parsed);
}

function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>, close: () => void) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    close();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
  if (!focusable.length) return;
  const first = focusable[0]!;
  const last = focusable[focusable.length - 1]!;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export default function ReleaseQualityPanel({
  gateStatus,
  gate,
  baselines,
  certifications,
  certificationStatus,
  certificationError,
  readOnly,
  saving,
  error,
  mutationError,
  mutationOutcome,
  onLoadCertifications,
  onCertify,
  onRequestWaiver,
}: ReleaseQualityPanelProps) {
  const [dialogVisible, setDialogVisible] = useState(false);
  const [waiverVisible, setWaiverVisible] = useState(false);
  const policy = gate?.policy ?? null;
  const currentCertification = gate?.certification ?? null;
  const [baselineId, setBaselineId] = useState<string>(
    currentCertification?.baseline_id ?? baselines[0]?.id ?? "",
  );
  const [reason, setReason] = useState("");
  const [approvalPolicyId, setApprovalPolicyId] = useState("");
  const [waiverExpiresAt, setWaiverExpiresAt] = useState("");
  const [waiverReason, setWaiverReason] = useState("");
  const certifyTriggerRef = useRef<HTMLButtonElement | null>(null);
  const waiverTriggerRef = useRef<HTMLButtonElement | null>(null);
  const certifyDialogRef = useRef<HTMLDivElement | null>(null);
  const waiverDialogRef = useRef<HTMLDivElement | null>(null);
  const metrics = useMemo(
    () => [
      {
        key: "experiments",
        metric: "Experiments",
        required: policy ? `≥ ${policy.min_experiment_count}` : "未返回",
        observed: currentCertification ? String(currentCertification.experiment_count) : "未返回",
        verdict:
          !policy || !currentCertification
            ? "unknown"
            : currentCertification.experiment_count >= policy.min_experiment_count
              ? "passed"
              : "failed",
      },
      {
        key: "coverage",
        metric: "Judgment coverage",
        required: policy ? percent(policy.min_judgment_coverage_bps) : "未返回",
        observed: percent(currentCertification?.judgment_coverage_bps ?? null),
        verdict:
          !policy || currentCertification?.judgment_coverage_bps == null
            ? "unknown"
            : currentCertification.judgment_coverage_bps >= policy.min_judgment_coverage_bps
              ? "passed"
              : "failed",
      },
      {
        key: "agreement",
        metric: "Exact agreement",
        required: policy ? percent(policy.min_exact_agreement_bps) : "未返回",
        observed: percent(currentCertification?.exact_agreement_bps ?? null),
        verdict:
          !policy || currentCertification?.exact_agreement_bps == null
            ? "unknown"
            : currentCertification.exact_agreement_bps >= policy.min_exact_agreement_bps
              ? "passed"
              : "failed",
      },
      {
        key: "score",
        metric: "Mean score",
        required: policy ? score(policy.min_mean_score_milli) : "未返回",
        observed: score(currentCertification?.mean_score_milli ?? null),
        verdict:
          !policy || currentCertification?.mean_score_milli == null
            ? "unknown"
            : currentCertification.mean_score_milli >= policy.min_mean_score_milli
              ? "passed"
              : "failed",
      },
      {
        key: "conflicts",
        metric: "Conflicting results",
        required: policy ? `≤ ${policy.max_conflicting_results}` : "未返回",
        observed: currentCertification
          ? String(currentCertification.conflicting_results)
          : "未返回",
        verdict:
          !policy || !currentCertification
            ? "unknown"
            : currentCertification.conflicting_results <= policy.max_conflicting_results
              ? "passed"
              : "failed",
      },
    ],
    [currentCertification, policy],
  );
  const canCertify = Boolean(
    !readOnly && !saving && gate?.channel_id && gate.channel_revision && policy && baselineId,
  );

  useEffect(() => {
    if (!dialogVisible) return;
    const timer = window.setTimeout(() => {
      certifyDialogRef.current
        ?.querySelector<HTMLElement>("input, textarea, button:not([disabled])")
        ?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [dialogVisible]);

  useEffect(() => {
    if (!waiverVisible) return;
    const timer = window.setTimeout(() => {
      waiverDialogRef.current
        ?.querySelector<HTMLElement>("input, textarea, button:not([disabled])")
        ?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [waiverVisible]);

  const closeCertifyDialog = () => {
    setDialogVisible(false);
    window.setTimeout(() => certifyTriggerRef.current?.focus(), 0);
  };

  const closeWaiverDialog = () => {
    setWaiverVisible(false);
    window.setTimeout(() => waiverTriggerRef.current?.focus(), 0);
  };

  const submit = () => {
    if (!canCertify || !gate?.channel_id || !gate.channel_revision || !policy || !reason.trim()) {
      return;
    }
    void onCertify({
      channelId: gate.channel_id,
      baselineId,
      policyId: policy.id,
      expectedPolicyRevision: policy.revision,
      expectedChannelRevision: gate.channel_revision,
      reason: reason.trim(),
    });
    setDialogVisible(false);
  };
  const canRequestWaiver = Boolean(
    !readOnly &&
    !saving &&
    onRequestWaiver &&
    gate?.channel_id &&
    gate.channel_revision &&
    policy &&
    gate.state === "blocked",
  );

  const submitWaiver = () => {
    if (
      !canRequestWaiver ||
      !onRequestWaiver ||
      !gate?.channel_id ||
      !gate.channel_revision ||
      !policy ||
      !approvalPolicyId.trim() ||
      !waiverExpiresAt.trim() ||
      !waiverReason.trim()
    ) {
      return;
    }
    void onRequestWaiver({
      channelId: gate.channel_id,
      policyId: policy.id,
      expectedPolicyRevision: policy.revision,
      expectedChannelRevision: gate.channel_revision,
      approvalPolicyId: approvalPolicyId.trim(),
      requestedExpiresAt: waiverExpiresAt.trim(),
      reason: waiverReason.trim(),
    });
    setWaiverVisible(false);
  };

  return (
    <section className="release-quality-panel" aria-label="Release 质量认证">
      <QualityGateStrip status={gateStatus} gate={gate} error={error} />
      {mutationOutcome || mutationError ? (
        <div
          className={`release-quality-panel__mutation-notice release-quality-panel__mutation-notice--${
            mutationError
              ? "error"
              : mutationOutcome?.state === "approval_required"
                ? "approval"
                : "success"
          }`}
          role="status"
        >
          <div>
            <strong>
              {mutationError
                ? "质量操作未完成"
                : mutationOutcome?.state === "approval_required"
                  ? "豁免审批已提交"
                  : mutationOutcome?.message || "质量事实已更新"}
            </strong>
            <span>
              {mutationError
                ? "服务端拒绝了本次质量操作，请核对当前 revision fence 后重试。"
                : mutationOutcome?.approval_request_id
                  ? `Approval Request ${mutationOutcome.approval_request_id}`
                  : mutationOutcome?.resource_id || "操作结果已持久化"}
            </span>
          </div>
          {mutationOutcome?.approval_request_id ? (
            <Button
              tag="button"
              variant="outline"
              size="small"
              onClick={() => navigateToApprovalRequest(mutationOutcome.approval_request_id!)}
            >
              查看审批
            </Button>
          ) : null}
        </div>
      ) : null}
      <header className="release-quality-panel__header">
        <div>
          <span className="release-quality-kicker">CERTIFICATION AUTHORITY / IMMUTABLE</span>
          <h3>Release 质量认证</h3>
          <p>将 Retrieval Experiment 与 reviewer judgment 固化为可审计发布证据。</p>
        </div>
        <div className="release-quality-panel__actions">
          <Tag
            theme={
              gate?.state === "passed"
                ? "success"
                : gate?.state === "waived"
                  ? "warning"
                  : "default"
            }
            variant="light-outline"
          >
            {gate?.state === "passed" ? "已认证" : gate?.state === "waived" ? "已豁免" : "待认证"}
          </Tag>
          {gate?.policy && gate.state !== "passed" && gate.state !== "waived" ? (
            <Button
              tag="button"
              variant="outline"
              ref={waiverTriggerRef}
              disabled={!canRequestWaiver}
              onClick={() => setWaiverVisible(true)}
            >
              申请豁免
            </Button>
          ) : null}
          <Button
            tag="button"
            theme="primary"
            icon={<SecuredIcon />}
            ref={certifyTriggerRef}
            disabled={
              readOnly || !policy || gate?.state === "unavailable" || baselines.length === 0
            }
            loading={saving}
            onClick={() => setDialogVisible(true)}
          >
            {currentCertification ? "重新认证" : "创建认证"}
          </Button>
        </div>
      </header>

      {gate?.state === "unavailable" ? (
        <Card className="release-quality-panel__unavailable" size="small" bordered>
          <strong>无法创建认证</strong>
          <span>{gate.reason}</span>
        </Card>
      ) : null}

      <div className="release-quality-panel__authority-rail" aria-label="质量认证证据链">
        <div>
          <span>01</span>
          <strong>Policy</strong>
          <small>{policy ? `${policy.name} · R${policy.revision}` : "未匹配策略"}</small>
        </div>
        <div>
          <span>02</span>
          <strong>Baseline</strong>
          <small>
            {currentCertification
              ? baselines.find((item) => item.id === currentCertification.baseline_id)?.name ||
                currentCertification.baseline_id
              : baselines[0]?.name || "未选择 Baseline"}
          </small>
        </div>
        <div>
          <span>03</span>
          <strong>Evidence</strong>
          <small>
            {currentCertification
              ? `${currentCertification.experiment_count} experiments / ${currentCertification.judgment_count} judgments`
              : "等待认证"}
          </small>
        </div>
        <div>
          <span>04</span>
          <strong>Publish Gate</strong>
          <small>
            {gate?.state === "passed"
              ? `已通过 · 有效至 ${dateLabel(gate.certification?.valid_until ?? null)}`
              : gate?.state === "waived"
                ? `已豁免 · 有效至 ${dateLabel(gate.waiver?.expires_at ?? null)}`
                : (gate?.state ?? "未返回")}
          </small>
        </div>
      </div>

      <Card className="release-quality-panel__metrics" size="small" bordered>
        <div className="release-quality-panel__card-heading">
          <div>
            <strong>Policy threshold vs observed</strong>
            <span>持久指标使用 basis points 与 milli-score，避免跨数据库浮点漂移。</span>
          </div>
          {currentCertification ? (
            <Tag
              icon={<CheckCircleIcon />}
              theme={currentCertification.status === "passed" ? "success" : "danger"}
              variant="light-outline"
            >
              {currentCertification.status === "passed" ? "Passed" : "Failed"}
            </Tag>
          ) : null}
        </div>
        <div className="release-quality-panel__metric-table">
          <PrimaryTable
            rowKey="key"
            data={metrics}
            columns={[
              { colKey: "metric", title: "Metric" },
              { colKey: "required", title: "Required" },
              { colKey: "observed", title: "Observed" },
              {
                colKey: "passed",
                title: "Verdict",
                cell: ({ row }) => (
                  <Tag
                    theme={
                      row.verdict === "passed"
                        ? "success"
                        : row.verdict === "failed"
                          ? "danger"
                          : "default"
                    }
                    variant="light-outline"
                  >
                    {row.verdict === "passed"
                      ? "通过"
                      : row.verdict === "failed"
                        ? "未满足"
                        : "未核验"}
                  </Tag>
                ),
              },
            ]}
            size="small"
            bordered={false}
            hover
          />
        </div>
        <div className="release-quality-panel__metric-cards" aria-label="质量指标移动视图">
          {metrics.map((metric) => (
            <article key={metric.key}>
              <div>
                <strong>{metric.metric}</strong>
                <Tag
                  theme={
                    metric.verdict === "passed"
                      ? "success"
                      : metric.verdict === "failed"
                        ? "danger"
                        : "default"
                  }
                  variant="light-outline"
                >
                  {metric.verdict === "passed"
                    ? "通过"
                    : metric.verdict === "failed"
                      ? "未满足"
                      : "未核验"}
                </Tag>
              </div>
              <dl>
                <div>
                  <dt>Required</dt>
                  <dd>{metric.required}</dd>
                </div>
                <div>
                  <dt>Observed</dt>
                  <dd>{metric.observed}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </Card>

      <Card className="release-quality-panel__history" size="small" bordered>
        <div className="release-quality-panel__card-heading">
          <div>
            <strong>Certification history</strong>
            <span>历史认证不可更新；新的认证通过时间与证据摘要排序。</span>
          </div>
          <Button
            tag="button"
            variant="outline"
            icon={<HistoryIcon />}
            loading={certificationStatus === "loading"}
            onClick={() => void onLoadCertifications()}
          >
            {certificationStatus === "idle" ? "加载认证历史" : "刷新历史"}
          </Button>
        </div>
        <div className="release-quality-panel__history-list">
          {certifications.length ? (
            certifications.map((item) => (
              <article key={item.id}>
                <Tag
                  theme={item.status === "passed" ? "success" : "danger"}
                  variant="light-outline"
                >
                  {item.status === "passed" ? "已通过" : "未通过"}
                </Tag>
                <div>
                  <strong>{item.id}</strong>
                  <span>
                    Policy R{item.policy_revision} · Baseline {item.baseline_id}
                  </span>
                </div>
                <time>{dateLabel(item.created_at)}</time>
              </article>
            ))
          ) : certificationStatus === "error" ? (
            <span className="release-quality-panel__empty">
              Certification history 不可用，请重试。{certificationError ? "" : ""}
            </span>
          ) : (
            <span className="release-quality-panel__empty">尚未加载 Certification history</span>
          )}
        </div>
      </Card>

      <Dialog
        visible={dialogVisible}
        destroyOnClose
        header="创建 Release 质量认证"
        confirmBtn={null}
        cancelBtn={null}
        onClose={closeCertifyDialog}
      >
        <div
          ref={certifyDialogRef}
          className="release-quality-certify-dialog"
          role="dialog"
          aria-label="创建 Release 质量认证"
          onKeyDown={(event) => handleDialogKeyDown(event, closeCertifyDialog)}
        >
          <label>
            <span>Quality Baseline</span>
            <Select
              value={baselineId}
              options={baselines.map((item) => ({
                value: item.id,
                label: `${item.name} · R${item.baseline_revision}`,
              }))}
              onChange={(value) => setBaselineId(String(value))}
            />
          </label>
          <label>
            <span>认证原因</span>
            <Textarea
              aria-label="认证原因"
              value={reason}
              maxlength={512}
              autosize={{ minRows: 3, maxRows: 5 }}
              onChange={(value) => setReason(String(value))}
            />
          </label>
          <div className="release-quality-certify-dialog__facts">
            <span>Policy {policy ? `${policy.name} · R${policy.revision}` : "未返回"}</span>
            <span>Channel R{gate?.channel_revision ?? "未返回"}</span>
          </div>
          <div className="release-quality-certify-dialog__actions">
            <Button tag="button" variant="outline" onClick={closeCertifyDialog}>
              取消
            </Button>
            <Button
              tag="button"
              theme="primary"
              disabled={!canCertify || !reason.trim()}
              onClick={submit}
            >
              提交认证
            </Button>
          </div>
        </div>
      </Dialog>

      <Dialog
        visible={waiverVisible}
        destroyOnClose
        header="申请 Release 质量豁免"
        confirmBtn={null}
        cancelBtn={null}
        onClose={closeWaiverDialog}
      >
        <div
          ref={waiverDialogRef}
          className="release-quality-certify-dialog"
          role="dialog"
          aria-label="申请 Release 质量豁免"
          onKeyDown={(event) => handleDialogKeyDown(event, closeWaiverDialog)}
        >
          <label>
            <span>Approval Policy ID</span>
            <Input
              value={approvalPolicyId}
              maxlength={64}
              onChange={(value) => setApprovalPolicyId(String(value))}
            />
          </label>
          <label>
            <span>豁免有效期</span>
            <Input
              value={waiverExpiresAt}
              placeholder="2026-08-29T08:00:00Z"
              maxlength={64}
              onChange={(value) => setWaiverExpiresAt(String(value))}
            />
          </label>
          <label>
            <span>豁免原因</span>
            <Textarea
              aria-label="豁免原因"
              value={waiverReason}
              maxlength={512}
              autosize={{ minRows: 3, maxRows: 5 }}
              onChange={(value) => setWaiverReason(String(value))}
            />
          </label>
          <div className="release-quality-certify-dialog__facts">
            <span>Release {gate?.release_id ?? "未返回"}</span>
            <span>Policy {policy ? `${policy.name} · R${policy.revision}` : "未返回"}</span>
            <span>Gate {gate?.reason ?? "未返回"}</span>
          </div>
          <div className="release-quality-certify-dialog__actions">
            <Button tag="button" variant="outline" onClick={closeWaiverDialog}>
              取消
            </Button>
            <Button
              tag="button"
              theme="primary"
              disabled={
                !canRequestWaiver ||
                !approvalPolicyId.trim() ||
                !waiverExpiresAt.trim() ||
                !waiverReason.trim()
              }
              onClick={submitWaiver}
            >
              提交豁免审批
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}
