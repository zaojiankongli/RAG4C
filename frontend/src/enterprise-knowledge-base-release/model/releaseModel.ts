export type ReleaseChannelStatus = "active" | "archived" | (string & {});
export type ReleaseRiskTier = "low" | "medium" | "high" | (string & {});
export type ReleaseManifestStatus =
  "draft" | "candidate" | "published" | "superseded" | "retired" | (string & {});
export type ReleaseReadinessState = "ready" | "blocked" | "unavailable";
export type ReleaseComparisonState = "aligned" | "drifted" | "unavailable";
export type ReleaseMutationState = "applied" | "approval_required" | "rejected" | "unavailable";
export type ReleaseMutationKind = "capture" | "promote" | "rollback";

export interface ReleaseConfiguredState {
  profile_revision: number | null;
  mutation_generation: number | null;
  ownership_revision: number | null;
  workspace_id: string | null;
  workspace_revision: number | null;
  policy_digest: string | null;
}

export interface ReleaseStateSnapshot {
  release_id: string | null;
  release_number: number | null;
  status: ReleaseManifestStatus | null;
  revision: number | null;
  manifest_digest?: string | null;
}

export interface ReleaseBlocker {
  code: string;
  label: string;
  severity: "blocked" | "unavailable";
  count: number | null;
  reason: string | null;
}

export interface ReleaseReadiness {
  state: ReleaseReadinessState;
  blocker_count: number | null;
  blockers: ReleaseBlocker[];
  fingerprint: string | null;
  reason: string | null;
}

export interface ReleaseChannelSummary {
  channel_id: string;
  configured: ReleaseConfiguredState | null;
  candidate: ReleaseStateSnapshot | null;
  effective: ReleaseStateSnapshot | null;
  serving: ReleaseStateSnapshot | null;
  serving_generation: number | null;
  comparison_state: ReleaseComparisonState;
  readiness: ReleaseReadiness;
  revision: number | null;
}

export interface ReleaseChannel {
  id: string;
  tenant_id: string;
  code: string;
  name: string;
  status: ReleaseChannelStatus;
  risk_tier: ReleaseRiskTier;
  promotion_order: number;
  is_default_serving: boolean;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
  summary: ReleaseChannelSummary | null;
}

export interface ReleaseChannelPage {
  items: ReleaseChannel[];
  count: number | null;
  next_cursor: string | null;
  summaries: Record<string, ReleaseChannelSummary>;
  invalid_item_count?: number;
}

export interface ReleaseManifest {
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_number: number;
  status: ReleaseManifestStatus;
  profile_revision: number;
  ownership_revision: number;
  workspace_revision: number;
  mutation_generation: number;
  serving_generation: number;
  schema_version: number;
  manifest_digest: string;
  readiness_state: ReleaseReadinessState;
  readiness_fingerprint: string;
  entry_count: number;
  revision: number;
  created_at: string | null;
  created_by: string | null;
  reason: string | null;
}

export interface ReleaseEntry {
  id: string;
  resource_type: string;
  resource_id: string;
  resource_revision: number;
  content_digest: string;
  facts: Record<string, unknown>;
}

export interface ReleaseEntryPage {
  items: ReleaseEntry[];
  count: number | null;
  next_cursor: string | null;
}

export interface ReleaseHistoryPage {
  items: ReleaseManifest[];
  count: number | null;
  next_cursor: string | null;
  summary: ReleaseChannelSummary | null;
  invalid_item_count?: number;
}

export interface ReleaseDetail {
  manifest: ReleaseManifest;
  entries: ReleaseEntryPage;
}

export interface ReleaseImpactItem {
  type: string;
  id: string;
  label: string;
  action: string;
  reason: string | null;
}

export interface ReleaseImpact {
  state: ReleaseReadinessState;
  items: ReleaseImpactItem[];
  count: number | null;
  next_cursor: string | null;
  reason?: string | null;
}

export interface ReleaseAuditItem {
  id: string;
  event: string;
  actor: string;
  occurred_at: string | null;
  reason: string | null;
  request_id: string | null;
}

