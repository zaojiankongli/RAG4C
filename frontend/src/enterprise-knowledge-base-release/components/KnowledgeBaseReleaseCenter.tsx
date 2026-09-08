import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ComponentType,
  type HTMLAttributes,
} from "react";
import { Alert, Button, PrimaryTable, Select, Tag, type PrimaryTableCol } from "tdesign-react";
import { CloudUploadIcon, RefreshIcon } from "tdesign-icons-react";
import PageState from "../../components/PageState";
import type { ReleaseQualityApi } from "../../enterprise-release-quality/api/qualityApi";
import QualityGateStrip from "../../enterprise-release-quality/components/QualityGateStrip";
import ReleaseQualityPanel from "../../enterprise-release-quality/components/ReleaseQualityPanel";
import { useReleaseQuality } from "../../enterprise-release-quality/hooks/useReleaseQuality";
import type { QualityGateState } from "../../enterprise-release-quality/model/qualityModel";
import { useKnowledgeBaseDrawerCoordinator } from "../../enterprise-knowledge-base-shell/KnowledgeBaseDrawerCoordinatorContext";
import type {
  ReleaseChannel,
  ReleaseChannelSummary,
  ReleaseManifest,
  ReleaseMutationKind,
  ReleaseMutationOutcome,
} from "../model/releaseModel";
import {
  releaseComparisonLabel,
  releaseComparisonTheme,
  releaseMutationLabel,
  releaseReadinessLabel,
  releaseReadinessTheme,
  releaseRiskLabel,
  releaseStatusLabel,
  safeReleaseDisplayText,
} from "../model/releaseModel";
import { useKnowledgeBaseReleases, type ReleaseApi } from "../hooks/useKnowledgeBaseReleases";
import ReleaseDetailDrawer from "./ReleaseDetailDrawer";
import ReleaseMutationDialog, {
  type ReleaseCaptureFence,
  type ReleaseMutationInput,
} from "./ReleaseMutationDialog";
import "../knowledge-base-release.css";

export interface KnowledgeBaseReleaseCenterProps {
  active: boolean;
  scope: {
    tenantId: string;
    datasetId: string;
    actorToken: string;
  };
  datasetName?: string;
  workspaceName?: string;
  readOnly?: boolean;
  scopeVerified?: boolean;
  api?: ReleaseApi;
  qualityApi?: ReleaseQualityApi;
}

type AccessibleSelectInputProps = NonNullable<ComponentProps<typeof Select>["inputProps"]> &
  HTMLAttributes<HTMLDivElement>;
const AccessibleSelect = Select as unknown as ComponentType<
  ComponentProps<typeof Select> & HTMLAttributes<HTMLDivElement>
>;

const CHANNEL_SELECT_PROPS: AccessibleSelectInputProps = {
  role: "combobox",
  "aria-label": "发布 Channel",
  "aria-haspopup": "listbox",
};

function useReleaseViewport(): boolean {
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

function shortId(value: string | null | undefined): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) return "未返回";
  return normalized.length > 24 ? `${normalized.slice(0, 11)}…${normalized.slice(-8)}` : normalized;
}

function snapshotLabel(snapshot: ReleaseChannelSummary["candidate"]): string {
  if (!snapshot) return "未返回";
  return snapshot.release_number == null
    ? snapshot.release_id || "未返回"
    : `Release ${snapshot.release_number}`;
}

function statusTheme(
  status: string | null | undefined,
): "success" | "warning" | "danger" | "default" {
  if (status === "published" || status === "aligned" || status === "ready") return "success";
  if (
    status === "candidate" ||
    status === "draft" ||
    status === "drifted" ||
    status === "blocked"
  ) {
    return "warning";
  }
  if (status === "retired" || status === "unavailable") return "danger";
  return "default";
}

function snapshotStatus(snapshot: ReleaseChannelSummary["candidate"]): string {
  return snapshot?.status ? releaseStatusLabel(snapshot.status) : "未返回";
}

