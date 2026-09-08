import { ApiError, request } from "../../api/client";
import {
  projectContentRetentionPolicy,
  projectDocumentLegalHold,
  projectDocumentPurgeRequest,
  projectRecoveryEntry,
  projectRecoveryEntryDetail,
  projectRecoveryMutationOutcome,
  projectRecoverySummary,
  sanitizeRecoveryMessage,
  type ContentRetentionPolicy,
  type LegalHold,
  type PurgeRequest,
  type RecoveryEntry,
  type RecoveryEntryDetail,
  type RecoveryEntryStatus,
  type RecoveryModelScope,
  type RecoveryMutationOutcome,
  type RecoveryPolicyStatus,
  type RecoverySummary,
} from "../model/recoveryModel";

export type {
  ContentRetentionPolicy,
  LegalHold,
  PurgeRequest,
  RecoveryEntry,
  RecoveryEntryDetail,
  RecoveryEntryStatus,
  RecoveryEvent,
  RecoveryMutationOutcome,
  RecoveryOriginalLifecycleState,
  RecoveryPolicyStatus,
  RecoverySummary,
} from "../model/recoveryModel";

export interface RecoveryApiScope {
  tenantId: string;
  actorToken: string;
}

export interface RecoveryRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface RecoveryPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export interface RecoveryEntryListQuery {
  cursor?: string | null;
  limit?: number;
  status?: RecoveryEntryStatus | "all";
  datasetId?: string;
  documentId?: string;
}

export interface RecoveryChildListQuery {
  cursor?: string | null;
  limit?: number;
  status?:
    | "active"
    | "released"
    | "all"
    | "pending_approval"
    | "approved"
    | "cancelled"
    | "expired"
    | "executed";
}

export interface RecycleDocumentInput {
  datasetId: string;
  expectedMutationGeneration: number;
  reason: string;
}

export interface RestoreRecycleEntryInput {
  expectedRevision: number;
  reason: string;
}

export interface ApplyLegalHoldInput {
  expectedRevision: number;
  reasonCode: string;
  safeReason: string;
}

export interface ReleaseLegalHoldInput {
  expectedRevision: number;
  reason: string;
}

export interface RequestDocumentPurgeInput {
  expectedRevision: number;
  reason: string;
}

export interface CancelDocumentPurgeRequestInput {
  expectedRevision: number;
  reason: string;
}

export interface UpdateContentRetentionPolicyInput {
  expectedRevision: number;
  status: RecoveryPolicyStatus;
  retentionDays: number;
  autoPurgeEnabled: boolean;
  purgeRequiresApproval: boolean;
  reason: string;
}

export interface BulkRecycleDocumentsInput {
  datasetId: string;
  documents: string[];
  expectedMutationGeneration: number;
  reason: string;
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(field + " is unavailable");
  }
  return value as Record<string, unknown>;
}

function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}

function requiredText(value: unknown, field: string, maximum: number): string {
  if (typeof value !== "string") throw new Error(field + " is invalid");
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || hasControlCharacters(normalized)) {
    throw new Error(field + " is invalid");
  }
  return normalized;
}

function safeIdentifier(value: unknown, field: string, maximum = 128): string {
  const normalized = requiredText(value, field, maximum);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(normalized) || normalized.includes("..")) {
    throw new Error(field + " is invalid");
  }
  return normalized;
}

function exactInteger(value: unknown, field: string, minimum = 1, maximum?: number): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    (maximum !== undefined && value > maximum)
  ) {
    throw new Error(field + " must be an exact integer");
  }
  return value;
}

function exactBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(field + " must be a boolean");
  return value;
}

function safeCode(value: unknown, field: string): string {
  const normalized = requiredText(value, field, 64);
  if (!/^[a-z][a-z0-9_.-]{0,63}$/.test(normalized)) {
    throw new Error(field + " is invalid");
  }
  return normalized;
}

function safeReason(value: unknown, field = "reason"): string {
  const normalized = requiredText(value, field, 512);
  if (
    /(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]|bearer\s+|(?:https?|ftp|file|mailto|javascript|data):\S+/i.test(
      normalized,
    )
  ) {
    throw new Error(field + " is unsafe");
  }
  return normalized;
}

