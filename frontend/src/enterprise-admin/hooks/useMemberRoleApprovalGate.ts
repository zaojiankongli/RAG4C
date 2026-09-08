import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import {
  createApprovalIdempotencyKey,
  createApprovalRequest,
  fetchApprovalPolicies,
  type ApprovalRequestOptions,
} from "../../enterprise-approval/api/enterpriseApprovalApi";
import type { ApprovalRequest } from "../../enterprise-approval/enterpriseApprovalModel";
import {
  buildMemberRoleApprovalSnapshot,
  policyFactsFromMemberRoleApprovalHint,
  projectMemberRoleApprovalPolicyFacts,
  projectMemberRoleApprovalRequiredHint,
  selectMemberRoleApprovalPolicy,
  type MemberRoleApprovalPolicyFacts,
  type MemberRoleApprovalRequiredHint,
} from "../memberRoleApprovalModel";

export type MemberRoleApprovalGateMode =
  "idle" | "loading" | "direct" | "approval" | "submitted" | "error";

export interface MemberRoleApprovalMember {
  account_id: string;
  role: string;
  status: string;
  revision?: number | null;
}

export interface MemberRoleApprovalGateState {
  mode: MemberRoleApprovalGateMode;
  policy: MemberRoleApprovalPolicyFacts | null;
  request: ApprovalRequest | null;
  loading: boolean;
  submitting: boolean;
  error: string | null;
}

export interface UseMemberRoleApprovalGateOptions {
  visible: boolean;
  scope: { tenantId: string; actorToken: string };
  member: MemberRoleApprovalMember | null;
}

function errorCode(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  const body =
    typeof error.body === "object" && error.body !== null
      ? (error.body as Record<string, unknown>)
      : null;
  const detail =
    body && typeof body.detail === "object" && body.detail !== null
      ? (body.detail as Record<string, unknown>)
      : null;
  const code = detail?.code ?? body?.code;
  return typeof code === "string" && code.trim() ? code.trim() : undefined;
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

function readError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "身份已失效，无法读取成员角色审批规则。";
    if (error.status === 403) return "当前身份没有读取成员角色审批规则的权限。";
    if (error.status === 503) return "审批规则数据库尚未就绪，角色变更已被阻止。";
    if (error.status === 404) return "审批规则事实暂时不可用（接口未挂载），角色变更已被阻止。";
    if (error.kind === "network" || error.kind === "timeout") {
      return "审批规则事实暂时不可用，角色变更已被阻止。";
    }
  }
  return "审批规则事实暂时不可用，角色变更已被阻止。";
}

function submitError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "身份已失效，无法提交成员角色审批申请。";
    if (error.status === 403) return "当前身份没有提交成员角色审批申请的权限。";
    if (error.status === 409) return "成员或审批规则事实已发生变化，请刷新后重试。";
    if (error.kind === "network" || error.kind === "timeout") {
      return "审批申请未确认提交，请检查连接后使用同一请求重试。";
    }
  }
  return "成员角色审批申请提交失败，请稍后重试。";
}

function retryable(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 503 || ["network", "timeout", "aborted"].includes(error.kind))
  );
}

