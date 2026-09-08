// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DatasetProfile, DocumentVersion, QAKnowledge } from "../governance/model/governanceModel";

let online: boolean | null = true;
let actorToken = "signed-token";
const workspace = { tenantId: "tenant-a", datasetId: "dataset-a", scope: { tenantId: "tenant-a", datasetId: "dataset-a" } };

vi.mock("../context/ConnectionContext", () => ({ useConnection: () => ({ online }) }));
vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({ useKnowledgeWorkspace: () => workspace }));
vi.mock("../knowledge/workspaceScope", () => ({ readKnowledgeActorToken: () => actorToken }));

const profile: DatasetProfile = {
  id: "dataset-a", tenant_id: "tenant-a", name: "Support", description: "Trusted",
  status: "active", profile_revision: 2, owner_id: "owner-a", visibility: "tenant", profile: {},
  policies: { parser: {}, chunk: {}, retrieval: {}, retention: {}, metadata: {} },
  default_language: "zh-CN", graph_enabled: true, qa_enabled: true, usage: { documents: 1, chunks: 4 },
  timestamps: { created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-25T00:00:00Z", archived_at: null, archived_by: null },
};
const qa: QAKnowledge = {
  id: "qa-1", tenant_id: "tenant-a", dataset_id: "dataset-a", revision: 1, question: "Q", answer: "A",
  origin: "manual", review_status: "pending", lifecycle_state: "active", retrieval_enabled: false,
  effective_from: null, expires_at: null, source_document_id: null, source_uri: "", metadata: {}, created_by: "editor-a",
  reviewed_by: null, reviewed_at: null, created_at: "2026-08-25T00:00:00Z", updated_at: "2026-08-25T00:00:00Z", alternatives: [],
};
const version: DocumentVersion = {
  id: "v1", tenant_id: "tenant-a", dataset_id: "dataset-a", document_id: "doc-a", revision: 1,
  source_identity: "upload:doc-a.pdf", source_hash: "a".repeat(64), parser_policy_snapshot: {}, parser_metadata: {},
  source_content_ref: "", created_by: "editor-a", change_reason: "initial", created_at: "2026-08-25T00:00:00Z",
};

const datasetHook = {
  status: "ready" as const, profile, error: null, mutating: false,
  refresh: vi.fn(async () => true), update: vi.fn(async () => true), archive: vi.fn(async () => true), restore: vi.fn(async () => true), disable: vi.fn(async () => true),
};
const qaHook = {
  status: "ready" as const, items: [qa], pageItems: [qa], page: 1, pageCount: 1, pageSize: 10, filters: {}, error: null, mutatingId: null,
  setPage: vi.fn(), setFilters: vi.fn(), refresh: vi.fn(async () => true), create: vi.fn(async () => true), update: vi.fn(async () => true), review: vi.fn(async () => true), expire: vi.fn(async () => true), restore: vi.fn(async () => true), addAlternative: vi.fn(async () => true), deleteAlternative: vi.fn(async () => true),
};
const versionHook = {
  status: "ready" as const, documentId: "doc-a", versions: [version], error: null, creating: false, readOnly: false, truncated: false,
  inspect: vi.fn(async () => true), refresh: vi.fn(async () => true), create: vi.fn(async () => true),
};

vi.mock("../governance/hooks/useDatasetGovernance", () => ({ useDatasetGovernance: () => datasetHook }));
vi.mock("../governance/hooks/useQAGovernance", () => ({ useQAGovernance: () => qaHook }));
vi.mock("../governance/hooks/useDocumentVersions", () => ({ useDocumentVersions: () => versionHook }));

import KnowledgeGovernancePage from "./KnowledgeGovernancePage";

beforeEach(() => { online = true; actorToken = "signed-token"; });
afterEach(cleanup);

describe("KnowledgeGovernancePage", () => {
  it("composes the authority, dataset, QA, and document-version governance surfaces", () => {
    render(<KnowledgeGovernancePage />);
    expect(screen.getByRole("heading", { name: "知识治理" })).toBeTruthy();
    expect(screen.getByText("Catalog 权威治理范围")).toBeTruthy();
    expect(screen.getByRole("region", { name: "数据集资料与策略" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "QA 生命周期" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "文档版本检查器" })).toBeTruthy();
  });

  it("fails closed when the authenticated actor scope is missing", () => {
    actorToken = "";
    render(<KnowledgeGovernancePage />);
    expect(screen.getByText("缺少知识库授权范围")).toBeTruthy();
    expect(screen.queryByText("Support")).toBeNull();
  });

  it("shows truthful offline state without demo facts", () => {
    online = false;
    render(<KnowledgeGovernancePage />);
    expect(screen.getByText("知识治理服务未连接")).toBeTruthy();
    expect(document.body.textContent).not.toContain("演示数据");
  });

  it("hides all dependent governance facts when the dataset is disabled", () => {
    const previous=datasetHook.profile; datasetHook.profile={...profile,status:"disabled"};
    render(<KnowledgeGovernancePage />); expect(screen.getByText("数据集已停用")).toBeTruthy(); expect(screen.queryByRole("region",{name:"QA 生命周期"})).toBeNull(); expect(screen.queryByRole("region",{name:"文档版本检查器"})).toBeNull();
    datasetHook.profile=previous;
  });


  it("passes archived read-only and truncation facts to the version inspector", () => {
    const oldProfile=datasetHook.profile, oldReadOnly=versionHook.readOnly, oldTruncated=versionHook.truncated;
    datasetHook.profile={...profile,status:"archived"}; versionHook.readOnly=true; versionHook.truncated=true;
    render(<KnowledgeGovernancePage />); expect(screen.queryByRole("button",{name:"创建文档版本"})).toBeNull(); expect(screen.getByText("结果可能被截断")).toBeTruthy();
    datasetHook.profile=oldProfile; versionHook.readOnly=oldReadOnly; versionHook.truncated=oldTruncated;
  });


  it("suppresses its standalone PageTopbar in embedded content-only mode", () => {
    render(<KnowledgeGovernancePage embedded />);
    expect(screen.queryByRole("heading", { name: "知识治理" })).toBeNull();
  });

});
