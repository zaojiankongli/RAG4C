// Computed-style verification: does the ink-rail cascade actually win?
import { chromium } from "playwright-core";

const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto("http://localhost:1420/#/overview", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

const report = await page.evaluate(() => {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return { found: false };
    const cs = getComputedStyle(el);
    return {
      found: true,
      background: cs.backgroundColor,
      backgroundImage: cs.backgroundImage.slice(0, 90),
      color: cs.color,
      borderRadius: cs.borderRadius,
      boxShadow: cs.boxShadow.slice(0, 110),
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
    };
  };
  const activeItem = document.querySelector(".app-sider .t-menu__item.t-is-active");
  const hoverTest = activeItem ? { text: activeItem.textContent?.trim() } : null;
  return {
    sider: pick(".app-sider"),
    brandLogo: pick(".brand-logo"),
    brandTitle: pick(".brand-title"),
    menuGroupTitle: pick(".app-sider .t-menu-group__title"),
    activeMenuItem: pick(".app-sider .t-menu__item.t-is-active"),
    hoverTest,
    menuItems: [...document.querySelectorAll(".app-sider .t-menu__item")].length,
    connPill: pick(".conn-pill"),
    content: pick(".app-content"),
    topbarTitle: pick(".page-topbar-title"),
    card: pick(".app-content .t-card"),
    statValue: pick(".stat-card .stat-value"),
    table: pick(".app-content .t-table__header > tr > th"),
    tag: pick(".app-content .t-tag"),
    siderTextBtn: pick(".sider-actions .t-button"),
    htmlTheme: document.documentElement.dataset.theme,
  };
});
console.log(JSON.stringify(report, null, 1));

// hover state check
const item = await page.$(".app-sider .t-menu__item:not(.t-is-active)");
if (item) {
  await item.hover();
  await page.waitForTimeout(300);
  const hoverBg = await item.evaluate((el) => getComputedStyle(el).backgroundColor);
  const hoverColor = await item.evaluate((el) => getComputedStyle(el).color);
  console.log("HOVER:", { hoverBg, hoverColor });
}
await browser.close();