function validRevision(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

const EMPTY_STATE: MemberRoleApprovalGateState = {
  mode: "idle",
  policy: null,
  request: null,
  loading: false,
  submitting: false,
  error: null,
};

export function useMemberRoleApprovalGate({
  visible,
  scope,
  member,
}: UseMemberRoleApprovalGateOptions) {
  const [state, setState] = useState<MemberRoleApprovalGateState>(EMPTY_STATE);
  const lookupControllerRef = useRef<AbortController | null>(null);
  const lookupSequenceRef = useRef(0);
  const submittingRef = useRef(false);
  const submissionKeyRef = useRef<string | null>(null);
  const submissionDraftFingerprintRef = useRef<string | null>(null);
  const pendingSubmissionRef = useRef<{ requestedRole: string; reason: string } | null>(null);

  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  const accountId = member?.account_id.trim() ?? "";
  const memberRole = member?.role.trim() ?? "";
  const memberStatus = member?.status.trim() ?? "";
  const memberRevision = member?.revision;
  const stableScope = useMemo(() => ({ tenantId, actorToken }), [actorToken, tenantId]);
  const stableMember = useMemo(
    () =>
      accountId
        ? {
            account_id: accountId,
            role: memberRole,
            status: memberStatus,
            revision: memberRevision ?? undefined,
          }
        : null,
    [accountId, memberRevision, memberRole, memberStatus],
  );

  const applyApprovalRequired = useCallback((error: unknown): boolean => {
    if (
      !(error instanceof ApiError) ||
      error.status !== 409 ||
      errorCode(error) !== "member_role_approval_required"
    ) {
      return false;
    }
    const hint = projectMemberRoleApprovalRequiredHint(error.body);
    const policy = policyFactsFromMemberRoleApprovalHint(hint);
    setState((current) => ({
      ...current,
      mode: "approval",
      policy,
      loading: false,
      submitting: false,
      error: policy ? null : "服务端要求审批，但未返回可用的审批规则 ID。",
    }));
    return true;
  }, []);

  const refresh = useCallback(async () => {
    lookupControllerRef.current?.abort();
    const controller = new AbortController();
    lookupControllerRef.current = controller;
    const sequence = ++lookupSequenceRef.current;
    submissionKeyRef.current = null;
    submissionDraftFingerprintRef.current = null;
    pendingSubmissionRef.current = null;

    if (!visible) return;
    if (!tenantId || !accountId || !actorToken || !stableMember) {
      setState({
        ...EMPTY_STATE,
        mode: "error",
        error: "缺少租户、成员或身份凭据，无法读取审批规则。",
      });
      return;
    }
    if (!validRevision(memberRevision)) {
      setState({
        ...EMPTY_STATE,
        mode: "error",
        error: "成员版本号未返回，已阻止角色变更。",
      });
      return;
    }
    if (memberStatus !== "active") {
      setState({
        ...EMPTY_STATE,
        mode: "error",
        error: "当前成员不是启用状态，已阻止角色变更。",
      });
      return;
    }

    setState((current) => ({
      ...current,
      mode: "loading",
      policy: null,
      request: null,
      loading: true,
      submitting: false,
      error: null,
    }));

    try {
      const page = await fetchApprovalPolicies(
        stableScope,
        { status: "active", actionType: "member_role_change" },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== lookupSequenceRef.current) return;
      const selected = selectMemberRoleApprovalPolicy(page.items, accountId);
      setState({
        mode: selected ? "approval" : "direct",
        policy: selected ? projectMemberRoleApprovalPolicyFacts(selected) : null,
        request: null,
        loading: false,
        submitting: false,
        error: null,
      });
    } catch (error) {
      if (controller.signal.aborted || sequence !== lookupSequenceRef.current || isAbort(error))
        return;
      setState({
        mode: "error",
        policy: null,
        request: null,
        loading: false,
        submitting: false,
        error: readError(error),
      });
    } finally {
      if (lookupControllerRef.current === controller) lookupControllerRef.current = null;
    }
  }, [
    accountId,
    actorToken,
    memberRevision,
    memberStatus,
    stableMember,
    stableScope,
    tenantId,
    visible,
  ]);

  useEffect(() => {
    if (!visible) {
      lookupControllerRef.current?.abort();
      lookupControllerRef.current = null;
      lookupSequenceRef.current += 1;
      submissionKeyRef.current = null;
      submissionDraftFingerprintRef.current = null;
      pendingSubmissionRef.current = null;
      setState(EMPTY_STATE);
      return;
    }
    void refresh();
    return () => lookupControllerRef.current?.abort();
  }, [refresh, visible]);

  const submitApproval = useCallback(
    async (requestedRole: string, reason: string): Promise<ApprovalRequest | null> => {
      if (submittingRef.current) return null;
      if (state.mode !== "approval") {
        setState((current) => ({ ...current, error: "当前没有可用的成员角色审批规则。" }));
        return null;
      }
      const normalizedRole = requestedRole.trim();
      const normalizedReason = reason.trim();
      if (!stableMember || !validRevision(stableMember.revision)) {
        setState((current) => ({ ...current, error: "成员版本号未返回，已阻止提交审批。" }));
        return null;
      }
      if (!normalizedRole || normalizedRole === stableMember.role) {
        setState((current) => ({ ...current, error: "请选择与当前角色不同的新角色。" }));
        return null;
      }
      if (!normalizedReason) {
        setState((current) => ({ ...current, error: "变更原因不能为空。" }));
        return null;
      }
      const policyId = state.policy?.id;
      if (!policyId) {
        setState((current) => ({
          ...current,
          error: "服务端未返回可用的审批规则 ID，已阻止提交。",
        }));
        return null;
      }

      submittingRef.current = true;
      pendingSubmissionRef.current = { requestedRole: normalizedRole, reason: normalizedReason };
      const draftFingerprint = JSON.stringify([normalizedRole, normalizedReason]);
      if (
        submissionKeyRef.current === null ||
        submissionDraftFingerprintRef.current !== draftFingerprint
      ) {
        submissionKeyRef.current = createApprovalIdempotencyKey();
        submissionDraftFingerprintRef.current = draftFingerprint;
      }
      const idempotencyKey = submissionKeyRef.current;
      setState((current) => ({ ...current, submitting: true, error: null }));
      try {
        const request = await createApprovalRequest(
          stableScope,
          {
            policy_id: policyId,
            resource_type: "tenant_member",
            resource_id: stableMember.account_id,
            snapshot: buildMemberRoleApprovalSnapshot(stableMember, normalizedRole),
            reason: normalizedReason,
          },
          { idempotencyKey } satisfies ApprovalRequestOptions,
        );
        pendingSubmissionRef.current = null;
        submissionKeyRef.current = null;
        submissionDraftFingerprintRef.current = null;
        setState((current) => ({
          ...current,
          mode: "submitted",
          request,
          loading: false,
          submitting: false,
          error: null,
        }));
        return request;
      } catch (error) {
        if (!retryable(error)) {
          pendingSubmissionRef.current = null;
          submissionKeyRef.current = null;
          submissionDraftFingerprintRef.current = null;
        }
        setState((current) => ({
          ...current,
          mode: "approval",
          loading: false,
          submitting: false,
          error: submitError(error),
        }));
        return null;
      } finally {
        submittingRef.current = false;
      }
    },
    [stableMember, stableScope, state.mode, state.policy?.id],
  );

  const retry = useCallback(() => {
    const pending = pendingSubmissionRef.current;
    return pending ? submitApproval(pending.requestedRole, pending.reason) : Promise.resolve(null);
  }, [submitApproval]);

  return {
    ...state,
    refresh,
    retry,
    applyApprovalRequired,
    submitApproval,
    approvalMode: state.mode === "approval" || state.mode === "submitted",
  };
}

export type { MemberRoleApprovalRequiredHint };
