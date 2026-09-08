import { ApiError, request } from "../../api/client";
import {
  projectNotificationDetail,
  projectNotificationInboxItem,
  projectNotificationMutationOutcome,
  projectNotificationSubscription,
  projectNotificationSummary,
  sanitizeNotificationMessage,
  type NotificationCategory,
  type NotificationDetail,
  type NotificationInboxItem,
  type NotificationModelScope,
  type NotificationMutationOutcome,
  type NotificationReceiptStatus,
  type NotificationSeverity,
  type NotificationSubscription,
  type NotificationSummary,
} from "../model/notificationModel";

export type {
  Notification,
  NotificationCategory,
  NotificationDetail,
  NotificationInboxItem,
  NotificationMutationOutcome,
  NotificationReceipt,
  NotificationReceiptStatus,
  NotificationSeverity,
  NotificationSubscription,
  NotificationSummary,
} from "../model/notificationModel";

export interface NotificationApiScope {
  tenantId: string;
  actorToken: string;
}

export interface NotificationRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface NotificationPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export interface NotificationListQuery {
  cursor?: string | null;
  limit?: number;
  status?: NotificationReceiptStatus | "all";
  category?: NotificationCategory;
  severity?: NotificationSeverity;
}

export interface SubscriptionListQuery {
  cursor?: string | null;
  limit?: number;
  status?: "active" | "archived" | "all";
  category?: NotificationCategory;
}

export interface ReceiptMutationInput {
  expectedRevision: number;
  reason: string;
}

export interface BulkReadInput {
  items: Array<{ notificationId: string; expectedRevision: number }>;
  reason: string;
}

export interface UpdateNotificationSubscriptionInput {
  expectedRevision: number;
  preference: "subscribed" | "muted";
  minimumSeverity: NotificationSeverity;
  mutedUntil: string | null;
  reason: string;
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(field + " is unavailable");
  }
  return value as Record<string, unknown>;
}

function hasNotificationControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code === 0 || code === 10 || code === 13) return true;
  }
  return false;
}

function requiredText(value: unknown, field: string, maximum: number): string {
  if (typeof value !== "string") throw new Error(field + " is invalid");
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || hasNotificationControlCharacters(normalized)) {
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

function normalizedScope(scope: NotificationApiScope): NotificationApiScope {
  return {
    tenantId: requiredText(scope.tenantId, "tenantId", 64),
    actorToken: requiredText(scope.actorToken, "actorToken", 2048),
  };
}

function headers(scope: NotificationApiScope, idempotencyKey?: string): Record<string, string> {
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

function modelScope(scope: NotificationApiScope): NotificationModelScope {
  return { tenantId: normalizedScope(scope).tenantId };
}

function requiredId(value: unknown, field: string, maximum = 128): string {
  return requiredText(value, field, maximum);
}

function reason(value: unknown): string {
  return requiredText(value, "reason", 512);
}

function mutationKey(options?: NotificationRequestOptions): string {
  if (!options?.idempotencyKey) throw new Error("idempotencyKey is required");
  return requiredText(options.idempotencyKey, "idempotencyKey", 128);
}

function pageQuery(query: NotificationListQuery): string {
  const params = new URLSearchParams();
  if (query.category !== undefined) params.set("category", query.category);
  if (query.cursor?.trim()) params.set("cursor", requiredText(query.cursor, "cursor", 2048));
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit", 1, 200)));
  if (query.severity !== undefined) params.set("severity", query.severity);
  if (query.status !== undefined) params.set("status", query.status);
  return "?" + params.toString();
}

function subscriptionQuery(query: SubscriptionListQuery): string {
  const params = new URLSearchParams();
  if (query.category !== undefined) params.set("category", query.category);
  if (query.cursor?.trim()) params.set("cursor", requiredText(query.cursor, "cursor", 2048));
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit", 1, 200)));
  if (query.status !== undefined) params.set("status", query.status);
  return "?" + params.toString();
}

function safeTimestamp(value: string, field: string): string {
  const normalized = requiredText(value, field, 64);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(normalized) ||
    Number.isNaN(Date.parse(normalized))
  ) {
    throw new Error(field + " is invalid");
  }
  return normalized;
}

function projectPage<T>(
  value: unknown,
  field: string,
  projector: (item: unknown) => T,
): NotificationPage<T> {
  const source = object(value, field);
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
    const message = sanitizeNotificationMessage(
      error instanceof Error ? error.message : null,
      fallback,
    );
    if (error instanceof ApiError) {
      throw new ApiError(message, error.kind, error.status, undefined, error.retryAfter);
    }
    throw new Error(message, { cause: error });
  }
}

async function mutationRequest(
  scope: NotificationApiScope,
  path: string,
  body: Record<string, unknown>,
  options: NotificationRequestOptions | undefined,
  fallback: string,
): Promise<NotificationMutationOutcome> {
  const key = mutationKey(options);
  const raw = await safeRequest<unknown>(
    path,
    {
      method: "POST",
      headers: headers(scope, key),
      body: JSON.stringify(body),
      signal: options?.signal,
    },
    fallback,
  );
  return projectNotificationMutationOutcome(raw);
}

export function createNotificationIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return "rag4c-notification-" + cryptoApi.randomUUID();
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return (
      "rag4c-notification-" +
      Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
    );
  }
  return (
    "rag4c-notification-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
  );
}

export async function fetchNotificationSummary(
  scope: NotificationApiScope,
  options: NotificationRequestOptions = {},
): Promise<NotificationSummary> {
  const normalized = normalizedScope(scope);
  const raw = await safeRequest<unknown>(
    "/api/enterprise/notifications/summary",
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Notification unread summary is unavailable",
  );
  return projectNotificationSummary(raw, modelScope(normalized));
}

