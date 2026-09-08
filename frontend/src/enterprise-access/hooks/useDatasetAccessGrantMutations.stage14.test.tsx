// @vitest-environment node

import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import { projectDatasetAccessGrantMutationError } from "./useDatasetAccessGrantMutations";

describe("Stage 14 dataset ACL approval-required error", () => {
  it("projects the 409 as a safe approval signal without rendering backend detail", () => {
    const error = projectDatasetAccessGrantMutationError(
      new ApiError("internal backend detail", "http", 409, {
        detail: {
          code: "dataset_acl_approval_required",
          policy_id: "policy-1",
          policy_name: "ACL 高风险规则",
          policy_revision: 4,
          required_approvals: 2,
          request_expiry_minutes: 1440,
          message: "do not render this backend message",
        },
      }),
    );

    expect(error).toMatchObject({
      code: "dataset_acl_approval_required",
      status: 409,
      approvalRequired: {
        policy_id: "policy-1",
        policy_name: "ACL 高风险规则",
        policy_revision: 4,
        required_approvals: 2,
        request_expiry_minutes: 1440,
      },
    });
    expect(error.message).not.toContain("do not render");
    expect(error.message).not.toContain("internal backend");
  });
});
