// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import CapabilityState from "./CapabilityState";

const unavailable = {
  state: "unavailable" as const,
  label: "组织架构",
  reason: "尚未接入企业组织目录",
};

describe("CapabilityState", () => {
  afterEach(cleanup);

  it("presents an unavailable backend capability as a professional state, not a fake zero count", () => {
    render(<CapabilityState capability={unavailable} />);

    expect(screen.getByText("组织架构")).toBeTruthy();
    expect(screen.getByText("尚未接入企业组织目录")).toBeTruthy();
    expect(screen.getByText("尚未接入")).toBeTruthy();
    expect(screen.queryByText(/0\s*(个|条|项)/)).toBeNull();
  });
});
