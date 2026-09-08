export type RecoveryEntryStatus =
  "recycled" | "restoring" | "restored" | "purge_requested" | "purged" | "failed";

export type RecoveryResourceStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "unavailable" | "error";

export type RecoveryMutationStatus = "idle" | "saving" | "success" | "error";

export interface RecoverySummary {
  state: "ready" | "partial" | "unavailable" | "error";
  tenant_id: string;
  as_of: string | null;
  recycled_count: number | null;
  expiring_count: number | null;
  held_count: number | null;
  purge_pending_count?: number | null;
  pending_purge_count?: number | null;
  reason_code: string | null;
}

export interface RecoveryEntry {
  id: string;
  tenant_id: string;
  dataset_id: string;
  dataset_label: string;
  document_id: string;
  document_label: string;
  status: RecoveryEntryStatus;
  revision: number;
  recycle_generation: number;
  original_lifecycle_state: "active" | "expired";
  original_retrieval_enabled: boolean;
  current_retrieval_enabled: boolean;
  retention_days_snapshot: number | null;
  recycled_at: string | null;
  purge_eligible_at: string | null;
  restored_at: string | null;
  purge_requested_at: string | null;
  active_hold_count: number | null;
  safe_snapshot?: Record<string, string | number | boolean | null>;
}

export interface RecoveryEvent {
  id: string;
  sequence: number;
  event_type:
    | "recycled"
    | "restored"
    | "hold_applied"
    | "hold_released"
    | "purge_requested"
    | "purge_approved"
    | "purge_cancelled";
  event_digest: string;
  previous_event_digest: string | null;
  actor_id: string;
  request_id: string;
  occurred_at: string;
  safe_snapshot: Record<string, string | number | boolean | null>;
}

export interface LegalHold {
  id: string;
  tenant_id: string;
  recycle_entry_id: string;
  document_id: string;
  status: "active" | "released";
  revision: number;
  reason_code: string;
  safe_reason: string;
  held_at: string;
  held_by: string;
  released_at: string | null;
}

export interface PurgeRequest {
  id: string;
  tenant_id: string;
  recycle_entry_id: string;
  document_id: string;
  status: "pending_approval" | "approved" | "cancelled" | "expired" | "executed";
  revision: number;
  expected_entry_revision: number;
  approval_request_id: string | null;
  requested_at: string;
  requested_by: string;
  expires_at: string | null;
  legal_hold_count_snapshot: number;
  retention_days_snapshot: number;
}

export interface RecoveryEntryDetail extends RecoveryEntry {
  events: RecoveryEvent[];
  holds: LegalHold[];
  purge_requests: PurgeRequest[];
}

export interface RetentionPolicy {
  id: string;
  tenant_id: string;
  status: "active" | "paused";
  retention_days: number;
  auto_purge_enabled: boolean;
  purge_requires_approval: boolean;
  revision: number;
  updated_at: string | null;
  updated_by: string | null;
}

export interface RecoveryResource<T> {
  status: RecoveryResourceStatus;
  value: T | null;
  error: string | null;
  load?: (id?: string) => void | Promise<unknown>;
  reload?: () => void | Promise<unknown>;
}

export interface RecoveryCollection<T> {
  status: RecoveryResourceStatus;
  items: T[];
  invalidItemCount: number;
  nextCursor: string | null;
  error: string | null;
  reload?: () => void | Promise<unknown>;
}

export interface LegalHoldInput {
  reason_code: string;
  safe_reason: string;
}

export interface RetentionPolicyInput {
  status: RetentionPolicy["status"];
  retention_days: number;
  auto_purge_enabled: boolean;
  purge_requires_approval: boolean;
  expected_revision: number;
}

export interface RecoveryMutationOutcome {
  state: "applied" | "replayed" | "conflict" | "blocked" | "unavailable";
  message?: string | null;
  approval_request_id?: string | null;
}

export interface RecoveryMutationController {
  status: RecoveryMutationStatus;
  error: string | null;
  restore?: (entry: RecoveryEntry) => void | Promise<RecoveryMutationOutcome>;
  applyHold?: (
    entry: RecoveryEntry,
    input: LegalHoldInput,
  ) => void | Promise<RecoveryMutationOutcome>;
  releaseHold?: (hold: LegalHold) => void | Promise<RecoveryMutationOutcome>;
  requestPurge?: (entry: RecoveryEntry) => void | Promise<RecoveryMutationOutcome>;
  updatePolicy?: (input: RetentionPolicyInput) => void | Promise<RecoveryMutationOutcome>;
}

export interface ContentRecoveryController {
  active: boolean;
  summary: RecoveryResource<RecoverySummary>;
  entries: RecoveryCollection<RecoveryEntry>;
  detail: RecoveryResource<RecoveryEntryDetail>;
  retentionPolicy: RecoveryResource<RetentionPolicy>;
  mutation: RecoveryMutationController;
}

export type RecoveryApprovalHandoff = (approvalRequestId: string) => void;
