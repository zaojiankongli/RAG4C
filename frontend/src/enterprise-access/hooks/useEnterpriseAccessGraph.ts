import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type {
  EnterpriseCapabilities,
  EnterpriseCapability,
  EnterpriseCapabilityState,
  EnterpriseScope,
} from "../../enterprise-admin/model";
import {
  fetchDatasetAccessGrants,
  fetchEnterpriseGroupMembers,
  fetchEnterpriseGroups,
  fetchEnterpriseInvitations,
  fetchOrganizationUnits,
  type EnterpriseAccessListQuery,
  type EnterpriseAccessRequestOptions,
} from "../api/enterpriseAccessApi";
import type {
  DatasetAccessGrant,
  EnterpriseAccessCursor,
  EnterpriseAccessPage,
  EnterpriseAccessResourceError,
  EnterpriseAccessResourceStatus,
  EnterpriseGroup,
  EnterpriseGroupMember,
  EnterpriseInvitation,
  OrganizationUnit,
} from "../enterpriseAccessModel";

const PAGE_SIZE = 50;

const CAPABILITY_FALLBACKS: Record<string, EnterpriseCapability> = {
  organization_units: {
    state: "unavailable",
    label: "组织架构",
    reason: "尚未接入企业组织目录",
  },
  user_groups: {
    state: "unavailable",
    label: "用户组",
    reason: "用户组能力尚未接入",
  },
  dataset_acl: {
    state: "unavailable",
    label: "知识库 ACL",
    reason: "知识库 ACL 能力尚未接入",
  },
  invitations: {
    state: "unavailable",
    label: "成员邀请",
    reason: "邀请流程尚未接入",
  },
};

export interface EnterpriseAccessCollection<T> {
  status: EnterpriseAccessResourceStatus;
  capabilityState: EnterpriseCapabilityState;
  capabilityReason: string | null;
  items: T[];
  count: number | null;
  nextBeforeId: EnterpriseAccessCursor | null;
  loadingMore: boolean;
  error: EnterpriseAccessResourceError | null;
  reload: () => Promise<void>;
  loadMore: () => Promise<void>;
  upsert: (item: T) => void;
}

export interface EnterpriseAccessGraphWorkspace {
  organizationUnits: EnterpriseAccessCollection<OrganizationUnit>;
  groups: EnterpriseAccessCollection<EnterpriseGroup>;
  selectedGroupId: string | null;
  selectedGroup: EnterpriseGroup | null;
  selectGroup: (groupId: string | null) => void;
  groupMembers: EnterpriseAccessCollection<EnterpriseGroupMember>;
  invitations: EnterpriseAccessCollection<EnterpriseInvitation>;
  accessGrants: EnterpriseAccessCollection<DatasetAccessGrant>;
  reloadAll: () => Promise<void>;
}

type PageLoader<T> = (
  scope: EnterpriseScope,
  query: EnterpriseAccessListQuery,
  options: EnterpriseAccessRequestOptions,
) => Promise<EnterpriseAccessPage<T>>;

interface KeysetResourceOptions<T> {
  scope: EnterpriseScope;
  capability: EnterpriseCapability;
  enabled: boolean;
  idleReason?: string | null;
  loader: PageLoader<T>;
  keyOf: (item: T) => string;
}

function capabilityFor(capabilities: EnterpriseCapabilities | null | undefined, key: string) {
  return capabilities?.[key] ?? CAPABILITY_FALLBACKS[key];
}

