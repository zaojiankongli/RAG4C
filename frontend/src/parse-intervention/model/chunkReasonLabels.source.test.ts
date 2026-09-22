// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CHUNK_REASON_CODE_LABELS } from "./chunkDiagnostics";

/**
 * 后端 `indexing/chunking_router.py` 是原因码的产地，这张表是它们在侧栏上的名字。
 * 两边隔了一门语言，所以"加了第 5 个判定却忘了配中文名"不会有任何一侧报错 ——
 * 表现是列表分面里冒出一个裸的 `snake_case`，操作员读到的是内部标识。
 * 与 `appNav.source.test.ts` 同型：正则读真文件，两份手抄表对账。
 */
const ROUTER = readFileSync(
  new URL("../../../../indexing/chunking_router.py", import.meta.url),
  "utf8",
);

const REASON_CODES = new Set(
  [...ROUTER.matchAll(/reason_code["']?\s*[:=]\s*["']([a-z_]+)["']/g)].map((m) => m[1]),
);

describe("切分判定原因码 ↔ 界面名", () => {
  it("读到了后端那张产地清单，而不是空集自己放过自己", () => {
    expect(REASON_CODES.size).toBeGreaterThanOrEqual(4);
    expect(REASON_CODES.has("complex_or_structured")).toBe(true);
  });

  it("每个后端会写下的判定码都有一个中文界面名", () => {
    const unlabelled = [...REASON_CODES].filter((code) => !(code in CHUNK_REASON_CODE_LABELS));
    expect(unlabelled, `这些判定码会在分面里以裸原因码出现: ${unlabelled.join(", ")}`).toEqual([]);
  });

  it("界面名表里没有后端已经不再产出的死名字", () => {
    const stale = Object.keys(CHUNK_REASON_CODE_LABELS).filter(
      (code) => !REASON_CODES.has(code) && code !== "unknown",
    );
    expect(stale, `这些判定码后端已经不发，界面名该跟着删: ${stale.join(", ")}`).toEqual([]);
  });
});
