import type {
  ApprovalPolicy,
  ApprovalRequest,
} from "../enterprise-approval/enterpriseApprovalModel";
import type { EnterpriseMember, TenantRole } from "./model/enterpriseAdminModel";

export interface MemberRoleApprovalPolicyFacts {
  id: string;
  name: string | null;
  resource_scope: string | null;
  required_approvals: number | null;
  request_expiry_minutes: number | null;
  revision: number | null;
  expires_at?: string | null;
}

export interface MemberRoleApprovalRequiredHint {
  policy_id?: string | null;
  policy_name?: string | null;
  policy_revision?: number | null;
  required_approvals?: number | null;
  request_expiry_minutes?: number | null;
  expires_at?: string | null;
  resource_scope?: string | null;
}

export type MemberRoleApprovalRequest = Pick<ApprovalRequest, "id" | "status">;

export interface MemberRoleApprovalSnapshot extends Record<string, unknown> {
  target_account_id: string;
  expected_member_revision: number;
  current_role: string;
  requested_role: string;
  current_status: string;
}

function normalizedScope(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .replace(/\/+$/, "")
    .toLowerCase();
}

function globalScope(value: string): boolean {
  return new Set([
    "*",
    "global",
    "wildcard",
    "tenant_member:*",
    "tenant_member/*",
    "tenant_member:global",
    "tenant_member:wildcard",
    "tenant_member:wildcard:global",
    "tenant_member/wildcard/global",
  ]).has(value);
}

export function memberRoleApprovalPolicyMatches(
  policy: Pick<ApprovalPolicy, "action_type" | "resource_scope" | "status">,
  accountId: string,
): boolean {
  const normalizedAccountId = accountId.trim();
  if (!normalizedAccountId || policy.status !== "active") return false;
  if (policy.action_type !== "member_role_change") return false;

  const configured = normalizedScope(policy.resource_scope);
  const exactScope = `tenant_member:${normalizedAccountId.toLowerCase()}`;
  if (configured === exactScope || configured === normalizedAccountId.toLowerCase()) return true;
  if (globalScope(configured)) return true;
  if (configured.endsWith("*")) {
    const prefix = configured.slice(0, -1);
    return exactScope.startsWith(prefix) || normalizedAccountId.toLowerCase().startsWith(prefix);
  }
  return false;
}

function policySpecificity(resourceScope: string | null): number {
  const configured = normalizedScope(resourceScope);
  if (configured.startsWith("tenant_member:") && !configured.includes("*")) return 3;
  if (configured && !globalScope(configured) && !configured.endsWith("*")) return 2;
  if (configured.endsWith("*")) return 1;
  return 0;
}

export function selectMemberRoleApprovalPolicy(
  policies: ApprovalPolicy[],
  accountId: string,
): ApprovalPolicy | null {
  return (
    policies
      .filter((policy) => memberRoleApprovalPolicyMatches(policy, accountId))
      .sort((left, right) => {
        const specificity =
          policySpecificity(right.resource_scope) - policySpecificity(left.resource_scope);
        return specificity || left.id.localeCompare(right.id);
      })[0] ?? null
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function boundedText(value: unknown, maximum = 512): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized && normalized.length <= maximum ? normalized : null;
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= maximum
    ? value
    : null;
}

function nullableInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): number | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return boundedInteger(value, minimum, maximum);
}

export function projectMemberRoleApprovalPolicyFacts(
  policy: ApprovalPolicy & { expires_at?: string | null },
): MemberRoleApprovalPolicyFacts {
  return {
    id: policy.id,
    name: policy.name || null,
    resource_scope: policy.resource_scope,
    required_approvals: boundedInteger(policy.required_approvals, 1, 5),
    request_expiry_minutes: boundedInteger(policy.request_expiry_minutes, 15, 10080),
    revision: boundedInteger(policy.revision, 1, Number.MAX_SAFE_INTEGER),
    ...(policy.expires_at !== undefined ? { expires_at: policy.expires_at } : {}),
  };
}

export function projectMemberRoleApprovalRequiredHint(
  input: unknown,
): MemberRoleApprovalRequiredHint {
  const source = record(input);
  const detail = record(source?.detail);
  const candidate = detail ?? source ?? {};
  const nestedPolicy = record(candidate.policy) ?? record(candidate.approval_policy);
  const values = nestedPolicy ? { ...candidate, ...nestedPolicy } : candidate;
  return {
    policy_id: boundedText(values.policy_id ?? values.policyId ?? values.id, 128),
    policy_name: boundedText(values.policy_name ?? values.policyName ?? values.name, 128),
    policy_revision: nullableInteger(
      values.policy_revision ?? values.policyRevision ?? values.revision,
      1,
      Number.MAX_SAFE_INTEGER,
    ),
    required_approvals: nullableInteger(
      values.required_approvals ?? values.requiredApprovals,
      1,
      5,
    ),
    request_expiry_minutes: nullableInteger(
      values.request_expiry_minutes ?? values.requestExpiryMinutes,
      15,
      10080,
    ),
    expires_at: boundedText(values.expires_at ?? values.expiry ?? values.expiresAt, 128),
    resource_scope: boundedText(values.resource_scope ?? values.resourceScope, 512),
  };
}

export function policyFactsFromMemberRoleApprovalHint(
  hint: MemberRoleApprovalRequiredHint,
): MemberRoleApprovalPolicyFacts | null {
  const id = boundedText(hint.policy_id, 128);
  if (!id) return null;
  return {
    id,
    name: boundedText(hint.policy_name, 128),
    resource_scope: boundedText(hint.resource_scope, 512),
    required_approvals:
      hint.required_approvals === null || hint.required_approvals === undefined
        ? null
        : boundedInteger(hint.required_approvals, 1, 5),
    request_expiry_minutes:
      hint.request_expiry_minutes === null || hint.request_expiry_minutes === undefined
        ? null
        : boundedInteger(hint.request_expiry_minutes, 15, 10080),
    revision:
      hint.policy_revision === null || hint.policy_revision === undefined
        ? null
        : boundedInteger(hint.policy_revision, 1, Number.MAX_SAFE_INTEGER),
    ...(hint.expires_at !== undefined ? { expires_at: hint.expires_at } : {}),
  };
}

export function buildMemberRoleApprovalSnapshot(
  member: Pick<EnterpriseMember, "account_id" | "role" | "status" | "revision">,
  requestedRole: TenantRole | string,
): MemberRoleApprovalSnapshot {
  if (!member.account_id.trim()) throw new Error("target_account_id is required");
  const revision = member.revision;
  if (typeof revision !== "number" || !Number.isInteger(revision) || revision <= 0) {
    throw new Error("expected_member_revision must be a positive integer");
  }
  const currentRole = member.role.trim();
  const nextRole = String(requestedRole).trim();
  const currentStatus = member.status.trim();
  if (!currentRole || !nextRole || !currentStatus)
    throw new Error("member role snapshot is invalid");
  return {
    target_account_id: member.account_id.trim(),
    expected_member_revision: revision,
    current_role: currentRole,
    requested_role: nextRole,
    current_status: currentStatus,
  };
}
