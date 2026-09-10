/**
 * 前端视觉质量度量：把"好不好看"拆成**可复现的数字**。
 *
 * 为什么要有这个脚本：上一轮改版只有 36 张 PNG 当证据，而 PNG 既不能进 CI、
 * 也没法比较"这轮比上轮好在哪"——"视觉质量显著提升"就退化成一句自我评价。
 * 这里改成先量化：跑一遍拿到每个页面 × 视口 × 主题的违规计数，改完再跑一遍比数字。
 *
 * 用法：
 *   node scripts/visual-quality.mjs baseline     # 存 output/visual-quality/baseline.json
 *   node scripts/visual-quality.mjs after
 *
 * 前置：vite dev 已在 1420 跑着（npm run dev）。
 */
import { chromium } from "playwright-core";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const BASE = process.env.VQ_BASE || "http://localhost:1420";
const label = process.argv[2] || "baseline";
const outDir = path.resolve("output/visual-quality");
mkdirSync(outDir, { recursive: true });

const ROUTES = [
  "/overview",
  "/query",
  "/documents",
  "/taxonomy",
  "/governance",
  "/sources",
  "/retrieval-lab",
  "/visualize",
  "/eval",
  "/monitor",
  "/consistency",
  "/enterprise",
  "/enterprise/notifications",
  "/enterprise/recycle-bin",
  "/enterprise/tasks",
  "/enterprise/automations",
  "/enterprise/knowledge-bases",
  "/enterprise/knowledge-base",
  "/config",
];

const VIEWPORTS = [
  { key: "1440", width: 1440, height: 900 },
  { key: "1366", width: 1366, height: 768 },
  { key: "375", width: 375, height: 812 },
];

