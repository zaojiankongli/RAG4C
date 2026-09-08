export type WorkspaceDialogKind =
  | "create"
  | "edit"
  | "archive"
  | "add-member"
  | "edit-member"
  | "remove-member"
  | "bind-dataset"
  | "remove-dataset";

export type WorkspaceValidationField =
  "code" | "name" | "description" | "accountId" | "datasetId" | "reason";

export interface WorkspaceValidationValues {
  code: string;
  name: string;
  description: string;
  accountId: string;
  datasetId: string;
  reason: string;
}

export type WorkspaceValidationErrors = Partial<Record<WorkspaceValidationField, string>>;

const WORKSPACE_CODE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const FIELD_ORDER: Record<WorkspaceDialogKind, WorkspaceValidationField[]> = {
  create: ["code", "name", "description", "reason"],
  edit: ["name", "description", "reason"],
  archive: ["reason"],
  "add-member": ["accountId", "reason"],
  "edit-member": ["reason"],
  "remove-member": ["reason"],
  "bind-dataset": ["datasetId", "reason"],
  "remove-dataset": ["reason"],
};

function required(
  errors: WorkspaceValidationErrors,
  field: WorkspaceValidationField,
  value: string,
  label: string,
  maximum: number,
): void {
  const normalized = value.trim();
  if (!normalized) errors[field] = `${label}为必填项`;
  else if (normalized.length > maximum) errors[field] = `${label}最多 ${maximum} 个字符`;
}

export function validateWorkspaceDialog(
  kind: WorkspaceDialogKind,
  values: WorkspaceValidationValues,
): WorkspaceValidationErrors {
  const errors: WorkspaceValidationErrors = {};
  if (kind === "create") {
    required(errors, "code", values.code, "Workspace Code ", 64);
    if (!errors.code && !WORKSPACE_CODE.test(values.code.trim())) {
      errors.code = "Workspace Code 仅允许小写字母、数字和连字符，且首尾必须为字母或数字";
    }
    required(errors, "name", values.name, "Workspace 名称", 128);
    if (values.description.trim().length > 512) errors.description = "描述最多 512 个字符";
  } else if (kind === "edit") {
    required(errors, "name", values.name, "Workspace 名称", 128);
    if (values.description.trim().length > 512) errors.description = "描述最多 512 个字符";
  } else if (kind === "add-member") {
    required(errors, "accountId", values.accountId, "成员 Account ID ", 64);
  } else if (kind === "bind-dataset") {
    required(errors, "datasetId", values.datasetId, "知识库 ID ", 64);
  }
  required(errors, "reason", values.reason, "变更原因", 512);
  return errors;
}

export function firstWorkspaceValidationField(
  kind: WorkspaceDialogKind,
  errors: WorkspaceValidationErrors,
): WorkspaceValidationField | null {
  return FIELD_ORDER[kind].find((field) => Boolean(errors[field])) ?? null;
}
