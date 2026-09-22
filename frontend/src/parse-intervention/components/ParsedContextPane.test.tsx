// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DocumentItem } from "../../types/rag";
import ParsedContextPane from "./ParsedContextPane";

afterEach(cleanup);

function doc(parserMeta: Record<string, unknown>): DocumentItem {
  return {
    id: "doc-a",
    name: "手册.pdf",
    doc_type: "pdf",
    status: "completed",
    status_detail: "",
    progress: 1,
    chunk_count: 4,
    error_message: "",
    source_type: "upload",
    source_uri: "file:///secret/path/手册.pdf",
    source_id: "",
    external_id: "",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    mutation_generation: 7,
    parser_meta: parserMeta,
    updated_at: null,
  } as unknown as DocumentItem;
}

function fact(label: string): string | null {
  const pair = Array.from(document.querySelectorAll(".parse-fact")).find((node) => {
    const key = node.querySelector("dt");
    return key?.textContent === label;
  });
  return pair ? (pair.querySelector("dd")?.textContent ?? "") : null;
}

describe("解析上下文里的引擎那一步", () => {
  it("说了走哪条路，也说了为什么，并且不把两件事挤成一行", () => {
    render(
      <ParsedContextPane
        document={doc({
          engine: "vision",
          provider: "mineru",
          pdf_type: "mixed",
          route_reason: "MVP：混合型整本走 vision",
          chunking_mode: "parent_child",
          chunking_reason: "长文或含版面结构",
        })}
        chunks={[]}
      />,
    );
    expect(fact("解析引擎")).toBe("视觉解析（OCR） · mineru");
    expect(fact("引擎判由")).toContain("MVP：混合型整本走 vision");
    // 切分那句"为什么"是另一件事，标签必须各自明确，否则操作员会以为引擎判由 = 切分理由。
    expect(fact("切分理由")).toContain("长文或含版面结构");
    expect(screen.queryByText("决策理由")).toBeNull();
    expect(fact("文档形态")).toBe("混合型");
    // 页面底部那句"没有原文预览"的 info 提示本来就在，这里只断言不该出现警示级提示。
    expect(document.querySelector(".t-alert--warning")).toBeNull();
  });

  it("分类失败退到备用引擎时，页面上有一处一眼看得出的降级提示", () => {
    render(
      <ParsedContextPane
        document={doc({
          engine: "vision",
          pdf_type: "classification_failed",
          fallback_reason: "pdf-inspector 未安装",
        })}
        chunks={[]}
      />,
    );
    const warning = document.querySelector(".t-alert--warning");
    expect(warning).not.toBeNull();
    expect(warning?.textContent).toContain("这篇是按退路解析的，不是正常路径");
    expect(warning?.textContent).toContain("pdf-inspector 未安装");
    // 提示是"加出来的"，不是把引擎那一行改成警示色就算完。
    expect(fact("引擎判由")).toContain("未记录");
  });

  it("老文档缺这些字段时逐项如实说未记录，不假装有值", () => {
    render(<ParsedContextPane document={doc({})} chunks={[]} />);
    expect(fact("解析引擎")).toBe("未记录");
    expect(fact("引擎判由")).toContain("未记录");
    expect(fact("文档形态")).toBe("pdf");
    expect(document.querySelector(".t-alert--warning")).toBeNull();
  });
});
