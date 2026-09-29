// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync, readdirSync, statSync } from "node:fs";
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { fileURLToPath } from "node:url";
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const STYLES = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

const LIGHT = ":root";
const DARK = 'html[data-theme="dark"]';
const ANIME = 'html[data-theme="anime"]';

/**
 * 焦点可见性 token 必须每个主题各自给值。anime 此前一个都没覆写，焦点环与光晕
 * 直接回落成 :root 的蓝色 rgba(49,100,244) —— 粉主题长出一圈蓝焦点环。
 */
const REQUIRED_EVERY_THEME = [
  "color-focus-ring",
  "focus-ring",
  "shadow-glow",
  "color-border-control",
];

/** 浅色与深色主题必须各自完整定义这批语义 token（深色不能靠浅色回落）。 */
const REQUIRED_IN_LIGHT_AND_DARK = [
  "color-primary",
  "color-primary-strong",
  "color-text",
  "color-text-secondary",
  "color-text-tertiary",
  "color-eyebrow",
  "color-eyebrow-warning",
  "color-on-primary",
  "color-bg",
  "color-bg-elevated",
  "color-border-guide",
  "color-success",
  "color-warning",
  "color-danger",
  ...REQUIRED_EVERY_THEME,
];

/** 尺寸类 token 只在 :root 定义一次即可（与主题配色无关）。 */
const REQUIRED_IN_ROOT_ONLY = ["control-min-h", "control-min-h-compact"];

/** anime 是独立的海蓝皮肤；以下是身份色与焦点色的最低契约，正文和表面也显式定义。 */
const REQUIRED_IN_ANIME = [
  "color-eyebrow",
  "color-eyebrow-warning",
  "color-border-guide",
  // --color-primary* 整族：这 8 个曾经一个都不在 anime 块里，303 个消费点全在拿 :root 的蓝，
  // 于是"粉主题里链接是蓝的、选中态描边是蓝的、光晕却是粉的"没人能及时发现。
  // 把它们钉成契约，任何一次误删覆写都会立刻红，而不是等到人眼发现串味。
  // 定值判据（同角色 = 同可见度档）记在 styles.css 的 anime 块注释与 docs/42 findings §19。
  "color-primary",
  "color-primary-hover",
  "color-primary-strong",
  "color-primary-bg",
  "color-primary-bg-strong",
  "color-primary-border",
  "color-primary-light",
  "color-primary-soft",
  ...REQUIRED_EVERY_THEME,
];

/**
 * 模块 CSS 里「值就是裸 hex」的自定义属性声明总数基线（实测 2026-09-19）。
 * 这是棘轮不是目标：只锁不许变差，等主题注册表单一真源落地后逐模块收下去。
 */
const MODULE_HEX_RATCHET = 77;

function blockFor(css: string, selector: string): string {
  const text = stripComments(css);
  const start = text.indexOf(selector + " {");
  if (start < 0) throw new Error("missing theme block: " + selector);
  const bodyStart = text.indexOf("{", start) + 1;
  let depth = 1;
  let i = bodyStart;
  while (i < text.length && depth > 0) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") depth -= 1;
    i += 1;
  }
  return text.slice(bodyStart, i - 1);
}

function declaredTokens(body: string): Set<string> {
  return new Set([...body.matchAll(/--([a-z0-9-]+):/g)].map((m) => m[1]));
}

/**
 * 剥掉 CSS 注释。注释里可以合法出现 `{`/`}`，会把"按花括号配对找规则体"的匹配器
 * 带偏——R10 的 `.rag-input` 假红就是这么来的（注释内示例 `select { … }` 的 `}`
 * 提前截断了 `[^}]*`）。凡做结构匹配先过这一层。
 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function cssFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) cssFiles(full, acc);
    else if (name.endsWith(".css")) acc.push(full);
  }
  return acc;
}

/** token 也可能只被 TS/TSX 里的 `var(--x)` 字符串消费，判死之前要一并扫。 */
function tsFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) tsFiles(full, acc);
    else if (name.endsWith(".ts") || name.endsWith(".tsx")) acc.push(full);
  }
  return acc;
}

