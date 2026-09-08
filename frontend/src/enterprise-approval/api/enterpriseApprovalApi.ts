import { request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectApprovalPolicy,
  projectApprovalPolicyPage,
  projectApprovalMutation,
  projectApprovalRequest,
  projectApprovalRequestDetail,
  projectApprovalRequestPage,
  projectApprovalSnapshot,
  type ApprovalActionType,
  type ApprovalApproverKind,
  type ApprovalPolicy,
  type ApprovalPolicyPage,
  type ApprovalMutationResponse,
  type ApprovalRequest,
  type ApprovalRequestDetail,
  type ApprovalRequestPage,
  type ApprovalRequestStatus,
} from "../enterpriseApprovalModel";

export interface ApprovalRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface ApprovalListQuery {
  cursor?: string;
  limit?: number;
}

export interface ApprovalRequestListQuery extends ApprovalListQuery {
  status?: ApprovalRequestStatus | "all";
  actionType?: ApprovalActionType | "all";
  mine?: boolean;
  pendingForMe?: boolean;
}

export interface ApprovalPolicyListQuery extends ApprovalListQuery {
  status?: "all" | "active" | "disabled";
  actionType?: ApprovalActionType | "all";
}

export interface ApprovalPolicyApproverInput {
  kind: ApprovalApproverKind;
  ref: string;
}

export interface ApprovalPolicyInput {
  name: string;
  action_type: ApprovalActionType;
  resource_scope?: string | null;
  required_approvals: number;
  request_expiry_minutes: number;
  approvers: ApprovalPolicyApproverInput[];
  reason: string;
}

export interface UpdateApprovalPolicyInput {
  revision: number;
  name?: string;
  resource_scope?: string | null;
  required_approvals?: number;
  request_expiry_minutes?: number;
  approvers?: ApprovalPolicyApproverInput[];
  reason: string;
}

export interface DisableApprovalPolicyInput {
  revision: number;
  reason: string;
}

export interface CreateApprovalRequestInput {
  policy_id: string;
  resource_type: string;
  resource_id: string;
  reason: string;
  snapshot: Record<string, unknown>;
}

export interface ApprovalDecisionInput {
  revision: number;
}

export interface RejectApprovalRequestInput extends ApprovalDecisionInput {
  comment: string;
}

export interface CancelApprovalRequestInput {
  revision: number;
  reason: string;
}

export interface ConsumeApprovalTicketInput {
  ticket: string;
  revision: number;
  action_type: ApprovalActionType;
  resource_type: string;
  resource_id: string;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for approval requests");
  if (!actorToken) throw new Error("actorToken is required for approval requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: "Bearer " + actorToken };
}

export function createApprovalIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return "rag4c-approval-" + cryptoApi.randomUUID();
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return (
      "rag4c-approval-" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
    );
  }
  return "rag4c-approval-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function mutationHeaders(
  scope: EnterpriseScope,
  options: ApprovalRequestOptions,
): Record<string, string> {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied)
    throw new Error("idempotencyKey is required for approval mutations");
  const key = supplied || createApprovalIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...authenticatedHeaders(scope), "Idempotency-Key": key };
}

function boundedInteger(value: number, name: string, minimum: number, maximum?: number): number {
  if (!Number.isInteger(value) || value < minimum || (maximum !== undefined && value > maximum)) {
    const upper = maximum === undefined ? "" : " and at most " + maximum;
    throw new Error(name + " must be an integer at least " + minimum + upper);
  }
  return value;
}

function requiredId(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(name + " is required");
  return normalized;
}

function requiredText(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(name + " is required");
  return normalized;
}

function listQuery(query: ApprovalListQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  params.set("limit", String(boundedInteger(query.limit ?? 50, "limit", 1, 200)));
  return params;
}

function requestQuery(query: ApprovalRequestListQuery): string {
  const params = listQuery(query);
  if (query.status && query.status !== "all") params.set("status", query.status);
  if (query.actionType && query.actionType !== "all") params.set("action_type", query.actionType);
  if (query.mine) params.set("mine", "true");
  if (query.pendingForMe) params.set("pending_for_me", "true");
  return params.toString();
}

