import { useCallback, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  createEnterpriseIdempotencyKey,
  createDatasetAccessGrant,
  disableDatasetAcl,
  resumeDatasetAccessGrant,
  revokeDatasetAccessGrant,
  updateDatasetAccessGrantRole,
  type DisableDatasetAclInput,
  type EnterpriseAccessRequestOptions,
} from "../api/enterpriseAccessApi";
import type { DatasetAclApprovalRequiredHint } from "../datasetAclApprovalModel";
import { projectDatasetAclApprovalRequiredHint } from "../datasetAclApprovalModel";
import type {
  CreateDatasetAccessGrantInput,
  DatasetAccessGrant,
  DatasetAccessGrantRevisionInput,
  PersistentDatasetAccessSummary,
  UpdateDatasetAccessGrantRoleInput,
} from "../enterpriseAccessModel";

export interface DatasetAccessGrantCollectionController {
  upsert: (item: DatasetAccessGrant) => void;
  reload: () => Promise<void>;
}

export interface DatasetAccessGrantMutationGate {
  allowed: boolean;
  reason: string;
}

export interface DatasetAccessGrantMutationError {
  message: string;
  needsRefresh: boolean;
  migrationRequired: boolean;
  retryAvailable: boolean;
  idempotencyConflict?: boolean;
  code?: string;
  approvalRequired?: DatasetAclApprovalRequiredHint | null;
  status?: number;
}

export interface DatasetAccessGrantMutationState {
  saving: boolean;
  error: DatasetAccessGrantMutationError | null;
  success: string | null;
}

