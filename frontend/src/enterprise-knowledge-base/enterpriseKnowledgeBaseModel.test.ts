import { describe, expect, it } from "vitest";
import {
  CROSS_CONTRACT_DEPENDENCIES_RESPONSE,
  CROSS_CONTRACT_DETAIL_RESPONSE,
  CROSS_CONTRACT_LIST_RESPONSE,
  CROSS_CONTRACT_TRANSFERRED_MUTATION_RESPONSE,
} from "./crossContractFixtures";
import {
  projectKnowledgeBase,
  projectKnowledgeBaseDependencies,
  projectKnowledgeBaseDetail,
  projectKnowledgeBasePage,
  projectKnowledgeBaseMutationResponse,
} from "./enterpriseKnowledgeBaseModel";

const dataset = {
  id: "dataset-prod",
  name: "客服知识库",
  description: "面向客服的正式知识内容",
  status: "active",
  visibility: "tenant",
  profile_revision: 12,
  owning_workspace: {
    id: "workspace-prod",
    name: "生产知识域",
    status: "active",
    revision: 7,
  },
  shared_association_count: 2,
  active_application_reference_count: 3,
  document_count: 128,
  chunk_count: 420,
  source_count: 4,
  updated_at: "2026-08-27T08:00:00Z",
  archive_ready: false,
  archive_blocker_count: 3,
  catalog_revision: "0028_enterprise_knowledge_base_registry",
  capability_state: "ready",
};

