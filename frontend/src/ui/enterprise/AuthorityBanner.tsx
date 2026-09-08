import type { ReactNode } from "react";
import { Alert, Tag } from "tdesign-react";
import {
  ErrorCircleIcon,
  InfoCircleIcon,
  SecuredIcon,
} from "tdesign-icons-react";

export type AuthorityBannerTone = "authoritative" | "projection" | "warning" | "danger";

export interface AuthorityBannerProps {
  title: string;
  description?: ReactNode;
  tone?: AuthorityBannerTone;
  badgeLabel?: string;
  actions?: ReactNode;
}

const ALERT_THEME: Record<AuthorityBannerTone, "success" | "info" | "warning" | "error"> = {
  authoritative: "success",
  projection: "info",
  warning: "warning",
  danger: "error",
};

const BADGE_THEME: Record<AuthorityBannerTone, "success" | "primary" | "warning" | "danger"> = {
  authoritative: "success",
  projection: "primary",
  warning: "warning",
  danger: "danger",
};

const TONE_ICON = {
  authoritative: <SecuredIcon />,
  projection: <InfoCircleIcon />,
  warning: <ErrorCircleIcon />,
  danger: <ErrorCircleIcon />,
} satisfies Record<AuthorityBannerTone, ReactNode>;

/** 明确区分目录真账、派生投影和异常状态，避免企业控制台把演示或陈旧数据伪装成权威数据。 */
export default function AuthorityBanner({
  title,
  description,
  tone = "authoritative",
  badgeLabel,
  actions,
}: AuthorityBannerProps) {
  const fallbackBadge = tone === "authoritative" ? "权威数据" : tone === "projection" ? "派生视图" : "需要关注";

  return (
    <section
      className={`enterprise-authority-banner is-${tone}`}
      role="status"
      aria-label={title}
    >
      <Alert
        theme={ALERT_THEME[tone]}
        icon={TONE_ICON[tone]}
        title={
          <span className="enterprise-authority-banner__title">
            {title}
            <Tag
              className="enterprise-authority-banner__badge"
              theme={BADGE_THEME[tone]}
              variant="light-outline"
              size="small"
            >
              {badgeLabel ?? fallbackBadge}
            </Tag>
          </span>
        }
        message={description}
        operation={actions ? <span className="enterprise-authority-banner__actions">{actions}</span> : undefined}
      />
    </section>
  );
}

