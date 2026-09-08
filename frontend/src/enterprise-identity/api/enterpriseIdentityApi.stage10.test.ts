// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildScimDataPlaneUrls } from "./enterpriseIdentityApi";

describe("Stage 10 SCIM endpoint API projection", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://rag.enterprise.example/root/");
  });
  afterEach(() => localStorage.clear());

  it("builds copy-safe SCIM v2 and ServiceProviderConfig URLs from the configured API origin", () => {
    expect(buildScimDataPlaneUrls()).toEqual({
      baseEndpoint: "https://rag.enterprise.example/scim/v2",
      serviceProviderConfigUrl: "https://rag.enterprise.example/scim/v2/ServiceProviderConfig",
    });
  });
});
