import { ApiError } from "../../api/client";
import type { KnowledgeWorkspaceScope } from "../../knowledge/workspaceScope";

export type JsonObject = Record<string, unknown>;
export type DatasetStatus = "active" | "archived" | "disabled";
export type DatasetVisibility = "private" | "tenant" | "public";
export type QAOrigin = "manual" | "automatic";
export type QAReviewStatus = "pending" | "approved" | "rejected";
export type QAReviewDecision = "approved" | "rejected";
export type QALifecycleState =
  | "active"
  | "expired"
  | "delete_requested"
  | "deleting"
  | "delete_failed"
  | "deleted";

export interface GovernanceScope extends KnowledgeWorkspaceScope {
  actorToken: string;
}

export class GovernanceScopeError extends Error {
  constructor() {
    super("Authenticated knowledge workspace scope is required");
    this.name = "GovernanceScopeError";
  }
}

export function requireGovernanceScope(
  scope: KnowledgeWorkspaceScope,
  actorToken: string,
): GovernanceScope {
  const tenantId = scope.tenantId.trim();
  const datasetId = scope.datasetId.trim();
  const token = actorToken.trim();
  if (!tenantId || !datasetId || !token) throw new GovernanceScopeError();
  return { tenantId, datasetId, actorToken: token };
}

export interface DatasetPolicies {
  parser: JsonObject;
  chunk: JsonObject;
  retrieval: JsonObject;
  retention: JsonObject;
  metadata: JsonObject;
}

export interface DatasetProfile {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: DatasetStatus;
  profile_revision: number;
  owner_id: string | null;
  visibility: DatasetVisibility;
  profile: JsonObject;
  policies: DatasetPolicies;
  default_language: string;
  graph_enabled: boolean;
  qa_enabled: boolean;
  usage: { documents: number; chunks: number };
  timestamps: {
    created_at: string;
    updated_at: string;
    archived_at: string | null;
    archived_by: string | null;
  };
}

export interface DatasetPoliciesPatch {
  parser?: JsonObject;
  chunk?: JsonObject;
  retrieval?: JsonObject;
  retention?: JsonObject;
  metadata?: JsonObject;
}

export interface DatasetProfilePatch {
  expected_revision: number;
  owner_id?: string | null;
  visibility?: DatasetVisibility;
  profile?: JsonObject;
  policies?: DatasetPoliciesPatch;
  default_language?: string;
  graph_enabled?: boolean;
  qa_enabled?: boolean;
}

export interface DatasetRevisionRequest {
  expected_revision: number;
}

export interface QAAlternative {
  id: string;
  qa_id: string;
  question: string;
  created_by: string;
  created_at: string;
}

export interface QAAlternativeCreated extends QAAlternative {
  qa_revision: number;
}

export interface QAKnowledge {
  id: string;
  tenant_id: string;
  dataset_id: string;
  revision: number;
  question: string;
  answer: string;
  origin: QAOrigin;
  review_status: QAReviewStatus;
  lifecycle_state: QALifecycleState;
  retrieval_enabled: boolean;
  effective_from: string | null;
  expires_at: string | null;
  source_document_id: string | null;
  source_uri: string;
  metadata: JsonObject;
  created_by: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
  alternatives: QAAlternative[];
}

export interface QAListResponse {
  items: QAKnowledge[];
  count: number;
}

export interface QAListFilters {
  review_status?: QAReviewStatus;
  lifecycle_state?: QALifecycleState;
  origin?: QAOrigin;
  limit?: number;
}

export interface QACreate {
  question: string;
  answer: string;
  origin?: QAOrigin;
  source_document_id?: string | null;
  source_uri?: string;
  metadata?: JsonObject;
  effective_from?: string | null;
  expires_at?: string | null;
}

export interface QAUpdate {
  expected_revision: number;
  question?: string;
  answer?: string;
  source_document_id?: string | null;
  source_uri?: string;
  metadata?: JsonObject | null;
  effective_from?: string | null;
  expires_at?: string | null;
}

export interface QAReviewRequest extends DatasetRevisionRequest {
  decision: QAReviewDecision;
}

