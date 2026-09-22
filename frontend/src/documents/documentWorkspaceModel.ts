import type { DocumentChunkItem, DocumentItem } from "../types/rag";
import { IN_FLIGHT_DOCUMENT_STATUSES } from "./documentModel";

export interface DocumentFilters {
  keyword: string;
  status: string;
  type: string;
  engine: string;
  chunkingReason: string;
  category?: string;
  tag?: string;
}

function documentType(document: DocumentItem): string {
  const declared = document.doc_type?.trim().toLowerCase();
  if (declared) return declared;
  return document.name.split(".").pop()?.toLowerCase() || "other";
}

function documentEngine(document: DocumentItem): string {
  return document.parser_meta?.engine?.trim().toLowerCase() || "unknown";
}

/** 与后端 `parser_meta.chunking_reason_code` 的 unknown 桶同义：没写就是没写，不猜。 */
function documentChunkingReason(document: DocumentItem): string {
  return document.parser_meta?.chunking_reason_code?.trim().toLowerCase() || "unknown";
}

export function buildDocumentFacets(documents: DocumentItem[]) {
  const types: Record<string, number> = {};
  const engines: Record<string, number> = {};
  const chunkingReasons: Record<string, number> = {};
  const categories: Record<string, number> = {};
  const tags: Record<string, number> = {};
  const statuses = { all: documents.length, completed: 0, processing: 0, error: 0 };
  for (const document of documents) {
    if (document.status === "completed") statuses.completed += 1;
    else if (document.status === "error") statuses.error += 1;
    else if (IN_FLIGHT_DOCUMENT_STATUSES.has(document.status)) statuses.processing += 1;
    const type = documentType(document);
    const engine = documentEngine(document);
    const chunkingReason = documentChunkingReason(document);
    types[type] = (types[type] ?? 0) + 1;
    engines[engine] = (engines[engine] ?? 0) + 1;
    chunkingReasons[chunkingReason] = (chunkingReasons[chunkingReason] ?? 0) + 1;
    const category = document.logical_folder_path?.trim() || "未分类";
    categories[category] = (categories[category] ?? 0) + 1;
    for (const tag of document.tags ?? []) tags[tag] = (tags[tag] ?? 0) + 1;
  }
  return { statuses, types, engines, chunkingReasons, categories, tags };
}

export function filterDocuments(documents: DocumentItem[], filters: DocumentFilters): DocumentItem[] {
  const keyword = filters.keyword.trim().toLowerCase();
  return documents.filter((document) => {
    if (filters.status === "processing" && !IN_FLIGHT_DOCUMENT_STATUSES.has(document.status)) return false;
    if (filters.status !== "all" && filters.status !== "processing" && document.status !== filters.status) return false;
    if (filters.type !== "all" && documentType(document) !== filters.type) return false;
    if (filters.engine !== "all" && documentEngine(document) !== filters.engine) return false;
    if (filters.chunkingReason !== "all" && documentChunkingReason(document) !== filters.chunkingReason) return false;
    if (filters.category && filters.category !== "all") {
      const category = document.logical_folder_path?.trim() || "未分类";
      if (category !== filters.category) return false;
    }
    if (filters.tag && filters.tag !== "all" && !(document.tags ?? []).includes(filters.tag)) return false;
    if (keyword && !`${document.name} ${document.id}`.toLowerCase().includes(keyword)) return false;
    return true;
  });
}

export function chunkDisplayTitle(chunk: DocumentChunkItem): string {
  const prefix = `切片 ${Number(chunk.seq ?? 0) + 1}`;
  return chunk.heading ? `${prefix} · ${chunk.heading}` : prefix;
}

export function shortChunkText(chunk: DocumentChunkItem, limit = 100): string {
  const normalized = chunk.text.replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}…` : normalized;
}


export function replaceChunk(chunks: DocumentChunkItem[], updated: DocumentChunkItem): DocumentChunkItem[] {
  return chunks.map((chunk) => chunk.chunk_id === updated.chunk_id ? updated : chunk);
}

export function removeChunk(chunks: DocumentChunkItem[], chunkId: string): DocumentChunkItem[] {
  return chunks.filter((chunk) => chunk.chunk_id !== chunkId);
}
