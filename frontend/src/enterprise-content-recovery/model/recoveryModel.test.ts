import { describe, expect, it } from "vitest";
import {
  projectContentRetentionPolicy,
  projectDocumentLegalHold,
  projectDocumentPurgeRequest,
  projectRecoveryEntry,
  projectRecoveryEntryDetail,
  projectRecoveryEvent,
  projectRecoveryMutationOutcome,
  projectRecoveryRoute,
  projectRecoverySummary,
  type RecoveryModelScope,
} from "./recoveryModel";

const timestamp = "2026-08-29T12:34:56.000000Z";
const digest = "a".repeat(64);
const nextDigest = "b".repeat(64);
const scope: RecoveryModelScope = { tenantId: "tenant-a" };

function policyRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "policy-a",
    tenant_id: "tenant-a",
    status: "active",
    retention_days: 30,
    auto_purge_enabled: false,
    purge_requires_approval: true,
    revision: 2,
    created_at: timestamp,
    created_by: "account-a",
    updated_at: timestamp,
    updated_by: "account-a",
    ...overrides,
  };
}

function entryRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "entry-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    document_id: "document-a",
    recycle_generation: 1,
    active_recycle_key: "dataset-a:document-a",
    status: "recycled",
    revision: 3,
    document_mutation_generation: 8,
    original_lifecycle_state: "active",
    original_retrieval_enabled: true,
    retention_days_snapshot: 30,
    recycled_at: timestamp,
    recycled_by: "account-a",
    purge_eligible_at: "2026-09-28T12:34:56.000000Z",
    restored_at: null,
    restored_by: null,
    purge_requested_at: null,
    purged_at: null,
    purged_by: null,
    safe_snapshot_json: {
      dataset_id: "dataset-a",
      document_id: "document-a",
      original_lifecycle_state: "active",
      original_retrieval_enabled: true,
    },
    snapshot_digest: digest,
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function holdRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "hold-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    document_id: "document-a",
    recycle_entry_id: "entry-a",
    status: "active",
    active_hold_key: "entry-a:legal_review",
    revision: 1,
    reason_code: "legal_review",
    safe_reason: "Legal review hold",
    held_at: timestamp,
    held_by: "account-a",
    released_at: null,
    released_by: null,
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function purgeRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "purge-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    document_id: "document-a",
    recycle_entry_id: "entry-a",
    status: "pending_approval",
    revision: 1,
    expected_entry_revision: 3,
    request_digest: nextDigest,
    idempotency_key_digest: digest,
    approval_request_id: "approval-a",
    route: {
      target_route_code: "enterprise_approval",
      target_route_params_json: { approval_request_id: "approval-a" },
    },
    retention_snapshot_json: {
      retention_days: 30,
      purge_eligible_at: "2026-09-28T12:34:56.000000Z",
    },
    legal_hold_count_snapshot: 0,
    requested_at: timestamp,
    requested_by: "account-a",
    approved_at: null,
    cancelled_at: null,
    cancelled_by: null,
    expires_at: "2026-10-05T12:34:56.000000Z",
    executed_at: null,
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function eventRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "event-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    document_id: "document-a",
    recycle_entry_id: "entry-a",
    sequence: 1,
    event_type: "recycled",
    previous_event_digest: null,
    event_digest: digest,
    actor_id: "account-a",
    request_id: "request-a",
    safe_snapshot_json: { status: "recycled", revision: 3 },
    occurred_at: timestamp,
    ...overrides,
  };
}

