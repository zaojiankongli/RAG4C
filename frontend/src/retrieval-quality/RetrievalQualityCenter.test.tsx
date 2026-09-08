// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api/retrievalQualityApi";
let online: boolean | null = true; let actorToken = "";
const workspace = { scope: { tenantId: "tenant-a", datasetId: "dataset-a" } };
vi.mock("../context/ConnectionContext", () => ({ useConnection: () => ({ online }) }));
vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({ useKnowledgeWorkspace: () => workspace }));
vi.mock("../knowledge/workspaceScope", () => ({ readKnowledgeActorToken: () => actorToken }));
vi.mock("./api/retrievalQualityApi");
import RetrievalQualityCenter from "./RetrievalQualityCenter";
function token(sub = "judge-a", tenant = "tenant-a") { return `${btoa(JSON.stringify({sub,tenant,iat:1,exp:9999999999,jti:"x"})).replace(/=/g,"").replace(/\+/g,"-").replace(/\//g,"_")}.sig`; }
beforeEach(() => { online = true; actorToken = token(); vi.clearAllMocks(); vi.mocked(api.fetchExperiments).mockResolvedValue({items:[],next_before_sequence:null}); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("RetrievalQualityCenter", () => {
  it("fails closed without authenticated actor scope", () => { actorToken = ""; render(<RetrievalQualityCenter />); expect(screen.getByText("缺少检索质量授权范围")).toBeTruthy(); expect(api.fetchExperiments).not.toHaveBeenCalled(); expect(api.runRetrievalComparison).not.toHaveBeenCalled(); });
  it("shows truthful offline state without demo facts", () => { online = false; render(<RetrievalQualityCenter />); expect(screen.getByText("检索质量服务未连接")).toBeTruthy(); expect(document.body.textContent).not.toContain("演示数据"); });
  it("exposes keyboard-operable mobile tabs at 375px", () => { vi.stubGlobal("matchMedia", vi.fn((query: string) => ({ matches: query.includes("max-width: 600px"), media: query, onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() }))); render(<RetrievalQualityCenter />); expect(screen.getByRole("tablist", { name: "检索质量中心视图" })).toBeTruthy(); expect(screen.getAllByRole("tab").map((item) => item.textContent)).toEqual(["配置", "结果", "历史"]); });
  it("composes the authenticated pure-retrieval workspace", async () => { render(<RetrievalQualityCenter />); expect(screen.getByRole("heading", { name: "检索质量中心" })).toBeTruthy(); expect(screen.getByRole("button", { name: "运行纯检索对比（不生成答案）" })).toBeTruthy(); await waitFor(() => expect(api.fetchExperiments).toHaveBeenCalled()); expect(screen.getByText(/不可变历史证据/)).toBeTruthy(); });
});
