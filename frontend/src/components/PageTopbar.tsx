import type { ReactNode } from "react";

interface Props {
  title: ReactNode;
  icon?: ReactNode;
  subtitle?: ReactNode;
  extra?: ReactNode;
}

/**
 * 页面顶栏 —— 与主流知识库产品一致的贴顶标题条：
 * 左侧图标 + 标题 + 一句话说明，右侧为该页的主要操作。
 * 顶栏固定不滚动，内容区独立滚动。
 */
export default function PageTopbar({ title, icon, subtitle, extra }: Props) {
  return (
    <header className="page-topbar">
      <div className="page-topbar-main">
        {icon && <span className="page-topbar-icon">{icon}</span>}
        <h1 className="page-topbar-title">{title}</h1>
        {subtitle && <span className="page-topbar-sub">{subtitle}</span>}
      </div>
      {extra && <div className="page-topbar-extra">{extra}</div>}
    </header>
  );
}
