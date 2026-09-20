/**
 * 给 6 个页面 × 若干视口 × 明暗两色批量截图，作为前端改动的前后对比依据。
 *
 * 用法：
 *   node scripts/shoot.mjs before          # 存到 output/shots/before/
 *   node scripts/shoot.mjs after           # 存到 output/shots/after/
 *   node scripts/shoot.mjs after --only monitor,query
 *
 * 前置：vite dev 已在 1420 端口跑着（npm run dev）。
 *
 * 为什么要有这个脚本而不是手动截：企业级观感的问题（间距不齐、窄屏溢出、
 * 暗色下的孤儿色值）几乎全是**跨页面对比**才看得出来的，一次一张手动截
 * 根本比不出来。而且改完之后必须能用同一组条件复现，否则"改好了"只是
 * 一句口头声明。
 *
 * 375 宽这一档是刻意留的：审计发现 .page-topbar 没有 flex-wrap 而
 * body{overflow:hidden}，窄屏下顶栏按钮会被直接裁掉且横向滚不到——
 * 那是唯一一处真的会坏掉的功能缺陷，必须有一档能拍到它。
 */
import { chromium } from "playwright-core";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const BASE = process.env.SHOOT_BASE || "http://localhost:1420";
const label = process.argv[2] || "shot";
const onlyArg = process.argv.indexOf("--only");
const only = onlyArg > -1 ? (process.argv[onlyArg + 1] || "").split(",").filter(Boolean) : null;

const PAGES = [
  { key: "query", hash: "#/query" },
  { key: "documents", hash: "#/documents" },
  { key: "visualize", hash: "#/visualize" },
  { key: "monitor", hash: "#/monitor" },
  { key: "eval", hash: "#/eval" },
  { key: "config", hash: "#/config" },
];

// 1440：主力桌面。1366×768：最常见的会议室笔记本，也是 .adv-config 固定
// 520px 高度会顶出去的那一档。375：手机窄屏，专拍顶栏溢出。
const VIEWPORTS = [
  { key: "1440", width: 1440, height: 900 },
  { key: "1366", width: 1366, height: 768 },
  { key: "375", width: 375, height: 812 },
];

const outRoot = path.resolve("output/shots", label);

/** 等页面"安静下来"：网络空闲 + 动画跑完，否则截到的是过渡中间态。 */
async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});

  // 等 Suspense 骨架消失再拍。
  //
  // networkidle 不够：路由是 lazy 的，vite dev 首次访问某个 chunk 要现编译，
  // 而编译发生在 networkidle 判定之后。实测 dark/1440/visualize 就拍到了一张
  // 「加载中…」——页面本身没问题，是快照拍早了。这类假象比漏拍更糟：它会让
  // 人以为深色模式下这个页面坏了，然后去查一个根本不存在的 bug。
  //
  // 判据用「加载态元素消失」而不是「某个内容元素出现」：后者每个页面都不一样，
  // 得维护一张选择器表，加一个页面就得记得改——迟早会忘。
  await page
    .waitForFunction(
      () => !document.querySelector(".ant-spin-spinning, .ant-skeleton-active"),
      null,
      { timeout: 20000 },
    )
    .catch(() => {});

  // ECharts 的入场动画约 1s；prefers-reduced-motion 只关 CSS 动画，关不掉它。
  await page.waitForTimeout(1200);
}

const browser = await chromium.launch();
let shots = 0;
const problems = [];

for (const theme of ["light", "dark"]) {
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
      colorScheme: theme,
      locale: "zh-CN",
    });
    // 应用只读 rag4c.theme_mode（useThemeMode.ts:4）；此前这里写的
    // rag4c.theme 是个没有任何消费者的死 key，于是拍到的主题归属并不可信。
    await ctx.addInitScript((t) => {
      window.localStorage.setItem("rag4c.theme_mode", t);
      // 关掉新手引导再拍。引导是一层 fixed inset-0 z-[9999] 的全屏遮罩，
      // 不置这个标记时每张截图拍到的都是引导弹窗而不是页面本身
      // （实测 36/36 张全部如此，见 docs/41）。
      window.localStorage.setItem("rag4c.onboarding_done", "1");
    }, theme);

    const page = await ctx.newPage();
    // 控制台报错要收集起来：改版最容易引入的回归就是某页在某个视口下直接白屏，
    // 而截图里白屏和"空状态"长得很像，只有 console 能分辨。
    page.on("pageerror", (e) => problems.push(`[${theme}/${vp.key}] pageerror: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() === "error") problems.push(`[${theme}/${vp.key}] console: ${m.text().slice(0, 200)}`);
    });

    for (const p of PAGES) {
      if (only && !only.includes(p.key)) continue;
      const dir = path.join(outRoot, theme, vp.key);
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await page.goto(`${BASE}/${p.hash}`, { waitUntil: "domcontentloaded" });
      await settle(page);
      await page.screenshot({ path: path.join(dir, `${p.key}.png`), fullPage: false });
      shots++;
      process.stdout.write(`  ${theme}/${vp.key}/${p.key}.png\n`);
    }
    await ctx.close();
  }
}

await browser.close();
console.log(`\n共 ${shots} 张 -> ${outRoot}`);
if (problems.length) {
  console.log(`\n页面报错 ${problems.length} 条：`);
  // 去重：同一个错误会在每个视口重复出现，全打出来会淹没真正的多样性
  for (const line of [...new Set(problems)]) console.log("  " + line);
}
