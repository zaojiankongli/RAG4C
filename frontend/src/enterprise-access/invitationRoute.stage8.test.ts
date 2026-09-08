// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { buildInvitationAcceptUrl, invitationAcceptTokenFromLocation } from "./invitationRoute";

describe("Stage 8 invitation acceptance route", () => {
  it("reads direct and hash invitation tokens without persisting them", () => {
    expect(
      invitationAcceptTokenFromLocation({
        pathname: "/enterprise/invitations/accept",
        search: "?token=direct-token",
        hash: "",
      }),
    ).toBe("direct-token");
    expect(
      invitationAcceptTokenFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/invitations/accept?token=hash-token",
      }),
    ).toBe("hash-token");
  });

  it("builds an enterprise invitation accept link with an encoded token", () => {
    expect(
      buildInvitationAcceptUrl("token/with space", {
        origin: "https://console.example.com",
        pathname: "/",
      }),
    ).toBe(
      "https://console.example.com/#/enterprise/invitations/accept?token=token%2Fwith%20space",
    );
  });
});
