export type ApprovalActionType =
  | "catalog_upgrade"
  | "membership_bootstrap"
  | "dataset_acl_disable"
  | "member_role_change"
  | "workspace_authorization_mode_change"
  | "dataset_workspace_transfer"
  | "knowledge_base_release_publish"
  | "knowledge_base_release_rollback"
  | "identity_provider_disable"
  | "audit_retention_execute"
  | (string & {});

export type ApprovalPolicyStatus = "active" | "disabled" | (string & {});
export type ApprovalRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "cancelled"
  | "expired"
  | "executing"
  | "executed"
  | "execution_failed"
  | (string & {});
export type ApprovalDecision = "approved" | "rejected" | (string & {});
export type ApprovalApproverKind = "account" | "role" | "group" | (string & {});
export type ApprovalExecutionAdapterStatus =
  "execution_adapter_not_connected" | "connected" | (string & {});
export type ApprovalMyApprovalState =
  "pending" | "approved" | "rejected" | "not_required" | (string & {});

export interface ApprovalActor {
  id: string;
  name: string;
  email?: string | null;
  role?: string | null;
}

export interface ApprovalPolicyApprover {
  kind: ApprovalApproverKind;
  ref: string;
  label?: string | null;
  status?: string | null;
  revision?: number | null;
}

export interface ApprovalPolicy {
  id: string;
  name: string;
  action_type: ApprovalActionType;
  resource_scope: string | null;
  status: ApprovalPolicyStatus;
  required_approvals: number;
  request_expiry_minutes: number;
  approvers: ApprovalPolicyApprover[];
  revision: number;
  created_at?: string | null;
  updated_at?: string | null;
  created_by?: ApprovalActor | null;
  updated_by?: ApprovalActor | null;
}

export interface ApprovalRequest {
  id: string;
  policy_id: string | null;
  action_type: ApprovalActionType;
  resource_type: string;
  resource_id: string;
  resource_name?: string | null;
  requester: ApprovalActor;
  reason: string;
  status: ApprovalRequestStatus;
  required_approvals: number;
  received_approvals: number;
  expires_at: string;
  created_at: string;
  updated_at?: string | null;
  revision: number;
  my_approval?: ApprovalMyApprovalState | null;
  my_approval_eligible?: boolean | null;
  execution_adapter_status: ApprovalExecutionAdapterStatus;
  execution_ticket_available?: boolean | null;
  snapshot: Record<string, unknown>;
}

export interface ApprovalDecisionRecord {
  id?: string;
  approver: ApprovalActor;
  decision: ApprovalDecision;
  comment?: string | null;
  decided_at: string;
  revision?: number | null;
}

export interface ApprovalProcessEvent {
  id: string;
  actor: ApprovalActor;
  status: string;
  occurred_at: string;
  comment?: string | null;
}

export interface ApprovalRequestDetail extends ApprovalRequest {
  approvers?: ApprovalPolicyApprover[];
  decisions: ApprovalDecisionRecord[];
  process: ApprovalProcessEvent[];
}

export interface ApprovalExecutionDelivery {
  state: string;
  adapter: ApprovalExecutionAdapterStatus;
  /** Raw ticket is intentionally scoped to the mutation response and never to request facts. */
  ticket?: string;
}

export interface ApprovalExecutionRequestScope {
  action_type: string;
  resource_type: string;
}

export interface ApprovalExecutionEvidence {
  state: string;
  adapter: ApprovalExecutionAdapterStatus;
  ticket?: string | null;
}

export interface ApprovalMutationResponse {
  request: ApprovalRequest;
  execution: ApprovalExecutionDelivery;
  decision?: ApprovalDecisionRecord | null;
}

export interface ApprovalEvidence {
  pending_count: number | null;
  my_pending_count: number | null;
  active_policy_count: number | null;
  catalog_revision: string | null;
  execution_adapter_status: ApprovalExecutionAdapterStatus;
}

export interface ApprovalPage<T> {
  items: T[];
  count: number;
  next_cursor: string | null;
  evidence: ApprovalEvidence;
}

export type ApprovalRequestPage = ApprovalPage<ApprovalRequest>;
export type ApprovalPolicyPage = ApprovalPage<ApprovalPolicy>;