export interface ReleaseAuditPage {
  items: ReleaseAuditItem[];
  count: number | null;
  next_cursor: string | null;
}

export interface ReleaseMutationOutcome {
  state: ReleaseMutationState;
  operation: ReleaseMutationKind;
  resource_id: string | null;
  approval_request_id: string | null;
  revision: number | null;
  message: string | null;
  retryable: boolean;
}

export interface ReleaseProjectorOptions {
  allowUnknownStatus?: boolean;
}

type JsonPrimitive = string | number | boolean | null;

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nullableText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value);
}

const DISPLAY_SECRET_TEXT =
  /(?:rag4c-approval-ticket-|opaque-ticket-|raw-password-|stage19-idempotency-key-|sk_(?:live|test)[-_]|ghp_|xox[baprs]-|secret:\/\/|(?:ticket|token|secret|credential|password|authorization|bearer|idempotency[-_ ]?key)\s*[:=]\s*\S+|(?:mysql|postgres(?:ql)?|mongodb|redis|sqlite):\/\/\S+)/i;

export function safeReleaseDisplayText(value: unknown): string | null {
  const normalized = nullableText(value);
  if (!normalized || DISPLAY_SECRET_TEXT.test(normalized)) return null;
  return normalized;
}

function integer(value: unknown, minimum = 0): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum ? value : null;
}

function digest(value: unknown): string | null {
  const normalized = text(value);
  return normalized && /^[a-f0-9]{64}$/.test(normalized) ? normalized : null;
}

function date(value: unknown): string | null {
  return nullableText(value);
}

const UNSAFE_KEY =
  /(?:body|content|secret|token|ticket|password|credential|authorization|api[_-]?key|private[_-]?key|idempotency)/i;

function safeFacts(value: unknown, depth = 0): Record<string, unknown> {
  if (depth > 3) return {};
  const source = record(value);
  if (!source) return {};
  const result: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(source)) {
    if (UNSAFE_KEY.test(key)) continue;
    if (
      nested === null ||
      typeof nested === "string" ||
      typeof nested === "number" ||
      typeof nested === "boolean"
    ) {
      if (typeof nested === "number" && !Number.isFinite(nested)) continue;
      result[key] = nested as JsonPrimitive;
      continue;
    }
    if (Array.isArray(nested)) {
      const values = nested.filter(
        (item): item is JsonPrimitive =>
          item === null ||
          typeof item === "string" ||
          typeof item === "boolean" ||
          (typeof item === "number" && Number.isFinite(item)),
      );
      if (values.length === nested.length) result[key] = values;
      continue;
    }
    const child = safeFacts(nested, depth + 1);
    if (Object.keys(child).length > 0) result[key] = child;
  }
  return result;
}

function projectConfigured(value: unknown): ReleaseConfiguredState | null {
  const source = record(value);
  if (!source) return null;
  return {
    profile_revision: integer(source.profile_revision, 0),
    mutation_generation: integer(source.mutation_generation, 0),
    ownership_revision: integer(source.ownership_revision, 0),
    workspace_id: nullableText(source.workspace_id),
    workspace_revision: integer(source.workspace_revision, 0),
    policy_digest: nullableText(source.policy_digest),
  };
}

function projectState(value: unknown): ReleaseStateSnapshot | null {
  const source = record(value);
  if (!source) return null;
  const releaseId = nullableText(source.release_id ?? source.id);
  const releaseNumber = source.release_number === null ? null : integer(source.release_number, 1);
  const status = nullableText(source.status) as ReleaseManifestStatus | null;
  const revision = source.revision === null ? null : integer(source.revision, 1);
  if (!releaseId && releaseNumber === null && !status && revision === null) return null;
  return {
    release_id: releaseId,
    release_number: releaseNumber,
    status,
    revision,
    manifest_digest: source.manifest_digest === null ? null : digest(source.manifest_digest),
  };
}

function projectComparisonState(value: unknown): ReleaseComparisonState {
  return value === "aligned" || value === "drifted" || value === "unavailable"
    ? value
    : "unavailable";
}

