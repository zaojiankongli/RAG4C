import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, GraphChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsType } from "echarts/core";
import type { EChartsOption } from "echarts";

// 按需注册，控制包体积（当前使用：graph 力导向 + bar 柱状 + line 趋势 + legend）
echarts.use([
  BarChart,
  GraphChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export type { EChartsOption };

interface Props {
  option: EChartsOption;
  height?: number | string;
  className?: string;
  /** 为当前图表提供具体、可理解的读屏说明 */
  ariaLabel?: string;
  /** 图表实例就绪回调（用于导出 PNG / 手动控制） */
  onReady?: (chart: EChartsType) => void;
  /** 图表事件（如节点点击） */
  onEvents?: Record<string, (params: unknown) => void>;
}

/**
 * ECharts 通用封装：
 * - 自动 init / dispose，ResizeObserver 响应容器尺寸变化；
 * - option 变更时 setOption(notMerge) 全量更新；
 * - 事件绑定 + 导出能力经 onReady 暴露。
 */
export default function EChart({
  option,
  height = 400,
  className,
  ariaLabel = "数据图表",
  onReady,
  onEvents,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const eventsRef = useRef(onEvents);
  eventsRef.current = onEvents;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    onReady?.(chart);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // 仅挂载时初始化一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (chartRef.current && eventsRef.current) {
      for (const [name, handler] of Object.entries(eventsRef.current)) {
        chartRef.current.on(name, handler);
      }
    }
    return () => {
      const chart = chartRef.current;
      if (chart && eventsRef.current) {
        for (const name of Object.keys(eventsRef.current)) {
          chart.off(name);
        }
      }
    };
  }, [onEvents]);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div
      ref={ref}
      className={className}
      style={{ width: "100%", height }}
      role="img"
      aria-label={ariaLabel}
    />
  );
}
