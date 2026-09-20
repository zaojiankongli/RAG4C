import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, PrimaryTable, Tag, type PrimaryTableCol } from "tdesign-react";
import {
  ChevronRightIcon,
  NotificationIcon,
  RefreshIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";

import type {
  OperationsHorizonBand,
  QualityAuthorityProjection,
  QualityOperationsAlert,
  QualityOperationsSummary,
  RecertificationJob,
} from "../model/operationsModel";
import "../quality-operations.css";

export interface QualityOperationsCenterProps {
  datasetName: string;
  workspaceName?: string;
  summary: QualityOperationsSummary | null;
  alerts: QualityOperationsAlert[];
  jobs: RecertificationJob[];
  selectedHorizon?: OperationsHorizonBand | "all";
  loading?: boolean;
  error?: string | null;
  readOnly?: boolean;
  onHorizonChange?: (band: OperationsHorizonBand) => void;
  onRefresh?: () => void;
  onOpenAuthority?: (authority: QualityAuthorityProjection) => void;
  onAcknowledgeAlert?: (alert: QualityOperationsAlert) => void;
  onQueueRecertification?: (authority: QualityAuthorityProjection) => void;
}

const HORIZONS: Array<{ value: OperationsHorizonBand; label: string }> = [
  { value: "expired", label: "EXPIRED" },
  { value: "24_hours", label: "24 HOURS" },
  { value: "7_days", label: "7 DAYS" },
  { value: "30_days", label: "30 DAYS" },
  { value: "healthy", label: "HEALTHY" },
  { value: "unavailable", label: "UNAVAILABLE" },
];

function useMobileViewport(): boolean {
  const query = "(max-width: 768px)";
  const [mobile, setMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.(query).matches === true,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return mobile;
}

function dateLabel(value: string | null): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "未返回";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function minuteLabel(value: number | null): string {
  if (value === null) return "不可判定";
  if (value <= 0) return `已过期 ${Math.abs(value)} 分钟`;
  if (value < 60) return `${value} 分钟`;
  if (value < 1440) return `${Math.floor(value / 60)} 小时 ${value % 60} 分钟`;
  return `${Math.floor(value / 1440)} 天`;
}

function severityTheme(value: QualityAuthorityProjection["severity"]) {
  if (value === "healthy") return "success" as const;
  if (value === "warning") return "warning" as const;
  if (value === "critical") return "danger" as const;
  return "default" as const;
}

function severityLabel(row: QualityAuthorityProjection): string {
  if (row.state === "unavailable") return "Authority unavailable";
  if (row.severity === "critical") return "Critical";
  if (row.severity === "warning") return "Warning";
  return "Healthy";
}

function authorityTitle(row: QualityAuthorityProjection): string {
  const release = row.release_number === null ? row.release_id : `Release ${row.release_number}`;
  return `${row.channel_name} · ${release}`;
}

export default function QualityOperationsCenter({
  datasetName,
  workspaceName = "未绑定 Workspace",
  summary,
  alerts,
  jobs,
  selectedHorizon = "all",
  loading = false,
  error = null,
  readOnly = false,
  onHorizonChange,
  onRefresh,
  onOpenAuthority,
  onAcknowledgeAlert,
  onQueueRecertification,
}: QualityOperationsCenterProps) {
  const mobile = useMobileViewport();
  const filtered = useMemo(() => {
    const items = summary?.items ?? [];
    if (selectedHorizon === "all") return items;
    return items.filter((item) => item.horizon_band === selectedHorizon);
  }, [selectedHorizon, summary?.items]);

  const columns = useMemo<PrimaryTableCol<QualityAuthorityProjection>[]>(
    () => [
      {
        colKey: "authority",
        title: "发布权威",
        minWidth: 230,
        cell: ({ row }) => (
          <button
            type="button"
            className="quality-operations__authority-link"
            onClick={() => onOpenAuthority?.(row)}
          >
            <strong>{row.channel_name}</strong>
            <span>
              {row.release_number === null ? row.release_id : `Release ${row.release_number}`}
            </span>
          </button>
        ),
      },
      {
        colKey: "gate_state",
        title: "Quality Gate",
        width: 150,
        cell: ({ row }) => (
          <Tag
            theme={
              row.gate_state === "passed"
                ? "success"
                : row.gate_state === "blocked"
                  ? "danger"
                  : "default"
            }
          >
            {row.gate_state}
          </Tag>
        ),
      },
      {
        colKey: "horizon_band",
        title: "SLO horizon",
        minWidth: 170,
        cell: ({ row }) => (
          <div className="quality-operations__horizon-cell">
            <Tag theme={severityTheme(row.severity)}>{severityLabel(row)}</Tag>
            <span>{minuteLabel(row.minutes_to_certification_expiry)}</span>
          </div>
        ),
      },
      {
        colKey: "active_alert_count",
        title: "Alert",
        width: 100,
        cell: ({ row }) => <span>{row.active_alert_count}</span>,
      },
      {
        colKey: "recertification_job_status",
        title: "Recertification",
        width: 150,
        cell: ({ row }) => row.recertification_job_status ?? "未排队",
      },
      {
        colKey: "last_observed_at",
        title: "Observed",
        width: 150,
        cell: ({ row }) => dateLabel(row.last_observed_at),
      },
      {
        colKey: "action",
        title: "",
        width: 56,
        cell: ({ row }) => (
          <Button
            variant="text"
            shape="square"
            aria-label={`查看 ${row.channel_name} 详情`}
            icon={<ChevronRightIcon />}
            onClick={() => onOpenAuthority?.(row)}
          />
        ),
      },
    ],
    [onOpenAuthority],
  );

  if (loading && !summary) {
    return (
      <section className="quality-operations quality-operations--state">
        正在读取质量运营权威…
      </section>
    );
  }

  if (error && !summary) {
    return (
      <section className="quality-operations quality-operations--state">
        <div role="alert">
          <Alert theme="error" title="Quality Operations unavailable" message={error} />
        </div>
      </section>
    );
  }

  return (
    <section className="quality-operations" aria-label="Release Quality Operations Center">
      <header className="quality-operations__header">
        <div className="quality-operations__authority-mark" aria-hidden="true">
          <SecuredIcon />
        </div>
        <div className="quality-operations__heading">
          <span>QUALITY OPERATIONS</span>
          <h2>{datasetName}</h2>
          <p>
            {workspaceName} · Tenant {summary?.tenant_id ?? "未验证"} · Dataset{" "}
            {summary?.dataset_id ?? "未验证"}
          </p>
        </div>
        <div className="quality-operations__header-actions">
          {readOnly ? <Tag theme="warning">只读模式</Tag> : <Tag theme="success">Governed</Tag>}
          <Button variant="outline" icon={<RefreshIcon />} onClick={onRefresh}>
            刷新
          </Button>
        </div>
      </header>

      <div className="quality-operations__status-line">
        <span>
          <TimeIcon aria-hidden="true" /> Last completed scan{" "}
          {dateLabel(summary?.last_completed_scan_at ?? null)}
        </span>
        <span>Generated {dateLabel(summary?.generated_at ?? null)}</span>
      </div>

      <nav className="quality-operations__rail" aria-label="SLO Horizon Rail">
        {HORIZONS.map((item) => {
          const count = summary?.horizon_counts[item.value] ?? 0;
          return (
            <button
              key={item.value}
              type="button"
              className={`quality-operations__rail-segment is-${item.value}${selectedHorizon === item.value ? " is-selected" : ""}`}
              aria-pressed={selectedHorizon === item.value}
              aria-label={`${item.label} ${count}`}
              onClick={() => onHorizonChange?.(item.value)}
            >
              <span>{item.label}</span>
              <strong>{count}</strong>
            </button>
          );
        })}
      </nav>

      <div className="quality-operations__workspace">
        <main className="quality-operations__main">
          <div className="quality-operations__section-heading">
            <div>
              <span>AT-RISK AUTHORITY</span>
              <h3>发布质量权威</h3>
            </div>
            <Tag variant="outline">{filtered.length} authorities</Tag>
          </div>

          {filtered.length === 0 ? (
            <Empty title="当前筛选范围没有发布质量权威" />
          ) : mobile ? (
            <div
              className="quality-operations__mobile-cards"
              data-testid="quality-operations-mobile-cards"
            >
              {filtered.map((row) => (
                <article
                  key={`${row.release_id}:${row.channel_id}:${row.release_role}`}
                  className="quality-operations__mobile-card"
                >
                  <div className="quality-operations__mobile-card-head">
                    <div>
                      <span>{row.release_role.toUpperCase()}</span>
                      <h4>{authorityTitle(row)}</h4>
                    </div>
                    <Tag theme={severityTheme(row.severity)}>{severityLabel(row)}</Tag>
                  </div>
                  <dl>
                    <div>
                      <dt>Quality Gate</dt>
                      <dd>{row.gate_state}</dd>
                    </div>
                    <div>
                      <dt>Expiry</dt>
                      <dd>{minuteLabel(row.minutes_to_certification_expiry)}</dd>
                    </div>
                    <div>
                      <dt>Alert</dt>
                      <dd>{row.active_alert_count}</dd>
                    </div>
                    <div>
                      <dt>Job</dt>
                      <dd>{row.recertification_job_status ?? "未排队"}</dd>
                    </div>
                  </dl>
                  <Button block variant="outline" onClick={() => onOpenAuthority?.(row)}>
                    查看 {row.channel_name} 详情
                  </Button>
                </article>
              ))}
            </div>
          ) : (
            <div
              data-testid="quality-operations-desktop-table"
              className="quality-operations__table-shell"
            >
              <PrimaryTable
                rowKey="release_id"
                data={filtered}
                columns={columns}
                bordered={false}
                hover
                stripe
              />
            </div>
          )}
        </main>

        <aside className="quality-operations__aside" aria-label="Alert 与 Recertification queue">
          <section className="quality-operations__inbox">
            <div className="quality-operations__aside-heading">
              <NotificationIcon aria-hidden="true" />
              <div>
                <span>OPERATIONS INBOX</span>
                <h3>Alert Inbox</h3>
              </div>
            </div>
            {alerts.length === 0 ? (
              <p className="quality-operations__muted">当前没有 active Alert</p>
            ) : (
              alerts.map((alert) => (
                <article key={alert.id} className="quality-operations__inbox-item">
                  <div>
                    <Tag theme={alert.severity === "critical" ? "danger" : "warning"}>
                      {alert.severity}
                    </Tag>
                    <span>{alert.status}</span>
                  </div>
                  <strong>{alert.alert_type}</strong>
                  <p>
                    {alert.release_id} · {alert.channel_id}
                  </p>
                  {!readOnly && onAcknowledgeAlert && alert.status === "open" ? (
                    <Button
                      size="small"
                      variant="outline"
                      onClick={() => onAcknowledgeAlert?.(alert)}
                    >
                      确认告警
                    </Button>
                  ) : null}
                </article>
              ))
            )}
          </section>

          <section className="quality-operations__jobs">
            <div className="quality-operations__aside-heading">
              <TimeIcon aria-hidden="true" />
              <div>
                <span>DURABLE QUEUE</span>
                <h3>Recertification</h3>
              </div>
            </div>
            {jobs.length === 0 ? (
              <p className="quality-operations__muted">当前没有 Recertification Job</p>
            ) : (
              jobs.map((job) => (
                <article key={job.id} className="quality-operations__job-item">
                  <div>
                    <strong>{job.status}</strong>
                    <span>{job.trigger}</span>
                  </div>
                  <p>
                    {job.release_id} · Attempt {job.attempt_count}/{job.max_attempts}
                  </p>
                </article>
              ))
            )}
            {!readOnly && onQueueRecertification && filtered[0] ? (
              <Button block theme="primary" onClick={() => onQueueRecertification?.(filtered[0]!)}>
                排队再认证
              </Button>
            ) : null}
          </section>
        </aside>
      </div>
    </section>
  );
}