export function projectReleaseReadiness(value: unknown): ReleaseReadiness {
  const source = record(value) ?? {};
  const state =
    source.state === "ready" || source.state === "blocked" || source.state === "unavailable"
      ? source.state
      : "unavailable";
  const blockers = Array.isArray(source.blockers)
    ? source.blockers.flatMap((item) => {
        const blocker = record(item);
        const code = text(blocker?.code);
        const severity = blocker?.severity === "unavailable" ? "unavailable" : "blocked";
        if (!code) return [];
        const label = safeReleaseDisplayText(blocker?.label) ?? code;
        return [
          {
            code,
            label,
            severity,
            count: integer(blocker?.count, 0),
            reason: safeReleaseDisplayText(blocker?.reason),
          } satisfies ReleaseBlocker,
        ];
      })
    : [];
  return {
    state,
    blocker_count: source.blocker_count === null ? null : integer(source.blocker_count, 0),
    blockers,
    fingerprint: source.fingerprint === null ? null : digest(source.fingerprint),
    reason: safeReleaseDisplayText(source.reason),
  };
}

export function projectReleaseChannelSummary(value: unknown): ReleaseChannelSummary | null {
  const source = record(value);
  const channelId = text(source?.channel_id ?? source?.id);
  if (!channelId) return null;
  const readiness = projectReleaseReadiness(source?.readiness);
  return {
    channel_id: channelId,
    configured: projectConfigured(source?.configured),
    candidate: projectState(source?.candidate),
    effective: projectState(source?.effective),
    serving: projectState(source?.serving),
    serving_generation:
      source?.serving_generation === null ? null : integer(source?.serving_generation, 0),
    comparison_state: projectComparisonState(source?.comparison_state ?? source?.state),
    readiness,
    revision: source?.revision === null ? null : integer(source?.revision, 1),
  };
}

export function projectReleaseChannel(value: unknown): ReleaseChannel | null {
  const source = record(value);
  if (!source) return null;
  const id = text(source.id ?? source.channel_id);
  const tenantId = text(source.tenant_id);
  const code = text(source.code);
  const name = text(source.name);
  const status = text(source.status);
  const riskTier = text(source.risk_tier);
  const promotionOrder = integer(source.promotion_order, 0);
  const revision = integer(source.revision, 1);
  if (
    !id ||
    !tenantId ||
    !code ||
    !name ||
    !status ||
    !riskTier ||
    promotionOrder === null ||
    revision === null
  ) {
    return null;
  }
  return {
    id,
    tenant_id: tenantId,
    code,
    name,
    status,
    risk_tier: riskTier,
    promotion_order: promotionOrder,
    is_default_serving: source.is_default_serving === true,
    revision,
    created_at: date(source.created_at),
    updated_at: date(source.updated_at),
    summary: projectReleaseChannelSummary(source.summary),
  };
}

export function projectReleaseChannelPage(value: unknown): ReleaseChannelPage {
  const source = record(value) ?? {};
  const rawItems = Array.isArray(source.items) ? source.items : [];
  const items = rawItems.flatMap((item) => {
    const channel = projectReleaseChannel(item);
    return channel ? [channel] : [];
  });
  const summaries: Record<string, ReleaseChannelSummary> = {};
  const summarySource = record(source.summaries);
  if (summarySource) {
    for (const [id, value] of Object.entries(summarySource)) {
      const summary = projectReleaseChannelSummary(value);
      if (summary) summaries[id] = summary;
    }
  }
  for (const channel of items) {
    if (channel.summary) summaries[channel.id] = channel.summary;
  }
  return {
    items,
    count: source.count === null ? null : integer(source.count, 0),
    next_cursor: nullableText(source.next_cursor),
    summaries,
    invalid_item_count: rawItems.length - items.length,
  };
}

