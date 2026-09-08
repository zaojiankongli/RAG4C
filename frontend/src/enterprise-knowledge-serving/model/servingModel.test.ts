import { describe, expect, it } from "vitest";
import {
  deriveServingOverallState,
  projectServingEvent,
  projectServingEventPage,
  projectServingEvidenceLink,
  projectServingEvidenceRoute,
  projectServingHandoff,
  projectServingPage,
  projectServingPreview,
  projectServingProfile,
  projectServingSnapshotDetail,
  projectServingSummary,
  projectServingStageFact,
  safeServingDisplayText,
  type ServingScope,
} from "./servingModel";

const scope: ServingScope = {
  tenantId: "tenant-a",
  accountId: "account-a",
  datasetId: "dataset-a",
};
const digest = "a".repeat(64);
const digestB = "b".repeat(64);
const now = "2026-08-30T00:00:00Z";

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: "profile-a",
    tenant_id: "tenant-a",
    workspace_id: "workspace-a",
    dataset_id: "dataset-a",
    name: "产品知识库服务",
    normalized_name: String("产品知识库服务").toLocaleLowerCase(),
    status: "active",
    active_profile_key: "dataset-a",
    revision: 4,
    current_policy_revision_id: "policy-a",
    current_snapshot_id: "snapshot-a",
    created_at: now,
    created_by: "account-a",
    updated_at: now,
    updated_by: "account-a",
    archived_at: null,
    archived_by: null,
    ...overrides,
  };
}

function stage(
  stage_code: string,
  sequence: number,
  state = "ready",
  overrides: Record<string, unknown> = {},
) {
  return {
    id: `${stage_code}-fact`,
    tenant_id: "tenant-a",
    profile_id: "profile-a",
    snapshot_id: "snapshot-a",
    stage_code,
    sequence,
    state,
    item_count: 10,
    ready_count: 8,
    warning_count: 1,
    pending_count: 1,
    error_count: 0,
    lag_seconds: 0,
    expected_revision: 4,
    observed_revision: 4,
    expected_digest: digest,
    observed_digest: digest,
    safe_error_code: null,
    safe_error: null,
    stage_digest: digest,
    observed_at: now,
    ...overrides,
  };
}

function summary(overrides: Record<string, unknown> = {}) {
  return {
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    profile_id: "profile-a",
    profile_name: "产品知识库服务",
    profile_status: "active",
    state: "ready",
    as_of: now,
    snapshot_id: "snapshot-a",
    snapshot_digest: digest,
    policy_revision: 4,
    serving_generation: 18,
    source_count: 12,
    ready_source_count: 12,
    stale_source_count: 0,
    active_document_count: 240,
    failed_document_count: 0,
    pending_index_count: 0,
    stage_count: 5,
    ready_stage_count: 5,
    blocked_stage_count: 0,
    current_release_id: "release-18",
    current_certification_id: "cert-18",
    reason_code: null,
    stage_facts: [
      stage("source", 1),
      stage("parse", 2),
      stage("chunk", 3),
      stage("index", 4),
      stage("serve", 5),
    ],
    ...overrides,
  };
}

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    id: "snapshot-a",
    tenant_id: "tenant-a",
    profile_id: "profile-a",
    policy_revision_id: "policy-a",
    observation_key: "observation-a",
    state: "ready",
    source_count: 12,
    ready_source_count: 12,
    stale_source_count: 0,
    active_document_count: 240,
    failed_document_count: 0,
    pending_index_count: 0,
    expected_serving_generation: 18,
    observed_serving_generation: 18,
    current_release_id: "release-18",
    current_certification_id: "cert-18",
    stage_count: 5,
    ready_stage_count: 5,
    blocked_stage_count: 0,
    snapshot_digest: digest,
    as_of: now,
    created_at: now,
    created_by: "account-a",
    ...overrides,
  };
}