export async function fetchNotifications(
  scope: NotificationApiScope,
  query: NotificationListQuery = {},
  options: NotificationRequestOptions = {},
): Promise<NotificationPage<NotificationInboxItem>> {
  const normalized = normalizedScope(scope);
  const raw = await safeRequest<unknown>(
    "/api/enterprise/notifications" + pageQuery(query),
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Notification inbox is unavailable",
  );
  return projectPage(raw, "notification_page", (item) =>
    projectNotificationInboxItem(item, modelScope(normalized)),
  );
}

export async function fetchNotification(
  scope: NotificationApiScope,
  notificationId: string,
  options: NotificationRequestOptions = {},
): Promise<NotificationDetail> {
  const normalized = normalizedScope(scope);
  const id = requiredId(notificationId, "notificationId");
  const raw = await safeRequest<unknown>(
    "/api/enterprise/notifications/" + encodeURIComponent(id),
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Notification detail is unavailable",
  );
  return projectNotificationDetail(raw, modelScope(normalized));
}

export async function fetchNotificationSubscriptions(
  scope: NotificationApiScope,
  query: SubscriptionListQuery = {},
  options: NotificationRequestOptions = {},
): Promise<NotificationPage<NotificationSubscription>> {
  const normalized = normalizedScope(scope);
  const raw = await safeRequest<unknown>(
    "/api/enterprise/notification-subscriptions" + subscriptionQuery(query),
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Notification subscriptions are unavailable",
  );
  return projectPage(raw, "notification_subscription_page", (item) =>
    projectNotificationSubscription(item, modelScope(normalized)),
  );
}

export function markNotificationRead(
  scope: NotificationApiScope,
  notificationId: string,
  input: ReceiptMutationInput,
  options?: NotificationRequestOptions,
): Promise<NotificationMutationOutcome> {
  const id = requiredId(notificationId, "notificationId");
  return mutationRequest(
    scope,
    "/api/enterprise/notifications/" + encodeURIComponent(id) + "/read",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: reason(input.reason),
    },
    options,
    "Mark notification read is unavailable",
  );
}

export function markNotificationUnread(
  scope: NotificationApiScope,
  notificationId: string,
  input: ReceiptMutationInput,
  options?: NotificationRequestOptions,
): Promise<NotificationMutationOutcome> {
  const id = requiredId(notificationId, "notificationId");
  return mutationRequest(
    scope,
    "/api/enterprise/notifications/" + encodeURIComponent(id) + "/unread",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: reason(input.reason),
    },
    options,
    "Mark notification unread is unavailable",
  );
}

export function archiveNotification(
  scope: NotificationApiScope,
  notificationId: string,
  input: ReceiptMutationInput,
  options?: NotificationRequestOptions,
): Promise<NotificationMutationOutcome> {
  const id = requiredId(notificationId, "notificationId");
  return mutationRequest(
    scope,
    "/api/enterprise/notifications/" + encodeURIComponent(id) + "/archive",
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      reason: reason(input.reason),
    },
    options,
    "Archive notification is unavailable",
  );
}

export function bulkMarkNotificationsRead(
  scope: NotificationApiScope,
  input: BulkReadInput,
  options?: NotificationRequestOptions,
): Promise<NotificationMutationOutcome> {
  if (!Array.isArray(input.items) || input.items.length < 1 || input.items.length > 200) {
    throw new Error("bulk notification read accepts between 1 and 200 items");
  }
  const seen = new Set<string>();
  const items = input.items.map((item) => {
    const notificationId = requiredId(item.notificationId, "notificationId");
    if (seen.has(notificationId)) throw new Error("bulk notification IDs must be unique");
    seen.add(notificationId);
    return {
      notification_id: notificationId,
      expected_revision: exactInteger(item.expectedRevision, "expectedRevision"),
    };
  });
  return mutationRequest(
    scope,
    "/api/enterprise/notifications/bulk-read",
    { items, reason: reason(input.reason) },
    options,
    "Bulk mark notification read is unavailable",
  );
}

export function updateNotificationSubscription(
  scope: NotificationApiScope,
  subscriptionId: string,
  input: UpdateNotificationSubscriptionInput,
  options?: NotificationRequestOptions,
): Promise<NotificationMutationOutcome> {
  const id = requiredId(subscriptionId, "subscriptionId");
  const preference = input.preference;
  if (preference !== "subscribed" && preference !== "muted") {
    throw new Error("preference is invalid");
  }
  const minimumSeverity = input.minimumSeverity;
  if (!["info", "warning", "critical"].includes(minimumSeverity)) {
    throw new Error("minimumSeverity is invalid");
  }
  const mutedUntil =
    input.mutedUntil === null ? null : safeTimestamp(input.mutedUntil, "mutedUntil");
  if (
    (preference === "muted" && mutedUntil === null) ||
    (preference === "subscribed" && mutedUntil !== null)
  ) {
    throw new Error("mutedUntil does not match preference");
  }
  return mutationRequest(
    scope,
    "/api/enterprise/notification-subscriptions/" + encodeURIComponent(id),
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      preference,
      minimum_severity: minimumSeverity,
      muted_until: mutedUntil,
      reason: reason(input.reason),
    },
    { ...options, idempotencyKey: options?.idempotencyKey },
    "Update notification subscription is unavailable",
  );
}

export const fetchNotificationDetail = fetchNotification;
export const markRead = markNotificationRead;
export const markUnread = markNotificationUnread;
export const bulkRead = bulkMarkNotificationsRead;