function SnapshotCell({
  label,
  snapshot,
}: {
  label: string;
  snapshot: ReleaseChannelSummary["candidate"];
}) {
  return (
    <div className="knowledge-base-release-channel-summary__snapshot">
      <span>{label}</span>
      <strong>{snapshotLabel(snapshot)}</strong>
      <small>
        <Tag theme={statusTheme(snapshot?.status)} variant="light-outline" size="small">
          {snapshotStatus(snapshot)}
        </Tag>
        {snapshot?.revision == null
          ? " · revision 未返回"
          : ` · ${revisionLabel(snapshot.revision)}`}
      </small>
    </div>
  );
}

function ConfiguredCell({ configured }: { configured: ReleaseChannelSummary["configured"] }) {
  return (
    <div className="knowledge-base-release-channel-summary__snapshot">
      <span>Configured</span>
      <strong>
        {configured
          ? `P${numberLabel(configured.profile_revision)} · M${numberLabel(configured.mutation_generation)}`
          : "未返回"}
      </strong>
      <small>{configured?.policy_digest ? "策略摘要已返回" : "策略摘要未返回"}</small>
    </div>
  );
}

function safeOutcomeMessage(outcome: ReleaseMutationOutcome): string {
  const message = safeReleaseDisplayText(outcome.message);
  if (message) return message;
  return "服务端已接受本次 revision-fenced 操作，页面保留该结果作为当前反馈。";
}

function MutationNotice({
  outcome,
  error,
  readOnly,
  onRetry,
}: {
  outcome: ReleaseMutationOutcome | null;
  error: Error | null;
  readOnly: boolean;
  onRetry: () => Promise<ReleaseMutationOutcome | null>;
}) {
  if (error) {
    return (
      <div role="alert">
        <Alert
          theme="error"
          title="Release 操作未提交"
          message={
            <span>
              {safeReleaseDisplayText(error.message) || "服务端拒绝了本次 Release 操作。"}
              {!readOnly ? (
                <Button variant="text" theme="primary" size="small" onClick={() => void onRetry()}>
                  使用同一请求重试
                </Button>
              ) : null}
            </span>
          }
        />
      </div>
    );
  }
  if (!outcome) return null;
  if (outcome.state === "approval_required") {
    const submitted = Boolean(outcome.approval_request_id);
    return (
      <div role="status" aria-live="polite">
        <Alert
          theme="warning"
          title={`${releaseMutationLabel(outcome.operation)} ${submitted ? "已提交审批" : "需要企业审批"}`}
          message={
            submitted
              ? "审批请求已提交；原始审批票据不会在页面展示，结果以服务端审批事实为准。"
              : safeReleaseDisplayText(outcome.message) ||
                "当前尚未生成审批请求，请先完成审批策略与申请流程。"
          }
        />
      </div>
    );
  }
  if (outcome.state === "applied") {
    const title =
      outcome.operation === "capture"
        ? "候选 Release 已生成"
        : outcome.operation === "promote"
          ? "Release 已发布"
          : "Channel 已回滚";
    return (
      <div role="status" aria-live="polite">
        <Alert theme="success" title={title} message={safeOutcomeMessage(outcome)} />
      </div>
    );
  }
  return (
    <Alert
      theme={outcome.state === "rejected" ? "error" : "warning"}
      title={`${releaseMutationLabel(outcome.operation)} 未完成`}
      message="服务端没有返回可应用的 Release 结果，当前页面不会推测或补齐状态。"
    />
  );
}