export interface UseDatasetAccessGrantMutationsOptions {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  accessGrants: DatasetAccessGrantCollectionController;
  accessSummary?: PersistentDatasetAccessSummary | null;
  onAccessSummaryRefresh?: () => Promise<void>;
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

export function datasetAccessGrantMutationGate(
  context: EnterpriseContext,
  scope: EnterpriseScope,
): DatasetAccessGrantMutationGate {
  if (!scope.datasetId?.trim()) {
    return { allowed: false, reason: "请先选择知识库" };
  }
  if (!context.effective_permissions.includes("knowledge.manage")) {
    return { allowed: false, reason: "当前身份没有 knowledge.manage 权限" };
  }
  const capability = context.capabilities.dataset_acl;
  if (!capability || capability.state === "unavailable") {
    return {
      allowed: false,
      reason: capability?.reason || "知识库 ACL 能力尚未接入",
    };
  }
  return { allowed: true, reason: "" };
}

export function datasetAclDisableGate(
  context: EnterpriseContext,
  scope: EnterpriseScope,
  accessSummary?: PersistentDatasetAccessSummary | null,
): DatasetAccessGrantMutationGate {
  const grantGate = datasetAccessGrantMutationGate(context, scope);
  if (!grantGate.allowed) return grantGate;
  if (!accessSummary) {
    return { allowed: false, reason: "尚未返回持久 ACL 模式证据" };
  }
  if (accessSummary.acl_mode !== "dataset_acl") {
    return { allowed: false, reason: "当前知识库未处于持久 ACL 模式" };
  }
  if (accessSummary.actor_role !== "owner" && accessSummary.actor_role !== "admin") {
    return { allowed: false, reason: "仅 tenant owner 或 tenant admin 可以停用 ACL" };
  }
  if (!Number.isInteger(accessSummary.acl_revision) || (accessSummary.acl_revision as number) < 1) {
    return { allowed: false, reason: "服务端未返回有效的 ACL revision" };
  }
  return { allowed: true, reason: "" };
}

export function projectDatasetAccessGrantMutationError(
  error: unknown,
): DatasetAccessGrantMutationError {
  if (error instanceof ApiError) {
    const code = apiErrorCode(error);
    if (error.status === 409 && code === "dataset_acl_approval_required") {
      return {
        message: "该停用操作需要审批，已切换到审批申请模式。",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        code,
        approvalRequired: projectDatasetAclApprovalRequiredHint(error.body),
        status: error.status,
      };
    }
    if (error.status === 409 && code?.includes("idempotency") && code.includes("conflict")) {
      return {
        message: "当前幂等键已对应另一份请求，系统已阻止重复写入。请重新打开操作并发起新的变更。",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        idempotencyConflict: true,
        status: error.status,
      };
    }
    if (code?.includes("migration_required")) {
      return {
        message: "企业访问图谱数据库版本尚未就绪，请完成数据库迁移后重试",
        needsRefresh: false,
        migrationRequired: true,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 409) {
      return {
        message: "授权记录已发生变化，请刷新授权列表后重试",
        needsRefresh: true,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 401) {
      return {
        message: "身份已失效，无法提交知识库授权变更",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 403) {
      return {
        message: "当前身份没有知识库授权管理权限",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout") {
      return {
        message: "本次请求未确认提交，可使用同一请求重试以避免重复写入",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: true,
        status: error.status,
      };
    }
  }
  return {
    message: "知识库授权变更提交失败，请稍后重试",
    needsRefresh: false,
    migrationRequired: false,
    retryAvailable: false,
    status: error instanceof ApiError ? error.status : undefined,
  };
}

type MutationResult = DatasetAccessGrant | PersistentDatasetAccessSummary | null;
type MutationOperation = (options: EnterpriseAccessRequestOptions) => Promise<MutationResult>;
type MutationGateFactory = () => DatasetAccessGrantMutationGate;

interface PendingMutation {
  operation: MutationOperation;
  success: string;
  gate: MutationGateFactory;
  upsertGrant: boolean;
  idempotencyKey: string;
}

function isDatasetAccessGrant(value: MutationResult): value is DatasetAccessGrant {
  return Boolean(
    value &&
    typeof value === "object" &&
    "id" in value &&
    "dataset_id" in value &&
    "subject_type" in value,
  );
}

export function useDatasetAccessGrantMutations({
  scope,
  context,
  accessGrants,
  accessSummary,
  onAccessSummaryRefresh,
}: UseDatasetAccessGrantMutationsOptions) {
  const [state, setState] = useState<DatasetAccessGrantMutationState>({
    saving: false,
    error: null,
    success: null,
  });
  const savingRef = useRef(false);
  const pendingMutationRef = useRef<PendingMutation | null>(null);

  const refreshFacts = useCallback(async () => {
    await Promise.all([
      accessGrants.reload(),
      onAccessSummaryRefresh ? onAccessSummaryRefresh() : Promise.resolve(),
    ]);
  }, [accessGrants, onAccessSummaryRefresh]);

  const run = useCallback(
    async (
      operation: MutationOperation,
      success: string,
      gate: MutationGateFactory,
      upsertGrant: boolean,
      idempotencyKey = createEnterpriseIdempotencyKey(),
    ): Promise<MutationResult> => {
      const mutationGate = gate();
      if (!mutationGate.allowed) {
        pendingMutationRef.current = null;
        setState({
          saving: false,
          error: {
            message: mutationGate.reason,
            needsRefresh: false,
            migrationRequired: false,
            retryAvailable: false,
          },
          success: null,
        });
        return null;
      }
      if (savingRef.current) return null;

      const pendingMutation: PendingMutation = {
        operation,
        success,
        gate,
        upsertGrant,
        idempotencyKey,
      };
      pendingMutationRef.current = pendingMutation;
      savingRef.current = true;
      setState({ saving: true, error: null, success: null });
      try {
        const result = await operation({ idempotencyKey });
        pendingMutationRef.current = null;
        if (upsertGrant && isDatasetAccessGrant(result)) accessGrants.upsert(result);
        const refreshResults = await Promise.allSettled([
          accessGrants.reload(),
          onAccessSummaryRefresh ? onAccessSummaryRefresh() : Promise.resolve(),
        ]);
        const refreshFailed = refreshResults.some((item) => item.status === "rejected");
        setState({
          saving: false,
          error: refreshFailed
            ? {
                message: "授权已提交，但最新权限事实刷新失败，请手动刷新",
                needsRefresh: true,
                migrationRequired: false,
                retryAvailable: false,
              }
            : null,
          success,
        });
        return result;
      } catch (error) {
        const projected = projectDatasetAccessGrantMutationError(error);
        if (!projected.retryAvailable) pendingMutationRef.current = null;
        setState({ saving: false, error: projected, success: null });
        return null;
      } finally {
        savingRef.current = false;
      }
    },
    [accessGrants, onAccessSummaryRefresh],
  );

  const retry = useCallback(async (): Promise<MutationResult> => {
    const pendingMutation = pendingMutationRef.current;
    if (!pendingMutation || savingRef.current) return null;
    return run(
      pendingMutation.operation,
      pendingMutation.success,
      pendingMutation.gate,
      pendingMutation.upsertGrant,
      pendingMutation.idempotencyKey,
    );
  }, [run]);

  const create = useCallback(
    async (payload: CreateDatasetAccessGrantInput) => {
      const result = await run(
        (options) =>
          createDatasetAccessGrant(scope, { ...payload, reason: payload.reason.trim() }, options),
        "授权已创建",
        () => datasetAccessGrantMutationGate(context, scope),
        true,
      );
      return isDatasetAccessGrant(result) ? result : null;
    },
    [context, run, scope],
  );
  const updateRole = useCallback(
    async (grant: DatasetAccessGrant, payload: UpdateDatasetAccessGrantRoleInput) => {
      const result = await run(
        (options) =>
          updateDatasetAccessGrantRole(
            scope,
            grant.id,
            { ...payload, reason: payload.reason.trim() },
            options,
          ),
        "授权角色已更新",
        () => datasetAccessGrantMutationGate(context, scope),
        true,
      );
      return isDatasetAccessGrant(result) ? result : null;
    },
    [context, run, scope],
  );
  const revoke = useCallback(
    async (grant: DatasetAccessGrant, payload: DatasetAccessGrantRevisionInput) => {
      const result = await run(
        (options) =>
          revokeDatasetAccessGrant(
            scope,
            grant.id,
            { ...payload, reason: payload.reason.trim() },
            options,
          ),
        "授权已撤销",
        () => datasetAccessGrantMutationGate(context, scope),
        true,
      );
      return isDatasetAccessGrant(result) ? result : null;
    },
    [context, run, scope],
  );
  const resume = useCallback(
    async (grant: DatasetAccessGrant, payload: DatasetAccessGrantRevisionInput) => {
      const result = await run(
        (options) =>
          resumeDatasetAccessGrant(
            scope,
            grant.id,
            { ...payload, reason: payload.reason.trim() },
            options,
          ),
        "授权已恢复",
        () => datasetAccessGrantMutationGate(context, scope),
        true,
      );
      return isDatasetAccessGrant(result) ? result : null;
    },
    [context, run, scope],
  );
  const disable = useCallback(
    async (payload: DisableDatasetAclInput): Promise<PersistentDatasetAccessSummary | null> => {
      const result = await run(
        (options) =>
          disableDatasetAcl(scope, { ...payload, reason: payload.reason.trim() }, options),
        "ACL 已停用",
        () => datasetAclDisableGate(context, scope, accessSummary),
        false,
      );
      return result && !isDatasetAccessGrant(result)
        ? (result as PersistentDatasetAccessSummary)
        : null;
    },
    [accessSummary, context, run, scope],
  );
  const clear = useCallback(() => {
    pendingMutationRef.current = null;
    setState({ saving: false, error: null, success: null });
  }, []);

  return {
    ...state,
    create,
    updateRole,
    revoke,
    resume,
    disable,
    retry,
    refreshFacts,
    clear,
  };
}
