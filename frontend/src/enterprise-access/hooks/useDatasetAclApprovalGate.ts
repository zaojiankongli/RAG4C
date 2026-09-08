import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  createApprovalIdempotencyKey,
  createApprovalRequest,
  fetchApprovalPolicies,
  type ApprovalRequestOptions,
} from "../../enterprise-approval/api/enterpriseApprovalApi";
import type { ApprovalRequest } from "../../enterprise-approval/enterpriseApprovalModel";
import {
  policyFactsFromApprovalRequiredHint,
  projectDatasetAclApprovalPolicyFacts,
  selectDatasetAclApprovalPolicy,
  type DatasetAclApprovalPolicyFacts,
  type DatasetAclApprovalRequiredHint,
} from "../datasetAclApprovalModel";

export type DatasetAclApprovalGateMode =
  "idle" | "loading" | "direct" | "approval" | "submitted" | "error";

export interface DatasetAclApprovalGateState {
  mode: DatasetAclApprovalGateMode;
  policy: DatasetAclApprovalPolicyFacts | null;
  request: ApprovalRequest | null;
  loading: boolean;
  submitting: boolean;
  error: string | null;
}

export interface UseDatasetAclApprovalGateOptions {
  visible: boolean;
  scope: EnterpriseScope;
  aclRevision: number | null | undefined;
  currentAclMode: string | null | undefined;
  serverApprovalRequired?: DatasetAclApprovalRequiredHint | null;
}

export interface DatasetAclApprovalSubmission {
  expected_acl_revision: number;
  reason: string;
}

function safeApprovalReadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "身份已失效，无法读取 ACL 停用审批规则。";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "当前身份没有读取 ACL 停用审批规则的权限。";
  }
  if (error instanceof ApiError && error.status === 503) {
    return "审批规则数据库尚未就绪，直接停用已被阻止。";
  }
  return "审批规则事实暂时不可用，直接停用已被阻止。";
}

function safeApprovalSubmitError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "审批规则或知识库访问事实已发生变化，请重新读取后再提交。";
  }
  if (error instanceof ApiError && error.status === 401) {
    return "身份已失效，无法提交 ACL 停用审批申请。";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "当前身份没有提交 ACL 停用审批申请的权限。";
  }
  if (error instanceof ApiError && (error.kind === "network" || error.kind === "timeout")) {
    return "审批申请未确认提交，请检查连接后使用同一请求重试。";
  }
  return "ACL 停用审批申请提交失败，请稍后重试。";
}

function validRevision(value: number): boolean {
  return Number.isInteger(value) && value > 0;
}

