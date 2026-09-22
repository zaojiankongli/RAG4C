import { LEGACY_PARSE_INTERVENTION_PATH, parseChunkWorkbenchLocation } from "../run/appRoute";
import type { ParsedChunkWorkbenchLocation } from "../run/appRoute";

export interface DocumentRouteLocationLike {
  pathname: string;
  search: string;
  hash: string;
}
export type DocumentRouteIntent = { mode: "history" | "hash"; url: string };

/**
 * 老深链别名 `/documents/parse?doc=<id>`：仍由文档页就地接管工作区，但参数迁移到新
 * 形状支持的那一套（`chunk=<id>` 直接选中那个切片），解析逻辑与 `/parse-intervention`
 * 共用 `run/appRoute` 的同一实现。
 *
 * 只认领别名那一条路径：`/parse-intervention` 归 `ChunkWorkbenchPage`。App 的 keep-alive
 * 让文档页在切走后仍然挂载，若它也解析新路径就会渲染出第二个工作区并重复拉同一份 ChunkHead。
 */
export function parseDocumentWorkspaceLocation(location: DocumentRouteLocationLike): ParsedChunkWorkbenchLocation | null {
  return parseChunkWorkbenchLocation(location, [LEGACY_PARSE_INTERVENTION_PATH]);
}

function deploymentMode(location: DocumentRouteLocationLike): "history" | "hash" {
  return location.pathname.startsWith("/documents") ? "history" : "hash";
}

export function documentWorkspaceNavigationIntent(location: DocumentRouteLocationLike, docId: string): DocumentRouteIntent {
  return { mode: deploymentMode(location), url: `/documents/parse?doc=${encodeURIComponent(docId)}` };
}

export function documentsReturnIntent(location: DocumentRouteLocationLike): DocumentRouteIntent {
  return { mode: deploymentMode(location), url: "/documents" };
}
