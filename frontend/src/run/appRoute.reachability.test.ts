// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { NAV_PAGE_KEYS } from "./appNav";
import { PAGE_KEYS, mainNavigationKey, navigationIntent, parsePageLocation } from "./appRoute";
import type { PageKey } from "./appRoute";

/**
 * 可达性守卫：一等页面不能只剩"知道 URL 才进得去"。
 *
 * 直接原因：`notifications` 这一页早就存在（有路由解析、有 pageNodes 分支、
 * `navigationIntent` 也知道它的那条 URL），但主导航里没有它，也没有任何控件
 * `setPage("notifications")` —— 操作员除了手敲 `#/enterprise/notifications` 之外到不了。
 * 页面清单与导航清单是两份东西，这一条把它们对起来。
 */
const SUB_PAGES: Partial<Record<PageKey, string>> = {
  // 详情页：从知识库列表里点开某一本进入，本来就不该在主导航里另立一项。
  "knowledge-base-workspace": "从知识库注册表点开单本进入",
};

const navKeys = new Set<string>(NAV_PAGE_KEYS);

describe("一级页面都要能从主导航到达", () => {
  it.each([...PAGE_KEYS])("%s", (key) => {
    expect(navKeys.has(key) || key in SUB_PAGES).toBe(true);
  });

  it("通知中心是这一条钉住的那个真实缺口", () => {
    expect(PAGE_KEYS).toContain("notifications");
    expect(NAV_PAGE_KEYS).toContain("notifications");
    // 进了主导航之后高亮就是它自己：折进"组织与权限"会让操作员以为自己在另一页。
    expect(mainNavigationKey("notifications")).toBe("notifications");
    expect(
      parsePageLocation({ pathname: "/enterprise/notifications", search: "", hash: "" }),
    ).toBe("notifications");
    // 点这一项要真的走到那条 URL（两种部署形态各走各的），不然只是多了个点不动的条目。
    expect(
      navigationIntent({ pathname: "/", search: "", hash: "" }, "notifications"),
    ).toEqual({ mode: "hash", url: "/enterprise/notifications" });
    expect(
      navigationIntent(
        { pathname: "/enterprise/notifications", search: "", hash: "" },
        "notifications",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/notifications" });
  });

  it("导航里不许出现路由不认识的名字", () => {
    for (const key of NAV_PAGE_KEYS) {
      expect(PAGE_KEYS).toContain(key);
    }
  });

  it("导航里不许出现点了不高亮自己的名字", () => {
    // 落点是侧栏 selectedKeys 那一次调用：菜单里有这一项，高亮就必须落在这一项上。
    for (const key of NAV_PAGE_KEYS) {
      expect(mainNavigationKey(key), `${key} 在导航里却把高亮折到别处`).toBe(key);
    }
  });

  it("例外必须是写清理由的详情页，不能是一等页面", () => {
    for (const [key, reason] of Object.entries(SUB_PAGES)) {
      expect(reason.length, `${key} 的例外要写清为什么`).toBeGreaterThan(6);
      expect(navKeys.has(key), `${key} 已经进了主导航，例外声明该删掉`).toBe(false);
    }
    // 20 个一等页面 = 19 个可从主导航到达 + 1 个有据可查的详情页
    expect(PAGE_KEYS.length).toBe(NAV_PAGE_KEYS.length + Object.keys(SUB_PAGES).length);
  });
});
