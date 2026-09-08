/**
 * 图表配色 —— 全站 ECharts 的唯一色源。
 *
 * 为什么要单独一层：ECharts 在 canvas 里绘制，读不到 CSS 变量，option 只能吃
 * 十六进制。此前三个图表组件（GraphExplorer / PipelineFlow / MonitorPage）
 * 各自维护一份「明 + 暗」调色板，同一个主色在四处各写一遍，加起来约 50 个
 * 硬编码色值；换主题色要手工找齐，漏掉一处就出现明暗不一致的图。
 *
 * 现在所有色值都从 styles.css 的 `--color-*` / theme/tokens.ts 的语义色抄写到
 * 这一个文件里，键名与 CSS 变量对齐，改配色时只改这里 + styles.css 两处。
 * 少数只在图表里出现、CSS 侧没有对应变量的「节点底色」在下方单独标注了来源。
 */
import { FONT_SIZE } from "../theme/tokens";
import { useIsDark } from "../theme/useIsDark";

export interface ChartPalette {
  /* ---- 画布 ---- */
  /** 图表画布底色（对应 --color-bg-elevated） */
  bg: string;
  /** 下沉画布（流程图用，比卡片略暗一档） */
  bgSunken: string;

  /* ---- 文本 ---- */
  /** 主文本（--color-text） */
  text: string;
  /** 次要文本 / 节点副标题（--color-text-tertiary） */
  textSub: string;
  /** 坐标轴与图例文字：与 tokens.ts 里 Table.headerColor 同值，
   *  比 --color-text-secondary 浅一档，避免网格上的字压过数据 */
  axis: string;

  /* ---- 线 ---- */
  /** 描边 / 关系边（--color-border） */
  border: string;
  /** 强描边（--color-border-strong） */
  borderStrong: string;
  /** 网格分隔线：浅色取 --color-border-light，暗色取 --color-border
   *  （暗色下 border-light 与背景几乎无对比，网格会整片消失） */
  splitLine: string;
  /** 流程图箭头 */
  arrow: string;
  /** 浮起阴影 */
  shadow: string;

  /* ---- 语义系列色（--color-primary / graph / success / warning / danger） ---- */
  primary: string;
  /** 主色深一档（--color-primary-strong / hover），用于选中描边与文字 */
  primaryStrong: string;
  /** 主色浅底（--color-primary-bg） */
  primarySoft: string;
  graph: string;
  /** 图谱浅底（--color-graph-bg） */
  graphSoft: string;
  success: string;
  /** 成功浅底（--color-success-bg） */
  successSoft: string;
  warning: string;
  danger: string;

  /* ---- 流程图节点（只有图表用到，CSS 侧无对应变量） ---- */
  node: { fill: string; stroke: string; text: string; sub: string };
  /** 本次链路命中的节点 */
  active: { fill: string; stroke: string; text: string };
  /** 图谱旁路节点 */
  graphNode: { fill: string; stroke: string; text: string };
  /** 图谱旁路 + 命中 */
  graphActive: { fill: string; stroke: string; text: string };
  /** 回放已完成的节点 */
  done: { fill: string; stroke: string; text: string };

  /* ---- 力导向图节点 ---- */
  /** 普通实体底色 */
  entityFill: string;
  /** 选中实体的邻居底色 */
  neighborFill: string;
}

const LIGHT: ChartPalette = {
  bg: "#ffffff",
  bgSunken: "#fafbfc",
  text: "#101828",
  textSub: "#98a2b3",
  axis: "#667085",
  border: "#e6eaf1",
  borderStrong: "#d4dae5",
  splitLine: "#eef1f6",
  arrow: "#98a2b3",
  shadow: "rgba(16, 24, 40, 0.16)",
  primary: "#3164f4",
  primaryStrong: "#1f46b8",
  primarySoft: "#eef3fe",
  graph: "#6d28d9",
  graphSoft: "#f5f1ff",
  success: "#16a34a",
  successSoft: "#ecfdf3",
  warning: "#d97706",
  danger: "#dc2626",
  node: { fill: "#ffffff", stroke: "#e6eaf1", text: "#101828", sub: "#98a2b3" },
  active: { fill: "#eef3fe", stroke: "#3164f4", text: "#1f46b8" },
  graphNode: { fill: "#f5f1ff", stroke: "#6d28d9", text: "#5b21b6" },
  graphActive: { fill: "#ece5ff", stroke: "#6d28d9", text: "#5b21b6" },
  done: { fill: "#ecfdf3", stroke: "#16a34a", text: "#15803d" },
  entityFill: "#f1f4f9",
  neighborFill: "#eef3fe",
};

const DARK: ChartPalette = {
  bg: "#161a23",
  bgSunken: "#0c0f15",
  text: "#f2f4f7",
  textSub: "#7c8496",
  axis: "#c3c9d4",
  border: "#262c3a",
  borderStrong: "#3a4254",
  splitLine: "#262c3a",
  arrow: "#4a5162",
  shadow: "rgba(0, 0, 0, 0.55)",
  primary: "#60a5fa",
  primaryStrong: "#93c5fd",
  primarySoft: "#1e2a3f",
  graph: "#a78bfa",
  graphSoft: "#2b2340",
  success: "#4ade80",
  successSoft: "#14301f",
  warning: "#fbbf24",
  danger: "#f87171",
  node: { fill: "#1c212c", stroke: "#3a4254", text: "#f2f4f7", sub: "#7c8496" },
  active: { fill: "#1e2a3f", stroke: "#60a5fa", text: "#93c5fd" },
  graphNode: { fill: "#2b2340", stroke: "#a78bfa", text: "#c4b5fd" },
  graphActive: { fill: "#342a4d", stroke: "#a78bfa", text: "#ddd6fe" },
  done: { fill: "#14301f", stroke: "#4ade80", text: "#86efac" },
  entityFill: "#1c212c",
  neighborFill: "#1e2a3f",
};

/** 明暗调色板（非 React 上下文里按需取，例如导出 PNG 时的背景色） */
export function chartPalette(dark: boolean): ChartPalette {
  return dark ? DARK : LIGHT;
}

/** 组件内取当前主题的调色板；跟随 html[data-theme] 切换自动重渲染 */
export function useChartPalette(): ChartPalette {
  return chartPalette(useIsDark());
}

/**
 * 图表内字号 —— 画布里的字不参与 DOM 的字号阶梯，但同样不该散落字面量。
 * 值取自 theme/tokens.ts 的 FONT_SIZE，比同层级 DOM 文字小一档：
 * canvas 文字没有字体平滑与行高兜底，同号显得更重。
 */
export const CHART_FONT = {
  /** 数据点角标 */
  annotation: FONT_SIZE.micro,
  /** 柱顶数值、边标签 */
  dataLabel: FONT_SIZE.xxs,
  /** 坐标轴刻度、图例 */
  axis: FONT_SIZE.xs,
  /** 轴名、节点副标题 */
  caption: FONT_SIZE.sm,
  /** 节点主标题 */
  nodeTitle: FONT_SIZE.md,
} as const;
