// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, cleanup } from "@testing-library/react";
import type { ParseScope } from "../model/parseInterventionModel";
import type { DocumentSourceBlob } from "../api/parseInterventionApi";

const api = vi.hoisted(() => ({ fetchDocumentSource: vi.fn() }));
vi.mock("../api/parseInterventionApi", async () => {
  const actual = await vi.importActual<typeof import("../api/parseInterventionApi")>("../api/parseInterventionApi");
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

const { default: SourcePreview, previewKind, dispositionToken, REFUSAL_COPY } =
  await import("./SourcePreview");

const SCOPE: ParseScope = { datasetId: "ds-1", docId: "doc-1", actorToken: "tok", tenantId: "t-1" } as ParseScope;
const OTHER: ParseScope = { ...SCOPE, docId: "doc-2" };

/** 与后端 `_content_disposition()` 同形态：`inline; filename="…"; filename*=UTF-8''…`。
 * 之前这里写的是光秃秃的 "inline" —— 那是后端**永远不会发出**的值，于是整条在线查看
 * 在真后端前已经全灭，而 16 条用例全绿（第九轮评审的 blocker）。 */
const INLINE_HEADER = `inline; filename="指南.pdf"; filename*=UTF-8''%E6%8C%87%E5%8D%97.pdf`;

function blobResponse(mediaType: string, over: Partial<DocumentSourceBlob> = {}): DocumentSourceBlob {
  return { blob: new Blob(["x".repeat(2048)], { type: mediaType }), mediaType, disposition: INLINE_HEADER, etag: '"3-aaaa"', ...over };
}

let created: string[];
let revoked: string[];

beforeEach(() => {
  api.fetchDocumentSource.mockReset();
  created = [];
  revoked = [];
  vi.stubGlobal("URL", Object.assign(Object.create(URL), {
    createObjectURL: (blob: Blob) => {
      const url = `blob:mock-${created.length}-${blob.type || "n/a"}`;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url: string) => { revoked.push(url); },
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function refused(code: string, status = 409) {
  // 与后端 _refused() 同形状：detail.code 决定界面说哪句人话。
  const Refused = (globalThis as unknown as { __Refused: new (message: string, code: string, status: number) => Error }).__Refused;
  return new Refused("原文查看被拒绝", code, status);
}

describe("previewKind", () => {
  it("只在后端声明的类型上渲染，未知类型永远落到下载", () => {
    expect(previewKind("application/pdf")).toBe("pdf");
    expect(previewKind("application/pdf; charset=binary")).toBe("pdf");
    expect(previewKind("image/png")).toBe("image");
    expect(previewKind("text/plain")).toBe("text");
    expect(previewKind("application/json")).toBe("text");
    // 这两条是这张表拒绝 inline 的理由：不能让界面自己把它们渲染起来。
    expect(previewKind("text/html")).toBe("download");
    expect(previewKind("image/svg+xml")).toBe("download");
    expect(previewKind("")).toBe("download");
  });

  it("只认 attachment 表态；表态听不到时退回类型规则，而不是一刀切降级成下载", () => {
    // 只按 mediaType 前缀判，等于界面替后端决定"这一类可以 inline"；但反过来把
    // "没听到表态"当成"要求下载"，就会在跨源时安静地干掉整条在线查看（第九轮 blocker）。
    expect(previewKind("text/plain", 'attachment; filename="x.txt"')).toBe("download");
    expect(previewKind("application/pdf", 'attachment; filename="x.pdf"')).toBe("download");
    expect(previewKind("text/plain", INLINE_HEADER)).toBe("text");
    // 后端真实形态：带参数的整串，从来不是裸 token。
    expect(previewKind("application/pdf", INLINE_HEADER)).toBe("pdf");
    expect(previewKind("application/pdf", "")).toBe("pdf");
    expect(previewKind("application/pdf", null)).toBe("pdf");
    expect(previewKind("application/pdf")).toBe("pdf");
    // 看不懂的表态同样算"没有信息"，但仍然过第二道类型闸门。
    expect(previewKind("text/html", "weird-thing")).toBe("download");
  });

  it("dispositionToken 只取前导 token，大小写与参数都不影响判断", () => {
    expect(dispositionToken(INLINE_HEADER)).toBe("inline");
    expect(dispositionToken('ATTACHMENT; FileName="a"')).toBe("attachment");
    expect(dispositionToken("")).toBeNull();
    expect(dispositionToken(undefined)).toBeNull();
    expect(dispositionToken("proxy-broke-this")).toBeNull();
  });
});

describe("SourcePreview", () => {
  it("PDF 用 sandbox 的 iframe 显示，并带上类型/大小/revision", async () => {
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf"));
    render(<SourcePreview scope={SCOPE} documentName="指南.pdf" />);
    const frame = await waitFor(() => screen.getByTitle("指南.pdf 原文预览"));
    expect(frame.getAttribute("src")).toMatch(/^blob:mock-/);
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.getByText("application/pdf")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();
    expect(screen.getByText("revision 3-aaaa")).toBeTruthy();
    expect(api.fetchDocumentSource).toHaveBeenCalledWith(SCOPE, "inline", expect.any(AbortSignal));
  });

  it("跨源听不到 Content-Disposition 时仍然照常在线查看", async () => {
    // `Content-Disposition` 不在 CORS 的响应头白名单里，客户端在 Vite dev / Tauri 下
    // 可能拿到空串（服务端已用 expose_headers 显式放行，但界面不能假设那一步一定生效）。
    // 上一版的写法是 `disposition !== "inline"` 就降级成下载 —— 那正好把整条在线查看
    // 在真后端面前关掉，而当时 16 条用例全绿，因为夹具发的是后端从不发的裸 "inline"。
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf", { disposition: "" }));
    render(<SourcePreview scope={SCOPE} documentName="指南.pdf" />);
    const frame = await waitFor(() => screen.getByTitle("指南.pdf 原文预览"));
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(screen.queryByText("这一类原文不支持在线查看")).toBeNull();
  });

  it("后端明确要 attachment 时不给任何渲染面，也不给 blob 链接", async () => {
    api.fetchDocumentSource.mockResolvedValue(
      blobResponse("text/plain", { disposition: 'attachment; filename="a.txt"' }),
    );
    const { container } = render(<SourcePreview scope={SCOPE} documentName="a.txt" />);
    await waitFor(() => screen.getByText("下载原文"));
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector('a[href^="blob:"]')).toBeNull();
  });

  it("会执行文档自身内容的类型：DOM 里既没有渲染面也没有 blob 链接，但仍给下载", async () => {
    // 后端那张表已经把这三类划成 inline 不安全，这一条钉的是**第二道闸门真的落在 DOM 上**。
    // previewKind 的单元测试只证明分类器说"download"；把 JSX 里那一支改成渲染 iframe，
    // 分类器一字不动也能让 HTML 在本源上跑起来 —— 只有查真实节点能抓到。
    for (const mediaType of ["text/html", "image/svg+xml", "application/xhtml+xml"]) {
      cleanup();
      api.fetchDocumentSource.mockResolvedValue(blobResponse(mediaType));
      const { container } = render(<SourcePreview scope={SCOPE} documentName="payload" />);
      const note = await waitFor(() => screen.getByText("这一类原文不支持在线查看"));
      expect(note).toBeTruthy();
      // 反手一条：也不能缩成"什么都不显示"，操作员要拿得到原文本身。
      expect(screen.getByText("下载原文")).toBeTruthy();
      expect(container.querySelector("iframe")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("pre")).toBeNull();
      // blob: 链接继承本站源，"新标签页打开"对这三类等于把执行换到顶层窗口。
      expect(container.querySelector('a[href^="blob:"]')).toBeNull();
      expect(container.querySelectorAll("a").length).toBe(0);
    }
  });

  it("每种拒绝原因给不同的一句话，而不是一条通用错误", async () => {
    for (const [code, expectText] of [
      ["source_preview_disabled", "catalog.source_preview_roots"],
      ["source_preview_missing", "已不在磁盘上"],
      ["source_preview_unsupported_kind", "可查看来源表里登记"],
      ["source_preview_too_large", "不做截断展示"],
    ] as const) {
      cleanup();
      api.fetchDocumentSource.mockRejectedValueOnce(refused(code));
      render(<SourcePreview scope={SCOPE} documentName="doc" />);
      const line = await waitFor(() => screen.getByText((content) => content.includes("（") && content.includes(code)));
      expect(line.textContent).toContain(expectText);
      expect(REFUSAL_COPY[code]).toContain(expectText);
    }
  });

  it("切换文档时撤销上一个对象的 URL 并中止在途请求", async () => {
    let resolveFirst: ((value: DocumentSourceBlob) => void) | null = null;
    api.fetchDocumentSource.mockImplementationOnce(
      () => new Promise<DocumentSourceBlob>((resolve) => { resolveFirst = resolve; }),
    );
    api.fetchDocumentSource.mockResolvedValueOnce(blobResponse("image/png"));
    const { rerender, unmount } = render(<SourcePreview scope={SCOPE} documentName="one" />);
    expect(api.fetchDocumentSource).toHaveBeenCalledTimes(1);
    const firstSignal = api.fetchDocumentSource.mock.calls[0][2] as AbortSignal;

    rerender(<SourcePreview scope={OTHER} documentName="two" />);
    await waitFor(() => expect(screen.getByAltText("two 原文")).toBeTruthy());
    expect(firstSignal.aborted).toBe(true);

    // 第一份的迟到成功不能接管界面。
    await act(async () => { resolveFirst?.(blobResponse("application/pdf")); });
    expect(screen.queryByTitle("one 原文预览")).toBeNull();
    expect(created).toEqual(["blob:mock-0-image/png"]);
    // 卸载之后不留没人回收的对象 URL。
    unmount();
    expect(revoked).toEqual(created);
  });

  it("卸载时回收对象 URL", async () => {
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf"));
    const { unmount } = render(<SourcePreview scope={SCOPE} documentName="doc" />);
    await waitFor(() => screen.getByTitle("doc 原文预览"));
    unmount();
    expect(revoked).toEqual(created);
    expect(created.length).toBe(1);
  });

  it("下载走 attachment 那一次请求，不复用 inline 的字节", async () => {
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/vnd.openxmlformats-officedocument.wordprocessingml.document"));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<SourcePreview scope={SCOPE} documentName="plan.docx" />);
    const button = await waitFor(() => screen.getByText("下载原文"));
    await act(async () => { (button.closest("button") ?? button).dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await waitFor(() => expect(api.fetchDocumentSource).toHaveBeenCalledTimes(2));
    expect(api.fetchDocumentSource.mock.calls[1][1]).toBe("attachment");
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("下载用的 object URL 不在 click 同 tick 回收，也不重复回收", async () => {
    // click() 只是把请求交给浏览器，同 tick revokeObjectURL 会让一份写到一半的文件落盘。
    // 反向也要成立：延后不等于泄漏。
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/vnd.openxmlformats-officedocument.wordprocessingml.document"));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { unmount } = render(<SourcePreview scope={SCOPE} documentName="plan.docx" />);
    const button = await waitFor(() => screen.getByText("下载原文"));
    (button.closest("button") ?? button).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    // 只冲微任务：下载那条 .then 不经过 React 状态，而同 tick 断言不能被定时器污染 ——
    // 用 waitFor 观察这一步会自己推进宏任务，把"还没回收"看没。
    for (let tick = 0; tick < 4; tick += 1) await Promise.resolve();
    const downloadUrl = created[1];
    expect(created[0]).not.toBe(downloadUrl);
    expect(api.fetchDocumentSource).toHaveBeenCalledTimes(2);
    expect(revoked).not.toContain(downloadUrl);
    await new Promise((resolve) => { setTimeout(resolve, 10); });
    expect(revoked.filter((url) => url === downloadUrl)).toHaveLength(1);
    unmount();
    expect(revoked.filter((url) => url === downloadUrl)).toHaveLength(1);
    click.mockRestore();
  });

  it("网络失败给重试入口，重试会重新发一次请求", async () => {
    api.fetchDocumentSource.mockRejectedValueOnce(new Error("network down"));
    api.fetchDocumentSource.mockResolvedValue(blobResponse("application/pdf"));
    render(<SourcePreview scope={SCOPE} documentName="doc" />);
    const retry = await waitFor(() => screen.getByText("重试"));
    expect(await screen.findByText("network down")).toBeTruthy();
    await act(async () => { (retry.closest("button") ?? retry).dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await waitFor(() => expect(api.fetchDocumentSource).toHaveBeenCalledTimes(2));
    expect(screen.getByTitle("doc 原文预览")).toBeTruthy();
  });
});
