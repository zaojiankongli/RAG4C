import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type {
  EnterpriseCapability,
  EnterpriseContext,
  EnterpriseScope,
} from "../../enterprise-admin/model";
import {
  fetchIdentityDomains,
  fetchIdentityProviders,
  fetchScimTokens,
  type IdentityListQuery,
  type IdentityRequestOptions,
} from "../api/enterpriseIdentityApi";
import {
  projectOidcRuntimeEvidence,
  projectScimDataPlaneEvidence,
  type EnterpriseIdentityCursor,
  type EnterpriseIdentityPage,
  type EnterpriseIdentityProvider,
  type EnterpriseScimReadinessReport,
  type EnterpriseScimToken,
  type EnterpriseVerifiedDomain,
} from "../enterpriseIdentityModel";

export type IdentityResourceStatus =
  | "idle"
  | "loading"
  | "ready"
  | "unauthorized"
  | "forbidden"
  | "migration-required"
  | "unavailable"
  | "error";
export interface IdentityResourceError {
  status: IdentityResourceStatus;
  title: string;
  description: string;
  canRetry: boolean;
  code?: string;
}
export interface IdentityResource<T> {
  status: IdentityResourceStatus;
  items: T[];
  count: number | null;
  nextBeforeId: EnterpriseIdentityCursor | null;
  loadingMore: boolean;
  error: IdentityResourceError | null;
  reload: () => Promise<void>;
  loadMore: () => Promise<void>;
  upsert: (item: T) => void;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function errorCode(error: ApiError): string | undefined {
  const body = recordValue(error.body);
  const detail = recordValue(body?.detail);
  const code = detail?.code ?? body?.code;
  return typeof code === "string" ? code : undefined;
}
function projectError(error: unknown, label: string): IdentityResourceError {
  if (error instanceof ApiError) {
    const code = errorCode(error);
    if (code?.includes("migration_required"))
      return {
        status: "migration-required",
        title: "身份联合数据库版本尚未就绪",
        description: "完成 0021 identity federation 迁移后重试",
        canRetry: true,
        code,
      };
    if (error.status === 401)
      return {
        status: "unauthorized",
        title: "签名身份已失效",
        description: `重新连接身份后读取${label}`,
        canRetry: false,
        code,
      };
    if (error.status === 403)
      return {
        status: "forbidden",
        title: `没有读取${label}的权限`,
        description: "当前租户角色仅能查看有限能力证据",
        canRetry: false,
        code,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        status: "unavailable",
        title: `${label}服务暂不可用`,
        description: "控制面服务未连接或数据库尚未就绪",
        canRetry: true,
        code,
      };
  }
  return {
    status: "error",
    title: `${label}读取失败`,
    description: "服务没有返回可安全展示的身份控制面事实",
    canRetry: true,
  };
}
function merge<T>(current: T[], next: T[], keyOf: (item: T) => string): T[] {
  const map = new Map<string, T>();
  for (const item of [...current, ...next]) map.set(keyOf(item), item);
  return [...map.values()];
}

type Loader<T> = (
  scope: EnterpriseScope,
  query: IdentityListQuery,
  options: IdentityRequestOptions,
) => Promise<EnterpriseIdentityPage<T>>;
function useResource<T>(
  scope: EnterpriseScope,
  enabled: boolean,
  label: string,
  loader: Loader<T>,
  keyOf: (item: T) => string,
): IdentityResource<T> {
  const [status, setStatus] = useState<IdentityResourceStatus>("idle");
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const [nextBeforeId, setNextBeforeId] = useState<EnterpriseIdentityCursor | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<IdentityResourceError | null>(null);
  const version = useRef(0);
  const load = useCallback(async () => {
    if (!enabled || !scope.actorToken.trim()) return;
    const requestVersion = ++version.current;
    const controller = new AbortController();
    setStatus("loading");
    setError(null);
    try {
      const page = await loader(scope, { limit: 50 }, { signal: controller.signal });
      if (requestVersion !== version.current) return;
      setItems(page.items);
      setCount(page.count);
      setNextBeforeId(page.next_before_id);
      setStatus("ready");
    } catch (reason) {
      if (requestVersion !== version.current) return;
      const projected = projectError(reason, label);
      setItems([]);
      setCount(null);
      setNextBeforeId(null);
      setError(projected);
      setStatus(projected.status);
    }
  }, [enabled, label, loader, scope]);
  const loadMore = useCallback(async () => {
    if (!enabled || nextBeforeId === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await loader(scope, { beforeId: nextBeforeId, limit: 50 }, {});
      setItems((current) => merge(current, page.items, keyOf));
      setCount(page.count);
      setNextBeforeId(page.next_before_id);
    } catch (reason) {
      setError(projectError(reason, label));
    } finally {
      setLoadingMore(false);
    }
  }, [enabled, keyOf, label, loader, loadingMore, nextBeforeId, scope]);
  const upsert = useCallback(
    (item: T) => setItems((current) => merge(current, [item], keyOf)),
    [keyOf],
  );
  useEffect(() => {
    if (!enabled) {
      setStatus("unavailable");
      setItems([]);
      return;
    }
    void load();
    return () => {
      version.current += 1;
    };
  }, [enabled, load]);
  return { status, items, count, nextBeforeId, loadingMore, error, reload: load, loadMore, upsert };
}

function identityCapability(context: EnterpriseContext): EnterpriseCapability | null {
  return context.capabilities.identity_federation ?? context.capabilities.sso ?? null;
}
export function useEnterpriseIdentityCenter(
  scope: EnterpriseScope,
  context: EnterpriseContext,
  readiness?: EnterpriseScimReadinessReport | null,
) {
  const stableScope = useMemo(
    () => ({ tenantId: scope.tenantId, datasetId: scope.datasetId, actorToken: scope.actorToken }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const capability = identityCapability(context);
  const readable = capability?.state === "ready" || capability?.state === "limited";
  const scimDataPlane = useMemo(() => projectScimDataPlaneEvidence(readiness), [readiness]);
  const domains = useResource(
    stableScope,
    readable,
    "可信域名",
    fetchIdentityDomains,
    (item: EnterpriseVerifiedDomain) => item.id,
  );
  const providers = useResource(
    stableScope,
    readable,
    "身份提供商",
    fetchIdentityProviders,
    (item: EnterpriseIdentityProvider) => item.id,
  );
  const scimTokens = useResource(
    stableScope,
    readable,
    "SCIM token",
    fetchScimTokens,
    (item: EnterpriseScimToken) => item.id,
  );
  const oidcRuntime = useMemo(
    () => projectOidcRuntimeEvidence(readiness, providers.items),
    [providers.items, readiness],
  );
  const reloadAll = useCallback(async () => {
    await Promise.all([domains.reload(), providers.reload(), scimTokens.reload()]);
  }, [domains, providers, scimTokens]);
  return {
    capability,
    readable,
    scimDataPlane,
    oidcRuntime,
    domains,
    providers,
    scimTokens,
    reloadAll,
  };
}