function normalizedScope(scope: RecoveryApiScope): RecoveryApiScope {
  return {
    tenantId: safeIdentifier(scope.tenantId, "tenantId", 64),
    actorToken: requiredText(scope.actorToken, "actorToken", 4096),
  };
}

function headers(scope: RecoveryApiScope, idempotencyKey?: string): Record<string, string> {
  const normalized = normalizedScope(scope);
  const result: Record<string, string> = {
    "Content-Type": "application/json",
    "X-RAG4C-Tenant": normalized.tenantId,
    Authorization: "Bearer " + normalized.actorToken,
  };
  if (idempotencyKey !== undefined) {
    result["Idempotency-Key"] = requiredText(idempotencyKey, "idempotencyKey", 128);
  }
  return result;
}

function modelScope(scope: RecoveryApiScope): RecoveryModelScope {
  return { tenantId: normalizedScope(scope).tenantId };
}

function requiredMutationKey(options?: RecoveryRequestOptions): string {
  if (!options?.idempotencyKey)
    throw new Error("idempotencyKey is required for recovery mutations");
  return requiredText(options.idempotencyKey, "idempotencyKey", 128);
}

function pageQuery(query: RecoveryEntryListQuery = {}): string {
  const params = new URLSearchParams();
  if (query.datasetId !== undefined)
    params.set("dataset_id", safeIdentifier(query.datasetId, "datasetId", 64));
  if (query.documentId !== undefined)
    params.set("document_id", safeIdentifier(query.documentId, "documentId"));
  if (query.cursor !== undefined && query.cursor !== null) {
    const cursor = requiredText(query.cursor, "cursor", 2048);
    params.set("cursor", cursor);
  }
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit", 1, 100)));
  if (query.status !== undefined) {
    if (
      !["all", "recycled", "restoring", "restored", "purge_requested", "purged", "failed"].includes(
        query.status,
      )
    ) {
      throw new Error("status is invalid");
    }
    params.set("status", query.status);
  }
  return "?" + params.toString();
}

function childPageQuery(query: RecoveryChildListQuery = {}): string {
  const params = new URLSearchParams();
  if (query.cursor !== undefined && query.cursor !== null) {
    params.set("cursor", requiredText(query.cursor, "cursor", 2048));
  }
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit", 1, 100)));
  if (query.status !== undefined) {
    const valid = [
      "all",
      "active",
      "released",
      "pending_approval",
      "approved",
      "cancelled",
      "expired",
      "executed",
    ];
    if (!valid.includes(query.status)) throw new Error("status is invalid");
    params.set("status", query.status);
  }
  return "?" + params.toString();
}

function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

async function safeRequest<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  try {
    return await request<T>(path, init);
  } catch (error) {
    if (isAbortError(error)) throw error;
    const message = sanitizeRecoveryMessage(
      error instanceof Error ? error.message : null,
      fallback,
    );
    if (error instanceof ApiError) {
      throw new ApiError(message, error.kind, error.status, undefined, error.retryAfter);
    }
    throw new Error(message, { cause: error });
  }
}

function projectPage<T>(
  value: unknown,
  field: string,
  projector: (item: unknown) => T,
): RecoveryPage<T> {
  const source = object(value, field);
  const allowed = new Set(["items", "next_cursor", "invalid_item_count"]);
  for (const key of Object.keys(source)) {
    if (!allowed.has(key)) throw new Error(field + " contains unexpected field " + key);
  }
  if (!Object.prototype.hasOwnProperty.call(source, "items")) {
    throw new Error(field + ".items is required");
  }
  if (!Object.prototype.hasOwnProperty.call(source, "next_cursor")) {
    throw new Error(field + ".next_cursor is required");
  }
  if (!Object.prototype.hasOwnProperty.call(source, "invalid_item_count")) {
    throw new Error(field + ".invalid_item_count is required");
  }
  if (!Array.isArray(source.items)) throw new Error(field + ".items is unavailable");
  const nextCursor =
    source.next_cursor === null
      ? null
      : requiredText(source.next_cursor, field + ".next_cursor", 2048);
  const serverInvalid = exactInteger(source.invalid_item_count, field + ".invalid_item_count", 0);
  const items: T[] = [];
  let localInvalid = 0;
  for (const item of source.items) {
    try {
      items.push(projector(item));
    } catch {
      localInvalid += 1;
    }
  }
  return {
    items,
    next_cursor: nextCursor,
    invalid_item_count: serverInvalid + localInvalid,
  };
}

