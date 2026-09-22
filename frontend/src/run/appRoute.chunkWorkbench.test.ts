import { describe, expect, it } from "vitest";
import {
  PAGE_KEYS,
  chunkWorkbenchDeepLink,
  chunkWorkbenchNavigationIntent,
  navigationIntent,
  parseChunkWorkbenchLocation,
  parsePageLocation,
} from "./appRoute";
import { parseDocumentWorkspaceLocation } from "../documents/documentParseRoute";

const history = { pathname: "/parse-intervention", search: "?doc=doc%2F1&chunk=chunk%2F9", hash: "" };
const hashed = { pathname: "/", search: "", hash: "#/parse-intervention?doc=doc%2F1&chunk=chunk%2F9" };

describe("parse intervention workspace route", () => {
  it("registers the page key so keep-alive and the sidebar can address it", () => {
    expect(PAGE_KEYS).toContain("parse-intervention");
    expect(navigationIntent({ pathname: "/query", search: "", hash: "" }, "parse-intervention")).toEqual({
      mode: "history",
      url: "/parse-intervention",
    });
    expect(navigationIntent({ pathname: "/", search: "", hash: "#/query" }, "parse-intervention")).toEqual({
      mode: "hash",
      url: "/parse-intervention",
    });
  });
  it("parses doc and chunk in both history and #hash shapes", () => {
    expect(parsePageLocation(history)).toBe("parse-intervention");
    expect(parsePageLocation(hashed)).toBe("parse-intervention");
    expect(parseChunkWorkbenchLocation(history)).toEqual({
      docId: "doc/1",
      chunkId: "chunk/9",
      datasetId: "",
    });
    expect(parseChunkWorkbenchLocation(hashed)).toEqual({
      docId: "doc/1",
      chunkId: "chunk/9",
      datasetId: "",
    });
  });
  it("keeps the legacy /documents/parse alias working and migrates its params", () => {
    // 别名归文档页认领（两条入口互斥，见 documentParseRoute.scope.test.ts），但参数形状与新的一致
    expect(
      parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "?doc=doc-1", hash: "" }),
    ).toEqual({ docId: "doc-1", chunkId: "", datasetId: "" });
    expect(
      parseDocumentWorkspaceLocation({
        pathname: "/",
        search: "",
        hash: "#/documents/parse?doc=doc-1&chunk=c-2",
      }),
    ).toEqual({ docId: "doc-1", chunkId: "c-2", datasetId: "" });
    expect(parseChunkWorkbenchLocation({ pathname: "/documents/parse", search: "?doc=doc-1", hash: "" })).toBeNull();
    expect(parsePageLocation({ pathname: "/documents/parse", search: "?doc=doc-1", hash: "" })).toBe(
      "documents",
    );
  });
  it("refuses to build a workbench link without the document scope", () => {
    expect(parseChunkWorkbenchLocation({ pathname: "/parse-intervention", search: "", hash: "" })).toBeNull();
    expect(
      parseChunkWorkbenchLocation({ pathname: "/parse-intervention", search: "?chunk=c-1", hash: "" }),
    ).toBeNull();
    expect(parseChunkWorkbenchLocation({ pathname: "/documents", search: "?doc=doc-1", hash: "" })).toBeNull();
  });
  it("emits canonical intents and keeps forward / back resolution stable", () => {
    expect(chunkWorkbenchNavigationIntent(history, { docId: "doc/1", chunkId: "chunk/9" })).toEqual({
      mode: "history",
      url: "/parse-intervention?doc=doc%2F1&chunk=chunk%2F9",
    });
    expect(chunkWorkbenchNavigationIntent(hashed, { docId: "d", chunkId: "", datasetId: "kb" })).toEqual({
      mode: "hash",
      url: "/parse-intervention?doc=d&dataset=kb",
    });
    expect(chunkWorkbenchDeepLink("doc/1", "chunk/9", "kb")).toBe(
      "#/parse-intervention?doc=doc%2F1&chunk=chunk%2F9&dataset=kb",
    );
    // 前进后退：两种形态解析出的 PageKey 与参数都在同一个文档上稳定收敛
    for (const location of [history, hashed]) {
      expect(parsePageLocation(location)).toBe("parse-intervention");
      expect(parseChunkWorkbenchLocation(location)?.docId).toBe("doc/1");
    }
  });
});
