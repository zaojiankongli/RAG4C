// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
afterEach(cleanup);
import RunTimeline from "./RunTimeline";

describe("RunTimeline keyboard DOM", () => {
  it.each(["{Enter}", " "])(
    "activates a native bar with %s and propagates aria-current",
    async (key) => {
      const onSelectNode = vi.fn();
      const props = {
        intervals: [
          {
            id: "generation:1",
            nodeId: "generation",
            attempt: 1,
            startMs: 0,
            endMs: 100,
            durationMs: 100,
            status: "completed" as const,
            partial: false,
            retry: false,
            startSeq: 2,
            endSeq: 3,
            wave: 1,
          },
        ],
        waves: [
          {
            id: 1,
            startMs: 0,
            endMs: 100,
            wallTimeMs: 100,
            intervalCount: 1,
            maxConcurrency: 1,
            intervalIds: ["generation:1"],
          },
        ],
        idleGaps: [],
        onSelectNode,
      };
      const rendered = render(<RunTimeline {...props} />);
      const button = screen.getByRole("button", { name: /generation.*attempt 1/i });
      button.focus();
      await userEvent.keyboard(key);
      expect(onSelectNode).toHaveBeenCalledWith("generation");
      rendered.rerender(<RunTimeline {...props} selectedNodeId="generation" />);
      expect(
        screen.getByRole("button", { name: /generation.*attempt 1/i }).getAttribute("aria-current"),
      ).toBe("true");
    },
  );
});
