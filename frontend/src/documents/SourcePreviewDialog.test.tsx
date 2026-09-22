// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import type { ParseScope } from "../parse-intervention/model/parseInterventionModel";
import type { DocumentSourceBlob } from "../parse-intervention/api/parseInterventionApi";

const api = vi.hoisted(() => ({ fetchDocumentSource: vi.fn() }));
vi.mock("../parse-intervention/api/parseInterventionApi", async () => {
  const actual =
    await vi.importActual<typeof import("../parse-intervention/api/parseInterventionApi")>(
      "../parse-intervention/api/parseInterventionApi"
    );
  class Refused extends Error {
    code: string;
    status: number;
    constructor(message: string, code: string, status: number) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }
  Object.assign(Refused.prototype, { name: "SourcePreviewRefusedError" });
  (globalThis as unknown as { __Refused: typeof Refused }).__Refused = Refused;
  return { ...actual, fetchDocumentSource: api.fetchDocumentSource, SourcePreviewRefusedError: Refused };
});

const { default: SourcePreviewDialog } = await import("./SourcePreviewDialog");
const { REFUSAL_COPY } = await import("../parse-intervention/components/SourcePreview");
const Refused = (globalThis as unknown as { __Refused: new (m: string, c: string, s: number) => Error }).__Refused;

const SCOPE: Omit<ParseScope, "docId"> = { tenantId: "t-1", datasetId: "ds-1", actorToken: "tok" };

/** 与后端 `_content_disposition()` 同形态：带参数，从来不是光秃秃的 `inline`。 */
const INLINE_HEADER = `inline; filename="指南.pdf"; filename*=UTF-8''%E6%8C%87%E5%8D%97.pdf`;
const ATTACH_HEADER = `attachment; filename="指南.pdf"`;

function blobResponse(mediaType: string, over: Partial<DocumentSourceBlob> = {}): DocumentSourceBlob {
  return {
    blob: new Blob(["x".repeat(2048)], { type: mediaType }),
    mediaType,
    disposition: INLINE_HEADER,
    etag: '"3-aaaa"',
    ...over,
  };
}

function open(documentId = "doc-1") {
  return render(
    <SourcePreviewDialog
      documentId={documentId}
      documentName="指南.pdf"
      scope={SCOPE}
      onClose={() => {}}
    />
  );
}

beforeEach(() => {
  api.fetchDocumentSource.mockReset();
  Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:one"), configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
});
afterEach(cleanup);

describe("SourcePreviewDialog", () => {
  it("后端表态 attachment 时这一层同样只有下载，拿不到渲染面", async () => {
    // 这条给"薄壳"本身把门：壳里若长出 mediaType/后缀分支，就是在后端那张表之外另开一个
    // "这类可以 inline"的判断源 —— 第九轮那个 blocker 的形状就是这么来的。
    api.fetchDocumentSource.mockResolvedValue(
      blobResponse("application/pdf", { disposition: ATTACH_HEADER })
    );
    open();
    // 取的是异步落到 ready 之后的那一帧：loading 阶段本来就没有 iframe，同步断言是假绿。
    await waitFor(() => expect(document.body.textContent).toContain("不支持在线查看"));
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("pre")).toBeNull();
    expect(document.querySelector('a[href^="blob:"]')).toBeNull();
  });

  it("表态 inline 时渲染面交给 SourcePreview，并把 scope 补成它要的完整形状", async () => {
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf"));
    open("doc-9");
    await waitFor(() => expect(document.querySelector("iframe")).toBeTruthy());
    expect(api.fetchDocumentSource).toHaveBeenCalledWith(
      { ...SCOPE, docId: "doc-9" },
      "inline",
      expect.any(AbortSignal)
    );
  });

  it("六种拒绝原因各出一句人话：分清「这份没有」与「这台机器没开这个能力」", async () => {
    const codes = Object.keys(REFUSAL_COPY);
    expect(codes.length).toBeGreaterThanOrEqual(6);
    for (const code of codes) {
      api.fetchDocumentSource.mockRejectedValue(new Refused(REFUSAL_COPY[code], code, 409));
      const { unmount } = open();
      // 渲染形态是 `${人话}（${code}）`，所以按 contains 而不是精确文本匹配。
      await waitFor(() => expect(document.body.textContent).toContain(REFUSAL_COPY[code]));
      expect(document.body.textContent).toContain(code);
      unmount();
    }
  });

  it("对话框自身有可读的无障碍名，查看面不是套在列表上的一层框架", async () => {
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf"));
    open();
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("指南.pdf");
  });
});
