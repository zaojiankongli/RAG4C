import { describe, expect, it } from "vitest";
import { chunkingDecisionAria, formatChunkingDecision } from "../parse-intervention/model/chunkDiagnostics";

describe("docs parser profile reason projection", () => {
  it("exposes reason code label and full reason for profile UI", () => {
    const view = formatChunkingDecision({
      chunking_mode: "parent_child",
      chunking_reason: "文本 5000 字或存在 1 个版面块，超出简单文档条件（阈值 4000）→ parent_child",
      chunking_reason_code: "complex_or_structured",
      chunking_decision: { text_chars: 5000, layout_blocks: 1, simple_max_chars: 4000 },
    });
    expect(view.modeLabel).toBe("按章节结构");
    expect(view.reasonCodeLabel).toBe("长文或含版面结构");
    expect(view.reason).toContain("5000 字");
    expect(chunkingDecisionAria(view)).toContain("长文或含版面结构");
  });

  it("omits reason when parser_meta lacks diagnostics", () => {
    const view = formatChunkingDecision({ chunking_mode: "recursive" });
    expect(view.reason).toBeNull();
    expect(view.reasonCodeLabel).toBeNull();
  });
});
