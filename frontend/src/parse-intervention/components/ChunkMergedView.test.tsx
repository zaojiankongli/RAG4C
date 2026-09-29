// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentChunkItem } from "../../types/rag";
import type { ParseFilters } from "../hooks/useParseIntervention";
import ChunkListPane from "./ChunkListPane";

afterEach(cleanup);

const FILTERS: ParseFilters = {
  lifecycle: "all",
  projection: "all",
  relation: "all",
  warningsOnly: false,
  showTombstones: true,
};

function head(id: string, overrides: Partial<DocumentChunkItem> = {}): DocumentChunkItem {
  return {
    chunk_id: id,
    doc_id: "doc-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    text: `正文 ${id}`,
    text_hash: "h",
    content_revision: 1,
    document_revision: 3,
    enabled: true,
    chunk_role: "flat",
    seq: Number(id.slice(-1)),
    context: "",
    char_count: 4,
    parent_relation: "none",
    metadata: {},
    ...overrides,
  };
}

function renderPane(overrides: Partial<Parameters<typeof ChunkListPane>[0]> = {}) {
  const props = {
    chunks: [head("c1"), head("c2", { enabled: false }), head("p9", { chunk_role: "parent" })],
    total: 250,
    selectedId: "c1",
    query: "",
    filters: FILTERS,
    knownParents: new Set<string>(),
    missingParents: new Set<string>(),
    hasMore: true,
    loadingMore: false,
    onQueryChange: vi.fn(),
    onFiltersChange: vi.fn(),
    onSelect: vi.fn(),
    onLoadMore: vi.fn(),
    ...overrides,
  };
  render(<ChunkListPane {...props} />);
  return props;
}

describe("切片列表与整篇合并视图的切换", () => {
  it("默认是切片列表，合并视图不提前渲染", () => {
    renderPane();
    expect(screen.getAllByRole("region", { name: "切片列表" })).toHaveLength(1);
    expect(screen.queryAllByRole("region", { name: "整篇合并视图" })).toEqual([]);
  });

  it("切到合并视图后如实说明只载入了部分，不把局部当整篇", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    expect(region.textContent).toContain("仅包含已载入的前 2 / 250");
    expect(region.textContent).toContain("下面的正文不是全文");
  });

  it("墓碑原位留占位并进正文之外，父块被排除且说明数量", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    expect(region.textContent).toContain("此处已被人工停用");
    expect(region.textContent).not.toContain("正文 p9");
    expect(region.textContent).toContain("2 段");
    expect(region.textContent).toContain("其中 1 处已停用");
  });

  it("每一段都能定位回对应切片，含墓碑那段", async () => {
    const user = userEvent.setup();
    const props = renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    await user.click(screen.getByRole("button", { name: "定位到已停用的切片 c2" }));
    expect(props.onSelect).toHaveBeenCalledWith("c2");
  });

  it("继续载入直接走既有的分页回调，不自建第二套取数", async () => {
    const user = userEvent.setup();
    const props = renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    await user.click(screen.getByRole("button", { name: "继续载入" }));
    expect(props.onLoadMore).toHaveBeenCalled();
  });

  it("全部载入后不再出现「仅包含」措辞", async () => {
    const user = userEvent.setup();
    renderPane({ chunks: [head("c1"), head("c2")], total: 2, hasMore: false });
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    expect(region.textContent).toContain("整篇 2 个切片已全部载入");
    expect(region.textContent).not.toContain("仅包含");
  });

  it("合并正文这块可滚动区域键盘可达", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const body = await waitFor(() =>
      screen.getByRole("region", { name: "合并正文，可用方向键滚动" }),
    );
    expect(body).toHaveProperty("tabIndex", 0);
    // 真按 Tab 走一遍：只在 DOM 上写 tabIndex 不算键盘可达
    for (let i = 0; i < 12; i += 1) {
      await user.tab();
      if (document.activeElement === body) break;
    }
    expect(document.activeElement).toBe(body);
  });

  it("当前编辑的那一段有非视觉的选中信号", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() =>
      screen.getByRole("region", { name: "整篇合并视图" }),
    );
    // 只有 is-selected 这个 CSS 类的话，读屏用户完全不知道"编辑的是哪一段"。
    const current = region.querySelectorAll('[aria-current="true"]');
    expect(current).toHaveLength(1);
    expect(current[0].textContent).toContain("正文 c1");
  });

  it("选中的是已停用那段时，占位行同样带选中信号", async () => {
    const user = userEvent.setup();
    renderPane({ selectedId: "c2" });
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() =>
      screen.getByRole("region", { name: "整篇合并视图" }),
    );
    const current = region.querySelectorAll('[aria-current="true"]');
    expect(current).toHaveLength(1);
    expect(current[0].textContent).toContain("此处已被人工停用");
  });

  it("处在服务端搜索结果里时，合并视图不拿命中数当全篇总数", async () => {
    const user = userEvent.setup();
    // 带搜索词时后端返回的 total 是"命中数"（count 也带 where 条件），
    // 若拿它跟已载条数比，2 条命中就会谎称 250 条的文档已经载入完整。
    renderPane({ query: "policy" });
    await user.click(screen.getByRole("button", { name: "整篇合并" }));
    const region = await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
    expect(region.textContent).not.toContain("已全部载入");
    expect(region.textContent).toContain("总数未知");
    expect(region.textContent).toContain("命中数而不是全篇切片数");
  });

  it("同一 chunkId 以正文和墓碑两种形态出现时不撞 React key（防御：模型层去重回归也不该让视图崩）", async () => {
    const warnSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      // buildMergedDocument 现按 seq 排序不去重；若未来去重被移除或来源混杂
      // （深链钉入的头部与列表重复载入同一条），chunkId 可能成对出现。
      // key 现含 kind 后缀，body/tombstone 同 id 也不再撞。
      renderPane({
        chunks: [
          head("c1"),
          head("c1", { enabled: false, text: "" }),
          head("c2"),
        ],
      });
      await userEvent.setup().click(screen.getByRole("button", { name: "整篇合并" }));
      const region = await waitFor(() => screen.getByRole("region", { name: "整篇合并视图" }));
      expect(region.textContent).toContain("正文 c1");
      expect(region.textContent).toContain("此处已被人工停用");
      expect(warnSpy).not.toHaveBeenCalledWith(expect.stringContaining("same key"), expect.anything(), expect.anything());
    } finally {
      warnSpy.mockRestore();
    }
  });
});
