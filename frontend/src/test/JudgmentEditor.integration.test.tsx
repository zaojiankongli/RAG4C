// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import JudgmentEditor from "../retrieval-quality/components/JudgmentEditor";

it("renders the real conflicted judgment editor with owner and foreign evidence", () => {
  render(
    <JudgmentEditor
      rank={1}
      actorId="judge-a"
      judgments={[
        {
          id: "owned",
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          experiment_id: "exp-1",
          result_rank: 1,
          document_id: "doc-1",
          chunk_id: "chunk-1",
          relevance_label: "partial",
          score: 2,
          note: "review",
          revision: 4,
          created_by: "judge-a",
          created_at: "2026-08-25T00:00:00Z",
        },
        {
          id: "foreign",
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          experiment_id: "exp-1",
          result_rank: 1,
          document_id: "doc-1",
          chunk_id: "chunk-1",
          relevance_label: "irrelevant",
          score: 0,
          note: "other",
          revision: 2,
          created_by: "judge-b",
          created_at: "2026-08-25T00:00:00Z",
        },
      ]}
      draft={{ relevanceLabel: "partial", score: 2, note: "review" }}
      conflict
      saving={false}
      onChange={vi.fn()}
      onSave={vi.fn()}
    />,
  );
  expect(screen.getByText("judge-a · r4")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("判断已被更新");
  expect(screen.getByText("judge-b · r2")).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存判断" })).toBeTruthy();
});
