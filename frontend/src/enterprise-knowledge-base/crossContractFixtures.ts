/**
 * Sanitized payloads copied from the Stage 18 Registry service boundary.
 *
 * These fixtures deliberately use the backend's wire names instead of the
 * frontend projection names so model/API tests exercise the real contract.
 */
export const CROSS_CONTRACT_CATALOG = {
  revision: "0028_enterprise_knowledge_base_registry",
  capability_state: "ready",
};

export const CROSS_CONTRACT_OWNING_WORKSPACE = {
  id: "workspace-prod",
  name: "生产知识域",
  status: "active",
  revision: 7,
  ownership_revision: 7,
};

export const CROSS_CONTRACT_ARCHIVE_READINESS = {
  ready: false,
  blocker_count: 3,
  blockers: [
    {
      code: "active_application_references",
      count: 3,
      reference_ids: ["app-ref-001", "app-ref-002", "app-ref-003"],
    },
  ],
};

export const CROSS_CONTRACT_KNOWLEDGE_BASE = {
  id: "dataset-prod",
  tenant_id: "tenant-a",
  name: "客服知识库",
  description: "面向客服的正式知识内容",
  status: "active",
  visibility: "tenant",
  profile_revision: 12,
  owning_workspace: CROSS_CONTRACT_OWNING_WORKSPACE,
  workspace: CROSS_CONTRACT_OWNING_WORKSPACE,
  ownership_revision: 7,
  active_shared_association_count: 2,
  active_application_reference_count: 3,
  document_count: 128,
  chunk_count: 420,
  source_count: 4,
  updated_at: "2026-08-27T08:00:00Z",
  archive_readiness: CROSS_CONTRACT_ARCHIVE_READINESS,
  catalog_revision: CROSS_CONTRACT_CATALOG.revision,
  catalog_capability_state: CROSS_CONTRACT_CATALOG.capability_state,
  catalog: CROSS_CONTRACT_CATALOG,
};

export const CROSS_CONTRACT_SHARED_ASSOCIATION = {
  id: 17,
  tenant_id: "tenant-a",
  workspace_id: "workspace-test",
  workspace_name: "测试知识域",
  workspace_status: "active",
  dataset_id: "dataset-prod",
  binding_kind: "shared",
  active_primary_slot: null,
  status: "active",
  revision: 2,
};

export const CROSS_CONTRACT_APPLICATION_REFERENCE = {
  id: "app-ref-001",
  tenant_id: "tenant-a",
  app_id: "app-support",
  app_name: "客服助手",
  app_kind: "assistant",
  dataset_id: "dataset-prod",
  reference_kind: "knowledge",
  status: "active",
  active_slot: "active",
  revision: 4,
  created_at: "2026-08-27T07:00:00Z",
  created_by: "account-owner",
  updated_at: "2026-08-27T07:00:00Z",
  updated_by: "account-owner",
  removed_at: null,
  removed_by: null,
  request_id: "request-reference-001",
};

export const CROSS_CONTRACT_LIST_RESPONSE = {
  items: [CROSS_CONTRACT_KNOWLEDGE_BASE],
  count: 1,
  next_cursor: null,
  catalog_revision: CROSS_CONTRACT_CATALOG.revision,
};

export const CROSS_CONTRACT_DETAIL_RESPONSE = {
  ...CROSS_CONTRACT_KNOWLEDGE_BASE,
  knowledge_base: CROSS_CONTRACT_KNOWLEDGE_BASE,
  workspace_associations: [CROSS_CONTRACT_SHARED_ASSOCIATION],
  application_references: [CROSS_CONTRACT_APPLICATION_REFERENCE],
  dependencies: {
    owning_workspace: CROSS_CONTRACT_OWNING_WORKSPACE,
    associations: [CROSS_CONTRACT_SHARED_ASSOCIATION],
    application_references: [CROSS_CONTRACT_APPLICATION_REFERENCE],
    archive_readiness: CROSS_CONTRACT_ARCHIVE_READINESS,
  },
  catalog: CROSS_CONTRACT_CATALOG,
};

export const CROSS_CONTRACT_DEPENDENCIES_RESPONSE = {
  dataset: CROSS_CONTRACT_KNOWLEDGE_BASE,
  knowledge_base: CROSS_CONTRACT_KNOWLEDGE_BASE,
  owning_workspace: CROSS_CONTRACT_OWNING_WORKSPACE,
  associations: [CROSS_CONTRACT_SHARED_ASSOCIATION],
  application_references: [CROSS_CONTRACT_APPLICATION_REFERENCE],
  archive_readiness: CROSS_CONTRACT_ARCHIVE_READINESS,
  catalog: CROSS_CONTRACT_CATALOG,
};

export const CROSS_CONTRACT_CREATED_MUTATION_RESPONSE = {
  reference: CROSS_CONTRACT_APPLICATION_REFERENCE,
  mutation: { result: "created", resource_id: "app-ref-001" },
};

export const CROSS_CONTRACT_REMOVED_MUTATION_RESPONSE = {
  reference: {
    ...CROSS_CONTRACT_APPLICATION_REFERENCE,
    status: "removed",
    active_slot: null,
    revision: 5,
  },
  mutation: { result: "removed", resource_id: "app-ref-001" },
};

export const CROSS_CONTRACT_TRANSFERRED_MUTATION_RESPONSE = {
  dataset: {
    id: "dataset-prod",
    tenant_id: "tenant-a",
    profile_revision: 13,
  },
  ownership: {
    id: "ownership-dataset-prod",
    tenant_id: "tenant-a",
    dataset_id: "dataset-prod",
    workspace_id: "workspace-test",
    revision: 8,
  },
  bindings: [CROSS_CONTRACT_SHARED_ASSOCIATION],
  mutation: { result: "transferred", resource_id: "dataset-prod" },
};
