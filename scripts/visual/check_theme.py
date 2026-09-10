"""诊断：localStorage 设置后弹窗是否仍出现 + data-theme 是否生效。"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

BASE = "http://localhost:1420"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(BASE, wait_until="networkidle", timeout=30000)
    page.evaluate(
        "() => { localStorage.setItem('rag4c.theme_mode', 'anime'); "
        "localStorage.setItem('rag4c.onboarding_done', '1'); }"
    )
    print("after set - onboarding_done:", page.evaluate("() => localStorage.getItem('rag4c.onboarding_done')"))
    print("after set - dialog count:", page.locator('[role="dialog"]').count())
    page.goto(BASE + "/#/eval", wait_until="networkidle", timeout=30000)
    print("after nav - onboarding_done:", page.evaluate("() => localStorage.getItem('rag4c.onboarding_done')"))
    print("after nav - dialog count:", page.locator('[role="dialog"]').count())
    print("after nav - data-theme:", page.evaluate("() => document.documentElement.dataset.theme"))
    # 关键测试：reload 后 loadInitial 应读到 anime
    page.reload(wait_until="networkidle", timeout=30000)
    print("after reload - data-theme:", page.evaluate("() => document.documentElement.dataset.theme"))
    sider = page.evaluate(
        "() => { const el = document.querySelector('.app-sider'); "
        "return el ? getComputedStyle(el).backgroundImage.slice(0, 100) : 'NO-SIDER'; }"
    )
    print("sider bg (anime):", sider)
    # 诊断：哪些 cssRules 匹配 .app-sider 的 background
    rules = page.evaluate(
        """() => {
            const out = [];
            for (const sheet of document.styleSheets) {
                try {
                    for (const rule of sheet.cssRules) {
                        const sel = rule.selectorText || '';
                        if (sel.includes('app-sider') && (rule.style && rule.style.backgroundImage)) {
                            out.push(sel + ' -> ' + rule.style.backgroundImage.slice(0, 60));
                        }
                    }
                } catch (e) {}
            }
            return out.slice(0, 8);
        }"""
    )
    print("matching sider rules:", rules)
    menu = page.evaluate(
        "() => { const el = document.querySelector('.app-nav .t-menu__item'); "
        "return el ? getComputedStyle(el).color : 'NO-ITEM'; }"
    )
    print("menu color (anime):", menu)
    browser.close()
