import {
  SERVING_EVIDENCE_ROUTE_CATALOG,
  type ServingEvidenceKind,
  type ServingRouteCode,
} from "../enterprise-knowledge-serving/model/servingModel";
import type { PageKey } from "../run/appRoute";

export type KnowledgeBaseResourceSection =
  "overview" | "documents" | "taxonomy" | "sources" | "serving" | "governance" | "releases";

export interface KnowledgeBaseResourceLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
}

export type KnowledgeBaseResourceRouteIntent = { mode: "history" | "hash"; url: string };

export interface KnowledgeBaseEvidenceNavigationInput {
  evidenceKind: ServingEvidenceKind;
  routeCode: ServingRouteCode;
  resourceId: string;
}

// eslint-disable-next-line no-control-regex
const EVIDENCE_RESOURCE_CONTROL = /[\u0000-\u001f\u007f]/;
const EVIDENCE_RESOURCE_URL = /(?:[a-z][a-z0-9+.-]{1,31}:\/\/|(?:^|\s)www\.)\S+/i;

export const KNOWLEDGE_BASE_RESOURCE_PATH = "/enterprise/knowledge-base";

export const KNOWLEDGE_BASE_RESOURCE_SECTIONS: readonly KnowledgeBaseResourceSection[] = [
  "overview",
  "documents",
  "taxonomy",
  "sources",
  "serving",
  "governance",
  "releases",
];

const DATASET_SCOPED_PATHS: Readonly<
  Record<
    Extract<KnowledgeBaseResourceSection, "documents" | "taxonomy" | "sources" | "governance">,
    string
  >
> = {
  documents: "/documents",
  taxonomy: "/taxonomy",
  sources: "/sources",
  governance: "/governance",
};

function normalizedPath(value: string): string {
  return value.replace(/^#/, "").split("?", 1)[0]?.replace(/\/+$/, "") || "/";
}

function isDirectResourceRoute(location: KnowledgeBaseResourceLocationLike): boolean {
  return normalizedPath(location.pathname) === KNOWLEDGE_BASE_RESOURCE_PATH;
}

function isHashResourceRoute(location: KnowledgeBaseResourceLocationLike): boolean {
  return normalizedPath(location.hash ?? "") === KNOWLEDGE_BASE_RESOURCE_PATH;
}

function routeQuery(location: KnowledgeBaseResourceLocationLike): string {
  if (isDirectResourceRoute(location)) return location.search ?? "";
  if (!isHashResourceRoute(location)) return "";
  const hash = location.hash ?? "";
  const queryIndex = hash.indexOf("?");
  return queryIndex >= 0 ? hash.slice(queryIndex) : "";
}

function routeMode(location: KnowledgeBaseResourceLocationLike): "history" | "hash" {
  return location.pathname === "/" || !location.pathname ? "hash" : "history";
}

function safeEvidenceResourceId(value: unknown): string {
  if (typeof value !== "string") throw new Error("evidence resource id is invalid");
  const result = value.trim();
  if (
    !result ||
    result.length > 128 ||
    EVIDENCE_RESOURCE_CONTROL.test(result) ||
    EVIDENCE_RESOURCE_URL.test(result) ||
    /\s/.test(result)
  )
    throw new Error("evidence resource id is invalid");
  return result;
}

export function knowledgeBaseEvidenceNavigationUrl(
  location: KnowledgeBaseResourceLocationLike,
  input: KnowledgeBaseEvidenceNavigationInput,
): KnowledgeBaseResourceRouteIntent {
  const route = SERVING_EVIDENCE_ROUTE_CATALOG[input.evidenceKind];
  if (!route || route.code !== input.routeCode)
    throw new Error("evidence route is not canonical for evidence kind");
  const resourceId = safeEvidenceResourceId(input.resourceId);
  return {
    mode: routeMode(location),
    url: `${route.path}?${route.parameter}=${encodeURIComponent(resourceId)}`,
  };
}

export function knowledgeBaseResourceRouteFromLocation(
  location: KnowledgeBaseResourceLocationLike,
): boolean {
  return isDirectResourceRoute(location) || isHashResourceRoute(location);
}

export function knowledgeBaseResourceDatasetIdFromLocation(
  location: KnowledgeBaseResourceLocationLike,
): string | null {
  const value = new URLSearchParams(routeQuery(location)).get("dataset")?.trim();
  return value || null;
}

export function knowledgeBaseResourceSectionFromLocation(
  location: KnowledgeBaseResourceLocationLike,
): KnowledgeBaseResourceSection {
  const value = new URLSearchParams(routeQuery(location)).get("section")?.trim() ?? "";
  return (KNOWLEDGE_BASE_RESOURCE_SECTIONS as readonly string[]).includes(value)
    ? (value as KnowledgeBaseResourceSection)
    : "overview";
}

export function knowledgeBaseResourceSectionForPage(
  page: PageKey | KnowledgeBaseResourceSection | string,
): KnowledgeBaseResourceSection {
  if ((KNOWLEDGE_BASE_RESOURCE_SECTIONS as readonly string[]).includes(page)) {
    return page as KnowledgeBaseResourceSection;
  }
  if (page === "documents" || page === "taxonomy" || page === "sources" || page === "governance") {
    return page;
  }
  return "overview";
}

export function knowledgeBaseResourcePageKey(section: KnowledgeBaseResourceSection): PageKey {
  return section === "overview" || section === "releases" || section === "serving"
    ? "knowledge-base-workspace"
    : section;
}

export function knowledgeBaseResourceNavigationUrl(
  location: KnowledgeBaseResourceLocationLike,
  input: { datasetId?: string | null; section: KnowledgeBaseResourceSection },
): KnowledgeBaseResourceRouteIntent {
  const params = new URLSearchParams();
  const datasetId = input.datasetId?.trim();
  if (datasetId) params.set("dataset", datasetId);

  const scopedPath =
    input.section in DATASET_SCOPED_PATHS
      ? DATASET_SCOPED_PATHS[input.section as keyof typeof DATASET_SCOPED_PATHS]
      : KNOWLEDGE_BASE_RESOURCE_PATH;
  if (scopedPath === KNOWLEDGE_BASE_RESOURCE_PATH) params.set("section", input.section);

  const query = params.toString();
  return {
    mode: routeMode(location),
    url: `${scopedPath}${query ? `?${query}` : ""}`,
  };
}