/** 页面里采集的全部指标。全部在浏览器里算完再带出来，避免把大对象传回 Node。 */
const COLLECT = () => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const root = document.documentElement;

  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const ownText = (el) =>
    [...el.childNodes]
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent || "")
      .join("")
      .trim();

  // 视觉隐藏但给读屏器用的元素（skip-link / sr-only / visually-hidden）会污染
  // 对比度与触控目标统计：它们本来就不可见，量它们等于量一堆假缺陷。
  const isA11yHidden = (el) =>
    Boolean(el.closest("a.skip-link, .sr-only, [class*='visually-hidden'], .t-visually-hidden"));

  // 只要祖先里有横向滚动容器，后代超出视口就是**设计内的横向滚动**而不是溢出。
  // 第一版漏了这条，于是 /documents 的 375px 视口报出 overPx=893 —— 那其实是一个
  // 可横向滚动的 facet 列表，量它等于自欺。
  const inHorizontalScroller = (el) => {
    let node = el.parentElement;
    while (node && node !== document.body) {
      const cs = getComputedStyle(node);
      if (cs.overflowX === "auto" || cs.overflowX === "scroll") return true;
      node = node.parentElement;
    }
    return false;
  };

  const describe = (el) => {
    const id = el.id ? `#${el.id}` : "";
    const cls = (el.className && typeof el.className === "string"
      ? `.${el.className.trim().split(/\s+/).slice(0, 2).join(".")}`
      : "");
    return `${el.tagName.toLowerCase()}${id}${cls}`;
  };

  // ---- 1. 横向溢出 -------------------------------------------------------
  const docOverflow = Math.max(0, root.scrollWidth - vw);
  const overflowing = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    if (isA11yHidden(el)) continue;
    const cs = getComputedStyle(el);
    // 故意横向滚动的容器**及其后代**都不算溢出。
    if (cs.overflowX === "auto" || cs.overflowX === "scroll") continue;
    if (inHorizontalScroller(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 2 || r.left < -2) {
      overflowing.push({
        sel: describe(el),
        overPx: Math.round(Math.max(r.right - vw, -r.left)),
        text: ownText(el).slice(0, 30),
      });
    }
  }
  overflowing.sort((a, b) => b.overPx - a.overPx);

  // ---- 2. 被截断的文字 ---------------------------------------------------
  const clipped = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    if (isA11yHidden(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.overflow === "visible" || cs.textOverflow === "ellipsis") continue;
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
      const t = ownText(el);
      if (t) clipped.push({ sel: describe(el), text: t.slice(0, 40) });
    }
  }

  // ---- 3. 字号直方图 -----------------------------------------------------
  // 分两档：10–11px 是设计令牌里就有的 eyebrow/label 档（--font-size-xxs/xs），
  // 属于**有意**的层级；真正该报的是 <10px。第一版把 2535 个 10–11px 全算成缺陷，
  // 数量大到没法指导任何决策——指标一旦不可执行就等于没有。
  const sizeHist = {};
  let belowTokenFloor = 0;
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    if (isA11yHidden(el)) continue;
    const t = ownText(el);
    if (!t) continue;
    const size = Math.round(parseFloat(getComputedStyle(el).fontSize) * 10) / 10;
    sizeHist[size] = (sizeHist[size] || 0) + 1;
    if (size < 10) belowTokenFloor += 1;
  }

  // ---- 4. 对比度 ---------------------------------------------------------
  const parseColor = (value) => {
    const m = String(value).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    const [r, g, b] = parts;
    const a = parts.length > 3 ? parts[3] : 1;
    return { r, g, b, a };
  };
  const lum = ({ r, g, b }) => {
    const f = (c) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  // 解析元素后面真正"压着什么"。逐层向上：
  //  - 遇到**不透明**的背景色 → 就是它，停；
  //  - 遇到带 background-image 的层（渐变/图片）而这一层之前还没有确定背景 → 无法从
  //    计算样式还原真实像素，标记 unknown 让调用方跳过；
  //  - 一路到顶都没确定 → 用主题底色兜底。
  // 关键是**顺序**：早先的版本只要祖先里有任何一层带渐变就整条跳过，而应用外壳根节点
  // 就有一层渐变 → 7600 个元素全被跳过，对比度指标直接变成"永远 0 违规"的假绿。
  const resolveBackground = (el) => {
    let node = el;
    let acc = null;
    let sawTransparentLayer = false;
    while (node && node !== document.documentElement.parentNode) {
      const cs = getComputedStyle(node);
      const c = parseColor(cs.backgroundColor);
      if (c && c.a > 0) {
        acc = acc ? over(acc, c) : c;
        if (acc.a >= 0.999) return { bg: acc, unknown: false };
      } else if (cs.backgroundImage && cs.backgroundImage !== "none") {
        sawTransparentLayer = true;
        break;
      }
      node = node.parentElement;
    }
    const dark = document.documentElement.dataset.theme === "dark";
    const base = dark ? { r: 11, g: 13, b: 17, a: 1 } : { r: 255, g: 255, b: 255, a: 1 };
    if (sawTransparentLayer && !acc) return { bg: base, unknown: true };
    return { bg: acc ? over(acc, base) : base, unknown: sawTransparentLayer };
  };
  const contrast = (a, b) => {
    const l1 = lum(a);
    const l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const contrastViolations = [];
  let contrastSkippedGradient = 0;
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    if (isA11yHidden(el)) continue;
    const t = ownText(el);
    if (!t) continue;
    const cs = getComputedStyle(el);
    // 禁用态与纯装饰（无文本）不参与；图标字体也可能被算进来，跳过 aria-hidden。
    if (el.closest('[aria-hidden="true"]')) continue;
    if (el.closest("[disabled], .t-is-disabled, .rag-button.is-disabled")) continue;
    if (cs.webkitTextFillColor === "rgba(0, 0, 0, 0)") continue;
    const fg = parseColor(cs.color);
    if (!fg) continue;
    // 全透明前景色（有些组件靠 background-clip 呈现）无法判对比度，跳过。
    if (fg.a < 0.5) continue;
    // 祖先带渐变/图片背景且没有更近的不透明底色时，无法还原真实像素 → 如实跳过并计数。
    const resolved = resolveBackground(el);
    if (resolved.unknown) {
      contrastSkippedGradient += 1;
      continue;
    }
    const size = parseFloat(cs.fontSize);
    const bold = Number(cs.fontWeight) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const ratio = contrast(over(fg, resolved.bg), resolved.bg);
    if (ratio < need) {
      contrastViolations.push({
        sel: describe(el),
        text: t.slice(0, 30),
        ratio: Math.round(ratio * 100) / 100,
        need,
        fontSize: size,
      });
    }
  }
  contrastViolations.sort((a, b) => a.ratio - b.ratio);

  // ---- 5. 触控目标尺寸（仅窄屏） ----------------------------------------
  const tinyTargets = [];
  if (vw <= 480) {
    for (const el of document.querySelectorAll(
      'button, a[href], select, [role="button"], [role="tab"], [role="switch"], [role="checkbox"]',
    )) {
      if (!visible(el)) continue;
      if (isA11yHidden(el)) continue;
      // 被 <label> 包裹的表单控件点击区域是整行，单独量控件本身会误报。
      if (el.tagName === "INPUT" && el.closest("label")) continue;
      const r = el.getBoundingClientRect();
      // 行内文字链接不是触控目标（它们按行排版），只量块级可交互元素。
      if (getComputedStyle(el.parentElement || el).display.includes("inline") && el.tagName === "A")
        continue;
      if (r.width < 44 || r.height < 44) {
        tinyTargets.push({
          sel: describe(el),
          w: Math.round(r.width),
          h: Math.round(r.height),
          label: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24),
        });
      }
    }
    tinyTargets.sort((a, b) => a.w * a.h - b.w * b.h);
  }

  // ---- 6. 主内容区的留白 -------------------------------------------------
  const main =
    document.querySelector("main") ||
    document.querySelector(".app-content") ||
    document.body;
  let trailingGapPx = 0;
  let largestGapPx = 0;
  const mainRect = main.getBoundingClientRect();
  const leaves = [...main.querySelectorAll("*")].filter(
    (el) => visible(el) && ownText(el),
  );
  if (leaves.length) {
    const bottom = Math.max(...leaves.map((el) => el.getBoundingClientRect().bottom));
    trailingGapPx = Math.max(0, Math.round(mainRect.bottom - bottom));
  }
  const kids = [...main.children].filter(visible);
  for (let i = 1; i < kids.length; i += 1) {
    const prev = kids[i - 1].getBoundingClientRect();
    const cur = kids[i].getBoundingClientRect();
    largestGapPx = Math.max(largestGapPx, Math.round(cur.top - prev.bottom));
  }

  // ---- 7. 中英混排（把实现概念直接摆给用户看） --------------------------
  const mixedLanguage = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    if (el.closest("code, pre, .rag-inline-code, .t-typography code")) continue;
    const cs = getComputedStyle(el);
    if (cs.fontFamily.includes("mono")) continue;
    const t = ownText(el);
    if (!t || t.length > 40) continue;
    if (!/[\u4e00-\u9fff]/.test(t)) continue;
    // 只挑"至少 3 个连续拉丁字母"的词——像 "Workspace"/"Authority"/"Revision" 这类
    // 未翻译的实现概念；单字母/缩写（ID、R2）不算。
    const words = t.match(/[A-Za-z][A-Za-z-]{2,}/g) || [];
    if (words.length) mixedLanguage.push({ sel: describe(el), text: t, words });
  }

  return {
    viewport: { w: vw, h: vh },
    docOverflow,
    overflowing: overflowing.slice(0, 8),
    overflowingCount: overflowing.length,
    clipped,
    clippedCount: clipped.length,
    sizeHist,
    distinctFontSizes: Object.keys(sizeHist).length,
    belowTokenFloor,
    contrastViolations: contrastViolations.slice(0, 8),
    contrastViolationCount: contrastViolations.length,
    contrastSkippedGradient,
    tinyTargets: tinyTargets.slice(0, 8),
    tinyTargetCount: tinyTargets.length,
    trailingGapPx,
    largestGapPx,
    mixedLanguage: mixedLanguage.slice(0, 10),
    mixedLanguageCount: mixedLanguage.length,
    contentHeight: Math.round(mainRect.height),
    pageHeight: Math.round(root.scrollHeight),
    viewportFill: Math.round(((mainRect.bottom - mainRect.top) / vh) * 100),
  };
};