export function projectReleaseManifest(value: unknown): ReleaseManifest | null {
  const source = record(value);
  if (!source) return null;
  const id = text(source.id ?? source.release_id);
  const tenantId = text(source.tenant_id);
  const datasetId = text(source.dataset_id);
  const releaseNumber = integer(source.release_number, 1);
  const status = text(source.status) as ReleaseManifestStatus | null;
  const profileRevision = integer(source.profile_revision, 1);
  const ownershipRevision = integer(source.ownership_revision, 1);
  const workspaceRevision = integer(source.workspace_revision, 1);
  const mutationGeneration = integer(source.mutation_generation, 0);
  const servingGeneration = integer(source.serving_generation, 0);
  const schemaVersion = integer(source.schema_version, 1);
  const manifestDigest = digest(source.manifest_digest);
  const readinessState = source.readiness_state;
  const readinessFingerprint = digest(source.readiness_fingerprint);
  const entryCount = integer(source.entry_count, 0);
  const revision = integer(source.revision, 1);
  if (
    !id ||
    !tenantId ||
    !datasetId ||
    releaseNumber === null ||
    !status ||
    profileRevision === null ||
    ownershipRevision === null ||
    workspaceRevision === null ||
    mutationGeneration === null ||
    servingGeneration === null ||
    schemaVersion === null ||
    !manifestDigest ||
    (readinessState !== "ready" &&
      readinessState !== "blocked" &&
      readinessState !== "unavailable") ||
    !readinessFingerprint ||
    entryCount === null ||
    revision === null
  ) {
    return null;
  }
  return {
    id,
    tenant_id: tenantId,
    dataset_id: datasetId,
    release_number: releaseNumber,
    status,
    profile_revision: profileRevision,
    ownership_revision: ownershipRevision,
    workspace_revision: workspaceRevision,
    mutation_generation: mutationGeneration,
    serving_generation: servingGeneration,
    schema_version: schemaVersion,
    manifest_digest: manifestDigest,
    readiness_state: readinessState,
    readiness_fingerprint: readinessFingerprint,
    entry_count: entryCount,
    revision,
    created_at: date(source.created_at),
    created_by: nullableText(source.created_by),
    reason: safeReleaseDisplayText(source.reason),
  };
}

export function projectReleaseEntry(value: unknown): ReleaseEntry | null {
  const source = record(value);
  if (!source) return null;
  const id = text(source.id ?? source.entry_id);
  const resourceType = text(source.resource_type);
  const resourceId = text(source.resource_id);
  const resourceRevision = integer(source.resource_revision, 0);
  const contentDigest = digest(source.content_digest);
  if (!id || !resourceType || !resourceId || resourceRevision === null || !contentDigest)
    return null;
  return {
    id,
    resource_type: resourceType,
    resource_id: resourceId,
    resource_revision: resourceRevision,
    content_digest: contentDigest,
    facts: safeFacts(source.facts),
  };
}

export function projectReleaseEntryPage(value: unknown): ReleaseEntryPage {
  const source = record(value) ?? {};
  return {
    items: Array.isArray(source.items)
      ? source.items.flatMap((item) => {
          const entry = projectReleaseEntry(item);
          return entry ? [entry] : [];
        })
      : [],
    count: source.count === null ? null : integer(source.count, 0),
    next_cursor: nullableText(source.next_cursor),
  };
}

export function projectReleaseHistoryPage(value: unknown): ReleaseHistoryPage {
  const source = record(value) ?? {};
  const rawItems = Array.isArray(source.items) ? source.items : [];
  const items = rawItems.flatMap((item) => {
    const manifest = projectReleaseManifest(item);
    return manifest ? [manifest] : [];
  });
  return {
    items,
    count: source.count === null ? null : integer(source.count, 0),
    next_cursor: nullableText(source.next_cursor),
    summary: projectReleaseChannelSummary(source.summary),
    invalid_item_count: rawItems.length - items.length,
  };
}

export function projectReleaseDetail(value: unknown): ReleaseDetail | null {
  const source = record(value);
  const manifest = projectReleaseManifest(source?.manifest ?? source?.release);
  if (!manifest) return null;
  return { manifest, entries: projectReleaseEntryPage(source?.entries) };
}

