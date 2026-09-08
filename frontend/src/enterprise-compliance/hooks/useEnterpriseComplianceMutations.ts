import { useCallback, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  createAuditExport,
  createComplianceIdempotencyKey,
  createLegalHold,
  executeRetention,
  releaseLegalHold,
  updateRetentionPolicy,
  type ComplianceRequestOptions,
} from "../api/enterpriseComplianceApi";
import type {
  AuditExportInput,
  AuditExportJob,
  AuditLegalHold,
  AuditRetentionPolicy,
  LegalHoldInput,
  RetentionExecuteInput,
  RetentionExecutionResult,
  RetentionPolicyInput,
  RevisionReasonInput,
} from "../enterpriseComplianceModel";

export interface ComplianceMutationError {
  message: string;
  needsRefresh: boolean;
  retryAvailable: boolean;
  migrationRequired: boolean;
  status?: number;
}
type OperationResult =
  AuditRetentionPolicy | RetentionExecutionResult | AuditLegalHold | AuditExportJob;
type Operation = (options: ComplianceRequestOptions) => Promise<OperationResult>;
interface Pending {
  operation: Operation;
  success: string;
  ownerOnly: boolean;
  key: string;
}
interface Options {
  scope: EnterpriseScope;
  context: EnterpriseContext;
  reload: () => Promise<void>;
}
function projectError(error: unknown): ComplianceMutationError {
  if (error instanceof ApiError) {
    const body =
      typeof error.body === "object" && error.body !== null
        ? (error.body as Record<string, unknown>)
        : {};
    const detail =
      typeof body.detail === "object" && body.detail !== null
        ? (body.detail as Record<string, unknown>)
        : {};
    const code = String(detail.code ?? body.code ?? "");
    if (code.includes("migration_required"))
      return {
        message: "审计合规数据库版本尚未就绪，请完成 0023 迁移",
        needsRefresh: false,
        retryAvailable: false,
        migrationRequired: true,
        status: error.status,
      };
    if (error.status === 409 || error.status === 412)
      return {
        message: "合规记录或 preview fingerprint 已变化，请刷新后重新预览",
        needsRefresh: true,
        retryAvailable: false,
        migrationRequired: false,
        status: error.status,
      };
    if (error.status === 401)
      return {
        message: "签名身份已失效",
        needsRefresh: false,
        retryAvailable: false,
        migrationRequired: false,
        status: error.status,
      };
    if (error.status === 403)
      return {
        message: "当前租户角色没有执行该合规操作的权限",
        needsRefresh: false,
        retryAvailable: false,
        migrationRequired: false,
        status: error.status,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        message: "合规请求结果未确认，可使用同一请求重试",
        needsRefresh: false,
        retryAvailable: true,
        migrationRequired: false,
        status: error.status,
      };
  }
  return {
    message: "合规操作失败，请稍后重试",
    needsRefresh: false,
    retryAvailable: false,
    migrationRequired: false,
  };
}
function allowed(context: EnterpriseContext, ownerOnly: boolean) {
  const role = context.actor.role;
  const capability =
    context.capabilities.audit_compliance ?? context.capabilities.enterprise_audit_compliance;
  return (
    capability?.state === "ready" &&
    (ownerOnly ? role === "owner" : role === "owner" || role === "admin")
  );
}
export function useEnterpriseComplianceMutations({ scope, context, reload }: Options) {
  const [state, setState] = useState<{
    saving: boolean;
    error: ComplianceMutationError | null;
    success: string | null;
  }>({ saving: false, error: null, success: null });
  const pending = useRef<Pending | null>(null);
  const busy = useRef(false);
  const run = useCallback(
    async (
      operation: Operation,
      success: string,
      ownerOnly: boolean,
      key = createComplianceIdempotencyKey(),
    ): Promise<OperationResult | null> => {
      if (!allowed(context, ownerOnly)) {
        setState({
          saving: false,
          error: {
            message: ownerOnly
              ? "仅 tenant owner 可以执行此合规操作"
              : "仅 tenant owner/admin 可以执行此合规操作",
            needsRefresh: false,
            retryAvailable: false,
            migrationRequired: false,
          },
          success: null,
        });
        return null;
      }
      if (busy.current) return null;
      busy.current = true;
      pending.current = { operation, success, ownerOnly, key };
      setState({ saving: true, error: null, success: null });
      try {
        const result = await operation({ idempotencyKey: key });
        await reload();
        pending.current = null;
        setState({ saving: false, error: null, success });
        return result;
      } catch (error) {
        const projected = projectError(error);
        if (!projected.retryAvailable) pending.current = null;
        setState({ saving: false, error: projected, success: null });
        return null;
      } finally {
        busy.current = false;
      }
    },
    [context, reload],
  );
  const retry = useCallback(() => {
    const value = pending.current;
    return value
      ? run(value.operation, value.success, value.ownerOnly, value.key)
      : Promise.resolve(null);
  }, [run]);
  const updatePolicy = useCallback(
    (input: RetentionPolicyInput) =>
      run(
        (options) => updateRetentionPolicy(scope, input, options),
        "保留策略已更新",
        true,
      ) as Promise<AuditRetentionPolicy | null>,
    [run, scope],
  );
  const execute = useCallback(
    (input: RetentionExecuteInput) =>
      run(
        (options) => executeRetention(scope, input, options),
        "保留删除已执行",
        true,
      ) as Promise<RetentionExecutionResult | null>,
    [run, scope],
  );
  const createHold = useCallback(
    (input: LegalHoldInput) =>
      run(
        (options) => createLegalHold(scope, input, options),
        "legal hold 已创建",
        true,
      ) as Promise<AuditLegalHold | null>,
    [run, scope],
  );
  const releaseHold = useCallback(
    (hold: Pick<AuditLegalHold, "id" | "revision">, input: RevisionReasonInput) =>
      run(
        (options) => releaseLegalHold(scope, hold.id, input, options),
        "legal hold 已释放",
        true,
      ) as Promise<AuditLegalHold | null>,
    [run, scope],
  );
  const createExport = useCallback(
    (input: AuditExportInput) =>
      run(
        (options) => createAuditExport(scope, input, options),
        "审计导出作业已创建",
        false,
      ) as Promise<AuditExportJob | null>,
    [run, scope],
  );
  const clear = useCallback(() => {
    pending.current = null;
    setState((current) => ({ ...current, error: null, success: null }));
  }, []);
  return { ...state, retry, updatePolicy, execute, createHold, releaseHold, createExport, clear };
}
