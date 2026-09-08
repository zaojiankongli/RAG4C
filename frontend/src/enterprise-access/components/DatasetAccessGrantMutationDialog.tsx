import { useEffect, useState } from "react";
import { Alert, Button, Dialog, Textarea } from "tdesign-react";
import type {
  CreateDatasetAccessGrantInput,
  DatasetAccessGrant,
  DatasetAccessGrantRevisionInput,
  DatasetAccessGrantRole,
  DatasetAccessGrantSubjectType,
  UpdateDatasetAccessGrantRoleInput,
} from "../enterpriseAccessModel";
import type { DatasetAccessGrantMutationError } from "../hooks/useDatasetAccessGrantMutations";

export type DatasetAccessGrantDialogMode = "create" | "role" | "revoke" | "resume";

export type DatasetAccessGrantDialogPayload =
  | { mode: "create"; input: CreateDatasetAccessGrantInput }
  | { mode: "role"; input: UpdateDatasetAccessGrantRoleInput }
  | { mode: "revoke"; input: DatasetAccessGrantRevisionInput }
  | { mode: "resume"; input: DatasetAccessGrantRevisionInput };

export interface DatasetAccessGrantMutationDialogProps {
  visible: boolean;
  mode: DatasetAccessGrantDialogMode;
  grant: DatasetAccessGrant | null;
  saving: boolean;
  error: DatasetAccessGrantMutationError | null;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onRetry?: () => Promise<unknown>;
  onSubmit: (payload: DatasetAccessGrantDialogPayload) => Promise<DatasetAccessGrant | null>;
}

const SUBJECT_TYPES: Array<{ label: string; value: DatasetAccessGrantSubjectType }> = [
  { label: "账号", value: "account" },
  { label: "用户组", value: "group" },
  { label: "组织单元", value: "organization_unit" },
];

const GRANT_ROLES: Array<{ label: string; value: DatasetAccessGrantRole }> = [
  { label: "只读成员", value: "viewer" },
  { label: "编辑者", value: "editor" },
  { label: "知识库管理员", value: "manager" },
];

function isGrantRole(value: string): value is DatasetAccessGrantRole {
  return GRANT_ROLES.some((option) => option.value === value);
}

function titleFor(mode: DatasetAccessGrantDialogMode): string {
  if (mode === "create") return "新增知识库授权";
  if (mode === "role") return "变更知识库授权角色";
  if (mode === "revoke") return "撤销知识库授权";
  return "恢复知识库授权";
}

function confirmLabelFor(mode: DatasetAccessGrantDialogMode): string {
  if (mode === "create") return "创建授权";
  if (mode === "role") return "保存角色";
  if (mode === "revoke") return "撤销授权";
  return "恢复授权";
}

function descriptionFor(mode: DatasetAccessGrantDialogMode): string {
  if (mode === "create") return "为当前已选择的知识库建立一条新的直接授权关系。";
  if (mode === "role") return "角色变更会递增授权版本，避免覆盖其他管理员的最新修改。";
  if (mode === "revoke") return "撤销后该授权关系不再参与知识库权限计算，原记录仍保留供审计。";
  return "恢复后该授权关系重新参与知识库权限计算。";
}