function ChannelSummary({
  channel,
  summary,
  historyCount,
  readOnly,
  captureReady,
  saving,
  onChange,
  onRefresh,
  onCapture,
  channels,
}: {
  channel: ReleaseChannel | null;
  summary: ReleaseChannelSummary | null;
  historyCount: number | null;
  readOnly: boolean;
  captureReady: boolean;
  saving: boolean;
  onChange: (channelId: string) => void;
  onRefresh: () => void;
  onCapture: (trigger: HTMLElement) => void;
  channels: ReleaseChannel[];
}) {
  const readiness = summary?.readiness;
  const comparison = summary?.comparison_state ?? "unavailable";
  const mainActionDisabled = readOnly || !captureReady || channel?.status !== "active" || saving;
  return (
    <section
      className="knowledge-base-release-channel-summary"
      aria-labelledby="release-channel-summary-title"
    >
      <div className="knowledge-base-release-channel-summary__topline">
        <div>
          <span className="knowledge-base-release-center__section-kicker">CHANNEL CONTROL</span>
          <h3 id="release-channel-summary-title">发布 Channel 摘要</h3>
          <p>
            <strong>{channel?.name || "Channel 未返回"}</strong> · {channel?.code || "code 未返回"}{" "}
            · 风险 {channel ? releaseRiskLabel(channel.risk_tier) : "未返回"}
          </p>
        </div>
        <div className="knowledge-base-release-channel-summary__controls">
          <label className="knowledge-base-release-channel-summary__select-field">
            <span>发布 Channel</span>
            <AccessibleSelect
              value={channel?.id ?? ""}
              options={channels.map((item) => ({
                value: item.id,
                label: `${item.name} · ${item.code}`,
              }))}
              inputProps={CHANNEL_SELECT_PROPS}
              disabled={channels.length === 0}
              onChange={(value) => onChange(String(value))}
            />
          </label>
          <Button
            variant="text"
            icon={<RefreshIcon />}
            aria-label="刷新 Release 事实"
            onClick={onRefresh}
            disabled={saving}
          >
            刷新
          </Button>
          <Button
            tag="button"
            theme="primary"
            icon={<CloudUploadIcon />}
            disabled={mainActionDisabled}
            aria-label="生成 Release 候选"
            onClick={(event) => onCapture(event.currentTarget)}
            title={
              !captureReady && !readOnly
                ? "最近一次 Release 没有返回完整 revision fence，不能安全生成候选"
                : undefined
            }
          >
            生成候选
          </Button>
        </div>
      </div>
      <div className="knowledge-base-release-channel-summary__grid">
        <ConfiguredCell configured={summary?.configured ?? null} />
        <SnapshotCell label="Candidate" snapshot={summary?.candidate ?? null} />
        <SnapshotCell label="Effective" snapshot={summary?.effective ?? null} />
        <SnapshotCell label="Serving" snapshot={summary?.serving ?? null} />
      </div>
      <div className="knowledge-base-release-channel-summary__status-row">
        <div>
          <span>当前一致性</span>
          <Tag theme={releaseComparisonTheme(comparison)} variant="light-outline">
            {releaseComparisonLabel(comparison)}
          </Tag>
        </div>
        <div>
          <span>Readiness</span>
          <Tag
            theme={releaseReadinessTheme(readiness?.state ?? "unavailable")}
            variant="light-outline"
          >
            {releaseReadinessLabel(readiness?.state ?? "unavailable")}
          </Tag>
          <small>
            {readiness?.blocker_count == null
              ? "阻断数未返回"
              : `${readiness.blocker_count} 个阻断`}
          </small>
        </div>
        <div>
          <span>Serving generation</span>
          <strong>{numberLabel(summary?.serving_generation)}</strong>
        </div>
        <div>
          <span>Channel revision</span>
          <strong>{revisionLabel(summary?.revision ?? channel?.revision)}</strong>
        </div>
        <div>
          <span>历史 Release</span>
          <strong>{historyCount == null ? "未返回" : historyCount}</strong>
        </div>
      </div>
      {readiness?.reason ? (
        <div className="knowledge-base-release-channel-summary__notice" role="status">
          <span>{readiness.reason}</span>
          {readOnly ? <Tag variant="light-outline">变更已禁用</Tag> : null}
        </div>
      ) : readOnly ? (
        <div className="knowledge-base-release-channel-summary__notice" role="status">
          <span>当前身份仅允许读取 Release authority。</span>
          <Tag variant="light-outline">只读事实</Tag>
        </div>
      ) : null}
    </section>
  );
}

