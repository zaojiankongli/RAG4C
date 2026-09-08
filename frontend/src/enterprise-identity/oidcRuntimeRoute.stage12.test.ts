// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildOidcCallbackUrl,
  clearOidcCallbackLocation,
  oidcCallbackFromLocation,
} from "./oidcRuntimeRoute";

describe("Stage 12 OIDC callback route", () => {
  afterEach(() => vi.restoreAllMocks());

  it("parses direct/hash code and state without accepting partial callbacks", () => {
    expect(
      oidcCallbackFromLocation({
        pathname: "/enterprise/sso/oidc/callback",
        search: "?code=c1&state=s1",
        hash: "",
        origin: "https://rag.example",
      }),
    ).toEqual({
      code: "c1",
      state: "s1",
    });
    expect(
      oidcCallbackFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/sso/oidc/callback?code=c2&state=s2",
        origin: "https://rag.example",
      }),
    ).toEqual({
      code: "c2",
      state: "s2",
    });
    expect(
      oidcCallbackFromLocation({
        pathname: "/enterprise/sso/oidc/callback",
        search: "?code=missing-state",
        hash: "",
      }),
    ).toBeNull();
  });

  it("builds the allowlisted callback URL and clears code/state with replaceState", () => {
    expect(
      buildOidcCallbackUrl({
        origin: "https://rag.example",
        pathname: "/enterprise/identity",
        hash: "",
      }),
    ).toBe("https://rag.example/enterprise/sso/oidc/callback");
    expect(
      buildOidcCallbackUrl({
        origin: "https://rag.example",
        pathname: "/",
        hash: "#/enterprise/identity",
      }),
    ).toBe("https://rag.example/enterprise/sso/oidc/callback");
    expect(
      buildOidcCallbackUrl({
        origin: "http://127.0.0.1:5173",
        pathname: "/",
        hash: "#/enterprise/identity",
      }),
    ).toBe("http://127.0.0.1:5173/enterprise/sso/oidc/callback");
    expect(
      buildOidcCallbackUrl({
        origin: "https://rag.example",
        pathname: "/",
        hash: "#/enterprise/identity",
      }),
    ).not.toContain("#");
    window.history.replaceState(null, "", "/enterprise/sso/oidc/callback?code=secret&state=secret");
    const replace = vi.spyOn(window.history, "replaceState");
    clearOidcCallbackLocation();
    expect(replace).toHaveBeenCalledWith(null, "", "/enterprise/identity");
    expect(window.location.search).toBe("");
  });
});