describe("enterprise content recovery model projectors", () => {
  it("projects strict policy, entry, hold, purge, event and summary authority", () => {
    const policy = projectContentRetentionPolicy(policyRaw(), scope);
    const entry = projectRecoveryEntry(entryRaw(), scope);
    const hold = projectDocumentLegalHold(holdRaw(), scope);
    const purge = projectDocumentPurgeRequest(purgeRaw(), scope);
    const event = projectRecoveryEvent(eventRaw(), scope);
    const summary = projectRecoverySummary(
      {
        tenant_id: "tenant-a",
        state: "ready",
        recycled_count: 4,
        expiring_count: 1,
        held_count: 1,
        pending_purge_count: 1,
        as_of: timestamp,
      },
      scope,
    );

    expect(policy.retention_days).toBe(30);
    expect(entry.active_recycle_key).toBe("dataset-a:document-a");
    expect(hold.active_hold_key).toBe("entry-a:legal_review");
    expect(purge.route).toEqual({
      code: "enterprise_approval",
      path: "/enterprise/approvals",
      query: { request: "approval-a" },
      href: "/enterprise/approvals?request=approval-a",
    });
    expect(event.event_type).toBe("recycled");
    expect(summary).toMatchObject({ recycled_count: 4, held_count: 1 });
  });

  it("projects detail event chains and rejects inconsistent lifecycle or tenant authority", () => {
    const detail = projectRecoveryEntryDetail(
      {
        entry: entryRaw(),
        events: [
          eventRaw(),
          eventRaw({
            id: "event-b",
            sequence: 2,
            event_type: "restored",
            previous_event_digest: digest,
            event_digest: nextDigest,
          }),
        ],
      },
      scope,
    );
    expect(detail.events).toHaveLength(2);

    expect(() => projectRecoveryEntry(entryRaw({ tenant_id: "tenant-b" }), scope)).toThrow(
      /tenant|scope/i,
    );
    expect(() => projectRecoveryEntry({ ...entryRaw(), unknown: true }, scope)).toThrow(
      /unexpected|field/i,
    );
    expect(() =>
      projectRecoveryEntry(
        entryRaw({ status: "restored", active_recycle_key: "dataset-a:document-a" }),
        scope,
      ),
    ).toThrow(/active_recycle_key|lifecycle|identity/i);
    expect(() =>
      projectRecoveryEntryDetail(
        {
          entry: entryRaw(),
          events: [eventRaw({ sequence: 2, previous_event_digest: nextDigest })],
        },
        scope,
      ),
    ).toThrow(/sequence|chain|previous/i);
  });

  it("rejects unsafe snapshots, reasons and route injection", () => {
    expect(() =>
      projectRecoveryEntry(entryRaw({ safe_snapshot_json: { body: "raw document body" } }), scope),
    ).toThrow(/unsafe|snapshot|body/i);
    expect(() => projectDocumentLegalHold(holdRaw({ safe_reason: "token=secret" }), scope)).toThrow(
      /unsafe|reason|token/i,
    );
    expect(() =>
      projectDocumentPurgeRequest(
        purgeRaw({ approval_request_id: "https://evil.example/purge" }),
        scope,
      ),
    ).toThrow(/route|approval|unsafe|invalid/i);
    expect(() =>
      projectRecoveryRoute({ approval_request_id: "approval-a", href: "https://evil.example" }),
    ).toThrow(/unexpected|route|field/i);
  });

  it("preserves partial summary authority and presentation-safe entry facts", () => {
    const partial = projectRecoverySummary(
      {
        tenant_id: "tenant-a",
        state: "partial",
        recycled_count: 4,
        expiring_count: null,
        held_count: 1,
        purge_pending_count: 1,
        as_of: timestamp,
        reason_code: "holds_temporarily_unavailable",
      },
      scope,
    );
    expect(partial.state).toBe("partial");
    expect(partial.expiring_count).toBeNull();
    expect(partial.purge_pending_count).toBe(1);

    const projected = projectRecoveryEntry(
      entryRaw({
        dataset_label: "产品知识库",
        document_label: "接入指南.md",
        current_retrieval_enabled: false,
        active_hold_count: 0,
      }),
      scope,
    );
    expect(projected.dataset_label).toBe("产品知识库");
    expect(projected.current_retrieval_enabled).toBe(false);
    expect(projected.active_hold_count).toBe(0);
  });

  it("keeps unavailable and zero summary states exact", () => {
    const zero = projectRecoverySummary(
      {
        tenant_id: "tenant-a",
        state: "ready",
        recycled_count: 0,
        expiring_count: 0,
        held_count: 0,
        pending_purge_count: 0,
        as_of: timestamp,
      },
      scope,
    );
    expect(zero.recycled_count).toBe(0);
    expect(zero.state).toBe("ready");

    const unavailable = projectRecoverySummary(
      {
        tenant_id: "tenant-a",
        state: "unavailable",
        recycled_count: null,
        expiring_count: null,
        held_count: null,
        pending_purge_count: null,
        as_of: null,
        reason_code: "recovery_authority_unavailable",
      },
      scope,
    );
    expect(unavailable.state).toBe("unavailable");
    expect(unavailable.recycled_count).toBeNull();
    expect(() =>
      projectRecoverySummary(
        {
          tenant_id: "tenant-a",
          state: "ready",
          recycled_count: null,
          expiring_count: 0,
          held_count: 0,
          pending_purge_count: 0,
          as_of: timestamp,
        },
        scope,
      ),
    ).toThrow(/count|summary/i);
  });

  it("projects safe mutation outcomes without leaking arbitrary messages", () => {
    expect(
      projectRecoveryMutationOutcome({
        state: "approval_required",
        operation: "request_purge",
        resource_id: "purge-a",
        approval_request_id: "approval-a",
        route: {
          target_route_code: "enterprise_approval",
          target_route_params_json: { approval_request_id: "approval-a" },
        },
        revision: 2,
        message: "等待审批",
        retryable: false,
      }),
    ).toMatchObject({
      state: "approval_required",
      resource_id: "purge-a",
      approval_request_id: "approval-a",
      revision: 2,
    });
    expect(
      projectRecoveryMutationOutcome({
        state: "unavailable",
        operation: "restore",
        resource_id: null,
        approval_request_id: null,
        route: null,
        revision: null,
        message: "恢复服务暂不可用",
        retryable: true,
      }).message,
    ).toBe("恢复服务暂不可用");
    expect(() =>
      projectRecoveryMutationOutcome({
        state: "applied",
        operation: "restore",
        resource_id: "entry-a",
        approval_request_id: null,
        route: null,
        revision: 4,
        message: "authorization: Bearer abc",
        retryable: false,
      }),
    ).not.toThrow();
    expect(
      projectRecoveryMutationOutcome({
        state: "applied",
        operation: "restore",
        resource_id: "entry-a",
        approval_request_id: null,
        route: null,
        revision: 4,
        message: "authorization: Bearer abc",
        retryable: false,
      }).message,
    ).toBe("Recovery operation unavailable");
  });
});
