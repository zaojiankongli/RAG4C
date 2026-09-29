export interface ConsistencyCounts {
  documents_scanned: number;
  authoritative_heads: number;
  projection_chunks: number;
  projection_read_incomplete_documents: number;
  missing_chunks: number;
  stale_chunks: number;
  orphaned_chunks: number;
  blocked_documents: number;
}

export interface ConsistencyDriftCategories {
  missing: number;
  stale: number;
  orphaned: number;
  blocked: number;
  stale_reasons: Record<string, number>;
}

export interface ConsistencyQaAuthority {
  total: number;
  effective_retrieval: number;
  pending_review: number;
  rejected: number;
  expired: number;
  retrieval_disabled: number;
  note?: string | null;
}

export interface ConsistencySummaryResponse {
  mode: "report-only";
  projection_read_status: "best_effort" | "incomplete";
  best_effort: true;
  counts: ConsistencyCounts;
  drift_categories: ConsistencyDriftCategories;
  manifest_ref: string;
  complete: false;
  confirmable: false;
  snapshot_guarantee: "catalog_only";
  has_drift: boolean;
  qa_authority?: ConsistencyQaAuthority | null;
}

export interface DeadLetterItem {
  dead_letter_ref: string;
  operation_ref: string;
  document_ref: string;
  target_store: string;
  operation: string;
  retry_count: number;
  failed_at: string;
  requeued_operation_ref: string | null;
}

export interface DeadLetterListResponse {
  items: DeadLetterItem[];
  count: number;
}

export interface DeadLetterRequeueResponse {
  status: "enqueued" | "already_requeued";
  dead_letter_ref: string;
  operation_ref: string;
}

export type ConsistencyCategoryKey = "missing" | "stale" | "orphaned" | "blocked";

export interface ConsistencyCategoryProjection {
  key: ConsistencyCategoryKey;
  label: string;
  count: number;
}

export interface ConsistencySummaryProjection {
  desired: number;
  projection: number;
  drift: number;
  categories: ConsistencyCategoryProjection[];
  bestEffort: boolean;
  catalogOnly: boolean;
  complete: false;
  confirmable: false;
  qaAuthority: ConsistencyQaAuthority | null;
  projectionReadIncompleteDocuments: number;
  projectionReadStatus: "best_effort" | "incomplete";
}

function asNonNeg(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
}

export function normalizeQaAuthority(
  payload: unknown,
): ConsistencyQaAuthority | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  // 需要完整计数字段；缺任一关键字段则返回 null（不用 0 伪装生产事实）
  const required = [
    "total",
    "effective_retrieval",
    "pending_review",
    "rejected",
    "expired",
    "retrieval_disabled",
  ] as const;
  for (const key of required) {
    if (typeof record[key] !== "number" && typeof record[key] !== "string") {
      return null;
    }
  }
  return {
    total: asNonNeg(record.total),
    effective_retrieval: asNonNeg(record.effective_retrieval),
    pending_review: asNonNeg(record.pending_review),
    rejected: asNonNeg(record.rejected),
    expired: asNonNeg(record.expired),
    retrieval_disabled: asNonNeg(record.retrieval_disabled),
    note: typeof record.note === "string" && record.note.trim() ? record.note.trim() : null,
  };
}

export function projectConsistencySummary(
  summary: ConsistencySummaryResponse,
): ConsistencySummaryProjection {
  const categories: ConsistencyCategoryProjection[] = [
    { key: "missing", label: "缺失", count: summary.drift_categories.missing },
    { key: "stale", label: "过期", count: summary.drift_categories.stale },
    { key: "orphaned", label: "孤儿", count: summary.drift_categories.orphaned },
    { key: "blocked", label: "阻塞", count: summary.drift_categories.blocked },
  ];
  return {
    desired: summary.counts.authoritative_heads,
    projection: summary.counts.projection_chunks,
    drift: categories.reduce((total, category) => total + category.count, 0),
    categories,
    bestEffort: summary.best_effort === true,
    catalogOnly: summary.snapshot_guarantee === "catalog_only",
    complete: summary.complete,
    confirmable: summary.confirmable,
    qaAuthority: normalizeQaAuthority(summary.qa_authority),
    projectionReadIncompleteDocuments: asNonNeg(
      summary.counts.projection_read_incomplete_documents,
    ),
    projectionReadStatus: summary.projection_read_status,
  };
}