const consoleErrors = [];
const browser = await chromium.launch();
const results = [];

for (const theme of ["light", "dark"]) {
  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      colorScheme: theme,
      locale: "zh-CN",
    });
    await ctx.addInitScript((t) => {
      window.localStorage.setItem("rag4c.theme", t);
      window.localStorage.setItem("rag4c.theme_mode", t);
      // 关键：新手引导是一层 `fixed inset-0 z-[9999]` 的全屏遮罩。不置这个标记，
      // 每个页面拍到的都是引导弹窗，"视觉质量"量出来的其实是弹窗的质量。
      // 第一次跑就在 114 个页面里全部命中（`button.px-5.py-2` 引导"下一步"
      // 出现在每一页的对比度与触控目标清单里）——这就是证据。
      window.localStorage.setItem("rag4c.onboarding_done", "1");
    }, theme);
    const page = await ctx.newPage();
    page.on("pageerror", (e) =>
      consoleErrors.push({ theme, vp: vp.key, kind: "pageerror", text: e.message.slice(0, 200) }),
    );
    page.on("console", (m) => {
      if (m.type() === "error")
        consoleErrors.push({ theme, vp: vp.key, kind: "console", text: m.text().slice(0, 200) });
    });

    for (const route of ROUTES) {
      await page.goto(`${BASE}/#${route}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
      await page
        .waitForFunction(
          () => !document.querySelector(".ant-spin-spinning, .ant-skeleton-active, .t-loading"),
          null,
          { timeout: 15000 },
        )
        .catch(() => {});
      await page.waitForTimeout(700);
      const m = await page.evaluate(COLLECT);
      results.push({ theme, vp: vp.key, route, ...m });
      process.stdout.write(`  ${theme}/${vp.key}${route}\n`);
    }
    await ctx.close();
  }
}
await browser.close();

// ---- 汇总 ---------------------------------------------------------------
const sum = (key) => results.reduce((n, r) => n + (r[key] || 0), 0);
const worst = (key, n = 12) =>
  [...results]
    .filter((r) => (r[key] || 0) > 0)
    .sort((a, b) => (b[key] || 0) - (a[key] || 0))
    .slice(0, n)
    .map((r) => ({ theme: r.theme, vp: r.vp, route: r.route, n: r[key] }));

const totals = {
  label,
  capturedAt: new Date().toISOString(),
  pages: results.length,
  docOverflowPages: results.filter((r) => r.docOverflow > 2).length,
  overflowingElements: sum("overflowingCount"),
  clippedElements: sum("clippedCount"),
  belowTokenFloorElements: sum("belowTokenFloor"),
  contrastViolations: sum("contrastViolationCount"),
  contrastSkippedGradient: sum("contrastSkippedGradient"),
  tinyTargets: sum("tinyTargetCount"),
  mixedLanguageLabels: sum("mixedLanguageCount"),
  consoleErrors: consoleErrors.length,
};

const report = {
  totals,
  worst: {
    docOverflow: worst("docOverflow"),
    overflowing: worst("overflowingCount"),
    clipped: worst("clippedCount"),
    contrast: worst("contrastViolationCount"),
    tinyTargets: worst("tinyTargetCount"),
    mixedLanguage: worst("mixedLanguageCount"),
    trailingGap: [...results]
      .sort((a, b) => b.trailingGapPx - a.trailingGapPx)
      .slice(0, 10)
      .map((r) => ({
        theme: r.theme,
        vp: r.vp,
        route: r.route,
        trailingGapPx: r.trailingGapPx,
        largestGapPx: r.largestGapPx,
      })),
  },
  samples: {
    contrast: results
      .flatMap((r) => r.contrastViolations.map((v) => ({ route: r.route, theme: r.theme, ...v })))
      .sort((a, b) => a.ratio - b.ratio)
      .slice(0, 25),
    mixedLanguage: results
      .flatMap((r) => r.mixedLanguage.map((v) => ({ route: r.route, ...v })))
      .slice(0, 40),
    clipped: results
      .flatMap((r) => r.clipped.map((v) => ({ route: r.route, theme: r.theme, vp: r.vp, ...v })))
      .slice(0, 30),
    tinyTargets: results
      .flatMap((r) => r.tinyTargets.map((v) => ({ route: r.route, ...v })))
      .slice(0, 25),
  },
  consoleErrors,
  results,
};

const file = path.join(outDir, `${label}.json`);
writeFileSync(file, JSON.stringify(report, null, 1), "utf8");

const line = (k, v) => `${String(k).padEnd(24)} ${v}`;
console.log(`\n===== 视觉质量度量 · ${label} =====`);
console.log(line("页面加载数", totals.pages));
console.log(line("横向溢出页面数", totals.docOverflowPages));
console.log(line("溢出元素总数", totals.overflowingElements));
console.log(line("被截断元素总数", totals.clippedElements));
console.log(line("小于 10px 的文字", totals.belowTokenFloorElements));
console.log(line("对比度违规总数", totals.contrastViolations));
console.log(line("对比度跳过(渐变底)", totals.contrastSkippedGradient));
console.log(line("触控目标 <44px", totals.tinyTargets));
console.log(line("中英混排标签", totals.mixedLanguageLabels));
console.log(line("控制台错误", totals.consoleErrors));
console.log(`\nJSON -> ${file}`);
