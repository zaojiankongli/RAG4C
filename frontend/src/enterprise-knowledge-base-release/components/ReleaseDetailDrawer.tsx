import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Button, Card, Drawer, Tabs, Tag } from "tdesign-react";
import {
  CheckCircleIcon,
  InfoCircleIcon,
  RollbackIcon,
  RocketIcon,
  SecuredIcon,
} from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type {
  ReleaseAuditPage,
  ReleaseChannel,
  ReleaseChannelSummary,
  ReleaseDetail,
  ReleaseHistoryPage,
  ReleaseImpact,
  ReleaseManifest,
  ReleaseReadiness,
} from "../model/releaseModel";
import {
  releaseComparisonLabel,
  releaseReadinessLabel,
  releaseReadinessTheme,
  releaseRiskLabel,
  releaseStatusLabel,
  safeReleaseDisplayText,
} from "../model/releaseModel";

type DetailTab = "manifest" | "readiness" | "certification" | "impact" | "audit";

const DETAIL_TABS: Array<{ value: DetailTab; label: string }> = [
  { value: "manifest", label: "Manifest" },
  { value: "readiness", label: "Readiness" },
  { value: "certification", label: "Certification" },
  { value: "impact", label: "Impact" },
  { value: "audit", label: "Audit" },
];

function detailTabLabel(
  label: string,
  value: DetailTab,
  active: DetailTab,
  onSelect: (value: DetailTab) => void,
) {
  const handleKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(value);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = DETAIL_TABS.findIndex((item) => item.value === value);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? DETAIL_TABS.length - 1
          : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + DETAIL_TABS.length) %
            DETAIL_TABS.length;
    const next = DETAIL_TABS[nextIndex]!;
    onSelect(next.value);
    const tabs = event.currentTarget
      .closest(".knowledge-base-release-detail__tabs")
      ?.querySelectorAll<HTMLElement>('[role="tab"]');
    window.setTimeout(() => tabs?.[nextIndex]?.focus(), 0);
  };
  return (
    <span
      id={`release-detail-tab-${value}`}
      role="tab"
      aria-controls={`release-detail-panel-${value}`}
      aria-selected={value === active}
      tabIndex={value === active ? 0 : -1}
      onKeyDown={handleKeyDown}
    >
      {label}
    </span>
  );
}

export interface ReleaseDetailDrawerProps {
  visible: boolean;
  channel: ReleaseChannel | null;
  summary: ReleaseChannelSummary | null;
  history: ReleaseHistoryPage["items"];
  detailStatus: "idle" | "loading" | "ready" | "error";
  detail: ReleaseDetail | null;
  readiness: ReleaseReadiness | null;
  impact: ReleaseImpact | null;
  audit: ReleaseAuditPage | null;
  qualityPanel?: ReactNode;
  error: Error | null;
  readOnly: boolean;
  onClose: () => void;
  onLoadImpact: () => Promise<void>;
  onLoadAudit: () => Promise<void>;
  onPromote: (release: ReleaseManifest, trigger: HTMLElement) => void;
  onRollback: (release: ReleaseManifest, trigger: HTMLElement) => void;
}

