import { getBaseUrl, request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectDomainMutation,
  projectDomainPage,
  projectProviderMutation,
  projectProviderPage,
  projectOidcCallbackDelivery,
  projectOidcStart,
  projectScimTokenMutation,
  projectScimTokenPage,
  type CreateDomainInput,
  type DomainMutationResult,
  type EnterpriseIdentityPage,
  type EnterpriseIdentityProvider,
  type EnterpriseScimToken,
  type EnterpriseVerifiedDomain,
  type IdentityProviderInput,
  type IdentityRevisionInput,
  type IssueScimTokenInput,
  type OidcCallbackDelivery,
  type OidcCallbackInput,
  type OidcStartInput,
  type OidcStartResult,
  type ProviderMutationResult,
  type ScimTokenMutationResult,
} from "../enterpriseIdentityModel";

export interface ScimDataPlaneUrls {
  baseEndpoint: string;
  serviceProviderConfigUrl: string;
}

export function buildScimDataPlaneUrls(baseUrl = getBaseUrl()): ScimDataPlaneUrls {
  const root = new URL(baseUrl);
  const baseEndpoint = new URL("/scim/v2", root).toString().replace(/\/$/, "");
  return {
    baseEndpoint,
    serviceProviderConfigUrl: `${baseEndpoint}/ServiceProviderConfig`,
  };
}

export interface IdentityRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}
export interface IdentityListQuery {
  beforeId?: string | number;
  limit?: number;
}

function headers(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for identity requests");
  if (!actorToken) throw new Error("actorToken is required for identity requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: `Bearer ${actorToken}` };
}
export function createIdentityIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-identity-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues)
    return `rag4c-identity-${Array.from(cryptoApi.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  return `rag4c-identity-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
function mutationHeaders(scope: EnterpriseScope, options: IdentityRequestOptions) {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied)
    throw new Error("idempotencyKey is required");
  const key = supplied || createIdentityIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...headers(scope), "Idempotency-Key": key };
}
function queryString(query: IdentityListQuery): string {
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200)
    throw new Error("limit must be between 1 and 200");
  const params = new URLSearchParams({ limit: String(limit) });
  if (query.beforeId !== undefined) params.set("before_id", String(query.beforeId));
  return params.toString();
}
function getPage<T>(
  scope: EnterpriseScope,
  path: string,
  query: IdentityListQuery,
  options: IdentityRequestOptions,
  projector: (input: unknown) => T,
): Promise<T> {
  return request<unknown>(`${path}?${queryString(query)}`, {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  }).then(projector);
}
function requiredReason(reason: string): string {
  const value = reason.trim();
  if (!value) throw new Error("reason is required");
  return value;
}
function requiredRevision(revision: number): number {
  if (!Number.isInteger(revision) || revision < 1) throw new Error("revision must be at least 1");
  return revision;
}
function requiredId(id: string, label: string): string {
  const value = id.trim();
  if (!value) throw new Error(`${label} is required`);
  return value;
}
function requiredDays(days: number): number {
  if (!Number.isInteger(days) || days < 1 || days > 365)
    throw new Error("expires_in_days must be between 1 and 365");
  return days;
}
function post<T>(
  scope: EnterpriseScope,
  path: string,
  body: unknown,
  options: IdentityRequestOptions,
  projector: (input: unknown) => T,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  return request<unknown>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projector);
}

export function fetchIdentityDomains(
  scope: EnterpriseScope,
  query: IdentityListQuery = {},
  options: IdentityRequestOptions = {},
): Promise<EnterpriseIdentityPage<EnterpriseVerifiedDomain>> {
  return getPage(scope, "/api/enterprise/identity/domains", query, options, projectDomainPage);
}
export function fetchIdentityProviders(
  scope: EnterpriseScope,
  query: IdentityListQuery = {},
  options: IdentityRequestOptions = {},
): Promise<EnterpriseIdentityPage<EnterpriseIdentityProvider>> {
  return getPage(scope, "/api/enterprise/identity/providers", query, options, projectProviderPage);
}
export function fetchScimTokens(
  scope: EnterpriseScope,
  query: IdentityListQuery = {},
  options: IdentityRequestOptions = {},
): Promise<EnterpriseIdentityPage<EnterpriseScimToken>> {
  return getPage(
    scope,
    "/api/enterprise/identity/scim-tokens",
    query,
    options,
    projectScimTokenPage,
  );
}

export function createDomain(
  scope: EnterpriseScope,
  payload: CreateDomainInput,
  options: IdentityRequestOptions = {},
): Promise<DomainMutationResult> {
  const domain = payload.domain
    .trim()
    .toLocaleLowerCase("en-US")
    .replace(/^\.+|\.+$/g, "");
  if (!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/.test(domain))
    throw new Error("a valid domain is required");
  return post(
    scope,
    "/api/enterprise/identity/domains",
    { domain, reason: requiredReason(payload.reason) },
    options,
    projectDomainMutation,
  );
}
export function verifyDomain(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityRevisionInput,
  options: IdentityRequestOptions = {},
): Promise<DomainMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/domains/${encodeURIComponent(requiredId(id, "domainId"))}/verify`,
    { revision: requiredRevision(payload.revision), reason: requiredReason(payload.reason) },
    options,
    projectDomainMutation,
  );
}
export function revokeDomain(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityRevisionInput,
  options: IdentityRequestOptions = {},
): Promise<DomainMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/domains/${encodeURIComponent(requiredId(id, "domainId"))}/revoke`,
    { revision: requiredRevision(payload.revision), reason: requiredReason(payload.reason) },
    options,
    projectDomainMutation,
  );
}

