/**
 * 应用壳层的手递（handoff）纯函数与常量。
 *
 * 从 App.tsx 拆出：任务中心 / 自动化中心 / 知识服务 的"跳转到目标页面"逻辑。
 * 全部为**纯函数 + 常量**（无 React state、无副作用），输入 route/location，
 * 输出 { page, url } 或 null。拆分后 App.tsx 只保留装配与事件绑定。
 */

import type { PageKey } from "./appRoute";
import type { TaskRoute } from "../enterprise-task-operations/model/taskModel";
import type { AutomationRoute } from "../enterprise-automation-workflows/model/automationModel";
import type {
  ServingEvidenceKind,
} from "../enterprise-knowledge-serving/model/servingModel";
import type {
  KnowledgeBaseResourceLocationLike,
  KnowledgeBaseResourceSection,
} from "../enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";
import { projectServingHandoff } from "../enterprise-knowledge-serving/model/servingModel";
import { knowledgeBaseEvidenceNavigationUrl } from "../enterprise-knowledge-base-shell/knowledgeBaseResourceRoute";

export const TASK_HANDOFF_ROUTES: Record<
  TaskRoute["path"],
  { page: PageKey; queryKeys: readonly string[] }
> = {
  "/documents": { page: "documents", queryKeys: ["document"] },
  "/sources": { page: "sources", queryKeys: ["source"] },
  "/enterprise": { page: "enterprise", queryKeys: ["section", "export", "run"] },
  "/enterprise/knowledge-base": {
    page: "knowledge-base-workspace",
    queryKeys: ["dataset", "operation"],
  },
};
export const TASK_HANDOFF_VALUE = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/;

export function safeTaskHandoff(route: TaskRoute): { page: PageKey; url: string } | null {
  const target = TASK_HANDOFF_ROUTES[route.path];
  if (!target) return null;
  const allowed = new Set(target.queryKeys);
  const entries = Object.entries(route.query);
  if (entries.some(([key, value]) => !allowed.has(key) || !TASK_HANDOFF_VALUE.test(value)))
    return null;
  const keys = entries
    .map(([key]) => key)
    .sort()
    .join("|");
  const exactRoute =
    (route.path === "/sources" && route.code === "source_control" && keys === "source") ||
    (route.path === "/documents" &&
      (route.code === "document_operations" || route.code === "document_deletion") &&
      keys === "document") ||
    (route.path === "/enterprise/knowledge-base" &&
      route.code === "index_operations" &&
      keys === "dataset|operation") ||
    (route.path === "/enterprise" &&
      route.code === "audit_compliance" &&
      route.query.section === "audit" &&
      keys === "export|section") ||
    (route.path === "/enterprise" &&
      route.code === "release_quality" &&
      route.query.section === "quality" &&
      keys === "run|section");
  if (!exactRoute) return null;
  const query = new URLSearchParams(entries).toString();
  return { page: target.page, url: `${route.path}?${query}` };
}

export const AUTOMATION_HANDOFF = {
  enterprise_tasks: { path: "/enterprise/tasks", page: "tasks", keys: ["task"] },
  knowledge_sources: { path: "/sources", page: "sources", keys: ["source"] },
  release_quality: {
    path: "/enterprise/knowledge-base",
    page: "knowledge-base-workspace",
    keys: ["section"],
  },
  enterprise_approvals: { path: "/enterprise/approvals", page: "enterprise", keys: ["request"] },
} as const;

export const KNOWLEDGE_SERVING_SECTION_BY_KIND: Readonly<
  Partial<Record<ServingEvidenceKind, KnowledgeBaseResourceSection>>
> = {
  source: "sources",
  source_sync_run: "sources",
  document: "documents",
  ingest_attempt: "documents",
  chunk_head: "documents",
  release: "releases",
  certification: "releases",
};

export function safeKnowledgeServingHandoff(
  route: unknown,
  datasetId: string,
  location: KnowledgeBaseResourceLocationLike,
): { page: PageKey; url: string; mode: "history" | "hash" } | null {
  const normalizedDatasetId = datasetId.trim();
  if (!normalizedDatasetId) return null;

  try {
    const handoff = projectServingHandoff(route, {
      tenantId: "application-shell",
      datasetId: normalizedDatasetId,
    });
    if (typeof handoff.resource_id !== "string") return null;
    const intent = knowledgeBaseEvidenceNavigationUrl(location, {
      evidenceKind: handoff.evidence_kind,
      routeCode: handoff.route_code,
      resourceId: handoff.resource_id,
    });
    const [path, query = ""] = intent.url.split("?", 2);
    if (path === "/enterprise/tasks") {
      return { page: "tasks", url: intent.url, mode: intent.mode };
    }
    if (path !== "/enterprise/knowledge-base") return null;
    const section = KNOWLEDGE_SERVING_SECTION_BY_KIND[handoff.evidence_kind];
    if (!section) return null;
    const canonicalQuery = new URLSearchParams(query);
    const entries: [string, string][] = [
      ["dataset", normalizedDatasetId],
      ["section", section],
      ...Array.from(canonicalQuery.entries()),
    ];
    return {
      page: "knowledge-base-workspace",
      url: `${path}?${new URLSearchParams(entries).toString()}`,
      mode: intent.mode,
    };
  } catch {
    return null;
  }
}

export function safeAutomationHandoff(route: AutomationRoute): { page: PageKey; url: string } | null {
  const target = AUTOMATION_HANDOFF[route.code as keyof typeof AUTOMATION_HANDOFF];
  if (!target || route.path !== target.path) return null;
  const entries = Object.entries(route.query);
  if (
    entries.some(
      ([key, value]) => !target.keys.includes(key as never) || !TASK_HANDOFF_VALUE.test(value),
    )
  )
    return null;
  if (route.code === "release_quality" && route.query.section !== "quality") return null;
  const query = new URLSearchParams(entries).toString();
  return { page: target.page as PageKey, url: query ? `${target.path}?${query}` : target.path };
}
