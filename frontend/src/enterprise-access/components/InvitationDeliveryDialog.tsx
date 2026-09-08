import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Dialog, Input, Tag } from "tdesign-react";
import { BrowseIcon, BrowseOffIcon, CopyIcon, LinkIcon } from "tdesign-icons-react";
import type { EnterpriseInvitationDelivery } from "../enterpriseAccessModel";
import { buildInvitationAcceptUrl } from "../invitationRoute";

export interface InvitationDeliveryDialogProps {
  delivery: EnterpriseInvitationDelivery | null;
  onClose: () => void;
}

export default function InvitationDeliveryDialog({
  delivery,
  onClose,
}: InvitationDeliveryDialogProps) {
  const [revealed, setRevealed] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const acceptUrl = useMemo(() => {
    if (!delivery || typeof window === "undefined") return "";
    return buildInvitationAcceptUrl(delivery.invite_token, window.location);
  }, [delivery]);

  useEffect(() => {
    setRevealed(false);
    setCopyState("idle");
  }, [delivery?.invite_token]);

  if (!delivery) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(acceptUrl);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <Dialog
      visible
      header="一次性邀请安全链接"
      width={620}
      destroyOnClose
      cancelBtn={null}
      confirmBtn={{ content: "关闭", theme: "primary" }}
      onClose={onClose}
      onConfirm={onClose}
      {...({
        role: "dialog",
        "aria-modal": "true",
        "aria-label": "一次性邀请安全链接",
      } as Record<string, unknown>)}
    >
      <Alert
        theme="warning"
        title="邮件通道未接入"
        message="delivery.state=manual_link_required；系统没有发送邮件。请仅通过受控渠道交付此一次性链接。"
      />
      <div className="enterprise-invitation-delivery-state">
        <LinkIcon aria-hidden="true" />
        <div>
          <span>delivery.state</span>
          <strong>manual_link_required</strong>
        </div>
        <Tag theme="warning" variant="light-outline" size="small">
          返回一次
        </Tag>
      </div>
      <div className="enterprise-invitation-secure-link">
        <Input
          type={revealed ? "text" : "password"}
          value={acceptUrl}
          readOnly
          aria-label="一次性邀请安全链接"
          suffixIcon={revealed ? <BrowseOffIcon /> : <BrowseIcon />}
          onClick={() => setRevealed((value) => !value)}
        />
        <Button theme="primary" icon={<CopyIcon />} onClick={() => void copy()}>
          复制安全链接
        </Button>
      </div>
      <div className="enterprise-invitation-delivery-meta">
        <span>链接到期时间</span>
        <strong>{delivery.expires_at}</strong>
      </div>
      {copyState !== "idle" ? (
        <Alert
          theme={copyState === "copied" ? "success" : "error"}
          title={copyState === "copied" ? "安全链接已复制" : "复制失败"}
          message={
            copyState === "copied"
              ? "请在受控渠道中粘贴；关闭此窗口后前端将清除此 token。"
              : "浏览器未授予剪贴板权限，请显示链接后手动复制。"
          }
        />
      ) : null}
    </Dialog>
  );
}
