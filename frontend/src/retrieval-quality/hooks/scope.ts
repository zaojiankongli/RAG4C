import type { RetrievalScope } from "../model/contracts";

function decodePayload(token: string): Record<string, unknown> | null {
  const part = token.split(".", 1)[0];
  if (!part || typeof globalThis.atob !== "function") return null;
  try {
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const bytes = Uint8Array.from(globalThis.atob(base64), (character) => character.charCodeAt(0));
    const value = JSON.parse(new TextDecoder().decode(bytes));
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch { return null; }
}

export function decodeActorSubject(token: string): string {
  const subject = decodePayload(token.trim())?.sub;
  return typeof subject === "string" ? subject.trim() : "";
}
export function requireRetrievalScope(workspace: { tenantId: string; datasetId: string } | null, actorToken: string): RetrievalScope | null {
  const tenantId = workspace?.tenantId.trim() ?? "";
  const datasetId = workspace?.datasetId.trim() ?? "";
  const token = actorToken.trim();
  const actorId = decodeActorSubject(token);
  return tenantId && datasetId && token && actorId ? { tenantId, datasetId, actorToken: token, actorId } : null;
}
export function retrievalScopeKey(scope: RetrievalScope | null): string {
  return scope ? `${scope.tenantId}\u0000${scope.datasetId}\u0000${scope.actorToken}` : "";
}