function servingEvent(safe_snapshot: unknown) {
  return {
    id: "event-safe-mapping",
    tenant_id: "tenant-a",
    profile_id: "profile-a",
    snapshot_id: "snapshot-a",
    stream_key: "profile-a",
    sequence: 1,
    event_type: "snapshot_recorded",
    previous_event_digest: null,
    event_digest: digest,
    actor_id: "account-a",
    request_id: "request-a",
    safe_snapshot,
    occurred_at: now,
  };
}

function evidenceLink(overrides: Record<string, unknown> = {}) {
  return {
    id: "evidence-a",
    tenant_id: "tenant-a",
    profile_id: "profile-a",
    snapshot_id: "snapshot-a",
    stage_fact_id: "source-fact",
    evidence_kind: "source",
    resource_id: "resource-a",
    resource_revision: 1,
    resource_digest: digest,
    route_code: "knowledge_sources",
    safe_label: "Source evidence",
    evidence_digest: digest,
    created_at: now,
    ...overrides,
  };
}

function nestedSafeMapping(depth: number): unknown {
  let value: unknown = "ready";
  for (let level = depth; level > 0; level -= 1) {
    value = { [`level_${level}`]: value };
  }
  return value;
}

describe("Stage26 strict serving model", () => {
  it("accepts a safe event mapping nested to the backend maximum depth of twelve", () => {
    const mapped = projectServingEvent(servingEvent(nestedSafeMapping(12)), scope);

    expect(mapped.safe_snapshot).toEqual(nestedSafeMapping(12));
  });

  it("rejects safe event mapping numbers beyond the JavaScript safe integer range", () => {
    expect(() =>
      projectServingEvent(servingEvent({ observed_count: Number.MAX_SAFE_INTEGER + 1 }), scope),
    ).toThrow(/safe integer/);
  });

  it("measures safe mapping strings by UTF-8 bytes instead of JavaScript character count", () => {
    const value = "知".repeat(3_000);

    expect(projectServingEvent(servingEvent({ detail: value }), scope).safe_snapshot).toEqual({
      detail: value,
    });
  });

  it("rejects safe mappings whose aggregate UTF-8 payload exceeds 16384 bytes", () => {
    const value = "知".repeat(3_000);

    expect(() => projectServingEvent(servingEvent({ first: value, second: value }), scope)).toThrow(
      /exceeds safe byte limit/,
    );
  });

  it("rejects safe event mappings deeper than the backend maximum depth", () => {
    expect(() => projectServingEvent(servingEvent(nestedSafeMapping(13)), scope)).toThrow(
      /too deeply nested/,
    );
  });

  it("requires the backend canonical route code for every evidence kind", () => {
    const cases = [
      ["source", "knowledge_sources"],
      ["source_sync_run", "knowledge_sources"],
      ["document", "knowledge_documents"],
      ["ingest_attempt", "knowledge_documents"],
      ["chunk_head", "knowledge_documents"],
      ["index_operation", "knowledge_indexing"],
      ["release", "knowledge_base_releases"],
      ["certification", "release_quality"],
      ["task", "enterprise_tasks"],
    ] as const;

    for (const [evidence_kind, route_code] of cases) {
      expect(
        projectServingEvidenceLink(evidenceLink({ evidence_kind, route_code }), scope),
      ).toEqual(expect.objectContaining({ evidence_kind, route_code }));
      const wrongRoute =
        route_code === "knowledge_sources" ? "knowledge_documents" : "knowledge_sources";
      expect(() =>
        projectServingEvidenceLink(evidenceLink({ evidence_kind, route_code: wrongRoute }), scope),
      ).toThrow(/canonical|evidence route/);
    }
  });

  it("preserves opaque release and certification resource IDs in handoff projections", () => {
    expect(
      projectServingHandoff(
        {
          evidence_kind: "release",
          route_code: "knowledge_base_releases",
          dataset_id: "dataset-a",
          resource_id: "release-a&channel=stable",
        },
        scope,
      ),
    ).toEqual({
      evidence_kind: "release",
      route_code: "knowledge_base_releases",
      dataset_id: "dataset-a",
      resource_id: "release-a&channel=stable",
    });
    expect(
      projectServingHandoff(
        {
          evidence_kind: "certification",
          route_code: "release_quality",
          dataset_id: "dataset-a",
          resource_id: "cert/a?revision=2",
        },
        scope,
      ).resource_id,
    ).toBe("cert/a?revision=2");
  });

  it("projects every evidence kind to its canonical encoded path and query parameter", () => {
    const cases = [
      ["source", "knowledge_sources", "/enterprise/knowledge-base", "source"],
      ["source_sync_run", "knowledge_sources", "/enterprise/knowledge-base", "sync"],
      ["document", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["ingest_attempt", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["chunk_head", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["index_operation", "knowledge_indexing", "/enterprise/tasks", "operation"],
      ["release", "knowledge_base_releases", "/enterprise/knowledge-base", "release"],
      ["certification", "release_quality", "/enterprise/knowledge-base", "certification"],
      ["task", "enterprise_tasks", "/enterprise/tasks", "task"],
    ] as const;

    for (const [evidence_kind, code, path, parameter] of cases) {
      const resource_id = `${evidence_kind}-a&segment=1`;
      expect(
        projectServingEvidenceRoute(
          evidenceLink({ evidence_kind, route_code: code, resource_id }),
          scope,
        ),
      ).toEqual({
        code,
        path,
        parameter,
        href: `${path}?${parameter}=${encodeURIComponent(resource_id)}`,
      });
    }
  });

  it("projects StageFact counters through summary, preview, detail, and list DTOs", () => {
    const rawStages = [
      stage("source", 1),
      stage("parse", 2),
      stage("chunk", 3),
      stage("index", 4),
      stage("serve", 5),
    ];

    expect(projectServingSummary(summary(), scope).stage_facts[0]).toEqual(
      expect.objectContaining({ ready_count: 8, warning_count: 1, pending_count: 1 }),
    );
    expect(
      projectServingPreview(
        { preview: true, state: "ready", policy_revision: 4, stage_facts: rawStages, blockers: [] },
        scope,
      ).stage_facts[0],
    ).toEqual(expect.objectContaining({ ready_count: 8, warning_count: 1, pending_count: 1 }));
    expect(
      projectServingSnapshotDetail(
        { snapshot: snapshot(), stage_facts: rawStages, evidence_links: [], events: [] },
        scope,
      ).stage_facts[0],
    ).toEqual(expect.objectContaining({ ready_count: 8, warning_count: 1, pending_count: 1 }));
    expect(
      projectServingPage(
        { items: [rawStages[0]], count: 1, next_cursor: null, invalid_item_count: 0 },
        scope,
        projectServingStageFact,
      ).items[0],
    ).toEqual(expect.objectContaining({ ready_count: 8, warning_count: 1, pending_count: 1 }));
  });

  it("rejects StageFact counters that are negative or exceed item_count", () => {
    for (const field of ["ready_count", "warning_count", "pending_count"] as const) {
      expect(() =>
        projectServingStageFact(stage("source", 1, "ready", { [field]: -1 }), scope),
      ).toThrow(field);
      expect(() =>
        projectServingStageFact(stage("source", 1, "ready", { [field]: 11 }), scope),
      ).toThrow(/item_count/);
    }
  });

  it("accepts a configured profile with a nullable workspace", () => {
    expect(projectServingProfile(profile({ workspace_id: null }), scope).workspace_id).toBeNull();
  });

  it("accepts snapshot_missing when a configured profile has no stage facts", () => {
    expect(
      projectServingSummary(
        summary({
          profile_id: "profile-a",
          profile_name: "产品知识库服务",
          profile_status: "active",
          state: "unavailable",
          as_of: null,
          snapshot_id: null,
          snapshot_digest: null,
          policy_revision: null,
          serving_generation: null,
          source_count: 0,
          ready_source_count: 0,
          stale_source_count: 0,
          active_document_count: 0,
          failed_document_count: 0,
          pending_index_count: 0,
          stage_count: 0,
          ready_stage_count: 0,
          blocked_stage_count: 0,
          current_release_id: null,
          current_certification_id: null,
          reason_code: "snapshot_missing",
          stage_facts: [],
        }),
        scope,
      ),
    ).toEqual(
      expect.objectContaining({
        profile_id: "profile-a",
        state: "unavailable",
        reason_code: "snapshot_missing",
        stage_facts: [],
      }),
    );
  });

  it("projects the canonical profile and preserves null authority pointers", () => {
    expect(projectServingProfile(profile(), scope)).toEqual(
      expect.objectContaining({
        id: "profile-a",
        tenant_id: "tenant-a",
        dataset_id: "dataset-a",
        current_policy_revision_id: "policy-a",
        current_snapshot_id: "snapshot-a",
        archived_at: null,
        archived_by: null,
      }),
    );
  });

  it("rejects cross-tenant and unknown profile fields instead of widening the DTO", () => {
    expect(() => projectServingProfile(profile({ tenant_id: "tenant-b" }), scope)).toThrow(
      "tenant scope mismatch",
    );
    expect(() => projectServingProfile(profile({ debug_payload: "must-not-pass" }), scope)).toThrow(
      "unexpected field",
    );
  });

  it("derives the five-stage overall state from canonical stage facts", () => {
    const stages = [
      stage("source", 1),
      stage("parse", 2),
      stage("chunk", 3),
      stage("index", 4),
      stage("serve", 5),
    ];
    expect(deriveServingOverallState(stages as never)).toBe("ready");
    expect(
      deriveServingOverallState(
        stages.map((item) =>
          item.stage_code === "index" ? { ...item, state: "lagging" } : item,
        ) as never,
      ),
    ).toBe("degraded");
    expect(
      deriveServingOverallState(
        stages.map((item) =>
          item.stage_code === "parse" ? { ...item, state: "blocked" } : item,
        ) as never,
      ),
    ).toBe("blocked");
    expect(
      deriveServingOverallState(
        stages.map((item) =>
          item.stage_code === "serve" ? { ...item, state: "unavailable" } : item,
        ) as never,
      ),
    ).toBe("unavailable");
    expect(
      deriveServingOverallState(
        stages.map((item) =>
          item.stage_code === "serve" || item.stage_code === "parse"
            ? { ...item, state: item.stage_code === "serve" ? "unavailable" : "blocked" }
            : item,
        ) as never,
      ),
    ).toBe("unavailable");
  });

  it("projects a summary only when the server state agrees with the five facts", () => {
    expect(projectServingSummary(summary(), scope).state).toBe("ready");
    expect(() =>
      projectServingSummary(
        summary({
          state: "ready",
          stage_facts: [
            stage("source", 1),
            stage("parse", 2),
            stage("chunk", 3),
            stage("index", 4),
            stage("serve", 5, "blocked"),
          ],
        }),
        scope,
      ),
    ).toThrow("derived serving state");
  });

  it("requires exact stage order and bounded stage facts", () => {
    expect(projectServingStageFact(stage("source", 1), scope).stage_code).toBe("source");
    expect(() => projectServingStageFact(stage("custom", 1), scope)).toThrow("stage_code");
    expect(() => projectServingStageFact(stage("source", 2), scope)).toThrow("stage sequence");
    expect(() =>
      projectServingStageFact(
        stage("source", 1, "ready", { safe_error: "password=secret" }),
        scope,
      ),
    ).toThrow("unsafe");
  });

  it("rejects empty safe mapping strings and aggregates UTF-8 bytes across arrays", () => {
    expect(() => projectServingEvent(servingEvent({ detail: "" }), scope)).toThrow(/unsafe/);
    const value = "知".repeat(3_000);
    expect(() => projectServingEvent(servingEvent({ values: [value, value] }), scope)).toThrow(
      /exceeds safe byte limit/,
    );
  });

  it("rejects resource identifiers outside the database and URI safety contract", () => {
    expect(() =>
      projectServingEvidenceLink(evidenceLink({ resource_id: "r".repeat(129) }), scope),
    ).toThrow(/resource identifier/);
    expect(() =>
      projectServingEvidenceLink(
        evidenceLink({ resource_id: "gopher://attacker.example/resource" }),
        scope,
      ),
    ).toThrow(/resource identifier/);
  });

  it("never exposes unsafe display text from evidence or mutation messages", () => {
    expect(safeServingDisplayText("normal operator note")).toBe("normal operator note");
    expect(safeServingDisplayText("SELECT * FROM documents")).toBeNull();
    expect(safeServingDisplayText("Bearer abc.def.ghi")).toBeNull();
    expect(safeServingDisplayText("https://external.example/hook")).toBeNull();
  });

  it("validates event pages per stream and allows a page to start mid-chain", () => {
    const page = projectServingEventPage(
      {
        items: [
          {
            id: "event-s1-2",
            tenant_id: "tenant-a",
            profile_id: "profile-a",
            snapshot_id: "snapshot-a",
            stream_key: "profile-a",
            sequence: 2,
            event_type: "snapshot_recorded",
            previous_event_digest: digest,
            event_digest: digestB,
            actor_id: "account-a",
            request_id: "request-a",
            safe_snapshot: { state: "ready" },
            occurred_at: now,
          },
          {
            id: "event-s2-1",
            tenant_id: "tenant-a",
            profile_id: "profile-a",
            snapshot_id: "snapshot-a",
            stream_key: "snapshot-a",
            sequence: 1,
            event_type: "stage_degraded",
            previous_event_digest: null,
            event_digest: "c".repeat(64),
            actor_id: "account-a",
            request_id: "request-b",
            safe_snapshot: { stage_code: "index" },
            occurred_at: now,
          },
        ],
        count: 2,
        next_cursor: null,
        invalid_item_count: 0,
      },
      scope,
    );
    expect(page.items).toHaveLength(2);
    expect(page.invalid_item_count).toBe(0);
  });

  it("rejects an unsafe event snapshot and an invalid adjacent predecessor", () => {
    expect(() =>
      projectServingEventPage(
        {
          items: [
            {
              id: "event-a",
              tenant_id: "tenant-a",
              profile_id: "profile-a",
              snapshot_id: "snapshot-a",
              stream_key: "profile-a",
              sequence: 2,
              event_type: "snapshot_recorded",
              previous_event_digest: null,
              event_digest: digest,
              actor_id: "account-a",
              request_id: "request-a",
              safe_snapshot: { apiKey: "raw-secret" },
              occurred_at: now,
            },
          ],
          count: 1,
          next_cursor: null,
          invalid_item_count: 0,
        },
        scope,
      ),
    ).toThrow("unsafe");
    expect(() =>
      projectServingEventPage(
        {
          items: [
            {
              id: "event-1",
              tenant_id: "tenant-a",
              profile_id: "profile-a",
              snapshot_id: "snapshot-a",
              stream_key: "profile-a",
              sequence: 1,
              event_type: "profile_created",
              previous_event_digest: null,
              event_digest: digest,
              actor_id: "account-a",
              request_id: "request-a",
              safe_snapshot: {},
              occurred_at: now,
            },
            {
              id: "event-2",
              tenant_id: "tenant-a",
              profile_id: "profile-a",
              snapshot_id: "snapshot-a",
              stream_key: "profile-a",
              sequence: 2,
              event_type: "snapshot_recorded",
              previous_event_digest: digestB,
              event_digest: "c".repeat(64),
              actor_id: "account-a",
              request_id: "request-b",
              safe_snapshot: {},
              occurred_at: now,
            },
          ],
          count: 2,
          next_cursor: null,
          invalid_item_count: 0,
        },
        scope,
      ),
    ).toThrow("event chain");
  });
});
