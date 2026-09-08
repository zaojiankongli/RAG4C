import type { SourceScope, SourceSyncRequest } from "../model/sourceModels";

const STORAGE_KEY = "rag4c.source-sync-intents.v1";
export interface SyncIntentStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export interface DurableSyncIntent { identity: string; idempotencyKey: string; createdAt: string; }
type IntentMap = Record<string, DurableSyncIntent>;

function defaultStorage(): SyncIntentStorage {
  return localStorage;
}
function read(storage: SyncIntentStorage): IntentMap {
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) ?? "{}") as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as IntentMap : {};
  } catch { return {}; }
}
function write(storage: SyncIntentStorage, value: IntentMap): void {
  const keys = Object.keys(value);
  if (!keys.length) storage.removeItem(STORAGE_KEY);
  else storage.setItem(STORAGE_KEY, JSON.stringify(value));
}
export function syncIntentIdentity(scope: SourceScope, sourceId: string, generation: number, payload: SourceSyncRequest): string {
  return JSON.stringify({ v: 1, tenant: scope.tenantId, dataset: scope.datasetId, source: sourceId, generation, force_full: payload.force_full, dry_run: payload.dry_run });
}
export function getOrCreateSyncIntent(
  scope: SourceScope,
  sourceId: string,
  generation: number,
  payload: SourceSyncRequest,
  storage: SyncIntentStorage = defaultStorage(),
  random: () => string = () => crypto.randomUUID(),
): DurableSyncIntent {
  const identity = syncIntentIdentity(scope, sourceId, generation, payload);
  const intents = read(storage);
  const existing = intents[identity];
  if (existing?.idempotencyKey) return existing;
  const intent = { identity, idempotencyKey: `srcsync-${random()}`, createdAt: new Date().toISOString() };
  intents[identity] = intent;
  write(storage, intents);
  return intent;
}
export function clearSyncIntent(identity: string, storage: SyncIntentStorage = defaultStorage()): void {
  const intents = read(storage);
  delete intents[identity];
  write(storage, intents);
}