function ReleaseHistory({
  items,
  status,
  error,
  nextCursor,
  invalidItemCount,
  authoritativeCount,
  qualityReleaseId,
  qualityState,
  mobile,
  onOpen,
  onLoadMore,
}: {
  items: ReleaseManifest[];
  status: "idle" | "loading" | "ready" | "error";
  error: Error | null;
  nextCursor: string | null;
  invalidItemCount: number;
  authoritativeCount: number | null;
  qualityReleaseId: string | null;
  qualityState: QualityGateState | null;
  mobile: boolean;
  onOpen: (release: ReleaseManifest, trigger: HTMLElement) => void;
  onLoadMore: () => void;
}) {
  const columns = useMemo<PrimaryTableCol<ReleaseManifest>[]>(
    () => [
      {
        colKey: "release_number",
        title: "Release",
        width: 230,
        cell: ({ row }) => (
          <div className="knowledge-base-release-history__release-cell">
            <strong>Release {row.release_number}</strong>
            <span>{shortId(row.id)}</span>
          </div>
        ),
      },
      {
        colKey: "status",
        title: "状态",
        width: 110,
        cell: ({ row }) => (
          <div className="knowledge-base-release-history__status-cell">
            <Tag theme={statusTheme(row.status)} variant="light-outline">
              {releaseStatusLabel(row.status)}
            </Tag>
            {row.id === qualityReleaseId && qualityState ? (
              <Tag
                theme={
                  qualityState === "passed"
                    ? "success"
                    : qualityState === "waived"
                      ? "warning"
                      : qualityState === "unavailable"
                        ? "danger"
                        : "default"
                }
                variant="light-outline"
                size="small"
              >
                {qualityState === "passed"
                  ? "已认证"
                  : qualityState === "waived"
                    ? "已豁免"
                    : qualityState === "not_required"
                      ? "No gate"
                      : qualityState === "blocked"
                        ? "Quality blocked"
                        : "Quality unavailable"}
              </Tag>
            ) : null}
          </div>
        ),
      },
      {
        colKey: "readiness_state",
        title: "Readiness",
        width: 125,
        cell: ({ row }) => (
          <Tag theme={releaseReadinessTheme(row.readiness_state)} variant="light-outline">
            {releaseReadinessLabel(row.readiness_state)}
          </Tag>
        ),
      },
      {
        colKey: "entry_count",
        title: "Entries",
        width: 90,
        align: "right",
        cell: ({ row }) => numberLabel(row.entry_count),
      },
      {
        colKey: "revision",
        title: "Fence",
        width: 170,
        cell: ({ row }) => (
          <div className="knowledge-base-release-history__fence-cell">
            <span>{revisionLabel(row.revision)}</span>
            <small>
              P{row.profile_revision} · W{row.workspace_revision}
            </small>
          </div>
        ),
      },
      {
        colKey: "created_at",
        title: "创建时间",
        width: 170,
        cell: ({ row }) => dateLabel(row.created_at),
      },
      {
        colKey: "actions",
        title: "操作",
        width: 100,
        cell: ({ row }) => (
          <Button
            variant="text"
            size="small"
            aria-label={`查看 Release ${row.release_number}`}
            onClick={(event) => onOpen(row, event.currentTarget)}
          >
            查看
          </Button>
        ),
      },
    ],
    [onOpen, qualityReleaseId, qualityState],
  );

  if (status === "loading" && items.length === 0) {
    return (
      <PageState
        compact
        status="loading"
        title="正在读取 Release history"
        description="正在读取当前 Channel 的不可变发布事实。"
      />
    );
  }
  if (status === "error" && items.length === 0) {
    return (
      <PageState
        compact
        status="error"
        title="Release history 不可用"
        description={safeReleaseDisplayText(error?.message) || "服务端没有返回可验证的发布历史。"}
      />
    );
  }
  if (items.length === 0 && (invalidItemCount > 0 || Number(authoritativeCount ?? 0) > 0)) {
    return (
      <PageState
        compact
        status="error"
        title="Release authority 无法验证"
        description="服务端声明存在 Release，但返回行未通过 ID、digest 或 revision 校验。"
      />
    );
  }
  if (items.length === 0) {
    return (
      <PageState
        compact
        status="empty"
        title="当前 Channel 没有 Release"
        description="服务端返回了真实空列表，页面不会用演示数据填充。"
      />
    );
  }

  return (
    <section className="knowledge-base-release-history" aria-labelledby="release-history-title">
      <div className="knowledge-base-release-history__heading">
        <div>
          <span className="knowledge-base-release-center__section-kicker">RELEASE HISTORY</span>
          <h3 id="release-history-title">不可变 Release history</h3>
          <p>每条记录都保留 Manifest digest、revision fence 和资源数量，支持从同一事实打开详情。</p>
        </div>
        {status === "loading" ? (
          <span className="knowledge-base-release-history__loading">正在更新…</span>
        ) : null}
      </div>
      {mobile ? (
        <div
          className="knowledge-base-release-history__cards"
          data-testid="release-history-mobile-cards"
        >
          {items.map((item) => (
            <article key={item.id} className="knowledge-base-release-history__card">
              <div className="knowledge-base-release-history__card-topline">
                <div>
                  <span className="knowledge-base-release-history__card-kicker">RELEASE</span>
                  <strong>#{item.release_number}</strong>
                </div>
                <div className="knowledge-base-release-history__card-tags">
                  <Tag theme={statusTheme(item.status)} variant="light-outline">
                    {releaseStatusLabel(item.status)}
                  </Tag>
                  {item.id === qualityReleaseId && qualityState ? (
                    <Tag
                      theme={
                        qualityState === "passed"
                          ? "success"
                          : qualityState === "waived"
                            ? "warning"
                            : "default"
                      }
                      variant="light-outline"
                      size="small"
                    >
                      {qualityState === "passed"
                        ? "已认证"
                        : qualityState === "waived"
                          ? "已豁免"
                          : qualityState === "blocked"
                            ? "质量阻断"
                            : qualityState}
                    </Tag>
                  ) : null}
                </div>
              </div>
              <div className="knowledge-base-release-history__card-id">{shortId(item.id)}</div>
              <div className="knowledge-base-release-history__card-meta">
                <div>
                  <span>Readiness</span>
                  <strong>
                    <Tag
                      theme={releaseReadinessTheme(item.readiness_state)}
                      variant="light-outline"
                      size="small"
                    >
                      {releaseReadinessLabel(item.readiness_state)}
                    </Tag>
                  </strong>
                </div>
                <div>
                  <span>Entries</span>
                  <strong>{numberLabel(item.entry_count)}</strong>
                </div>
                <div>
                  <span>Fence</span>
                  <strong>{revisionLabel(item.revision)}</strong>
                </div>
                <div>
                  <span>Created</span>
                  <strong>{dateLabel(item.created_at)}</strong>
                </div>
              </div>
              <Button
                className="knowledge-base-release-history__card-action"
                variant="outline"
                aria-label={`查看 Release ${item.release_number}`}
                onClick={(event) => onOpen(item, event.currentTarget)}
              >
                查看 Release
              </Button>
            </article>
          ))}
        </div>
      ) : (
        <div
          className="knowledge-base-release-history__table"
          data-testid="release-history-desktop-table"
          tabIndex={0}
          aria-label="Release history 表格，可横向滚动"
        >
          <PrimaryTable
            rowKey="id"
            data={items}
            columns={columns}
            tableLayout="fixed"
            bordered={false}
            hover
            size="small"
          />
        </div>
      )}
      {nextCursor ? (
        <div className="knowledge-base-release-history__footer">
          <span>还有更多不可变 Release，使用 opaque cursor 继续读取。</span>
          <Button variant="outline" onClick={onLoadMore} disabled={status === "loading"}>
            {status === "loading" ? "正在加载" : "加载更多"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

export default function KnowledgeBaseReleaseCenter({
  active,
  scope,
  datasetName,
  workspaceName,
  readOnly = false,
  scopeVerified = true,
  api,
  qualityApi,
}: KnowledgeBaseReleaseCenterProps) {
  const mobile = useReleaseViewport();
  const coordinator = useKnowledgeBaseDrawerCoordinator();
  const { activeDrawer, openDrawer, closeDrawer, setReleaseContext } = coordinator;
  const releases = useKnowledgeBaseReleases(
    scope,
    active && scopeVerified,
    api ? { api } : undefined,
  );
  const detailOpen = releases.detail.open;
  const detailClose = releases.detail.close;
  const detailValue = releases.detail.value;
  const mutationClear = releases.mutation.clear;
  const mutationSubmit = releases.mutation.submit;
  const channelsRefresh = releases.channels.refresh;
  const [detailVisible, setDetailVisible] = useState(false);
  const [mutationDialog, setMutationDialog] = useState<{
    kind: ReleaseMutationKind;
    release?: ReleaseManifest | null;
    targetRelease?: ReleaseManifest | null;
  } | null>(null);
  const [notice, setNotice] = useState<ReleaseMutationOutcome | null>(null);
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const mutationTriggerRef = useRef<HTMLElement | null>(null);

  const selectedChannel = useMemo(
    () => releases.channels.items.find((item) => item.id === releases.channels.selectedId) ?? null,
    [releases.channels.items, releases.channels.selectedId],
  );
  const summary = selectedChannel?.summary ?? releases.history.summary;
  const latestRelease = releases.history.items[0] ?? null;
  const qualityReleaseId = detailVisible
    ? (detailValue?.manifest.id ?? latestRelease?.id ?? null)
    : (latestRelease?.id ?? null);
  const qualityEnabled = Boolean(
    active &&
    scopeVerified &&
    selectedChannel?.id &&
    qualityReleaseId &&
    (qualityApi !== undefined || api === undefined),
  );
  const quality = useReleaseQuality(scope, {
    enabled: qualityEnabled,
    releaseId: qualityReleaseId,
    channelId: selectedChannel?.id ?? null,
    ...(qualityApi ? { api: qualityApi } : {}),
  });

  useEffect(() => {
    if (!active || !scopeVerified) {
      setReleaseContext(null);
      return;
    }
    setReleaseContext({
      channelName: selectedChannel?.name ?? null,
      effectiveReleaseNumber: summary?.effective?.release_number ?? null,
      servingReleaseNumber: summary?.serving?.release_number ?? null,
      unavailableReason: selectedChannel?.id || summary ? null : "Release Channel authority 未返回",
    });
    return () => setReleaseContext(null);
  }, [
    active,
    scopeVerified,
    selectedChannel?.id,
    selectedChannel?.name,
    setReleaseContext,
    summary,
  ]);
  const captureFence = useMemo<ReleaseCaptureFence | null>(() => {
    const configured = summary?.configured;
    const servingGeneration = summary?.serving_generation;
    if (!configured || servingGeneration === null || servingGeneration === undefined) return null;
    const values = [
      configured.profile_revision,
      configured.ownership_revision,
      configured.workspace_revision,
      configured.mutation_generation,
      servingGeneration,
    ];
    return values.every((value) => Number.isInteger(value) && Number(value) >= 0) &&
      Number(configured.profile_revision) > 0 &&
      Number(configured.ownership_revision) > 0 &&
      Number(configured.workspace_revision) > 0
      ? {
          profileRevision: Number(configured.profile_revision),
          ownershipRevision: Number(configured.ownership_revision),
          workspaceRevision: Number(configured.workspace_revision),
          mutationGeneration: Number(configured.mutation_generation),
          servingGeneration,
        }
      : null;
  }, [summary]);

  const focusTrigger = useCallback((ref: React.MutableRefObject<HTMLElement | null>) => {
    window.setTimeout(() => ref.current?.focus(), 0);
  }, []);

  const closeDetail = useCallback(() => {
    setDetailVisible(false);
    detailClose();
    closeDrawer("release-detail");
    focusTrigger(detailTriggerRef);
  }, [closeDrawer, detailClose, focusTrigger]);

  useEffect(() => {
    if (detailVisible && activeDrawer !== null && activeDrawer !== "release-detail") {
      setDetailVisible(false);
      detailClose();
    }
  }, [activeDrawer, detailClose, detailVisible]);

  useEffect(() => {
    if (!detailVisible || mutationDialog) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (document.querySelector('.release-quality-certify-dialog[role="dialog"]')) return;
      event.preventDefault();
      closeDetail();
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [closeDetail, detailVisible, mutationDialog]);

  const closeMutationDialog = useCallback(() => {
    setMutationDialog(null);
    focusTrigger(mutationTriggerRef);
  }, [focusTrigger]);

  const openDetail = useCallback(
    (release: ReleaseManifest, trigger: HTMLElement) => {
      detailTriggerRef.current = trigger;
      openDrawer("release-detail");
      void detailOpen(release.id);
      setDetailVisible(true);
    },
    [detailOpen, openDrawer],
  );

  const openMutation = useCallback(
    (
      kind: ReleaseMutationKind,
      trigger: HTMLElement,
      release: ReleaseManifest | null = null,
      targetRelease: ReleaseManifest | null = null,
    ) => {
      mutationTriggerRef.current = trigger;
      mutationClear();
      setNotice(null);
      setMutationDialog({ kind, release, targetRelease });
    },
    [mutationClear],
  );

  const submitMutation = useCallback(
    async (kind: ReleaseMutationKind, input: ReleaseMutationInput) => {
      const outcome = await mutationSubmit(kind, input as never);
      if (outcome && (outcome.state === "applied" || outcome.state === "approval_required")) {
        setNotice(outcome);
        void channelsRefresh();
      }
      return outcome;
    },
    [channelsRefresh, mutationSubmit],
  );

  const openCapture = useCallback(
    (trigger: HTMLElement) => openMutation("capture", trigger),
    [openMutation],
  );
  const openPromote = useCallback(
    (release: ReleaseManifest, trigger: HTMLElement) => openMutation("promote", trigger, release),
    [openMutation],
  );
  const openRollback = useCallback(
    (target: ReleaseManifest, trigger: HTMLElement) => {
      const current = detailValue?.manifest ?? latestRelease;
      openMutation("rollback", trigger, current, target);
    },
    [detailValue?.manifest, latestRelease, openMutation],
  );

  if (!active) return null;

  if (!scopeVerified) {
    return (
      <section
        className="knowledge-base-release-center"
        aria-label="Knowledge Base Release 控制中心"
      >
        <PageState
          status="loading"
          title="等待已验证的 Workspace scope"
          description="Release authority 只在 Workspace → Dataset 作用域验证完成后读取。"
        />
      </section>
    );
  }

  const channelsLoading =
    releases.channels.status === "loading" && releases.channels.items.length === 0;
  if (channelsLoading) {
    return (
      <section
        className="knowledge-base-release-center"
        aria-label="Knowledge Base Release 控制中心"
      >
        <PageState
          status="loading"
          title="正在读取 Release 控制中心"
          description="正在读取 Channel、Release history 与当前 serving projection。"
        />
      </section>
    );
  }
  if (
    (releases.channels.status === "error" || releases.channels.invalidItemCount > 0) &&
    releases.channels.items.length === 0
  ) {
    return (
      <section
        className="knowledge-base-release-center"
        aria-label="Knowledge Base Release 控制中心"
      >
        <PageState
          status="error"
          title="Release Channel 不可用"
          description={
            safeReleaseDisplayText(releases.channels.error?.message) ||
            "服务端没有返回可验证的发布 Channel。"
          }
          extra={<Button onClick={() => void releases.channels.refresh()}>重新读取</Button>}
        />
      </section>
    );
  }

  return (
    <section className="knowledge-base-release-center" aria-label="Knowledge Base Release 控制中心">
      <header className="knowledge-base-release-center__header">
        <div className="knowledge-base-release-center__mark" aria-hidden="true">
          <CloudUploadIcon />
        </div>
        <div className="knowledge-base-release-center__heading">
          <span className="knowledge-base-release-center__section-kicker">
            RELEASE CONTROL PLANE / GOVERNED
          </span>
          <h2>Release 控制中心</h2>
          <p>
            {datasetName || scope.datasetId} · {workspaceName || "Workspace 未返回"} · 以不可变
            Manifest 管理环境发布、漂移和回滚。
          </p>
        </div>
        <div className="knowledge-base-release-center__header-meta">
          <Tag theme={readOnly ? "default" : "success"} variant="light-outline">
            {readOnly ? "只读事实" : "发布控制面"}
          </Tag>
          <span>{shortId(scope.datasetId)}</span>
        </div>
      </header>
      <MutationNotice
        outcome={notice ?? releases.mutation.outcome}
        error={releases.mutation.error}
        readOnly={readOnly}
        onRetry={releases.mutation.retry}
      />
      {qualityEnabled ? (
        <QualityGateStrip
          status={quality.gate.status}
          gate={quality.gate.value}
          error={quality.gate.error}
        />
      ) : null}
      <ChannelSummary
        channel={selectedChannel}
        summary={summary}
        historyCount={releases.history.count}
        readOnly={readOnly}
        captureReady={Boolean(captureFence)}
        saving={releases.mutation.status === "saving"}
        channels={releases.channels.items}
        onChange={(channelId) => {
          setDetailVisible(false);
          void releases.selectChannel(channelId);
        }}
        onRefresh={() => void releases.channels.refresh()}
        onCapture={openCapture}
      />
      <ReleaseHistory
        items={releases.history.items}
        status={releases.history.status}
        error={releases.history.error}
        nextCursor={releases.history.nextCursor}
        invalidItemCount={releases.history.invalidItemCount}
        authoritativeCount={releases.history.count}
        qualityReleaseId={qualityReleaseId}
        qualityState={quality.gate.value?.state ?? null}
        mobile={mobile}
        onOpen={openDetail}
        onLoadMore={() => void releases.history.loadMore()}
      />
      <ReleaseDetailDrawer
        visible={detailVisible}
        channel={selectedChannel}
        summary={summary}
        history={releases.history.items}
        detailStatus={releases.detail.status}
        detail={releases.detail.value}
        readiness={releases.detail.readiness}
        impact={releases.detail.impact}
        audit={releases.detail.audit}
        qualityPanel={
          qualityEnabled ? (
            <ReleaseQualityPanel
              gateStatus={quality.gate.status}
              gate={quality.gate.value}
              policies={quality.policies.items}
              baselines={quality.baselines.items}
              certifications={quality.certifications.items}
              certificationStatus={quality.certifications.status}
              certificationError={quality.certifications.error}
              readOnly={readOnly}
              saving={quality.mutation.status === "saving"}
              error={quality.gate.error}
              mutationError={quality.mutation.error}
              mutationOutcome={quality.mutation.outcome}
              onLoadCertifications={quality.certifications.load}
              onCertify={quality.mutation.certify}
              onRequestWaiver={quality.mutation.requestWaiver}
            />
          ) : undefined
        }
        error={releases.detail.error}
        readOnly={readOnly}
        onClose={closeDetail}
        onLoadImpact={releases.detail.loadImpact}
        onLoadAudit={releases.detail.loadAudit}
        onPromote={openPromote}
        onRollback={openRollback}
      />
      <ReleaseMutationDialog
        visible={Boolean(mutationDialog)}
        kind={mutationDialog?.kind ?? null}
        channel={selectedChannel}
        release={mutationDialog?.release ?? null}
        targetRelease={mutationDialog?.targetRelease ?? null}
        captureFence={captureFence}
        servingGeneration={summary?.serving_generation ?? null}
        readOnly={readOnly}
        saving={releases.mutation.status === "saving"}
        error={releases.mutation.error}
        onClose={closeMutationDialog}
        onRetry={releases.mutation.retry}
        onSubmit={submitMutation}
      />
    </section>
  );
}