function policyQuery(query: ApprovalPolicyListQuery): string {
  const params = listQuery(query);
  if (query.status && query.status !== "all") params.set("status", query.status);
  if (query.actionType && query.actionType !== "all") params.set("action_type", query.actionType);
  return params.toString();
}

function requiredRevision(value: number): number {
  return boundedInteger(value, "revision", 1);
}

function requiredProjected<T>(value: T | null, message: string): T {
  if (value === null) throw new Error(message);
  return value;
}

export function fetchApprovalRequests(
  scope: EnterpriseScope,
  query: ApprovalRequestListQuery = {},
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequestPage> {
  return request<unknown>("/api/enterprise/approvals/requests?" + requestQuery(query), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then(projectApprovalRequestPage);
}

export function fetchApprovalPolicies(
  scope: EnterpriseScope,
  query: ApprovalPolicyListQuery = {},
  options: ApprovalRequestOptions = {},
): Promise<ApprovalPolicyPage> {
  return request<unknown>("/api/enterprise/approvals/policies?" + policyQuery(query), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then(projectApprovalPolicyPage);
}

export function fetchApprovalRequest(
  scope: EnterpriseScope,
  requestId: string,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequestDetail> {
  const id = requiredId(requestId, "requestId");
  return request<unknown>("/api/enterprise/approvals/requests/" + encodeURIComponent(id), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then((value) =>
    requiredProjected(projectApprovalRequestDetail(value), "approval request detail is invalid"),
  );
}

function mutation<T>(
  scope: EnterpriseScope,
  path: string,
  method: "POST" | "PATCH",
  body: unknown,
  options: ApprovalRequestOptions,
  projector: (input: unknown) => T | null,
): Promise<T> {
  return request<unknown>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then((value) => requiredProjected(projector(value), "approval mutation response is invalid"));
}

function approversBody(items: ApprovalPolicyApproverInput[]): Array<{ kind: string; ref: string }> {
  if (!Array.isArray(items) || items.length === 0)
    throw new Error("approvers must contain at least one approver");
  return items.map((item) => ({
    kind: requiredText(item.kind, "approver.kind"),
    ref: requiredText(item.ref, "approver.ref"),
  }));
}

function commonPolicyBody(input: ApprovalPolicyInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: requiredText(input.name, "name"),
    required_approvals: boundedInteger(input.required_approvals, "required_approvals", 1, 5),
    request_expiry_minutes: boundedInteger(
      input.request_expiry_minutes,
      "request_expiry_minutes",
      15,
      10080,
    ),
    approvers: approversBody(input.approvers),
    reason: requiredText(input.reason, "reason"),
  };
  if (input.resource_scope === null) body.resource_scope = null;
  else if (input.resource_scope?.trim()) body.resource_scope = input.resource_scope.trim();
  return body;
}

export function createApprovalPolicy(
  scope: EnterpriseScope,
  input: ApprovalPolicyInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalPolicy> {
  return mutation(
    scope,
    "/api/enterprise/approvals/policies",
    "POST",
    {
      action_type: requiredText(input.action_type, "action_type"),
      ...commonPolicyBody(input),
    },
    options,
    projectApprovalPolicy,
  );
}

export function updateApprovalPolicy(
  scope: EnterpriseScope,
  policyId: string,
  input: UpdateApprovalPolicyInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalPolicy> {
  const body: Record<string, unknown> = {
    revision: requiredRevision(input.revision),
    reason: requiredText(input.reason, "reason"),
  };
  if (input.name !== undefined) body.name = requiredText(input.name, "name");
  if (input.resource_scope !== undefined)
    body.resource_scope = input.resource_scope === null ? null : input.resource_scope.trim();
  if (input.required_approvals !== undefined)
    body.required_approvals = boundedInteger(input.required_approvals, "required_approvals", 1, 5);
  if (input.request_expiry_minutes !== undefined)
    body.request_expiry_minutes = boundedInteger(
      input.request_expiry_minutes,
      "request_expiry_minutes",
      15,
      10080,
    );
  if (input.approvers !== undefined) body.approvers = approversBody(input.approvers);
  if (Object.keys(body).length === 2) throw new Error("at least one policy field is required");
  return mutation(
    scope,
    "/api/enterprise/approvals/policies/" + encodeURIComponent(requiredId(policyId, "policyId")),
    "PATCH",
    body,
    options,
    projectApprovalPolicy,
  );
}

export function disableApprovalPolicy(
  scope: EnterpriseScope,
  policyId: string,
  input: DisableApprovalPolicyInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalPolicy> {
  return mutation(
    scope,
    "/api/enterprise/approvals/policies/" +
      encodeURIComponent(requiredId(policyId, "policyId")) +
      "/disable",
    "POST",
    { revision: requiredRevision(input.revision), reason: requiredText(input.reason, "reason") },
    options,
    projectApprovalPolicy,
  );
}

export function createApprovalRequest(
  scope: EnterpriseScope,
  input: CreateApprovalRequestInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequest> {
  return mutation(
    scope,
    "/api/enterprise/approvals/requests",
    "POST",
    {
      policy_id: requiredId(input.policy_id, "policy_id"),
      resource_type: requiredText(input.resource_type, "resource_type"),
      resource_id: requiredText(input.resource_id, "resource_id"),
      snapshot: projectApprovalSnapshot(input.snapshot),
      reason: requiredText(input.reason, "reason"),
    },
    options,
    projectApprovalRequest,
  );
}

export function approveApprovalRequest(
  scope: EnterpriseScope,
  requestId: string,
  input: ApprovalDecisionInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalMutationResponse> {
  return mutation(
    scope,
    "/api/enterprise/approvals/requests/" +
      encodeURIComponent(requiredId(requestId, "requestId")) +
      "/approve",
    "POST",
    { revision: requiredRevision(input.revision) },
    options,
    projectApprovalMutation,
  );
}

export function rejectApprovalRequest(
  scope: EnterpriseScope,
  requestId: string,
  input: RejectApprovalRequestInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequest> {
  const comment = requiredText(input.comment, "comment");
  if (comment.length > 500) throw new Error("comment must be at most 500 characters");
  return mutation(
    scope,
    "/api/enterprise/approvals/requests/" +
      encodeURIComponent(requiredId(requestId, "requestId")) +
      "/reject",
    "POST",
    { revision: requiredRevision(input.revision), comment },
    options,
    projectApprovalRequest,
  );
}

export function cancelApprovalRequest(
  scope: EnterpriseScope,
  requestId: string,
  input: CancelApprovalRequestInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequest> {
  return mutation(
    scope,
    "/api/enterprise/approvals/requests/" +
      encodeURIComponent(requiredId(requestId, "requestId")) +
      "/cancel",
    "POST",
    { revision: requiredRevision(input.revision), reason: requiredText(input.reason, "reason") },
    options,
    projectApprovalRequest,
  );
}

export function consumeApprovalTicket(
  scope: EnterpriseScope,
  requestId: string,
  input: ConsumeApprovalTicketInput,
  options: ApprovalRequestOptions = {},
): Promise<ApprovalRequest> {
  return mutation(
    scope,
    "/api/enterprise/approvals/requests/" +
      encodeURIComponent(requiredId(requestId, "requestId")) +
      "/consume-ticket",
    "POST",
    {
      ticket: requiredText(input.ticket, "ticket"),
      revision: requiredRevision(input.revision),
      action_type: requiredText(input.action_type, "action_type"),
      resource_type: requiredText(input.resource_type, "resource_type"),
      resource_id: requiredText(input.resource_id, "resource_id"),
    },
    options,
    projectApprovalRequest,
  );
}

export type {
  ApprovalPolicy,
  ApprovalPolicyPage,
  ApprovalRequest,
  ApprovalRequestDetail,
  ApprovalRequestPage,
};
