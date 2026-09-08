export type KnowledgeBaseStatus = string & {};
export type KnowledgeBaseVisibility = string & {};
export type KnowledgeBaseCapabilityState = "ready" | "limited" | "unavailable" | "unknown";
export type KnowledgeBaseDependencyState = "ready" | "unavailable" | "unknown";
export type KnowledgeBaseMutationState = "applied" | "approval_required" | "unknown";

export interface KnowledgeBaseWorkspace {
  id: string;
  name: string;
  status: string;
  revision: number | null;
}

export interface EnterpriseKnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  status: KnowledgeBaseStatus;
  visibility: KnowledgeBaseVisibility | null;
  profile_revision: number;
  owning_workspace: KnowledgeBaseWorkspace | null;
  /** Compatibility alias for callers that describe the owner as a Workspace. */
  workspace: KnowledgeBaseWorkspace | null;
  ownership_revision: number | null;
  shared_association_count: number | null;
  active_application_reference_count: number | null;
  document_count: number | null;
  chunk_count: number | null;
  source_count: number | null;
  updated_at: string | null;
  archive_ready: boolean | null;
  archive_blocker_count: number | null;
  catalog_revision: string | null;
  capability_state: KnowledgeBaseCapabilityState;
}

export interface KnowledgeBaseEvidence {
  knowledge_base_count: number | null;
  active_count: number | null;
  owned_count: number | null;
  active_application_reference_count: number | null;
  archive_ready_count: number | null;
  catalog_revision: string | null;
  capability_state: KnowledgeBaseCapabilityState;
}

export interface KnowledgeBasePage {
  items: EnterpriseKnowledgeBase[];
  count: number | null;
  next_cursor: string | null;
  evidence: KnowledgeBaseEvidence;
}