function isReadableCapability(state: EnterpriseCapabilityState): boolean {
  return state === "ready" || state === "limited";
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function apiErrorCode(error: ApiError): string | undefined {
  const body = recordValue(error.body);
  const detail = recordValue(body?.detail);
  const code = detail?.code ?? body?.code;
  return typeof code === "string" ? code : undefined;
}

export function projectEnterpriseAccessError(
  error: unknown,
  resourceLabel: string,
): EnterpriseAccessResourceError {
  if (error instanceof ApiError) {
    const code = apiErrorCode(error);
    if (code?.includes("migration_required")) {
      return {
        state: "migration-required",
        title: "访问图谱数据库版本尚未就绪",
        description: "完成企业访问图谱迁移后再重新读取",
        canRetry: true,
        status: error.status,
        code,
        detail: error.message,
      };
    }
    if (error.status === 401) {
      return {
        state: "unauthorized",
        title: `${resourceLabel}身份已失效`,
        description: `重新连接企业身份后再读取${resourceLabel}`,
        canRetry: false,
        status: error.status,
        detail: error.message,
      };
    }
    if (error.status === 403) {
      return {
        state: "forbidden",
        title: `没有读取${resourceLabel}的权限`,
        description: `当前身份未获得${resourceLabel}读取权限`,
        canRetry: false,
        status: error.status,
        detail: error.message,
      };
    }
    if (
      error.status === 404 ||
      error.status === 503 ||
      error.kind === "network" ||
      error.kind === "timeout"
    ) {
      return {
        state: "unavailable",
        title: `${resourceLabel} 服务暂不可用`,
        description:
          error.status === 404 ? "服务端尚未挂载该只读能力" : "数据库或企业目录服务尚未就绪",
        canRetry: true,
        status: error.status,
        detail: error.message,
      };
    }
  }
  return {
    state: "error",
    title: `${resourceLabel}读取失败`,
    description: "服务没有返回可安全展示的企业访问事实",
    canRetry: true,
    detail: error instanceof Error ? error.message : undefined,
  };
}

function mergePages<T>(current: T[], next: T[], keyOf: (item: T) => string): T[] {
  const seen = new Set<string>();
  const result: T[] = [];
  for (const item of [...current, ...next]) {
    const key = keyOf(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function useKeysetResource<T>({
  scope,
  capability,
  enabled,
  idleReason = null,
  loader,
  keyOf,
}: KeysetResourceOptions<T>): EnterpriseAccessCollection<T> {
  const [status, setStatus] = useState<EnterpriseAccessResourceStatus>("idle");
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const [nextBeforeId, setNextBeforeId] = useState<EnterpriseAccessCursor | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<EnterpriseAccessResourceError | null>(null);
  const requestVersion = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadInitial = useCallback(
    async (signal?: AbortSignal) => {
      if (!enabled || !isReadableCapability(capability.state) || !scope.actorToken.trim()) return;
      const version = ++requestVersion.current;
      setStatus("loading");
      setLoadingMore(false);
      setError(null);
      try {
        const page = await loader(scope, { limit: PAGE_SIZE }, { signal });
        if (!mounted.current || version !== requestVersion.current) return;
        setItems(page.items);
        setCount(page.count);
        setNextBeforeId(page.next_before_id);
        setStatus("ready");
      } catch (reason) {
        if (signal?.aborted || !mounted.current || version !== requestVersion.current) return;
        const projected = projectEnterpriseAccessError(reason, capability.label);
        setItems([]);
        setCount(null);
        setNextBeforeId(null);
        setError(projected);
        setStatus(projected.state);
      }
    },
    [capability.label, capability.state, enabled, loader, scope],
  );

  const reload = useCallback(async () => {
    const controller = new AbortController();
    await loadInitial(controller.signal);
  }, [loadInitial]);

  const upsert = useCallback(
    (item: T) => {
      if (!mounted.current) return;
      const itemKey = keyOf(item);
      if (!itemKey) return;
      setItems((current) => {
        const index = current.findIndex((candidate) => keyOf(candidate) === itemKey);
        if (index < 0) return [item, ...current];
        return current.map((candidate, candidateIndex) =>
          candidateIndex === index ? item : candidate,
        );
      });
    },
    [keyOf],
  );

  const loadMore = useCallback(async () => {
    if (
      !enabled ||
      !isReadableCapability(capability.state) ||
      !scope.actorToken.trim() ||
      nextBeforeId === null ||
      loadingMore
    ) {
      return;
    }
    const version = requestVersion.current;
    const controller = new AbortController();
    setLoadingMore(true);
    setError(null);
    try {
      const page = await loader(
        scope,
        { beforeId: nextBeforeId, limit: PAGE_SIZE },
        { signal: controller.signal },
      );
      if (!mounted.current || version !== requestVersion.current) return;
      setItems((current) => mergePages(current, page.items, keyOf));
      setCount(page.count);
      setNextBeforeId(page.next_before_id);
      setStatus("ready");
    } catch (reason) {
      if (!mounted.current || version !== requestVersion.current) return;
      setError(projectEnterpriseAccessError(reason, capability.label));
    } finally {
      if (mounted.current && version === requestVersion.current) setLoadingMore(false);
    }
  }, [
    capability.label,
    capability.state,
    enabled,
    keyOf,
    loader,
    loadingMore,
    nextBeforeId,
    scope,
  ]);

  useEffect(() => {
    requestVersion.current += 1;
    if (!enabled || !scope.actorToken.trim()) {
      setStatus("idle");
      setItems([]);
      setCount(null);
      setNextBeforeId(null);
      setLoadingMore(false);
      setError(null);
      return;
    }
    if (!isReadableCapability(capability.state)) {
      setStatus("unavailable");
      setItems([]);
      setCount(null);
      setNextBeforeId(null);
      setLoadingMore(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    void loadInitial(controller.signal);
    return () => controller.abort();
  }, [capability.state, enabled, loadInitial, scope.actorToken]);

  return {
    status,
    capabilityState: capability.state,
    capabilityReason: isReadableCapability(capability.state)
      ? capability.reason || idleReason
      : capability.reason,
    items,
    count,
    nextBeforeId,
    loadingMore,
    error,
    reload,
    loadMore,
    upsert,
  };
}

export function useEnterpriseAccessGraph(
  scope: EnterpriseScope,
  capabilities: EnterpriseCapabilities | null | undefined,
  options: { enabled?: boolean } = {},
): EnterpriseAccessGraphWorkspace {
  const enabled = options.enabled ?? true;
  const stableScope = useMemo<EnterpriseScope>(
    () => ({
      tenantId: scope.tenantId,
      datasetId: scope.datasetId,
      actorToken: scope.actorToken,
    }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const organizationCapability = capabilityFor(capabilities, "organization_units");
  const groupCapability = capabilityFor(capabilities, "user_groups");
  const grantCapability = capabilityFor(capabilities, "dataset_acl");
  const invitationCapability = capabilityFor(capabilities, "invitations");
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  const organizationUnits = useKeysetResource({
    scope: stableScope,
    capability: organizationCapability,
    enabled,
    loader: fetchOrganizationUnits,
    keyOf: (item) => item.id,
  });
  const groups = useKeysetResource({
    scope: stableScope,
    capability: groupCapability,
    enabled,
    loader: fetchEnterpriseGroups,
    keyOf: (item) => item.id,
  });
  const invitations = useKeysetResource({
    scope: stableScope,
    capability: invitationCapability,
    enabled,
    loader: fetchEnterpriseInvitations,
    keyOf: (item) => item.id,
  });
  const accessGrants = useKeysetResource({
    scope: stableScope,
    capability: grantCapability,
    enabled: enabled && Boolean(stableScope.datasetId?.trim()),
    idleReason: stableScope.datasetId?.trim() ? null : "未选择知识库",
    loader: fetchDatasetAccessGrants,
    keyOf: (item) => item.id,
  });

  const groupMemberLoader = useCallback<PageLoader<EnterpriseGroupMember>>(
    (requestScope, query, requestOptions) => {
      if (!selectedGroupId) {
        return Promise.resolve({ items: [], count: 0, next_before_id: null });
      }
      return fetchEnterpriseGroupMembers(requestScope, selectedGroupId, query, requestOptions);
    },
    [selectedGroupId],
  );
  const groupMembers = useKeysetResource({
    scope: stableScope,
    capability: groupCapability,
    enabled: enabled && Boolean(selectedGroupId),
    idleReason: selectedGroupId ? null : "选择用户组后读取真实成员关系",
    loader: groupMemberLoader,
    keyOf: (item) => item.id,
  });

  useEffect(() => {
    if (selectedGroupId && !groups.items.some((group) => group.id === selectedGroupId)) {
      setSelectedGroupId(null);
    }
  }, [groups.items, selectedGroupId]);

  const selectGroup = useCallback((groupId: string | null) => {
    setSelectedGroupId(groupId?.trim() || null);
  }, []);

  const selectedGroup = groups.items.find((group) => group.id === selectedGroupId) ?? null;
  const reloadAll = useCallback(async () => {
    await Promise.all([
      organizationUnits.reload(),
      groups.reload(),
      invitations.reload(),
      accessGrants.reload(),
      selectedGroupId ? groupMembers.reload() : Promise.resolve(),
    ]);
  }, [accessGrants, groupMembers, groups, invitations, organizationUnits, selectedGroupId]);

  return {
    organizationUnits,
    groups,
    selectedGroupId,
    selectedGroup,
    selectGroup,
    groupMembers,
    invitations,
    accessGrants,
    reloadAll,
  };
}
