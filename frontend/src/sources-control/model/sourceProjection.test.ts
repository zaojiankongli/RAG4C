import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import {
  SourceScopeError,
  isExecutionActive,
  projectSourceError,
  requireSourceScope,
  sameSourceGeneration,
  shortenReference,
  summarizeSourceConfig,
  safeSourceUri,
  type SourceRecord,
} from "./sourceProjection";

const local: SourceRecord = {
  id: "source-1234567890abcdef",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  name: "Handbook",
  kind: "local_dir",
  config: {
    path: "C:/private/knowledge/handbook",
    include: ["**/*.md"],
    exclude: ["drafts/**"],
    extensions: [".md", ".txt"],
    max_files: 200,
    credential_ref: "secret://sources/local-handbook",
  },
  metadata: {},
  status: "active",
  generation: 4,
  last_cursor: {},
  last_result: {},
  last_error: "",
  last_sync_at: null,
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:00Z",
};

describe("source control projections", () => {
  it("requires the complete authenticated workspace scope", () => {
    expect(() => requireSourceScope({ tenantId: "tenant-a", datasetId: "dataset-a" }, ""))
      .toThrow(SourceScopeError);
    expect(requireSourceScope({ tenantId: " tenant-a ", datasetId: " dataset-a " }, " token "))
      .toEqual({ tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token" });
  });

  it("summarizes connectors without exposing full paths or credential references", () => {
    const summary = summarizeSourceConfig(local);
    expect(summary.primary).toBe("handbook");
    expect(summary.details).toContain(".md、.txt");
    expect(summary.credentialBadge).toBe("secret:// 引用");
    expect(JSON.stringify(summary)).not.toContain("private/knowledge");
    expect(JSON.stringify(summary)).not.toContain("sources/local-handbook");

    const github = summarizeSourceConfig({
      ...local,
      kind: "github_repo",
      config: {
        repo: "trusted-org/platform",
        ref: "release/v2",
        mode: "api",
        include: [],
        exclude: [],
        credential_ref: "vault://github/platform",
      },
    });
    expect(github.primary).toBe("trusted-org/platform");
    expect(github.details).toContain("release/v2");
    expect(github.credentialBadge).toBe("vault:// 引用");
    expect(JSON.stringify(github)).not.toContain("github/platform");
  });

  it("projects known backend rejections accurately but hides arbitrary bodies", () => {
    const invalid = new ApiError("local_dir path 不存在或不在允许目录内", "http", 422, {
      detail: { code: "knowledge_source_invalid", message: "local_dir path 不存在或不在允许目录内" },
    });
    expect(projectSourceError(invalid)).toMatchObject({
      kind: "invalid",
      description: "local_dir path 不存在或不在允许目录内",
    });
    const unknown = new ApiError("token=secret stack trace", "http", 500, { token: "secret" });
    expect(projectSourceError(unknown).description).not.toContain("secret");
  });

  it("keeps execution activity and generation fences explicit", () => {
    expect(isExecutionActive("pending")).toBe(true);
    expect(isExecutionActive("executing")).toBe(true);
    expect(isExecutionActive("failed")).toBe(true);
    expect(isExecutionActive("completed")).toBe(false);
    expect(sameSourceGeneration({ source_generation: 4 }, local)).toBe(true);
    expect(sameSourceGeneration({ source_generation: 3 }, local)).toBe(false);
  });

  it("shortens operational references while preserving accessible copy values", () => {
    expect(shortenReference("run-1234567890-abcdefghijklmnop")).toEqual({
      display: "run-1234…klmnop",
      full: "run-1234567890-abcdefghijklmnop",
    });
  });
  it("projects source URIs without local paths, HTTP paths, queries, fragments, or credentials", () => {
    expect(safeSourceUri("file:///C:/private/payroll/guide.md")).toBe("[本地文件引用] · guide.md");
    expect(safeSourceUri("file://server/share/private.txt")).toBe("[本地文件引用] · private.txt");
    expect(safeSourceUri("https://user:pass@example.test/private/team/guide.md?token=secret#part")).toBe("https://example.test · guide.md");
    expect(safeSourceUri("http://example.test/private/no-extension")).toBe("http://example.test");
    expect(safeSourceUri("C:\\private\\notes.txt")).toBe("[来源引用] · notes.txt");
  });
});
