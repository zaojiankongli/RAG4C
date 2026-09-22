import { describe, expect, it } from "vitest";
import {
  mainNavigationKey,
  navigationIntent,
  parseGovernanceQaDeepLink,
  parsePageLocation,
} from "./appRoute";

describe("governance QA deep link", () => {
  it("reads the qa id from both hash and history forms of the governance route", () => {
    expect(
      parseGovernanceQaDeepLink({ pathname: "/", search: "", hash: "#/governance?qa=qa-faq-1&dataset=ds-1" }),
    ).toEqual({ qaId: "qa-faq-1" });
    expect(
      parseGovernanceQaDeepLink({ pathname: "/governance", search: "?qa=qa-faq-2", hash: "" }),
    ).toEqual({ qaId: "qa-faq-2" });
  });
  it("yields no qa id for a bare governance link or an unrelated page", () => {
    expect(parseGovernanceQaDeepLink({ pathname: "/", search: "", hash: "#/governance" })).toEqual({ qaId: "" });
    expect(
      parseGovernanceQaDeepLink({ pathname: "/", search: "", hash: "#/documents?qa=qa-1" }),
    ).toEqual({ qaId: "" });
  });
});

describe("app route parser", () => {
  it("opens direct and hash visualize deep links without treating query as page key", () => {
    expect(parsePageLocation({ pathname: "/visualize", hash: "", search: "?run=r" })).toBe(
      "visualize",
    );
    expect(
      parsePageLocation({ pathname: "/", hash: "#/visualize?run=r&tab=timeline", search: "" }),
    ).toBe("visualize");
  });
  it("keeps visualize selected across back-forward location shapes", () => {
    expect(parsePageLocation({ pathname: "/visualize", hash: "", search: "?tab=events" })).not.toBe(
      "query",
    );
  });
});
it("uses direct history navigation when pathname and hash conflict", () => {
  expect(
    navigationIntent({ pathname: "/visualize", search: "?run=r", hash: "#/visualize" }, "query"),
  ).toEqual({ mode: "history", url: "/query" });
  expect(parsePageLocation({ pathname: "/query", search: "", hash: "" })).toBe("query");
  expect(navigationIntent({ pathname: "/", search: "", hash: "#/visualize" }, "query")).toEqual({
    mode: "hash",
    url: "/query",
  });
});

describe("enterprise OIDC callback route", () => {
  it("keeps direct and hash callbacks mounted on the enterprise page", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/sso/oidc/callback",
        search: "?code=c&state=s",
        hash: "",
      }),
    ).toBe("enterprise");
    expect(
      parsePageLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/sso/oidc/callback?code=c&state=s",
      }),
    ).toBe("enterprise");
  });
});

describe("enterprise identity federation route", () => {
  it("keeps the nested identity center on the enterprise page", () => {
    expect(parsePageLocation({ pathname: "/enterprise/identity", search: "", hash: "" })).toBe(
      "enterprise",
    );
    expect(parsePageLocation({ pathname: "/", search: "", hash: "#/enterprise/identity" })).toBe(
      "enterprise",
    );
  });
});

describe("enterprise invitation accept routes", () => {
  it("keeps direct and hash accept deep links mounted on the enterprise page", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/invitations/accept",
        search: "?token=direct",
        hash: "",
      }),
    ).toBe("enterprise");
    expect(
      parsePageLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/invitations/accept?token=hash",
      }),
    ).toBe("enterprise");
  });
});

describe("knowledge workspace routes", () => {
  it("opens new knowledge workspace routes", () => {
    for (const key of [
      "overview",
      "taxonomy",
      "governance",
      "sources",
      "retrieval-lab",
      "consistency",
      "enterprise",
    ] as const) {
      expect(parsePageLocation({ pathname: `/${key}`, hash: "", search: "" })).toBe(key);
    }
  });
});

describe("enterprise workspace route", () => {
  it("keeps direct and hash Workspace Center deep links mounted on enterprise", () => {
    expect(parsePageLocation({ pathname: "/enterprise/workspaces", search: "", hash: "" })).toBe(
      "enterprise",
    );
    expect(parsePageLocation({ pathname: "/", search: "", hash: "#/enterprise/workspaces" })).toBe(
      "enterprise",
    );
  });
});

describe("enterprise notification center route", () => {
  it("opens direct and hash Content Recovery links as a primary knowledge navigation item", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/recycle-bin",
        search: "?status=recycled",
        hash: "",
      }),
    ).toBe("recycle-bin");
    expect(
      parsePageLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/recycle-bin?status=held",
      }),
    ).toBe("recycle-bin");
    expect(
      navigationIntent(
        { pathname: "/enterprise/recycle-bin", search: "", hash: "" },
        "recycle-bin",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/recycle-bin" });
    expect(mainNavigationKey("recycle-bin")).toBe("recycle-bin");
  });

  it("opens direct and hash Notification Center links without adding a primary nav key", () => {
    expect(
      parsePageLocation({ pathname: "/enterprise/notifications", search: "?tab=unread", hash: "" }),
    ).toBe("notifications");
    expect(
      parsePageLocation({ pathname: "/", search: "", hash: "#/enterprise/notifications?tab=all" }),
    ).toBe("notifications");
    expect(
      navigationIntent(
        { pathname: "/enterprise/notifications", search: "", hash: "" },
        "notifications",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/notifications" });
  });
});

describe("enterprise task operations route", () => {
  it("opens direct and hash Task Center links as a primary Knowledge Operations item", () => {
    expect(
      parsePageLocation({ pathname: "/enterprise/tasks", search: "?status=failed", hash: "" }),
    ).toBe("tasks");
    expect(
      parsePageLocation({ pathname: "/", search: "", hash: "#/enterprise/tasks?status=running" }),
    ).toBe("tasks");
    expect(
      navigationIntent({ pathname: "/enterprise/tasks", search: "", hash: "" }, "tasks"),
    ).toEqual({ mode: "history", url: "/enterprise/tasks" });
    expect(
      navigationIntent({ pathname: "/", search: "", hash: "#/enterprise/tasks" }, "tasks"),
    ).toEqual({ mode: "hash", url: "/enterprise/tasks" });
    expect(mainNavigationKey("tasks")).toBe("tasks");
  });
});

describe("enterprise automation workflow route", () => {
  it("opens direct and hash Automation Center links as a primary Knowledge Operations item", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/automations",
        search: "?status=active",
        hash: "",
      }),
    ).toBe("automations");
    expect(
      parsePageLocation({ pathname: "/", search: "", hash: "#/enterprise/automations?tab=runs" }),
    ).toBe("automations");
    expect(
      navigationIntent(
        { pathname: "/enterprise/automations", search: "", hash: "" },
        "automations",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/automations" });
    expect(
      navigationIntent(
        { pathname: "/", search: "", hash: "#/enterprise/automations" },
        "automations",
      ),
    ).toEqual({ mode: "hash", url: "/enterprise/automations" });
    expect(mainNavigationKey("automations")).toBe("automations");
  });
});
