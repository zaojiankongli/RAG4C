import { describe, expect, it } from "vitest";
import { projectKnowledgeOverview, projectSources, projectTaxonomy } from "./knowledgeModel";
import type { DocumentItem } from "../types/rag";

const docs: DocumentItem[] = [
  { id:"1", name:"a.pdf", status:"completed", progress:1, chunk_count:10, doc_type:"pdf", error_message:"", logical_folder_path:"制度/人力", tags:["员工","制度"], source_type:"local", source_id:"manual", parser_meta:{engine:"vision"}, updated_at:"2026-08-24T10:00:00" },
  { id:"2", name:"b.md", status:"completed", progress:1, chunk_count:5, doc_type:"markdown", error_message:"", logical_folder_path:"制度/人力", tags:["制度"], source_type:"github", source_id:"repo-a", parser_meta:{engine:"fast"}, updated_at:"2026-08-23T10:00:00" },
  { id:"3", name:"c.docx", status:"error", progress:.4, chunk_count:0, doc_type:"word", error_message:"failed", logical_folder_path:"产品", tags:["产品"], source_type:"github", source_id:"repo-a", parser_meta:{}, updated_at:"2026-08-22T10:00:00" },
];

describe("knowledge navigation projections", () => {
  it("projects overview and parser coverage", () => {
    expect(projectKnowledgeOverview(docs)).toMatchObject({ total:3, completed:2, failed:1, chunks:15, categories:2, tags:3, parserCoverage:67 });
  });
  it("groups real data sources", () => {
    expect(projectSources(docs)).toEqual([
      { key:"github:repo-a", label:"repo-a", type:"github", documents:2, chunks:5, errors:1 },
      { key:"local:manual", label:"manual", type:"local", documents:1, chunks:10, errors:0 },
    ]);
  });
  it("projects categories and tags", () => {
    const taxonomy=projectTaxonomy(docs);
    expect(taxonomy.categories[0]).toMatchObject({ name:"制度/人力", documents:2, chunks:15 });
    expect(taxonomy.tags.find((tag)=>tag.name==="制度")?.documents).toBe(2);
  });
});
