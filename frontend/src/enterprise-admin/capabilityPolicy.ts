import type { EnterpriseContext } from "./model";

export interface EnterpriseCapabilityPolicyContext {
  actorToken: string | null | undefined;
  tenantId: string;
  identity: EnterpriseContext | null;
  online: boolean | null;
  healthStatus?: string;
}

export interface EnterpriseCapabilityProjection {
  ready: boolean;
  readOnly: boolean;
}

interface CapabilityPolicySpec {
  backendKey: string;
  requiredPermission?: string;
}

function defineCapabilityPolicy(
  backendKey: string,
  requiredPermission?: string,
): CapabilityPolicySpec {
  return requiredPermission ? { backendKey, requiredPermission } : { backendKey };
}

export const ENTERPRISE_CAPABILITY_POLICIES = {
  notifications: defineCapabilityPolicy("enterprise_notification_center"),
  contentRecovery: defineCapabilityPolicy("enterprise_content_recovery"),
  taskOperations: defineCapabilityPolicy("enterprise_task_operations"),
  automationWorkflows: defineCapabilityPolicy("enterprise_automation_workflows"),
  knowledgeServing: defineCapabilityPolicy(
    "enterprise_knowledge_serving_reliability",
    "knowledge.manage",
  ),
} as const;

export type EnterpriseCapabilityPolicyKey = keyof typeof ENTERPRISE_CAPABILITY_POLICIES;

function serviceReadOnly(context: EnterpriseCapabilityPolicyContext): boolean {
  return (
    context.online !== true ||
    context.healthStatus === "degraded" ||
    context.healthStatus === "down"
  );
}

export function resolveEnterpriseCapabilityState(
  key: EnterpriseCapabilityPolicyKey,
  context: EnterpriseCapabilityPolicyContext,
): EnterpriseCapabilityProjection {
  const policy = ENTERPRISE_CAPABILITY_POLICIES[key];
  const identityMatchesTenant = context.identity?.tenant.id === context.tenantId;
  const ready =
    Boolean(context.actorToken) &&
    identityMatchesTenant &&
    context.identity?.capabilities[policy.backendKey]?.state === "ready";
  const missingRequiredPermission =
    policy.requiredPermission !== undefined &&
    context.identity?.effective_permissions.includes(policy.requiredPermission) !== true;

  return {
    ready,
    readOnly: serviceReadOnly(context) || missingRequiredPermission,
  };
}

export type EnterpriseCapabilityStates = {
  [K in EnterpriseCapabilityPolicyKey]: EnterpriseCapabilityProjection;
};

export function resolveEnterpriseCapabilityStates(
  context: EnterpriseCapabilityPolicyContext,
): EnterpriseCapabilityStates {
  return Object.fromEntries(
    (Object.keys(ENTERPRISE_CAPABILITY_POLICIES) as EnterpriseCapabilityPolicyKey[]).map((key) => [
      key,
      resolveEnterpriseCapabilityState(key, context),
    ]),
  ) as EnterpriseCapabilityStates;
}
