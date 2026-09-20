import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Alert, Button, Drawer, Empty, Loading, Steps, Tabs, Tag, Timeline } from "tdesign-react";
import { ChevronRightIcon, SecuredIcon } from "tdesign-icons-react";

import {
  sanitizeOperationsMessage,
  type OperationsGateState,
  type OperationsReleaseRole,
  type OperationsSeverity,
  type QualityAuthorityProjection,
  type QualityOperationsAlert,
  type RecertificationJob,
  type RecertificationJobStatus,
} from "../model/operationsModel";

export type QualityOperationsDetailTab =
  "overview" | "timeline" | "certification" | "alert" | "recertification";

export type QualityOperationsTimelineStatus =
  "idle" | "loading" | "ready" | "empty" | "error" | "unavailable";

/** Immutable, projected facts only. Extra backend fields are tolerated but never rendered. */
export interface QualityOperationsObservationFact {
  [key: string]: unknown;
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  channel_id: string;
  release_role: OperationsReleaseRole;
  scan_run_id: string;
  observed_at: string;
  observed_by: string;
  gate_state: OperationsGateState | "passing";
  gate_reason: string;
  severity: OperationsSeverity;
  observation_digest: string;
  event?: string | null;
}

/** Server-projected lifecycle facts; arbitrary payloads are intentionally not displayed. */
export interface QualityOperationsAuditFact {
  [key: string]: unknown;
  id: string;
  occurred_at: string;
  event: string;
  actor: string;
  object_ref?: string | null;
  revision?: number | null;
  digest?: string | null;
  server_confirmed?: boolean;
}

export type QualityOperationsFocusEntry =
  HTMLElement | RefObject<HTMLElement | null> | null | undefined;

export interface QualityOperationsDetailDrawerProps {
  visible: boolean;
  authority: QualityAuthorityProjection | null;
  datasetName?: string;
  workspaceName?: string;
  riskTier?: string | null;
  channelRevision?: number | null;
  alert?: QualityOperationsAlert | null;
  job?: RecertificationJob | null;
  observations?: readonly QualityOperationsObservationFact[];
  auditFacts?: readonly QualityOperationsAuditFact[];
  timelineStatus?: QualityOperationsTimelineStatus;
  timelineError?: string | null;
  error?: string | null;
  readOnly?: boolean;
  focusEntry?: QualityOperationsFocusEntry;
  onClose: () => void;
  onOpenCertification?: (authority: QualityAuthorityProjection) => void;
  onAcknowledgeAlert?: (alert: QualityOperationsAlert) => void;
  onResolveAlert?: (alert: QualityOperationsAlert) => void;
  onSuppressAlert?: (alert: QualityOperationsAlert) => void;
  onQueueRecertification?: (authority: QualityAuthorityProjection) => void;
  onCancelRecertification?: (job: RecertificationJob) => void;
}

type TimelineEntry =
  | {
      type: "observation";
      id: string;
      timestamp: string;
      title: string;
      dotColor: string;
      observedBy: string;
      scanRunId: string;
      digest: string;
      gate: string;
      gateReason: string;
    }
  | {
      type: "audit";
      id: string;
      timestamp: string;
      title: string;
      dotColor: string;
      actor: string;
      objectRef: string;
      revision: string;
      digest: string;
      serverConfirmed: string;
    };

const DETAIL_TABS: ReadonlyArray<{ value: QualityOperationsDetailTab; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "timeline", label: "Timeline" },
  { value: "certification", label: "Certification" },
  { value: "alert", label: "Alert" },
  { value: "recertification", label: "Recertification" },
];

const JOB_STEP_ORDER: readonly RecertificationJobStatus[] = [
  "pending",
  "claimed",
  "awaiting_evidence",
  "ready_to_certify",
  "completed",
];

