import { chromium } from "playwright-core";

const BASE = "http://localhost:1421";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
await page.goto(`${BASE}/governance`, { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(2500);
const before = await page.evaluate(() => ({
  keys: Object.keys(localStorage).filter((k) => k.includes("onboard")),
  welcome: !!document.body.innerText.includes("欢迎来到 RAG4C"),
}));
const skip = page.getByRole("button", { name: /跳过/ }).first();
if (await skip.isVisible().catch(() => false)) {
  await skip.click();
  await page.waitForTimeout(800);
}
const afterSkip = await page.evaluate(() => ({
  keys: Object.keys(localStorage).filter((k) => k.includes("onboard")),
  values: Object.fromEntries(
    Object.keys(localStorage)
      .filter((k) => k.includes("onboard"))
      .map((k) => [k, localStorage.getItem(k)]),
  ),
  welcome: !!document.body.innerText.includes("欢迎来到 RAG4C"),
}));
await page.goto(`${BASE}/overview`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
const revisit = await page.evaluate(() => ({
  welcome: !!document.body.innerText.includes("欢迎来到 RAG4C"),
  body: (document.body.innerText || "").slice(0, 200).replace(/\s+/g, " "),
}));
await page.screenshot({ path: "D:/program_project/python_project/RAG4C-compose-qa-faq-ops/.tmp/compose-onboarding-recheck.png" });
console.log(JSON.stringify({ before, afterSkip, revisit }, null, 2));
await browser.close();