function isRequestOptions(value: unknown): value is RecoveryRequestOptions {
  return (
    typeof value === "object" && value !== null && ("signal" in value || "idempotencyKey" in value)
  );
}

function mutationRequest(
  scope: RecoveryApiScope,
  path: string,
  body: Record<string, unknown>,
  options: RecoveryRequestOptions | undefined,
  fallback: string,
  method: "POST" | "PATCH" = "POST",
): Promise<RecoveryMutationOutcome> {
  const key = requiredMutationKey(options);
  return safeRequest<unknown>(
    path,
    {
      method,
      headers: headers(scope, key),
      body: JSON.stringify(body),
      signal: options?.signal,
    },
    fallback,
  ).then(projectRecoveryMutationOutcome);
}

function childIdempotencyKey(parentKey: string, index: number): string {
  const suffix = "-" + String(index);
  return parentKey.slice(0, 128 - suffix.length) + suffix;
}

export function createRecoveryIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return "rag4c-recovery-" + cryptoApi.randomUUID();
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return (
      "rag4c-recovery-" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
    );
  }
  return "rag4c-recovery-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

export function fetchRecoverySummary(
  scope: RecoveryApiScope,
  options: RecoveryRequestOptions = {},
): Promise<RecoverySummary> {
  return safeRequest<unknown>(
    "/api/enterprise/recovery/summary",
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Recovery summary is unavailable",
  ).then((value) => projectRecoverySummary(value, modelScope(scope)));
}

export function fetchRecycleEntries(
  scope: RecoveryApiScope,
  queryOrOptions: RecoveryEntryListQuery | RecoveryRequestOptions = {},
  maybeOptions: RecoveryRequestOptions = {},
): Promise<RecoveryPage<RecoveryEntry>> {
  const query = isRequestOptions(queryOrOptions) ? {} : queryOrOptions;
  const options = isRequestOptions(queryOrOptions) ? queryOrOptions : maybeOptions;
  return safeRequest<unknown>(
    "/api/enterprise/recycle-bin" + pageQuery(query),
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Recycle bin entries are unavailable",
  ).then((value) =>
    projectPage(value, "recycle_entries", (item) => projectRecoveryEntry(item, modelScope(scope))),
  );
}

export function fetchRecycleEntry(
  scope: RecoveryApiScope,
  entryId: string,
  options: RecoveryRequestOptions = {},
): Promise<RecoveryEntryDetail> {
  const id = safeIdentifier(entryId, "entryId");
  return safeRequest<unknown>(
    "/api/enterprise/recycle-bin/" + encodeURIComponent(id),
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Recycle bin entry is unavailable",
  ).then((value) => projectRecoveryEntryDetail(value, modelScope(scope)));
}

export const fetchRecoveryEntry = fetchRecycleEntry;
export const fetchRecycleEntryDetail = fetchRecycleEntry;

export function fetchLegalHolds(
  scope: RecoveryApiScope,
  entryId: string,
  queryOrOptions: RecoveryChildListQuery | RecoveryRequestOptions = {},
  maybeOptions: RecoveryRequestOptions = {},
): Promise<RecoveryPage<LegalHold>> {
  const id = safeIdentifier(entryId, "entryId");
  const query = isRequestOptions(queryOrOptions) ? {} : queryOrOptions;
  const options = isRequestOptions(queryOrOptions) ? queryOrOptions : maybeOptions;
  return safeRequest<unknown>(
    "/api/enterprise/recycle-bin/" + encodeURIComponent(id) + "/holds" + childPageQuery(query),
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Legal holds are unavailable",
  ).then((value) =>
    projectPage(value, "legal_holds", (item) => projectDocumentLegalHold(item, modelScope(scope))),
  );
}

