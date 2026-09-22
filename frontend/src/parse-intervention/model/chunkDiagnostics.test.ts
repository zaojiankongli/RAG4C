import { describe, expect, it } from "vitest";
import {
  chunkingDecisionAria,
  chunkModeLabel,
  formatChunkingDecision,
  formatEngineDecision,
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

describe("引擎判由与降级（parser_meta 的引擎那一步）", () => {
  it("缺引擎或引擎是空串时如实说未记录，不留空白格", () => {
    expect(formatEngineDecision({}).engineLabel).toBe("未记录");
    // 旧写法 `ENGINE_LABELS[meta.engine ?? ""] ?? meta.engine ?? "未记录"`：
    // engine 为空串时 ?? 不生效，那一格会渲染成空白，看着像"没有这一项"而不是"没记录"。
    expect(formatEngineDecision({ engine: "   " }).engineLabel).toBe("未记录");
    expect(formatEngineDecision({ engine: "vision" }).engineLabel).toBe("视觉解析（OCR）");
  });

  it("认得后端新给的 route_reason：说了走哪条路，也说清了为什么", () => {
    const view = formatEngineDecision({
      engine: "vision",
      provider: "mineru",
      route_reason: "MVP：混合型整本走 vision",
      pdf_type: "mixed",
      confidence: 0.62,
    });
    expect(view.routeReason).toBe("MVP：混合型整本走 vision");
    expect(view.provider).toBe("mineru");
    expect(view.pdfTypeLabel).toBe("混合型");
    expect(view.confidence).toBeCloseTo(0.62);
    expect(view.degraded).toBe(false);
  });

  it("分类失败退到备用引擎时 degraded 为真，并带上原因", () => {
    const view = formatEngineDecision({
      engine: "vision",
      pdf_type: "classification_failed",
      fallback_reason: "pdf-inspector 未安装",
    });
    expect(view.degraded).toBe(true);
    expect(view.fallbackReason).toBe("pdf-inspector 未安装");
    expect(view.pdfTypeLabel).toContain("退路");
  });

  it("认不得的取值原样透出，不编一个中文标签", () => {
    const view = formatEngineDecision({ engine: "quantum", pdf_type: "handwritten" });
    expect(view.engineLabel).toBe("quantum");
    expect(view.pdfTypeLabel).toBe("handwritten");
    expect(view.routeReason).toBeNull();
  });

  it("confidence 不是数字时不参与展示", () => {
    expect(formatEngineDecision({ confidence: Number.NaN }).confidence).toBeNull();
    expect(formatEngineDecision({ confidence: "0.9" as unknown as number }).confidence).toBeNull();
  });
});
