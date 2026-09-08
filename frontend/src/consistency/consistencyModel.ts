export interface ConsistencyCounts {
  documents_scanned: number;
  authoritative_heads: number;
  projection_chunks: number;
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

export interface ConsistencySummaryResponse {
  mode: "report-only";
  best_effort: true;
  counts: ConsistencyCounts;
  drift_categories: ConsistencyDriftCategories;
  manifest_ref: string;
  complete: false;
  confirmable: false;
  snapshot_guarantee: "catalog_only";
  has_drift: boolean;
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
  };
}