export function fetchPurgeRequests(
  scope: RecoveryApiScope,
  entryId: string,
  queryOrOptions: RecoveryChildListQuery | RecoveryRequestOptions = {},
  maybeOptions: RecoveryRequestOptions = {},
): Promise<RecoveryPage<PurgeRequest>> {
  const id = safeIdentifier(entryId, "entryId");
  const query = isRequestOptions(queryOrOptions) ? {} : queryOrOptions;
  const options = isRequestOptions(queryOrOptions) ? queryOrOptions : maybeOptions;
  return safeRequest<unknown>(
    "/api/enterprise/recycle-bin/" +
      encodeURIComponent(id) +
      "/purge-requests" +
      childPageQuery(query),
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Purge requests are unavailable",
  ).then((value) =>
    projectPage(value, "purge_requests", (item) =>
      projectDocumentPurgeRequest(item, modelScope(scope)),
    ),
  );
}

export function fetchRetentionPolicy(
  scope: RecoveryApiScope,
  options: RecoveryRequestOptions = {},
): Promise<ContentRetentionPolicy> {
  return safeRequest<unknown>(
    "/api/enterprise/recovery/retention-policy",
    { method: "GET", headers: headers(scope), signal: options.signal },
    "Content retention policy is unavailable",
  ).then((value) => projectContentRetentionPolicy(value, modelScope(scope)));
}

export const fetchContentRetentionPolicy = fetchRetentionPolicy;

export function recycleDocument(
  scope: RecoveryApiScope,
  documentId: string,
  input: RecycleDocumentInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const id = safeIdentifier(documentId, "documentId");
  const datasetId = safeIdentifier(input.datasetId, "datasetId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/documents/" + encodeURIComponent(id),
    {
      dataset_id: datasetId,
      expected_mutation_generation: exactInteger(
        input.expectedMutationGeneration,
        "expectedMutationGeneration",
        0,
      ),
      reason: safeReason(input.reason),
    },
    options,
    "Recycle document is unavailable",
  );
}

export function bulkRecycleDocuments(
  scope: RecoveryApiScope,
  input: BulkRecycleDocumentsInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome[]> {
  if (
    !Array.isArray(input.documents) ||
    input.documents.length < 1 ||
    input.documents.length > 100
  ) {
    throw new Error("bulk recycle accepts between 1 and 100 documents");
  }
  const datasetId = safeIdentifier(input.datasetId, "datasetId");
  const seen = new Set<string>();
  const documents = input.documents.map((documentId) => {
    const id = safeIdentifier(documentId, "documentId");
    if (seen.has(id)) throw new Error("bulk recycle document IDs must be unique");
    seen.add(id);
    return id;
  });
  const generation = exactInteger(
    input.expectedMutationGeneration,
    "expectedMutationGeneration",
    0,
  );
  const operationReason = safeReason(input.reason);
  const parentKey = requiredMutationKey(options);
  return (async () => {
    const outcomes: RecoveryMutationOutcome[] = [];
    for (const [index, documentId] of documents.entries()) {
      outcomes.push(
        await recycleDocument(
          scope,
          documentId,
          { datasetId, expectedMutationGeneration: generation, reason: operationReason },
          { ...options, idempotencyKey: childIdempotencyKey(parentKey, index + 1) },
        ),
      );
    }
    return outcomes;
  })();
}

export function restoreRecycleEntry(
  scope: RecoveryApiScope,
  entryId: string,
  input: RestoreRecycleEntryInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const id = safeIdentifier(entryId, "entryId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/" + encodeURIComponent(id) + "/restore",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: safeReason(input.reason),
    },
    options,
    "Restore recycle entry is unavailable",
  );
}

export const restoreDocument = restoreRecycleEntry;

export function applyLegalHold(
  scope: RecoveryApiScope,
  entryId: string,
  input: ApplyLegalHoldInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const id = safeIdentifier(entryId, "entryId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/" + encodeURIComponent(id) + "/holds",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason_code: safeCode(input.reasonCode, "reasonCode"),
      safe_reason: safeReason(input.safeReason, "safeReason"),
    },
    options,
    "Apply legal hold is unavailable",
  );
}