export interface QAAlternativeCreate extends DatasetRevisionRequest {
  question: string;
}

export interface DocumentVersion {
  id: string;
  tenant_id: string;
  dataset_id: string;
  document_id: string;
  revision: number;
  source_identity: string;
  source_hash: string;
  parser_policy_snapshot: JsonObject;
  parser_metadata: JsonObject;
  source_content_ref: string;
  created_by: string;
  change_reason: string;
  created_at: string;
}

export interface DocumentVersionListResponse {
  items: DocumentVersion[];
  count: number;
}

export interface DocumentVersionCreate {
  expected_current_revision: number;
  expected_current_version_id?: string | null;
  source_identity: string;
  source_hash: string;
  parser_policy_snapshot?: JsonObject;
  parser_metadata?: JsonObject;
  source_content_ref?: string;
  change_reason?: string;
  effective_from?: string | null;
  expires_at?: string | null;
  purge_after?: string | null;
  retrieval_enabled?: boolean;
}

export const datasetStatusPresentation: Record<
  DatasetStatus,
  { label: string; theme: "success" | "warning" | "danger" }
> = {
  active: { label: "运行中", theme: "success" },
  archived: { label: "已归档", theme: "warning" },
  disabled: { label: "已停用", theme: "danger" },
};

export const reviewStatusPresentation: Record<QAReviewStatus, { label: string; theme: string }> = {
  pending: { label: "待审核", theme: "warning" },
  approved: { label: "已通过", theme: "success" },
  rejected: { label: "已拒绝", theme: "danger" },
};

export const lifecyclePresentation: Record<QALifecycleState, { label: string; theme: string }> = {
  active: { label: "有效", theme: "success" },
  expired: { label: "已过期", theme: "warning" },
  delete_requested: { label: "待删除", theme: "warning" },
  deleting: { label: "删除中", theme: "primary" },
  delete_failed: { label: "删除失败", theme: "danger" },
  deleted: { label: "已删除", theme: "default" },
};

export interface PolicyFact {
  label: string;
  value: string;
}

export interface PolicySummary {
  key: keyof DatasetPolicies;
  label: string;
  facts: PolicyFact[];
  credentialRefs: string[];
}

const POLICY_LABELS: Record<keyof DatasetPolicies, string> = {
  parser: "解析策略",
  chunk: "分块策略",
  retrieval: "检索策略",
  retention: "保留策略",
  metadata: "元数据策略",
};

const SAFE_FACTS: Record<string, string> = {
  engine: "引擎",
  strategy: "策略",
  max_tokens: "最大 Token",
  overlap: "重叠",
  top_k: "Top K",
  days: "保留天数",
  language: "语言",
};

const SECRET_KEY = /(secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key|dsn)/i;
const REFERENCE_KEY = /(credential|secret|token|key).*(ref|reference)$|(^|_)(ref|reference)$/i;

export function safeReference(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    const url = new URL(trimmed);
    const scheme = url.protocol.toLowerCase();
    if (!["https:", "http:", "vault:", "secret-manager:", "aws-secretsmanager:"].includes(scheme)) return "[受保护引用]";
    const authority = url.hostname ? `//${url.hostname}${url.port ? `:${url.port}` : ""}` : "";
    return `${scheme}${authority}${url.pathname || ""}`.slice(0, 160);
  } catch { return "[受保护引用]"; }
}

function collectCredentialRefs(value: unknown, path = ""): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const refs: string[] = [];
  for (const [key, nested] of Object.entries(value as JsonObject)) {
    const nextPath = path ? `${path}.${key}` : key;
    if (REFERENCE_KEY.test(key) && typeof nested === "string") {
      const safe = safeReference(nested);
      if (safe) refs.push(safe);
      continue;
    }
    if (nested && typeof nested === "object") refs.push(...collectCredentialRefs(nested, nextPath));
  }
  return refs;
}

