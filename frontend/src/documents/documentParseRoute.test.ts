import { describe, expect, it } from "vitest";
import {
  documentWorkspaceNavigationIntent,
  documentsReturnIntent,
  parseDocumentWorkspaceLocation,
} from "./documentParseRoute";

const direct = { pathname: "/documents", search: "", hash: "" };
const hashed = { pathname: "/", search: "", hash: "#/documents" };

describe("document parse workspace route", () => {
  it("parses direct and hash nested routes without accepting incomplete scopes", () => {
    expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "?doc=doc%2F1", hash: "" })).toEqual({ docId: "doc/1", chunkId: "", datasetId: "" });
    expect(parseDocumentWorkspaceLocation({ pathname: "/", search: "", hash: "#/documents/parse?doc=doc%2F1" })).toEqual({ docId: "doc/1", chunkId: "", datasetId: "" });
    // 别名同时迁移新形状的参数：chunk 直接选中那个切片
    expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "?doc=doc-1&chunk=c-2", hash: "" })).toEqual({ docId: "doc-1", chunkId: "c-2", datasetId: "" });
    expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "", hash: "" })).toBeNull();
    expect(parseDocumentWorkspaceLocation({ pathname: "/monitor", search: "?doc=doc-1", hash: "" })).toBeNull();
    // 别名只认领自己那条路径：/parse-intervention 归 ChunkWorkbenchPage，否则被 keep-alive
    // 保留下来的文档页会在隐藏状态下就地再渲染一个工作区、重复读同一份 ChunkHead。
    expect(
      parseDocumentWorkspaceLocation({ pathname: "/parse-intervention", search: "?doc=doc-1", hash: "" }),
    ).toBeNull();
    expect(
      parseDocumentWorkspaceLocation({
        pathname: "/",
        search: "",
        hash: "#/parse-intervention?doc=doc-1&chunk=c-2",
      }),
    ).toBeNull();
  });

  it("preserves direct versus hash deployment navigation", () => {
    expect(documentWorkspaceNavigationIntent(direct, "doc/1")).toEqual({ mode: "history", url: "/documents/parse?doc=doc%2F1" });
    expect(documentWorkspaceNavigationIntent(hashed, "doc/1")).toEqual({ mode: "hash", url: "/documents/parse?doc=doc%2F1" });
    expect(documentsReturnIntent(direct)).toEqual({ mode: "history", url: "/documents" });
    expect(documentsReturnIntent(hashed)).toEqual({ mode: "hash", url: "/documents" });
  });
});
