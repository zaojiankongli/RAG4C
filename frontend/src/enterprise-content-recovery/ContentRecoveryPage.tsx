import { useMemo } from "react";

import { useEnterpriseContentRecovery } from "./hooks/useEnterpriseContentRecovery";
import {
  sanitizeRecoveryMessage,
  type LegalHold as ModelLegalHold,
  type PurgeRequest as ModelPurgeRequest,
  type RecoveryEntry as ModelRecoveryEntry,
  type RecoveryMutationOutcome as ModelMutationOutcome,
} from "./model/recoveryModel";
import {
  ContentRecoveryCenter,
  type ContentRecoveryController,
  type LegalHold,
  type PurgeRequest,
  type RecoveryEntry,
  type RecoveryMutationOutcome,
} from "./components";

export interface EnterpriseContentRecoveryPageProps {
  tenantId: string;
  actorToken: string;
  capabilityReady: boolean;
  tenantLabel: string;
  readOnly: boolean;
  mobile?: boolean;
  onApprovalHandoff?: (approvalRequestId: string) => void;
}

const RECOVERY_ERROR_FALLBACK = "企业内容恢复权威不可用";

// `safeRequest` 抛出的 message 已经过 `sanitizeRecoveryMessage`，但 hook 里还有
// 不经过 API 层的本地错误，所以这一层再过一次同一把尺子：能安全展示就给原因，
// 不能才回落到固定串。此前这里无条件返回常量，用户永远看不到服务端给的原因。
function safeError(error: Error | null): string | null {
  return error ? sanitizeRecoveryMessage(error.message, RECOVERY_ERROR_FALLBACK) : null;
}

function entryProjection(entry: ModelRecoveryEntry): RecoveryEntry {
  return {
    id: entry.id,
    tenant_id: entry.tenant_id,
    dataset_id: entry.dataset_id,
    dataset_label: entry.dataset_label ?? entry.dataset_id,
    document_id: entry.document_id,
    document_label: entry.document_label ?? entry.document_id,
    status: entry.status,
    revision: entry.revision,
    recycle_generation: entry.recycle_generation,
    original_lifecycle_state: entry.original_lifecycle_state,
    original_retrieval_enabled: entry.original_retrieval_enabled,
    current_retrieval_enabled: entry.current_retrieval_enabled ?? false,
    retention_days_snapshot: entry.retention_days_snapshot,
    recycled_at: entry.recycled_at,
    purge_eligible_at: entry.purge_eligible_at,
    restored_at: entry.restored_at,
    purge_requested_at: entry.purge_requested_at,
    active_hold_count: entry.active_hold_count ?? null,
    safe_snapshot: { ...entry.safe_snapshot },
  };
}

function holdProjection(hold: ModelLegalHold): LegalHold {
  return {
    id: hold.id,
    tenant_id: hold.tenant_id,
    recycle_entry_id: hold.recycle_entry_id,
    document_id: hold.document_id,
    status: hold.status,
    revision: hold.revision,
    reason_code: hold.reason_code,
    safe_reason: hold.safe_reason,
    held_at: hold.held_at,
    held_by: hold.held_by,
    released_at: hold.released_at,
  };
}

function purgeProjection(request: ModelPurgeRequest): PurgeRequest {
  const retentionValue = request.retention_snapshot.retention_days;
  return {
    id: request.id,
    tenant_id: request.tenant_id,
    recycle_entry_id: request.recycle_entry_id,
    document_id: request.document_id,
    status: request.status,
    revision: request.revision,
    expected_entry_revision: request.expected_entry_revision,
    approval_request_id: request.approval_request_id,
    requested_at: request.requested_at,
    requested_by: request.requested_by,
    expires_at: request.expires_at,
    legal_hold_count_snapshot: request.legal_hold_count_snapshot,
    retention_days_snapshot:
      typeof retentionValue === "number" && Number.isInteger(retentionValue) ? retentionValue : 0,
  };
}

function mutationProjection(outcome: ModelMutationOutcome | null): RecoveryMutationOutcome {
  if (!outcome) return { state: "unavailable", message: "Recovery operation unavailable" };
  const state =
    outcome.state === "conflict"
      ? "conflict"
      : outcome.state === "blocked" || outcome.state === "rejected"
        ? "blocked"
        : outcome.state === "unavailable"
          ? "unavailable"
          : outcome.state === "replayed"
            ? "replayed"
            : "applied";
  return {
    state,
    message: outcome.message,
    approval_request_id: outcome.approval_request_id,
  };
}

