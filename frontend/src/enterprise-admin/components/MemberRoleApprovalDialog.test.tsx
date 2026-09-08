// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemberRoleApprovalDialog from "./MemberRoleApprovalDialog";

const member = {
  account_id: "account-42",
  name: "周宁",
  email: "zhou@example.com",
  role: "member",
  status: "active",
  revision: 7,
};

const policy = {
  id: "policy-role-1",
  name: "成员角色双人复核",
  resource_scope: "tenant_member:account-42",
  required_approvals: 2,
  request_expiry_minutes: 60,
  revision: 9,
  expires_at: null,
};

const request = {
  id: "request-role-1",
  status: "pending",
};

afterEach(cleanup);

describe("MemberRoleApprovalDialog", () => {
  it("shows policy evidence and submits a request without changing the member role", async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <MemberRoleApprovalDialog
        member={member}
        visible
        saving={false}
        error={null}
        mode="approval"
        policy={policy}
        request={null}
        submitting={false}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        onNavigateToApprovalCenter={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "修改成员角色" });
    expect(within(dialog).getByText("成员角色双人复核")).toBeTruthy();
    expect(within(dialog).getByText("2 人")).toBeTruthy();
    expect(within(dialog).getByText("60 分钟")).toBeTruthy();
    expect(within(dialog).getByText("revision 9")).toBeTruthy();
    expect(within(dialog).getByText("成员角色保持不变")).toBeTruthy();

    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "新角色" }),
      "editor",
    );
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "职责调整");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交审批申请" }));

    expect(onSubmit).toHaveBeenCalledWith({ role: "editor", reason: "职责调整" });
  });

  it("shows request id/status and navigates to approval center after submission", async () => {
    const navigate = vi.fn();
    render(
      <MemberRoleApprovalDialog
        member={member}
        visible
        saving={false}
        error={null}
        mode="submitted"
        policy={policy}
        request={request}
        submitting={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onNavigateToApprovalCenter={navigate}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "修改成员角色" });
    expect(within(dialog).getByText("request-role-1")).toBeTruthy();
    expect(within(dialog).getByText("待审批")).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "前往审批中心" }));
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("keeps the role fields disabled while policy facts are loading or submitted", () => {
    const { rerender } = render(
      <MemberRoleApprovalDialog
        member={member}
        visible
        saving={false}
        error={null}
        mode="loading"
        policy={null}
        request={null}
        submitting={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onNavigateToApprovalCenter={vi.fn()}
      />,
    );
    const loadingDialog = screen.getByRole("dialog", { name: "修改成员角色" });
    expect(
      (within(loadingDialog).getByRole("combobox", { name: "新角色" }) as HTMLSelectElement)
        .disabled,
    ).toBe(true);

    rerender(
      <MemberRoleApprovalDialog
        member={member}
        visible
        saving={false}
        error={null}
        mode="submitted"
        policy={policy}
        request={request}
        submitting={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onNavigateToApprovalCenter={vi.fn()}
      />,
    );
    expect(
      (
        within(screen.getByRole("dialog", { name: "修改成员角色" })).getByRole("combobox", {
          name: "新角色",
        }) as HTMLSelectElement
      ).disabled,
    ).toBe(true);
  });
});
