// @vitest-environment node

import { describe, expect, it } from "vitest";
import { approvalActionLabel, projectApprovalSnapshot } from "./enterpriseApprovalModel";

describe("Stage 17 approval snapshot and action projection", () => {
  it("redacts ticket, token, and secret values even under ordinary snapshot fields", () => {
    const projected = projectApprovalSnapshot({
      metadata: "opaque-ticket-stage17",
      value: "raw-token-stage17",
      data: {
        nested: "raw-secret-stage17",
        safe: "shadow",
      },
      note: "Workspace mode review",
    });

    expect(projected).toEqual({
      metadata: "[已脱敏]",
      value: "[已脱敏]",
      data: {
        nested: "[已脱敏]",
        safe: "shadow",
      },
      note: "Workspace mode review",
    });
    expect(JSON.stringify(projected)).not.toContain("opaque-ticket-stage17");
    expect(JSON.stringify(projected)).not.toContain("raw-token-stage17");
    expect(JSON.stringify(projected)).not.toContain("raw-secret-stage17");
  });

  it("keeps the Stage 17 action on the stable enterprise label", () => {
    expect(approvalActionLabel("workspace_authorization_mode_change")).toBe(
      "变更 Workspace 授权模式",
    );
  });
});
