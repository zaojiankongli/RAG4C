import { ApiError } from "../../api/client";
import type { KnowledgeWorkspaceScope } from "../../knowledge/workspaceScope";
import type { ExecutionState, SourceRecord, SourceRun, SourceScope } from "./sourceModels";
export type { SourceRecord } from "./sourceModels";

export class SourceScopeError extends Error {
  constructor() { super("Authenticated source workspace scope is required"); this.name = "SourceScopeError"; }
}

export function requireSourceScope(scope: KnowledgeWorkspaceScope, actorToken: string): SourceScope {
  const tenantId = scope.tenantId.trim();
  const datasetId = scope.datasetId.trim();
  const token = actorToken.trim();
  if (!tenantId || !datasetId || !token) throw new SourceScopeError();
  return { tenantId, datasetId, actorToken: token };
}

export interface SafeConfigSummary { primary: string; details: string; credentialBadge: string | null; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }
function leafPath(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "未提供目录";
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).slice(-1)[0] ?? "本地目录";
}
function credentialBadge(value: unknown): string | null {
  if (typeof value !== "string") return null;
  if (value.startsWith("secret://")) return "secret:// 引用";
  if (value.startsWith("vault://")) return "vault:// 引用";
  return null;
}
export function summarizeSourceConfig(source: SourceRecord): SafeConfigSummary {
  const config = source.config;
  const include = strings(config.include).length;
  const exclude = strings(config.exclude).length;
  const extensions = strings(config.extensions);
  const limits = typeof config.max_files === "number" && config.max_files > 0 ? `最多 ${config.max_files} 个文件` : "文件数不限";
  const patterns = `包含 ${include} 条 / 排除 ${exclude} 条`;
  if (source.kind === "local_dir") {
    return {
      primary: leafPath(config.path),
      details: [extensions.length ? extensions.join("、") : "扩展名由服务端允许列表决定", patterns, limits].join(" · "),
      credentialBadge: credentialBadge(config.credential_ref),
    };
  }
  const repo = typeof config.repo === "string" ? config.repo : "未提供仓库";
  const ref = typeof config.ref === "string" ? config.ref : "main";
  const mode = typeof config.mode === "string" ? config.mode : "auto";
  return {
    primary: repo,
    details: [`${ref} · ${mode}`, extensions.length ? extensions.join("、") : "所有允许扩展名", patterns, limits].join(" · "),
    credentialBadge: credentialBadge(config.credential_ref),
  };
}

export interface SourceErrorView {
  kind: "scope" | "offline" | "unauthorized" | "forbidden" | "not-found" | "conflict" | "invalid" | "unavailable" | "unknown";
  title: string;
  description: string;
  canRetry: boolean;
}
function backendDetail(body: unknown): { code?: string; message?: string } {
  if (!body || typeof body !== "object") return {};
  const candidate = "detail" in body ? (body as { detail?: unknown }).detail : "error" in body ? (body as { error?: unknown }).error : undefined;
  if (!candidate || typeof candidate !== "object") return {};
  const value = candidate as { code?: unknown; message?: unknown };
  return { code: typeof value.code === "string" ? value.code : undefined, message: typeof value.message === "string" ? value.message : undefined };
}
export function projectSourceError(error: unknown): SourceErrorView {
  if (error instanceof SourceScopeError) return { kind: "scope", title: "需要登录知识工作区", description: "缺少可信的租户、数据集或操作者令牌，未发送任何来源请求。", canRetry: false };
  if (error instanceof ApiError) {
    const detail = backendDetail(error.body);
    if (error.kind === "network" || error.kind === "timeout") return { kind: "unavailable", title: "来源服务暂时不可用", description: "保留当前权威快照；恢复连接后可以刷新。", canRetry: true };
    if (error.status === 401) return { kind: "unauthorized", title: "登录凭据已失效", description: "请更新知识工作区令牌后重试。", canRetry: false };
    if (error.status === 403) return { kind: "forbidden", title: "当前账号没有此操作权限", description: "读取、同步和管理权限由后端角色授权决定。", canRetry: false };
    if (error.status === 404) return { kind: "not-found", title: "来源或运行不存在", description: "该资源不属于当前租户和数据集，或已被移除。", canRetry: true };
    if (error.status === 409) return { kind: "conflict", title: "来源代次已变化", description: "权威配置已刷新。请核对新代次后再次提交。", canRetry: true };
    if (error.status === 422 && detail.code === "knowledge_source_invalid" && detail.message) return { kind: "invalid", title: "连接器配置被服务端拒绝", description: detail.message.slice(0, 300), canRetry: false };
    if (error.status === 422) return { kind: "invalid", title: "请求不符合 Source API 契约", description: "请检查连接器字段、筛选条件或分页状态。", canRetry: false };
    if (error.status === 503) return { kind: "unavailable", title: "来源控制面不可用", description: "后端当前无法读取或调度来源操作。", canRetry: true };
  }
  return { kind: "unknown", title: "来源操作失败", description: "未显示未经验证的服务端错误内容。请刷新后重试。", canRetry: true };
}

export function isExecutionActive(state: ExecutionState): boolean { return state !== "completed"; }
export function sameSourceGeneration(run: Pick<SourceRun, "source_generation">, source: Pick<SourceRecord, "generation">): boolean { return run.source_generation === source.generation; }
export function shortenReference(full: string): { display: string; full: string } {
  return full.length <= 20 ? { display: full, full } : { display: `${full.slice(0, 8)}…${full.slice(-6)}`, full };
}


function safeBasename(value: string): string | null {
  const normalized = value.replace(/\\/g, "/").replace(/\/+$/, "");
  const raw = normalized.split("/").filter(Boolean).slice(-1)[0] ?? "";
  const name = (() => { try { return decodeURIComponent(raw); } catch { return raw; } })();
  if (!name || name.length > 128 || [...name].some((char) => { const code = char.charCodeAt(0); return code < 32 || code === 127; })) return null;
  if (/[/?#\\]/.test(name) || !/\.[A-Za-z0-9]{1,16}$/.test(name)) return null;
  return name;
}
export function safeSourceUri(value: string): string {
  try {
    const parsed = new URL(value);
    const basename = safeBasename(parsed.pathname);
    if (parsed.protocol === "file:") return basename ? `[本地文件引用] · ${basename}` : "[本地文件引用]";
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      const origin = `${parsed.protocol}//${parsed.host}`;
      return basename ? `${origin} · ${basename}` : origin;
    }
  } catch { /* fall through to opaque reference */ }
  const basename = safeBasename(value);
  return basename ? `[来源引用] · ${basename}` : "[来源引用]";
}

export function acceptedRequestSummary(accepted: import("./sourceModels").RunAccepted): { action: string; current: string } {
  const status = { running: "运行中", completed: "运行完成", failed: "运行失败", incomplete: "不完整", dry_run: "演练完成", superseded: "已被代次取代" }[accepted.status];
  const execution = { pending: "等待执行", executing: "正在执行", failed: "执行尝试失败", completed: "执行已结束" }[accepted.execution_state];
  return { action: accepted.replayed ? "原请求重放" : "请求已接受", current: `当前状态：${status} / ${execution}` };
}