function providerPayload(payload: IdentityProviderInput) {
  return {
    name: payload.name.trim(),
    provider_type: payload.provider_type,
    trusted_domain_id: payload.trusted_domain_id.trim(),
    ...(payload.issuer_url?.trim() ? { issuer_url: payload.issuer_url.trim() } : {}),
    ...(payload.client_id?.trim() ? { client_id: payload.client_id.trim() } : {}),
    ...(payload.secret_ref?.trim() ? { secret_ref: payload.secret_ref.trim() } : {}),
    ...(payload.scopes?.length
      ? { scopes: payload.scopes.map((item) => item.trim()).filter(Boolean) }
      : {}),
    ...(payload.entity_id?.trim() ? { entity_id: payload.entity_id.trim() } : {}),
    ...(payload.sso_url?.trim() ? { sso_url: payload.sso_url.trim() } : {}),
    ...(payload.metadata_url?.trim() ? { metadata_url: payload.metadata_url.trim() } : {}),
    ...(payload.certificate_fingerprint?.trim()
      ? { certificate_fingerprint: payload.certificate_fingerprint.trim() }
      : {}),
    reason: requiredReason(payload.reason),
    ...(payload.revision ? { revision: requiredRevision(payload.revision) } : {}),
  };
}
export function createIdentityProvider(
  scope: EnterpriseScope,
  payload: IdentityProviderInput,
  options: IdentityRequestOptions = {},
): Promise<ProviderMutationResult> {
  return post(
    scope,
    "/api/enterprise/identity/providers",
    providerPayload(payload),
    options,
    projectProviderMutation,
  );
}
export function updateIdentityProvider(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityProviderInput,
  options: IdentityRequestOptions = {},
): Promise<ProviderMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/providers/${encodeURIComponent(requiredId(id, "providerId"))}`,
    providerPayload(payload),
    options,
    projectProviderMutation,
    "PATCH",
  );
}
export function activateProvider(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityRevisionInput,
  options: IdentityRequestOptions = {},
): Promise<ProviderMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/providers/${encodeURIComponent(requiredId(id, "providerId"))}/activate`,
    { revision: requiredRevision(payload.revision), reason: requiredReason(payload.reason) },
    options,
    projectProviderMutation,
  );
}
export function disableProvider(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityRevisionInput,
  options: IdentityRequestOptions = {},
): Promise<ProviderMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/providers/${encodeURIComponent(requiredId(id, "providerId"))}/disable`,
    { revision: requiredRevision(payload.revision), reason: requiredReason(payload.reason) },
    options,
    projectProviderMutation,
  );
}
export function issueScimToken(
  scope: EnterpriseScope,
  payload: IssueScimTokenInput,
  options: IdentityRequestOptions = {},
): Promise<ScimTokenMutationResult> {
  const name = payload.name.trim();
  if (!name) throw new Error("SCIM token name is required");
  const scopes = payload.scopes.map((item) => item.trim()).filter(Boolean);
  if (!scopes.length) throw new Error("SCIM scopes are required");
  return post(
    scope,
    "/api/enterprise/identity/scim-tokens",
    {
      name,
      scopes,
      expires_in_days: requiredDays(payload.expires_in_days),
      reason: requiredReason(payload.reason),
    },
    options,
    projectScimTokenMutation,
  );
}
export function revokeScimToken(
  scope: EnterpriseScope,
  id: string,
  payload: IdentityRevisionInput,
  options: IdentityRequestOptions = {},
): Promise<ScimTokenMutationResult> {
  return post(
    scope,
    `/api/enterprise/identity/scim-tokens/${encodeURIComponent(requiredId(id, "tokenId"))}/revoke`,
    { revision: requiredRevision(payload.revision), reason: requiredReason(payload.reason) },
    options,
    projectScimTokenMutation,
  );
}

function requiredRuntimeUrl(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  const parsed = new URL(normalized);
  const allowed =
    parsed.protocol === "https:" ||
    (parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname));
  if (!allowed) throw new Error(`${label} must use HTTPS or direct loopback HTTP`);
  return normalized;
}

export function startOidcLogin(
  scope: EnterpriseScope,
  input: OidcStartInput,
  options: Pick<IdentityRequestOptions, "signal"> = {},
): Promise<OidcStartResult> {
  return request<unknown>("/api/enterprise/sso/oidc/start", {
    method: "POST",
    headers: headers(scope),
    body: JSON.stringify({
      tenant_id: scope.tenantId.trim(),
      provider_id: requiredId(input.provider_id, "providerId"),
      redirect_uri: requiredRuntimeUrl(input.redirect_uri, "redirect_uri"),
    }),
    credentials: "include",
    signal: options.signal,
  }).then(projectOidcStart);
}

export function completeOidcCallback(
  input: OidcCallbackInput,
  options: Pick<IdentityRequestOptions, "signal"> = {},
): Promise<OidcCallbackDelivery> {
  const code = input.code.trim();
  const state = input.state.trim();
  if (!code || !state) throw new Error("OIDC code and state are required");
  return request<unknown>("/api/enterprise/sso/oidc/callback", {
    method: "POST",
    body: JSON.stringify({ code, state }),
    credentials: "include",
    signal: options.signal,
  }).then(projectOidcCallbackDelivery);
}
