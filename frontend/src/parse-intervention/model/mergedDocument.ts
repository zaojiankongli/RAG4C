import type { DocumentChunkItem } from "../../types/rag";

/**
 * 整篇合并视图（WeKnora 的 `merged`）：把当前权威的 ChunkHead 按服务端次序拼回一篇可读全文。
 *
 * 刻意不复算原文比对 —— 列表响应不返回 `source_content`，逐行 diff 需要每条切片单独取详情。
 * 合并视图只回答"知识当前是什么"，不冒充"解析器当初看到了什么"（那是原文预览，另一件事）。
 */

export type MergedSegmentKind = "body" | "tombstone";

export interface MergedSegment {
  chunkId: string;
  kind: MergedSegmentKind;
  /** 墓碑的正文恒为空串：破洞原位可见，但不参与拼接 */
  text: string;
  seq: number;
  revision: number;
  editedManually: boolean;
  editSource?: string;
}

export interface MergedDocument {
  /** 已按服务端给定次序排好；调用方不要再按 `seq` 重排 */
  segments: MergedSegment[];
  segmentCount: number;
  /** 仅 body 段以空行相连 */
  bodyText: string;
  bodyCharCount: number;
  tombstoneCount: number;
  manuallyEditedCount: number;
  excludedParentCount: number;
  loadedCount: number;
  totalCount: number | null;
  complete: boolean;
  coverageLabel: string;
}

const PARENT_ROLE = "parent";

function toSegment(item: DocumentChunkItem): MergedSegment {
  const revision = Number(item.content_revision) || 0;
  const segment: MergedSegment = {
    chunkId: item.chunk_id,
    kind: item.enabled === false ? "tombstone" : "body",
    text: item.enabled === false ? "" : item.text ?? "",
    seq: Number(item.seq) || 0,
    revision,
    editedManually: revision > 1,
  };
  if (item.edit_source) segment.editSource = item.edit_source;
  return segment;
}

function coverageOf(loaded: number, total: number | null): string {
  if (total === null) return `已载入 ${loaded} 个切片，总数未知，无法判断是否已是整篇`;
  if (loaded < total) return `仅包含已载入的前 ${loaded} / ${total} 个切片，继续载入后才是一整篇`;
  return `整篇 ${total} 个切片已全部载入`;
}

export function buildMergedDocument(
  items: readonly DocumentChunkItem[],
  total: number | null = null,
): MergedDocument {
  // 父块与子块同拼会重复正文；服务端列表已按 SQL 过滤，这里再挡一层是为了
  // 让本函数对任意入参都成立（详情拼装、测试夹具、以后换数据源都不会静默翻倍）。
  const parents = items.filter((item) => item.chunk_role === PARENT_ROLE).length;
  const segments = items
    .filter((item) => item.chunk_role !== PARENT_ROLE)
    .map(toSegment);
  const bodies = segments.filter((segment) => segment.kind === "body");
  const bodyText = bodies.map((segment) => segment.text).join("\n\n");
  const reportedTotal = total === null || !Number.isFinite(total) ? null : Math.max(0, Number(total));
  return {
    segments,
    segmentCount: segments.length,
    bodyText,
    bodyCharCount: bodyText.length,
    tombstoneCount: segments.length - bodies.length,
    manuallyEditedCount: segments.filter((segment) => segment.editedManually).length,
    excludedParentCount: parents,
    loadedCount: segments.length,
    totalCount: reportedTotal,
    complete: reportedTotal !== null && segments.length >= reportedTotal,
    coverageLabel: coverageOf(segments.length, reportedTotal),
  };
}
