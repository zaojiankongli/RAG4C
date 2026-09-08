import { chromium } from "playwright-core";

const appUrl = process.env.RAG4C_E2E_URL ?? "http://127.0.0.1:1420";
const chromePath =
  process.env.RAG4C_CHROME_PATH ?? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const browser = await chromium.launch({ executablePath: chromePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
let submittedBody;

await page.route("http://localhost:8000/api/**", async (route) => {
  const request = route.request();
  const pathname = new URL(request.url()).pathname;
  if (pathname === "/api/health") {
    await route.fulfill({ json: { status: "ok" } });
    return;
  }
  if (pathname === "/api/documents" && request.method() === "GET") {
    await route.fulfill({ json: { documents: [] } });
    return;
  }
  if (pathname === "/api/documents/ingest-folder" && request.method() === "POST") {
    submittedBody = request.postDataJSON();
    await route.fulfill({
      json: {
        folder_path: "D:\\knowledge",
        discovered_count: 5,
        queued_count: 3,
        skipped_count: 2,
        unsupported_count: 1,
        duplicate_count: 1,
        rejected_count: 0,
        rejected_files: [],
        document_ids: ["doc-1", "doc-2", "doc-3"],
        note: "已登记 3 个文件，跳过 2 个；入库进度会在文档列表中自动刷新。",
      },
    });
    return;
  }
  await route.fulfill({ status: 404, json: { detail: "not mocked" } });
});

try {
  await page.goto(`${appUrl}/#/documents`);
  await page.getByRole("button", { name: "批量导入" }).click();
  await page.getByLabel("文件夹路径").fill("D:\\knowledge");
  await page.getByRole("button", { name: "开始导入" }).click();
  await page.getByText("已登记 3 个文件，跳过 2 个").waitFor();

  const expected = { folder_path: "D:\\knowledge", dataset_id: "default" };
  if (JSON.stringify(submittedBody) !== JSON.stringify(expected)) {
    throw new Error(`unexpected request body: ${JSON.stringify(submittedBody)}`);
  }
  console.log("batch folder import browser flow passed");
} finally {
  await browser.close();
}
