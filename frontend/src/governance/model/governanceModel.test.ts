import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import {
  GovernanceScopeError,
  datasetStatusPresentation,
  projectGovernanceError,
  requireGovernanceScope,
  summarizeDatasetPolicies,
  safeReference,
  qaActionPolicy,
  validateDateRange,
  validateVersionDates,
} from "./governanceModel";

describe("governance model", () => {
  it("fails closed unless tenant, dataset, and authenticated actor token are all present", () => {
    expect(
      requireGovernanceScope({ tenantId: "tenant-a", datasetId: "dataset-a" }, " actor-token "),
    ).toEqual({ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" });

    for (const candidate of [
      [{ tenantId: "", datasetId: "dataset-a" }, "token"],
      [{ tenantId: "tenant-a", datasetId: "" }, "token"],
      [{ tenantId: "tenant-a", datasetId: "dataset-a" }, ""],
    ] as const) {
      expect(() => requireGovernanceScope(candidate[0], candidate[1])).toThrow(
        GovernanceScopeError,
      );
    }
  });

  it("projects policy summaries without rendering secret values or credential-bearing URLs", () => {
    const summaries = summarizeDatasetPolicies({
      parser: {
        engine: "mineru",
        credential_ref: "secret-manager://parser-prod",
        api_key: "must-never-render",
        endpoint: "https://user:password@example.test/private",
      },
      chunk: { max_tokens: 512 },
      retrieval: { top_k: 12, password: "also-secret" },
      retention: { days: 365 },
      metadata: { language: "zh-CN" },
    });

    expect(summaries.find((item) => item.key === "parser")).toMatchObject({
      facts: [{ label: "引擎", value: "mineru" }],
      credentialRefs: ["secret-manager://parser-prod"],
    });
    const rendered = JSON.stringify(summaries);
    expect(rendered).not.toContain("must-never-render");
    expect(rendered).not.toContain("also-secret");
    expect(rendered).not.toContain("password@example.test");
  });

  it("keeps lifecycle status labels distinct and sanitizes ApiError output", () => {
    expect(datasetStatusPresentation.active.label).toBe("运行中");
    expect(datasetStatusPresentation.archived.label).toBe("已归档");
    expect(datasetStatusPresentation.disabled.label).toBe("已停用");

    expect(projectGovernanceError(new ApiError("raw conflict", "http", 409))).toEqual({
      kind: "conflict",
      title: "资料已被更新",
      description: "当前修订已过期，请刷新后重新提交。",
      canRetry: true,
    });
    expect(projectGovernanceError(new ApiError("postgresql://user:pass@db", "network"))).toEqual({
      kind: "offline",
      title: "无法连接知识治理服务",
      description: "当前事实不可用。请检查服务连接后重试。",
      canRetry: true,
    });
    expect(JSON.stringify(projectGovernanceError(new Error("api_key=secret")))).not.toContain(
      "secret",
    );
  });

  it("redacts signed URLs, opaque credentials, and exposes only safe reference identifiers", () => {
    expect(safeReference("https://user:pass@example.test/path/file?token=abc&X-Amz-Signature=sig#frag")).toBe("https://example.test/path/file");
    expect(safeReference("vault://documents/doc-a/v3?token=secret")).toBe("vault://documents/doc-a/v3");
    expect(safeReference("opaque-api-key-value")).toBe("[受保护引用]");
  });

  it("defines explicit QA actions for every lifecycle and validates date order", () => {
    expect(qaActionPolicy.active).toMatchObject({ edit: true, review: true, alternatives: true, expire: true, restore: false });
    expect(qaActionPolicy.expired).toMatchObject({ edit: false, review: false, alternatives: false, expire: false, restore: true });
    for (const state of ["delete_requested", "deleting", "delete_failed", "deleted"] as const) expect(qaActionPolicy[state]).toEqual({ edit:false, review:false, alternatives:false, expire:false, restore:false });
    expect(validateDateRange("bad", "")).toBe("生效时间格式无效。");
    expect(validateDateRange("2026-08-26T00:00:00Z", "2026-08-25T00:00:00Z")).toBe("生效时间必须早于过期时间。");
    expect(validateDateRange("2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z")).toBe("生效时间必须早于过期时间。");
    expect(validateDateRange("2026-08-25T00:00:00Z", "2026-08-26T00:00:00Z")).toBeNull();
    expect(validateVersionDates("", "2026-08-26T00:00:00Z", "bad")).toBe("清理时间格式无效。");
    expect(validateVersionDates("", "2026-08-26T00:00:00Z", "2026-08-26T00:00:00Z")).toBe("清理时间必须晚于过期时间。");
  });

});
