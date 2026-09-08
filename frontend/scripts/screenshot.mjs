import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://localhost:1420";

const pages = [
  { hash: "#/query", name: "query" },
  { hash: "#/visualize", name: "visualize" },
  { hash: "#/monitor", name: "monitor" },
  { hash: "#/eval", name: "eval" },
  { hash: "#/config", name: "config" },
];

mkdirSync(".shots", { recursive: true });

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();

const errs = [];
page.on("pageerror", (e) => errs.push("pageerror: " + String(e)));
page.on("console", (m) => {
  if (m.type() === "error") errs.push("console.error: " + m.text());
});

// 浅色
for (const p of pages) {
  await page.goto(BASE + p.hash, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(p.name === "config" ? 600 : 1800);
  if (p.name === "config") {
    await page.getByText("解析引擎").first().waitFor({ timeout: 10000 });
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: ".shots/" + p.name + "-light.png" });
}

// 问答页演示问答
await page.goto(BASE + "#/query", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(800);
await page.locator(".sample-item").first().click();
await page.waitForTimeout(4500);
await page.screenshot({ path: ".shots/query-answer-light.png" });
// 引用联动：点击第一个 [1] 标签
const refTag = page.locator(".ref-tag").first();
if (await refTag.count()) {
  await refTag.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: ".shots/query-citation-light.png" });
}

// 可视化页回放
await page.goto(BASE + "#/visualize", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(1500);
// 搜索演示图谱
await page.locator("input[placeholder*='搜人物']").fill("图灵");
await page.getByRole("button", { name: "搜 索" }).click();
await page.waitForTimeout(2500);
await page.screenshot({ path: ".shots/visualize-graph-light.png" });

// 暗色（query 放 hash 之前才能触发整页重载，确保 React 重新读取主题）
await page.evaluate(() => localStorage.setItem("rag4c.theme_mode", "dark"));
for (const p of pages) {
  await page.goto(BASE + "/?t=" + Date.now() + p.hash, {
    waitUntil: "networkidle",
    timeout: 30000,
  });
  if (p.name === "config") {
    await page.getByText("解析引擎").first().waitFor({ timeout: 10000 });
    await page.waitForTimeout(400);
  }
  await page.waitForTimeout(2000);
  await page.screenshot({ path: ".shots/" + p.name + "-dark.png" });
}
await page.evaluate(() => localStorage.setItem("rag4c.theme_mode", "light"));

console.log("errors:", errs.length ? errs.slice(0, 10).join("\n") : "(none)");
await browser.close();
console.log("screenshots done");