export function useDatasetAclApprovalGate({
  visible,
  scope,
  aclRevision,
  currentAclMode,
  serverApprovalRequired = null,
}: UseDatasetAclApprovalGateOptions) {
  const [state, setState] = useState<DatasetAclApprovalGateState>({
    mode: visible ? "idle" : "idle",
    policy: null,
    request: null,
    loading: false,
    submitting: false,
    error: null,
  });
  const lookupControllerRef = useRef<AbortController | null>(null);
  const lookupSequenceRef = useRef(0);
  const submissionKeyRef = useRef<string | null>(null);
  const submittingRef = useRef(false);

  const tenantId = scope.tenantId.trim();
  const datasetId = scope.datasetId?.trim() ?? "";
  const actorToken = scope.actorToken.trim();
  const stableScope = useMemo(
    () => ({
      tenantId,
      datasetId: datasetId || undefined,
      actorToken,
    }),
    [actorToken, datasetId, tenantId],
  );
  const hintExpiresAt = serverApprovalRequired?.expires_at;
  const hintPolicyId = serverApprovalRequired?.policy_id;
  const hintPolicyName = serverApprovalRequired?.policy_name;
  const hintPolicyRevision = serverApprovalRequired?.policy_revision;
  const hintRequestExpiryMinutes = serverApprovalRequired?.request_expiry_minutes;
  const hintRequiredApprovals = serverApprovalRequired?.required_approvals;
  const hintResourceScope = serverApprovalRequired?.resource_scope;
  const hasServerApprovalHint =
    serverApprovalRequired !== null && serverApprovalRequired !== undefined;
  const stableServerApprovalRequired = useMemo(
    () =>
      hasServerApprovalHint
        ? {
            expires_at: hintExpiresAt,
            policy_id: hintPolicyId,
            policy_name: hintPolicyName,
            policy_revision: hintPolicyRevision,
            request_expiry_minutes: hintRequestExpiryMinutes,
            required_approvals: hintRequiredApprovals,
            resource_scope: hintResourceScope,
          }
        : null,
    [
      hasServerApprovalHint,
      hintExpiresAt,
      hintPolicyId,
      hintPolicyName,
      hintPolicyRevision,
      hintRequestExpiryMinutes,
      hintRequiredApprovals,
      hintResourceScope,
    ],
  );

  const applyServerApprovalHint = useCallback((hint: DatasetAclApprovalRequiredHint) => {
    const policy = policyFactsFromApprovalRequiredHint(hint);
    setState((current) => ({
      ...current,
      mode: "approval",
      policy,
      loading: false,
      submitting: false,
      error: policy ? null : "服务端要求审批，但未返回可用的审批规则 ID。",
    }));
  }, []);

  const refresh = useCallback(async () => {
    lookupControllerRef.current?.abort();
    const controller = new AbortController();
    lookupControllerRef.current = controller;
    const sequence = ++lookupSequenceRef.current;
    submissionKeyRef.current = null;

    if (!tenantId || !datasetId || !actorToken) {
      setState({
        mode: "error",
        policy: null,
        request: null,
        loading: false,
        submitting: false,
        error: "缺少租户、知识库或身份凭据，无法读取审批规则。",
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
        { status: "active", actionType: "dataset_acl_disable" },
        { signal: controller.signal },
      );
      if (controller.signal.aborted || sequence !== lookupSequenceRef.current) return;
      if (stableServerApprovalRequired) {
        applyServerApprovalHint(stableServerApprovalRequired);
        return;
      }
      const selected = selectDatasetAclApprovalPolicy(page.items, datasetId);
      setState({
        mode: selected ? "approval" : "direct",
        policy: selected ? projectDatasetAclApprovalPolicyFacts(selected) : null,
        request: null,
        loading: false,
        submitting: false,
        error: null,
      });
    } catch (error) {
      if (controller.signal.aborted || sequence !== lookupSequenceRef.current) return;
      if (stableServerApprovalRequired) {
        applyServerApprovalHint(stableServerApprovalRequired);
        return;
      }
      setState({
        mode: "error",
        policy: null,
        request: null,
        loading: false,
        submitting: false,
        error: safeApprovalReadError(error),
      });
    } finally {
      if (lookupControllerRef.current === controller) lookupControllerRef.current = null;
    }
  }, [
    actorToken,
    applyServerApprovalHint,
    datasetId,
    stableServerApprovalRequired,
    stableScope,
    tenantId,
  ]);

  useEffect(() => {
    if (!visible) {
      lookupControllerRef.current?.abort();
      lookupControllerRef.current = null;
      lookupSequenceRef.current += 1;
      submissionKeyRef.current = null;
      submittingRef.current = false;
      setState({
        mode: "idle",
        policy: null,
        request: null,
        loading: false,
        submitting: false,
        error: null,
      });
      return;
    }
    void refresh();
    return () => lookupControllerRef.current?.abort();
  }, [refresh, visible]);

  useEffect(() => {
    if (visible && stableServerApprovalRequired)
      applyServerApprovalHint(stableServerApprovalRequired);
  }, [applyServerApprovalHint, stableServerApprovalRequired, visible]);

  const submitApproval = useCallback(
    async ({ expected_acl_revision, reason }: DatasetAclApprovalSubmission) => {
      if (submittingRef.current) return null;
      const normalizedReason = reason.trim();
      if (!datasetId || !tenantId || !actorToken) {
        setState((current) => ({
          ...current,
          mode: "error",
          error: "缺少租户、知识库或身份凭据，无法提交审批申请。",
        }));
        return null;
      }
      if (aclRevision !== null && aclRevision !== undefined && !validRevision(aclRevision)) {
        setState((current) => ({
          ...current,
          error: "服务端未返回有效的 ACL revision，已阻止提交。",
        }));
        return null;
      }
      if (currentAclMode !== "dataset_acl") {
        setState((current) => ({
          ...current,
          error: "当前知识库未处于持久 ACL 模式，已阻止提交。",
        }));
        return null;
      }
      if (!validRevision(expected_acl_revision) || !normalizedReason) {
        setState((current) => ({
          ...current,
          error: "审批申请缺少有效的 ACL revision 或停用原因。",
        }));
        return null;
      }
      const policyId = state.policy?.id;
      if (!policyId) {
        setState((current) => ({
          ...current,
          mode: "approval",
          error: "服务端未返回可用的审批规则 ID，已阻止提交。",
        }));
        return null;
      }

      submittingRef.current = true;
      const idempotencyKey = submissionKeyRef.current ?? createApprovalIdempotencyKey();
      submissionKeyRef.current = idempotencyKey;
      setState((current) => ({ ...current, submitting: true, error: null }));
      try {
        const request = await createApprovalRequest(
          stableScope,
          {
            policy_id: policyId,
            resource_type: "knowledge_base",
            resource_id: datasetId,
            snapshot: {
              dataset_id: datasetId,
              expected_acl_revision,
              current_acl_mode: currentAclMode,
            },
            reason: normalizedReason,
          },
          { idempotencyKey } satisfies ApprovalRequestOptions,
        );
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
        setState((current) => ({
          ...current,
          mode: "approval",
          loading: false,
          submitting: false,
          error: safeApprovalSubmitError(error),
        }));
        return null;
      } finally {
        submittingRef.current = false;
      }
    },
    [aclRevision, actorToken, currentAclMode, datasetId, stableScope, state.policy?.id, tenantId],
  );

  return {
    ...state,
    refresh,
    submitApproval,
    approvalMode: state.mode === "approval" || state.mode === "submitted",
  };
}
