import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import {
  fetchDatasetAccessSummary,
  fetchEnterpriseAuditEvents,
  fetchEnterpriseContext,
  fetchEnterpriseMembers,
  patchEnterpriseMember,
  restoreEnterpriseMember,
  suspendEnterpriseMember,
  updateEnterpriseMemberRole,
} from "../api/enterpriseAdminApi";
import type {
  DatasetAccessSummary,
  EnterpriseAuditEvent,
  EnterpriseAuditEventListResponse,
  EnterpriseCapabilities,
  EnterpriseContext,
  EnterpriseLoadState,
  EnterpriseMember,
  EnterpriseMemberListResponse,
  EnterpriseMemberMutationRequest,
  EnterpriseMemberMutationResponse,
  EnterpriseResourceError,
  EnterpriseScope,
  TenantRole,
} from "../model/enterpriseAdminModel";

export interface EnterpriseAdminWorkspaceOptions {
  /** Explicitly enable/disable member mutations; otherwise read context.capabilities. */
  memberMutationEnabled?: boolean;
  /** Plural alias for callers that name the capability after its context key. */
  memberMutationsEnabled?: boolean;
  /** Explicitly enable/disable tenant audit reads; otherwise read context.capabilities. */
  auditEnabled?: boolean;
  /** Optional capability map supplied by a page-level capability gate. */
  capabilities?: EnterpriseCapabilities;
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

function errorDetail(error: ApiError): string | undefined {
  const detail = error.message.trim();
  return detail || undefined;
}

export function projectEnterpriseError(error: unknown): EnterpriseResourceError {
  if (error instanceof ApiError) {
    const detail = errorDetail(error);
    if (error.status === 401) {
      return {
        state: "unauthorized",
        title: "身份已失效",
        description: "重新连接企业身份后再访问此工作面",
        canRetry: false,
        status: error.status,
        detail,
      };
    }
    if (error.status === 403) {
      return {
        state: "forbidden",
        title: "没有查看企业管理数据的权限",
        description: "当前身份未获得企业目录读取权限",
        canRetry: false,
        status: error.status,
        detail,
      };
    }
    if (error.status === 409) {
      return {
        state: "conflict",
        title: "成员信息发生变化",
        description: "成员信息已发生变化，请刷新后重试",
        canRetry: true,
        status: error.status,
        detail,
      };
    }
    if (error.status === 422) {
      return {
        state: "error",
        title: "成员变更请求无效",
        description: detail ?? "后端拒绝了成员变更请求",
        canRetry: false,
        status: error.status,
        detail,
      };
    }
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout") {
      return {
        state: "unavailable",
        title: "企业服务暂不可用",
        description: "数据库或企业目录服务尚未就绪",
        canRetry: true,
        status: error.status,
        detail,
      };
    }
  }
  return {
    state: "error",
    title: "企业管理事实读取失败",
    description: "服务没有返回可安全展示的企业目录结果",
    canRetry: true,
    detail: error instanceof Error ? error.message : undefined,
  };
}

function mergeMemberPages(
  current: EnterpriseMember[],
  next: EnterpriseMember[],
): EnterpriseMember[] {
  const seen = new Set<number>();
  const merged: EnterpriseMember[] = [];
  for (const member of [...current, ...next]) {
    if (seen.has(member.membership_id)) continue;
    seen.add(member.membership_id);
    merged.push(member);
  }
  return merged;
}

function mergeAuditPages(
  current: EnterpriseAuditEvent[],
  next: EnterpriseAuditEvent[],
): EnterpriseAuditEvent[] {
  const seen = new Set<number>();
  const merged: EnterpriseAuditEvent[] = [];
  for (const event of [...current, ...next]) {
    if (seen.has(event.sequence)) continue;
    seen.add(event.sequence);
    merged.push(event);
  }
  return merged;
}

function capabilityIsReady(
  capabilities: EnterpriseCapabilities | null | undefined,
  key: string,
): boolean {
  return capabilities?.[key]?.state === "ready";
}

function loadStateForResourceError(error: EnterpriseResourceError): EnterpriseLoadState {
  return error.state === "conflict" ? "error" : error.state;
}

