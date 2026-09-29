/** Frontend-owned projection of the Task Operations cross-layer vocabulary.
 *
 * Backend category/status facts stay canonical. This module owns the narrower
 * UI labels and read-compatible aliases without importing Python runtime code.
 */
export const TASK_SOURCE_KIND_VALUES = Object.freeze([
  "document_ingest",
  "index_operation",
  "source_sync",
  "document_delete",
  "audit_export",
  "release_quality_scan",
  "release_recertification",
] as const);

export type TaskSourceKind = (typeof TASK_SOURCE_KIND_VALUES)[number];

export const TASK_CATEGORY_FACT_VALUES = Object.freeze([
  "documents",
  "indexing",
  "sources",
  "compliance",
  "quality",
] as const);

export type TaskFactCategory = (typeof TASK_CATEGORY_FACT_VALUES)[number];

export const TASK_DISPLAY_CATEGORY_VALUES = Object.freeze([
  "content",
  "indexing",
  "source",
  "compliance",
  "quality",
] as const);

export type TaskCategory = (typeof TASK_DISPLAY_CATEGORY_VALUES)[number];

/** Values accepted by the HTTP SavedView category contract (not DB values). */
export const TASK_CATEGORY_API_VALUES = Object.freeze([
  "content",
  "documents",
  "indexing",
  "source",
  "sources",
  "compliance",
  "quality",
] as const);

export type TaskApiCategory = (typeof TASK_CATEGORY_API_VALUES)[number];

const CATEGORY_ALIAS_TO_FACT: Readonly<Record<string, TaskFactCategory>> = Object.freeze({
  documents: "documents",
  document: "documents",
  content: "documents",
  ingest: "documents",
  indexing: "indexing",
  index: "indexing",
  sources: "sources",
  source: "sources",
  compliance: "compliance",
  audit: "compliance",
  quality: "quality",
  release_quality: "quality",
});

export const TASK_CATEGORY_FACT_TO_DISPLAY: Readonly<
  Record<TaskFactCategory, TaskCategory>
> = Object.freeze({
  documents: "content",
  indexing: "indexing",
  sources: "source",
  compliance: "compliance",
  quality: "quality",
});

export const TASK_SOURCE_KIND_CATEGORY: Readonly<Record<TaskSourceKind, TaskFactCategory>> =
  Object.freeze({
    document_ingest: "documents",
    index_operation: "indexing",
    source_sync: "sources",
    document_delete: "documents",
    audit_export: "compliance",
    release_quality_scan: "quality",
    release_recertification: "quality",
  });

export const TASK_CATEGORY_LABELS: Readonly<Record<TaskCategory, string>> = Object.freeze({
  content: "内容",
  indexing: "索引",
  source: "来源",
  compliance: "合规",
  quality: "质量",
});

export const TASK_STATUS_FACT_VALUES = Object.freeze([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "unavailable",
] as const);

export type TaskStatus = (typeof TASK_STATUS_FACT_VALUES)[number];

export const TASK_DISPLAY_STATUS_VALUES = Object.freeze([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
  "blocked",
  "unavailable",
] as const);

export type TaskDisplayStatus = (typeof TASK_DISPLAY_STATUS_VALUES)[number];

export const TASK_STATUS_DISPLAY_LABELS: Readonly<Record<TaskDisplayStatus, string>> =
  Object.freeze({
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    blocked: "已阻塞",
    unavailable: "来源不可用",
  });

const TASK_STATUS_FACT_SET = new Set<string>(TASK_STATUS_FACT_VALUES);
const TASK_SOURCE_KIND_SET = new Set<string>(TASK_SOURCE_KIND_VALUES);
const TASK_DISPLAY_CATEGORY_SET = new Set<string>(TASK_DISPLAY_CATEGORY_VALUES);

function normalizedCategoryInput(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field} is not allowed`);
  return value.trim().toLowerCase().replace(/[- ]/g, "_");
}

export function normalizeTaskCategory(
  value: unknown,
  field = "category",
): TaskFactCategory {
  const key = normalizedCategoryInput(value, field);
  if (!Object.prototype.hasOwnProperty.call(CATEGORY_ALIAS_TO_FACT, key))
    throw new Error(`${field} is not allowed`);
  return CATEGORY_ALIAS_TO_FACT[key];
}

export function savedViewTaskCategory(value: unknown, field = "category"): TaskCategory {
  const factCategory = normalizeTaskCategory(value, field);
  return TASK_CATEGORY_FACT_TO_DISPLAY[factCategory];
}

export function displayTaskCategory(
  value: unknown,
  sourceKind: TaskSourceKind,
): TaskCategory {
  if (!TASK_SOURCE_KIND_SET.has(sourceKind)) throw new Error("source_kind is not allowed");
  const factCategory = normalizeTaskCategory(value);
  if (TASK_SOURCE_KIND_CATEGORY[sourceKind] !== factCategory)
    throw new Error("category does not match source_kind");
  return TASK_CATEGORY_FACT_TO_DISPLAY[factCategory];
}

export function isTaskSourceKind(value: unknown): value is TaskSourceKind {
  return typeof value === "string" && TASK_SOURCE_KIND_SET.has(value);
}

export function isTaskStatus(value: unknown): value is TaskStatus {
  return typeof value === "string" && TASK_STATUS_FACT_SET.has(value);
}

export function isTaskDisplayCategory(value: unknown): value is TaskCategory {
  return typeof value === "string" && TASK_DISPLAY_CATEGORY_SET.has(value);
}

export function normalizeTaskStatus(
  value: unknown,
  field = "normalized_status",
): TaskStatus {
  if (typeof value !== "string") throw new Error(`${field} is not allowed`);
  const normalized = value.trim();
  const fact = normalized === "completed" ? "succeeded" : normalized;
  if (!TASK_STATUS_FACT_SET.has(fact)) throw new Error(`${field} is not allowed`);
  return fact as TaskStatus;
}

export function displayTaskStatus(value: TaskStatus): TaskDisplayStatus {
  return value === "succeeded" ? "completed" : value;
}
