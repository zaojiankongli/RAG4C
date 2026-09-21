import { describe, expect, it } from "vitest";
import {
  countQaEvidence,
  documentDeepLink,
  evidenceQaId,
  evidenceSourceKind,
  normalizeAnswerEvidenceRef,
  parseQaChunkId,
  qaDeepLink,
} from "./answerEvidenceModel";

describe("answer evidence QA source model", () => {
  it("parses qa:: chunk ids", () => {
    expect(parseQaChunkId("qa::qa-1")).toBe("qa-1");
    expect(parseQaChunkId("doc::1")).toBeNull();
    expect(parseQaChunkId(null)).toBeNull();
  });

  it("derives source kind from chunk prefix when API omits fields", () => {
    expect(evidenceSourceKind({ chunk_id: "qa::qa-9", source_kind: undefined })).toBe("qa");
    expect(evidenceSourceKind({ chunk_id: "chunk-1", source_kind: "document" })).toBe("document");
    expect(evidenceQaId({ chunk_id: "qa::qa-9", source_kind: undefined })).toBe("qa-9");
  });

  it("normalizeAnswerEvidenceRef fills qa fields and prefers explicit API values", () => {
    const fromPrefix = normalizeAnswerEvidenceRef({
      id: "r1",
      seq: 0,
      chunk_id: "qa::qa-2",
      document_id: "doc-src",
      citation_status: "ok",
    });
    expect(fromPrefix?.source_kind).toBe("qa");
    expect(fromPrefix?.qa_id).toBe("qa-2");

    const fromApi = normalizeAnswerEvidenceRef({
      id: "r2",
      seq: 1,
      chunk_id: "custom",
      source_kind: "qa",
      qa_id: "qa-api",
      qa_revision: 7,
      citation_status: "ok",
    });
    expect(fromApi?.qa_id).toBe("qa-api");
    expect(fromApi?.qa_revision).toBe(7);
  });

  it("builds governance deep links and suppresses document links for qa ids", () => {
    expect(qaDeepLink("qa-1", "ds-1")).toBe("#/governance?qa=qa-1&dataset=ds-1");
    expect(documentDeepLink("qa::qa-1", "ds-1")).toBeNull();
    expect(documentDeepLink("doc-1", "ds-1")).toBe("#/documents?document=doc-1&dataset=ds-1");
  });

  it("counts QA evidence refs", () => {
    expect(
      countQaEvidence([
        { id: "a", seq: 0, chunk_id: "qa::x", chunk_revision_id: null, document_id: null, citation_status: "ok" },
        { id: "b", seq: 1, chunk_id: "c1", chunk_revision_id: null, document_id: "d1", citation_status: "ok" },
      ]),
    ).toBe(1);
  });
});