const JOB_STEP_LABELS: Record<RecertificationJobStatus, string> = {
  pending: "排队",
  claimed: "领取",
  awaiting_evidence: "等待证据",
  ready_to_certify: "待认证",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const JOB_STATUS_LABELS: Record<RecertificationJobStatus, string> = {
  pending: "Pending",
  claimed: "Claimed",
  awaiting_evidence: "Awaiting evidence",
  ready_to_certify: "Ready to certify",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

const GATE_LABELS: Record<OperationsGateState | "passing", string> = {
  passed: "Passed",
  passing: "Passing",
  waived: "Waived",
  not_required: "Not required",
  blocked: "Blocked",
  unavailable: "Unavailable",
};

const HORIZON_LABELS: Record<QualityAuthorityProjection["horizon_band"], string> = {
  expired: "Expired",
  "24_hours": "Within 24 hours",
  "7_days": "Within 7 days",
  "30_days": "Within 30 days",
  healthy: "Healthy",
  unavailable: "Unavailable",
};

const ALERT_TYPE_LABELS: Record<QualityOperationsAlert["alert_type"], string> = {
  certification_expiring: "Certification expiring",
  certification_expired: "Certification expired",
  certification_stale: "Certification stale",
  waiver_expiring: "Waiver expiring",
  waiver_expired: "Waiver expired",
  quality_gate_blocked: "Quality gate blocked",
  // 整张 ALERT_TYPE_LABELS 是纯英文 chrome（C 类，见 docs/42 §11.9）：
  // 半中半英的标签映射比全英文更糟，要改就整族一起改，不在本轮逐条动。
  quality_authority_unavailable: "Quality authority unavailable",
};

const ALERT_STATUS_LABELS: Record<QualityOperationsAlert["status"], string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  suppressed: "Suppressed",
};

const COLORS = {
  blue: "#0052d9",
  green: "#2ba471",
  amber: "#ed7b2f",
  red: "#d54941",
  muted: "#5f6b7a",
  ink: "#17233d",
  surface: "var(--td-bg-color-container, #ffffff)",
  border: "var(--td-component-border, #dcdcdc)",
} as const;

const bodyStyle: CSSProperties = {
  display: "grid",
  gap: 16,
  minWidth: 0,
  overflowX: "hidden",
  padding: "4px 0 24px",
};

const sectionStyle: CSSProperties = {
  background: COLORS.surface,
  border: `1px solid ${COLORS.border}`,
  borderRadius: 8,
  minWidth: 0,
  padding: 16,
};

const sectionHeadingStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  gap: 10,
  justifyContent: "space-between",
  marginBottom: 14,
};

const kickerStyle: CSSProperties = {
  color: COLORS.muted,
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.14em",
  margin: 0,
  textTransform: "uppercase",
};

const headingStyle: CSSProperties = {
  color: COLORS.ink,
  fontSize: 20,
  lineHeight: 1.3,
  margin: "4px 0 0",
};

const factGridStyle: CSSProperties = {
  display: "grid",
  gap: "12px 18px",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  margin: 0,
};

const factStyle: CSSProperties = {
  borderTop: `1px solid ${COLORS.border}`,
  display: "grid",
  gap: 4,
  minWidth: 0,
  paddingTop: 10,
};

const labelStyle: CSSProperties = { color: COLORS.muted, fontSize: 12 };
const valueStyle: CSSProperties = { color: COLORS.ink, overflowWrap: "anywhere" };
const codeStyle: CSSProperties = {
  background: "var(--td-bg-color-secondarycontainer, #f3f5f7)",
  borderRadius: 4,
  fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
  fontSize: 12,
  overflowWrap: "anywhere",
  padding: "2px 5px",
};
const actionStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 8, marginTop: 16 };

function safeText(value: unknown, fallback: string): string {
  return sanitizeOperationsMessage(value, fallback);
}

function safeDigest(value: unknown, fallback = "未返回"): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/i.test(value)) return fallback;
  return value.toLowerCase();
}

function digestLabel(value: unknown): string {
  const digest = safeDigest(value);
  return digest === "未返回" ? digest : `${digest.slice(0, 12)}…${digest.slice(-8)}`;
}

function safeRevision(value: unknown): string {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? String(value)
    : "未返回";
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "未返回";
  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    timeZoneName: "short",
    year: "numeric",
  }).format(parsed);
}

function minuteLabel(value: number | null): string {
  if (value === null) return "不可判定";
  if (value <= 0) return `已过期 ${Math.abs(value)} 分钟`;
  if (value < 60) return `${value} 分钟`;
  if (value < 1440) return `${Math.floor(value / 60)} 小时 ${value % 60} 分钟`;
  return `${Math.floor(value / 1440)} 天`;
}

function severityTheme(value: OperationsSeverity): "default" | "success" | "warning" | "danger" {
  if (value === "healthy") return "success";
  if (value === "warning") return "warning";
  if (value === "critical") return "danger";
  return "default";
}

function severityLabel(value: OperationsSeverity): string {
  if (value === "healthy") return "Healthy";
  if (value === "warning") return "Warning";
  if (value === "critical") return "Critical";
  return "Unavailable";
}

function gateTheme(value: QualityAuthorityProjection["gate_state"] | "passing") {
  if (value === "passed" || value === "passing") return "success" as const;
  if (value === "waived") return "warning" as const;
  if (value === "blocked" || value === "unavailable") return "danger" as const;
  return "default" as const;
}

