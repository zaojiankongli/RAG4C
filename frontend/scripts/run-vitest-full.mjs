import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const SOURCE_ROOT = resolve(ROOT, "src");
const VITEST = resolve(ROOT, "node_modules", "vitest", "vitest.mjs");
const TEST_FILE = /\.(?:test|spec)\.[cm]?[jt]sx?$/;
const SPECIAL_CONFIG_BY_FILE = new Map([
  [
    "src/retrieval-quality/components/ExperimentDetailDrawer.test.tsx",
    "vitest.experiment-detail.config.ts",
  ],
]);

function positiveInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : fallback;
}

function collect(directory) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...collect(absolute));
    else if (entry.isFile() && TEST_FILE.test(entry.name)) files.push(absolute);
  }
  return files;
}

function partition(files, batchSize) {
  const batches = [];
  for (let index = 0; index < files.length; index += batchSize) {
    batches.push(files.slice(index, index + batchSize));
  }
  return batches;
}

const uiBatchSize = positiveInteger(process.env.RAG4C_VITEST_UI_BATCH_SIZE, 2, 1, 8);
const nodeBatchSize = positiveInteger(process.env.RAG4C_VITEST_NODE_BATCH_SIZE, 20, 1, 64);
const workers = positiveInteger(process.env.RAG4C_VITEST_WORKERS, 1, 1, 16);
const heapMb = positiveInteger(process.env.RAG4C_VITEST_HEAP_MB, 4096, 512, 8192);
const files = collect(SOURCE_ROOT).sort((left, right) => left.localeCompare(right));
if (!files.length) {
  console.error("No frontend test files were discovered under src/");
  process.exit(2);
}

const relativeFiles = files.map((file) => relative(ROOT, file).replaceAll("\\", "/"));
const manifest = createHash("sha256").update(relativeFiles.join("\n")).digest("hex");
const uiFiles = [];
const nodeFiles = [];
const specialBatches = [];
for (const file of files) {
  const relativeFile = relative(ROOT, file).replaceAll("\\", "/");
  const specialConfig = SPECIAL_CONFIG_BY_FILE.get(relativeFile);
  if (specialConfig) {
    specialBatches.push({ files: [relativeFile], config: specialConfig });
    continue;
  }
  const sourcePrefix = readFileSync(file, "utf8").slice(0, 512);
  if (/\.[jt]sx$/.test(file) || sourcePrefix.includes("@vitest-environment jsdom"))
    uiFiles.push(relativeFile);
  else nodeFiles.push(relativeFile);
}
const shards = [
  ...specialBatches,
  ...partition(uiFiles, uiBatchSize).map((batch) => ({ files: batch, config: null })),
  ...partition(nodeFiles, nodeBatchSize).map((batch) => ({ files: batch, config: null })),
];
const childEnv = { ...process.env };
if (!/--max-old-space-size(?:=|\s)/.test(childEnv.NODE_OPTIONS ?? "")) {
  childEnv.NODE_OPTIONS = `${childEnv.NODE_OPTIONS ?? ""} --max-old-space-size=${heapMb}`.trim();
}

console.log(
  `[RAG4C full frontend test] files=${relativeFiles.length} ui_files=${uiFiles.length} node_files=${nodeFiles.length} batches=${shards.length} ui_batch=${uiBatchSize} node_batch=${nodeBatchSize} workers=${workers} heap_mb=${heapMb} manifest=${manifest}`,
);

const failures = [];
for (let index = 0; index < shards.length; index += 1) {
  const shard = shards[index];
  console.log(
    `\n=== Vitest batch ${index + 1}/${shards.length} · ${shard.files.length} file(s) ===`,
  );
  const result = spawnSync(
    process.execPath,
    [
      VITEST,
      "run",
      "--pool=forks",
      `--maxWorkers=${workers}`,
      "--minWorkers=1",
      ...(shard.config ? ["--config", shard.config] : []),
      ...shard.files,
    ],
    {
      cwd: ROOT,
      env: childEnv,
      stdio: "inherit",
      windowsHide: true,
    },
  );
  if (result.error || result.status !== 0) {
    // 汇总里只带 batch 号是不够的：上面 stdio:"inherit" 的 vitest 明细会被调用方的
    // `| tail` 截掉，于是"哪一批失败了"看得到、"哪个用例失败了"查不到
    // （R7 就是靠反推分批才定位到批 85）。这里把成员与找回方式一起打出来。
    console.error(`\n[batch ${index + 1} failed] 该批文件：`);
    for (const file of shard.files) console.error(`  ${file}`);
    console.error("用例级明细见上方 vitest 输出；若被截断，单独重跑上面这些文件即可。");
    failures.push({
      batch: index + 1,
      status: result.status,
      signal: result.signal,
      files: shard.files,
      error: result.error?.message ?? null,
    });
  }
}

if (failures.length) {
  console.error("\nRAG4C full frontend test failed:");
  console.error(JSON.stringify({ manifest, files: relativeFiles.length, failures }, null, 2));
  process.exit(1);
}

const covered = shards.reduce((total, shard) => total + shard.files.length, 0);
console.log(
  `\nRAG4C full frontend test passed: ${covered}/${relativeFiles.length} files, manifest=${manifest}`,
);
