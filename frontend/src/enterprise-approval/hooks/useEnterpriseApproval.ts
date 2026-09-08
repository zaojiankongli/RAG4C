import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { ApiError } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  approveApprovalRequest,
  cancelApprovalRequest,
  createApprovalIdempotencyKey,
  createApprovalPolicy,
  createApprovalRequest,
  consumeApprovalTicket,
  disableApprovalPolicy,
  fetchApprovalPolicies,
  fetchApprovalRequest,
  fetchApprovalRequests,
  rejectApprovalRequest,
  updateApprovalPolicy,
  type ApprovalPolicyInput,
  type ApprovalRequestListQuery,
  type ApprovalRequestOptions,
  type CancelApprovalRequestInput,
  type CreateApprovalRequestInput,
  type DisableApprovalPolicyInput,
  type RejectApprovalRequestInput,
  type UpdateApprovalPolicyInput,
} from "../api/enterpriseApprovalApi";
import { isApprovalExecutionEligible, projectApprovalSnapshot } from "../enterpriseApprovalModel";
import type {
  ApprovalEvidence,
  ApprovalMutationResponse,
  ApprovalPolicy,
  ApprovalPolicyPage,
  ApprovalRequest,
  ApprovalRequestDetail,
  ApprovalRequestPage,
} from "../enterpriseApprovalModel";

export type ApprovalResourceStatus =
  "idle" | "loading" | "ready" | "unavailable" | "unauthorized" | "forbidden" | "error";

export interface ApprovalResourceError {
  state: Exclude<ApprovalResourceStatus, "idle" | "loading" | "ready">;
  title: string;
  description: string;
  canRetry: boolean;
  status?: number;
  code?: string;
}

export interface ApprovalMutationError {
  title: string;
  message: string;
  retryAvailable: boolean;
  needsRefresh: boolean;
  status?: number;
  code?: string;
}

export interface ApprovalFilters {
  workspaceId: string;
  status: ApprovalRequestListQuery["status"];
  actionType: ApprovalRequestListQuery["actionType"];
  myApproval: "all" | "pending" | "mine";
  query: string;
}

export interface EnterpriseApprovalCenterOptions {
  enabled?: boolean;
  actorId?: string;
  actorRole?: string;
}

export interface ApprovalExecutionTarget {
  request_id: string;
  revision: number;
  action_type: string;
  resource_type: string;
  resource_id: string;
  change_facts: Record<string, unknown>;
}

export interface EnterpriseApprovalWorkspace {
  status: ApprovalResourceStatus;
  requests: ApprovalRequestPage;
  policies: ApprovalPolicyPage;
  evidence: ApprovalEvidence;
  filters: ApprovalFilters;
  setFilters: Dispatch<SetStateAction<ApprovalFilters>>;
  error: ApprovalResourceError | null;
  selectedRequest: ApprovalRequestDetail | null;
  selectedRequestLoading: boolean;
  detailError: ApprovalResourceError | null;
  loading: boolean;
  reload: (nextFilters?: ApprovalFilters) => Promise<void>;
  openRequest: (requestId: string) => Promise<void>;
  closeRequest: () => void;
  mutation: { saving: boolean; error: ApprovalMutationError | null; success: string | null };
  execution: { saving: boolean; error: ApprovalMutationError | null; success: string | null };
  executionTicketAvailable: boolean;
  executionTarget: ApprovalExecutionTarget | null;
  approve: (requestId: string, revision: number) => Promise<ApprovalMutationResponse | null>;
  consumeExecutionTicket: () => Promise<ApprovalRequest | null>;
  clearExecutionTicket: () => void;
  reject: (requestId: string, revision: number, comment: string) => Promise<ApprovalRequest | null>;
  cancel: (requestId: string, revision: number, reason: string) => Promise<ApprovalRequest | null>;
  createRequest: (input: CreateApprovalRequestInput) => Promise<ApprovalRequest | null>;
  createPolicy: (input: ApprovalPolicyInput) => Promise<ApprovalPolicy | null>;
  updatePolicy: (
    policyId: string,
    input: UpdateApprovalPolicyInput,
  ) => Promise<ApprovalPolicy | null>;
  disablePolicy: (
    policyId: string,
    input: DisableApprovalPolicyInput,
  ) => Promise<ApprovalPolicy | null>;
  retry: () => Promise<ApprovalRequest | ApprovalPolicy | ApprovalMutationResponse | null>;
  clearMutation: () => void;
}

