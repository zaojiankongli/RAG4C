import { describe, expect, it } from "vitest";
import {
  firstWorkspaceValidationField,
  validateWorkspaceDialog,
} from "./enterpriseWorkspaceValidation";

const empty = {
  code: "",
  name: "",
  description: "",
  accountId: "",
  datasetId: "",
  reason: "",
};

describe("enterprise workspace field validation", () => {
  it("validates create required fields, lengths, and backend code pattern", () => {
    const missing = validateWorkspaceDialog("create", empty);
    expect(missing).toMatchObject({
      code: "Workspace Code 为必填项",
      name: "Workspace 名称为必填项",
      reason: "变更原因为必填项",
    });
    expect(firstWorkspaceValidationField("create", missing)).toBe("code");

    expect(
      validateWorkspaceDialog("create", {
        ...empty,
        code: "Bad_Code",
        name: "生产域",
        reason: "创建",
      }).code,
    ).toContain("小写字母、数字和连字符");
    expect(
      validateWorkspaceDialog("create", {
        ...empty,
        code: "prod",
        name: "生产域",
        reason: "创建",
        description: "x".repeat(513),
      }).description,
    ).toContain("512");
  });

  it("validates add-member and bind-dataset identifiers with reasons", () => {
    expect(validateWorkspaceDialog("add-member", empty)).toMatchObject({
      accountId: "成员 Account ID 为必填项",
      reason: "变更原因为必填项",
    });
    expect(validateWorkspaceDialog("bind-dataset", empty)).toMatchObject({
      datasetId: "知识库 ID 为必填项",
      reason: "变更原因为必填项",
    });
    expect(
      validateWorkspaceDialog("add-member", {
        ...empty,
        accountId: "a".repeat(65),
        reason: "添加",
      }).accountId,
    ).toContain("64");
  });

  it.each(["archive", "remove-member", "remove-dataset"] as const)(
    "requires a bounded reason for %s",
    (kind) => {
      expect(validateWorkspaceDialog(kind, empty).reason).toBe("变更原因为必填项");
      expect(validateWorkspaceDialog(kind, { ...empty, reason: "x".repeat(513) }).reason).toContain(
        "512",
      );
    },
  );
});