const SECRET_KEY =
  /(?:token|ticket|secret|password|credential|authorization|invite[_-]?link|invite[_-]?token|access[_-]?key|private[_-]?key|client[_-]?secret|code|state|verifier)/i;
const SECRET_VALUE = /(?:^|[^a-z0-9])(ticket|token|secret)(?:$|[^a-z0-9])/i;
const DEFAULT_EXECUTION_ADAPTER: ApprovalExecutionAdapterStatus = "execution_adapter_not_connected";
const TRANSIENT_EXECUTION_SCOPE_KEYS = new Set([
  "dataset_acl_disable:knowledge_base",
  "member_role_change:tenant_member",
  "workspace_authorization_mode_change:tenant_workspace",
  "dataset_workspace_transfer:knowledge_base",
  "knowledge_base_release_publish:knowledge_base",
  "knowledge_base_release_rollback:knowledge_base",
]);

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function positiveInteger(value: unknown): number | null {
  const result = nonNegativeInteger(value);
  return result !== null && result > 0 ? result : null;
}

function actor(value: unknown): ApprovalActor | null {
  const source = record(value);
  if (!source) return null;
  const id = text(source.id ?? source.account_id ?? source.actor_id);
  const name = text(source.name ?? source.display_name ?? source.actor_name);
  if (!id || !name) return null;
  return {
    id,
    name,
    ...(source.email === null || text(source.email)
      ? { email: source.email === null ? null : text(source.email) }
      : {}),
    ...(source.role === null || text(source.role)
      ? { role: source.role === null ? null : text(source.role) }
      : {}),
  };
}

function fallbackActor(id: unknown): ApprovalActor | null {
  const value = text(id);
  return value ? { id: value, name: value } : null;
}

function actorFromFields(source: Record<string, unknown>, prefix: string): ApprovalActor | null {
  return actor({
    id: source[prefix + "_id"],
    name: source[prefix + "_name"],
    email: source[prefix + "_email"],
    role: source[prefix + "_role"],
  });
}

function projectAdapterStatus(value: unknown): ApprovalExecutionAdapterStatus {
  return text(value) ?? DEFAULT_EXECUTION_ADAPTER;
}

export function projectApprovalExecution(input: unknown): ApprovalExecutionDelivery {
  const source = record(input) ?? {};
  const ticket = text(source.ticket);
  return {
    state: text(source.state ?? source.status) ?? "not_returned",
    adapter: projectAdapterStatus(
      source.adapter ?? source.execution_adapter_status ?? source.adapter_status,
    ),
    ...(ticket ? { ticket } : {}),
  };
}

export function isApprovalExecutionEligible(
  request: ApprovalExecutionRequestScope,
  execution: ApprovalExecutionEvidence,
): boolean {
  return (
    execution.adapter === "connected" &&
    execution.state === "ticket_issued" &&
    Boolean(execution.ticket?.trim()) &&
    TRANSIENT_EXECUTION_SCOPE_KEYS.has(`${request.action_type}:${request.resource_type}`)
  );
}

function projectEvidence(input: unknown): ApprovalEvidence {
  const source = record(input) ?? {};
  return {
    pending_count: nonNegativeInteger(source.pending_count),
    my_pending_count: nonNegativeInteger(source.my_pending_count),
    active_policy_count: nonNegativeInteger(source.active_policy_count),
    catalog_revision: text(source.catalog_revision ?? source.current_revision),
    execution_adapter_status: projectAdapterStatus(
      source.execution_adapter_status ?? source.execution_adapter ?? source.adapter_status,
    ),
  };
}

export function projectApprovalEvidence(input: unknown): ApprovalEvidence {
  return projectEvidence(input);
}

export function projectApprovalSnapshot(input: unknown): Record<string, unknown> {
  const source = record(input) ?? {};
  const project = (value: unknown, key?: string): unknown => {
    if (key && SECRET_KEY.test(key)) return "[已脱敏]";
    if (Array.isArray(value)) return value.map((item) => project(item));
    const nested = record(value);
    if (nested) {
      return Object.fromEntries(
        Object.entries(nested).map(([nestedKey, nestedValue]) => [
          nestedKey,
          project(nestedValue, nestedKey),
        ]),
      );
    }
    if (typeof value === "string") return SECRET_VALUE.test(value) ? "[已脱敏]" : value;
    if (value === null || typeof value === "number" || typeof value === "boolean") return value;
    return "[不可展示]";
  };
  return project(source) as Record<string, unknown>;
}