const EMPTY_EVIDENCE: ApprovalEvidence = {
  pending_count: null,
  my_pending_count: null,
  active_policy_count: null,
  catalog_revision: null,
  execution_adapter_status: "execution_adapter_not_connected",
};
const EMPTY_REQUEST_PAGE: ApprovalRequestPage = {
  items: [],
  count: 0,
  next_cursor: null,
  evidence: EMPTY_EVIDENCE,
};
const EMPTY_POLICY_PAGE: ApprovalPolicyPage = {
  items: [],
  count: 0,
  next_cursor: null,
  evidence: EMPTY_EVIDENCE,
};
const DEFAULT_FILTERS: ApprovalFilters = {
  workspaceId: "",
  status: "all",
  actionType: "all",
  myApproval: "all",
  query: "",
};

function errorCode(error: ApiError): string | undefined {
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

function isMigrationCode(code?: string): boolean {
  return Boolean(code && code.toLowerCase().includes("migration"));
}

export function projectApprovalReadError(error: unknown): ApprovalResourceError {
  if (error instanceof ApiError) {
    const code = errorCode(error);
    if (isMigrationCode(code))
      return {
        state: "unavailable",
        title: "审批数据库尚未就绪",
        description: "请完成 0025 enterprise approval control 迁移后再读取审批事实",
        canRetry: true,
        status: error.status,
        code,
      };
    if (error.status === 401)
      return {
        state: "unauthorized",
        title: "审批身份已失效",
        description: "重新连接企业身份后再读取审批事实",
        canRetry: false,
        status: error.status,
        code,
      };
    if (error.status === 403)
      return {
        state: "forbidden",
        title: "没有读取审批中心的权限",
        description: "当前身份未获得企业审批读取权限",
        canRetry: false,
        status: error.status,
        code,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        state: "unavailable",
        title: "审批服务暂不可用",
        description: "服务没有返回可安全展示的审批事实",
        canRetry: true,
        status: error.status,
        code,
      };
  }
  return {
    state: "error",
    title: "审批事实读取失败",
    description: "服务没有返回可安全展示的审批结果",
    canRetry: true,
    code: error instanceof Error ? error.message : undefined,
  };
}

export function projectApprovalMutationError(error: unknown): ApprovalMutationError {
  if (error instanceof ApiError) {
    const code = errorCode(error);
    if (isMigrationCode(code))
      return {
        title: "审批数据库尚未就绪",
        message: "请完成 0025 enterprise approval control 迁移后再提交审批变更",
        retryAvailable: false,
        needsRefresh: false,
        status: error.status,
        code,
      };
    if (error.status === 401)
      return {
        title: "身份已失效",
        message: "重新连接企业身份后再提交审批变更",
        retryAvailable: false,
        needsRefresh: false,
        status: error.status,
        code,
      };
    if (error.status === 403)
      return {
        title: "审批权限不足",
        message: "当前身份不是该申请的有效审批人，或没有管理审批规则的权限",
        retryAvailable: false,
        needsRefresh: false,
        status: error.status,
        code,
      };
    if (error.status === 409 || error.status === 412)
      return {
        title: "审批事实已变化",
        message: "申请或规则的 revision 已变化，请刷新后重新操作",
        retryAvailable: false,
        needsRefresh: true,
        status: error.status,
        code,
      };
    if (error.status === 422)
      return {
        title: "审批请求无效",
        message: "服务端拒绝了当前审批参数，请核对后重试",
        retryAvailable: false,
        needsRefresh: false,
        status: error.status,
        code,
      };
    if (error.status === 503 || error.kind === "network" || error.kind === "timeout")
      return {
        title: "审批结果未确认",
        message: "企业审批服务暂不可用，可使用同一请求安全重试",
        retryAvailable: true,
        needsRefresh: false,
        status: error.status,
        code,
      };
  }
  return {
    title: "审批操作失败",
    message: "审批操作未完成，请稍后重试",
    retryAvailable: false,
    needsRefresh: false,
  };
}

function projectApprovalExecutionError(error: unknown): ApprovalMutationError {
  const projected = projectApprovalMutationError(error);
  const status = error instanceof ApiError ? error.status : projected.status;
  const code = errorCode(error instanceof ApiError ? error : ({} as ApiError));
  if (code === "execution_failed" || status === 502) {
    return {
      ...projected,
      title: "已批准变更执行失败",
      message: "执行适配器未完成该批准变更；授权已消费，请依据执行失败事实处理。",
      retryAvailable: false,
      needsRefresh: true,
      status,
      code: code ?? "execution_failed",
    };
  }
  return projected;
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

type MutationResult = ApprovalRequest | ApprovalPolicy | ApprovalMutationResponse;
type MutationOperation = (options: ApprovalRequestOptions) => Promise<MutationResult>;
interface PendingMutation {
  operation: MutationOperation;
  success: string;
  key: string;
}

interface TransientApprovalExecutionTicket extends ApprovalExecutionTarget {
  ticket: string;
  idempotencyKey: string;
}

function executionTargetFromResult(
  result: ApprovalMutationResponse,
): TransientApprovalExecutionTicket | null {
  const execution = result.execution;
  if (!isApprovalExecutionEligible(result.request, execution)) return null;
  if (!execution.ticket?.trim()) return null;
  return {
    ticket: execution.ticket,
    idempotencyKey: createApprovalIdempotencyKey(),
    request_id: result.request.id,
    revision: result.request.revision,
    action_type: result.request.action_type,
    resource_type: result.request.resource_type,
    resource_id: result.request.resource_id,
    change_facts: projectApprovalSnapshot(result.request.snapshot),
  };
}

function executionTargetView(ticket: TransientApprovalExecutionTicket): ApprovalExecutionTarget {
  return {
    request_id: ticket.request_id,
    revision: ticket.revision,
    action_type: ticket.action_type,
    resource_type: ticket.resource_type,
    resource_id: ticket.resource_id,
    change_facts: ticket.change_facts,
  };
}

function applyActorFacts(
  detail: ApprovalRequestDetail,
  actorId?: string,
  actorRole?: string,
): ApprovalRequestDetail {
  if (!actorId) return detail;
  if (detail.requester.id === actorId)
    return { ...detail, my_approval: "not_required", my_approval_eligible: false };
  if (detail.my_approval !== undefined && detail.my_approval !== null) return detail;
  const eligible =
    detail.approvers?.some(
      (approver) =>
        (approver.kind === "account" && approver.ref === actorId) ||
        (approver.kind === "role" && approver.ref === actorRole),
    ) ?? false;
  return eligible ? { ...detail, my_approval: "pending", my_approval_eligible: true } : detail;
}

export function useEnterpriseApprovalCenter(
  scope: EnterpriseScope,
  options: EnterpriseApprovalCenterOptions = {},
): EnterpriseApprovalWorkspace {
  const enabled = options.enabled ?? true;
  const actorId = options.actorId?.trim();
  const actorRole = options.actorRole?.trim();
  const [status, setStatus] = useState<ApprovalResourceStatus>(enabled ? "idle" : "unavailable");
  const [requests, setRequests] = useState<ApprovalRequestPage>(EMPTY_REQUEST_PAGE);
  const [policies, setPolicies] = useState<ApprovalPolicyPage>(EMPTY_POLICY_PAGE);
  const [filters, setFilters] = useState<ApprovalFilters>(DEFAULT_FILTERS);
  const [error, setError] = useState<ApprovalResourceError | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<ApprovalRequestDetail | null>(null);
  const [selectedRequestLoading, setSelectedRequestLoading] = useState(false);
  const [detailError, setDetailError] = useState<ApprovalResourceError | null>(null);
  const [mutation, setMutation] = useState<{
    saving: boolean;
    error: ApprovalMutationError | null;
    success: string | null;
  }>({ saving: false, error: null, success: null });
  const [execution, setExecution] = useState<{
    saving: boolean;
    error: ApprovalMutationError | null;
    success: string | null;
  }>({ saving: false, error: null, success: null });
  const [executionTicketAvailable, setExecutionTicketAvailable] = useState(false);
  const [executionTarget, setExecutionTarget] = useState<ApprovalExecutionTarget | null>(null);
  const sequenceRef = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const mutationBusyRef = useRef(false);
  const pendingMutationRef = useRef<PendingMutation | null>(null);
  const executionTicketRef = useRef<TransientApprovalExecutionTicket | null>(null);
  const executionBusyRef = useRef(false);
  const executionGenerationRef = useRef(0);

  const clearExecutionTicket = useCallback(() => {
    executionGenerationRef.current += 1;
    executionTicketRef.current = null;
    setExecutionTicketAvailable(false);
    setExecutionTarget(null);
    setExecution({ saving: false, error: null, success: null });
  }, []);

  const load = useCallback(
    async (nextFilters: ApprovalFilters = filters): Promise<void> => {
      if (!enabled) return;
      loadControllerRef.current?.abort();
      const controller = new AbortController();
      loadControllerRef.current = controller;
      const sequence = ++sequenceRef.current;
      setStatus("loading");
      setError(null);
      const requestQuery: ApprovalRequestListQuery = {
        status: nextFilters.status,
        actionType: nextFilters.actionType,
        pendingForMe: nextFilters.myApproval === "pending",
        mine: nextFilters.myApproval === "mine",
        limit: 50,
      };
      try {
        const [requestPage, policyPage] = await Promise.all([
          fetchApprovalRequests(scope, requestQuery, { signal: controller.signal }),
          fetchApprovalPolicies(scope, { limit: 50 }, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted || sequence !== sequenceRef.current) return;
        setRequests(requestPage);
        setPolicies(policyPage);
        setStatus("ready");
      } catch (reason) {
        if (controller.signal.aborted || sequence !== sequenceRef.current || isAbort(reason))
          return;
        const projected = projectApprovalReadError(reason);
        setStatus(projected.state);
        setError(projected);
        setRequests(EMPTY_REQUEST_PAGE);
        setPolicies(EMPTY_POLICY_PAGE);
      } finally {
        if (loadControllerRef.current === controller) loadControllerRef.current = null;
      }
    },
    [enabled, filters, scope],
  );

  useEffect(() => {
    if (!enabled) {
      setStatus("unavailable");
      setRequests(EMPTY_REQUEST_PAGE);
      setPolicies(EMPTY_POLICY_PAGE);
      clearExecutionTicket();
      return () => undefined;
    }
    void load();
    return () => {
      loadControllerRef.current?.abort();
      detailControllerRef.current?.abort();
    };
  }, [clearExecutionTicket, enabled, load]);

  useEffect(() => {
    return () => {
      executionTicketRef.current = null;
      executionBusyRef.current = false;
      executionGenerationRef.current += 1;
    };
  }, []);

  const openRequest = useCallback(
    async (requestId: string) => {
      detailControllerRef.current?.abort();
      const controller = new AbortController();
      detailControllerRef.current = controller;
      setSelectedRequestLoading(true);
      setDetailError(null);
      try {
        const detail = await fetchApprovalRequest(scope, requestId, { signal: controller.signal });
        if (!controller.signal.aborted)
          setSelectedRequest(applyActorFacts(detail, actorId, actorRole));
      } catch (reason) {
        if (!controller.signal.aborted && !isAbort(reason))
          setDetailError(projectApprovalReadError(reason));
      } finally {
        if (detailControllerRef.current === controller) {
          detailControllerRef.current = null;
          setSelectedRequestLoading(false);
        }
      }
    },
    [actorId, actorRole, scope],
  );

  const closeRequest = useCallback(() => {
    detailControllerRef.current?.abort();
    setSelectedRequest(null);
    setDetailError(null);
    setSelectedRequestLoading(false);
  }, []);

  const runMutation = useCallback(
    async (
      operation: MutationOperation,
      success: string,
      key = createApprovalIdempotencyKey(),
      onResult?: (result: MutationResult) => void,
    ): Promise<MutationResult | null> => {
      if (mutationBusyRef.current) return null;
      mutationBusyRef.current = true;
      pendingMutationRef.current = { operation, success, key };
      setMutation({ saving: true, error: null, success: null });
      try {
        const result = await operation({ idempotencyKey: key });
        onResult?.(result);
        await load();
        pendingMutationRef.current = null;
        setMutation({ saving: false, error: null, success });
        return result;
      } catch (reason) {
        const projected = projectApprovalMutationError(reason);
        if (!projected.retryAvailable) pendingMutationRef.current = null;
        setMutation({ saving: false, error: projected, success: null });
        return null;
      } finally {
        mutationBusyRef.current = false;
      }
    },
    [load],
  );

  const approve = useCallback(
    async (requestId: string, revision: number): Promise<ApprovalMutationResponse | null> => {
      clearExecutionTicket();
      let deliveredTicket: TransientApprovalExecutionTicket | null = null;
      const result = (await runMutation(
        (options) => approveApprovalRequest(scope, requestId, { revision }, options),
        "审批已同意",
        undefined,
        (delivery) => {
          if (!("request" in delivery) || !("execution" in delivery)) return;
          const ticket = executionTargetFromResult(delivery as ApprovalMutationResponse);
          if (!ticket) return;
          deliveredTicket = ticket;
          executionTicketRef.current = ticket;
          setExecutionTicketAvailable(true);
          setExecutionTarget(executionTargetView(ticket));
          setExecution({ saving: false, error: null, success: null });
        },
      )) as ApprovalMutationResponse | null;
      if (!result) return null;

      const ticket = deliveredTicket ?? executionTargetFromResult(result);
      if (!ticket) return result;
      setSelectedRequest((current) =>
        current && current.id === result.request.id
          ? {
              ...current,
              ...result.request,
              execution_adapter_status: result.execution.adapter,
            }
          : current,
      );
      return result;
    },
    [clearExecutionTicket, runMutation, scope],
  );

  const consumeExecutionTicket = useCallback(async (): Promise<ApprovalRequest | null> => {
    if (executionBusyRef.current) return null;
    const ticket = executionTicketRef.current;
    if (!ticket) return null;

    executionBusyRef.current = true;
    const executionGeneration = ++executionGenerationRef.current;
    setExecutionTicketAvailable(false);
    setExecution({ saving: true, error: null, success: null });
    try {
      const result = await consumeApprovalTicket(
        scope,
        ticket.request_id,
        {
          ticket: ticket.ticket,
          revision: ticket.revision,
          action_type: ticket.action_type,
          resource_type: ticket.resource_type,
          resource_id: ticket.resource_id,
        },
        { idempotencyKey: ticket.idempotencyKey },
      );
      executionTicketRef.current = null;
      setExecutionTicketAvailable(false);
      await load();
      await openRequest(ticket.request_id);
      if (executionGenerationRef.current !== executionGeneration) return result;
      setExecutionTarget(executionTargetView(ticket));
      setExecution({ saving: false, error: null, success: "已执行批准变更" });
      return result;
    } catch (reason) {
      try {
        await load();
        await openRequest(ticket.request_id);
      } catch {
        // The stable execution error below is authoritative even if the refresh also fails.
      }
      if (executionGenerationRef.current === executionGeneration) {
        const retryable =
          reason instanceof ApiError &&
          (["network", "timeout", "aborted"].includes(reason.kind) || reason.status === 503);
        if (retryable) {
          executionTicketRef.current = ticket;
          setExecutionTicketAvailable(true);
        } else {
          executionTicketRef.current = null;
          setExecutionTicketAvailable(false);
        }
        setExecutionTarget(executionTargetView(ticket));
        setExecution({
          saving: false,
          error: projectApprovalExecutionError(reason),
          success: null,
        });
      }
      return null;
    } finally {
      executionBusyRef.current = false;
    }
  }, [load, openRequest, scope]);
  const reject = useCallback(
    (requestId: string, revision: number, comment: string) =>
      runMutation(
        (options) => rejectApprovalRequest(scope, requestId, { revision, comment }, options),
        "审批已拒绝",
      ) as Promise<ApprovalRequest | null>,
    [runMutation, scope],
  );
  const cancel = useCallback(
    (requestId: string, revision: number, reason: string) =>
      runMutation(
        (options) => cancelApprovalRequest(scope, requestId, { revision, reason }, options),
        "审批申请已取消",
      ) as Promise<ApprovalRequest | null>,
    [runMutation, scope],
  );
  const createRequest = useCallback(
    (input: CreateApprovalRequestInput) =>
      runMutation(
        (options) => createApprovalRequest(scope, input, options),
        "审批申请已提交",
      ) as Promise<ApprovalRequest | null>,
    [runMutation, scope],
  );
  const createPolicy = useCallback(
    (input: ApprovalPolicyInput) =>
      runMutation(
        (options) => createApprovalPolicy(scope, input, options),
        "审批规则已创建",
      ) as Promise<ApprovalPolicy | null>,
    [runMutation, scope],
  );
  const updatePolicy = useCallback(
    (policyId: string, input: UpdateApprovalPolicyInput) =>
      runMutation(
        (options) => updateApprovalPolicy(scope, policyId, input, options),
        "审批规则已更新",
      ) as Promise<ApprovalPolicy | null>,
    [runMutation, scope],
  );
  const disablePolicy = useCallback(
    (policyId: string, input: DisableApprovalPolicyInput) =>
      runMutation(
        (options) => disableApprovalPolicy(scope, policyId, input, options),
        "审批规则已停用",
      ) as Promise<ApprovalPolicy | null>,
    [runMutation, scope],
  );
  const retry = useCallback((): Promise<MutationResult | null> => {
    const pending = pendingMutationRef.current;
    return pending
      ? runMutation(pending.operation, pending.success, pending.key)
      : Promise.resolve(null);
  }, [runMutation]);
  const clearMutation = useCallback(() => {
    pendingMutationRef.current = null;
    setMutation((current) => ({ ...current, error: null, success: null }));
  }, []);

  return {
    status,
    requests,
    policies,
    evidence: requests.evidence,
    filters,
    setFilters,
    error,
    selectedRequest,
    selectedRequestLoading,
    detailError,
    loading: status === "loading",
    reload: load,
    openRequest,
    closeRequest,
    mutation,
    execution,
    executionTicketAvailable,
    executionTarget,
    approve,
    consumeExecutionTicket,
    clearExecutionTicket,
    reject,
    cancel,
    createRequest,
    createPolicy,
    updatePolicy,
    disablePolicy,
    retry,
    clearMutation,
  };
}

export type {
  ApprovalPolicy,
  ApprovalMutationResponse,
  ApprovalRequest,
  ApprovalRequestDetail,
  CancelApprovalRequestInput,
  DisableApprovalPolicyInput,
  RejectApprovalRequestInput,
  UpdateApprovalPolicyInput,
};
