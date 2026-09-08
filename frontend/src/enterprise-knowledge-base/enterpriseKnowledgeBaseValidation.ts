export type KnowledgeBaseValidationField =
  | "appId"
  | "workspaceId"
  | "expectedProfileRevision"
  | "expectedOwnershipRevision"
  | "expectedSourceWorkspaceRevision"
  | "expectedTargetWorkspaceRevision"
  | "reason";

export type KnowledgeBaseValidationErrors = Partial<Record<KnowledgeBaseValidationField, string>>;

export interface ApplicationReferenceInput {
  appId: string;
  reason: string;
}

export interface WorkspaceTransferInput {
  targetWorkspaceId?: string;
  expectedDatasetProfileRevision?: number | null;
  workspaceId?: string;
  expectedProfileRevision?: number | null;
  expectedOwnershipRevision: number | null;
  expectedSourceWorkspaceRevision: number | null;
  expectedTargetWorkspaceRevision: number | null;
  reason: string;
}

function requiredText(value: string, label: string, maximum: number): string | null {
  const normalized = value.trim();
  if (!normalized) return `${label}不能为空`;
  if (normalized.length > maximum) return `${label}不能超过 ${maximum} 个字符`;
  return null;
}

function positiveRevision(value: number | null): string | null {
  return Number.isInteger(value) && (value as number) > 0 ? null : "revision 必须是正整数";
}

export function validateApplicationReferenceInput(
  input: ApplicationReferenceInput,
): KnowledgeBaseValidationErrors {
  const errors: KnowledgeBaseValidationErrors = {};
  if (requiredText(input.appId, "Application ID", 128)) errors.appId = "请输入 Application ID";
  if (requiredText(input.reason, "变更原因", 512)) errors.reason = "请填写变更原因";
  return errors;
}

export function validateWorkspaceTransferInput(
  input: WorkspaceTransferInput,
): KnowledgeBaseValidationErrors {
  const errors: KnowledgeBaseValidationErrors = {};
  const targetWorkspaceId = input.targetWorkspaceId ?? input.workspaceId ?? "";
  const expectedDatasetProfileRevision =
    input.expectedDatasetProfileRevision ?? input.expectedProfileRevision;
  if (requiredText(targetWorkspaceId, "目标 Workspace", 128))
    errors.workspaceId = "请选择目标 Workspace";
  if (positiveRevision(expectedDatasetProfileRevision ?? null)) {
    errors.expectedProfileRevision = "profile revision 必须是正整数";
  }
  if (positiveRevision(input.expectedOwnershipRevision)) {
    errors.expectedOwnershipRevision = "ownership revision 必须是正整数";
  }
  if (positiveRevision(input.expectedSourceWorkspaceRevision)) {
    errors.expectedSourceWorkspaceRevision = "source Workspace revision 必须是正整数";
  }
  if (positiveRevision(input.expectedTargetWorkspaceRevision)) {
    errors.expectedTargetWorkspaceRevision = "target Workspace revision 必须是正整数";
  }
  if (requiredText(input.reason, "变更原因", 512)) errors.reason = "请填写变更原因";
  return errors;
}

export function firstKnowledgeBaseValidationField(
  errors: KnowledgeBaseValidationErrors,
): KnowledgeBaseValidationField | null {
  const order: KnowledgeBaseValidationField[] = [
    "appId",
    "workspaceId",
    "expectedProfileRevision",
    "expectedOwnershipRevision",
    "expectedSourceWorkspaceRevision",
    "expectedTargetWorkspaceRevision",
    "reason",
  ];
  return order.find((field) => errors[field]) ?? null;
}
