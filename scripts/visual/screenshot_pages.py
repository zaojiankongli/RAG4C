"""用 Playwright 遍历 RAG4C 所有页面截图（供 GLM 视觉评估美观度）。

用法：python scripts/visual/screenshot_pages.py [--out output/visual]
依赖：playwright（chromium）+ 前端 dev 服务器（localhost:1420）。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# 页面路由（hash 模式） + 截图文件名 + 等待渲染的额外提示（秒）
PAGES = [
    ("query", "01-query.png", 3.0),
    ("overview", "02-overview.png", 3.0),
    ("documents", "03-documents.png", 3.0),
    ("taxonomy", "04-taxonomy.png", 3.0),
    ("sources", "05-sources.png", 3.0),
    ("retrieval-lab", "06-retrieval-lab.png", 3.0),
    ("visualize", "07-visualize.png", 3.0),
    ("eval", "08-eval.png", 3.0),
    ("monitor", "09-monitor.png", 3.0),
    ("consistency", "10-consistency.png", 3.0),
    ("enterprise", "11-enterprise.png", 3.0),
    ("recycle-bin", "12-recycle-bin.png", 3.0),
    ("tasks", "13-tasks.png", 3.0),
    ("automations", "14-automations.png", 3.0),
    ("knowledge-bases", "15-knowledge-bases.png", 3.0),
    ("config", "16-config.png", 3.0),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:1420")
    parser.add_argument("--out", default="output/visual")
    parser.add_argument("--viewport", default="1440,900")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = (int(x) for x in args.viewport.split(","))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        # 首次访问首页：若引导弹窗自动弹出则点「跳过」关闭（同时写入 localStorage 标记）
        page.goto(args.base, wait_until="networkidle", timeout=30000)
        try:
            skip = page.get_by_role("button", name="跳过")
            if skip.is_visible(timeout=2000):
                skip.click()
                time.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass
        page.evaluate(
            """() => {
                localStorage.setItem('rag4c.theme_mode', 'anime');
                localStorage.setItem('rag4c.onboarding_done', '1');
            }"""
        )
        # 重新加载：让 App 挂载时读取 anime 主题（applyThemeMode 只在启动时同步；
        # 注意：hash 导航不重载页面，必须 reload 才能触发 loadInitial 读 localStorage）
        page.reload(wait_until="networkidle", timeout=30000)
        time.sleep(1.0)
        results = []
        for route, filename, wait_s in PAGES:
            url = f"{args.base}/#/{route}"
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(wait_s)  # 等懒加载 + 图表渲染
                shot = out_dir / filename
                page.screenshot(path=str(shot), full_page=False)
                results.append((route, "OK", str(shot)))
            except Exception as exc:  # noqa: BLE001
                results.append((route, "FAIL", str(exc)[:100]))
        browser.close()

    print("\n=== 截图结果 ===")
    for route, status, detail in results:
        print(f"  {status} {route}: {detail}")
    return 0 if all(s == "OK" for _, s, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