export interface KnowledgeBaseApplicationReference {
  id: string;
  app_id: string;
  app_name: string | null;
  dataset_id: string | null;
  reference_kind: string;
  status: string;
  revision: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface KnowledgeBaseApplicationReferencePage {
  items: KnowledgeBaseApplicationReference[];
  count: number | null;
  next_cursor: string | null;
}

export interface KnowledgeBaseSharedAssociation {
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  binding_kind: string;
  status: string;
  revision: number | null;
}

export interface KnowledgeBaseArchiveBlocker {
  code: string;
  label: string;
  count: number | null;
  status: string | null;
  reference_ids?: string[] | null;
}

export interface KnowledgeBaseArchiveReadiness {
  ready: boolean | null;
  blocker_count: number | null;
  blockers: KnowledgeBaseArchiveBlocker[];
}

export interface KnowledgeBaseDependencies {
  state: KnowledgeBaseDependencyState;
  reason: string | null;
  owning_workspace: KnowledgeBaseWorkspace | null;
  shared_associations: KnowledgeBaseSharedAssociation[];
  application_references: KnowledgeBaseApplicationReference[];
  archive: KnowledgeBaseArchiveReadiness;
}

export interface KnowledgeBaseDetail {
  knowledge_base: EnterpriseKnowledgeBase;
  application_references: KnowledgeBaseApplicationReferencePage;
  dependencies: KnowledgeBaseDependencies;
}

export type EnterpriseKnowledgeBaseDetail = KnowledgeBaseDetail;

export interface KnowledgeBaseMutationResponse {
  state: KnowledgeBaseMutationState;
  result?: string | null;
  resource_id?: string | null;
  knowledge_base: EnterpriseKnowledgeBase | null;
  approval_request_id: string | null;
  message: string | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function identifier(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return text(value);
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

function booleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function nullableDate(value: unknown): string | null {
  return text(value);
}

function capabilityState(value: unknown): KnowledgeBaseCapabilityState {
  const source = record(value);
  const state = source?.state ?? source?.capability_state ?? value;
  return state === "ready" || state === "limited" || state === "unavailable" || state === "unknown"
    ? state
    : "unknown";
}

function dependencyState(value: unknown): KnowledgeBaseDependencyState {
  const state = capabilityState(value);
  if (state === "ready") return "ready";
  if (state === "limited" || state === "unavailable") return "unavailable";
  return value === "ready" ? "ready" : "unknown";
}

function mutationState(value: unknown): KnowledgeBaseMutationState {
  const source = record(value);
  const state = source?.state ?? source?.result ?? value;
  if (
    state === "applied" ||
    state === "created" ||
    state === "removed" ||
    state === "transferred" ||
    state === "updated" ||
    state === "success"
  ) {
    return "applied";
  }
  return state === "approval_required" ? "approval_required" : "unknown";
}

function listSource(value: Record<string, unknown> | null, ...keys: string[]): unknown[] {
  for (const key of keys) {
    if (Array.isArray(value?.[key])) return value[key] as unknown[];
  }
  return [];
}

function pageCursor(value: Record<string, unknown> | null): string | null {
  return text(value?.next_cursor ?? value?.next_before_id);
}

function pageCount(value: Record<string, unknown> | null): number | null {
  return nonNegativeInteger(value?.count ?? value?.total_count);
}

function projectWorkspace(value: unknown): KnowledgeBaseWorkspace | null {
  const source = record(value);
  if (!source) return null;
  const id = identifier(source.id ?? source.workspace_id);
  const name = text(source.name ?? source.workspace_name);
  const status = text(source.status ?? source.workspace_status);
  if (!id || !name || !status) return null;
  return {
    id,
    name,
    status,
    revision: positiveInteger(source.revision ?? source.workspace_revision),
  };
}

export function projectKnowledgeBase(value: unknown): EnterpriseKnowledgeBase | null {
  const source = record(value);
  if (!source) return null;
  const id = identifier(source.id ?? source.dataset_id);
  const name = text(source.name ?? source.dataset_name);
  const status = text(source.status);
  const profileRevision = positiveInteger(source.profile_revision ?? source.revision);
  if (!id || !name || !status || profileRevision === null) return null;

  const workspaceSource = source.owning_workspace ?? source.workspace;
  const ownershipSource = record(source.workspace_ownership ?? source.ownership);
  const workspaceRecord = record(workspaceSource);
  const owner = projectWorkspace(
    workspaceSource ?? {
      id: source.workspace_id,
      name: source.workspace_name,
      status: source.workspace_status,
      revision: source.workspace_revision,
    },
  );
  const ownershipRevision = positiveInteger(
    source.ownership_revision ??
      workspaceRecord?.ownership_revision ??
      ownershipSource?.revision ??
      ownershipSource?.ownership_revision,
  );
  const archiveSource = source.archive_readiness ?? source.archive;
  const archive = record(archiveSource);
  const catalog = record(source.catalog);

  return {
    id,
    name,
    description: text(source.description),
    status,
    visibility: text(source.visibility),
    profile_revision: profileRevision,
    owning_workspace: owner,
    workspace: owner,
    ownership_revision: ownershipRevision,
    shared_association_count: nonNegativeInteger(
      source.shared_association_count ?? source.active_shared_association_count,
    ),
    active_application_reference_count: nonNegativeInteger(
      source.active_application_reference_count ??
        source.active_reference_count ??
        source.application_reference_count,
    ),
    document_count: nonNegativeInteger(source.document_count),
    chunk_count: nonNegativeInteger(source.chunk_count),
    source_count: nonNegativeInteger(source.source_count),
    updated_at: nullableDate(source.updated_at),
    archive_ready: booleanOrNull(source.archive_ready ?? archive?.ready),
    archive_blocker_count: nonNegativeInteger(
      source.archive_blocker_count ?? archive?.blocker_count,
    ),
    catalog_revision: text(
      source.catalog_revision ?? source.registry_revision ?? catalog?.revision,
    ),
    capability_state: capabilityState(
      source.capability_state ??
        source.catalog_capability_state ??
        source.registry_capability_state ??
        catalog,
    ),
  };
}

export function projectKnowledgeBaseEvidence(value: unknown): KnowledgeBaseEvidence {
  const source = record(value);
  const catalog = record(source?.catalog);
  return {
    knowledge_base_count: nonNegativeInteger(
      source?.knowledge_base_count ?? source?.dataset_count ?? source?.count,
    ),
    active_count: nonNegativeInteger(source?.active_count ?? source?.active_dataset_count),
    owned_count: nonNegativeInteger(source?.owned_count ?? source?.owned_dataset_count),
    active_application_reference_count: nonNegativeInteger(
      source?.active_application_reference_count ?? source?.active_reference_count,
    ),
    archive_ready_count: nonNegativeInteger(source?.archive_ready_count),
    catalog_revision: text(
      source?.catalog_revision ?? source?.registry_revision ?? catalog?.revision,
    ),
    capability_state: capabilityState(
      source?.capability_state ?? source?.catalog_capability_state ?? catalog,
    ),
  };
}

export function projectKnowledgeBasePage(value: unknown): KnowledgeBasePage {
  const envelope = record(value) ?? {};
  const source = record(envelope.data) ?? envelope;
  const items = listSource(source, "items", "knowledge_bases", "datasets")
    .map(projectKnowledgeBase)
    .filter((item): item is EnterpriseKnowledgeBase => item !== null);
  return {
    items,
    count: pageCount(source),
    next_cursor: pageCursor(source),
    evidence: projectKnowledgeBaseEvidence(source.evidence ?? source),
  };
}

function projectApplicationReference(value: unknown): KnowledgeBaseApplicationReference | null {
  const source = record(value);
  if (!source) return null;
  const id = identifier(source.id ?? source.reference_id);
  const appId = identifier(source.app_id ?? source.application_id);
  const referenceKind = text(source.reference_kind) ?? "knowledge";
  const status = text(source.status);
  if (!id || !appId || !status) return null;
  return {
    id,
    app_id: appId,
    app_name: text(source.app_name ?? source.application_name),
    dataset_id: identifier(source.dataset_id),
    reference_kind: referenceKind,
    status,
    revision: positiveInteger(source.revision),
    created_at: nullableDate(source.created_at),
    updated_at: nullableDate(source.updated_at),
  };
}

function projectApplicationReferencePage(value: unknown): KnowledgeBaseApplicationReferencePage {
  if (Array.isArray(value)) {
    const items = value
      .map(projectApplicationReference)
      .filter((item): item is KnowledgeBaseApplicationReference => item !== null);
    return { items, count: items.length, next_cursor: null };
  }
  const source = record(value) ?? {};
  const items = listSource(source, "items", "application_references", "references")
    .map(projectApplicationReference)
    .filter((item): item is KnowledgeBaseApplicationReference => item !== null);
  return { items, count: pageCount(source), next_cursor: pageCursor(source) };
}

function projectSharedAssociation(value: unknown): KnowledgeBaseSharedAssociation | null {
  const source = record(value);
  if (!source) return null;
  const id = identifier(source.id ?? source.binding_id);
  const workspaceId = identifier(source.workspace_id);
  const bindingKind = text(source.binding_kind);
  const status = text(source.status);
  if (!id || !workspaceId || !bindingKind || !status) return null;
  return {
    id,
    workspace_id: workspaceId,
    workspace_name: text(source.workspace_name ?? source.name),
    binding_kind: bindingKind,
    status,
    revision: positiveInteger(source.revision),
  };
}

function archiveBlockerLabel(code: string): string {
  const labels: Record<string, string> = {
    active_application_references: "存在活跃 Application 引用",
  };
  return labels[code] ?? code;
}

function projectArchiveBlocker(value: unknown): KnowledgeBaseArchiveBlocker | null {
  const source = record(value);
  if (!source) return null;
  const code = text(source.code ?? source.kind ?? source.blocker_code);
  if (!code) return null;
  const referenceIds = Array.isArray(source.reference_ids)
    ? source.reference_ids.map(identifier).filter((id): id is string => id !== null)
    : null;
  return {
    code,
    label: text(source.label ?? source.message ?? source.reason) ?? archiveBlockerLabel(code),
    count: nonNegativeInteger(source.count),
    status: text(source.status),
    reference_ids: referenceIds,
  };
}

function projectArchiveReadiness(value: unknown): KnowledgeBaseArchiveReadiness {
  const source = record(value);
  const blockers = listSource(source, "blockers", "archive_blockers")
    .map(projectArchiveBlocker)
    .filter((item): item is KnowledgeBaseArchiveBlocker => item !== null);
  return {
    ready: booleanOrNull(source?.ready ?? source?.archive_ready),
    blocker_count: nonNegativeInteger(source?.blocker_count ?? source?.archive_blocker_count),
    blockers,
  };
}

export function projectKnowledgeBaseDependencies(value: unknown): KnowledgeBaseDependencies {
  const envelope = record(value) ?? {};
  const source = record(envelope.dependencies) ?? envelope;
  const dataset = record(source.dataset ?? source.knowledge_base);
  const catalog = record(source.catalog) ?? record(dataset?.catalog);
  const capability =
    source.state ??
    source.capability_state ??
    source.catalog_capability_state ??
    catalog?.capability_state;
  const archiveSource = source.archive ?? source.archive_readiness ?? dataset?.archive_readiness;
  return {
    state: dependencyState(capability),
    reason: text(source.reason ?? catalog?.reason),
    owning_workspace: projectWorkspace(
      source.owning_workspace ?? source.workspace ?? dataset?.owning_workspace,
    ),
    shared_associations: listSource(
      source,
      "shared_associations",
      "associations",
      "workspace_associations",
    )
      .map(projectSharedAssociation)
      .filter((item): item is KnowledgeBaseSharedAssociation => item !== null),
    application_references: listSource(source, "application_references", "references")
      .map(projectApplicationReference)
      .filter((item): item is KnowledgeBaseApplicationReference => item !== null),
    archive: projectArchiveReadiness(archiveSource),
  };
}

const EMPTY_REFERENCE_PAGE: KnowledgeBaseApplicationReferencePage = {
  items: [],
  count: null,
  next_cursor: null,
};

export function projectKnowledgeBaseDetail(value: unknown): KnowledgeBaseDetail {
  const envelope = record(value) ?? {};
  const source = record(envelope.data) ?? envelope;
  const rawKnowledgeBase = source.knowledge_base ?? source.dataset ?? source.item ?? source;
  const knowledgeBase = projectKnowledgeBase(rawKnowledgeBase);
  if (!knowledgeBase) {
    throw new Error("knowledge base detail response is missing registry authority");
  }
  const references = source.application_references ?? source.references;
  return {
    knowledge_base: knowledgeBase,
    application_references: references
      ? projectApplicationReferencePage(references)
      : { ...EMPTY_REFERENCE_PAGE },
    dependencies: projectKnowledgeBaseDependencies(
      source.dependencies
        ? {
            ...(record(source.dependencies) ?? {}),
            catalog: record(source.dependencies)?.catalog ?? source.catalog,
            knowledge_base: record(source.dependencies)?.knowledge_base ?? rawKnowledgeBase,
          }
        : source,
    ),
  };
}

function mutationResult(value: unknown): string | null {
  const source = record(value);
  return text(source?.result ?? source?.state ?? source?.outcome ?? value);
}

export function projectKnowledgeBaseMutationResponse(
  value: unknown,
): KnowledgeBaseMutationResponse {
  const envelope = record(value) ?? {};
  const source = record(envelope.data) ?? envelope;
  const mutation = record(source.mutation);
  const result = mutationResult(
    mutation?.result ?? mutation?.state ?? source.state ?? source.result ?? source.outcome,
  );
  const rawKnowledgeBase = source.knowledge_base ?? source.dataset ?? source.item;
  return {
    state: mutationState(result),
    result,
    resource_id: identifier(mutation?.resource_id ?? source.resource_id),
    knowledge_base: rawKnowledgeBase ? projectKnowledgeBase(rawKnowledgeBase) : null,
    approval_request_id: identifier(
      source.approval_request_id ?? source.request_id ?? mutation?.approval_request_id,
    ),
    message: text(source.message ?? mutation?.message),
  };
}

export function knowledgeBaseCapabilityLabel(state: KnowledgeBaseCapabilityState): string {
  const labels: Record<KnowledgeBaseCapabilityState, string> = {
    ready: "已接入",
    limited: "能力受限",
    unavailable: "尚未接入",
    unknown: "未返回",
  };
  return labels[state];
}
