import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type {
  EnterpriseCapability,
  EnterpriseContext,
  EnterpriseScope,
} from "../../enterprise-admin/model";
import {
  fetchAuditExportJobs,
  fetchLegalHolds,
  fetchRetentionPolicy,
  previewRetention,
} from "../api/enterpriseComplianceApi";
import type {
  AuditExportJob,
  AuditLegalHold,
  AuditRetentionPolicy,
  RetentionPreview,
} from "../enterpriseComplianceModel";

export type ComplianceLoadStatus =
  | "idle"
  | "loading"
  | "ready"
  | "unauthorized"
  | "forbidden"
  | "migration-required"
  | "unavailable"
  | "error";
export interface ComplianceLoadError {
  status: ComplianceLoadStatus;
  title: string;
  description: string;
  canRetry: boolean;
}
function capabilityOf(context: EnterpriseContext): EnterpriseCapability | null {
  return (
    context.capabilities.audit_compliance ??
    context.capabilities.enterprise_audit_compliance ??
    null
  );
}
function errorOf(error: unknown): ComplianceLoadError {
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
        status: "migration-required",
        title: "审计合规数据库版本尚未就绪",
        description: "完成 0023 enterprise audit compliance 迁移后重试",
        canRetry: true,
      };
    if (error.status === 401)
      return {
        status: "unauthorized",
        title: "签名身份已失效",
        description: "重新连接企业身份后读取合规事实",
        canRetry: false,
      };
    if (error.status === 403)
      return {
        status: "forbidden",
        title: "没有读取合规事实的权限",
        description: "当前租户角色只能查看有限 capability 证据",
        canRetry: false,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        status: "unavailable",
        title: "合规控制面暂不可用",
        description: "服务未连接或数据库尚未就绪",
        canRetry: true,
      };
  }
  return {
    status: "error",
    title: "合规事实读取失败",
    description: "服务未返回可安全展示的事实",
    canRetry: true,
  };
}
export function useEnterpriseComplianceCenter(scope: EnterpriseScope, context: EnterpriseContext) {
  const stableScope = useMemo(
    () => ({ tenantId: scope.tenantId, datasetId: scope.datasetId, actorToken: scope.actorToken }),
    [scope.actorToken, scope.datasetId, scope.tenantId],
  );
  const capability = capabilityOf(context);
  const readable = capability?.state === "ready" || capability?.state === "limited";
  const [status, setStatus] = useState<ComplianceLoadStatus>("idle");
  const [error, setError] = useState<ComplianceLoadError | null>(null);
  const [policy, setPolicy] = useState<AuditRetentionPolicy | null>(null);
  const [holds, setHolds] = useState<AuditLegalHold[]>([]);
  const [exports, setExports] = useState<AuditExportJob[]>([]);
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const version = useRef(0);
  const reload = useCallback(async () => {
    if (!readable || !stableScope.actorToken.trim()) return;
    const current = ++version.current;
    setStatus("loading");
    setError(null);
    try {
      const [nextPolicy, nextHolds, nextExports] = await Promise.all([
        fetchRetentionPolicy(stableScope),
        fetchLegalHolds(stableScope),
        fetchAuditExportJobs(stableScope),
      ]);
      if (current !== version.current) return;
      setPolicy(nextPolicy);
      setHolds(nextHolds.items);
      setExports(nextExports.items);
      setStatus("ready");
    } catch (reason) {
      if (current !== version.current) return;
      const projected = errorOf(reason);
      setError(projected);
      setStatus(projected.status);
    }
  }, [readable, stableScope]);
  const runPreview = useCallback(async () => {
    if (!policy) return null;
    try {
      const value = await previewRetention(stableScope, { policy_revision: policy.revision });
      setPreview(value);
      return value;
    } catch (reason) {
      setError(errorOf(reason));
      return null;
    }
  }, [policy, stableScope]);
  useEffect(() => {
    if (!readable) {
      setStatus("unavailable");
      return;
    }
    void reload();
    return () => {
      version.current += 1;
    };
  }, [readable, reload]);
  return {
    capability,
    readable,
    status,
    error,
    policy,
    holds,
    exports,
    preview,
    setPreview,
    reload,
    runPreview,
  };
}