export function projectReleaseImpact(value: unknown): ReleaseImpact {
  const source = record(value) ?? {};
  const state =
    source.state === "ready" || source.state === "blocked" || source.state === "unavailable"
      ? source.state
      : "unavailable";
  const items = Array.isArray(source.items)
    ? source.items.flatMap((item) => {
        const row = record(item);
        const type = text(row?.type ?? row?.resource_type);
        const id = text(row?.id ?? row?.resource_id);
        const label = safeReleaseDisplayText(row?.label ?? row?.name);
        const action = text(row?.action);
        if (!type || !id || !label || !action) return [];
        return [{ type, id, label, action, reason: safeReleaseDisplayText(row?.reason) }];
      })
    : [];
  return {
    state,
    items,
    count: source.count === null ? null : integer(source.count, 0),
    next_cursor: nullableText(source.next_cursor),
    reason: safeReleaseDisplayText(source.reason),
  };
}

export function projectReleaseAuditPage(value: unknown): ReleaseAuditPage {
  const source = record(value) ?? {};
  const items = Array.isArray(source.items)
    ? source.items.flatMap((item) => {
        const row = record(item);
        const id = text(row?.id ?? row?.event_id);
        const event = text(row?.event);
        const actor = text(row?.actor ?? row?.actor_id);
        if (!id || !event || !actor) return [];
        return [
          {
            id,
            event,
            actor,
            occurred_at: date(row?.occurred_at),
            reason: safeReleaseDisplayText(row?.reason),
            request_id: safeReleaseDisplayText(row?.request_id),
          },
        ];
      })
    : [];
  return {
    items,
    count: source.count === null ? null : integer(source.count, 0),
    next_cursor: nullableText(source.next_cursor),
  };
}

export function projectReleaseMutationOutcome(value: unknown): ReleaseMutationOutcome {
  const source = record(value) ?? {};
  const state: ReleaseMutationState =
    source.state === "applied" ||
    source.state === "approval_required" ||
    source.state === "rejected" ||
    source.state === "unavailable"
      ? source.state
      : "unavailable";
  const operation: ReleaseMutationKind =
    source.operation === "capture" ||
    source.operation === "promote" ||
    source.operation === "rollback"
      ? source.operation
      : "capture";
  return {
    state,
    operation,
    resource_id: safeReleaseDisplayText(source.resource_id ?? source.release_id),
    approval_request_id: safeReleaseDisplayText(source.approval_request_id),
    revision: source.revision === null ? null : integer(source.revision, 1),
    message: safeReleaseDisplayText(source.message),
    retryable: source.retryable === true,
  };
}

export function releaseComparisonLabel(state: ReleaseComparisonState): string {
  if (state === "aligned") return "配置一致";
  if (state === "drifted") return "配置已漂移";
  return "权威事实不可用";
}

export function releaseComparisonTheme(
  state: ReleaseComparisonState,
): "success" | "warning" | "default" {
  if (state === "aligned") return "success";
  if (state === "drifted") return "warning";
  return "default";
}

export function releaseReadinessLabel(state: ReleaseReadinessState): string {
  if (state === "ready") return "可发布";
  if (state === "blocked") return "存在阻断";
  return "不可用";
}

export function releaseReadinessTheme(
  state: ReleaseReadinessState,
): "success" | "warning" | "danger" | "default" {
  if (state === "ready") return "success";
  if (state === "blocked") return "warning";
  return "danger";
}

export function releaseStatusLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    draft: "草稿",
    candidate: "候选",
    published: "已发布",
    superseded: "已被替代",
    retired: "已退役",
  };
  return (status && labels[status]) || status || "未返回";
}

export function releaseRiskLabel(risk: string): string {
  return { low: "低风险", medium: "中风险", high: "高风险" }[risk] ?? risk;
}

export function releaseMutationLabel(kind: ReleaseMutationKind): string {
  return { capture: "生成候选", promote: "发布到 Channel", rollback: "回滚 Channel" }[kind];
}
