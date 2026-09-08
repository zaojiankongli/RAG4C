import type { DocumentItem } from "../types/rag";
import { IN_FLIGHT_DOCUMENT_STATUSES } from "../documents/documentModel";

export function projectKnowledgeOverview(documents: DocumentItem[]) {
  const categories = new Set(documents.map((doc) => doc.logical_folder_path?.trim() || "未分类"));
  const tags = new Set(documents.flatMap((doc) => doc.tags ?? []));
  const parserObserved = documents.filter((doc) => Object.keys(doc.parser_meta ?? {}).length > 0).length;
  return {
    total: documents.length,
    completed: documents.filter((doc) => doc.status === "completed").length,
    processing: documents.filter((doc) => IN_FLIGHT_DOCUMENT_STATUSES.has(doc.status)).length,
    failed: documents.filter((doc) => doc.status === "error").length,
    chunks: documents.reduce((sum, doc) => sum + Number(doc.chunk_count || 0), 0),
    categories: categories.size,
    tags: tags.size,
    parserCoverage: documents.length ? Math.round(parserObserved / documents.length * 100) : 0,
    recent: [...documents].sort((a, b) => String(b.updated_at ?? "").localeCompare(String(a.updated_at ?? ""))).slice(0, 6),
  };
}

export interface SourceProjection { key:string; label:string; type:string; documents:number; chunks:number; errors:number }
export function projectSources(documents: DocumentItem[]): SourceProjection[] {
  const groups = new Map<string, SourceProjection>();
  for (const doc of documents) {
    const type = doc.source_type?.trim() || "local";
    const label = doc.source_id?.trim() || (type === "local" ? "manual" : "未命名来源");
    const key = `${type}:${label}`;
    const current = groups.get(key) ?? { key, label, type, documents:0, chunks:0, errors:0 };
    current.documents += 1; current.chunks += Number(doc.chunk_count || 0); current.errors += doc.status === "error" ? 1 : 0;
    groups.set(key,current);
  }
  return [...groups.values()].sort((a,b)=>b.documents-a.documents || a.key.localeCompare(b.key));
}

export function projectTaxonomy(documents: DocumentItem[]) {
  const categories = new Map<string,{name:string;documents:number;chunks:number;tags:Set<string>}>();
  const tags = new Map<string,{name:string;documents:number;chunks:number}>();
  for (const doc of documents) {
    const categoryName=doc.logical_folder_path?.trim() || "未分类";
    const category=categories.get(categoryName) ?? {name:categoryName,documents:0,chunks:0,tags:new Set<string>()};
    category.documents += 1; category.chunks += Number(doc.chunk_count || 0); (doc.tags ?? []).forEach((tag)=>category.tags.add(tag)); categories.set(categoryName,category);
    for (const tagName of doc.tags ?? []) { const tag=tags.get(tagName) ?? {name:tagName,documents:0,chunks:0}; tag.documents += 1; tag.chunks += Number(doc.chunk_count || 0); tags.set(tagName,tag); }
  }
  return {
    categories:[...categories.values()].map((item)=>({...item,tags:[...item.tags]})).sort((a,b)=>b.documents-a.documents || a.name.localeCompare(b.name)),
    tags:[...tags.values()].sort((a,b)=>b.documents-a.documents || a.name.localeCompare(b.name)),
  };
}
