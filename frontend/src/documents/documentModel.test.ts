import { describe, expect, it } from "vitest";
import { formatDuration, isDocumentSelectable } from "./documentModel";

describe("document workbench projection", () => {
  it("keeps millisecond precision instead of displaying zero seconds", () => {
    expect(formatDuration(0.4)).toBe("<1ms");
    expect(formatDuration(1.1)).toBe("1.1ms");
    expect(formatDuration(238)).toBe("238ms");
    expect(formatDuration(1240)).toBe("1.24s");
    expect(formatDuration(63000)).toBe("1m 03s");
  });

  it("prevents selecting queued and running documents for destructive actions", () => {
    expect(isDocumentSelectable({ status: "completed" })).toBe(true);
    expect(isDocumentSelectable({ status: "error" })).toBe(true);
    expect(isDocumentSelectable({ status: "parsing" })).toBe(false);
    expect(isDocumentSelectable({ status: "waiting" })).toBe(false);
  });
});
