import { describe, expect, it } from "vitest";
import type { EnterpriseContext } from "./model";
import {
  ENTERPRISE_CAPABILITY_POLICIES,
  resolveEnterpriseCapabilityState,
  resolveEnterpriseCapabilityStates,
} from "./capabilityPolicy";

function context(overrides: Partial<EnterpriseContext> = {}): EnterpriseContext {
  return {
    tenant: {
      id: "tenant-a",
      name: "Tenant A",
      plan: "enterprise",
      status: "active",
      quota_documents: 100,
      quota_chunks: 1000,
      doc_count: 1,
      chunk_count: 1,
    },
    actor: {
      id: "account-a",
      name: "Account A",
      email: "a@example.test",
      role: "admin",
    },
    member_count: 1,
    dataset_count: 1,
    effective_permissions: ["knowledge.manage"],
    role_permissions: {},
    capabilities: Object.fromEntries(
      Object.values(ENTERPRISE_CAPABILITY_POLICIES).map(({ backendKey }) => [
        backendKey,
        { state: "ready", label: backendKey, reason: null },
      ]),
    ),
    ...overrides,
  };
}

describe("enterprise capability policy registry", () => {
  it("projects every registered capability using the same tenant/token guard", () => {
    const states = resolveEnterpriseCapabilityStates({
      actorToken: "actor-token",
      tenantId: "tenant-a",
      identity: context(),
      online: true,
      healthStatus: "healthy",
    });

    expect(states).toEqual({
      notifications: { ready: true, readOnly: false },
      contentRecovery: { ready: true, readOnly: false },
      taskOperations: { ready: true, readOnly: false },
      automationWorkflows: { ready: true, readOnly: false },
      knowledgeServing: { ready: true, readOnly: false },
    });
  });

  it("fails closed when token, tenant, or capability evidence is missing", () => {
    const base = {
      tenantId: "tenant-a",
      identity: context(),
      online: true as const,
      healthStatus: "healthy",
    };

    expect(resolveEnterpriseCapabilityState("notifications", { ...base, actorToken: "" })).toEqual({
      ready: false,
      readOnly: false,
    });
    expect(
      resolveEnterpriseCapabilityState("notifications", {
        ...base,
        actorToken: "token",
        tenantId: "tenant-b",
      }),
    ).toEqual({ ready: false, readOnly: false });
    expect(
      resolveEnterpriseCapabilityState("notifications", {
        ...base,
        actorToken: "token",
        identity: context({
          capabilities: {
            ...context().capabilities,
            enterprise_notification_center: {
              state: "limited",
              label: "limited",
              reason: "not ready",
            },
          },
        }),
      }),
    ).toEqual({ ready: false, readOnly: false });
  });

  it("makes every capability read-only on degraded/offline services", () => {
    const identity = context();
    for (const online of [false, null] as const) {
      expect(
        resolveEnterpriseCapabilityState("notifications", {
          actorToken: "token",
          tenantId: "tenant-a",
          identity,
          online,
          healthStatus: "healthy",
        }),
      ).toEqual({ ready: true, readOnly: true });
    }
    expect(
      resolveEnterpriseCapabilityState("taskOperations", {
        actorToken: "token",
        tenantId: "tenant-a",
        identity,
        online: true,
        healthStatus: "degraded",
      }),
    ).toEqual({ ready: true, readOnly: true });
  });

  it("keeps the serving permission gate separate from capability readiness", () => {
    const identity = context({ effective_permissions: [] });
    expect(
      resolveEnterpriseCapabilityState("knowledgeServing", {
        actorToken: "token",
        tenantId: "tenant-a",
        identity,
        online: true,
        healthStatus: "healthy",
      }),
    ).toEqual({ ready: true, readOnly: true });
    expect(
      resolveEnterpriseCapabilityState("notifications", {
        actorToken: "token",
        tenantId: "tenant-a",
        identity,
        online: true,
        healthStatus: "healthy",
      }),
    ).toEqual({ ready: true, readOnly: false });
  });
});
