import { request } from "../../api/client";
import {
  projectServingEventPage,
  projectServingMutationOutcome,
  projectServingPage,
  projectServingPreview,
  projectServingProfileEnvelope,
  projectServingSnapshot,
  projectServingSnapshotDetail,
  projectServingStageFact,
  projectServingSummary,
  type ServingEventPage,
  type ServingMutationOutcome,
  type ServingPage,
  type ServingPreview,
  type ServingProfileEnvelope,
  type ServingScope,
  type ServingSnapshot,
  type ServingSnapshotDetail,
  type ServingStageFact,
  type ServingSummary,
} from "../model/servingModel";

export interface ServingApiScope extends ServingScope {
  actorToken: string;
}
export interface ServingRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}
export interface ServingListQuery {
  cursor?: string;
  limit?: number;
  state?: string;
  stageCode?: string;
  sourceKind?: string;
  ownerId?: string;
  keyword?: string;
  snapshotId?: string;
}
export interface ServingPolicyInput {
  maxSourceStalenessSeconds: number;
  maxParseLagSeconds: number;
  maxIndexLagSeconds: number;
  maxFailedDocumentCount: number;
  maxPendingIndexCount: number;
  requireCurrentRelease: boolean;
  requirePassingCertification: boolean;
}
export interface CreateServingProfileInput {
  name: string;
  workspaceId: string | null;
  reason: string;
}
export interface CreateServingPolicyRevisionInput {
  expectedRevision: number;
  expectedPolicyDigest: string | null;
  policy: ServingPolicyInput;
  reason: string;
}
export interface ActivateServingProfileInput {
  policyRevisionId: string;
  expectedRevision: number;
  expectedPolicyDigest: string;
  reason: string;
}
export interface PreviewServingPolicyInput extends CreateServingPolicyRevisionInput {
  profileId?: string;
}

