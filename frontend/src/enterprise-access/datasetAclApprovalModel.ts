import type { ApprovalPolicy } from "../enterprise-approval/enterpriseApprovalModel";

export interface DatasetAclApprovalPolicyFacts {
  id: string;
  name: string | null;
  resource_scope: string | null;
  required_approvals: number | null;
  request_expiry_minutes: number | null;
  revision: number | null;
}

/**
 * Sanitized facts returned by the ACL disable guard. It is deliberately
 * partial: a server-side 409 may not include every policy field, and the UI
 * must display "未返回" rather than filling in a guess.
 */
export interface DatasetAclApprovalRequiredHint {
  policy_id?: string | null;
  policy_name?: string | null;
  policy_revision?: number | null;
  required_approvals?: number | null;
  request_expiry_minutes?: number | null;
  expires_at?: string | null;
  resource_scope?: string | null;
}

function normalizedScope(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .replace(/\/+$/, "");
}

function isGlobalOrWildcardScope(value: string): boolean {
  return new Set([
    "*",
    "global",
    "wildcard",
    "knowledge_base:*",
    "knowledge_base:global",
    "knowledge_base:wildcard",
    "knowledge_base:wildcard:global",
    "knowledge_base/*",
    "knowledge_base/wildcard/global",
  ]).has(value);
}

export function datasetAclApprovalPolicyMatches(
  policy: Pick<ApprovalPolicy, "action_type" | "resource_scope" | "status">,
  datasetId: string,
): boolean {
  const normalizedDatasetId = datasetId.trim();
  if (!normalizedDatasetId || policy.status !== "active") return false;
  if (policy.action_type !== "dataset_acl_disable") return false;

  const configured = normalizedScope(policy.resource_scope);
  const exactScope = `knowledge_base:${normalizedDatasetId}`;
  if (configured === exactScope || configured === normalizedDatasetId) return true;
  if (configured === `${exactScope}/wildcard/global`) return true;
  if (isGlobalOrWildcardScope(configured)) return true;

  // Keep parity with the Stage 13 authority's prefix wildcard contract, but
  // only allow a wildcard to cover the canonical resource prefix.
  if (configured.endsWith("*")) {
    const prefix = configured.slice(0, -1);
    return exactScope.startsWith(prefix) || normalizedDatasetId.startsWith(prefix);
  }
  return false;
}

function policySpecificity(resourceScope: string | null): number {
  const configured = normalizedScope(resourceScope);
  if (isGlobalOrWildcardScope(configured) || configured.endsWith("*")) return 1;
  if (configured.startsWith("knowledge_base:")) return 2;
  return 0;
}

export function selectDatasetAclApprovalPolicy(
  policies: ApprovalPolicy[],
  datasetId: string,
): ApprovalPolicy | null {
  return (
    policies
      .filter((policy) => datasetAclApprovalPolicyMatches(policy, datasetId))
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

function text(value: unknown, maxLength = 256): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized && normalized.length <= maxLength ? normalized : null;
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= maximum
    ? value
    : null;
}

function nullableBoundedInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): number | null | undefined {
  if (value === undefined || value === null) return value === null ? null : undefined;
  return boundedInteger(value, minimum, maximum);
}

export function projectDatasetAclApprovalPolicyFacts(
  policy: ApprovalPolicy,
): DatasetAclApprovalPolicyFacts {
  return {
    id: policy.id,
    name: policy.name || null,
    resource_scope: policy.resource_scope,
    required_approvals: boundedInteger(policy.required_approvals, 1, 5),
    request_expiry_minutes: boundedInteger(policy.request_expiry_minutes, 15, 10080),
    revision: boundedInteger(policy.revision, 1, Number.MAX_SAFE_INTEGER),
  };
}

export function projectDatasetAclApprovalRequiredHint(
  input: unknown,
): DatasetAclApprovalRequiredHint {
  const source = record(input);
  const detail = record(source?.detail);
  const candidate = detail ?? source ?? {};
  const policy = record(candidate.policy) ?? record(candidate.approval_policy);
  const values = policy ? { ...candidate, ...policy } : candidate;
  const expiresAt = text(values.expires_at ?? values.expiry ?? values.expiresAt, 128);
  return {
    policy_id: text(values.policy_id ?? values.policyId, 128),
    policy_name: text(values.policy_name ?? values.policyName ?? values.name, 128),
    policy_revision: nullableBoundedInteger(
      values.policy_revision ?? values.policyRevision ?? values.revision,
      1,
      Number.MAX_SAFE_INTEGER,
    ),
    required_approvals: nullableBoundedInteger(
      values.required_approvals ?? values.requiredApprovals,
      1,
      5,
    ),
    request_expiry_minutes: nullableBoundedInteger(
      values.request_expiry_minutes ?? values.requestExpiryMinutes,
      15,
      10080,
    ),
    expires_at: expiresAt,
    resource_scope: text(values.resource_scope ?? values.resourceScope, 512),
  };
}

export function policyFactsFromApprovalRequiredHint(
  hint: DatasetAclApprovalRequiredHint,
): DatasetAclApprovalPolicyFacts | null {
  const id = text(hint.policy_id, 128);
  if (!id) return null;
  return {
    id,
    name: text(hint.policy_name, 128),
    resource_scope: text(hint.resource_scope, 512),
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
  };
}