export interface EnterpriseAdminWorkspace {
  status: EnterpriseLoadState;
  context: EnterpriseContext | null;
  members: EnterpriseMemberListResponse | null;
  membersLoading: boolean;
  membersLoadingMore: boolean;
  access: DatasetAccessSummary | null;
  membersError: EnterpriseResourceError | null;
  membersLoadMoreError: EnterpriseResourceError | null;
  accessError: EnterpriseResourceError | null;
  memberMutationLoading: boolean;
  memberMutationError: EnterpriseResourceError | null;
  memberMutation: {
    loading: boolean;
    error: EnterpriseResourceError | null;
  };
  audit: EnterpriseAuditEventListResponse | null;
  auditEvents: EnterpriseAuditEvent[];
  auditLoading: boolean;
  auditLoadingMore: boolean;
  auditError: EnterpriseResourceError | null;
  loadMoreMembers: () => Promise<void>;
  updateMember: (accountId: string, payload: EnterpriseMemberMutationRequest) => Promise<boolean>;
  updateMemberRole: (
    accountId: string,
    role: TenantRole,
    reason: string,
    expectedRevision: number,
  ) => Promise<boolean>;
  suspendMember: (accountId: string, reason: string, expectedRevision: number) => Promise<boolean>;
  restoreMember: (accountId: string, reason: string, expectedRevision: number) => Promise<boolean>;
  loadMoreAudit: () => Promise<void>;
  reload: () => Promise<void>;
}