function authorityTitle(authority: QualityAuthorityProjection | null): string {
  if (!authority) return "Quality Operations";
  const release =
    authority.release_number === null
      ? safeText(authority.release_id, "Release 未返回")
      : `Release ${authority.release_number}`;
  return `${safeText(authority.channel_name, "Channel 未返回")} · ${release}`;
}

function certificationLabel(authority: QualityAuthorityProjection): string {
  // 三条返回同属纯英文标签族（docs/42 §11.9）：整族一起改，不单独中文化第一条。
  if (authority.state === "unavailable") return "Quality authority unavailable";
  if (authority.gate_state === "blocked") return "Quality gate blocked";
  if (authority.certification_valid_until === null) return "Certification unavailable";
  if (
    authority.minutes_to_certification_expiry !== null &&
    authority.minutes_to_certification_expiry <= 0
  ) {
    return "Certification expired";
  }
  return "Certification current";
}

function jobStepIndex(status: RecertificationJobStatus): number {
  if (status === "failed" || status === "cancelled") return 0;
  const index = JOB_STEP_ORDER.indexOf(status);
  return index >= 0 ? index : 0;
}

function stepStatus(status: RecertificationJobStatus, index: number) {
  if (status === "failed" || status === "cancelled") {
    return index === jobStepIndex(status) ? ("error" as const) : ("default" as const);
  }
  const current = jobStepIndex(status);
  if (index < current) return "finish" as const;
  if (index === current) return "process" as const;
  return "default" as const;
}

function timelineDotColor(severity: OperationsSeverity): string {
  if (severity === "healthy") return COLORS.green;
  if (severity === "warning") return COLORS.amber;
  if (severity === "critical") return COLORS.red;
  return COLORS.muted;
}

function resolveFocusEntry(entry: QualityOperationsFocusEntry): HTMLElement | null {
  if (!entry) return null;
  if ("current" in entry) return entry.current;
  return entry;
}

function timelineSortValue(timestamp: string): number {
  const value = Date.parse(timestamp);
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
}

function StateUnavailable({ title, description }: { title: string; description: string }) {
  return (
    <div data-testid="quality-operations-detail-unavailable" role="status">
      <Alert theme="error" title={title} message={description} />
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={factStyle}>
      <dt style={labelStyle}>{label}</dt>
      <dd style={{ ...valueStyle, margin: 0 }}>{children}</dd>
    </div>
  );
}

function DigestValue({ value }: { value: unknown }) {
  return <code style={codeStyle}>{digestLabel(value)}</code>;
}