export function releaseLegalHold(
  scope: RecoveryApiScope,
  entryId: string,
  holdId: string,
  input: ReleaseLegalHoldInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const entry = safeIdentifier(entryId, "entryId");
  const hold = safeIdentifier(holdId, "holdId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/" +
      encodeURIComponent(entry) +
      "/holds/" +
      encodeURIComponent(hold) +
      "/release",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: safeReason(input.reason),
    },
    options,
    "Release legal hold is unavailable",
  );
}

export function requestDocumentPurge(
  scope: RecoveryApiScope,
  entryId: string,
  input: RequestDocumentPurgeInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const id = safeIdentifier(entryId, "entryId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/" + encodeURIComponent(id) + "/purge-requests",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: safeReason(input.reason),
    },
    options,
    "Request document purge is unavailable",
  );
}

export const requestPurge = requestDocumentPurge;

export function cancelDocumentPurgeRequest(
  scope: RecoveryApiScope,
  entryId: string,
  requestId: string,
  input: CancelDocumentPurgeRequestInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  const entry = safeIdentifier(entryId, "entryId");
  const requestIdValue = safeIdentifier(requestId, "requestId");
  return mutationRequest(
    scope,
    "/api/enterprise/recycle-bin/" +
      encodeURIComponent(entry) +
      "/purge-requests/" +
      encodeURIComponent(requestIdValue) +
      "/cancel",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: safeReason(input.reason),
    },
    options,
    "Cancel purge request is unavailable",
  );
}

export const cancelPurgeRequest = cancelDocumentPurgeRequest;

export function updateContentRetentionPolicy(
  scope: RecoveryApiScope,
  input: UpdateContentRetentionPolicyInput,
  options?: RecoveryRequestOptions,
): Promise<RecoveryMutationOutcome> {
  if (input.status !== "active" && input.status !== "paused") throw new Error("status is invalid");
  return mutationRequest(
    scope,
    "/api/enterprise/recovery/retention-policy",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      status: input.status,
      retention_days: exactInteger(input.retentionDays, "retentionDays", 1, 3650),
      auto_purge_enabled: exactBoolean(input.autoPurgeEnabled, "autoPurgeEnabled"),
      purge_requires_approval: exactBoolean(input.purgeRequiresApproval, "purgeRequiresApproval"),
      reason: safeReason(input.reason),
    },
    options,
    "Update content retention policy is unavailable",
    "PATCH",
  );
}

export const updateRetentionPolicy = updateContentRetentionPolicy;

export interface EnterpriseContentRecoveryApi {
  bulkRecycle?: typeof bulkRecycleDocuments;
  fetchSummary: typeof fetchRecoverySummary;
  fetchEntries: typeof fetchRecycleEntries;
  fetchDetail: typeof fetchRecycleEntry;
  fetchHolds: typeof fetchLegalHolds;
  fetchPurgeRequests: typeof fetchPurgeRequests;
  fetchPolicy: typeof fetchRetentionPolicy;
  recycle: typeof recycleDocument;
  restore: typeof restoreRecycleEntry;
  applyHold: typeof applyLegalHold;
  releaseHold: typeof releaseLegalHold;
  requestPurge: typeof requestDocumentPurge;
  cancelPurge: typeof cancelDocumentPurgeRequest;
  updatePolicy: typeof updateContentRetentionPolicy;
}

export const recoveryApi: EnterpriseContentRecoveryApi = {
  bulkRecycle: bulkRecycleDocuments,
  fetchSummary: fetchRecoverySummary,
  fetchEntries: fetchRecycleEntries,
  fetchDetail: fetchRecycleEntry,
  fetchHolds: fetchLegalHolds,
  fetchPurgeRequests,
  fetchPolicy: fetchRetentionPolicy,
  recycle: recycleDocument,
  restore: restoreRecycleEntry,
  applyHold: applyLegalHold,
  releaseHold: releaseLegalHold,
  requestPurge: requestDocumentPurge,
  cancelPurge: cancelDocumentPurgeRequest,
  updatePolicy: updateContentRetentionPolicy,
};
