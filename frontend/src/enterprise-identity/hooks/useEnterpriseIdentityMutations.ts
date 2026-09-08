import { useCallback, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  activateProvider,
  createDomain as createDomainApi,
  createIdentityIdempotencyKey,
  createIdentityProvider,
  disableProvider,
  issueScimToken,
  revokeDomain as revokeDomainApi,
  revokeScimToken,
  updateIdentityProvider,
  verifyDomain as verifyDomainApi,
  type IdentityRequestOptions,
} from "../api/enterpriseIdentityApi";
import type {
  CreateDomainInput,
  DomainMutationResult,
  EnterpriseIdentityProvider,
  EnterpriseScimToken,
  EnterpriseVerifiedDomain,
  IdentityProviderInput,
  IdentityRevisionInput,
  IssueScimTokenInput,
  ProviderMutationResult,
  ScimTokenDelivery,
  ScimTokenMutationResult,
} from "../enterpriseIdentityModel";

export interface IdentityCollection<T> {
  upsert: (item: T) => void;
  reload: () => Promise<void>;
}
export interface IdentityMutationError {
  message: string;
  needsRefresh: boolean;
  migrationRequired: boolean;
  retryAvailable: boolean;
  status?: number;
}
interface Options {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  domains: IdentityCollection<EnterpriseVerifiedDomain>;
  providers: IdentityCollection<EnterpriseIdentityProvider>;
  scimTokens: IdentityCollection<EnterpriseScimToken>;
}
type Result = DomainMutationResult | ProviderMutationResult | ScimTokenMutationResult;
type Operation = (options: IdentityRequestOptions) => Promise<Result>;
type Kind = "domain" | "provider" | "scim";
interface Pending {
  operation: Operation;
  kind: Kind;
  success: string;
  key: string;
  ownerOnly: boolean;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function codeOf(error: ApiError): string | undefined {
  const body = recordValue(error.body);
  const detail = recordValue(body?.detail);
  const code = detail?.code ?? body?.code;
  return typeof code === "string" ? code : undefined;
}
export function projectIdentityMutationError(error: unknown): IdentityMutationError {
  if (error instanceof ApiError) {
    const code = codeOf(error);
    if (code?.includes("migration_required"))
      return {
        message: "身份联合数据库版本尚未就绪，请完成 0021 迁移后重试",
        needsRefresh: false,
        migrationRequired: true,
        retryAvailable: false,
        status: error.status,
      };
    if (error.status === 409)
      return {
        message: "身份控制面记录已变化，请刷新后重试",
        needsRefresh: true,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    if (error.status === 401)
      return {
        message: "签名身份已失效",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    if (error.status === 403)
      return {
        message: "当前租户角色没有执行该身份控制面操作的权限",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        message: "身份控制面请求未确认提交，可使用同一请求重试",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: true,
        status: error.status,
      };
  }
  return {
    message: "身份控制面操作失败，请稍后重试",
    needsRefresh: false,
    migrationRequired: false,
    retryAvailable: false,
  };
}
function allowed(context: EnterpriseContext, ownerOnly: boolean): boolean {
  if (context.capabilities.identity_federation?.state !== "ready") return false;
  return ownerOnly
    ? context.actor.role === "owner"
    : context.actor.role === "owner" || context.actor.role === "admin";
}

export function useEnterpriseIdentityMutations({
  scope,
  context,
  domains,
  providers,
  scimTokens,
}: Options) {
  const [state, setState] = useState<{
    saving: boolean;
    error: IdentityMutationError | null;
    success: string | null;
    scimDelivery: ScimTokenDelivery | null;
  }>({ saving: false, error: null, success: null, scimDelivery: null });
  const pending = useRef<Pending | null>(null);
  const busy = useRef(false);
  const run = useCallback(
    async (
      operation: Operation,
      kind: Kind,
      success: string,
      ownerOnly = false,
      key = createIdentityIdempotencyKey(),
    ): Promise<Result | null> => {
      if (!allowed(context, ownerOnly)) {
        setState({
          saving: false,
          error: {
            message: ownerOnly
              ? "仅 tenant owner 可以执行此操作"
              : "仅 tenant owner/admin 可以执行此操作",
            needsRefresh: false,
            migrationRequired: false,
            retryAvailable: false,
          },
          success: null,
          scimDelivery: null,
        });
        return null;
      }
      if (busy.current) return null;
      busy.current = true;
      pending.current = { operation, kind, success, key, ownerOnly };
      setState({ saving: true, error: null, success: null, scimDelivery: null });
      try {
        const result = await operation({ idempotencyKey: key });
        if (kind === "domain" && "domain" in result) domains.upsert(result.domain);
        if (kind === "provider" && "provider" in result) providers.upsert(result.provider);
        if (kind === "scim" && "token" in result) scimTokens.upsert(result.token);
        const refresh =
          kind === "domain"
            ? domains.reload()
            : kind === "provider"
              ? providers.reload()
              : scimTokens.reload();
        await refresh;
        pending.current = null;
        setState({
          saving: false,
          error: null,
          success,
          scimDelivery: "delivery" in result ? result.delivery : null,
        });
        return result;
      } catch (error) {
        const projected = projectIdentityMutationError(error);
        if (!projected.retryAvailable) pending.current = null;
        setState({ saving: false, error: projected, success: null, scimDelivery: null });
        return null;
      } finally {
        busy.current = false;
      }
    },
    [context, domains, providers, scimTokens],
  );
  const retry = useCallback(() => {
    const value = pending.current;
    return value
      ? run(value.operation, value.kind, value.success, value.ownerOnly, value.key)
      : Promise.resolve(null);
  }, [run]);
  const createDomain = useCallback(
    async (payload: CreateDomainInput): Promise<DomainMutationResult | null> =>
      (await run(
        (options) => createDomainApi(scope, payload, options),
        "domain",
        "域名挑战已创建",
      )) as DomainMutationResult | null,
    [run, scope],
  );
  const verifyDomain = useCallback(
    async (
      domain: EnterpriseVerifiedDomain,
      payload: IdentityRevisionInput,
    ): Promise<DomainMutationResult | null> =>
      (await run(
        (options) => verifyDomainApi(scope, domain.id, payload, options),
        "domain",
        "域名验证已检查",
      )) as DomainMutationResult | null,
    [run, scope],
  );
  const revokeDomain = useCallback(
    async (
      domain: EnterpriseVerifiedDomain,
      payload: IdentityRevisionInput,
    ): Promise<DomainMutationResult | null> =>
      (await run(
        (options) => revokeDomainApi(scope, domain.id, payload, options),
        "domain",
        "域名已撤销",
      )) as DomainMutationResult | null,
    [run, scope],
  );
  const createProvider = useCallback(
    async (payload: IdentityProviderInput): Promise<ProviderMutationResult | null> =>
      (await run(
        (options) => createIdentityProvider(scope, payload, options),
        "provider",
        "身份提供商草稿已创建",
      )) as ProviderMutationResult | null,
    [run, scope],
  );
  const updateProvider = useCallback(
    async (
      provider: EnterpriseIdentityProvider,
      payload: IdentityProviderInput,
    ): Promise<ProviderMutationResult | null> =>
      (await run(
        (options) => updateIdentityProvider(scope, provider.id, payload, options),
        "provider",
        "身份提供商配置已更新",
      )) as ProviderMutationResult | null,
    [run, scope],
  );
  const activate = useCallback(
    async (
      provider: EnterpriseIdentityProvider,
      payload: IdentityRevisionInput,
    ): Promise<ProviderMutationResult | null> =>
      (await run(
        (options) => activateProvider(scope, provider.id, payload, options),
        "provider",
        "身份提供商控制面已激活",
        true,
      )) as ProviderMutationResult | null,
    [run, scope],
  );
  const disable = useCallback(
    async (
      provider: EnterpriseIdentityProvider,
      payload: IdentityRevisionInput,
    ): Promise<ProviderMutationResult | null> =>
      (await run(
        (options) => disableProvider(scope, provider.id, payload, options),
        "provider",
        "身份提供商已停用",
        true,
      )) as ProviderMutationResult | null,
    [run, scope],
  );
  const issueScim = useCallback(
    async (payload: IssueScimTokenInput): Promise<ScimTokenMutationResult | null> =>
      (await run(
        (options) => issueScimToken(scope, payload, options),
        "scim",
        "SCIM token 已签发",
      )) as ScimTokenMutationResult | null,
    [run, scope],
  );
  const revokeScim = useCallback(
    async (
      token: EnterpriseScimToken,
      payload: IdentityRevisionInput,
    ): Promise<ScimTokenMutationResult | null> =>
      (await run(
        (options) => revokeScimToken(scope, token.id, payload, options),
        "scim",
        "SCIM token 已撤销",
      )) as ScimTokenMutationResult | null,
    [run, scope],
  );
  const clear = useCallback(() => {
    pending.current = null;
    setState((current) => ({ ...current, error: null, success: null }));
  }, []);
  const clearScimDelivery = useCallback(
    () => setState((current) => ({ ...current, scimDelivery: null })),
    [],
  );
  return {
    ...state,
    retry,
    createDomain,
    verifyDomain,
    revokeDomain,
    createProvider,
    updateProvider,
    activate,
    disable,
    issueScim,
    revokeScim,
    clear,
    clearScimDelivery,
  };
}
