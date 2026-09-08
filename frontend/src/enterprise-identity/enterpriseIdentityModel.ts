export const SCIM_DATA_PLANE_REVISION = "0022_scim_provisioning_data_plane";
export const SCIM_DATA_PLANE_BASE_PATH = "/scim/v2";
export const SCIM_DATA_PLANE_RESOURCES = ["Users", "Groups"] as const;
export const SCIM_DATA_PLANE_SCOPES = [
  "users:read",
  "users:write",
  "groups:read",
  "groups:write",
] as const;

export interface EnterpriseScimReadinessReport {
  expected_head?: string | null;
  current_revision?: string | null;
  status?: string;
  missing_capability_groups?: string[];
}
export interface ScimDataPlaneEvidence {
  state: "scim_data_plane_ready" | "scim_data_plane_not_connected";
  ready: boolean;
  revision: string | null;
  base_path: typeof SCIM_DATA_PLANE_BASE_PATH;
  resources: (typeof SCIM_DATA_PLANE_RESOURCES)[number][];
  scopes: (typeof SCIM_DATA_PLANE_SCOPES)[number][];
}

export const OIDC_RUNTIME_REVISION = "0024_oidc_sso_runtime";
export const OIDC_CALLBACK_PATH = "/enterprise/sso/oidc/callback";

export interface OidcRuntimeEvidence {
  state: "oidc_runtime_ready" | "oidc_runtime_not_connected";
  ready: boolean;
  revision: string | null;
  provider_id: string | null;
  provider_name: string | null;
  callback_path: typeof OIDC_CALLBACK_PATH;
}
export interface OidcStartInput {
  provider_id: string;
  redirect_uri: string;
}
export interface OidcStartResult {
  authorization_url: string;
  provider_id: string;
  expires_at: string;
}
export interface OidcCallbackInput {
  code: string;
  state: string;
}
export interface OidcCallbackResult {
  status: "authenticated" | (string & {});
  actor_id: string;
  actor_name: string;
  actor_email: string;
  tenant_id: string;
  tenant_name: string;
  provider_id: string;
  provider_name: string;
  session_id: string;
  session_status: string;
  session_revision: number;
  expires_at: string;
}
export interface OidcCallbackDelivery {
  knowledge_actor_token: string;
  result: OidcCallbackResult;
}

export type EnterpriseIdentityCursor = string | number;
export interface EnterpriseIdentityPage<T> {
  items: T[];
  count: number;
  next_before_id: EnterpriseIdentityCursor | null;
}

export type DomainStatus = "pending" | "verified" | "revoked" | (string & {});
export interface EnterpriseVerifiedDomain {
  id: string;
  domain: string;
  normalized_domain: string;
  status: DomainStatus;
  verification_method: "dns_txt" | (string & {});
  txt_host: string;
  txt_value: string;
  revision: number;
  checked_at?: string;
  verified_at?: string;
  revoked_at?: string;
  created_at?: string;
  updated_at?: string;
}

export type IdentityProviderType = "oidc" | "saml";
export type IdentityProviderStatus = "draft" | "active" | "disabled" | (string & {});
export type IdentityValidationState =
  "unchecked" | "valid" | "invalid" | "unavailable" | (string & {});
export interface EnterpriseIdentityProvider {
  id: string;
  name: string;
  provider_type: IdentityProviderType;
  status: IdentityProviderStatus;
  trusted_domain_id: string;
  validation_state: IdentityValidationState;
  runtime_state: "runtime_not_connected" | (string & {});
  revision: number;
  issuer_url?: string;
  client_id?: string;
  secret_ref?: string;
  scopes?: string[];
  entity_id?: string;
  sso_url?: string;
  metadata_url?: string;
  certificate_fingerprint?: string;
  last_validation_at?: string;
  last_validation_error?: string;
  activated_at?: string;
  disabled_at?: string;
}

export type ScimTokenStatus = "active" | "revoked" | "expired" | (string & {});
export interface EnterpriseScimToken {
  id: string;
  name: string;
  prefix: string;
  status: ScimTokenStatus;
  scopes: string[];
  expires_at: string;
  revision: number;
  last_used_at?: string;
  use_count?: number;
  created_at?: string;
  revoked_at?: string;
}

export interface ScimTokenDelivery {
  state: "token_returned_once" | "token_already_issued" | (string & {});
  scim_token?: string;
}
export interface ScimTokenMutationResult {
  token: EnterpriseScimToken;
  delivery: ScimTokenDelivery | null;
}
export interface DomainMutationResult {
  domain: EnterpriseVerifiedDomain;
}
export interface ProviderMutationResult {
  provider: EnterpriseIdentityProvider;
}

