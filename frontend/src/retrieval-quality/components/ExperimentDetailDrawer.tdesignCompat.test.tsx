// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import type { Agreement, ExperimentDetail } from "../model/contracts";
import type { JudgmentDraft } from "../hooks/useJudgmentMutation";

vi.mock("../../ui/rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => ["alert", "button", "drawer", "tag"].includes(component),
  },
}));

import ExperimentDetailDrawer from "./ExperimentDetailDrawer";

afterEach(cleanup);

const detail: ExperimentDetail = {
  sequence: 1,
  id: "exp-tdesign",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  query: "Q",
  query_hash: "a".repeat(64),
  strategy_snapshot: { strategy_revision: 1, dataset_serving_generation: 12 },
  result_snapshot: { results: [] },
  evidence_lineage: { citations: [] },
  latency_ms: 10,
  status: "completed",
  created_by: "runner",
  created_at: "2026-08-25T00:00:00Z",
  run_id: "run-tdesign",
  judgments: [],
};
const agreement: Agreement = {
  experiment_id: "exp-tdesign",
  judged_results: 0,
  judgment_count: 0,
  multi_judged_results: 0,
  unanimous_results: 0,
  conflicting_results: 0,
  exact_agreement_rate: null,
  label_counts: {},
  mean_score: null,
};
const draft: JudgmentDraft = { relevanceLabel: "partial", score: null, note: "" };

describe("ExperimentDetailDrawer TDesign facade compatibility", () => {
  it("keeps TDesign alert, button, and tag semantics", async () => {
    render(
      <ExperimentDetailDrawer
        visible
        detail={detail}
        agreement={agreement}
        status="ready"
        error={new Error("detail unavailable")}
        actorId="judge-a"
        openerRef={createRef()}
        conflictRanks={[]}
        savingRanks={[]}
        draftFor={() => draft}
        setDraft={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(document.querySelector(".t-button")).not.toBeNull();
    expect(document.querySelector(".t-tag--light-outline")).not.toBeNull();
    expect(document.querySelector(".t-alert--error")).not.toBeNull();
    expect(document.querySelector(".t-drawer")).not.toBeNull();
    expect(document.querySelector(".t-drawer__footer")).toBeNull();
    expect(document.querySelector(".t-drawer__close-btn")).toBeNull();
    await waitFor(() => expect(screen.getByRole("dialog", { name: "检索实验详情" })).not.toBeNull());
    expect(screen.getByRole("alert").textContent).toContain("实验详情读取不完整");
  });
});
