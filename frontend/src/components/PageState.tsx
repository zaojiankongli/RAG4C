import type { ReactNode } from "react";
import { Spin } from "../ui/index";
import { ExclamationCircleOutlined, InboxOutlined } from "../ui/icons";

export type PageStateStatus = "loading" | "empty" | "error";

interface Props {
  status: PageStateStatus;
  /** 一句话说清「现在是什么情况」；不传时用状态默认文案 */
  title?: ReactNode;
  /** 补充说明：为什么会这样、接下来能做什么 */
  description?: ReactNode;
  /** 覆盖默认图标（loading 状态忽略此项） */
  icon?: ReactNode;
  /** 行动区：重试按钮、跳转链接等 */
  extra?: ReactNode;
  /** 紧凑版：嵌在卡片或表格内部时用，纵向留白减半 */
  compact?: boolean;
}

const DEFAULT_TITLE: Record<PageStateStatus, string> = {
  loading: "加载中…",
  empty: "暂时没有内容",
  error: "暂时取不到数据",
};

/**
 * 页面态占位 —— 加载 / 空 / 错误三态的统一表达。
 *
 * 之前每个页面各写各的：有的用裸 `<Spin tip>`（TDesign 5 已废弃该用法，
 * 控制台报警告），有的用 `<Empty imageStyle>`（同样已废弃），有的直接写
 * 一段灰字，还有的干脆不显示错误、静默换成演示数据。三态没有统一表达，
 * 用户就无法从视觉上分辨「还没加载完」「确实没有」和「出错了」。
 *
 * 用法上刻意不含重试逻辑：重试策略由调用方通过 `extra` 传入按钮决定。
 */
export default function PageState({
  status,
  title,
  description,
  icon,
  extra,
  compact = false,
}: Props) {
  const visual =
    status === "loading" ? (
      <Spin size="large" />
    ) : (
      (icon ?? (status === "error" ? <ExclamationCircleOutlined /> : <InboxOutlined />))
    );

  return (
    <div
      className={
        "page-state" +
        (compact ? " is-compact" : "") +
        (status === "error" ? " is-error" : "")
      }
      // 错误用 alert 打断读屏，加载 / 空态用 status 平和播报
      role={status === "error" ? "alert" : "status"}
      aria-live={status === "error" ? "assertive" : "polite"}
    >
      <div className="page-state-icon">{visual}</div>
      <div className="page-state-title">{title ?? DEFAULT_TITLE[status]}</div>
      {description && <p className="page-state-desc">{description}</p>}
      {extra && <div className="page-state-extra">{extra}</div>}
    </div>
  );
}