export interface CreateDomainInput {
  domain: string;
  reason: string;
}
export interface IdentityRevisionInput {
  revision: number;
  reason: string;
}
export interface IdentityProviderInput {
  name: string;
  provider_type: IdentityProviderType;
  trusted_domain_id: string;
  issuer_url?: string;
  client_id?: string;
  secret_ref?: string;
  scopes?: string[];
  entity_id?: string;
  sso_url?: string;
  metadata_url?: string;
  certificate_fingerprint?: string;
  reason: string;
  revision?: number;
}
export interface IssueScimTokenInput {
  name: string;
  scopes: string[];
  expires_in_days: number;
  reason: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function integerValue(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}
function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value
        .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
        .map((item) => item.trim())
    : [];
}
function cursorValue(value: unknown): EnterpriseIdentityCursor | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}
function projectPage<T>(
  input: unknown,
  project: (item: unknown) => T | null,
): EnterpriseIdentityPage<T> {
  const source = isRecord(input) ? input : {};
  const items = (Array.isArray(source.items) ? source.items : [])
    .map(project)
    .filter((item): item is T => item !== null);
  return {
    items,
    count: integerValue(source.count) ?? items.length,
    next_before_id: cursorValue(source.next_before_id),
  };
}
function addLifecycle<T extends object>(
  target: T,
  source: Record<string, unknown>,
  keys: readonly string[],
): T {
  for (const key of keys) {
    const value = stringValue(source[key]);
    if (value) Object.assign(target, { [key]: value });
  }
  return target;
}

export function projectEnterpriseDomain(input: unknown): EnterpriseVerifiedDomain | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const domain = stringValue(input.domain);
  const normalized = stringValue(input.normalized_domain);
  const status = stringValue(input.status);
  const method = stringValue(input.verification_method);
  const host = stringValue(input.txt_host);
  const txt = stringValue(input.txt_value);
  const revision = integerValue(input.revision);
  if (!id || !domain || !normalized || !status || !method || !host || !txt || revision === null)
    return null;
  return addLifecycle(
    {
      id,
      domain,
      normalized_domain: normalized,
      status: status as DomainStatus,
      verification_method: method as EnterpriseVerifiedDomain["verification_method"],
      txt_host: host,
      txt_value: txt,
      revision,
    },
    input,
    ["checked_at", "verified_at", "revoked_at", "created_at", "updated_at"],
  );
}

export function projectEnterpriseIdentityProvider(
  input: unknown,
): EnterpriseIdentityProvider | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const name = stringValue(input.name);
  const type = stringValue(input.provider_type);
  const status = stringValue(input.status);
  const domainId = stringValue(input.trusted_domain_id);
  const validation = stringValue(input.validation_state);
  const revision = integerValue(input.revision);
  if (
    !id ||
    !name ||
    (type !== "oidc" && type !== "saml") ||
    !status ||
    !domainId ||
    !validation ||
    revision === null
  )
    return null;
  const provider: EnterpriseIdentityProvider = {
    id,
    name,
    provider_type: type,
    status,
    trusted_domain_id: domainId,
    validation_state: validation,
    runtime_state: stringValue(input.runtime_state) ?? "runtime_not_connected",
    revision,
  };
  for (const key of [
    "issuer_url",
    "client_id",
    "secret_ref",
    "entity_id",
    "sso_url",
    "metadata_url",
    "certificate_fingerprint",
    "last_validation_at",
    "last_validation_error",
    "activated_at",
    "disabled_at",
  ] as const) {
    const value = stringValue(input[key]);
    if (value) provider[key] = value;
  }
  const scopes = stringArray(input.scopes);
  if (scopes.length) provider.scopes = scopes;
  return provider;
}

export function projectEnterpriseScimToken(input: unknown): EnterpriseScimToken | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const name = stringValue(input.name);
  const prefix = stringValue(input.prefix);
  const status = stringValue(input.status);
  const expiresAt = stringValue(input.expires_at);
  const revision = integerValue(input.revision);
  if (!id || !name || !prefix || !status || !expiresAt || revision === null) return null;
  const token: EnterpriseScimToken = addLifecycle(
    {
      id,
      name,
      prefix,
      status,
      scopes: stringArray(input.scopes),
      expires_at: expiresAt,
      revision,
    },
    input,
    ["last_used_at", "created_at", "revoked_at"],
  );
  const useCount = integerValue(input.use_count);
  if (useCount !== null) token.use_count = useCount;
  return token;
}

export function projectScimDataPlaneEvidence(
  readiness: EnterpriseScimReadinessReport | null | undefined,
): ScimDataPlaneEvidence {
  const revision = stringValue(readiness?.current_revision) ?? null;
  const missing = Array.isArray(readiness?.missing_capability_groups)
    ? readiness.missing_capability_groups
    : [];
  const completedRevisions = new Set([
    SCIM_DATA_PLANE_REVISION,
    "0023_enterprise_audit_compliance",
    OIDC_RUNTIME_REVISION,
  ]);
  const ready =
    readiness?.status === "ready" &&
    revision !== null &&
    completedRevisions.has(revision) &&
    !missing.includes("scim_provisioning_data_plane");
  return {
    state: ready ? "scim_data_plane_ready" : "scim_data_plane_not_connected",
    ready,
    revision,
    base_path: SCIM_DATA_PLANE_BASE_PATH,
    resources: [...SCIM_DATA_PLANE_RESOURCES],
    scopes: [...SCIM_DATA_PLANE_SCOPES],
  };
}

