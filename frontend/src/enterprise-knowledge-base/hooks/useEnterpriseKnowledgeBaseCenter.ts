import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  createApplicationReference,
  createKnowledgeBaseIdempotencyKey,
  fetchEnterpriseKnowledgeBaseDependencies,
  fetchEnterpriseKnowledgeBaseDetail,
  fetchEnterpriseKnowledgeBases,
  removeApplicationReference,
  transferKnowledgeBaseOwnership,
  type KnowledgeBaseListQuery,
  type RemoveApplicationReferenceInput,
  type WorkspaceTransferInput,
} from "../api/enterpriseKnowledgeBaseApi";
import type {
  KnowledgeBaseDetail,
  KnowledgeBaseMutationResponse,
  KnowledgeBasePage,
} from "../enterpriseKnowledgeBaseModel";

export type KnowledgeBaseLoadStatus =
  "identity-missing" | "loading" | "ready" | "unavailable" | "error";

export interface KnowledgeBaseResourceError {
  title: string;
  description: string;
  canRetry: boolean;
  status?: number;
  code?: string;
}

export interface KnowledgeBaseMutationState {
  saving: boolean;
  error: KnowledgeBaseResourceError | null;
  success: string | null;
  outcome: KnowledgeBaseMutationResponse | null;
}

export interface KnowledgeBaseCenterFilters {
  workspaceId?: string;
  status?: string;
  keyword?: string;
  limit?: number;
}

export interface AddApplicationReferenceInput {
  appId: string;
  datasetId: string;
  reason: string;
}

export interface RemoveApplicationReferenceRequest extends RemoveApplicationReferenceInput {
  appId: string;
  datasetId: string;
}

export interface KnowledgeBaseCenterResult {
  status: KnowledgeBaseLoadStatus;
  page: KnowledgeBasePage | null;
  error: KnowledgeBaseResourceError | null;
  selected: KnowledgeBaseDetail | null;
  selectedLoading: boolean;
  detailError: KnowledgeBaseResourceError | null;
  moreLoading: boolean;
  moreError: KnowledgeBaseResourceError | null;
  mutation: KnowledgeBaseMutationState;
  reload: () => Promise<void>;
  loadMore: () => Promise<void>;
  openKnowledgeBase: (datasetId: string) => Promise<void>;
  closeKnowledgeBase: () => void;
  retryMutation: () => Promise<boolean>;
  clearMutation: () => void;
  addApplicationReference: (
    input: AddApplicationReferenceInput,
  ) => Promise<KnowledgeBaseMutationResponse | null>;
  removeApplicationReference: (
    input: RemoveApplicationReferenceRequest,
  ) => Promise<KnowledgeBaseMutationResponse | null>;
  transferOwnership: (
    datasetId: string,
    input: WorkspaceTransferInput,
  ) => Promise<KnowledgeBaseMutationResponse | null>;
}

function apiCode(error: ApiError): string | null {
  const body = error.body;
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export function projectKnowledgeBaseError(error: unknown): KnowledgeBaseResourceError {
  if (error instanceof ApiError) {
    const code = apiCode(error) ?? undefined;
    if (error.status === 401) {
      return {
        title: "企业身份已失效",
        description: "重新连接企业身份后再读取知识库真账。",
        canRetry: false,
        status: error.status,
        code,
      };
    }
    if (error.status === 403) {
      return {
        title: "没有执行此知识库操作的权限",
        description: "需要租户所有者、管理员或该知识库的有效管理权限。",
        canRetry: false,
        status: error.status,
        code,
      };
    }
    if (error.status === 404) {
      return {
        title: "知识库资源不存在",
        description: "资源可能已归档、移除，或不属于当前租户。",
        canRetry: false,
        status: error.status,
        code,
      };
    }
    if (error.status === 409) {
      const messages: Record<string, string> = {
        knowledge_base_revision_conflict: "知识库事实已变化，请刷新最新 revision 后重试。",
        dataset_workspace_transfer_approval_required: "当前所有权转移命中审批策略，需要等待审批。",
        dataset_archive_blocked: "当前知识库存在活跃依赖，审批不能绕过归档保护。",
        application_reference_exists: "该 Application 已存在有效知识库引用。",
        knowledge_base_idempotency_conflict: "本次操作标识已用于不同请求，请刷新后重新操作。",
      };
      return {
        title: "知识库状态已变化",
        description: (code && messages[code]) || "服务端拒绝了过期或冲突的知识库变更。",
        canRetry: code === "knowledge_base_revision_conflict",
        status: error.status,
        code,
      };
    }
    if (error.status === 503) {
      return {
        title: "知识库 Registry 尚未就绪",
        description:
          code === "knowledge_base_registry_migration_required" ||
          code === "enterprise_registry_migration_required"
            ? "需要先完成 0028_enterprise_knowledge_base_registry 数据库迁移。"
            : "Registry 服务暂不可用，未展示或写入模拟知识库。",
        canRetry: true,
        status: error.status,
        code,
      };
    }
    if (error.kind === "network" || error.kind === "timeout") {
      return {
        title: "无法连接知识库 Registry",
        description: "服务端未返回知识库事实；检查连接后可重新读取。",
        canRetry: true,
        status: error.status,
        code,
      };
    }
  }
  return {
    title: "知识库 Registry 不可用",
    description: error instanceof Error ? error.message : "服务端未返回可验证的知识库事实。",
    canRetry: true,
  };
}

function mergeById<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const merged = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) merged.set(item.id, item);
  return [...merged.values()];
}

