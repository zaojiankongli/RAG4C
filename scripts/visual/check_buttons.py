"""检查 eval 页按钮的实际 TDesign 类名（用于 anime.css 覆盖）。"""
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
    page.reload(wait_until="networkidle", timeout=30000)
    page.goto(BASE + "/#/eval", wait_until="networkidle", timeout=30000)
    # 收集按钮类名（去重，前 10 个不同类组合）
    classes = page.evaluate(
        """() => {
            const seen = new Set();
            const out = [];
            for (const btn of document.querySelectorAll('button')) {
                const c = btn.className;
                if (typeof c === 'string' && !seen.has(c)) {
                    seen.add(c);
                    out.push(c.slice(0, 120));
                }
                if (out.length >= 10) break;
            }
            return out;
        }"""
    )
    for c in classes:
        print("BTN:", c)
    # 检查第一个按钮的 background
    bg = page.evaluate(
        """() => {
            const btn = document.querySelector('button');
            return btn ? getComputedStyle(btn).backgroundImage.slice(0, 80) : 'none';
        }"""
    )
    print("first btn bg:", bg)
    browser.close()