function AccessibleTabLabel({
  tab,
  activeTab,
  onSelect,
}: {
  tab: { value: QualityOperationsDetailTab; label: string };
  activeTab: QualityOperationsDetailTab;
  onSelect: (value: QualityOperationsDetailTab) => void;
}) {
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLSpanElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(tab.value);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = DETAIL_TABS.findIndex((item) => item.value === tab.value);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? DETAIL_TABS.length - 1
          : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + DETAIL_TABS.length) %
            DETAIL_TABS.length;
    const nextTab = DETAIL_TABS[nextIndex];
    const tabsRoot = event.currentTarget.closest(".t-tabs");
    onSelect(nextTab.value);
    window.setTimeout(() => {
      const tabs = tabsRoot?.querySelectorAll<HTMLElement>('[role="tab"]');
      tabs?.[nextIndex]?.focus();
    }, 0);
  };

  return (
    <span
      id={`quality-operations-detail-tab-${tab.value}`}
      role="tab"
      aria-controls={`quality-operations-detail-panel-${tab.value}`}
      aria-selected={tab.value === activeTab}
      tabIndex={tab.value === activeTab ? 0 : -1}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(tab.value);
      }}
      onKeyDown={handleKeyDown}
    >
      {tab.label}
    </span>
  );
}
export default function QualityOperationsDetailDrawer({
  visible,
  authority,
  datasetName = "未返回 Dataset",
  workspaceName = "未绑定 Workspace",
  riskTier = null,
  channelRevision = null,
  alert = null,
  job = null,
  observations = [],
  auditFacts = [],
  timelineStatus,
  timelineError = null,
  error = null,
  readOnly = false,
  focusEntry,
  onClose,
  onOpenCertification,
  onAcknowledgeAlert,
  onResolveAlert,
  onSuppressAlert,
  onQueueRecertification,
  onCancelRecertification,
}: QualityOperationsDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<QualityOperationsDetailTab>("overview");
  const closeHandledRef = useRef(false);
  const hasAuthority = authority?.state === "ready";
  const drawerTitle = `${authorityTitle(authority)} 质量运营详情`;

  useEffect(() => {
    closeHandledRef.current = false;
    setActiveTab("overview");
  }, [authority?.channel_id, authority?.release_id, visible]);

  const timelineEntries = useMemo<TimelineEntry[]>(() => {
    const observationEntries: TimelineEntry[] = observations.map((fact) => ({
      type: "observation",
      id: `observation:${fact.id}`,
      timestamp: fact.observed_at,
      title: safeText(fact.event, "Observed"),
      dotColor: timelineDotColor(fact.severity),
      observedBy: safeText(fact.observed_by, "system actor 未返回"),
      scanRunId: safeText(fact.scan_run_id, "Scan Run 未返回"),
      digest: safeDigest(fact.observation_digest),
      gate: GATE_LABELS[fact.gate_state],
      gateReason: safeText(fact.gate_reason, "Gate reason 未返回"),
    }));
    const auditEntries: TimelineEntry[] = auditFacts.map((fact) => ({
      type: "audit",
      id: `audit:${fact.id}`,
      timestamp: fact.occurred_at,
      title: safeText(fact.event, "Audit fact"),
      dotColor: COLORS.blue,
      actor: safeText(fact.actor, "actor 未返回"),
      objectRef: safeText(fact.object_ref, "Object reference 未返回"),
      revision: safeRevision(fact.revision),
      digest: safeDigest(fact.digest),
      serverConfirmed: fact.server_confirmed === true ? "Server confirmed" : "确认状态未返回",
    }));
    return [...observationEntries, ...auditEntries].sort(
      (left, right) => timelineSortValue(left.timestamp) - timelineSortValue(right.timestamp),
    );
  }, [auditFacts, observations]);

  const effectiveTimelineStatus: QualityOperationsTimelineStatus =
    timelineStatus ?? (timelineEntries.length ? "ready" : "empty");

  const close = () => {
    if (closeHandledRef.current) return;
    closeHandledRef.current = true;
    onClose();
    queueMicrotask(() => resolveFocusEntry(focusEntry)?.focus());
  };

  const renderOverview = () => (
    <div style={bodyStyle} data-testid="quality-operations-detail-overview">
      <section style={sectionStyle} aria-label="Quality authority overview">
        <div style={sectionHeadingStyle}>
          <div>
            <p style={kickerStyle}>RELEASE QUALITY AUTHORITY</p>
            <h2 style={headingStyle}>
              {authority?.release_number === null || authority?.release_number === undefined
                ? "Release 未返回"
                : `Release ${authority.release_number}`}
            </h2>
            <p style={{ color: COLORS.muted, margin: "4px 0 0", overflowWrap: "anywhere" }}>
              {safeText(authority?.release_id, "Release ID 未返回")}
            </p>
          </div>
          <div style={{ alignItems: "flex-end", display: "flex", flexDirection: "column", gap: 6 }}>
            <Tag
              theme={authority ? severityTheme(authority.severity) : "default"}
              variant="light-outline"
            >
              {authority ? severityLabel(authority.severity) : "Unavailable"}
            </Tag>
          </div>
        </div>
        {authority?.state === "unavailable" || !authority ? (
          <>
            <StateUnavailable
              title="质量权威不可用"
              description={safeText(
                authority?.unavailable_reason,
                "服务端没有返回可验证的质量权威。",
              )}
            />
            <p style={{ color: COLORS.muted, margin: "12px 0 0" }}>当前质量权威不可用</p>
          </>
        ) : null}
        <dl style={{ ...factGridStyle, marginTop: 16 }}>
          <Fact label="Dataset">{safeText(datasetName, "Dataset 未返回")}</Fact>
          <Fact label="Workspace">{safeText(workspaceName, "Workspace 未返回")}</Fact>
          <Fact label="Channel">
            {safeText(authority?.channel_name, "Channel 未返回")} ·{" "}
            {safeText(authority?.channel_id, "ID 未返回")}
          </Fact>
          <Fact label="Risk tier">{safeText(riskTier, "未返回")}</Fact>
          <Fact label="Channel revision">{safeRevision(channelRevision)}</Fact>
          <Fact label="Release role">{safeText(authority?.release_role, "未返回")}</Fact>
          <Fact label="Quality Gate">
            <Tag
              theme={authority ? gateTheme(authority.gate_state) : "default"}
              variant="light-outline"
            >
              {authority ? GATE_LABELS[authority.gate_state] : "Unavailable"}
            </Tag>
            <span style={{ color: COLORS.muted, display: "block", marginTop: 4 }}>
              {safeText(authority?.gate_reason, "Gate reason 未返回")}
            </span>
          </Fact>
          <Fact label="SLO horizon">
            {authority ? HORIZON_LABELS[authority.horizon_band] : "Unavailable"}
            {authority?.minutes_to_certification_expiry !== null && authority ? (
              <span style={{ color: COLORS.muted, display: "block", marginTop: 4 }}>
                {minuteLabel(authority.minutes_to_certification_expiry)}
              </span>
            ) : null}
          </Fact>
          <Fact label="Certification">
            <span>{authority ? certificationLabel(authority) : "Certification unavailable"}</span>
            {authority?.certification_id ? (
              <code style={{ ...codeStyle, display: "block", marginTop: 4 }}>
                {safeText(authority.certification_id, "ID 未返回")}
              </code>
            ) : null}
          </Fact>
          <Fact label="Certification valid until">
            {dateLabel(authority?.certification_valid_until)}
          </Fact>
          <Fact label="Waiver">
            {authority?.waiver_id ? safeText(authority.waiver_id, "ID 未返回") : "未绑定 Waiver"}
            {authority?.minutes_to_waiver_expiry !== null &&
            authority?.minutes_to_waiver_expiry !== undefined ? (
              <span style={{ color: COLORS.muted, display: "block", marginTop: 4 }}>
                {minuteLabel(authority.minutes_to_waiver_expiry)}
              </span>
            ) : null}
          </Fact>
          <Fact label="Active Alerts">{authority?.active_alert_count ?? "未返回"}</Fact>
          <Fact label="Recertification Job">
            {job
              ? JOB_STATUS_LABELS[job.status]
              : (authority?.recertification_job_status ?? "未排队")}
          </Fact>
          <Fact label="Last observed">{dateLabel(authority?.last_observed_at)}</Fact>
          <Fact label="Observation digest">
            <DigestValue value={authority?.observation_digest} />
          </Fact>
        </dl>
      </section>
    </div>
  );

  const renderTimeline = () => {
    if (effectiveTimelineStatus === "loading") {
      return (
        <div
          data-testid="quality-operations-detail-timeline-loading"
          role="status"
          style={sectionStyle}
        >
          <Loading size="small" text="正在读取不可变时间线…" />
        </div>
      );
    }
    if (effectiveTimelineStatus === "error") {
      return (
        <div data-testid="quality-operations-detail-timeline-error" role="alert">
          <Alert
            theme="error"
            title="运营时间线读取失败"
            message={safeText(timelineError, "服务端没有返回可验证的时间线。")}
          />
        </div>
      );
    }
    if (effectiveTimelineStatus === "unavailable") {
      return (
        <StateUnavailable
          title="运营时间线不可用"
          description="当前服务端没有提供可验证的 immutable observations 或 audit facts。"
        />
      );
    }
    if (effectiveTimelineStatus === "idle") {
      return (
        <Empty
          type="network-error"
          title="运营时间线尚未读取"
          description="仅展示服务端已返回的 immutable facts，不根据当前状态补齐历史。"
        />
      );
    }
    if (effectiveTimelineStatus === "empty" || timelineEntries.length === 0) {
      return (
        <Empty
          title="尚无运营时间线"
          description="服务端未返回 immutable observations 或 audit facts，不虚构历史节点。"
        />
      );
    }
    return (
      <section style={sectionStyle} aria-label="Immutable operations timeline">
        <div style={sectionHeadingStyle}>
          <div>
            <p style={kickerStyle}>SERVER-CONFIRMED HISTORY</p>
            <h3 style={{ ...headingStyle, fontSize: 18 }}>
              Immutable observations &amp; audit facts
            </h3>
          </div>
          <Tag theme="primary" variant="light-outline">
            {timelineEntries.length} facts
          </Tag>
        </div>
        <Timeline mode="same" theme="dot" layout="vertical">
          {timelineEntries.map((entry) => (
            <Timeline.Item
              key={entry.id}
              dotColor={entry.dotColor}
              label={dateLabel(entry.timestamp)}
              content={
                entry.type === "observation" ? (
                  <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
                    <strong>{entry.title}</strong>
                    <span style={{ color: COLORS.muted }}>Server confirmed</span>
                    <span>
                      {entry.gate} · {entry.gateReason}
                    </span>
                    <span style={{ color: COLORS.muted }}>
                      {entry.observedBy} · <code style={codeStyle}>{entry.scanRunId}</code>
                    </span>
                    <span style={{ color: COLORS.muted }}>
                      Observation <DigestValue value={entry.digest} />
                    </span>
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
                    <strong>{entry.title}</strong>
                    <span style={{ color: COLORS.muted }}>{entry.serverConfirmed}</span>
                    <span>
                      {entry.actor} · <code style={codeStyle}>{entry.objectRef}</code>
                    </span>
                    <span style={{ color: COLORS.muted }}>
                      Revision {entry.revision} · Digest <DigestValue value={entry.digest} />
                    </span>
                  </div>
                )
              }
            />
          ))}
        </Timeline>
      </section>
    );
  };

  const renderCertification = () => {
    if (!hasAuthority || !authority) {
      return (
        <StateUnavailable
          title="Certification unavailable"
          description="当前 authority 不可用，无法展示 Certification 事实。"
        />
      );
    }
    if (!authority.certification_id || !authority.certification_valid_until) {
      return (
        <Empty
          type="network-error"
          title="当前没有可验证的质量认证权威"
          description="Certification ID 或有效期未返回，不以当前 Gate 状态猜测 Certification。"
        />
      );
    }
    return (
      <div style={bodyStyle} data-testid="quality-operations-detail-certification">
        <section style={sectionStyle} aria-label="质量认证权威">
          <div style={sectionHeadingStyle}>
            <div>
              <p style={kickerStyle}>STAGE 20 AUTHORITY</p>
              <h3 style={{ ...headingStyle, fontSize: 18 }}>质量认证权威</h3>
            </div>
            <Tag theme={severityTheme(authority.severity)} variant="light-outline">
              {certificationLabel(authority)}
            </Tag>
          </div>
          <dl style={factGridStyle}>
            <Fact label="Certification ID">
              <code style={codeStyle}>{safeText(authority.certification_id, "ID 未返回")}</code>
            </Fact>
            <Fact label="有效至">{dateLabel(authority.certification_valid_until)}</Fact>
            <Fact label="Remaining horizon">
              {minuteLabel(authority.minutes_to_certification_expiry)}
            </Fact>
            <Fact label="Observed at">{dateLabel(authority.last_observed_at)}</Fact>
            <Fact label="Gate reason">{safeText(authority.gate_reason, "Gate reason 未返回")}</Fact>
            <Fact label="Observation digest">
              <DigestValue value={authority.observation_digest} />
            </Fact>
          </dl>
          <div style={actionStyle}>
            <Button
              tag="button"
              theme="primary"
              variant="outline"
              icon={<ChevronRightIcon />}
              disabled={!onOpenCertification}
              onClick={() => onOpenCertification?.(authority)}
            >
              打开 Release Certification 详情
            </Button>
          </div>
        </section>
      </div>
    );
  };

  const renderAlert = () => {
    if (!hasAuthority || !authority) {
      return (
        <StateUnavailable
          title="Alert unavailable"
          description="当前 authority 不可用，无法验证 Alert 事实。"
        />
      );
    }
    if (!alert) {
      return (
        <Empty
          title="当前未返回 Alert 明细"
          description={
            authority.active_alert_count > 0
              ? "Summary 声明存在 active Alert，但本 Drawer 没有可验证的明细。"
              : "当前 authority 没有 active Alert。"
          }
        />
      );
    }
    const acknowledgeDisabled = readOnly || !onAcknowledgeAlert || alert.status !== "open";
    const resolveDisabled =
      readOnly || !onResolveAlert || !["open", "acknowledged", "suppressed"].includes(alert.status);
    const suppressDisabled =
      readOnly || !onSuppressAlert || !["open", "acknowledged"].includes(alert.status);
    return (
      <div style={bodyStyle} data-testid="quality-operations-detail-alert">
        <section style={sectionStyle} aria-label="Alert authority">
          <div style={sectionHeadingStyle}>
            <div>
              <p style={kickerStyle}>OPERATIONS INBOX</p>
              <h3 style={{ ...headingStyle, fontSize: 18 }}>Alert authority</h3>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, justifyContent: "flex-end" }}>
              <Tag
                theme={alert.severity === "critical" ? "danger" : "warning"}
                variant="light-outline"
              >
                {alert.severity}
              </Tag>
              <Tag variant="light-outline">{ALERT_STATUS_LABELS[alert.status]}</Tag>
            </div>
          </div>
          <div aria-live="polite" role="status" style={{ color: COLORS.muted, marginBottom: 12 }}>
            {alert.status === "open"
              ? "Alert requires operator action"
              : `Alert is ${ALERT_STATUS_LABELS[alert.status]}`}
          </div>
          <dl style={factGridStyle}>
            <Fact label="Alert type">{ALERT_TYPE_LABELS[alert.alert_type]}</Fact>
            <Fact label="Alert ID">
              <code style={codeStyle}>{safeText(alert.id, "ID 未返回")}</code>
            </Fact>
            <Fact label="Revision">{safeRevision(alert.revision)}</Fact>
            <Fact label="Occurrences">{alert.occurrence_count}</Fact>
            <Fact label="Opened at">{dateLabel(alert.opened_at)}</Fact>
            <Fact label="Last observed">{dateLabel(alert.last_observed_at)}</Fact>
            <Fact label="Acknowledged by">
              {safeText(alert.acknowledged_by, "未确认")}
              {alert.acknowledged_at ? (
                <span style={{ color: COLORS.muted, display: "block", marginTop: 4 }}>
                  {dateLabel(alert.acknowledged_at)}
                </span>
              ) : null}
            </Fact>
            <Fact label="Acknowledged comment">
              {safeText(alert.acknowledged_comment, "未返回")}
            </Fact>
            <Fact label="Resolved at">{dateLabel(alert.resolved_at)}</Fact>
            <Fact label="Suppressed until">{dateLabel(alert.suppressed_until)}</Fact>
          </dl>
          <div style={actionStyle} aria-label="Alert lifecycle actions">
            <Button
              tag="button"
              theme="primary"
              disabled={acknowledgeDisabled}
              onClick={() => onAcknowledgeAlert?.(alert)}
            >
              确认告警
            </Button>
            <Button
              tag="button"
              theme="danger"
              variant="outline"
              disabled={resolveDisabled}
              onClick={() => onResolveAlert?.(alert)}
            >
              解决告警
            </Button>
            <Button
              tag="button"
              theme="warning"
              variant="outline"
              disabled={suppressDisabled}
              onClick={() => onSuppressAlert?.(alert)}
            >
              抑制告警
            </Button>
          </div>
          {readOnly ? (
            <p style={{ color: COLORS.muted, margin: "12px 0 0" }}>
              只读事实：Alert lifecycle Dialog 由上层协调器负责，本 Drawer 不执行状态变更。
            </p>
          ) : null}
        </section>
      </div>
    );
  };

  const renderRecertification = () => {
    if (!hasAuthority || !authority) {
      return (
        <StateUnavailable
          title="Recertification unavailable"
          description="当前 authority 不可用，无法展示 Job 事实。"
        />
      );
    }
    const terminal = job ? ["completed", "failed", "cancelled"].includes(job.status) : false;
    const queueDisabled = readOnly || !onQueueRecertification || (Boolean(job) && !terminal);
    const cancelDisabled = readOnly || !onCancelRecertification || !job || terminal;
    if (!job) {
      return (
        <div style={bodyStyle} data-testid="quality-operations-detail-recertification">
          <section style={sectionStyle} aria-label="Recertification queue">
            <div style={sectionHeadingStyle}>
              <div>
                <p style={kickerStyle}>DURABLE QUEUE</p>
                <h3 style={{ ...headingStyle, fontSize: 18 }}>Recertification</h3>
              </div>
              <Tag variant="light-outline">未排队</Tag>
            </div>
            <Empty
              title="当前没有 Recertification Job"
              description="排队动作只会创建持久 Job，不会直接执行 Certification。"
            />
            <div style={actionStyle}>
              <Button
                tag="button"
                theme="primary"
                disabled={queueDisabled}
                onClick={() => onQueueRecertification?.(authority)}
              >
                排队再认证
              </Button>
            </div>
          </section>
        </div>
      );
    }
    return (
      <div style={bodyStyle} data-testid="quality-operations-detail-recertification">
        <section style={sectionStyle} aria-label="Recertification queue">
          <div style={sectionHeadingStyle}>
            <div>
              <p style={kickerStyle}>DURABLE QUEUE</p>
              <h3 style={{ ...headingStyle, fontSize: 18 }}>再认证生命周期</h3>
            </div>
            <Tag theme={job.status === "failed" ? "danger" : "primary"} variant="light-outline">
              {JOB_STATUS_LABELS[job.status]}
            </Tag>
          </div>
          <Steps
            current={job.status === "completed" ? "FINISH" : jobStepIndex(job.status)}
            layout="vertical"
            readOnly
          >
            {JOB_STEP_ORDER.map((step, index) => (
              <Steps.StepItem
                key={step}
                title={JOB_STEP_LABELS[step]}
                status={stepStatus(job.status, index)}
              />
            ))}
          </Steps>
          <dl style={{ ...factGridStyle, marginTop: 16 }}>
            <Fact label="Job ID">
              <code style={codeStyle}>{safeText(job.id, "ID 未返回")}</code>
            </Fact>
            <Fact label="Trigger">
              <code style={codeStyle}>{safeText(job.trigger, "trigger 未返回")}</code>
            </Fact>
            <Fact label="Status">
              {JOB_STATUS_LABELS[job.status]} · <code style={codeStyle}>{job.status}</code>
            </Fact>
            <Fact label="Attempt">
              Attempt {job.attempt_count} / {job.max_attempts}
            </Fact>
            <Fact label="Next attempt">{dateLabel(job.next_attempt_at)}</Fact>
            <Fact label="Created">{dateLabel(job.created_at)}</Fact>
            <Fact label="Updated">{dateLabel(job.updated_at)}</Fact>
            <Fact label="Safe error code">{safeText(job.safe_error_code, "未返回")}</Fact>
            <Fact label="Safe error">{safeText(job.safe_error, "未返回")}</Fact>
          </dl>
          {job.safe_error ? (
            <div
              data-testid="quality-operations-detail-job-error"
              role="alert"
              style={{ marginTop: 16 }}
            >
              <Alert
                theme="error"
                title="Recertification Job failed safely"
                message={safeText(job.safe_error, "Job error unavailable")}
              />
            </div>
          ) : null}
          <div style={actionStyle} aria-label="Recertification lifecycle actions">
            <Button
              tag="button"
              theme="primary"
              disabled={queueDisabled}
              onClick={() => onQueueRecertification?.(authority)}
            >
              排队再认证
            </Button>
            <Button
              tag="button"
              variant="outline"
              disabled={cancelDisabled}
              onClick={() => onCancelRecertification?.(job)}
            >
              取消再认证
            </Button>
          </div>
          {readOnly ? (
            <p style={{ color: COLORS.muted, margin: "12px 0 0" }}>
              只读事实：Job action Dialog 由上层协调器负责，本 Drawer 不直接修改队列。
            </p>
          ) : null}
        </section>
      </div>
    );
  };

  const renderTab = (tab: QualityOperationsDetailTab) => {
    if (tab === "overview") return renderOverview();
    if (tab === "timeline") return <div style={bodyStyle}>{renderTimeline()}</div>;
    if (tab === "certification") return renderCertification();
    if (tab === "alert") return renderAlert();
    return renderRecertification();
  };

  if (!visible) return null;

  return (
    <Drawer
      visible={visible}
      header={drawerTitle}
      aria-label={drawerTitle}
      size="min(760px, 100vw)"
      placement="right"
      destroyOnClose
      closeOnEscKeydown
      closeOnOverlayClick
      footer={false}
      closeBtn={
        <Button variant="text" aria-label="关闭 Quality Operations 详情" onClick={close}>
          关闭
        </Button>
      }
      onClose={close}
      className="quality-operations-detail-drawer"
    >
      <div
        data-testid="quality-operations-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={drawerTitle}
        tabIndex={-1}
        style={bodyStyle}
      >
        <div
          style={{
            alignItems: "center",
            background: "color-mix(in srgb, #0052d9 6%, transparent)",
            border: "1px solid color-mix(in srgb, #0052d9 20%, transparent)",
            borderRadius: 8,
            display: "flex",
            gap: 10,
            padding: "10px 12px",
          }}
          role="status"
        >
          <SecuredIcon aria-hidden="true" />
          <span style={{ color: COLORS.muted, fontSize: 12 }}>
            只展示已投影的 Tenant / Dataset 质量事实；请求体、查询内容、凭据与原始 ticket
            不在此显示。
          </span>
          {readOnly ? <Tag variant="light-outline">只读事实</Tag> : null}
        </div>
        {error ? (
          <div data-testid="quality-operations-detail-error" role="alert">
            <Alert
              theme="error"
              title="Quality Operations unavailable"
              message={safeText(error, "Quality Operations unavailable")}
            />
          </div>
        ) : null}
        <Tabs
          value={activeTab}
          onChange={(value) => setActiveTab(value as QualityOperationsDetailTab)}
          theme="normal"
        >
          {DETAIL_TABS.map((tab) => (
            <Tabs.TabPanel
              key={tab.value}
              value={tab.value}
              label={<AccessibleTabLabel tab={tab} activeTab={activeTab} onSelect={setActiveTab} />}
              destroyOnHide
            >
              <div
                id={`quality-operations-detail-panel-${tab.value}`}
                role="tabpanel"
                aria-labelledby={`quality-operations-detail-tab-${tab.value}`}
              >
                {renderTab(tab.value)}
              </div>
            </Tabs.TabPanel>
          ))}
        </Tabs>
      </div>
    </Drawer>
  );
}
