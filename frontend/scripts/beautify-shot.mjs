// Screenshot helper for the beautify pass: captures key pages in light + dark.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const OUT = "output/beautify/final";
mkdirSync(OUT, { recursive: true });

const PAGES = [
  ["overview", "/#/overview"],
  ["query", "/#/query"],
  ["documents", "/#/documents"],
  ["sources", "/#/sources"],
  ["governance", "/#/governance"],
  ["retrieval-lab", "/#/retrieval-lab"],
  ["monitor", "/#/monitor"],
  ["knowledge-overview", "/#/knowledge-bases"],
];

const exe = process.env.CHROME_PATH;
const browser = await chromium.launch({ executablePath: exe, headless: true });

async function shoot(theme) {
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
    colorScheme: theme === "dark" ? "dark" : "light",
  });
  const page = await ctx.newPage();
  // 应用从 localStorage 读主题，init script 预置才不会被启动逻辑覆盖
  await ctx.addInitScript(
    (t) => {
      localStorage.setItem("rag4c.theme_mode", t);
    },
    theme,
  );
  for (const [name, hash] of PAGES) {
    await page.goto("http://localhost:1420/" + hash, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/${name}-${theme}.png` });
    console.log("shot", name, theme);
  }
  await ctx.close();
}

await shoot("light");
await shoot("dark");
await browser.close();
console.log("done");