export interface ApprovalChangeFactEntry {
  key: string;
  label: string;
  value: string;
}

const CHANGE_FACT_LABELS: Record<string, string> = {
  target_account_id: "目标账号",
  expected_member_revision: "成员 revision",
  current_role: "当前角色",
  requested_role: "申请角色",
  current_status: "当前状态",
  dataset_id: "知识库 ID",
  expected_acl_revision: "ACL revision",
  current_acl_mode: "当前 ACL 模式",
  workspace_id: "Workspace ID",
  workspace_revision: "Workspace revision",
  policy_revision: "策略 revision",
  from_mode: "当前授权模式",
  target_mode: "目标授权模式",
  permission_model_version: "权限模型版本",
  permission_matrix_fingerprint: "权限矩阵指纹",
};

function changeFactValue(value: unknown): string {
  if (value === null) return "未返回";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value) ?? "[不可展示]";
  } catch {
    return "[不可展示]";
  }
}

export function approvalChangeFactEntries(
  snapshot: Record<string, unknown>,
): ApprovalChangeFactEntry[] {
  return Object.entries(projectApprovalSnapshot(snapshot)).map(([key, value]) => ({
    key,
    label: CHANGE_FACT_LABELS[key] ?? key,
    value: changeFactValue(value),
  }));
}

function projectApprover(value: unknown): ApprovalPolicyApprover | null {
  const source = record(value);
  if (!source) return null;
  const kind = text(source.kind ?? source.approver_kind);
  const ref = text(source.ref ?? source.approver_ref ?? source.reference);
  if (!kind || !ref) return null;
  return {
    kind,
    ref,
    ...(source.label === null || text(source.label)
      ? { label: source.label === null ? null : text(source.label) }
      : {}),
    ...(source.status === null || text(source.status)
      ? { status: source.status === null ? null : text(source.status) }
      : {}),
    ...(source.revision === null || nonNegativeInteger(source.revision) !== null
      ? { revision: source.revision === null ? null : nonNegativeInteger(source.revision) }
      : {}),
  };
}

export function projectApprovalPolicy(input: unknown): ApprovalPolicy | null {
  const outer = record(input);
  const source = record(outer?.policy) ?? outer;
  if (!source) return null;
  const id = text(source.id ?? source.policy_id);
  const name = text(source.name ?? source.policy_name);
  const actionType = text(source.action_type);
  const status = text(source.status);
  const required = positiveInteger(source.required_approvals);
  const expiry = positiveInteger(source.request_expiry_minutes ?? source.request_expiry);
  const revision = positiveInteger(source.revision);
  if (
    !id ||
    !name ||
    !actionType ||
    !status ||
    required === null ||
    expiry === null ||
    revision === null
  )
    return null;
  const rawApprovers = Array.isArray(source.approvers)
    ? source.approvers
    : Array.isArray(outer?.approvers)
      ? outer.approvers
      : [];
  return {
    id,
    name,
    action_type: actionType,
    resource_scope: source.resource_scope === null ? null : (text(source.resource_scope) ?? null),
    status,
    required_approvals: required,
    request_expiry_minutes: expiry,
    approvers: rawApprovers
      .map(projectApprover)
      .filter((item): item is ApprovalPolicyApprover => item !== null),
    revision,
    ...(source.created_at === null || text(source.created_at)
      ? { created_at: source.created_at === null ? null : text(source.created_at) }
      : {}),
    ...(source.updated_at === null || text(source.updated_at)
      ? { updated_at: source.updated_at === null ? null : text(source.updated_at) }
      : {}),
    ...(source.created_by === null || actor(source.created_by)
      ? { created_by: source.created_by === null ? null : actor(source.created_by) }
      : {}),
    ...(source.updated_by === null || actor(source.updated_by)
      ? { updated_by: source.updated_by === null ? null : actor(source.updated_by) }
      : {}),
  };
}

