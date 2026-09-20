import { ApiError } from "../../api/client";

export type StorageProviderId =
  | "local"
  | "minio"
  | "s3"
  | "cos"
  | "oss"
  | "tos"
  | "obs"
  | (string & {});

export type StorageBackendStatus = "active" | "disabled";
export type StorageBackendSource = "user" | "env";
export type StorageBackendTestStatus = "ok" | "validated_config_only" | "error";

/** Provider 字段定义（来自 GET /api/storage-backends/types） */
export interface StorageBackendField {
  name: string;
  label: string;
  required: boolean;
  secret?: boolean;
}

export interface StorageBackendProviderType {
  provider: StorageProviderId;
  label: string;
  fields: StorageBackendField[];
}

/** 脱敏配置：secret_access_key 恒为 "***"，绝不回传明文 */
export interface StorageBackendConfigMasked {
  endpoint?: string;
  bucket?: string;
  region?: string;
  path_prefix?: string;
  access_key_id?: string;
  secret_access_key?: string;
  use_ssl?: boolean;
  force_path_style?: boolean;
  root_path?: string;
  [key: string]: unknown;
}

export interface StorageBackend {
  id: string;
  name: string;
  provider: StorageProviderId;
  status: StorageBackendStatus;
  source: StorageBackendSource;
  is_default: boolean;
  config_masked: StorageBackendConfigMasked;
  created_at: string;
  updated_at: string;
}

export interface StorageBackendListResponse {
  items: StorageBackend[];
  default_storage_backend_id: string | null;
}

export interface StorageBackendTypesResponse {
  providers: StorageBackendProviderType[];
}

export type StorageBackendConfigInput = Record<string, string | boolean | undefined>;

export interface StorageBackendCreatePayload {
  name: string;
  provider: StorageProviderId;
  config: StorageBackendConfigInput;
}

export interface StorageBackendPatchPayload {
  name?: string;
  status?: StorageBackendStatus;
  config?: StorageBackendConfigInput;
}

export interface StorageBackendTestResult {
  status: StorageBackendTestStatus;
  detail: string;
}

export interface StorageBackendBindPayload {
  dataset_id: string;
  storage_backend_id: string;
}

export interface KnowledgeBaseOption {
  id: string;
  name: string;
}

export type StorageBackendErrorKind =
  | "offline"
  | "conflict"
  | "forbidden"
  | "not-found"
  | "invalid"
  | "unavailable";

export interface StorageBackendErrorView {
  kind: StorageBackendErrorKind;
  title: string;
  description: string;
  canRetry: boolean;
}

export const PROVIDER_LABELS: Record<string, string> = {
  local: "本地目录",
  minio: "MinIO",
  s3: "Amazon S3",
  cos: "腾讯云 COS",
  oss: "阿里云 OSS",
  tos: "火山引擎 TOS",
  obs: "华为云 OBS",
};

export const STATUS_LABELS: Record<StorageBackendStatus, string> = {
  active: "启用",
  disabled: "停用",
};

export const SOURCE_LABELS: Record<StorageBackendSource, string> = {
  user: "用户注册",
  env: "环境变量",
};

export const TEST_STATUS_LABELS: Record<StorageBackendTestStatus, string> = {
  ok: "连通成功",
  validated_config_only: "仅校验配置",
  error: "连接失败",
};

/** 无 SDK 时的兜底 provider 字段（spec 2.3 / 2.5） */
export const FALLBACK_PROVIDER_FIELDS: StorageBackendProviderType[] = [
  {
    provider: "local",
    label: PROVIDER_LABELS.local,
    fields: [{ name: "root_path", label: "根路径", required: true }],
  },
  {
    provider: "minio",
    label: PROVIDER_LABELS.minio,
    fields: [
      { name: "endpoint", label: "Endpoint", required: true },
      { name: "bucket", label: "存储桶", required: true },
      { name: "access_key_id", label: "Access Key ID", required: true },
      { name: "secret_access_key", label: "Secret Access Key", required: true, secret: true },
      { name: "use_ssl", label: "使用 SSL", required: false },
      { name: "force_path_style", label: "Path-style", required: false },
    ],
  },
  {
    provider: "s3",
    label: PROVIDER_LABELS.s3,
    fields: [
      { name: "endpoint", label: "Endpoint", required: false },
      { name: "region", label: "Region", required: false },
      { name: "bucket", label: "存储桶", required: true },
      { name: "access_key_id", label: "Access Key ID", required: true },
      { name: "secret_access_key", label: "Secret Access Key", required: true, secret: true },
      { name: "path_prefix", label: "路径前缀", required: false },
    ],
  },
  ...(["cos", "oss", "tos", "obs"] as const).map((provider) => ({
    provider,
    label: PROVIDER_LABELS[provider] ?? provider,
    fields: [
      { name: "endpoint", label: "Endpoint", required: false },
      { name: "region", label: "Region", required: false },
      { name: "bucket", label: "存储桶", required: true },
      { name: "access_key_id", label: "Access Key ID", required: true },
      { name: "secret_access_key", label: "Secret Access Key", required: true, secret: true },
    ],
  })),
];

export function providerLabel(provider: StorageProviderId): string {
  return PROVIDER_LABELS[provider] ?? String(provider);
}

