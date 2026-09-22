// @vitest-environment node
// 源级契约守卫：本仓库用这类断言锁住"接口已存在但界面还在自认缺口"的回归
// （同一手法见 run/App.consistency.route.test.ts）。
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const editor = read("./components/ChunkEditorPane.tsx");
const list = read("./components/ChunkListPane.tsx");
const api = read("./api/parseInterventionApi.ts");
const hook = read("./hooks/useParseIntervention.ts");

const OBSOLETE = ["当前 API 不持久化修改原因", "当前没有切片 Revision 历史接口"];

describe("chunk lifecycle capability guards", () => {
  for (const text of OBSOLETE) {
    it(`no longer advertises the closed gap 「${text}」`, () => {
      expect(editor).not.toContain(text);
      expect(`${editor}${list}${api}${hook}`).not.toContain(text);
    });
  }
  it("drives enable / disable / revisions / revert through the real endpoints", () => {
    expect(api).toContain("setParseChunkEnabled");
    expect(api).toContain("fetchParseChunkRevisions");
    expect(api).toContain("revertParseChunk");
    expect(api).toMatch(/JSON.stringify\(\{ text, expected_revision: expectedRevision, reason \}\)/);
    expect(hook).toContain("setEnabled");
    expect(hook).toContain("revertTo");
    expect(editor).toContain("state.setEnabled");
    expect(editor).toContain("state.revertTo");
  });
  it("keeps the tombstone exit reachable and reuses the single diff builder", () => {
    expect(editor).toContain("启用此切片");
    expect(editor).toContain("停用此切片");
    expect(editor).toContain("compareChunkToSource");
    expect(editor).toContain("buildDiff");
    expect(editor).not.toContain("function buildDiff");
  });
  it("hides tombstones behind an explicit text filter instead of colour alone", () => {
    expect(list).toContain("showTombstones");
    expect(list).toContain("显示墓碑");
  });
  // TDesign 的 Button 在 disabled 时渲染成没有 role 的 <div>，aria-label 与文本都会随之失效，
  // 因此本仓库的惯例是显式 tag="button"（同一手法见工作区的「提交修改」）。
  const buttonSource = (text: string, label: string) => {
    const head = text.split(`>${label}</Button>`)[0] ?? "";
    return head.slice(head.lastIndexOf("<Button"));
  };
  it("keeps a real button role for every control that can go disabled", () => {
    for (const label of ["重置草稿", "删除切片", "回滚到此版", "读取原始内容"])
      expect(buttonSource(editor, label), label).toContain('tag="button"');
    const lifecycle = buttonSource(editor, "{toggleLabel}");
    expect(lifecycle).toContain('tag="button"');
    expect(lifecycle).toContain("icon={<PoweroffIcon/>}");
    // 文案、可访问名与写入值同源，避免按钮写着「停用」却把 enabled 又设回 true
    expect(editor).toContain('const toggleLabel=nextEnabled?"启用此切片":"停用此切片"');
    expect(editor).toContain("state.setEnabled(nextEnabled)");
  });
});