function cursor(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function projectApprovalPolicyPage(input: unknown): ApprovalPolicyPage {
  const source = record(input) ?? {};
  const items = (Array.isArray(source.items) ? source.items : [])
    .map(projectApprovalPolicy)
    .filter((item): item is ApprovalPolicy => item !== null);
  return {
    items,
    count: nonNegativeInteger(source.count) ?? items.length,
    next_cursor: cursor(source.next_cursor),
    evidence: projectEvidence(source.evidence),
  };
}

export function projectApprovalRequest(input: unknown): ApprovalRequest | null {
  const outer = record(input);
  const source = record(outer?.request) ?? outer;
  if (!source) return null;
  const id = text(source.id ?? source.request_id);
  const actionType = text(source.action_type);
  const resourceType = text(source.resource_type);
  const resourceId = text(source.resource_id);
  const reason = text(source.reason ?? source.requester_reason);
  const status = text(source.status);
  const expiresAt = text(source.expires_at);
  const createdAt = text(source.created_at ?? source.requested_at);
  const requester =
    actor(source.requester) ??
    actorFromFields(source, "requester") ??
    fallbackActor(source.requester_id);
  const required = positiveInteger(source.required_approvals);
  const received = nonNegativeInteger(source.received_approvals ?? source.approval_count);
  const revision = positiveInteger(source.revision);
  if (
    !id ||
    !actionType ||
    !resourceType ||
    !resourceId ||
    !reason ||
    !status ||
    !expiresAt ||
    !createdAt ||
    !requester ||
    required === null ||
    received === null ||
    revision === null
  )
    return null;
  return {
    id,
    policy_id: text(source.policy_id),
    action_type: actionType,
    resource_type: resourceType,
    resource_id: resourceId,
    ...(source.resource_name === null || text(source.resource_name)
      ? { resource_name: source.resource_name === null ? null : text(source.resource_name) }
      : {}),
    requester,
    reason,
    status,
    required_approvals: required,
    received_approvals: received,
    expires_at: expiresAt,
    created_at: createdAt,
    ...(source.updated_at === null || text(source.updated_at)
      ? { updated_at: source.updated_at === null ? null : text(source.updated_at) }
      : {}),
    revision,
    my_approval: text(source.my_approval),
    ...(typeof source.my_approval_eligible === "boolean"
      ? { my_approval_eligible: source.my_approval_eligible }
      : {}),
    execution_adapter_status: projectAdapterStatus(
      source.execution_adapter_status ?? source.execution_adapter ?? source.adapter_status,
    ),
    ...(typeof source.execution_ticket_available === "boolean"
      ? { execution_ticket_available: source.execution_ticket_available }
      : {}),
    snapshot: projectApprovalSnapshot(
      source.snapshot ?? source.request_snapshot ?? source.change_facts ?? {},
    ),
  };
}

function projectDecision(input: unknown): ApprovalDecisionRecord | null {
  const source = record(input);
  if (!source) return null;
  const approver =
    actor(source.approver) ??
    actorFromFields(source, "approver") ??
    fallbackActor(source.approver_id);
  const decision = text(source.decision);
  const decidedAt = text(source.decided_at ?? source.created_at);
  if (!approver || !decision || !decidedAt) return null;
  const revision = source.revision === null ? null : nonNegativeInteger(source.revision);
  return {
    ...(text(source.id) ? { id: text(source.id) ?? undefined } : {}),
    approver,
    decision,
    ...(source.comment === null || text(source.comment)
      ? { comment: source.comment === null ? null : text(source.comment) }
      : {}),
    decided_at: decidedAt,
    ...(revision !== null ? { revision } : {}),
  };
}

function projectProcessEvent(input: unknown): ApprovalProcessEvent | null {
  const source = record(input);
  if (!source) return null;
  const eventActor =
    actor(source.actor) ?? actorFromFields(source, "actor") ?? fallbackActor(source.actor_id);
  const id = text(source.id ?? source.event_id);
  const status = text(source.status ?? source.event);
  const occurredAt = text(source.occurred_at ?? source.created_at);
  if (!id || !eventActor || !status || !occurredAt) return null;
  return {
    id,
    actor: eventActor,
    status,
    occurred_at: occurredAt,
    ...(source.comment === null || text(source.comment)
      ? { comment: source.comment === null ? null : text(source.comment) }
      : {}),
  };
}

export function projectApprovalRequestDetail(input: unknown): ApprovalRequestDetail | null {
  const source = record(input);
  if (!source) return null;
  const request = projectApprovalRequest(source.request ?? source);
  if (!request) return null;
  const decisions = (Array.isArray(source.decisions) ? source.decisions : [])
    .map(projectDecision)
    .filter((item): item is ApprovalDecisionRecord => item !== null);
  const approvers = (Array.isArray(source.approvers) ? source.approvers : [])
    .map(projectApprover)
    .filter((item): item is ApprovalPolicyApprover => item !== null);
  const explicitProcess = Array.isArray(source.process)
    ? source.process
    : Array.isArray(source.timeline)
      ? source.timeline
      : null;
  const process = explicitProcess
    ? explicitProcess
        .map(projectProcessEvent)
        .filter((item): item is ApprovalProcessEvent => item !== null)
    : [
        {
          id: request.id + ":submitted",
          actor: request.requester,
          status: "submitted",
          occurred_at: request.created_at,
        },
        ...decisions.map((decision) => ({
          id: decision.id ?? request.id + ":decision:" + decision.approver.id,
          actor: decision.approver,
          status: decision.decision,
          occurred_at: decision.decided_at,
          ...(decision.comment ? { comment: decision.comment } : {}),
        })),
      ];
  const execution = record(source.execution);
  const adapter = execution?.adapter ?? request.execution_adapter_status;
  return {
    ...request,
    execution_adapter_status: projectAdapterStatus(adapter ?? request.execution_adapter_status),
    approvers,
    decisions,
    process,
  };
}

export function projectApprovalMutation(input: unknown): ApprovalMutationResponse | null {
  const source = record(input);
  if (!source) return null;
  const request = projectApprovalRequest(source.request ?? source);
  if (!request) return null;
  const decisionSource = source.decision;
  const decision = decisionSource === undefined ? undefined : projectDecision(decisionSource);
  return {
    request,
    execution: projectApprovalExecution(source.execution),
    ...(decision !== undefined ? { decision } : {}),
  };
}

export function projectApprovalRequestPage(input: unknown): ApprovalRequestPage {
  const source = record(input) ?? {};
  const items = (Array.isArray(source.items) ? source.items : [])
    .map(projectApprovalRequest)
    .filter((item): item is ApprovalRequest => item !== null);
  return {
    items,
    count: nonNegativeInteger(source.count) ?? items.length,
    next_cursor: cursor(source.next_cursor),
    evidence: projectEvidence(source.evidence),
  };
}

export function approvalActionLabel(action: string): string {
  const labels: Record<string, string> = {
    catalog_upgrade: "升级目录数据库",
    membership_bootstrap: "初始化成员体系",
    dataset_acl_disable: "停用知识库 ACL",
    member_role_change: "调整成员角色",
    identity_provider_disable: "停用身份提供商",
    audit_retention_execute: "执行审计保留策略",
    workspace_authorization_mode_change: "变更 Workspace 授权模式",
    dataset_workspace_transfer: "转移 Knowledge Base Workspace",
    knowledge_base_release_publish: "发布 Knowledge Base Release",
    knowledge_base_release_rollback: "回滚 Knowledge Base Release",
  };
  return labels[action] ?? action;
}

export function approvalStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待审批",
    approved: "已批准",
    rejected: "已拒绝",
    cancelled: "已取消",
    expired: "已过期",
    executed: "已执行",
    execution_failed: "执行失败",
    active: "启用",
    disabled: "已停用",
    submitted: "已提交",
    awaiting_approval: "等待审批",
    ticket_issued: "已签发执行授权",
    ticket_already_issued: "执行授权已签发",
  };
  return labels[status] ?? status;
}

export function approvalDecisionLabel(decision: string): string {
  return decision === "approved" ? "已同意" : decision === "rejected" ? "已拒绝" : decision;
}

export function approvalApproverLabel(approver: ApprovalPolicyApprover): string {
  if (approver.label?.trim()) return approver.label.trim();
  const kindLabels: Record<string, string> = { account: "账号", role: "角色", group: "用户组" };
  return (kindLabels[approver.kind] ?? approver.kind) + " · " + approver.ref;
}

export function canApproveApprovalRequest(request: ApprovalRequest, actorId?: string): boolean {
  if (request.status !== "pending" || (actorId && request.requester.id === actorId)) return false;
  if (request.my_approval === "pending") return request.my_approval_eligible !== false;
  return request.my_approval_eligible === true;
}

export function canCancelApprovalRequest(request: ApprovalRequest, actorId: string): boolean {
  return request.status === "pending" && request.requester.id === actorId;
}