describe("Stage18 Knowledge Base strict projections", () => {
  it("keeps server-owned counts and nullable authority evidence without inventing zeros", () => {
    const projected = projectKnowledgeBase({
      ...dataset,
      document_count: undefined,
      source_count: undefined,
      ownership_revision: undefined,
      owning_workspace: { ...dataset.owning_workspace, revision: undefined },
    });

    expect(projected?.id).toBe("dataset-prod");
    expect(projected?.document_count).toBeNull();
    expect(projected?.source_count).toBeNull();
    expect(projected?.owning_workspace?.revision).toBeNull();
    expect(projected?.active_application_reference_count).toBe(3);
  });

  it("never substitutes ownership revision for Workspace revision authority", () => {
    const flat = projectKnowledgeBase({
      ...dataset,
      owning_workspace: undefined,
      workspace_id: "workspace-prod",
      workspace_name: "生产知识域",
      workspace_status: "active",
      workspace_revision: undefined,
      ownership_revision: 9,
    });
    expect(flat?.owning_workspace?.revision).toBeNull();
    expect(flat?.ownership_revision).toBe(9);

    const missingOwnershipRevision = projectKnowledgeBase({
      ...dataset,
      ownership_revision: undefined,
    });
    expect(missingOwnershipRevision?.owning_workspace?.revision).toBe(7);
    expect(missingOwnershipRevision?.ownership_revision).toBeNull();
  });

  it("rejects malformed registry rows instead of projecting a fake resource", () => {
    expect(projectKnowledgeBase({ ...dataset, id: "" })).toBeNull();
    expect(projectKnowledgeBase({ ...dataset, name: undefined })).toBeNull();
    expect(projectKnowledgeBase({ ...dataset, profile_revision: 0 })).toBeNull();
  });

  it("projects list pagination and preserves an omitted count as null", () => {
    const page = projectKnowledgeBasePage({
      items: [dataset, { ...dataset, id: "" }],
      next_cursor: "opaque-next",
      evidence: {
        knowledge_base_count: undefined,
        active_count: 1,
        owned_count: 1,
        active_application_reference_count: 3,
        archive_ready_count: 0,
        catalog_revision: "0028_enterprise_knowledge_base_registry",
        capability_state: "ready",
      },
    });

    expect(page.items).toHaveLength(1);
    expect(page.count).toBeNull();
    expect(page.next_cursor).toBe("opaque-next");
    expect(page.evidence.archive_ready_count).toBe(0);
    expect(page.evidence.knowledge_base_count).toBeNull();
  });

  it("keeps detail dependency facts separate from ownership and permissions", () => {
    const detail = projectKnowledgeBaseDetail({
      knowledge_base: dataset,
      application_references: {
        items: [
          {
            id: "ref-1",
            app_id: "app-support",
            app_name: "客服助手",
            dataset_id: "dataset-prod",
            reference_kind: "knowledge",
            status: "active",
            revision: 4,
          },
        ],
        count: 1,
      },
      dependencies: {
        owning_workspace: dataset.owning_workspace,
        shared_associations: [
          {
            id: "assoc-1",
            workspace_id: "workspace-test",
            workspace_name: "测试知识域",
            binding_kind: "shared",
            status: "active",
            revision: 2,
          },
        ],
        application_references: [
          {
            id: "ref-1",
            app_id: "app-support",
            app_name: "客服助手",
            status: "active",
            revision: 4,
          },
        ],
        archive: {
          ready: false,
          blocker_count: 1,
          blockers: [{ code: "active_application_references", label: "存在活跃 Application 引用" }],
        },
      },
    });

    expect(detail.knowledge_base.id).toBe("dataset-prod");
    expect(detail.application_references.items[0]?.app_id).toBe("app-support");
    expect(detail.dependencies.owning_workspace?.id).toBe("workspace-prod");
    expect(detail.dependencies.shared_associations[0]?.binding_kind).toBe("shared");
    expect(detail.dependencies.archive.ready).toBe(false);
    expect(detail.dependencies.archive.blockers[0]?.code).toBe("active_application_references");
  });

  it("projects the real Registry list, detail and dependency wire shapes", () => {
    const page = projectKnowledgeBasePage(CROSS_CONTRACT_LIST_RESPONSE);
    const item = page.items[0];
    expect(item?.shared_association_count).toBe(2);
    expect(item?.archive_ready).toBe(false);
    expect(item?.archive_blocker_count).toBe(3);
    expect(item?.capability_state).toBe("ready");
    expect(page.evidence).toEqual(
      expect.objectContaining({
        knowledge_base_count: 1,
        catalog_revision: "0028_enterprise_knowledge_base_registry",
        capability_state: "unknown",
      }),
    );

    const detail = projectKnowledgeBaseDetail(CROSS_CONTRACT_DETAIL_RESPONSE);
    expect(detail.application_references.items).toHaveLength(1);
    expect(detail.dependencies.state).toBe("ready");
    expect(detail.dependencies.shared_associations[0]?.workspace_id).toBe("workspace-test");
    expect(detail.dependencies.archive.blockers[0]).toEqual(
      expect.objectContaining({
        code: "active_application_references",
        count: 3,
        reference_ids: ["app-ref-001", "app-ref-002", "app-ref-003"],
      }),
    );

    const dependencies = projectKnowledgeBaseDependencies(CROSS_CONTRACT_DEPENDENCIES_RESPONSE);
    expect(dependencies.state).toBe("ready");
    expect(dependencies.archive.ready).toBe(false);
  });

  it("projects nested backend mutation results as applied outcomes", () => {
    const outcome = projectKnowledgeBaseMutationResponse(
      CROSS_CONTRACT_TRANSFERRED_MUTATION_RESPONSE,
    );
    expect(outcome.state).toBe("applied");
    expect(outcome.result).toBe("transferred");
    expect(outcome.resource_id).toBe("dataset-prod");
  });

  it("projects an unavailable dependency response without treating it as an empty dependency set", () => {
    const projected = projectKnowledgeBaseDependencies({
      state: "unavailable",
      reason: "0028 registry migration required",
    });

    expect(projected.state).toBe("unavailable");
    expect(projected.reason).toBe("0028 registry migration required");
    expect(projected.archive.ready).toBeNull();
    expect(projected.archive.blocker_count).toBeNull();
    expect(projected.application_references).toEqual([]);
  });
});
