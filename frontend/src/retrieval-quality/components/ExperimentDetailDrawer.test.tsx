// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agreement, ExperimentDetail } from "../model/contracts";
import type { JudgmentDraft } from "../hooks/useJudgmentMutation";
import ExperimentDetailDrawer from "./ExperimentDetailDrawer";

afterEach(cleanup);
const detail: ExperimentDetail = { sequence: 1, id: "exp-1", tenant_id: "tenant-a", dataset_id: "dataset-a", query: "Q", query_hash: "a".repeat(64), strategy_snapshot: { strategy_revision: 1, dataset_serving_generation: 12, variant_name: "策略 A" }, result_snapshot: { results: [{ rank: 1, document_id: "doc-1", chunk_id: "chunk-1", score: .8, content: "evidence" }] }, evidence_lineage: { citations: [] }, latency_ms: 10, status: "completed", created_by: "runner", created_at: "2026-08-25T00:00:00Z", run_id: "run-1", judgments: [{ id: "owned", tenant_id: "tenant-a", dataset_id: "dataset-a", experiment_id: "exp-1", result_rank: 1, document_id: "doc-1", chunk_id: "chunk-1", relevance_label: "partial", score: 2, note: "review", revision: 4, created_by: "judge-a", created_at: "2026-08-25T00:00:00Z" }, { id: "foreign", tenant_id: "tenant-a", dataset_id: "dataset-a", experiment_id: "exp-1", result_rank: 1, document_id: "doc-1", chunk_id: "chunk-1", relevance_label: "irrelevant", score: 0, note: "other", revision: 2, created_by: "judge-b", created_at: "2026-08-25T00:00:00Z" }] };
const agreement: Agreement = { experiment_id: "exp-1", judged_results: 1, judgment_count: 2, multi_judged_results: 1, unanimous_results: 0, conflicting_results: 1, exact_agreement_rate: 0, label_counts: { partial: 1, irrelevant: 1 }, mean_score: 1 };
const draft: JudgmentDraft = { relevanceLabel: "partial", score: 2, note: "review" };

describe("ExperimentDetailDrawer", () => {
  it("shows immutable facts, owner/revision, agreement, conflict, and an honest Eval note", () => {
    render(<ExperimentDetailDrawer visible detail={detail} agreement={agreement} status="ready" error={null} actorId="judge-a" openerRef={createRef()} conflictRanks={[1]} savingRanks={[]} draftFor={() => draft} setDraft={vi.fn()} onSave={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("不可变实验快照")).toBeTruthy();
    expect(screen.getByText("judge-a · r4")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("判断已被更新");
    expect(screen.getByText("一致率")).toBeTruthy();
    expect(screen.getByText(/当前没有实验转入 Eval 的真实接口/)).toBeTruthy();
    expect(screen.getByText("judge-b · r2")).toBeTruthy();
  });

  it("restores focus to the history opener when closed", async () => {
    const user = userEvent.setup(); const openerRef = createRef<HTMLButtonElement>();
    render(<><button ref={openerRef}>opener</button><ExperimentDetailDrawer visible detail={detail} agreement={agreement} status="ready" error={null} actorId="judge-a" openerRef={openerRef} conflictRanks={[]} savingRanks={[]} draftFor={() => draft} setDraft={vi.fn()} onSave={vi.fn()} onClose={vi.fn()} /></>);
    await user.click(screen.getByRole("button", { name: "关闭实验详情" }));
    expect(document.activeElement).toBe(openerRef.current);
  });
});
