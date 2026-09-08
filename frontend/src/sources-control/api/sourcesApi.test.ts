import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import type { SourceScope } from "../model/sourceModels";
import {
  createSource, disableSource, enableSource, fetchRun, fetchRunItems, fetchRuns,
  fetchSource, fetchSources, patchSource, retryRun, syncSourceNow,
} from "./sourcesApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope: SourceScope = { tenantId: "tenant a", datasetId: "dataset/a", actorToken: "SignedToken" };
const auth = { "X-RAG4C-Tenant": "tenant a", Authorization: "Bearer SignedToken" };

beforeEach(() => mocked.mockReset().mockResolvedValue({}));

describe("Source API", () => {
  it("uses exact source list/detail/create/patch contracts", async () => {
    await fetchSources(scope, "disabled");
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources?status=disabled", { method: "GET", headers: auth, signal: undefined });
    await fetchSource(scope, "source/1");
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1", { method: "GET", headers: auth, signal: undefined });
    const create = { name: "Repo", kind: "github_repo" as const, config: { repo: "trusted/repo", ref: "main" }, metadata: {}, enabled: true };
    await createSource(scope, create);
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources", { method: "POST", headers: auth, body: JSON.stringify(create), signal: undefined });
    const patch = { expected_generation: 4, name: "Renamed" };
    await patchSource(scope, "source/1", patch);
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1", { method: "PATCH", headers: auth, body: JSON.stringify(patch), signal: undefined });
  });

  it("sends generation CAS for enable and disable", async () => {
    await disableSource(scope, "source/1", 5);
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/disable", { method: "POST", headers: auth, body: JSON.stringify({ expected_generation: 5 }), signal: undefined });
    await enableSource(scope, "source/1", 6);
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/enable", { method: "POST", headers: auth, body: JSON.stringify({ expected_generation: 6 }), signal: undefined });
  });

  it("uses durable idempotency and exact run contracts", async () => {
    const payload = { force_full: true, dry_run: false };
    await syncSourceNow(scope, "source/1", payload, "Case-Sensitive-Key-001");
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/sync-now", {
      method: "POST", headers: { ...auth, "Idempotency-Key": "Case-Sensitive-Key-001" }, body: JSON.stringify(payload), signal: undefined,
    });
    await fetchRuns(scope, "source/1", { status: "failed", trigger: "manual", cursor: "opaque_cursor_123456", limit: 10 });
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/runs?status=failed&trigger=manual&cursor=opaque_cursor_123456&limit=10", { method: "GET", headers: auth, signal: undefined });
    await fetchRun(scope, "source/1", "run/1");
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/runs/run%2F1", { method: "GET", headers: auth, signal: undefined });
  });

  it("uses exact item filters and retry empty object", async () => {
    await fetchRunItems(scope, "source/1", "run/1", { result: "failed", action: "upsert", cursor: "item_cursor_123456", limit: 10 });
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/runs/run%2F1/items?result=failed&action=upsert&cursor=item_cursor_123456&limit=10", { method: "GET", headers: auth, signal: undefined });
    await retryRun(scope, "source/1", "run/1");
    expect(mocked).toHaveBeenLastCalledWith("/api/knowledge-bases/dataset%2Fa/sources/source%2F1/runs/run%2F1/retry", { method: "POST", headers: auth, body: "{}", signal: undefined });
  });
});