function trapDrawerFocus(
  event: Pick<globalThis.KeyboardEvent, "key" | "shiftKey" | "preventDefault">,
  body: HTMLElement | null,
) {
  if (event.key !== "Tab" || !body) return;
  const root = body.closest(".t-drawer") ?? body;
  const focusable = Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((item) => item.offsetParent !== null || item === document.activeElement);
  if (!focusable.length) {
    event.preventDefault();
    body.focus();
    return;
  }
  const first = focusable[0]!;
  const last = focusable[focusable.length - 1]!;
  if (event.shiftKey && (document.activeElement === first || document.activeElement === body)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function revisionLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : `R${value}`;
}

function numberLabel(value: number | null | undefined): string {
  return value == null ? "未返回" : String(value);
}

function shortDigest(value: string | null | undefined): string {
  if (!value) return "未返回";
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function stateTheme(state: string): "success" | "warning" | "danger" | "default" {
  if (state === "ready" || state === "aligned" || state === "published") return "success";
  if (state === "blocked" || state === "drifted" || state === "candidate") return "warning";
  if (state === "unavailable" || state === "retired") return "danger";
  return "default";
}

function FactsGrid({
  rows,
  className = "",
}: {
  rows: Array<{ label: string; value: ReactNode }>;
  className?: string;
}) {
  return (
    <dl className={`knowledge-base-release-detail__facts ${className}`.trim()}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ManifestPanel({ detail }: { detail: ReleaseDetail }) {
  const { manifest, entries } = detail;
  return (
    <div
      className="knowledge-base-release-detail__panel"
      data-testid="release-detail-manifest-panel"
    >
      <Card className="knowledge-base-release-detail__manifest-card" bordered={false}>
        <div className="knowledge-base-release-detail__manifest-heading">
          <div>
            <span className="knowledge-base-release-detail__section-kicker">
              IMMUTABLE MANIFEST
            </span>
            <h3>Release {manifest.release_number}</h3>
            <p>{manifest.reason || "未返回发布原因"}</p>
          </div>
          <Tag theme={stateTheme(manifest.status)} variant="light-outline">
            {releaseStatusLabel(manifest.status)}
          </Tag>
        </div>
        <FactsGrid
          rows={[
            { label: "Release ID", value: manifest.id },
            {
              label: "Manifest digest",
              value: <code>{shortDigest(manifest.manifest_digest)}</code>,
            },
            {
              label: "Profile / Ownership",
              value: `${revisionLabel(manifest.profile_revision)} · ${revisionLabel(manifest.ownership_revision)}`,
            },
            { label: "Workspace revision", value: revisionLabel(manifest.workspace_revision) },
            {
              label: "Mutation / Serving",
              value: `${numberLabel(manifest.mutation_generation)} · ${numberLabel(manifest.serving_generation)}`,
            },
            { label: "Entry count", value: numberLabel(manifest.entry_count) },
            { label: "Created by", value: manifest.created_by || "未返回" },
            { label: "Created at", value: dateLabel(manifest.created_at) },
          ]}
        />
      </Card>
      <section
        className="knowledge-base-release-detail__entries"
        aria-labelledby="release-entry-title"
      >
        <div className="knowledge-base-release-detail__subheading">
          <div>
            <span className="knowledge-base-release-detail__section-kicker">MANIFEST ENTRIES</span>
            <h3 id="release-entry-title">已捕获资源</h3>
          </div>
          <span>{entries.count == null ? "未返回" : `${entries.count} 项`}</span>
        </div>
        {entries.items.length ? (
          <div className="knowledge-base-release-detail__entry-list">
            {entries.items.map((entry) => (
              <article key={entry.id} className="knowledge-base-release-detail__entry">
                <div className="knowledge-base-release-detail__entry-icon" aria-hidden="true">
                  <SecuredIcon />
                </div>
                <div className="knowledge-base-release-detail__entry-copy">
                  <strong>{entry.resource_type}</strong>
                  <span>{entry.resource_id}</span>
                </div>
                <div className="knowledge-base-release-detail__entry-meta">
                  <span>Revision {entry.resource_revision}</span>
                  <code>{shortDigest(entry.content_digest)}</code>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <PageState
            compact
            status="empty"
            title="Manifest 没有返回 entries"
            description="服务端没有返回可展示的资源条目。"
          />
        )}
      </section>
    </div>
  );
}

function ReadinessPanel({
  readiness,
  summary,
}: {
  readiness: ReleaseReadiness | null;
  summary: ReleaseChannelSummary | null;
}) {
  const value = readiness ?? summary?.readiness ?? null;
  if (!value) {
    return (
      <PageState
        compact
        status="empty"
        title="Readiness 未返回"
        description="当前 Release 没有可验证的发布就绪事实。"
      />
    );
  }
  return (
    <div
      className="knowledge-base-release-detail__panel"
      data-testid="release-detail-readiness-panel"
    >
      <div className={`knowledge-base-release-detail__readiness-banner is-${value.state}`}>
        <div className="knowledge-base-release-detail__readiness-icon" aria-hidden="true">
          {value.state === "ready" ? <CheckCircleIcon /> : <InfoCircleIcon />}
        </div>
        <div>
          <strong>{releaseReadinessLabel(value.state)}</strong>
          <span>{value.reason || "没有额外阻断说明"}</span>
        </div>
        <Tag theme={releaseReadinessTheme(value.state)} variant="light-outline">
          {value.blocker_count == null ? "未返回阻断数" : `${value.blocker_count} 个阻断`}
        </Tag>
      </div>
      <FactsGrid
        rows={[
          { label: "Readiness fingerprint", value: <code>{shortDigest(value.fingerprint)}</code> },
          {
            label: "Channel comparison",
            value: releaseComparisonLabel(summary?.comparison_state ?? "unavailable"),
          },
          { label: "Channel revision", value: revisionLabel(summary?.revision) },
        ]}
      />
      {value.blockers.length ? (
        <div className="knowledge-base-release-detail__blockers">
          {value.blockers.map((blocker) => (
            <article key={blocker.code} className="knowledge-base-release-detail__blocker">
              <Tag
                theme={blocker.severity === "unavailable" ? "danger" : "warning"}
                variant="light-outline"
              >
                {blocker.severity === "unavailable" ? "证据不可用" : "阻断"}
              </Tag>
              <div>
                <strong>{blocker.label}</strong>
                <span>{blocker.reason || blocker.code}</span>
              </div>
              <small>{blocker.count == null ? "未返回" : `${blocker.count} 项`}</small>
            </article>
          ))}
        </div>
      ) : (
        <div className="knowledge-base-release-detail__clear-state">
          <CheckCircleIcon aria-hidden="true" />
          <span>没有待处理的发布阻断</span>
        </div>
      )}
    </div>
  );
}

function ImpactPanel({ impact, error }: { impact: ReleaseImpact | null; error: Error | null }) {
  if (error && !impact) {
    return (
      <PageState
        compact
        status="error"
        title="影响面读取失败"
        description={safeReleaseDisplayText(error.message) || "影响面权威无法安全读取。"}
      />
    );
  }
  if (!impact) {
    return (
      <PageState
        compact
        status="loading"
        title="正在读取影响面"
        description="影响面默认延迟加载，避免打开详情时请求大体量证据。"
      />
    );
  }
  return (
    <div className="knowledge-base-release-detail__panel" data-testid="release-detail-impact-panel">
      <div className="knowledge-base-release-detail__lazy-heading">
        <div>
          <span className="knowledge-base-release-detail__section-kicker">CHANGE IMPACT</span>
          <h3>发布影响面</h3>
        </div>
        <Tag theme={stateTheme(impact.state)} variant="light-outline">
          {releaseReadinessLabel(impact.state)}
        </Tag>
      </div>
      {impact.items.length ? (
        <div className="knowledge-base-release-detail__impact-list">
          {impact.items.map((item) => (
            <article key={`${item.type}:${item.id}`}>
              <div>
                <strong>{item.label}</strong>
                <span>
                  {item.type} · {item.id}
                </span>
              </div>
              <Tag variant="light-outline">{item.action}</Tag>
              <p>{item.reason || "未返回影响说明"}</p>
            </article>
          ))}
        </div>
      ) : (
        <PageState
          compact
          status="empty"
          title="没有返回影响项"
          description={impact.reason || "服务端返回了空影响面。"}
        />
      )}
    </div>
  );
}

function AuditPanel({ audit, error }: { audit: ReleaseAuditPage | null; error: Error | null }) {
  if (error && !audit) {
    return (
      <PageState
        compact
        status="error"
        title="审计读取失败"
        description={safeReleaseDisplayText(error.message) || "审计权威无法安全读取。"}
      />
    );
  }
  if (!audit) {
    return (
      <PageState
        compact
        status="loading"
        title="正在读取审计事件"
        description="审计默认延迟加载，仅在打开该标签时读取。"
      />
    );
  }
  return (
    <div className="knowledge-base-release-detail__panel" data-testid="release-detail-audit-panel">
      <div className="knowledge-base-release-detail__lazy-heading">
        <div>
          <span className="knowledge-base-release-detail__section-kicker">AUDIT EVIDENCE</span>
          <h3>发布审计</h3>
        </div>
        <span>{audit.count == null ? "未返回" : `${audit.count} 条事件`}</span>
      </div>
      {audit.items.length ? (
        <ol className="knowledge-base-release-detail__audit-list">
          {audit.items.map((item) => (
            <li key={item.id}>
              <span className="knowledge-base-release-detail__audit-dot" aria-hidden="true" />
              <div>
                <strong>{item.event}</strong>
                <span>
                  {item.actor} · {dateLabel(item.occurred_at)}
                </span>
                <p>{item.reason || "未返回原因"}</p>
              </div>
              <code>{safeReleaseDisplayText(item.request_id) || "request 未返回"}</code>
            </li>
          ))}
        </ol>
      ) : (
        <PageState
          compact
          status="empty"
          title="没有返回审计事件"
          description="服务端返回了空审计列表。"
        />
      )}
    </div>
  );
}

export default function ReleaseDetailDrawer({
  visible,
  channel,
  summary,
  history,
  detailStatus,
  detail,
  readiness,
  impact,
  audit,
  qualityPanel,
  error,
  readOnly,
  onClose,
  onLoadImpact,
  onLoadAudit,
  onPromote,
  onRollback,
}: ReleaseDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<DetailTab>("manifest");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const selected = detail?.manifest ?? null;
  const rollbackTargets = useMemo(
    () =>
      selected
        ? history.filter(
            (item) =>
              item.id !== selected.id &&
              (item.status === "published" || item.status === "superseded"),
          )
        : [],
    [history, selected],
  );

  useEffect(() => {
    if (visible) setActiveTab("manifest");
  }, [selected?.id, visible]);

  useEffect(() => {
    if (!visible) return;
    const focusDialog = () => dialogRef.current?.focus();
    const frame = window.requestAnimationFrame(focusDialog);
    const timer = window.setTimeout(focusDialog, 360);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [selected?.id, visible]);

  useEffect(() => {
    if (!visible) return;
    const handleTab = (event: globalThis.KeyboardEvent) =>
      trapDrawerFocus(event, dialogRef.current);
    document.addEventListener("keydown", handleTab);
    return () => document.removeEventListener("keydown", handleTab);
  }, [visible]);

  useEffect(() => {
    if (!visible || !selected) return;
    if (activeTab === "impact" && impact === null) void onLoadImpact();
    if (activeTab === "audit" && audit === null) void onLoadAudit();
  }, [activeTab, audit, impact, onLoadAudit, onLoadImpact, selected, visible]);

  if (!visible) return null;

  const detailTitle = selected ? `Release ${selected.release_number} 详情` : "Release 详情";
  const readinessState = readiness?.state ?? selected?.readiness_state ?? "unavailable";
  const configured = summary?.configured;
  const configuredMatchesCandidate = Boolean(
    selected &&
    configured &&
    configured.profile_revision === selected.profile_revision &&
    configured.mutation_generation === selected.mutation_generation &&
    configured.ownership_revision === selected.ownership_revision &&
    configured.workspace_revision === selected.workspace_revision,
  );
  const canPromote = Boolean(
    selected &&
    channel &&
    summary &&
    channel.status === "active" &&
    selected.status === "candidate" &&
    readinessState === "ready" &&
    summary.serving_generation !== null &&
    configuredMatchesCandidate,
  );
  const canRollback = Boolean(
    selected &&
    channel &&
    summary &&
    channel.status === "active" &&
    summary.serving_generation !== null &&
    rollbackTargets.length > 0,
  );

  return (
    <Drawer
      visible={visible}
      header={detailTitle}
      aria-label={detailTitle}
      size="min(760px, 100vw)"
      placement="right"
      destroyOnClose
      closeOnEscKeydown
      closeOnOverlayClick
      footer={false}
      closeBtn={
        <Button variant="text" aria-label="关闭 Release 详情" onClick={onClose}>
          关闭
        </Button>
      }
      onClose={onClose}
      className="knowledge-base-release-detail-drawer"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        aria-label={detailTitle}
        className="knowledge-base-release-detail-drawer__body"
      >
        {detailStatus === "loading" ? (
          <PageState
            status="loading"
            title="正在读取 Release 详情"
            description="正在并行读取 Manifest 与 Readiness 权威。"
          />
        ) : detailStatus === "error" || !detail || !selected ? (
          <PageState
            status="error"
            title="Release 详情不可用"
            description={
              safeReleaseDisplayText(error?.message) || "服务端没有返回可验证的 Release 详情。"
            }
          />
        ) : (
          <>
            <header className="knowledge-base-release-detail__header">
              <div>
                <span className="knowledge-base-release-detail__section-kicker">
                  RELEASE MANIFEST
                </span>
                <h2>Release {selected.release_number}</h2>
                <p>{selected.id}</p>
              </div>
              <div className="knowledge-base-release-detail__header-meta">
                <Tag theme={stateTheme(selected.status)} variant="light-outline">
                  {releaseStatusLabel(selected.status)}
                </Tag>
                <Tag theme={releaseReadinessTheme(readinessState)} variant="light-outline">
                  {releaseReadinessLabel(readinessState)}
                </Tag>
              </div>
            </header>
            <div className="knowledge-base-release-detail__context-line">
              <span>{channel?.name || "Channel 未返回"}</span>
              <span>风险：{channel ? releaseRiskLabel(channel.risk_tier) : "未返回"}</span>
              <span>Channel {revisionLabel(summary?.revision ?? channel?.revision)}</span>
            </div>
            <div className="knowledge-base-release-detail__action-row" aria-label="Release 操作">
              <Button
                tag="button"
                theme="primary"
                icon={<RocketIcon />}
                disabled={readOnly || !canPromote}
                onClick={(event) => onPromote(selected, event.currentTarget)}
                title={
                  !canPromote && !readOnly
                    ? "仅当候选 Release 已通过 Readiness 且返回当前 Serving generation 时可发布"
                    : undefined
                }
              >
                发布到 {channel?.name || "当前 Channel"}
              </Button>
              {rollbackTargets.map((target) => (
                <Button
                  tag="button"
                  key={target.id}
                  variant="outline"
                  icon={<RollbackIcon />}
                  disabled={readOnly || !canRollback}
                  onClick={(event) => onRollback(target, event.currentTarget)}
                >
                  回滚到 Release {target.release_number}
                </Button>
              ))}
              {!rollbackTargets.length ? (
                <span className="knowledge-base-release-detail__action-note">
                  没有可验证的历史 published Release
                </span>
              ) : null}
            </div>
            <div role="tablist" aria-label="Release 详情导航">
              <Tabs
                className="knowledge-base-release-detail__tabs"
                value={activeTab}
                theme="normal"
                onChange={(value) => setActiveTab(String(value) as DetailTab)}
              >
                {DETAIL_TABS.map((tab) => (
                  <Tabs.TabPanel
                    key={tab.value}
                    value={tab.value}
                    label={detailTabLabel(tab.label, tab.value, activeTab, setActiveTab)}
                    destroyOnHide
                  >
                    <section
                      id={`release-detail-panel-${tab.value}`}
                      role="tabpanel"
                      aria-labelledby={`release-detail-tab-${tab.value}`}
                    >
                      {tab.value === "manifest" ? <ManifestPanel detail={detail} /> : null}
                      {tab.value === "readiness" ? (
                        <ReadinessPanel readiness={readiness} summary={summary} />
                      ) : null}
                      {tab.value === "certification"
                        ? (qualityPanel ?? (
                            <PageState
                              compact
                              status="error"
                              title="质量认证权威未返回"
                              description="服务端没有返回可验证的 Release 质量认证事实。"
                            />
                          ))
                        : null}
                      {tab.value === "impact" ? (
                        <ImpactPanel impact={impact} error={error} />
                      ) : null}
                      {tab.value === "audit" ? <AuditPanel audit={audit} error={error} /> : null}
                    </section>
                  </Tabs.TabPanel>
                ))}
              </Tabs>
            </div>
          </>
        )}
      </div>
    </Drawer>
  );
}