export interface ServingApi {
  fetchSummary: typeof fetchServingSummary;
  fetchProfile: typeof fetchServingProfile;
  fetchSnapshots: typeof fetchServingSnapshots;
  fetchSnapshot: typeof fetchServingSnapshot;
  fetchStageFacts: typeof fetchServingStageFacts;
  fetchEvents: typeof fetchServingEvents;
  createProfile: typeof createServingProfile;
  createPolicyRevision: typeof createServingPolicyRevision;
  activateProfile: typeof activateServingProfile;
  previewPolicy: typeof previewServingPolicy;
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SECRET =
  /(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key|access[_ -]?key)\s*[:=]?\s*\S+/i;
const SQL_LIKE =
  /(?:\bselect\s+(?:distinct\s+)?[\w*"\x60'([]|\binsert\s+(?:into\s+)?[\w"\x60'(]|\bupdate\s+[\w"\x60.]+\s+set\b|\bdelete\s+from\b|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bgrant\s+\w+\s+on\b|\brevoke\s+\w+\s+on\b|\bexec(?:ute)?\s+\S+|\bunion\s+(?:all\s+)?select\b)/i;
const BEARER = /\bbearer\b/i;
const JWT =
  /(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])/;
function requiredText(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(field + " is invalid");
  const result = value.trim();
  if (
    !result ||
    result.length > max ||
    CONTROL.test(result) ||
    URL.test(result) ||
    SECRET.test(result) ||
    SQL_LIKE.test(result) ||
    BEARER.test(result) ||
    JWT.test(result)
  )
    throw new Error(field + " is unsafe");
  return result;
}
function opaqueToken(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 4096 || CONTROL.test(value))
    throw new Error(field + " is required");
  return value.trim();
}
function id(value: unknown, field: string): string {
  const result = requiredText(value, field, 128);
  if (!IDENTIFIER.test(result) || result.includes("..")) throw new Error(field + " is invalid");
  return result;
}
function digest(value: unknown, field: string): string {
  const result = requiredText(value, field, 64);
  if (!DIGEST.test(result)) throw new Error(field + " is invalid digest");
  return result;
}
const EMPTY_POLICY_DIGEST = "0".repeat(64);
function optionalDigest(value: unknown, field: string): string | null {
  if (value === null || value === EMPTY_POLICY_DIGEST) return null;
  return digest(value, field);
}
function integer(value: unknown, field: string, min = 0, max = 31_536_000): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max)
    throw new Error(field + " must be a safe integer");
  return value;
}
function bool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(field + " must be boolean");
  return value;
}
function scopeOf(scope: ServingApiScope): ServingApiScope {
  return {
    tenantId: id(scope.tenantId, "tenantId"),
    accountId: scope.accountId === undefined ? undefined : id(scope.accountId, "accountId"),
    datasetId: id(scope.datasetId, "datasetId"),
    actorToken: opaqueToken(scope.actorToken, "actorToken"),
  };
}
function headers(
  scope: ServingApiScope,
  options: ServingRequestOptions = {},
  includeIdempotency = false,
): Record<string, string> {
  const s = scopeOf(scope);
  const result: Record<string, string> = {
    "Content-Type": "application/json",
    "X-RAG4C-Tenant": s.tenantId,
    Authorization: "Bearer " + s.actorToken,
  };
  if (s.accountId) result["X-RAG4C-Account"] = s.accountId;
  if (includeIdempotency) {
    if (!options.idempotencyKey) throw new Error("Idempotency-Key is required");
    result["Idempotency-Key"] = requiredText(options.idempotencyKey, "Idempotency-Key", 128);
  }
  return result;
}
function basePath(scope: ServingApiScope): string {
  return (
    "/api/enterprise/knowledge-bases/" + encodeURIComponent(scopeOf(scope).datasetId) + "/serving"
  );
}
function query(input: ServingListQuery = {}): string {
  const entries: Array<[string, string]> = [];
  if (input.cursor !== undefined)
    entries.push(["cursor", requiredText(input.cursor, "cursor", 2_048)]);
  if (input.limit !== undefined)
    entries.push(["limit", String(integer(input.limit, "limit", 1, 200))]);
  if (input.state !== undefined) entries.push(["state", requiredText(input.state, "state", 32)]);
  if (input.snapshotId !== undefined)
    entries.push(["snapshot_id", id(input.snapshotId, "snapshotId")]);
  if (input.stageCode !== undefined) entries.push(["stage_code", stageCode(input.stageCode)]);
  if (input.sourceKind !== undefined)
    entries.push(["source_kind", requiredText(input.sourceKind, "sourceKind", 64)]);
  if (input.ownerId !== undefined) entries.push(["owner_id", id(input.ownerId, "ownerId")]);
  if (input.keyword !== undefined)
    entries.push(["keyword", requiredText(input.keyword, "keyword", 256)]);
  return entries.length ? "?" + new URLSearchParams(entries).toString() : "";
}
function policyBody(policy: ServingPolicyInput): Record<string, unknown> {
  return {
    max_source_staleness_seconds: integer(
      policy.maxSourceStalenessSeconds,
      "maxSourceStalenessSeconds",
    ),
    max_parse_lag_seconds: integer(policy.maxParseLagSeconds, "maxParseLagSeconds"),
    max_index_lag_seconds: integer(policy.maxIndexLagSeconds, "maxIndexLagSeconds"),
    max_failed_document_count: integer(policy.maxFailedDocumentCount, "maxFailedDocumentCount"),
    max_pending_index_count: integer(policy.maxPendingIndexCount, "maxPendingIndexCount"),
    require_current_release: bool(policy.requireCurrentRelease, "requireCurrentRelease"),
    require_passing_certification: bool(
      policy.requirePassingCertification,
      "requirePassingCertification",
    ),
  };
}
function expectedRevision(value: unknown): number {
  return integer(value, "expectedRevision", 1);
}
const SERVING_STAGE_CODES = ["source", "parse", "chunk", "index", "serve"] as const;
export type ServingStageCode = (typeof SERVING_STAGE_CODES)[number];
function stageCode(value: unknown): ServingStageCode {
  const result = requiredText(value, "stageCode", 32);
  if (!(SERVING_STAGE_CODES as readonly string[]).includes(result))
    throw new Error("stageCode is not allow-listed");
  return result as ServingStageCode;
}
function read<T>(
  scope: ServingApiScope,
  path: string,
  parser: (value: unknown, scope: ServingScope) => T,
  options: ServingRequestOptions = {},
): Promise<T> {
  return request<unknown>(path, {
    method: "GET",
    headers: headers(scope, options),
    signal: options.signal,
  }).then((value) => parser(value, scopeOf(scope)));
}
function mutate(
  scope: ServingApiScope,
  path: string,
  body: Record<string, unknown>,
  options: ServingRequestOptions,
): Promise<ServingMutationOutcome> {
  return request<unknown>(path, {
    method: "POST",
    headers: headers(scope, options, true),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projectServingMutationOutcome);
}

export function fetchServingSummary(
  scope: ServingApiScope,
  options: ServingRequestOptions = {},
): Promise<ServingSummary> {
  return read(scope, basePath(scope) + "/summary", projectServingSummary, options);
}
export function fetchServingProfile(
  scope: ServingApiScope,
  options: ServingRequestOptions = {},
): Promise<ServingProfileEnvelope> {
  return read(scope, basePath(scope) + "/profile", projectServingProfileEnvelope, options);
}
export function fetchServingSnapshots(
  scope: ServingApiScope,
  list: ServingListQuery = {},
  options: ServingRequestOptions = {},
): Promise<ServingPage<ServingSnapshot>> {
  return read(
    scope,
    basePath(scope) + "/snapshots" + query(list),
    (value, modelScope) => projectServingPage(value, modelScope, projectServingSnapshot),
    options,
  );
}
export function fetchServingSnapshot(
  scope: ServingApiScope,
  snapshotId: string,
  options: ServingRequestOptions = {},
): Promise<ServingSnapshotDetail> {
  return read(
    scope,
    basePath(scope) + "/snapshots/" + encodeURIComponent(id(snapshotId, "snapshotId")),
    projectServingSnapshotDetail,
    options,
  );
}
export function fetchServingStageFacts(
  scope: ServingApiScope,
  list: ServingListQuery = {},
  options: ServingRequestOptions = {},
): Promise<ServingPage<ServingStageFact>> {
  return read(
    scope,
    basePath(scope) + "/stage-facts" + query(list),
    (value, modelScope) => projectServingPage(value, modelScope, projectServingStageFact),
    options,
  );
}
export function fetchServingEvents(
  scope: ServingApiScope,
  list: ServingListQuery = {},
  options: ServingRequestOptions = {},
): Promise<ServingEventPage> {
  return read(scope, basePath(scope) + "/events" + query(list), projectServingEventPage, options);
}
export function createServingProfile(
  scope: ServingApiScope,
  input: CreateServingProfileInput,
  options: ServingRequestOptions = {},
): Promise<ServingMutationOutcome> {
  return mutate(
    scope,
    basePath(scope) + "/profile",
    {
      name: requiredText(input.name, "name", 128),
      workspace_id: input.workspaceId === null ? null : id(input.workspaceId, "workspaceId"),
      reason: requiredText(input.reason, "reason"),
    },
    options,
  );
}
export function createServingPolicyRevision(
  scope: ServingApiScope,
  input: CreateServingPolicyRevisionInput,
  options: ServingRequestOptions = {},
): Promise<ServingMutationOutcome> {
  return mutate(
    scope,
    basePath(scope) + "/profile/revisions",
    {
      expected_profile_revision: expectedRevision(input.expectedRevision),
      expected_policy_digest: optionalDigest(input.expectedPolicyDigest, "expectedPolicyDigest"),
      ...policyBody(input.policy),
      reason: requiredText(input.reason, "reason"),
    },
    options,
  );
}
export function activateServingProfile(
  scope: ServingApiScope,
  input: ActivateServingProfileInput,
  options: ServingRequestOptions = {},
): Promise<ServingMutationOutcome> {
  return mutate(
    scope,
    basePath(scope) + "/profile/activate",
    {
      policy_revision_id: id(input.policyRevisionId, "policyRevisionId"),
      expected_profile_revision: expectedRevision(input.expectedRevision),
      expected_policy_digest: digest(input.expectedPolicyDigest, "expectedPolicyDigest"),
      reason: requiredText(input.reason, "reason"),
    },
    options,
  );
}
export function previewServingPolicy(
  scope: ServingApiScope,
  input: PreviewServingPolicyInput,
  options: Omit<ServingRequestOptions, "idempotencyKey"> = {},
): Promise<ServingPreview> {
  return request<unknown>(basePath(scope) + "/preview", {
    method: "POST",
    headers: headers(scope, options),
    body: JSON.stringify({
      profile_id: id(input.profileId, "profileId"),
      expected_profile_revision: expectedRevision(input.expectedRevision),
      expected_policy_digest: optionalDigest(input.expectedPolicyDigest, "expectedPolicyDigest"),
      ...policyBody(input.policy),
      reason: requiredText(input.reason, "reason"),
    }),
    signal: options.signal,
  }).then((value) => projectServingPreview(value, scopeOf(scope)));
}
export function createServingIdempotencyKey(): string {
  const cryptoObject = globalThis.crypto;
  if (cryptoObject?.randomUUID) return "rag4c-serving-" + cryptoObject.randomUUID();
  return "rag4c-serving-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}
