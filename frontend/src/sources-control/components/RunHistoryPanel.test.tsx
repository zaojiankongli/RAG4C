// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SourceRecord, SourceRun } from "../model/sourceModels";
import RunHistoryPanel from "./RunHistoryPanel";

afterEach(cleanup);

const source: SourceRecord = { id: "source-a", tenant_id: "tenant-a", dataset_id: "dataset-a", name: "Docs", kind: "github_repo", config: { repo: "trusted/docs" }, metadata: {}, status: "active", generation: 4, last_cursor: {}, last_result: {}, last_error: "", last_sync_at: null, created_at: "2026-08-25T00:00:00Z", updated_at: "2026-08-25T00:00:00Z" };
const failed: SourceRun = { id: "run-1234567890-abcdefghijklmnop", source_id: "source-a", tenant_id: "tenant-a", dataset_id: "dataset-a", status: "failed", trigger: "manual", force_full: false, dry_run: false, source_generation: 4, dataset_generation: 9, retry_of_run_id: null, execution_state: "completed", execution_attempts: 3, execution_last_error: "", execution_finished_at: "2026-08-25T00:00:04Z", cursor_before: {}, cursor_after: {}, counts: { fetched: 1, ingested: 0, skipped: 0, removed: 0, pending_deletes: 0, chunks: 0, failed: 1 }, fetch_error: "safe", duration_ms: 4000, started_at: "2026-08-25T00:00:00Z", finished_at: "2026-08-25T00:00:04Z" };

it("separates run and execution status, exposes generations and honest retry capability", () => {
  const retry = vi.fn();
  render(<RunHistoryPanel source={source} runs={[failed]} status="ready" error={null} runStatus={undefined} trigger={undefined} nextCursor={null} hasPrevious={false} selectedRun={failed} items={[]} itemsNextCursor={null} hasPreviousItems={false} itemResult={undefined} itemAction={undefined} paging={false} itemsPaging={false} detailLoading={false} retrying={false} lastAccepted={null} onRefresh={vi.fn()} onNext={vi.fn()} onPrevious={vi.fn()} onFilters={vi.fn()} onSelect={vi.fn()} onClose={vi.fn()} onNextItems={vi.fn()} onPreviousItems={vi.fn()} onItemFilters={vi.fn()} onRetry={retry} />);
  expect(screen.getAllByText("运行失败").length).toBeGreaterThan(0);
  expect(screen.getAllByText("执行已结束").length).toBeGreaterThan(0);
  expect(screen.getAllByText("来源 G4 / 数据集 G9").length).toBeGreaterThan(0);
  expect(screen.getByText("API 未提供下次尝试时间或退避时长")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重试失败运行" }));
  expect(retry).toHaveBeenCalledWith(failed);
  expect(document.body.textContent).not.toContain("tenant-a");
});
it("derives accepted retry copy from replay, run status and execution state", () => {
  render(<RunHistoryPanel source={source} runs={[]} status="ready" error={null} runStatus={undefined} trigger={undefined} nextCursor={null} hasPrevious={false} paging={false} selectedRun={null} items={[]} itemsNextCursor={null} hasPreviousItems={false} itemsPaging={false} detailLoading={false} itemResult={undefined} itemAction={undefined} retrying={false} lastAccepted={{ run_id: "retry-a", source_id: "source-a", status: "running", trigger: "retry", retry_of_run_id: failed.id, execution_state: "executing", replayed: true }} onRefresh={vi.fn()} onNext={vi.fn()} onPrevious={vi.fn()} onFilters={vi.fn()} onSelect={vi.fn()} onClose={vi.fn()} onNextItems={vi.fn()} onPreviousItems={vi.fn()} onItemFilters={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText(/原请求重放/)).toBeTruthy();
  expect(screen.getByText(/当前状态：运行中 \/ 正在执行/)).toBeTruthy();
});

it("hides retry for a disabled source and renders captions plus explicit loading rows", () => {
  render(<RunHistoryPanel source={{ ...source, status: "disabled" }} runs={[]} status="loading" error={null} runStatus={undefined} trigger={undefined} nextCursor={null} hasPrevious={false} paging selectedRun={{ ...failed }} items={[]} itemsNextCursor={null} hasPreviousItems={false} itemsPaging={false} detailLoading itemResult={undefined} itemAction={undefined} retrying={false} lastAccepted={null} onRefresh={vi.fn()} onNext={vi.fn()} onPrevious={vi.fn()} onFilters={vi.fn()} onSelect={vi.fn()} onClose={vi.fn()} onNextItems={vi.fn()} onPreviousItems={vi.fn()} onItemFilters={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText("来源运行历史，每页十条")).toBeTruthy();
  expect(screen.getByText("正在加载运行记录…")).toBeTruthy();
  expect(screen.getByText("正在加载运行项目…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "重试失败运行" })).toBeNull();
  expect(screen.getByText(/操作权限由 Source API/)).toBeTruthy();
});