describe("theme token blocks", () => {
  it.each([
    ["light", LIGHT, REQUIRED_IN_LIGHT_AND_DARK],
    ["dark", DARK, REQUIRED_IN_LIGHT_AND_DARK],
    ["anime", ANIME, REQUIRED_IN_ANIME],
  ])("defines every required semantic token in the %s block", (_theme, selector, required) => {
    const declared = declaredTokens(blockFor(STYLES, selector));
    const missing = required.filter((token) => !declared.has(token));
    expect(missing, `${selector} 缺少: ${missing.join(", ")}`).toEqual([]);
  });

  it("keeps sizing tokens at exactly one root value plus one narrow-screen override", () => {
    // :root 定基础值 + @media(max-width:480px) 抬到 44px 是有意设计；
    // 第三处出现意味着有人开始用特异性对打，那正是本轮踩过的坑。
    for (const token of REQUIRED_IN_ROOT_ONLY) {
      const occurrences = [...STYLES.matchAll(new RegExp(`--${token}:`, "g"))];
      expect(occurrences.length, `${token} 声明了 ${occurrences.length} 次`).toBe(2);
    }
  });

  it("does not let a second root block fight the first over semantic color tokens", () => {
    // styles.css 历史上把 :root 写过两次（第二处重定义 --td-* 品牌阶）。
    // 允许第二个 :root 存在，但不允许它在两处重复声明同一个 --color-* 语义色。
    const topLevel = [...STYLES.matchAll(/(^|\n):root \{([^}]*)\}/g)].map((m) => m[2]);
    expect(topLevel.length, "top-level :root blocks").toBeGreaterThan(1);
    const seen = new Map<string, number>();
    for (const body of topLevel) {
      for (const token of declaredTokens(body)) {
        if (!token.startsWith("color-")) continue;
        seen.set(token, (seen.get(token) ?? 0) + 1);
      }
    }
    const dupes = [...seen].filter(([, n]) => n > 1).map(([k]) => k);
    expect(dupes, `多个 :root 重复声明: ${dupes.join(", ")}`).toEqual([]);
  });

  it("keeps brand-tinted label colors off the raw TDesign brand token", () => {
    // 模块私有 --*-primary 直接取 --td-brand-color，会让 10px 文字在深色下掉到 3.4:1
    const offenders: string[] = [];
    for (const file of cssFiles(join(fileURLToPath(new URL("..", import.meta.url))))) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/--([a-z0-9-]*-primary):\s*var\(--td-brand-color/g)) {
        offenders.push(`${file}: --${match[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("pins TDesign control boundaries to the control border token in every theme bridge", () => {
    // TDesign 的 .t-input / .t-input-number__decrease|increase 取的是
    // --td-border-level-2-color，**不是** --td-component-border。只覆写后者是
    // 一次静默空转：R9 就是这么把部件对比度停在 396 条不动的。
    const bridges: Array<[string, string]> = [
      ...[...stripComments(STYLES).matchAll(/(^|\n):root \{([^}]*)\}/g)].map(
        (m) => [":root", m[2]] as [string, string],
      ),
      [DARK, blockFor(STYLES, DARK)],
    ];
    for (const [name, body] of bridges) {
      if (!/--td-component-border:/.test(body)) continue;
      expect(
        /--td-border-level-2-color:\s*var\(--color-border-control\)/.test(body),
        `${name} 覆写了 --td-component-border 却没把 --td-border-level-2-color 指到 control token`,
      ).toBe(true);
    }
  });

  it("gives self-drawn form controls the same boundary token as TDesign ones", () => {
    // .rag-select 的源规则若在文件后段留一条 `select { border-color: … }` 兜底，
    // 兜底特异性 (0,0,1) 会输给 (0,1,0) —— 必须改源规则本身。
    const rule = /\.rag-input,\s*\n\.rag-select,\s*\n\.rag-textarea \{[^}]*\}/.exec(
      stripComments(STYLES),
    );
    expect(rule, "找不到 .rag-input/.rag-select/.rag-textarea 源规则").not.toBeNull();
    expect(rule![0]).toMatch(/border:\s*1px solid var\(--color-border-control\)/);
  });

  it("keeps module border aliases off the TDesign control variable and off bare hex", () => {
    // R10 把 --td-border-level-2-color 桥到控件描边 token 后，所有借它取值的模块
    // 装饰线（树形连接线、分隔线、生命周期导轨、虚线占位框）跟着变成 #6b7280 深灰；
    // 写死 hex 的那批则只有 light+dark 两份值，anime 主题拿不到覆写，粉画布上长蓝灰线。
    // 该变量只在 styles.css 的主题桥里出现一次是契约本身，模块 CSS 一律不许直接读它。
    const riders: string[] = [];
    const hardcoded: string[] = [];
    for (const file of cssFiles(join(fileURLToPath(new URL("..", import.meta.url))))) {
      const name = file.split(/[\\/]/).pop()!;
      const text = readFileSync(file, "utf8");
      if (name !== "styles.css" && text.includes("--td-border-level-2-color")) riders.push(name);
      for (const m of text.matchAll(/(--[a-z0-9-]*-border[a-z0-9-]*)\s*:\s*([^;]+);/g)) {
        if (name !== "styles.css" && /^#[0-9a-fA-F]{3,8}$/.test(m[2].trim())) {
          hardcoded.push(`${name}: ${m[1]}: ${m[2].trim()}`);
        }
      }
    }
    expect(riders, `模块 CSS 直接读控件描边桥接变量: ${riders.join(", ")}`).toEqual([]);
    expect(hardcoded, `描边别名写死 hex: ${hardcoded.join(", ")}`).toEqual([]);
  });

  it("does not let module-private palettes grow past the recorded bypass baseline", () => {
    // 实测：13 个模块 CSS 各自带 [data-theme="dark"] 私有色板，而 anime 覆写在模块层是 0 处
    // （全局只有 styles.css/theme 里 6 处）。于是第三主题下模块私有值一律回落浅色——
    // R7 的蓝焦点环、R10 的蓝灰导轨都出在这个结构里。
    // 彻底解法是主题注册表单一真源（把 dark/anime 色板收进 tokens.ts）；在那之前
    // 先锁"旁路面不许再变大"，与视觉门禁的 componentContrast 棘轮同一套路。
    const srcDir = join(fileURLToPath(new URL("..", import.meta.url)));
    const perFile: string[] = [];
    let total = 0;
    for (const file of cssFiles(srcDir)) {
      // `file.slice(srcDir.length)` 不保证带前导分隔符，写死 `"/styles.css"` 的比较
      // 在 Windows 上永不成立，于是全局 styles.css 的 203 处裸 hex 被当成"模块私有色板"
      // 计入棘轮（R10 假红：280 > 77）。先归一化再去掉前导 `/`。
      const rel = file
        .slice(srcDir.length)
        .replace(/\\/g, "/")
        .replace(/^\//, "");
      if (rel === "styles.css" || rel.startsWith("theme/")) continue;
      const n = [
        ...readFileSync(file, "utf8").matchAll(/--[a-z0-9][a-z0-9-]*:\s*#[0-9a-fA-F]{3,8}\s*;/g),
      ].length;
      if (n) perFile.push(`${rel}=${n}`);
      total += n;
    }
    expect(
      total,
      `模块私有色板裸 hex 声明 ${total} 处 > 基线 ${MODULE_HEX_RATCHET}：${perFile.join(", ")}`,
    ).toBeLessThanOrEqual(MODULE_HEX_RATCHET);
  });

  it("does not keep unreferenced custom properties alive in theme/*.css", () => {
    // anime.css 曾有一整块 32 个 --color-anko-* 的 Tailwind @theme 色板：全仓零引用，
    // 却照样被注入 :root，而里面的 #ff7fa5 / #f06292 正是压白底只有 2.4~3.1:1 的那批粉。
    // 死 token 的代价不是字节数，是"看起来像可用设计系统"的诱饵。
    const themeDir = join(fileURLToPath(new URL(".", import.meta.url)));
    const sources: string[] = [];
    for (const file of cssFiles(join(themeDir, ".."))) sources.push(readFileSync(file, "utf8"));
    for (const file of tsFiles(join(themeDir, ".."))) sources.push(readFileSync(file, "utf8"));
    const corpus = sources.join("\n");

    const dead: string[] = [];
    for (const file of cssFiles(themeDir)) {
      // @theme 里的值靠 Tailwind 生成的工具类消费，不走 var()，本判据管不到，先剥掉。
      const text = readFileSync(file, "utf8").replace(/@theme\s*\{[^}]*\}/g, "");
      for (const m of text.matchAll(/(^|[{;\s])(--[a-z0-9][a-z0-9-]*)\s*:/g)) {
        const name = m[2];
        if (!corpus.includes(`var(${name}`)) dead.push(`${file.split(/[\\/]/).pop()}: ${name}`);
      }
    }
    expect(dead, `未被任何 var() 引用的主题 token: ${dead.join(", ")}`).toEqual([]);
  });

  it("honours prefers-reduced-motion in every stylesheet that animates", () => {
    // 门禁 171 页从不模拟 reduced-motion，所以这一维此前**完全没人量**：
    // 4 个模块各自写了 `expect(css).toContain("prefers-reduced-motion")` 的一次性断言，
    // 而第 13 个含动效的文件（theme/anime.css）正好是那个没人看的角落。
    // 这里收成全仓硬契约——动效声明与兜底必须成对出现，不留例外清单。
    const srcDir = join(fileURLToPath(new URL("..", import.meta.url)));
    const offenders: string[] = [];
    let animated = 0;
    for (const file of cssFiles(srcDir)) {
      const text = readFileSync(file, "utf8");
      // 判据与手工量分母时用的命令**逐字对应**
      // （grep -l "transition:\|animation:\|@keyframes" src --include=*.css → 13 个文件），
      // 否则下面的分母断言就是在赌一个没量过的数。
      if (
        !text.includes("transition:") &&
        !text.includes("animation:") &&
        !text.includes("@keyframes")
      ) {
        continue;
      }
      animated += 1;
      if (!/prefers-reduced-motion/.test(text)) {
        offenders.push(file.slice(srcDir.length).replace(/\\/g, "/").replace(/^\//, ""));
      }
    }
    // 分母自检：这条契约若哪天扫不到文件（口径坏了），也会"通过"，所以要钉住覆盖数。
    // 实测 13 个文件含动效声明；这里只要求 >10，留正常增删的余量。
    expect(animated, "一个声明动效的 CSS 都没扫到，扫描口径可能坏了").toBeGreaterThan(10);
    expect(offenders, `有动效但没有 reduced-motion 兜底的 CSS: ${offenders.join(", ")}`).toEqual([]);
  });
});
