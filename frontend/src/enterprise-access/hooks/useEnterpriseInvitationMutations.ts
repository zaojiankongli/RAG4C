import { useCallback, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import {
  acceptEnterpriseInvitation,
  createEnterpriseIdempotencyKey,
  createEnterpriseInvitation,
  resendEnterpriseInvitation,
  revokeEnterpriseInvitation,
  type EnterpriseAccessRequestOptions,
} from "../api/enterpriseAccessApi";
import type {
  AcceptEnterpriseInvitationInput,
  CreateEnterpriseInvitationInput,
  EnterpriseInvitation,
  EnterpriseInvitationDelivery,
  EnterpriseInvitationMutationResult,
  ResendEnterpriseInvitationInput,
  RevokeEnterpriseInvitationInput,
} from "../enterpriseAccessModel";

export interface EnterpriseInvitationCollectionController {
  upsert: (invitation: EnterpriseInvitation) => void;
  reload: () => Promise<void>;
}

export interface EnterpriseInvitationMutationGate {
  allowed: boolean;
  reason: string;
}

export interface EnterpriseInvitationMutationError {
  message: string;
  needsRefresh: boolean;
  migrationRequired: boolean;
  retryAvailable: boolean;
  idempotencyConflict?: boolean;
  status?: number;
}

export interface EnterpriseInvitationMutationState {
  saving: boolean;
  error: EnterpriseInvitationMutationError | null;
  success: string | null;
  delivery: EnterpriseInvitationDelivery | null;
}

export interface UseEnterpriseInvitationMutationsOptions {
  scope: EnterpriseScope;
  context?: EnterpriseContext | null;
  invitations?: EnterpriseInvitationCollectionController | null;
  onAccepted?: () => Promise<void>;
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

export function invitationMutationGate(
  context: EnterpriseContext | null | undefined,
  scope: EnterpriseScope,
  targetRole?: string | null,
): EnterpriseInvitationMutationGate {
  if (!scope.actorToken.trim()) return { allowed: false, reason: "请先连接签名企业身份" };
  if (!context) return { allowed: false, reason: "尚未返回企业身份上下文" };
  const capability = context.capabilities.invitations;
  if (!capability || capability.state !== "ready") {
    return { allowed: false, reason: capability?.reason || "成员邀请写能力尚未就绪" };
  }
  if (context.actor.role !== "owner" && context.actor.role !== "admin") {
    return { allowed: false, reason: "仅 tenant owner 或 tenant admin 可以管理邀请" };
  }
  if (context.actor.role === "admin" && targetRole === "owner") {
    return { allowed: false, reason: "tenant admin 不能发起或管理 owner 邀请" };
  }
  return { allowed: true, reason: "" };
}

function acceptanceGate(scope: EnterpriseScope, token: string): EnterpriseInvitationMutationGate {
  if (!scope.actorToken.trim()) return { allowed: false, reason: "请先连接签名账户身份" };
  if (!token.trim()) return { allowed: false, reason: "邀请安全链接缺少 token" };
  return { allowed: true, reason: "" };
}

export function projectEnterpriseInvitationMutationError(
  error: unknown,
): EnterpriseInvitationMutationError {
  if (error instanceof ApiError) {
    const code = apiErrorCode(error);
    if (error.status === 409 && code?.includes("idempotency") && code.includes("conflict")) {
      return {
        message: "当前幂等键已对应另一份邀请请求，系统已阻止重复写入，请重新发起操作",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        idempotencyConflict: true,
        status: error.status,
      };
    }
    if (code?.includes("migration_required")) {
      return {
        message: "邀请生命周期数据库版本尚未就绪，请完成迁移后重试",
        needsRefresh: false,
        migrationRequired: true,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 409) {
      return {
        message: "邀请记录已发生变化，请刷新邀请列表后重试",
        needsRefresh: true,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 401) {
      return {
        message: "签名身份已失效，无法提交邀请操作",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 403) {
      return {
        message: "当前身份没有成员邀请管理权限",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: false,
        status: error.status,
      };
    }
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout") {
      return {
        message: "本次邀请请求未确认提交，可使用同一请求重试避免重复邀请",
        needsRefresh: false,
        migrationRequired: false,
        retryAvailable: true,
        status: error.status,
      };
    }
  }
  return {
    message: "成员邀请操作提交失败，请稍后重试",
    needsRefresh: false,
    migrationRequired: false,
    retryAvailable: false,
    status: error instanceof ApiError ? error.status : undefined,
  };
}

type InvitationOperation = (
  options: EnterpriseAccessRequestOptions,
) => Promise<EnterpriseInvitationMutationResult>;
type InvitationGateFactory = () => EnterpriseInvitationMutationGate;

interface PendingInvitationMutation {
  operation: InvitationOperation;
  gate: InvitationGateFactory;
  success: string;
  idempotencyKey: string;
  afterSuccess?: () => Promise<void>;
}

export function useEnterpriseInvitationMutations({
  scope,
  context,
  invitations = null,
  onAccepted,
}: UseEnterpriseInvitationMutationsOptions) {
  const [state, setState] = useState<EnterpriseInvitationMutationState>({
    saving: false,
    error: null,
    success: null,
    delivery: null,
  });
  const savingRef = useRef(false);
  const pendingRef = useRef<PendingInvitationMutation | null>(null);

  const run = useCallback(
    async (
      operation: InvitationOperation,
      gate: InvitationGateFactory,
      success: string,
      afterSuccess?: () => Promise<void>,
      idempotencyKey = createEnterpriseIdempotencyKey(),
    ): Promise<EnterpriseInvitationMutationResult | null> => {
      const currentGate = gate();
      if (!currentGate.allowed) {
        pendingRef.current = null;
        setState({
          saving: false,
          error: {
            message: currentGate.reason,
            needsRefresh: false,
            migrationRequired: false,
            retryAvailable: false,
          },
          success: null,
          delivery: null,
        });
        return null;
      }
      if (savingRef.current) return null;

      const pending = { operation, gate, success, idempotencyKey, afterSuccess };
      pendingRef.current = pending;
      savingRef.current = true;
      setState({ saving: true, error: null, success: null, delivery: null });
      try {
        const result = await operation({ idempotencyKey });
        pendingRef.current = null;
        invitations?.upsert(result.invitation);
        const refreshResults = await Promise.allSettled([
          invitations ? invitations.reload() : Promise.resolve(),
          afterSuccess ? afterSuccess() : Promise.resolve(),
        ]);
        const refreshFailed = refreshResults.some((item) => item.status === "rejected");
        setState({
          saving: false,
          error: refreshFailed
            ? {
                message: "邀请已提交，但最新邀请事实刷新失败，请手动刷新",
                needsRefresh: true,
                migrationRequired: false,
                retryAvailable: false,
              }
            : null,
          success,
          delivery: result.delivery,
        });
        return result;
      } catch (error) {
        const projected = projectEnterpriseInvitationMutationError(error);
        if (!projected.retryAvailable) pendingRef.current = null;
        setState({ saving: false, error: projected, success: null, delivery: null });
        return null;
      } finally {
        savingRef.current = false;
      }
    },
    [invitations],
  );

  const retry = useCallback(async () => {
    const pending = pendingRef.current;
    if (!pending || savingRef.current) return null;
    return run(
      pending.operation,
      pending.gate,
      pending.success,
      pending.afterSuccess,
      pending.idempotencyKey,
    );
  }, [run]);

  const create = useCallback(
    (payload: CreateEnterpriseInvitationInput) =>
      run(
        (options) => createEnterpriseInvitation(scope, payload, options),
        () => invitationMutationGate(context, scope, payload.role),
        "邀请已创建",
      ),
    [context, run, scope],
  );

  const resend = useCallback(
    (invitation: EnterpriseInvitation, payload: ResendEnterpriseInvitationInput) =>
      run(
        (options) => resendEnterpriseInvitation(scope, invitation.id, payload, options),
        () => invitationMutationGate(context, scope, invitation.role),
        "一次性邀请链接已重新生成",
      ),
    [context, run, scope],
  );

  const revoke = useCallback(
    (invitation: EnterpriseInvitation, payload: RevokeEnterpriseInvitationInput) =>
      run(
        (options) => revokeEnterpriseInvitation(scope, invitation.id, payload, options),
        () => invitationMutationGate(context, scope, invitation.role),
        "邀请已撤销",
      ),
    [context, run, scope],
  );

  const accept = useCallback(
    (payload: AcceptEnterpriseInvitationInput) =>
      run(
        (options) => acceptEnterpriseInvitation(scope, payload, options),
        () => acceptanceGate(scope, payload.invite_token),
        "邀请已接受",
        onAccepted,
      ),
    [onAccepted, run, scope],
  );

  const clear = useCallback(() => {
    pendingRef.current = null;
    setState((current) => ({ ...current, saving: false, error: null, success: null }));
  }, []);
  const clearDelivery = useCallback(() => {
    setState((current) => ({ ...current, delivery: null }));
  }, []);

  return {
    ...state,
    create,
    resend,
    revoke,
    accept,
    retry,
    clear,
    clearDelivery,
    refresh: invitations?.reload ?? (async () => undefined),
  };
}
