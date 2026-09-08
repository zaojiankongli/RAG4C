import { describe, expect, it } from "vitest";
import {
  firstKnowledgeBaseValidationField,
  validateApplicationReferenceInput,
  validateWorkspaceTransferInput,
} from "./enterpriseKnowledgeBaseValidation";

describe("Stage18 Knowledge Base mutation validation", () => {
  it("requires a bounded Application ID and reason", () => {
    const errors = validateApplicationReferenceInput({ appId: " ", reason: " " });
    expect(errors).toEqual({ appId: "请输入 Application ID", reason: "请填写变更原因" });
    expect(firstKnowledgeBaseValidationField(errors)).toBe("appId");
  });

  it("rejects invalid transfer revisions and target Workspace", () => {
    const errors = validateWorkspaceTransferInput({
      workspaceId: "",
      expectedProfileRevision: 0,
      expectedOwnershipRevision: null,
      expectedSourceWorkspaceRevision: null,
      expectedTargetWorkspaceRevision: null,
      reason: " ",
    });

    expect(errors.workspaceId).toBe("请选择目标 Workspace");
    expect(errors.expectedProfileRevision).toBe("profile revision 必须是正整数");
    expect(errors.expectedOwnershipRevision).toBe("ownership revision 必须是正整数");
    expect(errors.expectedSourceWorkspaceRevision).toBe("source Workspace revision 必须是正整数");
    expect(errors.expectedTargetWorkspaceRevision).toBe("target Workspace revision 必须是正整数");
    expect(errors.reason).toBe("请填写变更原因");
  });

  it("does not trim away a valid reason in the returned normalized payload", () => {
    const errors = validateApplicationReferenceInput({
      appId: " app-1 ",
      reason: "  关联客服助手  ",
    });
    expect(errors).toEqual({});
  });
});
