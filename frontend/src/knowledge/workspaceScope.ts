export const KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY = "rag4c.knowledge_actor_token";
export const KNOWLEDGE_TENANT_STORAGE_KEY = "rag4c.knowledge_tenant_id";
export const KNOWLEDGE_DATASET_STORAGE_KEY = "rag4c.knowledge_dataset_id";

const DEFAULT_SCOPE_ID = "default";

export interface KnowledgeWorkspaceScope {
  tenantId: string;
  datasetId: string;
}

export interface StorageReader {
  getItem(key: string): string | null;
}

export interface KnowledgeLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
}

const DATASET_SCOPED_PATHS = ["/documents", "/taxonomy", "/sources", "/governance"] as const;

interface ResolveScopeOptions {
  tenantId?: string;
  datasetId?: string;
  actorToken?: string;
  storage?: StorageReader | null;
}

function browserStorage(): StorageReader | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage;
}

function storedValue(storage: StorageReader | null, key: string): string {
  try {
    return storage?.getItem(key)?.trim() ?? "";
  } catch {
    return "";
  }
}

function routePath(value: string): string {
  return value.replace(/^#/, "").split("?", 1)[0]?.replace(/\/+$/, "") || "/";
}

function isDatasetScopedPath(value: string): boolean {
  const path = routePath(value);
  return DATASET_SCOPED_PATHS.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function routeQuery(location: KnowledgeLocationLike): string {
  if (isDatasetScopedPath(location.pathname)) return location.search ?? "";
  const hash = location.hash ?? "";
  if (!isDatasetScopedPath(hash)) return "";
  const queryIndex = hash.indexOf("?");
  return queryIndex >= 0 ? hash.slice(queryIndex) : "";
}

export function readKnowledgeDatasetIdFromLocation(location: KnowledgeLocationLike): string | null {
  const datasetId = new URLSearchParams(routeQuery(location)).get("dataset")?.trim();
  return datasetId || null;
}

function decodeActorTenant(token: string): string {
  const payloadPart = token.split(".", 1)[0];
  if (!payloadPart || typeof globalThis.atob !== "function") return "";
  try {
    const base64 = payloadPart.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const binary = globalThis.atob(padded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as { tenant?: unknown };
    return typeof payload.tenant === "string" ? payload.tenant.trim() : "";
  } catch {
    return "";
  }
}

export function readKnowledgeActorToken(
  explicit?: string,
  storage: StorageReader | null = browserStorage(),
): string {
  return explicit?.trim() || storedValue(storage, KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY);
}

export function resolveKnowledgeWorkspaceScope({
  tenantId,
  datasetId,
  actorToken,
  storage = browserStorage(),
}: ResolveScopeOptions = {}): KnowledgeWorkspaceScope {
  const token = readKnowledgeActorToken(actorToken, storage);
  return {
    tenantId:
      tenantId?.trim() ||
      decodeActorTenant(token) ||
      storedValue(storage, KNOWLEDGE_TENANT_STORAGE_KEY) ||
      DEFAULT_SCOPE_ID,
    datasetId:
      datasetId?.trim() || storedValue(storage, KNOWLEDGE_DATASET_STORAGE_KEY) || DEFAULT_SCOPE_ID,
  };
}