export default function EnterpriseContentRecoveryPage({
  tenantId,
  actorToken,
  capabilityReady,
  tenantLabel,
  readOnly,
  mobile = false,
  onApprovalHandoff,
}: EnterpriseContentRecoveryPageProps) {
  const recovery = useEnterpriseContentRecovery(
    { tenantId, actorToken },
    { enabled: capabilityReady, readOnly },
  );

  const controller = useMemo<ContentRecoveryController>(() => {
    const entries = recovery.entries.items.map(entryProjection);
    const detailValue = recovery.detail.value;
    const detailEntry = detailValue ? entryProjection(detailValue.entry) : null;
    const detail = detailEntry
      ? {
          ...detailEntry,
          events: detailValue!.events.map((event) => ({
            id: event.id,
            sequence: event.sequence,
            event_type: event.event_type,
            event_digest: event.event_digest,
            previous_event_digest: event.previous_event_digest,
            actor_id: event.actor_id,
            request_id: event.request_id,
            occurred_at: event.occurred_at,
            safe_snapshot: { ...event.safe_snapshot },
          })),
          holds:
            recovery.holds.entryId === detailEntry.id
              ? recovery.holds.items.map(holdProjection)
              : [],
          purge_requests:
            recovery.purgeRequests.entryId === detailEntry.id
              ? recovery.purgeRequests.items.map(purgeProjection)
              : [],
        }
      : null;
    const policy = recovery.retentionPolicy.value;

    const refresh = async () => {
      await recovery.load.reload();
    };
    const refreshEntry = async (entryId: string) => {
      await Promise.all([
        recovery.detail.load(entryId),
        recovery.holds.load(entryId),
        recovery.purgeRequests.load(entryId),
      ]);
    };
    const afterMutation = async (outcome: ModelMutationOutcome | null, entryId?: string) => {
      await recovery.load.reload();
      if (entryId) await refreshEntry(entryId);
      return mutationProjection(outcome);
    };

    return {
      active: recovery.active,
      summary: {
        status: recovery.summary.status,
        value: recovery.summary.value,
        error: safeError(recovery.summary.error),
        reload: refresh,
      },
      entries: {
        status: recovery.entries.status,
        items: entries,
        invalidItemCount: recovery.entries.invalidItemCount,
        nextCursor: recovery.entries.nextCursor,
        error: safeError(recovery.entries.error),
        reload: refresh,
      },
      detail: {
        status: recovery.detail.status,
        value: detail,
        error: safeError(recovery.detail.error),
        load: async (entryId?: string) => (entryId ? refreshEntry(entryId) : false),
      },
      retentionPolicy: {
        status: recovery.retentionPolicy.status,
        value: policy
          ? {
              id: policy.id,
              tenant_id: policy.tenant_id,
              status: policy.status,
              retention_days: policy.retention_days,
              auto_purge_enabled: policy.auto_purge_enabled,
              purge_requires_approval: policy.purge_requires_approval,
              revision: policy.revision,
              updated_at: policy.updated_at,
              updated_by: policy.updated_by,
            }
          : null,
        error: safeError(recovery.retentionPolicy.error),
        load: recovery.retentionPolicy.load,
      },
      mutation: {
        status: recovery.mutation.status,
        error: safeError(recovery.mutation.error),
        restore: async (entry) =>
          afterMutation(
            await recovery.mutation.restore(
              entry.id,
              { expectedRevision: entry.revision, reason: "Recovery Center explicit restore" },
              undefined,
            ),
            entry.id,
          ),
        applyHold: async (entry, input) =>
          afterMutation(
            await recovery.mutation.applyHold(
              entry.id,
              {
                expectedRevision: entry.revision,
                reasonCode: input.reason_code,
                safeReason: input.safe_reason,
              },
              undefined,
            ),
            entry.id,
          ),
        releaseHold: async (hold) =>
          afterMutation(
            await recovery.mutation.releaseHold(
              hold.recycle_entry_id,
              hold.id,
              { expectedRevision: hold.revision, reason: "Recovery Center release legal hold" },
              undefined,
            ),
            hold.recycle_entry_id,
          ),
        requestPurge: async (entry) =>
          afterMutation(
            await recovery.mutation.requestPurge(
              entry.id,
              {
                expectedRevision: entry.revision,
                reason: "Recovery Center request purge approval",
              },
              undefined,
            ),
            entry.id,
          ),
        updatePolicy: async (input) =>
          afterMutation(
            await recovery.mutation.updatePolicy(
              {
                expectedRevision: input.expected_revision,
                status: input.status,
                retentionDays: input.retention_days,
                autoPurgeEnabled: input.auto_purge_enabled,
                purgeRequiresApproval: input.purge_requires_approval,
                reason: "Recovery Center explicit retention policy update",
              },
              undefined,
            ),
          ),
      },
    };
  }, [recovery]);

  return (
    <ContentRecoveryCenter
      controller={controller}
      capabilityReady={capabilityReady}
      readOnly={readOnly}
      mobile={mobile}
      tenantLabel={tenantLabel}
      onApprovalHandoff={onApprovalHandoff}
    />
  );
}
