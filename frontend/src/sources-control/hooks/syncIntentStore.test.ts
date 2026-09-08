import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearSyncIntent,
  getOrCreateSyncIntent,
  syncIntentIdentity,
  type SyncIntentStorage,
} from "./syncIntentStore";

class MemoryStorage implements SyncIntentStorage {
  value: string | null = null;
  getItem() { return this.value; }
  setItem(_key: string, value: string) { this.value = value; }
  removeItem() { this.value = null; }
}

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "signed" };
const payload = { force_full: false, dry_run: false };

describe("durable source sync intents", () => {
  let storage: MemoryStorage;
  beforeEach(() => { storage = new MemoryStorage(); });

  it("persists and reuses one exact-case key for the same source generation and payload", () => {
    const random = vi.fn().mockReturnValue("A1B2C3D4-E5F6-47A8-90BC-1234567890AB");
    const first = getOrCreateSyncIntent(scope, "source-a", 7, payload, storage, random);
    const second = getOrCreateSyncIntent(scope, "source-a", 7, payload, storage, random);
    expect(second).toEqual(first);
    expect(first.idempotencyKey).toBe("srcsync-A1B2C3D4-E5F6-47A8-90BC-1234567890AB");
    expect(random).toHaveBeenCalledTimes(1);
  });

  it("uses distinct identities when payload or generation changes", () => {
    expect(syncIntentIdentity(scope, "source-a", 7, payload)).not.toBe(
      syncIntentIdentity(scope, "source-a", 8, payload),
    );
    expect(syncIntentIdentity(scope, "source-a", 7, payload)).not.toBe(
      syncIntentIdentity(scope, "source-a", 7, { ...payload, dry_run: true }),
    );
  });

  it("clears only the acknowledged intent and recovers from malformed storage", () => {
    const intent = getOrCreateSyncIntent(scope, "source-a", 7, payload, storage, () => "fixed-uuid-value");
    getOrCreateSyncIntent(scope, "source-b", 1, payload, storage, () => "other-uuid-value");
    clearSyncIntent(intent.identity, storage);
    expect(storage.value).not.toContain(intent.idempotencyKey);
    expect(storage.value).toContain("other-uuid-value");

    storage.value = "not-json";
    expect(getOrCreateSyncIntent(scope, "source-a", 7, payload, storage, () => "fresh-uuid-value").idempotencyKey)
      .toBe("srcsync-fresh-uuid-value");
  });
});
