// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchEvalDatasets: vi.fn(),
  fetchEvalResults: vi.fn(),
  runEval: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("../context/ConnectionContext", () => ({
  useConnection: () => ({ online: true }),
}));
vi.mock("../charts/EChart", () => ({
  default: ({ ariaLabel }: { ariaLabel?: string }) => <div role="img" aria-label={ariaLabel} />,
}));

import EvalPage from "./EvalPage";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EvalPage", () => {
  it("renders the initial evaluation controls without entering a radio render loop", async () => {
    api.fetchEvalDatasets.mockResolvedValue({
      datasets: ["eval/dataset_sample.py:SAMPLE_DATASET"],
      default: "eval/dataset_sample.py:SAMPLE_DATASET",
    });
    api.fetchEvalResults.mockResolvedValue({ report: null });

    render(<EvalPage />);

    expect(screen.getByRole("heading", { name: "质量评测" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "快速演练" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "真实资料评测" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行评测" })).toBeTruthy();
    await waitFor(() => expect(api.fetchEvalResults).toHaveBeenCalledTimes(1));
  });
});
