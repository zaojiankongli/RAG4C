// Vitest runs this route contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");
const routeSource = readFileSync(new URL("../AppRoutes.tsx", import.meta.url), "utf8");
// 主导航清单单独成一份模块（`run/appNav.tsx`），可达性守卫直接读它，不必把整个 App 拖进来。
const navSource = readFileSync(new URL("./appNav.tsx", import.meta.url), "utf8");

describe("解析干预 route registration", () => {
  it("lazy-loads the workbench page and puts the entry under 知识库", () => {
    expect(routeSource).toMatch(
      /const ChunkWorkbenchPage = lazy\(\(\) => import\("\.\/pages\/ChunkWorkbenchPage"\)\)/,
    );
    const knowledgeGroup = navSource.match(/label: "知识库",[\s\S]*?children: \[([\s\S]*?)\n\s*\],/);
    expect(knowledgeGroup?.[1]).toContain('key: "parse-intervention"');
    expect(knowledgeGroup?.[1]).toContain('label: "解析干预"');
  });
  it("registers one page node and keeps the dirty-draft guard on both entries", () => {
    // 单一装配：侧栏入口与深链共用同一个 pageNodes 键，不允许出现第二份工作区装配
    expect(routeSource.match(/"parse-intervention":\s*<ChunkWorkbenchPage/g)).toHaveLength(1);
    expect(routeSource).toMatch(/"parse-intervention":\s*<ChunkWorkbenchPage\s*onDirtyChange=\{onWorkbenchDirtyChange\}\s*\/>/);
    expect(appSource).toMatch(
      /\(page === "documents" && documentsWorkspaceDirty\)[\s\S]{0,80}\(page === "parse-intervention" && workbenchDirty\)/,
    );
  });
});
