import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  addWorkspaceMember,
  archiveWorkspace,
  bindWorkspaceDataset,
  createEnterpriseWorkspace,
  createWorkspaceIdempotencyKey,
  fetchEnterpriseWorkspaceDetail,
  fetchEnterpriseWorkspaces,
  fetchWorkspaceDatasets,
  fetchWorkspaceMembers,
  removeWorkspaceDataset,
  removeWorkspaceMember,
  updateEnterpriseWorkspace,
  updateWorkspaceMember,
  type AddWorkspaceMemberInput,
  type BindWorkspaceDatasetInput,
  type CreateWorkspaceInput,
  type UpdateWorkspaceInput,
  type UpdateWorkspaceMemberInput,
  type WorkspaceRevisionInput,
} from "../api/enterpriseWorkspaceApi";
import {
  type EnterpriseWorkspace,
  type WorkspaceDatasetBinding,
  type WorkspaceDetail,
  type WorkspaceMember,
  type WorkspacePage,
} from "../enterpriseWorkspaceModel";

export type WorkspaceLoadStatus = "identity-missing" | "loading" | "ready" | "error";

export const ENTERPRISE_WORKSPACE_STORAGE_KEY = "rag4c.enterprise_workspace_id";

function storedWorkspaceId(): string {
  try {
    return localStorage.getItem(ENTERPRISE_WORKSPACE_STORAGE_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

function storeWorkspaceId(workspaceId: string): void {
  try {
    localStorage.setItem(ENTERPRISE_WORKSPACE_STORAGE_KEY, workspaceId);
  } catch {
    // Selection remains in memory when browser storage is unavailable.
  }
}

function removeStoredWorkspaceId(): void {
  try {
    localStorage.removeItem(ENTERPRISE_WORKSPACE_STORAGE_KEY);
  } catch {
    // Fail open for UI selection only; no authorization fact is stored here.
  }
}

export interface WorkspaceResourceError {
  title: string;
  description: string;
  canRetry: boolean;
  status?: number;
}

export interface WorkspaceMutationState {
  saving: boolean;
  error: WorkspaceResourceError | null;
  success: string | null;
}

type MutationOperation = (idempotencyKey: string) => Promise<unknown>;

function apiCode(error: ApiError): string | null {
  const body = error.body;
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

export function projectWorkspaceError(error: unknown): WorkspaceResourceError {
  if (error instanceof ApiError) {
    const code = apiCode(error);
    if (error.status === 401) {
      return {
        title: "企业身份已失效",
        description: "重新连接企业身份后再读取 Workspace 真账。",
        canRetry: false,
        status: error.status,
      };
    }
    if (error.status === 403) {
      return {
        title: "没有执行此 Workspace 操作的权限",
        description: "由租户所有者、租户管理员或 Workspace 管理角色执行此操作。",
        canRetry: false,
        status: error.status,
      };
    }
    if (error.status === 404) {
      return {
        title: "Workspace 资源不存在",
        description: "资源可能已归档、移除，或不属于当前租户。",
        canRetry: false,
        status: error.status,
      };
    }
    if (error.status === 409) {
      const descriptions: Record<string, string> = {
        workspace_revision_conflict: "Workspace revision 已变化，请刷新事实后重试。",
        workspace_last_owner_required: "不能移除或降级最后一名有效 Workspace 所有者。",
        workspace_default_primary_binding_conflict:
          "默认 Workspace 仍拥有有效主知识库绑定，不能归档。",
        workspace_archived: "已归档 Workspace 不能继续添加成员或知识库绑定。",
        workspace_idempotency_conflict: "本次操作标识已用于不同请求，请刷新后重新操作。",
      };
      return {
        title: "Workspace 状态已变化",
        description: (code && descriptions[code]) || "服务端拒绝了过期或冲突的 Workspace 变更。",
        canRetry: code === "workspace_revision_conflict",
        status: error.status,
      };
    }
    if (error.status === 503) {
      return {
        title: "Workspace 控制面尚未就绪",
        description:
          code === "workspace_migration_required" ||
          code === "enterprise_workspace_migration_required"
            ? "需要先完成 0026_enterprise_workspace_control 数据库迁移。"
            : "Workspace 服务暂不可用，未展示或写入模拟数据。",
        canRetry: true,
        status: error.status,
      };
    }
    if (["network", "timeout"].includes(error.kind)) {
      return {
        title: "无法连接 Workspace 服务",
        description: "网络请求未确认完成，可使用相同操作标识安全重试。",
        canRetry: true,
      };
    }
  }
  return {
    title: "Workspace 操作失败",
    description: "服务没有返回可安全展示的 Workspace 结果。",
    canRetry: true,
  };
}

function mergeByKey<T>(current: T[], incoming: T[], keyOf: (item: T) => string): T[] {
  const merged = new Map(current.map((item) => [keyOf(item), item]));
  for (const item of incoming) merged.set(keyOf(item), item);
  return [...merged.values()];
}

export function useEnterpriseWorkspaceCenter(scope: EnterpriseScope) {
  const requestScope = useMemo(
    () => ({
      tenantId: scope.tenantId,
      datasetId: scope.datasetId,
      actorToken: scope.actorToken,
    }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const [status, setStatus] = useState<WorkspaceLoadStatus>(
    scope.actorToken.trim() ? "loading" : "identity-missing",
  );
  const [page, setPage] = useState<WorkspacePage | null>(null);
  const [error, setError] = useState<WorkspaceResourceError | null>(null);
  const [selected, setSelected] = useState<WorkspaceDetail | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [detailError, setDetailError] = useState<WorkspaceResourceError | null>(null);
  const [workspaceMoreLoading, setWorkspaceMoreLoading] = useState(false);
  const [workspaceMoreError, setWorkspaceMoreError] = useState<WorkspaceResourceError | null>(null);
  const [memberMoreLoading, setMemberMoreLoading] = useState(false);
  const [memberMoreError, setMemberMoreError] = useState<WorkspaceResourceError | null>(null);
  const [datasetMoreLoading, setDatasetMoreLoading] = useState(false);
  const [datasetMoreError, setDatasetMoreError] = useState<WorkspaceResourceError | null>(null);
  const [mutation, setMutation] = useState<WorkspaceMutationState>({
    saving: false,
    error: null,
    success: null,
  });
  const sequenceRef = useRef(0);
  const listControllerRef = useRef<AbortController | null>(null);
  const listMoreControllerRef = useRef<AbortController | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const memberMoreControllerRef = useRef<AbortController | null>(null);
  const datasetMoreControllerRef = useRef<AbortController | null>(null);
  const pendingMutationRef = useRef<{
    operation: MutationOperation;
    key: string;
    success: string;
  } | null>(null);

  const load = useCallback(async () => {
    if (!requestScope.actorToken.trim()) {
      setStatus("identity-missing");
      setPage(null);
      return;
    }
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const sequence = ++sequenceRef.current;
    setStatus("loading");
    setError(null);
    setWorkspaceMoreError(null);
    setWorkspaceMoreLoading(false);
    try {
      const result = await fetchEnterpriseWorkspaces(
        requestScope,
        { status: "all", limit: 100 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== sequenceRef.current) return;
      setPage(result);
      setStatus("ready");
    } catch (reason) {
      if (controller.signal.aborted || sequence !== sequenceRef.current) return;
      setPage(null);
      setError(projectWorkspaceError(reason));
      setStatus("error");
    }
  }, [requestScope]);

  const openWorkspace = useCallback(
    async (workspaceId: string) => {
      detailControllerRef.current?.abort();
      const controller = new AbortController();
      detailControllerRef.current = controller;
      setSelectedLoading(true);
      setDetailError(null);
      setMemberMoreError(null);
      setDatasetMoreError(null);
      setMemberMoreLoading(false);
      setDatasetMoreLoading(false);
      try {
        const [detail, members, datasets] = await Promise.all([
          fetchEnterpriseWorkspaceDetail(requestScope, workspaceId, { signal: controller.signal }),
          fetchWorkspaceMembers(
            requestScope,
            workspaceId,
            { status: "active", limit: 100 },
            { signal: controller.signal },
          ),
          fetchWorkspaceDatasets(
            requestScope,
            workspaceId,
            { status: "active", limit: 100 },
            { signal: controller.signal },
          ),
        ]);
        if (controller.signal.aborted) return;
        setSelected({
          ...detail,
          members,
          datasets,
          authorization_state: detail.authorization_state,
        });
      } catch (reason) {
        if (!controller.signal.aborted) setDetailError(projectWorkspaceError(reason));
      } finally {
        if (!controller.signal.aborted) setSelectedLoading(false);
      }
    },
    [requestScope],
  );

  const loadMoreWorkspaces = useCallback(async () => {
    const cursor = page?.next_cursor;
    if (!cursor || workspaceMoreLoading) return;
    listMoreControllerRef.current?.abort();
    const controller = new AbortController();
    listMoreControllerRef.current = controller;
    setWorkspaceMoreLoading(true);
    setWorkspaceMoreError(null);
    try {
      const next = await fetchEnterpriseWorkspaces(
        requestScope,
        { status: "all", cursor, limit: 100 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setPage((current) =>
        current
          ? {
              ...current,
              items: mergeByKey(current.items, next.items, (item) => item.id),
              count: next.count ?? current.count,
              next_cursor: next.next_cursor,
              evidence: {
                workspace_count: next.evidence.workspace_count ?? current.evidence.workspace_count,
                active_count: next.evidence.active_count ?? current.evidence.active_count,
                default_workspace_id:
                  next.evidence.default_workspace_id ?? current.evidence.default_workspace_id,
                primary_dataset_binding_count:
                  next.evidence.primary_dataset_binding_count ??
                  current.evidence.primary_dataset_binding_count,
                authorization_state: next.evidence.authorization_state,
              },
            }
          : current,
      );
    } catch (reason) {
      if (!controller.signal.aborted) setWorkspaceMoreError(projectWorkspaceError(reason));
    } finally {
      if (!controller.signal.aborted) setWorkspaceMoreLoading(false);
    }
  }, [page?.next_cursor, requestScope, workspaceMoreLoading]);

  const loadMoreMembers = useCallback(async () => {
    const workspaceId = selected?.workspace.id;
    const cursor = selected?.members.next_cursor;
    if (!workspaceId || !cursor || memberMoreLoading) return;
    memberMoreControllerRef.current?.abort();
    const controller = new AbortController();
    memberMoreControllerRef.current = controller;
    setMemberMoreLoading(true);
    setMemberMoreError(null);
    try {
      const next = await fetchWorkspaceMembers(
        requestScope,
        workspaceId,
        { status: "active", cursor, limit: 100 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setSelected((current) =>
        current && current.workspace.id === workspaceId
          ? {
              ...current,
              members: {
                ...current.members,
                items: mergeByKey<WorkspaceMember>(
                  current.members.items,
                  next.items,
                  (item) => item.account_id,
                ),
                count: next.count ?? current.members.count,
                next_cursor: next.next_cursor,
                authorization_state: next.authorization_state,
              },
            }
          : current,
      );
    } catch (reason) {
      if (!controller.signal.aborted) setMemberMoreError(projectWorkspaceError(reason));
    } finally {
      if (!controller.signal.aborted) setMemberMoreLoading(false);
    }
  }, [memberMoreLoading, requestScope, selected?.members.next_cursor, selected?.workspace]);

  const loadMoreDatasets = useCallback(async () => {
    const workspaceId = selected?.workspace.id;
    const cursor = selected?.datasets.next_cursor;
    if (!workspaceId || !cursor || datasetMoreLoading) return;
    datasetMoreControllerRef.current?.abort();
    const controller = new AbortController();
    datasetMoreControllerRef.current = controller;
    setDatasetMoreLoading(true);
    setDatasetMoreError(null);
    try {
      const next = await fetchWorkspaceDatasets(
        requestScope,
        workspaceId,
        { status: "active", cursor, limit: 100 },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setSelected((current) =>
        current && current.workspace.id === workspaceId
          ? {
              ...current,
              datasets: {
                ...current.datasets,
                items: mergeByKey<WorkspaceDatasetBinding>(
                  current.datasets.items,
                  next.items,
                  (item) => item.dataset_id,
                ),
                count: next.count ?? current.datasets.count,
                next_cursor: next.next_cursor,
                authorization_state: next.authorization_state,
              },
            }
          : current,
      );
    } catch (reason) {
      if (!controller.signal.aborted) setDatasetMoreError(projectWorkspaceError(reason));
    } finally {
      if (!controller.signal.aborted) setDatasetMoreLoading(false);
    }
  }, [datasetMoreLoading, requestScope, selected?.datasets.next_cursor, selected?.workspace]);

  const closeWorkspace = useCallback(() => {
    detailControllerRef.current?.abort();
    memberMoreControllerRef.current?.abort();
    datasetMoreControllerRef.current?.abort();
    setSelected(null);
    setDetailError(null);
    setSelectedLoading(false);
    setMemberMoreLoading(false);
    setDatasetMoreLoading(false);
    setMemberMoreError(null);
    setDatasetMoreError(null);
  }, []);

  const refreshSelected = useCallback(async () => {
    if (selected) await openWorkspace(selected.workspace.id);
  }, [openWorkspace, selected]);

  const runMutation = useCallback(
    async (operation: MutationOperation, success: string, existingKey?: string) => {
      const key = existingKey ?? createWorkspaceIdempotencyKey();
      pendingMutationRef.current = { operation, key, success };
      setMutation({ saving: true, error: null, success: null });
      try {
        await operation(key);
        pendingMutationRef.current = null;
        await load();
        if (selected) await openWorkspace(selected.workspace.id);
        setMutation({ saving: false, error: null, success });
        return true;
      } catch (reason) {
        setMutation({ saving: false, error: projectWorkspaceError(reason), success: null });
        return false;
      }
    },
    [load, openWorkspace, selected],
  );

  const retryMutation = useCallback(() => {
    const pending = pendingMutationRef.current;
    return pending
      ? runMutation(pending.operation, pending.success, pending.key)
      : Promise.resolve(false);
  }, [runMutation]);

  const clearMutation = useCallback(() => {
    pendingMutationRef.current = null;
    setMutation({ saving: false, error: null, success: null });
  }, []);

  useEffect(() => {
    void load();
    return () => {
      listControllerRef.current?.abort();
      listMoreControllerRef.current?.abort();
      detailControllerRef.current?.abort();
      memberMoreControllerRef.current?.abort();
      datasetMoreControllerRef.current?.abort();
    };
  }, [load]);

  return {
    status,
    page,
    error,
    selected,
    selectedLoading,
    detailError,
    workspaceMoreLoading,
    workspaceMoreError,
    memberMoreLoading,
    memberMoreError,
    datasetMoreLoading,
    datasetMoreError,
    mutation,
    reload: load,
    loadMoreWorkspaces,
    loadMoreMembers,
    loadMoreDatasets,
    openWorkspace,
    closeWorkspace,
    refreshSelected,
    retryMutation,
    clearMutation,
    createWorkspace: (input: CreateWorkspaceInput) =>
      runMutation(
        (key) => createEnterpriseWorkspace(requestScope, input, { idempotencyKey: key }),
        "Workspace 已创建",
      ),
    updateWorkspace: (workspaceId: string, input: UpdateWorkspaceInput) =>
      runMutation(
        (key) =>
          updateEnterpriseWorkspace(requestScope, workspaceId, input, { idempotencyKey: key }),
        "Workspace 已更新",
      ),
    archiveWorkspace: (workspaceId: string, input: WorkspaceRevisionInput) =>
      runMutation(
        (key) => archiveWorkspace(requestScope, workspaceId, input, { idempotencyKey: key }),
        "Workspace 已归档",
      ),
    addMember: (workspaceId: string, input: AddWorkspaceMemberInput) =>
      runMutation(
        (key) => addWorkspaceMember(requestScope, workspaceId, input, { idempotencyKey: key }),
        "Workspace 成员已添加",
      ),
    updateMember: (workspaceId: string, accountId: string, input: UpdateWorkspaceMemberInput) =>
      runMutation(
        (key) =>
          updateWorkspaceMember(requestScope, workspaceId, accountId, input, {
            idempotencyKey: key,
          }),
        "Workspace 成员已更新",
      ),
    removeMember: (workspaceId: string, accountId: string, input: WorkspaceRevisionInput) =>
      runMutation(
        (key) =>
          removeWorkspaceMember(requestScope, workspaceId, accountId, input, {
            idempotencyKey: key,
          }),
        "Workspace 成员已移除",
      ),
    bindDataset: (workspaceId: string, input: BindWorkspaceDatasetInput) =>
      runMutation(
        (key) => bindWorkspaceDataset(requestScope, workspaceId, input, { idempotencyKey: key }),
        "知识库已绑定",
      ),
    removeDataset: (workspaceId: string, datasetId: string, input: WorkspaceRevisionInput) =>
      runMutation(
        (key) =>
          removeWorkspaceDataset(requestScope, workspaceId, datasetId, input, {
            idempotencyKey: key,
          }),
        "知识库绑定已移除",
      ),
  };
}

export function useEnterpriseWorkspaceSelector(scope: EnterpriseScope, enabled = true) {
  const requestScope = useMemo(
    () => ({
      tenantId: scope.tenantId,
      datasetId: scope.datasetId,
      actorToken: scope.actorToken,
    }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<EnterpriseWorkspace[]>([]);
  const [value, setValue] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!enabled || !requestScope.actorToken.trim()) {
      controllerRef.current?.abort();
      setStatus("idle");
      setError(null);
      setWorkspaces([]);
      setValue("");
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setStatus("loading");
    setError(null);
    let collected: EnterpriseWorkspace[] = [];
    let cursor: string | undefined;
    let defaultWorkspaceId: string | null = null;
    const seenCursors = new Set<string>();
    try {
      for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
        const page = await fetchEnterpriseWorkspaces(
          requestScope,
          {
            status: "active",
            limit: 200,
            ...(cursor ? { cursor } : {}),
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        collected = mergeByKey(collected, page.items, (workspace) => workspace.id);
        if (collected.length > 20000) {
          throw new Error("active Workspace selector exceeded the 20000 item safety limit");
        }
        defaultWorkspaceId = page.evidence.default_workspace_id ?? defaultWorkspaceId;
        setWorkspaces(collected);
        const nextCursor = page.next_cursor?.trim() || null;
        if (!nextCursor) {
          const savedId = storedWorkspaceId();
          const savedIsActive = Boolean(
            savedId && collected.some((workspace) => workspace.id === savedId),
          );
          if (savedId && !savedIsActive) removeStoredWorkspaceId();
          setValue((current) => {
            if (savedIsActive) return savedId;
            if (current && collected.some((workspace) => workspace.id === current)) return current;
            if (
              defaultWorkspaceId &&
              collected.some((workspace) => workspace.id === defaultWorkspaceId)
            ) {
              return defaultWorkspaceId;
            }
            return collected[0]?.id ?? "";
          });
          setStatus("ready");
          return;
        }
        if (seenCursors.has(nextCursor)) {
          throw new Error("active Workspace selector returned a repeated cursor");
        }
        seenCursors.add(nextCursor);
        cursor = nextCursor;
      }
      throw new Error("active Workspace selector exceeded the 100 page safety limit");
    } catch {
      if (!controller.signal.aborted) {
        setWorkspaces(collected);
        setError("Workspace 列表未完整加载，已保留服务端返回的部分事实");
        setStatus("error");
      }
    }
  }, [enabled, requestScope]);

  const selectWorkspace = useCallback(
    (workspaceId: string) => {
      const normalized = workspaceId.trim();
      if (!workspaces.some((workspace) => workspace.id === normalized)) return;
      setValue(normalized);
      storeWorkspaceId(normalized);
    },
    [workspaces],
  );

  useEffect(() => {
    void load();
    return () => controllerRef.current?.abort();
  }, [load]);

  return { status, error, workspaces, value, setValue: selectWorkspace, reload: load };
}