type MutationOperation = (idempotencyKey: string) => Promise<KnowledgeBaseMutationResponse>;

export function useEnterpriseKnowledgeBaseCenter(
  scope: EnterpriseScope,
  filters: KnowledgeBaseCenterFilters = {},
): KnowledgeBaseCenterResult {
  const requestScope = useMemo(
    () => ({ tenantId: scope.tenantId, datasetId: scope.datasetId, actorToken: scope.actorToken }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const query = useMemo<KnowledgeBaseListQuery>(
    () => ({
      ...(filters.workspaceId?.trim() ? { workspaceId: filters.workspaceId.trim() } : {}),
      ...(filters.status?.trim() ? { status: filters.status.trim() } : {}),
      ...(filters.keyword?.trim() ? { keyword: filters.keyword.trim() } : {}),
      limit: filters.limit ?? 50,
    }),
    [filters.keyword, filters.limit, filters.status, filters.workspaceId],
  );
  const [status, setStatus] = useState<KnowledgeBaseLoadStatus>(
    requestScope.actorToken.trim() ? "loading" : "identity-missing",
  );
  const [page, setPage] = useState<KnowledgeBasePage | null>(null);
  const [error, setError] = useState<KnowledgeBaseResourceError | null>(null);
  const [selected, setSelected] = useState<KnowledgeBaseDetail | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [detailError, setDetailError] = useState<KnowledgeBaseResourceError | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState<KnowledgeBaseResourceError | null>(null);
  const [mutation, setMutation] = useState<KnowledgeBaseMutationState>({
    saving: false,
    error: null,
    success: null,
    outcome: null,
  });
  const listControllerRef = useRef<AbortController | null>(null);
  const moreControllerRef = useRef<AbortController | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const sequenceRef = useRef(0);
  const pendingMutationRef = useRef<{
    operation: MutationOperation;
    key: string;
    success: string;
  } | null>(null);

  const reload = useCallback(async () => {
    if (!requestScope.actorToken.trim()) {
      listControllerRef.current?.abort();
      setStatus("identity-missing");
      setPage(null);
      setError(null);
      return;
    }
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const sequence = ++sequenceRef.current;
    setStatus("loading");
    setError(null);
    setMoreError(null);
    try {
      const result = await fetchEnterpriseKnowledgeBases(requestScope, query, {
        signal: controller.signal,
      });
      if (controller.signal.aborted || sequence !== sequenceRef.current) return;
      setPage(result);
      setStatus("ready");
    } catch (reason) {
      if (controller.signal.aborted || sequence !== sequenceRef.current) return;
      setPage(null);
      const projected = projectKnowledgeBaseError(reason);
      setError(projected);
      setStatus(projected.status === 503 ? "unavailable" : "error");
    }
  }, [query, requestScope]);

  const loadMore = useCallback(async () => {
    const cursor = page?.next_cursor;
    if (!cursor || moreLoading || !requestScope.actorToken.trim()) return;
    moreControllerRef.current?.abort();
    const controller = new AbortController();
    moreControllerRef.current = controller;
    setMoreLoading(true);
    setMoreError(null);
    try {
      const result = await fetchEnterpriseKnowledgeBases(
        requestScope,
        { ...query, cursor },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setPage((current) =>
        current
          ? {
              ...current,
              items: mergeById(current.items, result.items),
              next_cursor: result.next_cursor,
            }
          : result,
      );
    } catch (reason) {
      if (!controller.signal.aborted) setMoreError(projectKnowledgeBaseError(reason));
    } finally {
      if (!controller.signal.aborted) setMoreLoading(false);
    }
  }, [moreLoading, page?.next_cursor, query, requestScope]);

  const openKnowledgeBase = useCallback(
    async (datasetId: string) => {
      const normalized = datasetId.trim();
      if (!normalized || !requestScope.actorToken.trim()) return;
      detailControllerRef.current?.abort();
      const controller = new AbortController();
      detailControllerRef.current = controller;
      setSelectedLoading(true);
      setDetailError(null);
      try {
        const [detail, dependencies] = await Promise.all([
          fetchEnterpriseKnowledgeBaseDetail(requestScope, normalized, {
            signal: controller.signal,
          }),
          fetchEnterpriseKnowledgeBaseDependencies(requestScope, normalized, {
            signal: controller.signal,
          }),
        ]);
        if (controller.signal.aborted) return;
        setSelected({ ...detail, dependencies });
      } catch (reason) {
        if (!controller.signal.aborted) setDetailError(projectKnowledgeBaseError(reason));
      } finally {
        if (!controller.signal.aborted) setSelectedLoading(false);
      }
    },
    [requestScope],
  );

  const closeKnowledgeBase = useCallback(() => {
    detailControllerRef.current?.abort();
    setSelected(null);
    setDetailError(null);
    setSelectedLoading(false);
  }, []);

  const runMutation = useCallback(
    async (operation: MutationOperation, success: string, existingKey?: string) => {
      const key = existingKey ?? createKnowledgeBaseIdempotencyKey();
      pendingMutationRef.current = { operation, key, success };
      setMutation({ saving: true, error: null, success: null, outcome: null });
      try {
        const outcome = await operation(key);
        pendingMutationRef.current = null;
        await reload();
        const selectedId = selected?.knowledge_base.id;
        if (selectedId) await openKnowledgeBase(selectedId);
        setMutation({
          saving: false,
          error: null,
          success: outcome.state === "approval_required" ? null : success,
          outcome,
        });
        return outcome;
      } catch (reason) {
        const projected = projectKnowledgeBaseError(reason);
        if (projected.code === "dataset_workspace_transfer_approval_required") {
          const outcome: KnowledgeBaseMutationResponse = {
            state: "approval_required",
            knowledge_base: null,
            approval_request_id: null,
            message: null,
          };
          pendingMutationRef.current = null;
          setMutation({ saving: false, error: null, success: null, outcome });
          return outcome;
        }
        setMutation({ saving: false, error: projected, success: null, outcome: null });
        return null;
      }
    },
    [openKnowledgeBase, reload, selected],
  );

  const retryMutation = useCallback(() => {
    const pending = pendingMutationRef.current;
    return pending
      ? runMutation(pending.operation, pending.success, pending.key).then(
          (result) => result !== null,
        )
      : Promise.resolve(false);
  }, [runMutation]);

  const clearMutation = useCallback(() => {
    pendingMutationRef.current = null;
    setMutation({ saving: false, error: null, success: null, outcome: null });
  }, []);

  const addReference = useCallback(
    (input: AddApplicationReferenceInput) =>
      runMutation(
        (key) =>
          createApplicationReference(
            requestScope,
            input.appId,
            input.datasetId,
            { reason: input.reason },
            { idempotencyKey: key },
          ),
        "Application 引用已添加",
      ),
    [requestScope, runMutation],
  );

  const removeReference = useCallback(
    (input: RemoveApplicationReferenceRequest) =>
      runMutation(
        (key) =>
          removeApplicationReference(
            requestScope,
            input.appId,
            input.datasetId,
            { expectedRevision: input.expectedRevision, reason: input.reason },
            { idempotencyKey: key },
          ),
        "Application 引用已移除",
      ),
    [requestScope, runMutation],
  );

  const transferOwnership = useCallback(
    (datasetId: string, input: WorkspaceTransferInput) =>
      runMutation(
        (key) =>
          transferKnowledgeBaseOwnership(requestScope, datasetId, input, { idempotencyKey: key }),
        "知识库所有权已转移",
      ),
    [requestScope, runMutation],
  );

  useEffect(() => {
    void reload();
    return () => {
      listControllerRef.current?.abort();
      moreControllerRef.current?.abort();
      detailControllerRef.current?.abort();
    };
  }, [reload]);

  return {
    status,
    page,
    error,
    selected,
    selectedLoading,
    detailError,
    moreLoading,
    moreError,
    mutation,
    reload,
    loadMore,
    openKnowledgeBase,
    closeKnowledgeBase,
    retryMutation,
    clearMutation,
    addApplicationReference: addReference,
    removeApplicationReference: removeReference,
    transferOwnership,
  };
}
