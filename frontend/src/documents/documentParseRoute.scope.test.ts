import { describe, expect, it } from "vitest";
import { parseDocumentWorkspaceLocation } from "./documentParseRoute";
import { parseChunkWorkbenchLocation } from "../run/appRoute";

const at = (pathname: string, search = "", hash = "") => ({ pathname, search, hash });

/**
 * `App.tsx` 用 `.page-slot.is-hidden` 保留已访问页面——隐藏不等于卸载。两条入口各自渲染
 * 一份 `ParseInterventionWorkspace`，所以认领范围必须互斥：一条路径只能被一个入口认领，
 * 否则后台那份隐藏的装配会重复拉切片页、重复上报 dirty，并二次弹出离开确认。
 */
describe("解析干预深链的作用域划分", () => {
  it("规范入口只认领 /parse-intervention", () => {
    expect(parseChunkWorkbenchLocation(at("/parse-intervention", "?doc=d-1&chunk=c-2"))).toEqual({
      docId: "d-1",
      chunkId: "c-2",
      datasetId: "",
    });
    expect(parseChunkWorkbenchLocation(at("/documents/parse", "?doc=d-1"))).toBeNull();
  });

  it("老别名入口只认领 /documents/parse", () => {
    expect(parseDocumentWorkspaceLocation(at("/documents/parse", "?doc=d-1"))).toEqual({
      docId: "d-1",
      chunkId: "",
      datasetId: "",
    });
    expect(parseDocumentWorkspaceLocation(at("/parse-intervention", "?doc=d-1"))).toBeNull();
  });

  it("hash 部署下同样互斥，任何 location 至多被一个入口认领", () => {
    const locations = [
      at("/", "", "#/parse-intervention?doc=d-1&chunk=c-2"),
      at("/", "", "#/documents/parse?doc=d-1&chunk=c-2"),
      at("/documents", "", "#/parse-intervention?doc=d-1"),
      at("/parse-intervention?doc=d-1", "", "#/documents/parse?doc=d-1"),
    ];
    for (const location of locations) {
      const claimed = [
        parseChunkWorkbenchLocation(location) !== null,
        parseDocumentWorkspaceLocation(location) !== null,
      ];
      expect(claimed.filter(Boolean).length).toBeLessThanOrEqual(1);
    }
  });

  it("缺 doc 参数的两条路径都不成立", () => {
    expect(parseChunkWorkbenchLocation(at("/parse-intervention"))).toBeNull();
    expect(parseDocumentWorkspaceLocation(at("/documents/parse"))).toBeNull();
  });
});