export function projectOidcRuntimeEvidence(
  readiness: EnterpriseScimReadinessReport | null | undefined,
  providers: EnterpriseIdentityProvider[],
): OidcRuntimeEvidence {
  const revision = stringValue(readiness?.current_revision) ?? null;
  const missing = Array.isArray(readiness?.missing_capability_groups)
    ? readiness.missing_capability_groups
    : [];
  const provider = providers.find(
    (item) =>
      item.provider_type === "oidc" &&
      item.status === "active" &&
      item.validation_state === "valid" &&
      item.runtime_state === "oidc_runtime_ready",
  );
  const ready =
    readiness?.status === "ready" &&
    readiness.expected_head === OIDC_RUNTIME_REVISION &&
    revision === OIDC_RUNTIME_REVISION &&
    !missing.includes("oidc_sso_runtime") &&
    Boolean(provider);
  return {
    state: ready ? "oidc_runtime_ready" : "oidc_runtime_not_connected",
    ready,
    revision,
    provider_id: ready ? (provider?.id ?? null) : null,
    provider_name: ready ? (provider?.name ?? null) : null,
    callback_path: OIDC_CALLBACK_PATH,
  };
}

export function projectOidcStart(input: unknown): OidcStartResult {
  const source = isRecord(input) ? input : {};
  const authorizationUrl = stringValue(source.authorization_url);
  const providerId = stringValue(source.provider_id);
  const expiresAt = stringValue(source.expires_at);
  if (!authorizationUrl || !providerId || !expiresAt)
    throw new Error("OIDC start response is missing runtime facts");
  const parsed = new URL(authorizationUrl);
  if (
    parsed.protocol !== "https:" &&
    !(parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname))
  )
    throw new Error("OIDC authorization URL is not allowed");
  return { authorization_url: authorizationUrl, provider_id: providerId, expires_at: expiresAt };
}

export function projectOidcCallbackDelivery(input: unknown): OidcCallbackDelivery {
  const source = isRecord(input) ? input : {};
  const actor = isRecord(source.actor) ? source.actor : {};
  const tenant = isRecord(source.tenant) ? source.tenant : {};
  const provider = isRecord(source.provider) ? source.provider : {};
  const session = isRecord(source.session) ? source.session : {};
  const token = stringValue(source.knowledge_actor_token);
  const status = stringValue(source.status);
  const actorId = stringValue(actor.id);
  const actorName = stringValue(actor.name);
  const actorEmail = stringValue(actor.email);
  const tenantId = stringValue(tenant.id);
  const tenantName = stringValue(tenant.name);
  const providerId = stringValue(provider.id);
  const providerName = stringValue(provider.name);
  const sessionId = stringValue(session.id);
  const sessionStatus = stringValue(session.status);
  const sessionRevision = integerValue(session.revision);
  const expiresAt = stringValue(session.expires_at);
  if (
    !token ||
    !status ||
    !actorId ||
    !actorName ||
    !actorEmail ||
    !tenantId ||
    !tenantName ||
    !providerId ||
    !providerName ||
    !sessionId ||
    !sessionStatus ||
    sessionRevision === null ||
    !expiresAt
  )
    throw new Error("OIDC callback response is missing authenticated facts");
  return {
    knowledge_actor_token: token,
    result: {
      status,
      actor_id: actorId,
      actor_name: actorName,
      actor_email: actorEmail,
      tenant_id: tenantId,
      tenant_name: tenantName,
      provider_id: providerId,
      provider_name: providerName,
      session_id: sessionId,
      session_status: sessionStatus,
      session_revision: sessionRevision,
      expires_at: expiresAt,
    },
  };
}

export const projectDomainPage = (input: unknown) => projectPage(input, projectEnterpriseDomain);
export const projectProviderPage = (input: unknown) =>
  projectPage(input, projectEnterpriseIdentityProvider);
export const projectScimTokenPage = (input: unknown) =>
  projectPage(input, projectEnterpriseScimToken);

export function projectDomainMutation(input: unknown): DomainMutationResult {
  const source = isRecord(input) ? input : {};
  const domain = projectEnterpriseDomain(source.domain ?? source.item ?? input);
  if (!domain) throw new Error("identity domain mutation response is missing domain facts");
  return { domain };
}
export function projectProviderMutation(input: unknown): ProviderMutationResult {
  const source = isRecord(input) ? input : {};
  const provider = projectEnterpriseIdentityProvider(source.provider ?? source.item ?? input);
  if (!provider) throw new Error("identity provider mutation response is missing provider facts");
  return { provider };
}
export function projectScimTokenMutation(input: unknown): ScimTokenMutationResult {
  const source = isRecord(input) ? input : {};
  const token = projectEnterpriseScimToken(source.token ?? source.item ?? input);
  if (!token) throw new Error("SCIM token mutation response is missing token facts");
  const deliverySource = isRecord(source.delivery) ? source.delivery : null;
  const state = deliverySource ? stringValue(deliverySource.state) : null;
  const raw = deliverySource ? stringValue(deliverySource.scim_token) : null;
  return { token, delivery: state ? { state, ...(raw ? { scim_token: raw } : {}) } : null };
}