/** 展示脱敏配置条目：secret_access_key 强制 "***"，其余非空字段列出 */
export function formatMaskedConfigEntries(
  config: StorageBackendConfigMasked | undefined,
): Array<{ key: string; label: string; value: string }> {
  if (!config) return [];
  const entries: Array<{ key: string; label: string; value: string }> = [];
  for (const [key, value] of Object.entries(config)) {
    if (value === undefined || value === null || value === "") continue;
    if (key === "secret_access_key") {
      entries.push({ key, label: "Secret Access Key", value: "***" });
      continue;
    }
    if (key === "access_key_id") {
      entries.push({ key, label: "Access Key ID", value: maskAccessKeyId(String(value)) });
      continue;
    }
    entries.push({ key, label: key.replace(/_/g, " "), value: String(value) });
  }
  return entries;
}

/** access_key_id → 尾 4 保留，其余脱敏（对齐 spec 2.3） */
export function maskAccessKeyId(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "***";
  if (trimmed === "***") return "***";
  const tail = trimmed.slice(-4);
  return `ak_***${tail}`;
}

/** 是否绝不能在 UI 中回显明文（secret 字段永远用占位符） */
export function isSecretConfigField(name: string): boolean {
  return name === "secret_access_key" || name.endsWith("_secret") || name === "password";
}

export function projectStorageBackendError(error: unknown): StorageBackendErrorView {
  if (error instanceof ApiError) {
    if (error.kind === "network" || error.kind === "timeout" || error.kind === "aborted") {
      return {
        kind: "offline",
        title: "无法连接设置服务",
        description: "当前无法读取对象存储后端注册表。请检查后端服务后重试。",
        canRetry: true,
      };
    }
    if (error.status === 409) {
      return {
        kind: "conflict",
        title: "操作被拒绝",
        description: error.message || "默认后端或已绑定知识库的后端不能删除；名称可能已存在。",
        canRetry: false,
      };
    }
    if (error.status === 401 || error.status === 403) {
      return {
        kind: "forbidden",
        title: "没有配置权限",
        description: "当前身份不能读取或修改对象存储后端。",
        canRetry: false,
      };
    }
    if (error.status === 404) {
      return {
        kind: "not-found",
        title: "存储后端不存在",
        description: "该后端可能已被删除，请刷新列表。",
        canRetry: true,
      };
    }
    if (error.status === 400 || error.status === 422) {
      return {
        kind: "invalid",
        title: "提交内容无效",
        description: error.message || "请检查名称、Provider 与必填配置字段后重试。",
        canRetry: false,
      };
    }
  }
  return {
    kind: "unavailable",
    title: "对象存储后端不可用",
    description: "服务未返回可安全展示的结果。请稍后重试。",
    canRetry: true,
  };
}

/** 本地校验：必填字段非空；编辑时 secret 空串表示「保留原值」 */
export function validateStorageConfig(
  fields: StorageBackendField[],
  config: StorageBackendConfigInput,
  mode: "create" | "edit",
): string | null {
  for (const field of fields) {
    if (!field.required) continue;
    const raw = config[field.name];
    const value = raw === undefined || raw === null ? "" : String(raw).trim();
    if (!value) {
      if (mode === "edit" && field.secret) continue;
      return `请填写「${field.label}」`;
    }
  }
  return null;
}

/** 组装 PATCH config：去掉空 secret（后端保持原值），去掉空非必填可选字段时保留空串仅当用户改过 */
export function buildPatchConfig(
  fields: StorageBackendField[],
  config: StorageBackendConfigInput,
): StorageBackendConfigInput {
  const next: StorageBackendConfigInput = {};
  for (const field of fields) {
    const raw = config[field.name];
    const value = raw === undefined || raw === null ? "" : String(raw).trim();
    if (field.secret && value === "") continue;
    if (!field.required && value === "") continue;
    if (value === "" && !field.required) continue;
    next[field.name] = value;
  }
  return next;
}

/** 组装 create config：仅提交非空值；bool 字段保留布尔 */
export function buildCreateConfig(
  fields: StorageBackendField[],
  config: StorageBackendConfigInput,
): StorageBackendConfigInput {
  const next: StorageBackendConfigInput = {};
  const known = new Set(fields.map((f) => f.name));
  for (const field of fields) {
    const raw = config[field.name];
    if (raw === undefined || raw === null) continue;
    if (typeof raw === "boolean") {
      next[field.name] = raw;
      continue;
    }
    const value = String(raw).trim();
    if (!value) continue;
    next[field.name] = value;
  }
  // 透传 types 未声明但用户填了的通用字段
  for (const [key, raw] of Object.entries(config)) {
    if (known.has(key)) continue;
    if (raw === undefined || raw === null || raw === "") continue;
    if (typeof raw === "boolean") {
      next[key] = raw;
      continue;
    }
    next[key] = String(raw).trim();
  }
  return next;
}

export function emptyConfigDraft(fields: StorageBackendField[]): Record<string, string> {
  return Object.fromEntries(fields.map((f) => [f.name, ""]));
}

/** 从脱敏配置回填编辑草稿：secret 永远留空（空=保留原值） */
export function maskedConfigToDraft(
  fields: StorageBackendField[],
  masked: StorageBackendConfigMasked | undefined,
): Record<string, string> {
  const draft = emptyConfigDraft(fields);
  if (!masked) return draft;
  for (const field of fields) {
    if (field.secret || isSecretConfigField(field.name)) {
      draft[field.name] = "";
      continue;
    }
    const value = masked[field.name];
    if (value === undefined || value === null) continue;
    draft[field.name] = typeof value === "boolean" ? String(value) : String(value);
  }
  return draft;
}

export function resolveProviderFields(
  providers: StorageBackendProviderType[],
  provider: StorageProviderId,
): StorageBackendField[] {
  const match = providers.find((item) => item.provider === provider);
  if (match?.fields?.length) return match.fields;
  return FALLBACK_PROVIDER_FIELDS.find((item) => item.provider === provider)?.fields ?? [];
}