export function useEnterpriseAdminWorkspace(
  scope: EnterpriseScope,
  options: EnterpriseAdminWorkspaceOptions = {},
): EnterpriseAdminWorkspace {
  const tenantId = scope.tenantId;
  const datasetId = scope.datasetId?.trim() || undefined;
  const actorToken = scope.actorToken;
  const hasIdentity = Boolean(actorToken.trim());
  const explicitMemberMutationEnabled =
    options.memberMutationEnabled ?? options.memberMutationsEnabled;
  const explicitAuditEnabled = options.auditEnabled;
  const suppliedMemberMutationCapability = capabilityIsReady(
    options.capabilities,
    "member_mutations",
  );
  const suppliedAuditCapability = capabilityIsReady(options.capabilities, "tenant_audit");
  const [status, setStatus] = useState<EnterpriseLoadState>(
    hasIdentity ? "loading" : "identity-missing",
  );
  const [context, setContext] = useState<EnterpriseContext | null>(null);
  const [members, setMembers] = useState<EnterpriseMemberListResponse | null>(null);
  const [membersLoading, setMembersLoading] = useState(hasIdentity);
  const [membersLoadingMore, setMembersLoadingMore] = useState(false);
  const [access, setAccess] = useState<DatasetAccessSummary | null>(null);
  const [membersError, setMembersError] = useState<EnterpriseResourceError | null>(null);
  const [membersLoadMoreError, setMembersLoadMoreError] = useState<EnterpriseResourceError | null>(
    null,
  );
  const [accessError, setAccessError] = useState<EnterpriseResourceError | null>(null);
  const [memberMutationLoading, setMemberMutationLoading] = useState(false);
  const [memberMutationError, setMemberMutationError] = useState<EnterpriseResourceError | null>(
    null,
  );
  const [audit, setAudit] = useState<EnterpriseAuditEventListResponse | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditLoadingMore, setAuditLoadingMore] = useState(false);
  const [auditError, setAuditError] = useState<EnterpriseResourceError | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const membersMoreControllerRef = useRef<AbortController | null>(null);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const auditMoreControllerRef = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);
  const mutationBusyRef = useRef(false);

  const hasSuppliedCapabilities = options.capabilities !== undefined;
  const isMemberMutationEnabled = useCallback(
    (currentContext: EnterpriseContext | null): boolean => {
      if (explicitMemberMutationEnabled !== undefined) return explicitMemberMutationEnabled;
      if (hasSuppliedCapabilities) return suppliedMemberMutationCapability;
      return capabilityIsReady(currentContext?.capabilities, "member_mutations");
    },
    [explicitMemberMutationEnabled, hasSuppliedCapabilities, suppliedMemberMutationCapability],
  );

  const isAuditEnabled = useCallback(
    (currentContext: EnterpriseContext | null): boolean => {
      if (explicitAuditEnabled !== undefined) return explicitAuditEnabled;
      if (hasSuppliedCapabilities) return suppliedAuditCapability;
      return capabilityIsReady(currentContext?.capabilities, "tenant_audit");
    },
    [explicitAuditEnabled, hasSuppliedCapabilities, suppliedAuditCapability],
  );

  const resetWorkspaceState = useCallback((nextStatus: EnterpriseLoadState) => {
    setStatus(nextStatus);
    setContext(null);
    setMembers(null);
    setMembersLoading(false);
    setMembersLoadingMore(false);
    setAccess(null);
    setMembersError(null);
    setMembersLoadMoreError(null);
    setAccessError(null);
    setMemberMutationLoading(false);
    setMemberMutationError(null);
    setAudit(null);
    setAuditLoading(false);
    setAuditLoadingMore(false);
    setAuditError(null);
  }, []);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    membersMoreControllerRef.current?.abort();
    mutationControllerRef.current?.abort();
    auditMoreControllerRef.current?.abort();
    mutationBusyRef.current = false;
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    controllerRef.current = controller;

    if (!actorToken.trim()) {
      resetWorkspaceState("identity-missing");
      return;
    }

    setStatus("loading");
    setContext(null);
    setMembers(null);
    setMembersLoading(true);
    setMembersLoadingMore(false);
    setMembersError(null);
    setMembersLoadMoreError(null);
    setAccess(null);
    setAccessError(null);
    setMemberMutationLoading(false);
    setMemberMutationError(null);
    setAudit(null);
    setAuditLoading(false);
    setAuditLoadingMore(false);
    setAuditError(null);

    const requestScope: EnterpriseScope = { tenantId, datasetId, actorToken };
    const accessRequest = datasetId
      ? fetchDatasetAccessSummary(requestScope, { signal: controller.signal })
      : Promise.resolve<DatasetAccessSummary | null>(null);
    const [contextResult, membersResult, accessResult] = await Promise.allSettled([
      fetchEnterpriseContext(requestScope, { signal: controller.signal }),
      fetchEnterpriseMembers(requestScope, {}, { signal: controller.signal }),
      accessRequest,
    ]);

    if (controller.signal.aborted || sequence !== requestSequence.current) return;

    if (contextResult.status === "rejected") {
      if (isAbort(contextResult.reason)) return;
      const projected = projectEnterpriseError(contextResult.reason);
      resetWorkspaceState(loadStateForResourceError(projected));
      return;
    }

    setContext(contextResult.value);
    setStatus("ready");
    setMembersLoading(false);

    if (membersResult.status === "fulfilled") {
      setMembers(membersResult.value);
      setMembersError(null);
    } else if (!isAbort(membersResult.reason)) {
      setMembers(null);
      setMembersError(projectEnterpriseError(membersResult.reason));
    }

    if (accessResult.status === "fulfilled") {
      if (accessResult.value) {
        setAccess(accessResult.value);
      } else {
        setAccess(null);
      }
      setAccessError(null);
    } else if (!isAbort(accessResult.reason)) {
      setAccess(null);
      setAccessError(projectEnterpriseError(accessResult.reason));
    }

    if (!isAuditEnabled(contextResult.value)) return;

    setAuditLoading(true);
    setAuditError(null);
    try {
      const auditResult = await fetchEnterpriseAuditEvents(
        requestScope,
        {},
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      setAudit({
        ...auditResult,
        items: mergeAuditPages([], auditResult.items),
      });
      setAuditError(null);
    } catch (error) {
      if (controller.signal.aborted || sequence !== requestSequence.current || isAbort(error))
        return;
      setAudit(null);
      setAuditError(projectEnterpriseError(error));
    } finally {
      if (!controller.signal.aborted && sequence === requestSequence.current) {
        setAuditLoading(false);
      }
    }
  }, [actorToken, datasetId, isAuditEnabled, resetWorkspaceState, tenantId]);

  const loadMoreMembers = useCallback(async () => {
    const beforeId = members?.next_before_id;
    if (!members || beforeId == null || membersLoadingMore || !actorToken.trim()) return;

    membersMoreControllerRef.current?.abort();
    const controller = new AbortController();
    membersMoreControllerRef.current = controller;
    const sequence = requestSequence.current;
    setMembersLoadingMore(true);
    setMembersLoadMoreError(null);

    try {
      const nextPage = await fetchEnterpriseMembers(
        { tenantId, datasetId, actorToken },
        { beforeId, limit: 50 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      setMembers((current) =>
        current
          ? {
              ...nextPage,
              items: mergeMemberPages(current.items, nextPage.items),
            }
          : nextPage,
      );
    } catch (error) {
      if (controller.signal.aborted || sequence !== requestSequence.current || isAbort(error))
        return;
      setMembersLoadMoreError(projectEnterpriseError(error));
    } finally {
      if (!controller.signal.aborted && sequence === requestSequence.current) {
        setMembersLoadingMore(false);
      }
    }
  }, [actorToken, datasetId, members, membersLoadingMore, tenantId]);

  const mutateMember = useCallback(
    async (
      operation: (
        requestScope: EnterpriseScope,
        options: { signal: AbortSignal },
      ) => Promise<EnterpriseMemberMutationResponse>,
    ): Promise<boolean> => {
      if (!actorToken.trim() || !isMemberMutationEnabled(context) || mutationBusyRef.current) {
        return false;
      }

      mutationControllerRef.current?.abort();
      const controller = new AbortController();
      mutationControllerRef.current = controller;
      const sequence = requestSequence.current;
      mutationBusyRef.current = true;
      setMemberMutationLoading(true);
      setMemberMutationError(null);

      try {
        const response = await operation(
          { tenantId, datasetId, actorToken },
          { signal: controller.signal },
        );
        if (controller.signal.aborted || sequence !== requestSequence.current) return false;
        setMembers((current) => {
          if (!current) return current;
          return {
            ...current,
            items: current.items.map((member) =>
              member.account_id === response.member.account_id ||
              member.membership_id === response.member.membership_id
                ? response.member
                : member,
            ),
          };
        });
        setMemberMutationError(null);
        return true;
      } catch (error) {
        if (controller.signal.aborted || sequence !== requestSequence.current || isAbort(error)) {
          return false;
        }
        setMemberMutationError(projectEnterpriseError(error));
        return false;
      } finally {
        if (mutationControllerRef.current === controller) mutationControllerRef.current = null;
        if (sequence === requestSequence.current) {
          mutationBusyRef.current = false;
          setMemberMutationLoading(false);
        }
      }
    },
    [actorToken, context, datasetId, isMemberMutationEnabled, tenantId],
  );

  const updateMember = useCallback(
    (accountId: string, payload: EnterpriseMemberMutationRequest): Promise<boolean> =>
      mutateMember((requestScope, requestOptions) =>
        patchEnterpriseMember(requestScope, accountId, payload, requestOptions),
      ),
    [mutateMember],
  );

  const updateMemberRole = useCallback(
    (
      accountId: string,
      role: TenantRole,
      reason: string,
      expectedRevision: number,
    ): Promise<boolean> =>
      mutateMember((requestScope, requestOptions) =>
        updateEnterpriseMemberRole(
          requestScope,
          accountId,
          role,
          reason,
          expectedRevision,
          requestOptions,
        ),
      ),
    [mutateMember],
  );

  const suspendMember = useCallback(
    (accountId: string, reason: string, expectedRevision: number): Promise<boolean> =>
      mutateMember((requestScope, requestOptions) =>
        suspendEnterpriseMember(requestScope, accountId, reason, expectedRevision, requestOptions),
      ),
    [mutateMember],
  );

  const restoreMember = useCallback(
    (accountId: string, reason: string, expectedRevision: number): Promise<boolean> =>
      mutateMember((requestScope, requestOptions) =>
        restoreEnterpriseMember(requestScope, accountId, reason, expectedRevision, requestOptions),
      ),
    [mutateMember],
  );

  const loadMoreAudit = useCallback(async () => {
    const beforeSequence = audit?.next_before_sequence;
    if (
      !audit ||
      beforeSequence == null ||
      auditLoadingMore ||
      !actorToken.trim() ||
      !isAuditEnabled(context)
    ) {
      return;
    }

    auditMoreControllerRef.current?.abort();
    const controller = new AbortController();
    auditMoreControllerRef.current = controller;
    const sequence = requestSequence.current;
    setAuditLoadingMore(true);
    setAuditError(null);

    try {
      const nextPage = await fetchEnterpriseAuditEvents(
        { tenantId, datasetId, actorToken },
        { beforeSequence, limit: 50 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== requestSequence.current) return;
      setAudit((current) =>
        current
          ? {
              ...nextPage,
              items: mergeAuditPages(current.items, nextPage.items),
            }
          : nextPage,
      );
    } catch (error) {
      if (controller.signal.aborted || sequence !== requestSequence.current || isAbort(error))
        return;
      setAuditError(projectEnterpriseError(error));
    } finally {
      if (!controller.signal.aborted && sequence === requestSequence.current) {
        setAuditLoadingMore(false);
      }
    }
  }, [actorToken, audit, auditLoadingMore, context, datasetId, isAuditEnabled, tenantId]);

  useEffect(() => {
    void load();
    return () => {
      controllerRef.current?.abort();
      membersMoreControllerRef.current?.abort();
      mutationControllerRef.current?.abort();
      auditMoreControllerRef.current?.abort();
    };
  }, [load]);

  return {
    status,
    context,
    members,
    membersLoading,
    membersLoadingMore,
    access,
    membersError,
    membersLoadMoreError,
    accessError,
    memberMutationLoading,
    memberMutationError,
    memberMutation: { loading: memberMutationLoading, error: memberMutationError },
    audit,
    auditEvents: audit?.items ?? [],
    auditLoading,
    auditLoadingMore,
    auditError,
    loadMoreMembers,
    updateMember,
    updateMemberRole,
    suspendMember,
    restoreMember,
    loadMoreAudit,
    reload: load,
  };
}
