// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceRecord } from "../sources-control/model/sourceModels";

let online: boolean | null = true;
let actorToken = "signed-token";
const workspace = { scope: { tenantId: "tenant-a", datasetId: "dataset-a" } };
const source: SourceRecord = { id: "source-a", tenant_id: "tenant-a", dataset_id: "dataset-a", name: "Handbook", kind: "local_dir", config: { path: "C:/docs/handbook" }, metadata: {}, status: "active", generation: 3, last_cursor: {}, last_result: {}, last_error: "", last_sync_at: null, created_at: "2026-08-25T00:00:00Z", updated_at: "2026-08-25T00:00:00Z" };
const sourceHook = { sources: [source], status: "ready", error: null, mutatingId: null, lastAccepted: null, refresh: vi.fn(), create: vi.fn(), update: vi.fn(), setEnabled: vi.fn(), sync: vi.fn(), clearAccepted: vi.fn() };
const runsHook = { runs: [], status: "ready", error: null, runStatus: undefined, trigger: undefined, nextCursor: null, hasPrevious: false, paging: false, selectedRun: null, items: [], itemsNextCursor: null, hasPreviousItems: false, itemsPaging: false, detailLoading: false, itemResult: undefined, itemAction: undefined, retrying: false, lastAccepted: null, refresh: vi.fn(), resetAndRefresh: vi.fn(), nextPage: vi.fn(), previousPage: vi.fn(), setFilters: vi.fn(), selectRun: vi.fn(), closeRun: vi.fn(), nextItemsPage: vi.fn(), previousItemsPage: vi.fn(), setItemFilters: vi.fn(), retry: vi.fn() };
const useRuns = vi.fn((_scope: unknown, _source: unknown, _active: boolean) => runsHook);

vi.mock("../context/ConnectionContext", () => ({ useConnection: () => ({ online }) }));
vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({ useKnowledgeWorkspace: () => workspace }));
vi.mock("../knowledge/workspaceScope", () => ({ readKnowledgeActorToken: () => actorToken }));
vi.mock("../sources-control/hooks/useSourceControl", () => ({ useSourceControl: () => sourceHook, sourceScopeKey: (scope: { tenantId: string; datasetId: string; actorToken: string }) => `${scope.tenantId}:${scope.datasetId}:${scope.actorToken}` }));
vi.mock("../sources-control/hooks/useSourceRuns", () => ({ useSourceRuns: (scope: unknown, source: unknown, active: boolean) => useRuns(scope, source, active) }));

import KnowledgeSourcesPage from "./KnowledgeSourcesPage";

beforeEach(() => { online = true; actorToken = "signed-token"; useRuns.mockClear(); });
afterEach(cleanup);

describe("KnowledgeSourcesPage", () => {
  it("composes authoritative source operations and honest capability explanations", () => {
    render(<KnowledgeSourcesPage active />);
    expect(screen.getByRole("heading", { name: "来源控制面" })).toBeTruthy();
    expect(screen.getByText(/至少一次/)).toBeTruthy();
    expect(screen.getByText(/定时同步尚未由后端实现/)).toBeTruthy();
    expect(screen.getByText("Handbook")).toBeTruthy();
    expect(document.body.textContent).not.toContain("演示数据");
    expect(useRuns).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ id: "source-a" }), true);
  });

  it("fails closed without an actor token", () => {
    actorToken = "";
    render(<KnowledgeSourcesPage active />);
    expect(screen.getByText("需要登录知识工作区")).toBeTruthy();
    expect(screen.queryByText("Handbook")).toBeNull();
  });

  it("shows truthful offline state and passes kept-alive activity to polling", () => {
    online = false;
    render(<KnowledgeSourcesPage active={false} />);
    expect(screen.getByText("来源服务未连接")).toBeTruthy();
    expect(useRuns).not.toHaveBeenCalled();
  });

  it("suppresses its standalone PageTopbar in embedded content-only mode", () => {
    render(<KnowledgeSourcesPage active embedded />);
    expect(screen.queryByRole("heading", { name: "来源控制面" })).toBeNull();
    expect(screen.getByText("Handbook")).toBeTruthy();
  });

});
