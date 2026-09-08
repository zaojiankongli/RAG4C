// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseComplianceCenter from "./EnterpriseComplianceCenter";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
const context: EnterpriseContext = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 1,
    quota_chunks: 1,
    doc_count: 0,
    chunk_count: 0,
  },
  actor: { id: "owner", name: "Owner", email: "owner@example.com", role: "owner" },
  member_count: 1,
  dataset_count: 1,
  effective_permissions: ["enterprise.manage"],
  role_permissions: {},
  capabilities: { audit_compliance: { state: "ready", label: "审计合规", reason: null } },
};
const policy = {
  id: "policy-1",
  status: "active",
  audit_retention_days: 365,
  export_retention_days: 30,
  revision: 7,
  last_preview_at: "2026-08-26T08:00:00Z",
};
const hold = {
  id: "hold-1",
  name: "监管调查",
  reason: "保全审计证据",
  status: "active",
  revision: 2,
  sequence_start: 100,
  sequence_end: 500,
  created_at: "2026-08-26T08:00:00Z",
  created_by: "owner-1",
};
const job = {
  id: "export-1",
  format: "ndjson",
  status: "completed",
  filters: { action: "member.role.updated" },
  sha256: "a".repeat(64),
  byte_size: 4096,
  row_count: 88,
  revision: 3,
  requested_at: "2026-08-26T08:00:00Z",
  completed_at: "2026-08-26T08:01:00Z",
  expires_at: "2026-09-25T08:00:00Z",
};
const preview = {
  policy_revision: 7,
  cutoff_at: "2025-08-26T08:00:00Z",
  candidate_count: 120,
  protected_count: 15,
  deletable_count: 105,
  sequence_start: 10,
  sequence_end: 900,
  preview_fingerprint: "sha256:preview-fingerprint",
  generated_at: "2026-08-26T08:00:00Z",
};
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function installMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      media: "(max-width: 600px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}
function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("retention-policy")) return Promise.resolve(jsonResponse({ policy }));
      if (url.includes("retention/preview")) return Promise.resolve(jsonResponse(preview));
      if (url.includes("retention/execute"))
        return Promise.resolve(
          jsonResponse({
            deleted_count: 105,
            protected_count: 15,
            policy_revision: 7,
            executed_at: "2026-08-26T08:10:00Z",
          }),
        );
      if (url.includes("legal-holds") && method === "GET")
        return Promise.resolve(jsonResponse({ items: [hold], count: 1, next_before_id: null }));
      if (url.includes("audit-exports") && method === "GET" && !url.endsWith("/download"))
        return Promise.resolve(jsonResponse({ items: [job], count: 1, next_before_id: null }));
      if (url.endsWith("/download"))
        return Promise.resolve(
          new Response("export", {
            status: 200,
            headers: { "Content-Disposition": 'attachment; filename="audit.ndjson"' },
          }),
        );
      return Promise.resolve(jsonResponse({ hold, job }));
    }),
  );
}

describe("Stage 11 enterprise compliance center", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    installMedia(false);
    installFetch();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders policy revision, manual execution evidence and a fingerprint-fenced danger execute dialog", async () => {
    const user = userEvent.setup();
    render(<EnterpriseComplianceCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审计合规中心" });
    expect(within(center).getByText("manual_execution_only")).toBeTruthy();
    expect(within(center).getByText("revision 7")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "预览保留影响" }));
    expect(await within(center).findByText("120 条候选")).toBeTruthy();
    expect(within(center).getByText("15 条受 legal hold 保护")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "执行审计保留删除" }));
    const dialog = await screen.findByRole("dialog", { name: "执行审计保留删除" });
    expect(within(dialog).getByDisplayValue("sha256:preview-fingerprint")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("7")).toBeTruthy();
    expect(within(dialog).getByText("manual_execution_only")).toBeTruthy();
  });

  it("shows legal holds and release actions without fabricating protected rows", async () => {
    const user = userEvent.setup();
    render(<EnterpriseComplianceCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审计合规中心" });
    await user.click(within(center).getByRole("tab", { name: /Legal holds/ }));
    expect(await within(center).findByText("监管调查")).toBeTruthy();
    expect(within(center).getByText("sequence 100–500")).toBeTruthy();
    expect(within(center).getByRole("button", { name: "释放 监管调查 legal hold" })).toBeTruthy();
    expect(within(center).getByRole("button", { name: "创建 legal hold" })).toBeTruthy();
  });

  it("opens a TDesign audit export wizard and exposes hash/download job evidence", async () => {
    const user = userEvent.setup();
    render(<EnterpriseComplianceCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审计合规中心" });
    await user.click(within(center).getByRole("tab", { name: /审计导出/ }));
    expect(await within(center).findByText("88 rows")).toBeTruthy();
    expect(within(center).getByText("a".repeat(64))).toBeTruthy();
    expect(within(center).getByRole("button", { name: "下载 export-1 审计导出" })).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "创建审计导出" }));
    const wizard = await screen.findByRole("dialog", { name: "创建审计导出" });
    expect(wizard.querySelector(".t-steps")).toBeTruthy();
    expect(wizard.querySelector(".t-form")).toBeTruthy();
    expect(within(wizard).getByText("仅允许审计字段白名单过滤")).toBeTruthy();
  });

  it("renders 375/280 cards and a legal-hold action drawer without desktop duplication", async () => {
    installMedia(true);
    const user = userEvent.setup();
    render(<EnterpriseComplianceCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审计合规中心" });
    await user.click(within(center).getByRole("tab", { name: /Legal holds/ }));
    expect(within(center).getByTestId("compliance-legal-hold-mobile-list")).toBeTruthy();
    await user.click(within(center).getByRole("button", { name: "管理 监管调查 legal hold" }));
    const drawer = await screen.findByRole("dialog", { name: "合规对象操作" });
    expect(within(drawer).getByText("revision 2")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "释放 legal hold" })).toBeTruthy();
  });
});
