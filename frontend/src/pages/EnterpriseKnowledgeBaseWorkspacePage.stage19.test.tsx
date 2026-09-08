// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  workspace: {
    workspaceScopeStatus: "verified",
    datasetId: "dataset-a",
    workspaceId: "workspace-a",
    scope: { tenantId: "tenant-a", datasetId: "dataset-a" },
  },
}));

vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({
  useKnowledgeWorkspace: () => state.workspace,
}));
vi.mock("../enterprise-knowledge-base-shell/KnowledgeBaseResourceShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <section aria-label="资源壳">{children}</section>
  ),
}));
vi.mock("../enterprise-release-quality-operations/components/QualityOperationsWorkspace", () => ({
  default: ({
    active,
    scope,
    readOnly,
    scopeVerified,
  }: {
    active: boolean;
    scope: { tenantId: string; datasetId: string; actorToken: string };
    readOnly?: boolean;
    scopeVerified?: boolean;
  }) => (
    <section aria-label="Knowledge Base Release 控制中心">
      <output>{`${active}:${scope.tenantId}:${scope.datasetId}:${scope.actorToken}:${readOnly ? "readonly" : "managed"}:${scopeVerified ? "verified" : "unverified"}`}</output>
    </section>
  ),
}));

import EnterpriseKnowledgeBaseWorkspacePage from "./EnterpriseKnowledgeBaseWorkspacePage";

beforeEach(() => {
  state.workspace.workspaceScopeStatus = "verified";
});

afterEach(cleanup);

describe("Stage19 Workspace Releases integration", () => {
  it("mounts the Release Center only for the releases resource and forwards the verified scope", () => {
    render(
      <EnterpriseKnowledgeBaseWorkspacePage
        active
        section="releases"
        tenantId="tenant-a"
        datasetId="dataset-a"
        actorToken="actor-token"
        readOnly
      />,
    );

    expect(screen.getByRole("region", { name: "Knowledge Base Release 控制中心" })).toBeTruthy();
    expect(screen.getByText("true:tenant-a:dataset-a:actor-token:readonly:verified")).toBeTruthy();
  });

  it("keeps the local Releases tabs out of non-Releases resources", () => {
    render(
      <EnterpriseKnowledgeBaseWorkspacePage
        active
        section="overview"
        tenantId="tenant-a"
        datasetId="dataset-a"
        actorToken="actor-token"
      />,
    );

    expect(screen.queryByRole("region", { name: "Knowledge Base Release 控制中心" })).toBeNull();
  });
  it("does not enable Release reads before the Workspace → Dataset scope is verified", () => {
    state.workspace.workspaceScopeStatus = "loading";
    render(
      <EnterpriseKnowledgeBaseWorkspacePage
        active
        section="releases"
        tenantId="tenant-a"
        datasetId="dataset-a"
        actorToken="actor-token"
      />,
    );

    expect(screen.getByText("true:tenant-a:dataset-a:actor-token:managed:unverified")).toBeTruthy();
  });
});
