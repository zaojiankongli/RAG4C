import { describe, expect, it } from "vitest";
import {
  chunkingDecisionAria,
  chunkModeLabel,
  formatChunkingDecision,
} from "./chunkDiagnostics";

describe("chunkDiagnostics model", () => {
  it("maps mode labels and missing mode to 待记录", () => {
    expect(chunkModeLabel("parent_child")).toBe("按章节结构");
    expect(chunkModeLabel("")).toBe("待记录");
  });

  it("formats reason, reason code, and fact summary", () => {
    const view = formatChunkingDecision({
      chunking_mode: "parent_child",
      chunking_reason: "文本 5000 字或存在 1 个版面块",
      chunking_reason_code: "complex_or_structured",
      chunking_decision: {
        doc_type: "pdf",
        text_chars: 5000,
        layout_blocks: 1,
        simple_max_chars: 4000,
        configured_mode: "auto",
      },
    });
    expect(view.modeLabel).toBe("按章节结构");
    expect(view.reasonCodeLabel).toBe("长文或含版面结构");
    expect(view.factsSummary).toContain("5000 字");
    expect(view.factsSummary).toContain("1 版面块");
    expect(chunkingDecisionAria(view)).toContain("文本 5000 字");
  });

  it("returns empty diagnostics for historical docs without reason", () => {
    const view = formatChunkingDecision({ chunking_mode: "recursive" });
    expect(view.modeLabel).toBe("按固定长度");
    expect(view.reason).toBeNull();
    expect(view.factsSummary).toBeNull();
  });
});