export default function DatasetAccessGrantMutationDialog({
  visible,
  mode,
  grant,
  saving,
  error,
  onClose,
  onRefresh,
  onRetry,
  onSubmit,
}: DatasetAccessGrantMutationDialogProps) {
  const [subjectType, setSubjectType] = useState<DatasetAccessGrantSubjectType>("account");
  const [subjectId, setSubjectId] = useState("");
  const [role, setRole] = useState<DatasetAccessGrantRole>("viewer");
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState("");
  const title = titleFor(mode);
  const isCreate = mode === "create";
  const initialRole = grant && isGrantRole(grant.role) ? grant.role : "viewer";

  useEffect(() => {
    if (!visible) return;
    setSubjectType("account");
    setSubjectId("");
    setRole(initialRole);
    setReason("");
    setValidation("");
  }, [grant?.id, grant?.revision, initialRole, mode, visible]);

  if (!visible) return null;

  const retryMutation = async () => {
    if (!onRetry) return;
    const result = await onRetry();
    if (result) onClose();
  };

  const submit = async () => {
    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setValidation("变更原因不能为空。");
      return;
    }
    if (isCreate) {
      const trimmedSubjectId = subjectId.trim();
      if (!trimmedSubjectId) {
        setValidation("主体 ID 不能为空。");
        return;
      }
      const result = await onSubmit({
        mode,
        input: {
          subject_type: subjectType,
          subject_id: trimmedSubjectId,
          role,
          reason: trimmedReason,
        },
      });
      if (result) onClose();
      return;
    }
    if (!grant) {
      setValidation("未找到要变更的授权记录。");
      return;
    }
    if (!Number.isInteger(grant.revision) || grant.revision < 1) {
      setValidation("授权版本号未返回，无法安全提交变更。");
      return;
    }
    if (mode === "role") {
      const result = await onSubmit({
        mode,
        input: { role, reason: trimmedReason, revision: grant.revision },
      });
      if (result) onClose();
      return;
    }
    const result = await onSubmit({
      mode,
      input: { reason: trimmedReason, revision: grant.revision },
    });
    if (result) onClose();
  };

  return (
    <Dialog
      visible={visible}
      header={title}
      width={540}
      destroyOnClose
      closeOnEscKeydown={!saving}
      closeOnOverlayClick={!saving}
      confirmBtn={{
        content: confirmLabelFor(mode),
        theme: mode === "revoke" ? "danger" : "primary",
      }}
      cancelBtn={{ content: "取消", disabled: saving }}
      confirmLoading={saving}
      onClose={onClose}
      onConfirm={() => void submit()}
      {...({ role: "dialog", "aria-modal": "true", "aria-label": title } as Record<
        string,
        unknown
      >)}
    >
      {error ? (
        <div className="enterprise-access-mutation-error" role="alert">
          <Alert theme="error" title="授权变更未提交" message={error.message} />
          {error.retryAvailable && onRetry ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              onClick={() => void retryMutation()}
              disabled={saving}
            >
              使用同一请求重试
            </Button>
          ) : null}
          {error.needsRefresh ? (
            <Button
              variant="text"
              theme="primary"
              size="small"
              onClick={() => void onRefresh()}
              disabled={saving}
            >
              刷新授权列表
            </Button>
          ) : null}
        </div>
      ) : null}
      {validation ? <Alert theme="warning" title="请补齐授权信息" message={validation} /> : null}
      <div className="enterprise-access-mutation-dialog__intro">
        <strong>{descriptionFor(mode)}</strong>
        {grant ? (
          <span>
            {grant.subject_name} · {grant.subject_id}
          </span>
        ) : (
          <span>当前知识库：由服务端按请求作用域确认</span>
        )}
      </div>
      <div className="enterprise-access-mutation-form">
        {isCreate ? (
          <>
            <label>
              <span>主体类型</span>
              <select
                className="enterprise-access-native-select"
                aria-label="主体类型"
                value={subjectType}
                disabled={saving}
                onChange={(event) => {
                  setSubjectType(event.currentTarget.value as DatasetAccessGrantSubjectType);
                  setValidation("");
                }}
              >
                {SUBJECT_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>主体 ID</span>
              <input
                className="enterprise-access-native-text-input"
                type="text"
                aria-label="主体 ID"
                value={subjectId}
                disabled={saving}
                placeholder="输入已存在的账号、用户组或组织单元 ID"
                onChange={(event) => {
                  setSubjectId(event.currentTarget.value);
                  setValidation("");
                }}
              />
            </label>
          </>
        ) : null}
        {mode === "role" || isCreate ? (
          <label>
            <span>知识库角色</span>
            <select
              className="enterprise-access-native-select"
              aria-label="知识库角色"
              value={role}
              disabled={saving}
              onChange={(event) => {
                setRole(event.currentTarget.value as DatasetAccessGrantRole);
                setValidation("");
              }}
            >
              {GRANT_ROLES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {!isCreate ? (
          <label>
            <span>授权版本</span>
            <input
              className="enterprise-access-revision-input"
              aria-label="revision"
              value={String(grant?.revision ?? "")}
              readOnly
              disabled={saving}
            />
          </label>
        ) : null}
        <label className="enterprise-access-mutation-form__wide">
          <span>变更原因</span>
          <Textarea
            aria-label="变更原因"
            value={reason}
            disabled={saving}
            placeholder="说明本次授权变更的业务原因"
            autosize={{ minRows: 3, maxRows: 6 }}
            onChange={(value) => {
              setReason(String(value));
              setValidation("");
            }}
          />
        </label>
      </div>
    </Dialog>
  );
}