export function summarizeDatasetPolicies(policies: DatasetPolicies): PolicySummary[] {
  return (Object.keys(POLICY_LABELS) as Array<keyof DatasetPolicies>).map((key) => {
    const policy = policies[key] ?? {};
    const facts: PolicyFact[] = [];
    for (const [field, label] of Object.entries(SAFE_FACTS)) {
      const value = policy[field];
      if (SECRET_KEY.test(field) || value === undefined || value === null) continue;
      if (["string", "number", "boolean"].includes(typeof value)) {
        facts.push({ label, value: String(value) });
      }
    }
    return {
      key,
      label: POLICY_LABELS[key],
      facts,
      credentialRefs: collectCredentialRefs(policy),
    };
  });
}


export interface QAActionPolicy { edit:boolean; review:boolean; alternatives:boolean; expire:boolean; restore:boolean }
const NO_QA_ACTIONS: QAActionPolicy = { edit:false, review:false, alternatives:false, expire:false, restore:false };
export const qaActionPolicy: Record<QALifecycleState, QAActionPolicy> = {
  active: { edit:true, review:true, alternatives:true, expire:true, restore:false },
  expired: { edit:false, review:false, alternatives:false, expire:false, restore:true },
  delete_requested: NO_QA_ACTIONS, deleting: NO_QA_ACTIONS, delete_failed: NO_QA_ACTIONS, deleted: NO_QA_ACTIONS,
};

export function validateDateRange(effectiveFrom: string, expiresAt: string): string | null {
  const effective = effectiveFrom.trim(); const expires = expiresAt.trim();
  const effectiveTime = effective ? Date.parse(effective) : NaN; const expiresTime = expires ? Date.parse(expires) : NaN;
  if (effective && Number.isNaN(effectiveTime)) return "生效时间格式无效。";
  if (expires && Number.isNaN(expiresTime)) return "过期时间格式无效。";
  if (effective && expires && effectiveTime >= expiresTime) return "生效时间必须早于过期时间。";
  return null;
}

export function validateVersionDates(effectiveFrom:string, expiresAt:string, purgeAfter:string): string | null {
  const range=validateDateRange(effectiveFrom,expiresAt); if(range) return range;
  const purge=purgeAfter.trim(), expires=expiresAt.trim(); const purgeTime=purge?Date.parse(purge):NaN;
  if(purge && Number.isNaN(purgeTime)) return "清理时间格式无效。";
  if(purge && expires && purgeTime <= Date.parse(expires)) return "清理时间必须晚于过期时间。";
  return null;
}

export type GovernanceErrorKind =
  | "scope"
  | "offline"
  | "conflict"
  | "forbidden"
  | "not-found"
  | "invalid"
  | "unavailable";

export interface GovernanceErrorView {
  kind: GovernanceErrorKind;
  title: string;
  description: string;
  canRetry: boolean;
}

export function projectGovernanceError(error: unknown): GovernanceErrorView {
  if (error instanceof GovernanceScopeError) {
    return {
      kind: "scope",
      title: "缺少知识库授权范围",
      description: "请选择已授权的租户和知识库，并重新完成身份验证。",
      canRetry: false,
    };
  }
  if (error instanceof ApiError) {
    if (error.kind === "network" || error.kind === "timeout") {
      return {
        kind: "offline",
        title: "无法连接知识治理服务",
        description: "当前事实不可用。请检查服务连接后重试。",
        canRetry: true,
      };
    }
    if (error.status === 409) {
      return {
        kind: "conflict",
        title: "资料已被更新",
        description: "当前修订已过期，请刷新后重新提交。",
        canRetry: true,
      };
    }
    if (error.status === 401 || error.status === 403) {
      return {
        kind: "forbidden",
        title: "没有治理权限",
        description: "当前身份不能读取或修改此知识库。",
        canRetry: false,
      };
    }
    if (error.status === 404) {
      return {
        kind: "not-found",
        title: "治理资源不存在",
        description: "资源可能已删除或不属于当前知识库范围。",
        canRetry: true,
      };
    }
    if (error.status === 422) {
      return {
        kind: "invalid",
        title: "提交内容无效",
        description: "请检查必填字段、格式和修订号后重试。",
        canRetry: false,
      };
    }
  }
  return {
    kind: "unavailable",
    title: "知识治理事实不可用",
    description: "服务未返回可安全展示的治理结果。请稍后重试。",
    canRetry: true,
  };
}
