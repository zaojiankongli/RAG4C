// @vitest-environment jsdom

import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Agreement } from "../model/contracts";
import AgreementPanel from "./AgreementPanel";

afterEach(cleanup);

const agreement: Agreement = {
  experiment_id: "exp-1",
  judged_results: 3,
  judgment_count: 4,
  multi_judged_results: 2,
  unanimous_results: 1,
  conflicting_results: 1,
  exact_agreement_rate: 0.75,
  label_counts: { relevant: 2, partial: 1, irrelevant: 1 },
  mean_score: 2.5,
};

describe("AgreementPanel", () => {
  it("renders through the native Card facade with the existing agreement facts", () => {
    const { container } = render(<AgreementPanel agreement={agreement} />);

    const card = container.querySelector(".rag-card");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText("判断一致性")).toBeTruthy();
    expect(within(card as HTMLElement).getByText("75.0%")).toBeTruthy();
    expect(within(card as HTMLElement).getByText("2.50")).toBeTruthy();
    expect(within(card as HTMLElement).getByText("相关 2 · 部分相关 1 · 不相关 1")).toBeTruthy();
  });
});
