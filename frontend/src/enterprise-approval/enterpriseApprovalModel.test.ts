// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  approvalActionLabel,
  approvalStatusLabel,
  projectApprovalEvidence,
  projectApprovalPolicyPage,
  projectApprovalRequestDetail,
  projectApprovalRequestPage,
  projectApprovalSnapshot,
} from "./enterpriseApprovalModel";

const request = {
  id: "request-1",
  policy_id: "policy-1",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-1",
  resource_name: "产品知识库",
  requester: { id: "account-1", name: "林澈", email: "lin@example.com" },
  reason: "准备切换知识库访问策略",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  request_snapshot: {
    dataset_id: "dataset-1",
    authorization: ["Bearer ", "super-", "secret"].join(""),
    nested: { client_secret: ["never", "-render"].join("") },
  },
};

const evidence = {
  pending_count: 8,
  my_pending_count: 2,
  active_policy_count: 4,
  catalog_revision: "0024_oidc_sso_runtime",
  execution_adapter_status: "execution_adapter_not_connected",
};

describe("Stage 13 approval model projection", () => {
  it("projects only valid request rows and preserves server evidence without inventing counts", () => {
    const page = projectApprovalRequestPage({
      items: [request, { id: "malformed" }],
      count: 8,
      next_cursor: "cursor-2",
      evidence,
    });

    expect(page.items).toHaveLength(1);
    expect(page.items[0]).toMatchObject({
      id: "request-1",
      received_approvals: 1,
      execution_adapter_status: "execution_adapter_not_connected",
    });
    expect(page.evidence).toEqual(evidence);

    const empty = projectApprovalRequestPage({ items: [], next_cursor: null });
    expect(empty.count).toBe(0);
    expect(empty.evidence.pending_count).toBeNull();
    expect(empty.evidence.my_pending_count).toBeNull();
    expect(empty.evidence.active_policy_count).toBeNull();
    expect(empty.evidence.catalog_revision).toBeNull();
    expect(empty.evidence.execution_adapter_status).toBe("execution_adapter_not_connected");
  });

  it("redacts secrets from request snapshots while keeping auditable change facts", () => {
    const projected = projectApprovalSnapshot(request.request_snapshot);
    expect(projected).toMatchObject({
      dataset_id: "dataset-1",
      authorization: "[已脱敏]",
      nested: { client_secret: "[已脱敏]" },
    });
    expect(JSON.stringify(projected)).not.toContain("super-secret");
    expect(JSON.stringify(projected)).not.toContain("never-render");
  });

  it("projects detail decisions and keeps missing process facts empty instead of fabricating actors", () => {
    const detail = projectApprovalRequestDetail({
      request,
      decisions: [
        {
          id: "decision-1",
          approver: { id: "account-2", name: "顾问" },
          decision: "approved",
          comment: "已核对变更范围",
          decided_at: "2026-08-27T08:20:00Z",
        },
      ],
      process: [],
    });

    expect(detail).not.toBeNull();
    expect(detail?.decisions).toHaveLength(1);
    expect(detail?.process).toEqual([]);
    expect(detail?.snapshot.authorization).toBe("[已脱敏]");
  });

  it("projects policy pages and exposes stable Chinese labels for the table", () => {
    const policies = projectApprovalPolicyPage({
      items: [
        {
          id: "policy-1",
          name: "知识库 ACL 变更",
          action_type: "dataset_acl_disable",
          resource_scope: "dataset-1",
          status: "active",
          required_approvals: 2,
          request_expiry_minutes: 1440,
          approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
          revision: 3,
        },
      ],
      count: 1,
      next_cursor: null,
    });

    expect(policies.items[0]).toMatchObject({ required_approvals: 2 });
    expect(approvalActionLabel("dataset_acl_disable")).toBe("停用知识库 ACL");
    expect(approvalStatusLabel("pending")).toBe("待审批");
    expect(projectApprovalEvidence({})).toMatchObject({
      pending_count: null,
      execution_adapter_status: "execution_adapter_not_connected",
    });
  });
});
