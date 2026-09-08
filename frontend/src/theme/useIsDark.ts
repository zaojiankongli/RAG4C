import { useEffect, useState } from "react";

/**
 * 订阅当前是否为暗色主题（读取 <html data-theme>）。
 * 供非 React 主题体系（ECharts 等）在主题切换时同步配色。
 */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");

  useEffect(() => {
    const el = document.documentElement;
    const update = () => setDark(el.dataset.theme === "dark");
    const obs = new MutationObserver(update);
    obs.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  return dark;
}
