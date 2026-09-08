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
    expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "?doc=doc%2F1", hash: "" })).toEqual({ docId: "doc/1" });
    expect(parseDocumentWorkspaceLocation({ pathname: "/", search: "", hash: "#/documents/parse?doc=doc%2F1" })).toEqual({ docId: "doc/1" });
    expect(parseDocumentWorkspaceLocation({ pathname: "/documents/parse", search: "", hash: "" })).toBeNull();
    expect(parseDocumentWorkspaceLocation({ pathname: "/monitor", search: "?doc=doc-1", hash: "" })).toBeNull();
  });

  it("preserves direct versus hash deployment navigation", () => {
    expect(documentWorkspaceNavigationIntent(direct, "doc/1")).toEqual({ mode: "history", url: "/documents/parse?doc=doc%2F1" });
    expect(documentWorkspaceNavigationIntent(hashed, "doc/1")).toEqual({ mode: "hash", url: "/documents/parse?doc=doc%2F1" });
    expect(documentsReturnIntent(direct)).toEqual({ mode: "history", url: "/documents" });
    expect(documentsReturnIntent(hashed)).toEqual({ mode: "hash", url: "/documents" });
  });
});
