// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const sources = [
  readFileSync(new URL("../enterprise-approval/approvalRoute.ts", import.meta.url), "utf8"),
  readFileSync(
    new URL("../enterprise-admin/memberRoleApprovalNavigation.ts", import.meta.url),
    "utf8",
  ),
  readFileSync(
    new URL("../enterprise-access/components/DatasetAclDisableDialog.tsx", import.meta.url),
    "utf8",
  ),
  readFileSync(
    new URL(
      "../enterprise-workspace/components/WorkspacePermissionsRolloutCenter.tsx",
      import.meta.url,
    ),
    "utf8",
  ),
];

describe("approval navigation commit boundary", () => {
  it("keeps approval browser side effects behind the shared adapter", () => {
    for (const source of sources) {
      expect(source).toContain("commitNavigationIntent");
      expect(source).not.toMatch(/window\.history\.(pushState|replaceState)/);
      expect(source).not.toMatch(/window\.dispatchEvent\(new PopStateEvent\("popstate"\)/);
    }
  });
});
