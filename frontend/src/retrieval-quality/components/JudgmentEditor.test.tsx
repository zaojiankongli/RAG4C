// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import type { Judgment } from "../model/contracts";
import type { JudgmentDraft } from "../hooks/useJudgmentMutation";
import JudgmentEditor from "./JudgmentEditor";

afterEach(cleanup);

const draft: JudgmentDraft = { relevanceLabel: "partial", score: 2, note: "原备注" };
const judgments: Judgment[] = [
  {
    id: "judgment-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    experiment_id: "exp-a",
    result_rank: 1,
    document_id: "doc-a",
    chunk_id: "chunk-a",
    relevance_label: "partial",
    score: 2,
    note: "原备注",
    revision: 3,
    created_by: "actor-a",
    created_at: "2026-09-26T00:00:00Z",
  },
];

describe("JudgmentEditor", () => {
  it("uses the shared facade and preserves judgment callback values", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onSave = vi.fn();
    function Harness() {
      const [current, setCurrent] = useState(draft);
      return (
        <JudgmentEditor
          rank={1}
          judgments={judgments}
          actorId="actor-a"
          draft={current}
          conflict
          saving={false}
          onChange={(next) => {
            onChange(next);
            setCurrent(next);
          }}
          onSave={onSave}
        />
      );
    }
    render(<Harness />);

    expect(document.querySelector(".rag-tag")).not.toBeNull();
    expect(screen.getByText(/已刷新最新 owner\/revision/)).toBeTruthy();
    const note = screen.getByRole("textbox");
    await user.clear(note);
    await user.type(note, "新备注");
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, note: "新备注" });

    await user.click(screen.getByRole("button", { name: "保存判断" }));
    expect(onSave).toHaveBeenCalledOnce();
  });

  it("keeps a cleared score as null", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <JudgmentEditor
        rank={1}
        judgments={[]}
        actorId="actor-a"
        draft={draft}
        conflict={false}
        saving={false}
        onChange={onChange}
        onSave={vi.fn()}
      />,
    );

    await user.clear(screen.getByRole("spinbutton"));
    expect(onChange).toHaveBeenLastCalledWith({ ...draft, score: null });
  });
});
